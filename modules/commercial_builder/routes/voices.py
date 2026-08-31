"""Voice Studio (spec section 9) — ElevenLabs voice selection, per-scene
voiceover generation, and per-client pronunciation dictionaries."""

import os
import tempfile

from flask import Blueprint, jsonify, request

from ..db import db
from ..models import Client, CommercialProject, Scene
from ..services import cloudinary_service, elevenlabs_service

# The casting question, shared with the Radio Promo builder. Guarded because
# this module runs standalone, where there is no hub to read it from.
try:
    from hub import voice_casting
except Exception:                                    # noqa: BLE001
    voice_casting = None

bp = Blueprint("cb_voices", __name__, url_prefix="/api")

try:
    from hub import audit as _hub_audit
    _cb_log = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001 — standalone, no Hub to log into
    def _cb_log(*_a, **_k):
        return None


def _log(event, client="", detail="", **extra):
    """Never costs the write it describes. See `routes/review.py:_log`."""
    try:
        _cb_log(event, client=client or "", detail=detail, **extra)
    except Exception:  # noqa: BLE001
        pass


# Writes on this blueprint that deliberately record nothing, each with the
# reason. The line: a read that spends a model call is metered by
# `hub/quotas.py`, which is where a bill is answered; the activity log
# answers *what was made for this client*, and only a take that is kept has
# been made.
HOUSEKEEPING_ROUTES = {
    "cast_voices": "ranks the account's voices against what the script needs "
                   "and returns a shortlist. Nothing is chosen and nothing "
                   "is stored.",
    "generate_scene_voiceover": "auditions one scene so a rep can hear the "
                                "voice before committing to a read. The take "
                                "is returned and never stored — the full "
                                "voiceover is the one that is kept, and it "
                                "records.",
}


@bp.get("/voices")
def list_voices():
    return jsonify({"ok": True, "voices": elevenlabs_service.list_voices(),
                    "live": elevenlabs_service.is_live()})


@bp.get("/voice-characteristics")
def voice_characteristics():
    """What the read should sound like, as a set of choices.

    The Voice Studio asked for a voice id out of a flat dropdown of everything
    on the account, in whatever order ElevenLabs returned it and with no way
    to hear any of it. The Radio Promo builder — same provider, same account,
    same question — asks what the read should sound like and offers three
    ranked voices with a preview on each. There is one question now, in
    hub/voice_casting.py, and both tools ask it.
    """
    if voice_casting is None:
        return jsonify({"ok": True, "characteristics": [], "default": {},
                        "note": "Voice casting is unavailable outside the Hub."})
    return jsonify({"ok": True,
                    # The detailed form: each option carries the words it will
                    # match on, and energy carries the `style` value it sends.
                    # A picker built from labels alone gives somebody five rows
                    # of choices and no idea what any of them does.
                    "characteristics": voice_casting.characteristics_detail(),
                    "default": voice_casting.DEFAULT_WANT, "note": ""})


@bp.post("/voices/cast")
def cast_voices():
    """Rank the account's voices against what the read should sound like.

    A POST because the answer depends on a body of choices, and because the
    result is not cacheable by URL in any way a reader would expect. It reads
    only — nothing is saved until a voice is picked.
    """
    data = request.get_json(force=True) or {}
    want = data.get("want") or {}

    # A spot's own tone is a real signal and it has already been captured on
    # the brief. Folding it into the search terms means the casting starts
    # somewhere sensible rather than from nothing — the Proposal Builder
    # shipped four discovery answers that were read by nobody, and this is the
    # same failure one screen along.
    project_id = data.get("project_id")
    if project_id:
        project = CommercialProject.query.get(project_id)
        if project:
            terms = list(want.get("search_terms") or [])
            tone = (project.brief or {}).get("tone")
            if tone:
                terms.append(str(tone))
            want = {**want, "search_terms": terms}

    matched, note = elevenlabs_service.cast_voices(want, int(data.get("count") or 3))
    # How many characteristics were actually asked for. "No preference" is not
    # a question, so counting it would make a voice that matched everything
    # asked read as "2 of 5".
    asked = voice_casting.asked_count(want) if voice_casting else 0
    return jsonify({"ok": True, "voices": matched, "note": note, "asked": asked,
                    "live": elevenlabs_service.is_live()})


@bp.put("/clients/<int:client_id>/pronunciation")
def save_pronunciation(client_id):
    client = Client.query.get_or_404(client_id)
    data = request.get_json(force=True) or {}
    client.pronunciation_dict = data.get("pronunciation_dict") or {}
    db.session.commit()
    # This writes the same brand-profile field `update_client` writes, and
    # that one records. Two routes changing one field with only one of them
    # recorded is the inconsistency this triage exists to close: how a client's
    # name comes to be said differently is exactly what somebody would go
    # looking for later.
    _log("cb_client_updated", client=client.name,
         detail="Brand profile updated: pronunciation_dict.")
    return jsonify({"ok": True, "pronunciation_dict": client.pronunciation_dict})


@bp.post("/projects/<int:project_id>/scenes/<int:scene_id>/voiceover")
def generate_scene_voiceover(project_id, scene_id):
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(force=True) or {}

    voice_id = data.get("voice_id") or client.preferred_voiceover_id
    if not voice_id:
        return jsonify({"ok": False, "error": "Choose a voice first (or set the client's preferred voiceover)."}), 400

    result = elevenlabs_service.generate_voiceover(
        text=scene.narration or "",
        voice_id=voice_id,
        stability=float(data.get("stability", 0.5)),
        style=float(data.get("style", 0.5)),
        speed=float(data.get("speed", 1.0)),
        pronunciation_dict=client.pronunciation_dict,
    )
    meta = scene.asset_meta or {}
    meta["voiceover"] = {"voice_id": voice_id, **result}
    scene.asset_meta = meta
    db.session.commit()
    return jsonify({"ok": True, "voiceover": result, "live": elevenlabs_service.is_live()})


@bp.post("/projects/<int:project_id>/voiceover/full")
def generate_full_voiceover(project_id):
    """Generates one continuous VO track for the whole commercial (used at
    render time so pacing/breath timing is consistent, rather than stitching
    per-scene clips)."""
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(force=True) or {}
    voice_id = data.get("voice_id") or client.preferred_voiceover_id
    if not voice_id:
        return jsonify({"ok": False, "error": "Choose a voice first."}), 400

    full_text = " ".join(s.narration or "" for s in project.scenes.order_by(Scene.order_index).all()
                          if not s.is_cta)
    result = elevenlabs_service.generate_voiceover(
        text=full_text, voice_id=voice_id,
        stability=float(data.get("stability", 0.5)), style=float(data.get("style", 0.5)),
        speed=float(data.get("speed", 1.0)), pronunciation_dict=client.pronunciation_dict,
    )

    # Store it, or the render has no narration on it.
    #
    # This was called "preview" and behaved like one: it generated the whole
    # voiceover, paid ElevenLabs for every character of it, reported the
    # estimated duration and threw the audio away. `routes/render.py` reads
    # `project.music["voice_track_url"]` to put the voice track on the
    # timeline, and nothing in this module had ever written that key — so
    # every commercial this tool rendered was silent, with no error at either
    # end. The bytes go to the client's library and the URL onto the project.
    stored = _store_voice_track(project, client, result)
    result.update(stored)
    # The take that is kept. It goes into the client's own Cloudinary tree and
    # onto the spot's timeline, which makes it work produced for them rather
    # than an audition — and whether it reached storage is said rather than
    # assumed, because `_store_voice_track` answers with the reason when it
    # could not.
    _log("commercial_voiceover_recorded", client=client.name,
         detail=(f"Voiceover recorded for {project.title or 'the spot'}"
                 + ("." if stored.get("voice_track_url")
                    else ", but it could not be stored.")),
         project=project.id)

    return jsonify({"ok": True, "voiceover": result,
                    "voice_track_url": (project.music or {}).get("voice_track_url") or "",
                    "live": elevenlabs_service.is_live()})


def _store_voice_track(project, client, result):
    """Put the generated MP3 somewhere the renderer can reach it.

    Returns what happened, in words, rather than a bare boolean: "no key set",
    "generated but not stored" and "stored" are three different situations and
    only the middle one is something to chase.
    """
    audio = result.get("audio_bytes")
    if not audio:
        if result.get("error"):
            return {"stored": False, "store_note": f"ElevenLabs refused it: {result['error']}"}
        return {"stored": False,
                "store_note": ("Mock mode — no ELEVENLABS_API key is set, so no audio "
                               "was produced and the render will have no narration.")}

    tmp_path = ""
    try:
        # upload_asset takes a path or a URL, not bytes, so the MP3 goes
        # through a temp file rather than a second upload path being invented
        # here. Removed in the finally, whatever happens.
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
            fh.write(audio)
            tmp_path = fh.name
        upload = cloudinary_service.upload_asset(
            tmp_path, client.slug, "voice",
            public_id=f"project-{project.id}-voice", resource_type="video")
    except Exception as exc:  # noqa: BLE001
        return {"stored": False, "store_note": f"The voice track could not be stored: {exc}"}
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    url = upload.get("secure_url")
    if not url:
        return {"stored": False,
                "store_note": ("The voice track was generated but could not be stored, so "
                               "the render would have no narration. "
                               + (upload.get("error") or ""))}

    # Merged, never assigned: project.music also carries the mood and level,
    # and the music panel writes those back.
    music = dict(project.music or {})
    music["voice_track_url"] = url
    music["voice_id"] = (result.get("voice_id")
                         or music.get("voice_id") or "")
    project.music = music
    db.session.commit()
    return {"stored": True, "store_note": "Stored — the render will carry this narration."}
