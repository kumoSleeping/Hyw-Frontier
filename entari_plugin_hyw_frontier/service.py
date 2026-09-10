"""Bounded, per-account/channel/member conversation ownership."""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from dataclasses import dataclass, field
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


@dataclass
class Conversation:
    sticky: bool = False
    messages: list[dict] = field(default_factory=list)
    sources: tuple[tuple[str, str], ...] = ()
    turns: int = 0
    size: int = 0
    updated: float = field(default_factory=time.monotonic)


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
        self.records: OrderedDict[Scope, Conversation] = OrderedDict()
        # Receipt IDs are scoped to this bot/channel, not the member issuing /link.
        self.reply_sources: OrderedDict[Scope, ReplySources] = OrderedDict()
        self.running: dict[Scope, asyncio.Task] = {}
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
        for key, record in list(self.records.items()):
            if key not in self.running and now - record.updated > self.config.session_ttl:
                del self.records[key]
        for key, record in list(self.reply_sources.items()):
            if now - record.created > self.config.session_ttl:
                del self.reply_sources[key]

    def remember_sources(self, key: Scope, receipts, sources):
        self.prune()
        record = ReplySources(key, sources, time.monotonic(),
                              len(json.dumps(sources, ensure_ascii=False).encode('utf-8')))
        for receipt in receipts or []:
            if receipt.id:
                address = (*key[:4], str(receipt.id))
                self.reply_sources[address] = record
                self.reply_sources.move_to_end(address)
        size = sum(item.size for item in self.reply_sources.values())
        while self.reply_sources and (len(self.reply_sources) > self.config.max_sessions * self.config.max_turns
                                      or size > self.config.max_total_history_bytes):
            _, removed = self.reply_sources.popitem(last=False)
            size -= removed.size

    async def send(self, session, text: str):
        async with asyncio.timeout(self.config.send_timeout):
            return await session.send(MessageChain(Text(text)), reply_to=self.config.quote)

    async def stop(self, session, key: Scope):
        task = self.running.get(key)
        if task and not task.done():
            task.cancel()
            await self.send(session, "已请求停止，正在回收本轮资源；完成前不能开始下一问。")
        else:
            await self.send(session, "当前没有正在处理的问题。")

    async def reset(self, session, key: Scope):
        if key in self.running:
            await self.send(session, f"请先用 {self.config.stop_command} 停止当前问题，收尾后再重置。")
            return
        self.records.pop(key, None)
        for address, record in list(self.reply_sources.items()):
            if record.owner == key:
                del self.reply_sources[address]
        await self.send(session, "已清空你的当前会话和来源链接。")

    async def links(self, session, key: Scope, quoted_id: str | None = None):
        self.prune()
        if quoted_id is not None:
            record = self.reply_sources.get((*key[:4], quoted_id))
            if record is None:
                await self.send(session, "未找到这条消息的来源记录；请回复本插件发出的回答。重启、过期或清空后的记录不可恢复。")
                return
        else:
            record = self.records.get(key)
        sources = record.sources if record else ()
        text = "\n\n".join(f"{i}. {title}\n{readable_url(url)}" for i, (title, url) in enumerate(sources, 1))
        await self.send_chunks(session, text or "这条回答没有引用来源链接。")

    async def send_chunks(self, session, text: str):
        receipts = []
        for start in range(0, len(text), 3000):
            receipts.extend(await self.send(session, text[start:start + 3000]) or [])
        return receipts

    async def submit(self, session, key: Scope, question: str, chain: MessageChain, sticky: bool):
        self.prune()
        if self.closed:
            return
        if key in self.running:
            await self.send(session, f"上一问仍在处理；本条未排队、未插入。可用 {self.config.stop_command} 停止。")
            return
        if len(self.running) >= self.config.max_concurrent:
            await self.send(session, "当前问答任务已满，请稍后重试。")
            return
        if len(question) > self.config.max_question_chars:
            await self.send(session, f"问题过长，最多 {self.config.max_question_chars} 字符。")
            return
        record = self.records.get(key)
        if record is None:
            if len(self.records) >= self.config.max_sessions:
                await self.send(session, "会话容量已满，请稍后重试。")
                return
            record = Conversation()
            self.records[key] = record
        if record.sticky and record.turns >= self.config.max_turns:
            await self.send(session, f"会话轮数已满；请用 {self.config.command} reset 开新会话，不会自动截断历史。")
            return
        # Reserve synchronously, before any I/O. Keep ownership through cancellation cleanup.
        task = asyncio.create_task(self._run(session, key, record, question, chain, sticky))
        self.running[key] = task
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            raise
        finally:
            self.running.pop(key, None)

    async def _run(self, session, key, record, question, chain, sticky):
        started = time.monotonic()
        trace = BotTrace(self.config, question, key, session.event.message.id)
        status = "error"

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
                trace.event({"type": "input_ready", "images": len(images),
                             "history_messages": len(record.messages) if record.sticky else 0})
                if not question and not images:
                    if sticky:
                        record.sticky = True
                        record.updated = time.monotonic()
                        await self.send(session, f"已开启续接；后续仍需使用 {self.config.command} 提问，reset 结束。")
                    else:
                        await self.send(session, f"请输入问题：{self.config.command} <问题>；帮助：{self.config.help_command}")
                    status = "done"
                    return
                result = await answer(
                    question,
                    images=images,
                    history=record.messages if record.sticky else None,
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
                        receipts = await self.send_chunks(session, "图片发送未确认，以下为文字版（图片可能已经送达）：\n" + result.display_text)
                        trace.event({"type": "text_fallback_delivered", "receipt_ids": [item.id for item in receipts]})
                # Commit only after final delivery. Failure/cancel leaves prior history intact.
                self.remember_sources(key, receipts, sources)
                record.sources = sources
                record.updated = time.monotonic()
                record.sticky = record.sticky or sticky
                if record.sticky:
                    size = len(json.dumps(result.messages, ensure_ascii=False).encode("utf-8"))
                    total = sum(r.size for r in self.records.values()) - record.size + size
                    if size > self.config.max_history_bytes or total > self.config.max_total_history_bytes:
                        record.sticky = False
                        record.messages = []
                        record.size = record.turns = 0
                        await self.send(session, "回答已发送，但历史达到容量上限，已结束续接。下一问将新建上下文。")
                    else:
                        record.messages = result.messages
                        record.size = size
                        record.turns += 1
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
            trace.event({"type": "request_failed", "error_type": type(exc).__name__, **details})
            logger.warning("Frontier request {} failed ({}) after {:.1f}s", trace.id, type(exc).__name__, time.monotonic() - started)
            if isinstance(exc, TimeoutError):
                message = "本轮已超时并回收资源，原有会话保留。"
            else:
                message = "本轮未完成，请检查模型/搜索凭据、附件和服务日志；原有会话保留。"
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
        tasks = list(self.running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.running.clear()
        self.records.clear()
        self.reply_sources.clear()
