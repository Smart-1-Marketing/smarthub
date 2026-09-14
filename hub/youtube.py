"""A client's YouTube channel, read live: subscribers, lifetime views and
the number of videos, from the YouTube Data API rather than from nothing.

The Hub already sells YouTube two ways -- TrueView and bumpers on the rate
card, and Social Media Management, which posts to the channel -- and knew
nothing about the channel itself: the SEO record holds a link somebody
typed, and no screen has ever read past it. This is the keyed half of the
answer: the Data API v3, a key on the same Cloud project as Places, no
consent, which answers for every public channel. The other half -- watch
time, subscribers gained per day, traffic sources -- is the Analytics API
behind OAuth, and is not built: it needs a scope on Google Finder's list,
and every login connected before the scope was added keeps its old grant
and has to re-consent, which is the rule CLAUDE.md records for any scope
added later.

Six rules, each a way this goes confidently wrong:

* **A channel is resolved once, and a person confirms it.** ``candidates()``
  reads a link first -- the one on the client's SEO record, or one a rep
  pastes -- because a channel id, a ``@handle`` or a ``/user/`` address
  resolves for one quota unit and names one channel. Only with no link does
  it search, which costs a hundred units and returns whatever carries the
  name. It proposes exactly one answer: the only result, the channel a
  link named, or the only result whose title is exactly the client's own
  name. Two candidates propose neither and both are shown. Never a
  substring, never the first row -- a wrong channel on a client's record
  is somebody else's subscriber count under their name.
* **A reading is taken once a night, never on a page load.** Every read
  spends quota against the project's daily ceiling, and the client's
  report page is opened by clients. ``sweep()`` runs on the scheduler's
  hourly tick and reads each confirmed channel once inside the nightly
  window; every page reads the stored reading and prints its date.
  ``snapshot()`` on its own is a **button** -- Confirm and Refresh -- and
  never a GET.
* **A hidden subscriber count is not measured, never zero.** A channel may
  hide its count, and the API then answers ``hiddenSubscriberCount`` with
  no figure. Views and the video count are still read; the subscriber
  tile says *hidden* rather than printing a nought.
* **No channel is not measured, never a zero.** "Nobody has confirmed a
  channel", "the key is not set", "we could not read it" and "read last
  night" are four states a card draws apart -- ``reading()`` names which.
* **The 30-day change needs a reading 30 days old.** Until one exists the
  change is *not measured*, with the date of the first reading, rather
  than a comparison against the reading taken a minute ago. Views are
  lifetime, so the 30-day change is the only period figure the keyed API
  can give: "views gained in the last 30 days" is the number a client
  reads, and it is arithmetic on two of our own readings.
* **The key is shared and says so.** ``YOUTUBE_API_KEY`` first, then
  ``GOOGLE_PLACES_API_KEY`` -- the same Cloud project, and a deployment
  that enabled the API on it need set nothing more. ``key_source()`` names
  which answered, because a Places key restricted to Places API (New) is
  refused here and the fix is either an API restriction or a second key,
  and the /diagnostics row has to say which variable it read to say that.

Every call goes through ``quotas.record_google()`` with the quota **units**
it spent -- a search is a hundred and a channel read is one -- so the
usage page's per-day figure is in the unit Google meters, and
``youtube.googleapis.com`` is in ``hub/quotas._GOOGLE_HOSTS``, or the usage
page could not name this API. The key never reaches a result, an error or
a log line: it rides in the query string on the wire and nowhere else.
Stored through ``hub/jsonstore.py`` rather than a table, the places rule:
the readers are three Flask apps and a background thread. The channel is
keyed on the client's **name**, the rule every overlay here works to.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger(__name__)

CHANNELS_URL = "https://youtube.googleapis.com/youtube/v3/channels"
SEARCH_URL = "https://youtube.googleapis.com/youtube/v3/search"
# snippet for the title, the handle and the thumbnail; statistics for the
# three counts. brandingSettings, contentDetails and the rest are not asked
# for -- nothing here has a screen for them.
CHANNEL_PARTS = "snippet,statistics"
# What each call spends against the project's daily quota, from Google's
# own quota table: a list is one unit and a search is a hundred.
LIST_UNITS = 1
SEARCH_UNITS = 100
TIMEOUT = 15
CHANGE_DAYS = 30
MAX_CANDIDATES = 6
KEEP_READINGS = 400
REFRESH_HOUR_ENV = "YOUTUBE_REFRESH_HOUR"
SWEEP_BUDGET_SECONDS = 90
MODULE = "youtube"
KEY_NAMES = ("YOUTUBE_API_KEY", "GOOGLE_PLACES_API_KEY")
# What a channel id looks like: the store's own door refuses anything else
# before a call is made, so the refusal does not depend on the wire.
_CHANNEL_ID_RE = re.compile(r"UC[A-Za-z0-9_\-]{22}")
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._\-]{3,30}$")

STATES = ("ok", "no_snapshot", "no_channel", "unconfigured", "unread")


# ---------------------------------------------------------------------------
# Configuration and the wire
# ---------------------------------------------------------------------------

def _key() -> tuple[str, str]:
    """``(key, name)``: the key, and which variable answered."""
    try:
        from hub.config import settings
        own = (settings.youtube_key or "").strip()
        if own:
            return own, "YOUTUBE_API_KEY"
        shared = (settings.google_places_key or "").strip()
        if shared:
            return shared, "GOOGLE_PLACES_API_KEY"
        return "", ""
    except Exception:                                   # noqa: BLE001
        for name in KEY_NAMES:
            v = (os.environ.get(name) or "").strip()
            if v:
                return v, name
        return "", ""


def api_key() -> str:
    """The key, read through hub/config.py at call time."""
    return _key()[0]


def key_source() -> str:
    """Which variable the key came from -- named on /diagnostics and the
    card, because a refusal is fixed on that variable and not the other."""
    return _key()[1]


def configured() -> bool:
    return bool(api_key())


def _redact(text: str) -> str:
    key = api_key()
    text = str(text or "")
    return text.replace(key, "[key]") if key else text


def _record(url: str, ok: bool, units: int) -> None:
    try:
        from hub import quotas
        quotas.record_google(url, module=MODULE, ok=ok, units=units)
    except Exception:                                   # noqa: BLE001 - a count is not the call
        pass


def _reason(data) -> str:
    """The reason Google gives inside an error body, in either of the two
    shapes the v3 API uses; empty when there is none."""
    try:
        err = (data or {}).get("error") or {}
        for row in (err.get("errors") or []) + (err.get("details") or []):
            r = str((row or {}).get("reason") or "")
            if r:
                return r
    except Exception:                                   # noqa: BLE001
        pass
    return ""


def _call(url: str, params: dict, *, units: int) -> tuple[dict | None, dict]:
    """One request. ``(data, error)`` where error is ``{}`` on success and
    otherwise names the kind: refused (the key, or the API not enabled),
    rate-limited (the quota), unreachable, or an HTTP status -- kept apart
    because each sends somebody to a different place. The key rides in the
    query string on the wire; the URL recorded on the usage page is the
    endpoint alone."""
    import requests
    try:
        r = requests.get(url, params={**params, "key": api_key()}, timeout=TIMEOUT)
    except requests.Timeout:
        _record(url, False, units)
        return None, {"kind": "unreachable", "message": "YouTube did not answer in time."}
    except requests.RequestException as exc:
        _record(url, False, units)
        return None, {"kind": "unreachable",
                      "message": _redact(f"YouTube could not be reached ({type(exc).__name__}).")}
    ok = 200 <= r.status_code < 300
    _record(url, ok, units)
    data = None
    try:
        data = r.json()
    except ValueError:
        data = None
    reason = _reason(data) if isinstance(data, dict) else ""
    src = key_source() or "the key"
    if r.status_code == 429 or reason in ("quotaExceeded", "dailyLimitExceeded",
                                          "rateLimitExceeded", "userRateLimitExceeded"):
        return None, {"kind": "rate_limited",
                      "message": ("YouTube refused for quota (" + (reason or "429") + "): the project's "
                                  "daily units are spent, or the per-minute limit was hit; try later.")}
    if r.status_code in (400, 401, 403) and (r.status_code != 400 or reason in ("keyInvalid", "badRequest")):
        return None, {"kind": "refused",
                      "message": (f"Google refused the YouTube key ({r.status_code}"
                                  + (f", {reason}" if reason else "") + f"). It was read from {src}: "
                                  "check that key's API restrictions allow YouTube Data API v3 and "
                                  "that the API is enabled on its project, or set YOUTUBE_API_KEY "
                                  "to a key that is.")}
    if not ok:
        return None, {"kind": "http", "message": _redact(f"YouTube answered HTTP {r.status_code}.")}
    if not isinstance(data, dict):
        return None, {"kind": "http", "message": "YouTube answered with something that is not JSON."}
    return data, {}


def _int(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _channel_row(item: dict) -> dict:
    """One channel as the screens read it, from a channels.list item."""
    sn = item.get("snippet") or {}
    st = item.get("statistics") or {}
    cid = str(item.get("id") or "")
    custom = str(sn.get("customUrl") or "").strip()
    handle = custom if custom.startswith("@") else ""
    thumbs = sn.get("thumbnails") or {}
    thumb = ""
    for size in ("default", "medium", "high"):
        t = thumbs.get(size) or {}
        if t.get("url"):
            thumb = str(t["url"])
            break
    hidden = bool(st.get("hiddenSubscriberCount"))
    return {
        "channel_id": cid,
        "title": str(sn.get("title") or "").strip(),
        "handle": handle,
        "url": (f"https://www.youtube.com/{handle}" if handle
                else f"https://www.youtube.com/channel/{cid}" if cid else ""),
        "thumbnail": thumb,
        "published_at": str(sn.get("publishedAt") or "")[:10],
        "subscribers": None if hidden else _int(st.get("subscriberCount")),
        "subscribers_hidden": hidden,
        "views": _int(st.get("viewCount")),
        "videos": _int(st.get("videoCount")),
    }


# ---------------------------------------------------------------------------
# Resolving a channel: a link first, a search second, and a person confirms
# ---------------------------------------------------------------------------

def parse_ref(text: str) -> tuple[str, str]:
    """What a pasted string names: ``("id", "UC…")``, ``("handle", "@x")``,
    ``("user", "name")`` for the three the API resolves directly for one
    unit; ``("url", text)`` for a YouTube address it cannot (a video, a
    playlist); ``("search", text)`` for plain words; ``("", "")`` for
    nothing."""
    s = str(text or "").strip()
    if not s:
        return "", ""
    m = _CHANNEL_ID_RE.search(s)
    if m and (len(s) == len(m.group(0)) or "youtube.com/channel/" in s.lower()):
        return "id", m.group(0)
    m = re.search(r"youtube\.com/@([A-Za-z0-9._\-]{3,30})", s, re.IGNORECASE)
    if m:
        return "handle", "@" + m.group(1)
    if _HANDLE_RE.match(s):
        return "handle", s
    m = re.search(r"youtube\.com/user/([A-Za-z0-9._\-]+)", s, re.IGNORECASE)
    if m:
        return "user", m.group(1)
    # A legacy custom URL is a handle now: Google folded /c/ addresses into
    # handles, and forHandle resolves them. One that does not resolve falls
    # through to a search rather than being reported as no channel.
    m = re.search(r"youtube\.com/c/([A-Za-z0-9._\-]+)", s, re.IGNORECASE)
    if m:
        return "handle", "@" + m.group(1)
    if re.search(r"youtu\.?be(\.com)?/", s, re.IGNORECASE):
        return "url", s
    return "search", s


def _resolve(kind: str, value: str) -> tuple[list[dict], dict]:
    """channels.list for a direct reference. ``(rows, error)``."""
    param = {"id": "id", "handle": "forHandle", "user": "forUsername"}[kind]
    data, err = _call(CHANNELS_URL, {"part": CHANNEL_PARTS, param: value, "maxResults": 1}, units=LIST_UNITS)
    if err:
        return [], err
    rows = [_channel_row(i) for i in (data.get("items") or []) if isinstance(i, dict)]
    return [r for r in rows if r["channel_id"]], {}


def _search(text: str) -> tuple[list[dict], dict]:
    """search.list for channels carrying the name (a hundred units), then
    one channels.list for their figures (one unit). ``(rows, error)``."""
    data, err = _call(SEARCH_URL, {"part": "snippet", "type": "channel", "q": text,
                                   "maxResults": MAX_CANDIDATES}, units=SEARCH_UNITS)
    if err:
        return [], err
    ids = []
    for it in data.get("items") or []:
        cid = str(((it or {}).get("id") or {}).get("channelId") or ((it or {}).get("snippet") or {}).get("channelId") or "")
        if cid and cid not in ids:
            ids.append(cid)
    if not ids:
        return [], {}
    data, err = _call(CHANNELS_URL, {"part": CHANNEL_PARTS, "id": ",".join(ids[:MAX_CANDIDATES]),
                                     "maxResults": MAX_CANDIDATES}, units=LIST_UNITS)
    if err:
        return [], err
    rows = [_channel_row(i) for i in (data.get("items") or []) if isinstance(i, dict)]
    rows = [r for r in rows if r["channel_id"]]
    order = {cid: i for i, cid in enumerate(ids)}
    rows.sort(key=lambda r: order.get(r["channel_id"], 99))
    return rows, {}


def candidates(name: str, query: str = "", hint: str = "") -> dict:
    """The client's channel, with one proposal or none.

    ``query`` is what a rep typed -- a link, a handle, or words to narrow a
    search; ``hint`` is the link on the client's own record. A link is
    resolved directly for one unit and names one channel; only with no
    link does this search, for a hundred. Behind a button, never a page
    load.
    """
    name = str(name or "").strip()
    query = str(query or "").strip()
    hint = str(hint or "").strip()
    out = {"measured": False, "error": "", "kind": "", "query": "", "hint": hint,
           "candidates": [], "proposed": None, "why": "", "units": 0, "how": ""}
    if not configured():
        out["error"] = "YouTube is not set up on this deployment (YOUTUBE_API_KEY or GOOGLE_PLACES_API_KEY)."
        out["kind"] = "unconfigured"
        return out
    qkind, qval = parse_ref(query)
    hkind, hval = parse_ref(hint)
    note = ""
    # 1. a link the rep gave, or the one on the record: resolved, not searched.
    for source, kind, value in (("the link you gave", qkind, qval), ("the link on their record", hkind, hval)):
        if kind not in ("id", "handle", "user"):
            continue
        if source == "the link on their record" and query:
            # Words typed beside a linked record are a request to search.
            continue
        rows, err = _resolve(kind, value)
        out["units"] += LIST_UNITS
        out["query"] = value
        if err:
            out["error"], out["kind"] = err["message"], err["kind"]
            return out
        if rows:
            out.update({"measured": True, "candidates": rows[:1], "proposed": rows[0]["channel_id"],
                        "why": f"the channel at {source} ({value})", "how": "link"})
            return out
        if source == "the link you gave":
            out.update({"measured": True, "how": "link",
                        "why": f"no channel answers to {value}; check the link, or search by name"})
            return out
        note = f"nothing answers to {value}, the link on their record; searched by name instead"
        break
    if qkind == "url":
        out.update({"measured": True, "how": "link", "query": query,
                    "why": "that is a YouTube address but not a channel's (a video or a playlist); "
                           "paste the channel page, or search by name"})
        return out
    # 2. a search, on the name plus whatever the rep typed.
    text = " ".join(t for t in (name, query if qkind == "search" else "") if t).strip()
    if not text:
        out["error"], out["kind"] = "Nothing to search for.", "empty"
        return out
    out["query"] = text
    out["how"] = "search"
    rows, err = _search(text)
    out["units"] += SEARCH_UNITS + (LIST_UNITS if rows else 0)
    if err:
        out["error"], out["kind"] = err["message"], err["kind"]
        return out
    want = _norm(name)
    for r in rows:
        r["name_match"] = bool(want) and _norm(r["title"]) == want
    out["measured"] = True
    out["candidates"] = rows
    matches = [r for r in rows if r["name_match"]]
    if len(rows) == 1:
        out["proposed"] = rows[0]["channel_id"]
        out["why"] = "the only channel YouTube returned for that search"
    elif len(matches) == 1:
        out["proposed"] = matches[0]["channel_id"]
        out["why"] = f"the only channel titled exactly {matches[0]['title']!r}"
    elif not rows:
        out["why"] = "YouTube returned no channel for that search"
    elif matches:
        out["why"] = f"{len(matches)} channels carry the client's exact name; pick the one that is theirs"
    else:
        out["why"] = ("several channels and none is titled with the client's name; pick the one "
                      "that is theirs, paste its link, or narrow the search")
    if note:
        out["why"] = note + " -- " + out["why"]
    return out


def details(channel_id: str) -> dict:
    """One channel, read now. ``{"measured", "error", "kind", "channel"}``."""
    channel_id = str(channel_id or "").strip()
    out = {"measured": False, "error": "", "kind": "", "channel": None}
    if not configured():
        out["error"] = "YouTube is not set up on this deployment (YOUTUBE_API_KEY or GOOGLE_PLACES_API_KEY)."
        out["kind"] = "unconfigured"
        return out
    if not _CHANNEL_ID_RE.fullmatch(channel_id):
        out["error"], out["kind"] = "That is not a channel id.", "bad_id"
        return out
    rows, err = _resolve("id", channel_id)
    if err:
        out["error"], out["kind"] = err["message"], err["kind"]
        return out
    if not rows:
        out["error"], out["kind"] = "No channel has that id any more.", "not_found"
        return out
    out["measured"] = True
    out["channel"] = rows[0]
    return out


# ---------------------------------------------------------------------------
# The store: the confirmed channel per client, and the readings
# ---------------------------------------------------------------------------

def _root() -> str:
    from hub import jsonstore
    return jsonstore.data_dir(MODULE)


def _channels_path() -> str:
    return os.path.join(_root(), "channels.json")


def _state_path() -> str:
    return os.path.join(_root(), "sweep.json")


def _slug(name: str) -> str:
    try:
        from hub.client_key import name_slug
        s = name_slug(name)
        if s:
            return s
    except Exception:                                   # noqa: BLE001
        pass
    return re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-") or "client"


def _readings_path(name: str) -> str:
    d = os.path.join(_root(), "readings")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, _slug(name) + ".json")


def _norm(name: str) -> str:
    try:
        from hub.client_key import normalise_name
        return normalise_name(name) or str(name or "").strip().casefold()
    except Exception:                                   # noqa: BLE001
        return str(name or "").strip().casefold()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def all_records() -> dict:
    """``{client name: record}`` -- every confirmed channel."""
    from hub import jsonstore
    data = jsonstore.read_json(_channels_path(), default={}) or {}
    return data if isinstance(data, dict) else {}


def record(name: str) -> dict | None:
    """The confirmed channel for a client, matched on the exact normalised
    name and nothing looser."""
    want = _norm(name)
    if not want:
        return None
    for k, v in all_records().items():
        if _norm(k) == want and isinstance(v, dict):
            return {**v, "client": k}
    return None


def _log(event: str, name: str, actor: str, **extra) -> None:
    try:
        from hub import audit
        audit.log("youtube", event, actor=actor or "system", client=name, **extra)
    except Exception:                                   # noqa: BLE001 - a log line is not the write
        pass


def confirm(name: str, channel_id: str, *, actor: str = "", today: date | None = None) -> dict:
    """A person says this channel is the client's. The channel is read now
    (one unit) so the record carries its own title and handle and the card
    shows a figure on the press; a read that fails still stores the
    confirmation and says the reading could not be taken.
    Returns ``{"ok", "record", "reading", "error"}``."""
    from hub import jsonstore
    name = str(name or "").strip()
    channel_id = str(channel_id or "").strip()
    if not name or not channel_id:
        return {"ok": False, "error": "A client and a channel id are both needed.",
                "record": None, "reading": None}
    if not _CHANNEL_ID_RE.fullmatch(channel_id):
        return {"ok": False, "error": "That is not a channel id.", "record": None, "reading": None}
    det = details(channel_id)
    ch = det.get("channel") or {"channel_id": channel_id}
    rec = {
        "channel_id": channel_id,
        "title": ch.get("title") or "",
        "handle": ch.get("handle") or "",
        "url": ch.get("url") or f"https://www.youtube.com/channel/{channel_id}",
        "thumbnail": ch.get("thumbnail") or "",
        "confirmed_by": str(actor or ""),
        "confirmed_at": _now_iso(),
    }
    existing = record(name)
    key = existing["client"] if existing else name

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[key] = rec
        return data

    jsonstore.update_json(_channels_path(), mutate)
    _log("channel_confirmed", key, actor, channel_id=channel_id,
         detail=f"YouTube channel confirmed for {key}: {rec['title'] or channel_id}"
                + (f" ({rec['handle']})" if rec["handle"] else ""))
    if det["measured"]:
        _append_reading(key, ch, today=today)
    else:
        _append_reading(key, None, today=today, error=det["error"])
    return {"ok": True, "record": {**rec, "client": key},
            "reading": reading(key, today=today), "error": det.get("error") or ""}


def clear(name: str, *, actor: str = "") -> dict:
    """Not this channel. The readings are kept -- they are readings of a
    channel, and the next confirmation may be the same one -- but nothing
    reads them without a record."""
    from hub import jsonstore
    existing = record(name)
    if not existing:
        return {"ok": False, "error": "No channel is confirmed for this client."}
    key = existing["client"]

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        if key not in data:
            return None
        data.pop(key, None)
        return data

    jsonstore.update_json(_channels_path(), mutate)
    _log("channel_cleared", key, actor, channel_id=existing.get("channel_id"),
         detail=f"YouTube channel cleared for {key}: {existing.get('title') or existing.get('channel_id')}")
    return {"ok": True, "cleared": existing.get("channel_id")}


def readings(name: str) -> list[dict]:
    """Every stored reading for a client, newest first."""
    from hub import jsonstore
    rows = jsonstore.read_json(_readings_path(name), default=[]) or []
    rows = [r for r in rows if isinstance(r, dict) and r.get("day")]
    rows.sort(key=lambda r: r["day"], reverse=True)
    return rows


def _append_reading(name: str, channel: dict | None, *, today: date | None = None,
                    error: str = "") -> dict:
    """One reading per client per day: a second on the same day replaces
    the first, so a Refresh press does not stack rows."""
    from hub import jsonstore
    day = (today or date.today()).isoformat()
    row = {"day": day, "fetched_at": _now_iso(), "error": error or ""}
    if channel:
        row.update({"subscribers": channel.get("subscribers"),
                    "subscribers_hidden": bool(channel.get("subscribers_hidden")),
                    "views": channel.get("views"), "videos": channel.get("videos"),
                    "channel_id": channel.get("channel_id") or ""})

    def mutate(rows):
        rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("day") != day]
        rows.append(row)
        rows.sort(key=lambda r: r.get("day") or "", reverse=True)
        return rows[:KEEP_READINGS]

    jsonstore.update_json(_readings_path(name), mutate)
    return row


def snapshot(name: str, *, today: date | None = None) -> dict:
    """Read the confirmed channel now and store the reading. A button and
    the nightly sweep, never a page load. ``{"ok", "error", "reading"}``."""
    rec = record(name)
    if not rec:
        return {"ok": False, "error": "No channel is confirmed for this client.", "reading": None}
    det = details(rec["channel_id"])
    if not det["measured"]:
        _append_reading(rec["client"], None, today=today, error=det["error"])
        return {"ok": False, "error": det["error"], "kind": det["kind"],
                "reading": reading(rec["client"], today=today)}
    _append_reading(rec["client"], det["channel"], today=today)
    return {"ok": True, "error": "", "reading": reading(rec["client"], today=today)}


# ---------------------------------------------------------------------------
# What a screen reads
# ---------------------------------------------------------------------------

def _good(rows: list[dict]) -> list[dict]:
    """The readings that carry a figure: a hidden subscriber count with
    views beside it is a reading; an errored row is not."""
    return [r for r in rows
            if not r.get("error")
            and any(r.get(k) is not None for k in ("subscribers", "views", "videos"))]


def _delta(latest: dict, base: dict, key: str):
    a, b = latest.get(key), base.get(key)
    if a is None or b is None:
        return None
    return int(a) - int(b)


def _change(good: list[dict], latest: dict, today: date) -> dict:
    """The 30-day change: the newest good reading dated on or before the
    latest one minus CHANGE_DAYS, or not measured with the reason."""
    as_of = date.fromisoformat(latest["day"])
    cutoff = as_of - timedelta(days=CHANGE_DAYS)
    base = next((r for r in good if r["day"] <= cutoff.isoformat()), None)
    if base is None:
        first = good[-1]
        first_day = date.fromisoformat(first["day"])
        waiting = max(0, CHANGE_DAYS - (as_of - first_day).days)
        return {"measured": False, "days": CHANGE_DAYS, "since": None,
                "note": (f"First reading {first_day.isoformat()}; the {CHANGE_DAYS}-day change "
                         f"needs a reading from {CHANGE_DAYS} days back"
                         + (f" ({waiting} more day{'' if waiting == 1 else 's'})" if waiting else "")
                         + ".")}
    return {"measured": True, "days": CHANGE_DAYS, "since": base["day"],
            "subscribers_delta": _delta(latest, base, "subscribers"),
            "views_delta": _delta(latest, base, "views"),
            "videos_delta": _delta(latest, base, "videos"),
            "base_subscribers": base.get("subscribers"), "base_views": base.get("views"),
            "base_videos": base.get("videos"), "note": ""}


def reading(name: str, *, today: date | None = None) -> dict:
    """The card's whole answer for one client. ``state`` is one of
    ``STATES``: ``ok`` carries a reading, the others say which kind of
    nothing this is. ``staff_note`` is for the staff screens and never a
    client's page; ``public_view()`` strips it."""
    today = today or date.today()
    out = {"measured": False, "state": "", "configured": configured(), "key_source": key_source(),
           "record": None, "subscribers": None, "subscribers_hidden": False, "views": None,
           "videos": None, "as_of": None, "change": None, "readings": 0,
           "note": "", "staff_note": "", "error": ""}
    rec = record(name)
    out["record"] = rec
    if not configured():
        out["state"] = "unconfigured"
        out["staff_note"] = ("YouTube is not set up on this deployment (YOUTUBE_API_KEY, or the "
                             "GOOGLE_PLACES_API_KEY it falls back to), so no channel can be read.")
        return out
    if not rec:
        out["state"] = "no_channel"
        out["staff_note"] = "No YouTube channel has been confirmed for this client."
        return out
    rows = readings(rec["client"])
    out["readings"] = len(rows)
    good = _good(rows)
    if not rows:
        out["state"] = "no_snapshot"
        out["staff_note"] = "The channel is confirmed and has not been read yet."
        return out
    if not good:
        out["state"] = "unread"
        out["error"] = str(rows[0].get("error") or "the channel could not be read")
        out["staff_note"] = f"The last read failed: {out['error']}"
        return out
    latest = good[0]
    out.update({
        "measured": True, "state": "ok",
        "subscribers": latest.get("subscribers"),
        "subscribers_hidden": bool(latest.get("subscribers_hidden")),
        "views": latest.get("views"), "videos": latest.get("videos"),
        "as_of": latest["day"],
        "change": _change(good, latest, today),
    })
    if rows[0] is not latest and rows[0].get("error"):
        out["staff_note"] = (f"The newest read ({rows[0]['day']}) failed: {rows[0]['error']}; "
                             f"showing the reading from {latest['day']}.")
    age = (today - date.fromisoformat(latest["day"])).days
    if age > 2:
        out["note"] = f"Read {age} days ago."
    return out


def public_view(r: dict | None) -> dict | None:
    """What a client's page may carry: the channel's own title and link,
    the three counts and their date, the change, and nothing about our
    tooling. None unless there is a reading."""
    if not r or r.get("state") != "ok":
        return None
    change = dict(r.get("change") or {})
    rec = r.get("record") or {}
    return {"measured": True, "title": rec.get("title") or "", "url": rec.get("url") or "",
            "handle": rec.get("handle") or "",
            "subscribers": r["subscribers"], "subscribers_hidden": bool(r.get("subscribers_hidden")),
            "views": r["views"], "videos": r["videos"], "as_of": r["as_of"],
            "change": change if change.get("measured") else {"measured": False}}


def card_for(name: str, *, today: date | None = None) -> dict | None:
    """The client page's channel block, or None when there is nothing
    measured to draw -- a card that says "not connected" on a page a
    client reads is a sentence about our tooling on a document about
    their business."""
    try:
        return public_view(reading(name, today=today))
    except Exception:                                   # noqa: BLE001 - the page must render
        log.exception("youtube: card_for failed")
        return None


# ---------------------------------------------------------------------------
# The nightly sweep
# ---------------------------------------------------------------------------

def _state() -> dict:
    from hub import jsonstore
    data = jsonstore.read_json(_state_path(), default={}) or {}
    return data if isinstance(data, dict) else {}


def due_for_refresh(now: datetime | None = None) -> bool:
    """Has the nightly window passed since the last completed sweep?"""
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
    """Read every confirmed channel once, inside the nightly window.

    ``force=False`` is what the scheduler passes: it ticks hourly and this
    returns without a call unless the window has passed. One unit per
    client, a channel already read today skipped (a restart inside the
    window must not read the book twice), and a wall-clock budget because
    the scheduler's one thread is shared -- what is left over is named and
    picked up on the next tick, not silently dropped.
    """
    from hub import jsonstore
    now = now or datetime.now(timezone.utc)
    today = today or now.date()
    clock = clock or time.monotonic
    budget = SWEEP_BUDGET_SECONDS if budget is None else float(budget)
    out = {"ran": False, "skipped": "", "clients": 0, "read": 0, "failed": 0,
           "already": 0, "left": 0, "errors": {}}
    if not configured():
        out["skipped"] = "YouTube is not set up (YOUTUBE_API_KEY or GOOGLE_PLACES_API_KEY)."
        return out
    if not force and not due_for_refresh(now):
        out["skipped"] = "Not due yet."
        return out
    recs = all_records()
    out["clients"] = len(recs)
    started = clock()
    names = sorted(recs)
    for i, name in enumerate(names):
        if clock() - started > budget:
            out["left"] = len(names) - i
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
            if res.get("kind") in ("refused", "unconfigured", "rate_limited"):
                # A key Google refuses refuses every client, and a spent
                # quota is spent for every client: stop rather than record
                # the same refusal a hundred times.
                out["left"] = len(names) - i - 1
                break
    out["ran"] = True
    state = {"last_run_at": now.isoformat(timespec="seconds"), "read": out["read"],
             "failed": out["failed"], "already": out["already"], "left": out["left"],
             "clients": out["clients"]}
    try:
        jsonstore.write_json(_state_path(), state, durable=False)
    except Exception:                                   # noqa: BLE001 - a note is not the sweep
        pass
    return out


def sweep_state() -> dict:
    """When the sweep last ran and what it did -- what /status and the
    card's staff note read."""
    st = _state()
    return {"last_run_at": st.get("last_run_at"), "read": st.get("read"),
            "failed": st.get("failed"), "left": st.get("left"), "clients": st.get("clients"),
            "configured": configured(), "key_source": key_source(), "confirmed": len(all_records())}
