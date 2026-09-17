"""A copy of the landed rows, so none of this has to be pulled twice.

The fact table is the product of every native pull and every history
window, and the campaign map is the product of people confirming names.
Both live in one Postgres. This module keeps a second copy on the Hub's
persistent disk and, where Cloudinary is configured, a third off the box,
and can put either back.

## The layout

``/var/data/reports/backups/`` (``hub/jsonstore.data_dir``):

* ``facts/<platform>/<YYYY-MM>.jsonl.gz`` -- the fact rows for one platform
  and one month, one JSON object per line, gzip. A month is the unit
  because the platforms restate for four weeks and then settle: an old
  month's file is written once and never again, a recent month's file is
  rewritten when its rows change, and the whole set never grows past the
  data it copies. Changed is measured by hashing the serialized rows
  against the manifest, so a run with nothing new writes nothing.
* ``tables/<table>.jsonl.gz`` -- the campaign map, the aliases, the budget
  lines, the markups, the links and the sync watermarks, whole, rewritten
  when their hash changes. Small, and the map is the one nobody wants to
  confirm twice.
* ``manifest.json`` -- per file: rows, bytes, sha256, written_at, and the
  off-site URL when one was taken. ``status()`` reads it for the card.

The off-site copy goes through ``hub/storage.put`` into the ``backups``
bucket (``smart1-backups/reports/...``), only when Cloudinary is ready:
the disk fallback in ``put()`` would be a second copy on the same disk,
which is not a second copy. With Cloudinary unset the card says so.

## Restore

``restore()`` reads the manifest and puts the rows back through
``store.upsert_rows(screen=False)`` -- the rows passed the screen when they
first landed -- and a keyed merge for the small tables. It adds and
updates and never deletes, so it is safe to run against a table that still
has its rows: the result is the same table. A Render deploy that ends a
run mid-way leaves a partial copy on the disk and the manifest one step
behind it; the next run rewrites what differs.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import os
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import extract, func, inspect as sa_inspect

from . import store

log = logging.getLogger(__name__)

STALE_HOURS = 36
BATCH = 500

# The small tables, whole. Not the pacing snapshots (rebuilt hourly from
# the rows), the quarantine or the reconcile (both re-measured) -- a
# backup is for what cannot be recomputed.
TABLES = {
    "campaign_map": store.CampaignMap,
    "campaign_aliases": store.CampaignAlias,
    "budget_lines": store.BudgetLine,
    "platform_markup": store.PlatformMarkup,
    "links": store.ReportLink,
    "sync": store.ReportsSync,
    "map_refusals": store.MapRefusal,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def root() -> str:
    from hub import jsonstore
    return jsonstore.data_dir("reports", "backups")


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _plain(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _row_dict(obj) -> dict:
    return {c.key: _plain(getattr(obj, c.key)) for c in sa_inspect(type(obj)).mapper.column_attrs}


def _coerce(model, row: dict) -> dict:
    """A serialized row back into column values, by the column's type."""
    from sqlalchemy import Boolean, Date, DateTime, Numeric
    out = {}
    for col in model.__table__.columns:
        if col.key not in row:
            continue
        v = row[col.key]
        if v is None:
            out[col.key] = None
        elif isinstance(col.type, DateTime):
            out[col.key] = datetime.fromisoformat(str(v))
        elif isinstance(col.type, Date):
            out[col.key] = date.fromisoformat(str(v)[:10])
        elif isinstance(col.type, Numeric):
            out[col.key] = Decimal(str(v))
        elif isinstance(col.type, Boolean):
            out[col.key] = bool(v)
        else:
            out[col.key] = v
    return out


def _pack(rows: list[dict]) -> bytes:
    text = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str) for r in rows)
    if text:
        text += "\n"
    buf = io.BytesIO()
    # mtime pinned so the same rows gzip to the same bytes and hash.
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    return buf.getvalue()


def _unpack(data: bytes) -> list[dict]:
    text = gzip.decompress(data).decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# The manifest
# ---------------------------------------------------------------------------

def _manifest_path() -> str:
    return os.path.join(root(), "manifest.json")


def manifest() -> dict:
    try:
        from hub import jsonstore
        data = jsonstore.read_json(_manifest_path(), default={}) or {}
    except Exception:                                   # noqa: BLE001
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("facts", {})
    data.setdefault("tables", {})
    return data


def _save_manifest(data: dict) -> None:
    from hub import jsonstore
    # durable=False: the manifest describes files on this disk, and the
    # database mirror would describe files the database cannot reach.
    jsonstore.write_json(_manifest_path(), data, durable=False, indent=1)


# ---------------------------------------------------------------------------
# Reading the rows
# ---------------------------------------------------------------------------

def fact_partitions() -> list[tuple[str, int, int, int]]:
    """``[(platform, year, month, rows)]`` for every platform-month on file."""
    db = store.SessionLocal()
    try:
        y = extract("year", store.AdPerfDaily.date)
        m = extract("month", store.AdPerfDaily.date)
        rows = (db.query(store.AdPerfDaily.platform, y, m, func.count())
                  .group_by(store.AdPerfDaily.platform, y, m)
                  .order_by(store.AdPerfDaily.platform, y, m).all())
        return [(str(p), int(yy), int(mm), int(n)) for p, yy, mm, n in rows]
    finally:
        db.close()


def fact_rows(platform: str, year: int, month: int) -> list[dict]:
    first = date(year, month, 1)
    nxt = date(year + (month == 12), (month % 12) + 1, 1)
    db = store.SessionLocal()
    try:
        q = (db.query(store.AdPerfDaily)
               .filter(store.AdPerfDaily.platform == platform,
                       store.AdPerfDaily.date >= first, store.AdPerfDaily.date < nxt)
               .order_by(store.AdPerfDaily.account_id, store.AdPerfDaily.campaign_id,
                         store.AdPerfDaily.date))
        return [_row_dict(r) for r in q.all()]
    finally:
        db.close()


def table_rows(name: str) -> list[dict]:
    model = TABLES[name]
    db = store.SessionLocal()
    try:
        keys = [c.key for c in model.__table__.primary_key.columns]
        q = db.query(model).order_by(*[getattr(model, k) for k in keys])
        return [_row_dict(r) for r in q.all()]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _offsite_available() -> str:
    """"" when a copy can go off this disk, else why not."""
    try:
        from hub import storage
    except Exception as exc:                            # noqa: BLE001
        return f"storage unavailable ({type(exc).__name__})"
    if not storage.ready():
        return "Cloudinary is not configured, so there is no copy off this disk."
    return ""


def _offsite(relpath: str, data: bytes) -> tuple[str, str]:
    """``(url, note)``: the off-site copy's URL, or "" and why not."""
    why = _offsite_available()
    if why:
        return "", why
    try:
        from hub import storage
        from hub.config import settings
        public_id = f"{settings.folder('backups')}/reports/{relpath}"
        asset = storage.put("backups", os.path.basename(relpath), data, subpath="reports",
                            overwrite=True, public_id=public_id)
        return asset.url or "", "" if asset.url else "upload answered without a URL"
    except Exception as exc:                            # noqa: BLE001 - the disk copy stands
        return "", f"off-site copy failed: {type(exc).__name__}: {str(exc)[:160]}"


def _write_file(relpath: str, data: bytes) -> None:
    path = os.path.join(root(), relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def run(actor: str = "scheduler", offsite: bool = True) -> dict:
    """Write what changed. ``{"ok", "files", "written", "rows", "bytes",
    "offsite", "offsite_note", "errors"}``."""
    out = {"ok": False, "files": 0, "written": 0, "rows": 0, "bytes": 0,
           "offsite": 0, "offsite_note": "", "errors": {}, "actor": actor}
    data = manifest()
    data["started_at"] = _now().isoformat(timespec="seconds")
    data["finished_at"] = ""
    _save_manifest(data)
    seen_facts = set()
    if offsite:
        # Said on every run, not only on one that had something to send:
        # a backup that is current and on one disk is the card's finding.
        out["offsite_note"] = _offsite_available()
        offsite = not out["offsite_note"]
    try:
        # The fact rows, a platform-month at a time.
        for platform, year, month, _n in fact_partitions():
            key = f"{platform}/{year:04d}-{month:02d}"
            seen_facts.add(key)
            rows = fact_rows(platform, year, month)
            packed = _pack(rows)
            digest = hashlib.sha256(packed).hexdigest()
            entry = dict(data["facts"].get(key) or {})
            relpath = f"facts/{platform}/{year:04d}-{month:02d}.jsonl.gz"
            changed = entry.get("sha256") != digest or not os.path.exists(os.path.join(root(), relpath))
            if changed:
                _write_file(relpath, packed)
                entry.update({"file": relpath, "rows": len(rows), "bytes": len(packed),
                              "sha256": digest, "written_at": _now().isoformat(timespec="seconds")})
                out["written"] += 1
                if offsite:
                    url, note = _offsite(relpath, packed)
                    entry["offsite_url"] = url
                    if note:
                        out["offsite_note"] = note
            if entry.get("offsite_url"):
                out["offsite"] += 1
            data["facts"][key] = entry
            out["files"] += 1
            out["rows"] += len(rows)
            out["bytes"] += int(entry.get("bytes") or 0)
        # A platform-month with no rows any more (a quarantine reversal, a
        # deletion) keeps its last file: a backup does not forget on purpose.
        for name in TABLES:
            rows = table_rows(name)
            packed = _pack(rows)
            digest = hashlib.sha256(packed).hexdigest()
            entry = dict(data["tables"].get(name) or {})
            relpath = f"tables/{name}.jsonl.gz"
            changed = entry.get("sha256") != digest or not os.path.exists(os.path.join(root(), relpath))
            if changed:
                _write_file(relpath, packed)
                entry.update({"file": relpath, "rows": len(rows), "bytes": len(packed),
                              "sha256": digest, "written_at": _now().isoformat(timespec="seconds")})
                out["written"] += 1
                if offsite:
                    url, note = _offsite(relpath, packed)
                    entry["offsite_url"] = url
                    if note:
                        out["offsite_note"] = note
            if entry.get("offsite_url"):
                out["offsite"] += 1
            data["tables"][name] = entry
            out["files"] += 1
            out["rows"] += len(rows)
            out["bytes"] += int(entry.get("bytes") or 0)
        out["ok"] = True
    except Exception as exc:                            # noqa: BLE001 - the manifest records it
        log.exception("reports: backup failed")
        out["errors"]["run"] = f"{type(exc).__name__}: {exc}"[:300]
    data["finished_at"] = _now().isoformat(timespec="seconds")
    data["last"] = {k: v for k, v in out.items() if k != "errors"} | {"errors": dict(out["errors"])}
    _save_manifest(data)
    return out


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

def restore(actor: str = "", facts: bool = True, tables: bool = True) -> dict:
    """Put the copy back: adds and updates, never deletes. ``{"ok",
    "facts", "tables", "files", "errors"}`` with row counts."""
    out = {"ok": False, "facts": 0, "tables": 0, "files": 0, "errors": {}, "actor": actor}
    data = manifest()
    if not data["facts"] and not data["tables"]:
        out["errors"]["manifest"] = "There is no backup on this disk yet."
        return out
    if facts:
        for key, entry in sorted(data["facts"].items()):
            path = os.path.join(root(), str(entry.get("file") or ""))
            if not entry.get("file") or not os.path.exists(path):
                out["errors"][key] = "file missing"
                continue
            try:
                with open(path, "rb") as fh:
                    rows = _unpack(fh.read())
                for offset in range(0, len(rows), BATCH):
                    out["facts"] += store.upsert_rows(rows[offset:offset + BATCH], screen=False)
                out["files"] += 1
            except Exception as exc:                    # noqa: BLE001 - one file, not the restore
                out["errors"][key] = f"{type(exc).__name__}: {exc}"[:200]
    if tables:
        for name, entry in sorted(data["tables"].items()):
            model = TABLES.get(name)
            path = os.path.join(root(), str(entry.get("file") or ""))
            if model is None or not entry.get("file") or not os.path.exists(path):
                out["errors"][name] = "file missing" if model else "unknown table"
                continue
            try:
                with open(path, "rb") as fh:
                    rows = _unpack(fh.read())
                db = store.SessionLocal()
                try:
                    for row in rows:
                        db.merge(model(**_coerce(model, row)))
                        out["tables"] += 1
                    db.commit()
                finally:
                    db.close()
                out["files"] += 1
            except Exception as exc:                    # noqa: BLE001
                out["errors"][name] = f"{type(exc).__name__}: {exc}"[:200]
    out["ok"] = not out["errors"]
    data["last_restore"] = {**{k: v for k, v in out.items() if k != "errors"},
                            "errors": dict(out["errors"]), "at": _now().isoformat(timespec="seconds")}
    _save_manifest(data)
    return out


# ---------------------------------------------------------------------------
# What the card prints
# ---------------------------------------------------------------------------

def status() -> dict:
    data = manifest()
    last = data.get("last") or {}
    finished = data.get("finished_at") or ""
    started = data.get("started_at") or ""
    stale = True
    age_hours = None
    if finished:
        try:
            when = datetime.fromisoformat(finished)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            age_hours = round((_now() - when).total_seconds() / 3600, 1)
            stale = age_hours > STALE_HOURS
        except ValueError:
            # A finish time that does not parse is a manifest this code did
            # not write: no age to report, and stale is the safe reading.
            age_hours = None
            stale = True
    running = bool(started and not finished)
    if running:
        try:
            since = datetime.fromisoformat(started)
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            running = (_now() - since).total_seconds() < 3600
        except ValueError:
            running = False                 # an unparseable start is not a run
    files = list(data["facts"].values()) + list(data["tables"].values())
    return {
        "root": root(),
        "ever": bool(finished),
        "started_at": started, "finished_at": finished,
        "running": running,
        "stalled": bool(started and not finished and not running),
        "age_hours": age_hours, "stale": stale,
        "files": len(files),
        "rows": sum(int(f.get("rows") or 0) for f in files),
        "bytes": sum(int(f.get("bytes") or 0) for f in files),
        "offsite": sum(1 for f in files if f.get("offsite_url")),
        "offsite_note": last.get("offsite_note") or "",
        "platforms": sorted({k.split("/")[0] for k in data["facts"]}),
        "months": len(data["facts"]),
        "last": last,
        "last_restore": data.get("last_restore") or {},
        "indexes": store.index_status(),
    }
