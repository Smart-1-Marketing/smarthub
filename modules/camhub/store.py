"""Pages, sources, the conditions cache, the refresh and the health screen.

Reads come from the cache; nothing user-facing ever calls an external API.
The scheduled refresh runs each source on its own cadence, writes the last
GOOD payload to `camhub_conditions_cache`, and records every outcome on the
source row -- so "is this page healthy" is one query and not a log dig.

A failed fetch never clears the cache: the page serves the stale value with
an honest timestamp, and only after a source has been failing past its
tolerance does its tile collapse (tiles.py decides that, from `fetched_at`).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from . import adapters
from .models import CamPage, CamSource, ConditionsCache, session

log = logging.getLogger("hub")


def _now():
    return datetime.now(timezone.utc)


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


# ----------------------------------------------------------------- pages

def page_dict(row: CamPage) -> dict:
    return {
        "id": row.id, "slug": row.slug, "title": row.title,
        "client_name": row.client_name, "business_name": row.business_name,
        "location_name": row.location_name, "address": row.address,
        "lat": row.lat, "lon": row.lon, "timezone": row.timezone or "America/New_York",
        "location_type": row.location_type or "inland_lake",
        "cam_embed_url": row.cam_embed_url, "cam_embed_type": row.cam_embed_type or "youtube",
        "cam_caption": row.cam_caption, "seo_html": row.seo_html or "",
        "config": _loads(row.config, {}), "status": row.status or "live",
        "created_at": _aware(row.created_at), "updated_at": _aware(row.updated_at),
    }


def list_pages() -> list[dict]:
    with session() as s:
        rows = s.execute(select(CamPage).order_by(CamPage.title)).scalars().all()
        return [page_dict(r) for r in rows]


def get_page(slug: str) -> dict | None:
    with session() as s:
        row = s.execute(select(CamPage).where(CamPage.slug == slug)).scalar_one_or_none()
        return page_dict(row) if row else None


def upsert_page(spec: dict) -> dict:
    """Create or update a page by slug. The spec is the shape `seeds.py` and
    the builder produce; `sources` on it are written by `upsert_sources`."""
    fields = ("title", "client_name", "business_name", "location_name", "address",
              "lat", "lon", "timezone", "location_type", "cam_embed_url",
              "cam_embed_type", "cam_caption", "seo_html", "status")
    with session() as s:
        row = s.execute(select(CamPage).where(CamPage.slug == spec["slug"])).scalar_one_or_none()
        created = row is None
        if created:
            row = CamPage(slug=spec["slug"])
            s.add(row)
        for f in fields:
            if f in spec:
                setattr(row, f, spec[f])
        if "config" in spec:
            row.config = json.dumps(spec["config"] or {})
        s.commit()
        out = page_dict(row)
    if spec.get("sources"):
        upsert_sources(out["id"], spec["sources"])
    out["created"] = created
    return out


# --------------------------------------------------------------- sources

def source_dict(row: CamSource) -> dict:
    return {
        "id": row.id, "page_id": row.page_id, "key": row.key, "adapter": row.adapter,
        "label": row.label, "config": _loads(row.config, {}),
        "cadence_minutes": row.cadence_minutes or 60,
        "tolerance_minutes": row.tolerance_minutes or 90,
        "enabled": bool(row.enabled), "last_attempt": _aware(row.last_attempt),
        "last_success": _aware(row.last_success), "last_error": row.last_error,
        "error_count": row.error_count or 0,
    }


def upsert_sources(page_id: int, sources: list[dict]) -> list[dict]:
    out = []
    with session() as s:
        for spec in sources:
            row = s.execute(select(CamSource).where(
                CamSource.page_id == page_id, CamSource.key == spec["key"])).scalar_one_or_none()
            if row is None:
                row = CamSource(page_id=page_id, key=spec["key"])
                s.add(row)
            row.adapter = spec["adapter"]
            row.label = spec.get("label") or spec["key"]
            row.config = json.dumps(spec.get("config") or {})
            row.cadence_minutes = int(spec.get("cadence_minutes") or 60)
            row.tolerance_minutes = int(spec.get("tolerance_minutes") or 90)
            row.enabled = bool(spec.get("enabled", True))
            s.flush()
            out.append(source_dict(row))
        s.commit()
    return out


def list_sources(page_id: int | None = None, *, enabled_only: bool = False) -> list[dict]:
    with session() as s:
        q = select(CamSource).order_by(CamSource.page_id, CamSource.key)
        if page_id is not None:
            q = q.where(CamSource.page_id == page_id)
        if enabled_only:
            q = q.where(CamSource.enabled.is_(True))
        return [source_dict(r) for r in s.execute(q).scalars().all()]


# ----------------------------------------------------------------- cache

def cache_for(page_id: int) -> dict[str, dict]:
    """{key: {"payload", "fetched_at", "status", "error"}} -- the last good
    payload per source, whatever the latest attempt did."""
    with session() as s:
        rows = s.execute(select(ConditionsCache).where(
            ConditionsCache.page_id == page_id)).scalars().all()
        return {r.key: {"payload": _loads(r.payload, None), "fetched_at": _aware(r.fetched_at),
                        "status": r.status, "error": r.error} for r in rows}


def _write_cache(s, page_id: int, key: str, *, payload=None, status: str, error: str | None):
    row = s.execute(select(ConditionsCache).where(
        ConditionsCache.page_id == page_id, ConditionsCache.key == key)).scalar_one_or_none()
    if row is None:
        row = ConditionsCache(page_id=page_id, key=key)
        s.add(row)
    if payload is not None:
        row.payload = json.dumps(payload, default=str)
        row.fetched_at = _now()
    row.status = status
    row.error = error


def seed_cache(page_id: int, key: str, payload: dict, fetched_at: datetime) -> None:
    """A value known before the first fetch (the spec's resolved numbers),
    stamped with when it was known rather than now."""
    with session() as s:
        _write_cache(s, page_id, key, payload=payload, status="seeded", error=None)
        row = s.execute(select(ConditionsCache).where(
            ConditionsCache.page_id == page_id, ConditionsCache.key == key)).scalar_one()
        row.fetched_at = fetched_at
        s.commit()


# --------------------------------------------------------------- refresh

def refresh_source(source: dict, *, force: bool = False) -> dict:
    """One scheduled pull. Returns {"key", "ok", "skipped"?, "error"?}."""
    now = _now()
    if not source.get("enabled"):
        return {"key": source["key"], "ok": True, "skipped": "disabled"}
    if not force and source.get("last_attempt"):
        due = source["last_attempt"] + timedelta(minutes=source["cadence_minutes"])
        if now < due:
            return {"key": source["key"], "ok": True, "skipped": "not due"}
    config = dict(source.get("config") or {})
    if source["adapter"] == "scrape":
        # The last good value guards the sanity check on the next extraction.
        cached = cache_for(source["page_id"]).get(source["key"]) or {}
        if cached.get("payload"):
            config["last_good"] = cached["payload"]
    try:
        payload = adapters.fetch(source["adapter"], config)
        ok, error = True, None
    except adapters.SourceError as exc:
        payload, ok, error = None, False, str(exc)
    except Exception as exc:  # noqa: BLE001 -- an adapter bug is a source error, recorded
        payload, ok, error = None, False, f"{type(exc).__name__}: {exc}"
    with session() as s:
        row = s.get(CamSource, source["id"])
        if row is not None:
            row.last_attempt = now
            if ok:
                row.last_success = now
                row.last_error = None
                row.error_count = 0
            else:
                row.last_error = error[:1000] if error else "failed"
                row.error_count = (row.error_count or 0) + 1
        status = "ok" if ok else "error"
        if ok and payload.get("seeded"):
            status = "seeded"
        _write_cache(s, source["page_id"], source["key"],
                     payload=payload if ok else None, status=status, error=error)
        s.commit()
    if not ok:
        log.warning("camhub: %s/%s failed: %s", source.get("page_id"), source["key"], error)
    return {"key": source["key"], "ok": ok, "error": error}


def refresh_page(slug: str, *, force: bool = True) -> dict:
    page = get_page(slug)
    if not page:
        raise LookupError(f"no cam page '{slug}'")
    results = [refresh_source(src, force=force) for src in list_sources(page["id"])]
    return {"slug": slug, "results": results,
            "ok": sum(1 for r in results if r["ok"] and not r.get("skipped")),
            "errors": [r for r in results if not r["ok"]]}


def refresh_due() -> dict:
    """Every enabled source on every page whose cadence has elapsed."""
    fetched, skipped, errors = 0, 0, []
    for src in list_sources(enabled_only=True):
        result = refresh_source(src)
        if result.get("skipped"):
            skipped += 1
        elif result["ok"]:
            fetched += 1
        else:
            errors.append({"page_id": src["page_id"], "key": src["key"], "error": result["error"]})
    return {"fetched": fetched, "skipped": skipped, "errors": len(errors), "failures": errors[:10]}


# ---------------------------------------------------------------- health

def health(page_id: int | None = None) -> list[dict]:
    """One row per source: green when its last success is inside the
    tolerance, amber when it is stale but still served, red when it has
    never succeeded or the tolerance has run out."""
    now = _now()
    cache = {}
    if page_id is not None:
        cache = cache_for(page_id)
    rows = []
    for src in list_sources(page_id):
        c = cache.get(src["key"]) if page_id is not None else (cache_for(src["page_id"]).get(src["key"]) or {})
        c = c or {}
        fetched = c.get("fetched_at")
        age_min = (now - fetched).total_seconds() / 60 if fetched else None
        if not src["enabled"]:
            state = "off"
        elif age_min is None:
            state = "red"
        elif age_min <= src["tolerance_minutes"]:
            state = "green" if not src["last_error"] else "amber"
        else:
            state = "red"
        rows.append({**src, "state": state, "fetched_at": fetched,
                     "age_minutes": round(age_min) if age_min is not None else None,
                     "cache_status": c.get("status"), "cache_error": c.get("error"),
                     "has_payload": bool(c.get("payload"))})
    return rows


def health_summary(page_id: int) -> str:
    states = [r["state"] for r in health(page_id) if r["state"] != "off"]
    if not states:
        return "red"
    if all(s == "green" for s in states):
        return "green"
    if any(s == "red" for s in states):
        return "red"
    return "amber"
