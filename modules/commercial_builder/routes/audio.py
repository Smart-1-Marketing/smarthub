"""Generated audio — a sound effect on a scene, and the bed under the spot.

Two capabilities, one blueprint, because they are one provider and one set of
rules about what may be asked for. The scene half is the fifth action on a
storyboard card, beside Find Stock / Make a frame / Use Spokesperson /
Upload / Use Client Asset — a sourcing-layer asset like any other. The
project half replaces a Music step that had a mood dropdown, a level slider
and nothing behind either: it was a preset picker that generated no audio at
all, so the level slider was ducking a track that did not exist.

The CTA stinger and a transition whoosh need no routes of their own: the end
card **is** a scene, and so is the boundary a whoosh sits on, so both are the
scene route with a different prompt. The audio-only path needs none either —
a VO-only spot is this wizard with the visual steps unused.

## What records and what does not

The line this module records on is whether a file reaches the client's own
Cloudinary tree or changes their own record. Generating **options** does
neither in the sense that matters: both are written to one draft id per
scene, overwritten on the next press, and thrown away unless somebody picks
one. Choosing is what puts audio into the spot, so choosing is what records —
which is also the only moment there is a decision worth reconstructing later.

## Two options per press, never automatically

The cost-control rule this module already applies to AI stills and AI video.
Nothing here fires on a page load, on a save, or as part of the render.
"""

import os
import tempfile

from flask import Blueprint, jsonify, request

from ..config import (MUSIC_LEVELS, SOUND_EFFECTS_DEFAULT_INFLUENCE,
                      SOUND_EFFECTS_MAX_DURATION_S, SOUND_EFFECTS_MIN_DURATION_S,
                      MUSIC_MOODS, music_generation_enabled, music_length_ms,
                      music_prompt_starter)
from ..db import db
from ..models import Client, CommercialProject, Scene
from ..services import cloudinary_service, elevenlabs_audio_service as audio

bp = Blueprint("cb_audio", __name__, url_prefix="/api")

# How many drafts a press produces. Two, for the same reason "Generate AI"
# produces two: one is a coin toss and three is a decision nobody wants to
# make about a noise.
OPTIONS_PER_PRESS = 2


HOUSEKEEPING_ROUTES = {
    "generate_scene_sfx": "draws options to choose between. Billed, and the "
                          "bill is `hub/quotas.py`'s answer — the drafts are "
                          "written to one id per scene and overwritten on the "
                          "next press, so nothing has been made for the "
                          "client until one is chosen.",
    "compose_project_music": "the same, for the bed. The take that is kept is "
                             "recorded when it is chosen.",
    "clear_scene_sfx": "takes a draft back off a scene. Nothing is deleted "
                       "from the client's tree — the file stays where it was "
                       "stored and only the spot stops pointing at it.",
}

try:
    from hub import audit as _hub_audit
    _cb_log = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001
    def _cb_log(*_a, **_k):
        return None


# ---------------------------------------------------------------------------
# Storing what came back.
#
# The bytes go to the client's own audio folder, and Cloudinary has to be told
# this is a **video** resource: it keeps images, raw files and video in three
# namespaces and audio lives in the third — stored as raw an MP3 is kept and
# is neither transformable nor streamable, and asked for as an image it comes
# back "not found", which is the shape `cloudinary_sink.destroy()` already
# paid for.
#
# What actually decides that is the **`.mp3` on the temp file**:
# `hub/storage.resource_type_for()` reads the extension, and `_AUDIO_EXT` is
# folded into `_VIDEO_EXT` there for exactly this reason. `resource_type` is
# passed as well because the signature takes it and the voice track passes it
# too — it is inert on the live path today, and a caller that stopped passing
# it would read as having made a decision it did not make.
# ---------------------------------------------------------------------------
def _store(client, data, public_id, filename):
    """Put generated audio where the renderer can reach it.

    Returns `(url, note)` — the note is a sentence rather than a bool because
    "no key", "generated and not stored" and "stored" are three different
    situations and only the middle one is something to chase.
    """
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
            fh.write(data)
            tmp_path = fh.name
        stored = cloudinary_service.upload_asset(
            tmp_path, client.slug if client else "unassigned", "audio",
            public_id=public_id, resource_type="video",
            client_name=client.name if client else "", filename=filename)
        # `client_name` reaches `_file_in_gallery`, which files a still into
        # the client's gallery and deliberately skips audio: a gallery row
        # whose thumbnail can never render is worse than an absent one, which
        # is why `GALLERY_CATEGORIES` holds images alone. Passed anyway rather
        # than withheld, so this call site is not the one place that decides
        # what a gallery holds.
    except Exception as exc:                             # noqa: BLE001
        return "", f"It was generated but could not be stored: {exc}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                # The bytes are already in Cloudinary, or the upload already
                # failed and said why. A temp file that will not delete must
                # not turn either of those into a third answer -- and this is
                # a `finally`, so raising here would replace the return the
                # caller is about to read with an error about housekeeping.
                pass
    url = stored.get("secure_url")
    if not url:
        return "", ("It was generated but could not be stored, so the render "
                    "would not carry it. " + (stored.get("error") or ""))
    return url, ""


def _option(index, result, client, public_id, filename):
    """One generated draft, in the shape the picker draws.

    A failed option carries **its own** error rather than the batch collapsing
    into one: asking for two and getting one is ordinary — a refusal on one
    prompt, a timeout on the other — and reporting the whole press as failed
    throws away the option that worked. That is the rule the AI stills picker
    had to learn.
    """
    row = {"index": index, "url": "", "seconds": result.get("seconds"),
           "requested_seconds": result.get("requested_seconds"),
           "prompt": result.get("prompt") or "", "cached": False}
    if result.get("error"):
        return {**row, "error": result["error"]}
    if result.get("_mock"):
        return {**row, "error": result.get("note") or "Mock mode — nothing was generated."}
    data = result.get("audio_bytes")
    if not data:
        return {**row, "error": "ElevenLabs returned no audio."}
    url, note = _store(client, data, public_id, filename)
    if not url:
        return {**row, "error": note}
    return {**row, "url": url, "public_id": public_id, "bytes": len(data)}


# ---------------------------------------------------------------------------
# Sound effects, per scene
# ---------------------------------------------------------------------------
@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/sound-effect")
def generate_scene_sfx(project_id, scene_id):
    """'Add Sound Effect' — two drafts to listen to, nothing attached yet."""
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get(project.client_id)
    data = request.get_json(force=True) or {}

    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"ok": False,
                        "error": "Say what the sound is — “cinematic whoosh”, "
                                 "“car door slam”, “cash register”."}), 400

    # Blank means "you decide", which is ElevenLabs' own default and is very
    # often the right answer: a thump and an ambience are not the same length,
    # and the model reads that from the description better than a slider does.
    duration = data.get("duration_seconds")
    if duration in ("", None):
        duration = None
    influence = data.get("prompt_influence", SOUND_EFFECTS_DEFAULT_INFLUENCE)

    key = audio.cache_key("sound_effect", client.slug if client else "",
                          prompt, duration=duration, influence=influence)
    hit = audio.cached(key)
    if hit:
        # A press that costs nothing still has to look like a press: one
        # option rather than two, marked as the take that was already made.
        return jsonify({"ok": True, "options": [{**hit, "index": 0, "cached": True}],
                        "cached": True, "live": audio.is_live(),
                        "note": "This exact effect has been generated before, so it "
                                "was reused rather than generated again."})

    options = []
    for i in range(OPTIONS_PER_PRESS):
        result = audio.generate_sound_effect(prompt, duration_seconds=duration,
                                             prompt_influence=influence)
        options.append(_option(i, result, client,
                               f"sfx-p{project_id}-s{scene_id}-{i}",
                               f"sfx-{scene_id}-{i}.mp3"))

    kept = [o for o in options if o.get("url")]
    if kept:
        # Cached on the FIRST usable draft. The second is a different take of
        # the same prompt, so remembering both would mean deciding which a
        # later press should get, and a cache that quietly picks is worse than
        # one that reuses what it kept.
        audio.remember(key, {k: kept[0][k] for k in
                             ("url", "public_id", "seconds", "prompt")
                             if k in kept[0]})

    meta = dict(scene.asset_meta or {})
    meta["sfx_options"] = options
    scene.asset_meta = meta
    db.session.commit()
    return jsonify({"ok": bool(kept), "options": options, "live": audio.is_live(),
                    "error": None if kept else (options[0].get("error") if options else None)})


@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/sound-effect/choose")
def choose_scene_sfx(project_id, scene_id):
    """Attach one of the drafts to this scene.

    Deliberately a merge onto `asset_meta` rather than an assignment: the
    scene's footage, its shot grammar and its spokesperson all live there, and
    `set_music` has already paid for the version of this that replaced the
    dict — a music save that quietly wiped the voice track and rendered a
    silent commercial.
    """
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get(project.client_id)
    data = request.get_json(force=True) or {}

    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "url is required"}), 400

    meta = dict(scene.asset_meta or {})
    meta["sfx"] = {
        "url": url,
        "public_id": data.get("public_id") or "",
        "prompt": (data.get("prompt") or "").strip(),
        "seconds": data.get("seconds"),
        "requested_seconds": data.get("requested_seconds"),
    }
    scene.asset_meta = meta
    db.session.commit()

    if client:
        _cb_log("commercial_sfx_added", client=client.name,
                detail=(f"Sound effect on shot {scene.order_index + 1} of "
                        f"{project.title or 'the spot'}"
                        + (f" — “{meta['sfx']['prompt']}”"
                           if meta["sfx"]["prompt"] else "")),
                project=project.id)
    return jsonify({"ok": True, "scene": scene.to_dict()})


@bp.delete("/projects/<int:project_id>/scenes/<int:scene_id>/sound-effect")
def clear_scene_sfx(project_id, scene_id):
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    meta = dict(scene.asset_meta or {})
    meta.pop("sfx", None)
    scene.asset_meta = meta
    db.session.commit()
    return jsonify({"ok": True, "scene": scene.to_dict()})


# ---------------------------------------------------------------------------
# Music, per project
# ---------------------------------------------------------------------------
@bp.get("/projects/<int:project_id>/audio/options")
def audio_options(project_id):
    """What both audio panels need to draw themselves.

    `/audio/options` rather than `/music/options`, because the effect picker
    reads it too: a name that describes half of what a route answers is how
    the next reader concludes the other half belongs somewhere else and adds
    a second route for it.

    The prompt starters, the levels, the length that will be asked for, and
    whether composing is switched on at all. Read from `config.py` rather than
    restated in the template, so a mood added there reaches the screen without
    an edit — and so a deployment with composing off gets a panel that says so
    instead of a button that fails on press.
    """
    project = CommercialProject.query.get_or_404(project_id)
    return jsonify({
        "ok": True,
        "enabled": music_generation_enabled(),
        "live": audio.is_live(),
        "length_ms": music_length_ms(project.length_seconds),
        "length_seconds": project.length_seconds,
        "moods": [{"id": m, "prompt": music_prompt_starter(m)} for m in MUSIC_MOODS],
        "levels": {k: {"bed": v[0], "ducked": v[1]} for k, v in MUSIC_LEVELS.items()},
        "sfx_duration": {"min": SOUND_EFFECTS_MIN_DURATION_S,
                         "max": SOUND_EFFECTS_MAX_DURATION_S},
        "music": project.music or {},
    })


@bp.post("/projects/<int:project_id>/music/compose")
def compose_project_music(project_id):
    """Compose a bed at the spot's own runway.

    The length is **not** asked of the caller. It comes from
    `config.music_length_ms(project.length_seconds)`, which is the same
    runway QC measures the scenes against, so the track lands at the right
    length rather than being trimmed to fit afterwards — and a browser cannot
    ask for a length the spot does not have.
    """
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get(project.client_id)
    data = request.get_json(force=True) or {}

    if not music_generation_enabled():
        return jsonify({"ok": False,
                        "error": "Composing is switched off on this deployment "
                                 "(MUSIC_GENERATION_ENABLED)."}), 400

    prompt = (data.get("prompt") or "").strip()
    if not prompt:
        prompt = music_prompt_starter(data.get("mood") or (project.music or {}).get("mood") or "")
    if not prompt:
        return jsonify({"ok": False,
                        "error": "Describe the music, or pick a mood to fill the "
                                 "box in."}), 400

    length_ms = music_length_ms(project.length_seconds)
    key = audio.cache_key("music", client.slug if client else "", prompt,
                          length_ms=length_ms)
    hit = audio.cached(key)
    if hit:
        return jsonify({"ok": True, "options": [{**hit, "index": 0, "cached": True}],
                        "cached": True, "live": audio.is_live(),
                        "length_ms": length_ms,
                        "note": "This exact bed has been composed before, so it was "
                                "reused rather than composed again."})

    options = []
    for i in range(OPTIONS_PER_PRESS):
        result = audio.compose_music(prompt, length_ms)
        options.append(_option(i, result, client,
                               f"music-p{project_id}-{i}", f"music-{project_id}-{i}.mp3"))

    kept = [o for o in options if o.get("url")]
    if kept:
        audio.remember(key, {k: kept[0][k] for k in
                             ("url", "public_id", "seconds", "prompt")
                             if k in kept[0]})

    music = dict(project.music or {})
    music["music_options"] = options
    music["music_prompt"] = prompt
    project.music = music
    db.session.commit()
    return jsonify({"ok": bool(kept), "options": options, "length_ms": length_ms,
                    "live": audio.is_live(),
                    "error": None if kept else (options[0].get("error") if options else None)})


@bp.post("/projects/<int:project_id>/music/choose")
def choose_project_music(project_id):
    """Put one of the composed beds on the timeline.

    `music_track_url` is the key `routes/render.py` reads, which is why this
    exists at all: before it, the Music step wrote a mood and a level and the
    render had no bed to duck. Merged, never assigned — the trap `set_music`
    is already commented for.
    """
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get(project.client_id)
    data = request.get_json(force=True) or {}

    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "url is required"}), 400

    music = dict(project.music or {})
    music["music_track_url"] = url
    music["music_public_id"] = data.get("public_id") or ""
    music["music_prompt"] = (data.get("prompt") or music.get("music_prompt") or "").strip()
    music["music_source"] = "elevenlabs"
    # Both numbers are kept, because `music_length_mismatch` compares them and
    # a check handed one of the two cannot say anything. `music_seconds` is
    # None where the length could not be derived, and the check reads that as
    # *not measured* rather than as agreement.
    music["music_seconds"] = data.get("seconds")
    music["music_requested_ms"] = music_length_ms(project.length_seconds)
    project.music = music
    db.session.commit()

    if client:
        _cb_log("commercial_music_composed", client=client.name,
                detail=(f"Music bed on {project.title or 'the spot'}"
                        + (f" — “{music['music_prompt']}”"
                           if music.get("music_prompt") else "")),
                project=project.id)
    return jsonify({"ok": True, "music": project.music})
