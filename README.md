# Hyw Frontier

Hyw Frontier 是 Hyw 基于 DeepSeek V4.1 Flash（`deepseek-flash`）开发的下一代 Hyw 核心，提供无需浏览器的 PIL 快速原生 Markdown 渲染, 提供自动多处链路优化、压缩，以及下载超时、图片裁剪、搜索结果筛选等功能。

## 使用

Python 3.11+。在工作区执行 `uv sync` 安装依赖。以下示例使用环境变量 `DEEPSEEK_API_KEY`，搜索选择无需密钥的 DDGS；运行问答会调用实际模型与搜索服务。

```python
import asyncio
import base64
import os
from collections.abc import Awaitable, Callable
from functools import partial
from pathlib import Path
from queue import SimpleQueue

from openai import AsyncOpenAI
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from md2png import FontSet

from hyw_frontier import Answer, answer


# 高阶封装：注入模型与回调，预绑定配置，返回可重复调用的异步问答函数。
def create_answerer(
    model: Model,
    *,
    send: Callable[[str], object | Awaitable[object]],
    on_event: Callable[[dict], None] | None = None,
    search_provider: str = "ddgs",
) -> Callable[..., Awaitable[Answer]]:
    return partial(
        answer,
        model=model,
        send=send,
        on_event=on_event,
        search_provider=search_provider,   # "parallel" / "jina" / "ddgs"
        search_mode="turbo",               # 仅影响 Parallel 搜索
        turbo=False,                       # True 关闭图片搜索、裁剪与工具图片审阅
        language="中文",
        home=Path.home() / ".hyw-frontier", # 搜索凭据目录；模型凭据由注入实例管理
        fonts=FontSet.bundled(),            # 可换成 FontSet.load(Path("fonts.json"))
        system_prompt=None,                # 默认内置提示词；可传入自定义提示词正文
        max_rounds=30,
        timeout=90,                        # 单次模型请求/过程回调上限，不是整轮时限
    )


async def main(question: str, image_path: Path | None = None) -> Answer:
    # 过程回调可同步或异步；此处可替换成 bot.send_message 等发送函数。
    # 仅有效的 send_process_intro 工具调用会触发，不逐 token 推送或发送最终回答。
    async def send(text: str) -> None:
        print(text)

    # 诊断回调在工作线程执行：必须同步、线程安全且不阻塞。
    # 此处只收集事件类型，不保存可能含用户内容的完整事件。
    events: SimpleQueue[str] = SimpleQueue()

    def on_event(event: dict) -> None:
        events.put(event.get("type", "unknown"))

    # 可选图片输入：这里接收 PNG；其他格式须填写与实际字节一致的 MIME。
    images = None if image_path is None else [{
        "mimeType": "image/png",
        "data": base64.b64encode(image_path.read_bytes()).decode("ascii"),
    }]

    # 客户端注入：连接、认证和重试由调用者配置，并负责关闭。
    async with AsyncOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
        max_retries=0,
    ) as client:
        model = OpenAIResponsesModel(
            os.environ.get("HYW_MODEL", "deepseek-flash"),
            provider=DeepSeekProvider(openai_client=client),
            settings={"openai_reasoning_effort": "low", "openai_store": False},
        )
        ask = create_answerer(model, send=send, on_event=on_event)

        # 整轮截止时间由调用方控制；取消后仍会等待连接与渲染资源回收。
        async with asyncio.timeout(300):
            result = await ask(question, images=images, history=None)

        # 续接由调用者持有历史；在同一客户端上下文中按需调用：
        # result = await ask("补充说明其中的关键依据", history=result.messages)
        # partial 的预绑定参数也可按次覆盖：await ask("问题", turbo=True)

    # 最终回答由调用者投递：纯文本不绘图，Markdown 返回 PNG。
    if result.kind == "image" and result.png is not None:
        Path("answer.png").write_bytes(result.png)
    else:
        print(result.display_text)

    print("来源链接：", result.links)
    print("耗时（ms）：", result.answer_ms, result.render_ms)
    print("是否截断：", result.truncated, "渲染诊断：", result.diagnostics)
    print("费用（USD）：", result.costs.total_usd, "估算：", result.costs.estimated_total_usd)
    print("费用明细：", result.costs.items)  # 未知金额为 None，不是零
    print("诊断事件数：", events.qsize())
    # result.text 保留模型原文；display_text 是清理后的正文，也可作图片投递备份。
    # result.messages 是完整消息历史，由调用者决定是否保存或用于下一轮。
    return result


if __name__ == "__main__":
    asyncio.run(main("介绍 Hyw Frontier 这类检索问答系统的关键设计"))
    # 附图调用：asyncio.run(main("分析这张图片", Path("input.png")))
```

注入的 `Model` 决定提供商、协议和模型设置，不要同时传入 `provider`、`api`、`base_url`、`api_key` 或 `backend`。不传 `reasoning` 时保留实例设置；已验证的 DeepSeek 模型可显式传入三档映射，仅逐请求覆盖思考强度，不修改共享实例。共享客户端应在同一个事件循环中使用；Hyw 不关闭调用方注入的模型或客户端。示例模型为限时 ID，失效后需通过 `HYW_MODEL` 显式指定可用模型，不自动回退。

动态思考参数为 `reasoning={"high": "max", "medium": "low", "low": "off"}`，必须且只能包含高／中／低三个键，值可重复（例如全部 `"off"` 固定关闭，或只有两种实际强度）。不再接受单个字符串。每次提问从 `medium` 开始，模型通过 `set_reasoning` 切换后续轮次：特别简单用低档，特别困难、尤其图片与复杂关系交织时用高档。支持的模型 ID 默认使用上述映射；本地页面可独立设置三档，并选择“自动思考等级”或固定高／中／低。API 对应 `reasoning_mode="auto"`（默认），或 `"high"`、`"medium"`、`"low"`；固定档位不向模型开放切换工具，仍须传入完整三档映射。

也可以不注入实例，直接向 `answer()` 传入模型 ID 和连接参数；更底层的模型传输可通过 `backend` 注入。完整参数、返回字段及生命周期约定见 [库接口文档](docs/library.md)。

## 项目定义

Hyw Frontier 是 Python 原生的检索问答核心：负责提示词、模型工具循环、网页搜索与读取、图片审阅，以及最终回答的文本或 PNG 输出。

模型接入仅使用 **Pydantic AI Slim 的模型层**，不运行其 Agent 框架；绘图由独立的 **md2png / Pillow** 离线完成，不依赖 Node 或浏览器截图。核心库不持久保存会话，聊天平台由 [Entari 插件](entari_plugin_hyw_frontier/README.md) 接入。本地调试页面使用 `uv run hyw-frontier serve` 启动，地址为 `http://127.0.0.1:8767`。

## 修改搜索服务

**切换已有服务不需要改源码。** 修改示例中的 `search_provider`，或在本地网页、Entari 插件配置中选择同名选项：

| `search_provider` | 网页搜索 | 图片搜索 | 所需凭据 |
| --- | --- | --- | --- |
| `parallel` | Parallel，核心库默认 | Jina SVIP | `PARALLEL_API_KEY`；图片搜索另需 `JINA_API_KEY` |
| `jina` | Jina SVIP | Jina SVIP | `JINA_API_KEY` |
| `ddgs` | DDGS | DDGS | 无需搜索 API Key |

`search_mode` 仅对 Parallel 生效，可选 `turbo`、`fast`、`basic`、`advanced`；与关闭图片链路的 `turbo=True` 无关。无论选择哪个搜索服务，网页读取工具 `jina_read_url` 仍使用匿名 Jina Reader。

密钥放在环境变量，或 `home` 目录中的 `parallel.json` / `jina.json`（字段为 `api_key`），不要写入源码或 README。搜索凭据与模型凭据相互独立。

**接入新的搜索服务**：参考 `hyw_frontier/parallel.py`、`jina.py` 或 `ddgs.py` 实现客户端，在 `hyw_frontier/tools.py` 的 `SEARCH_PROVIDERS` 和 `ToolRuntime` 中注册、选择和释放资源；同步调整 `hyw_frontier/tools.json` 的工具参数及服务适用范围。若需在本地网页正确显示名称、模式和凭据提示，同步更新 `hyw_frontier/static/settings.js`。公共 `answer()` 当前没有任意搜索客户端注入参数，`model` / `backend` 注入只替换模型层，不替换搜索服务。
