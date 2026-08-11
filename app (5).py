
import io
import os
import re
from pathlib import Path
from typing import List, Tuple

from flask import Flask, render_template, request, send_file, jsonify
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024

ALLOWED_FORMATS = {"PNG", "JPEG", "GIF"}
MAX_DIMENSION = 12000


def sanitize_filename(value: str | None, fallback: str = "optimized-image") -> str:
    value = (value or "").strip()
    if not value:
        value = fallback

    # Remove an extension supplied by the user because the selected output
    # format determines the final extension.
    value = Path(value).stem
    value = re.sub(r"[^A-Za-z0-9._ -]+", "-", value)
    value = re.sub(r"\s+", " ", value).strip(" .-_")
    return value[:120] or fallback


def validate_dimension(value: str | None, label: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be a whole number.") from exc
    if number < 1 or number > MAX_DIMENSION:
        raise ValueError(f"{label} must be between 1 and {MAX_DIMENSION:,} pixels.")
    return number


def contain_size(original: Tuple[int, int], width: int | None, height: int | None, lock_aspect: bool) -> Tuple[int, int]:
    ow, oh = original
    if width is None and height is None:
        return ow, oh

    if lock_aspect:
        if width is not None and height is not None:
            ratio = min(width / ow, height / oh)
            return max(1, round(ow * ratio)), max(1, round(oh * ratio))
        if width is not None:
            return width, max(1, round(width * oh / ow))
        return max(1, round(height * ow / oh)), height

    return width or ow, height or oh


def prepare_for_jpeg(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, "white")
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def save_static(image: Image.Image, output_format: str, quality: int) -> bytes:
    output = io.BytesIO()
    image = ImageOps.exif_transpose(image)

    if output_format == "JPEG":
        image = prepare_for_jpeg(image)
        image.save(output, "JPEG", quality=quality, optimize=True, progressive=True, subsampling="4:2:0")
    elif output_format == "PNG":
        if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            image = image.convert("RGBA")
        image.save(output, "PNG", optimize=True, compress_level=9)
    else:
        if image.mode not in ("P", "L"):
            image = image.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
        image.save(output, "GIF", optimize=True)

    return output.getvalue()


def save_animated_gif(source: Image.Image, size: Tuple[int, int]) -> bytes:
    frames: List[Image.Image] = []
    durations: List[int] = []

    for frame in ImageSequence.Iterator(source):
        current = frame.convert("RGBA")
        if current.size != size:
            current = current.resize(size, Image.Resampling.LANCZOS)
        current = current.convert("P", palette=Image.Palette.ADAPTIVE, colors=256)
        frames.append(current)
        durations.append(frame.info.get("duration", source.info.get("duration", 100)))

    if not frames:
        raise ValueError("The GIF did not contain readable frames.")

    output = io.BytesIO()
    frames[0].save(
        output,
        "GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=source.info.get("loop", 0),
        optimize=True,
        disposal=2,
    )
    return output.getvalue()


def compress_to_target(image: Image.Image, output_format: str, quality: int, target_kb: int, animated: bool, size: Tuple[int, int]) -> bytes:
    target_bytes = max(10, target_kb) * 1024

    if animated and output_format == "GIF":
        result = save_animated_gif(image, size)
        if len(result) <= target_bytes:
            return result

        # Gradually reduce dimensions for animated GIFs.
        working_size = size
        while len(result) > target_bytes and min(working_size) > 160:
            working_size = (max(1, round(working_size[0] * 0.9)), max(1, round(working_size[1] * 0.9)))
            result = save_animated_gif(image, working_size)
        return result

    working = ImageOps.exif_transpose(image.copy())
    if working.size != size:
        working = working.resize(size, Image.Resampling.LANCZOS)

    if output_format == "JPEG":
        low, high = 30, max(30, quality)
        best = save_static(working, output_format, low)
        while low <= high:
            mid = (low + high) // 2
            candidate = save_static(working, output_format, mid)
            if len(candidate) <= target_bytes:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        result = best
    else:
        result = save_static(working, output_format, quality)

    # PNG/GIF often need dimension reduction to meet a strict byte target.
    while len(result) > target_bytes and min(working.size) > 160:
        next_size = (max(1, round(working.width * 0.92)), max(1, round(working.height * 0.92)))
        working = working.resize(next_size, Image.Resampling.LANCZOS)
        result = save_static(working, output_format, quality)

    return result


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/process")
def process_image():
    upload = request.files.get("image")
    if not upload or not upload.filename:
        return jsonify(error="Choose an image to upload."), 400

    try:
        width = validate_dimension(request.form.get("width"), "Width")
        height = validate_dimension(request.form.get("height"), "Height")
        crop_x = validate_dimension(request.form.get("crop_x"), "Crop X") or 0
        crop_y = validate_dimension(request.form.get("crop_y"), "Crop Y") or 0
        crop_width = validate_dimension(request.form.get("crop_width"), "Crop width")
        crop_height = validate_dimension(request.form.get("crop_height"), "Crop height")
        crop_enabled = request.form.get("crop_enabled") == "true"
        lock_aspect = request.form.get("lock_aspect") == "true"
        optimize = request.form.get("optimize") == "true"
        target_kb = int(request.form.get("target_kb", "150"))
        quality = int(request.form.get("quality", "82"))
        output_format = request.form.get("format", "PNG").upper()
        requested_name = request.form.get("output_name")

        if output_format == "JPG":
            output_format = "JPEG"
        if output_format not in ALLOWED_FORMATS:
            raise ValueError("Output format must be PNG, JPG, or GIF.")
        if not 20 <= quality <= 100:
            raise ValueError("Quality must be between 20 and 100.")
        if not 10 <= target_kb <= 10000:
            raise ValueError("Target size must be between 10 KB and 10,000 KB.")

        raw = upload.read()
        source = Image.open(io.BytesIO(raw))
        source.load() if not getattr(source, "is_animated", False) else None

        animated = bool(getattr(source, "is_animated", False))

        if crop_enabled:
            if crop_width is None or crop_height is None:
                raise ValueError("Choose a crop area before processing.")
            if crop_x + crop_width > source.width or crop_y + crop_height > source.height:
                raise ValueError("The crop area extends beyond the image.")
            crop_box = (crop_x, crop_y, crop_x + crop_width, crop_y + crop_height)

            if animated:
                cropped_frames = []
                durations = []
                for frame in ImageSequence.Iterator(source):
                    current = frame.convert("RGBA").crop(crop_box)
                    cropped_frames.append(current)
                    durations.append(frame.info.get("duration", source.info.get("duration", 100)))

                temp = io.BytesIO()
                cropped_frames[0].save(
                    temp,
                    "GIF",
                    save_all=True,
                    append_images=cropped_frames[1:],
                    duration=durations,
                    loop=source.info.get("loop", 0),
                    disposal=2,
                )
                temp.seek(0)
                source = Image.open(temp)
                source._smart1_temp_buffer = temp
            else:
                source = source.crop(crop_box)

        size = contain_size(source.size, width, height, lock_aspect)

        if animated and output_format == "GIF":
            if optimize:
                result = compress_to_target(source, output_format, quality, target_kb, True, size)
            else:
                result = save_animated_gif(source, size)
        else:
            frame = source.seek(0) or source.copy()
            if frame.size != size:
                frame = frame.resize(size, Image.Resampling.LANCZOS)
            if optimize:
                result = compress_to_target(frame, output_format, quality, target_kb, False, size)
            else:
                result = save_static(frame, output_format, quality)

        extension = {"JPEG": "jpg", "PNG": "png", "GIF": "gif"}[output_format]
        original_stem = Path(upload.filename).stem or "optimized-image"
        default_stem = f"{original_stem}-resized"
        stem = sanitize_filename(requested_name, default_stem)
        filename = f"{stem}.{extension}"
        mime = {"JPEG": "image/jpeg", "PNG": "image/png", "GIF": "image/gif"}[output_format]

        return send_file(
            io.BytesIO(result),
            mimetype=mime,
            as_attachment=True,
            download_name=filename,
            max_age=0,
        )

    except (ValueError, UnidentifiedImageError, OSError) as exc:
        return jsonify(error=str(exc) or "The image could not be processed."), 400
    except Exception:
        app.logger.exception("Image processing failed")
        return jsonify(error="The image could not be processed. Try a smaller file or different format."), 500


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
