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
from .models import CsTemplate, CsTemplateScene

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


def _scene_specs(template: CsTemplate, resolved: dict) -> list[dict]:
    """One dict per template scene, in position order, with every text layer
    resolved and every background slot left for the editor's own scene
    actions to fill -- WO-CS3's "keep every existing scene action" rule.
    """
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
        out.append({
            "layout_key": tscene.layout_key,
            "default_duration": tscene.default_duration,
            "layers": layer_vals,
            "label": meta.get("label", tscene.layout_key),
            "needs_background": needs_background,
        })
    return out


def bind(project) -> dict:
    """Create (once) the `CommercialProject` this `cs_project` opens into.

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
        from modules.commercial_builder import client_link as cb_client_link
        from modules.commercial_builder import template_bind as cb_template_bind
        from modules.commercial_builder.config import COMMERCIAL_LENGTHS
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"The Commercial Builder is not available ({exc})."}

    domain = _client_domain(project.client_name)
    client_name = project.client_name or "Smart 1 Marketing"

    try:
        cb_client = cb_client_link.ensure_client(client_name, domain)
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"Could not find or create the brand profile ({exc})."}

    length = project.duration or template.duration or 30
    if length not in COMMERCIAL_LENGTHS:
        length = min(COMMERCIAL_LENGTHS, key=lambda x: abs(x - length))
    platform = PLATFORM_BY_CREATIVE_TYPE.get(project.creative_type, "both")
    fmt = project.aspect_ratio if project.aspect_ratio in _CB_FORMATS else "16:9"

    resolved = resolver.resolve(template, project, client=project.client_name, domain=domain)
    scenes = _scene_specs(template, resolved)

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
