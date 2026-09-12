"""Transaction-local snapshots. Polling and stale flags do not create new takes."""
import json

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from .models import Scene, CommercialProject, ProductionTake
from .services.media_state import fingerprint

PRESENTER_KEYS = ("spokesperson_url", "spokesperson_mirrored", "spokesperson_over_footage",
                  "avatar_id", "chroma_key", "chroma_key_color", "heygen_job", "media")
TRACK_KEYS = ("voice_track_url", "voice_mode", "voice_signature")


def _json_dict(raw):
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}  # History must not prevent repairing a damaged legacy field.


def snapshots(row, scene=True):
    if not scene:
        music = _json_dict(row.get("music_json"))
        return {"track": {k: music.get(k) for k in TRACK_KEYS}} if music.get("voice_track_url") else {}
    meta = _json_dict(row.get("asset_meta_json"))
    result = {"script": {k: row.get(k) for k in ("narration", "visual_description")}}
    voice = dict(meta.get("voiceover") or {})
    voice.pop("stale", None)
    if voice.get("audio_url"):
        result["voice"] = voice
    job = meta.get("heygen_job") or {}
    if (meta.get("spokesperson_url") and job.get("status") == "completed"
            and meta.get("spokesperson_mirrored") is not False):
        result["presenter"] = {k: meta.get(k) for k in PRESENTER_KEYS}
        result["presenter"]["asset"] = {k: row.get(k) for k in
                                        ("asset_type", "asset_source", "asset_url", "asset_thumb_url")}
    return result


def capture(session, _context, _instances):
    for obj in list(session.dirty) + list(session.deleted):
        if not isinstance(obj, (Scene, CommercialProject)) or not obj.id:
            continue
        is_scene = isinstance(obj, Scene)
        table = obj.__table__
        old = session.connection().execute(select(table).where(table.c.id == obj.id)).mappings().first()
        if not old:
            continue
        current = {c.name: getattr(obj, c.name) for c in table.columns}
        before, after = snapshots(old, is_scene), snapshots(current, is_scene)
        project_id = obj.project_id if is_scene else obj.id
        if session.get(CommercialProject, project_id) in session.deleted:
            continue
        scene_id = obj.id if is_scene else None
        if is_scene and obj in session.deleted:
            # SQLite may reuse a deleted scene ID. Detach its history so a
            # replacement scene cannot inherit or restore somebody else's take.
            session.query(ProductionTake).filter_by(project_id=project_id, scene_id=scene_id).update(
                {"scene_id": None}, synchronize_session=False)
            for pending in session.new:
                if isinstance(pending, ProductionTake) and pending.project_id == project_id and pending.scene_id == scene_id:
                    pending.scene_id = None
            scene_id = None
        for kind in before.keys() | after.keys():
            if before.get(kind) == after.get(kind) and obj not in session.deleted:
                continue
            for value in (before.get(kind), after.get(kind)):
                if not value:
                    continue
                digest = fingerprint(value)
                exists = any(isinstance(t, ProductionTake) and t.project_id == project_id
                             and t.scene_id == scene_id and t.kind == kind and t.digest == digest
                             for t in session.new)
                if not exists:
                    exists = session.query(ProductionTake.id).filter_by(
                        project_id=project_id, scene_id=scene_id, kind=kind, digest=digest).first()
                if not exists:
                    take = ProductionTake(project_id=project_id, scene_id=scene_id, kind=kind, digest=digest)
                    take.snapshot = value
                    session.add(take)


def install():
    if not event.contains(Session, "before_flush", capture):
        event.listen(Session, "before_flush", capture)


def preserve_presenter(session, scene):
    """The paid-job reservation uses a bulk CAS update, bypassing ORM events."""
    row = {c.name: getattr(scene, c.name) for c in scene.__table__.columns}
    value = snapshots(row).get("presenter")
    if value:
        digest = fingerprint(value)
        if not session.query(ProductionTake.id).filter_by(project_id=scene.project_id,
                scene_id=scene.id, kind="presenter", digest=digest).first():
            take = ProductionTake(project_id=scene.project_id, scene_id=scene.id,
                                  kind="presenter", digest=digest)
            take.snapshot = value
            session.add(take)
