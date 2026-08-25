"""Hub-wide authentication: one shared password + your name.

A signed, HttpOnly cookie (12 h) unlocks EVERY module.  The same cookie is
checked both by the hub's own Flask views and by the WSGI guard that sits in
front of each mounted module, so nothing is reachable without logging in.

Ported (and kept behaviour-compatible) from the Node control panel:
timing-safe password compare + per-IP login throttling.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "s1hub_auth"
SESSION_TTL_SECONDS = 12 * 60 * 60  # 12 hours

_SECRET = os.environ.get("SECRET_KEY") or os.environ.get("SESSION_SECRET") or ""
if not _SECRET:
    # Ephemeral secret: everyone re-logs-in after a restart. Set SECRET_KEY!
    _SECRET = secrets.token_hex(32)

_serializer = URLSafeTimedSerializer(_SECRET, salt="s1hub-session")


def panel_password() -> str:
    return os.environ.get("PANEL_PASSWORD", "")


def check_password(candidate: str) -> bool:
    expected = panel_password()
    if not expected or not isinstance(candidate, str):
        return False
    return hmac.compare_digest(
        hashlib.sha256(candidate.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def issue_cookie_value(name: str) -> str:
    clean = (name or "").strip()[:60] or "Unknown"
    return _serializer.dumps({"n": clean})


def verify_cookie_value(value: str | None) -> str | None:
    """Return the logged-in user's name, or None."""
    if not value:
        return None
    try:
        data = _serializer.loads(value, max_age=SESSION_TTL_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data.get("n") or "Unknown"


def user_from_environ(environ: dict) -> str | None:
    """Read + verify the hub cookie straight from a WSGI environ.

    Falls back to the embed companion cookie, which is the same signed value
    under `SameSite=None` so it survives being sent from a page framed inside
    Smart 1 Suite. That fallback is deliberately narrow — safe methods, on an
    allowlisted path — and `hub.embed` owns the rule rather than restating it
    here, so the WSGI guard, `login_required` and `api_login_required` all get
    the same answer. It is checked *second*: an ordinary session is the normal
    case, and this stays a fallback rather than a second front door.
    """
    cookie_header = environ.get("HTTP_COOKIE", "")
    for part in cookie_header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME:
            user = verify_cookie_value(v)
            if user:
                return user
    try:
        from . import suite_embed as embed
        return embed.user_from_environ(environ)
    except Exception:  # noqa: BLE001 — never let the embed path break login
        return None


# ---------------- Login throttling (per IP, in memory) ----------------
LOGIN_MAX_ATTEMPTS = 6
LOGIN_WINDOW_SECONDS = 10 * 60
LOGIN_LOCKOUT_SECONDS = 15 * 60

_attempts: dict[str, dict] = {}
_attempts_lock = threading.Lock()


def throttle_check(ip: str) -> int:
    """Seconds the caller must still wait, or 0 if allowed."""
    with _attempts_lock:
        rec = _attempts.get(ip)
        if rec and rec.get("locked_until", 0) > time.time():
            return int(rec["locked_until"] - time.time())
    return 0


def throttle_fail(ip: str) -> None:
    now = time.time()
    with _attempts_lock:
        rec = _attempts.get(ip)
        if not rec or now - rec["first"] > LOGIN_WINDOW_SECONDS:
            rec = {"count": 0, "first": now}
        rec["count"] += 1
        if rec["count"] >= LOGIN_MAX_ATTEMPTS:
            rec["locked_until"] = now + LOGIN_LOCKOUT_SECONDS
        _attempts[ip] = rec
        if len(_attempts) > 5000:
            _attempts.clear()


def throttle_reset(ip: str) -> None:
    with _attempts_lock:
        _attempts.pop(ip, None)


def login_required(fn):
    """Page-level guard for blueprints registered on the hub app.

    Modules written outside this repo import this by name and fall back to a
    no-op when it's missing — which silently serves an admin page to anyone.
    Defining it here closes that hole rather than leaving each module to guess.
    """
    import functools
    from flask import redirect, request

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not user_from_environ(request.environ):
            return redirect("/login?next=" + request.path)
        return fn(*a, **kw)
    return wrapper


def api_login_required(fn):
    """Same, for JSON endpoints — 401 instead of a redirect."""
    import functools
    from flask import jsonify, request

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        if not user_from_environ(request.environ):
            return jsonify({"error": "Not authenticated."}), 401
        return fn(*a, **kw)
    return wrapper
