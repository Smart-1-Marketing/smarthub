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


def structure_report() -> dict:
    """Where client data actually lives, and where it can drift apart.

    Built by reading the source rather than the running app, so it stays
    honest even when a module fails to import.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    engines, json_stores, client_keys = [], [], []

    for p in root.rglob("*.py"):
        if any(x in p.parts for x in ("_attic", "__pycache__")):
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
        if "json.dump" in src and "/templates/" not in rel:
            json_stores.append({"module": mod, "file": rel})
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
    if len(json_stores) > 6:
        risks.append({
            "level": "medium",
            "title": f"{len(json_stores)} modules persist to JSON files",
            "detail": "JSON on the Render disk is not backed up with the "
                      "database and is lost if the disk is recreated. Fine for "
                      "caches; a problem for anything that is the only copy.",
            "where": sorted({j["module"] for j in json_stores})[:12],
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


def url_audit(limit: int = 500) -> dict:
    """Every client with no usable URL, and every domain with several names."""
    rows, by_domain = [], {}
    try:
        from hub import clients_registry
        clients = clients_registry.all_clients()
    except Exception:                                     # noqa: BLE001
        clients = []
    for c in clients[:limit]:
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
    return {
        "checked": len(clients),
        "missing_url": rows,
        "duplicate_domains": dupes,
        "unique_domains": len(by_domain),
        "note": "A client with no URL can't be joined to a scan, a brand "
                "lookup, or anything else keyed on domain — they are invisible "
                "to every cross-tool report.",
    }
