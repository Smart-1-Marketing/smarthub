"""Who is using the Hub — Google Workspace login, roles, and demo accounts.

Before v7 the Hub had one shared PANEL_PASSWORD and a free-text "your name"
box. Whoever typed "Todd" was Todd. That made the activity log unattributable,
made per-user help progress impossible, and meant off-boarding somebody
required changing the password for everyone.

This adds "Sign in with Google", restricted to your Workspace domain. The
OAuth flow is the same one modules/google_finder already runs successfully, so
the client ID and secret on Render are reused — no new Google Cloud project.

Three things are checked, in this order, and all three must pass:

  1. Google says the token is valid and the email is verified.
  2. The email's domain is in ALLOWED_LOGIN_DOMAINS.
  3. If ALLOWED_EMAILS is set, the address is on it.

The `hd` parameter is sent to Google as a *hint* only. It is not a security
control — a user can edit it in the URL — so the domain is re-checked
server-side against the verified email. That distinction is the usual way
Workspace-restricted logins get bypassed.

The shared password still works, and is what demo accounts use, so nothing
breaks the day this deploys.
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, asdict
from urllib.parse import urlencode

import requests
from itsdangerous import BadSignature, SignatureExpired

from hub import audit

# Deliberately NOT auth.COOKIE_NAME: both are set on login, and an identical
# name means the second set_cookie silently overwrites the first — which is
# exactly how the rich session was being lost on every sign-in.
COOKIE_NAME = "s1hub_id"
SESSION_TTL_SECONDS = 12 * 60 * 60
DEMO_TTL_SECONDS = 8 * 60 * 60

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# ---- roles -----------------------------------------------------------------
# admin  — everything, including Suite writes, deletes and settings
# member — normal staff use: create, edit, run tools
# demo   — read-only, and blocked from anything that spends money (see hub.demo)
ROLES = ("admin", "member", "demo")


def _csv(name: str) -> set[str]:
    return {x.strip().lower() for x in (os.environ.get(name) or "").split(",") if x.strip()}


def _note_google(url: str, ok: bool = True) -> None:
    """Count a sign-in handshake on the Google usage page. Never raises —
    an accounting failure must not be able to block a login."""
    try:
        from hub import quotas as _q
        _q.record_google(url, module="identity", ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


def allowed_domains() -> set[str]:
    d = _csv("ALLOWED_LOGIN_DOMAINS")
    return d or {"smart1marketing.com"}


def allowed_emails() -> set[str]:
    """Optional extra allowlist. Empty means 'anyone on an allowed domain'."""
    return _csv("ALLOWED_EMAILS")


def admin_emails() -> set[str]:
    return _csv("HUB_ADMIN_EMAILS")


def _signing_timed(salt):
    from hub import signing as _signing
    return _signing.timed_serializer(salt)


def _secret() -> str:
    """The signing secret, under every name it answers to.

    hub/auth.py signs the same cookie and must agree with this; both read
    hub.config so neither can know a spelling the other does not.
    """
    from hub import signing as _signing
    return _signing.value()


_serializer = _signing_timed("s1hub-session")


@dataclass(frozen=True)
class User:
    email: str
    name: str
    role: str = "member"
    picture: str = ""
    via: str = "google"          # google | password | demo

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_demo(self) -> bool:
        return self.role == "demo"

    @property
    def domain(self) -> str:
        return self.email.rsplit("@", 1)[-1] if "@" in self.email else ""

    def as_dict(self) -> dict:
        return asdict(self)


# ---- cookie ----------------------------------------------------------------

def issue_cookie(user: User) -> str:
    return _serializer.dumps({"e": user.email, "n": user.name, "r": user.role,
                              "p": user.picture, "v": user.via})


def read_cookie(value: str | None) -> User | None:
    if not value:
        return None
    try:
        ttl = SESSION_TTL_SECONDS
        data = _serializer.loads(value, max_age=ttl)
    except (BadSignature, SignatureExpired):
        return None
    role = data.get("r") if data.get("r") in ROLES else "member"
    # Legacy cookies from the old password-only login carried only {"n": name}.
    # Accept them so a deploy doesn't log everybody out mid-shift.
    email = data.get("e") or ""
    return User(email=email, name=data.get("n") or "Unknown", role=role,
                picture=data.get("p") or "", via=data.get("v") or "password")


def user_from_environ(environ: dict) -> User | None:
    for part in (environ.get("HTTP_COOKIE") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME:
            return read_cookie(v)
    return None


# ---- Google OAuth ----------------------------------------------------------

def google_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


def authorize_url(redirect_uri: str, state: str) -> str:
    domains = sorted(allowed_domains())
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        # Hint only — Google shows the right account picker. The real check is
        # domain_ok() against the verified email after the token exchange.
        "hd": domains[0] if len(domains) == 1 else "*",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def domain_ok(email: str) -> bool:
    email = (email or "").lower().strip()
    if "@" not in email:
        return False
    if email.rsplit("@", 1)[1] not in allowed_domains():
        return False
    extra = allowed_emails()
    return (not extra) or email in extra


def role_for(email: str) -> str:
    return "admin" if email.lower() in admin_emails() else "member"


class LoginRejected(Exception):
    """Raised with a message safe to show the user."""


def complete_google_login(code: str, redirect_uri: str) -> User:
    """Exchange the code, verify, and return a User. Raises LoginRejected."""
    if not google_configured():
        raise LoginRejected("Google sign-in isn't configured on this Hub yet.")
    try:
        tok = requests.post(TOKEN_URL, data={
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }, timeout=20)
        # Sign-in costs nothing, but it is a Google API call and the usage
        # page counts every one — a token endpoint climbing without a matching
        # rise in logins is the shape of a retry loop.
        _note_google(TOKEN_URL, ok=tok.ok)
        if not tok.ok:
            raise LoginRejected("Google wouldn't complete the sign-in. Try again.")
        access = tok.json().get("access_token", "")
        info = requests.get(USERINFO_URL,
                            headers={"Authorization": f"Bearer {access}"},
                            timeout=20)
        _note_google(USERINFO_URL, ok=info.ok)
        if not info.ok:
            raise LoginRejected("Couldn't read your Google profile. Try again.")
        data = info.json()
    except LoginRejected:
        raise
    except requests.RequestException as exc:
        raise LoginRejected("Couldn't reach Google to sign you in.") from exc

    email = (data.get("email") or "").lower().strip()

    # An unverified address can be attacker-chosen. Reject it.
    if not data.get("email_verified"):
        audit.log("auth", "login_rejected", actor=email, reason="unverified")
        raise LoginRejected("That Google account's email address isn't verified.")

    if not domain_ok(email):
        audit.log("auth", "login_rejected", actor=email, reason="domain")
        pretty = " or ".join(f"@{d}" for d in sorted(allowed_domains()))
        raise LoginRejected(f"The Hub only accepts {pretty} accounts. "
                            f"You signed in as {email}.")

    user = User(email=email,
                name=data.get("name") or email.split("@")[0],
                role=role_for(email),
                picture=data.get("picture") or "",
                via="google")
    audit.log("auth", "login", actor=email, role=user.role, via="google")
    return user


# ---- demo accounts ---------------------------------------------------------

def demo_code() -> str:
    return (os.environ.get("HUB_DEMO_CODE") or "").strip()


def demo_enabled() -> bool:
    return bool(demo_code())


def complete_demo_login(code: str, display_name: str) -> User:
    """A shared code that grants a read-only, spend-blocked session.

    This is how the team tests without a Workspace account and without
    burning Insites credits, remove.bg credits or OpenAI spend.
    """
    import hmac
    expected = demo_code()
    if not expected:
        raise LoginRejected("Demo access isn't enabled on this Hub.")
    if not hmac.compare_digest((code or "").strip(), expected):
        raise LoginRejected("That demo code isn't right.")
    name = (display_name or "").strip()[:60] or "Demo user"
    user = User(email=f"demo+{name.lower().replace(' ', '.')}@demo.local",
                name=name, role="demo", via="demo")
    audit.log("auth", "login", actor=user.email, role="demo", via="demo")
    return user


# ---- legacy password -------------------------------------------------------

def password_login(password: str, display_name: str) -> User:
    """The pre-v7 shared password. Kept so this deploy locks nobody out."""
    import hashlib
    import hmac
    expected = os.environ.get("PANEL_PASSWORD", "")
    if not expected:
        raise LoginRejected("No Hub password is set.")
    if not hmac.compare_digest(hashlib.sha256((password or "").encode()).digest(),
                               hashlib.sha256(expected.encode()).digest()):
        raise LoginRejected("Wrong password.")
    name = (display_name or "").strip()[:60] or "Unknown"
    user = User(email="", name=name, role="member", via="password")
    audit.log("auth", "login", actor=name, via="password")
    return user


def new_state() -> str:
    return secrets.token_urlsafe(24)


def state_ok(sent: str, got: str, issued_at: float, max_age: int = 600) -> bool:
    """CSRF check on the OAuth round trip, with an expiry."""
    import hmac
    if not sent or not got:
        return False
    if time.time() - (issued_at or 0) > max_age:
        return False
    return hmac.compare_digest(sent, got)
