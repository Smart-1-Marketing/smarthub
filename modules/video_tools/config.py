"""Video Tools — the numbers both tools are tuned by, in one place.

Two tools share this module because they share everything below the buttons:
a source picker over the same Cloudinary account, one job table, one way of
submitting a derived render and one way of filing the result in a client's
library. What differs is a single transformation string.

Nothing here touches ffmpeg. That was the constraint the tools were designed
to, not a shortcut taken inside them: the Hub is one Render web service with
no media binary on the image, and a video encode inside the request that asks
for it is the failure mode that takes the whole Hub down rather than the one
page. Every edit here is a Cloudinary *derived* asset -- submitted async,
polled, and only then filed -- so the original is never modified and a job
that fails costs a row in a table.
"""
from __future__ import annotations

# --------------------------------------------------------------- dead air

# A pause shorter than this is speech rhythm, not dead air. Under about half
# a second, cutting reads as a glitch rather than as tightening: the listener
# hears the edit. Measured against broadcast VO, where a comma is ~0.3s and a
# full stop ~0.7s.
DEFAULT_GAP = 0.6
MIN_GAP = 0.25
MAX_GAP = 5.0

# What is left behind at each cut. Removing a gap *entirely* butts two words
# together with no air between them and sounds worse than the dead air did --
# this is the single control that decides whether the output sounds edited or
# sounds fast. Split evenly either side of the cut.
DEFAULT_BREATH = 0.25
MAX_BREATH = 1.5

# How loud a moment has to be to count as sound, as a fraction of the loudest
# moment in the clip. Relative rather than absolute because the waveform is a
# picture: it carries no dB scale, and a quietly-recorded interview and a
# mastered spot would need different absolute floors.
SENSITIVITY = {
    "gentle": 0.015,   # ~-36 dB. Cuts only true silence; keeps room tone.
    "normal": 0.035,   # ~-29 dB. The default.
    "aggressive": 0.08,  # ~-22 dB. Cuts breaths and low room tone too.
}
DEFAULT_SENSITIVITY = "normal"

# The ceiling on how many cuts one derived asset may carry.
#
# Every cut adds two components to the transformation, and a Cloudinary URL
# that grows without limit stops being delivered rather than being delivered
# slowly. When a clip has more qualifying gaps than this, the LONGEST ones are
# cut and the rest are left alone -- which removes the most dead air the
# budget buys, and is a decision the plan states rather than one the tool
# makes quietly.
MAX_CUTS = 30

# A kept segment shorter than this is a syllable stranded between two cuts.
# It contributes a component and contributes nothing audible.
MIN_SEGMENT = 0.20

# Columns per second of the waveform image the analysis reads. 20 gives 50ms
# per column, comfortably finer than MIN_GAP. The width is clamped because a
# 40-minute recording would otherwise ask Cloudinary for an image no server
# should render.
WAVEFORM_COLUMNS_PER_SECOND = 20
WAVEFORM_MIN_WIDTH = 800
WAVEFORM_MAX_WIDTH = 4000
WAVEFORM_HEIGHT = 120

# ------------------------------------------------------------- reframing

# Ratios worth offering, and what each is actually for. Named rather than
# free-form: a rep typing "9x16" is a broken transformation, and the four
# below cover every placement the Hub's own creative specs list.
RATIOS = {
    "9:16": {"label": "9:16 vertical", "w": 1080, "h": 1920,
             "note": "Reels, TikTok, Shorts, Stories."},
    "4:5":  {"label": "4:5 portrait", "w": 1080, "h": 1350,
             "note": "The tallest thing the Meta feed will show in full."},
    "1:1":  {"label": "1:1 square", "w": 1080, "h": 1080,
             "note": "Feed placements that crop a vertical."},
    "16:9": {"label": "16:9 landscape", "w": 1920, "h": 1080,
             "note": "Back to landscape, from a vertical source."},
}
DEFAULT_RATIO = "9:16"

# How the frame is chosen.
#
# `g_auto` is Cloudinary's own subject detection and is the same gravity
# hub/video_library.background_url() has been delivering with; `g_auto:faces`
# is worth its own entry because a spokesperson spot has exactly one right
# answer and general saliency sometimes prefers a logo. `g_center` is here
# because a locked center crop is correct for a locked-off product shot and is
# the one option that cannot surprise anybody.
FOCUS = {
    "auto": {"gravity": "g_auto", "label": "Automatic (subject)"},
    "faces": {"gravity": "g_auto:faces", "label": "Faces"},
    "center": {"gravity": "g_center", "label": "Dead center (no detection)"},
}
DEFAULT_FOCUS = "auto"

# Crop, or keep the whole frame and fill the rest.
#
# The second mode exists because cropping is not always the right answer and
# a tool that only crops invites it to be used where it is wrong: a wide
# establishing shot, a two-up comparison, anything with a lower third. Padding
# against a blurred blow-up of the same frame keeps every pixel and reads as
# deliberate rather than as letterboxing.
MODES = {
    "crop": {"label": "Crop to fill", "note": "Fills the frame. Loses the edges."},
    "blur": {"label": "Keep the whole frame, blurred backdrop",
             "note": "Nothing is cut. Bars are a blurred blow-up of the shot."},
}
DEFAULT_MODE = "crop"

# ------------------------------------------------------------------ jobs

# How long a derived render may take before the page stops waiting on it. A
# Cloudinary eager transformation on a 60-second spot returns in seconds; a
# long recording with thirty cuts is minutes. Polling is client-side and this
# is only when the page gives up asking, not when Cloudinary gives up: a job
# that lands later is still filed, and the tool page lists it.
JOB_POLL_SECONDS = 4
JOB_TIMEOUT_SECONDS = 900

# Refuse rather than submit. Both numbers are Cloudinary account limits on
# most plans, and finding out by way of a failed derived asset costs the wait.
MAX_SOURCE_SECONDS = 3600
MAX_SOURCE_MB = 2000
