"""Fan Radio — casting and rendering, via ElevenLabs.

Shares Radio Promo's approach so the two tools cast from the same pool and
report duration the same way:

* voices are matched against a wanted profile (gender / age / accent /
  energy / delivery) with a transparent score, so the reason a voice was
  suggested is visible rather than magic;
* renders go through ``/with-timestamps``, whose character alignment gives
  the *measured* duration to the millisecond — no ffmpeg in the Hub's
  runtime. If that endpoint isn't available the plain one is used and the
  duration is labelled ``estimated`` rather than passed off as measured.
"""
from __future__ import annotations

import base64
import os
import re
import time

import requests

BASE = os.environ.get("ELEVENLABS_BASE_URL",
                      "https://api.elevenlabs.io/v1").rstrip("/")
MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")

_cache: dict = {"at": 0.0, "voices": []}

ACCENT_ALIASES = {
    "american": ["american", "us", "usa", "transatlantic"],
    "british": ["british", "english", "uk", "received"],
    "australian": ["australian", "aussie"],
    "any": [],
}
ENERGY_WORDS = {
    "laid_back": ["calm", "relaxed", "soothing", "soft", "gentle"],
    "conversational": ["conversational", "casual", "natural", "friendly", "warm"],
    "energetic": ["energetic", "upbeat", "excited", "confident", "expressive"],
    "explosive": ["intense", "powerful", "dramatic", "strong", "energetic"],
}
DELIVERY_WORDS = {
    "announcer": ["announcer", "commercial", "advertisement", "broadcast", "promo"],
    "narrator": ["narration", "narrator", "audiobook", "documentary"],
    "best_friend": ["conversational", "casual", "friendly", "social media"],
    "spokesperson": ["commercial", "advertisement", "professional", "corporate"],
    "character": ["characters", "animation", "video games", "character"],
}
STYLE_BY_ENERGY = {"laid_back": 0.15, "conversational": 0.3,
                   "energetic": 0.55, "explosive": 0.75}


class VoiceError(RuntimeError):
    """Message is safe to show a user."""


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


def _blob(voice: dict) -> str:
    labels = voice.get("labels") or {}
    return " ".join([str(voice.get("name") or ""),
                     str(voice.get("description") or ""),
                     " ".join(f"{k} {v}" for k, v in labels.items())]).lower()


def list_voices(force: bool = False) -> list[dict]:
    if not force and _cache["voices"] and time.time() - _cache["at"] < 300:
        return _cache["voices"]
    try:
        res = requests.get(f"{BASE}/voices", headers=_headers(), timeout=30)
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).")
    if res.status_code != 200:
        raise VoiceError(f"ElevenLabs returned {res.status_code} listing voices.")
    voices = (res.json() or {}).get("voices") or []
    _cache.update({"at": time.time(), "voices": voices})
    return voices


def match_voices(want: dict, count: int = 3) -> list[dict]:
    """Score the pool against a wanted profile. Reasons are returned."""
    want = want or {}
    out = []
    for v in list_voices():
        labels = v.get("labels") or {}
        blob = _blob(v)
        score, reasons = 0, []

        gender = str(labels.get("gender") or "").lower()
        if want.get("gender") in ("male", "female"):
            if gender == want["gender"]:
                score += 4
                reasons.append(want["gender"])
            elif gender:
                score -= 2

        age = str(labels.get("age") or "").lower().replace(" ", "_")
        if want.get("age") and age:
            if want["age"] in age:
                score += 2
                reasons.append(age.replace("_", " "))

        accent = str(labels.get("accent") or "").lower()
        aliases = ACCENT_ALIASES.get(str(want.get("accent") or "any"), [])
        if aliases and any(a in accent for a in aliases):
            score += 2
            reasons.append(accent or want["accent"])

        for word in ENERGY_WORDS.get(str(want.get("energy") or ""), []):
            if word in blob:
                score += 1
                reasons.append(word)
                break
        for word in DELIVERY_WORDS.get(str(want.get("delivery") or ""), []):
            if word in blob:
                score += 2
                reasons.append(word)
                break
        if "advertisement" in blob or "commercial" in blob:
            score += 1

        out.append({
            "voice_id": v.get("voice_id"), "name": v.get("name"),
            "preview_url": v.get("preview_url"),
            "gender": gender, "age": age, "accent": accent,
            "descriptor": str(labels.get("descriptive")
                              or labels.get("description") or ""),
            "use_case": str(labels.get("use_case") or ""),
            "custom": (v.get("category") or "") == "cloned",
            "match_reasons": sorted(set(reasons))[:4], "score": score,
        })
    out.sort(key=lambda r: (-r["score"], r["name"] or ""))
    return out[:max(1, count)]


def _mp3_seconds(data: bytes) -> float:
    """Rough duration from MP3 frame headers — the labelled fallback."""
    bitrates = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256,
                320, 0]
    i, total, frames = 0, 0, 0
    while i < len(data) - 4 and frames < 20000:
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            br = bitrates[(data[i + 2] >> 4) & 0x0F]
            if br:
                total += br
                frames += 1
                i += 4
                continue
        i += 1
    if not frames:
        return 0.0
    return round(frames * 1152 / 44100.0, 2)


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
                 energy: str = "energetic") -> dict:
    """Return {'audio': bytes, 'seconds': float, 'measured': bool}."""
    if not voice_id:
        raise VoiceError("Pick a voice first.")
    body = {
        "text": script,
        "model_id": MODEL,
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.75,
                           "style": STYLE_BY_ENERGY.get(energy, 0.4),
                           "use_speaker_boost": True},
    }
    try:
        res = requests.post(
            f"{BASE}/text-to-speech/{voice_id}/with-timestamps",
            headers=_headers({"Content-Type": "application/json"}),
            json=body, timeout=120)
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).")

    if res.status_code == 200:
        # Recorded on acceptance, not on a successful parse: the fall-through
        # below re-renders, and both requests spend characters.
        _note_characters(script, voice_id)
        try:
            payload = res.json()
            audio = base64.b64decode(payload["audio_base64"])
            align = (payload.get("normalized_alignment")
                     or payload.get("alignment") or {})
            ends = align.get("character_end_times_seconds") or []
            if ends:
                return {"audio": audio, "seconds": round(float(ends[-1]), 2),
                        "measured": True}
            return {"audio": audio, "seconds": _mp3_seconds(audio),
                    "measured": False}
        except (KeyError, ValueError):
            pass

    # Plain endpoint, estimated duration, and said so.
    try:
        res = requests.post(f"{BASE}/text-to-speech/{voice_id}",
                            headers=_headers({"Content-Type": "application/json"}),
                            json=body, timeout=120)
    except requests.RequestException as exc:
        raise VoiceError(f"Couldn't reach ElevenLabs ({exc.__class__.__name__}).")
    if res.status_code != 200:
        raise VoiceError(f"ElevenLabs returned {res.status_code} rendering audio.")
    _note_characters(script, voice_id)
    audio = res.content
    return {"audio": audio, "seconds": _mp3_seconds(audio), "measured": False}


def slug(text: str) -> str:
    return re.sub(r"-{2,}", "-",
                  re.sub(r"[^a-z0-9]+", "-", str(text or "").lower())).strip("-")
