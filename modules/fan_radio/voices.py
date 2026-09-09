"""Fan Radio — casting and rendering, via ElevenLabs.

The question *what should this read sound like* is `hub/voice_casting.py`, and
this module asks it there rather than answering it again. It used to carry its
own copy of the characteristics, the accent aliases, the energy and delivery
word lists and the scoring — five tables that had already drifted from the
shared ones: no `transatlantic` accent, no `neutral` voice type, a shorter
energy vocabulary, and a scoring pass that awarded points in different amounts.
So the same client, cast in the two radio tools against the same ElevenLabs
account, got two different shortlists with two different reasons printed under
them, and nothing anywhere said which to believe.

What stays here is what is genuinely this module's:

* the transport — the key, the cache, the refusals ElevenLabs' own answers earn;
* the render, which goes through ``/with-timestamps`` so the character
  alignment gives the *measured* duration to the millisecond. There is no
  ffmpeg in the Hub's runtime, so where that endpoint is unavailable the plain
  one is used and the duration is labelled ``estimated`` rather than passed off
  as measured.

The MP3 estimate behind that fallback is `hub/radio_spec.mp3_seconds` now, and
it is worth saying why: the copy that lived here advanced four bytes per
candidate sync word rather than by the frame's own length, so it counted sync
patterns *inside* frame data as frames and reported durations several times the
real one — on the number a rep reads to decide whether a read fits its slot.
"""
from __future__ import annotations

import os
import re
import time
from urllib.parse import quote

import requests
from hub.audio_response import timestamp_audio, plain_audio

from hub import radio_spec, voice_casting

BASE = os.environ.get("ELEVENLABS_BASE_URL",
                      "https://api.elevenlabs.io/v1").rstrip("/")
MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")

_cache: dict = {"at": 0.0, "voices": []}

# Re-exported under the names this module's callers already use. There is one
# copy of each and it is the shared one — the arrangement
# `modules/radio_promo/voices.py` uses over the same module.
CHARACTERISTICS = voice_casting.CHARACTERISTICS
ACCENT_ALIASES = voice_casting.ACCENT_ALIASES
ENERGY_WORDS = voice_casting.ENERGY_WORDS
DELIVERY_WORDS = voice_casting.DELIVERY_WORDS
STYLE_BY_ENERGY = voice_casting.STYLE_BY_ENERGY
DEFAULT_WANT = voice_casting.DEFAULT_WANT

# The labelled fallback, from the one reading of it. Never a measurement in the
# sense `with-timestamps` is: exact for a constant-bitrate file, an estimate
# for a variable one, and every caller reports it as "estimated".
mp3_seconds = radio_spec.mp3_seconds


class VoiceError(RuntimeError):
    """Message is safe to show a user."""


def api_key() -> str:
    """The ElevenLabs key, under whichever name it is actually set.

    Through hub.config, which accepts ELEVENLABS_API too — the short spelling
    every other provider on this deployment uses. Read here at call time
    rather than at import so a key added in the Render dashboard takes effect
    on restart rather than needing the module reloaded.
    """
    try:
        from hub.config import settings
        if settings.elevenlabs_key:
            return settings.elevenlabs_key
    except Exception:  # noqa: BLE001 — standalone, or settings failed to build
        pass
    for name in ("ELEVENLABS_API", "ELEVENLABS_API_KEY", "ELEVENLABS_KEY"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def ready() -> bool:
    return bool(api_key())


def _headers(extra: dict | None = None) -> dict:
    if not ready():
        raise VoiceError("ElevenLabs key is not set. Add ELEVENLABS_API "
                         "(or ELEVENLABS_API_KEY).")
    head = {"xi-api-key": api_key()}
    head.update(extra or {})
    return head


def list_voices(force: bool = False) -> list[dict]:
    """The account's voices, in the shape `hub/voice_casting` scores.

    Normalised here rather than handed over raw: that module reads `name`,
    `description` and `labels`, and ElevenLabs publishes the description in
    either of two places depending on how a voice was made.
    """
    if not force and _cache["voices"] and time.time() - _cache["at"] < 300:
        return _cache["voices"]
    try:
        res = requests.get(f"{BASE}/voices", headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).") from exc
    if res.status_code >= 400:
        raise VoiceError(f"ElevenLabs refused the request (HTTP {res.status_code}).")
    voices = []
    for v in (res.json() or {}).get("voices") or []:
        labels = v.get("labels") or {}
        voices.append({"voice_id": v.get("voice_id"), "name": v.get("name"),
                       "category": v.get("category"),
                       "preview_url": v.get("preview_url"),
                       "description": v.get("description")
                       or labels.get("description") or "",
                       "labels": labels})
    _cache.update({"at": time.time(), "voices": voices})
    return voices


def match_voices(want: dict, count: int = 3) -> list[dict]:
    """The best `count` voices for the picked characteristics.

    The ranking is the shared one; what stays here is the refusal. That module
    ranks whatever list it is handed and never reaches the network, so "the
    account has no voices" is this module's answer to give — it is the half
    that knows the key was accepted and the list came back empty.
    """
    matched = voice_casting.match(list_voices(), want, count)
    if not matched:
        raise VoiceError("No voices came back from ElevenLabs. Check the API key "
                         "and that the account has voices in its library.")
    return matched


def match_quality(matched: list[dict]) -> str:
    """Why the ranking looks the way it does, in words a screen can print.

    An account of cloned voices carries no labels, so a shortlist with no
    reasons on it is the account's own order rather than a ranking — and
    saying so is what stops somebody reading a flat list as a bad answer to a
    good question.
    """
    try:
        total = len(list_voices())
    except VoiceError:
        total = 0
    return voice_casting.match_quality(matched, total)


def get_voice(voice_id: str) -> dict:
    """One voice by its ElevenLabs ID.

    The way past the ranking: a client who has already chosen a voice, or one
    cloned on the account and carrying no labels to be matched on, is named
    rather than hunted for in a shortlist that cannot score it.
    """
    voice_id = str(voice_id or "").strip()
    if not voice_id:
        raise VoiceError("Paste an ElevenLabs voice ID.")
    try:
        res = requests.get(f"{BASE}/voices/{quote(voice_id)}",
                           headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).") from exc
    if res.status_code == 404:
        raise VoiceError(f"No ElevenLabs voice with the ID {voice_id}.")
    if res.status_code >= 400:
        raise VoiceError(f"ElevenLabs refused the request (HTTP {res.status_code}).")
    return voice_casting.shape(res.json(), custom=True)


def _note_characters(script: str, voice_id: str) -> None:
    """Count this read against the monthly ElevenLabs allowance.

    The unit is characters of the script sent, not renders — ElevenLabs bills
    per character, so a 60-second read costs five times a tag.
    """
    try:
        from hub import quotas as _q
        _q.record_tts(script, module="fan_radio", model=MODEL, voice=voice_id)
    except Exception:                                    # noqa: BLE001
        pass


def render_audio(voice_id: str, script: str,
                 energy: str = "energetic", speed: float = 1.0,
                 prompt_strength: float | None = None) -> dict:
    """Return {'audio': bytes, 'seconds': float, 'measured': bool}."""
    if not voice_id:
        raise VoiceError("Pick a voice first.")
    try:
        speed = min(1.2, max(0.7, float(speed)))
    except (TypeError, ValueError):
        speed = 1.0
    from hub.customer_voices import ensure_usable, LibraryError
    try:
        ensure_usable(voice_id)
    except LibraryError as exc:
        raise VoiceError(str(exc)) from exc
    body = {
        "text": script,
        "model_id": MODEL,
        # `style` is the one characteristic that does more than rank: it is
        # sent on the render, so a read cast as explosive and rendered at the
        # default style is cast for nothing. From the shared table.
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.75,
                           "style": (voice_casting.style_for(energy) if prompt_strength is None
                                     else min(1.0, max(0.0, float(prompt_strength)))),
                           "speed": speed,
                           "use_speaker_boost": True},
    }
    try:
        res = requests.post(
            f"{BASE}/text-to-speech/{quote(voice_id)}/with-timestamps",
            headers=_headers({"Content-Type": "application/json"}),
            json=body, timeout=120)
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).")

    if res.status_code == 200:
        # Count accepted synthesis even when its response is malformed.
        _note_characters(script, voice_id)
        return timestamp_audio(res, mp3_seconds, VoiceError)
    if res.status_code not in (404, 405, 501):
        raise VoiceError(f"ElevenLabs returned {res.status_code} rendering audio.")

    # Plain endpoint, estimated duration, and said so.
    try:
        res = requests.post(f"{BASE}/text-to-speech/{quote(voice_id)}",
                            headers=_headers({"Content-Type": "application/json"}),
                            json=body, timeout=120)
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).")
    if res.status_code != 200:
        raise VoiceError(f"ElevenLabs returned {res.status_code} rendering audio.")
    _note_characters(script, voice_id)
    return plain_audio(res, mp3_seconds, VoiceError)


def account_check() -> dict:
    """What is left of the month's characters.

    Worth a button rather than a page load: it is one authenticated call, it
    generates nothing and it bills nothing, and a :60 is about twice a :30 of
    the allowance every time it is re-recorded.
    """
    try:
        res = requests.get(f"{BASE}/user/subscription", headers=_headers(),
                           timeout=30)
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).") from exc
    if res.status_code >= 400:
        raise VoiceError(f"ElevenLabs refused the request (HTTP {res.status_code}).")
    d = res.json() or {}
    used = d.get("character_count") or 0
    limit = d.get("character_limit") or 0
    return {"tier": d.get("tier"), "characters_used": used,
            "character_limit": limit, "remaining": limit - used}


def slug(text: str) -> str:
    return re.sub(r"-{2,}", "-",
                  re.sub(r"[^a-z0-9]+", "-", str(text or "").lower())).strip("-")
