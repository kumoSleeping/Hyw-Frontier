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
from hyw_frontier.image_input import ImageInputError
from hyw_frontier.jina import JinaError
from hyw_frontier.source_titles import source_key, source_titles

from .attachments import prepare_components
from .message_parser import parse_input
from .config import Config
from .delivery import outgoing_jpeg
from .trace import BotTrace
from hyw_frontier.telemetry import stage

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
        # Percent-encoded URLs stay encoded: chat clients linkify them as-is, while a
        # decoded Unicode path may not be recognized as a clickable address.
        text = "\n\n".join(f"{i}. {title}\n{url}" for i, (title, url) in enumerate(sources, 1))
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
        phase = "attachments"

        async def send_intro(text: str):
            trace.event({"type": "intro_callback_start", "text": text})
            try:
                with stage(trace.event, 'intro_delivery'):
                    receipts = await self.send(session, text)
            except BaseException as exc:
                trace.event({"type": "intro_delivery_failed", "error_type": type(exc).__name__})
                raise
            trace.event({"type": "intro_delivered", "receipt_ids": [item.id for item in receipts or []]})

        try:
            async with asyncio.timeout(self.config.timeout):
                with stage(trace.event, 'message_parse') as metrics:
                    parsed = await parse_input(session, chain)
                    metrics.update(record_messages=parsed.message_count, parts=len(parsed.parts))
                if not question and not parsed.parts:
                    await self.send(session, f"请输入问题：{self.config.command} <问题>；帮助：{self.config.help_command}")
                    status = "done"
                    return
                from hyw_frontier.prompt_files import read_prompt
                question = question or read_prompt('component_question.md')
                with stage(trace.event, 'attachment_prepare') as metrics:
                    prepared = await prepare_components(parsed, question, on_event=trace.event)
                    metrics.update(images=prepared.images, input_bytes=prepared.byte_count,
                                   failed_images=prepared.failed_images, truncated=prepared.truncated)
                trace.event({"type": "input_ready", "images": prepared.images, "history_messages": 0,
                             "record_messages": parsed.message_count, "input_bytes": prepared.byte_count,
                             "failed_images": prepared.failed_images, "input_truncated": prepared.truncated,
                             "truncation_reason": prepared.truncation_reason,
                             "parsed_text": ''.join(block['text'] for block in prepared.content if block['type'] == 'text')})
                phase = "answer"
                result = await answer(
                    question,
                    message_content=prepared.content,
                    send=send_intro,
                    on_event=trace.event,
                    **self.config.answer_options(),
                )
                phase = "result_processing"
                titles = source_titles(result.messages)
                sources = tuple((titles.get(source_key(url)) or urlsplit(url).hostname or url, url)
                                for url in result.links)
                receipts = []
                if result.kind == 'text':
                    phase = "text_delivery"
                    trace.event({"type": "text_delivery_start", "characters": len(result.display_text)})
                    with stage(trace.event, 'text_delivery'):
                        receipts = await self.send_chunks(session, result.display_text)
                    trace.event({"type": "text_delivered", "receipt_ids": [item.id for item in receipts]})
                else:
                    phase = "jpeg_encoding"
                    try:
                        with stage(trace.event, 'jpeg_encoding') as metrics:
                            outgoing, width, height = await outgoing_jpeg(result.png, self.config.jpeg_quality)
                            metrics.update(input_bytes=len(result.png), output_bytes=len(outgoing), width=width, height=height)
                        trace.event({"type": "outgoing_image", "mime_type": "image/jpeg",
                                     "png_bytes": len(result.png), "bytes": len(outgoing),
                                     "quality": self.config.jpeg_quality, "width": width, "height": height})
                        phase = "image_delivery"
                        trace.event({"type": "image_delivery_start", "mime_type": "image/jpeg"})
                        with stage(trace.event, 'image_delivery'):
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
                if prepared.truncated:
                    await self.send(session, f"输入资料已截断：{prepared.truncation_reason}；回答仅依据已提供部分。")
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
            if isinstance(exc, ImageInputError):
                reason = str(exc)
                details["code"] = getattr(exc, "code", "attachment_invalid")
            elif isinstance(exc, JinaError):
                reason = f"搜索服务失败：{exc}"
                details["code"] = "search_" + exc.code
            elif isinstance(exc, FrontierError):
                # These are application-owned, sanitized messages, not SDK exceptions.
                reason = str(exc)
                details.setdefault("code", getattr(exc, "code", "answer_failed"))
            elif isinstance(exc, TimeoutError):
                reason = {
                    "attachments": "解析消息或获取图片超时，请缩小聊天记录后重试。",
                    "text_delivery": "回答文字发送超时，平台可能仍会送达。",
                }.get(phase, "本轮处理超时，已回收资源，请稍后重试或缩小问题范围。")
                details["code"] = phase + "_timeout"
            else:
                reason = {
                    "attachments": "无法准备图片附件，请重新上传原图。",
                    "answer": "问答处理发生内部异常，请联系管理员查看服务日志。",
                    "result_processing": "回答结果处理失败，请联系管理员查看服务日志。",
                    "text_delivery": "回答文字发送失败，请稍后重试。",
                }.get(phase, "本轮处理失败，请联系管理员查看服务日志。")
                details["code"] = phase + "_failed"
            trace.event({"type": "request_failed", "phase": phase, "error_type": type(exc).__name__,
                         "message": reason, **details})
            logger.warning("Frontier request {} failed ({}) phase={} code={} reason={} after {:.1f}s",
                           trace.id, type(exc).__name__, phase, details.get("code"), reason,
                           time.monotonic() - started)
            if phase in ("jpeg_encoding", "image_delivery"):
                # Also silence the outer request deadline expiring during image delivery.
                return
            message = f"本轮未完成：{reason}\n其他问题不受影响。"
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
