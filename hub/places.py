"""A client's Google Business Profile, read live: rating, review count and
whether the listing is open -- from Google Places rather than from the
last Insites scan.

Client 360 already shows a rating and a review count, and the client's
own report page has carried a Business Profile card reading *"Coming soon:
calls, direction requests and profile views"* since the day it was drawn.
The first is a **snapshot as of the last scan**, weeks old on most
records; the second was a promise on a page a client reads. This module
is the live half: the Places API, keyed, no consent, which answers for the
majority of local businesses whose profile nobody has ever connected.

Six rules, each a way this goes confidently wrong:

* **A place is resolved once, and a person confirms it.** ``candidates()``
  asks Text Search for the client's name (plus whatever a rep adds -- a
  city, a street) and proposes exactly one answer: the only candidate, or
  the only candidate whose website is the client's own domain. Two
  candidates propose neither and both are shown. Never a substring, never
  the first row -- a wrong listing on a client's record is somebody else's
  reviews under their name, on the one screen everybody reads.
* **A reading is taken once a night, never on a page load.** Place Details
  is billed (the Pro SKU, for the rating and the count), and the client's
  report page is opened by clients. ``sweep()`` runs on the scheduler's
  hourly tick and reads each confirmed listing once inside the nightly
  window; every page reads the stored reading and prints its date.
  ``snapshot()`` on its own is a **button** -- Confirm and Refresh -- and
  never a GET.
* **Review text is not asked for.** The rating and the count are the Pro
  SKU; the text is the Enterprise SKU and the reviewer's own words, and
  nothing here has a screen that needs it. Said here so the field mask is
  a decision rather than a default.
* **No listing is not measured, never a zero rating.** A rating is always
  printed with its review count, and "nobody has confirmed a listing",
  "the key is not set", "we could not read it" and "read today" are four
  states a card draws apart -- ``reading()`` names which.
* **The 30-day change needs a reading 30 days old.** Until one exists the
  change is *not measured*, with the date of the first reading, rather
  than a comparison against the reading taken a minute ago.
* **Snapshot and scan stay apart.** ``hub/scan_facts.py`` reports what the
  audit observed on the day it ran, and this reports what Google answered
  last night. Both are true, on different days, and neither is folded into
  the other: a card that showed one figure would be claiming a source it
  cannot name.

Every call goes through ``quotas.record_google()``, and
``places.googleapis.com`` is in ``hub/quotas._GOOGLE_HOSTS``, or the usage
page could not name this API. The key never reaches a result, an error or
a log line. Stored through ``hub/jsonstore.py`` rather than a table: the
readers are three different Flask apps and a background thread, and a
Flask-SQLAlchemy session bound to whichever app is current is the
``flask.g`` trap one layer down. The place is keyed on the client's
**name**, the rule every overlay here works to.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger(__name__)

SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
# The Pro SKU fields and nothing from the Enterprise one: no reviews, no
# photos, no editorial summary.
SEARCH_MASK = ("places.id,places.displayName,places.formattedAddress,places.websiteUri,"
               "places.businessStatus,places.rating,places.userRatingCount,"
               "places.googleMapsUri")
DETAILS_MASK = ("id,displayName,formattedAddress,websiteUri,businessStatus,rating,"
                "userRatingCount,googleMapsUri")
TIMEOUT = 15
CHANGE_DAYS = 30
MAX_CANDIDATES = 6
KEEP_READINGS = 400
# The nightly window's hour is an env var so a deployment can move it; the
# job ticks hourly and this decides, the purchased_domains shape.
REFRESH_HOUR_ENV = "PLACES_REFRESH_HOUR"
SWEEP_BUDGET_SECONDS = 90
MODULE = "places"
# What a Google place id looks like: the store's own door refuses anything
# else before a call is made, so the refusal does not depend on the wire.
_PLACE_ID_RE = re.compile(r"[A-Za-z0-9_\-]{5,300}")

STATES = ("ok", "no_snapshot", "no_place", "unconfigured", "unread")
STATUS_LABELS = {
    "OPERATIONAL": "Open",
    "CLOSED_TEMPORARILY": "Temporarily closed",
    "CLOSED_PERMANENTLY": "Permanently closed",
}


# ---------------------------------------------------------------------------
# Configuration and the wire
# ---------------------------------------------------------------------------

def api_key() -> str:
    """The key, read through hub/config.py at call time."""
    try:
        from hub.config import settings
        return (settings.google_places_key or "").strip()
    except Exception:                                   # noqa: BLE001
        return (os.environ.get("GOOGLE_PLACES_API_KEY") or "").strip()


def configured() -> bool:
    return bool(api_key())


def _redact(text: str) -> str:
    key = api_key()
    text = str(text or "")
    return text.replace(key, "[key]") if key else text


def _record(url: str, ok: bool) -> None:
    try:
        from hub import quotas
        quotas.record_google(url, module=MODULE, ok=ok)
    except Exception:                                   # noqa: BLE001 - a count is not the call
        pass


def _call(url: str, *, mask: str, body: dict | None = None) -> tuple[dict | None, dict]:
    """One request. ``(data, error)`` where error is ``{}`` on success and
    otherwise names the kind: refused (the key), rate-limited, unreachable,
    or an HTTP status -- kept apart because each sends somebody to a
    different place, the services/provider_check.py rule."""
    import requests
    headers = {"X-Goog-Api-Key": api_key(), "X-Goog-FieldMask": mask,
               "Content-Type": "application/json"}
    try:
        if body is not None:
            r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
        else:
            r = requests.get(url, headers=headers, timeout=TIMEOUT)
    except requests.Timeout:
        _record(url, False)
        return None, {"kind": "unreachable", "message": "Google Places did not answer in time."}
    except requests.RequestException as exc:
        _record(url, False)
        return None, {"kind": "unreachable",
                      "message": _redact(f"Google Places could not be reached ({type(exc).__name__}).")}
    ok = 200 <= r.status_code < 300
    _record(url, ok)
    if r.status_code in (401, 403):
        return None, {"kind": "refused",
                      "message": (f"Google refused the Places key ({r.status_code}). Check the "
                                  "key's API restrictions allow Places API (New).")}
    if r.status_code == 429:
        return None, {"kind": "rate_limited",
                      "message": "Google rate-limited the Places key (429); try again later."}
    if not ok:
        return None, {"kind": "http",
                      "message": _redact(f"Google Places answered HTTP {r.status_code}.")}
    try:
        data = r.json()
    except ValueError:
        return None, {"kind": "http", "message": "Google Places answered with something that is not JSON."}
    return (data if isinstance(data, dict) else {}), {}


def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _place_row(p: dict) -> dict:
    """One place as the screens read it, from either endpoint's shape."""
    name = p.get("displayName")
    if isinstance(name, dict):
        name = name.get("text")
    website = str(p.get("websiteUri") or "").strip()
    return {
        "place_id": str(p.get("id") or ""),
        "name": str(name or "").strip(),
        "address": str(p.get("formattedAddress") or "").strip(),
        "website": website,
        "domain": _domain(website),
        "status": str(p.get("businessStatus") or ""),
        "status_label": STATUS_LABELS.get(str(p.get("businessStatus") or ""), ""),
        "rating": _num(p.get("rating")),
        "review_count": _int(p.get("userRatingCount")),
        "maps_url": str(p.get("googleMapsUri") or ""),
    }


def _domain(value: str) -> str:
    try:
        from hub.client_context import canonical_domain
        return canonical_domain(value or "") or ""
    except Exception:                                   # noqa: BLE001
        v = re.sub(r"^https?://", "", str(value or "").strip().lower())
        v = v.split("/")[0]
        return v[4:] if v.startswith("www.") else v


# ---------------------------------------------------------------------------
# Resolving a place: propose, never decide
# ---------------------------------------------------------------------------

def candidates(name: str, query: str = "", domain: str = "") -> dict:
    """Text Search for a client, with one proposal or none.

    ``query`` is what a rep typed to narrow it (a city, a street); ``domain``
    is the client's own, and the only evidence strong enough to pick
    between several candidates. Billed: behind a button, never a page load.
    """
    name = str(name or "").strip()
    text = " ".join(t for t in (name, str(query or "").strip()) if t)
    out = {"measured": False, "error": "", "kind": "", "query": text,
           "candidates": [], "proposed": None, "why": ""}
    if not configured():
        out["error"] = "Google Places is not set up on this deployment (GOOGLE_PLACES_API_KEY)."
        out["kind"] = "unconfigured"
        return out
    if not text:
        out["error"] = "Nothing to search for."
        out["kind"] = "empty"
        return out
    data, err = _call(SEARCH_URL, mask=SEARCH_MASK,
                      body={"textQuery": text, "pageSize": MAX_CANDIDATES})
    if err:
        out["error"], out["kind"] = err["message"], err["kind"]
        return out
    rows = [_place_row(p) for p in (data.get("places") or []) if isinstance(p, dict)]
    rows = [r for r in rows if r["place_id"]][:MAX_CANDIDATES]
    want = _domain(domain) if domain else ""
    for r in rows:
        r["domain_match"] = bool(want) and r["domain"] == want
    out["measured"] = True
    out["candidates"] = rows
    matches = [r for r in rows if r["domain_match"]]
    if len(rows) == 1:
        out["proposed"] = rows[0]["place_id"]
        out["why"] = "the only listing Google returned for that search"
    elif len(matches) == 1:
        out["proposed"] = matches[0]["place_id"]
        out["why"] = f"the only listing whose website is {want}"
    elif not rows:
        out["why"] = "Google returned no listing for that search"
    elif matches:
        out["why"] = (f"{len(matches)} listings carry the website {want}; "
                      "pick the one that is theirs")
    else:
        out["why"] = ("several listings and none carries their website; "
                      "pick the one that is theirs, or narrow the search")
    return out


def details(place_id: str) -> dict:
    """One listing, read now. ``{"measured", "error", "kind", "place"}``."""
    place_id = str(place_id or "").strip()
    out = {"measured": False, "error": "", "kind": "", "place": None}
    if not configured():
        out["error"] = "Google Places is not set up on this deployment (GOOGLE_PLACES_API_KEY)."
        out["kind"] = "unconfigured"
        return out
    if not _PLACE_ID_RE.fullmatch(place_id):
        out["error"], out["kind"] = "That is not a place id.", "bad_id"
        return out
    data, err = _call(DETAILS_URL.format(place_id=place_id), mask=DETAILS_MASK)
    if err:
        out["error"], out["kind"] = err["message"], err["kind"]
        return out
    out["measured"] = True
    out["place"] = _place_row(data)
    return out


# ---------------------------------------------------------------------------
# The store: the confirmed place per client, and the readings
# ---------------------------------------------------------------------------

def _root() -> str:
    from hub import jsonstore
    return jsonstore.data_dir(MODULE)


def _places_path() -> str:
    return os.path.join(_root(), "places.json")


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
    """``{client name: record}`` -- every confirmed listing."""
    from hub import jsonstore
    data = jsonstore.read_json(_places_path(), default={}) or {}
    return data if isinstance(data, dict) else {}


def record(name: str) -> dict | None:
    """The confirmed listing for a client, matched on the exact normalised
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
        audit.log("places", event, actor=actor or "system", client=name, **extra)
    except Exception:                                   # noqa: BLE001 - a log line is not the write
        pass


def confirm(name: str, place_id: str, *, actor: str = "", today: date | None = None) -> dict:
    """A person says this listing is the client's. The listing is read now
    (one billed call) so the record carries Google's own name and address
    and the card shows a figure on the press; a read that fails still
    stores the confirmation and says the reading could not be taken.
    Returns ``{"ok", "record", "reading", "error"}``."""
    from hub import jsonstore
    name = str(name or "").strip()
    place_id = str(place_id or "").strip()
    if not name or not place_id:
        return {"ok": False, "error": "A client and a place id are both needed.",
                "record": None, "reading": None}
    if not _PLACE_ID_RE.fullmatch(place_id):
        return {"ok": False, "error": "That is not a place id.", "record": None, "reading": None}
    det = details(place_id)
    place = det.get("place") or {"place_id": place_id}
    rec = {
        "place_id": place_id,
        "name": place.get("name") or "",
        "address": place.get("address") or "",
        "website": place.get("website") or "",
        "maps_url": place.get("maps_url") or "",
        "confirmed_by": str(actor or ""),
        "confirmed_at": _now_iso(),
    }
    existing = record(name)
    key = existing["client"] if existing else name

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        data[key] = rec
        return data

    jsonstore.update_json(_places_path(), mutate)
    _log("place_confirmed", key, actor, place_id=place_id,
         detail=f"Google listing confirmed for {key}: {rec['name'] or place_id}"
                + (f", {rec['address']}" if rec["address"] else ""))
    reading_row = None
    if det["measured"]:
        reading_row = _append_reading(key, place, today=today)
    else:
        _append_reading(key, None, today=today, error=det["error"])
    return {"ok": True, "record": {**rec, "client": key},
            "reading": reading(key, today=today), "error": det.get("error") or ""}


def clear(name: str, *, actor: str = "") -> dict:
    """Not this listing. The readings are kept -- they are readings of a
    place, and the next confirmation may be the same one -- but nothing
    reads them without a record."""
    from hub import jsonstore
    existing = record(name)
    if not existing:
        return {"ok": False, "error": "No listing is confirmed for this client."}
    key = existing["client"]

    def mutate(data):
        data = data if isinstance(data, dict) else {}
        if key not in data:
            return None
        data.pop(key, None)
        return data

    jsonstore.update_json(_places_path(), mutate)
    _log("place_cleared", key, actor, place_id=existing.get("place_id"),
         detail=f"Google listing cleared for {key}: {existing.get('name') or existing.get('place_id')}")
    return {"ok": True, "cleared": existing.get("place_id")}


def readings(name: str) -> list[dict]:
    """Every stored reading for a client, newest first."""
    from hub import jsonstore
    rows = jsonstore.read_json(_readings_path(name), default=[]) or []
    rows = [r for r in rows if isinstance(r, dict) and r.get("day")]
    rows.sort(key=lambda r: r["day"], reverse=True)
    return rows


def _append_reading(name: str, place: dict | None, *, today: date | None = None,
                    error: str = "") -> dict:
    """One reading per client per day: a second on the same day replaces
    the first, so a Refresh press does not stack rows."""
    from hub import jsonstore
    day = (today or date.today()).isoformat()
    row = {"day": day, "fetched_at": _now_iso(), "error": error or ""}
    if place:
        row.update({"rating": place.get("rating"), "review_count": place.get("review_count"),
                    "status": place.get("status") or "", "place_id": place.get("place_id") or ""})

    def mutate(rows):
        rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("day") != day]
        rows.append(row)
        rows.sort(key=lambda r: r.get("day") or "", reverse=True)
        return rows[:KEEP_READINGS]

    jsonstore.update_json(_readings_path(name), mutate)
    return row


def snapshot(name: str, *, today: date | None = None) -> dict:
    """Read the confirmed listing now and store the reading. A button and
    the nightly sweep, never a page load. ``{"ok", "error", "reading"}``."""
    rec = record(name)
    if not rec:
        return {"ok": False, "error": "No listing is confirmed for this client.", "reading": None}
    det = details(rec["place_id"])
    if not det["measured"]:
        _append_reading(rec["client"], None, today=today, error=det["error"])
        return {"ok": False, "error": det["error"], "kind": det["kind"],
                "reading": reading(rec["client"], today=today)}
    _append_reading(rec["client"], det["place"], today=today)
    return {"ok": True, "error": "", "reading": reading(rec["client"], today=today)}


# ---------------------------------------------------------------------------
# What a screen reads
# ---------------------------------------------------------------------------

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
    rating_delta = (round(latest["rating"] - base["rating"], 2)
                    if latest.get("rating") is not None and base.get("rating") is not None else None)
    reviews_delta = (int(latest["review_count"]) - int(base["review_count"])
                     if latest.get("review_count") is not None and base.get("review_count") is not None
                     else None)
    return {"measured": True, "days": CHANGE_DAYS, "since": base["day"],
            "rating_delta": rating_delta, "reviews_delta": reviews_delta,
            "base_rating": base.get("rating"), "base_review_count": base.get("review_count"),
            "note": ""}


def reading(name: str, *, today: date | None = None) -> dict:
    """The card's whole answer for one client. ``state`` is one of
    ``STATES``: ``ok`` carries a reading, the others say which kind of
    nothing this is. ``staff_note`` is for the staff screens and never a
    client's page; ``public_view()`` strips it."""
    today = today or date.today()
    out = {"measured": False, "state": "", "configured": configured(),
           "record": None, "rating": None, "review_count": None, "status": "",
           "status_label": "", "as_of": None, "change": None, "readings": 0,
           "note": "", "staff_note": "", "error": ""}
    rec = record(name)
    out["record"] = rec
    if not configured():
        out["state"] = "unconfigured"
        out["staff_note"] = ("Google Places is not set up on this deployment "
                             "(GOOGLE_PLACES_API_KEY), so no listing can be read.")
        return out
    if not rec:
        out["state"] = "no_place"
        out["staff_note"] = "No Google listing has been confirmed for this client."
        return out
    rows = readings(rec["client"])
    out["readings"] = len(rows)
    good = [r for r in rows
            if not r.get("error")
            and (r.get("rating") is not None or r.get("review_count") is not None)]
    if not rows:
        out["state"] = "no_snapshot"
        out["staff_note"] = "The listing is confirmed and has not been read yet."
        return out
    if not good:
        out["state"] = "unread"
        out["error"] = str(rows[0].get("error") or "the listing could not be read")
        out["staff_note"] = f"The last read failed: {out['error']}"
        return out
    latest = good[0]
    out.update({
        "measured": True, "state": "ok",
        "rating": latest.get("rating"), "review_count": latest.get("review_count"),
        "status": latest.get("status") or "",
        "status_label": STATUS_LABELS.get(latest.get("status") or "", ""),
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
    """What a client's page may carry: the reading and its date, the change,
    and nothing about our tooling. None unless there is a reading."""
    if not r or r.get("state") != "ok":
        return None
    change = dict(r.get("change") or {})
    return {"measured": True, "rating": r["rating"], "review_count": r["review_count"],
            "status_label": r.get("status_label") or "", "as_of": r["as_of"],
            "change": change if change.get("measured") else {"measured": False},
            "maps_url": (r.get("record") or {}).get("maps_url") or ""}


def card_for(name: str, *, today: date | None = None) -> dict | None:
    """The client page's Business Profile block, or None when there is
    nothing measured to draw -- a card that says "coming soon" or "not
    connected" on a page a client reads is a sentence about our tooling on
    a document about their business."""
    try:
        return public_view(reading(name, today=today))
    except Exception:                                   # noqa: BLE001 - the page must render
        log.exception("places: card_for failed")
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
    """Read every confirmed listing once, inside the nightly window.

    ``force=False`` is what the scheduler passes: it ticks hourly and this
    returns without a call unless the window has passed. One call per
    client, a listing already read today skipped (a restart inside the
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
        out["skipped"] = "Google Places is not set up (GOOGLE_PLACES_API_KEY)."
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
            if res.get("kind") in ("refused", "unconfigured"):
                # A key Google refuses refuses every client; stop rather than
                # record the same refusal a hundred times.
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
            "configured": configured(), "confirmed": len(all_records())}
