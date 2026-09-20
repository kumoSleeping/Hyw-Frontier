# 以图搜图复测 · 2026-09-20

## 结论与处理

TinEye 接入可用，保留。Google Lens 的现有匿名 Reader 通道在本次三张图片及两次入口/读取模式对照中均返回验证页，已从默认调用移除；保留解析器供后续诊断。默认工具现在只并行请求 Yandex 和 TinEye。

Google Lens 的 `/upload?url=` 与 [Search by Image 当前源码](https://github.com/dessant/search-by-image/blob/main/src/utils/data.js)一致，没有发现入口拼写错误。验证页正文明确包含 `Our systems have detected unusual traffic`，并非解析器漏掉搜索卡片。此结论针对当前匿名 Reader 通道，不代表 Google Lens 的交互式产品永久不可用。

## 方法与实测结果

使用本项目 `JinaClient` 和 `ReverseImageSearch`，匿名 Reader、`X-Engine: browser`、Markdown 返回格式，无模型推断或模拟响应。漫画、摄影样本先下载原始图片字节，通过项目图床上传，再使用完整 `search(source_id)` 路径。梗图的本机下载返回 HTTP 403，改以该公开图片 URL 直接调用同一 `_read_engine` 路径，单独验证引擎；这一行不计为图床上传成功。各输入/入口请求一次，没有自动重试。

| 样本 | Yandex 解析候选数 | Google Lens | TinEye 原始 num_matches | TinEye 本页解析来源数 |
|---|---:|---|---:|---:|
| [漫画](https://images.plurk.com/7v3DnVYpcGZOMgNtY7pS4S.jpg)，1024×734 | 4 | blocked | 1 | 1 |
| [TinEye 教程摄影样本](https://d33v4339jhl8k0.cloudfront.net/docs/assets/5707f24490336008d09da66f/images/5c8fef730428633d2cf3bc0b/file-9VCDk3IIxJ.jpg)，1350×732 | 4 | blocked | 1778 | 10 |
| [Drake 梗图模板](https://imgflip.com/s/meme/Drake-Hotline-Bling.jpg)，公开 URL 直查 | 4 | blocked | 13349 | 45 |

这些数值是搜索引擎计数和解析出的候选来源，未逐条人工确认同图；不能当作独立证据数量。TinEye 的 `num_matches` 是总匹配数，本页来源数是当前页 backlink 关联数，两者不应混用。摄影样本另有 46 条不可用匹配，`num_filtered_matches=1732`，当前页解析 10 条。

单个引擎耗时（秒，不含上传）：

| 样本 | Yandex | Google Lens | TinEye |
|---|---:|---:|---:|
| 漫画 | 7.46 | 5.05 | 7.00 |
| 摄影 | 8.28 | 3.73 | 6.25 |
| 梗图 | 6.12 | 4.26 | 14.39 |

Google Lens 对照均使用同一 Drake 公开图片 URL：

- `browser` Reader + `/uploadbyurl?url=`：blocked，3.61 秒。
- `default` Reader + `/upload?url=`：blocked，1.22 秒。

合计三张样本的标准通道与两种额外组合共五次 Lens 请求，均为验证页。移除 Lens 能减少无效请求和回答中的失败信息，但三引擎原本并行运行，不能把 Lens 耗时直接算作整次请求节省时间。

## 历史日志核查

先通过项目日志 CLI 查询关键词，再检查原始 `tool_end` 数据。没有定位到截图所示“水无月萤”这一条，因此没有将其他请求冒充截图的原始输入。

2026-09-17 的三引擎历史记录中：两次漫画检索是 Yandex 命中 4 条、Lens blocked、TinEye 命中 1 条；另一次请求的两个裁剪图均为 Yandex 命中 5 条、Lens blocked、TinEye `matches=[]` 且 `num_matches=0`。这两个零匹配响应同时包含输入图片尺寸和指纹，说明 TinEye 确实处理了图片，零结果不是本地解析后随意填入的默认值。

TinEye 官方说明其主要搜索[同图及修改版本](https://help.tineye.com/article/235-can-tineye-find-similar-images-does-tineye-do-facial-recognition)；[零匹配只说明其索引未找到该图](https://help.tineye.com/article/265-tineye-tutorial)，不能据此否定角色或作品的存在。

## 实现与验证

- 默认来源、并发数、工具描述、系统提示词、日志 provider 标记、使用文档均同步为 Yandex + TinEye。
- `/api/config` 的 `disabled_engines.google_lens` 标记为 `anonymous_reader_verification`。
- 提示词明确区分 `no_matches` 与 `blocked/error/unparsed`，要求仅描述实际调用引擎。
- 回归检查涵盖两来源并发、去重后来源关联、部分失败、输入上传复用、裁剪图、图片下载与分页。
- 全部 44 项测试通过；沿用原有项目调试服务启动配置重启，并通过 `/api/config` 确认两个活动引擎、并发数及新提示词已生效。

附带发现：以 JSON URL 交给当前图床导入的三张样本均返回 HTTP 400，尚未进入搜图引擎；另一次直接上传请求遭 Cloudflare HTTP 403。这些失败没有计为搜索引擎零结果。以上成功的完整链路使用图片字节上传，与用户粘贴图片路径一致。URL 导入问题需单独排查，不据此更换搜图引擎。

完整原始响应保存在本机 `~/.hyw-frontier/diagnostics/reverse-image-20260920/`，未将个人图床地址和临时图片 URL 写入仓库。
