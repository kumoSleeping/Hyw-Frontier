# entari_plugin_hyw_frontier

Entari 0.18.6 的 Hyw-Frontier 适配插件，直接调用 `hyw_frontier.answer()`，不依赖 Pi、Node 或本地 HTTP 问答服务。模型、搜索和绘图由核心库负责；插件负责命令、并发任务、来源记录、附件、日志与平台消息投递。

## 构建与安装

从工作区根目录构建，三个包应一起更新：

```sh
uv build --project md2png --wheel --out-dir dist
uv build --wheel --out-dir dist
uv build --project entari_plugin_hyw_frontier --wheel --out-dir dist
```

在 Entari 的 Python 环境安装这三个 wheel，然后在 Entari 配置中启用插件。无需安装本地调试网页或 Mashiro Web 适配器。

## 配置

```yaml
plugins:
  entari_plugin_hyw_frontier:
    command: /q
    stop_command: /qstop
    help_command: /qhelp
    link_command: /link
    provider: deepseek
    model: deepseek-flash
    reasoning_mode: auto  # 自动切换暂时停用，auto 保持中档；high/medium/low 固定档位
    reasoning:  # 三个键必须同时提供，值允许重复；替代旧的单字符串配置
      high: max
      medium: low
      low: "off"
    search_provider: jina
    timeout: 300
    request_timeout: 90
    max_concurrent: 2
    max_tool_images: 600  # 搜索/Reader 图片总预算；非负整数，0禁用新增工具图片
    reader_engine: browser  # Jina 强制浏览器模式；default 使用默认引擎
    max_reader_images: 30  # 单个 Reader 网页图片尝试上限；非负整数，0禁用新增网页图片
```

当前模型为限时 ID，失效后需显式配置可用模型，不会自动回退。示例关闭思考，不改变核心库的默认值。支持核心库的 `provider`、`model`、`api`、`base_url`、`language` 和搜索设置，完整参数见 `config.py`。

凭据默认读取 `~/.hyw-frontier/`，可用 `home` 指定独立目录；也支持 `DEEPSEEK_API_KEY`、`JINA_API_KEY` 等环境变量。`api_key_env` 只填写模型密钥的环境变量名，不填写密钥本身。生产应使用项目外的私有凭据目录，不把真实密钥提交进配置。

## 消息行为

- `/q 问题`：执行问答。过程消息仅来自模型有效调用 `send_process_intro`，没有固定开场白。文字型直接发送；图片型将库返回的 PNG 转为 JPEG，默认 quality=85、4:4:4、不缩小分辨率。
- 每个 `/q` 都是独立请求，同一人可同时提多个问题。已删除 `-s` / `--session` 模式，不保存或自动续接完整对话历史；回复消息会解析被回复那条消息的文字、图片、分享组件及合并转发内容，但不自动读取群聊历史。
- `/q reset`：清空本人的来源链接记录。有任务运行时先 `/qstop`，等待收尾后清空。
- `/qstop`：取消本人在当前机器人、频道下的所有任务，等待库清理连接、线程和渲染进程；不影响其他成员或频道。
- `/link`：回复本插件发出的回答，按消息回执 ID 返回该回答的来源标题与 URL；不引用时查询本人最近成功回答。兼容 `/qlink` 和配置的 `link_command`。
- `/qhelp`：查看帮助。

支持前置 @、引用文字和引用图片；普通消息最多4张附件。新增 `message_parser.py` 统一解析 JSON/XML 分享卡片、小程序、音乐、链接和合并转发：提取标题、描述、歌手、来源、公开内容/音频链接与封面；图片下载压缩后以相邻标识绑定所属消息，下载失败保留原标识，不让后面的图片错位。不执行小程序、不播放音视频，不把卡片摘要冒充网页正文；普通链接交给问答模型按既有 Reader 规则决定是否读取。

组件和聊天记录图片不占 `max_tool_images` 工具预算，也不受普通4张附件限制。按原顺序展开（嵌套原位展开、不按时间排序），单张原图（公开链接下载或内联图片）最多20 MiB，以文字 UTF-8 + 压缩图片二进制合计256 MiB为上限，预留当前请求和截断提示；超限停止整个后缀，不跳过大图挑小图。另有单卡片256 KiB、嵌套8层、2000条展开消息和16000个内容块的安全边界，触及边界会提示。压缩沿用最长边1280/JPEG质量75/单图256 KiB，动图取首帧；最多10张同时下载，失败以占位说明继续。模型提供商的请求大小、图片数量或上下文限制仍可能更低，并不保证256 MiB全部可发送。接受公开 HTTP(S) 或内联图片，拒绝本地路径及私网，复用核心库的下载、校验与压缩。图片编码或投递异常（含发送超时、整轮时限在图片阶段耗尽）只记录后台日志，不自动重发图片、不补发答案文字，也不发送任何失败或结果未确认提示；平台可能仍会稍后送达图片。

来源引用查询允许同机器人、同频道内其他成员的公开回答，不跨频道查询；无记录时明确提示，不误用最近一问。成功投递的文字、图片均记录回执；发送结果未确认时不记录为成功。索引仅在内存，受 TTL、条数和容量限制；重启、本人 reset 或容量淘汰后不可恢复。

默认全局同时2个任务，可通过 `max_concurrent` 调大；没有单人成员限制，满额直接拒绝、不排队。命令提交后立即返回，由插件持有后台任务直至完成或取消清理结束；卸载插件会取消并回收全部任务。

仅成功投递最终回答后更新来源记录；不引用的 `/link` 取最近完成投递的回答，与提问顺序无关。来源缓存默认 `source_ttl=3600` 秒、`max_source_records=1280` 条、`max_source_bytes=67108864` 字节；成员最近回答索引与消息回执索引合计受限，过期或超限时淘汰最旧记录，不影响新问题接收。旧配置 `max_sessions`、`session_ttl`、`max_turns`、`max_history_bytes`、`max_total_history_bytes` 已移除，来源保留策略请改用上述 `source_*` / `max_source_*` 配置。

`timeout` 是整轮时限，`request_timeout` 是单次模型调用时限，`send_timeout` 默认30秒，是单次投递时限；取消仍等待资源回收。`allow_users`、`allow_channels` 不填代表不额外限制，空列表代表全部拒绝。

## 请求日志

默认开启逐请求日志，本地默认 `~/.hyw-frontier/entari/logs/`；生产若使用 `home=/opt/entari/frontier-home`，日志位于 `/opt/entari/frontier-home/entari/logs/`，与本地网页日志隔离。

```sh
.venv/bin/python -m hyw_frontier.cli --home ~/.hyw-frontier/entari logs --query '关键词' --limit 5
```

日志包含模型、工具、图片处理、过程消息、渲染诊断、PNG/JPEG 大小及投递回执。`ready` 表示图片可供模型审阅，不代表最终被选用；`selected_ready_images` 用于区分这两步。过程消息日志区分模型未调用、工具拒绝和平台投递失败。

省略图片 Base64、思考正文及签名、凭据字段和 URL 查询参数，仍含问题、回答和搜索材料，须按私有数据保管。目录0700、文件0600，默认保留7天、最多100份、总64MiB、单请求2MiB。清理仅作用于专用目录，不删除正在写入的日志；日志失败不影响问答。可用 `log_enabled`、`log_home` 和 `log_*` 限额调整。

## 本地开发

正式源码仅保留 `http://127.0.0.1:8767` 问答调试网页，用于观察核心问答与渲染。个人已有的 `dev.entari/` 工作台仍保存在本机，但整个目录被 Git 忽略，不参与分发。它可用于实际检查机器人命令、引用、成员隔离与投递行为；不能将仅通过核心网页检查视为平台集成已验证。

自动化测试、测试集、CI 门禁和独立验证脚本已移除。修改后手动构建正式包或直接运行实际功能，不再依赖测试框架。
