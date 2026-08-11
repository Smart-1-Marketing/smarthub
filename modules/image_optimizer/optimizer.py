from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageOps

TARGET_BYTES_DEFAULT = 150 * 1024
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif"}


class OptimizationError(RuntimeError):
    pass


@dataclass
class OptimizationResult:
    data: bytes
    extension: str
    original_bytes: int
    output_bytes: int
    width: int
    height: int
    iterations: int

    @property
    def savings_pct(self) -> float:
        if not self.original_bytes:
            return 0.0
        return max(0.0, (1 - self.output_bytes / self.original_bytes) * 100)


def _read_dimensions(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as im:
        return im.size


def _resize_dimensions(width: int, height: int, factor: float) -> tuple[int, int]:
    return max(1, int(width * factor)), max(1, int(height * factor))


def _fit_within_target(candidates: Iterable[bytes], target_bytes: int) -> bytes | None:
    valid = [blob for blob in candidates if blob and len(blob) <= target_bytes]
    if not valid:
        return None
    # Largest valid file usually preserves the most visual detail.
    return max(valid, key=len)


def optimize_jpeg(data: bytes, target_bytes: int) -> tuple[bytes, int]:
    with Image.open(io.BytesIO(data)) as source:
        source = ImageOps.exif_transpose(source)
        if source.mode not in ("RGB", "L"):
            source = source.convert("RGB")

        original_w, original_h = source.size
        iterations = 0
        scale = 1.0

        while scale >= 0.25:
            w, h = _resize_dimensions(original_w, original_h, scale)
            image = source if (w, h) == source.size else source.resize((w, h), Image.Resampling.LANCZOS)

            low, high = 20, 95
            best: bytes | None = None
            while low <= high:
                q = (low + high) // 2
                buf = io.BytesIO()
                image.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
                blob = buf.getvalue()
                iterations += 1
                if len(blob) <= target_bytes:
                    best = blob
                    low = q + 1
                else:
                    high = q - 1

            if best is not None:
                return best, iterations
            scale *= 0.90

    raise OptimizationError("JPEG could not be reduced to the requested size without becoming impractically small.")


def _png_with_pillow(image: Image.Image, colors: int | None = None) -> bytes:
    working = image
    if colors is not None:
        # FASTOCTREE supports RGB/RGBA and generally preserves transparency well.
        if working.mode not in ("RGB", "RGBA"):
            working = working.convert("RGBA" if "A" in working.getbands() else "RGB")
        working = working.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)

    buf = io.BytesIO()
    working.save(buf, format="PNG", optimize=True, compress_level=9)
    return buf.getvalue()


def _run_pngquant(input_png: bytes, quality_max: int) -> bytes | None:
    if shutil.which("pngquant") is None:
        return None

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "source.png"
        out = Path(td) / "optimized.png"
        src.write_bytes(input_png)
        cmd = [
            "pngquant",
            f"--quality=20-{quality_max}",
            "--speed=1",
            "--strip",
            "--force",
            "--output",
            str(out),
            "--",
            str(src),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode not in (0, 99) or not out.exists():
            return None
        return out.read_bytes()


def optimize_png(data: bytes, target_bytes: int) -> tuple[bytes, int]:
    with Image.open(io.BytesIO(data)) as source:
        source = ImageOps.exif_transpose(source)
        source.load()
        original_w, original_h = source.size
        iterations = 0
        scale = 1.0

        while scale >= 0.25:
            w, h = _resize_dimensions(original_w, original_h, scale)
            image = source if (w, h) == source.size else source.resize((w, h), Image.Resampling.LANCZOS)

            base = _png_with_pillow(image)
            iterations += 1
            if len(base) <= target_bytes:
                return base, iterations

            candidates: list[bytes] = []
            # Try pngquant first when installed, then Pillow palette reductions.
            for quality in (90, 80, 70, 60, 50, 40, 30):
                candidate = _run_pngquant(base, quality)
                iterations += 1
                if candidate:
                    candidates.append(candidate)
                    if len(candidate) <= target_bytes:
                        return candidate, iterations

            for colors in (256, 192, 128, 96, 64, 48, 32, 24, 16):
                candidate = _png_with_pillow(image, colors=colors)
                iterations += 1
                candidates.append(candidate)
                if len(candidate) <= target_bytes:
                    return candidate, iterations

            fitting = _fit_within_target(candidates, target_bytes)
            if fitting:
                return fitting, iterations

            scale *= 0.90

    raise OptimizationError("PNG could not be reduced to the requested size without becoming impractically small.")


def _run_gifsicle(input_path: Path, output_path: Path, lossy: int, scale: float) -> bool:
    if shutil.which("gifsicle") is None:
        raise OptimizationError("gifsicle is required for animated GIF optimization. Deploy with the included Dockerfile.")

    cmd = ["gifsicle", "--optimize=3", "--colors=256", f"--lossy={lossy}"]
    if scale < 0.999:
        cmd.append(f"--scale={scale:.4f}")
    cmd.extend([str(input_path), "--output", str(output_path)])

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return proc.returncode == 0 and output_path.exists()


def optimize_gif(data: bytes, target_bytes: int) -> tuple[bytes, int]:
    if len(data) <= target_bytes:
        return data, 0

    iterations = 0
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        src = td_path / "source.gif"
        src.write_bytes(data)

        scale = 1.0
        while scale >= 0.25:
            for lossy in (20, 35, 50, 70, 90, 120, 160, 200):
                out = td_path / f"out-{iterations}.gif"
                iterations += 1
                if _run_gifsicle(src, out, lossy=lossy, scale=scale):
                    blob = out.read_bytes()
                    if len(blob) <= target_bytes:
                        return blob, iterations
            scale *= 0.90

    raise OptimizationError("GIF could not be reduced to the requested size. Consider fewer frames, shorter duration, or smaller dimensions.")


def optimize_image(data: bytes, filename: str, target_kb: int = 150) -> OptimizationResult:
    if not data:
        raise OptimizationError("The uploaded file is empty.")

    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise OptimizationError("Supported formats are GIF, JPG, JPEG, and PNG.")

    target_bytes = max(10, target_kb) * 1024
    original_bytes = len(data)

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
    except Exception as exc:
        raise OptimizationError("The uploaded file is not a valid supported image.") from exc

    # Already compliant: return original bytes with no quality loss.
    if original_bytes <= target_bytes:
        width, height = _read_dimensions(data)
        return OptimizationResult(data, ext, original_bytes, original_bytes, width, height, 0)

    if ext in {".jpg", ".jpeg"}:
        output, iterations = optimize_jpeg(data, target_bytes)
    elif ext == ".png":
        output, iterations = optimize_png(data, target_bytes)
    else:
        output, iterations = optimize_gif(data, target_bytes)

    if len(output) > target_bytes:
        raise OptimizationError("Optimizer finished above the requested size limit.")

    width, height = _read_dimensions(output)
    return OptimizationResult(output, ext, original_bytes, len(output), width, height, iterations)
