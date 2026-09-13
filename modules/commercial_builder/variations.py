"""Explicit variations: a reviewable edit plan before copying any project."""
from copy import deepcopy
from .db import db
from .models import CommercialProject, Scene, Variation, RenderApproval
from .services.media_state import has_presenter, fingerprint, timeline_signature


def plan(parent, changes):
    if not isinstance(changes, list) or not 1 <= len(changes) <= 12:
        raise ValueError("Choose between 1 and 12 scene edits.")
    scenes = {s.id: s for s in parent.scenes.all()}
    edits, seen = [], set()
    for change in changes:
        if not isinstance(change, dict) or not isinstance(change.get("scene_id"), int):
            raise ValueError("Each edit needs a scene and its new spoken text.")
        scene = scenes.get(change["scene_id"])
        text = change.get("narration")
        if not scene or scene.id in seen or not isinstance(text, str) or not 1 <= len(text.strip()) <= 3000:
            raise ValueError("Choose each scene once and enter 1–3000 characters of spoken text.")
        if scene.locked:
            raise ValueError(f"Scene {scene.order_index + 1} is locked. Unlock it before planning a variation.")
        seen.add(scene.id)
        if text.strip() == (scene.narration or "").strip():
            continue
        edits.append({"scene_id": scene.id, "scene": scene.order_index + 1,
                      "before": scene.narration or "", "after": text.strip(),
                      "regenerate": "HeyGen presenter" if has_presenter(scene.to_dict()) else "Narration",
                      "reuse_footage": scene.asset_type != "spokesperson"})
    if not edits:
        raise ValueError("Change at least one scene's spoken text.")
    payload = {"edits": edits, "full_voice_track": bool((parent.music or {}).get("voice_track_url")),
               "final_render": True, "reused_scenes": len(scenes) - len(edits),
               "note": "Creating this draft is free. Regenerate only the flagged speech, then render and approve the new cut."}
    payload["key"] = fingerprint({"project": parent.to_dict(), "edits": edits})
    return payload


def clone(parent, *, title=None):
    child = CommercialProject(client_id=parent.client_id, campaign_id=parent.campaign_id,
        title=(title or f"{parent.title} — variation")[:300], length_seconds=parent.length_seconds,
        commercial_type=parent.commercial_type, platform=parent.platform, status="draft")
    for key in ("formats", "brief", "concepts", "script", "music", "cta"):
        setattr(child, key, deepcopy(getattr(parent, key)))
    child.selected_concept_id = parent.selected_concept_id
    db.session.add(child)
    db.session.flush()
    mapping = {}
    for scene in parent.scenes.all():
        copied = Scene(project_id=child.id)
        for key in ("order_index", "start", "end", "narration", "visual_description", "asset_type", "asset_source",
                    "asset_url", "asset_thumb_url", "is_cta", "locked", "asset_meta"):
            setattr(copied, key, deepcopy(getattr(scene, key)))
        db.session.add(copied)
        mapping[scene.id] = copied
    db.session.flush()
    # New scene IDs are part of the full-track signature. Exact clones can
    # reuse the paid track; an actual speech change invalidates it below.
    music = dict(child.music or {})
    if music.get("voice_signature") and not (parent.music or {}).get("voice_track_stale"):
        music["voice_signature"] = timeline_signature([s.to_dict() for s in mapping.values()])
        music.pop("voice_track_stale", None)
    child.music = music
    return child, mapping


def create(parent, data):
    approval = RenderApproval.query.filter_by(project_id=parent.id).order_by(RenderApproval.id.desc()).first()
    if not approval:
        raise ValueError("Approve a source commercial before making controlled campaign variations.")
    from .finishing_models import RenderInspection
    from .services.finished_video import creative_key
    inspected = db.session.get(RenderInspection, approval.render_job_id)
    if inspected and (inspected.expected or {}).get("creative_key") and inspected.expected["creative_key"] != creative_key(parent, [s.to_dict() for s in parent.scenes.all()]):
        raise ValueError("The source changed after its approved render. Render and approve the current version before creating campaign variations.")
    preview = plan(parent, data.get("changes"))
    if data.get("plan_key") != preview["key"]:
        raise ValueError("The source or edits changed. Preview the changes again.")
    child, mapping = clone(parent, title=data.get("title"))
    for edit in preview["edits"]:
        scene = mapping[edit["scene_id"]]
        scene.narration = edit["after"]
        scene.locked = False
    db.session.flush()  # existing speech invalidation preserves old takes
    script = dict(child.script or {})
    script["scenes"] = [{"start": s.start, "end": s.end, "voiceover": s.narration, "visual": s.visual_description}
                        for s in mapping.values()]
    child.script = script
    brief = dict(child.brief or {})
    brief["variation_source"] = parent.id
    brief["variation_plan"] = preview
    child.brief = brief
    db.session.add(Variation(parent_project_id=parent.id, child_project_id=child.id,
                            variation_type="controlled", changes=preview))
    db.session.commit()
    return child, preview
