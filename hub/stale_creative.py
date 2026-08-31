"""
Smart 1 Hub — Stale Creative audit.

Answers one question per client: when did we last put creative in front of them?

Sources are pulled from the tools that already produce client creative — the SEO
Image Pipeline, the Image Creator and the Background Remover — plus a Cloudinary
folder scan as a last-resort fallback if none of those expose a record model.

Only clients running a product today are listed. A former client is owed no
creative, so listing them made the report read as an indictment when it was
mostly accounts we no longer serve.

Those clients are grouped by how long it has been, worst elapsed time first:

    90+ days              stale, act now
    60-90 days            slipping
    30-60 days            watch
    Under 30 days         current
    No creative on file   nothing on file for a client we are working for
    Evergreen             creative is fixed for the campaign, so elapsed time
                          is not a gap anybody is going to close

Evergreen is an overlay rather than a bucket — `hub/creative_evergreen.py` —
applied on every read of the cached audit so a mark taken in one gunicorn
worker is honoured by the other immediately.

Mounted at /qa/stale-creative with a JSON API at /api/qa/stale-creative and a
small scorecard payload at /api/qa/stale-creative/scorecard for the dashboard.

Integration notes are in INSTALL-stale-creative.md. Everything that touches
another module is isolated in SOURCES and the _registry_clients() helper, so a
wrong guess about a model or field name degrades to "that source returned
nothing" rather than a 500.
"""

from __future__ import annotations

import importlib
import os
import re
import threading
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request)

bp = Blueprint("stale_creative", __name__)

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

# Bucket edges in days. Override with STALE_CREATIVE_BUCKETS="30,60,90".
def _bucket_edges():
    raw = os.getenv("STALE_CREATIVE_BUCKETS", "30,60,90")
    try:
        edges = sorted({int(x.strip()) for x in raw.split(",") if x.strip()})
        return edges if len(edges) == 3 else [30, 60, 90]
    except Exception:
        return [30, 60, 90]


# How many creatives to return inside an expanded row.
DEFAULT_ITEMS_PER_CLIENT = 12
MAX_ITEMS_PER_CLIENT = 100

# The audit is held for the day by `hub/report_cache.py`, like every other QA
# report, so a Cloudinary scan of the whole account happens once rather than on
# every open of the page and every draw of the dashboard tile.
#
# The five-minute in-process memo below stays *underneath* it, and the two do
# different jobs. The day cache is a file, so both gunicorn workers read the
# same one and it survives a deploy; the memo saves a JSON read when the page
# and its tile ask within seconds of each other. What the memo could never do
# is the thing that mattered: it is per process, so each worker paid for its
# own scan, and a restart threw both away.
CACHE_TTL_SECONDS = int(os.getenv("STALE_CREATIVE_CACHE_TTL", "300"))

_CACHE = {"at": None, "data": None}
_CACHE_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Source adapters
#
# Each entry names one or more "module.path:ClassName" candidates and the field
# names to try, in order, for each piece of data. The first candidate that
# imports wins; the first field that exists and is non-empty wins.
#
# ---> If a source comes back empty on the live Hub, fix it here. <---
# --------------------------------------------------------------------------

def _rows_from_callable(path: str) -> list[dict]:
    """Read a JSON-backed archive via its module's own loader.

    Preferred over reading the file directly: the module owns where its data
    lives, and load_archive()/load_index() already handle a missing or corrupt
    file by returning [].
    """
    mod_name, _, fn_name = path.partition(":")
    try:
        mod = __import__(mod_name, fromlist=[fn_name])
        rows = getattr(mod, fn_name)()
        return [r for r in rows if isinstance(r, dict)]
    except Exception:                                   # noqa: BLE001
        return []


# The field names below were guesses at four modules this file does not own,
# and three of the four guessed wrong -- silently, each in its own way, so the
# report that answers "how long since we made anything for this client" was
# reading two sources and claiming four:
#
#   Image Picker        `SavedImage` is a plain declarative model with its own
#                       session(), not Flask-SQLAlchemy, so it has no `.query`
#                       and `_load_source` returned [] at the guard. That is
#                       the store `filing.file_asset` writes to -- every asset
#                       every tool files against a client -- so the busiest
#                       source in the Hub contributed nothing.
#   Image Creator       asked for created_at/saved_at/updated_at; the index
#                       writes `created` / `created_date` / `updated`. Every
#                       row was dropped by `if not when: continue`.
#   Commercial Builder  asked for client_name/client/company; the row has
#                       `client_id` and nothing else. This one is the worst of
#                       the three, because the rows were *not* empty: the
#                       source counted as live, inflated `totals.creatives`,
#                       and every record was then dropped for having no client.
#
# So the three image stores are not described here any more. `hub/image_audit.py`
# reads exactly these stores, correctly, and had done all along -- two modules
# each guessing at one store's columns is the drift `hub/storage.py` exists to
# stop, and it is what happened. `store` names one of its `STORES` entries and
# the tuples below read that reader's normalised shape rather than each
# module's own spelling, so the next column renamed in the Image Picker is one
# edit in one place instead of a source that quietly stops reporting.
def _rows_from_store(key):
    """Rows from one of `hub/image_audit.py`'s stores, in its normalised shape.

    Never raises: a store that will not answer costs this source its records
    and is reported as `no_records`, exactly as an empty one is. That is the
    weaker of the two answers and it is the one this module already gives --
    `measured` is what says nothing answered at all.
    """
    try:
        from hub import image_audit
        store = next((x for x in image_audit.STORES if x["key"] == key), None)
        if store is None:
            return []
        return [r for r in store["reader"]() if isinstance(r, dict)]
    except Exception:                                   # noqa: BLE001
        return []


def _commercial_rows():
    """Commercials approved and filed for a client.

    An approved render rather than a project row, because the question this
    report asks is what we have *produced*: `approve_render` is the point a cut
    somebody has watched reaches the client's library, and a draft nobody
    rendered is not creative anybody received -- the distinction that module
    already draws and the reason `check_render` stopped filing on its own.

    Joined through the project to `cb_clients` for the name. The project row
    carries `client_id` and nothing else, which is what the old guess at
    `client_name` missed.
    """
    try:
        from modules.commercial_builder.models import (
            Client, CommercialProject, RenderApproval)
    except Exception:                                   # noqa: BLE001
        return []
    try:
        projects = {p.id: p for p in CommercialProject.query.all()}
        names = {c.id: c.name for c in Client.query.all()}
        out = []
        for a in RenderApproval.query.all():
            project = projects.get(a.project_id)
            if project is None:
                continue
            name = names.get(project.client_id, "")
            if not name:
                # No name, no filing -- resolving a client id we cannot name
                # is the guess `filing.file_asset` refuses to make.
                continue
            out.append({
                "client": name,
                "when": str(a.approved_at or "")[:19],
                "label": project.title or "Commercial",
                # The Cloudinary copy, never the provider URL: a render URL is
                # signed and expires, so a link that works today 404s next week.
                "url": a.stored_url or "",
                "where": f"{project.length_seconds}s {project.platform or ''}".strip(),
            })
        return out
    except Exception:                                   # noqa: BLE001
        return []


SOURCES = [
    {
        "key": "seo_images",
        "label": "SEO Image Pipeline",
        "store": "seo_images",
        "client": ("client",),
        "when": ("when",),
        "title": ("label",),
        "url": ("url",),
        "note": ("where",),
    },
    {
        "key": "image_creator",
        "label": "Image Creator",
        "store": "image_creator",
        "client": ("client",),
        "when": ("when",),
        "title": ("label",),
        "url": ("url",),
        "note": ("where",),
    },
    {
        "key": "image_picker",
        "label": "Image Picker",
        "store": "image_picker",
        "client": ("client",),
        "when": ("when",),
        "title": ("label",),
        "url": ("url",),
        "note": ("where",),
    },
    {
        # Not one of image_audit's stores, and deliberately not: a commercial
        # is video, and `SavedImage` models an image or a raw file, so the
        # Commercial Builder leaves the spot in the client's Cloudinary tree
        # rather than filing a gallery row whose thumbnail can never render.
        # It is read here from its own tables instead.
        "key": "commercial_builder",
        "label": "Commercial Builder",
        "callable": "hub.stale_creative:_commercial_rows",
        "client": ("client",),
        "when": ("when",),
        "title": ("label",),
        "url": ("url",),
        "note": ("where",),
    },
]

# Cloudinary folders scanned only when no source model resolves at all.
CLOUDINARY_FOLDERS = [
    os.getenv("SEO_IMAGES_FOLDER", "smart1-seo-images"),
    os.getenv("IMAGE_CREATOR_FOLDER", "smart1-image-creator"),
    os.getenv("BG_REMOVER_FOLDER", "smart1-bg-remover"),
]


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

_LEGAL_SUFFIXES = {
    "llc", "l.l.c", "inc", "inc.", "incorporated", "co", "co.", "corp",
    "corporation", "ltd", "ltd.", "limited", "lp", "llp", "pllc", "pc",
    "company", "the",
}


def _norm_name(value):
    """Normalise a business name for matching. Mirrors the fuzzy match used by
    invoice_off() in hub/qa.py — see _client_matcher() for reuse of the real one."""
    if not value:
        return ""
    s = re.sub(r"[^a-z0-9 ]+", " ", str(value).lower())
    words = [w for w in s.split() if w and w not in _LEGAL_SUFFIXES]
    return " ".join(words)


def _client_matcher():
    """Prefer the Hub's existing matcher if it is importable; fall back to ours."""
    for path in ("hub.qa:_match_client_name", "hub.qa:_norm_client", "hub.qa:_slug"):
        fn = _resolve(path)
        if callable(fn):
            return fn
    return _norm_name


def _resolve(dotted):
    """'pkg.mod:Attr' -> the attribute, or None if anything about it fails."""
    try:
        mod_path, _, attr = dotted.partition(":")
        mod = importlib.import_module(mod_path)
        return getattr(mod, attr, None) if attr else mod
    except Exception:
        return None


def _first(obj, names, default=None):
    """First non-empty attribute OR dict key, in order.

    Must handle both: the SQLAlchemy sources yield row objects, while the
    JSON-backed archives (SEO Images, Image Creator) yield plain dicts.
    getattr() alone silently returns None for every dict key, which made both
    JSON sources report zero records while looking perfectly healthy.
    """
    is_map = isinstance(obj, dict)
    for n in names or ():
        try:
            v = obj.get(n) if is_map else getattr(obj, n, None)
        except Exception:                               # noqa: BLE001
            continue
        if v not in (None, "", []):
            return v
    return default


def _as_utc(value):
    """Everything becomes timezone-aware UTC.

    The Scans module shipped naive-UTC timestamps serialised without an offset
    and the browser read them as local time, so scans displayed hours in the
    future. Not repeating that here."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    if isinstance(value, (int, float)):
        try:
            value = datetime.fromtimestamp(value, tz=timezone.utc)
        except Exception:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(dt):
    return dt.isoformat() if dt else None


def _thumb(url):
    """The URL this row's tile should draw.

    `hub/storage.preview_url()` is the one reader of that rule and this was a
    second one, disagreeing with it on both halves: `c_fill` center-crops, so a
    logo or a tall photograph loses its subject, and a second derived size is a
    second derivative of every asset -- Cloudinary caches and bills each one
    separately, which is exactly what "one derived size for the whole Hub"
    exists to prevent. It also skipped both of that function's guards, so a URL
    that is not ours and a PDF were rewritten alike.

    Never raises: a missing thumbnail costs a tile its picture, and this runs
    once per creative record.
    """
    try:
        from hub import storage
        return storage.preview_url(url)
    except Exception:                                   # noqa: BLE001
        return url


def _text(value, limit=200):
    """Coerce anything to a safe short string. Scans crashed on a numeric field
    being sliced; everything goes through here first."""
    if value is None:
        return ""
    return str(value)[:limit]


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _load_source(src):
    """Return a list of normalised creative records for one source.

    Every source is a function returning dicts now -- `store` names one of
    `hub/image_audit.py`'s readers, `callable` names any other. There used to
    be a second branch that reflected over a SQLAlchemy model and read columns
    named in this file, and it is what broke two of the four sources: it
    guessed `SavedImage` had `.query` (it is plain declarative, so it does not)
    and that a commercial row had `client_name` (it has `client_id`). A module
    that owns its own store reads it better than a table of guesses here can,
    so a new source writes a reader -- `_commercial_rows()` is the example.
    """
    cb = src.get("callable")
    store = src.get("store")
    if not (cb or store):
        # A source declaring neither is a source nobody can read. Returning []
        # rather than falling off the end, which handed `records.extend()` a
        # None and took the whole audit down with it.
        return []
    rows = _rows_from_store(store) if store else _rows_from_callable(cb)
    out = []
    for row in rows:
        when = _as_utc(_first(row, src["when"]))
        if not when:
            continue
        url = _text(_first(row, src.get("url", ())), 600)
        out.append({
            "source": src["key"],
            "source_label": src["label"],
            "client_raw": _text(_first(row, src["client"]), 200),
            "uploaded_at": when,
            "title": _text(_first(row, src["title"]), 160) or "Untitled",
            "note": _text(_first(row, src.get("note", ())), 160),
            "alt": _text(_first(row, src.get("alt", ())), 200),
            "url": url,
            "thumb": _thumb(url),
        })
    return out

from hub.knack_data import CREATIVE_EXCLUDE   # one list, not two


def _load_knack_creative():
    """Creative recorded against a client's products in Knack.

    This is the one that mattered and was missing. The Hub tools below are
    where *we* make creative; Knack is where creative actually gets filed
    against an insertion order — the Drive and PDF links the Clients module and
    Client 360 both show. A client whose creative arrived that way had none of
    it counted, so the report said "never uploaded anything" about clients with
    years of creative on file.

    Products are read live where the API is reachable and from the committed
    export otherwise, the same order Client 360 uses, so the two agree.
    """
    try:
        from hub import knack_data
        rows, _source, _age = knack_data._product_source()
    except Exception as exc:                            # noqa: BLE001
        current_app.logger.warning("stale_creative: knack read failed: %s", exc)
        return []

    out = []
    from hub.knack_data import creative_kind
    for r in rows:
        # Knack holds up to four External Creative Links per product, so a
        # product with a proof and two revisions contributes several. Each is
        # counted, because "when did we last make something for this client"
        # is answered by the newest of them, not by the first field that
        # happened to be filled.
        links = [u for u in (r.get("creative_urls") or []) if u]
        if not links:
            legacy = r.get("creative_url") or r.get("url")
            if legacy:
                links = [legacy]
        # Same rule as Client 360: the URL decides, because `kind` means the
        # link type in the export and the product type in the live rows. Left
        # on `kind`, this counted a client's own homepage as creative.
        links = [u for u in links if creative_kind(u, r.get("kind"))]
        if not links:
            continue
        url = links[0]
        product = str(r.get("product") or "")
        if any(x in product.lower() for x in CREATIVE_EXCLUDE):
            continue
        # ts is YYYYMMDD in the export; the live rows have only start, as
        # MM/DD/YYYY. _as_utc parses ISO and nothing else, so the live rows
        # silently produced no date and every one of them was dropped —
        # hub.dates handles both shapes and is the one parser now.
        from hub import dates as _d
        _day = _d.to_date(r.get("ts")) or _d.to_date(r.get("start"))
        when = _as_utc(datetime(_day.year, _day.month, _day.day)) if _day else None
        if not when:
            continue
        out.append({
            "source": "knack",
            "source_label": "Knack (IO creative)",
            "client_raw": _text(r.get("client") or r.get("organization"), 200),
            "uploaded_at": when,
            "title": _text(product, 160) or "Untitled",
            "note": _text(r.get("campaign"), 160),
            "alt": "",
            "url": _text(url, 600),
            "thumb": _thumb(_text(url, 600)),
        })
    return out


def _knack_date(ts):
    """YYYYMMDD, as Knack stores it, to something _as_utc understands."""
    s = str(ts or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return None


def _load_cloudinary():
    """Fallback: list assets by folder prefix. Used only when no model resolved."""
    try:
        import cloudinary
        import cloudinary.api  # noqa: F401
        from cloudinary import Search
    except Exception:
        return []
    if not (os.getenv("CLOUDINARY_URL") or cloudinary.config().cloud_name):
        return []

    out = []
    for folder in [f for f in CLOUDINARY_FOLDERS if f]:
        cursor, pages = None, 0
        while pages < 5:  # hard ceiling; this is a fallback, not a crawler
            try:
                q = (Search()
                     .expression(f"folder:{folder}/*")
                     .sort_by("created_at", "desc")
                     .max_results(100))
                if cursor:
                    q = q.next_cursor(cursor)
                res = q.execute()
            except Exception as exc:
                current_app.logger.warning(
                    "stale_creative: cloudinary scan of %s failed: %s", folder, exc
                )
                break
            for asset in res.get("resources", []) or []:
                pid = _text(asset.get("public_id"), 400)
                parts = pid.split("/")
                client_raw = parts[1] if len(parts) > 2 else (parts[0] if parts else "")
                url = _text(asset.get("secure_url") or asset.get("url"), 600)
                out.append({
                    "source": "cloudinary",
                    "source_label": f"Cloudinary ({folder})",
                    "client_raw": client_raw.replace("-", " "),
                    "uploaded_at": _as_utc(asset.get("created_at")),
                    "title": parts[-1] if parts else pid,
                    "note": "/".join(parts[2:-1]),
                    "alt": "",
                    "url": url,
                    "thumb": _thumb(url),
                })
            cursor = res.get("next_cursor")
            pages += 1
            if not cursor:
                break
    return [r for r in out if r["uploaded_at"]]


def _registry_clients():
    """The client universe: every active client, so 'never uploaded' is visible.

    Without this the report can only show clients that already have creative,
    which hides the worst cases."""
    for path in (
        "hub.clients_registry:list_clients",
        "hub.clients_registry:all_clients",
        "hub.clients_registry:clients",
        "hub.knack_api:list_clients",
    ):
        fn = _resolve(path)
        if not callable(fn):
            continue
        try:
            rows = fn() or []
        except Exception as exc:
            current_app.logger.warning("stale_creative: %s failed: %s", path, exc)
            continue
        out = []
        for r in rows:
            get = r.get if isinstance(r, dict) else (lambda k, d=None: getattr(r, k, d))
            name = _text(get("name") or get("client_name") or get("company"), 200)
            if not name:
                continue
            if get("is_house") or get("house"):
                continue  # house URLs are ours, not customers
            products = get("product_count") or get("products") or 0
            if isinstance(products, (list, tuple, set)):
                products = len(products)
            # A client with no product delivering *today* isn't someone we owe
            # creative to. This used to read `live or product_count > 0`, and
            # product_count is every insertion order the client has ever had —
            # so an account that ended in 2019 still counted as active and sat
            # in "no creative on file" forever. running_count comes from
            # knack_data.is_running(), the same test Client 360 and the renewal
            # queue use, so all three agree on who is a current client.
            running = get("running_count")
            if running is None:
                running = get("running_products")
            if isinstance(running, (list, tuple, set)):
                running = len(running)
            out.append({
                "name": name,
                "id": _text(get("id") or get("client_id") or get("knack_id"), 80),
                "products": products,
                "seo": bool(get("is_seo") or get("seo")),
                # A source that cannot report running products at all falls
                # back to its own live flag rather than dropping every row.
                "active": bool(get("live")) if running is None
                          else int(running or 0) > 0,
            })
        if out:
            return out
    return []


# --------------------------------------------------------------------------
# The audit
# --------------------------------------------------------------------------

def _bucket_for(days, edges):
    lo, mid, hi = edges
    if days is None:
        return "never"
    if days >= hi:
        return "over_%d" % hi
    if days >= mid:
        return "d%d_%d" % (mid, hi)
    if days >= lo:
        return "d%d_%d" % (lo, mid)
    return "fresh"


def build_audit(items_per_client=DEFAULT_ITEMS_PER_CLIENT, now=None):
    now = now or datetime.now(timezone.utc)
    edges = _bucket_edges()
    match = _client_matcher()

    records = []
    sources_live, sources_dead = [], []
    for src in SOURCES:
        got = _load_source(src)
        (sources_live if got else sources_dead).append(src["label"])
        records.extend(got)

    # Knack last, but it is the one that decides most rows: it is where
    # creative is filed against an insertion order, which is how most of it
    # reaches a client at all.
    knack = _load_knack_creative()
    (sources_live if knack else sources_dead).append("Knack (IO creative)")
    records.extend(knack)

    used_fallback = False
    if not records:
        records = _load_cloudinary()
        used_fallback = bool(records)

    # Bucket records by matched client key.
    by_key = {}
    for rec in records:
        key = match(rec["client_raw"])
        if not key:
            continue
        by_key.setdefault(key, []).append(rec)

    # Start from the client registry so "never" is representable.
    registry_rows = _registry_clients()
    clients, seen = [], set()
    for c in registry_rows:
        key = match(c["name"])
        seen.add(key)
        clients.append({"name": c["name"], "key": key, "id": c["id"],
                        "in_registry": True, "products": c["products"],
                        "active": c.get("active", True)})

    # Creative whose client name matched nothing in the registry. We cannot
    # confirm those are running a product, so they are not listed — but the
    # count is reported, because an unmatched name is usually a spelling that
    # needs fixing rather than a client who does not exist.
    unmatched = 0
    for key, recs in by_key.items():
        if key in seen:
            continue
        unmatched += 1
        clients.append({"name": recs[0]["client_raw"] or key, "key": key,
                        "id": "", "in_registry": False, "products": 0,
                        "active": False})

    rows = []
    for c in clients:
        recs = sorted(by_key.get(c["key"], []),
                      key=lambda r: r["uploaded_at"], reverse=True)
        last = recs[0]["uploaded_at"] if recs else None
        days = (now - last).days if last else None
        year_ago = now - timedelta(days=365)
        rows.append({
            "client": c["name"],
            "client_id": c["id"],
            # Derived on read, never stored. The evergreen overlay is keyed on
            # the client's name and re-matched here, so tightening the matcher
            # cannot orphan a mark somebody made.
            "key": c["key"],
            "active": c.get("active", True),
            "in_registry": c["in_registry"],
            "days_since": days,
            "last_upload": _iso(last),
            "last_source": recs[0]["source_label"] if recs else None,
            "total_creatives": len(recs),
            "last_12_months": sum(1 for r in recs if r["uploaded_at"] >= year_ago),
            "bucket": _bucket_for(days, edges),
            "creatives": [{
                "title": r["title"],
                "note": r["note"],
                "alt": r["alt"],
                "url": r["url"],
                "thumb": r["thumb"],
                "source": r["source_label"],
                "uploaded_at": _iso(r["uploaded_at"]),
            } for r in recs[:items_per_client]],
        })

    # Clients with nothing running are dropped outright, not merely sorted
    # below. A former client with no recent creative is not a gap in our work,
    # and listing them made "no creative on file" read as an indictment when it
    # was mostly accounts we no longer serve. Sorting alone still left them in
    # the counts and in every export of this page.
    dropped = sum(1 for r in rows if not r.get("active", True))
    inactive_dropped = dropped - unmatched
    rows = [r for r in rows if r.get("active", True)]

    # Longest overdue first inside each group; "never" has no number and sorts
    # to the end of whichever group it lands in (its own, in practice).
    rows.sort(key=lambda r: (r["days_since"] is None,
                             -(r["days_since"] or 0), r["client"].lower()))

    lo, mid, hi = edges
    # Worst first, by elapsed time: 90+, 60-90, 30-60, under 30 — then the
    # clients with nothing on file at all. "Never" reads as the most urgent
    # group and used to lead the page, but it is the least actionable of the
    # five: most of it is clients whose creative arrived some way this audit
    # cannot see, and it buried the 90-day list, which is the one a rep can do
    # something about this week. Everything in it is a current client now, so
    # it is worth reading — just last.
    groups = [
        {"key": "over_%d" % hi, "label": "%d+ days" % hi,
         "blurb": "Stale. Nothing new in over %d days." % hi},
        {"key": "d%d_%d" % (mid, hi), "label": "%d-%d days" % (mid, hi),
         "blurb": "Slipping. Schedule the next batch."},
        {"key": "d%d_%d" % (lo, mid), "label": "%d-%d days" % (lo, mid),
         "blurb": "Watch. Due within the month."},
        {"key": "fresh", "label": "Under %d days" % lo,
         "blurb": "Current. No action needed."},
        {"key": "never", "label": "No creative on file",
         "blurb": "Active clients we have never uploaded anything for."},
    ]
    for g in groups:
        # Every row left is a client with a product delivering today, so the
        # count is simply the length. It was a filtered sum back when inactive
        # clients still rendered underneath.
        g["clients"] = [r for r in rows if r["bucket"] == g["key"]]
        g["count"] = len(g["clients"])
        g["inactive_count"] = 0

    return {
        "generated_at": _iso(now),
        # Did anything answer at all? Every source in this audit degrades to an
        # empty list rather than raising, so a morning where Cloudinary, the
        # galleries and Knack all refused produces a complete-looking page
        # saying every client is overdue for creative. That is not an answer,
        # and `hub/report_cache.py` must not store it as the day's — it would
        # stand until tomorrow with nothing on the page saying why.
        #
        # Both halves, because this report is a join and either side going
        # quiet produces a confident page. `_registry_clients()` tries four
        # paths and swallows each failure, so a client list that refused
        # returned [] and the audit reported *nought clients* while the
        # creative sources answered perfectly well — measured: True, and held
        # as the day's answer.
        "measured": (bool(sources_live) or used_fallback) and bool(registry_rows),
        "sources_measured": bool(sources_live) or used_fallback,
        "clients_measured": bool(registry_rows),
        "edges": edges,
        "groups": groups,
        "totals": {
            "clients": len(rows),
            "all_clients": len(rows),
            # Excluded before this point; reported so the page can say how many
            # were left out rather than silently shrinking.
            "inactive": inactive_dropped,
            "unmatched": unmatched,
            "creatives": len(records),
            "needs_attention": sum(
                g["count"] for g in groups if g["key"] not in ("fresh",)
            ),
        },
        "sources": {
            "live": sources_live,
            "no_records": sources_dead,
            "cloudinary_fallback": used_fallback,
        },
    }


EVERGREEN_GROUP = {
    "key": "evergreen",
    "label": "Evergreen",
    "blurb": "Creative is fixed for the campaign. Not counted as stale.",
}


def _apply_evergreen(data):
    """Pull the evergreen clients out of the groups, into their own.

    Deliberately applied on every *read* of the cache rather than inside
    build_audit(): the audit is cached for five minutes and there are two
    gunicorn workers, so a mark made in one of them would go on being ignored
    by the other until its own cache expired — a button that appears to do
    nothing, which is the failure `hub/client_urls.missing()` had to undo.
    Reading the overlay here costs one small JSON read per page.

    Nothing is mutated in place: the cached dict is shared between requests
    and between the page, the API, the CSV and the dashboard scorecard.
    """
    try:
        from hub import creative_evergreen
        marked = creative_evergreen.by_key(_client_matcher())
    except Exception as exc:                            # noqa: BLE001
        current_app.logger.warning("stale_creative: evergreen read failed: %s", exc)
        marked = {}

    groups, pulled = [], []
    for g in data["groups"]:
        if g["key"] == "evergreen":
            continue                                    # rebuilt below
        kept = []
        for row in g["clients"]:
            mark = marked.get(row.get("key"))
            if mark is None:
                kept.append(row)
                continue
            copy = dict(row)
            copy["evergreen"] = {
                "by": mark.get("by") or "",
                "at": mark.get("at") or "",
                "note": mark.get("note") or "",
                "campaign": mark.get("campaign") or "",
                # The bucket it would have sat in, so the evergreen list still
                # says how long it has actually been. A row parked here with
                # no elapsed time reads as a row nobody measured.
                "from_group": g["label"],
            }
            pulled.append(copy)
        ng = dict(g)
        ng["clients"] = kept
        ng["count"] = len(kept)
        groups.append(ng)

    pulled.sort(key=lambda r: (r["days_since"] is None,
                               -(r["days_since"] or 0), r["client"].lower()))
    ever = dict(EVERGREEN_GROUP)
    ever["clients"] = pulled
    ever["count"] = len(pulled)
    ever["inactive_count"] = 0
    groups.append(ever)

    out = dict(data)
    out["groups"] = groups
    totals = dict(data["totals"])
    listed = sum(g["count"] for g in groups if g["key"] != "evergreen")
    totals["clients"] = listed
    totals["evergreen"] = len(pulled)
    totals["needs_attention"] = sum(
        g["count"] for g in groups if g["key"] not in ("fresh", "evergreen")
    )
    out["totals"] = totals
    return out


def _cached(refresh=False, items_per_client=DEFAULT_ITEMS_PER_CLIENT):
    """Today's audit. Built on the first ask, read by every ask after it.

    `refresh=True` is the Refresh button and re-runs the scan.

    The evergreen overlay is applied on the way *out* and is never part of
    what is stored. `_apply_evergreen()` gives the reason — a mark made in one
    gunicorn worker would otherwise be ignored by the other until its cache
    expired — and holding the audit for a day rather than five minutes puts a
    much longer fuse on exactly that: the row somebody has just marked would
    sit where it was until tomorrow, which is a button that appears to do
    nothing. So the mark needs no cache invalidation at all; it is read fresh
    every time.
    """
    now = datetime.now(timezone.utc)
    with _CACHE_LOCK:
        fresh_enough = (
            _CACHE["data"] is not None
            and _CACHE["at"] is not None
            and (now - _CACHE["at"]).total_seconds() < CACHE_TTL_SECONDS
            and _CACHE.get("items") == items_per_client
        )
        if fresh_enough and not refresh:
            data = _CACHE["data"]
        else:
            def build():
                return build_audit(items_per_client=items_per_client, now=now)

            # The import is guarded and the call is not: a scan that fails
            # must fail once and reach the caller, not be quietly run a second
            # time by a bare `except` that cannot tell "report_cache is
            # missing" from "Cloudinary refused".
            try:
                from hub import report_cache
            except Exception:                           # noqa: BLE001
                report_cache = None
            data = (build() if report_cache is None else
                    report_cache.serve("qa:stale-creative", build,
                                       params=f"items={int(items_per_client)}",
                                       force=bool(refresh)))
            _CACHE.update({"at": now, "data": data, "items": items_per_client})
    return _apply_evergreen(data)


def scorecard():
    """Compact payload for the dashboard tile.

    **It carries `measured`, and both halves of it.** This copies eleven keys
    out of the audit, and the first draft copied the counts and left that flag
    behind — so on a morning where the client list refused, `build_audit()`
    said `measured: False`, the report page one click away drew *Not
    measured*, and this tile drew **0 · 0 · 0 · 0** on the dashboard: every
    client up to date on creative, in four confident noughts, because the
    fetch succeeded and every band was genuinely zero. Two screens answering
    one question differently, which is the trap `by_client()` below is written
    to avoid one function later.

    The tile's own note says it fails quietly so the dashboard never goes down
    when the card cannot load, and that is right about a fetch that fails —
    and it is what made this invisible, because this fetch does not fail.
    """
    data = _cached()
    by = {g["key"]: g["count"] for g in data["groups"]}
    lo, mid, hi = data["edges"]
    worst = []
    for g in data["groups"]:
        if g["key"] in ("never", "over_%d" % hi):
            worst.extend(g["clients"][:5])
    return {
        "generated_at": data["generated_at"],
        "edges": data["edges"],
        "never": by.get("never", 0),
        "over_hi": by.get("over_%d" % hi, 0),
        "mid_hi": by.get("d%d_%d" % (mid, hi), 0),
        "lo_mid": by.get("d%d_%d" % (lo, mid), 0),
        "fresh": by.get("fresh", 0),
        "needs_attention": data["totals"]["needs_attention"],
        "clients": data["totals"]["clients"],
        "worst": [{"client": w["client"], "days_since": w["days_since"]}
                  for w in worst[:5]],
        # Both halves, because they send somebody to different places: the
        # client list refusing and every creative source refusing are not the
        # same outage, and the tile says which.
        "measured": bool(data.get("measured")),
        "sources_measured": bool(data.get("sources_measured")),
        "clients_measured": bool(data.get("clients_measured")),
        "url": "/qa/stale-creative",
    }


def by_client() -> tuple[dict, str]:
    """`({matched key: row}, error)` — this audit, one row per client.

    The client-health report needs to ask "how long since we made anything for
    *this* client", which is what the whole audit already answers; walking the
    six sources a second time there would be the mirror this codebase has paid
    for twice. So the report reads this, keyed on the audit's **own** match key
    so the two cannot disagree about what counts as one client.

    The evergreen overlay has already been applied by `_cached()`, and its
    group is deliberately included: a client whose creative is fixed for the
    campaign is not a gap, and the row carries `evergreen` so the reader can
    say so rather than counting it as one.

    A pair rather than a bare dict, for the reason `connected_accounts_result`
    gives in Google Finder: *nobody is overdue* and *the audit would not run*
    are different answers.
    """
    try:
        data = _cached()
    except Exception as exc:                            # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"[:200]
    out: dict = {}
    for group in data.get("groups") or ():
        for row in group.get("clients") or ():
            key = row.get("key")
            if not key:
                continue
            out[key] = {
                "client": row.get("client") or "",
                "days_since": row.get("days_since"),
                "last_upload": row.get("last_upload") or "",
                "last_source": row.get("last_source") or "",
                "total_creatives": int(row.get("total_creatives") or 0),
                "last_12_months": int(row.get("last_12_months") or 0),
                "group": group.get("key") or "",
                "group_label": group.get("label") or "",
                "evergreen": row.get("evergreen") or None,
            }
    return out, ""


def match_key(name: str) -> str:
    """The key this audit files a client under. Handed out rather than copied.

    `hub/creative_evergreen.by_key()` already takes the matcher as an argument
    for the same reason: a second normaliser somewhere else is a mark filed
    against a client the audit does not think it is about.
    """
    try:
        return _client_matcher()(name) or ""
    except Exception:                                   # noqa: BLE001
        return ""


def _audit_log(event, **extra):
    """hub/audit.py exposes log(module, type_, actor=..., **extra).
    The Scans module called a record() that never existed and every event was
    swallowed by a bare except. Using the real signature."""
    try:
        from hub import audit as hub_audit
        hub_audit.log("stale_creative", event, **extra)
    except Exception as exc:
        current_app.logger.debug("stale_creative: audit log skipped: %s", exc)


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

def _actor():
    """The signed-in name, or "" for the shared-password session."""
    try:
        from hub import current_user
        return current_user() or ""
    except Exception:                                   # noqa: BLE001
        return ""


@bp.before_request
def _require_login():
    """One guard on the blueprint, not one per view.

    This report names every active client and how far behind we are on each,
    and it now carries a write route. `hub/auth.py` names the failure in its
    own docstring: a guard written per view is a guard the next route added
    does not have to remember, and Commercial Builder shipped forty of them
    answering 200 to anyone with the URL.
    """
    from flask import redirect
    from hub import current_user, access
    if current_user():
        return None
    if access.wants_json(request.path or "/", request.headers.get("Accept", "")):
        return jsonify({"error": "Sign in to read this report."}), 401
    return redirect("/login?next=" + (request.path or "/"))


def _items_param():
    raw = request.args.get("items", DEFAULT_ITEMS_PER_CLIENT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = DEFAULT_ITEMS_PER_CLIENT
    return max(1, min(n, MAX_ITEMS_PER_CLIENT))  # clamp both ends


# Re-running is a POST and nothing else. `?refresh=1` on a GET used to do it,
# which is a URL a reload, a bookmark or a link preview fires without anybody
# asking — and re-running here is a walk of the whole Cloudinary account. The
# page's Refresh button posts to /qa/stale-creative/refresh below.
@bp.route("/qa/stale-creative")
def page():
    data = _cached(items_per_client=_items_param())
    _audit_log("report_viewed", clients=data["totals"]["clients"])
    return render_template("stale_creative.html", data=data)


@bp.route("/api/qa/stale-creative")
def api():
    return jsonify(_cached(items_per_client=_items_param()))


@bp.route("/qa/stale-creative/refresh", methods=["POST"])
def refresh():
    """Re-run the audit now, then show it.

    A redirect rather than JSON: the button that posts here is on a
    server-rendered page, and answering it with a payload would replace the
    report with a wall of it.
    """
    _cached(refresh=True, items_per_client=_items_param())
    return redirect("/qa/stale-creative")


@bp.route("/api/qa/stale-creative/scorecard")
def api_scorecard():
    return jsonify(scorecard())


@bp.route("/api/qa/stale-creative.csv")
def api_csv():
    import csv
    import io
    data = _cached()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Group", "Client", "Days since last creative", "Last upload",
                "Last source", "Creatives on file", "Last 12 months",
                "In client registry"])
    for g in data["groups"]:
        for c in g["clients"]:
            w.writerow([g["label"], c["client"],
                        "" if c["days_since"] is None else c["days_since"],
                        c["last_upload"] or "", c["last_source"] or "",
                        c["total_creatives"], c["last_12_months"],
                        "yes" if c["in_registry"] else "no"])
    return (buf.getvalue(), 200, {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": 'attachment; filename="stale-creative.csv"',
    })


@bp.route("/api/qa/stale-creative/evergreen", methods=["POST"])
def api_evergreen():
    """Mark a client's creative evergreen, or take the mark off again.

    Evergreen means the creative is not going to change for this campaign, so
    the elapsed time since the last one is not a gap anybody is going to close.
    The row leaves the stale list and joins the Evergreen group underneath it —
    it is never simply deleted, because a list that quietly gets shorter cannot
    be told from a list that failed to load.
    """
    from hub import creative_evergreen
    body = request.get_json(silent=True) or {}
    client = str(body.get("client") or "").strip()
    on = bool(body.get("evergreen", True))
    result = creative_evergreen.set_mark(
        client, on,
        actor=_actor(),
        note=str(body.get("note") or ""),
        campaign=str(body.get("campaign") or ""),
    )
    if not result.get("ok"):
        return jsonify(result), 400
    _audit_log("evergreen_marked" if on else "evergreen_cleared",
               actor=_actor(), client=client)
    return jsonify(result)


def register_stale_creative(app):
    app.register_blueprint(bp)
    return app
