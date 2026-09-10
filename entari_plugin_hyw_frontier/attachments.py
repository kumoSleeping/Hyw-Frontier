"""Reuse Hyw's bounded public-image downloader; never open chat-supplied paths."""
import asyncio
import base64
from threading import Event

from arclet.entari import Image, MessageChain

from hyw_frontier.image_input import MAX_IMAGE_BYTES, MAX_IMAGES, validate_images
from hyw_frontier.media import Candidate, compress, download

# Chat CDNs are slower than search thumbnails; keep the same bounded worker, longer budget.
ATTACHMENT_DOWNLOAD_TIMEOUT = 15.0


def _prepare(sources: list[str], cancelled: Event) -> list[dict]:
    images = []
    for source in sources:
        if cancelled.is_set():
            break
        if source.startswith("data:"):
            header, sep, data = source.partition(",")
            if not sep or not header.endswith(";base64") or len(data) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
                raise ValueError("图片编码无效或超过5MB。")
            mime = header[5:-7]
            validate_images([{"mimeType": mime, "data": data}])
            raw = base64.b64decode(data, validate=True)
        elif source.startswith(("https://", "http://")) and len(source) <= 8192:
            _, raw, _, _ = download(Candidate(source, source, "用户附件", 0), cancelled,
                                    timeout=ATTACHMENT_DOWNLOAD_TIMEOUT)
            if raw is None:
                raise ValueError("图片下载失败；仅支持可公开访问的 HTTP(S) 图片，请重新上传。")
        else:
            raise ValueError("不读取本地文件或私有地址，请上传图片。")
        try:
            raw, _ = compress(raw)
        except Exception:  # noqa: BLE001 - sanitize decoder errors at the attachment boundary
            raise ValueError("图片损坏、过小或超过尺寸限制。") from None
        images.append({"mimeType": "image/jpeg", "data": base64.b64encode(raw).decode("ascii")})
    return images


async def prepare_images(chain: MessageChain) -> list[dict]:
    sources = [image.src for image in chain.get(Image)]
    if len(sources) > MAX_IMAGES:
        raise ValueError("每次最多4张图片。")
    if not sources:
        return []
    cancelled = Event()
    task = asyncio.create_task(asyncio.to_thread(_prepare, sources, cancelled))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:  # noqa: BLE001 - cancellation still joins and observes worker failure
                break
        if not task.cancelled():
            task.exception()
        raise
