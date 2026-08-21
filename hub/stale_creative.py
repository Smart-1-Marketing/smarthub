"""
Smart 1 Hub — Stale Creative audit.

Answers one question per client: when did we last put creative in front of them?

Sources are pulled from the tools that already produce client creative — the SEO
Image Pipeline, the Image Creator and the Background Remover — plus a Cloudinary
folder scan as a last-resort fallback if none of those expose a record model.

Clients are grouped by how long it has been:

    No creative on file   never uploaded anything
    90+ days              stale, act now
    60-90 days            slipping
    30-60 days            watch
    Under 30 days         fresh (counted, not listed)

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

from flask import Blueprint, current_app, jsonify, render_template, request

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

# Cache the whole audit briefly so the dashboard tile and the report page don't
# each re-scan Cloudinary on every page load.
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


SOURCES = [
    # Corrected 2026-08-17 against the real modules. The original guesses all
    # pointed at SQLAlchemy models that do not exist: SEO Images, Image Creator
    # and Background Remover persist to JSON on disk, not to tables. A source
    # that fails resolves to zero rows *silently*, so the page reported "no
    # records from" every tool while looking perfectly healthy.
    {
        "key": "seo_images",
        "label": "SEO Image Pipeline",
        "callable": "modules.seo_images.app:load_archive",
        "client": ("company", "client", "client_name", "company_name"),
        "when": ("saved_at", "created_at", "uploaded_at", "timestamp"),
        "title": ("filename", "public_id", "alt"),
        "url": ("url", "secure_url"),
    },
    {
        "key": "image_creator",
        "label": "Image Creator",
        "callable": "modules.image_creator.projects:load_index",
        "client": ("client", "client_name", "company", "client_slug"),
        "when": ("updated_at", "saved_at", "created_at"),
        "title": ("name", "project", "title", "id"),
        "url": ("preview", "preview_url", "url"),
    },
    {
        "key": "image_picker",
        "label": "Image Picker",
        "models": ["modules.image_picker.models:SavedImage"],
        "client": ("client", "client_name", "client_slug", "company"),
        "when": ("created_at", "saved_at"),
        "title": ("filename", "public_id", "title"),
        "url": ("url", "secure_url"),
    },
    {
        "key": "commercial_builder",
        "label": "Commercial Builder",
        "models": ["modules.commercial_builder.models:CommercialProject"],
        "client": ("client_name", "client", "company"),
        "when": ("updated_at", "created_at", "rendered_at"),
        "title": ("name", "title", "concept"),
        "url": ("output_url", "video_url", "url"),
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
    """Cloudinary delivery URL -> a small filled thumbnail of the same asset."""
    if not url or "/upload/" not in url:
        return url
    head, _, tail = url.partition("/upload/")
    if tail.startswith(("w_", "c_", "f_", "q_")):
        return url
    return f"{head}/upload/w_320,h_240,c_fill,q_auto,f_auto/{tail}"


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

    Two shapes, because the modules genuinely differ: SEO Images and Image
    Creator persist to JSON on disk and expose their own loader, while Image
    Picker and Commercial Builder use SQLAlchemy models. Trying to read all
    four as models is what made every source return nothing.
    """
    cb = src.get("callable")
    if cb:
        rows = _rows_from_callable(cb)
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

    model = None
    for candidate in src.get("models", []):
        model = _resolve(candidate)
        if model is not None:
            break
    if model is None or not hasattr(model, "query"):
        return []

    try:
        rows = model.query.all()
    except Exception as exc:  # table missing, DB asleep, wrong bind
        current_app.logger.warning(
            "stale_creative: %s query failed: %s", src["key"], exc
        )
        return []

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
            # A client with no live product isn't someone we owe creative to.
            # Counting all 950 as "no creative on file" made the headline
            # number meaningless — it was really "clients we have ever had".
            products = get("product_count") or get("products") or 0
            if isinstance(products, (list, tuple, set)):
                products = len(products)
            out.append({
                "name": name,
                "id": _text(get("id") or get("client_id") or get("knack_id"), 80),
                "products": products,
                "seo": bool(get("is_seo") or get("seo")),
                "active": bool(get("live")) or int(products or 0) > 0,
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
    clients, seen = [], set()
    for c in _registry_clients():
        key = match(c["name"])
        seen.add(key)
        clients.append({"name": c["name"], "key": key, "id": c["id"],
                        "in_registry": True, "products": c["products"],
                        "active": c.get("active", True)})

    # Anything with creative but no registry match still gets a row, flagged.
    for key, recs in by_key.items():
        if key in seen:
            continue
        clients.append({"name": recs[0]["client_raw"] or key, "key": key,
                        "id": "", "in_registry": False, "products": 0,
                        "active": True})   # they have creative, so they count

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

    # Inactive clients are dropped outright, not merely sorted below. A former
    # client with no recent creative is not a gap in our work, and listing them
    # made "no creative on file" read as an indictment when it was mostly
    # accounts we no longer serve. Sorting alone still left them in the counts
    # and in every export of this page.
    inactive_dropped = sum(1 for r in rows if not r.get("active", True))
    rows = [r for r in rows if r.get("active", True)]

    rows.sort(key=lambda r: (not r.get("active", True),
                             r["days_since"] is not None,
                             -(r["days_since"] or 0), r["client"].lower()))

    lo, mid, hi = edges
    groups = [
        {"key": "never", "label": "No creative on file",
         "blurb": "We have never uploaded anything for these clients."},
        {"key": "over_%d" % hi, "label": "%d+ days" % hi,
         "blurb": "Stale. Nothing new in over %d days." % hi},
        {"key": "d%d_%d" % (mid, hi), "label": "%d-%d days" % (mid, hi),
         "blurb": "Slipping. Schedule the next batch."},
        {"key": "d%d_%d" % (lo, mid), "label": "%d-%d days" % (lo, mid),
         "blurb": "Watch. Due within the month."},
        {"key": "fresh", "label": "Under %d days" % lo,
         "blurb": "Current. No action needed."},
    ]
    for g in groups:
        g["clients"] = [r for r in rows if r["bucket"] == g["key"]]
        # The headline count is ACTIVE clients only. A former client with no
        # creative is not a gap in our work, and counting them made "950 with
        # no creative on file" read as alarming when it mostly meant "clients
        # we have ever had". Inactive rows still appear, below the active ones
        # and labelled, so nothing is hidden.
        g["count"] = sum(1 for r in g["clients"] if r.get("active", True))
        g["inactive_count"] = len(g["clients"]) - g["count"]

    active_rows = [r for r in rows if r.get("active", True)]
    return {
        "generated_at": _iso(now),
        "edges": edges,
        "groups": groups,
        "totals": {
            "clients": len(active_rows),
            "all_clients": len(rows),
            # Inactive clients are excluded before this point; reported so the
            # page can say how many were left out rather than silently shrinking.
            "inactive": inactive_dropped,
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


def _cached(refresh=False, items_per_client=DEFAULT_ITEMS_PER_CLIENT):
    now = datetime.now(timezone.utc)
    with _CACHE_LOCK:
        fresh_enough = (
            _CACHE["data"] is not None
            and _CACHE["at"] is not None
            and (now - _CACHE["at"]).total_seconds() < CACHE_TTL_SECONDS
            and _CACHE.get("items") == items_per_client
        )
        if fresh_enough and not refresh:
            return _CACHE["data"]
        data = build_audit(items_per_client=items_per_client, now=now)
        _CACHE.update({"at": now, "data": data, "items": items_per_client})
        return data


def scorecard():
    """Compact payload for the dashboard tile."""
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
        "url": "/qa/stale-creative",
    }


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

def _items_param():
    raw = request.args.get("items", DEFAULT_ITEMS_PER_CLIENT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = DEFAULT_ITEMS_PER_CLIENT
    return max(1, min(n, MAX_ITEMS_PER_CLIENT))  # clamp both ends


@bp.route("/qa/stale-creative")
def page():
    data = _cached(refresh=request.args.get("refresh") == "1",
                   items_per_client=_items_param())
    _audit_log("report_viewed", clients=data["totals"]["clients"])
    return render_template("stale_creative.html", data=data)


@bp.route("/api/qa/stale-creative")
def api():
    return jsonify(_cached(refresh=request.args.get("refresh") == "1",
                           items_per_client=_items_param()))


@bp.route("/api/qa/stale-creative/scorecard")
def api_scorecard():
    return jsonify(scorecard())


@bp.route("/api/qa/stale-creative.csv")
def api_csv():
    import csv
    import io
    data = _cached(refresh=request.args.get("refresh") == "1")
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


def register_stale_creative(app):
    app.register_blueprint(bp)
    return app
