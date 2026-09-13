"""Read chat components as ordered source material, never as executable instructions.

Only /q invokes network expansion. Forward IDs come from the triggering/replied
message; this module never scans a channel or follows arbitrary reply IDs.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
import asyncio
import json
import re
from xml.etree import ElementTree

from hyw_frontier.image_input import MAX_MESSAGE_BYTES
from hyw_frontier.jina import JinaError, public_url

MAX_COMPONENT_BYTES = 256 * 1024
MAX_PARTS = 16000
MAX_FORWARD_DEPTH = 8
MAX_FORWARD_MESSAGES = 2000
_TEXT_FIELDS = {"title": "标题", "desc": "描述", "description": "描述", "summary": "摘要",
                "content": "内容", "prompt": "卡片摘要", "tag": "来源", "name": "名称",
                "artist": "歌手", "singer": "歌手", "author": "作者", "address": "地址"}
_LINK_FIELDS = {"qqdocurl": "内容链接", "jumpurl": "内容链接", "url": "链接",
                "musicurl": "音频链接（未下载/播放）", "audiourl": "音频链接（未下载/播放）",
                "videourl": "视频链接（未下载/播放）", "href": "链接", "actionurl": "链接",
                "src": "资源链接", "file": "附件链接"}
_IMAGE_FIELDS = {"preview", "cover", "coverurl", "image", "imageurl", "pic", "picurl", "thumb", "thumbnail"}


@dataclass(frozen=True)
class MessagePart:
    kind: str
    value: str
    source_id: str = ""


@dataclass
class ParsedMessage:
    parts: list[MessagePart] = field(default_factory=list)
    text_bytes: int = 0
    message_count: int = 0
    truncated: bool = False
    truncation_reason: str = ""

    def stop(self, reason: str):
        self.truncated = True
        self.truncation_reason = reason

    def text(self, value: str):
        if self.truncated or not value:
            return
        if len(self.parts) >= MAX_PARTS:
            self.stop("内容块数量达到安全上限")
            return
        raw = value.encode("utf-8", errors="replace")
        remaining = MAX_MESSAGE_BYTES - 4096 - self.text_bytes
        if len(raw) > remaining:
            value = raw[:max(0, remaining)].decode("utf-8", errors="ignore")
            self.stop("聊天记录文本达到128 MiB上限")
        if value:
            self.parts.append(MessagePart("text", value))
            self.text_bytes += len(value.encode("utf-8"))

    def image(self, source: str, source_id: str):
        if self.truncated:
            return
        if len(self.parts) >= MAX_PARTS:
            self.stop("内容块数量达到安全上限")
            return
        self.parts.append(MessagePart("image", source, source_id))


def readable_url(value) -> str | None:
    if not isinstance(value, str) or len(value) > 8192:
        return None
    value = value.strip()
    if value.startswith("//"):
        value = "https:" + value
    elif value.startswith("m.q.qq.com/"):
        value = "https://" + value
    try:
        return public_url(value)
    except JinaError:
        return None


def element_segments(elements: Iterable) -> list[dict]:
    """Translate Satori elements, including OneBot custom elements, at one boundary."""
    result = []
    for element in elements:
        tag = element.tag.removeprefix("onebot:")
        # raw().attrs preserves custom JSON/XML without reparsing escaped markup.
        data = dict(element.raw().attrs)
        if tag == "text":
            data["text"] = element.text
        elif tag in ("img", "image"):
            tag, data = "image", {"url": element.src}
        elif tag in ("a", "link"):
            tag = "link"
            data["url"] = getattr(element, "href", data.get("href", ""))
        children = element_segments(element.children) if element.children else []
        result.append({"type": tag, "data": data, "children": children})
    return result


class ComponentParser:
    def __init__(self, get_forward: Callable[[str], Awaitable[dict]] | None = None):
        self.get_forward = get_forward
        self.output = ParsedMessage()
        self.active: set[str] = set()

    def _card_fields(self, value, path: str, *, depth: int = 0, seen: set[str] | None = None):
        if self.output.truncated:
            return
        if depth > 6:
            self.output.text('[卡片元数据嵌套过深，该部分未展开]\n')
            return
        seen = seen if seen is not None else set()
        if isinstance(value, list):
            for index, child in enumerate(value[:128], 1):
                self._card_fields(child, f"{path}.{index}", depth=depth + 1, seen=seen)
            if len(value) > 128:
                self.output.text('[卡片元数据列表超过128项，其余未展开]\n')
            return
        if not isinstance(value, dict):
            return
        for index, (key, item) in enumerate(value.items(), 1):
            field_name = key.lower()
            # Never dump tokens, host account details, app config, or arbitrary JSON.
            if field_name in _TEXT_FIELDS and isinstance(item, str) and item.strip():
                label = f"{_TEXT_FIELDS[field_name]}：{item.strip()}\n"
                if label not in seen:
                    seen.add(label)
                    self.output.text(label)
            elif field_name in _LINK_FIELDS:
                url = readable_url(item)
                if url and url not in seen:
                    seen.add(url)
                    self.output.text(f"{_LINK_FIELDS[field_name]}：{url}\n")
            elif field_name in _IMAGE_FIELDS:
                url = readable_url(item)
                if url and url not in seen:
                    seen.add(url)
                    self.output.text(f"封面/图片链接：{url}\n")
                    self.output.image(url, f"{path}.image_{index}")
            elif field_name not in {"config", "extra", "host", "token", "secret", "password"}:
                if isinstance(item, (dict, list)):
                    self._card_fields(item, f"{path}.{index}", depth=depth + 1, seen=seen)

    def _json(self, raw, path: str):
        self.output.text(f"\n【分享组件 {path}】\n")
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_COMPONENT_BYTES:
            self.output.text("[卡片数据为空或超过256 KiB，未展开]\n")
            return
        try:
            card = json.loads(raw)
        except (ValueError, RecursionError):
            self.output.text("[卡片 JSON 格式无效，未展开]\n")
            return
        if not isinstance(card, dict):
            self.output.text("[卡片结构无效，未展开]\n")
            return
        app = card.get("app", "")
        kind = ("音乐分享" if app == "com.tencent.music.lua" else
                "小程序" if isinstance(app, str) and "miniapp" in app else "分享卡片")
        if kind == '音乐分享' and isinstance(card.get('meta'), dict):
            music = card['meta'].get('music')
            if isinstance(music, dict) and isinstance(music.get('desc'), str):
                music['artist'] = music.pop('desc')
        self.output.text(f"类型：{kind}\n")
        before = len(self.output.parts)
        self._card_fields(card, path)
        if before == len(self.output.parts):
            self.output.text("[未发现可读标题、描述、公开链接或图片；不执行小程序]\n")
        self.output.text("【分享组件结束】\n")

    def _xml(self, raw, path: str):
        self.output.text(f"\n【XML 分享组件 {path}】\n")
        if (not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_COMPONENT_BYTES
                or re.search(r"<!\s*(?:DOCTYPE|ENTITY)", raw, re.I)):
            self.output.text("[XML 过大或包含不允许的实体声明，未展开]\n")
            return
        try:
            root = ElementTree.fromstring(raw)
        except (ElementTree.ParseError, ValueError, RecursionError):
            self.output.text("[XML 格式无效，未展开]\n")
            return
        seen: set[str] = set()
        for index, node in enumerate(root.iter(), 1):
            if index > 512:
                self.output.text("[XML 节点超过512个，剩余组件内容未展开]\n")
                break
            if node.tag.lower() in ('image', 'img', 'picture'):
                self._card_fields({'preview': node.get('src') or node.get('url') or node.get('cover')},
                                  f"{path}.{index}", seen=seen)
            self._card_fields(node.attrib, f"{path}.{index}", seen=seen)
            if node.tag.lower() in _TEXT_FIELDS and node.text:
                self._card_fields({node.tag: node.text}, f"{path}.{index}", seen=seen)
        self.output.text("【XML 分享组件结束】\n")

    async def _forward(self, forward_id: str, path: str, depth: int):
        if depth >= MAX_FORWARD_DEPTH or forward_id in self.active:
            self.output.text(f"[聊天记录 {path} 嵌套过深或循环引用，未展开]\n")
            return
        if not self.get_forward or not forward_id or len(forward_id) > 256:
            self.output.text(f"[聊天记录 {path} 无可用读取接口或标识]\n")
            return
        self.output.text(f"\n【聊天记录 {path} 开始；按原始顺序】\n")
        self.active.add(forward_id)
        try:
            try:
                response = await self.get_forward(forward_id)
            except Exception:
                self.output.text("[聊天记录读取失败，未提供内部内容]\n")
                return
            messages = response.get("messages") if isinstance(response, dict) else None
            if not isinstance(messages, list):
                self.output.text("[聊天记录返回格式无效，未展开]\n")
                return
            for index, message in enumerate(messages, 1):
                if self.output.truncated:
                    break
                if self.output.message_count >= MAX_FORWARD_MESSAGES:
                    self.output.stop("展开消息数量达到2000条安全上限")
                    break
                self.output.message_count += 1
                address = f"{path}.msg_{index:04d}"
                if not isinstance(message, dict):
                    self.output.text(f"【消息 {address}】[消息格式无效]\n")
                    continue
                sender = message.get("sender") or {}
                sender = sender if isinstance(sender, dict) else {}
                name = sender.get("nickname") or message.get("name") or "未知发送者"
                uid = sender.get("user_id", message.get("uin", "未知"))
                stamp = message.get("time")
                try:
                    time_text = datetime.fromtimestamp(float(stamp), timezone.utc).isoformat()
                except (ValueError, TypeError, OverflowError, OSError):
                    time_text = "未知时间"
                self.output.text(f"\n【消息 {address}】\n发送者：{str(name)[:200]}（{str(uid)[:100]}）\n时间：{time_text}\n")
                await self.parse(message.get("message", message.get("content", [])), address, depth + 1)
            self.output.text(f"\n【聊天记录 {path} 结束】\n")
        finally:
            self.active.remove(forward_id)

    async def parse(self, segments, path: str = "input", depth: int = 0) -> ParsedMessage:
        if isinstance(segments, str):
            self.output.text(segments)
            return self.output
        if not isinstance(segments, list):
            self.output.text("[消息结构无法解析]\n")
            return self.output
        for index, segment in enumerate(segments, 1):
            if self.output.truncated:
                break
            address = f"{path}.part_{index:04d}"
            if not isinstance(segment, dict):
                self.output.text("[消息段结构无效]\n")
                continue
            kind = str(segment.get("type", "unknown")).removeprefix("onebot:")
            data = segment.get("data", {})
            data = data if isinstance(data, dict) else {}
            if kind == "text":
                self.output.text(str(data.get("text", "")))
            elif kind == "image":
                url = data.get("url") or data.get("src") or data.get("file", "")
                self.output.image(str(url), address + ".image")
            elif kind == "json":
                self._json(data.get("data"), address)
            elif kind == "xml":
                self._xml(data.get("data"), address)
            elif kind == "forward":
                await self._forward(str(data.get("id", "")), address, depth)
            elif kind == 'node' and depth < MAX_FORWARD_DEPTH:
                self.output.text(f"\n【转发节点 {address}】发送者：{str(data.get('name', '未知'))[:200]}\n")
                await self.parse(data.get('content', []), address, depth + 1)
            elif kind in ("quote", "message") and segment.get("children"):
                if depth >= MAX_FORWARD_DEPTH:
                    self.output.text("[嵌套消息过深，未展开]\n")
                else:
                    self.output.text(f"\n【引用/消息 {address}】\n")
                    await self.parse(segment["children"], address, depth + 1)
            elif kind in ("reply", "quote"):
                self.output.text(f"[回复消息 {str(data.get('id', '未知'))[:100]}；未自动追溯]\n")
            elif kind in ("at", "author"):
                self.output.text(f"[{kind}：{str(data.get('name') or data.get('id', '未知'))[:200]}]\n")
            else:
                self.output.text(f"\n【组件 {kind[:80]} {address}】\n")
                self._card_fields(data, address)
                if segment.get("children") and depth < MAX_FORWARD_DEPTH:
                    await self.parse(segment["children"], address, depth + 1)
                if kind in ("video", "audio", "record", "file"):
                    self.output.text("[仅保留元数据和公开链接，未下载或解码此附件]\n")
        return self.output


async def parse_input(session, chain) -> ParsedMessage:
    """Resolve only the triggering quote and forward records, inside the owned task."""
    async def get_forward(forward_id):
        async with asyncio.timeout(20):
            return await session.account.protocol.internal('get_forward_msg', message_id=forward_id)

    parser = ComponentParser(get_forward)
    quote = session.event.quote
    quoted = list(quote.children) if quote is not None else []
    if not quoted and session.reply is not None:
        quoted = list(session.reply.origin.message)
    if not quoted and quote is not None and quote.id:
        try:
            async with asyncio.timeout(15):
                quoted = list((await session.message_get(str(quote.id))).message)
        except Exception:
            parser.output.text('[被回复消息读取失败，未提供其内容]\n')
    segments = element_segments(chain)
    quoted_segments = element_segments(quoted)
    simple_types = {'text', 'image', 'at', 'author'}
    if all(seg['type'] in simple_types for seg in [*quoted_segments, *segments]):
        from hyw_frontier.image_input import ImageInputError, MAX_IMAGES
        if sum(seg['type'] == 'image' for seg in [*quoted_segments, *segments]) > MAX_IMAGES:
            raise ImageInputError('普通消息每次最多4张图片；合并转发记录和分享组件使用独立容量预算。')
    if quoted_segments:
        parser.output.text('【被回复消息】\n')
        await parser.parse(quoted_segments, 'reply')
    if any(seg['type'] != 'text' or str(seg['data'].get('text', '')).strip() for seg in segments):
        parser.output.text('\n【当前消息内容】\n')
        await parser.parse(segments, 'current')
    return parser.output
