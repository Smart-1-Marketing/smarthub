"""One identity stamped onto every client asset uploaded to Cloudinary.

Twelve tools make an image and file it under a client, each choosing its own
context keys and its own tags — one client's gallery reads `{"client": "Acme
Plumbing"}` from one producer and `{"clientName": "acme-plumbing"}` from
another, and nothing joins them. `for_client()` is the one shape, mirroring
the join `hub/client_key.py` already settled for every other cross-tool
report in this Hub: the client's own name, a derived slug for anything that
needs one to match on, and — where it can be resolved — the industry.

Never a second identity. This does not replace `hub/client_context.py` or
`hub/client_key.py`; it is what a Cloudinary upload call passes as its own
``context=``/``tags=`` arguments, built from the same sources everything else
in the Hub reads a client from.

Two rules:

* **Never on Smart 1's own assets.** ``client="Smart 1 Marketing"`` is the
  one allowed no-client brand — this Hub's own logo, its own templates, its
  own stock library — and it gets ``tags=["smart1", "house"]`` rather than a
  fabricated client slug, the same distinction `hub/industry.py`'s absence
  and `client_brief.build_from_fields()` both draw between a real business
  and none.
* **Never raises.** An upload must not fail because the identity stamp could
  not be resolved — a client with no industry on file still gets a
  ``context``/``tags`` pair, with the industry key simply absent.
"""
from __future__ import annotations

import re

HOUSE_CLIENT = "Smart 1 Marketing"


def _slug(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    return text or "unknown"


def for_client(client: str, domain: str = "") -> dict:
    """``{"context": {...}, "tags": [...]}`` for one Cloudinary upload.

    Pass straight through to ``hub.storage.put(..., context=meta["context"],
    tags=meta["tags"])`` (merging with anything the caller already builds —
    never replacing it, since several producers already carry their own
    context keys that other code reads back).
    """
    client = (client or "").strip()

    if not client or client.lower() == HOUSE_CLIENT.lower():
        return {
            "context": {"client": HOUSE_CLIENT, "source": "house"},
            "tags": ["smart1", "house"],
        }

    client_slug = _slug(client)
    industry_key = ""
    industry_label = ""
    try:
        from hub import industry as _industry
        resolved = _industry.resolve_industry(client=client, domain=domain)
        industry_key = resolved.get("key", "")
        industry_label = resolved.get("label", "")
    except Exception:                                     # noqa: BLE001
        # SEAM (WO-2): hub/industry.py does not exist on this branch yet.
        # An asset uploaded before it lands simply carries no industry tag —
        # never a guessed one, the same rule `hub/client_brief.py` names at
        # its own industry seam.
        pass

    palette_primary = ""
    try:
        from hub.client_brand import brand_kit
        kit = brand_kit(client, domain) or {}
        colors = kit.get("colors") or []
        if colors:
            palette_primary = colors[0].get("hex", "")
    except Exception:                                     # noqa: BLE001
        pass

    context = {
        "client": client,
        "client_slug": client_slug,
        "source": "smart1_hub",
    }
    if industry_key:
        context["industry_key"] = industry_key
    if industry_label:
        context["industry_label"] = industry_label
    if palette_primary:
        context["palette_primary"] = palette_primary

    tags = ["smart1", client_slug]
    if industry_key:
        tags.append(industry_key)

    return {"context": context, "tags": tags}
