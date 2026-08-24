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

import base64
import os
import re
import struct
import time
from urllib.parse import quote

import requests

BASE = os.environ.get("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io/v1").rstrip("/")
MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")

_cache: dict = {"at": 0.0, "voices": []}

ACCENT_ALIASES = {
    "american": ["american", "us", "usa", "transatlantic"],
    "british": ["british", "english", "uk", "received"],
    "australian": ["australian", "aussie"],
    "transatlantic": ["transatlantic", "american", "british"],
}
ENERGY_WORDS = {
    "laid_back": ["calm", "relaxed", "soothing", "soft", "chill", "gentle", "meditative"],
    "conversational": ["conversational", "casual", "natural", "friendly", "warm"],
    "energetic": ["energetic", "upbeat", "excited", "confident", "expressive"],
    "explosive": ["intense", "powerful", "shouty", "dramatic", "strong", "energetic"],
}
DELIVERY_WORDS = {
    "announcer": ["announcer", "commercial", "advertisement", "broadcast", "promo"],
    "narrator": ["narration", "narrator", "audiobook", "documentary"],
    "best_friend": ["conversational", "casual", "friendly", "social media"],
    "spokesperson": ["commercial", "advertisement", "professional", "corporate", "news"],
    "character": ["characters", "animation", "video games", "character"],
}
STYLE_BY_ENERGY = {"laid_back": 0.15, "conversational": 0.3,
                   "energetic": 0.55, "explosive": 0.75}


class VoiceError(RuntimeError):
    """Safe to show a user."""


def api_key() -> str:
    return (os.environ.get("ELEVENLABS_API_KEY") or "").strip()


def ready() -> bool:
    return bool(api_key())


def _headers(extra: dict | None = None) -> dict:
    if not ready():
        raise VoiceError("ElevenLabs key is not set. Add ELEVENLABS_API_KEY.")
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
    labels = voice.get("labels") or {}
    bag = _norm(" ".join(str(x) for x in [
        labels.get("description"), labels.get("use_case"), labels.get("usecase"),
        labels.get("descriptive"), voice.get("description"), voice.get("name")]))
    score, reasons = 0, []

    want_gender = want.get("gender")
    if want_gender and want_gender != "any":
        if _norm(labels.get("gender")) == _norm(want_gender):
            score += 5
            reasons.append(want_gender)
        elif labels.get("gender"):
            score -= 4
    want_age = want.get("age")
    if want_age and want_age != "any" and _norm(labels.get("age")) == _norm(want_age):
        score += 3
        reasons.append(str(labels.get("age")).replace("_", " "))
    want_accent = want.get("accent")
    if want_accent and want_accent != "any":
        aliases = ACCENT_ALIASES.get(want_accent, [want_accent])
        if any(_norm(a) in _norm(labels.get("accent")) for a in aliases):
            score += 3
            reasons.append(labels.get("accent"))
    for key, table in (("energy", ENERGY_WORDS), ("delivery", DELIVERY_WORDS)):
        picked = want.get(key)
        if picked:
            hits = [w for w in table.get(picked, []) if _norm(w) in bag]
            score += min(len(hits), 2) * 2
            if hits:
                reasons.append(picked.replace("_", " "))
    for term in want.get("search_terms") or []:
        if _norm(term) and _norm(term) in bag:
            score += 1
    if "advertisement" in bag or "commercial" in bag:
        score += 1
    seen, unique = set(), []
    for r in reasons:
        if r and r not in seen:
            seen.add(r)
            unique.append(r)
    return score, unique


def _shape(voice: dict, score: int = 0, reasons: list | None = None,
           custom: bool = False) -> dict:
    labels = voice.get("labels") or {}
    return {"voice_id": voice.get("voice_id"), "name": voice.get("name"),
            "preview_url": voice.get("preview_url"),
            "accent": labels.get("accent", ""), "age": labels.get("age", ""),
            "gender": labels.get("gender", ""),
            "descriptor": labels.get("description") or labels.get("descriptive") or "",
            "use_case": labels.get("use_case") or labels.get("usecase") or "",
            "match_reasons": reasons or (["added by ID"] if custom else []),
            "score": score, "custom": custom}


def match_voices(want: dict, count: int = 3) -> list[dict]:
    voices = list_voices()
    ranked = []
    for v in voices:
        score, reasons = _score(v, want)
        ranked.append((score, reasons, v))
    ranked.sort(key=lambda row: row[0], reverse=True)
    picked = ranked[:max(count, 1)]
    if not picked:
        raise VoiceError("No voices came back from ElevenLabs. Check the API key "
                         "and that the account has voices in its library.")
    return [_shape(v, score, reasons) for score, reasons, v in picked]


def get_voice(voice_id: str) -> dict:
    res = requests.get(f"{BASE}/voices/{quote(voice_id)}", headers=_headers(), timeout=30)
    if res.status_code == 404:
        raise VoiceError(f"No ElevenLabs voice with the ID {voice_id}.")
    if res.status_code >= 400:
        raise VoiceError(f"ElevenLabs refused the request (HTTP {res.status_code}).")
    return _shape(res.json(), custom=True)


# ------------------------------------------------------------------- render
_BITRATES = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0]
_RATES = [44100, 48000, 32000, 0]


def mp3_seconds(data: bytes) -> float | None:
    """Duration from MP3 frame headers. Only used when the timestamped
    endpoint is unavailable — it is an estimate, not a measurement."""
    try:
        i, total, frames = 0, 0.0, 0
        n = len(data)
        if data[:3] == b"ID3":
            size = struct.unpack(">4B", data[6:10])
            i = 10 + (size[0] << 21 | size[1] << 14 | size[2] << 7 | size[3])
        while i + 4 <= n and frames < 200000:
            if data[i] != 0xFF or (data[i + 1] & 0xE0) != 0xE0:
                i += 1
                continue
            bitrate = _BITRATES[(data[i + 2] & 0xF0) >> 4]
            rate = _RATES[(data[i + 2] & 0x0C) >> 2]
            if not bitrate or not rate:
                i += 1
                continue
            padding = (data[i + 2] & 0x02) >> 1
            length = int(144000 * bitrate / rate) + padding
            total += 1152 / rate
            frames += 1
            i += max(length, 1)
        return round(total, 2) if frames else None
    except Exception:                                        # noqa: BLE001
        return None


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
            # Characters are spent the moment ElevenLabs accepts the request,
            # whether or not the alignment we wanted came back with it. Record
            # here rather than at the return, or the fall-through below is a
            # render nobody counted and a second one billed on top of it.
            _note_characters(script, voice_id)
            body = res.json()
            audio = base64.b64decode(body.get("audio_base64") or "")
            align = body.get("normalized_alignment") or body.get("alignment") or {}
            ends = align.get("character_end_times_seconds") or []
            if audio and ends:
                return {"audio": audio, "seconds": round(float(ends[-1]), 2),
                        "measured": True}
            if audio:
                return {"audio": audio, "seconds": mp3_seconds(audio), "measured": False}
    except (requests.RequestException, ValueError):
        pass                                   # fall through to the plain endpoint

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
    return {"audio": res.content, "seconds": mp3_seconds(res.content), "measured": False}


def account_check() -> dict:
    res = requests.get(f"{BASE}/user/subscription", headers=_headers(), timeout=30)
    if res.status_code >= 400:
        raise VoiceError(f"ElevenLabs refused the request (HTTP {res.status_code}).")
    d = res.json()
    used = d.get("character_count") or 0
    limit = d.get("character_limit") or 0
    return {"tier": d.get("tier"), "characters_used": used,
            "character_limit": limit, "remaining": limit - used}
