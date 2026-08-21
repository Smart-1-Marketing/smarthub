"""Download an image and turn it into a web-ready WebP.

Same order of operations as the SEO Image Pipeline: rotate by EXIF first, cap
the longest edge second, encode WebP last. Resizing before encoding is the part
that actually shrinks a camera photo — WebP alone does not.
"""

import io

from PIL import Image, ImageOps

from . import settings
from .scanner import ScanError, _get

Image.MAX_IMAGE_PIXELS = 120_000_000  # refuse decompression bombs


def download(url):
    """Fetch the full image, refusing anything oversized."""
    resp = _get(url, timeout=settings.IMAGE_FETCH_TIMEOUT, stream=True)
    if resp.status_code >= 400:
        resp.close()
        raise ScanError(f"That image returned HTTP {resp.status_code}.")

    declared = resp.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.MAX_IMAGE_BYTES:
        resp.close()
        raise ScanError("That image is larger than 25 MB.")

    chunks, total = [], 0
    for chunk in resp.iter_content(65536):
        chunks.append(chunk)
        total += len(chunk)
        if total > settings.MAX_IMAGE_BYTES:
            resp.close()
            raise ScanError("That image is larger than 25 MB.")
    resp.close()
    return b"".join(chunks)


def _flatten(im):
    """WebP keeps alpha, but palette and CMYK images need converting first."""
    if im.mode in ("RGBA", "LA"):
        return im.convert("RGBA")
    if im.mode == "P":
        return im.convert("RGBA" if "transparency" in im.info else "RGB")
    if im.mode != "RGB":
        return im.convert("RGB")
    return im


def optimize(raw_bytes, *, max_edge=None, quality=82):
    """Return (webp_bytes, info) for one image, via the shared optimiser.

    The cap-then-convert order, the transparency handling and the guard
    against returning something larger than the input all now live in
    hub.images. They were written here first; keeping a second copy is how the
    "resize before converting" rule ended up needing to be fixed in several
    places separately.
    """
    from hub import images as _images
    max_edge = max_edge or settings.SEO_IMAGES_MAX_EDGE

    with Image.open(io.BytesIO(raw_bytes)) as im:
        source_format = (im.format or "").lower()
        original_w, original_h = ImageOps.exif_transpose(im).size

    proc = _images.optimise(raw_bytes, max_edge=max_edge, fmt="WEBP",
                            quality=quality)
    data = proc.data
    info = {
        "source_format": source_format,
        "source_bytes": len(raw_bytes),
        "source_width": original_w,
        "source_height": original_h,
        "width": proc.width,
        "height": proc.height,
        "bytes": len(data),
        "resized": (proc.width, proc.height) != (original_w, original_h),
        "saved_bytes": max(0, len(raw_bytes) - len(data)),
        "saved_pct": (
            round(100 * (len(raw_bytes) - len(data)) / len(raw_bytes))
            if raw_bytes else 0
        ),
    }
    return data, info


def preview(raw_bytes, max_edge=None):
    """Small WebP used by the review screen so the browser never loads originals."""
    max_edge = max_edge or settings.PREVIEW_MAX_EDGE
    with Image.open(io.BytesIO(raw_bytes)) as im:
        im = ImageOps.exif_transpose(im)
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        im = _flatten(im)
        buf = io.BytesIO()
        im.save(buf, format="WEBP", quality=72, method=4)
        return buf.getvalue()
