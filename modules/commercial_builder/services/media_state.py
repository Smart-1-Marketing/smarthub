"""Content identities shared by generation, recovery and export validation."""

import hashlib
import json
import math


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def speech_signature(scene):
    return fingerprint((scene.get("narration") or "").strip())


def has_presenter(scene):
    meta = scene.get("asset_meta") or {}
    return bool(meta.get("heygen_job") or meta.get("spokesperson_url")
                or scene.get("asset_type") == "spokesperson")


def timeline_signature(scenes):
    return fingerprint([
        {k: s.get(k) for k in ("id", "start", "end", "narration", "is_cta")}
        | {"presenter": has_presenter(s)}
        for s in sorted(scenes, key=lambda s: (s.get("start") or 0, s.get("id") or 0))
    ])


def integrity(project, scenes):
    """Non-overridable media requirements; legacy assets remain editable."""
    problems = []
    music = project.get("music") or {}
    formats = project.get("formats") or ["16:9"]
    signature = music.get("voice_signature")
    if music.get("voice_track_stale") or (signature and signature != timeline_signature(scenes)):
        problems.append("Narration changed: generate a new voiceover.")
    if music.get("voice_track_url") and any(has_presenter(s) for s in scenes):
        problems.append("Generate narration again to separate it from the HeyGen presenter speech.")
    for index, scene in enumerate(scenes, 1):
        meta = scene.get("asset_meta") or {}
        job = meta.get("heygen_job") or {}
        if meta.get("presenter_stale") or (job.get("speech_signature") and
                                           job["speech_signature"] != speech_signature(scene)):
            problems.append(f"Scene {index}: presenter speech is out of date; regenerate it.")
        if job.get("format_id") and job["format_id"] != formats[0]:
            problems.append(f"Scene {index}: regenerate the presenter for the project's primary format.")
        if meta.get("spokesperson_mirrored") is False:
            problems.append(f"Scene {index}: retry saving the presenter to the client library.")
        duration = job.get("duration")
        if meta.get("spokesperson_url") and duration is not None:
            try:
                length = float(scene["end"]) - float(scene["start"])
                if not math.isfinite(float(duration)) or float(duration) <= 0 or abs(float(duration) - length) > 0.5:
                    problems.append(f"Scene {index}: presenter lasts {float(duration):.1f}s but the scene is {length:.1f}s; adjust timing or regenerate.")
            except (TypeError, ValueError, KeyError):
                problems.append(f"Scene {index}: presenter duration could not be verified.")
        voice = meta.get("voiceover") or {}
        if not music.get("voice_track_url") and not has_presenter(scene) and (scene.get("narration") or "").strip():
            if not voice.get("audio_url") or voice.get("stale") or voice.get("speech_signature") != speech_signature(scene):
                problems.append(f"Scene {index}: generate and save its narration.")
            elif voice.get("duration") is not None:
                try:
                    duration = float(voice["duration"])
                    if not math.isfinite(duration) or duration <= 0 or duration > float(scene["end"]) - float(scene["start"]) + 0.5:
                        problems.append(f"Scene {index}: narration does not fit the scene.")
                except (TypeError, ValueError):
                    problems.append(f"Scene {index}: narration duration could not be verified.")
    return {"passed": not problems, "message": " ".join(problems) if problems else "Generated media matches the current script."}
