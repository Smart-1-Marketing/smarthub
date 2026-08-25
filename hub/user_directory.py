"""The staff roster, as data, and the profile fields an account carries.

Everyone in the company was uploaded from one census export, so the roster is
kept here in the shape that export had rather than being typed into a form
fourteen times. `sync_roster()` runs on boot and is idempotent.

## What a re-run does, and deliberately does not do

**It creates accounts that are missing and nothing else.** Three things must
survive a deploy, and each is a way this could quietly undo somebody's work:

  * **A password is never reset.** The default password is written at creation
    only. A sync that re-applied it would hand everyone's account back to the
    documented default on every deploy -- with every screen still green, and no
    log line anybody reads saying so.
  * **A role is never demoted.** Promote someone in the Users panel and the
    next boot must not take it back. The roster says what a person *starts*
    as, which is not the same claim as what they are now.
  * **A profile field is filled in, never overwritten.** Somebody who corrects
    a phone number in the panel has better information than the export does.

That leaves the one case a re-run must handle: a person added to the roster
after the first boot gets an account on the next one.

## The default password, and why it is not run through the policy

`users.check_password()` refuses anything under 12 characters and anything
containing "smart1". `Smart12026!` is both -- which is correct for a password
somebody chooses and wrong for a starting credential that exists to be
replaced. So it is written through `users.set_starting_password()`, which
bypasses the policy *and* sets `must_change_password`, and those two are one
call precisely so a starting password cannot be issued without the gate that
retires it. The policy still applies to whatever they replace it with.

## Two levels, three roles

The census has two: General and Admin. The Hub has three, because
`super_admin` already existed and guards the "don't lock yourself out of the
admin panel" rules. Admin maps to `admin`, General to `member`, and the three
founding super admins keep `super_admin` -- a strictly wider role than the
Admin the census asked for, so nobody is under-privileged by the mapping.
`hub/access.py` reads `is_admin`, which is true for both admin roles, so the
distinction is invisible to every access decision.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String

from hub import audit
from hub.extensions import db

# The starting password every roster account is created with. Documented here
# rather than in an environment variable because it is not a secret: it is
# valid for exactly one sign-in, and the forced change in hub/users_routes.py
# is what makes that true. HUB_DEFAULT_PASSWORD overrides it for a deployment
# that would rather not have it written down.
DEFAULT_PASSWORD = "Smart12026!"

# Who a person locked out is told to ask. One constant, read by the
# forgot-password page and the sign-in copy, so the two cannot name different
# people -- which is the whole failure mode of a message like this.
SUPPORT_NAME = "John"
SUPPORT_EMAIL = "john@smart1marketing.com"

LEVEL_GENERAL = "General"
LEVEL_ADMIN = "Admin"

# Census level -> Hub role. Only ever consulted at account creation.
_ROLE_FOR_LEVEL = {LEVEL_ADMIN: "admin", LEVEL_GENERAL: "member"}


# ---------------------------------------------------------------------------
# The roster
# ---------------------------------------------------------------------------
#
# Transcribed from Census_1.csv exactly: level, first, last, title, phone,
# birthday, date of hire, work email. Dates are ISO here rather than the
# export's MM/DD/YYYY, because a bare 04/17/1971 is ambiguous the moment
# anybody outside the US reads it and the export is not the thing that has to
# stay readable.
ROSTER = (
    (LEVEL_GENERAL, "Aimee", "Tacey", "Senior Campaign Strategist",
     "309/631-2397", "1971-04-17", "2021-06-01", "aimee@smart1marketing.com"),
    (LEVEL_GENERAL, "Brandon", "Lipps", "Data Specialist",
     "614/623-3497", "1999-02-23", "2019-08-01", "brandon@smart1marketing.com"),
    (LEVEL_GENERAL, "Erik", "Schmidt", "VP of Strategic Partnership",
     "614/551-0969", "1975-11-23", "2024-04-16", "erik@smart1marketing.com"),
    (LEVEL_ADMIN, "George", "Roberts", "VP Of Innovation",
     "860/690-6051", "1975-08-13", "2019-08-01", "george@smart1marketing.com"),
    (LEVEL_GENERAL, "Jaclyn", "Johnsen", "Senior Campaign Strategist",
     "407/607-5758", "1980-05-15", "2021-12-21", "jaclyn@smart1marketing.com"),
    (LEVEL_GENERAL, "James", "Hern", "Creative Marketing Director",
     "614/353-0854", "1963-08-27", "2023-09-01", "jim@smart1marketing.com"),
    (LEVEL_ADMIN, "John", "Koenig", "Director Of Web Services",
     "614/886-8221", "1964-06-09", "2019-08-01", "john@smart1marketing.com"),
    (LEVEL_ADMIN, "Kaden", "Ferguson", "Director of Data Innovation",
     "614/462-0804", "1998-12-04", "2019-08-01", "kaden@smart1marketing.com"),
    (LEVEL_GENERAL, "Lauren", "Jordan", "Digital Advertising Trafficker",
     "517/902-6082", "1995-08-18", "2023-07-29", "lauren@smart1marketing.com"),
    (LEVEL_ADMIN, "Louann", "Johnson", "VP of Product Strategy",
     "614 4968470", "1954-04-27", "2019-11-04", "louann@smart1marketing.com"),
    (LEVEL_GENERAL, "Michael", "Hawkins", "Sr Mgr Of Campaign Strategy",
     "614/607-4336", "1972-08-07", "2019-08-01", "mhawkins@smart1marketing.com"),
    (LEVEL_GENERAL, "Todd", "Johnston", "Senior Solutions Strategist",
     "207/852-9662", "1968-01-16", "2025-04-08", "tjohnston@smart1marketing.com"),
    (LEVEL_ADMIN, "Todd", "Swickard", "Ceo",
     "614/289-8655", "1972-02-23", "2019-08-01", "todd@smart1marketing.com"),
    (LEVEL_GENERAL, "Traci", "Thompson", "Sr Client Success Specialist",
     "614/327-8094", "1982-05-06", "2019-08-01", "traci@smart1marketing.com"),
)


def roster_rows() -> list[dict]:
    """The roster as dicts, phone numbers formatted, dates validated."""
    out = []
    for level, first, last, title, phone, birthday, hired, email in ROSTER:
        out.append({
            "level": level,
            "role": _ROLE_FOR_LEVEL[level],
            "first_name": first,
            "last_name": last,
            "name": f"{first} {last}",
            "title": title,
            "phone": format_phone(phone),
            "phone_raw": phone,
            "birthday": iso_date(birthday),
            "hired_at": iso_date(hired),
            "email": email.strip().lower(),
        })
    return out


# ---------------------------------------------------------------------------
# Field cleaning
# ---------------------------------------------------------------------------

def format_phone(value: str) -> str:
    """(614) 886-8221 from whatever separators the export happened to use.

    The census writes most numbers 614/886-8221 and one of them 614 4968470,
    so the separators carry no information and the digits are the whole value.
    A number that is not ten digits is returned **as it was given** rather than
    padded, truncated or rejected: a phone number nobody can dial is a worse
    answer than an oddly formatted one, and inventing the missing digit is the
    kind of confident wrong answer this codebase treats as worse than a gap.
    """
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return (value or "").strip()
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def iso_date(value: str) -> str:
    """Accepts ISO or the export's MM/DD/YYYY. Returns "" for anything else.

    Empty rather than today's date, and empty rather than a guess: a birthday
    the Hub invented would read on the panel exactly like one somebody
    confirmed.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def pretty_date(iso: str) -> str:
    """1971-04-17 -> 17 Apr 1971. Unparseable input comes back untouched."""
    try:
        return date.fromisoformat(iso).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return iso or ""


def years_of_service(iso: str, today: date | None = None) -> float | None:
    """Whole years since the hire date, or None when there is no hire date.

    None rather than 0.0 — "started today" and "we never recorded a start
    date" are different answers and only one of them means look it up.
    """
    try:
        start = date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    now = today or datetime.now(timezone.utc).date()
    return round((now - start).days / 365.25, 1)


# ---------------------------------------------------------------------------
# The profile row
# ---------------------------------------------------------------------------

class UserProfile(db.Model):
    """The census fields, in their own table beside hub_users.

    A separate table rather than six more columns on `hub_users`, and that is
    not a style preference: `create_all()` creates missing tables and **never
    adds a column to an existing one**, so a column added to `hub_users` here
    would exist on every local SQLite run and be silently absent on the live
    Postgres — every test green, every read of it None in production. The same
    reasoning `hub/client_key.py` gives for not storing a derived key.

    Keyed on email rather than the user id because email is the account's
    identity everywhere else in this file, and because a profile that outlives
    a deleted-and-recreated account is harmless while a profile silently
    attached to a recycled row id is not. `users.delete()` removes it.
    """
    __tablename__ = "hub_user_profiles"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    first_name = Column(String(80), default="")
    last_name = Column(String(80), default="")
    title = Column(String(160), default="")
    phone = Column(String(40), default="")
    birthday = Column(String(10), default="")        # ISO, or "" for not given
    hired_at = Column(String(10), default="")        # ISO, or "" for not given
    level = Column(String(20), default=LEVEL_GENERAL)
    source = Column(String(40), default="")          # "census" or "panel"
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict:
        return {
            "email": self.email,
            "first_name": self.first_name or "",
            "last_name": self.last_name or "",
            "title": self.title or "",
            "phone": self.phone or "",
            "birthday": self.birthday or "",
            "birthday_pretty": pretty_date(self.birthday or ""),
            "hired_at": self.hired_at or "",
            "hired_at_pretty": pretty_date(self.hired_at or ""),
            "years_of_service": years_of_service(self.hired_at or ""),
            "source": self.source or "",
        }


_PROFILE_FIELDS = ("first_name", "last_name", "title", "phone",
                   "birthday", "hired_at")


def profile_for(email: str) -> UserProfile | None:
    return UserProfile.query.filter_by(email=(email or "").strip().lower()).first()


def profiles_by_email() -> dict[str, dict]:
    """Every profile in one read — the Users panel needs all of them at once."""
    try:
        return {p.email: p.as_dict() for p in UserProfile.query.all()}
    except Exception:                                   # noqa: BLE001
        # A deployment mid-deploy may not have the table yet. An empty map
        # renders the panel with the profile columns blank, which is the
        # honest answer; raising would take the whole page down.
        return {}


def save_profile(email: str, values: dict, *, source: str = "panel",
                 overwrite: bool = True) -> UserProfile:
    """Write a profile. With ``overwrite=False`` only fills in blanks.

    The roster sync passes overwrite=False so a corrected phone number in the
    panel survives the next deploy.
    """
    email = (email or "").strip().lower()
    row = profile_for(email)
    if row is None:
        row = UserProfile(email=email, source=source)
        db.session.add(row)
    for field in _PROFILE_FIELDS:
        if field not in values:
            continue
        new = (values.get(field) or "").strip()
        if field == "phone":
            new = format_phone(new)
        if field in ("birthday", "hired_at"):
            new = iso_date(new)
        if not overwrite and (getattr(row, field) or ""):
            continue
        if new or overwrite:
            setattr(row, field, new)
    if values.get("level") in (LEVEL_GENERAL, LEVEL_ADMIN):
        if overwrite or not row.level:
            row.level = values["level"]
    row.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return row


def delete_profile(email: str) -> None:
    row = profile_for(email)
    if row is not None:
        db.session.delete(row)
        db.session.commit()


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

def sync_roster() -> dict:
    """Create any roster account that doesn't exist and fill in its profile.

    Returns a summary rather than a count, so the boot log and /api/users can
    both say which of the three things happened: an account created, a profile
    filled in, or a row left exactly as it was.
    """
    from hub import users

    created, profiled, untouched = [], [], []
    for row in roster_rows():
        user = users.by_email(row["email"])
        if user is None:
            user = users.create_account(
                email=row["email"], name=row["name"], role=row["role"],
                password=default_password(), status="active",
                approved_by="census")
            created.append(row["email"])
        else:
            untouched.append(row["email"])
            # A seeded super admin with no password yet is not "untouched" in
            # any useful sense — they cannot sign in. Give them the starting
            # password so the roster upload means the same thing for all
            # fourteen people.
            if not user.password_hash:
                users.set_starting_password(user, default_password())
                if user.status == "pending":
                    users.activate(user, by="census")
                untouched.pop()
                created.append(row["email"])

        before = profile_for(row["email"])
        save_profile(row["email"], row, source="census", overwrite=False)
        if before is None:
            profiled.append(row["email"])

    out = {"created": created, "profiled": profiled, "unchanged": untouched,
           "roster_size": len(ROSTER)}
    if created or profiled:
        audit.log("users", "roster_synced", created=len(created),
                  profiled=len(profiled), roster=len(ROSTER))
    return out


def default_password() -> str:
    import os
    return os.environ.get("HUB_DEFAULT_PASSWORD") or DEFAULT_PASSWORD
