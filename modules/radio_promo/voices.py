"""ElevenLabs: list voices, match them to the picked characteristics, render.

Ported from radio-studio's `src/services/elevenlabs.js`, with one addition.
The studio measured a finished render with ffmpeg. There is no ffmpeg in the
Hub's Python runtime, so the render goes through the `with-timestamps`
endpoint instead, which returns character-level alignment — the last
character's end time *is* the measured duration, to the millisecond, with no
binary dependency. If that endpoint is unavailable the plain endpoint is used
and the duration falls back to an MP3 frame-header estimate.
"""
from __future__ import annotations

import os
import re
import time
from urllib.parse import quote

import requests
from hub.audio_response import timestamp_audio, plain_audio

from hub import radio_spec, voice_casting

BASE = os.environ.get("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io/v1").rstrip("/")
MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")

_cache: dict = {"at": 0.0, "voices": []}

# The characteristics, the word tables and the scoring moved to
# hub/voice_casting.py when the Commercial Builder needed the same casting
# step. They are re-exported here under their old names so this module's
# callers and anything importing them are unchanged -- there is one copy, and
# it is the shared one.
ACCENT_ALIASES = voice_casting.ACCENT_ALIASES
ENERGY_WORDS = voice_casting.ENERGY_WORDS
DELIVERY_WORDS = voice_casting.DELIVERY_WORDS
STYLE_BY_ENERGY = voice_casting.STYLE_BY_ENERGY


class VoiceError(RuntimeError):
    """Safe to show a user."""


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


def _norm(s) -> str:
    return re.sub(r"[^a-z]", "", str(s or "").lower())


def list_voices(force: bool = False) -> list[dict]:
    if not force and _cache["voices"] and time.time() - _cache["at"] < 300:
        return _cache["voices"]
    try:
        res = requests.get(f"{BASE}/voices", headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).") from exc
    if res.status_code >= 400:
        raise VoiceError(f"ElevenLabs refused the request (HTTP {res.status_code}).")
    voices = []
    for v in res.json().get("voices", []):
        labels = v.get("labels") or {}
        voices.append({"voice_id": v.get("voice_id"), "name": v.get("name"),
                       "category": v.get("category"),
                       "preview_url": v.get("preview_url"),
                       "description": v.get("description") or labels.get("description") or "",
                       "labels": labels})
    _cache.update(at=time.time(), voices=voices)
    return voices


def _score(voice: dict, want: dict) -> tuple[int, list[str]]:
    return voice_casting.score(voice, want)


def _shape(voice: dict, score: int = 0, reasons: list | None = None,
           custom: bool = False) -> dict:
    return voice_casting.shape(voice, score, reasons, custom)


def match_voices(want: dict, count: int = 3) -> list[dict]:
    """The best `count` voices for the picked characteristics.

    The ranking is hub/voice_casting's; what stays here is the refusal. That
    module ranks whatever list it is handed and never reaches the network, so
    "the account has no voices" is this module's answer to give -- it is the
    half that knows the key was accepted and the list came back empty.
    """
    matched = voice_casting.match(list_voices(), want, count)
    if not matched:
        raise VoiceError("No voices came back from ElevenLabs. Check the API key "
                         "and that the account has voices in its library.")
    return matched


def get_voice(voice_id: str) -> dict:
    try:
        res = requests.get(f"{BASE}/voices/{quote(voice_id, safe='')}", headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        raise VoiceError("Could not reach ElevenLabs. Try refreshing the voice status.") from exc
    if res.status_code == 404:
        raise VoiceError(f"No ElevenLabs voice with the ID {voice_id}.")
    if res.status_code >= 400:
        raise VoiceError(f"ElevenLabs refused the request (HTTP {res.status_code}).")
    try:
        raw = res.json()
    except ValueError as exc:
        raise VoiceError("ElevenLabs returned an unreadable voice response.") from exc
    if not isinstance(raw, dict) or raw.get("voice_id") != voice_id:
        raise VoiceError("ElevenLabs did not confirm the requested voice ID.")
    verification = raw.get("voice_verification") or {}
    needs_verification = not (verification.get("is_verified") is True or verification.get("requires_verification") is False)
    return {**_shape(raw, custom=True), "requires_verification": needs_verification}


def clone_voice(name: str, samples: list[tuple[str, bytes, str]],
                description: str = "", remove_background_noise: bool = False) -> dict:
    """Create an ElevenLabs Instant Voice Clone from authorized samples.

    Sample bytes intentionally never touch the Hub disk; they are relayed to
    the configured ElevenLabs account and discarded when this request ends.
    """
    if not samples:
        raise VoiceError("Upload at least one voice recording.")
    files = [("files", (filename, data, mime or "audio/mpeg"))
             for filename, data, mime in samples]
    form = {"name": name, "description": description,
            "remove_background_noise": "true" if remove_background_noise else "false"}
    try:
        res = requests.post(f"{BASE}/voices/add", headers=_headers(), data=form,
                            files=files, timeout=180)
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).") from exc
    if res.status_code >= 400:
        try:
            detail = res.json().get("detail") or res.json().get("message")
        except ValueError:
            detail = ""
        raise VoiceError(f"ElevenLabs could not create that clone (HTTP {res.status_code})"
                         + (f": {detail}" if detail else "."))
    created = res.json()
    if not isinstance(created, dict):
        raise VoiceError("ElevenLabs did not return a voice ID for the clone.")
    voice_id = created.get("voice_id")
    if not voice_id:
        raise VoiceError("ElevenLabs did not return a voice ID for the clone.")
    _cache.update(at=0.0, voices=[])
    # The create response is authoritative. A failed metadata lookup must never
    # turn an accepted clone into a request to create another paid clone.
    return {"voice_id": voice_id, "name": name, "description": description,
            "requires_verification": created.get("requires_verification", True),
            "custom": True, "preview_url": ""}


# ------------------------------------------------------------------- render
# The MP3 estimate moved to `hub/radio_spec.mp3_seconds` when Fan Radio needed
# it too -- and its copy of it was wrong, advancing a fixed four bytes per
# candidate sync word rather than by the frame's own length, so it counted the
# same audio many times over. Re-exported under the old name; this module's own
# callers and `test_radio_ads.py` read it from here.
mp3_seconds = radio_spec.mp3_seconds


def _note_characters(script: str, voice_id: str) -> None:
    """Count this read against the monthly ElevenLabs allowance.

    ElevenLabs bills per character of the text sent, so the unit is the length
    of `script`, not one render — a 60-second read costs five times a tag.
    """
    try:
        from hub import quotas as _q
        _q.record_tts(script, module="radio_promo", model=MODEL, voice=voice_id)
    except Exception:                                            # noqa: BLE001
        pass


def render_audio(voice_id: str, script: str, energy: str = "conversational") -> dict:
    """Render to MP3. Returns ``{"audio": bytes, "seconds": float|None,
    "measured": bool}``."""
    from hub.customer_voices import ensure_usable, LibraryError
    try:
        ensure_usable(voice_id)
    except LibraryError as exc:
        raise VoiceError(str(exc)) from exc
    style = STYLE_BY_ENERGY.get(energy, 0.3)
    payload = {"text": script, "model_id": MODEL,
               "voice_settings": {"stability": 0.45, "similarity_boost": 0.8,
                                  "style": style, "use_speaker_boost": True}}
    url = f"{BASE}/text-to-speech/{quote(voice_id)}"
    query = "?output_format=mp3_44100_128"

    # Preferred: alignment comes back with the audio, so the length is measured.
    try:
        res = requests.post(f"{url}/with-timestamps{query}",
                            headers=_headers({"Content-Type": "application/json"}),
                            json=payload, timeout=180)
        if res.status_code < 400:
            # Count accepted synthesis even when its response is malformed.
            _note_characters(script, voice_id)
            return timestamp_audio(res, mp3_seconds, VoiceError)
        if res.status_code not in (404, 405, 501):
            raise VoiceError(f"ElevenLabs render failed (HTTP {res.status_code}).")
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).") from exc

    try:
        res = requests.post(url + query,
                            headers=_headers({"Content-Type": "application/json",
                                              "Accept": "audio/mpeg"}),
                            json=payload, timeout=180)
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).") from exc
    if res.status_code >= 400:
        raise VoiceError(f"ElevenLabs render failed (HTTP {res.status_code}).")
    _note_characters(script, voice_id)
    return plain_audio(res, mp3_seconds, VoiceError)


def account_check() -> dict:
    res = requests.get(f"{BASE}/user/subscription", headers=_headers(), timeout=30)
    if res.status_code >= 400:
        raise VoiceError(f"ElevenLabs refused the request (HTTP {res.status_code}).")
    d = res.json()
    used = d.get("character_count") or 0
    limit = d.get("character_limit") or 0
    return {"tier": d.get("tier"), "characters_used": used,
            "character_limit": limit, "remaining": limit - used}
