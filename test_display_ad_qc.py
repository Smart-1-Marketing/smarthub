"""Display ad QC — the seven checks in modules/image_creator/qc.py.

    python3 test_display_ad_qc.py

No pytest, no new dependencies: a throwaway data directory so this never
touches /var/data or the real database.

Covers, per work order:

1. Dimension match (block) against the selected preset.
2. File size — a GDN/IAB size is judged by the spec kit's own ceiling
   (never a restated number); a Social/Custom size is advisory against the
   house ceiling.
3. Animation duration (block past 30s), read from the same timeline
   metadata the animated export walks.
4. Brand logo present (advisory), tagged rather than guessed at.
5. Text-safe margin (advisory), the kit's own 14%.
6. Contrast (advisory), WCAG AA, measured only where the background is
   knowable (a solid color), never guessed against a photo.
7. White-background border (advisory).
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1displayadqc_test_")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "display-ad-qc-test-secret")

from modules.image_creator import qc  # noqa: E402
from modules.image_creator.app import CANVAS_PRESETS  # noqa: E402

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def has_code(findings, code):
    return any(f["code"] == code for f in findings)


def preset(key):
    return next(p for p in CANVAS_PRESETS if p["key"] == key)


print("\ndisplay ad QC")
print("-" * 46)

# ---------------------------------------------------------------- dimension
mrec = preset("mrec")
findings = qc.check_dimensions_and_weight(mrec, width=300, height=250,
                                          size_bytes=1000, fmt="png")
check("a correctly sized mrec passes dimensions", has_code(findings, "dimension_mismatch"), False)

findings = qc.check_dimensions_and_weight(mrec, width=301, height=250,
                                          size_bytes=1000, fmt="png")
check("an off-by-one mrec export is a block", has_code(findings, "dimension_mismatch"), True)
check("dimension mismatch is a FAIL", next(f["level"] for f in findings
      if f["code"] == "dimension_mismatch"), qc.FAIL)

custom = preset("custom")
findings = qc.check_dimensions_and_weight(custom, width=500, height=500,
                                          size_bytes=1000, fmt="png")
check("custom size (no fixed w/h) never flags a dimension mismatch",
     has_code(findings, "dimension_mismatch"), False)

# --------------------------------------------------------------- file size
findings = qc.check_dimensions_and_weight(mrec, width=300, height=250,
                                          size_bytes=153600, fmt="png")
check("mrec at exactly the kit's 150KB ceiling passes", has_code(findings, "file_too_large"), False)
findings = qc.check_dimensions_and_weight(mrec, width=300, height=250,
                                          size_bytes=153601, fmt="png")
check("mrec one byte over the kit's ceiling blocks", has_code(findings, "file_too_large"), True)
check("file size over the kit ceiling is a FAIL", next(f["level"] for f in findings
      if f["code"] == "file_too_large"), qc.FAIL)

mobile = preset("mobile-banner")
findings = qc.check_dimensions_and_weight(mobile, width=320, height=50,
                                          size_bytes=100000, fmt="png")
check("mobile banner's real kit ceiling is 50KB, not a flat 150KB",
     has_code(findings, "file_too_large"), True)

halfpage = preset("halfpage")
findings = qc.check_dimensions_and_weight(halfpage, width=300, height=600,
                                          size_bytes=200000, fmt="png")
check("half page's real kit ceiling is 250KB, so 200KB passes",
     has_code(findings, "file_too_large"), False)

igsquare = preset("ig-square")
findings = qc.check_dimensions_and_weight(igsquare, width=1080, height=1080,
                                          size_bytes=140 * 1024, fmt="png")
check("a social size under the house 150KB advisory passes",
     has_code(findings, "file_too_large"), False)
findings = qc.check_dimensions_and_weight(igsquare, width=1080, height=1080,
                                          size_bytes=200 * 1024, fmt="png")
check("a social size over the house 150KB is advisory, not a block",
     has_code(findings, "file_too_large"), True)
check("...and it's a WARN, never a FAIL", next(f["level"] for f in findings
      if f["code"] == "file_too_large"), qc.WARN)

# ------------------------------------------------------------- animation
check("no s1anim on any object -> no animation finding",
     qc.check_animation_duration([{"type": "text"}]), [])
short_objs = [{"s1anim": {"type": "fade-in", "delayMs": 0, "durationMs": 2000}}]
check("a 2s entrance passes the 30s ceiling",
     qc.check_animation_duration(short_objs), [])
long_objs = [{"s1anim": {"type": "slide-up", "delayMs": 29000, "durationMs": 3000}}]
findings = qc.check_animation_duration(long_objs)
check("32s of animation is a block", has_code(findings, "animation_too_long"), True)
check("...and it's a FAIL", findings[0]["level"], qc.FAIL)

# ------------------------------------------------------------------- logo
check("no logo-tagged object -> advisory finding",
     has_code(qc.check_logo_present([{"s1meta": {"role": "product"}}]), "missing_logo"), True)
check("an object tagged role=logo clears the check",
     qc.check_logo_present([{"s1meta": {"role": "logo"}}]), [])

# ---------------------------------------------------------- safe margin
tall = {"type": "story", "w": 1080, "h": 1920}
top_text = {"type": "text", "left": 100, "top": 10, "width": 400, "height": 40,
            "scaleX": 1, "scaleY": 1}
findings = qc.check_text_safe_margin([top_text], width=1080, height=1920)
check("text in the top 14% is flagged", has_code(findings, "text_in_safe_margin"), True)
check("...advisory, never a block", findings[0]["level"], qc.WARN)

mid_text = {"type": "text", "left": 100, "top": 900, "width": 400, "height": 40,
           "scaleX": 1, "scaleY": 1}
check("text safely in the middle clears the check",
     qc.check_text_safe_margin([mid_text], width=1080, height=1920), [])

# ------------------------------------------------------------------ contrast
canvas_low_contrast = {
    "width": 300, "height": 250, "background": "#ffffff",
    "objects": [{"type": "textbox", "fill": "#f0f0f0", "fontSize": 16,
                "left": 10, "top": 10, "width": 100, "height": 20,
                "scaleX": 1, "scaleY": 1, "name": "Headline"}],
}
findings = qc.check_contrast(canvas_low_contrast)
check("near-white text on a white canvas background is flagged",
     has_code(findings, "low_contrast"), True)

canvas_good_contrast = {
    "width": 300, "height": 250, "background": "#ffffff",
    "objects": [{"type": "textbox", "fill": "#111111", "fontSize": 16,
                "left": 10, "top": 10, "width": 100, "height": 20,
                "scaleX": 1, "scaleY": 1, "name": "Headline"}],
}
check("dark text on a white background passes",
     qc.check_contrast(canvas_good_contrast), [])

canvas_over_photo = {
    "width": 300, "height": 250,
    "objects": [
        {"type": "image", "s1kind": "background", "left": 0, "top": 0,
         "width": 300, "height": 250, "scaleX": 1, "scaleY": 1},
        {"type": "textbox", "fill": "#f0f0f0", "fontSize": 16,
         "left": 10, "top": 10, "width": 100, "height": 20,
         "scaleX": 1, "scaleY": 1, "name": "Headline"},
    ],
}
check("text over a photo background is not measured, never guessed at",
     qc.check_contrast(canvas_over_photo), [])

# -------------------------------------------------------- white background
check("a white background recommends a border",
     has_code(qc.check_white_background_border({"background": "#ffffff", "objects": []}),
              "white_background_border"), True)
check("a navy background needs no border advice",
     qc.check_white_background_border({"background": "#1a2e58", "objects": []}), [])

# ---------------------------------------------------------------------- run
canvas = {
    "width": 300, "height": 250, "background": "#ffffff",
    "objects": [{"type": "textbox", "fill": "#111111", "fontSize": 16,
                "left": 10, "top": 10, "width": 100, "height": 20,
                "scaleX": 1, "scaleY": 1, "s1meta": {"role": "logo"}}],
}
result = qc.run(mrec, canvas, size_bytes=1000, fmt="png")
check("run() bundles every check and computes an overall verdict",
     result["result"] in ("pass", "warn", "fail"), True)
check("a clean design with a logo still warns on the white background",
     result["result"], "warn")

print("-" * 46)
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
