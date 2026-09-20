# 独立库调用

`hyw_frontier` 负责问答与工具编排；`md2png` 是独立绘图库。测试页面仅供源码开发使用，不进入 `hyw_frontier` wheel。

## 安装本地构建包

```sh
uv build --project md2png --out-dir dist
uv build --out-dir dist
pip install dist/md2png-0.1.0-py3-none-any.whl dist/hyw_frontier-0.1.0-py3-none-any.whl
```

模型接入使用 `pydantic-ai-slim[openai]`，仅使用模型层，不使用其 Agent 框架；模型通信和回答协议解析均不需要 Node/npm。默认覆盖 DeepSeek、OpenAI 与 OpenAI 兼容端点。Anthropic、Google 原生接口可按需安装 `anthropic` / `google` extra，不将全部供应商 SDK 装给每个使用者。

使用字符串/内置配置时，DeepSeek 固定走 `https://api.deepseek.com/responses`，OpenAI 默认 Responses；通用兼容服务使用 `provider="openai-compatible"` 并明确 `base_url`，`api="chat"` 或 `"responses"`。库可传 `api_key=`（建议从环境变量读取，不写死），也可用凭据目录的 `models.json` 配置非秘密端点及 `api_key_env`，格式见 [README](../README.md)。DeepSeek 不允许切换到 Chat，也不自动回退模型。其 Responses 无服务端会话，库每轮重放完整历史，不使用 `previous_response_id`。

模型与搜索凭据仍使用独立配置目录（默认 `~/.hyw-frontier`，或指定 `home=Path(...)`）和环境变量，兼容原 `auth.json` 中的 API Key。旧 OAuth 凭据保留但不使用；`openai-codex` OAuth 不属于当前支持路径。不读取 Pi 的全局提示词、会话或工具。也可通过 `backend=` 传入提供 `home` 和同步 `call(request)` 的调用方自有模型对象，它自行负责连接、超时与取消，不能同时传入 `home`、`api`、`base_url` 或 `api_key`。

使用模型 ID 字符串或本地调试页面时，最大输出额度自动按模型容量设置，无需用户配置。DeepSeek V4 系列使用官方公布的 384,000 tokens（思考与正文共用），不再统一限制为 8,192。其他模型优先读取提供商容量元数据；原生提供商未返回容量时查询 Models.dev 的对应提供商目录（只下载公开目录，不发送凭据或对话；进程内缓存一小时）。兼容端点只信任其自身元数据，不根据同名模型猜测容量；无法确定上限时明确报错，不静默使用较小默认值。直接注入的外部模型实例仍遵循下述设置保留约定。

## 直接传入 Pydantic AI 模型实例

`model=` 同时接受模型 ID 字符串和 Pydantic AI 的 `Model` **实例**，不接受类本身，也不需要 hyw 专用包装类。把端点、模型和思考强度配置在实例上：

```python
import asyncio
import os
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from hyw_frontier import answer

async def main():
    # 显式管理共享客户端；禁用 SDK 内部重试便于逐轮理解请求次数。
    async with AsyncOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
        max_retries=0,
    ) as client:
        llm = OpenAIResponsesModel(
            "deepseek-flash",
            provider=DeepSeekProvider(openai_client=client),
            settings={"openai_reasoning_effort": "high", "openai_store": False},
        )
        first = await answer("用户的问题", model=llm)
        second = await answer("继续追问", model=llm, history=first.messages)
        print(second.text)

asyncio.run(main())
```

- 直接使用实例的 `settings`，不会覆盖其思考强度、token 上限、温度、SDK 超时或连接配置；不修改实例。上面的 `AsyncOpenAI` 仅用于显式控制连接生命周期，也可以使用你已有的 Pydantic AI 提供商实例。
- 不再同时传 `provider`、`api`、`base_url`、`api_key` 或 `backend`，冲突会明确报错。`home` 仍可指定**搜索工具**凭据目录，模型不读取 hyw 的模型凭据文件。
- 思考等级切换工具及相关提示词暂时停用，底层参数保留。`reasoning` 可显式提供三档映射，例如 `{"high": "max", "medium": "low", "low": "off"}`。必须恰好包含这三个键，DeepSeek 的值可选 `off/low/high/max`，Gemini 3.8 Flash 可选 `low/medium/high`，允许任意重复，不接受旧的单字符串。仅支持 `reasoning.json` 中已验证的模型。不传时，模型 ID 使用项目默认映射，注入的 `Model` 保留自己的设置；显式映射只覆盖单次请求的 effort，不修改实例。`reasoning_mode="auto"`（默认）暂时全程保持中档（DeepSeek 默认为 `low`，Gemini 默认为 `medium`）；设为 `"high"`、`"medium"` 或 `"low"` 可固定对应档位，仍使用完整三档映射。
- 支持流式文本和函数工具的 Pydantic AI 模型适配器可使用这一入口，包括其他供应商的原生模型。hyw 保留自己的系统提示词、工具注册表、历史和图片预算，不运行 Pydantic AI Agent。
- 模型请求在调用 `answer()` 的事件循环中执行，工具编排与绘图仍在工作线程/隔离进程。共享实例应在**同一个事件循环**中使用，不要跨多个 `asyncio.run()` 复用已使用过的异步客户端。只要模型/客户端本身支持并发，同一实例可服务多个并发 `answer()`。
- hyw 不进入或退出外部模型的上下文，不关闭其客户端。取消只取消并等待本次请求收尾，不取消使用同一客户端的其他问答；`timeout` 仍作为本次单轮请求的外层时间上限，不改写实例的 SDK 设置。
- 外部实例自己的重试、路由、协议和服务端存储策略由调用方决定，hyw 不替你更改。模型费用按可见返回用量计算，无法枚举外部 SDK 的隐藏重试；不要将返回的模型轮次数视为这种情况下的全部 HTTP 尝试次数。
- 本地调试服务也按此模式运行：网页选项先构造成任务自有的原生 `Model`（在首次模型请求时加载凭据），之后从实例的设置执行。服务关闭自己创建的客户端，但保留既有会话、日志和结果图规则。`/api/config` 的 `transport.model.input` 为 `model_instance`，流事件的 `model_input` 为 `instance`。

## 一个入口、一个发送回调

```python
import asyncio
from pathlib import Path
from hyw_frontier import answer

async def main():
    result = await answer("用户的问题", search_provider="parallel", search_mode="fast")
    if result.kind == "image":
        Path("answer.png").write_bytes(result.png)
    else:
        print(result.display_text)

asyncio.run(main())
```

`search_provider` 支持 `"parallel"`（默认）、`"jina"` 和 `"ddgs"`。Parallel 的 `search_mode` 可选 `"turbo"`（默认）、`"fast"`、`"basic"`、`"advanced"`；Jina/DDGS 不使用该模式。网页搜索按所选服务执行，所有搜索服务均开放 `search_images`：DDGS 模式使用免密钥的 `ddgs>=9.16,<10` 进行网页和图片搜索，自动选择引擎，每次查询最多10条；其他模式图片搜索使用 `https://svip.jina.ai/` 和原有 Jina 凭据，缺少该凭据时不影响 Parallel 网页搜索。选择 Jina 时网页搜索也使用该 SVIP 端点；Reader 始终使用匿名 Jina 接口，默认发送 `X-Engine: browser` 强制浏览器渲染、`X-Respond-With: markdown` 返回完整页面 Markdown 正文（包括以图搜图的 Reader 调用），无须 API Key；调试服务 `/api/config` 的 `reader.engine` 为 `browser`、`reader.format` 为 `markdown`。图片搜索沿用下述图片下载与审阅预算。

DDGS 示例：`await answer("富士山的介绍与图片", search_provider="ddgs")`。无需配置 Jina/Parallel 密钥；需要代理时可设置上游支持的 `DDGS_PROXY`。DDGS 网页工具支持 `location`（映射为地区默认语言区域，省略为 `us-en`）及 `timelimit: d/w/m/y`（一天／一周／一个月／一年），不接受 `after_date`；其他服务仍使用 `after_date`。引擎对地区和时间筛选支持不同，结果需核对来源。搜索成功、空结果及失败均在本任务内缓存，DDGS 自身可能尝试多个引擎，费用记录标为无 API 费用。

`send` 是可选回调参数，默认 `print`。它连接现有的 **`send_process_intro` 工具**，不是绘图进度回调。模型成功调用工具时发送其原话；模型没调用，不会伪造临时消息。最终回复返回给调用方自行发送，不经过 `send`。

```python
async def send_temporary(text: str):
    await bot.send_message(text)

result = await answer("用户的问题", send=send_temporary, language="中文")
if result.kind == "text":
    await bot.send_message(result.display_text)
else:
    await bot.send_image(result.png)
```

- `language: str = "中文"` 设置最终回复与过程介绍的优先语言，例如 `language="英文"`、`language="日语"`；翻译、用户指定其他语言或任务本身的语言需求可覆盖这一偏好。语言名称须为1–100字符的非空单行字符串。默认提示词的 `{{language}}` 和日期、时间占位符一起注入；自定义 `system_prompt` 可使用同一占位符，没有该占位符则保留自定义提示词自己的语言规则。也可用 `load_prompt(language="英文")` 提前生成提示词，供多轮复用。
- 同步函数（如 `print`、`list.append`）与异步函数都支持，在调用方事件循环线程执行。
- 过程介绍必须与有效的其他工具同轮调用，通常每次提问一次；读取文章前尚未告知读取计划时，可与 `jina_read_url` 同轮额外补充一次，先发送再读取。已说明读取计划则不重复。
- 发送抛出异常时，工具返回 `intro_delivery_failed`，不谎报成功、不自动重发（异常前可能已送达），继续处理最终回答。工具结果保存在 `result.messages` 中。
- 默认工具函数的调用发生在模型完成工具调用响应后，无需等待搜索完成。
- 如需静默，传 `send=lambda text: None`。不要传 `None`。
- `result.kind` 是 `"text"` 或 `"image"`。协议解析为纯文本时直接返回 `display_text`，`png=None`、`render_ms=0`，不创建绘图 worker；Markdown 回复才生成 PNG `bytes`。不按字数硬切换类型。
- `result.text` 保留模型原文（兼容既有调用），`display_text` 是去掉协议标签及评分后的展示正文；Markdown 正文首个标题前的前置内容会删除，代码、引用块内的标题不作为截断点，无标题的回复不截断。历史和原始诊断不修改。
- 库不启动 HTTP 服务，不持久保存问答日志或图片；绘图使用的私有临时文件在请求后清理。结果还包含 `messages`、`answer_ms`、`render_ms`、`truncated`、绘图 `diagnostics`、`links` 和 `costs`。纯文本的 `links` 使用相同的离线链接识别，不依赖绘图。
- 多轮由调用者持有 `result.messages`，下一次传入 `history=`；库会复制而非修改输入历史。
- `timeout` 是模型单次调用/发送回调的超时，不是整个多轮任务的总时限；`max_rounds` 默认30。内置绘图进程有独立45秒上限。单次 `answer()` 自己创建并关闭绘图 worker，不留全局后台进程；测试前端持有同一个 `CardRenderer`，预热后跨请求复用字体/解析器，不缓存用户回答。
- `asyncio` 取消会取消 Python 模型请求并通知绘图进程，等待连接、线程和进程清理。hyw 创建的 SDK 客户端自动重试关闭，外部模型的重试策略保持原样。同步自定义回调不应长时间阻塞事件循环。

## 诊断事件回调

可选 `on_event=callback` 接收模型每轮响应、工具调用及结果、搜索查询、图片下载/压缩、最终正文和渲染事件。回调必须是**同步、线程安全**函数，在工作线程执行；不要传异步函数或阻塞事件循环。事件深拷贝后交给观察者，观察者抛错会停用后续诊断，不中断问答。简单自定义 backend 不会因开启观察者而被强制切换到流式协议。

这不同于 `send`：`send` 仍只承接模型的过程介绍，可为同步或异步函数，并在调用方事件循环执行。不要把所有诊断事件当作聊天消息发送。

原始事件可能包含用户输入、模型文本、工具材料和附件。库不会自动持久化事件；调用方负责脱敏、大小限制、权限和保留期限。Entari 插件已提供独立的私有请求日志。

## 链接与本次费用

`result.links` 是 `tuple[str, ...]`，复用 PNG 来源卡的链接识别：仅最终回答中的 HTTP(S) 链接，按首次出现顺序去重，包括正文链接、自动识别的裸网址和图片链接；不混入未引用的搜索结果。Markdown 模式下不识别代码块/行内代码中的网址，不把评分面板当作正文。保留 URL 的路径、查询和片段，不联网验证链接是否有效。链接只返回，不通过 `send` 额外发消息。

来源卡标题左侧的网站图标由任务在搜索结果/模型流式正文出现完整域名时提前下载，不由渲染器联网获取。每任务最多32个站点、4路并发、每站点10秒后台防挂上限（不是开绘前等待时间）；同站点复用（失败不重试），图标隔离解码为64×64 PNG、最多24KiB。最终回答完成即冻结就绪快照并进入渲染，缺图用本地页面图标；未完成下载在后台停止，开绘前不等下载完成、进程退出或线程 join，渲染后 release 再完成清理。任务结束/异常/取消时回收线程、下载子进程、图标缓存和渲染临时文件；图标不写入 `messages` 或持久缓存。自定义非流式 `backend` 仅能在工具来源或完整回答返回时触发预取，新站点可能来不及完成而使用兜底；内置后端开启内部流式读取，不改变 `send` 只发送过程介绍的约定。

```python
for index, url in enumerate(result.links, 1):
    print(f"{index}. {url}")

for item in result.costs.items:
    actual = "未知" if item.amount_usd is None else f"${item.amount_usd:.8f}"
    print(item.provider, item.operation, item.model or item.mode or "", item.requests, actual)
    if item.estimated_usd is not None:
        print(f"  SDK 估算（非实际扣款）：${item.estimated_usd:.8f}")

print("本次实际合计（USD）：", result.costs.total_usd)
```

- `costs.currency` 固定为 `"USD"`。`items` 按提供商、操作、模型/搜索模式和费用来源分项；搜索项的 `requests` 是实际请求尝试次数，不是工具调用次数；模型项统计本次可见模型轮次（外部客户端内部重试不可枚举）。相同请求命中任务内缓存（包括失败缓存）不重复计费；一批五条查询若均未命中缓存，则分别计入五次。
- 只统计本次新增模型轮次与搜索请求，不重复累计传入 `history` 中的费用。模型本轮实际重发历史产生的 token 消耗仍属于本次费用。
- `amount_usd` 表示已知美元费用；缺少可靠金额时为 `None`，不是零。`source` 说明依据。当前 DeepSeek 官方响应提供 token 用量，没有直接的美元扣款字段；SDK 的 `usage.cost` 仅作为 `estimated_usd`，标记 `sdk_catalog_estimate`。新接入使用依赖自带的价格目录，不启动后台价格更新；内测模型没有匹配价格时估算也为 `None`，**不会沿用其他模型价格，不将估算当作实际账单**。
- Parallel 当前 Search 响应的 `usage` 是 SKU 名称和次数；Jina 的 token 用量也不能直接当美元。两种搜索费用均标为未知，不硬编码价格、不用账号余额差推算本次费用。匿名 Reader 不使用付费凭据，记 `$0`，来源为 `anonymous_free_tier`。发送过程介绍、下载/压缩图片和本地渲染无独立付费 API，因此不计入 API 费用表；本机算力、网络和订阅费不在统计范围内。
- `CostItem.usage` 是不可变的 `(用量名称, 数量)` 元组序列，保留接口提供的 token 或 SKU 数量。自定义后端可在助手消息 `usage.cost_usd` 中提供非负有限数值，明确表示实际美元费用；`usage.cost.total` 仍只按估算处理。
- `total_usd` 仅在所有分项实际费用已知时给出，否则为 `None`，`complete=False`；`known_total_usd` 是已知部分的小计，不是总账单。`estimated_total_usd` 仅在每项有实际值或估值时给出，任一项未知则为 `None`。

## 调用结束后的资源清理

`answer()` 成功、异常或取消后，都会关闭调用自有的搜索/模型连接及渲染、图片下载子进程，清空搜索客户端的成功/失败缓存和费用暂存，释放下载图片、压缩图片副本及 Agent 上下文引用，删除本次私有渲染目录。异常保留类型、消息和栈位置，但已退出的栈帧会清空局部引用，避免调用方保存异常时连带保留图片和上下文。

取消会等待工作线程退出；重复调用 `cancel()` 不会跳过清理。正在执行的搜索请求可能需要等其网络超时/线程收尾，下载子进程有独立硬超时。传入的 Pydantic AI 模型及其客户端、`backend` 和外部回调属于调用方，库不擅自关闭；自定义后端需自行管理其连接、超时和取消。

返回的 PNG 字节、链接、费用及独立复制的 `messages` 均保留可用；其中用于后续对话的压缩图片附件不会删除。清理不保证 Python 分配器立即将内存归还操作系统，也不是安全擦除内存。不会遍历删除凭据目录，不清理本地调试框架保留的会话、日志和结果图；调试服务仍按原有规则管理它们。

## 结构化聊天输入

`answer(question, message_content=blocks)` 接收按原顺序排列的 `{"type": "text", "text": "…"}` 与 `{"type": "image", "mimeType": "image/jpeg", "data": "Base64…"}` 内容块。图片前应放置绑定该图和所属消息的文字标识，不要分别排序文本和图片数组。`message_content` 与 `images` 互斥，聊天资料放在当前问题之前；不把这些资料伪造成助手历史或系统指令。

上限为文本 UTF-8（含当前问题）加图片解码后字节合计256 MiB、32768个块；单图仍校验格式及5 MiB上限，但不限制为4张，不占工具图片预算。超过时核心明确拒绝，不静默删除；聊天插件负责下载压缩、按原顺序截断、保留失败图片标识及提示。机器人在压缩前允许单张原图最多20 MiB（公开链接与内联图片一致）。普通 `images=`、网页粘贴接口的4张/10 MiB限制不变。256 MiB不是提供商的接收承诺，模型请求大小、图片数量、token上下文限制仍适用。

## 网页直接阅读

`answer(..., reader_engine="browser", max_reader_images=30)` 直接将网页 Markdown 和压缩图片交给主模型。`reader_engine="default"` 不传引擎覆盖头；`"browser"` 强制浏览器，两种引擎均返回 Markdown。本地测试页可切换引擎、调整单页图片上限，并展示读取、图片下载和压缩耗时。

`max_reader_images` 是每个 `jina_read_url` 页面尝试下载图片的上限，默认30，接受非负整数，0禁用新增网页图片。按现有候选发现顺序选取（原图链接优先，再按 Markdown 图片等顺序）；URL 去重后计数，下载失败或低分辨率过滤也占尝试额度，不补位。相同页面在本次请求中重复读取共享该上限；历史已有图片不删除，已记录的该页尝试数也占额度。超出上限的链接仍在完整 Markdown 中，不下载为附件。该限制不限制搜索或以图搜图结果数量。

核心 `answer()`、`Bridge.ask()`、`SearchAgent`、CLI `ask --max-reader-images`、网页 `/api/chat` 与 Entari 同名配置均支持。提示词位于 `hyw_frontier/prompts/`，用途与注入时机见 [提示词审计](prompt-audit.md)。

## 搜索图片

`reverse_image_search` 按需进行混合以图搜图：原图 `source_id`、成功裁剪返回的 `crop_id`（填入 `source_id`），或公开 HTTPS 图片 `url`；`source_id` 与 `url` 二选一，上传一次后并行通过两个匿名 Jina Reader 查询 Yandex、TinEye（Google Lens 因匿名 Reader 持续返回验证页，已从默认调用移除）。`sources` 始终保留两个引擎的名称、状态、完整 Reader 正文和 `match_ids`，一个失败不影响其他来源；`matches` 按相同图片 URL（无图片时按页面 URL）合并，保留全部 `engines`、来源关联及 `duplicate_count`。结果按引擎轮流排列，避免一个来源占满图片预算。不做视觉相似度去重，不把验证页当成空匹配。可选 `page` 仅控制 TinEye，Yandex 复用任务内缓存。图片沿用统一压缩、下载预算及取消机制，硬超时 5 秒（普通搜图 2.5 秒）；不下载 blob 缩略图，失败仍保留文字。只有调用才上传，同图链接跨历史消息复用到失效；图床单张上限 5 MiB，按北京时间每天 00:00 过期，午夜前最后 60 秒不接受新上传。

图床地址及上传密钥通过本机设置保存到应用 home 下的 `image-bridge.json`（默认 `~/.hyw-frontier/image-bridge.json`，权限 0600）。仓库仅包含通用 Worker 模板，使用 Cloudflare secret `UPLOAD_TOKEN`，不含个人部署地址、KV ID 或真实密钥。未配置时明确报错，无默认个人服务。`/api/config` 仅公开配置状态；图床设置读写需要本机页面鉴权，读取也不返回密钥，空密钥只在地址不变时保留旧值。公网图片 URL 必须传给搜索引擎并返回给模型；请勿把包含个人图片链接的本地日志或测试报告纳入公开仓库。

`answer(..., max_tool_images=600)` 可逐请求配置工具图片总预算，接受任意非负整数；`0` 禁用新增工具图片。有工具图片的历史仍需足够预算，不因传0静默删图。`Bridge.ask`、`SearchAgent`、CLI `ask --max-tool-images`、网页 `/api/chat` 参数和 Entari 同名配置均支持；每轮无额外总张数限制，Reader 单页仍受 `max_reader_images` 限制；最多20张同时下载，不随总预算改变。

默认 DeepSeek 视觉模型会从搜索/Reader 返回的实际图片链接中提取候选，按配置处理候选（Reader 单页默认最多尝试30张），受默认总共600张工具图片预算约束（同轮工具共享额度，失败占尝试预算，历史图片复用并计入总预算）；单张原图最多下载20 MiB、2.5秒硬超时、最多20张并发，失败跳过后继续处理后续候选；每批处理完成即释放原始下载字节。压缩为最长边1280、质量75的 JPEG，全程内存处理后随工具结果发送，单图最多256KiB。不增设压缩 HTTP 服务，不让模型访问本地文件。

Reader 正文中的图片链接不按张数截断，JSON 原始响应受 2 MiB 上限约束，超限整次报错。下载候选按“原图/下载链接 → Markdown 图片 → 裸图片 URL 或分享参数”顺序提取、按 URL 去重，不按清晰度排序。网页图片逐页准备，每页选入上限内的候选分批处理；多类搜索结果一起准备时，以图搜图候选优先3个名额，再与其他搜索图片共享剩余总预算。解码时拒绝短边小于64像素、总像素超过2500万或非 JPEG/PNG/WEBP/GIF 的图片；失败仍占总尝试预算且不自动重试，但不阻止后续已选候选继续处理。成功图片全部附给模型，`width`、`height` 是压缩后的尺寸，由模型按相关性和清晰度决定最终展示哪些。超过单页/总预算或下载/解码失败的图片，其正文链接仍保留，但没有图片附件，也不会自动排队补抓；最终渲染只使用已下载通过处理的图片。

普通配图和截图裁剪统一通过 `media_images` 返回图片信息；相邻的 `media_attachment` 标记将这些信息绑定到紧随其后的单张附件。

| 字段 | 用途 |
|---|---|
| `image_id` | 核对图片身份 |
| `display_url` | 内部展示标识，插图时原样复制 |
| `url` | 原图地址，普通配图保留此字段 |
| `source_url` | 真实来源网页，用于正文引用 |
| `status` | 仅 `ready` 的图片可供展示 |
| `width`、`height` | 实际发送和展示图片的像素尺寸 |

模型逐张审阅图片，在相关正文附近用独立段落的 `![图片说明](display_url)` 插图，图注直接使用 Markdown alt 文本。`display_url` 为 `hyw-media://image/…` 形式，仅匹配已提供的内存资源，不可作为网页引用或网络下载地址；引用依据使用真实的 `source_url`。PNG 渲染复用压缩字节，不再联网。旧对话的原图 URL 和截图裁剪片段 URL 仍可恢复及展示。

渲染前按最终答案的 Markdown 结构，仅选取实际展示的图片和来源站点图标；未使用的候选不占渲染资源额度。日志中的 `render_asset_selection` 区分候选数与最终使用数。`result.messages` 保留压缩附件供后续多轮复用，历史图片超过本次 `max_tool_images` 预算时需提高预算或新建对话。

搜索、Reader 配图、整页截图和截图裁剪共用总预算，每次截图或裁剪先预留一次额度，额度不足时不执行；用户上传、用户图片裁剪、聊天记录及组件图片另行计算。`jina_pageshot` 的整页附件仅供审阅，`pageshot_id` 仅在当前任务有效；裁剪图可随 `result.messages` 在后续对话中恢复展示。没有图片线索或下载失败不影响文字回答。

渲染失败仍抛出 `RenderError`，不会改发正文文字、截断回答或自动重试模型。`render_error` 事件和异常 diagnostics 提供具体错误码：`render_asset_limit`（图片/图标数量）、`render_asset_pixels`（解码总像素）、`render_canvas_limit`（画布高度/像素）、`render_width_limit`（无法排入宽度）、`render_text_limit`（正文大小）、`render_structure_limit`（节点/层级）、`render_formula_limit`（公式）、`render_file_limit`（PNG 大小）、`render_protocol_limit`（进程通信大小），以及 `render_timeout` / `render_failed`。现有安全限制保持不变，正常模型图片审阅仍使用完整候选池。

## 字体和 Windows

- 库入口默认 `FontSet.bundled()`：原样捆绑 Noto 系列字体（SIL OFL）及 BabelStone Han（原版 Arphic Public License），附许可证与来源说明。
- 捆绑字体约80MiB未压缩，包含中文、等宽、韩文、单色 Emoji 和扩展区汉字回退；运行时不下载、不扫描字体目录，不要求用户安装微软雅黑或苹方。正文、代码、公式可显示此前缺失的 `𠀀`、`𪚥`，但不保证全部 Unicode。HYw 图片遇到真正缺字会显示 Unicode 标签并返回诊断；来源、许可证及哈希见 `md2png/font_assets/NOTICE.md`（源码位于 `md2png/src/md2png/font_assets/`）。公式使用可选 `math` extra 的 Python 排版链路及 Ziamath 自带 STIX 数学字体，不依赖外部 TeX 或 JavaScript 程序。
- 可传 `fonts=FontSet.load(Path("fonts.json"))`。配置支持文件路径、TTC index、可变字体轴和倾斜；相对路径以 manifest 目录为准。
- HYw 绘图器在非 macOS 系统上默认使用捆绑字体；macOS 源码测试前端仍保持原系统字体配置。不同字体不承诺与原 Mac 截图像素一致。
- Windows 适配包括字体选择、UTF-8 文件读写、模型凭据锁、绘图子进程清理、worker 管道通信和内存统计。绘图管道使用有界读取线程，不用 Windows 不支持的匿名管道 selector；内存统计使用系统 API，不依赖 psutil。模型通信使用任务自有的 Python asyncio 循环/SDK 客户端，不再区分 Windows one-shot 与 POSIX Node worker。
- 运行时依赖由 pip 安装相应 wheel，不直接调用 Cargo；无匹配原生 wheel 的 Python/CPU 组合仍可能触发编译。因此只应宣称经过安装与运行验证的平台组合。
- 自动化测试与 CI 门禁已移除。分发包通过本文命令手动构建；构建成功不代表目标平台的实际运行已验证。

独立绘图库可以不使用模型 SDK 和 Node：

```python
from md2png import FontSet
from md2png.hyw import render

result = render({"markdown": "# 标题\n\n这里是正文。"}, font_set=FontSet.bundled(), scale=1)
result.image.save("answer.png")
```

## 开发测试前端

在源码目录继续运行 `.venv/bin/python -m hyw_frontier.cli serve`。`server.py`、`dev_reload.py` 和 `static/` 不进入库 wheel；后端回答协议位于 `hyw_frontier/render_protocol.py`。浏览器直接展示后端 PNG 或纯文本，不包含独立 Markdown 渲染或截图参考链路，无需 npm。调试服务的 `answer_ready` 和 `done` 带 `kind`、`display_text` 与原始 `text`；纯文本的 `done.image=null`、`render_ms=0`，不发送绘图事件，前端直接安全显示文字。

自动化测试、测试集、CI 门禁和安装包验证脚本已移除。需要验证安装包时，在独立环境安装构建的 wheel，从源码目录外直接使用本文的库 API 或绘图 API；问答调用会使用实际模型及搜索服务，绘图 API 可完全离线使用。

## 本地目录与凭据保护

`dev.entari/` 工作台保留在本机，但整个目录被 `.gitignore` 排除，不参与正式源码分发；其配置、聊天记录和日志不会被清理。正式插件的配置和使用说明见 [插件 README](../entari_plugin_hyw_frontier/README.md)。

API Key 优先放在环境变量或项目外的 `~/.hyw-frontier/`。项目及 `md2png/` 目录的 `.gitignore` 排除本地凭据目录、`auth.json`、`jina.json`、`parallel.json` 及备份、常用秘密文件、私钥、本地覆盖配置、请求日志和结果图。`.env.example` 只应包含无真实密钥的示例。

Git 忽略规则按路径工作，不识别文件内容，也不会移除已跟踪或已提交的秘密。不要在源码或说明中硬编码密钥；如果密钥曾提交或泄露，需要撤销并轮换，单改 `.gitignore` 不能补救历史泄露。

裁剪接力搜图：`crop_user_image` 返回 `crop_id`，对应实际返回的裁剪 JPEG 字节。将它传入 `reverse_image_search(source_id=...)` 后只上传裁剪图，不上传原图；裁剪本身不触发上传。裁剪编号可在同一请求及后续聊天中复用，直到出现新的含图用户消息；再次裁剪仍使用原图编号与原图坐标。
