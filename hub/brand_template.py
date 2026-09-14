"""One rep-confirmed pick of a client's brand, instead of every tool guessing.

`hub/client_brand.brand_kit()` already merges Brandfetch data and what the
last scan observed into one card — several logos, several colours, each tile
saying where it came from. What it has never answered is which one is *the*
one. Brandfetch itself says so: the Display Ad Builder's own `/site-brand`
route notes that Brandfetch "frequently does not say which entry is the brand
colour", and `brand_guide_payload()` has always guessed — `kit["colors"][0]`,
whatever position the API happened to return first — and every tool built
since has either copied that guess or made its own. `hub/client_context.py`
takes `kit["logos"][0]` the same way, for the same reason.

This is the guess replaced by a pick. A rep opens the merged card once,
presses the logo and the colour that are actually the brand, and this is
saved. `brand_kit()` reads it back and promotes the confirmed logo and colour
to the front of `logos` / `colors` — so `client_context.py`,
`brand_guide_payload()` and anything else that has always trusted position
zero gets the confirmed answer automatically, with no caller-side change and
nothing new to keep in step. A caller that has never heard of this file keeps
working exactly as it did.

Three rules, the ones this corner of the Hub keeps having to relearn.

**A logo is never invented; a colour can be typed.** A logo pick is checked
against a fresh `brand_kit()` call at save time — the exact URL must be one
the merge is *currently* offering for that client, and a stale pick (a
Brandfetch answer that has changed since it was confirmed) is refused rather
than silently accepted, the `client_urls.NOT_A_WEBSITE` rule wearing a swatch.
A colour is different on purpose: the automated palette is Brandfetch's guess
and a scan's own sighting, and both routinely pick up a page's accent colour
— an announcement bar, a header background — rather than the brand's actual
one. A rep holding the client's real style guide is the authority the guess
exists to be corrected by, so a colour is accepted as typed, as long as it is
a well-formed hex; it does not have to be one this Hub ever observed for that
client. `hub/client_brand.py` still draws it on the card, as its own swatch
labelled where it came from, so a rep who typed a colour can see that and
clear it exactly like any other.

**A pick is not a fetch.** Saving one costs nothing at Brandfetch — it is a
choice among tiles `brand_kit()` already paid for and is already showing, the
same distinction `hub/brand_lookup.py` draws between a page load and a
button.

**Read by nothing until it is used, which is how a placeholder stays a
placeholder.** `brand_kit()` reads this on every call, so a pick a rep makes
changes what a proposal, a brand-guide push and a Magic Resize project
reference all say from that moment on — rather than becoming the sixth
declared-and-unwired field this codebase already lists.

## What this deliberately does not do

It does not reach a provider, and it does not touch the raw Brandfetch or
scan data those two continue to own — a pick names an existing tile, it does
not replace the merge. Once approved, however, that choice is authoritative:
even an observed-only logo reaches the shared brand payload and downstream
builders. Unapproved observations remain candidates and never cross that
boundary.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from hub import jsonstore

COLOR_ROLES = ("primary", "secondary", "accent", "background", "text")
FONT_ROLES = ("heading", "body")


def _key(client: str) -> str:
    from hub.client_key import name_slug
    return name_slug(client) or "client"


def _path(client: str) -> str:
    return os.path.join(jsonstore.data_dir("brand_template"), _key(client) + ".json")


def get(client: str) -> dict:
    """The confirmed pick for a client, or an empty shell.

    `picked` is its own field rather than the caller inferring it from an
    empty dict, so "nobody has confirmed anything yet" reads differently from
    "confirmed, and later cleared" — the tri-state this corner of the Hub
    keeps needing (`connected_accounts_result()`'s rule, wearing a swatch).
    """
    row = jsonstore.read_json(_path(client), default=None)
    if not isinstance(row, dict):
        row = {}
    colors_in = row.get("colors") if isinstance(row.get("colors"), dict) else {}
    colors = {role: str(colors_in.get(role) or "") for role in COLOR_ROLES}
    fonts_in = row.get("fonts") if isinstance(row.get("fonts"), dict) else {}
    fonts = {role: str(fonts_in.get(role) or "") for role in FONT_ROLES}
    out = {
        "client": client,
        "logo_url": str(row.get("logo_url") or ""),
        "logo_theme": str(row.get("logo_theme") or ""),
        "colors": colors,
        "fonts": fonts,
        "updated_at": str(row.get("updated_at") or ""),
        "updated_by": str(row.get("updated_by") or ""),
    }
    out["picked"] = bool(out["logo_url"] or any(colors.values()) or any(fonts.values()))
    return out


def _hex_of(value: str) -> str:
    v = str(value or "").strip().upper()
    if v and not v.startswith("#"):
        v = "#" + v
    return v if re.fullmatch(r"#[0-9A-F]{3,8}", v) else ""


def save(client: str, domain: str, field: str, value: str, actor: str = "") -> dict:
    """Confirm a tile or type a colour — or clear either with `value=""`.

    A logo still has to be one `brand_kit()` is currently showing for this
    client, never a stray URL. A colour needs only to be a well-formed hex:
    it is a rep naming the client's real brand colour, not this Hub guessing
    at one, so there is nothing to check it against. Clearing is always
    allowed either way: taking a pick back can never be "not offered".
    """
    client = str(client or "").strip()
    if not client:
        return {"ok": False, "error": "No client named."}
    font_role = field.removeprefix("font_") if field.startswith("font_") else ""
    if field != "logo" and field not in COLOR_ROLES and font_role not in FONT_ROLES:
        return {"ok": False, "error": f"{field!r} is not something this can confirm."}

    value = str(value or "").strip()
    logo_theme = ""
    if value:
        if field == "logo":
            from hub.client_brand import brand_kit
            kit = brand_kit(client, domain)
            tile = next((t for t in (kit.get("logo_tiles") or [])
                        if t.get("url") == value), None)
            if not tile:
                return {"ok": False, "error": "That logo is not one this Hub "
                        "currently has on file for this client."}
            logo_theme = tile.get("theme", "")
        elif field in COLOR_ROLES:
            hx = _hex_of(value)
            if not hx:
                return {"ok": False, "error": "That isn't a color code — use "
                        "a hex value like #0077B4."}
            value = hx
        else:
            from hub.client_brand import brand_kit
            names = {str(f.get("name") or "") for f in
                     (brand_kit(client, domain).get("fonts") or [])}
            if value not in names:
                return {"ok": False, "error": "That font is not one this Hub "
                        "currently has on file for this client."}

    row = jsonstore.read_json(_path(client), default={}) or {}
    if not isinstance(row, dict):
        row = {}
    if field == "logo":
        row["logo_url"] = value
        row["logo_theme"] = logo_theme
    elif field in COLOR_ROLES:
        colors = row.get("colors") if isinstance(row.get("colors"), dict) else {}
        if value:
            colors[field] = value
        else:
            colors.pop(field, None)
        row["colors"] = colors
    else:
        fonts = row.get("fonts") if isinstance(row.get("fonts"), dict) else {}
        if value:
            fonts[font_role] = value
        else:
            fonts.pop(font_role, None)
        row["fonts"] = fonts
    row["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row["updated_by"] = actor or ""
    jsonstore.write_json(_path(client), row)

    try:
        from hub import audit
        audit.log("hub", "brand_template_set", actor=actor or None, client=client,
                  detail=f"{field}={'cleared' if not value else value}")
    except Exception:                                   # noqa: BLE001
        pass
    return {"ok": True, "template": get(client)}


def save_many(client: str, domain: str, values: dict, actor: str = "",
              *, allow_stale: bool = False) -> dict:
    """Validate and save a complete visual identity as one review action.

    Validation happens before the first write, so an invalid stale logo or
    font cannot leave half of an approval applied.
    """
    values = values if isinstance(values, dict) else {}
    color_values = values.get("colors") if isinstance(values.get("colors"), dict) else values
    font_values = values.get("fonts") if isinstance(values.get("fonts"), dict) else values
    proposed = {
        "logo": str(values.get("logo") or values.get("logo_url") or "").strip(),
        **{role: str(color_values.get(role) or "").strip()
           for role in COLOR_ROLES},
        **{f"font_{role}": str(font_values.get(role) or
                                font_values.get(f"font_{role}") or "").strip()
           for role in FONT_ROLES},
    }
    if not str(client or "").strip():
        return {"ok": False, "error": "No client named."}
    from hub.client_brand import brand_kit
    kit = brand_kit(client, domain)
    offered_logos = {str(t.get("url") or "") for t in kit.get("logo_tiles") or []}
    offered_fonts = {str(f.get("name") or "") for f in kit.get("fonts") or []}
    if proposed["logo"] and proposed["logo"] not in offered_logos and not allow_stale:
        return {"ok": False, "error": "That logo is not one this Hub currently "
                "has on file for this client."}
    for role in COLOR_ROLES:
        if proposed[role] and not _hex_of(proposed[role]):
            return {"ok": False, "error": f"The {role} color is not a valid hex value."}
    for role in FONT_ROLES:
        value = proposed[f"font_{role}"]
        if value and value not in offered_fonts and not allow_stale:
            return {"ok": False, "error": f"The {role} font is not currently on file."}

    row = jsonstore.read_json(_path(client), default={}) or {}
    if not isinstance(row, dict):
        row = {}
    selected = next((t for t in kit.get("logo_tiles") or []
                     if t.get("url") == proposed["logo"]), {})
    row["logo_url"] = proposed["logo"]
    row["logo_theme"] = str(selected.get("theme") or
                            (values.get("logo_theme") if allow_stale else "") or "")
    row["colors"] = {role: _hex_of(proposed[role]) for role in COLOR_ROLES
                     if proposed[role]}
    row["fonts"] = {role: proposed[f"font_{role}"] for role in FONT_ROLES
                    if proposed[f"font_{role}"]}
    row["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row["updated_by"] = actor or ""
    jsonstore.write_json(_path(client), row)
    try:
        from hub import audit
        audit.log("hub", "brand_template_set", actor=actor or None, client=client,
                  detail="visual identity approved")
    except Exception:                                   # noqa: BLE001
        pass
    return {"ok": True, "template": get(client)}


__all__ = ["COLOR_ROLES", "FONT_ROLES", "get", "save", "save_many"]
