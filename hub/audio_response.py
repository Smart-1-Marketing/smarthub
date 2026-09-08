"""Validate TTS responses without silently paying for a second render."""
import base64
import math


def timestamp_audio(response, estimate, error):
    try:
        payload = response.json()
        audio = base64.b64decode(payload["audio_base64"], validate=True)
        if not audio:
            raise ValueError("empty audio")
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise error("ElevenLabs returned invalid audio. Please try again.") from exc
    # Missing or malformed alignment does not invalidate already generated audio.
    try:
        align = payload.get("normalized_alignment") or payload.get("alignment") or {}
        seconds = float(align["character_end_times_seconds"][-1])
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("invalid duration")
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return {"audio": audio, "seconds": estimate(audio), "measured": False}
    return {"audio": audio, "seconds": round(seconds, 2), "measured": True}


def plain_audio(response, estimate, error):
    audio = response.content
    content_type = response.headers.get("Content-Type", "").lower()
    if not audio or "json" in content_type or "text/" in content_type:
        raise error("ElevenLabs returned invalid audio. Please try again.")
    return {"audio": audio, "seconds": estimate(audio), "measured": False}
