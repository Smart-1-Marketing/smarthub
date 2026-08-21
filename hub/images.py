"""One image pipeline.

Four modules had grown their own copy of the same code — seo_images,
bg_remover, image_optimizer and image_creator all call ImageOps.exif_transpose
followed by thumbnail/resize, with slightly different rules each time.

The important lesson already learned the hard way and encoded here: converting
to WebP does not shrink a camera photo. A 6000x4000 JPEG stays 6000x4000 and
stays enormous. The longest edge has to be capped *first*, and EXIF rotation
has to be applied *before* the cap or portrait photos cap on the wrong axis.

Image Creator's final export still has no server-side compression pass. That is
what `optimise()` is for.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass

from PIL import Image, ImageOps

from hub.config import settings

Image.MAX_IMAGE_PIXELS = 200_000_000        # refuse decompression bombs

_TRANSPARENT_OK = {"WEBP", "PNG", "AVIF"}


@dataclass(frozen=True)
class Processed:
    data: bytes
    fmt: str
    width: int
    height: int
    bytes_in: int

    @property
    def bytes_out(self) -> int:
        return len(self.data)

    @property
    def saved_pct(self) -> int:
        if not self.bytes_in:
            return 0
        return max(0, round(100 * (1 - self.bytes_out / self.bytes_in)))

    def as_dict(self) -> dict:
        return {"format": self.fmt, "width": self.width, "height": self.height,
                "bytes_in": self.bytes_in, "bytes_out": self.bytes_out,
                "saved_pct": self.saved_pct}


def _open(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data))
    im.load()
    return ImageOps.exif_transpose(im)      # honour phone rotation first


def optimise(data: bytes, *, max_edge: int | None = None, fmt: str = "WEBP",
             quality: int = 82, strip_metadata: bool = True) -> Processed:
    """Cap the longest edge, then convert. In that order — it matters."""
    max_edge = max_edge or settings.max_edge
    im = _open(data)
    source_fmt = (im.format or "").upper()
    keep_alpha = fmt.upper() in _TRANSPARENT_OK and im.mode in ("RGBA", "LA", "P")

    resized = max(im.size) > max_edge
    if resized:
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)

    if keep_alpha:
        im = im.convert("RGBA")
    elif im.mode == "P":
        # A palette image may carry transparency; converting straight to RGB
        # drops it and leaves a black fringe where the alpha was.
        im = im.convert("RGBA" if "transparency" in im.info else "RGB")
    elif im.mode not in ("RGB", "L"):
        im = im.convert("RGB")

    buf = io.BytesIO()
    save: dict = {"quality": quality, "optimize": True}
    if fmt.upper() == "WEBP":
        save["method"] = 5
    if fmt.upper() == "JPEG":
        save["progressive"] = True
    if not strip_metadata:
        save["exif"] = im.info.get("exif", b"")
    im.save(buf, format=fmt.upper(), **save)
    out = buf.getvalue()

    # An optimiser that returns something larger than it was given has not
    # optimised anything. Re-encoding an already-compressed file at the same
    # size routinely does exactly that. Only safe when no resize happened and
    # the source is already the requested format — otherwise the caller asked
    # for a conversion and must get one.
    if not resized and source_fmt == fmt.upper() and len(data) < len(out):
        return Processed(data, fmt.upper(), im.width, im.height, len(data))
    return Processed(out, fmt.upper(), im.width, im.height, len(data))


def preview(data: bytes, edge: int | None = None) -> Processed:
    """Small WebP thumbnail for galleries and project cards."""
    return optimise(data, max_edge=edge or settings.preview_edge,
                    fmt="WEBP", quality=70)


def dimensions(data: bytes) -> tuple[int, int]:
    im = _open(data)
    return im.width, im.height


def is_image(filename: str) -> bool:
    return os.path.splitext(filename or "")[1].lower() in {
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif"}
