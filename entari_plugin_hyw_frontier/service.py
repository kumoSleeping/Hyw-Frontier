"""Independent concurrent requests with bounded, owner-scoped source records."""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urlsplit

from arclet.entari import Image, MessageChain, Text
from loguru import logger

from hyw_frontier import answer
from hyw_frontier.errors import FrontierError
from hyw_frontier.source_titles import source_key, source_titles

from .attachments import prepare_images
from .config import Config
from .delivery import outgoing_jpeg, readable_url
from .trace import BotTrace

Scope = tuple[str, str, str, str, str]


@dataclass(frozen=True)
class ReplySources:
    owner: Scope
    sources: tuple[tuple[str, str], ...]
    created: float
    size: int


class FrontierService:
    def __init__(self, config: Config):
        config.validate()
        self.config = config
        self.latest_sources: OrderedDict[Scope, ReplySources] = OrderedDict()
        # Receipt IDs are scoped to this bot/channel, not the member issuing /link.
        self.reply_sources: OrderedDict[Scope, ReplySources] = OrderedDict()
        self.running: dict[asyncio.Task, Scope] = {}
        self.closed = False

    def scope(self, session) -> Scope | None:
        event = session.event
        account = session.account
        if not event.user or not event.channel or event.user.id == account.self_id or event.user.is_bot:
            return None
        if self.config.allow_users is not None and event.user.id not in self.config.allow_users:
            return None
        if self.config.allow_channels is not None and event.channel.id not in self.config.allow_channels:
            return None
        return (account.platform, account.self_id, event.guild.id if event.guild else "",
                event.channel.id, event.user.id)

    def prune(self):
        now = time.monotonic()
        caches = (self.latest_sources, self.reply_sources)
        for cache in caches:
            for key, record in list(cache.items()):
                if now - record.created > self.config.source_ttl:
                    del cache[key]
        size = sum(record.size for cache in caches for record in cache.values())
        while (sum(map(len, caches)) > self.config.max_source_records
               or size > self.config.max_source_bytes):
            oldest = min((cache for cache in caches if cache),
                         key=lambda cache: next(iter(cache.values())).created)
            _, removed = oldest.popitem(last=False)
            size -= removed.size

    def remember_sources(self, key: Scope, receipts, sources):
        record = ReplySources(key, sources, time.monotonic(),
                              len(json.dumps(sources, ensure_ascii=False).encode('utf-8')))
        self.latest_sources[key] = record
        self.latest_sources.move_to_end(key)
        for receipt in receipts or []:
            if receipt.id:
                address = (*key[:4], str(receipt.id))
                self.reply_sources[address] = record
                self.reply_sources.move_to_end(address)
        self.prune()

    async def send(self, session, text: str):
        async with asyncio.timeout(self.config.send_timeout):
            return await session.send(MessageChain(Text(text)), reply_to=self.config.quote)

    async def stop(self, session, key: Scope):
        tasks = [task for task, owner in self.running.items() if owner == key and not task.done()]
        if tasks:
            for task in tasks:
                if not task.cancelling():
                    task.cancel()
            await self.send(session, f"已请求停止你的 {len(tasks)} 个任务，正在回收资源。")
        else:
            await self.send(session, "当前没有正在处理的问题。")

    async def reset(self, session, key: Scope):
        if key in self.running.values():
            await self.send(session, f"请先用 {self.config.stop_command} 停止本人的所有任务，收尾后再清空来源记录。")
            return
        self.latest_sources.pop(key, None)
        for address, record in list(self.reply_sources.items()):
            if record.owner == key:
                del self.reply_sources[address]
        await self.send(session, "已清空你的来源链接记录；每个问题均独立执行，不保存对话历史。")

    async def links(self, session, key: Scope, quoted_id: str | None = None):
        self.prune()
        if quoted_id is not None:
            record = self.reply_sources.get((*key[:4], quoted_id))
            if record is None:
                await self.send(session, "未找到这条消息的来源记录；请回复本插件发出的回答。重启、过期或清空后的记录不可恢复。")
                return
        else:
            record = self.latest_sources.get(key)
        sources = record.sources if record else ()
        text = "\n\n".join(f"{i}. {title}\n{readable_url(url)}" for i, (title, url) in enumerate(sources, 1))
        await self.send_chunks(session, text or "这条回答没有引用来源链接。")

    async def send_chunks(self, session, text: str):
        receipts = []
        for start in range(0, len(text), 3000):
            receipts.extend(await self.send(session, text[start:start + 3000]) or [])
        return receipts

    async def submit(self, session, key: Scope, question: str, chain: MessageChain):
        self.prune()
        if self.closed:
            return
        if len(self.running) >= self.config.max_concurrent:
            await self.send(session, "当前问答任务已满，本条未排队，请稍后重试。")
            return
        if len(question) > self.config.max_question_chars:
            await self.send(session, f"问题过长，最多 {self.config.max_question_chars} 字符。")
            return
        # Reserve before yielding, then release the event handler immediately so even
        # serial adapters can deliver further commands. The service owns task cleanup.
        task = asyncio.create_task(self._run(session, key, question, chain))
        self.running[task] = key
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task):
        self.running.pop(task, None)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.warning("Frontier background request failed ({})", type(error).__name__)

    async def _run(self, session, key, question, chain):
        started = time.monotonic()
        trace = BotTrace(self.config, question, key, session.event.message.id)
        status = "error"
        phase = "answer"

        async def send_intro(text: str):
            trace.event({"type": "intro_callback_start", "text": text})
            try:
                receipts = await self.send(session, text)
            except BaseException as exc:
                trace.event({"type": "intro_delivery_failed", "error_type": type(exc).__name__})
                raise
            trace.event({"type": "intro_delivered", "receipt_ids": [item.id for item in receipts or []]})

        try:
            async with asyncio.timeout(self.config.timeout):
                images = await prepare_images(chain)
                trace.event({"type": "input_ready", "images": len(images), "history_messages": 0})
                if not question and not images:
                    await self.send(session, f"请输入问题：{self.config.command} <问题>；帮助：{self.config.help_command}")
                    status = "done"
                    return
                result = await answer(
                    question,
                    images=images,
                    send=send_intro,
                    on_event=trace.event,
                    **self.config.answer_options(),
                )
                titles = source_titles(result.messages)
                sources = tuple((titles.get(source_key(url)) or urlsplit(url).hostname or url, url)
                                for url in result.links)
                receipts = []
                if result.kind == 'text':
                    trace.event({"type": "text_delivery_start", "characters": len(result.display_text)})
                    receipts = await self.send_chunks(session, result.display_text)
                    trace.event({"type": "text_delivered", "receipt_ids": [item.id for item in receipts]})
                else:
                    phase = "jpeg_encoding"
                    try:
                        outgoing, width, height = await outgoing_jpeg(result.png, self.config.jpeg_quality)
                        trace.event({"type": "outgoing_image", "mime_type": "image/jpeg",
                                     "png_bytes": len(result.png), "bytes": len(outgoing),
                                     "quality": self.config.jpeg_quality, "width": width, "height": height})
                        phase = "image_delivery"
                        trace.event({"type": "image_delivery_start", "mime_type": "image/jpeg"})
                        async with asyncio.timeout(self.config.send_timeout):
                            receipts = await session.send(MessageChain(Image.of(raw=outgoing, mime="image/jpeg")),
                                                          reply_to=self.config.quote)
                        trace.event({"type": "image_delivered", "receipt_ids": [item.id for item in receipts or []]})
                    except Exception as exc:  # noqa: BLE001 - adapter-independent delivery boundary
                        trace.event({"type": "image_delivery_failed", "phase": phase, "error_type": type(exc).__name__})
                        logger.warning("Frontier request {} image delivery failed ({})", trace.id, type(exc).__name__)
                        # The adapter may still deliver the image. Diagnostics stay in
                        # logs: no answer resend and no chat notice of any kind.
                        return
                phase = "delivered"
                # Only successful final delivery updates /link; history is never retained.
                self.remember_sources(key, receipts, sources)
                if result.truncated:
                    await self.send(session, "本次模型输出达到长度上限，回答可能不完整。")
                status = "done"
                logger.info("Frontier completed: answer_ms={} render_ms={} links={} actual_usd={} estimated_usd={}",
                            result.answer_ms, result.render_ms, len(result.links),
                            result.costs.total_usd, result.costs.estimated_total_usd)
        except asyncio.CancelledError:
            status = "cancelled"
            logger.info("Frontier request {} cancelled after {:.1f}s", trace.id, time.monotonic() - started)
            raise
        except Exception as exc:  # noqa: BLE001 - sanitize all provider/adapter errors
            # Never expose SDK messages/URLs/keys or traceback locals in a group chat.
            details = {key: exc.diagnostics[key] for key in ("code", "http_status", "retryable")
                       if key in exc.diagnostics} if isinstance(exc, FrontierError) else {}
            trace.event({"type": "request_failed", "phase": phase, "error_type": type(exc).__name__, **details})
            logger.warning("Frontier request {} failed ({}) after {:.1f}s", trace.id, type(exc).__name__, time.monotonic() - started)
            if phase in ("jpeg_encoding", "image_delivery"):
                # Also silence the outer request deadline expiring during image delivery.
                return
            if isinstance(exc, TimeoutError):
                message = "本轮已超时并回收资源，其他问题不受影响。"
            else:
                message = "本轮未完成，请检查模型/搜索凭据、附件和服务日志；其他问题不受影响。"
            try:
                await self.send(session, message)
            except Exception as delivery:  # noqa: BLE001 - failed error delivery must not escape
                logger.warning("Frontier error notice failed ({})", type(delivery).__name__)
        finally:
            try:
                trace.close(status)
            except Exception as exc:  # noqa: BLE001 - diagnostics must not mask cancellation or delivery
                logger.warning("Frontier trace close failed ({})", type(exc).__name__)

    async def close(self):
        self.closed = True
        tasks = list(self.running)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.running.clear()
        self.latest_sources.clear()
        self.reply_sources.clear()
