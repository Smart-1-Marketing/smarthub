"""The Brand Kit fields Todd's spec asks for that Brandfetch cannot answer.

`hub/client_brand.brand_kit()` already reads the one stored brand record for
logos, colours and fonts, merged with what the last scan observed and
whichever tile a rep has confirmed through `hub/brand_template.py`. None of
that is duplicated here -- house rule 2 is explicit that a Commercial Builder
field belongs on the shared record rather than a third copy, and the shared
record for logo/colour/font is that one.

What is missing is the half nothing publishes: services, products,
promotions, disclaimers, legal copy, certifications, locations, and the
creative preferences (logo position, CTA style, image style, voice, music
style, preferred spokesperson, the pronunciation dictionary). Those are
business facts a rep types in once, not something Brandfetch can look up --
so they are a small overlay, keyed on the same client identity as everything
else (`hub.client_key.name_slug`) and stored the way `hub/brand_template.py`
stores a confirmed pick: one JSON file per client, mirrored by jsonstore.

`kit(client, domain)` is the one function a screen calls -- it merges the
read-only Brandfetch/observed data with this overlay so the Brand Kit screen
never has to reconcile two answers to "what is this client's brand" on its
own.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from hub import jsonstore

# One field per business fact the build spec names in §5/§7 that has nowhere
# else to live. Kept as a flat dict of strings/lists rather than a table --
# create_all() never adds a column to an existing table, and a schema this
# unsettled (Todd's own open questions list several of these as still being
# decided) is exactly the shape a JSON overlay is for rather than a Postgres
# migration this Hub cannot easily reverse.
_TEXT_FIELDS = (
    "logo_position", "cta_style", "image_style", "brand_voice",
    "preferred_music_style", "preferred_spokesperson_id", "preferred_voice_id",
)
_LIST_FIELDS = (
    "services", "products", "promotions", "disclaimers", "legal",
    "certifications", "locations",
)
# WO-CS10 item 5's "library_opt_out flag on the brand record" -- a business
# decision typed once, exactly the shape every other field in this file
# already is, not a fact Brandfetch or a scan could ever answer.
_BOOL_FIELDS = ("library_opt_out",)


def _key(client: str) -> str:
    from hub.client_key import name_slug
    return name_slug(client) or "client"


def _path(client: str) -> str:
    return os.path.join(jsonstore.data_dir("creative_studio", "brand_ext"),
                        _key(client) + ".json")


def get(client: str) -> dict:
    """The overlay alone -- what this Brand Kit screen owns and can edit.

    Never raises: a store that cannot be read must not cost the rest of the
    Brand Kit screen, which still has logos and colours to show from
    `brand_kit()` regardless.
    """
    row = jsonstore.read_json(_path(client), default=None)
    if not isinstance(row, dict):
        row = {}
    out = {"client": client}
    for f in _TEXT_FIELDS:
        out[f] = str(row.get(f) or "")
    for f in _LIST_FIELDS:
        v = row.get(f)
        out[f] = [str(x) for x in v] if isinstance(v, list) else []
    pron = row.get("pronunciation_dict")
    out["pronunciation_dict"] = pron if isinstance(pron, dict) else {}
    for f in _BOOL_FIELDS:
        out[f] = bool(row.get(f))
    out["updated_at"] = str(row.get("updated_at") or "")
    out["updated_by"] = str(row.get("updated_by") or "")
    return out


def save(client: str, fields: dict, actor: str = "") -> dict:
    """Write the fields given; leave everything else on the row alone.

    A partial save (one field edited, the rest untouched) must not clobber
    the rest of the record -- the `set_music` trap CLAUDE.md names for the
    Commercial Builder's music panel, wearing a brand kit.
    """
    client = str(client or "").strip()
    if not client:
        return {"ok": False, "error": "No client named."}

    row = jsonstore.read_json(_path(client), default={}) or {}
    if not isinstance(row, dict):
        row = {}

    for f in _TEXT_FIELDS:
        if f in fields:
            row[f] = str(fields.get(f) or "").strip()[:300]
    for f in _LIST_FIELDS:
        if f in fields:
            v = fields.get(f)
            if isinstance(v, str):
                v = [ln.strip() for ln in v.splitlines() if ln.strip()]
            row[f] = [str(x)[:300] for x in (v or []) if str(x).strip()][:60]
    if "pronunciation_dict" in fields:
        pron = fields.get("pronunciation_dict") or {}
        if isinstance(pron, dict):
            row["pronunciation_dict"] = {
                str(k)[:80]: str(v)[:120] for k, v in pron.items() if str(k).strip()}
    for f in _BOOL_FIELDS:
        if f in fields:
            row[f] = bool(fields.get(f))

    row["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row["updated_by"] = str(actor or "")[:120]
    jsonstore.write_json(_path(client), row)
    return {"ok": True, **get(client)}


def is_opted_out(client: str) -> bool:
    """Read alone, for the library's exclusion filter -- pulling the whole
    overlay (and, through `kit()`, a Brandfetch-merged record) per client on
    every library listing would be the "eight calls on a page load"
    `services/provider_check.py` refuses; this is one small local read."""
    if not str(client or "").strip():
        return False
    row = jsonstore.read_json(_path(client), default=None)
    return bool(isinstance(row, dict) and row.get("library_opt_out"))


def kit(client: str, domain: str = "") -> dict:
    """Logos/colours/fonts from `hub.client_brand.brand_kit()`, plus this
    module's overlay of business and creative-preference fields, in one dict
    a screen can render without knowing there are two sources."""
    try:
        from hub.client_brand import brand_kit as _brand_kit
        base = _brand_kit(client, domain)
    except Exception:                                   # noqa: BLE001
        base = {"found": False, "client": client, "domain": domain,
                "logos": [], "colors": [], "fonts": [],
                "logo_tiles": [], "palette": [], "has_brand": False}
    out = dict(base)
    out["ext"] = get(client)
    return out
