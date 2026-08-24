"""ElevenLabs service — Voice Studio (spec section 9).

Voiceover generation, kept separate from HeyGen's spokesperson audio (that
audio is baked into the talking-head clip; this is for scenes with no
on-screen presenter). Also manages per-client pronunciation dictionaries so
local place names ("Gahanna", "Scioto") stop getting mangled.
"""

import os

import requests

API_KEY = os.environ.get("ELEVENLABS_API_KEY")
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


def is_live():
    return bool(API_KEY)


def _headers():
    return {"xi-api-key": API_KEY, "Content-Type": "application/json"}


def list_voices():
    if not is_live():
        return [{"voice_id": f"mock_{name.lower()}", "name": name, "style": style, "_mock": True}
                for style, name in STYLE_TO_MOCK_VOICE.items()]
    try:
        r = requests.get(f"{BASE_URL}/voices", headers=_headers(), timeout=8)
        r.raise_for_status()
        voices = r.json().get("voices", [])
        return [{"voice_id": v.get("voice_id"), "name": v.get("name"),
                  "style": ", ".join(v.get("labels", {}).values())} for v in voices]
    except Exception:
        return []


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
