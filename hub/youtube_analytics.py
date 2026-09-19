"""The other half of a client's YouTube channel: watch time, subscribers
gained and traffic sources, from the YouTube Analytics API rather than the
keyed Data API v3 that ``hub/youtube.py`` already reads.

``hub/youtube.py``'s own docstring names this gap: "The Analytics API half
-- watch time, subscribers gained per day, traffic sources -- is behind
OAuth and is not built, because it is a scope on Google Finder's list and
every login connected before it keeps its old grant and has to re-consent."
That was the only OAuth path this Hub had for a YouTube channel at the
time it was written. ``modules/youtube_studio`` has since grown its own,
independent one: a per-client, per-channel OAuth connection, already
requesting ``yt-analytics.readonly``, already calling this exact API for
its own internal "which channel is active" recommendation
(``optimization.activity()``). Building a second OAuth flow through Google
Finder would be the two-systems-answering-one-question failure this
codebase keeps having to undo one module later -- so this reads through
the connection that already exists rather than asking for a new one.

**The join is the channel id, never the client's name.** Each tool spells
a client's name its own way -- ``hub/client_key.py``'s derived key here,
a raw casefolded hash in ``modules/youtube_studio/store.py`` -- and a
name match across two systems is exactly the "two spellings of one
client" failure this codebase has paid for twice already
(``hub/proposal_adapters/reports.py``). The channel id is the one
identifier both tools agree on: a client's *confirmed* channel
(``hub.youtube.record``) is looked up in youtube_studio's own store by
that id, never by re-deriving or comparing a name.

**Unconnected is a real, common answer, and it is not an error.** Most
confirmed channels will have no youtube_studio connection -- that tool is
for a channel we actively manage (uploads, drafts, launch), and plenty of
clients' channels are simply *theirs*, read for the keyed counts alone.
``reading()`` says ``not_connected`` for that case, staff-only, and the
client's page carries nothing for this section at all: a card announcing
"not connected" on a document about their business is a sentence about our
tooling, the rule every reading in this file already follows one module
over.

**A reading is taken once a night, alongside the keyed one, never on a
page load.** The Analytics API has its own daily quota, separate from the
Data API v3's; a client's report page is opened by clients.
``hub.youtube.snapshot()``'s Confirm/Refresh presses call ``snapshot()``
here too, so one button on the card refreshes both halves; the nightly
sweep is its own scheduler job so a leader restarting through the window
still catches up on its own tick, the ``places_snapshot`` shape.

**The window is the period, not a delta between two readings of ours.**
Watch time and subscribers gained/lost are period metrics YouTube Analytics
answers directly for the trailing window -- unlike the Data API's lifetime
counters, there is no "our own two readings, subtracted" arithmetic here.
The window ends two days back (``ANALYTICS_LAG_DAYS``): Analytics data is
not final for the most recent day or two, and a window that included it
would read short on every pull, silently, forever.

**Traffic sources are ranked and capped, never a raw dump.** YouTube
publishes about twenty ``insightTrafficSourceType`` codes; a client's page
gets the top few by views with a house label per code
(``TRAFFIC_SOURCE_LABELS``), because "EXT_URL" and "NOTIFICATION" mean
nothing to somebody who does not work here.

Every call goes through ``modules.youtube_studio.youtube.record_google_request``,
so it is metered exactly as every other youtube_studio call is --
``module="youtube_studio"`` -- and ``youtubeanalytics.googleapis.com`` is
in ``hub/quotas._GOOGLE_HOSTS`` under its own bucket, or the usage page
could not name it. Stored through ``hub/jsonstore.py``, keyed on the
client's name, the rule every overlay in this file's family works to.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger(__name__)

MODULE = "youtube_analytics"
ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
# YouTube Analytics is not final for the last day or two; a window ending
# today would read short on every pull, silently, forever.
ANALYTICS_LAG_DAYS = 2
WINDOW_DAYS = 28
TIMEOUT = 20
KEEP_READINGS = 400
REFRESH_HOUR_ENV = "YOUTUBE_ANALYTICS_REFRESH_HOUR"
SWEEP_BUDGET_SECONDS = 90

STATES = ("ok", "no_reading", "not_connected", "no_channel", "unread")

# A house label per traffic-source code YouTube publishes. Named here rather
# than shown raw: "EXT_URL" and "NOTIFICATION" mean nothing to a client.
TRAFFIC_SOURCE_LABELS = {
    "ADVERTISING": "Ads",
    "ANNOTATION": "In-video annotations",
    "CAMPAIGN_CARD": "Campaign cards",
    "END_SCREEN": "End screens",
    "EXT_URL": "Links from other websites",
    "NO_LINK_EMBEDDED": "Embedded players, no link",
    "NO_LINK_OTHER": "Other, no link",
    "NOTIFICATION": "Notifications",
    "PLAYLIST": "Playlists",
    "PROMOTED": "Promoted content",
    "RELATED_VIDEO": "Suggested videos",
    "SUBSCRIBER": "Subscription feed",
    "YT_CHANNEL": "The channel page",
    "YT_OTHER_PAGE": "Other YouTube pages",
    "YT_PLAYLIST_PAGE": "YouTube playlist pages",
    "YT_SEARCH": "YouTube search",
    "SHORTS": "Shorts feed",
}
MAX_SOURCES = 5


def configured() -> bool:
    """Whether youtube_studio itself can run at all -- the OAuth client and
    token encryption it needs. Never whether any one channel is connected:
    that is ``available()``, per client."""
    try:
        from modules.youtube_studio import youtube as studio_yt
        return bool(studio_yt.status().get("connect_ready"))
    except Exception:                                    # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# The join: a confirmed channel to a youtube_studio connection, by id
# ---------------------------------------------------------------------------

def connection_for(channel_id: str) -> dict | None:
    """The youtube_studio client row connected for this channel id, or
    None. Matched on the channel id alone -- the one identifier both tools
    agree on -- never on a client name, which each tool spells its own
    way. Never returns a token."""
    channel_id = str(channel_id or "").strip()
    if not channel_id:
        return None
    try:
        from modules.youtube_studio import store as studio_store
    except Exception:                                    # noqa: BLE001
        return None
    try:
        data = studio_store.read()
    except Exception:                                    # noqa: BLE001
        return None
    for row in (data.get("clients") or {}).values():
        if not isinstance(row, dict):
            continue
        ch = (row.get("channels") or {}).get(channel_id)
        if isinstance(ch, dict) and ch.get("refresh_token"):
            return {"studio_name": str(row.get("name") or ""), "channel_id": channel_id,
                    "connected_at": ch.get("connected_at"),
                    "connection_error": str(ch.get("connection_error") or "")}
    return None


def available(channel_id: str) -> bool:
    return connection_for(channel_id) is not None


# ---------------------------------------------------------------------------
# The wire: one read through youtube_studio's own OAuth connection
# ---------------------------------------------------------------------------

def _fetch(studio_name: str, channel_id: str) -> tuple[dict | None, dict]:
    """One Analytics read: the period totals and the ranked traffic
    sources. ``(data, error)`` -- error names the kind, the
    services/provider_check.py rule: a connection that needs reconnecting
    sends somebody to youtube_studio, an unreachable API sends them to
    retry."""
    import requests
    try:
        from modules.youtube_studio import youtube as studio_yt
    except Exception as exc:                             # noqa: BLE001
        return None, {"kind": "unavailable",
                      "message": f"YouTube Studio is not available ({type(exc).__name__})."}
    try:
        token = studio_yt.access_token(studio_name, channel_id)
    except ValueError as exc:
        return None, {"kind": "refused", "message": str(exc)}
    except Exception as exc:                             # noqa: BLE001
        return None, {"kind": "unreachable",
                      "message": f"Could not read the YouTube Studio connection ({type(exc).__name__})."}
    end = date.today() - timedelta(days=ANALYTICS_LAG_DAYS)
    start = end - timedelta(days=WINDOW_DAYS - 1)
    ids = "channel==" + channel_id
    try:
        totals = studio_yt.checked(studio_yt.record_google_request(
            "GET", ANALYTICS_URL, headers={"Authorization": "Bearer " + token},
            params={"ids": ids, "startDate": start.isoformat(), "endDate": end.isoformat(),
                    "metrics": "views,estimatedMinutesWatched,subscribersGained,subscribersLost"},
            timeout=TIMEOUT))
        sources = studio_yt.checked(studio_yt.record_google_request(
            "GET", ANALYTICS_URL, headers={"Authorization": "Bearer " + token},
            params={"ids": ids, "startDate": start.isoformat(), "endDate": end.isoformat(),
                    "metrics": "views", "dimensions": "insightTrafficSourceType",
                    "sort": "-views", "maxResults": MAX_SOURCES},
            timeout=TIMEOUT))
    except ValueError as exc:
        return None, {"kind": "refused", "message": str(exc)}
    except requests.Timeout:
        return None, {"kind": "unreachable", "message": "YouTube Analytics did not answer in time."}
    except requests.RequestException as exc:
        return None, {"kind": "unreachable",
                      "message": f"YouTube Analytics could not be reached ({type(exc).__name__})."}

    def _row(report: dict) -> dict:
        cols = [str(c.get("name") or "") for c in (report.get("columnHeaders") or [])]
        rows = report.get("rows") or []
        if not cols or not rows or len(rows[0]) != len(cols):
            return {}
        return dict(zip(cols, rows[0]))

    trow = _row(totals)
    if not trow:
        return {"views": None, "watch_minutes": None, "subscribers_gained": None,
                "subscribers_lost": None, "sources": [], "start": start.isoformat(),
                "end": end.isoformat()}, {}
    src_cols = [str(c.get("name") or "") for c in (sources.get("columnHeaders") or [])]
    src_rows = []
    if "insightTrafficSourceType" in src_cols and "views" in src_cols:
        si, vi = src_cols.index("insightTrafficSourceType"), src_cols.index("views")
        for r in sources.get("rows") or []:
            if len(r) != len(src_cols):
                continue
            code = str(r[si] or "")
            src_rows.append({"source": code, "label": TRAFFIC_SOURCE_LABELS.get(code, code),
                             "views": int(r[vi]) if r[vi] is not None else None})
    return {
        "views": int(trow["views"]) if trow.get("views") is not None else None,
        "watch_minutes": round(float(trow["estimatedMinutesWatched"]), 1)
                          if trow.get("estimatedMinutesWatched") is not None else None,
        "subscribers_gained": int(trow["subscribersGained"]) if trow.get("subscribersGained") is not None else None,
        "subscribers_lost": int(trow["subscribersLost"]) if trow.get("subscribersLost") is not None else None,
        "sources": src_rows, "start": start.isoformat(), "end": end.isoformat(),
    }, {}


# ---------------------------------------------------------------------------
# The store: one reading per client per day
# ---------------------------------------------------------------------------

def _root() -> str:
    from hub import jsonstore
    return jsonstore.data_dir(MODULE)


def _state_path() -> str:
    return os.path.join(_root(), "sweep.json")


def _slug(name: str) -> str:
    try:
        from hub.client_key import name_slug
        s = name_slug(name)
        if s:
            return s
    except Exception:                                    # noqa: BLE001
        pass
    import re
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-") or "client"


def _readings_path(name: str) -> str:
    d = os.path.join(_root(), "readings")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, _slug(name) + ".json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def readings(name: str) -> list[dict]:
    """Every stored reading for a client, newest first."""
    from hub import jsonstore
    rows = jsonstore.read_json(_readings_path(name), default=[]) or []
    rows = [r for r in rows if isinstance(r, dict) and r.get("day")]
    rows.sort(key=lambda r: r["day"], reverse=True)
    return rows


def _append_reading(name: str, data: dict | None, *, today: date | None = None,
                    error: str = "") -> dict:
    """One reading per client per day: a second on the same day replaces
    the first, so a Refresh press does not stack rows."""
    from hub import jsonstore
    day = (today or date.today()).isoformat()
    row = {"day": day, "fetched_at": _now_iso(), "error": error or ""}
    if data:
        row.update(data)

    def mutate(rows):
        rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("day") != day]
        rows.append(row)
        rows.sort(key=lambda r: r.get("day") or "", reverse=True)
        return rows[:KEEP_READINGS]

    jsonstore.update_json(_readings_path(name), mutate)
    return row


def snapshot(name: str, *, today: date | None = None) -> dict:
    """Read the client's confirmed channel now, through its youtube_studio
    connection if one exists, and store the reading. A button (alongside
    ``hub.youtube.snapshot``) and the nightly sweep, never a page load.
    ``{"ok", "error", "reading"}``. A client with no confirmed channel or
    no connection is not a failure -- it is told apart in ``reading()``,
    below, and nothing is stored for it here."""
    from hub import youtube as hub_youtube
    rec = hub_youtube.record(name)
    if not rec:
        return {"ok": False, "error": "No YouTube channel is confirmed for this client.", "reading": None}
    conn = connection_for(rec["channel_id"])
    if not conn:
        return {"ok": False, "error": "not_connected", "reading": None}
    data, err = _fetch(conn["studio_name"], rec["channel_id"])
    if err:
        _append_reading(rec["client"], None, today=today, error=err["message"])
        return {"ok": False, "error": err["message"], "kind": err["kind"],
                "reading": reading(rec["client"], today=today)}
    _append_reading(rec["client"], data, today=today)
    return {"ok": True, "error": "", "reading": reading(rec["client"], today=today)}


# ---------------------------------------------------------------------------
# What a screen reads
# ---------------------------------------------------------------------------

def reading(name: str, *, today: date | None = None) -> dict:
    """The card's whole answer for this half. ``state`` is one of
    ``STATES``: ``ok`` carries a reading, the others say which kind of
    nothing this is. ``staff_note`` is for the staff screens and never a
    client's page; ``public_view()`` strips it."""
    today = today or date.today()
    out = {"measured": False, "state": "", "views": None, "watch_minutes": None,
           "subscribers_gained": None, "subscribers_lost": None, "subscribers_net": None,
           "sources": [], "start": None, "end": None, "as_of": None,
           "connection": None, "note": "", "staff_note": "", "error": ""}
    try:
        from hub import youtube as hub_youtube
        rec = hub_youtube.record(name)
    except Exception as exc:                            # noqa: BLE001
        out["state"] = "unread"
        out["staff_note"] = f"The channel store could not be read ({type(exc).__name__})."
        return out
    if not rec:
        out["state"] = "no_channel"
        out["staff_note"] = "No YouTube channel has been confirmed for this client."
        return out
    conn = connection_for(rec["channel_id"])
    out["connection"] = conn
    if not conn:
        out["state"] = "not_connected"
        out["staff_note"] = ("This channel has no YouTube Studio connection, so watch time, "
                             "subscribers gained and traffic sources cannot be read. Connect the "
                             "channel's owner at /tools/youtube/ to enable this.")
        return out
    if conn.get("connection_error"):
        out["state"] = "unread"
        out["staff_note"] = f"The YouTube Studio connection needs reconnecting: {conn['connection_error']}"
        return out
    rows = readings(rec["client"])
    if not rows:
        out["state"] = "no_reading"
        out["staff_note"] = "The connection is live and has not been read yet."
        return out
    good = [r for r in rows if not r.get("error")]
    if not good:
        out["state"] = "unread"
        out["error"] = str(rows[0].get("error") or "the reading could not be taken")
        out["staff_note"] = f"The last read failed: {out['error']}"
        return out
    latest = good[0]
    gained, lost = latest.get("subscribers_gained"), latest.get("subscribers_lost")
    out.update({
        "measured": True, "state": "ok",
        "views": latest.get("views"), "watch_minutes": latest.get("watch_minutes"),
        "subscribers_gained": gained, "subscribers_lost": lost,
        "subscribers_net": (gained - lost) if gained is not None and lost is not None else None,
        "sources": latest.get("sources") or [], "start": latest.get("start"), "end": latest.get("end"),
        "as_of": latest["day"],
    })
    if rows[0] is not latest and rows[0].get("error"):
        out["staff_note"] = (f"The newest read ({rows[0]['day']}) failed: {rows[0]['error']}; "
                             f"showing the reading from {latest['day']}.")
    age = (today - date.fromisoformat(latest["day"])).days
    if age > 2:
        out["note"] = f"Read {age} days ago."
    return out


def public_view(r: dict | None) -> dict | None:
    """What a client's page may carry: the period figures, the date range
    and the ranked sources, and nothing about our tooling. None unless
    there is a reading -- a card announcing "not connected" on a document
    about their business is a sentence about our tooling, the rule every
    reading in this file's family follows."""
    if not r or r.get("state") != "ok":
        return None
    return {"measured": True, "views": r["views"], "watch_minutes": r["watch_minutes"],
            "subscribers_gained": r["subscribers_gained"], "subscribers_lost": r["subscribers_lost"],
            "subscribers_net": r["subscribers_net"], "sources": r["sources"],
            "start": r["start"], "end": r["end"], "as_of": r["as_of"]}


# ---------------------------------------------------------------------------
# The nightly sweep
# ---------------------------------------------------------------------------

def _state() -> dict:
    from hub import jsonstore
    data = jsonstore.read_json(_state_path(), default={}) or {}
    return data if isinstance(data, dict) else {}


def due_for_refresh(now: datetime | None = None) -> bool:
    from hub import nightly
    now = now or datetime.now(timezone.utc)
    st = _state()
    last = None
    if st.get("last_run_at"):
        try:
            last = datetime.fromisoformat(str(st["last_run_at"]))
        except ValueError:
            last = None
    return nightly.due(last, env_var=REFRESH_HOUR_ENV, now=now)


def sweep(*, force: bool = False, today: date | None = None,
          now: datetime | None = None, budget: float | None = None,
          clock=None) -> dict:
    """Read every confirmed channel that has a youtube_studio connection,
    once, inside the nightly window. The places_snapshot shape: ticks
    hourly and the module decides, a client already read today is
    skipped, and a wall-clock budget because the scheduler's one thread is
    shared -- what is left over is named and picked up on the next tick.
    A confirmed channel with no connection is simply not in the set this
    sweep reads: there is nothing for it to fetch."""
    from hub import jsonstore, youtube as hub_youtube
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    clock = clock or time.monotonic
    budget = SWEEP_BUDGET_SECONDS if budget is None else float(budget)
    out = {"ran": False, "skipped": "", "clients": 0, "read": 0, "failed": 0,
           "already": 0, "not_connected": 0, "left": 0, "errors": {}}
    if not force and not due_for_refresh(now):
        out["skipped"] = "Not due yet."
        return out
    recs = hub_youtube.all_records()
    names = sorted(recs)
    eligible = []
    for name in names:
        rec = recs.get(name) or {}
        if connection_for(str(rec.get("channel_id") or "")):
            eligible.append(name)
        else:
            out["not_connected"] += 1
    out["clients"] = len(eligible)
    started = clock()
    for i, name in enumerate(eligible):
        if clock() - started > budget:
            out["left"] = len(eligible) - i
            break
        rows = readings(name)
        if rows and rows[0].get("day") == today.isoformat() and not rows[0].get("error"):
            out["already"] += 1
            continue
        res = snapshot(name, today=today)
        if res["ok"]:
            out["read"] += 1
        else:
            out["failed"] += 1
            out["errors"][name] = res.get("error") or "unread"
    out["ran"] = True
    state = {"last_run_at": now.isoformat(timespec="seconds"), "read": out["read"],
             "failed": out["failed"], "already": out["already"], "left": out["left"],
             "clients": out["clients"], "not_connected": out["not_connected"]}
    try:
        jsonstore.write_json(_state_path(), state, durable=False)
    except Exception:                                    # noqa: BLE001 - a note is not the sweep
        pass
    return out


def sweep_state() -> dict:
    st = _state()
    return {"last_run_at": st.get("last_run_at"), "read": st.get("read"),
            "failed": st.get("failed"), "left": st.get("left"), "clients": st.get("clients"),
            "not_connected": st.get("not_connected"), "configured": configured()}
