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

**Nothing is invented.** A pick is checked against a fresh `brand_kit()` call
at save time: the exact logo URL or the exact hex must be one the merge is
*currently* offering for that client. There is no way to set a template to a
URL or a colour this Hub never actually saw for that client, and a stale pick
— a Brandfetch answer that has changed since it was confirmed — is refused
rather than silently accepted, the `client_urls.NOT_A_WEBSITE` rule wearing a
swatch.

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
not replace the merge. And it does not (yet) let `brand_guide_payload()` push
a template built from an *observed*-only tile — that function still gates on
`kit["found"]`, which is Brandfetch data specifically, so a pick made for a
client with no Brandfetch record shows on the card and on a Magic Resize
project but does not yet reach the Suite push. Widening that gate is a real
next step and a separate change: it moves what "there is brand data to push"
means, which is the Suite button's own error message as well as its gate.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from hub import jsonstore

COLOR_ROLES = ("primary", "secondary", "accent")


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
    out = {
        "client": client,
        "logo_url": str(row.get("logo_url") or ""),
        "logo_theme": str(row.get("logo_theme") or ""),
        "colors": colors,
        "updated_at": str(row.get("updated_at") or ""),
        "updated_by": str(row.get("updated_by") or ""),
    }
    out["picked"] = bool(out["logo_url"] or any(colors.values()))
    return out


def _hex_of(value: str) -> str:
    v = str(value or "").strip().upper()
    if v and not v.startswith("#"):
        v = "#" + v
    return v if re.fullmatch(r"#[0-9A-F]{3,8}", v) else ""


def save(client: str, domain: str, field: str, value: str, actor: str = "") -> dict:
    """Confirm — or clear, with `value=""` — one pick against what is on offer now.

    Refuses a value `brand_kit()` is not currently showing for this client,
    logo URLs and hex codes alike, so a rep can only ever confirm something
    this Hub has actually fetched or observed, never type a stray one in.
    Clearing is always allowed: taking a pick back can never be "not offered".
    """
    client = str(client or "").strip()
    if not client:
        return {"ok": False, "error": "No client named."}
    if field != "logo" and field not in COLOR_ROLES:
        return {"ok": False, "error": f"{field!r} is not something this can confirm."}

    value = str(value or "").strip()
    logo_theme = ""
    if value:
        from hub.client_brand import brand_kit
        kit = brand_kit(client, domain)
        if field == "logo":
            tile = next((t for t in (kit.get("logo_tiles") or [])
                        if t.get("url") == value), None)
            if not tile:
                return {"ok": False, "error": "That logo is not one this Hub "
                        "currently has on file for this client."}
            logo_theme = tile.get("theme", "")
        else:
            hx = _hex_of(value)
            if not hx or hx not in {c.get("hex") for c in kit.get("palette") or []}:
                return {"ok": False, "error": "That color is not one this Hub "
                        "currently has on file for this client."}
            value = hx

    row = jsonstore.read_json(_path(client), default={}) or {}
    if not isinstance(row, dict):
        row = {}
    if field == "logo":
        row["logo_url"] = value
        row["logo_theme"] = logo_theme
    else:
        colors = row.get("colors") if isinstance(row.get("colors"), dict) else {}
        if value:
            colors[field] = value
        else:
            colors.pop(field, None)
        row["colors"] = colors
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
