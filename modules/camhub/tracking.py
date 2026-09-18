"""Impressions and clicks a sponsor could have audited.

Most local ad programs count page loads and call them impressions. If a
number is handed to a sponsor every month, it has to be one that survives
their own analytics being held up beside it. So:

- **A viewable impression** is what the page's own script reports after an
  `IntersectionObserver` has seen at least half of the unit for one
  continuous second (the IAB display standard), once per unit per
  pageview, never re-counted on scroll-back. The server takes the
  script's word for the timing and checks everything else.
- **A click** goes through `/go/<placement_id>`, which records it and
  answers a 302 to the destination. A second click within two seconds
  from the same session is a double-click, kept and marked.
- **Filtering never deletes.** A crawler user agent, a click before the
  page could have been read, a pageview with no scroll and a click, a
  session or an address past the per-minute rate: each is written with
  `filtered` and the reason, and the rollup leaves it out.
- **The address is hashed with the Hub secret and truncated.** Nothing
  here can give a visitor's IP back.
- **The rollup** writes one row per placement per day and one per page,
  reads only unfiltered rows, and purges raw events past ninety days.
  Reports read the rollup, never the raw table.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select

from .models import DailyStat, Event, Placement, session

log = logging.getLogger("hub")

KINDS = ("pageview", "impression", "click")
RAW_RETENTION_DAYS = 90
DOUBLE_CLICK_SECONDS = 2
INSTANT_CLICK_MS = 500
SESSION_EVENTS_PER_MINUTE = 60
IP_PAGEVIEWS_PER_MINUTE = 30
MAX_BATCH = 40
CRAWLER_RE = re.compile(r"bot|crawl|spider|slurp|headless|phantom|lighthouse|pagespeed|python-requests|"
                        r"curl/|wget/|go-http-client|java/|okhttp|facebookexternalhit|preview|monitor|scan",
                        re.IGNORECASE)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _secret() -> str:
    try:
        from hub.signing import value
        return value() or "camhub"
    except Exception:  # noqa: BLE001
        return "camhub"


def hash_ip(ip: str) -> str:
    return hashlib.sha256(f"{_secret()}:ip:{ip or ''}".encode()).hexdigest()[:24]


def hash_session(token: str, ip: str = "", ua: str = "") -> str:
    """The page's own random session token, hashed; without one (no script
    ran, a bare click) the address and agent stand in for the session."""
    seed = token.strip() if token and len(token.strip()) >= 8 else f"{ip}|{ua[:80]}"
    return hashlib.sha256(f"{_secret()}:s:{seed}".encode()).hexdigest()[:32]


def device_class(ua: str) -> str:
    ua = ua or ""
    if re.search(r"iPad|Tablet", ua):
        return "tablet"
    if re.search(r"Mobi|Android|iPhone", ua):
        return "mobile"
    return "desktop"


def referrer_host(ref: str) -> str:
    from urllib.parse import urlsplit
    try:
        return (urlsplit(ref or "").hostname or "")[:120]
    except ValueError:
        return ""


def is_crawler(ua: str) -> bool:
    if not ua:
        return True
    if CRAWLER_RE.search(ua):
        return True
    try:
        from hub.no_crawl import AI_CRAWLERS, SEARCH_CRAWLERS
        low = ua.lower()
        return any(name.lower() in low for name in AI_CRAWLERS + SEARCH_CRAWLERS)
    except Exception:  # noqa: BLE001
        return False


def _local_day(at: datetime, tz_name: str) -> str:
    try:
        return at.astimezone(ZoneInfo(tz_name or "UTC")).date().isoformat()
    except Exception:  # noqa: BLE001
        return at.date().isoformat()


def _recent_count(s, *, session_hash: str = "", ip_hash: str = "", kind: str | None = None,
                  seconds: int = 60) -> int:
    q = select(func.count(Event.id)).where(Event.at >= _now() - timedelta(seconds=seconds))
    if session_hash:
        q = q.where(Event.session_hash == session_hash)
    if ip_hash:
        q = q.where(Event.ip_hash == ip_hash)
    if kind:
        q = q.where(Event.kind == kind)
    return int(s.execute(q).scalar() or 0)


# ---------------------------------------------------------------- ingest

def ingest(page: dict, payload: dict, *, ip: str, user_agent: str, referrer: str = "") -> dict:
    """One batch from one pageview: {"session", "pageview": {"scrolled",
    "ms"}, "events": [{"kind", "placement", "position", "ms"}]}. Returns
    what was written and what was filtered, by reason."""
    if not isinstance(payload, dict):
        raise ValueError("a JSON object is expected")
    ua = (user_agent or "")[:200]
    token = str(payload.get("session") or "")
    sh, ih = hash_session(token, ip, ua), hash_ip(ip)
    pv = payload.get("pageview") if isinstance(payload.get("pageview"), dict) else {}
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    if len(events) > MAX_BATCH:
        raise ValueError(f"at most {MAX_BATCH} events per batch")
    scrolled = bool(pv.get("scrolled"))
    try:
        ms_on_page = max(0, min(int(pv.get("ms") or 0), 6 * 3600 * 1000))
    except (TypeError, ValueError):
        ms_on_page = 0
    now = _now()
    day = _local_day(now, page.get("timezone") or "UTC")
    ref_host = referrer_host(referrer)
    dev = device_class(ua)

    reason = ""
    if is_crawler(ua):
        reason = "crawler"
    with session() as s:
        if not reason and _recent_count(s, session_hash=sh) >= SESSION_EVENTS_PER_MINUTE:
            reason = "session_rate"
        if not reason and _recent_count(s, ip_hash=ih, kind="pageview") >= IP_PAGEVIEWS_PER_MINUTE:
            reason = "ip_rate"
        placements = {r.id: r for r in s.execute(select(Placement).where(
            Placement.page_id == page["id"])).scalars().all()}
        written, filtered = 0, {}
        rows = []
        if payload.get("pageview") is not None:
            rows.append(Event(page_id=page["id"], kind="pageview", at=now, day=day, session_hash=sh,
                              ip_hash=ih, device=dev, referrer_host=ref_host, user_agent=ua,
                              scrolled=scrolled, ms_on_page=ms_on_page,
                              filtered=bool(reason), filter_reason=reason or None))
        seen = set()
        for ev in events:
            if not isinstance(ev, dict):
                continue
            kind = str(ev.get("kind") or "")
            if kind not in ("impression", "click"):
                continue
            try:
                pid = int(ev.get("placement"))
                pos = int(ev.get("position") or 0)
                ms = int(ev.get("ms") or 0)
            except (TypeError, ValueError):
                continue
            pl = placements.get(pid)
            if pl is None or pl.is_house:
                continue                        # not this page's, or the client's own: not counted
            key = (kind, pid)
            if key in seen:
                continue                        # once per unit per pageview, whatever the script sent
            seen.add(key)
            ev_reason = reason
            if not ev_reason and kind == "click" and ms < INSTANT_CLICK_MS:
                ev_reason = "instant_click"
            if not ev_reason and kind == "click" and not scrolled and pos > 0:
                ev_reason = "no_scroll"
            rows.append(Event(page_id=page["id"], placement_id=pid, sponsor_id=pl.sponsor_id, kind=kind,
                              at=now, day=day, position=pos, session_hash=sh, ip_hash=ih, device=dev,
                              referrer_host=ref_host, user_agent=ua, scrolled=scrolled, ms_on_page=ms,
                              filtered=bool(ev_reason), filter_reason=ev_reason or None))
        for row in rows:
            s.add(row)
            if row.filtered:
                filtered[row.filter_reason] = filtered.get(row.filter_reason, 0) + 1
            else:
                written += 1
        s.commit()
    return {"written": written, "filtered": filtered, "session": sh[:8]}


def click(placement_id: int, *, session_token: str, ip: str, user_agent: str,
          referrer: str = "") -> dict | None:
    """Record the click and answer where to send them, or None when the
    placement is unknown or has nowhere to go."""
    ua = (user_agent or "")[:200]
    with session() as s:
        pl = s.get(Placement, int(placement_id))
        if pl is None or not (pl.url or "").strip():
            return None
        page_tz = "UTC"
        from .models import CamPage
        page = s.get(CamPage, pl.page_id)
        if page is not None:
            page_tz = page.timezone or "UTC"
        sh, ih = hash_session(session_token, ip, ua), hash_ip(ip)
        now = _now()
        reason = ""
        if pl.is_house:
            reason = "house"
        elif is_crawler(ua):
            reason = "crawler"
        else:
            last = s.execute(select(Event.at).where(
                Event.kind == "click", Event.placement_id == pl.id, Event.session_hash == sh)
                .order_by(Event.at.desc()).limit(1)).scalar()
            if last is not None:
                last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() < DOUBLE_CLICK_SECONDS:
                    reason = "double_click"
            if not reason and _recent_count(s, session_hash=sh, kind="click") >= 10:
                reason = "session_rate"
        s.add(Event(page_id=pl.page_id, placement_id=pl.id, sponsor_id=pl.sponsor_id, kind="click",
                    at=now, day=_local_day(now, page_tz), position=0 if pl.position == "presenting" else None,
                    session_hash=sh, ip_hash=ih, device=device_class(ua),
                    referrer_host=referrer_host(referrer), user_agent=ua,
                    filtered=bool(reason), filter_reason=reason or None))
        s.commit()
        return {"url": pl.url, "filtered": reason or None}


# ---------------------------------------------------------------- rollup

def rollup(day: str, page_id: int | None = None) -> dict:
    """One row per placement per day and one per page per day, from the
    unfiltered raw rows. Idempotent: run it again and the same rows are
    rewritten, which is what lets it run hourly for today."""
    written = 0
    with session() as s:
        q = select(Event).where(Event.day == day)
        if page_id is not None:
            q = q.where(Event.page_id == page_id)
        rows = s.execute(q).scalars().all()
        per: dict[tuple, dict] = {}
        for ev in rows:
            keys = [(ev.page_id, None)]
            if ev.placement_id:
                keys.append((ev.page_id, ev.placement_id))
            for key in keys:
                agg = per.setdefault(key, {"pageviews": 0, "impressions": 0, "clicks": 0,
                                           "sessions": set(), "filtered": 0, "sponsor_id": None})
                if ev.filtered:
                    agg["filtered"] += 1
                    continue
                if key[1] is None:
                    if ev.kind == "pageview":
                        agg["pageviews"] += 1
                        agg["sessions"].add(ev.session_hash)
                    elif ev.kind == "impression":
                        agg["impressions"] += 1
                    elif ev.kind == "click":
                        agg["clicks"] += 1
                else:
                    agg["sponsor_id"] = ev.sponsor_id
                    if ev.kind == "impression":
                        agg["impressions"] += 1
                        agg["sessions"].add(ev.session_hash)
                    elif ev.kind == "click":
                        agg["clicks"] += 1
                        agg["sessions"].add(ev.session_hash)
        for (pid, plid), agg in per.items():
            row = s.execute(select(DailyStat).where(
                DailyStat.page_id == pid, DailyStat.placement_id == plid, DailyStat.day == day)
            ).scalar_one_or_none()
            if row is None:
                row = DailyStat(page_id=pid, placement_id=plid, day=day)
                s.add(row)
            row.sponsor_id = agg["sponsor_id"]
            row.pageviews = agg["pageviews"]
            row.impressions = agg["impressions"]
            row.clicks = agg["clicks"]
            row.unique_sessions = len(agg["sessions"])
            row.filtered = agg["filtered"]
            written += 1
        s.commit()
    return {"day": day, "rows": written}


def purge(days: int = RAW_RETENTION_DAYS) -> int:
    cutoff = _now() - timedelta(days=days)
    with session() as s:
        result = s.execute(delete(Event).where(Event.at < cutoff))
        s.commit()
        return int(result.rowcount or 0)


def rollup_recent(today: date | None = None) -> dict:
    """Today and yesterday, every page, then the purge. The scheduler's
    hourly call; a day is final once it is two days old."""
    today = today or _now().date()
    out = {"days": [], "purged": 0}
    for d in (today - timedelta(days=1), today):
        out["days"].append(rollup(d.isoformat()))
    out["purged"] = purge()
    return out


# --------------------------------------------------------------- reading

def stats(page_id: int, start: str, end: str) -> dict:
    """The rollup between two ISO days inclusive: the page's totals and one
    entry per placement, with CTR and the share of pageviews the unit was
    viewable on -- the number that makes the presenting slot obviously
    worth more, and defends the price without arguing it."""
    with session() as s:
        rows = s.execute(select(DailyStat).where(
            DailyStat.page_id == page_id, DailyStat.day >= start, DailyStat.day <= end)).scalars().all()
    page = {"pageviews": 0, "impressions": 0, "clicks": 0, "unique_sessions": 0, "filtered": 0, "daily": {}}
    per: dict[int, dict] = {}
    for r in rows:
        if r.placement_id is None:
            page["pageviews"] += r.pageviews or 0
            page["unique_sessions"] += r.unique_sessions or 0
            page["filtered"] += r.filtered or 0
            page["daily"][r.day] = {"pageviews": r.pageviews or 0, "impressions": r.impressions or 0,
                                    "clicks": r.clicks or 0}
        else:
            agg = per.setdefault(r.placement_id, {"placement_id": r.placement_id, "sponsor_id": r.sponsor_id,
                                                  "impressions": 0, "clicks": 0, "unique_sessions": 0,
                                                  "filtered": 0, "daily": {}})
            agg["impressions"] += r.impressions or 0
            agg["clicks"] += r.clicks or 0
            agg["unique_sessions"] += r.unique_sessions or 0
            agg["filtered"] += r.filtered or 0
            agg["daily"][r.day] = {"impressions": r.impressions or 0, "clicks": r.clicks or 0}
    for agg in per.values():
        agg["ctr"] = round(100.0 * agg["clicks"] / agg["impressions"], 2) if agg["impressions"] else 0.0
        agg["viewable_share"] = (round(100.0 * agg["impressions"] / page["pageviews"])
                                 if page["pageviews"] else None)
    return {"start": start, "end": end, "page": page, "placements": per}


def month_bounds(today: date | None = None) -> tuple[str, str]:
    today = today or _now().date()
    return today.replace(day=1).isoformat(), today.isoformat()
