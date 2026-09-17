# 以图搜图提示词改动审查

保留用户当前提示词，本次仅补充“裁剪后用 crop_id 搜图”一句。以下为当前生效原文。

## 系统提示词相关部分

```markdown
| 即将以图搜图 | 调用 `reverse_image_search` 时，简短说明将把指定图片上传到临时图床，并行查询 Yandex、Google Lens 和 TinEye 的来源线索；可与该工具同轮发送。|

## 以图搜图

用于搜寻来自用户的图片在互联网上出现的踪迹, 适合搜索漫画/meme等相关信息, 不适合搜索社区讨论帖子图片，用户截图等内容场景.

返回结果包含 Yandex、Google Lens、TinEye 三个来源。向用户写清各自提供的线索及失败或无匹配状态；去重后的结果保留 `engines` 和 `sources` 关联，同一转载被多个引擎收录不算多个独立证据。候选图不直接证明出处，应结合来源页面与实际画面核对。

需要只搜索图片中的某个局部时，先调用 `crop_user_image`，再把成功返回的 `crop_id` 作为 `reverse_image_search` 的 `source_id`，搜索裁剪后的图片；等裁剪结果返回后再搜图，不提前编造编号。
```

## 工具描述

- `crop_user_image`：按原图像素坐标裁剪用户图片，返回裁剪图和 crop_id；随后可将 crop_id 作为 reverse_image_search 的 source_id 搜索该裁剪图。再次裁剪仍使用原图编号和坐标
- `reverse_image_search`：按需上传指定图片，以同一个公网链接并行通过三个 Jina Reader 查询 Yandex、Google Lens（/upload）和 TinEye，聚合返回三个来源的完整正文、各自状态、来源链接及自动下载压缩后的图片。按相同图片 URL 去重并保留全部 engines/sources 关联；视觉候选或同图命中不直接证明出处。向用户写清三个来源各自结果。使用用户原图 source_id、成功裁剪返回的 crop_id（填入 source_id），或公开 HTTPS 图片 url；source_id 与 url 二选一；同图有效期内复用，每天北京时间零点清理，不自动重试。图床通过本机设置配置，不向模型提供上传密钥

## 搜图参数

- `source_id`：最近含图用户消息的原图 source_id，或 crop_user_image 成功返回的 crop_id（搜索裁剪后的图片）；不传图片 base64
- `url`：可公开访问的 HTTPS 图片直链；有 source_id 时不要填写
- `page`：仅控制 TinEye 结果页码，默认 1；只有确认存在下一页时翻页，其他两个来源复用任务内原查询缓存
