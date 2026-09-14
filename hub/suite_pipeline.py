"""The client's own pipeline, read from their Smart 1 Suite sub-account.

The "Pipeline & leads" card on Client 360. Every other Suite card on the
record is about *our* relationship with the client -- which sub-account is
theirs, whether the app is installed, our proposal against them in the
agency pipeline (hub/suite_opportunity.py). This one is about *their*
business: the leads the marketing is producing, sitting in the pipelines
they run inside their own sub-account.

Two calls on a **sub-account token** from `hub.suite_accounts.token_for()`:

  * ``GET /opportunities/pipelines``  -- the pipelines and their stages, so
    the card can say "4 in Quote sent" rather than print a stage id.
  * ``GET /opportunities/search``     -- every opportunity, paged 100 at a
    time. HighLevel's filters are thin (status, created-after), so the
    counting is done here from the raw rows: open by stage, new this week
    and month, won and lost in the last 30 days, and the open ones nobody
    has touched in a month.

Rules, the ones hub/suite_accounts.py sets for anything on a client token:

**Three answers, never two.** ``state`` is ``connected`` (numbers below are
real), ``not_connected`` (no sub-account, or the app is not installed on it
-- a setting, with the detail saying which), or ``not_measured`` (the
account is there, the read failed -- a fault, with the detail saying why).
A card that shows zero leads because the token could not be minted is a
card that tells a rep the marketing is not working.

**Never raises to the route.** ``for_client()`` returns a dict on every
path, and no token value ever lands in one.

**Counting is local and dated from `now`.** Passed in by the tests, taken
from the clock by the route -- and every date HighLevel sends is parsed
tolerantly, because they arrive as ISO strings with and without zones and
as epoch milliseconds depending on the endpoint's age.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import requests

__all__ = ["for_client", "summarize", "CACHE_TTL", "MAX_PAGES"]

API_BASE = os.environ.get("GHL_API_BASE", "https://services.leadconnectorhq.com")
API_VERSION = os.environ.get("GHL_API_VERSION", "2021-07-28")
TIMEOUT = 25
PAGE = 100
MAX_PAGES = 10              # 1,000 opportunities is more than any client we run
RECENT = 8                  # rows in the "newest leads" list
STALE_DAYS = 30             # open and untouched this long = "going cold"
CACHE_TTL = 300             # seconds; one client record reload is not a re-read

_cache: dict[str, tuple[float, dict]] = {}


class SuitePipelineError(RuntimeError):
    """Message is safe to show a rep -- never contains the token."""


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Version": API_VERSION,
            "Accept": "application/json"}


def _get(token: str, path: str, params: dict) -> dict:
    try:
        r = requests.get(f"{API_BASE}{path}", headers=_headers(token),
                         params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SuitePipelineError(f"Couldn't reach Smart 1 Suite ({type(exc).__name__}).")
    if r.status_code in (401, 403):
        # Never the body: HighLevel errors have carried token fragments.
        raise SuitePipelineError(
            f"Smart 1 Suite rejected the request ({r.status_code}). It needs the "
            "opportunities.readonly scope on the Hub app, which takes an agency re-consent.")
    if not r.ok:
        raise SuitePipelineError(f"Smart 1 Suite returned HTTP {r.status_code} for {path}.")
    try:
        return r.json() or {}
    except ValueError:
        return {}


# ------------------------------------------------------------------- dates
def _when(value) -> datetime | None:
    """ISO with or without a zone, or epoch ms -- as an aware UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            secs = float(value) / (1000.0 if value > 1e11 else 1.0)
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if s.isdigit():
        return _when(int(s))
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _money(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# ----------------------------------------------------------------- reading
def pipelines(token: str, location_id: str) -> list[dict]:
    """``[{id, name, stages: [{id, name}]}]`` in the order Suite shows them."""
    data = _get(token, "/opportunities/pipelines", {"locationId": location_id})
    out = []
    for p in data.get("pipelines") or []:
        if not p.get("id"):
            continue
        out.append({"id": str(p["id"]), "name": str(p.get("name") or "Pipeline"),
                    "stages": [{"id": str(s.get("id") or ""), "name": str(s.get("name") or "")}
                               for s in (p.get("stages") or []) if s.get("id")]})
    return out


def opportunities(token: str, location_id: str) -> tuple[list[dict], bool]:
    """Every opportunity on the sub-account, and whether the read was complete.

    Offset paging, 100 a page, capped at MAX_PAGES: a client with more than
    a thousand deals gets the newest thousand and a ``truncated`` flag the
    card turns into a footnote, not a wrong total presented as a right one.
    """
    rows: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        data = _get(token, "/opportunities/search",
                    {"location_id": location_id, "limit": PAGE, "page": page,
                     "status": "all"})
        batch = data.get("opportunities") or []
        rows.extend(batch)
        meta = data.get("meta") or {}
        if len(batch) < PAGE or not (meta.get("nextPage") or meta.get("nextPageUrl")):
            return rows, True
    return rows, False


# ---------------------------------------------------------------- counting
def summarize(pipes: list[dict], rows: list[dict], *, now: datetime | None = None) -> dict:
    """The card's numbers from the raw rows. Pure; the tests drive it directly."""
    now = now or datetime.now(timezone.utc)
    week, month = now - timedelta(days=7), now - timedelta(days=30)
    stale_before = now - timedelta(days=STALE_DAYS)
    stage_of = {s["id"]: (p, s) for p in pipes for s in p["stages"]}
    by_pipe: dict[str, dict] = {}
    stage_slot: dict[str, dict] = {}
    for p in pipes:
        bp = {"id": p["id"], "name": p["name"], "open": 0, "value": 0.0, "stages": []}
        for s in p["stages"]:
            slot = {"id": s["id"], "name": s["name"], "count": 0, "value": 0.0}
            bp["stages"].append(slot)
            stage_slot[s["id"]] = slot
        by_pipe[p["id"]] = bp
    totals = {"open": 0, "open_value": 0.0, "new_week": 0, "new_month": 0,
              "won_month": 0, "won_value_month": 0.0, "lost_month": 0, "stale": 0,
              "all": len(rows), "unstaged": 0}
    recent = []
    for raw in rows:
        status = str(raw.get("status") or "open").lower()
        value = _money(raw.get("monetaryValue"))
        created = _when(raw.get("createdAt") or raw.get("dateAdded"))
        updated = _when(raw.get("updatedAt") or raw.get("lastStatusChangeAt")
                        or raw.get("lastStageChangeAt") or raw.get("dateUpdated")) or created
        changed = _when(raw.get("lastStatusChangeAt")) or updated
        stage_id = str(raw.get("pipelineStageId") or "")
        pipe_id = str(raw.get("pipelineId") or "")
        pipe, stage = stage_of.get(stage_id, (None, None))
        if created and created >= week:
            totals["new_week"] += 1
        if created and created >= month:
            totals["new_month"] += 1
        if status == "open":
            totals["open"] += 1
            totals["open_value"] += value
            if updated and updated < stale_before:
                totals["stale"] += 1
            slot = stage_slot.get(stage_id)
            if slot is not None:
                slot["count"] += 1
                slot["value"] += value
                bp = by_pipe.get(pipe["id"]) if pipe else by_pipe.get(pipe_id)
                if bp:
                    bp["open"] += 1
                    bp["value"] += value
            else:
                totals["unstaged"] += 1
        elif status == "won" and changed and changed >= month:
            totals["won_month"] += 1
            totals["won_value_month"] += value
        elif status in ("lost", "abandoned") and changed and changed >= month:
            totals["lost_month"] += 1
        contact = raw.get("contact") or {}
        recent.append({
            "id": str(raw.get("id") or ""),
            "name": str(raw.get("name") or contact.get("name") or "Untitled"),
            "contact": str(contact.get("name") or ""),
            "status": status,
            "value": value,
            "pipeline": pipe["name"] if pipe else "",
            "stage": stage["name"] if stage else "",
            "source": str(raw.get("source") or ""),
            "created": created.isoformat(timespec="seconds") if created else "",
            "_sort": created.timestamp() if created else 0.0,
        })
    recent.sort(key=lambda r: r["_sort"], reverse=True)
    for r in recent:
        r.pop("_sort", None)
    pipes_out = list(by_pipe.values())
    for p in pipes_out:
        p["value"] = round(p["value"], 2)
        for s in p["stages"]:
            s["value"] = round(s["value"], 2)
    totals["open_value"] = round(totals["open_value"], 2)
    totals["won_value_month"] = round(totals["won_value_month"], 2)
    return {"totals": totals, "pipelines": pipes_out, "recent": recent[:RECENT]}


# ---------------------------------------------------------------- the card
def _answer(state: str, detail: str, **extra) -> dict:
    return dict({"state": state, "measured": state == "connected", "detail": detail}, **extra)


def for_client(name: str, url: str = "", *, now: datetime | None = None, fresh: bool = False) -> dict:
    name = str(name or "").strip()
    if not name:
        return _answer("not_measured", "No client was named.")
    key = f"{name.lower()}|{(url or '').lower()}"
    hit = _cache.get(key)
    if hit and not fresh and hit[0] > time.time():
        return dict(hit[1], cached=True)
    try:
        from hub import suite_accounts
        acct = suite_accounts.token_for(name, url)
    except Exception as exc:                               # noqa: BLE001
        return _answer("not_measured", f"The Suite link could not be read ({type(exc).__name__}).")
    if acct.get("state") != "connected" or not acct.get("token"):
        state = "not_measured" if acct.get("state") == "not_measured" else "not_connected"
        return _answer(state, acct.get("detail") or "This client has no Smart 1 Suite sub-account attached.",
                       location_id=acct.get("location_id") or "")
    token, loc = acct["token"], acct["location_id"]
    try:
        pipes = pipelines(token, loc)
    except SuitePipelineError as exc:
        return _answer("not_measured", str(exc), location_id=loc)
    except Exception as exc:                               # noqa: BLE001
        return _answer("not_measured", f"The pipelines could not be read ({type(exc).__name__}).", location_id=loc)
    try:
        rows, complete = opportunities(token, loc)
    except SuitePipelineError as exc:
        return _answer("not_measured", str(exc), location_id=loc)
    except Exception as exc:                               # noqa: BLE001
        return _answer("not_measured", f"The opportunities could not be read ({type(exc).__name__}).", location_id=loc)
    out = _answer("connected", "", location_id=loc, client=name,
                  suite_url=f"/suite/?manage={loc}", truncated=not complete,
                  stale_days=STALE_DAYS, read_at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"))
    out.update(summarize(pipes, rows, now=now))
    out["no_pipelines"] = not pipes
    _cache[key] = (time.time() + CACHE_TTL, out)
    return out
