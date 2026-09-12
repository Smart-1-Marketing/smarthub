"""Cloudinary cutouts and OpenAI scene editing, with validated image bytes."""
from __future__ import annotations

import hashlib
import io
import time
from urllib.parse import urlsplit, unquote

import requests
from PIL import Image, ImageOps

MAX_BYTES = 12 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024


class BackgroundError(RuntimeError):
    """A message safe to show beside the user's image."""


def normalize(data: bytes) -> bytes:
    if not data or len(data) > MAX_BYTES:
        raise BackgroundError("Choose an image up to 12 MB.")
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("format")
            if source.width * source.height > 25_000_000:
                raise BackgroundError("That image is too large. Resize it below 25 megapixels first.")
            im = ImageOps.exif_transpose(source).convert("RGBA")
            buf = io.BytesIO()
            im.save(buf, "PNG")
            raw = buf.getvalue()
            if len(raw) > MAX_OUTPUT_BYTES:
                raise BackgroundError("That image is too large to process. Resize it first.")
            return raw
    except BackgroundError:
        raise
    except Exception as exc:
        raise BackgroundError("That image could not be read. Choose a valid JPG, PNG or WebP.") from exc


def validate_output(data: bytes, *, cutout: bool) -> bytes:
    try:
        if len(data) > MAX_OUTPUT_BYTES:
            raise ValueError("size")
        with Image.open(io.BytesIO(data)) as im:
            if im.format != "PNG" or im.width * im.height > 40_000_000:
                raise ValueError("format")
            im.load()
            if cutout:
                alpha = im.convert("RGBA").getchannel("A").getextrema()
                if alpha[0] == 255 or alpha[1] == 0:
                    raise BackgroundError("No usable cutout came back. Try a tighter crop around the subject.")
        return data
    except BackgroundError:
        raise
    except Exception as exc:
        raise BackgroundError("The image service returned an unreadable image. Please try again later.") from exc


def cloud_ready() -> bool:
    from hub import storage
    return storage.ready()


def ai_ready() -> bool:
    from hub import ai
    return ai.ready()


def _read_response(response, limit: int) -> bytes:
    chunks, total = [], 0
    for chunk in response.iter_content(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise BackgroundError("That image is too large to download. Use a smaller image.")
        chunks.append(chunk)
    return b"".join(chunks)


def saved_image(url: str) -> bytes:
    """Only this Hub's Cloudinary image deliveries, without redirects."""
    from hub import storage
    import cloudinary
    storage._configure()
    cloud = cloudinary.config().cloud_name
    parsed = urlsplit(url)
    prefix = f"/{cloud}/image/upload/"
    if (not cloud or parsed.scheme != "https" or parsed.netloc != "res.cloudinary.com"
            or not parsed.path.startswith(prefix) or parsed.query or parsed.fragment
            or any(p in (".", "..") for p in unquote(parsed.path).split("/"))
            or "\\" in unquote(parsed.path)):
        raise BackgroundError("Use an image link from this Hub's saved image gallery.")
    try:
        with requests.get(url, timeout=30, stream=True, allow_redirects=False) as r:
            if r.status_code != 200:
                raise BackgroundError("That saved image could not be opened. Check its link.")
            return _read_response(r, MAX_BYTES)
    except requests.RequestException as exc:
        raise BackgroundError("The saved image could not be reached. Please try again later.") from exc


def cloud_cutout(data: bytes) -> bytes:
    from hub import storage
    import cloudinary.utils
    if not cloud_ready():
        raise BackgroundError("Background removal is not connected. Ask an administrator to check Cloudinary.")
    digest = hashlib.sha256(data).hexdigest()
    try:
        asset = storage.put("cutouts", "source.png", data,
                            public_id=f"smart1-cutout-sources/{digest}",
                            tags=["background-source"], overwrite=False)
        if asset.backend != "cloudinary":
            raise BackgroundError("The image could not be sent to the background service.")
        url, _ = cloudinary.utils.cloudinary_url(
            asset.public_id, secure=True, sign_url=True, format="png",
            transformation=[{"effect": "background_removal"}])
        # A 423 is processing, not a failed upload. Retry the same derived
        # image; never upload again or silently switch to a different model.
        for attempt in range(6):
            with requests.get(url, timeout=20, stream=True, allow_redirects=False) as r:
                if r.status_code == 423:
                    if attempt < 5:
                        time.sleep(2)
                        continue
                    raise BackgroundError("The cutout is still processing. Wait a moment and try this image again.")
                if r.status_code in (401, 402, 403):
                    raise BackgroundError("Cloudinary background removal is unavailable for this account. Ask an administrator to check access and usage limits.")
                if r.status_code != 200:
                    raise BackgroundError("The background service could not process this image. Please try again later.")
                result = validate_output(_read_response(r, MAX_OUTPUT_BYTES), cutout=True)
                from hub import quotas
                quotas.record_asset(module="bg_remover", kind="background_removal", detail=digest)
                return result
        raise BackgroundError("The cutout is still processing. Please try again shortly.")
    except BackgroundError:
        raise
    except Exception as exc:
        raise BackgroundError("The background service could not complete this image. Please try again later.") from exc


def replace_background(data: bytes, prompt: str) -> bytes:
    from hub import ai
    try:
        with Image.open(io.BytesIO(data)) as im:
            size = "1536x1024" if im.width > im.height else "1024x1536" if im.height > im.width else "1024x1024"
        images = ai.image_edit(
            "Replace only the background of this reference image. Preserve the foreground "
            "subject, its identity, shape, colors, logo and lettering as closely as possible. "
            "Do not add text or extra products. New background: " + prompt,
            data, module="bg_remover", purpose="background_replace", size=size, n=1)
        return validate_output(images[0], cutout=False)
    except BackgroundError:
        raise
    except Exception as exc:
        raise BackgroundError("The AI background edit could not be completed. Try a different description or ask an administrator to check AI access and usage limits.") from exc
