"""Authenticated Smart 1 Sites Builder routes under Client Tools."""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request

bp = Blueprint("sites_builder_tool", __name__)


@bp.before_request
def _require_login():
    from hub import access, current_user
    if current_user():
        return None
    if access.wants_json(request.path or "/", request.headers.get("Accept", "")):
        return jsonify({"ok": False, "error": "Sign in to build a website preview."}), 401
    return redirect("/login?next=" + (request.path or "/"))


@bp.route("/tools/sites-builder")
def page():
    from hub import current_user
    return render_template("sites_builder.html", user=current_user(),
                           active="tools")


@bp.route("/api/sites-builder/preview", methods=["POST"])
def preview():
    from hub import audit
    from hub.sites_builder import build_preview
    raw = request.get_json(silent=True) or {}
    intake = {
        "business_name": str(raw.get("business_name") or "").strip()[:100],
        "business_type": str(raw.get("business_type") or "").strip()[:100],
        "city": str(raw.get("city") or "").strip()[:100],
        "goal": str(raw.get("goal") or "leads").strip()[:30],
        "description": str(raw.get("description") or "").strip()[:600],
    }
    if not intake["business_name"] or not intake["business_type"]:
        return jsonify({"ok": False,
                        "error": "Add the business name and what the business does."}), 400
    result = build_preview(intake)
    audit.log("sites_builder", "preview_built",
              business=intake["business_name"],
              themes=len(result.get("themes") or []))
    return jsonify({"ok": True, **result})


def register(app):
    app.register_blueprint(bp)
    return app
