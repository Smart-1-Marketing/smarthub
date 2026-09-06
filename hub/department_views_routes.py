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


def _my_department() -> dict | None:
    email, _name, _admin = _who()
    dept_id = dv.assignment_for(email)
    return dv.get_department(dept_id) if dept_id else None


# --------------------------------------------------------------------- pages
@bp.route("/views")
def page_my_view():
    _email, name, is_admin = _who()
    dept = _my_department()
    return render_template("department_view.html", user=name, active="deptviews",
                           department=dept, is_admin=is_admin, is_mine=True)


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
    mine = _my_department()
    return render_template("department_view.html", user=name, active="deptviews",
                           department=dept, is_admin=is_admin,
                           is_mine=bool(mine and mine.get("id") == dept.get("id")))


# ---------------------------------------------------------------------- API
@bp.route("/api/department-views/mine")
def api_mine():
    email, _name, is_admin = _who()
    dept = _my_department()
    return jsonify({"ok": True, "department": dept, "is_admin": is_admin,
                    "email": email})


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
    email, _name, _admin = _who()
    body = request.get_json(silent=True) or request.form or {}
    try:
        dv.set_assignment(str(body.get("email") or ""), body.get("department_id"),
                          actor_email=email)
    except dv.DepartmentViewError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True})


def register_department_views(app):
    app.register_blueprint(bp)
    return app
