"""Animated GIF export — modules/image_creator/animation.py.

    python3 test_animated_export.py

No pytest, no new dependencies (Pillow is already one). Covers:

* a project with no animated objects — no timeline, no export;
* a project with one fade-in object — frame count and duration math;
* a project whose animation would exceed 30s — flagged, not silently
  truncated or silently allowed (that is qc.py's job; this file checks the
  timeline arithmetic and the GIF assembly it feeds).
"""
import base64
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1animatedexport_test_")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "animated-export-test-secret")

from PIL import Image  # noqa: E402

from modules.image_creator import animation  # noqa: E402
from modules.image_creator import qc  # noqa: E402

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def png_frame(color=(255, 0, 0, 255), size=(20, 20)) -> str:
    im = Image.new("RGBA", size, color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


print("\nanimated GIF export")
print("-" * 46)

# -------------------------------------------------------------- no motion
check("a project with no s1anim on anything has no timeline",
     animation.timeline_duration_ms([{"type": "text"}, {"type": "image"}]), None)
check("...and a duration of None asks for exactly one frame",
     animation.frame_count(0), 1)

# ----------------------------------------------------------- one entrance
objects = [{"s1anim": {"type": "fade-in", "delayMs": 200, "durationMs": 800}}]
check("one fade-in's total runtime is delay + duration",
     animation.timeline_duration_ms(objects), 1000)
n = animation.frame_count(1000, interval_ms=100)
check("1000ms at 100ms/frame needs 11 frames (0..1000 inclusive)", n, 11)
times = animation.frame_times(1000, interval_ms=100)
check("frame_times starts at 0 and ends at the total", (times[0], times[-1]), (0, 1000))
check("frame_times is evenly spaced", times[1] - times[0], 100)

# ------------------------------------------------------- several entrances
objects = [
    {"s1anim": {"type": "fade-in", "delayMs": 0, "durationMs": 500}},
    {"s1anim": {"type": "slide-up", "delayMs": 400, "durationMs": 600}},
    {"s1anim": {"type": "none"}},  # explicitly not animated -- ignored
]
check("the timeline is the LATEST object to finish settling, not the first",
     animation.timeline_duration_ms(objects), 1000)

# ------------------------------------------------------------ over 30s
long_objects = [{"s1anim": {"type": "slide-in-left", "delayMs": 29500, "durationMs": 2000}}]
total_ms = animation.timeline_duration_ms(long_objects)
check("a 31.5s timeline is measured, not clamped", total_ms, 31500)
findings = qc.check_animation_duration(long_objects)
check("qc flags it as a block rather than truncating", findings[0]["code"], "animation_too_long")
check("...at FAIL", findings[0]["level"], qc.FAIL)

# --------------------------------------------------------------- assembly
frames = [png_frame((255, 0, 0, 255)), png_frame((0, 255, 0, 255)),
          png_frame((0, 0, 255, 255))]
gif_bytes = animation.assemble_gif(frames, frame_ms=100)
check("assemble_gif produces real GIF bytes", gif_bytes[:6] in (b"GIF89a", b"GIF87a"), True)

with Image.open(io.BytesIO(gif_bytes)) as im:
    frame_n = 0
    try:
        while True:
            im.seek(frame_n)
            frame_n += 1
    except EOFError:
        pass
check("the assembled GIF carries every frame that was sent", frame_n, len(frames))

# A transparent frame is flattened onto white -- GIF carries no alpha
# blending, the same fallback /api/export/optimize already uses.
transparent = png_frame((10, 20, 30, 0))
flattened = animation.assemble_gif([transparent], frame_ms=100)
with Image.open(io.BytesIO(flattened)) as im:
    px = im.convert("RGB").getpixel((0, 0))
check("a transparent frame is flattened onto white, not left transparent", px, (255, 255, 255))

# --------------------------------------------------------------- refusals
try:
    animation.assemble_gif([], frame_ms=100)
    check("assembling zero frames raises", False, True)
except ValueError:
    check("assembling zero frames raises", True, True)

too_many = [png_frame()] * (animation.MAX_FRAMES + 1)
try:
    animation.assemble_gif(too_many, frame_ms=100)
    check("a request over MAX_FRAMES is refused, not silently truncated", False, True)
except ValueError:
    check("a request over MAX_FRAMES is refused, not silently truncated", True, True)

print("-" * 46)
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
