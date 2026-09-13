"""`/creative/library` -- WO-CS10 item 1.

Approved spots (`CsProject.status == "Approved"`), browsable by industry,
archetype and client. A project's own `template_id` already names its
industry (`binder.project_industry`); its archetype -- when it went through
the AI Concepts/Script path -- lives on the bound `CommercialProject.brief`,
because that is the one place `library_spec.archetype_for()` reads it from,
and a copy stored here would be the exact drift CLAUDE.md's spelling table
exists to stop.

Only TOP-LEVEL approved projects belong in the library. `variation_kind`
(`"aspect"`, `"link_image"`, `"weather"`, ...) marks a project as a resized
or condition-specific COPY of another approved spot -- listing those
alongside their parent would show the same creative several times over for
one approval.
"""
from __future__ import annotations

from . import binder
from .models import CsProject, CsProjectVersion


def _archetype_of(project) -> str:
    if not project.cb_project_id:
        return ""
    try:
        from modules.commercial_builder import library_spec
        from modules.commercial_builder.models import CommercialProject as CbProject
        cb_project = CbProject.query.get(project.cb_project_id)
        if cb_project is None:
            return ""
        key, _source = library_spec.archetype_for(cb_project.brief, cb_project.commercial_type)
        return key
    except Exception:                                       # noqa: BLE001
        return ""


def approved_spots(*, industry: str = "", archetype: str = "", client: str = "") -> list[dict]:
    """Every approved, top-level spot a rep may open, with a client's own
    `library_opt_out` already applied. Never raises: a listing that could
    not be built must not take the gallery page down, only leave it
    reporting an empty shelf."""
    from . import brand_ext

    try:
        rows = (CsProject.query
               .filter_by(status="Approved", variation_kind="")
               .order_by(CsProject.approved_at.desc())
               .all())
    except Exception:                                       # noqa: BLE001
        return []

    out = []
    for project in rows:
        if client and (project.client_name or "").strip().lower() != client.strip().lower():
            continue
        if project.client_name and brand_ext.is_opted_out(project.client_name):
            continue
        row_industry = binder.project_industry(project)
        if industry and row_industry != industry:
            continue
        row_archetype = _archetype_of(project)
        if archetype and row_archetype != archetype:
            continue
        version = (project.versions.order_by(CsProjectVersion.version.desc()).first())
        out.append({
            "id": project.id, "name": project.name,
            "client_name": project.client_name or "",
            "industry": row_industry, "archetype": row_archetype,
            "creative_type": project.creative_type, "aspect_ratio": project.aspect_ratio or "",
            "duration": project.duration, "approved_at":
                project.approved_at.isoformat() if project.approved_at else "",
            "render_url": version.render_url if version else "",
            "thumbnail_url": version.thumbnail_url if version else "",
            "has_template_source": bool(project.template_id),
        })
    return out
