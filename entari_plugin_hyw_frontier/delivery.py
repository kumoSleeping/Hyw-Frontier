"""Format Bot delivery without changing library results or stored source URLs."""
import asyncio
from io import BytesIO

from PIL import Image


def _jpeg(png: bytes, quality: int) -> tuple[bytes, int, int]:
    with BytesIO(png) as source, Image.open(source) as image, image.convert("RGB") as rgb, BytesIO() as output:
        rgb.save(output, "JPEG", quality=quality, subsampling=0, optimize=True, progressive=True)
        return output.getvalue(), rgb.width, rgb.height


async def outgoing_jpeg(png: bytes, quality: int) -> tuple[bytes, int, int]:
    task = asyncio.create_task(asyncio.to_thread(_jpeg, png, quality))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:  # noqa: BLE001 - join and observe the encoder before cancellation exits
                break
        if not task.cancelled():
            task.exception()
        raise
