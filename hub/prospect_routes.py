"""The Prospect 360 record: /prospect/<lead id>.

A blueprint on the hub app, so it shares the Jinja environment and the client
APIs. Which means the login gate is **on the blueprint** — `wsgi.py`'s
AuthGuard wraps only dispatcher-mounted modules and the hub app guards its own
pages one view at a time, which is how Commercial Builder shipped forty
unguarded routes serving client names to anyone with the URL. Every route here
names a real person, their phone number and what we think is wrong with their
website; the next one added must not have to remember.

`hub/prospect.py` decides what a prospect record contains. This file is the
thin layer that turns it into a page and six writes.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request

bp = Blueprint("prospect_record", __name__)


@bp.before_request
def _require_login():
    from hub import access, current_user
    if current_user():
        return None
    if access.wants_json(request.path or "/", request.headers.get("Accept", "")):
        return jsonify({"ok": False,
                        "error": "Sign in to open a prospect record."}), 401
    return redirect("/login?next=" + (request.path or "/"))


def _actor() -> str:
    try:
        from hub import current_user
        return current_user() or ""
    except Exception:                                       # noqa: BLE001
        return ""


# -------------------------------------------------------------------- page
@bp.route("/prospect/<lead_id>")
def page_prospect(lead_id):
    from hub import current_user
    return render_template("prospect.html", user=current_user(),
                           active="leads", lead_id=lead_id)


# --------------------------------------------------------------------- API
@bp.route("/api/prospect/<lead_id>")
def api_prospect(lead_id):
    from hub import prospect
    return jsonify(prospect.record(lead_id))


@bp.route("/api/prospect/<lead_id>/note", methods=["POST"])
def api_note(lead_id):
    """Write a note onto the Suite contact behind this prospect.

    Deliberately not stored here. Notes belong with the conversation, and the
    conversation is in Smart 1 Suite — a second notebook only this Hub can
    read is how the person who picks the prospect up next misses what was
    said. A prospect with no Suite contact is **refused by name**: silently
    keeping the note locally would be exactly that second notebook.
    """
    from hub import leads, suite_opportunity as suite
    body = request.get_json(silent=True) or {}
    text = str(body.get("note") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "A note needs some words."}), 400
    lead = leads.get(lead_id)
    if lead is None:
        return jsonify({"ok": False, "error": "That prospect could not be "
                                              "found."}), 404
    contact_id = str(lead.get("contact_id") or "").strip()
    if not contact_id:
        return jsonify({
            "ok": False,
            "error": "This prospect has no Smart 1 Suite contact yet, so a "
                     "note has nowhere to go that anyone else would look. "
                     "Retry the delivery from the Leads panel first."}), 409
    try:
        note = suite.add_note(contact_id, text)
    except Exception as exc:                                # noqa: BLE001
        return jsonify({"ok": False,
                        "error": f"Smart 1 Suite would not take the note "
                                 f"({type(exc).__name__}: {exc})."[:300]}), 502
    return jsonify({"ok": True, "note": note,
                    "where": "Written onto the Suite contact, where the rest "
                             "of the history is."})


@bp.route("/api/prospect/<lead_id>/asset", methods=["POST"])
def api_add_asset(lead_id):
    from hub import prospect
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify({"ok": False, "error": "Choose a file."}), 400
    result = prospect.add_asset(lead_id, upload.filename, upload.read(),
                                label=request.form.get("label", ""),
                                actor=_actor())
    return jsonify(result), (200 if result.get("ok") else 400)


@bp.route("/api/prospect/<lead_id>/asset/<asset_id>", methods=["DELETE"])
def api_delete_asset(lead_id, asset_id):
    from hub import prospect
    result = prospect.delete_asset(lead_id, asset_id, actor=_actor())
    return jsonify(result), (200 if result.get("ok") else 404)


@bp.route("/api/prospect/<lead_id>/convert", methods=["POST"])
def api_convert(lead_id):
    from hub import prospect
    body = request.get_json(silent=True) or {}
    result = prospect.convert(lead_id, str(body.get("client") or ""),
                              actor=_actor())
    return jsonify(result), (200 if result.get("ok") else 400)


def register_prospect(app):
    app.register_blueprint(bp)
    return app
