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


def scene_voice_fits(scenes):
    """Which scenes carry a read longer than the scene itself.

    The per-scene half of the length question, and the one nothing was asking.
    `creatomate_service.build_source` gives each scene's voice element an
    explicit ``duration`` of that scene's own span, so in scenes mode a read
    that runs past its scene is not heard running over -- it is **cut at the
    scene boundary**, mid-word, and the render succeeds. That is the same
    failure the full-track path has against the spot's length, one grain finer,
    and `_check_scene_assets` does not cover it: that check is about the
    *footage* being shorter than the scene, not the narration being longer.

    Only a MEASURED take counts. A scene whose take came back at a bitrate
    nobody could name has not been checked, and saying so is the honest answer
    -- the rule `cbr_seconds()` already applies to the bytes.

    Returns one row per narration scene that has a measured take::

        {"scene": 3, "seconds": 6.4, "span": 5.0, "over": 1.4, "needed": 1.29}

    ``needed`` is the exact rate that scene would take to fit, unrounded and
    uncapped -- `hub/radio_spec.speed_suggestion()` owns the rounding and the
    ceiling, and this must not grow a second opinion about either.
    """
    rows = []
    for index, scene in enumerate(sorted(scenes, key=lambda s: (s.get("start") or 0,
                                                                s.get("id") or 0)), 1):
        if has_presenter(scene):
            continue                    # HeyGen speaks these; the clip is the read
        voice = (scene.get("asset_meta") or {}).get("voiceover") or {}
        if not voice.get("measured"):
            continue
        try:
            seconds = float(voice.get("seconds"))
            span = float(scene.get("end")) - float(scene.get("start"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(seconds) or not math.isfinite(span) or span <= 0:
            continue
        rows.append({"scene": index, "id": scene.get("id"),
                     "seconds": round(seconds, 2), "span": round(span, 2),
                     "over": round(seconds - span, 2),
                     "needed": seconds / span})
    return rows


def scene_tolerance_s():
    """How far a scene's read may sit past its scene before it is a finding.

    `config.SCENE_VOICE_TOLERANCE_S`, read rather than restated, and read by
    the finding, the offer and the gate alike -- these three disagreed once,
    with the per-scene check flagging an overrun that the rate to fix it then
    called "fits" because the rate was worked out against the BED's one-second
    tolerance. One second is 3% of a :30 runway and a quarter of a four-second
    scene.
    """
    try:
        from ..config import SCENE_VOICE_TOLERANCE_S
        return float(SCENE_VOICE_TOLERANCE_S)
    except Exception:                                      # noqa: BLE001
        return 0.1


def worst_scene_voice_fit(scenes, tolerance_s=None):
    """The scene that binds, or None. Re-reading fixes every scene at once.

    One pace is asked of the whole spot, so the scene needing the highest rate
    is the one the offer has to satisfy -- fix that and the rest come with it.
    Picking the longest overrun instead would be the wrong scene whenever a
    short scene is proportionally worse, which is the common case: 0.3s over a
    4s scene needs a harder push than 1s over a 20s one.
    """
    tol = scene_tolerance_s() if tolerance_s is None else float(tolerance_s)
    over = [r for r in scene_voice_fits(scenes) if r["over"] > tol]
    return max(over, key=lambda r: r["needed"]) if over else None


def integrity(project, scenes):
    """Non-overridable media requirements; legacy assets remain editable."""
    problems = []
    if (project.get("brief") or {}).get("variation_needs_script"):
        problems.append("This variation has a new brief or duration: regenerate its script before rendering.")
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
