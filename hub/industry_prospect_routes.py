"""Authenticated UI and JSON-only writes for the Industry Prospect Builder."""
from flask import Blueprint, current_app, jsonify, redirect, render_template, request
from sqlalchemy.exc import SQLAlchemyError

from hub import industry_prospects as service
from hub import industry_prospect_store as store

bp = Blueprint("industry_prospects", __name__)


@bp.before_request
def guard():
    from hub import current_user
    if not current_user():
        if request.path.startswith("/api/"):
            return jsonify(ok=False, error="Sign in to use the prospect builder."), 401
        return redirect("/login?next=/sales/industry-prospects")
    if request.method == "POST":
        if not request.is_json or request.headers.get("X-Prospect-Request") != "1":
            return jsonify(ok=False, error="Use the prospect builder to submit this action."), 403
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify(ok=False, error="Cross-site requests are not allowed."), 403
        if request.content_length and request.content_length > 32768:
            return jsonify(ok=False, error="Request is too large."), 413
        if not isinstance(request.get_json(silent=True), dict):
            return jsonify(ok=False, error="Expected a JSON object."), 400


@bp.after_request
def no_cache(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.errorhandler(store.ProspectError)
def problem(exc):
    return jsonify(ok=False, error=str(exc)), 409


@bp.errorhandler(SQLAlchemyError)
def database_problem(exc):
    current_app.logger.error("Prospect database operation failed: %s", type(exc).__name__)
    return jsonify(ok=False, error="The prospect database is unavailable. No uncertain paid operation will be retried automatically."), 503


@bp.route("/sales/industry-prospects")
def page():
    from hub import current_user
    return render_template("industry_prospects.html", user=current_user(), active="industry_prospects",
                           industries=service.INDUSTRIES)


@bp.route("/api/industry-prospects/status")
def status():
    return jsonify(ok=True, **service.status())


@bp.route("/api/industry-prospects/history/<cid>")
def history(cid):
    return jsonify(ok=True, rows=service.history(cid), jobs=service.purchase_jobs(cid))


@bp.route("/api/industry-prospects/action", methods=["POST"])
def action():
    from hub import current_user
    body = request.get_json()
    actor = current_user()
    name = body.get("action")
    allowed = {"sync_start", "sync_step", "sync_queue", "sync_resume", "connections", "create", "search", "quote", "approve", "buy", "purchase_queue", "purchase_stop", "import"}
    if not isinstance(name, str) or name not in allowed:
        return jsonify(ok=False, error="Unknown prospect action."), 400
    with store.operation(actor, name):
        if name == "purchase_queue":
            result = service.queue_purchase(str(body.get("plan") or ""), actor)
        elif name == "purchase_stop":
            result = service.pause_purchase(str(body.get("job") or ""), actor)
        elif name == "connections":
            result = service.test_connections(actor)
        elif name in {"sync_queue", "sync_resume"}:
            result = service.queue_sync(actor, resume=name == "sync_resume")
        elif name == "sync_start":
            result = service.start_sync(actor)
        elif name == "sync_step":
            result = service.sync_step()
        elif name == "create":
            result = service.new_campaign(body, actor)
        elif name == "search":
            result = service.search(str(body.get("campaign") or ""), service.integer(body, "page", 1, 1, 500))
        elif name == "quote":
            result = service.quote(str(body.get("campaign") or ""), body.get("ids"), actor)
        elif name == "approve":
            result = service.approve(str(body.get("plan") or ""), actor, body.get("confirmed"))
        elif name == "buy":
            result = service.buy_one(str(body.get("plan") or ""), str(body.get("person") or ""), actor)
        else:
            result = service.import_one(str(body.get("person") or ""), actor)
    try:
        from hub import audit
        audit.log("industry_prospects", name, actor=actor,
                  campaign=body.get("campaign") or result.get("campaign") or "",
                  status=result.get("status") or result.get("phase") or "ok")
    except Exception:
        current_app.logger.warning("Prospect action audit log unavailable")
    # Never return raw provider payloads or connection fingerprints to clients.
    return jsonify(ok=True, result={k: v for k, v in result.items() if k not in ("scope", "person", "last_ids")})


def register_industry_prospects(app):
    # A database outage must not stop the rest of SmartHub from booting.
    try:
        store.init_store()
    except store.ProspectError:
        app.logger.exception("Industry prospect database initialization failed")
    app.register_blueprint(bp)
