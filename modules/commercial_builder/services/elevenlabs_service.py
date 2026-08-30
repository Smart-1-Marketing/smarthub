"""ElevenLabs service — Voice Studio (spec section 9).

Voiceover generation, kept separate from HeyGen's spokesperson audio (that
audio is baked into the talking-head clip; this is for scenes with no
on-screen presenter). Also manages per-client pronunciation dictionaries so
local place names ("Gahanna", "Scioto") stop getting mangled.

The key is read at CALL time, through the Hub's settings, for the reason
heygen_service gives at length: read at import under one spelling, a key
added to Render as ELEVENLABS_API never reached this module and every
voiceover silently came back mock — a commercial with no voice track, and a
dashboard chip reading "mock mode" beside a key that was plainly set.
"""

import os

import requests

BASE_URL = "https://api.elevenlabs.io/v1"
# Named only so usage reporting can price the render: the Flash and Turbo
# models bill half a credit per character and the rest bill one, so a spend
# estimate that did not know the model would be wrong by a factor of two.
# Not sent in the payload — that stays ElevenLabs' account default, as before.
MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")

STYLE_TO_MOCK_VOICE = {
    "Male": "Adam", "Female": "Rachel", "Youthful": "Jessie", "Authoritative": "Marcus",
    "Conversational": "Dana", "Energetic": "Kai", "Luxury": "Vivienne", "Announcer": "Grant",
}


def _api_key():
    """Read at call time, not import time — see the module docstring."""
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


def is_live():
    return bool(_api_key())


def _headers():
    return {"xi-api-key": _api_key(), "Content-Type": "application/json"}


# Mock labels, so casting can be exercised without a key. Each mock voice
# carries the labels ElevenLabs would put on a voice of that kind — otherwise
# the casting step in mock mode ranks eight identical rows and reads as
# broken, when what is wrong is only that there is no key.
MOCK_LABELS = {
    "Adam": {"gender": "male", "age": "middle_aged", "accent": "american",
             "description": "confident commercial announcer", "use_case": "advertisement"},
    "Rachel": {"gender": "female", "age": "young", "accent": "american",
               "description": "warm conversational narrator", "use_case": "narration"},
    "Jessie": {"gender": "female", "age": "young", "accent": "american",
               "description": "energetic upbeat social media", "use_case": "social media"},
    "Marcus": {"gender": "male", "age": "old", "accent": "american",
               "description": "authoritative powerful broadcast", "use_case": "advertisement"},
    "Dana": {"gender": "female", "age": "middle_aged", "accent": "american",
             "description": "casual friendly conversational", "use_case": "conversational"},
    "Kai": {"gender": "male", "age": "young", "accent": "american",
            "description": "excited energetic expressive", "use_case": "advertisement"},
    "Vivienne": {"gender": "female", "age": "middle_aged", "accent": "british",
                 "description": "calm soothing premium", "use_case": "narration"},
    "Grant": {"gender": "male", "age": "middle_aged", "accent": "transatlantic",
              "description": "dramatic promo announcer", "use_case": "advertisement"},
}


def list_voices():
    """Every voice on the account, carrying the labels casting matches on.

    `labels` and `preview_url` used to be thrown away here -- the shape kept
    was `{voice_id, name, style}`, where `style` was the label values joined
    with commas. That is enough to fill a dropdown and not enough to rank
    anything or to play a sample, which is why this tool's Voice Studio was a
    flat list of names while the Radio Promo builder, against the same account,
    offered three ranked voices with a preview on each. `style` is still
    returned so nothing that reads it breaks.
    """
    if not is_live():
        return [{"voice_id": f"mock_{name.lower()}", "name": name, "style": style,
                  "labels": MOCK_LABELS.get(name, {}),
                  "description": MOCK_LABELS.get(name, {}).get("description", ""),
                  "preview_url": "", "_mock": True}
                for style, name in STYLE_TO_MOCK_VOICE.items()]
    try:
        r = requests.get(f"{BASE_URL}/voices", headers=_headers(), timeout=8)
        r.raise_for_status()
        voices = r.json().get("voices", [])
        out = []
        for v in voices:
            labels = v.get("labels") or {}
            out.append({"voice_id": v.get("voice_id"), "name": v.get("name"),
                        "style": ", ".join(str(x) for x in labels.values()),
                        "labels": labels,
                        "description": v.get("description") or labels.get("description") or "",
                        "preview_url": v.get("preview_url") or ""})
        return out
    except Exception:
        return []


def cast_voices(want, count=3):
    """The voices that best fit what the read should sound like.

    The ranking is `hub/voice_casting`'s, shared with the Radio Promo builder
    so one provider account is not scored two different ways depending on
    which tool is open. What stays here is the account: that module ranks the
    list it is handed and never reaches the network, so whether there is a key
    and whether the account answered are this module's questions.

    Returns `(matched, note)`. The note is never empty when the ranking is not
    a ranking -- an account of cloned voices carries no labels at all, and a
    list of eight names in the account's own order, presented as a match,
    is the confident wrong answer this codebase keeps having to undo.
    """
    voices = list_voices()
    try:
        from hub import voice_casting
    except Exception:                                    # noqa: BLE001 — standalone
        return (voices[:count],
                "Not ranked — voice casting is unavailable outside the Hub.")
    matched = voice_casting.match(voices, want, count)
    return matched, voice_casting.match_quality(matched, len(voices))


def apply_pronunciation_dict(text, pronunciation_dict):
    """Applies a client's saved pronunciation substitutions before TTS, e.g.
    {'Gahanna': 'guh-HAN-uh'}. ElevenLabs also supports uploaded pronunciation
    dictionary files (phoneme-level); this simple text-substitution approach
    covers V1 without needing to manage dictionary file IDs per client."""
    if not pronunciation_dict:
        return text
    out = text
    for word, phonetic in pronunciation_dict.items():
        out = out.replace(word, phonetic)
    return out


def generate_voiceover(text, voice_id, stability=0.5, style=0.5, speed=1.0,
                        pronunciation_dict=None, out_path=None):
    """
    Returns {"audio_path": local file path, "duration_estimate": seconds} or,
    when running live, writes the MP3 to out_path and returns its path.
    """
    spoken_text = apply_pronunciation_dict(text, pronunciation_dict)
    word_count = len(spoken_text.split())
    # ~150 wpm average commercial VO pace, adjusted by requested speed
    duration_estimate = round((word_count / 150.0) * 60.0 / max(speed, 0.5), 2)

    if not is_live():
        return {"audio_path": None, "audio_url": None, "duration_estimate": duration_estimate,
                "_mock": True}

    payload = {
        "text": spoken_text,
        "voice_settings": {"stability": stability, "similarity_boost": 0.75, "style": style,
                            "use_speaker_boost": True},
    }
    try:
        r = requests.post(f"{BASE_URL}/text-to-speech/{voice_id}", headers=_headers(),
                           json=payload, timeout=30)
        r.raise_for_status()
        # ElevenLabs bills the character, so the unit is len(spoken_text) —
        # and it is the *spoken* text, after the pronunciation substitutions
        # above, because those change the length that was actually sent.
        try:
            from hub import quotas as _q
            _q.record_tts(spoken_text, module="commercial_builder",
                          model=MODEL, voice=voice_id)
        except Exception:                                 # noqa: BLE001
            pass
        if out_path:
            with open(out_path, "wb") as f:
                f.write(r.content)
            return {"audio_path": out_path, "duration_estimate": duration_estimate}
        return {"audio_bytes": r.content, "duration_estimate": duration_estimate}
    except Exception as e:
        return {"audio_path": None, "duration_estimate": duration_estimate, "error": str(e)}
