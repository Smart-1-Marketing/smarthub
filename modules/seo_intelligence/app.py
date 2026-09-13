"""SmartHub SEO Overview + internal action center."""
from __future__ import annotations

from datetime import datetime, timezone
import json

from flask import Blueprint, current_app, jsonify, request

from hub.extensions import db
from .activity import log as _log
from .context import get_seo_context
from .file_store import mirror_client
from .models import SEOAction, SEOMemory, SEOProperty, SEORecommendation, SEOSnapshot
from .service import refresh_property, upsert_property

bp = Blueprint("seo_intelligence", __name__, url_prefix="/seo/intelligence")


def _actor():
    """The signed-in name, or nothing. Never the value a POST body claims."""
    try:
        from hub import current_user
        return current_user() or None
    except Exception:                                   # noqa: BLE001
        return None


def _loads(raw, fallback):
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def _property_dict(p):
    return {
        "id": p.id, "client_id": p.client_id, "site_url": p.site_url,
        "display_name": p.display_name, "active": p.active,
        "last_sync_at": p.last_sync_at.isoformat() if p.last_sync_at else None,
        "last_sync_status": p.last_sync_status, "last_sync_error": p.last_sync_error,
    }


def _recommendation_dict(r):
    return {
        "id": r.id, "client_id": r.client_id, "property_id": r.property_id,
        "kind": r.kind, "title": r.title, "page_url": r.page_url, "query": r.query,
        "priority_score": r.priority_score, "impact": r.impact, "effort": r.effort,
        "status": r.status, "evidence": _loads(r.evidence_json, {}),
        "actions": _loads(r.actions_json, []),
        "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
        "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
    }


def _token_for(prop):
    """Resolve a short-lived access token without storing it in this module."""
    provider = current_app.config.get("SEO_GSC_TOKEN_PROVIDER")
    if callable(provider):
        return provider(prop)
    token = current_app.config.get("SEO_GSC_ACCESS_TOKEN")
    if token:
        return token
    return None


@bp.get("/api/clients/<path:client_id>/overview")
def client_overview(client_id):
    properties = SEOProperty.query.filter_by(client_id=client_id, active=True).all()
    memory_row = SEOMemory.query.filter_by(client_id=client_id).first()
    recommendations = (SEORecommendation.query.filter_by(client_id=client_id, status="open")
                       .order_by(SEORecommendation.priority_score.desc()).limit(100).all())
    memory = _loads(memory_row.memory_json, {}) if memory_row else {}
    kinds = {}
    for r in recommendations:
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    score_penalty = sum(max(1, r.priority_score / 25) for r in recommendations[:20])
    health = max(0, min(100, round(100 - score_penalty))) if memory else None
    return jsonify({
        "client_id": client_id,
        "seo_health_score": health,
        "properties": [_property_dict(p) for p in properties],
        "metrics": memory.get("current_metrics") or {},
        "previous_metrics": memory.get("previous_metrics") or {},
        "source_week": str(memory_row.source_week) if memory_row and memory_row.source_week else None,
        "open_opportunities": len(recommendations),
        "opportunity_types": kinds,
        "top_opportunities": [_recommendation_dict(r) for r in recommendations[:12]],
    })


@bp.get("/api/action-queue")
def action_queue():
    status = request.args.get("status", "open")
    kind = request.args.get("kind")
    q = SEORecommendation.query.filter_by(status=status)
    if kind:
        q = q.filter_by(kind=kind)
    rows = q.order_by(SEORecommendation.priority_score.desc(), SEORecommendation.last_seen_at.desc()).limit(500).all()
    return jsonify({"count": len(rows), "items": [_recommendation_dict(r) for r in rows]})


@bp.post("/api/properties")
def register_property():
    body = request.get_json(silent=True) or {}
    client_id = str(body.get("client_id") or "").strip()
    site_url = str(body.get("site_url") or "").strip()
    if not client_id or not site_url:
        return jsonify({"error": "client_id and site_url are required"}), 400
    prop = upsert_property(client_id, site_url, body.get("display_name"))
    # Recorded because this is the join: every snapshot, recommendation and
    # sitemap write below is filed against whichever client this row names, so
    # a property pointed at the wrong one is wrong everywhere and silently.
    _log("property_registered", actor=_actor(),
         client=prop.display_name or prop.client_id, site=prop.site_url)
    return jsonify(_property_dict(prop)), 201


@bp.post("/api/properties/<int:property_id>/refresh")
def refresh_now(property_id):
    prop = db.session.get(SEOProperty, property_id)
    if not prop:
        return jsonify({"error": "SEO property not found"}), 404
    token = _token_for(prop)
    if not token:
        return jsonify({"error": "Search Console credential is not connected to the SEO token provider"}), 409
    body = request.get_json(silent=True) or {}
    try:
        result = refresh_property(prop, token, inspect_urls=body.get("inspect_urls") or [])
        result["intelligence_file"] = bool(mirror_client(prop.client_id))
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@bp.get("/api/clients/<path:client_id>/context")
def ai_context(client_id):
    return jsonify(get_seo_context(
        client_id,
        capability=request.args.get("capability", "general"),
        page_url=request.args.get("page_url"),
        topic=request.args.get("topic"),
    ))


@bp.post("/api/recommendations/<int:recommendation_id>/status")
def recommendation_status(recommendation_id):
    rec = db.session.get(SEORecommendation, recommendation_id)
    if not rec:
        return jsonify({"error": "Recommendation not found"}), 404
    body = request.get_json(silent=True) or {}
    status = str(body.get("status") or "").strip().lower()
    if status not in {"open", "reviewing", "task_created", "dismissed", "completed"}:
        return jsonify({"error": "Invalid status"}), 400
    rec.status = status
    if status in {"dismissed", "completed"}:
        rec.resolved_at = datetime.now(timezone.utc)
    else:
        rec.resolved_at = None
    if body.get("action_type"):
        db.session.add(SEOAction(
            client_id=rec.client_id,
            recommendation_id=rec.id,
            action_type=str(body.get("action_type"))[:80],
            page_url=rec.page_url,
            before_json=json.dumps(body.get("before") or {}),
            after_json=json.dumps(body.get("after") or {}),
            external_ref=str(body.get("external_ref") or "")[:500] or None,
            actor=str(body.get("actor") or "")[:255] or None,
        ))
    db.session.commit()
    return jsonify(_recommendation_dict(rec))


@bp.get("/api/properties/<int:property_id>/history")
def history(property_id):
    rows = (SEOSnapshot.query.filter_by(property_id=property_id)
            .order_by(SEOSnapshot.week_start.desc()).limit(104).all())
    return jsonify({"items": [{
        "week_start": str(r.week_start), "period_start": str(r.period_start), "period_end": str(r.period_end),
        "metrics": _loads(r.metrics_json, {}), "sitemaps": _loads(r.sitemaps_json, []),
        "inspections": _loads(r.inspections_json, []),
    } for r in rows]})
