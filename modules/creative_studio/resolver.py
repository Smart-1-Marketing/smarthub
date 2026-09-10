"""Template variable resolution.

CLAUDE.md's own account of the order, verbatim: "manual override on the
project -> brief field -> Brand Kit -> template default -> unresolved."
`resolve()` is the one function that walks it, so the gallery preview, the
project detail page and (in WO-CS3) the editor cannot each invent their own
reading of what a variable is worth.

``weather_headline`` / ``weather_offer`` / ``weather_image`` / ``weather_cta``
are reserved: CLAUDE.md says they "resolve to the base value when no weather
variant is active," and there is no weather-variant concept anywhere in this
Hub yet (SmartForecast integration is a later phase) -- so every one of them
resolves as if it were its own base name (``weather_headline`` reads exactly
like ``headline``) at no extra cost today, and the day a variant exists this
is the one place that has to change.
"""
from __future__ import annotations

WEATHER_PREFIX = "weather_"


def _base_name(name: str) -> str:
    if name.startswith(WEATHER_PREFIX) and len(name) > len(WEATHER_PREFIX):
        return name[len(WEATHER_PREFIX):]
    return name


def _brand_value(name: str, client: str, domain: str) -> str:
    """One variable's value from the Brand Kit, or "".

    Tries the extended overlay's own fields first (services, promotions, the
    creative preferences -- the business facts nothing else publishes), then
    the handful of well-known names every template is likely to use
    (company name, website, logo, the three color roles, phone). Never
    raises: a Brand Kit that cannot be read costs this one variable, not the
    resolution of every other one.
    """
    if not client:
        return ""
    try:
        from . import brand_ext
        kit = brand_ext.kit(client, domain)
    except Exception:                                   # noqa: BLE001
        kit = {}
    ext = kit.get("ext") or {}

    if name == "company_name":
        return kit.get("name") or client
    if name == "website":
        return kit.get("domain") or domain or ""
    if name == "logo":
        tiles = kit.get("logo_tiles") or []
        return tiles[0].get("url", "") if tiles else ""
    if name in ("primary_color", "secondary_color", "accent_color"):
        role = name.split("_", 1)[0]
        try:
            from hub.brand_template import get as get_confirmed
            picked = (get_confirmed(client).get("colors") or {}).get(role, "")
        except Exception:                               # noqa: BLE001
            picked = ""
        if picked:
            return picked
        palette = kit.get("palette") or []
        idx = {"primary": 0, "secondary": 1, "accent": 2}.get(role, -1)
        return palette[idx]["hex"] if 0 <= idx < len(palette) else ""
    if name == "phone":
        locations = ext.get("locations") or []
        if locations:
            return locations[0]
        try:
            from hub.scan_facts import contact_observed
            observed = contact_observed(domain)
        except Exception:                               # noqa: BLE001
            observed = {}
        return (observed.get("fields") or {}).get("phone", "")
    if name == "cta":
        return ext.get("cta_style") or ""
    if name == "tagline":
        return (kit.get("description") or "")[:120]

    ext_val = ext.get(name)
    if isinstance(ext_val, str):
        return ext_val
    if isinstance(ext_val, list):
        return ", ".join(str(v) for v in ext_val[:3])
    return ""


def resolve(template, project=None, *, client: str = "", domain: str = "") -> dict:
    """Every variable a template declares, resolved for one project.

    ``template`` is a `CsTemplate` row (its `.variables` relationship is
    read). ``project`` is a `CsProject` row or None -- resolving a template
    with no project (the gallery preview) skips straight to Brand Kit and
    the template default, which is exactly what a preview should show: what
    this template looks like for this client today, before anyone has typed
    a brief.

    Returns ``{name: {"value": str, "source": "manual"|"brief"|"brand"|
    "template_default"|"unresolved", "required": bool}}``.
    """
    overrides = (project.resolved_vars if project else {}) or {}
    brief = (project.brief if project else {}) or {}
    out: dict[str, dict] = {}

    for v in template.variables:
        name = v.name
        required = bool(v.required)
        base = _base_name(name)

        override_val = overrides.get(name)
        if override_val and str(override_val).strip():
            out[name] = {"value": str(override_val), "source": "manual", "required": required}
            continue

        brief_val = brief.get(base)
        if brief_val and str(brief_val).strip():
            out[name] = {"value": str(brief_val), "source": "brief", "required": required}
            continue

        brand_val = _brand_value(base, client, domain)
        if brand_val:
            out[name] = {"value": brand_val, "source": "brand", "required": required}
            continue

        if v.default:
            out[name] = {"value": v.default, "source": "template_default", "required": required}
            continue

        out[name] = {"value": "", "source": "unresolved", "required": required}

    return out


def unresolved_required(resolved: dict) -> list[str]:
    """Names of every required variable that resolved to nothing -- the
    list a project detail page blocks a render on (from WO-CS5 onward) and
    surfaces today so a rep can see what a template still needs before they
    get to that point."""
    return [name for name, r in resolved.items()
            if r["source"] == "unresolved" and r["required"]]
