"""Client brand kit, and a log of everything the Hub has made for a client.

Two gaps this closes on the Client 360 record.

**Brand.** Brandfetch data has been stored per client since v1.6 — logos,
colours, fonts — and nothing ever showed it. So the person building a graphic
opens Image Creator, types the company name, and waits for a lookup the Hub
already had cached. Worse, if Brandfetch is over its monthly allowance that
lookup fails and they guess at the colours. The data was there the whole time.

**Work log.** Twenty tools make things for clients and each files its output in
its own place: images in one gallery, quotes in another, scans in a third.
Nobody could answer "what have we actually done for this client?" without
opening six screens. This assembles one reverse-chronological list from the
activity log plus each tool's own records.

Both are read-only views over data that already exists. Nothing new is stored,
so there is nothing to keep in sync and nothing to migrate.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from hub import audit, seo

# Modules whose activity counts as "work produced for a client", and how to
# describe it. Anything not listed is Hub housekeeping and stays out, so the
# log reads as a record of deliverables rather than a debug feed.
WORK_KINDS = {
    "seo_images":           ("Images optimised", "SEO Image Pipeline"),
    "image_creator":        ("Graphic created", "Image Creator"),
    "image_picker":         ("Images added to library", "Image Picker"),
    "page_image_optimizer": ("Page images fixed", "Page Image Optimizer"),
    "bg_remover":           ("Cut-out produced", "Background Remover"),
    "scans":                ("Site audit", "Site Scans"),
    "seo":                  ("Schema / FAQ", "SEO"),
    "proposals":            ("Proposal", "Proposals"),
    "proposal_builder":     ("Proposal generated", "Proposal Builder"),
    "sales_builder":        ("Quote", "Sales Builder"),
    "ads_builder":          ("Ads campaign", "Ads Builder"),
    "fan_radio":            ("Radio spot", "Fan Radio"),
    "commercial_builder":   ("Commercial", "Commercial Builder"),
    "utm_builder":          ("Tracked links", "UTM Builder"),
    "social_planner":       ("Social calendar", "Social Content Planner"),
    "calculators":          ("Calculator published", "Calculators"),
    "google_access":        ("Google access", "Google Access"),
    "suite_panel":          ("Suite account", "Suite Panel"),
    "sites_admin":          ("Website", "Sites"),
    "hooks":                ("Suite opportunity", "Smart 1 Suite"),
}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


# ---------------------------------------------------------------------------
# Brand kit
# ---------------------------------------------------------------------------

def _hex(value: str) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    if not v.startswith("#"):
        v = "#" + v
    return v if re.fullmatch(r"#[0-9a-fA-F]{3,8}", v) else ""


def _observed(domain: str) -> dict:
    """What the last site scan saw, as a second source. Never merged in."""
    try:
        from hub import scan_facts
        return scan_facts.brand_observed(domain)
    except Exception:                                   # noqa: BLE001
        return {"found": False}


def brand_kit(client: str, domain: str = "") -> dict:
    """Logos, colours and fonts for a client, from stored brand data.

    Returns a `found: False` shell rather than raising when there's nothing,
    so the card can say "no brand data yet" and offer to fetch it instead of
    disappearing — an absent card looks like a broken page.

    **Why this card was empty for almost every client.** Three things, and
    each of them looked like nothing being wrong:

    * Client 360 called this with a name and no domain, so the domain-keyed
      half of the store — which is where every lookup anybody had actually
      run ended up — was never consulted. The caller passes the client's
      website now, and `hub/seo.brand_for` tries both.
    * Nothing but the Suite Panel ever *saved* a lookup, and only when it was
      handed a `?client=`. Image Creator paid for a live call on every search
      and threw the answer away. `hub/brand_lookup.py` is the one path now,
      and it keeps what it paid for.
    * There was no way to ask for a lookup from here at all. There is a
      button, because the call is billed and a page load must not spend one.

    And where a lookup genuinely has nothing — which is the ordinary case for
    a local business that has never registered a brand anywhere — the last
    site scan usually saw the logo on the client's own home page. That is
    carried separately as `observed`, labelled as observed, and never folded
    into `logos`: a logo scraped off a page is a candidate, and the whole
    point of this card is that a wrong logo on a client-facing document is
    worse than none.
    """
    payload = None
    try:
        payload = seo.brand_for(client, domain)
    except Exception:                                   # noqa: BLE001
        payload = None
    if not payload:
        observed = _observed(domain)
        # "Nobody has looked yet", "we cannot look" and "we looked and there
        # is nothing" are three different answers, and only the first is
        # something to press a button about.
        try:
            from hub import brand_lookup
            ready = brand_lookup.configured()
            dom = brand_lookup.domain_of(domain)
        except Exception:                               # noqa: BLE001
            ready, dom = False, ""
        if not dom:
            note = ("No website on this client record, so there is nothing to "
                    "look a brand up by. Attach one below and the lookup opens.")
        elif not ready:
            note = ("No brand data on file, and brand lookup is not switched "
                    "on for this deployment — see the environment reference "
                    "on Settings.")
        else:
            note = f"No brand data on file yet. Look it up from {dom}."
        return {"found": False, "client": client, "domain": domain,
                "logos": [], "colors": [], "fonts": [],
                "can_lookup": bool(dom and ready),
                "lookup_domain": dom,
                "observed": observed,
                "note": note}

    logos = []
    for logo in (payload.get("logos") or []):
        for fmt in (logo.get("formats") or []):
            url = fmt.get("src") or ""
            if not url:
                continue
            logos.append({
                "url": url,
                "kind": logo.get("type") or "logo",      # logo | symbol | icon
                "theme": logo.get("theme") or "",        # light | dark
                "format": (fmt.get("format") or "").lower(),
                "width": fmt.get("width"), "height": fmt.get("height"),
            })
    # Prefer SVG, then the largest raster — that's the order a designer wants.
    logos.sort(key=lambda l: (0 if l["format"] == "svg" else 1,
                              -(l.get("width") or 0)))

    colors = []
    seen = set()
    for c in (payload.get("colors") or []):
        hx = _hex(c.get("hex") or c)
        if not hx or hx.lower() in seen:
            continue
        seen.add(hx.lower())
        colors.append({"hex": hx.upper(),
                       "type": (c.get("type") if isinstance(c, dict) else "") or "",
                       "brightness": (c.get("brightness") if isinstance(c, dict) else None)})

    fonts = []
    for f in (payload.get("fonts") or []):
        name = f.get("name") if isinstance(f, dict) else str(f)
        if name and name not in [x["name"] for x in fonts]:
            fonts.append({"name": name,
                          "usage": (f.get("type") if isinstance(f, dict) else "") or "",
                          "google": f"https://fonts.google.com/?query={name}"})

    return {
        "found": True, "client": client,
        "domain": payload.get("domain") or domain,
        "name": payload.get("name") or client,
        "logos": logos[:8], "colors": colors[:10], "fonts": fonts[:6],
        # Was cut at 280 characters, which lands mid-sentence on most
        # Brandfetch descriptions — the card looked like it had rendered a
        # broken string rather than a shortened one. 1200 is generous enough
        # that a real description arrives whole, and still bounded.
        "description": (payload.get("description") or "")[:1200],
        # When the guide was last pushed, so the card can show the state
        # instead of a button that would overwrite it.
        "suite_brand_guide": _pushed_at(client),
        # A refresh is still offered on a card that has data: brand details
        # go stale, and the alternative is somebody with a new logo having
        # nowhere to put it.
        "can_lookup": bool(_lookup_domain(domain, payload)),
        "lookup_domain": _lookup_domain(domain, payload),
        # The scan's own sighting, beside the stored kit rather than in it —
        # useful precisely when the stored kit has colours and no logo.
        "observed": _observed(domain or payload.get("domain") or ""),
    }


def _lookup_domain(domain: str, payload: dict | None = None) -> str:
    """The domain a refresh would ask about, or '' when none is possible."""
    try:
        from hub import brand_lookup
        if not brand_lookup.configured():
            return ""
        return (brand_lookup.domain_of(domain)
                or brand_lookup.domain_of((payload or {}).get("domain") or ""))
    except Exception:                                   # noqa: BLE001
        return ""


def _pushed_at(client: str) -> str:
    try:
        from hub import seo
        return str((seo.load_store(client) or {}).get("suite_brand_guide") or "")
    except Exception:  # noqa: BLE001
        return ""


def mark_pushed(client: str) -> None:
    """Record that the brand guide reached Suite."""
    from datetime import datetime, timezone
    try:
        from hub import seo
        store = seo.load_store(client) or {}
        store["suite_brand_guide"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        seo.save_store(client, store)
    except Exception:  # noqa: BLE001
        pass


def brand_guide_payload(client: str, domain: str = "") -> dict:
    """Flat key/value shape suitable for pushing into GoHighLevel custom fields.

    Deliberately flat and string-valued: GHL custom fields don't take nested
    objects, and a JSON blob in a text field is unusable inside their workflow
    builder.
    """
    kit = brand_kit(client, domain)
    if not kit["found"]:
        return {"found": False, "client": client}
    primary = kit["colors"][0]["hex"] if kit["colors"] else ""
    return {
        "found": True,
        "brand_client": kit["client"],
        "brand_domain": kit["domain"],
        "brand_primary_color": primary,
        "brand_colors": ", ".join(c["hex"] for c in kit["colors"]),
        "brand_fonts": ", ".join(f["name"] for f in kit["fonts"]),
        "brand_logo_url": kit["logos"][0]["url"] if kit["logos"] else "",
        "brand_logo_svg": next((l["url"] for l in kit["logos"]
                                if l["format"] == "svg"), ""),
        "brand_logo_dark": next((l["url"] for l in kit["logos"]
                                 if l["theme"] == "dark"), ""),
    }


# ---------------------------------------------------------------------------
# Work log
# ---------------------------------------------------------------------------

def work_log(client: str, limit: int = 60, also: list[str] | None = None) -> dict:
    """Everything the Hub has produced for one client, newest first.

    Reads the activity log rather than each tool's own store: a tool that
    logs its work appears here automatically, with no per-tool integration.
    The flip side is that a tool which doesn't log is invisible, which is
    exactly what the integrity audit's "modules that never log" check is for.

    `also` is the other members of this client's group (hub/client_groups.py).
    Their work is merged in and every merged row carries `member`, because the
    group is a billing relationship and not a rename: work done for Fast
    Fingerprints has to keep reading as Fast Fingerprints' work on National
    Background Check's record.
    """
    want = _norm(client)
    extra = {}
    for other in (also or []):
        n = _norm(other)
        if n and n != want:
            extra[n] = str(other)
    rows = []
    for e in audit.tail(limit=6000):
        mod = e.get("module") or ""
        if mod not in WORK_KINDS:
            continue
        # A client can be named under any of several keys depending on the tool.
        named = ""
        for key in ("client", "client_name", "company", "business_name", "tool_client"):
            if e.get(key):
                named = str(e[key])
                break
        norm_named = _norm(named) if named else ""
        if not norm_named:
            continue
        if norm_named != want and norm_named not in extra:
            continue
        label, source = WORK_KINDS[mod]
        row = {
            "when": e.get("time", ""),
            "kind": label, "source": source, "module": mod,
            "action": e.get("type", ""),
            "actor": e.get("actor") or "",
            "detail": str(e.get("detail") or e.get("title") or "")[:160],
        }
        if norm_named != want:
            row["member"] = extra[norm_named]
        rows.append(row)
        if len(rows) >= limit:
            break

    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r["source"]] = by_source.get(r["source"], 0) + 1

    return {
        "client": client, "count": len(rows), "items": rows,
        "group": sorted(extra.values()),
        "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
        "last_activity": rows[0]["when"] if rows else None,
        "note": "Assembled from the activity log. A tool that doesn't write "
                "there won't appear — /api/integrity lists which ones those are.",
    }


def log_work(module: str, client: str, action: str, actor: str = "", **extra):
    """Helper for tools to record a deliverable against a client.

    One call is all a tool needs to appear on the client's record:

        from hub.client_brand import log_work
        log_work("fan_radio", client, "spot_produced", actor, length="30")
    """
    try:
        audit.log(module, action, actor=actor or None, client=client, **extra)
    except Exception:                                   # noqa: BLE001
        pass
