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
    "seo_images":           ("Images optimized", "SEO Image Pipeline"),
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
    # The Display Ad Builder logs under `display_ads`, not under its directory
    # name — `modules/ad_builder` is the TypeScript renderer and its Hub-side
    # half lives in `hub/ad_builder_link.py`, which is why `audit.LOG_NAMES`
    # declares the mapping. This table was keyed on neither, so every build
    # started and every pack filed against a client was written to the
    # activity log, kept, and then dropped on the way to the record it was
    # written for: `work_log()` skips a module it cannot name, and a skipped
    # module is indistinguishable from a client nobody has done any work for.
    "display_ads":          ("Display ads", "Display Ad Builder"),
    "fan_radio":            ("Radio spot", "Fan Radio"),
    "commercial_builder":   ("Commercial", "Commercial Builder"),
    "utm_builder":          ("Tracked links", "UTM Builder"),
    "social_planner":       ("Social calendar", "Social Content Planner"),
    "calculators":          ("Calculator published", "Calculators"),
    "google_access":        ("Google access", "Google Access"),
    "suite_panel":          ("Suite account", "Suite Panel"),
    "sites_admin":          ("Website", "Sites"),
    "hooks":                ("Suite opportunity", "Smart 1 Suite"),
    # An ad copy request is work filed against a client, so it is logged
    # under its own name rather than under `hub` — `work_log()` skips a
    # module it cannot name, and a skipped module is indistinguishable from
    # a client nobody has done any work for. That is the `display_ads`
    # failure above, one tool later.
    "ad_copy":              ("Ad copy request", "Ad Copy Request"),
    # A website audit run for a client is work filed against them: somebody
    # spent a credit reading their site and the answer is what the next
    # proposal is written from. Named here as well as declared to the activity
    # log, because `work_log()` skips a module its own table cannot name and a
    # skipped module reads on the record as a client nobody has done any work
    # for -- the `display_ads` failure, two tools later.
    "website_audit":        ("Website audit", "Website Audit"),
    # Five more, found by asking the question of every call site rather than
    # one tool at a time. Each writes `audit.log(..., client=…)` and each was
    # dropped on the way to the record it was written for. The insertion
    # order is the worst of them: it is the document the campaign is sold on,
    # and `hub/io_clients.py` exists precisely because a client whose only
    # trace is an IO was invisible on their own record — so the IO was
    # registering the client and then not appearing as work for them.
    "io_builder":           ("Insertion order", "IO Builder"),
    # Built for a client and often pasted onto the client's own domain.
    "landing_maker":        ("Landing page", "Landing Page Maker"),
    # Creative picked for a client, the same kind of row as seo_images and
    # image_picker, which are both already here.
    "stock_photos":         ("Stock photo used", "Stock Photos"),
    # A logo filed into their gallery, and a brand guide delivered to their
    # Suite. Both are things the client receives.
    "brand":                ("Brand assets", "Brand"),
    # `hub/ghl_blog.py` publishing a post's llms.txt into the client's Suite.
    "suite":                ("Published to Suite", "Smart 1 Suite"),
}

# The other side of the same question, written down rather than left as an
# absence. These modules log with a `client=` and are deliberately **not**
# work produced for a client, so `check_work_kinds()` can tell "decided to
# leave out" apart from "nobody has noticed yet" — which is the only reason
# the check can be green rather than a list somebody re-triages every time it
# runs. The rule `tools/spellcheck.py`'s ALLOW works to: per name, with the
# reason beside it.
NOT_WORK = {
    # Every landing module files the prospect's own business name from the
    # form. A prospect is not a client, and putting a lead on a client record
    # would be the Hub inventing a relationship — the distinction
    # `hub/leads.py` and `hub/prospect.py` are built around.
    "leads":        "a prospect, not a client we have done work for",
    "boat":         "a lead off a landing page — the prospect's own name",
    "hvac":         "a lead off a landing page — the prospect's own name",
    "legal":        "a lead off a landing page — the prospect's own name",
    "recruit":      "a lead off a landing page — the prospect's own name",
    "restaurant":   "a lead off a landing page — the prospect's own name",
    "rv":           "a lead off a landing page — the prospect's own name",
    "ski":          "a lead off a landing page — the prospect's own name",
    "stadium":      "a lead off a landing page — the prospect's own name",
    "tourism":      "a lead off a landing page — the prospect's own name",
    # A join we recorded, not something the client received. Attaching a GA4
    # property says who owns it; it does not say we made anything.
    "google_index": "a resource joined to a client, not work delivered",
    # Hub housekeeping: a domain attached, an SEO task ticked. Same reason.
    "hub":          "housekeeping — a join or a status, not a deliverable",
    "qa":           "a report row acted on, not work produced",
}


def check_work_kinds(root=None) -> list[dict]:
    """Module names that log client work and that this table cannot name.

    `work_log()` skips a module it cannot name, and a skipped module is
    indistinguishable on the record from a client nobody has done any work
    for. That has now happened five times — display_ads, ad_copy,
    website_audit and the five added above — each found by somebody opening
    one client's record and noticing, which is not a way of finding the sixth.

    Reads `audit.log(...)` call sites through the AST rather than by matching
    text: three modules explain this failure in prose that quotes the call,
    and a check that reads the explanation of a fix as the defect is one
    somebody switches off. Only an attribute call on a name ending in
    `audit` counts — a bare `log()` is a module's own wrapper whose first
    argument is the event rather than the module, and counting those made
    four modules look misfiled.
    """
    import ast
    import os

    base = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out, seen = [], {}
    for folder in ("hub", "modules"):
        top = os.path.join(base, folder)
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [d for d in dirnames
                           if d not in ("_attic", "node_modules", ".git")]
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8", errors="ignore") as fh:
                        tree = ast.parse(fh.read())
                except (OSError, SyntaxError):
                    continue
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    f = node.func
                    if not (isinstance(f, ast.Attribute) and f.attr == "log"
                            and isinstance(f.value, ast.Name)
                            and f.value.id.endswith("audit")):
                        continue
                    if not any(k.arg == "client" for k in node.keywords):
                        continue
                    if not node.args:
                        continue
                    first = node.args[0]
                    if not (isinstance(first, ast.Constant)
                            and isinstance(first.value, str)):
                        continue
                    mod = first.value
                    if mod in WORK_KINDS or mod in NOT_WORK:
                        continue
                    seen.setdefault(mod, set()).add(
                        os.path.relpath(path, base))
    for mod in sorted(seen):
        out.append({
            "file": sorted(seen[mod])[0],
            "module": mod,
            "detail": (f"logs work against a client under {mod!r}, which "
                       "hub/client_brand.WORK_KINDS cannot name — work_log() "
                       "skips it, so the client record reads as a client "
                       "nobody has done any work for"),
            "fix": (f"Add {mod!r} to WORK_KINDS with how to describe it, or "
                    "to NOT_WORK with the reason it is not a deliverable."),
        })
    return out


def stale_work_exemptions(root=None) -> list[str]:
    """NOT_WORK entries naming a module that no longer logs with a client.

    An exemption that outlives what it exempted goes on covering whatever is
    written under that name next — the failure `check_stale_json_exemptions()`
    names, on a different shelf.
    """
    import ast
    import os

    base = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    live = set()
    for folder in ("hub", "modules"):
        for dirpath, dirnames, filenames in os.walk(os.path.join(base, folder)):
            dirnames[:] = [d for d in dirnames
                           if d not in ("_attic", "node_modules", ".git")]
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                try:
                    with open(os.path.join(dirpath, name), encoding="utf-8",
                              errors="ignore") as fh:
                        tree = ast.parse(fh.read())
                except (OSError, SyntaxError):
                    continue
                for node in ast.walk(tree):
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "log"
                            and isinstance(node.func.value, ast.Name)
                            and node.func.value.id.endswith("audit")
                            and node.args
                            and isinstance(node.args[0], ast.Constant)
                            and isinstance(node.args[0].value, str)
                            and any(k.arg == "client" for k in node.keywords)):
                        live.add(node.args[0].value)
    return sorted(set(NOT_WORK) - live)


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
    """What was seen on the client's own website, as a second source."""
    try:
        from hub import scan_facts
        return scan_facts.brand_observed(domain)
    except Exception:                                   # noqa: BLE001
        return {"found": False}


def _merge(logos: list[dict], colors: list[dict], observed: dict) -> tuple[list, list]:
    """One set of logos and one palette for the card, from both sources.

    Client 360 drew these as two blocks — a "Brand" card fed by the brand
    lookup, and a second block underneath fed by what had been read off the
    client's own website — so the same company's colours appeared twice, in
    two sizes, under two headings, and the rep had to work out which of them
    was the brand. For most local businesses the lookup publishes nothing at
    all, which made the *upper* block the empty one: the card led with "No
    brand data on file yet" above the logo it plainly had.

    So the card is one card. What does **not** merge is the claim: a logo the
    client gave us and a logo lifted off their home page are different things,
    and only the first belongs on a document a client reads. Each tile
    carries its own origin and `logos` is left exactly as it was, which is
    what `brand_guide_payload()` pushes to Suite and what `hub/io_prefill.py`,
    `hub/landing_maker.py` and `hub/client_context.py` read. Merging is a
    thing this card does for a reader; it is not a thing done to the data.

    A colour is deduped on the hex, so a palette both sources agree on draws
    once — and it keeps the stored role label when it has one, because
    "Primary accent" read off a stylesheet is a guess at what the brand calls
    it and the brand's own answer is not.
    """
    tiles = [{"url": l["url"], "origin": "file", "label": "On file",
              "format": l.get("format") or "", "theme": l.get("theme") or ""}
             for l in logos if l.get("url")]
    seen = {c["hex"].upper() for c in colors if c.get("hex")}
    palette = [{"hex": c["hex"].upper(), "type": c.get("type") or "",
                "origin": "file"} for c in colors if c.get("hex")]

    if observed and observed.get("found"):
        if observed.get("logo_url") and observed["logo_url"] not in [t["url"] for t in tiles]:
            tiles.append({"url": observed["logo_url"], "origin": "site",
                          "label": "Seen on their website",
                          "format": "", "theme": ""})
        for c in (observed.get("colors") or []):
            hx = str(c.get("hex") or "").upper()
            if hx and hx not in seen:
                seen.add(hx)
                palette.append({"hex": hx, "type": c.get("type") or "",
                                "origin": "site"})
    return tiles[:8], palette[:14]


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
        tiles, palette = _merge([], [], observed)
        return {"found": False, "client": client, "domain": domain,
                "logos": [], "colors": [], "fonts": [],
                "can_lookup": bool(dom and ready),
                "lookup_domain": dom,
                "observed": observed,
                # Nothing was looked up, and their own website may still have
                # published a logo and a palette. `found` stays False — it is
                # the answer to "is there brand data on file" and the lookup
                # button reads it — while `has_brand` is the answer to "is
                # there anything to draw", which is what the card asks.
                "logo_tiles": tiles, "palette": palette,
                "has_brand": bool(tiles or palette),
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

    observed = _observed(domain or payload.get("domain") or "")
    tiles, palette = _merge(logos[:8], colors[:10], observed)

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
        # The website's own sighting, beside the stored kit rather than in
        # it — useful precisely when the stored kit has colours and no logo.
        "observed": observed,
        # The one set the card draws, from both sources, each tile and each
        # swatch saying which it came from. See `_merge`.
        "logo_tiles": tiles, "palette": palette,
        "has_brand": bool(tiles or palette or fonts),
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


def mark_pushed(client: str) -> str:
    """Record that the brand guide reached Suite, and return the stamp.

    Called only where the delivery actually succeeded. A push that was
    refused, that could not be reached, or that was merely offered for
    somebody to paste by hand has not reached Suite, and a card that says it
    has is the confident wrong answer this file spends its length avoiding.

    Returns the timestamp it wrote so the route can hand back the same string
    the next page load will read, rather than the browser inventing a second
    idea of when this happened. Empty when nothing could be written -- the
    push still landed, and refusing to report it because our own note failed
    would be worse.
    """
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        from hub import seo
        store = seo.load_store(client) or {}
        store["suite_brand_guide"] = stamp
        seo.save_store(client, store)
        return stamp
    except Exception:  # noqa: BLE001
        return ""


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
