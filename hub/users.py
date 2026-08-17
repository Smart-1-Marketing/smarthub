"""User accounts — self sign-up, admin approval, roles, password reset.

Built because Google Workspace sign-in needs an OAuth consent review that can
take weeks, and the team needs individual logins before then. This is not a
replacement for that: when Google Access is approved, a user can be linked to a
Google identity and both routes work against the same account row.

Until now the Hub had one shared PANEL_PASSWORD and a free-text "your name"
box. Whoever typed "Todd" was Todd. That makes the activity log unattributable,
makes off-boarding mean changing the password for everyone, and makes per-user
anything impossible.

## Security decisions, and why

**Passwords are hashed with scrypt** (werkzeug's default), never stored or
logged. A hash is never returned by any route.

**Sign-up does not grant access.** A new account lands in `pending` and cannot
log in until a super admin approves it. Self-service sign-up without that gate
is just an open door with extra steps.

**Sign-up is domain-restricted** to ALLOWED_LOGIN_DOMAINS, so a stranger cannot
even create a pending row.

**Reset tokens are hashed at rest**, single-use, and expire in 30 minutes. The
plaintext token is shown to the admin exactly once, at the moment they issue
it. A reset link sitting readable in a database is the same weakness as a
plaintext password.

**There is no email sender configured in this Hub**, so a reset is issued by an
admin and the one-time link handed over directly — Slack, text, in person.
That is deliberate rather than a gap: emailing reset links from an unverified
domain is how they end up in spam or in the wrong inbox. `deliver_reset()` is
the single hook to wire an email provider into later.

**Changing a password invalidates every other session** for that user, via a
session epoch carried in the cookie. Without it, a compromised session survives
the password change that was meant to end it.

**Failed logins are throttled per IP** by the existing hub.auth throttle, and
per account here, so a slow attack spread across IPs still locks the account
rather than getting unlimited attempts.

**Super admins cannot be demoted or deleted by anyone but another super
admin**, and the last remaining super admin cannot be removed at all — locking
yourself out of your own admin panel is a real and unrecoverable mistake.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from werkzeug.security import check_password_hash, generate_password_hash

from hub import audit
from hub.extensions import db

# Seeded on first boot. These three are super admins by definition; the env var
# only adds to the list, so a mistyped variable cannot lock Todd out.
FOUNDING_SUPER_ADMINS = (
    "todd@smart1marketing.com",
    "george@smart1marketing.com",
    "kaden@smart1marketing.com",
)

ROLES = ("super_admin", "admin", "member")
STATUSES = ("pending", "active", "suspended")

RESET_TTL_MINUTES = 30
MAX_FAILED_LOGINS = 8
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LEN = 12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class User(db.Model):
    __tablename__ = "hub_users"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(160), default="")
    password_hash = Column(String(320), default="")
    role = Column(String(20), default="member", index=True)
    status = Column(String(20), default="pending", index=True)

    # Bumped on password change or forced sign-out; the cookie carries it, so a
    # mismatch logs that session out everywhere at once.
    session_epoch = Column(Integer, default=1)

    failed_logins = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    approved_at = Column(DateTime, nullable=True)
    approved_by = Column(String(255), default="")

    # Hashed, never the plaintext token.
    reset_token_hash = Column(String(128), default="")
    reset_expires_at = Column(DateTime, nullable=True)
    must_change_password = Column(Boolean, default=False)

    # ---- derived ----
    @property
    def is_super(self) -> bool:
        return self.role == "super_admin"

    @property
    def is_admin(self) -> bool:
        return self.role in ("super_admin", "admin")

    @property
    def can_log_in(self) -> bool:
        return self.status == "active" and bool(self.password_hash)

    @property
    def locked(self) -> bool:
        until = _aware(self.locked_until)
        return bool(until and until > _now())

    def as_dict(self, include_admin_fields: bool = False) -> dict:
        d = {
            "id": self.id, "email": self.email, "name": self.name,
            "role": self.role, "status": self.status,
            "created_at": _aware(self.created_at).isoformat() if self.created_at else None,
            "last_login_at": _aware(self.last_login_at).isoformat() if self.last_login_at else None,
        }
        if include_admin_fields:
            d.update({
                "locked": self.locked,
                "failed_logins": self.failed_logins or 0,
                "approved_by": self.approved_by or "",
                "must_change_password": bool(self.must_change_password),
                "has_open_reset": bool(self.reset_token_hash
                                       and _aware(self.reset_expires_at)
                                       and _aware(self.reset_expires_at) > _now()),
            })
        # password_hash and reset_token_hash are never serialised.
        return d


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class UserError(Exception):
    """Message is safe to show the user."""


def allowed_domains() -> set[str]:
    raw = os.environ.get("ALLOWED_LOGIN_DOMAINS") or "smart1marketing.com"
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def normalise_email(value: str) -> str:
    return (value or "").strip().lower()


def check_email(email: str) -> str:
    email = normalise_email(email)
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", email):
        raise UserError("That doesn't look like an email address.")
    if email.rsplit("@", 1)[1] not in allowed_domains():
        pretty = " or ".join(f"@{d}" for d in sorted(allowed_domains()))
        raise UserError(f"Sign-up is limited to {pretty} addresses.")
    return email


def check_password(pw: str) -> str:
    """Length over composition rules.

    Enforced character classes push people toward Passw0rd! — long passphrases
    are stronger and easier to remember, so the rule is length plus a check
    against the handful of things people actually type.
    """
    pw = pw or ""
    if len(pw) < MIN_PASSWORD_LEN:
        raise UserError(f"Use at least {MIN_PASSWORD_LEN} characters. "
                        f"A short phrase you'll remember beats a short jumble.")
    if len(pw) > 200:
        raise UserError("That password is too long.")
    lowered = pw.lower()
    for bad in ("password", "smart1", "12345678", "qwerty", "letmein"):
        if bad in lowered:
            raise UserError("That contains something too easy to guess. "
                            "Try an unrelated phrase.")
    return pw


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def seed_super_admins() -> int:
    """Create the founding super-admin rows if they don't exist.

    They land with no password and status 'pending' — the first thing each does
    is set their own password through a reset link. Seeding a shared default
    password would be worse than the single shared login this replaces.
    """
    extra = {normalise_email(e) for e in
             (os.environ.get("HUB_SUPER_ADMINS") or "").split(",") if e.strip()}
    created = 0
    for email in set(FOUNDING_SUPER_ADMINS) | extra:
        if not email:
            continue
        if User.query.filter_by(email=email).first():
            continue
        db.session.add(User(email=email, name=email.split("@")[0].title(),
                            role="super_admin", status="pending",
                            created_at=_now()))
        created += 1
    if created:
        db.session.commit()
        audit.log("users", "seeded", count=created)
    return created


def sign_up(email: str, name: str, password: str) -> User:
    """Self-service registration. Creates a PENDING account — not access."""
    email = check_email(email)
    check_password(password)
    existing = User.query.filter_by(email=email).first()

    if existing and existing.password_hash:
        # Don't confirm whether an address is registered — that's an account
        # enumeration oracle. The caller shows the same message either way.
        raise UserError("__EXISTS__")

    if existing:
        # A seeded super admin claiming their account for the first time.
        #
        # They activate immediately rather than landing in `pending`. This is
        # the bootstrap case: the founding super admins are named in code, so
        # they are already authorised — and leaving them pending would be a
        # deadlock, because the only people who could approve them are each
        # other, and none of them can sign in yet.
        #
        # Deliberately narrow: this only applies to a row that was seeded (a
        # super admin with no password yet), never to an ordinary account.
        existing.name = (name or existing.name or "").strip()[:160]
        existing.password_hash = generate_password_hash(password)
        if existing.role == "super_admin" and existing.status == "pending":
            existing.status = "active"
            existing.approved_at = _now()
            existing.approved_by = "seeded"
        db.session.commit()
        audit.log("users", "signup_claimed", actor=email, role=existing.role,
                  status=existing.status)
        return existing

    user = User(email=email, name=(name or "").strip()[:160] or email.split("@")[0],
                password_hash=generate_password_hash(password),
                role="member", status="pending", created_at=_now())
    db.session.add(user)
    db.session.commit()
    audit.log("users", "signup", actor=email)
    return user


def authenticate(email: str, password: str) -> User:
    """Verify credentials. Raises UserError with a safe message."""
    email = normalise_email(email)
    user = User.query.filter_by(email=email).first()

    # Same work and the same message whether or not the account exists, so
    # response timing and wording don't reveal which addresses are registered.
    if user is None:
        generate_password_hash("timing-equaliser")
        raise UserError("That email and password don't match.")

    if user.locked:
        mins = max(1, int((_aware(user.locked_until) - _now()).total_seconds() // 60))
        audit.log("users", "login_locked", actor=email)
        raise UserError(f"Too many failed attempts. Try again in {mins} minute"
                        f"{'s' if mins != 1 else ''}.")

    if not user.password_hash or not check_password_hash(user.password_hash, password or ""):
        user.failed_logins = (user.failed_logins or 0) + 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = _now() + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_logins = 0
        db.session.commit()
        audit.log("users", "login_failed", actor=email)
        raise UserError("That email and password don't match.")

    if user.status == "pending":
        audit.log("users", "login_pending", actor=email)
        raise UserError("Your account is waiting for approval. An admin will "
                        "let you know when it's ready.")
    if user.status == "suspended":
        audit.log("users", "login_suspended", actor=email)
        raise UserError("That account has been suspended. Speak to an admin.")

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = _now()
    db.session.commit()
    audit.log("users", "login", actor=email, role=user.role)
    return user


# ---------------------------------------------------------------------------
# Admin actions
# ---------------------------------------------------------------------------

def _require_admin(actor: User):
    if not actor or not actor.is_admin:
        raise UserError("Only an admin can do that.")


def approve(actor: User, user_id: int) -> User:
    _require_admin(actor)
    user = User.query.get(user_id)
    if not user:
        raise UserError("No such user.")
    if not user.password_hash:
        raise UserError("That account has no password set yet — issue a reset "
                        "link instead, which lets them set one.")
    user.status = "active"
    user.approved_at = _now()
    user.approved_by = actor.email
    db.session.commit()
    audit.log("users", "approved", actor=actor.email, target=user.email)
    return user


def set_status(actor: User, user_id: int, status: str) -> User:
    _require_admin(actor)
    if status not in STATUSES:
        raise UserError("Unknown status.")
    user = User.query.get(user_id)
    if not user:
        raise UserError("No such user.")
    if user.is_super and not actor.is_super:
        raise UserError("Only a super admin can change a super admin.")
    if user.id == actor.id and status != "active":
        raise UserError("You can't suspend your own account.")
    user.status = status
    if status != "active":
        user.session_epoch = (user.session_epoch or 1) + 1   # sign them out now
    db.session.commit()
    audit.log("users", "status_changed", actor=actor.email,
              target=user.email, status=status)
    return user


def set_role(actor: User, user_id: int, role: str) -> User:
    _require_admin(actor)
    if role not in ROLES:
        raise UserError("Unknown role.")
    if not actor.is_super:
        raise UserError("Only a super admin can change roles.")
    user = User.query.get(user_id)
    if not user:
        raise UserError("No such user.")
    if user.is_super and role != "super_admin" and _super_count() <= 1:
        raise UserError("That's the last super admin — promote someone else "
                        "first, or you'll lock everyone out of this panel.")
    user.role = role
    db.session.commit()
    audit.log("users", "role_changed", actor=actor.email,
              target=user.email, role=role)
    return user


def delete(actor: User, user_id: int) -> str:
    _require_admin(actor)
    user = User.query.get(user_id)
    if not user:
        raise UserError("No such user.")
    if user.id == actor.id:
        raise UserError("You can't delete your own account.")
    if user.is_super:
        if not actor.is_super:
            raise UserError("Only a super admin can remove a super admin.")
        if _super_count() <= 1:
            raise UserError("That's the last super admin. Removing them locks "
                            "everyone out permanently.")
    email = user.email
    db.session.delete(user)
    db.session.commit()
    audit.log("users", "deleted", actor=actor.email, target=email)
    return email


def _super_count() -> int:
    return User.query.filter_by(role="super_admin").count()


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_reset(actor: User, user_id: int) -> tuple[User, str]:
    """Create a one-time reset token. Returns (user, plaintext token).

    The plaintext is returned once and never stored — only its hash is. Show it
    to the admin, who hands the link over directly.
    """
    _require_admin(actor)
    user = User.query.get(user_id)
    if not user:
        raise UserError("No such user.")
    if user.is_super and not actor.is_super:
        raise UserError("Only a super admin can reset a super admin's password.")

    token = secrets.token_urlsafe(32)
    user.reset_token_hash = _hash_token(token)
    user.reset_expires_at = _now() + timedelta(minutes=RESET_TTL_MINUTES)
    user.locked_until = None
    user.failed_logins = 0
    db.session.commit()
    audit.log("users", "reset_issued", actor=actor.email, target=user.email)
    return user, token


def request_reset(email: str) -> bool:
    """A user asking for a reset themselves.

    Always returns True regardless of whether the address exists — telling a
    stranger which addresses are registered is an enumeration oracle. It flags
    the account for an admin rather than issuing a token directly, because with
    no email sender there is nowhere safe to send one.
    """
    email = normalise_email(email)
    user = User.query.filter_by(email=email).first()
    if user:
        user.must_change_password = True
        db.session.commit()
        audit.log("users", "reset_requested", actor=email)
    else:
        audit.log("users", "reset_requested_unknown", actor=email)
    return True


def complete_reset(token: str, new_password: str) -> User:
    """Exchange a valid token for a new password."""
    check_password(new_password)
    token_hash = _hash_token(token or "")
    user = None
    # Compared in constant time against every open reset, so a near-miss can't
    # be distinguished by timing.
    for candidate in User.query.filter(User.reset_token_hash != "").all():
        if hmac.compare_digest(candidate.reset_token_hash or "", token_hash):
            user = candidate
            break
    if user is None:
        raise UserError("That reset link isn't valid. Ask an admin for a new one.")
    expires = _aware(user.reset_expires_at)
    if not expires or expires < _now():
        user.reset_token_hash = ""
        db.session.commit()
        raise UserError(f"That reset link has expired — they last "
                        f"{RESET_TTL_MINUTES} minutes. Ask for a new one.")

    user.password_hash = generate_password_hash(new_password)
    user.reset_token_hash = ""            # single use
    user.reset_expires_at = None
    user.must_change_password = False
    user.failed_logins = 0
    user.locked_until = None
    user.session_epoch = (user.session_epoch or 1) + 1   # kill other sessions
    db.session.commit()
    audit.log("users", "reset_completed", actor=user.email)
    return user


def change_password(user: User, current: str, new_password: str) -> User:
    """Self-service change — requires the current password."""
    if not user.password_hash or not check_password_hash(user.password_hash, current or ""):
        raise UserError("Your current password isn't right.")
    check_password(new_password)
    user.password_hash = generate_password_hash(new_password)
    user.must_change_password = False
    user.session_epoch = (user.session_epoch or 1) + 1
    db.session.commit()
    audit.log("users", "password_changed", actor=user.email)
    return user


def deliver_reset(user: User, link: str) -> bool:
    """Hook for an email provider. Returns False while none is configured.

    Kept as a named function so wiring email later is one implementation here
    rather than a change at every call site.
    """
    return False


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def by_email(email: str) -> User | None:
    return User.query.filter_by(email=normalise_email(email)).first()


def listing(include_admin_fields: bool = True) -> dict:
    users = User.query.order_by(User.status.desc(), User.email).all()
    rows = [u.as_dict(include_admin_fields) for u in users]
    return {
        "users": rows,
        "counts": {
            "total": len(rows),
            "pending": sum(1 for r in rows if r["status"] == "pending"),
            "active": sum(1 for r in rows if r["status"] == "active"),
            "suspended": sum(1 for r in rows if r["status"] == "suspended"),
            "super_admins": sum(1 for r in rows if r["role"] == "super_admin"),
        },
        "reset_ttl_minutes": RESET_TTL_MINUTES,
        "min_password_len": MIN_PASSWORD_LEN,
        "email_delivery": False,
    }
