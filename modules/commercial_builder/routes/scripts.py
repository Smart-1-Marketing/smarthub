"""Storyboard Editor scene operations (spec section 5): reorder, change
duration, regenerate, replace footage, edit narration, duplicate, delete."""

from flask import Blueprint, jsonify, request

from ..db import db
from ..config import (DEFAULT_SHOT_GRAMMAR, SHOT_SIZES, SHOT_ANGLES,
                      SHOT_MOVES, shot_label)
from ..models import CommercialProject, Scene, Client
from ..services import openai_service

bp = Blueprint("cb_scripts", __name__, url_prefix="/api/projects/<int:project_id>/scenes")


def _resequence(project):
    """Recompute contiguous start/end times from each scene's own duration,
    in current order_index order, so total always equals the project length."""
    scenes = project.scenes.order_by(Scene.order_index).all()
    t = 0.0
    for s in scenes:
        duration = max(s.end - s.start, 0.5) if s.end and s.start is not None else 3.0
        s.start = round(t, 2)
        t += duration
        s.end = round(t, 2)
    # stretch/compress last scene so total matches the project's committed length exactly
    if scenes:
        scale = project.length_seconds / t if t else 1
        if abs(scale - 1) > 0.02:
            t = 0.0
            for s in scenes:
                duration = (s.end - s.start) * scale
                s.start = round(t, 2)
                t += duration
                s.end = round(t, 2)
            scenes[-1].end = float(project.length_seconds)


@bp.get("")
def list_scenes(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    return jsonify({"ok": True, "scenes": [s.to_dict() for s in project.scenes.all()]})


def _clean_grammar(grammar):
    """Only values the vocabularies know. The lists are closed because a
    `<select>` and a stock query both read them, so a value from neither is
    replaced by the default rather than written through: "size: banana"
    reaches a stock search and an AI prompt as a search term.
    """
    grammar = grammar or {}
    allowed = {"size": {x["id"] for x in SHOT_SIZES},
               "angle": {x["id"] for x in SHOT_ANGLES},
               "move": {x["id"] for x in SHOT_MOVES}}
    out = dict(DEFAULT_SHOT_GRAMMAR)
    for field, values in allowed.items():
        value = str(grammar.get(field) or "").strip().lower()
        if value in values:
            out[field] = value
    return out


@bp.put("/<int:scene_id>")
def update_scene(project_id, scene_id):
    """Edit narration/visual description, or set asset directly (upload / client asset)."""
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    data = request.get_json(force=True) or {}
    for field in ["narration", "visual_description", "asset_type", "asset_source",
                  "asset_url", "asset_thumb_url", "locked"]:
        if field in data:
            setattr(scene, field, data[field])
    if "asset_meta" in data:
        scene.asset_meta = data["asset_meta"]
    if "grammar" in data:
        # Merged into asset_meta rather than replacing it: `beat`, `beat_index`
        # and `shot_no` live in the same dict, and an assignment here would
        # drop the shot out of its beat the first time somebody changed a
        # camera angle — the same trap `set_music` had.
        meta = dict(scene.asset_meta or {})
        meta["grammar"] = _clean_grammar(data.get("grammar"))
        scene.asset_meta = meta
    if "duration" in data:
        new_duration = max(float(data["duration"]), 0.5)
        scene.end = scene.start + new_duration
        project = CommercialProject.query.get(project_id)
        _resequence(project)
    db.session.commit()
    return jsonify({"ok": True, "scene": scene.to_dict()})


@bp.post("/<int:scene_id>/regenerate")
def regenerate_scene(project_id, scene_id):
    """Regenerate just this scene's visual + narration, keeping the rest of
    the storyboard untouched."""
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    concept = next((c for c in (project.concepts or []) if c["id"] == project.selected_concept_id), None)

    # Cheap, targeted regen: rewrite just this scene's shot + narration
    # rather than re-running the whole commercial's beat structure.
    duration = max(scene.end - scene.start, 1.0)
    mini_brief = dict(project.brief or {})
    mini_brief["_scene_context"] = scene.visual_description
    fresh = openai_service.regenerate_scene_content(
        concept or {"title": project.title, "angle": "", "summary": ""}, duration, mini_brief, client.to_dict())
    scene.narration = fresh["voiceover"]
    scene.visual_description = fresh["visual"]
    scene.asset_url = None
    scene.asset_thumb_url = None
    scene.locked = False
    db.session.commit()
    return jsonify({"ok": True, "scene": scene.to_dict()})


@bp.post("/<int:scene_id>/duplicate")
def duplicate_scene(project_id, scene_id):
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    project = CommercialProject.query.get_or_404(project_id)
    clone = Scene(
        project_id=project_id, order_index=scene.order_index + 1,
        start=scene.start, end=scene.end, narration=scene.narration,
        visual_description=scene.visual_description, asset_type=scene.asset_type,
        asset_source=scene.asset_source, asset_url=scene.asset_url,
        asset_thumb_url=scene.asset_thumb_url, is_cta=False,
    )
    clone.asset_meta = scene.asset_meta
    # shift everything after the duplicated scene down one slot
    for s in project.scenes.filter(Scene.order_index > scene.order_index).all():
        s.order_index += 1
    db.session.add(clone)
    db.session.flush()
    _resequence(project)
    db.session.commit()
    return jsonify({"ok": True, "scenes": [s.to_dict() for s in project.scenes.order_by(Scene.order_index).all()]})


@bp.delete("/<int:scene_id>")
def delete_scene(project_id, scene_id):
    scene = Scene.query.filter_by(id=scene_id, project_id=project_id).first_or_404()
    project = CommercialProject.query.get_or_404(project_id)
    if scene.is_cta:
        return jsonify({"ok": False, "error": "The CTA scene can't be deleted — edit it instead."}), 400
    db.session.delete(scene)
    db.session.flush()
    for i, s in enumerate(project.scenes.order_by(Scene.order_index).all()):
        s.order_index = i
    _resequence(project)
    db.session.commit()
    return jsonify({"ok": True, "scenes": [s.to_dict() for s in project.scenes.order_by(Scene.order_index).all()]})


@bp.post("/reorder")
def reorder_scenes(project_id):
    """Body: {"order": [scene_id, scene_id, ...]} in the new display order."""
    project = CommercialProject.query.get_or_404(project_id)
    data = request.get_json(force=True) or {}
    order = data.get("order") or []
    scenes_by_id = {s.id: s for s in project.scenes.all()}
    for idx, scene_id in enumerate(order):
        if scene_id in scenes_by_id:
            scenes_by_id[scene_id].order_index = idx
    db.session.flush()
    _resequence(project)
    db.session.commit()
    return jsonify({"ok": True, "scenes": [s.to_dict() for s in project.scenes.order_by(Scene.order_index).all()]})
