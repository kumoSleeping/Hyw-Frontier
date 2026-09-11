# entari_plugin_hyw_frontier

Entari 0.18.6 的 Hyw-Frontier 适配插件，直接调用 `hyw_frontier.answer()`，不依赖 Pi、Node 或本地 HTTP 问答服务。模型、搜索和绘图由核心库负责；插件负责命令、会话、附件、日志与平台消息投递。

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
    reasoning_mode: auto  # auto 自动切换；high/medium/low 固定档位
    reasoning:  # 三个键必须同时提供，值允许重复；替代旧的单字符串配置
      high: max
      medium: low
      low: "off"
    search_provider: jina
    timeout: 300
    request_timeout: 90
    max_concurrent: 2
```

当前模型为限时 ID，失效后需显式配置可用模型，不会自动回退。示例关闭思考，不改变核心库的默认值。支持核心库的 `provider`、`model`、`api`、`base_url`、`language`、`turbo` 和搜索设置，完整参数见 `config.py`。

凭据默认读取 `~/.hyw-frontier/`，可用 `home` 指定独立目录；也支持 `DEEPSEEK_API_KEY`、`JINA_API_KEY` 等环境变量。`api_key_env` 只填写模型密钥的环境变量名，不填写密钥本身。生产应使用项目外的私有凭据目录，不把真实密钥提交进配置。

## 消息行为

- `/q 问题`：执行问答。过程消息仅来自模型有效调用 `send_process_intro`，没有固定开场白。文字型直接发送；图片型将库返回的 PNG 转为 JPEG，默认 quality=85、4:4:4、不缩小分辨率。
- `/q -s 问题` 或 `/q --session 问题`：开启按机器人、频道、成员隔离的续接；后续仍需使用 `/q`，不自动接管普通聊天。
- `/q reset`：清空本人会话和来源链接。忙时先 `/qstop`，等待收尾后重置。
- `/qstop`：取消当前任务，等待库清理连接、线程和渲染进程。
- `/link`：回复本插件发出的回答，按消息回执 ID 返回该回答的来源标题与 URL；不引用时查询本人最近成功回答。兼容 `/qlink` 和配置的 `link_command`。
- `/qhelp`：查看帮助。

支持前置 @、引用文字和引用图片；最多4张附件，接受公开 HTTP(S) 或内联图片，拒绝本地路径及私网，复用核心库的下载、校验与压缩。图片投递异常时发送明确标注的文字备份，不声称图片一定没送达。

来源引用查询允许同机器人、同频道内其他成员的公开回答，不跨频道查询；无记录时明确提示，不误用最近一问。文字、图片及图片失败后的文字备份均记录回执。索引仅在内存，受 TTL、条数和容量限制；重启、本人 reset 或容量淘汰后不可恢复。

默认同时2个任务、64个会话、每会话20轮；空闲1小时后在下次请求时清理。单会话历史8MiB、总历史64MiB，容量不足不截断旧历史，达到提交上限时明确结束续接。并发满或本人忙时明确拒绝；不提供排队、群共享上下文、跨重启恢复或自动引用续聊。仅成功投递最终回答后提交历史。

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
