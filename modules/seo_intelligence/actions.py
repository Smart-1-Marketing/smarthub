"""Safe Search Console actions exposed through the SEO Action Center."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from hub.extensions import db
from . import google_search_console as gsc
from .activity import log as _log
from .models import SEOAction, SEOProperty
from .tokens import token_for_property

bp = Blueprint("seo_intelligence_actions", __name__, url_prefix="/seo/intelligence")


def _property(property_id):
    return db.session.get(SEOProperty, property_id)


def _actor(body):
    """Whoever is signed in, and the posted name only where nobody is.

    The Hub session is the answer a browser cannot put in a POST body, so it
    wins; the `actor` field these routes already accept stays as the fallback
    for a caller outside the Hub.
    """
    try:
        from hub import current_user
        who = current_user()
        if who:
            return str(who)[:60]
    except Exception:                                 # noqa: BLE001
        pass
    return str(body.get("actor") or "")[:60] or None


@bp.post("/api/properties/<int:property_id>/sitemaps")
def submit_sitemap(property_id):
    prop = _property(property_id)
    if not prop:
        return jsonify({"error": "SEO property not found"}), 404
    body = request.get_json(silent=True) or {}
    sitemap_url = str(body.get("sitemap_url") or "").strip()
    if not sitemap_url:
        return jsonify({"error": "sitemap_url is required"}), 400
    try:
        token = token_for_property(prop)
        gsc.submit_sitemap(token, prop.site_url, sitemap_url)
        _log("sitemap_submitted", actor=_actor(body),
             client=prop.display_name or prop.client_id,
             site=prop.site_url, sitemap=sitemap_url)
        db.session.add(SEOAction(
            client_id=prop.client_id,
            action_type="submit_sitemap",
            page_url=sitemap_url,
            after_json='{"submitted":true}',
            actor=str(body.get("actor") or "")[:255] or None,
        ))
        db.session.commit()
        return jsonify({"ok": True, "site_url": prop.site_url, "sitemap_url": sitemap_url})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 502


@bp.delete("/api/properties/<int:property_id>/sitemaps")
def delete_sitemap(property_id):
    prop = _property(property_id)
    if not prop:
        return jsonify({"error": "SEO property not found"}), 404
    body = request.get_json(silent=True) or {}
    sitemap_url = str(body.get("sitemap_url") or "").strip()
    if not sitemap_url:
        return jsonify({"error": "sitemap_url is required"}), 400
    try:
        token = token_for_property(prop)
        gsc.delete_sitemap(token, prop.site_url, sitemap_url)
        _log("sitemap_deleted", actor=_actor(body),
             client=prop.display_name or prop.client_id,
             site=prop.site_url, sitemap=sitemap_url)
        db.session.add(SEOAction(
            client_id=prop.client_id,
            action_type="delete_sitemap",
            page_url=sitemap_url,
            before_json='{"submitted":true}',
            after_json='{"submitted":false}',
            actor=str(body.get("actor") or "")[:255] or None,
        ))
        db.session.commit()
        return jsonify({"ok": True, "site_url": prop.site_url, "sitemap_url": sitemap_url})
    except Exception as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 502


@bp.post("/api/properties/<int:property_id>/inspect")
def inspect_url(property_id):
    prop = _property(property_id)
    if not prop:
        return jsonify({"error": "SEO property not found"}), 404
    body = request.get_json(silent=True) or {}
    url = str(body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400
    try:
        token = token_for_property(prop)
        result = gsc.inspect_url(token, prop.site_url, url)
        return jsonify({"ok": True, "site_url": prop.site_url, "url": url, "inspection": result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502
