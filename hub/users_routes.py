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
    # Through hub/signing.py: this signs the same kind of token hub/auth.py
    # does, and a route that knows two of the three spellings signs with a
    # different secret than the guard that has to verify it.
    #
    # The fallback here was the literal "dev-only", and this is the cookie the
    # middleware reads for the role and the password gate -- so with no
    # SECRET_KEY set, a cookie signed with a string out of this file claiming
    # {"r": "admin", "c": false} was accepted as an Admin session belonging to
    # no account. An ephemeral secret costs a re-login and cannot be forged.
    from hub import signing as _signing
    return _signing.timed_serializer("s1hub-user")


def issue_cookie(user: User) -> str:
    # The session epoch travels in the cookie: bump it on the user row and
    # every existing session for that account stops validating immediately.
    #
    # `r` (role) and `c` (owes a password change) ride along so the WSGI
    # middleware in wsgi.py can answer both questions without a database read
    # — it runs outside any app context, in front of every mounted module.
    # Neither can go stale: both of the things that change them, a role change
    # and a password change, are exactly the things that bump the epoch, and a
    # bumped epoch invalidates this cookie outright. The signature is what
    # stops either being edited.
    return _serializer().dumps({"u": user.id, "e": user.email,
                                "r": user.role, "s": user.session_epoch or 1,
                                "c": bool(user.must_change_password)})


def session_from_environ(environ) -> dict:
    """The signed session payload straight from a WSGI environ, or {}.

    For the middleware, which has no request context and no app context. It
    verifies the signature and reads nothing else — the account row it names
    is re-read by `current_account()` wherever a real access decision is being
    made, so this is only ever used for chrome and for the password gate that
    a signed cookie can answer on its own.
    """
    raw = ""
    for part in (environ.get("HTTP_COOKIE", "") or "").split(";"):
        key, _, value = part.strip().partition("=")
        if key == COOKIE_NAME:
            raw = value
            break
    if not raw:
        return {}
    try:
        return _serializer().loads(raw, max_age=SESSION_DAYS * 86400) or {}
    except Exception:                                   # noqa: BLE001
        return {}


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
        from hub import suite_embed as embed
        embed.issue_cookie(resp, user.name or user.email, secure)
    except Exception:                                   # noqa: BLE001
        pass
    return resp


def _require_account():
    """Require a real user account, not just a session.

    The shared PANEL_PASSWORD gives a valid Hub session but creates no account
    row, so pages that need an account used to redirect to /login — which saw
    a valid session and redirected straight back. An infinite loop, and the
    browser's only clue was ERR_TOO_MANY_REDIRECTS.

    A redirect is the wrong tool when the person is already signed in and the
    thing they lack can't be obtained by signing in again. Explain it instead.
    """
    user = current_account()
    if user:
        return user, None

    from hub import auth as hub_auth
    if hub_auth.user_from_environ(request.environ):
        # Signed in on the shared password: don't bounce, tell them why.
        return None, (render_template("users_need_account.html",
                                      next=request.path), 403)
    return None, redirect("/login?next=" + request.path)


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
    # The last hop, via the one helper that knows why — the first entry in
    # X-Forwarded-For is client-supplied, so throttling on it hands an
    # attacker a fresh allowance per request.
    ip = auth.client_ip(request.headers, request.remote_addr or "")
    wait = auth.throttle_check(ip)
    if wait:
        return render_template("users_signin.html", next=nxt,
                               error=f"Too many attempts. Wait {wait}s."), 429
    email = request.form.get("email", "")
    try:
        user = users.authenticate(email, request.form.get("password", ""))
    except UserError as exc:
        # The address is passed so one IP working through the staff list trips
        # the credential-stuffing check, which counting attempts alone cannot
        # see: fourteen addresses at one guess each never reaches six on any
        # of them.
        auth.throttle_fail(ip, email)
        return render_template("users_signin.html", next=nxt, error=str(exc)), 401
    except Exception:                                   # noqa: BLE001
        # The same hole /login had, and it has to sit BELOW the UserError arm
        # or it would swallow every wrong password as a database fault.
        # authenticate() is a database read, and catching only UserError let an
        # unreachable Postgres out of the route as a 500 -- on the one page
        # nobody can already be signed in to read. Deliberately not a throttle
        # strike: the password was never checked.
        return render_template(
            "users_signin.html", next=nxt,
            error="Sign-in can't reach the Hub's database right now, so your "
                  "password could not be checked. Nothing is wrong with your "
                  "account - try again in a minute."), 503
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


@bp.route("/forgot")
def forgot():
    """What "Forgot password?" opens, and it is a person rather than a form.

    There is no email sender on this Hub, so a self-service reset has nowhere
    to send anything. The page it used to open collected an address and told
    you an admin had been flagged -- which is a form that looks like it did
    something, on a queue nobody watches. Naming the person who can actually
    do it is a shorter path to a working password.

    The name and the address come from `hub/user_directory.py`, so this page
    and the sign-in copy cannot end up pointing at different people.
    """
    from hub.user_directory import SUPPORT_EMAIL, SUPPORT_NAME
    return render_template("users_forgot.html", support_name=SUPPORT_NAME,
                           support_email=SUPPORT_EMAIL)


@bp.route("/reset", methods=["GET", "POST"])
def reset():
    """Complete a reset with a token an admin issued.

    A GET with no token used to draw the "request a reset" form; it sends you
    to /forgot instead, so there is one answer to "I've forgotten it" rather
    than two pages disagreeing about whether the Hub can email you.
    """
    token = request.args.get("t", "") or request.form.get("token", "")
    if request.method == "GET":
        if not token:
            return redirect("/forgot")
        return render_template("users_reset.html", token=token,
                               min_len=users.MIN_PASSWORD_LEN)
    if request.form.get("mode") == "request":
        # Kept working for anything still posting here, and answered the same
        # way: the address goes nowhere, so the page says who to ask.
        users.request_reset(request.form.get("email", ""))
        return redirect("/forgot")

    # Completing a reset is a credential endpoint like any other, and it was
    # the one with no throttle on it: a token is 32 random bytes, but "not
    # guessable" is a property of this token and not a reason to leave the
    # door swinging for the next one.
    from hub import auth
    ip = auth.client_ip(request.headers, request.remote_addr or "")
    wait = auth.throttle_check(ip)
    if wait:
        return render_template("users_reset.html", token=token,
                               error=f"Too many attempts. Wait {wait}s.",
                               min_len=users.MIN_PASSWORD_LEN), 429
    try:
        user = users.complete_reset(token, request.form.get("password", ""))
    except UserError as exc:
        auth.throttle_fail(ip)
        return render_template("users_reset.html", token=token, error=str(exc),
                               min_len=users.MIN_PASSWORD_LEN), 400
    auth.throttle_reset(ip)
    if not user.can_log_in:
        return render_template("users_reset.html", done_pending=True,
                               min_len=users.MIN_PASSWORD_LEN)
    return _login_response(user, "/")


@bp.route("/account", methods=["GET", "POST"])
def account():
    """Your own account, and the only page a forced password change lets you
    reach. `first=1` is the redirect the gate sends, and it changes the copy
    rather than the form: the person has not asked to be here and needs to be
    told why in the words they are seeing."""
    user, gate = _require_account()
    if gate:
        return gate
    from hub.user_directory import DEFAULT_PASSWORD, profile_for
    ctx = {"account": user, "min_len": users.MIN_PASSWORD_LEN,
           "first": request.args.get("first") == "1",
           "must_change": bool(user.must_change_password),
           "profile": (profile_for(user.email) or None),
           "default_password": DEFAULT_PASSWORD}
    if request.method == "GET":
        return render_template("users_account.html", **ctx)

    # A password change is a credential endpoint: without a throttle, a
    # session left open on an unlocked laptop is an unlimited oracle for the
    # current password.
    from hub import auth
    ip = auth.client_ip(request.headers, request.remote_addr or "")
    wait = auth.throttle_check(ip)
    if wait:
        return render_template("users_account.html",
                               error=f"Too many attempts. Wait {wait}s.",
                               **ctx), 429
    try:
        users.change_password(user, request.form.get("current", ""),
                              request.form.get("password", ""))
    except UserError as exc:
        auth.throttle_fail(ip, user.email)
        return render_template("users_account.html", error=str(exc), **ctx), 400
    auth.throttle_reset(ip)
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
        if action == "set-password":
            u, password = users.set_password_admin(actor, uid,
                                                   body.get("password", ""))
            # Returned once, exactly like the reset link above, and stored
            # nowhere. `generated` tells the panel whether to present it as
            # something to copy or as the value the admin already typed.
            return jsonify({"ok": True, "password": password,
                            "generated": not (body.get("password") or "").strip(),
                            "user": u.as_dict(True)})
        if action == "default-password":
            from hub.user_directory import default_password
            u, password = users.set_password_admin(actor, uid, default_password())
            return jsonify({"ok": True, "password": password,
                            "generated": False, "default": True,
                            "user": u.as_dict(True)})
        if action == "profile":
            from hub.user_directory import save_profile
            row = save_profile(_email_of(uid), body, source="panel")
            audit.log("users", "profile_edited", actor=actor.email,
                      target=row.email)
            return jsonify({"ok": True, "profile": row.as_dict()})
        if action == "delete":
            return jsonify({"ok": True, "deleted": users.delete(actor, uid)})
    except UserError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"error": "Unknown action."}), 404


def _email_of(uid: int) -> str:
    user = User.query.get(uid)
    if not user:
        raise UserError("No such user.")
    return user.email


def register_users(app) -> None:
    """Mount the blueprint, seed the founding super admins, expose helpers."""
    if "users_bp" in app.blueprints:
        return
    app.register_blueprint(bp)
    try:
        # Imported BEFORE create_all, not for tidiness: a model class that has
        # not been imported is not in db.metadata, so create_all would skip
        # hub_user_profiles entirely and every profile read would come back
        # empty with the table quietly absent.
        from hub import user_directory as _profiles          # noqa: F401
        # create_all goes through the locked helper so two workers don't race
        # each other's DDL. Seeding still needs its own app context.
        from hub.extensions import create_all as _create_all
        err = _create_all(app)
        if err:
            app.config["HUB_USERS_BOOT_ERROR"] = err
        with app.app_context():
            users.seed_super_admins()
            # The census roster, second: seeding creates the three founding
            # super admins with no password, and sync_roster gives every
            # roster account -- those three included -- its starting password.
            # Run the other way round and the three would be created by the
            # roster and then found to exist by the seeder, which is harmless
            # but means the founding list stops being what defines them.
            from hub.user_directory import sync_roster
            app.config["HUB_ROSTER_SYNC"] = sync_roster()
    except Exception as exc:                            # noqa: BLE001
        app.config["HUB_USERS_BOOT_ERROR"] = str(exc)
    app.jinja_env.globals.setdefault("current_account", current_account)
