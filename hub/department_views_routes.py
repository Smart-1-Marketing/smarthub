"""The Department Views screens: /views and its admin editor at /views/manage.

A blueprint on the hub app, guarded once by `hub/blueprint_guard.py` rather
than view by view — the failure that guard exists to stop is named at length
in its own docstring, and the fastest way to repeat it here would be to add a
route next month and forget the check. The admin half is gated a second way,
through `hub/access.py`'s `UTILITY_PREFIXES` (`/views/manage` and
`/api/department-views/admin`), because editing what a department sees is a
Utilities-shaped action and General Access reaches everything in the Hub
*except* Utilities.

`hub/department_views.py` owns every decision about what a view is and what
may happen to it. This file is the thin layer that turns that into pages and
a handful of writes.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from hub import department_views as dv
from hub.blueprint_guard import install as install_guard

bp = Blueprint("department_views", __name__)
install_guard(bp, mount="/views")


def _who() -> tuple[str, str, bool]:
    """(email, display name, is_admin). A shared-password session has no
    account behind it — `hub/access.py` says why that session exists — so it
    comes back with no email and `is_admin=True` (the same "shared password
    counts as Admin" decision that module makes, applied here rather than a
    second reading of it)."""
    try:
        from hub.users_routes import current_account
        account = current_account()
        if account is not None:
            return (account.email or "").lower(), (account.name or account.email or ""), bool(account.is_admin)
    except Exception:                                       # noqa: BLE001
        pass
    try:
        from hub import current_user
        return "", (current_user() or ""), True
    except Exception:                                       # noqa: BLE001
        return "", "", True


def _my_departments() -> list[dict]:
    """Every department this signed-in person is on, in display order —
    reading `list_departments()` filtered to their assigned ids rather than
    building a second sorted list, so the picker and the admin roster agree
    on how departments are ordered."""
    email, _name, _admin = _who()
    ids = set(dv.assignments_for(email))
    if not ids:
        return []
    return [d for d in dv.list_departments() if d.get("id") in ids]


def _selected(mine: list[dict], requested_id: str) -> dict | None:
    """Which of a person's own departments to show. The request may name one
    (the picker's own links do) — honoured only when it is actually one of
    theirs, or a stale/tampered `?dept=` would show somebody a colleague's
    view under the "My View" heading. Falls back to the first, alphabetically,
    which is `mine`'s own order."""
    if not mine:
        return None
    by_id = {d["id"]: d for d in mine}
    return by_id.get(requested_id) or mine[0]


# --------------------------------------------------------------------- pages
@bp.route("/views")
def page_my_view():
    _email, name, is_admin = _who()
    mine = _my_departments()
    dept = _selected(mine, request.args.get("dept") or "")
    return render_template("department_view.html", user=name, active="deptviews",
                           department=dept, is_admin=is_admin, is_mine=True,
                           my_departments=mine)


@bp.route("/views/manage")
def page_manage():
    _email, name, is_admin = _who()
    return render_template("department_views_manage.html", user=name,
                           active="deptviews_manage", is_admin=is_admin)


@bp.route("/views/<dept_id>")
def page_view_department(dept_id):
    dept = dv.get_department(dept_id)
    if dept is None:
        return ("That department view could not be found.", 404)
    _email, name, is_admin = _who()
    mine = _my_departments()
    is_mine = any(d.get("id") == dept.get("id") for d in mine)
    return render_template("department_view.html", user=name, active="deptviews",
                           department=dept, is_admin=is_admin,
                           is_mine=is_mine, my_departments=mine)


# ---------------------------------------------------------------------- API
@bp.route("/api/department-views/mine")
def api_mine():
    email, _name, is_admin = _who()
    mine = _my_departments()
    dept = _selected(mine, request.args.get("dept") or "")
    return jsonify({"ok": True, "departments": mine, "department": dept,
                    "is_admin": is_admin, "email": email})


@bp.route("/api/department-views/admin/list")
def api_admin_list():
    return jsonify({"ok": True, "departments": dv.list_departments(),
                    "counts": dv.assignment_counts()})


@bp.route("/api/department-views/admin/catalog")
def api_admin_catalog():
    return jsonify({"ok": True, "groups": dv.catalog()})


@bp.route("/api/department-views/admin/roster")
def api_admin_roster():
    rows, error = dv.roster()
    return jsonify({"ok": not error, "people": rows, "error": error,
                    "departments": dv.list_departments()})


@bp.route("/api/department-views/admin/departments", methods=["POST"])
def api_admin_create():
    email, _name, _admin = _who()
    body = request.get_json(silent=True) or request.form or {}
    try:
        dept = dv.create_department(str(body.get("name") or ""),
                                    str(body.get("description") or ""),
                                    actor_email=email)
    except dv.DepartmentViewError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "department": dept})


@bp.route("/api/department-views/admin/departments/<dept_id>", methods=["POST"])
def api_admin_update(dept_id):
    email, _name, _admin = _who()
    body = request.get_json(silent=True) or request.form or {}
    try:
        dept = dv.update_department(
            dept_id,
            name=body.get("name") if "name" in body else None,
            description=body.get("description") if "description" in body else None,
            actor_email=email)
    except dv.DepartmentViewError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "department": dept})


@bp.route("/api/department-views/admin/departments/<dept_id>/delete", methods=["POST"])
def api_admin_delete(dept_id):
    email, _name, _admin = _who()
    try:
        unassigned = dv.delete_department(dept_id, actor_email=email)
    except dv.DepartmentViewError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "unassigned": unassigned})


@bp.route("/api/department-views/admin/departments/<dept_id>/blocks", methods=["POST"])
def api_admin_save_blocks(dept_id):
    email, _name, _admin = _who()
    body = request.get_json(silent=True) or {}
    try:
        blocks, dropped = dv.save_blocks(dept_id, body.get("blocks") or [],
                                         actor_email=email)
    except dv.DepartmentViewError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "blocks": blocks, "dropped": dropped})


@bp.route("/api/department-views/admin/assignments", methods=["POST"])
def api_admin_assign():
    """Put one person on one department's view, or take them off it —
    `on` says which. This route (under `/api/department-views/admin`, so
    `hub/access.py`'s Utilities gate covers it) is the only place an
    assignment is ever written; My View's own picker only ever reads."""
    email, _name, _admin = _who()
    body = request.get_json(silent=True) or request.form or {}
    on = body.get("on")
    try:
        department_ids = dv.set_assignment(
            str(body.get("email") or ""), str(body.get("department_id") or ""),
            on=True if on is None else bool(on), actor_email=email)
    except dv.DepartmentViewError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "department_ids": department_ids})


def register_department_views(app):
    app.register_blueprint(bp)
    return app
