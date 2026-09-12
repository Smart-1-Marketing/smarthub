"""Inactive GA4 / GTM housekeeping for SmartHub QA.

GA4 can answer the 60-day activity question directly. GTM cannot: Tag Manager
has no traffic-reporting API. A GTM container is therefore called inactive only
when it has no published tags, or when every GA4 measurement ID in its live
version maps to an accessible GA4 property with zero events and zero sessions
in the same 60-day window. Ambiguous containers are shown as Needs Review.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote

import requests
from flask import Blueprint, current_app, jsonify, render_template, request

from hub import jsonstore
from .app import require_login

qa_bp = Blueprint(
    "google_inactive_qa", __name__,
    url_prefix="/tools/google-access/qa-inactive",
    template_folder="templates",
)

WINDOW_DAYS = 60
CACHE_SECONDS = 600
# A GA4 property or GTM container checked within this window is not checked
# again on the next scan -- its classification is read out of
# google_inactive_qa_resource_cache.json instead of spending an
# `_ga_activity`/`_ga_measurement_ids`/`_live_tags` call on a resource whose
# answer cannot have changed in the last few hours. That is the whole of
# "held in a table so we don't have to perform the same scan over and over":
# a resource already known active or inactive costs nothing further until
# this window has passed, whatever traffic it carries -- there is no cheaper
# way to learn a property's own 60-day activity than to ask for it once, so
# the saving is in not asking twice, not in guessing which ones to skip.
# `full=1` on the start route bypasses this per-resource cache entirely
# (a genuine from-scratch recheck); the ordinary "Update scan" press does not.
RESOURCE_STALE_HOURS = 24
# GA4's Data/Admin APIs carry no per-user throttle the way Tag Manager does
# (this file's own gtm_get() docstring says so), so properties are checked
# several at a time rather than one after another. GTM stays paced through
# Google Finder's shared, adaptive gtm_get() regardless of how many workers
# call it -- its own lock serializes the actual HTTP to stay under Tag
# Manager's limit, so a worker pool here only lets one container's
# classification run while another's network call is still waiting its turn.
GA4_WORKERS = 6
GTM_WORKERS = 4
_GA_RE = re.compile(r"\bG-[A-Z0-9]{4,}\b", re.I)
_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_LOCK = threading.Lock()

# A scan across every connected login's GA4 properties and GTM containers is
# minutes, not seconds -- and slower still on a day this account's Tag
# Manager quota is already stretched, since the scan now paces itself rather
# than burning through it faster and wrongly. Running it inline on the
# request thread meant the page had nothing to show but a static "Scanning..."
# for the entire wait, indistinguishable from a hung request. It runs on a
# background thread instead, and progress is written to a small file on the
# shared data disk rather than kept in a module-level dict -- this Hub runs
# two gunicorn workers, and a browser's poll can land on either one
# regardless of which worker started the scan.
_SCAN_THREAD_LOCK = threading.Lock()
_SCAN_THREAD: threading.Thread | None = None
# A `running: true` flag with no recent heartbeat is a worker that died
# mid-scan, not a scan still in flight -- the hub/domain_purchase.py
# distinction between a stale snapshot and a dead sweep. Treating it as
# running forever would mean nobody could start a new scan without
# restarting the whole Hub.
_HEARTBEAT_STALE_SECONDS = 90


def _finder():
    # Imported lazily so this QA page cannot make Hub startup depend on the
    # standalone Google Finder Flask app.
    from modules.google_finder import app as gf
    return gf


def _path(name: str) -> str:
    return os.path.join(jsonstore.data_root(), name)


def _progress_path() -> str:
    return _path("google_inactive_qa_progress.json")


def _result_path() -> str:
    return _path("google_inactive_qa_last_result.json")


def _resource_cache_path() -> str:
    return _path("google_inactive_qa_resource_cache.json")


def _resource_cache() -> dict:
    data = jsonstore.read_json(_resource_cache_path(), default={})
    return data if isinstance(data, dict) else {}


def _save_resource_cache(data: dict) -> None:
    jsonstore.write_json(_resource_cache_path(), data)


def _cache_fresh(entry: dict | None, cutoff_iso: str) -> bool:
    """Whether a cached resource is still within RESOURCE_STALE_HOURS.

    Timestamps are ISO-8601 UTC with a fixed `timespec="seconds"`, the same
    shape _audit() already writes, so a plain string comparison sorts
    correctly without parsing either side back into a datetime.
    """
    if not entry:
        return False
    checked = str(entry.get("checked_at") or "")
    return bool(checked) and checked >= cutoff_iso


def _progress_read() -> dict:
    data = jsonstore.read_json(_progress_path(), default={})
    return data if isinstance(data, dict) else {}


def _progress_write(data: dict) -> None:
    data = dict(data)
    data["heartbeat_at"] = time.time()
    jsonstore.write_json(_progress_path(), data)


def _progress_update(**kwargs) -> None:
    data = _progress_read()
    data.update(kwargs)
    _progress_write(data)


def _progress_is_running(data: dict) -> bool:
    if not data.get("running"):
        return False
    return (time.time() - float(data.get("heartbeat_at") or 0)) < _HEARTBEAT_STALE_SECONDS


def _actor() -> str:
    try:
        from hub.users_routes import session_from_environ
        data = session_from_environ(request.environ) or {}
        return str(data.get("e") or data.get("email") or data.get("n") or "")
    except Exception:
        return str(request.environ.get("s1hub.user") or "")


def _is_admin() -> bool:
    try:
        from hub.users_routes import session_from_environ
        data = session_from_environ(request.environ) or {}
        return True if not data else data.get("r") in ("admin", "super_admin")
    except Exception:
        return False


def _skip_key(kind: str, login: str, resource: str) -> str:
    return f"{kind.upper()}:{login.lower()}:{resource}"


def _skips() -> dict:
    data = jsonstore.read_json(_path("google_inactive_qa_skips.json"), default={})
    return data if isinstance(data, dict) else {}


def _save_skips(data: dict) -> None:
    jsonstore.write_json(_path("google_inactive_qa_skips.json"), data)


def _audit(action: str, row: dict, result="ok", detail="") -> None:
    p = _path("google_inactive_qa_audit.json")
    data = jsonstore.read_json(p, default=[])
    if not isinstance(data, list):
        data = []
    data.append({
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "actor": _actor(), "action": action, "result": result,
        "kind": row.get("kind", ""), "google_login": row.get("login", ""),
        "account_id": row.get("account_id", ""), "resource": row.get("resource", ""),
        "name": row.get("name", ""), "detail": str(detail)[:500],
    })
    jsonstore.write_json(p, data[-2000:])


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _note_google(url: str, ok: bool = True) -> None:
    """Count one Google API call against the daily quota on /diagnostics.

    The same shape as `google_finder`'s and `hub/drive_files.py`'s own
    `_note_google` -- filed by URL, so `quotas.google_api_of()` buckets it
    under the API it belongs to rather than the total disappearing into
    "other". This tool walks every GA4 property and every Tag Manager
    container across every connected login, which is the heaviest Google
    sweep in the Hub, so leaving it out understated the day's usage by
    exactly the calls most likely to exhaust it.

    Recorded **before** `raise_for_status()` at every call site, and whatever
    the response was: a 403 or a 429 has still spent a request against the
    quota, and a run of those is what a spent quota looks like from this
    side. Counting only the successes would make the usage page quietest
    precisely when it matters.

    It never raises: a quota note that breaks the sweep it is measuring is
    worse than a number nobody has.
    """
    try:
        from hub import quotas
        quotas.record_google(url, module="google_access", ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


def _get(token: str, url: str, params=None) -> dict:
    r = requests.get(url, headers=_headers(token), params=params or {}, timeout=20)
    _note_google(url, ok=r.ok)
    r.raise_for_status()
    return r.json() if r.content else {}


def _post(token: str, url: str, body: dict) -> dict:
    r = requests.post(url, headers=_headers(token), json=body, timeout=25)
    _note_google(url, ok=r.ok)
    r.raise_for_status()
    return r.json() if r.content else {}


def _delete(token: str, url: str) -> None:
    r = requests.delete(url, headers=_headers(token), timeout=20)
    _note_google(url, ok=r.ok)
    r.raise_for_status()


def _pages(token: str, url: str, key: str, params=None):
    params = dict(params or {})
    while True:
        payload = _get(token, url, params)
        yield from (payload.get(key) or [])
        nxt = payload.get("nextPageToken")
        if not nxt:
            return
        params["pageToken"] = nxt


def _gtm_get(token: str, url: str, params=None) -> dict:
    """Tag Manager reads go through Google Finder's shared, paced getter.

    Every other call in this file hits `_get` directly, which is right for
    GA4 -- that API does not rate-limit the way Tag Manager does. GTM is the
    one Google Finder built `gtm_get()` for after measuring what an unpaced
    sweep costs against it: 180 accounts on one login threw a 429 on very
    nearly every first attempt, and the retries alone spent 440 seconds and a
    quarter of the day's quota rediscovering a limit that a shared, adaptive
    interval avoids hitting in the first place.

    This is "the heaviest Google sweep in the Hub" (its own commit message
    says so) and it walks GTM containers across *every* connected login on
    every visit to this page -- unpaced, it was firing a burst of GTM calls
    with no memory of the last one, on the same per-user limit Google
    Finder's own scheduled sweep paces itself against. Two callers hitting
    one limit, only one of them slowing down, is what turns a few
    rate-limited accounts into a scan that keeps retrying (or keeps stacking
    "Could not inspect live container -- HTTP 429" rows) for a very long
    time. Going through `gtm_get()` puts this tool on the *same* shared
    interval and retry-after handling as the sweep, so a 429 either meets is
    news the other slows down for.
    """
    return _finder().gtm_get(token, url, params=params or {})


def _gtm_pages(token: str, url: str, key: str, params=None):
    params = dict(params or {})
    while True:
        payload = _gtm_get(token, url, params)
        yield from (payload.get(key) or [])
        nxt = payload.get("nextPageToken")
        if not nxt:
            return
        params["pageToken"] = nxt


def _ga_properties(token: str) -> list[dict]:
    out = []
    url = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
    for acc in _pages(token, url, "accountSummaries", {"pageSize": 200}):
        account_id = str(acc.get("account") or "").split("/")[-1]
        account_name = acc.get("displayName") or account_id or "Google Analytics"
        for prop in acc.get("propertySummaries") or []:
            pid = str(prop.get("property") or "").split("/")[-1]
            if pid:
                out.append({"account": account_name, "account_id": account_id,
                            "name": prop.get("displayName") or pid, "property_id": pid})
    return out


def _ga_activity(token: str, property_id: str) -> dict:
    payload = _post(
        token,
        f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport",
        {"dateRanges": [{"startDate": f"{WINDOW_DAYS}daysAgo", "endDate": "yesterday"}],
         "metrics": [{"name": "eventCount"}, {"name": "sessions"}], "limit": "1"},
    )
    vals = ((payload.get("rows") or [{}])[0].get("metricValues") or [])

    def _val(i):
        try:
            return int(float(vals[i].get("value") or 0))
        except (IndexError, AttributeError, TypeError, ValueError):
            return 0

    return {"events": _val(0), "sessions": _val(1)}


def _ga_measurement_ids(token: str, property_id: str) -> set[str]:
    out: set[str] = set()
    try:
        url = f"https://analyticsadmin.googleapis.com/v1beta/properties/{property_id}/dataStreams"
        for stream in _pages(token, url, "dataStreams", {"pageSize": 200}):
            mid = str((stream.get("webStreamData") or {}).get("measurementId") or "").upper()
            if mid:
                out.add(mid)
    except requests.HTTPError:
        # Stream detail is helpful for linking GTM but is not required to call
        # the GA property itself active/inactive.
        pass
    return out


def _gtm_accounts(token: str) -> list[dict]:
    # GTM v2 list endpoints accept pageToken but not pageSize.
    return list(_gtm_pages(token,
        "https://tagmanager.googleapis.com/tagmanager/v2/accounts", "account"))


def _gtm_containers(token: str, account_id: str) -> list[dict]:
    return list(_gtm_pages(token,
        f"https://tagmanager.googleapis.com/tagmanager/v2/accounts/{account_id}/containers",
        "container"))


def _live_tags(token: str, account_id: str, container_id: str) -> tuple[list[dict], str]:
    url = ("https://tagmanager.googleapis.com/tagmanager/v2/accounts/"
           f"{account_id}/containers/{container_id}/versions:live")
    try:
        version = _gtm_get(token, url)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (400, 404):
            return [], "No published container version"
        raise
    return list(version.get("tag") or []), ""


def _measurement_ids(value: Any) -> set[str]:
    try:
        value = json.dumps(value, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        value = str(value)
    return {m.upper() for m in _GA_RE.findall(value)}


def _http_reason(exc: requests.HTTPError) -> str:
    status = exc.response.status_code if exc.response is not None else "error"
    try:
        msg = (exc.response.json().get("error") or {}).get("message") or ""
    except Exception:
        msg = ""
    return f"HTTP {status}" + (f": {msg}" if msg else "")


def _scan_login(login: str, refresh: str, on_progress=None, resource_cache: dict | None = None,
                 new_cache: dict | None = None, full: bool = False,
                 cutoff_iso: str = "") -> tuple[list[dict], list[dict], list[dict]]:
    """Classify one login's GA4 properties and GTM containers.

    `resource_cache` is the whole cache as it stood before this scan (shared
    read-only across every login this run); `new_cache` is the cache this
    run is building and is mutated in place with only what this login's own
    discovery calls actually found, so a resource deleted in Google since
    the last scan drops out rather than lingering forever.

    `on_progress(login, status, text, **extra)` is called once per resource
    classified -- `status` is "inactive"/"review"/"active" so the caller can
    keep a running count, or None for a stage announcement with nothing
    classified yet (e.g. "Listing Tag Manager accounts").
    """
    resource_cache = resource_cache if resource_cache is not None else {}
    if new_cache is None:
        new_cache = {}
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    def _progress(status: str | None, text: str, **extra) -> None:
        if on_progress:
            try:
                on_progress(login, status, text, **extra)
            except Exception:                              # noqa: BLE001
                pass

    gf = _finder()
    token = gf.refresh_access_token(login, refresh)
    inactive, review, active = [], [], []
    buckets = {"inactive": inactive, "review": review, "active": active}
    by_mid: dict[str, list[dict]] = {}

    def _emit(status: str, row: dict) -> dict:
        row = {**row, "status": status}
        buckets[status].append(row)
        return row

    # -- GA4 properties -----------------------------------------------
    _progress(None, "Listing GA4 properties")
    properties = _ga_properties(token)
    total_ga4 = len(properties)

    def _ga_base(prop: dict) -> dict:
        return {"kind": "GA4", "login": login, "account": prop["account"],
                "account_id": prop["account_id"], "name": prop["name"],
                "resource": prop["property_id"], "public_id": ""}

    # A resource never seen before, or one whose last known answer was
    # "inactive"/"review", goes to the front of the queue that still has to
    # make a live call; one already known active last time goes to the
    # back. With a bounded worker pool that is what "bump an obviously
    # active site down the queue" actually buys: the calls spent this run
    # are spent on the properties this tool exists to find, not reconfirming
    # ones nobody is asking about. A property that answers active for the
    # first time is not skipped -- there is no cheaper way to learn a
    # property's own 60-day activity than to ask for it once -- but every
    # scan after that one, within RESOURCE_STALE_HOURS, costs it nothing.
    cached_ga4, fresh_ga4 = [], []
    for prop in properties:
        entry = resource_cache.get(_skip_key("GA4", login, prop["property_id"]))
        if not full and _cache_fresh(entry, cutoff_iso):
            cached_ga4.append((prop, entry))
        else:
            fresh_ga4.append((prop, (entry or {}).get("status")))
    fresh_ga4.sort(key=lambda pair: 0 if pair[1] != "active" else 1)

    ga4_done = 0

    def _place_ga4(prop: dict, row: dict, status: str, mids: set[str], cache_it: bool) -> None:
        nonlocal ga4_done
        ga4_done += 1
        row = _emit(status, row)
        if status != "review":
            for mid in mids:
                by_mid.setdefault(mid, []).append(row)
        if cache_it and status != "review":
            new_cache[_skip_key("GA4", login, prop["property_id"])] = {
                **row, "measurement_ids": sorted(mids), "checked_at": now_iso,
            }
        _progress(status, f"GA4 property {ga4_done} of {total_ga4}: {row['name']}",
                  resource_index=ga4_done, resource_total=total_ga4, resource_kind="GA4")

    if cached_ga4:
        _progress(None, f"GA4 properties: {len(cached_ga4)} checked recently, "
                         f"{len(fresh_ga4)} to check", resource_total=total_ga4)
    for prop, entry in cached_ga4:
        status = entry.get("status") or "review"
        row = {**_ga_base(prop), "events": entry.get("events"), "sessions": entry.get("sessions"),
               "reason": entry.get("reason") or ""}
        _place_ga4(prop, row, status, set(entry.get("measurement_ids") or []), cache_it=False)
        new_cache[_skip_key("GA4", login, prop["property_id"])] = entry

    def _fetch_ga4(prop: dict):
        base = _ga_base(prop)
        try:
            a = _ga_activity(token, prop["property_id"])
        except requests.HTTPError as exc:
            return prop, base, None, None, set(), _http_reason(exc)
        mids = _ga_measurement_ids(token, prop["property_id"])
        return prop, base, a["events"], a["sessions"], mids, ""

    if fresh_ga4:
        props_only = [p for p, _ in fresh_ga4]
        with ThreadPoolExecutor(max_workers=min(GA4_WORKERS, len(props_only))) as pool:
            futures = [pool.submit(_fetch_ga4, p) for p in props_only]
            for fut in as_completed(futures):
                prop, base, events, sessions, mids, error = fut.result()
                if error:
                    row = {**base, "events": None, "sessions": None,
                           "reason": "Could not read GA4 activity — " + error}
                    _place_ga4(prop, row, "review", set(), cache_it=True)
                    continue
                dead = events == 0 and sessions == 0
                row = {**base, "events": events, "sessions": sessions,
                       "reason": (f"0 events and 0 sessions in the last {WINDOW_DAYS} days"
                                  if dead else "Activity detected")}
                _place_ga4(prop, row, "inactive" if dead else "active", mids, cache_it=True)

    # -- GTM accounts and containers -----------------------------------
    _progress(None, "Listing Tag Manager accounts")
    try:
        accounts = _gtm_accounts(token)
    except requests.HTTPError as exc:
        _emit("review", {"kind": "GTM", "login": login, "account": "Tag Manager",
                          "account_id": "", "name": "Could not list GTM accounts", "resource": "",
                          "public_id": "", "events": None, "sessions": None,
                          "reason": _http_reason(exc)})
        return inactive, review, active

    containers_all: list[dict] = []
    for idx, acc in enumerate(accounts):
        aid = str(acc.get("accountId") or acc.get("path") or "").split("/")[-1]
        aname = acc.get("name") or aid or "Tag Manager"
        if not aid:
            continue
        _progress(None, f"Tag Manager account {idx + 1} of {len(accounts)}: {aname}")
        try:
            containers = _gtm_containers(token, aid)
        except requests.HTTPError as exc:
            _emit("review", {"kind": "GTM", "login": login, "account": aname,
                              "account_id": aid, "name": "Could not list containers", "resource": "",
                              "public_id": "", "events": None, "sessions": None,
                              "reason": _http_reason(exc)})
            continue
        for c in containers:
            cid = str(c.get("containerId") or "")
            if cid:
                containers_all.append({"account_id": aid, "account": aname, "container_id": cid,
                                        "name": c.get("name") or cid, "public_id": c.get("publicId") or ""})

    total_gtm = len(containers_all)

    def _gtm_base(c: dict) -> dict:
        return {"kind": "GTM", "login": login, "account": c["account"], "account_id": c["account_id"],
                "name": c["name"], "resource": c["container_id"], "public_id": c["public_id"],
                "events": None, "sessions": None}

    def _classify_gtm(c: dict, has_tags: bool, mids: set[str], why_no_live: str) -> tuple[dict, str]:
        base = _gtm_base(c)
        if why_no_live or not has_tags:
            return {**base, "reason": why_no_live or "Published container has no tags"}, "inactive"
        if not mids:
            return ({**base, "reason": "Live tags exist, but no GA4 measurement ID can be resolved"},
                    "review")
        linked = [r for mid in mids for r in by_mid.get(mid, [])]
        linked = list({r["resource"]: r for r in linked}.values())
        if not linked:
            return ({**base, "reason": "GA4 ID found, but its property is not visible to this login"},
                    "review")
        if all(r["status"] == "inactive" for r in linked):
            return ({**base, "events": sum(r.get("events") or 0 for r in linked),
                     "sessions": sum(r.get("sessions") or 0 for r in linked),
                     "reason": "Linked GA4 property/properties have no activity for 60 days"},
                    "inactive")
        if any(r["status"] == "active" for r in linked):
            # This is the "GTM firing recently needs no further check" case:
            # its own live tags already resolved to a GA4 property this scan
            # already knows is active, whether that property was checked
            # fresh a moment ago or read back from cache -- no further Tag
            # Manager call happens for a container once it lands here.
            return {**base, "reason": "Linked GA4 activity detected"}, "active"
        return {**base, "reason": "Linked GA4 activity could not be measured"}, "review"

    cached_gtm, fresh_gtm = [], []
    for c in containers_all:
        entry = resource_cache.get(_skip_key("GTM", login, c["container_id"]))
        if not full and _cache_fresh(entry, cutoff_iso):
            cached_gtm.append((c, entry))
        else:
            fresh_gtm.append((c, (entry or {}).get("status")))
    fresh_gtm.sort(key=lambda pair: 0 if pair[1] != "active" else 1)

    gtm_done = 0

    def _place_gtm(c: dict, has_tags: bool | None, mids: set[str] | None, why_no_live: str | None,
                   error: str = "", cache_it: bool = True) -> None:
        nonlocal gtm_done
        gtm_done += 1
        if error:
            row = {**_gtm_base(c), "name": "Could not inspect live container",
                   "reason": "Could not inspect live container — " + error}
            _emit("review", row)
            status = "review"
        else:
            row, status = _classify_gtm(c, bool(has_tags), mids or set(), why_no_live or "")
            _emit(status, row)
            if cache_it:
                # checked_at marks when Google was last actually asked about
                # this container's live tags, not when its verdict was last
                # recomputed -- the two are different, and stamping "now"
                # here on a cache-hit replay would reset the staleness clock
                # every time it was reused, so a container found active
                # once would never be asked about again.
                new_cache[_skip_key("GTM", login, c["container_id"])] = {
                    "has_tags": bool(has_tags), "measurement_ids": sorted(mids or set()),
                    "why_no_live": why_no_live or "", "checked_at": now_iso,
                }
        _progress(status, f"Tag Manager container {gtm_done} of {total_gtm}: {c['name']}",
                  resource_index=gtm_done, resource_total=total_gtm, resource_kind="GTM")

    if cached_gtm:
        _progress(None, f"Tag Manager containers: {len(cached_gtm)} checked recently, "
                         f"{len(fresh_gtm)} to check", resource_total=total_gtm)
    for c, entry in cached_gtm:
        # A cached container's *verdict* is always recomputed against the
        # by_mid map this scan just built, never replayed from the cache --
        # only the live-tags call itself is skipped. A property that was
        # inactive last week and has since come back to life must not leave
        # a container reading a week-old "inactive" it was never asked
        # about again.
        _place_gtm(c, entry.get("has_tags", False), set(entry.get("measurement_ids") or []),
                   entry.get("why_no_live") or "", cache_it=False)
        new_cache[_skip_key("GTM", login, c["container_id"])] = entry

    def _fetch_gtm(c: dict):
        try:
            tags, why_no_live = _live_tags(token, c["account_id"], c["container_id"])
        except requests.HTTPError as exc:
            return c, None, None, None, _http_reason(exc)
        mids = set().union(*(_measurement_ids(tag) for tag in tags)) if tags else set()
        return c, bool(tags), mids, why_no_live, ""

    if fresh_gtm:
        containers_only = [c for c, _ in fresh_gtm]
        with ThreadPoolExecutor(max_workers=min(GTM_WORKERS, len(containers_only))) as pool:
            futures = [pool.submit(_fetch_gtm, c) for c in containers_only]
            for fut in as_completed(futures):
                c, has_tags, mids, why_no_live, error = fut.result()
                _place_gtm(c, has_tags, mids, why_no_live, error=error)

    return inactive, review, active


def _dedupe(rows: list[dict]) -> list[dict]:
    out = {}
    for r in rows:
        key = (r.get("kind"), r.get("account_id") or r.get("account"), r.get("resource"), r.get("name"))
        out.setdefault(key, r)
    return list(out.values())


def _run_scan(full: bool = False) -> dict:
    """The scan body, run on a background thread so the page can poll live
    progress instead of staring at a static "Scanning..." for however long
    this account's Tag Manager throttling makes it take.

    `full=True` (the "Full rescan" control, not the everyday "Update scan"
    press) ignores the per-resource cache entirely -- every property and
    container is checked live again, the way every scan used to work.
    """
    old_cache = _resource_cache()
    new_cache: dict = {}
    now = dt.datetime.now(dt.timezone.utc)
    cutoff_iso = (now - dt.timedelta(hours=RESOURCE_STALE_HOURS)).isoformat(timespec="seconds")

    gf = _finder()
    accounts, source_error = gf.connected_accounts_result()
    inactive, review, active = [], [], []
    errors = []
    _progress_write({
        "running": True, "done": False, "error": None,
        "started_at": now.isoformat(timespec="seconds"),
        "total_logins": len(accounts), "completed_logins": 0,
        "current_login": "", "current_stage": "",
        "resource_index": 0, "resource_total": 0, "resource_kind": "",
        "inactive_count": 0, "review_count": 0, "active_count": 0,
    })

    # Logins are scanned one at a time on this single background thread, so
    # only this thread ever calls _on_progress -- the lock is defensive
    # (and doubles as the write serializer for _progress_update) rather than
    # something a race actually depends on today.
    totals_lock = threading.Lock()
    totals = {"inactive": 0, "review": 0, "active": 0}

    def _on_progress(login: str, status: str | None, text: str, **extra) -> None:
        with totals_lock:
            if status:
                totals[status] = totals.get(status, 0) + 1
            _progress_update(current_login=login, current_stage=text,
                              inactive_count=totals["inactive"], review_count=totals["review"],
                              active_count=totals["active"], **extra)

    for i, account in enumerate(accounts):
        login = account.get("email") or ""
        if str(account.get("status") or "ACTIVE") != "ACTIVE":
            review.append({"kind": "Google", "login": login, "account": "Connected login",
                           "account_id": "", "name": login, "resource": login, "public_id": "",
                           "status": "review", "events": None, "sessions": None,
                           "reason": "Google login requires reconnection"})
        else:
            try:
                dead, unsure, alive = _scan_login(
                    login, account["refresh_token"], on_progress=_on_progress,
                    resource_cache=old_cache, new_cache=new_cache, full=full, cutoff_iso=cutoff_iso)
                inactive += dead; review += unsure; active += alive
            except Exception as exc:
                errors.append(f"{login}: {type(exc).__name__}: {exc}")
        with totals_lock:
            totals["inactive"], totals["review"], totals["active"] = len(inactive), len(review), len(active)
        _progress_update(completed_logins=i + 1, current_login="", current_stage="",
                          resource_index=0, resource_total=0, resource_kind="",
                          inactive_count=totals["inactive"], review_count=totals["review"],
                          active_count=totals["active"])

    _save_resource_cache(new_cache)

    inactive, review, active = map(_dedupe, (inactive, review, active))
    skip_data = _skips()
    visible, skipped = [], []
    for row in inactive:
        rec = skip_data.get(_skip_key(row["kind"], row["login"], row["resource"]))
        if rec:
            skipped.append({**row, "skip": rec})
        else:
            visible.append(row)

    sort_key = lambda r: (str(r.get("kind")), str(r.get("account", "")).lower(), str(r.get("name", "")).lower())
    payload = {
        "ok": not (source_error and not accounts), "window_days": WINDOW_DAYS,
        "scanned_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "connected_logins": len(accounts), "active_count": len(active),
        "inactive": sorted(visible, key=sort_key), "review": sorted(review, key=sort_key),
        "skipped": sorted(skipped, key=sort_key), "source_error": source_error, "errors": errors,
    }
    with _LOCK:
        _CACHE.update(at=time.time(), payload=payload)
    jsonstore.write_json(_result_path(), payload)
    _progress_update(running=False, done=True, current_login="", current_stage="",
                      completed_logins=len(accounts),
                      inactive_count=len(visible) + len(skipped), review_count=len(review),
                      active_count=len(active))
    return payload


def start_scan_async(force: bool, full: bool = False) -> dict:
    """Kick off a scan on a background thread, or say why one did not start.

    Checked cross-worker through the shared progress file rather than a
    module-level flag -- this Hub runs two gunicorn workers, and a start
    request from one browser tab can land on either regardless of which one
    a poll later lands on.

    `force` only means "start a pass now even if the last one finished
    recently" -- the everyday "Update scan" press. It does not by itself
    touch the per-resource cache; `full` is the separate "Full rescan"
    control that ignores it, because the two questions ("is it worth
    running a pass right now" and "should already-known resources be
    reconfirmed") are not the same question and conflating them would make
    every ordinary press pay for a from-scratch recheck.
    """
    global _SCAN_THREAD
    with _SCAN_THREAD_LOCK:
        if _progress_is_running(_progress_read()):
            return {"ok": True, "started": False, "already_running": True}

        if not force:
            with _LOCK:
                fresh = bool(_CACHE.get("payload")) and (
                    time.time() - float(_CACHE.get("at") or 0) < CACHE_SECONDS)
            if fresh:
                return {"ok": True, "started": False, "already_running": False, "cached": True}

        if force:
            _clear_cache()

        app_obj = current_app._get_current_object()

        def _runner():
            with app_obj.app_context():
                try:
                    _run_scan(full=full)
                except Exception as exc:                    # noqa: BLE001
                    _progress_update(running=False, done=True,
                                      error=f"{type(exc).__name__}: {exc}")

        _SCAN_THREAD = threading.Thread(target=_runner, name="qa-inactive-scan", daemon=True)
        _SCAN_THREAD.start()
        return {"ok": True, "started": True, "already_running": False}


def _clear_cache():
    with _LOCK:
        _CACHE.update(at=0.0, payload=None)


@qa_bp.route("/")
@require_login
def page():
    return render_template("qa_inactive.html", window_days=WINDOW_DAYS)


@qa_bp.route("/api/scan")
@require_login
def api_scan():
    """The most recently completed scan. Never blocks and never starts one --
    a GET that could trigger minutes of Google API calls is the shape
    hub/domain_purchase.py already refuses for its own refresh: a reload, a
    prefetch or a link preview must not be able to fire this. Starting a scan
    is POST /api/scan/start; watching one run is GET /api/scan/progress.
    """
    with _LOCK:
        payload = _CACHE.get("payload")
    if payload is None:
        stored = jsonstore.read_json(_result_path(), default=None)
        if isinstance(stored, dict):
            payload = stored
            with _LOCK:
                _CACHE.update(at=time.time(), payload=payload)
    if payload is None:
        return jsonify(ok=False, pending=True,
                        error="No scan has completed yet. Press Update scan.")
    return jsonify(payload)


def _truthy(value) -> bool:
    return str(value or "").lower() in ("1", "true", "yes")


@qa_bp.route("/api/scan/start", methods=["POST"])
@require_login
def api_scan_start():
    force = _truthy(request.args.get("force"))
    full = _truthy(request.args.get("full"))
    return jsonify(start_scan_async(force=force, full=full))


@qa_bp.route("/api/scan/progress")
@require_login
def api_scan_progress():
    prog = _progress_read()
    running = _progress_is_running(prog)
    prog["running"] = running
    if not running and prog.get("done"):
        stored = jsonstore.read_json(_result_path(), default=None)
        if isinstance(stored, dict):
            prog["result"] = stored
    try:
        prog["gtm_pace"] = _finder().gtm_pace_state()
    except Exception:                                       # noqa: BLE001
        prog["gtm_pace"] = None
    return jsonify(prog)


@qa_bp.route("/api/skip", methods=["POST"])
@require_login
def api_skip():
    row = request.get_json(silent=True) or {}
    kind = str(row.get("kind") or "").upper()
    login, resource = str(row.get("login") or "").strip(), str(row.get("resource") or "").strip()
    if kind not in ("GA4", "GTM") or not login or not resource:
        return jsonify(ok=False, error="kind, login and resource are required"), 400
    data = _skips()
    data[_skip_key(kind, login, resource)] = {
        "kind": kind, "login": login, "resource": resource, "name": str(row.get("name") or ""),
        "reason": str(row.get("reason") or "").strip(), "by": _actor(),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    _save_skips(data); _audit("skip", row, detail=row.get("reason", "")); _clear_cache()
    return jsonify(ok=True)


@qa_bp.route("/api/unskip", methods=["POST"])
@require_login
def api_unskip():
    row = request.get_json(silent=True) or {}
    data = _skips()
    data.pop(_skip_key(str(row.get("kind") or ""), str(row.get("login") or ""),
                       str(row.get("resource") or "")), None)
    _save_skips(data); _audit("unskip", row); _clear_cache()
    return jsonify(ok=True)


def _access_token(login: str) -> str:
    gf = _finder()
    accounts, error = gf.connected_accounts_result()
    if error and not accounts:
        raise RuntimeError(error)
    found = next((a for a in accounts if a.get("email", "").lower() == login.lower()), None)
    if not found:
        raise LookupError("Connected Google login not found")
    return gf.refresh_access_token(found["email"], found["refresh_token"])


@qa_bp.route("/api/delete", methods=["POST"])
@require_login
def api_delete():
    if not _is_admin():
        return jsonify(ok=False, error="Admin access is required to delete Google resources."), 403
    row = request.get_json(silent=True) or {}
    kind = str(row.get("kind") or "").upper()
    login = str(row.get("login") or "").strip()
    resource = str(row.get("resource") or "").strip()
    account_id = str(row.get("account_id") or "").strip()
    name, confirm = str(row.get("name") or "").strip(), str(row.get("confirm") or "").strip()
    if kind not in ("GA4", "GTM") or not login or not resource:
        return jsonify(ok=False, error="kind, login and resource are required"), 400
    if not name or confirm != name:
        return jsonify(ok=False, error="Type the resource name exactly to confirm deletion."), 400
    if kind == "GTM" and not account_id:
        return jsonify(ok=False, error="GTM account id is required."), 400

    try:
        token = _access_token(login)
        if kind == "GA4":
            url = f"https://analyticsadmin.googleapis.com/v1beta/properties/{quote(resource, safe='')}"
            success = "GA4 property moved to the Analytics trash can."
        else:
            url = ("https://tagmanager.googleapis.com/tagmanager/v2/accounts/"
                   f"{quote(account_id, safe='')}/containers/{quote(resource, safe='')}")
            success = "GTM container deleted."
        _delete(token, url)
    except requests.HTTPError as exc:
        detail = _http_reason(exc)
        status = exc.response.status_code if exc.response is not None else 500
        if status in (401, 403):
            needed = ("https://www.googleapis.com/auth/analytics.edit" if kind == "GA4" else
                      "https://www.googleapis.com/auth/tagmanager.delete.containers")
            detail += (f". This Google login may have the older read-only/edit grant. "
                       f"Reconnect it with {needed} permission, then retry.")
        _audit("delete", row, result="error", detail=detail)
        return jsonify(ok=False, error=detail), status
    except Exception as exc:
        _audit("delete", row, result="error", detail=str(exc))
        return jsonify(ok=False, error=str(exc)), 500

    data = _skips(); data.pop(_skip_key(kind, login, resource), None); _save_skips(data)
    _audit("delete", row, detail=success); _clear_cache()
    return jsonify(ok=True, message=success)


@qa_bp.route("/api/audit")
@require_login
def api_audit():
    rows = jsonstore.read_json(_path("google_inactive_qa_audit.json"), default=[])
    return jsonify(rows=list(reversed(rows[-200:]))) if isinstance(rows, list) else jsonify(rows=[])
