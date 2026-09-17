# Reader 以图搜图实测对比 · 2026-09-17

## 结论

这张图上，Yandex 对“找到同一张图并返回图片”最有用，Google Lens `/upload` 对“找作品名称和上下文线索”最有用。TinEye 能命中同图，但只返回了一个信息不足的来源记录。当前项目已接入的是 TinEye；本次仅测试其他入口，没有把这些引擎自动加入模型工具。

> 开源版本已用占位域名替换个人部署地址，表中链接为格式示例。实际测试链接仅保存在本机私有报告中。

## 测试方式

- 输入：此前已上传的[同一张公网图片](https://YOUR-WORKER.example.com/images/eb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5)，本轮未重新上传。
- 图片由临时图床于北京时间 2026-09-18 00:00 到期删除；到期后下面的 Reader 链接可能无法重现。
- 使用项目现有匿名 Jina Reader POST 传入目标 URL；下表链接是对应的 `https://r.jina.ai/<目标 URL>` 便捷入口，并非声称逐条另做了 GET 测试。
- 新测 11 个引擎、12 个入口（Google 两种入口）；另复用已完成的 TinEye 基线。每个入口单次请求，无自动重试、登录或验证码处理。
- 耗时是本次 Reader 请求墙钟时间，包含网络与缓存影响；不含后续图片下载。单图单次结果不能代表长期稳定性或全库召回率。
- 成功以正文是否包含有效搜图结果为准，HTTP 成功不等于搜图成功。

## 实测表

| 引擎 / Reader 链接 | 耗时 | 判定 | 实际结果 | 对本项目的价值 |
|---|---:|---|---|---|
| [Yandex Images](https://r.jina.ai/https://yandex.com/images/search?url=https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5&rpt=imageview) | 8.82 秒 | 有效命中 | 找到同一格漫画的日文版，人工核对画面一致；返回 4 个站点条目（含重复 Pinterest 记录）及具体 Plurk 帖子、作者名线索。 | 最佳同图与图片回传候选；直链图片下载成功。 |
| [Google Lens upload](https://r.jina.ai/https://lens.google.com/upload?url=https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5) | 7.47 秒 | 有效线索 | 返回相关漫画标题、多个来源页面；抽查一张缩略图为同一漫画场景的另一格，也混入无关漫画。 | 标题、作品背景线索最丰富；需额外处理跳转链接和 blob 缩略图。 |
| [Google Lens uploadbyurl](https://r.jina.ai/https://lens.google.com/uploadbyurl?url=https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5) | 6.11 秒 | 访问受阻 | 返回 unusual traffic 验证页。 | 本入口本次不可用；不能与 /upload 混为一谈。 |
| [Bing Visual Search](https://r.jina.ai/https://www.bing.com/images/search?q=imgurl:https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5&view=detailv2&iss=sbi&FORM=IRSBIQ) | 4.35 秒 | 搜索失败 | 正文包含无法使用该链接、无法处理搜索；其余图片为无关推荐。 | 不能把返回大量图片当成搜图成功。 |
| [Sogou Images](https://r.jina.ai/https://pic.sogou.com/ris?query=https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5&flag=1&drag=0) | 4.89 秒 | 没有匹配 | 明确显示没有找到相关图片结果，其后是推荐图。 | 本图未得到有效线索。 |
| [IQDB](https://r.jina.ai/https://iqdb.org/?url=https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5) | 9.40 秒 | 没有匹配 | 确认读取了 1280×919 输入图；返回 No relevant matches。 | 本图未得到有效线索。 |
| [Baidu Images legacy URL](https://r.jina.ai/https://graph.baidu.com/details?isfromtusoupc=1&tn=pc&carousel=0&promotion_name=pc_image_shituindex&extUiData%5bisLogoShow%5d=1&image=https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5) | 17.60 秒 | 没有匹配 | 旧 URL 入口返回未找到相关结果。 | 只说明本次旧入口未成功，不代表交互式百度识图能力。 |
| [Lenso.ai](https://r.jina.ai/https://lenso.ai/en/search-by-url?url=https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5) | 6.42 秒 | 未取得结果 | Reader 仅取得 Cookie 同意与说明内容。 | 无法评估其真实匹配质量。 |
| [SauceNAO](https://r.jina.ai/https://saucenao.com/search.php?db=999&url=https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5) | 2.03 秒 | 访问受阻 | 返回安全验证页。 | 无法评估其真实匹配质量。 |
| [Ascii2d](https://r.jina.ai/https://ascii2d.net/search/url/https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5) | 2.43 秒 | 访问受阻 | 返回安全验证页。 | 无法评估其真实匹配质量。 |
| [360 Images URL](https://r.jina.ai/https://st.so.com/stu?imgurl=https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5) | 7.60 秒 | 搜索失败 | 返回服务器开小差错误提示。 | 本入口本次不可用。 |
| [trace.moe API](https://r.jina.ai/https://api.trace.moe/search?anilistInfo&url=https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5) | 1.24 秒 | 返回候选但不能证实出处 | 取得 JSON；最高候选为 REBORN 第 168 集，similarity 0.835113，属于动画候选。 | 未核验候选帧，不能当作本漫画出处；该服务定位为动画截图检索。 |
| [TinEye（已有基线）](https://r.jina.ai/https://tineye.com/api/v1/result_json/?page=1&url=https%3A%2F%2FYOUR-WORKER.example.com%2Fimages%2Feb26140ba409d4581166b1ecae7bbcd7ba31d37e7c5d5f3ca2feb1dd1a5bb4d5) | — | 命中 1 条 | 中文版本同图；仅有 Plurk 账号主页，Reader 读取该主页为 404。共 1 页、无额外不可用匹配。 | 图片回传已跑通，但无法仅据此确认作者和作品。 |

## 人工核对与接入差异

### Yandex

返回的日文版本已成功下载并人工核对：与用户图片人物、构图及背景一致，气泡文字和部分气泡布局不同。文件为 361,332 字节。搜索条目保留了“どじろー”的作者名线索及具体 Plurk 帖子；该帖子通过 Reader 打开仍为 404，所以只能称为作者线索，不能声称已核验原始发布者。

对接现有工具相对直接：结果正文同时包含来源链接、缩略图和公开 HTTPS 大图链接。本次仅验证直接下载与画面，不等于已经完成 Yandex 的模型工具适配。

### Google Lens

`/upload?url=` 返回了相关漫画系列标题以及 NGA、Ruliweb 等页面线索；同一输入的 `/uploadbyurl?url=` 则触发异常流量验证。因此应保留入口差异，不能笼统说“Google 能用”或“Google 不能用”。

本次核验的一个可下载缩略图属于同一漫画场景的另一格；其他结果也有无关漫画，需要模型继续核对，不能把全部视觉匹配当作同图。

当前正文中许多缩略图是 `blob:http://localhost/...`，现有后端下载器无法访问；另有一部分是正常 HTTPS 图片，已成功下载一张。来源链接多为 `lens.google.com/goto?url=...`，需要解析跳转。若接入，必须保留正文、过滤不可下载图片，并处理来源跳转。

### TinEye 与超时

TinEye 主要匹配同图及其变体，不自动给图片补充作品名、作者或剧情。排序、翻页可以组织已索引的命中，不能把本次只有一条的结果变成更完整的知识。Jina Reader 负责获取返回内容，也不能增加搜索引擎的索引。

现有 TinEye 工具已由本地 DeepSeek Flash 实际调用：上传、Reader 解析、图片下载压缩均成功，下一轮确认发送了 1 张匹配图片。测试在持续弱线索搜索后提前终止，没有取得最终出处结论。没有调用 Qwen。

现有 5 秒 / 2.5 秒差异是图片附件下载的硬超时，不是整次 Reader 搜索的总耗时。Google、Yandex 本次 Reader 搜索约 7–9 秒，不应套用 5 秒整次请求上限。

## 建议

将 Yandex 作为同图及可下载图片的优先候选，Google Lens `/upload` 用来补作品标题和来源上下文，TinEye 保留为补充。同一次工具调用可复用当前已上传图片，避免每轮自动上传；空匹配或验证页要明确返回，不能把推荐图包装成命中。本次结果支持进一步接入验证，尚不能保证长期免验证、稳定可用。

## 检索入口与能力参考

- [Search by Image：实际引擎入口配置](https://github.com/dessant/search-by-image/blob/main/src/utils/data.js)
- [Search by Image：引擎列表](https://github.com/dessant/search-by-image/wiki/Search-engines)
- [SearchJumper：百度与 360 URL 入口讨论](https://github.com/hoothin/SearchJumper/discussions/73)
- [TinEye：匹配原理](https://help.tineye.com/article/233-how-does-tineye-work)
- [TinEye：搜索结果教程](https://help.tineye.com/article/265-tineye-tutorial)
- [trace.moe：官方 API 项目](https://github.com/soruly/trace.moe-api)
