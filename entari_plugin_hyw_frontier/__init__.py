"""Entari chat commands backed by Hyw-Frontier's Python library."""
from arclet.alconna import Alconna, AllParam, Args, Arparma
from arclet.entari import (
    At,
    Cleanup,
    Element,
    Image,
    MessageChain,
    MessageCreatedEvent,
    Quote,
    Session,
    Text,
    collect_disposes,
    command,
    listen,
    metadata,
    plugin_config,
)
from arclet.entari.event.command import CommandReceive

from .config import Config
from .service import FrontierService

__version__ = "0.1.0"
__plugin__ = metadata(
    "hyw-frontier", author=[{"name": "kumo"}], version=__version__, config=Config,
    description="Python-native concurrent search answers with compressed image delivery",
)
conf = plugin_config(Config)
service = FrontierService(conf)
collect_disposes(service.close)
LINK_COMMANDS = tuple(dict.fromkeys((conf.link_command, "/link", "/qlink")))


def leading_content(content: MessageChain) -> MessageChain:
    result = content.copy()
    while result:
        first = result[0]
        if isinstance(first, (Quote, At)) or isinstance(first, Text) and not first.text.strip():
            result.pop(0)
        elif isinstance(first, Text):
            result[0] = Text(first.text.lstrip())
            break
        else:
            break
    return result


def quoted_elements(session: Session[MessageCreatedEvent]) -> list[Element]:
    """Content of the replied-to message; the adapter may or may not inline it."""
    quote = session.event.quote
    elements = list(quote.children) if quote is not None else []
    if not elements and session.reply is not None:
        elements = list(session.reply.origin.message)
    return elements


def quoted_images(session: Session[MessageCreatedEvent]) -> list[Image]:
    return [element for element in quoted_elements(session) if isinstance(element, Image)]


def quoted_text(session: Session[MessageCreatedEvent]) -> str:
    """Plain text of the replied-to message; authors, images and other blocks are skipped."""
    return MessageChain(quoted_elements(session)).extract_plain_text().strip()


@listen(CommandReceive)
async def strip_leading_mentions(content: MessageChain):
    cleaned = leading_content(content)
    text = cleaned.extract_plain_text().strip()
    # Leave other plugins' command inputs untouched.
    if any(text == c or text.startswith((c + " ", c + "\n", c + "\t"))
           for c in (conf.command, conf.stop_command, conf.help_command, *LINK_COMMANDS)):
        return cleaned


@command.on(Alconna(conf.command, Args["content;?", AllParam]))
async def ask(session: Session[MessageCreatedEvent], result: Arparma):
    key = service.scope(session)
    if key is None:
        return
    raw = result.all_matched_args.get("content")
    chain = raw if isinstance(raw, MessageChain) else MessageChain(raw or [])
    if not chain.get(Image):
        # entari strips the leading quote from command content, and OneBot carries the
        # replied-to image inside that quote, so read it back from the event itself.
        chain = MessageChain([*chain, *quoted_images(session)])
    question = chain.extract_plain_text().strip()
    if question.lower() in ("reset", "clear", "重置", "清空"):
        await service.reset(session, key)
        return
    quoted = quoted_text(session)
    if quoted:
        question = f"被回复消息：\n{quoted}\n\n用户当前请求：\n{question or '请结合上面的消息回答。'}"
    await service.submit(session, key, question, chain)


@command.on(Alconna(conf.stop_command))
async def stop(session: Session[MessageCreatedEvent]):
    if (key := service.scope(session)) is not None:
        await service.stop(session, key)


async def links(session: Session[MessageCreatedEvent]):
    if (key := service.scope(session)) is not None:
        quote = session.event.quote
        quoted_id = str(quote.id or '') if quote is not None else None
        if quoted_id is None and session.reply is not None:
            quoted_id = str(session.reply.origin.id or '')
        await service.links(session, key, quoted_id)


for link_command in LINK_COMMANDS:
    command.on(Alconna(link_command))(links)


@command.on(Alconna(conf.help_command))
async def help_command(session: Session[MessageCreatedEvent]):
    if service.scope(session) is None:
        return
    await service.send(session, "\n".join([
        f"{conf.command} <问题>：检索问答，支持最多4张图片；简单回答直接返回文字，Markdown 回答返回压缩 JPG。",
        f"{conf.command} reset：清空你的来源链接记录。",
        f"{conf.stop_command}：停止本人在当前频道的所有任务，等待资源收尾。",
        f"/link（或 {conf.link_command}）：回复一条回答获取其来源标题＋链接；不引用则取本人最近一次回答。",
        f"每个 Q 独立执行，同一人也可并发；全局最多 {conf.max_concurrent} 个任务，满额拒绝、不排队。",
        "不保存对话历史；回复消息仅携带该条消息的文字和图片，不自动恢复完整对话；不监听普通聊天。",
        "来源链接按机器人/频道/成员隔离，仅存内存；重启或过期后失效。",
    ]))


@listen(Cleanup)
async def cleanup():
    await service.close()
