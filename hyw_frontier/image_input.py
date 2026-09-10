"""Bounded, validated inline images for user messages (never fetch remote URLs)."""
from __future__ import annotations

import base64
import binascii
from io import BytesIO
import warnings

from PIL import Image

MIME_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP", "image/gif": "GIF"}
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 10 * 1024 * 1024
IMAGE_ONLY_TEXT = "[用户发送了这张图，请探究根据这张图，给出一份符合系统提示词所需求的的文章]"
IMAGE_INPUT_CONFIG = {"enabled": True, "paste_only": True, "mime_types": list(MIME_FORMATS),
                      "max_images": MAX_IMAGES, "max_image_bytes": MAX_IMAGE_BYTES,
                      "max_total_bytes": MAX_TOTAL_BYTES, "image_only_text": IMAGE_ONLY_TEXT}


class ImageInputError(ValueError):
    pass


def validate_images(images) -> list[dict]:
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise ImageInputError("每次最多粘贴4张图片。")
    result = []
    total = 0
    for item in images:
        if (not isinstance(item, dict) or not isinstance(item.get("mimeType"), str)
                or item["mimeType"] not in MIME_FORMATS):
            raise ImageInputError("图片仅支持 PNG、JPEG、WebP、GIF。")
        data = item.get("data")
        if not isinstance(data, str) or not data or len(data) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ImageInputError("图片为空或超过单张5MB上限。")
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ImageInputError("图片编码无效。") from exc
        total += len(raw)
        if len(raw) > MAX_IMAGE_BYTES or total > MAX_TOTAL_BYTES:
            raise ImageInputError("图片超过单张5MB或合计10MB上限。")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(raw)) as image:
                    if image.format != MIME_FORMATS[item["mimeType"]]:
                        raise ValueError("Image format invalid")
                    image.verify()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ImageInputError("图片触发解码器安全保护，请压缩后重新粘贴。") from exc
        except (OSError, ValueError, SyntaxError) as exc:
            raise ImageInputError("图片损坏或格式不匹配。") from exc
        result.append({"type": "image", "mimeType": item["mimeType"], "data": data})
    return result
