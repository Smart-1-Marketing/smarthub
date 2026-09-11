"""Weekly Search Console snapshot, SEO memory, and recommendation persistence."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json

from hub.extensions import db
from .models import SEOProperty, SEOSnapshot, SEOMemory, SEORecommendation
from . import google_search_console as gsc
from .recommendations import generate


def _loads(value, fallback):
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _dumps(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _week_start(day):
    return day - timedelta(days=day.weekday())


def _aggregate(rows):
    clicks = sum(float(r.get("clicks") or 0) for r in rows)
    impressions = sum(float(r.get("impressions") or 0) for r in rows)
    weighted = sum(float(r.get("position") or 0) * float(r.get("impressions") or 0) for r in rows)
    return {
        "clicks": round(clicks, 2),
        "impressions": round(impressions, 2),
        "ctr": round(clicks / impressions, 6) if impressions else 0,
        "position": round(weighted / impressions, 2) if impressions else 0,
    }


def _compact_rows(rows, dimension_index, limit=100):
    out = []
    for row in sorted(rows, key=lambda r: float(r.get("impressions") or 0), reverse=True):
        keys = row.get("keys") or []
        if len(keys) <= dimension_index:
            continue
        out.append({
            "value": keys[dimension_index],
            "query": keys[0] if keys else "",
            "page": keys[1] if len(keys) > 1 else "",
            "clicks": round(float(row.get("clicks") or 0), 2),
            "impressions": round(float(row.get("impressions") or 0), 2),
            "ctr": round(float(row.get("ctr") or 0), 6),
            "position": round(float(row.get("position") or 0), 2),
        })
        if len(out) >= limit:
            break
    return out


def _save_recommendations(prop, recommendations):
    now = datetime.now(timezone.utc)
    seen = set()
    for rec in recommendations:
        seen.add(rec["key"])
        item = SEORecommendation.query.filter_by(property_id=prop.id, recommendation_key=rec["key"]).first()
        if not item:
            item = SEORecommendation(
                client_id=prop.client_id,
                property_id=prop.id,
                recommendation_key=rec["key"],
                kind=rec["kind"],
                title=rec["title"],
            )
            db.session.add(item)
        item.page_url = rec.get("page_url")
        item.query = rec.get("query")
        item.priority_score = rec.get("priority_score", 0)
        item.impact = rec.get("impact", "medium")
        item.effort = rec.get("effort", "medium")
        item.evidence_json = _dumps(rec.get("evidence") or {})
        item.actions_json = _dumps(rec.get("actions") or [])
        item.last_seen_at = now
        if item.status == "auto_resolved":
            item.status = "open"
            item.resolved_at = None

    # If an evidence-driven condition disappeared, close it automatically but
    # never override a human dismissed/completed state.
    open_items = SEORecommendation.query.filter_by(property_id=prop.id, status="open").all()
    for item in open_items:
        if item.recommendation_key not in seen:
            item.status = "auto_resolved"
            item.resolved_at = now


def _build_memory(prop, rows, recommendations, current_metrics, previous_metrics):
    top = sorted(rows, key=lambda r: float(r.get("impressions") or 0), reverse=True)
    striking = []
    low_ctr = []
    questions = []
    for r in top:
        keys = r.get("keys") or []
        if len(keys) < 2:
            continue
        q, page = keys[0], keys[1]
        pos = float(r.get("position") or 100)
        imp = float(r.get("impressions") or 0)
        ctr = float(r.get("ctr") or 0)
        row = {"query": q, "page": page, "impressions": round(imp), "clicks": round(float(r.get("clicks") or 0)), "ctr": round(ctr, 4), "position": round(pos, 1)}
        if 4 <= pos <= 20 and imp >= 100 and len(striking) < 30:
            striking.append(row)
        if pos <= 10 and imp >= 250 and ctr < .03 and len(low_ctr) < 20:
            low_ctr.append(row)
        if q.lower().startswith(("how ", "what ", "why ", "when ", "where ", "can ", "does ", "do ", "is ", "are ", "should ")) and len(questions) < 30:
            questions.append(row)
    return {
        "version": 1,
        "client_id": prop.client_id,
        "site_url": prop.site_url,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "current_metrics": current_metrics,
        "previous_metrics": previous_metrics,
        "top_search_pairs": _compact_rows(top, 0, 60),
        "striking_distance": striking,
        "low_ctr": low_ctr,
        "questions": questions,
        "priority_opportunities": recommendations[:35],
        "guidance": {
            "evidence_first": True,
            "avoid_cannibalization": True,
            "titles_and_meta_should_use_real_queries": True,
            "schema_must_match_visible_page_content_and_google_guidelines": True,
            "do_not_claim_schema_itself_improves_rankings": True,
        },
    }


def refresh_property(prop, access_token, *, end_date=None, inspect_urls=None):
    """Refresh one SEO property's most recent complete 28-day window.

    The caller supplies an OAuth access token. Credential ownership remains in
    SmartHub's Google layer; this module deliberately does not persist tokens.
    """
    end = end_date or (date.today() - timedelta(days=3))
    start = end - timedelta(days=27)
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=27)
    week = _week_start(end)

    prop.last_sync_status = "running"
    prop.last_sync_error = None
    db.session.commit()
    try:
        rows = gsc.all_search_analytics(access_token, prop.site_url, start, end)
        previous_rows = gsc.all_search_analytics(access_token, prop.site_url, previous_start, previous_end)
        sitemaps = gsc.list_sitemaps(access_token, prop.site_url)
        inspections = []
        for url in (inspect_urls or [])[:50]:
            try:
                inspections.append({"url": url, "result": gsc.inspect_url(access_token, prop.site_url, url)})
            except Exception as exc:
                inspections.append({"url": url, "error": str(exc)})

        current_metrics = _aggregate(rows)
        previous_metrics = _aggregate(previous_rows)
        recs = generate(rows, previous_rows)

        snapshot = SEOSnapshot.query.filter_by(property_id=prop.id, week_start=week).first()
        if not snapshot:
            snapshot = SEOSnapshot(property_id=prop.id, week_start=week, period_start=start, period_end=end)
            db.session.add(snapshot)
        snapshot.period_start = start
        snapshot.period_end = end
        snapshot.metrics_json = _dumps({"current": current_metrics, "previous": previous_metrics})
        snapshot.queries_json = _dumps(_compact_rows(rows, 0, 250))
        snapshot.pages_json = _dumps(_compact_rows(rows, 1, 250))
        snapshot.sitemaps_json = _dumps(sitemaps)
        snapshot.inspections_json = _dumps(inspections)

        _save_recommendations(prop, recs)
        memory_payload = _build_memory(prop, rows, recs, current_metrics, previous_metrics)
        memory = SEOMemory.query.filter_by(client_id=prop.client_id).first()
        if not memory:
            memory = SEOMemory(client_id=prop.client_id)
            db.session.add(memory)
        memory.memory_json = _dumps(memory_payload)
        memory.source_week = week

        prop.last_sync_at = datetime.now(timezone.utc)
        prop.last_sync_status = "ok"
        db.session.commit()
        return {"property_id": prop.id, "site_url": prop.site_url, "week": str(week), "metrics": current_metrics, "recommendations": len(recs)}
    except Exception as exc:
        db.session.rollback()
        prop = db.session.get(SEOProperty, prop.id)
        prop.last_sync_at = datetime.now(timezone.utc)
        prop.last_sync_status = "error"
        prop.last_sync_error = str(exc)[:4000]
        db.session.commit()
        raise


def upsert_property(client_id, site_url, display_name=None):
    prop = SEOProperty.query.filter_by(site_url=site_url).first()
    if not prop:
        prop = SEOProperty(client_id=client_id, site_url=site_url)
        db.session.add(prop)
    prop.client_id = client_id
    prop.display_name = display_name or prop.display_name
    prop.active = True
    db.session.commit()
    return prop
