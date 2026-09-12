"""Immutable, manually supplied comparison evidence; no provider calls or activation."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import math
import re
import wave
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from hub import ai_models, jsonstore

BRIEFS = {
    "offer-15-v1": {"label": "15-second offer", "seconds": 15,
        "prompt": "Write only the spoken script for a 15-second spot for Cedar Auto. Offer: a $29 oil change. End with Book today. Include exactly: Offer ends Friday. No invented claims.",
        "required": ["Cedar Auto", "$29", "Book today", "Offer ends Friday"]},
    "pronunciation-30-v1": {"label": "30-second pronunciation", "seconds": 30,
        "prompt": "Write only the spoken script for a 30-second spot for Saoirse's Garden. Announce the spring plant sale. Include exactly: While supplies last. End with Visit us Saturday. Pronounce Saoirse as SEER-sha in the rendered voice; do not put pronunciation notes in the spoken script. No invented prices or guarantees.",
        "required": ["Saoirse's Garden", "spring plant sale", "While supplies last", "Visit us Saturday"]},
    "revision-60-v1": {"label": "60-second revision", "seconds": 60,
        "prompt": "Revise this fictional campaign into only a 60-second spoken script: Harbor Books has a summer reading event Sunday at noon. Change the event to Saturday at ten, removing the old day and time. Include exactly: Free admission. End with Find your next story at Harbor Books. Do not invent guests, discounts or giveaways.",
        "required": ["Saturday", "ten", "Free admission", "Find your next story at Harbor Books"],
        "forbidden": ["Sunday", "noon"]},
}
MAX_AUDIO_BYTES = 4 * 1024 * 1024


def _text(value, label, minimum=1, maximum=4000):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(f"{label} must contain {minimum}–{maximum} characters.")
    return value.strip()


def _number(value, label, maximum):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"Enter a valid {label}.")
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Enter a valid {label}.") from None
    if not math.isfinite(result) or not 0 <= result <= maximum:
        raise ValueError(f"Enter a valid {label} between 0 and {maximum}.")
    return result


def measure_wav(encoded):
    """Measure complete PCM data, not a supplied duration or unverified WAV header."""
    if encoded in (None, ""):
        return None
    if not isinstance(encoded, str) or len(encoded) > (MAX_AUDIO_BYTES + 2) // 3 * 4:
        raise ValueError("Each WAV must be at most 4 MB.")
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > MAX_AUDIO_BYTES:
            raise ValueError("Each WAV must be at most 4 MB.")
        with wave.open(io.BytesIO(raw), "rb") as audio:
            frames, rate = audio.getnframes(), audio.getframerate()
            size = audio.getnchannels() * audio.getsampwidth()
            if rate <= 0 or frames <= 0 or audio.getcomptype() != "NONE":
                raise ValueError("Upload a nonempty PCM WAV.")
            if len(audio.readframes(frames)) != frames * size:
                raise ValueError("WAV audio is truncated.")
            seconds = frames / rate
            if seconds > 180:
                raise ValueError("Comparison audio must be at most 180 seconds.")
    except (binascii.Error, wave.Error, EOFError) as exc:
        raise ValueError("Upload a valid, complete PCM WAV.") from exc
    return {"seconds": round(seconds, 4), "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw), "source": "server-measured PCM WAV"}


def _sample(body, brief):
    if not isinstance(body, dict):
        raise ValueError("Supply both comparison samples.")
    script = _text(body.get("script"), "Spoken script", maximum=12000)
    quality = _number(body.get("quality"), "human quality score", 5)
    if quality is None or quality < 1 or not quality.is_integer():
        raise ValueError("Score human quality from 1 to 5.")
    compatibility = body.get("compatibility")
    if not isinstance(compatibility, str) or compatibility not in {"pass", "fail", "untested"}:
        raise ValueError("Choose a compatibility result.")
    delivery = body.get("delivery")
    if not isinstance(delivery, str) or delivery not in {"pass", "fail", "untested"}:
        raise ValueError("Choose a listening review result.")
    audio = measure_wav(body.get("wav_base64"))
    normalized = " ".join(script.casefold().split())
    missing = [p for p in brief["required"] if " ".join(p.casefold().split()) not in normalized]
    forbidden = [p for p in brief.get("forbidden", []) if re.search(r"\b" + re.escape(p.casefold()) + r"\b", normalized)]
    return {"script": script, "quality": int(quality), "compatibility": compatibility,
            "delivery": delivery, "audio": audio, "missing": missing, "forbidden": forbidden,
            "within_slot": audio["seconds"] <= brief["seconds"] if audio else None,
            "latency_seconds": _number(body.get("latency_seconds"), "latency", 3600),
            "reported_cost_usd": _number(body.get("reported_cost_usd"), "reported cost", 1000),
            "reference": _text(body.get("reference"), "Sample reference", maximum=1000),
            "notes": _text(body.get("notes"), "Review notes", minimum=20)}


def assess(active, candidate):
    """A sample-level suggestion, never a claim that a model is safe to deploy."""
    if candidate["missing"] or candidate["forbidden"] or candidate["within_slot"] is False or candidate["compatibility"] == "fail" or candidate["delivery"] == "fail" or candidate["quality"] < 3:
        return "Candidate needs revision", "Candidate failed a required-content, timing, compatibility or human-review check."
    for sample in (active, candidate):
        if sample["audio"] is None or sample["compatibility"] == "untested" or sample["delivery"] == "untested" or sample["latency_seconds"] is None or sample["reported_cost_usd"] is None:
            return "More evidence needed", "Complete both samples' audio, listening, compatibility, latency and cost evidence."
    if candidate["quality"] > active["quality"]:
        return "Candidate leads on this sample", "Human quality is higher and candidate checks passed. Compare more briefs and review cost and speed before recommending an upgrade."
    return "Keep current pending more tests", "This sample does not show a human-rated quality improvement."


def record(body, actor):
    if not isinstance(body, dict):
        raise ValueError("Expected an object.")
    profile = body.get("profile")
    if not isinstance(profile, str) or profile not in ai_models.PROFILES or not profile.endswith(".text"):
        raise ValueError("Choose a writing profile.")
    brief_id = body.get("brief")
    if not isinstance(brief_id, str) or brief_id not in BRIEFS:
        raise ValueError("Choose a fixed test brief.")
    active_model = ai_models.model(profile)
    if body.get("active_model") != active_model:
        raise ValueError("The active model changed. Reload and compare against the current model.")
    candidate = _text(body.get("candidate"), "Candidate model ID", maximum=120)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._:-]*", candidate) or candidate == active_model:
        raise ValueError("Choose a different, valid candidate model ID.")
    brief = deepcopy(BRIEFS[brief_id])
    active, proposed = _sample(body.get("active"), brief), _sample(body.get("proposed"), brief)
    suggestion, reason = assess(active, proposed)
    row = {"id": uuid4().hex, "at": datetime.now(timezone.utc).isoformat(), "actor": str(actor),
           "profile": profile, "active_model": active_model, "candidate": candidate,
           "brief_id": brief_id, "brief": brief, "active": active, "proposed": proposed,
           "settings": _text(body.get("settings"), "Shared prompt and request settings", minimum=20),
           "suggestion": suggestion, "reason": reason, "rubric_version": "comparison-v1",
           "provenance": "Manually supplied samples and human scores; WAV duration measured by server"}
    jsonstore.write_json(ai_models._path("comparison-" + row["id"]), row)
    return row


def history():
    import glob
    rows = [jsonstore.read_json(path, {}) for path in glob.glob(ai_models._path("comparison-*"))]
    return sorted((r for r in rows if r), key=lambda r: r["at"], reverse=True)[:100]
