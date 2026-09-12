"""Admin-only AI model review. No endpoint activates a production model."""
from flask import Blueprint, jsonify, render_template, request
from hub import ai_models, ai_comparisons

bp = Blueprint("ai_model_review", __name__)


@bp.before_request
def guard():
    from hub.users_routes import _require_admin_api
    _, error = _require_admin_api()
    if error:
        return error
    if request.method == "POST" and (request.content_length is None or request.content_length > 12 * 1024 * 1024):
        return jsonify(error="Send a request smaller than 12 MB with a content length."), 413
    if request.method == "POST" and not request.is_json:
        return jsonify(error="Send application/json."), 415
    if request.method == "POST" and request.headers.get("Origin"):
        if request.headers["Origin"].rstrip("/") != request.host_url.rstrip("/"):
            return jsonify(error="Cross-origin changes are not allowed."), 403


@bp.get("/diagnostics/ai-models")
def page():
    from hub.users_routes import current_account
    return render_template("ai_models.html", active="diagnostics", user=current_account().email,
                           review=ai_models.inventory(), briefs=ai_comparisons.BRIEFS,
                           comparisons=ai_comparisons.history())


@bp.post("/api/diagnostics/ai-models/refresh")
def refresh():
    try:
        return jsonify(ai_models.refresh_catalog())
    except ValueError as exc:
        return jsonify(error=str(exc)), 400


@bp.post("/api/diagnostics/ai-models/reviews")
def review():
    from hub.users_routes import current_account
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="Expected an object."), 400
    try:
        return jsonify(ai_models.record_review(body.get("profile"), body.get("candidate"),
                       body.get("decision"), body.get("evidence"), current_account().email)), 201
    except (ValueError, TypeError) as exc:
        return jsonify(error=str(exc)), 400


@bp.post("/api/diagnostics/ai-models/comparisons")
def comparison():
    from hub.users_routes import current_account
    try:
        row = ai_comparisons.record(request.get_json(silent=True), current_account().email)
        return jsonify(row), 201
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
