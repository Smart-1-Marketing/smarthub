"""Reading a video's audio level without ever downloading the video.

Cloudinary will render the audio track of a video asset as a waveform image:
change the extension to .png and add `fl_waveform`. That picture is the whole
trick this module turns. A 2000-pixel-wide PNG of a 60-second spot is about
30 KB and arrives in under a second, and each of its columns is the audio
envelope of one 30ms slice -- which is a loudness reading, at a resolution
finer than any cut worth making, from an HTTP GET.

Why this and not a transcript. The obvious way to find dead air is to
transcribe and look at the gaps between words, and it is wrong here in a way
that only shows up on real client work: a transcript detects SPEECH, so a
music bed under a montage, a sound effect, and a deliberate held beat all read
as silence and all get cut. The waveform detects SOUND. On a spot with a music
bed the transcript approach removes the montage; this one correctly finds
almost nothing to cut and says so.

What it is not. The waveform is a picture, so it carries no dB scale and no
channel information, and it is a peak envelope rather than RMS -- a single
loud click inside a silence raises that column. Every threshold here is
therefore relative to the loudest column in the clip, and the plan this feeds
is shown to a person before anything is rendered.
"""
from __future__ import annotations

import io
from urllib.parse import quote

import requests

from . import config


class WaveformError(RuntimeError):
    """The audio could not be read. Carries a sentence fit to show a person."""


def width_for(duration: float | int | None) -> int:
    """How wide to ask for, given how long the clip is.

    Clamped at both ends. The floor keeps a five-second clip from being
    analysed at 100 columns, where a column is 50ms and rounding decides the
    cuts; the ceiling keeps a 40-minute recording from asking Cloudinary to
    render an image whose columns would each be a third of a second anyway.
    """
    seconds = float(duration or 0)
    if seconds <= 0:
        return config.WAVEFORM_MAX_WIDTH
    wanted = int(seconds * config.WAVEFORM_COLUMNS_PER_SECOND)
    return max(config.WAVEFORM_MIN_WIDTH,
               min(config.WAVEFORM_MAX_WIDTH, wanted))


def waveform_url(base: str, public_id: str, *, width: int,
                 height: int = config.WAVEFORM_HEIGHT) -> str:
    """The delivery URL for the waveform image.

    Black on white deliberately: the reading below counts dark pixels, and a
    white-on-black waveform (Cloudinary's default) would have it counting the
    background instead. The same URL is what the tool page displays behind the
    cut markers, so the person sees the picture the decision was made from
    rather than a redrawing of it.
    """
    pid = quote(str(public_id or ""), safe="/")
    return (f"{base}/fl_waveform,co_black,b_white,"
            f"w_{int(width)},h_{int(height)}/{pid}.png")


def amplitudes(url: str, *, timeout: int = 30) -> list[float]:
    """One 0..1 loudness reading per column of the waveform image.

    The waveform is drawn symmetrically about the middle of the image, so the
    height of the dark ink in a column is proportional to that slice's peak
    level. Counting dark pixels per column is therefore the reading, and it
    needs no assumption about how Cloudinary draws the envelope beyond "louder
    is taller" -- which is the only property being relied on.

    Normalised against the tallest column rather than against the image
    height: a quietly recorded interview never approaches full scale, and an
    absolute threshold would find no sound in it at all.
    """
    try:
        from PIL import Image
    except ImportError as exc:                      # noqa: BLE001
        raise WaveformError("Pillow is not installed on this server, so the "
                            "audio could not be read.") from exc
    try:
        resp = requests.get(url, timeout=timeout)
    except Exception as exc:                        # noqa: BLE001 — network
        raise WaveformError("Cloudinary did not answer when asked for this "
                            "clip's waveform.") from exc
    if resp.status_code >= 400:
        # Cloudinary names the reason in a header rather than in the body, and
        # the common one is worth translating: an asset with no audio track
        # has no waveform to draw.
        why = resp.headers.get("x-cld-error") or f"HTTP {resp.status_code}"
        raise WaveformError(f"Cloudinary could not draw a waveform for this "
                            f"clip ({why}). A video with no audio track has "
                            f"no dead air to cut.")
    try:
        img = Image.open(io.BytesIO(resp.content)).convert("L")
    except Exception as exc:                        # noqa: BLE001
        raise WaveformError("The waveform image came back unreadable.") from exc

    width, height = img.size
    if width < 2 or height < 2:
        raise WaveformError("The waveform image came back empty.")

    # Threshold, then collapse the image to a single row.
    #
    # 200 of 255, not 128: the waveform is anti-aliased and its edge columns
    # are mid-grey. A midpoint threshold drops the quietest real audio, which
    # is exactly the audio this is trying to tell apart from silence.
    #
    # The collapse is a BOX resize to one pixel tall, which is the mean of
    # each column -- the same number as counting ink pixels and dividing, and
    # it is done in Pillow's C rather than in a half-million-iteration Python
    # loop. That loop was the first version of this function and it cost most
    # of a second on a four-minute recording, on the web worker, inside the
    # request that asked for it.
    mask = img.point(lambda p: 255 if p < 200 else 0, mode="L")
    raw = list(mask.resize((width, 1), Image.BOX).getdata())

    peak = max(raw)
    if peak <= 0:
        # Every column blank. Silent track, or a waveform that rendered as a
        # flat line -- indistinguishable here, and both mean there is nothing
        # to cut against.
        raise WaveformError("This clip's audio reads as silent from end to "
                            "end, so there is nothing to cut against.")
    return [c / peak for c in raw]


def read(base: str, public_id: str, duration: float | int | None) -> dict:
    """The reading both the analysis and the page work from.

    Returns the levels, the image the levels came from, and the seconds each
    column covers -- the last of which is what every timestamp downstream is
    computed with, so it is carried rather than recomputed.
    """
    width = width_for(duration)
    url = waveform_url(base, public_id, width=width)
    levels = amplitudes(url)
    seconds = float(duration or 0)
    if seconds <= 0:
        raise WaveformError("This clip's duration is unknown, so its waveform "
                            "cannot be placed on a timeline.")
    return {
        "url": url,
        "levels": levels,
        "columns": len(levels),
        "seconds_per_column": seconds / len(levels),
        "duration": seconds,
    }
