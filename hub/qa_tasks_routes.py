"""The QA Tasks screen and its API: /qa-tasks.

A blueprint on the hub app, so it shares the Jinja environment, the shared
database instance and the account table. Which means the login gate is **on
the blueprint** — `wsgi.py`'s AuthGuard wraps only dispatcher-mounted modules
and the hub app guards its own pages one view at a time, which is how the
Commercial Builder shipped forty unguarded routes. `hub/blueprint_guard.py`
is that gate, once, and a route added here next month is covered without
anybody remembering.

Nothing on this blueprint is public. There is no client-facing surface here
at all: every route names a member of staff, what they were asked to check
and what they said about it.

`hub/qa_tasks.py` decides what a task is and what may happen to it. This file
is the thin layer that turns it into a page, seven writes and a file download.
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, render_template, request

from hub.blueprint_guard import install as install_guard

# Imported for its side effect as well as its use: the two models have to
# exist before the `create_all()` at the end of `create_hub_app()`, or
# `hub_qa_tasks` and `hub_qa_responses` are never created and every read of
# them fails into "the QA task list could not be read" for ever. That is the
# note `hub/presence.py` carries, and registering this blueprint is the only
# thing that imports this module -- so the import lives here rather than at
# the registration, where the next person to move the call would drop it.
from hub import qa_tasks  # noqa: F401

bp = Blueprint("qa_tasks", __name__)
install_guard(bp, mount="/qa-tasks")


def _who() -> tuple[str, str]:
    """(email, display name) for the person making this request.

    The email is the identity everything here is keyed on, and a
    shared-password session has none — `hub/access.py` says why that session
    exists and `hub/users.py` says why it has no account row. So it comes back
    blank and every reader says, in words, that there is nobody to show tasks
    for. Guessing an identity for a shared login would file one person's
    review under another's name.
    """
    email = ""
    try:
        from hub.users_routes import current_account
        account = current_account()
        if account is not None:
            return (account.email or "").lower(), (account.name or account.email or "")
    except Exception:                                   # noqa: BLE001
        pass
    try:
        from hub import current_user
        return email, (current_user() or "")
    except Exception:                                   # noqa: BLE001
        return email, ""


def _no_account():
    return jsonify({
        "ok": False,
        "error": "This session is signed in with the shared password, which "
                 "has no account behind it. Sign in with your own Hub account "
                 "to raise or answer a QA task."}), 403


# -------------------------------------------------------------------- page
@bp.route("/qa-tasks")
def page_qa_tasks():
    _email, name = _who()
    return render_template("qa_tasks.html", user=name, active="qatasks")


@bp.route("/qa-tasks/<int:task_id>")
def page_qa_task(task_id):
    """One task, deep-linkable.

    The same page: the list and the detail are one screen with the detail
    opened, rather than two templates that would each need the reply box
    written into them. The id rides in as a variable the script reads.
    """
    _email, name = _who()
    return render_template("qa_tasks.html", user=name, active="qatasks",
                           open_task=task_id)


# --------------------------------------------------------------------- API
@bp.route("/api/qa-tasks/new")
def api_new_task_form():
    """What the "assign a review" form needs to draw itself.

    One request rather than three: the dropdown of pages, the list of people,
    and today's date for the need-by field, which is drawn server-side so the
    form and the stamp on the row agree about what day it is.
    """
    import datetime as _dt
    people = qa_tasks.assignable()
    return jsonify({
        "targets": qa_tasks.targets(),
        "people": people["people"],
        "people_error": people["error"],
        "today": _dt.date.today().isoformat(),
        "max_attachment_mb": qa_tasks.MAX_ATTACHMENT_BYTES // (1024 * 1024),
    })


@bp.route("/api/qa-tasks")
def api_my_tasks():
    """Everything on this person's plate, both directions."""
    email, _name = _who()
    return jsonify(qa_tasks.for_person(email))


@bp.route("/api/qa-tasks/board")
def api_board():
    """Every open task, whoever raised it — the whole-team view."""
    return jsonify(qa_tasks.board())


@bp.route("/api/qa-tasks/summary")
def api_summary():
    """The count the dashboard card and the sign-in reminder both read.

    Deliberately the same function the page reads, cut down: two screens
    counting the same thing separately is how they come to disagree in front
    of the same person.
    """
    email, _name = _who()
    data = qa_tasks.for_person(email, limit=50)
    return jsonify({
        "measured": data["measured"], "error": data["error"],
        "counts": data["counts"], "line": data["line"],
        "url": "/qa-tasks",
        # The reminder keys its once-a-day marker on this, so signing in as
        # somebody else does not inherit the answer.
        "email": email,
        # A short list under the numbers, so the card answers "which ones"
        # without a click. Both queues, newest movement first.
        "rows": (data["to_do"] + data["waiting_on_you"])[:6],
    })


@bp.route("/api/qa-tasks/<int:task_id>")
def api_task(task_id):
    email, _name = _who()
    task = qa_tasks.get(task_id, viewer_email=email)
    if task is None:
        return jsonify({"ok": False, "error": "That task could not be found."}), 404
    # Opening one task is what marks it read — never loading the list. A badge
    # that clears itself because somebody glanced at a dashboard is a badge
    # that stops meaning anything.
    if email:
        qa_tasks.mark_seen(task_id, actor_email=email)
    return jsonify({"ok": True, "task": task})


@bp.route("/api/qa-tasks", methods=["POST"])
def api_create():
    email, name = _who()
    if not email:
        return _no_account()
    body = request.get_json(silent=True) or request.form or {}
    try:
        task = qa_tasks.create(
            target_key=str(body.get("target") or ""),
            target_other=str(body.get("target_other") or ""),
            instructions=str(body.get("instructions") or ""),
            assigned_to_email=str(body.get("assigned_to") or ""),
            due_on=str(body.get("due_on") or ""),
            actor_email=email, actor_name=name)
    except qa_tasks.QaTaskError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:                            # noqa: BLE001
        # A sentence, not the exception: a SQLAlchemy OperationalError carries
        # the database host and the SQL, and this reply is drawn straight into
        # the form. The cause goes to the error log, which is where /status
        # reads it from.
        try:
            from hub import errors
            errors.log_exception("qa_tasks", exc, path=request.path, actor=email)
        except Exception:                               # noqa: BLE001
            pass
        return jsonify({"ok": False,
                        "error": "That could not be saved, so nothing has "
                                 "been assigned. Try again in a moment."}), 500
    return jsonify({"ok": True, "task": task.as_dict(viewer_email=email),
                    "where": f"Assigned to {task.assigned_to_name}. It is on "
                             f"their dashboard now."})


@bp.route("/api/qa-tasks/<int:task_id>/respond", methods=["POST"])
def api_respond(task_id):
    """An answer, or a request for more information. See `qa_tasks.respond`
    for why which one it is comes from who is posting rather than from a flag
    the form sends.

    Multipart, because half of these carry a file. A JSON body is accepted too
    so the text-only case does not need a FormData wrapper.
    """
    email, name = _who()
    if not email:
        return _no_account()

    upload = request.files.get("file")
    file_name = file_type = ""
    file_bytes = None
    if upload is not None and upload.filename:
        file_name = upload.filename
        file_type = upload.mimetype or ""
        file_bytes = upload.read()

    if request.files:
        body = request.form.get("body", "")
    else:
        payload = request.get_json(silent=True) or request.form or {}
        body = str(payload.get("body") or "")

    try:
        post = qa_tasks.respond(task_id, body=body, actor_email=email,
                               actor_name=name, file_name=file_name,
                               file_type=file_type, file_bytes=file_bytes)
    except qa_tasks.QaTaskError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "response": post.as_dict(),
                    "task": qa_tasks.get(task_id, viewer_email=email)})


@bp.route("/api/qa-tasks/<int:task_id>/complete", methods=["POST"])
def api_complete(task_id):
    email, _name = _who()
    if not email:
        return _no_account()
    try:
        task = qa_tasks.complete(task_id, actor_email=email)
    except qa_tasks.QaTaskError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "task": task.as_dict(viewer_email=email)})


@bp.route("/api/qa-tasks/<int:task_id>/reopen", methods=["POST"])
def api_reopen(task_id):
    email, _name = _who()
    if not email:
        return _no_account()
    try:
        task = qa_tasks.reopen(task_id, actor_email=email)
    except qa_tasks.QaTaskError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "task": task.as_dict(viewer_email=email)})


@bp.route("/qa-tasks/attachment/<int:response_id>")
def download_attachment(response_id):
    """Stream one attachment back.

    `Content-Disposition: attachment` rather than inline: these are files
    somebody uploaded, and rendering an arbitrary upload inside the Hub's own
    origin is how a stored file becomes a script running on the staff domain.
    """
    found = qa_tasks.attachment(response_id)
    if found is None:
        return ("That file could not be found.", 404)
    data, name, mimetype = found
    safe = "".join(c for c in name if c.isalnum() or c in "._- ").strip() or "attachment"
    return Response(data, mimetype=mimetype, headers={
        "Content-Disposition": f'attachment; filename="{safe}"',
        "Content-Length": str(len(data)),
        "X-Content-Type-Options": "nosniff",
    })


def register_qa_tasks(app):
    app.register_blueprint(bp)
    return app
