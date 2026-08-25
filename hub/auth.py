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


# ---------------------------------------------------------------------------
# Brute force
# ---------------------------------------------------------------------------
#
# Three separate attacks, and the old six-strikes-per-IP counter only stopped
# the first of them:
#
#   1. **One account, hammered.** Guessing one person's password from one
#      place. The per-IP window catches it.
#   2. **One account, hammered from everywhere.** A botnet spends six guesses
#      per address and never trips a per-IP counter at all. The per-account
#      lockout in `hub/users.py` is what catches that one — it lives on the
#      user row, so it is shared across both gunicorn workers and survives a
#      restart, which the in-memory counter here does neither of.
#   3. **Credential stuffing.** A leaked password list tried one guess against
#      each of fourteen known addresses. Nothing here counted it: fourteen
#      addresses at one guess each never reached six on any account, and the
#      per-IP counter was only ever reset by a *success*. `_seen_emails`
#      below is the answer — an IP that has tried more than a handful of
#      distinct addresses is not somebody who forgot which email they used.
#
# The lockout **escalates**: 15 minutes, then an hour, then six. A fixed
# window is a rate limit rather than a deterrent, because an attacker who can
# wait fifteen minutes has an unlimited number of six-guess batches. The
# escalation decays after a quiet day so a genuinely forgetful person is not
# locked out for six hours a week later.
#
# It is in memory and there are two gunicorn workers, so the effective per-IP
# allowance is up to double what these numbers say. That is stated rather than
# papered over: the numbers below are the floor, the per-account lockout is
# the ceiling, and the ceiling is the one that is actually shared. Moving this
# to the database would put a write on every failed login, which is itself a
# way to make a login endpoint expensive to hit.
LOGIN_MAX_ATTEMPTS = 6
LOGIN_WINDOW_SECONDS = 10 * 60
LOGIN_LOCKOUT_SECONDS = 15 * 60

# Escalation, in seconds, by how many times this IP has been locked out
# already. The last entry repeats for every lockout after it.
LOCKOUT_LADDER = (15 * 60, 60 * 60, 6 * 60 * 60)

# Distinct email addresses one IP may try before it is treated as stuffing
# rather than as forgetfulness. Four is generous for a person: most people
# have two work addresses at the outside, and the fourteen-address sweep this
# is aimed at trips it on the fifth.
STUFFING_MAX_EMAILS = 4

# How long a clean IP keeps its record. Past this, the escalation resets —
# somebody who was locked out last month starts again at fifteen minutes.
RECORD_TTL_SECONDS = 24 * 60 * 60

_attempts: dict[str, dict] = {}
_attempts_lock = threading.Lock()


def _fresh(now: float) -> dict:
    return {"count": 0, "first": now, "locked_until": 0.0,
            "lockouts": 0, "emails": set(), "last": now}


def _record(ip: str, now: float) -> dict:
    rec = _attempts.get(ip)
    if not rec or now - rec.get("last", 0) > RECORD_TTL_SECONDS:
        rec = _fresh(now)
        _attempts[ip] = rec
    return rec


def throttle_check(ip: str) -> int:
    """Seconds the caller must still wait, or 0 if allowed."""
    now = time.time()
    with _attempts_lock:
        rec = _attempts.get(ip)
        if rec and rec.get("locked_until", 0) > now:
            return int(rec["locked_until"] - now)
    return 0


def throttle_fail(ip: str, email: str = "") -> int:
    """Record one failed attempt. Returns the lockout in seconds, or 0.

    ``email`` is what makes the stuffing check possible, and it is hashed
    rather than kept: this dict is read by the diagnostics page, and a list of
    the addresses an attacker guessed is a list of our staff's addresses
    sitting in a page somebody screenshots.
    """
    now = time.time()
    with _attempts_lock:
        rec = _record(ip, now)
        rec["last"] = now
        if now - rec["first"] > LOGIN_WINDOW_SECONDS:
            rec["count"], rec["first"] = 0, now
            rec["emails"] = set()
        rec["count"] += 1
        if email:
            rec["emails"].add(hashlib.sha256(email.strip().lower().encode())
                              .hexdigest()[:16])

        stuffing = len(rec["emails"]) > STUFFING_MAX_EMAILS
        if rec["count"] >= LOGIN_MAX_ATTEMPTS or stuffing:
            step = min(rec["lockouts"], len(LOCKOUT_LADDER) - 1)
            wait = LOCKOUT_LADDER[step]
            rec["locked_until"] = now + wait
            rec["lockouts"] += 1
            rec["count"] = 0
            _prune(now)
            _log_lockout(ip, wait, stuffing, len(rec["emails"]))
            return int(wait)
        _prune(now)
    return 0


def _log_lockout(ip: str, wait: int, stuffing: bool, addresses: int) -> None:
    """Say which of the two things happened. They need different responses.

    A rate lockout is usually somebody who forgot their password. A stuffing
    lockout is an attack, and reading them as the same event is how the second
    one hides inside a week of the first.
    """
    try:
        from . import audit
        audit.log("auth", "stuffing_blocked" if stuffing else "throttled",
                  actor=ip, seconds=wait,
                  addresses=addresses if stuffing else None)
    except Exception:                   # noqa: BLE001 — never break a refusal
        pass


def _prune(now: float) -> None:
    """Drop stale records. Called with the lock held."""
    if len(_attempts) <= 5000:
        return
    for key in [k for k, v in _attempts.items()
                if v.get("locked_until", 0) < now
                and now - v.get("last", 0) > RECORD_TTL_SECONDS]:
        _attempts.pop(key, None)
    if len(_attempts) > 5000:           # still full of live lockouts
        _attempts.clear()


def throttle_reset(ip: str) -> None:
    """Clear an IP after a successful sign-in.

    The escalation count goes with it, deliberately: somebody who eventually
    remembered their password is not a repeat offender, and carrying the
    ladder across a success is how a busy person ends up locked out for six
    hours over three separate bad mornings.
    """
    with _attempts_lock:
        _attempts.pop(ip, None)


def client_ip(headers, remote_addr: str = "") -> str:
    """The last hop in X-Forwarded-For, not the first.

    The first entry is supplied by the client and is trivially spoofed, so
    throttling on it means an attacker gets a fresh allowance per request by
    changing one header. This exact mistake was flagged in three separate apps
    during the suite audit, and it was written out longhand at four call sites
    here — one of which had it backwards.
    """
    fwd = ""
    try:
        fwd = headers.get("X-Forwarded-For", "") or ""
    except AttributeError:
        fwd = ""
    if fwd:
        return fwd.split(",")[-1].strip()
    return remote_addr or "?"


def throttle_status() -> dict:
    """What the throttle is currently holding — for the diagnostics page.

    Counts only. The addresses are hashed and the IPs are not listed: a page
    that names the addresses somebody guessed is a page that leaks the staff
    directory to whoever opens it.
    """
    now = time.time()
    with _attempts_lock:
        locked = sum(1 for r in _attempts.values()
                     if r.get("locked_until", 0) > now)
        stuffing = sum(1 for r in _attempts.values()
                       if len(r.get("emails") or ()) > STUFFING_MAX_EMAILS)
        return {"tracked_ips": len(_attempts), "locked_out": locked,
                "stuffing_suspects": stuffing,
                "window_seconds": LOGIN_WINDOW_SECONDS,
                "max_attempts": LOGIN_MAX_ATTEMPTS,
                "ladder_seconds": list(LOCKOUT_LADDER),
                "shared_across_workers": False}


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
