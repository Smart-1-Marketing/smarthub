"""Opening a `cs_project` in the Storyboard Editor (WO-CS3).

CLAUDE.md's own instruction for this work order: "Do not build a new editor
and do not build a timeline" -- the Commercial Builder's Storyboard Editor
(`modules/commercial_builder`) already is the scene editor, complete with
Find Stock, Generate AI, Use Spokesperson, Upload, Use Client Asset, reorder,
duplicate, delete, duration and regenerate. What was missing is a way for a
project built from a `cs_template` to arrive there with its scenes already
laid out, rather than empty and waiting on a brief nobody is going to type
because the template already answered what each scene is for.

`bind()` is the whole of it: find or adopt the client's `CommercialProject`
brand row, resolve every `{{variable}}` the template's scenes reference
against this project's Brand Kit and brief, and hand the result to
`modules.commercial_builder.template_bind.build_from_template()` -- which is
where the actual `cb_projects` / `cb_scenes` rows get written, because
`CsProject.cb_project_id`'s own comment is explicit that this module "never
writes to modules.commercial_builder's tables, it only remembers which row
it handed the work to."

Binding happens once. `project.cb_project_id` is the record of it, and a
second `bind()` call for a project that already has one is a no-op that
returns the row it already made -- reopening a project does not rebuild its
storyboard out from under whatever a rep has since done to it.
"""
from __future__ import annotations

from . import layouts, resolver
from .models import CsProject, CsTemplate, CsTemplateScene

# A cs_template's `creative_type` says what kind of deliverable this is; a
# CommercialProject's `platform` says which screen it plays on, and the two
# vocabularies are orthogonal (CLAUDE.md's PLATFORMS are ctv/youtube/social/
# both, driving QC rules and safe-area guidance that has nothing to do with
# "product ad" versus "UGC ad"). Only the creative types that actually route
# to the template gallery (config.CREATIVE_TYPES's `route` field) ever carry
# a cs_template, so only those need an entry here.
PLATFORM_BY_CREATIVE_TYPE = {
    "video_commercial": "both",
    "ctv_commercial": "ctv",
    "social_video": "social",
    "ugc_ad": "social",
    "product_ad": "both",
    "intro_outro": "both",
}

_CB_FORMATS = ("16:9", "9:16", "1:1")


def _client_domain(client_name: str) -> str:
    if not client_name:
        return ""
    try:
        from hub.clients_registry import find_client
        hit = find_client(client_name)
        return (hit or {}).get("domain", "")
    except Exception:                                     # noqa: BLE001
        return ""


def _project_chrome(resolved: dict) -> dict:
    """The three values every layout may draw that are the *project's*, not
    any one scene's -- a logo, a phone number, a website. Read straight off
    the already-resolved variable table (`resolver.resolve()`'s own
    priority order), so a project with no ``logo``/``phone``/``website``
    variable on its template simply contributes nothing here, the same
    "absent, not invented" rule every reader of this table follows."""
    return {
        "logo_url": (resolved.get("logo") or {}).get("value") or "",
        "phone": (resolved.get("phone") or {}).get("value") or "",
        "website": (resolved.get("website") or {}).get("value") or "",
    }


def _scene_specs(template: CsTemplate, resolved: dict) -> list[dict]:
    """One dict per template scene, in position order, with every text layer
    resolved and every background slot left for the editor's own scene
    actions to fill -- WO-CS3's "keep every existing scene action" rule.

    WO-CS7 adds two computed fields per scene, both read by
    `modules.commercial_builder.template_bind.build_from_template` as an
    opaque passthrough onto `Scene.asset_meta` -- neither function there
    needs to know a layout exists. `text_overlay` is this scene's own
    Creatomate elements at THIS project's aspect ratio, from
    `layouts.elements_for()`: before this, only the end-card scene ever
    drew anything, and even that read an empty `CommercialProject.cta`
    nothing populated -- every other layout rendered as bare footage.
    `background_fill` is the flat colour a layout with no background slot
    (`offer_card`, `logo_reveal`) sits on, since a background-less scene
    otherwise becomes an image element with no `source` at all.
    """
    chrome = _project_chrome(resolved)
    out = []
    for tscene in template.scenes.order_by(CsTemplateScene.position):
        allowed = set(layouts.layers_for(tscene.layout_key))
        needs_background = None
        layer_vals: dict[str, dict] = {}
        for key, raw in (tscene.layers or {}).items():
            if key == "background":
                # "slot:image" / "slot:video" -- not a variable, a note to
                # the storyboard editor about what kind of footage this
                # scene wants. A literal URL or a {{variable}} background is
                # left for a future template author; nothing here invents one.
                if isinstance(raw, str) and raw.startswith("slot:"):
                    kind = raw.split(":", 1)[1]
                    if kind in layouts.SLOT_KINDS:
                        needs_background = kind
                continue
            if key not in allowed:
                continue
            var_name = (raw[2:-2] if isinstance(raw, str)
                       and raw.startswith("{{") and raw.endswith("}}") else None)
            if var_name:
                r = resolved.get(var_name) or {}
                # `var` rides along so the layer panel's variable chip knows
                # which project-level override to write -- without it, an
                # edit here could only ever change this one scene's own copy,
                # never the variable every other scene referencing it reads.
                layer_vals[key] = {"value": r.get("value") or "",
                                   "source": r.get("source") or "unresolved",
                                   "var": var_name}
            elif isinstance(raw, str) and raw:
                layer_vals[key] = {"value": raw, "source": "template"}
        meta = layouts.layout(tscene.layout_key) or {}
        aspect = "16:9"  # resolved by the caller against the project below
        out.append({
            "layout_key": tscene.layout_key,
            "default_duration": tscene.default_duration,
            "layers": layer_vals,
            "label": meta.get("label", tscene.layout_key),
            "needs_background": needs_background,
            "background_fill": "" if meta.get("slots") else "#12151c",
            "_chrome": chrome,   # consumed by build() below, never written to CB
        })
    return out


def _attach_text_overlays(scenes: list[dict], aspect: str) -> None:
    """Compute `text_overlay` for every scene spec, now that the project's
    chosen aspect ratio is known -- `_scene_specs()` runs before `bind()`
    picks a format, so this is the one place both are in hand at once.

    `chrome` (the project's logo/phone/website) rides onto the scene spec
    too, not only consumed here -- `bind_variation()` recomputes
    `text_overlay` for a NEW aspect from a scene it copied, and it must not
    have to re-run variable resolution to do it: every value it needs is
    already sitting in this scene's own resolved data, the same "same
    resolved variables" WO-CS7 asks for.
    """
    for spec in scenes:
        chrome = spec.pop("_chrome", {})
        spec["chrome"] = chrome
        spec["text_overlay"] = layouts.elements_for(
            spec["layout_key"], aspect, spec.get("layers") or {},
            logo_url=chrome.get("logo_url", ""), phone=chrome.get("phone", ""),
            website=chrome.get("website", ""))


def _ensure_client_row(project):
    """`(cb_client, error)` -- the client-adoption half both bind paths need,
    factored out so it is asked once rather than copied twice."""
    try:
        from modules.commercial_builder import client_link as cb_client_link
    except Exception as exc:                              # noqa: BLE001
        return None, f"The Commercial Builder is not available ({exc})."
    domain = _client_domain(project.client_name)
    client_name = project.client_name or "Smart 1 Marketing"
    try:
        return cb_client_link.ensure_client(client_name, domain), None
    except Exception as exc:                              # noqa: BLE001
        return None, f"Could not find or create the brand profile ({exc})."


def bind(project) -> dict:
    """Create (once) the `CommercialProject` this `cs_project` opens into.

    Requires a template -- WO-CS3's rule, unchanged: opening the Storyboard
    Editor with nothing built yet reads as a broken page rather than an
    invitation to start from a brief, which is a different, slower flow
    `bind_for_generation()` below is for. `/creative-studio/api/projects/<id>/open`
    is the only caller of this function.

    Returns `{"ok": True, "cb_project_id": int}` or `{"ok": False, "error": str}`.
    Never raises: the Commercial Builder not being installed, a template
    that has since been deleted, and an ordinary database error are all
    reported the same way a caller can show on a page, per the tri-state
    rule this Hub applies to every cross-module join.
    """
    if project.cb_project_id:
        return {"ok": True, "cb_project_id": project.cb_project_id}

    if not project.template_id:
        return {"ok": False, "error": "Pick a template before opening the editor."}

    template = CsTemplate.query.get(project.template_id)
    if template is None:
        return {"ok": False, "error": "That template no longer exists."}
    if template.scenes.count() == 0:
        return {"ok": False, "error": "That template has no scenes to build from."}

    try:
        from modules.commercial_builder import template_bind as cb_template_bind
        from modules.commercial_builder.config import COMMERCIAL_LENGTHS
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"The Commercial Builder is not available ({exc})."}

    cb_client, error = _ensure_client_row(project)
    if error:
        return {"ok": False, "error": error}

    domain = _client_domain(project.client_name)
    length = project.duration or template.duration or 30
    if length not in COMMERCIAL_LENGTHS:
        length = min(COMMERCIAL_LENGTHS, key=lambda x: abs(x - length))
    platform = PLATFORM_BY_CREATIVE_TYPE.get(project.creative_type, "both")
    fmt = project.aspect_ratio if project.aspect_ratio in _CB_FORMATS else "16:9"

    resolved = resolver.resolve(template, project, client=project.client_name, domain=domain)
    scenes = _scene_specs(template, resolved)
    _attach_text_overlays(scenes, fmt)

    try:
        cb_project = cb_template_bind.build_from_template(
            client_id=cb_client.id, client_name=cb_client.name, title=project.name,
            length_seconds=length, platform=platform, formats=[fmt],
            commercial_type="stock_vo", scenes=scenes)
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"Could not build the storyboard ({exc})."}

    from .db import db
    project.cb_project_id = cb_project.id
    db.session.commit()

    try:
        from hub import audit
        audit.log("creative_studio", "project_opened_in_editor", actor=project.created_by or "",
                  client=project.client_name or None, project=project.name,
                  template=project.template_id, cb_project_id=cb_project.id)
    except Exception:                                     # noqa: BLE001
        pass

    return {"ok": True, "cb_project_id": cb_project.id}


def bind_for_generation(project) -> dict:
    """Like `bind()`, but a project with no template binds to a **blank**
    `CommercialProject` (no scenes) instead of refusing.

    WO-CS4's "storyboard" and "script" job kinds write a concept and a script
    onto whatever this hands back -- a project that has not picked a template
    is exactly the case they exist for, since a template-bound project's
    scenes already say what each one is for and has no reason to ask a model.
    Called only from the `/generate/...` routes those jobs are enqueued
    through; `/open` keeps calling `bind()` above, unchanged, so a project
    with no template still refuses to open the (empty) Storyboard Editor.
    """
    if project.template_id:
        return bind(project)
    if project.cb_project_id:
        return {"ok": True, "cb_project_id": project.cb_project_id}

    try:
        from modules.commercial_builder import template_bind as cb_template_bind
        from modules.commercial_builder.config import COMMERCIAL_LENGTHS
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"The Commercial Builder is not available ({exc})."}

    cb_client, error = _ensure_client_row(project)
    if error:
        return {"ok": False, "error": error}

    length = project.duration or 30
    if length not in COMMERCIAL_LENGTHS:
        length = min(COMMERCIAL_LENGTHS, key=lambda x: abs(x - length))
    platform = PLATFORM_BY_CREATIVE_TYPE.get(project.creative_type, "both")
    fmt = project.aspect_ratio if project.aspect_ratio in _CB_FORMATS else "16:9"

    try:
        cb_project = cb_template_bind.build_blank(
            client_id=cb_client.id, client_name=cb_client.name, title=project.name,
            length_seconds=length, platform=platform, formats=[fmt],
            commercial_type="stock_vo", brief=project.brief or {})
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"Could not start the storyboard ({exc})."}

    from .db import db
    project.cb_project_id = cb_project.id
    db.session.commit()

    try:
        from hub import audit
        audit.log("creative_studio", "project_opened_in_editor", actor=project.created_by or "",
                  client=project.client_name or None, project=project.name,
                  template=None, cb_project_id=cb_project.id)
    except Exception:                                     # noqa: BLE001
        pass

    return {"ok": True, "cb_project_id": cb_project.id}


def bind_variation(project) -> dict:
    """Build a variation's own storyboard by copying its parent's, scene
    for scene -- WO-CS7. "Create Variations" re-renders the same scenes,
    same resolved variables, same footage, at a new aspect: this is the
    function that makes that literally true rather than a description of
    intent, because it reads the PARENT's already-bound scenes rather than
    resolving the template a second time.

    Requires `project.parent_project_id` (set by the caller before this
    runs, alongside `variation_kind`). Idempotent like `bind()`: a project
    that already has a `cb_project_id` returns it rather than rebuilding.
    """
    if project.cb_project_id:
        return {"ok": True, "cb_project_id": project.cb_project_id}
    if not project.parent_project_id:
        return {"ok": False, "error": "This project has no parent to copy from."}

    parent = CsProject.query.get(project.parent_project_id)
    if parent is None or not parent.cb_project_id:
        return {"ok": False, "error": "The source project has no storyboard to copy."}

    try:
        from modules.commercial_builder import template_bind as cb_template_bind
        from modules.commercial_builder.models import (CommercialProject as CbProject,
                                                        Scene as CbScene)
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"The Commercial Builder is not available ({exc})."}

    cb_parent = CbProject.query.get(parent.cb_project_id)
    if cb_parent is None:
        return {"ok": False, "error": "The source storyboard no longer exists."}

    cb_client, error = _ensure_client_row(project)
    if error:
        return {"ok": False, "error": error}

    from .reframe import looks_like_video, reframe_background

    target = project.aspect_ratio
    width, height = layouts.ASPECT_DIMS.get(target, (1920, 1080))

    source_scenes = list(cb_parent.scenes.order_by(CbScene.order_index).all())
    if project.variation_kind == "link_image":
        # The static link/display frame is a still of the end card alone,
        # never the whole storyboard -- WO-CS7's own words. A template with
        # no end_card scene at all still gets something rather than an
        # empty render: its last scene, whatever that is.
        end_cards = [s for s in source_scenes if s.is_cta]
        source_scenes = end_cards or source_scenes[-1:]

    new_scenes = []
    cursor = 0.0
    for scene in source_scenes:
        meta = dict(scene.asset_meta or {})
        layout_key = meta.get("layout_key") or ""
        layer_values = meta.get("layers") or {}
        chrome = meta.get("chrome") or {}
        asset_url = scene.asset_url or ""
        if asset_url and layout_key:
            # Only a background this module itself understands the shape
            # of gets reframed -- a scene the script pipeline wrote (no
            # layout_key) is not this work order's to touch.
            asset_url = reframe_background(
                asset_url, width, height,
                resource_type="video" if looks_like_video(asset_url, meta) else "image")
        new_meta = dict(meta)
        new_meta["text_overlay"] = (
            layouts.elements_for(layout_key, target, layer_values,
                                 logo_url=chrome.get("logo_url", ""),
                                 phone=chrome.get("phone", ""),
                                 website=chrome.get("website", ""))
            if layout_key else [])
        duration = round(float(scene.end or 0) - float(scene.start or 0), 2)
        new_scenes.append({
            "start": round(cursor, 2), "end": round(cursor + duration, 2),
            "narration": scene.narration or "", "visual_description": scene.visual_description or "",
            "is_cta": bool(scene.is_cta), "asset_url": asset_url,
            "asset_type": scene.asset_type or "", "asset_source": scene.asset_source or "",
            "asset_thumb_url": scene.asset_thumb_url or "", "asset_meta": new_meta,
        })
        cursor += duration

    length = max(1, round(cursor)) if project.variation_kind != "link_image" else 1

    try:
        cb_project = cb_template_bind.build_from_scenes(
            client_id=cb_client.id, client_name=cb_client.name, title=project.name,
            length_seconds=length, platform=cb_parent.platform, formats=[target],
            commercial_type=cb_parent.commercial_type, scenes=new_scenes)
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"Could not build the variation's storyboard ({exc})."}

    from .db import db
    project.cb_project_id = cb_project.id
    project.duration = length
    db.session.commit()

    try:
        from hub import audit
        audit.log("creative_studio", "variation_bound", actor=project.created_by or "",
                  client=project.client_name or None, project=project.name,
                  detail=f"{project.variation_kind} of #{parent.id} at {target}")
    except Exception:                                     # noqa: BLE001
        pass

    return {"ok": True, "cb_project_id": cb_project.id}
