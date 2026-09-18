"""The read-only sponsor portal: one URL, no password, their own numbers.

The spec's second reporting deliverable, per docs/camhub-spec.md section
"The self-serve dashboard": "scoped so a sponsor sees only their own
placements", read-only, with a downloadable CSV. The Hub has no external
user model and a login for every sponsor is more account than the value
in the portal justifies; a signed URL per sponsor gets the same result
and matches the preview-token pattern the module already uses.

- One `sponsor_token(sponsor_id)` mints a signed opaque string, keyed
  on the Hub secret, that resolves to that sponsor id and nothing else.
- The token is timed but with a long ceiling (400 days), so a link a
  sponsor bookmarks keeps working past a year of quiet weeks, and
  regenerating the token from the sponsor screen invalidates the old
  one at leisure.
- The portal reads the *current* placement rows on every load. If a
  supporting slot ends, the sponsor's portal shows that; if the creative
  is refreshed by staff, the sponsor sees the new copy immediately.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .models import Sponsor, session
from . import reports

log = logging.getLogger("hub")

PORTAL_SALT = "camhub-portal"
PORTAL_MAX_AGE = 400 * 24 * 60 * 60      # a bookmarked link survives a year


def mint(sponsor_id: int) -> str:
    from hub.signing import timed_serializer
    # The token itself carries no `iat` -- URLSafeTimedSerializer signs
    # a timestamp alongside the payload and returns it from .loads() as
    # `return_timestamp=True`. That is what read() checks against the
    # sponsor's `portal_rotated_at`.
    return timed_serializer(PORTAL_SALT).dumps({"sponsor": int(sponsor_id)})


def read(token: str) -> int | None:
    """Resolve a token to a sponsor id, or None when the token is invalid,
    expired, or predates the sponsor's most recent rotation."""
    from itsdangerous import BadSignature
    from hub.signing import timed_serializer
    try:
        claim, issued_at = timed_serializer(PORTAL_SALT).loads(
            token, max_age=PORTAL_MAX_AGE, return_timestamp=True)
    except BadSignature:
        return None
    if not isinstance(claim, dict):
        return None
    sid = claim.get("sponsor")
    try:
        sid = int(sid) if sid is not None else None
    except (TypeError, ValueError):
        return None
    if sid is None:
        return None
    # Rotation cutoff: reject a token that predates the sponsor's most
    # recent rotation. `portal_rotated_at` is set by the staff rotate
    # button on /sponsors/<id>; a sponsor that has never been rotated
    # has None here and every unexpired token stays valid.
    with session() as s:
        sponsor = s.get(Sponsor, sid)
        if sponsor is None:
            return None
        rotated = getattr(sponsor, "portal_rotated_at", None)
    if rotated is not None:
        # itsdangerous returns a naive UTC datetime; SQLite hands back
        # naive UTC too (its DateTime(timezone=True) has no effect there),
        # while Postgres hands back aware UTC. Normalize both to aware
        # UTC before comparing so a mix of backends can't mis-reject.
        from datetime import timezone as _tz
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=_tz.utc)
        if rotated.tzinfo is None:
            rotated = rotated.replace(tzinfo=_tz.utc)
        # Strict less-than: itsdangerous timestamps have one-second
        # precision, so a token minted in the same wall-clock second as
        # `rotate()` sets the cutoff shares that second. Only the freshly
        # minted token can be issued in the rotation's own second (an
        # older token was minted earlier, in an earlier second), so `<`
        # rejects the old ones and admits the new one.
        if issued_at < rotated:
            return None
    return sid


def rotate(sponsor_id: int, *, actor: str = "") -> str:
    """Turn off every portal link previously issued for this sponsor by
    setting `portal_rotated_at = now()`, then mint a fresh one and return
    it. Called from the staff `/sponsors/<id>/rotate-portal` route."""
    from datetime import datetime, timezone
    with session() as s:
        sponsor = s.get(Sponsor, int(sponsor_id))
        if sponsor is None:
            raise LookupError(f"sponsor {sponsor_id} not found")
        # itsdangerous timestamps have one-second precision; keeping
        # microseconds on the rotation cutoff would mean a freshly-minted
        # token from the same second reads as strictly earlier and gets
        # rejected. Truncate.
        sponsor.portal_rotated_at = datetime.now(timezone.utc).replace(microsecond=0)
        s.commit()
    log.info("camhub_portal_rotated sponsor=%s actor=%s", sponsor_id, actor or "unknown")
    # A freshly-minted token carries a timestamp >= now, so the very next
    # read() beats the rotation cutoff and hands the sponsor back a
    # working link.
    return mint(int(sponsor_id))


def _default_range(today: date | None = None) -> tuple[str, str]:
    """The window that opens when the portal is first hit: the current
    month to date. A month is what the report ships and the portal
    should default to the same period; the picker moves it from there."""
    today = today or date.today()
    start = today.replace(day=1)
    return start.isoformat(), today.isoformat()


def _parse_range(start: str, end: str, today: date | None = None) -> tuple[str, str]:
    today = today or date.today()
    d0, d1 = _default_range(today)
    if start:
        try:
            d0 = date.fromisoformat(start).isoformat()
        except ValueError:
            # A garbage `?start=` from a bookmark or a typo falls back to
            # the default -- the picker is a convenience, not a validator.
            pass
    if end:
        try:
            d1 = date.fromisoformat(end).isoformat()
        except ValueError:
            # Same as above -- an unparseable end date keeps the default.
            pass
    # Clamp: end must be on or after start, and neither may be in the
    # future -- the rollup does not write today until the next hour.
    a = date.fromisoformat(d0)
    b = date.fromisoformat(d1)
    if b < a:
        b = a
    if a > today:
        a = today
    if b > today:
        b = today
    return a.isoformat(), b.isoformat()


def sponsor_view(sponsor_id: int, start: str = "", end: str = "",
                 today: date | None = None) -> dict:
    """Everything the portal template renders in one payload."""
    today = today or date.today()
    with session() as s:
        sponsor = s.get(Sponsor, int(sponsor_id))
        if sponsor is None:
            raise LookupError(f"sponsor {sponsor_id} not found")
        name = sponsor.name
        category = sponsor.category or ""
    a, b = _parse_range(start, end, today=today)
    try:
        window = reports.sponsor_range(int(sponsor_id), a, b)
    except LookupError:
        window = {"sponsor": {"id": int(sponsor_id), "name": name, "category": category},
                  "window": {"start": a, "end": b,
                             "days": (date.fromisoformat(b) - date.fromisoformat(a)).days + 1},
                  "totals": {"impressions": 0, "clicks": 0, "ctr": 0.0,
                             "viewable_share": None},
                  "placements": [], "daily": []}
    monthly = None
    try:
        # The current-month card at the top of the portal: same numbers
        # the PDF will show on the 1st, so a sponsor visiting today
        # sees where the month is running.
        monthly = reports.sponsor_monthly(int(sponsor_id), today.year, today.month,
                                          today=today)
    except LookupError:
        monthly = None
    return {"sponsor": {"id": int(sponsor_id), "name": name, "category": category},
            "window": window["window"],
            "totals": window["totals"],
            "placements": window["placements"],
            "creative": (monthly["placements"] if monthly else []),
            "flights": (monthly["flights"] if monthly else []),
            "daily": window["daily"],
            "current_month": (monthly["totals"] if monthly else None),
            "current_period": (monthly["period"] if monthly else None),
            "footnote_text": ("A viewable impression is what the page's "
                              "script reports after at least half of the ad "
                              "has been on screen for a continuous second "
                              "(the IAB display standard). Crawler traffic, "
                              "instant clicks and clicks on unscrolled pages "
                              "are excluded."),
            "min_date": (today - timedelta(days=400)).isoformat(),
            "max_date": today.isoformat()}
