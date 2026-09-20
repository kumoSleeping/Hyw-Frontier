# 模型提示词审计

范围：扫描 `hyw_frontier/*.py`、`hyw_frontier/tools.json`、`hyw_frontier/prompts/`、Entari 接入代码及 README/library 文档。主要模型行为指令统一放在 `hyw_frontier/prompts/`；所有文件均随核心库打包，不依赖本地前端。主提示词 `system.md` 保留使用者正在进行的修改。

## 模型可编辑指令清单

| 文件 | 收件模型 / 注入时机 | 变量与用途 |
|---|---|---|
| `system.md` | 主模型，每次提问 | `current_date`、`current_time`、`language`；既有检索、推理、选图与回答规则 |
| `image_budget.md` | 主模型，任务建立时追加 | `download_concurrency`=20、`max_tool_images`默认600、`max_reader_images`默认30；说明当前图片预算 |
| `round_limit.md` | 主模型，距离安全阈值还差两轮时 | 无变量；使用用户指定原文，要求收束并直接回答 |
| `reconsider.md` | 主模型，思考档位发生应重新审视的变化时 | 无变量；目前动态切档工具暂停，保留已有逻辑 |
| `media_notice.md` | 看到工具图片的模型，图片工具结果内 | 说明附件绑定、宽高、相关性和展示规则 |
| `image_only.md` | 主模型，用户只发送图片没有文字时 | 既有纯图片请求的默认问题，文本原样迁移 |
| `component_question.md` | 主模型，Entari 组件/聊天资料没有明确问题时 | 既有组件消息的默认问题，文本原样迁移 |

程序中的新自然语言行为指令通过 `prompt_files.read_prompt` 读取。`{{变量名}}` 由程序替换；JSON 数据、图片编号等通过结构化字段传递。`image_only.md` / `reconsider.md` 在模块加载时读取，本地开发自动重启会使修改生效；长期运行的外部进程需重新启动。

## 轮次提醒的精确定义

`warning_round = max(1, max_rounds - 2)`：上限30时第28轮，上限5时第3轮，上限1–3时从第1轮起。

在该轮模型请求前，将 `round_limit.md` 追加到本次系统提示词；第28轮注入后，第29、30轮继续保留同一条提醒，不重复追加。`round_limit_warning` 事件只发一次，前端实时审视显示原文，`effective_prompt` 记录实际提示词。原始 `system.md`、会话基础提示词和聊天历史不被改写，下一次提问重新计算轮次。

这是一条收束指令；保留现有的最后一轮禁止继续执行工具的硬性保护。模型提前正常回复时不额外制造轮次。测试覆盖1、2、3、5、30轮边界。

## 仍保留在代码中的内容及原因

- `tools.py`、`jina.py`、`image_crop.py`、`reverse_image.py` 等返回的错误/状态说明，是对应失败条件的工具结果；其中包含修正参数或不要把错误当证据的短说明，随工具协议保留，不是额外系统提示词。
- 原有工具 schema / 描述位于 `tools.json`，是与执行代码保持一致的工具协议定义。
- `user_image_original`、`media_attachment` 等是程序与模型共同使用的结构化绑定标记；`【用户当前请求】`、聊天消息边界、时间、发送者、下载失败占位等是来源资料标签。
- UI 文案、异常提示、服务器日志、图像排版标签不属于发送给模型的行为提示词，仍由各自模块管理。

网页正文和用户消息是每次请求的数据，不属于本地提示词文件。实际系统提示词通过 effective_prompt 事件记录，方便审视。

## 文档核对

- README / library 已说明直接阅读、单页默认30张可调整，以及 Jina 默认/浏览器选项。
- library 图片规则已更新为20并发、Reader 单页默认尝试30张、每轮无额外总张数限制、总尝试600。
- Entari 的“聊天记录图片最多10并发”属于另一条输入预处理路径，保持其原有说明，不误改成网页工具的20并发。
