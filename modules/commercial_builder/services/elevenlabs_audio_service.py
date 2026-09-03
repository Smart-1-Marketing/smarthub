"""ElevenLabs sound effects and music — the sourcing layer's audio half.

Wired from the two official ElevenLabs Agent Skills
(github.com/elevenlabs/skills: `sound-effects` and `music`). This sits beside
`elevenlabs_service.py` rather than inside it, and the split is the point:
that module renders **speech**, billed by the character, and everything about
its metering, its casting and its pronunciation dictionaries follows from
that. These two endpoints bill by the generation and return a piece of
*music* or a *noise*, which is a different unit, a different cache and a
different failure. One module answering to both names would make "how much
did the voice cost" unanswerable.

## What it is for

Two capabilities, both missing from this builder until now:

  * **Sound effects** — a whoosh on a transition, a door, a register, a
    stinger under the end card. Attached to a scene, the same tier as stock
    footage or an AI still.
  * **Music** — the Music step was a mood dropdown and a level slider over a
    preset library that generated nothing. It composes a real bed now, to the
    spot's own runway, so the track never has to be trimmed to fit.

The audio-only path needs no separate plumbing: a VO-only spot is the same
wizard with the visual steps unused, so both of these reach it as they are.

## Four rules, each a way this goes quietly wrong

**A limit is ElevenLabs', and a value outside it is refused by name.** The
published ranges are transcribed into `config.py` — 0.5-30s for an effect,
3s-10min for a bed. Clamping a 40-second request to 30 hands somebody a file
that is not what they asked for and says nothing, which is the failure
`hub/quote_validity.py` refuses for a quote window and `runway_service`
refuses for a scene it cannot cover.

**A retry never re-spends.** Both of these are billed per generation, and a
rep re-opening a storyboard, pressing back, or generating the same effect on
two scenes must not pay twice. The cache is keyed on the content — the
prompt and every parameter that changes the output — and it lives on the
**shared data disk**, not in a module-level dict: gunicorn runs two workers,
so an in-process cache is a cache that works about half the time, which is
the trap `modules/bg_remover` had to undo on the one module whose stated
design goal was not spending money twice.

**A duration is derived or it is not measured.** Nothing here decodes audio.
Both endpoints are asked for a constant-bitrate MP3, and at a known constant
bitrate the length is arithmetic on the byte count; where the response comes
back as something else, `seconds` is None and every reader says *not
measured* rather than printing a confident number. That is what makes
`music_length_mismatch` worth having.

**Nothing raises.** A provider that refuses, a network that drops and a key
that is unset each come back as a dict with the reason in it. Mock mode
produces no audio at all and says so — the alternative, a silent placeholder
file, is a spot that renders and is empty, which is the failure this module's
neighbours already carry notes about.

## Why `requests` rather than the `elevenlabs` SDK

CLAUDE.md: no new Python dependencies unless genuinely unavoidable. Every
other provider in this module speaks HTTP directly, `elevenlabs_service.py`
included, and adding an SDK for two POSTs would make this the only service
here that cannot run without one.
"""

import hashlib
import json
import os
import time

import requests

from ..config import (AUDIO_OUTPUT_FORMAT, AUDIO_OUTPUT_KBPS,
                      MUSIC_MAX_LENGTH_MS, MUSIC_MIN_LENGTH_MS, MUSIC_MODEL_ID,
                      SOUND_EFFECTS_DEFAULT_INFLUENCE,
                      SOUND_EFFECTS_MAX_DURATION_S, SOUND_EFFECTS_MIN_DURATION_S,
                      SOUND_EFFECTS_MODEL_ID)
from .elevenlabs_service import BASE_URL, _api_key

# The two endpoints, named once. `/v1/sound-generation` is the effects path
# behind the skill's `client.text_to_sound_effects.convert()`, and `/v1/music`
# is the composer behind `client.music.compose()`.
SFX_PATH = "/sound-generation"
MUSIC_PATH = "/music"

# Generation is slow — a bed for a :60 is tens of seconds of real work — and
# the read timeout has to allow for it or a perfectly good generation is
# thrown away after paying for it, which is the one failure mode worse than
# not generating at all.
CONNECT_TIMEOUT = 8
READ_TIMEOUT = 180


def is_live():
    """Whether a key is set. Read at call time — the module docstring of
    `elevenlabs_service` gives the reason at length, and this shares its
    reading rather than keeping a second one."""
    return bool(_api_key())


def _headers():
    return {"xi-api-key": _api_key(), "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Duration, derived rather than decoded.
# ---------------------------------------------------------------------------
def cbr_seconds(nbytes, kbps=AUDIO_OUTPUT_KBPS):
    """Seconds of audio in `nbytes` at a constant bitrate, or None.

    Only ever called when the response actually came back as the MP3 we asked
    for. Anything else — a VBR file, a different container, an empty body —
    answers None, and every caller renders that as *not measured*: a length
    the check cannot verify must not be reported as one it verified.
    """
    try:
        n = int(nbytes or 0)
        rate = int(kbps or 0)
    except (TypeError, ValueError):
        return None
    if n <= 0 or rate <= 0:
        return None
    return round((n * 8.0) / (rate * 1000.0), 2)


def _seconds_from(response, data):
    """The length of what came back, if it can be known."""
    ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype not in ("audio/mpeg", "audio/mp3"):
        return None
    return cbr_seconds(len(data or b""))


# ---------------------------------------------------------------------------
# The cache.
#
# A cache and nothing else: losing it costs one generation on the next press
# and no data at all, so it is plain files rather than `hub/jsonstore.py`,
# which mirrors into the database and is for state whose loss matters. That
# is `modules/bg_remover`'s reasoning, and so is the bound — an unbounded
# cache on the 5 GB disk takes every other module down with it.
#
# What is stored is the *stored asset*, not the bytes: by the time anything is
# worth remembering the audio is already in Cloudinary, so a few hundred bytes
# of JSON per entry stands in for a megabyte of MP3.
# ---------------------------------------------------------------------------
_TTL = 30 * 24 * 3600
_CACHE_MAX_ENTRIES = 4000


def cache_key(kind, scope, prompt, **params):
    """A digest of everything that changes the output.

    `scope` is the client. Deliberately part of the key rather than shared
    across the book: a hit points at a stored asset in somebody's own
    Cloudinary tree, and handing one client's folder to another as a cache hit
    would put their audio on another client's spot — the one mistake in this
    corner that cannot be undone by editing a row.
    """
    payload = json.dumps(
        {"kind": kind, "scope": scope or "", "prompt": (prompt or "").strip().lower(),
         "params": {k: params[k] for k in sorted(params)}},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_dir():
    """Where the shared copy lives, or None if we cannot have one.

    Never raises: a cache that can break the tool it accelerates is worse than
    no cache, so every failure here costs a generation and nothing else.
    """
    try:
        from hub import jsonstore
        path = os.path.join(jsonstore.data_dir("commercial_builder"), "audio_cache")
        os.makedirs(path, exist_ok=True)
        return path
    except Exception:                                    # noqa: BLE001
        return None


def cached(digest):
    """What was stored under this key last time, or None."""
    folder = _cache_dir()
    if not folder or not digest:
        return None
    path = os.path.join(folder, digest + ".json")
    try:
        if os.path.getmtime(path) <= time.time() - _TTL:
            return None
        with open(path, encoding="utf-8") as fh:
            row = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(row, dict) or not row.get("url"):
        return None
    return {**row, "cached": True}


def remember(digest, row):
    """Keep what a generation produced, so the next identical press is free.

    Never raises, and never reports whether it worked: a caller that had to
    branch on this would end up not calling it.
    """
    folder = _cache_dir()
    if not folder or not digest or not (row or {}).get("url"):
        return
    tmp = os.path.join(folder, digest + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({k: v for k, v in row.items() if k != "cached"}, fh)
        os.replace(tmp, os.path.join(folder, digest + ".json"))
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return
    _sweep()


def _sweep():
    """Drop what has expired, then the oldest until the cache is under its cap."""
    folder = _cache_dir()
    if not folder:
        return
    cutoff = time.time() - _TTL
    try:
        entries = []
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime < cutoff or name.endswith(".tmp"):
                try:
                    os.unlink(path)
                except OSError:
                    pass
                continue
            entries.append((st.st_mtime, path))
        for _, path in sorted(entries)[:max(0, len(entries) - _CACHE_MAX_ENTRIES)]:
            try:
                os.unlink(path)
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Sound effects
# ---------------------------------------------------------------------------
def generate_sound_effect(prompt, duration_seconds=None,
                          prompt_influence=SOUND_EFFECTS_DEFAULT_INFLUENCE,
                          loop=False):
    """One generated effect.

    `duration_seconds` may be None, which is ElevenLabs' own default and means
    "you decide from the prompt" — a two-word thump and a thirty-second
    ambience are not the same length and the model is better placed than a
    slider to know which was asked for.

    Returns a dict carrying `audio_bytes` on success, `error` on a refusal,
    and `_mock` with no audio at all when there is no key. `seconds` is the
    measured length where it could be derived and None where it could not.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"audio_bytes": None, "seconds": None,
                "error": "Say what the sound is — a sound effect needs a description."}

    if duration_seconds is not None:
        try:
            duration_seconds = round(float(duration_seconds), 2)
        except (TypeError, ValueError):
            return {"audio_bytes": None, "seconds": None,
                    "error": "The duration has to be a number of seconds."}
        # Refused by name, never clamped: see the module docstring.
        if not (SOUND_EFFECTS_MIN_DURATION_S <= duration_seconds
                <= SOUND_EFFECTS_MAX_DURATION_S):
            return {"audio_bytes": None, "seconds": None,
                    "error": (f"ElevenLabs generates effects between "
                              f"{SOUND_EFFECTS_MIN_DURATION_S:g} and "
                              f"{SOUND_EFFECTS_MAX_DURATION_S:g} seconds, so "
                              f"{duration_seconds:g}s cannot be asked for. Leave it "
                              f"blank to let the model choose from the description.")}

    try:
        influence = min(1.0, max(0.0, float(prompt_influence)))
    except (TypeError, ValueError):
        influence = SOUND_EFFECTS_DEFAULT_INFLUENCE

    if not is_live():
        return {"audio_bytes": None, "seconds": None, "_mock": True,
                "prompt": prompt, "duration_requested": duration_seconds,
                "note": ("Mock mode — no ELEVENLABS_API key is set, so no sound was "
                         "generated and this scene would render silent.")}

    payload = {"text": prompt, "model_id": SOUND_EFFECTS_MODEL_ID,
               "prompt_influence": influence, "loop": bool(loop),
               "output_format": AUDIO_OUTPUT_FORMAT}
    if duration_seconds is not None:
        payload["duration_seconds"] = duration_seconds
    return _post(SFX_PATH, payload, kind="sound_effect", prompt=prompt,
                 requested_seconds=duration_seconds,
                 model=SOUND_EFFECTS_MODEL_ID)


# ---------------------------------------------------------------------------
# Music
# ---------------------------------------------------------------------------
def compose_music(prompt, music_length_ms, model_id=MUSIC_MODEL_ID):
    """One composed bed, at the length the spot actually needs.

    `music_length_ms` is the caller's — `config.music_length_ms()` derives it
    from the spot's runway, so the track lands at the right length rather than
    being trimmed afterwards.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return {"audio_bytes": None, "seconds": None,
                "error": "Describe the music, or pick a mood to fill the box in."}
    try:
        length = int(music_length_ms)
    except (TypeError, ValueError):
        return {"audio_bytes": None, "seconds": None,
                "error": "The music length has to be a whole number of milliseconds."}
    if not (MUSIC_MIN_LENGTH_MS <= length <= MUSIC_MAX_LENGTH_MS):
        return {"audio_bytes": None, "seconds": None,
                "error": (f"ElevenLabs composes between {MUSIC_MIN_LENGTH_MS / 1000:g} "
                          f"seconds and {MUSIC_MAX_LENGTH_MS / 60000:g} minutes, so "
                          f"{length / 1000:g}s cannot be asked for.")}

    if not is_live():
        return {"audio_bytes": None, "seconds": None, "_mock": True,
                "prompt": prompt, "requested_seconds": round(length / 1000.0, 2),
                "note": ("Mock mode — no ELEVENLABS_API key is set, so no music was "
                         "composed and the render would carry no bed.")}

    payload = {"prompt": prompt, "music_length_ms": length,
               "model_id": model_id or MUSIC_MODEL_ID,
               "output_format": AUDIO_OUTPUT_FORMAT}
    return _post(MUSIC_PATH, payload, kind="music", prompt=prompt,
                 requested_seconds=round(length / 1000.0, 2),
                 model=model_id or MUSIC_MODEL_ID)


# ---------------------------------------------------------------------------
# The one request.
# ---------------------------------------------------------------------------
def _post(path, payload, *, kind, prompt, requested_seconds, model):
    """POST, meter, and answer in one shape whatever happened.

    The metering is the part worth reading. It records **after** the response,
    like `record_tts` and for the same reason, and it records a refusal too
    with `ok=False` — a refused generation spends nothing and stays out of
    every billable total, but a wall of them is what a spent allowance looks
    like from this side.
    """
    try:
        r = requests.post(f"{BASE_URL}{path}", headers=_headers(), json=payload,
                          timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
        r.raise_for_status()
        data = r.content or b""
        if not data:
            _meter(kind, requested_seconds, model, ok=False)
            return {"audio_bytes": None, "seconds": None,
                    "error": "ElevenLabs answered with an empty file."}
        seconds = _seconds_from(r, data)
        _meter(kind, seconds or requested_seconds, model, ok=True)
        return {"audio_bytes": data, "seconds": seconds,
                "requested_seconds": requested_seconds, "prompt": prompt,
                "model": model, "bytes": len(data),
                "output_format": AUDIO_OUTPUT_FORMAT}
    except Exception as exc:                             # noqa: BLE001
        _meter(kind, requested_seconds, model, ok=False)
        # The provider's own sentence, not an invented diagnosis of it —
        # `hub/openai_responses.py`'s rule. A body is quoted where there is
        # one, because "429" and "this voice is not on your plan" send
        # somebody to two different places.
        detail = ""
        body = getattr(getattr(exc, "response", None), "text", "") or ""
        if body:
            detail = f" {body.strip()[:200]}"
        return {"audio_bytes": None, "seconds": None,
                "requested_seconds": requested_seconds,
                "error": f"ElevenLabs refused it: {exc}{detail}"}


def _meter(kind, seconds, model, *, ok):
    """One generation, counted. Never raises — a meter must not cost a file.

    Recorded through `quotas.record_audio_generation`, which files it apart
    from the character-billed voice rows: folded in, a 30-second bed would be
    counted as a handful of characters of script and the voiceover estimate
    would quietly absorb a cost source that is not measured in characters at
    all.
    """
    try:
        from hub import quotas as _q
        _q.record_audio_generation(kind, module="commercial_builder",
                                   seconds=seconds, model=model, ok=ok)
    except Exception:                                    # noqa: BLE001
        pass
