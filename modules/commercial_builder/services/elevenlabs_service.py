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

from ..config import AUDIO_OUTPUT_FORMAT, AUDIO_OUTPUT_KBPS

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


# The window ElevenLabs reads in. Both radio builders clamp to exactly this
# before sending, and a value outside it is not refused by the API -- it is
# ignored, which is a slider that moves and changes nothing.
SPEED_MIN, SPEED_MAX = 0.7, 1.2


def clamp_speed(speed):
    """The read pace, inside the window the provider actually honors."""
    try:
        return round(min(SPEED_MAX, max(SPEED_MIN, float(speed))), 3)
    except (TypeError, ValueError):
        return 1.0


def generate_voiceover(text, voice_id, stability=0.5, style=0.5, speed=1.0,
                        pronunciation_dict=None, out_path=None):
    """One voiceover take.

    Returns the audio plus how long it is and **how well that is known**:

    * ``seconds`` / ``measured`` -- derived from the byte count of the MP3 we
      asked for, through `elevenlabs_audio_service.cbr_seconds()`. The same
      reading the bed already uses, at the bitrate this request names, and
      `None` for anything that did not come back as that MP3.
    * ``duration_estimate`` -- the words-per-minute guess, kept because it is
      the only answer available in mock mode and because callers read it.

    Those were one field before, named `duration_estimate` and computed only
    from the word count -- so the Voice step reported "Estimated 34.1s of
    narration" for a file nobody had looked at, on the number a rep uses to
    decide whether the read fits a :30. It reads like a measurement of the
    take and was a division.

    ``speed`` is now **sent**, which it was not. It was accepted, divided into
    the estimate, and dropped: the Voice step's Speed slider moved the number
    on screen and produced byte-identical audio, while both radio builders
    have always put it in `voice_settings`. Clamped to the window the provider
    honors rather than passed through, for the reason
    `hub/quote_validity.py` gives about a silent clamp -- except here the
    silence was the provider's.
    """
    from hub.customer_voices import ensure_usable, LibraryError
    try:
        ensure_usable(voice_id)
    except LibraryError as exc:
        return {"error": str(exc)}
    spoken_text = apply_pronunciation_dict(text, pronunciation_dict)
    word_count = len(spoken_text.split())
    rate = clamp_speed(speed)
    # ~150 wpm average commercial VO pace, adjusted by requested speed. The
    # fallback reading, never the one to prefer -- see the docstring.
    duration_estimate = round((word_count / 150.0) * 60.0 / max(rate, 0.5), 2)

    if not is_live():
        return {"audio_path": None, "audio_url": None,
                "duration_estimate": duration_estimate,
                "seconds": None, "measured": False, "speed": rate,
                "length_note": ("Mock mode — no audio was produced, so this "
                                "length is a words-per-minute estimate of the "
                                "script rather than a reading of a file."),
                "_mock": True}

    payload = {
        "text": spoken_text,
        "voice_settings": {"stability": stability, "similarity_boost": 0.75, "style": style,
                            "speed": rate, "use_speaker_boost": True},
    }
    try:
        # The output format is named rather than left to the account default,
        # because the length below is arithmetic on the byte count and that is
        # only true at a bitrate we chose. Radio's own render asks for the
        # same one.
        r = requests.post(f"{BASE_URL}/text-to-speech/{voice_id}",
                           headers=_headers(),
                           params={"output_format": AUDIO_OUTPUT_FORMAT},
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
        length = _take_seconds(r, r.content)
        common = {"duration_estimate": duration_estimate, "speed": rate, **length}
        if out_path:
            with open(out_path, "wb") as f:
                f.write(r.content)
            return {"audio_path": out_path, **common}
        return {"audio_bytes": r.content, **common}
    except Exception as e:
        return {"audio_path": None, "duration_estimate": duration_estimate,
                "seconds": None, "measured": False, "speed": rate,
                "length_note": "", "error": str(e)}


def _take_seconds(response, data):
    """How long the take is, and whether that is a reading or a guess.

    One reading of "how long is this MP3", borrowed from the bed rather than
    written again: `elevenlabs_audio_service.cbr_seconds()` is the module's own
    answer and it already refuses anything that is not the constant-bitrate
    MP3 it asked for. A second copy here is how the bed and the voice come to
    disagree about the length of the same kind of file.
    """
    ctype = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype not in ("audio/mpeg", "audio/mp3"):
        return {"seconds": None, "measured": False,
                "length_note": (f"The take came back as {ctype or 'an unnamed type'} "
                                "rather than the MP3 that was asked for, so its "
                                "length is not measured.")}
    from .elevenlabs_audio_service import cbr_seconds
    seconds = cbr_seconds(len(data or b""), AUDIO_OUTPUT_KBPS)
    if seconds is None:
        return {"seconds": None, "measured": False,
                "length_note": "The take is empty, so there is no length to read."}
    return {"seconds": seconds, "measured": True,
            "length_note": (f"{seconds}s, from the byte count of the "
                            f"{AUDIO_OUTPUT_KBPS} kbps MP3 that came back.")}
