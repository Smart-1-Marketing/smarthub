"""One client record for every form in the Hub, and a watch on where data lives.

## Why this exists

Twenty-two tools each ask for the same things — company name, website, phone,
address, city, industry — and each stores its answer in its own place. So the
same client gets typed in five times, three of those spellings disagree, and
the billing audit that matches on name reports false alarms forever.

`context()` assembles one merged view from everything the Hub already knows so
a form can prefill instead of asking. Nothing new is stored: this is a read
across existing sources with a stated precedence, which means there is no sync
problem and no migration.

**Precedence, most trusted first.** Knack is the system of record for who a
client is, so it wins on identity. A site scan is the most recent *observed*
truth about their website, so it wins on anything scraped. Brandfetch fills
visual identity. The website record fills platform detail. Where two sources
disagree the winner is recorded in `sources` so you can see why a field says
what it says.

## The duplication problem this surfaces

There is no single client table. Four different keys identify a client:

    hub seo store        client name (a string)
    Site Scans           domain_key (a normalised domain)
    Image Picker         client_id (its own table)
    Commercial Builder   client_id (a second, separate table)

Nothing joins them. `structure_report()` makes that visible rather than leaving
it to be discovered when two reports disagree.
"""
from __future__ import annotations

import ast
import os
import re
from datetime import datetime, timezone

# Fields a form might want prefilled, and which source is allowed to win.
FIELD_ORDER = ("knack", "scan", "website", "brand", "store")


def _clean(v) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def _domain(v: str) -> str:
    d = re.sub(r"^https?://", "", _clean(v).lower()).removeprefix("www.")
    return d.split("/")[0]


def context(client: str, domain: str = "") -> dict:
    """Everything the Hub knows about a client, merged and attributed.

    Never raises: a form that can't prefill should still open. Every source is
    tried independently so one failing provider doesn't lose the others.
    """
    out: dict = {}
    sources: dict[str, str] = {}
    tried: dict[str, str] = {}

    def take(field: str, value, source: str):
        value = _clean(value)
        if not value:
            return
        if field not in out:                 # first writer wins, by precedence
            out[field] = value
            sources[field] = source

    # ---- 1. Knack: the system of record for identity ----
    try:
        from hub import clients_registry
        rec = clients_registry.find_client(client) or {}
        tried["knack"] = "ok" if rec else "no match"
        take("client", rec.get("name"), "knack")
        take("website", rec.get("url") or rec.get("domain"), "knack")
        take("city", rec.get("city"), "knack")
        take("state", rec.get("state"), "knack")
        take("industry", rec.get("industry") or rec.get("vertical"), "knack")
        take("phone", rec.get("phone"), "knack")
        if rec.get("products"):
            out.setdefault("products", rec["products"])
            sources.setdefault("products", "knack")
    except Exception as exc:                              # noqa: BLE001
        tried["knack"] = f"error: {type(exc).__name__}"

    # ---- 2. Latest site scan: the most recent observed truth ----
    try:
        from modules.scans.app import Scan, SessionLocal, domain_key
        key = domain_key(domain or out.get("website") or client)
        db = SessionLocal()
        try:
            s = (db.query(Scan)
                 .filter(Scan.domain_key == key, Scan.status == "complete")
                 .order_by(Scan.created_at.desc()).first())
        finally:
            db.close()
        if s:
            tried["scan"] = "ok"
            take("client", s.business_name, "scan")
            take("website", s.input_url, "scan")
            report = {}
            try:
                import json
                report = json.loads(s.report_json or "{}")
            except Exception:                             # noqa: BLE001
                report = {}
            # Insites scrapes the things every form asks for. Using it means a
            # scanned client never has to be typed in again.
            for field, keys in (
                ("phone", ("detected_phone", "phone")),
                ("address", ("detected_address", "address")),
                ("city", ("detected_city", "city")),
                ("state", ("detected_state", "state")),
                ("zip", ("detected_postcode", "postcode", "zip")),
                ("business_name", ("detected_name", "business_name")),
                ("email", ("detected_email", "email")),
            ):
                for k in keys:
                    if report.get(k):
                        take(field, report[k], "scan")
                        break
            out.setdefault("last_scan", str(s.created_at or ""))
            out.setdefault("scan_id", s.public_id)
        else:
            tried["scan"] = "no completed scan"
    except Exception as exc:                              # noqa: BLE001
        tried["scan"] = f"error: {type(exc).__name__}"

    # ---- 2a. The scan, read by the module that knows where things are ----
    #
    # The block above asked the report for `detected_phone`, `detected_address`
    # and five more like them, at the top level. Insites publishes none of them
    # there -- they are `meta.detected_phone`, `meta.detected_address`,
    # `local_presence.business_phone` and so on, which is why
    # `hub/scan_facts.py` exists and carries the paths as data. So every one of
    # those seven `take()` calls has always resolved to None: a client with a
    # complete site audit prefilled a form with a name and a URL and left the
    # phone number, the address and the industry blank, on a record that had
    # been holding all three since the scan ran. Nothing errored, and a blank
    # field reads exactly like a client we know nothing about.
    #
    # Read through scan_facts rather than by fixing the seven keys here: those
    # paths move when Insites moves them, and two descriptions of where a phone
    # number lives is one that goes stale. It also brings the ordering with it
    # -- the business describing itself on its own site beats the Google
    # listing, which is the address most often out of date.
    try:
        from hub import scan_facts
        dom = domain or out.get("website") or client
        seen = scan_facts.contact_observed(dom)
        if seen.get("error"):
            tried["scan_facts"] = f"error: {seen['error']}"
        elif seen.get("found"):
            tried["scan_facts"] = "ok"
            fields = seen.get("fields") or {}
            take("business_name", fields.get("name"), "scan")
            take("phone", fields.get("phone"), "scan")
            take("address", fields.get("address"), "scan")
            take("email", fields.get("email"), "scan")
            take("industry", fields.get("category"), "scan")
            out.setdefault("observed_at", seen.get("observed_at") or "")
        else:
            tried["scan_facts"] = "nothing read from this website"
    except Exception as exc:                              # noqa: BLE001
        tried["scan_facts"] = f"error: {type(exc).__name__}"

    # ---- 2b. Knack website registry (object_153) ----
    # Carries GA/GTM ids, platform, go-live and the H&M fee. Placed after the
    # scan because a scan observes the site as it is now, but before brand and
    # profile because this is a maintained record rather than a cache.
    try:
        from hub.knack_websites import enrich as _wreg
        reg = _wreg(client, domain or out.get("website", ""))
        tried["website_registry"] = "ok" if reg.get("found") else "no match"
        if reg.get("found"):
            take("website", reg.get("website"), "registry")
            take("platform", reg.get("platform"), "registry")
            take("ga_account", reg.get("ga_account"), "registry")
            take("gtm_account", reg.get("gtm_account"), "registry")
            take("login_url", reg.get("login_url"), "registry")
            take("media_partner", reg.get("media_partner"), "registry")
            if reg.get("hm_fee"):
                out.setdefault("hm_fee", reg["hm_fee"])
                sources.setdefault("hm_fee", "registry")
    except Exception as exc:                              # noqa: BLE001
        tried["website_registry"] = f"error: {type(exc).__name__}"

    # ---- 3. Brandfetch: visual identity ----
    try:
        from hub.client_brand import brand_kit
        kit = brand_kit(client, domain or out.get("website", ""))
        tried["brand"] = "ok" if kit.get("found") else "none on file"
        if kit.get("found"):
            if kit["colors"]:
                out.setdefault("brand_primary_color", kit["colors"][0]["hex"])
                sources.setdefault("brand_primary_color", "brand")
            if kit["logos"]:
                out.setdefault("logo_url", kit["logos"][0]["url"])
                sources.setdefault("logo_url", "brand")
            if kit["fonts"]:
                out.setdefault("brand_font", kit["fonts"][0]["name"])
                sources.setdefault("brand_font", "brand")
    except Exception as exc:                              # noqa: BLE001
        tried["brand"] = f"error: {type(exc).__name__}"

    # ---- 4. The client's own saved profile: manual entry wins nothing it
    # didn't already own, but fills anything still blank ----
    try:
        from hub import seo
        store = seo.load_store(client) or {}
        prof = store.get("business_info") or store.get("profile") or {}
        tried["store"] = "ok" if prof else "empty"
        for field in ("phone", "address", "city", "state", "zip", "email",
                      "industry", "hours"):
            take(field, prof.get(field), "store")
    except Exception as exc:                              # noqa: BLE001
        tried["store"] = f"error: {type(exc).__name__}"

    out.setdefault("client", _clean(client))
    if out.get("website"):
        out["domain"] = _domain(out["website"])

    missing = [f for f in ("website", "phone", "city", "industry")
               if not out.get(f)]
    return {
        "client": out.get("client", client),
        "fields": out,
        "sources": sources,
        "providers": tried,
        "missing": missing,
        "complete": not missing,
        "note": "Prefill only. Nothing here is saved — the form still owns "
                "whatever the person submits.",
    }


# ---------------------------------------------------------------------------
# Prefill: what a form may offer, and what it may never overwrite
# ---------------------------------------------------------------------------

# Where a value came from, said the way a rep can act on it. Deliberately not
# the provider that answered: "Brandfetch" and "Insites" name which of our
# tools did the reading, which is not a fact anybody on this end can do
# anything about -- the note `modules/ads_builder/logo.py` makes about naming
# a provider to somebody who cannot rotate its key, and the same reason
# `hub/client_brand.py` labels a tile "on file" or "seen on their website".
SOURCE_LABELS = {
    "knack": "the client record",
    "scan": "their website",
    "registry": "the website record",
    "brand": "their brand kit",
    "store": "this client's profile",
}

# Read, never offered. `products` is a list rather than a value, and the scan
# and logo pointers are provenance for the panel rather than something a text
# field can hold.
NOT_PREFILLABLE = frozenset({
    "products", "scan_id", "last_scan", "observed_at", "hm_fee",
})


def prefill(client: str, domain: str = "", have: dict | None = None) -> dict:
    """What a form can fill in for this client, into its empty fields only.

    Every form in the Hub asks a client for the same six things -- name,
    website, phone, address, city, industry -- and the Hub already holds all
    six for most clients, across the Knack record, the last site scan and the
    brand kit. Asking again is how one client comes to be typed in five times
    with three of the spellings disagreeing, which is the failure the whole of
    `hub/client_key.py` exists downstream of.

    Four rules, each one this codebase has already paid for:

    * **Offered into the empty fields only.** A value somebody typed is the
      better source and is never offered over -- the overlay rule
      `hub/client_urls.py` works to, and the reason
      `scan_facts.contact_suggestions()` gates on what the record already
      holds. Pass what the form already has as ``have``.
    * **Nothing is written.** This is a read. The form still owns whatever the
      person submits, and a value only becomes the client's when they save it.
    * **A source that could not be read is named, not absent.** "This client
      has no phone number on file" and "we could not reach the client list"
      are different answers and only the first means type one in. `providers`
      carries every source that was tried and what it said.
    * **The source is named in words, not in plumbing.** See `SOURCE_LABELS`.

    Never raises: a form that cannot prefill must still open.
    """
    try:
        ctx = context(client, domain)
    except Exception as exc:                              # noqa: BLE001
        return {"client": client, "values": {}, "offers": {}, "providers": {},
                "unreadable": [f"{type(exc).__name__}: {exc}"],
                "note": "Nothing could be read for this client, so nothing is "
                        "offered. The form is unchanged."}

    held = {k: _clean(v) for k, v in (have or {}).items()}
    fields = ctx.get("fields") or {}
    sources = ctx.get("sources") or {}

    offers: dict[str, dict] = {}
    for field, value in fields.items():
        if field in NOT_PREFILLABLE or not _clean(value):
            continue
        if held.get(field):                  # what somebody typed wins
            continue
        src = sources.get(field, "")
        offers[field] = {
            "value": _clean(value),
            "source": src,
            "from": SOURCE_LABELS.get(src, src or "the client record"),
        }

    providers = ctx.get("providers") or {}
    unreadable = [f"{name}: {state.split('error: ', 1)[-1]}"
                  for name, state in providers.items()
                  if str(state).startswith("error")]

    return {
        "client": ctx.get("client", client),
        "domain": fields.get("domain", ""),
        # The flat shape a form actually assigns from.
        "values": {f: o["value"] for f, o in offers.items()},
        # ...and the same values with where each came from, for a panel that
        # draws an offer as an offer rather than as a saved value.
        "offers": offers,
        "products": fields.get("products") or [],
        "providers": providers,
        "unreadable": unreadable,
        "note": ("Offered from what the Hub already holds. Nothing is saved "
                 "until you submit, and anything you have already typed is "
                 "left alone."),
    }


# Which form field each context field may answer. Keyed by the *form's* own
# field key, because the three Knack objects the Hub draws forms from name the
# same thing three ways. Deliberately narrow: `new_website_url` on a web
# ticket is the site being built, not the one they have, and filling it with
# their current site is a wrong answer that reads exactly like a right one.
FORM_ALIASES = {
    "website":       ("website", "client_url", "site_url", "web_url",
                      "client_website"),
    "phone":         ("phone", "client_phone"),
    "email":         ("email", "client_email"),
    "address":       ("address", "client_address"),
    "city":          ("city",),
    "state":         ("state",),
    "zip":           ("zip", "postcode"),
    "industry":      ("industry", "vertical", "category"),
    "media_partner": ("media_partner",),
}

# Controls this may fill. A **connection is never offered**: it is written by
# record id, and putting the display text into one creates nothing and clears
# the link -- the rule `hub/knack_api.py` gives at length, and the reason
# create_ticket used to skip those fields entirely. A form that resolves a
# connection does it itself, exactly or not at all.
FILLABLE_CONTROLS = frozenset({"text", "textarea", "paragraph", "url", "email",
                               "phone", "short_text", "number", ""})


def offer_into(fields: list[dict], values: dict, client: str,
               url: str = "") -> tuple[dict, list[str]]:
    """Fill a drawn form's empty fields from what the Hub already holds.

    One place, read by every form the Hub draws from a Knack object -- the web
    ticket, the campaign support request and the ad copy request all ask a
    client for a website and a phone number the Hub has held since their last
    site scan. Three copies of this mapping is how two of the three come to
    offer a different answer for one client.

    Returns `(values, notes)`: `values` is the caller's own dict with the
    empty fields filled in, and `notes` says what was offered and where each
    came from, in the same list the form already prints. It never overwrites,
    never touches a connection, and never writes anything anywhere.

    Never raises. A prefill that fails must cost the form nothing.
    """
    values = dict(values or {})
    notes: list[str] = []
    try:
        got = prefill(client, url, have=values)
    except Exception:                                     # noqa: BLE001
        return values, notes

    offers = got.get("offers") or {}
    by_key = {f.get("key"): f for f in (fields or [])}
    filled: list[str] = []
    for ctx_field, form_keys in FORM_ALIASES.items():
        offer = offers.get(ctx_field)
        if not offer:
            continue
        for key in form_keys:
            f = by_key.get(key)
            if not f or values.get(key) or not f.get("writable", True):
                continue
            if str(f.get("control") or "") not in FILLABLE_CONTROLS:
                continue
            values[key] = offer["value"]
            filled.append(f"{f.get('label') or key} from {offer['from']}")
            break

    if filled:
        notes.append("Filled in from what we already hold — "
                     + "; ".join(filled) + ". Change anything that is wrong.")
    for line in got.get("unreadable") or []:
        notes.append("Could not be read, so nothing was offered from it: "
                     + line + ". A blank here is not an answer.")
    return values, notes


# ---------------------------------------------------------------------------
# The shape a creative tool asks for
# ---------------------------------------------------------------------------

def gallery_images(client: str, limit: int = 60) -> tuple[list[dict], str]:
    """The client's existing images, newest first, and why there are none.

    Read directly rather than over HTTP -- it is the same process, and a
    background draft should not need a session cookie to talk to it. Name
    matching is the narrow kind `filing.gallery_for_name` does: an exact slug
    or nothing, because putting one client's photography into another
    client's ad is the worst thing any of these tools could do.

    `(images, note)`, never a bare list: "this client's gallery is empty" and
    "the gallery is unreachable" are different answers and only the first
    means go and add some.
    """
    try:
        from sqlalchemy import select

        from modules.image_picker import filing
        from modules.image_picker.models import SavedImage, session
    except Exception:                                     # noqa: BLE001
        return [], "Image gallery unavailable in this environment."
    try:
        db = session()
    except Exception:                                     # noqa: BLE001
        return [], "Image gallery database unreachable."
    try:
        gallery = filing.gallery_for_name(db, client)
        if gallery is None:
            return [], f"No image gallery on file for {client}."
        rows = db.execute(
            select(SavedImage)
            .where(SavedImage.client_id == gallery.id)
            .where(SavedImage.resource_type == "image")
            .order_by(SavedImage.created_at.desc())
            .limit(limit)
        ).scalars().all()
        out = []
        for row in rows:
            url = row.cloudinary_url or row.source_url or ""
            if not url.startswith("https://"):
                continue
            out.append({
                "url": url,
                "public_id": row.cloudinary_public_id or "",
                "alt": (row.alt_text or "")[:300],
                "label": (row.collection_label or row.filename or "")[:120],
            })
        if not out:
            return [], (f"{client}'s gallery is empty — add images in Client "
                        "Image Uploads or make one in Image Creator.")
        return out, ""
    except Exception as exc:                              # noqa: BLE001
        return [], f"Image gallery read failed ({type(exc).__name__})."
    finally:
        try:
            db.close()
        except Exception:                                 # noqa: BLE001
            pass


def tool_context(client: str, url: str = "", *, gallery: bool = True) -> dict:
    """What a creative tool needs to know about a client, assembled once.

    The Social Content Planner and the GPT Ads Builder each carried this
    function and `gallery_images` above, character for character bar a
    docstring -- the second copy this codebase names as a failure twice over,
    once for the image-resize rule and once for the Pexels key, where the fix
    went in and the tool was still broken because it was fixed in one place.

    It also adds what neither copy ever read: **the client's own site scan**.
    A completed Insites audit knows the business name their site uses, their
    phone number, their address and the color scheme their pages actually
    paint, and both tools were writing a month of posts and a set of ads from
    a business name and a Brandfetch record that, for most local businesses,
    does not exist. `brand_observed()` is where the logo comes from for that
    majority, which is exactly the client these tools are used for.

    Every lookup is optional and every one is wrapped: a client with no brand
    record, no products, no scan and no gallery must still be plannable. An
    absent source reports itself as absent rather than as an empty result --
    "no photos on file" and "the gallery is unreachable" are different
    answers, and the strategist needs to know which one they got.
    """
    out = {"client": client, "url": url, "domain": "", "industry": "",
           "description": "", "products": [], "colors": [], "logo": "",
           "logo_from": "", "observed": {}, "gallery": [],
           "gallery_note": "", "brand_note": "", "scan_note": ""}
    try:
        out["domain"] = canonical_domain(url or client) or ""
    except Exception:                                     # noqa: BLE001
        pass

    try:
        from hub import clients_registry
        row = clients_registry.find_client(client)
        if row:
            out["url"] = out["url"] or row.get("url") or ""
            out["domain"] = out["domain"] or row.get("domain") or ""
            out["industry"] = out["industry"] or str(
                row.get("industry") or row.get("vertical") or "")
            products = sorted(row.get("running") or row.get("products") or [])
            out["products"] = [str(p) for p in products][:12]
    except Exception:                                     # noqa: BLE001
        pass

    try:
        from hub import client_brand
        kit = client_brand.brand_kit(client, out["domain"])
        if kit.get("found"):
            out["description"] = kit.get("description") or ""
            out["colors"] = [c["hex"] for c in (kit.get("colors") or [])][:6]
            logos = kit.get("logos") or []
            if logos:
                out["logo"] = logos[0]["url"]
                out["logo_from"] = "their brand kit"
        else:
            out["brand_note"] = kit.get("note") or ""
    except Exception:                                     # noqa: BLE001
        out["brand_note"] = "Brand lookup unavailable."

    # The scan, and only where the brand kit had nothing. A logo lifted off a
    # home page is a *candidate* -- `hub/scan_facts.py` is explicit that it is
    # never merged into the stored logos -- so it fills a gap here and says
    # where it came from rather than standing in as an approved asset.
    try:
        from hub import scan_facts
        if out["domain"]:
            seen = scan_facts.brand_observed(out["domain"])
            if seen.get("error"):
                out["scan_note"] = ("We could not read this client's last site "
                                    "scan, so nothing was taken from it.")
            elif seen.get("found"):
                if not out["logo"] and seen.get("logo_url"):
                    out["logo"] = seen["logo_url"]
                    out["logo_from"] = "seen on their website"
                have = {c.lower() for c in out["colors"]}
                for c in (seen.get("colors") or []):
                    if c["hex"].lower() not in have and len(out["colors"]) < 6:
                        out["colors"].append(c["hex"])
                        have.add(c["hex"].lower())
            else:
                out["scan_note"] = seen.get("note") or ""
            contact = scan_facts.contact_observed(out["domain"])
            if contact.get("found"):
                out["observed"] = contact.get("fields") or {}
                out["industry"] = out["industry"] or str(
                    out["observed"].get("category") or "")
    except Exception:                                     # noqa: BLE001
        out["scan_note"] = "Site scan unavailable in this environment."

    if gallery:
        images, note = gallery_images(client)
        out["gallery"] = images
        out["gallery_note"] = note
    return out


# ---------------------------------------------------------------------------
# The same facts, for a model
# ---------------------------------------------------------------------------

# Ordered the way a writer needs them: who the business is, how to reach them,
# what they look like, what they are already running.
_PROMPT_ROWS = [
    ("client", "Business name"),
    ("business_name", "Name as it appears on their own site"),
    ("website", "Website"),
    ("industry", "Industry"),
    ("city", "City"),
    ("state", "State"),
    ("address", "Address"),
    ("phone", "Phone"),
    ("email", "Email"),
    ("brand_primary_color", "Primary brand color"),
    ("brand_font", "Brand font"),
    ("platform", "Website platform"),
]


def for_prompt(client: str, domain: str = "", *, heading: str = "") -> str:
    """The client's facts as a block to hand a model, or "" when there are none.

    Every AI feature in the Hub writes better copy when it knows who it is
    writing about, and most of them were given a business name and nothing
    else -- so a model wrote a Connected TV script for a company whose
    industry, city, brand colors and live products were all on file, from the
    name alone. What it produces then is plausible and generic, which is the
    hardest kind of wrong to spot on a page of finished copy.

    Three rules on what goes in it:

    * **Facts, each attributed, and nothing else.** A model handed a phone
      number puts it in the ad. So the block carries only values the Hub
      actually holds, and says where each came from, because "seen on their
      website in March" and "on the client record" are different degrees of
      confidence about a number that is going in front of a customer.
    * **What is not known is said, not left out.** A gap the model cannot see
      is a gap it fills in. The block names the fields nobody has recorded and
      tells the model to write around them rather than inventing them -- the
      `hub/social_plan.py` rule that a fact not in what a human supplied is a
      blocking flag, one step earlier.
    * **No credential ever travels.** The rule
      `services/provider_check.py` works to: this text is pasted into prompts,
      logged, and shown on screens.

    Returns "" rather than a heading with nothing under it: a caller appends
    this straight onto a prompt, and an empty section reads to a model as a
    client we know nothing about, which is not the same as a client we did not
    look up.
    """
    try:
        ctx = context(client, domain)
    except Exception:                                     # noqa: BLE001
        return ""

    fields = ctx.get("fields") or {}
    sources = ctx.get("sources") or {}
    lines: list[str] = []
    looked_up = 0
    for key, label in _PROMPT_ROWS:
        value = _clean(fields.get(key))
        if not value:
            continue
        src = SOURCE_LABELS.get(sources.get(key, ""), "")
        looked_up += 1 if src else 0
        lines.append(f"- {label}: {value}" + (f" (from {src})" if src else ""))

    products = [str(p) for p in (fields.get("products") or [])][:12]
    if products:
        looked_up += 1
        lines.append("- Currently running with us: " + ", ".join(products))

    # The business name alone is not a lookup: `context()` falls back to the
    # name the caller handed in, so a client nothing could be found for still
    # produces one line. Returning a "what we know" block whose only content is
    # the caller's own input, followed by eleven things we do not know, tells a
    # model this is a business with no facts -- which is a different claim from
    # not having looked one up, and it is the one that changes the copy.
    if not looked_up:
        return ""

    unknown = [label for key, label in _PROMPT_ROWS
               if not _clean(fields.get(key))]
    out = [heading or f"What we know about {ctx.get('client', client)}:"]
    out += lines
    if unknown:
        out.append("Not on file, and not to be invented: "
                   + ", ".join(unknown).lower() + ".")
    out.append("Use these facts where they are relevant. Do not state any "
               "fact about this business that is not listed above.")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Database structure watch
# ---------------------------------------------------------------------------

# The two files allowed to build an engine: the shared registry itself, and the
# /status probe, which deliberately opens a short-lived connection of its own so
# a pool that is already wedged cannot report itself healthy.
_ENGINE_OWNERS = ("hub/extensions.py", "hub/diagnostics.py")

# Importing any of these is what "uses the shared engine" means.
_SHARED_ENGINE_NAMES = {"shared_engine", "engine_for", "session_factory",
                        "create_all_metadata", "db"}


def _engine_use(src: str) -> tuple[bool, bool]:
    """(builds its own engine, takes one from hub/extensions) for one file.

    Parsed rather than grepped. The substring version of this check counted a
    *comment* mentioning hub.extensions as proof that a module used it, so two
    modules that each opened their own pool were reported as sharing one — the
    check said four engines when there were six.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        # Unparseable: fall back to the crude test, and treat it as its own
        # engine rather than quietly clearing it.
        return ("create_engine(" in src, False)

    builds = shared = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name == "create_engine":
                builds = True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").startswith("hub.extensions"):
                if any(a.name in _SHARED_ENGINE_NAMES for a in node.names):
                    shared = True
    return (builds, shared)


def _resolves_through_key(root, module: str) -> bool:
    """Does this module actually go through hub/client_key for its identity?

    Source-read, like the rest of this report, and deliberately literal: it
    looks for the import, not for a comment saying the module should have one.
    That distinction is why the engine check above is parsed rather than
    grepped — a promise in a comment counted as evidence once already.
    """
    import pathlib
    base = pathlib.Path(root) / ("modules/" + module if module != "hub" else "hub")
    if not base.exists():
        return False
    for p in base.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if re.search(r"from\s+hub\.client_key\s+import|import\s+hub\.client_key",
                     src):
            return True
    return False


def structure_report() -> dict:
    """Where client data actually lives, and where it can drift apart.

    Built by reading the source rather than the running app, so it stays
    honest even when a module fails to import.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    engines, client_keys = [], []

    # Who still writes JSON without the mirror is asked by hub/jsonstore.py
    # now, not here. This file kept its own version of the test and reached a
    # different answer from /api/integrity's — it counted build scripts, so
    # the panel reported "1 file writes JSON outside hub/jsonstore.py —
    # ad_builder" for modules/ad_builder/scripts/fix_safezones.py, which
    # rewrites layout JSON committed to the repo and never touches the data
    # disk. ad_builder is the Node renderer and keeps no Python state there at
    # all, so the row named a module with nothing to move, immediately above an
    # audit of the same question that had found nothing.
    from . import jsonstore
    json_stores = jsonstore.unmirrored_json_writers(root)

    # Vendored code is not ours to fix, and counting it buries the findings
    # that are. hub/integrity.py learned this when the scan reported the openai
    # package itself; without node_modules here the JSON count read 102.
    skip = {"_attic", "__pycache__", ".git", "node_modules", ".venv", "venv",
            "env", "site-packages", ".tox", "build", "dist"}
    for p in root.rglob("*.py"):
        if any(x in p.parts for x in skip):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = p.relative_to(root).as_posix()
        mod = p.parts[len(root.parts) + 1] if "modules" in p.parts else "hub"

        if rel not in _ENGINE_OWNERS:
            builds, shared = _engine_use(src)
            if builds or shared:
                engines.append({"module": mod, "file": rel,
                                "shared": shared and not builds})
        for key in ("client_id", "client_name", "domain_key", "client_slug"):
            if re.search(rf"\b{key}\b\s*=\s*Column", src):
                client_keys.append({"module": mod, "file": rel, "key": key})

    own_engines = [e for e in engines if not e["shared"]]
    own_modules = sorted({e["module"] for e in own_engines})
    keys_used = sorted({k["key"] for k in client_keys})

    risks = []
    if len(own_modules) > 1:
        risks.append({
            "level": "high",
            "title": f"{len(own_modules)} separate database engines",
            "detail": "Each module builds its own engine instead of using the "
                      "shared one in hub/extensions.py. That means separate "
                      "connection pools against the same Postgres, no shared "
                      "transaction, and a backup of one is not a backup of the "
                      "others.",
            "where": own_modules,
        })
    if len(keys_used) > 1:
        # The columns are still different — that part is structural and would
        # take a migration to change. What matters is whether anything joins
        # them, so check for the joiner rather than assuming it is absent.
        # This check reported "nothing joining them" for as long as that was
        # true; it has to stop saying so when it stops being true, or the
        # diagnostics page trains people to ignore it.
        joined = (root / "hub" / "client_key.py").is_file()
        users = sorted({k["module"] for k in client_keys
                        if _resolves_through_key(root, k["module"])})
        unjoined = sorted({k["module"] for k in client_keys}) if not joined else \
            [m for m in sorted({k["module"] for k in client_keys}) if m not in users]
        if not joined:
            risks.append({
                "level": "high",
                "title": f"A client is identified {len(keys_used)} different ways",
                "detail": "There is no single client table. " + ", ".join(keys_used) +
                          " are used across modules with nothing joining them, so "
                          "the same client can exist several times under slightly "
                          "different names. This is what makes the billing audit "
                          "report false alarms.",
                "where": sorted({k['module'] for k in client_keys}),
            })
        elif unjoined:
            risks.append({
                "level": "medium",
                "title": f"{len(unjoined)} module(s) not on the shared client key",
                "detail": "hub/client_key.py derives one client key from "
                          "whatever a module holds, and " + ", ".join(users) +
                          " resolve through it. These do not, so their records "
                          "still can't be grouped with anybody else's. "
                          "/api/clients/crosswalk shows what is and isn't "
                          "joined right now.",
                "where": unjoined,
            })
        else:
            risks.append({
                "level": "low",
                "title": f"{len(keys_used)} client key columns, joined on read",
                "detail": "The columns still differ — " + ", ".join(keys_used) +
                          " — because changing them needs a migration and "
                          "create_all() does not do migrations. They are joined "
                          "instead by a key derived on read in "
                          "hub/client_key.py, so a client renamed in Knack is "
                          "re-joined on the next request and there is no second "
                          "copy to drift. /api/clients/crosswalk lists every "
                          "client that exists in more than one module, every "
                          "one filed under more than one name, and every record "
                          "with no URL on file that therefore cannot be joined "
                          "to anything.",
                "where": users,
            })
    try:
        from .extensions import legacy_databases
        leftovers = legacy_databases()
    except Exception:                   # noqa: BLE001
        leftovers = []
    if leftovers:
        risks.append({
            "level": "medium",
            "title": f"{len(leftovers)} pre-merge SQLite "
                     f"file{'s' if len(leftovers) != 1 else ''} still on disk",
            "detail": "These are the per-module database files from before the "
                      "modules shared one engine. Nothing reads them any more, "
                      "so any rows still in them are invisible to the Hub — "
                      "which is worth knowing before deleting them. Only a "
                      "deploy running without DATABASE_URL ever wrote one.",
            "where": [x["module"] for x in leftovers],
        })
    if json_stores:
        mods = sorted({j["module"] for j in json_stores})
        risks.append({
            "level": "medium",
            "title": (f"{len(json_stores)} file writes JSON"
                      if len(json_stores) == 1 else
                      f"{len(json_stores)} files write JSON") +
                     " outside hub/jsonstore.py",
            "detail": "JSON on the Render disk is not backed up with the "
                      "database and is lost if the disk is recreated. Fine for "
                      "caches; a problem for anything that is the only copy. "
                      "Files written through hub/jsonstore.py are mirrored into "
                      "the database and are not counted here; /api/backup says "
                      "what that mirror actually holds.",
            "where": mods[:12],
        })

    return {
        "engines": engines,
        "own_engines": len(own_engines),
        "shared_engine_users": len([e for e in engines if e["shared"]]),
        "json_stores": len(json_stores),
        "legacy_databases": leftovers,
        "client_keys": keys_used,
        "risks": risks,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Read from source, so it reports accurately even when a module "
                "fails to import. A module counts as sharing the engine only "
                "if it actually imports one from hub/extensions and calls no "
                "create_engine of its own — a comment saying it should is not "
                "evidence that it does.",
    }


# ---------------------------------------------------------------------------
# URL as the join key
# ---------------------------------------------------------------------------

# Eleven different field names hold the same value across this codebase:
#   url · domain · website · web_url · weburl · site · site_url · web ·
#   homepage · input_url · source_url
# Matching clients by *name* is what produces false positives in the billing
# audit — "Riverside HVAC" and "Riverside HVAC LLC" are different strings for
# the same company. A domain is not: riverside-hvac.com is exactly one client.
# So the domain is the join key, and this is the only place that decides what
# a domain means.

URL_FIELD_NAMES = ("url", "website", "web_url", "weburl", "site_url", "site",
                   "web", "domain", "homepage", "website_url", "input_url",
                   "client_url", "page_url", "source_url")


def canonical_domain(value: str) -> str:
    """One domain string from anything that might hold a URL.

    Deliberately aggressive: strips scheme, www., port, path, query and a
    trailing dot, and lowercases. "HTTPS://WWW.Example.com:443/about?x=1"
    and "example.com" must produce the same key or the join fails silently
    and everything looks fine.
    """
    v = str(value or "").strip().lower()
    if not v:
        return ""
    v = re.sub(r"^[a-z][a-z0-9+.-]*://", "", v)   # any scheme, not just http
    v = v.split("/")[0].split("?")[0].split("#")[0]
    v = v.split("@")[-1]                          # strip any user:pass@
    v = v.split(":")[0]                           # strip port
    v = v.removeprefix("www.").rstrip(".")
    # An email address is not a website. This exact confusion spent an Insites
    # credit auditing nonsense before domain validation was tightened.
    if "@" in value and "." in v and not v.startswith("http"):
        if re.fullmatch(r"[^@\s]+@[^@\s]+", str(value).strip()):
            return ""
    return v if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", v) else ""


def url_from(record: dict) -> str:
    """Pull a URL out of a record whatever the field happens to be called."""
    if not isinstance(record, dict):
        return ""
    for key in URL_FIELD_NAMES:
        if record.get(key):
            d = canonical_domain(record[key])
            if d:
                return d
    return ""


def resolve_by_url(value: str) -> dict:
    """Find the client that owns a domain, across every store.

    This is the join that doesn't exist in the data. It searches by domain
    rather than by name, so a client filed as "Riverside HVAC LLC" in one
    place and "Riverside HVAC" in another still resolves to one answer.
    """
    key = canonical_domain(value)
    if not key:
        return {"domain": "", "found": False,
                "note": "That isn't a usable domain."}

    hits, seen = [], set()

    def add(name, source, extra=""):
        name = _clean(name)
        if not name or (name.lower(), source) in seen:
            return
        seen.add((name.lower(), source))
        hits.append({"client": name, "source": source, "detail": extra})

    try:
        from hub import clients_registry
        for c in clients_registry.all_clients():
            if url_from(c) == key:
                add(c.get("name"), "knack",
                    ", ".join(c.get("products") or [])[:60])
    except Exception:                                     # noqa: BLE001
        pass

    try:
        from modules.scans.app import Scan, SessionLocal
        db = SessionLocal()
        try:
            for s in (db.query(Scan).filter(Scan.domain_key == key)
                      .order_by(Scan.created_at.desc()).limit(5).all()):
                add(s.business_name or key, "scan", str(s.created_at or "")[:10])
        finally:
            db.close()
    except Exception:                                     # noqa: BLE001
        pass

    names = {h["client"].lower() for h in hits}
    return {
        "domain": key,
        "found": bool(hits),
        "clients": hits,
        # More than one *name* for one domain is the duplicate this is meant
        # to catch. Reporting it is the point.
        "conflict": len(names) > 1,
        "note": ("This domain is filed under more than one client name — "
                 "they are almost certainly the same company. Reconciling "
                 "them is what stops the billing audit reporting false alarms."
                 if len(names) > 1 else ""),
    }


def url_audit(limit: int = 2000) -> dict:
    """Every client with no usable URL, and every domain with several names.

    The cap used to be 500 against a registry of ~950, and `checked` reported
    the full count regardless — so this said it had checked every client and
    quietly missed nine of the eleven duplicate domains. A report that stops
    early has to say it stopped early; a clean-looking number that was never
    measured is the failure mode this codebase keeps hitting.
    """
    rows, by_domain = [], {}
    try:
        from hub import clients_registry
        clients = clients_registry.all_clients()
    except Exception:                                     # noqa: BLE001
        clients = []
    examined = clients[:limit]
    for c in examined:
        name = _clean(c.get("name"))
        if not name:
            continue
        dom = url_from(c)
        if not dom:
            rows.append({"client": name, "issue": "no usable URL on file",
                         "raw": _clean(c.get("url") or c.get("domain"))})
            continue
        by_domain.setdefault(dom, set()).add(name)
    dupes = [{"domain": d, "clients": sorted(n)}
             for d, n in by_domain.items() if len(n) > 1]
    note = ("A client with no URL can't be joined to a scan, a brand lookup, "
            "or anything else keyed on domain — they are invisible to every "
            "cross-tool report.")
    if len(clients) > len(examined):
        note += (f" Only the first {len(examined)} of {len(clients)} clients "
                 f"were checked, so this is a floor, not a total.")
    return {
        "checked": len(examined),
        "clients_on_file": len(clients),
        "truncated": len(clients) > len(examined),
        "missing_url": rows,
        "duplicate_domains": dupes,
        "unique_domains": len(by_domain),
        "note": note,
    }
