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
- `reasoning` 可显式启用动态三档，例如 `{"high": "max", "medium": "low", "low": "off"}`。必须恰好包含这三个键，每个值可选 `off/low/high/max`，允许任意重复，不接受旧的单字符串。仅支持 `reasoning.json` 中已验证的 DeepSeek 模型；每次提问默认中档，`set_reasoning` 从下一模型轮次生效。不传时，模型 ID 使用项目默认映射，注入的 `Model` 保留自己的设置；显式映射只覆盖单次请求的 effort，不修改实例。`reasoning_mode="auto"` 为默认自动模式；设为 `"high"`、`"medium"` 或 `"low"` 可固定档位并禁用切换工具，仍使用完整三档映射。
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

`search_provider` 支持 `"parallel"`（默认）、`"jina"` 和 `"ddgs"`。Parallel 的 `search_mode` 可选 `"turbo"`（默认）、`"fast"`、`"basic"`、`"advanced"`；Jina/DDGS 不使用该模式。网页搜索按所选服务执行，默认主链路下所有搜索服务均开放 `search_images`：DDGS 模式使用免密钥的 `ddgs>=9.16,<10` 进行网页和图片搜索，自动选择引擎，每次查询最多10条；其他模式图片搜索使用 `https://svip.jina.ai/` 和原有 Jina 凭据，缺少该凭据时不影响 Parallel 网页搜索。选择 Jina 时网页搜索也使用该 SVIP 端点；Reader 始终使用匿名 Jina 标准接口。图片搜索沿用下述图片下载与审阅预算。

DDGS 示例：`await answer("富士山的介绍与图片", search_provider="ddgs")`。无需配置 Jina/Parallel 密钥；需要代理时可设置上游支持的 `DDGS_PROXY`。DDGS 网页工具支持 `location`（映射为地区默认语言区域，省略为 `us-en`）及 `timelimit: d/w/m/y`（一天／一周／一个月／一年），不接受 `after_date`；其他服务仍使用 `after_date`。引擎对地区和时间筛选支持不同，结果需核对来源。搜索成功、空结果及失败均在本任务内缓存，DDGS 自身可能尝试多个引擎，费用记录标为无 API 费用。

### Turbo 快速链路

```python
result = await answer("你的问题", turbo=True, search_provider="ddgs")
```

`turbo` 是严格的布尔参数，默认 `False`（主链路），与 Parallel 的 `search_mode="turbo"` **相互独立**，不改变搜索服务、模型、思考强度或轮次预算。`True` 时只开放 `web_search`、`jina_read_url`、`send_process_intro`，关闭 `search_images`、`crop_user_image` 以及工具图片的下载、压缩和审阅。用户原图仍可输入，已有历史不静默删除；Markdown 回答仍可渲染成 PNG，Turbo 不等于强制纯文本回复。

两条链路共用 `hyw_frontier/prompts/system.md`。图片相关段落或行内片段按如下方式标记；自定义 `system_prompt` 也支持：

```markdown
通用说明。
<!-- turbo:omit:start -->
仅主链路使用的图片说明。
<!-- turbo:omit:end -->
其他通用说明。
```

发给模型前，主链路保留标记间正文，Turbo 删除标记间正文；两者均移除所有 HTML 注释。标记不可嵌套，缺少配对或注释未闭合时报错；裁剪后提示词为空也报错，不回退默认提示词。请传原始 Markdown，不要把已去掉标记的主链路提示词再用于 Turbo。

本地页面可选择「回答模式」；HTTP `/api/chat` 接受 `"turbo": true`，缺省为主链路，日志记录该字段。`/api/config` 的 `answer_modes` 展示各链路的工具及处理后提示词。命令行支持 `ask --turbo`、`preview --turbo`、`tools --turbo`。Entari 配置同样支持 `turbo: true`，默认关闭。

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

## 搜索图片

默认 DeepSeek 视觉模型会从搜索/Reader 返回的实际图片链接中提取候选，每轮新增处理最多5张、总共20张工具图片（同轮工具共享额度，失败占尝试预算，历史图片复用并计入总预算）；单张下载1.5秒硬超时、最多4张并发，失败跳过。压缩为最长边1280、质量75的 JPEG，全程内存处理后随工具结果发送，单图最多256KiB。不增设压缩 HTTP 服务，不让模型访问本地文件。

模型通过 `media_images` 了解每张图的原链接、来源与附件顺序；每张附件紧邻的 `media_attachment` 文本标记再次绑定该图的 ID 与 URL，降低多图错配风险。图片下方说明直接来自该 URL 对应的 Markdown alt 文本，模型必须逐张核对，不能集中错配说明。最终在文章内部的相关正文附近，用独立段落的 `![图片说明](原图URL)` 选择已审阅图片；PNG渲染复用压缩字节，不再联网。`result.messages` 保留压缩图片附件供后续多轮复用，历史超过20张工具图片时需新建对话；这与用户上传图片上限分开计算。没有图片线索或下载失败不影响文字回答。

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
