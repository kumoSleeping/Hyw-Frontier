"""Entari chat commands backed by Hyw-Frontier's Python library."""
from arclet.alconna import Alconna, AllParam, Args, Arparma
from arclet.entari import (
    At,
    Cleanup,
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
    question = chain.extract_plain_text().strip()
    if question.lower() in ("reset", "clear", "重置", "清空"):
        await service.reset(session, key)
        return
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
        "支持引用分享卡片、小程序、音乐和合并转发：提取文字、链接、封面及内部图片，按原顺序绑定；原图最多20 MiB，压缩后合计256 MiB，超出截断。",
        "仅 /q 触发解析；不执行小程序、不播放音视频、不自动读取链接正文，不恢复群聊上下文或监听普通聊天。",
        "来源链接按机器人/频道/成员隔离，仅存内存；重启或过期后失效。",
    ]))


@listen(Cleanup)
async def cleanup():
    await service.close()
