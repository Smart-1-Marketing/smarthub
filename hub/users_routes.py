"""Routes for user accounts: sign-up, sign-in, reset, and the admin panel.

Mounted from the Hub factory via ``register_users(app)``. The admin panel lives
at /diagnostics/users so account management sits with the other operational
tooling rather than becoming its own top-level section.

The pages that must be reachable without a session — sign-up, sign-in and
completing a reset — are the only public ones. Everything else requires an
active account, and the admin routes additionally require an admin role checked
server-side on every request, not just hidden in the UI.
"""
from __future__ import annotations

from flask import (Blueprint, jsonify, redirect, render_template, request,
                   make_response)

from hub import audit, users
from hub.users import User, UserError

bp = Blueprint("users_bp", __name__)

COOKIE_NAME = "s1hub_user"
SESSION_DAYS = 14


def _serializer():
    import os
    from itsdangerous import URLSafeTimedSerializer
    secret = os.environ.get("SECRET_KEY") or os.environ.get("SESSION_SECRET") or ""
    return URLSafeTimedSerializer(secret or "dev-only", salt="s1hub-user")


def issue_cookie(user: User) -> str:
    # The session epoch travels in the cookie: bump it on the user row and
    # every existing session for that account stops validating immediately.
    return _serializer().dumps({"u": user.id, "e": user.email,
                                "r": user.role, "s": user.session_epoch or 1})


def current_account() -> User | None:
    """The signed-in account, or None. Re-reads the row every request.

    Deliberately not cached in the session: a suspension or role change must
    take effect on the next click, not whenever the cookie happens to expire.
    """
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    try:
        data = _serializer().loads(raw, max_age=SESSION_DAYS * 86400)
    except Exception:                                   # noqa: BLE001
        return None
    user = User.query.get(data.get("u"))
    if not user or not user.can_log_in:
        return None
    if (user.session_epoch or 1) != data.get("s"):
        return None                                     # password changed
    return user


def _login_response(user: User, nxt: str = "/"):
    import os
    if not nxt.startswith("/"):
        nxt = "/"
    resp = make_response(redirect(nxt))
    secure = os.environ.get("FLASK_ENV") == "production" or \
        os.environ.get("NODE_ENV") == "production"
    resp.set_cookie(COOKIE_NAME, issue_cookie(user), max_age=SESSION_DAYS * 86400,
                    httponly=True, samesite="Lax", secure=secure)
    # Also set the legacy cookie so every existing @requires_login check in the
    # Hub keeps working untouched while both systems coexist.
    try:
        from hub import auth
        resp.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value(user.name or user.email),
                        max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="Lax", secure=secure)
    except Exception:                                   # noqa: BLE001
        pass
    return resp


def _require_account():
    user = current_account()
    if not user:
        return None, redirect("/signin?next=" + request.path)
    return user, None


def _require_admin_api():
    user = current_account()
    if not user:
        return None, (jsonify({"error": "Not signed in."}), 401)
    if not user.is_admin:
        return None, (jsonify({"error": "Admins only."}), 403)
    return user, None


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("users_signup.html",
                               domains=sorted(users.allowed_domains()),
                               min_len=users.MIN_PASSWORD_LEN)
    try:
        users.sign_up(request.form.get("email", ""),
                      request.form.get("name", ""),
                      request.form.get("password", ""))
    except UserError as exc:
        if str(exc) == "__EXISTS__":
            # Same wording as success: whether an address is already registered
            # is not something an anonymous visitor should be able to discover.
            return render_template("users_signup.html", submitted=True,
                                   domains=sorted(users.allowed_domains()),
                                   min_len=users.MIN_PASSWORD_LEN)
        return render_template("users_signup.html", error=str(exc),
                               domains=sorted(users.allowed_domains()),
                               min_len=users.MIN_PASSWORD_LEN), 400
    return render_template("users_signup.html", submitted=True,
                           domains=sorted(users.allowed_domains()),
                           min_len=users.MIN_PASSWORD_LEN)


@bp.route("/signin", methods=["GET", "POST"])
def signin():
    """Kept as an alias only. Two sign-in pages is one too many to explain, so
    GET redirects to /login, which handles both accounts and the shared
    password. POST still works for anything already pointing here."""
    if request.method == "GET":
        return redirect("/login?next=" + request.args.get("next", "/"))
    nxt = request.args.get("next", "/")
    if request.method == "GET":
        if current_account():
            return redirect(nxt)
        return render_template("users_signin.html", next=nxt)

    from hub import auth
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    ip = ip.split(",")[-1].strip()          # last hop: the client-supplied
    wait = auth.throttle_check(ip)          # first hop is spoofable
    if wait:
        return render_template("users_signin.html", next=nxt,
                               error=f"Too many attempts. Wait {wait}s."), 429
    try:
        user = users.authenticate(request.form.get("email", ""),
                                  request.form.get("password", ""))
    except UserError as exc:
        auth.throttle_fail(ip)
        return render_template("users_signin.html", next=nxt, error=str(exc)), 401
    auth.throttle_reset(ip)
    return _login_response(user, request.form.get("next") or nxt)


@bp.route("/signout")
def signout():
    user = current_account()
    if user:
        audit.log("users", "signout", actor=user.email)
    resp = make_response(redirect("/signin"))
    resp.delete_cookie(COOKIE_NAME)
    try:
        from hub import auth
        resp.delete_cookie(auth.COOKIE_NAME)
    except Exception:                                   # noqa: BLE001
        pass
    return resp


@bp.route("/reset", methods=["GET", "POST"])
def reset():
    """Complete a reset with a token, or ask an admin for one."""
    token = request.args.get("t", "") or request.form.get("token", "")
    if request.method == "GET":
        return render_template("users_reset.html", token=token,
                               min_len=users.MIN_PASSWORD_LEN)
    if request.form.get("mode") == "request":
        users.request_reset(request.form.get("email", ""))
        # Always the same message, whether or not the address exists.
        return render_template("users_reset.html", requested=True,
                               min_len=users.MIN_PASSWORD_LEN)
    try:
        user = users.complete_reset(token, request.form.get("password", ""))
    except UserError as exc:
        return render_template("users_reset.html", token=token, error=str(exc),
                               min_len=users.MIN_PASSWORD_LEN), 400
    if not user.can_log_in:
        return render_template("users_reset.html", done_pending=True,
                               min_len=users.MIN_PASSWORD_LEN)
    return _login_response(user, "/")


@bp.route("/account", methods=["GET", "POST"])
def account():
    user, gate = _require_account()
    if gate:
        return gate
    if request.method == "GET":
        return render_template("users_account.html", account=user,
                               min_len=users.MIN_PASSWORD_LEN)
    try:
        users.change_password(user, request.form.get("current", ""),
                              request.form.get("password", ""))
    except UserError as exc:
        return render_template("users_account.html", account=user,
                               error=str(exc),
                               min_len=users.MIN_PASSWORD_LEN), 400
    # The epoch just changed, so this session's own cookie is now stale too.
    return _login_response(user, "/account?changed=1")


# ---------------------------------------------------------------------------
# Admin panel — inside Diagnostics
# ---------------------------------------------------------------------------

@bp.route("/diagnostics/users")
def admin_page():
    user, gate = _require_account()
    if gate:
        return gate
    if not user.is_admin:
        return render_template("users_denied.html", account=user), 403
    return render_template("users_admin.html", account=user)


@bp.route("/api/users")
def api_list():
    _, gate = _require_admin_api()
    if gate:
        return gate
    return jsonify(users.listing())


@bp.route("/api/users/<int:uid>/<action>", methods=["POST"])
def api_action(uid, action):
    actor, gate = _require_admin_api()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    try:
        if action == "approve":
            return jsonify({"ok": True, "user": users.approve(actor, uid).as_dict(True)})
        if action == "status":
            u = users.set_status(actor, uid, body.get("status", ""))
            return jsonify({"ok": True, "user": u.as_dict(True)})
        if action == "role":
            u = users.set_role(actor, uid, body.get("role", ""))
            return jsonify({"ok": True, "user": u.as_dict(True)})
        if action == "reset":
            u, token = users.issue_reset(actor, uid)
            link = request.url_root.rstrip("/") + "/reset?t=" + token
            delivered = users.deliver_reset(u, link)
            # Shown once. Nothing stores the plaintext, so if the admin loses
            # this the only option is to issue a fresh one.
            return jsonify({"ok": True, "link": link, "delivered": delivered,
                            "expires_minutes": users.RESET_TTL_MINUTES,
                            "user": u.as_dict(True)})
        if action == "delete":
            return jsonify({"ok": True, "deleted": users.delete(actor, uid)})
    except UserError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"error": "Unknown action."}), 404


def register_users(app) -> None:
    """Mount the blueprint, seed the founding super admins, expose helpers."""
    if "users_bp" in app.blueprints:
        return
    app.register_blueprint(bp)
    try:
        with app.app_context():
            from hub.extensions import db
            db.create_all()
            users.seed_super_admins()
    except Exception as exc:                            # noqa: BLE001
        app.config["HUB_USERS_BOOT_ERROR"] = str(exc)
    app.jinja_env.globals.setdefault("current_account", current_account)
