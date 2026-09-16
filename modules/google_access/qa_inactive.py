"""Inactive GA4 / GTM housekeeping for SmartHub QA.

GA4 can answer the 60-day activity question directly. GTM cannot: Tag Manager
has no traffic-reporting API, so a container's own tags are the only thing
that can positively confirm it is still firing -- and confirmed GA4 activity
is one way in for a container to read "active". Everything else -- no
published tags, no GA4 tag in what is published, a GA4 tag whose property is
not visible to this login or could not be read -- reads as inactive by
default. Whether the linked GA4 property is itself separately flagged
active or inactive by our own GA4-side scan is not a gating condition here;
only a confirmed "yes, this fired" overrides the default.

A container GA4 confirms alive needs no more of this and is skipped; every
other GTM container the scan calls inactive is checked a second, direct way
instead -- a real fetch of its resolved website, looking for the container's
own tag in the raw HTML, because that is the only positive signal available
short of GA4. A confirmed find there promotes the row out of the deletable
inactive bucket into Needs Review, since the tag is genuinely on the page and
whether to act on that is a person's call. Needs Review otherwise stays what
it always was: a scan that genuinely could not run (a listing call that
failed, a login that needs reconnecting), not an activity judgment call.

A Needs Review row can be skipped, and the scan honors it. A container whose
tag a site check found genuinely live is never going to be deleted, so
without this it came back every scan forever with no way to say "seen it,
leave it" -- the record was written and only the inactive list was ever
partitioned against it. A connected login that needs reconnecting is the one
row skipping is refused for (SKIPPABLE_KINDS): the scan cannot see a single
resource behind a login it could not read, so hiding the row would hide
however many properties are behind it and report a clean sweep of accounts
nothing actually looked at.
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


# GTM has no traffic-reporting API, and the GA4-linkage check above answers a
# different question anyway -- "does a resolvable GA4 property show
# activity", not "is this container's own tag actually on a page". This is
# the direct answer to the second question: fetch a real page and look for
# the container's own public ID in its raw HTML, the way the tag actually
# arrives on a site (a <script> snippet pasted in <head>, or the <noscript>
# fallback iframe). It runs no JavaScript, so a container injected purely by
# another script's own runtime behaviour would not show up here -- which is
# why a "not found" result is worded as that, never as a confirmed removal.
#
# A container GA4 already confirms alive needs none of this and is skipped
# (it is already "active"). Every *other* GTM container the scan calls
# inactive gets one of these automatically now -- the manual "Check site"
# button remains for a container the automatic pass could not resolve a
# website for, or for re-checking one on demand.
_SITE_CHECK_TIMEOUT = 20
_SITE_CHECK_MAX_BYTES = 2_000_000
_SITE_CHECK_UA = "Mozilla/5.0 (compatible; Smart1Hub/1.0; +https://smart1.agency)"
# A site check checked within this window is not repeated on the next scan,
# the same shape RESOURCE_STALE_HOURS already gives GA4/GTM resources --
# there is no cheaper way to learn whether a tag is on a page than to fetch
# it once. Bounded on count too, per scan pass: fetching client websites is
# not a Google API call with a shared pacer behind it, and what is left over
# is simply not a candidate this run rather than something the scan waits
# on indefinitely -- it is picked up on the next one.
SITE_CHECK_STALE_HOURS = 24
SITE_CHECK_WORKERS = 4
SITE_CHECK_BUDGET = 60


def _site_checks() -> dict:
    data = jsonstore.read_json(_path("google_inactive_qa_site_checks.json"), default={})
    return data if isinstance(data, dict) else {}


def _save_site_checks(data: dict) -> None:
    jsonstore.write_json(_path("google_inactive_qa_site_checks.json"), data)


def _fetch_page_html(url: str) -> dict:
    try:
        resp = requests.get(url, timeout=_SITE_CHECK_TIMEOUT, allow_redirects=True,
                            headers={"User-Agent": _SITE_CHECK_UA,
                                     "Accept": "text/html,application/xhtml+xml"})
    except requests.RequestException as exc:
        return {"ok": False, "url": url, "status": None,
                "error": f"Could not reach the page: {exc}"}
    body = resp.content[:_SITE_CHECK_MAX_BYTES]
    try:
        html = body.decode(resp.encoding or "utf-8", errors="replace")
    except (LookupError, TypeError):
        html = body.decode("utf-8", errors="replace")
    return {"ok": resp.ok, "url": resp.url, "status": resp.status_code,
            "html": html if resp.ok else "",
            "error": "" if resp.ok else f"The page answered HTTP {resp.status_code}."}


def _run_site_check(public_id: str, url: str) -> dict:
    """Fetch `url` and look for `public_id` in its raw HTML.

    Returns the entry shape stored in google_inactive_qa_site_checks.json
    and drawn on the row: {url, checked_at, found, status, error}. `by` is
    stamped by the caller -- a person's own check and the automatic scan
    pass record it differently.
    """
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    fetched = _fetch_page_html(url)
    entry: dict[str, Any] = {
        "url": fetched.get("url") or url,
        "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    if not fetched.get("ok"):
        entry.update(found=None, status=fetched.get("status"),
                     error=fetched.get("error") or "Could not fetch the page.")
    else:
        entry.update(found=public_id.upper() in (fetched.get("html") or "").upper(),
                     status=fetched.get("status"), error="")
    return entry


def _resolve_client_url(account: str) -> tuple[str, dict]:
    """An exact client-registry match on a GTM account's own name, or nothing.

    Never a substring or a fuzzy guess: checking, and reporting on, the
    wrong client's website is worse than not checking at all. Shared by the
    "suggest a URL" endpoint and the automatic site-check pass in
    `_run_scan()`.
    """
    account = str(account or "").strip()
    if not account:
        return "", {"known": False}
    try:
        from hub import client_key
        result = client_key.resolve(name=account)
    except Exception as exc:                               # noqa: BLE001
        return "", {"known": False, "error": str(exc)}
    domain = str(result.get("domain") or "")
    if result.get("known") and domain:
        return f"https://{domain}", result
    return "", result


def _audit_entry(action: str, row: dict, result="ok", detail="") -> dict:
    return {
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "actor": _actor(), "action": action, "result": result,
        "kind": row.get("kind", ""), "google_login": row.get("login", ""),
        "account_id": row.get("account_id", ""), "resource": row.get("resource", ""),
        "name": row.get("name", ""), "detail": str(detail)[:500],
    }


def _audit_write(entries: list[dict]) -> None:
    """Append a batch of audit entries in one read-modify-write.

    A bulk action files one entry per resource -- the log has to name every
    property that was skipped or deleted, not "20 rows" -- but writing the
    whole file once per row would be twenty reads and twenty writes of the
    same JSON, and on the mirrored jsonstore that is twenty database round
    trips as well. One write for the batch instead.
    """
    if not entries:
        return
    p = _path("google_inactive_qa_audit.json")
    data = jsonstore.read_json(p, default=[])
    if not isinstance(data, list):
        data = []
    data.extend(entries)
    jsonstore.write_json(p, data[-2000:])


def _audit(action: str, row: dict, result="ok", detail="") -> None:
    _audit_write([_audit_entry(action, row, result, detail)])


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
        """Inactive is the default; confirmed GA4 traffic is the only override.

        Whether the property a live tag points at is itself visible to this
        login, readable, or separately flagged active/inactive by the GA4
        side of this same scan is not a gating condition -- a container with
        no positively-confirmed activity in the window reads inactive,
        whatever the reason we could not confirm it. Needs Review stays for
        a scan that could not run at all (see the callers of this function),
        never for an activity judgment call.
        """
        base = _gtm_base(c)
        if why_no_live or not has_tags:
            return {**base, "reason": why_no_live or "Published container has no tags"}, "inactive"
        if not mids:
            return ({**base, "reason": "Live tags exist, but reference no GA4 measurement ID -- "
                                        f"nothing here to check for traffic in the last "
                                        f"{WINDOW_DAYS} days"}, "inactive")
        linked = [r for mid in mids for r in by_mid.get(mid, [])]
        linked = list({r["resource"]: r for r in linked}.values())
        if any(r["status"] == "active" for r in linked):
            # This is the "GTM firing recently needs no further check" case:
            # its own live tags already resolved to a GA4 property this scan
            # already knows is active, whether that property was checked
            # fresh a moment ago or read back from cache -- no further Tag
            # Manager call happens for a container once it lands here.
            return {**base, "reason": "Linked GA4 activity detected"}, "active"
        if linked:
            return ({**base, "events": sum(r.get("events") or 0 for r in linked),
                     "sessions": sum(r.get("sessions") or 0 for r in linked),
                     "reason": f"Linked GA4 property/properties have no activity for "
                               f"{WINDOW_DAYS} days"},
                    "inactive")
        return ({**base, "reason": "Live tags reference a GA4 ID, but its activity could not be "
                                    "confirmed from this login -- no confirmed traffic in the "
                                    f"last {WINDOW_DAYS} days"}, "inactive")

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
        # Saved after every login, not only once at the end -- this Hub
        # redeploys on every merge to main, often several times an hour, and
        # a deploy restarts the worker this scan is running on mid-flight.
        # Saving only at completion meant a scan interrupted by one of those
        # deploys threw away everything it had already checked, so on a busy
        # merge day the scan could go a long time without ever completing
        # once and every attempt paid the full cost again. Merged onto the
        # cache as it stood before this run, never replaced by new_cache
        # alone -- new_cache only holds the logins reached so far, and
        # writing that wholesale mid-run would erase perfectly good cached
        # entries for the logins not yet reached. Only the final save below,
        # once every login has actually been covered, is allowed to replace
        # the file outright and let a resource Google no longer has drop out.
        _save_resource_cache({**old_cache, **new_cache})

    _save_resource_cache(new_cache)

    inactive, review, active = map(_dedupe, (inactive, review, active))

    # A GTM container GA4 already confirms alive is skipped here -- it is
    # already "active", above, and needs no fetch of its own. Every *other*
    # GTM container the scan just called inactive gets a direct check of its
    # own now: fetch its resolved website and look for its own tag, because
    # GTM has no traffic API and this is the only positive signal available
    # short of that. Bounded on count (SITE_CHECK_BUDGET) rather than a
    # phase deadline -- a ThreadPoolExecutor used as a `with` block waits for
    # every submitted future at exit however long that takes, so the only
    # bound actually enforced here is how many fetches are ever submitted,
    # each itself capped at _SITE_CHECK_TIMEOUT seconds by requests.get()
    # (worst case: ceil(SITE_CHECK_BUDGET / SITE_CHECK_WORKERS) *
    # _SITE_CHECK_TIMEOUT). Nothing left over is lost -- it is simply not a
    # candidate this run and is picked up on the next scan.
    site_cutoff_iso = (now - dt.timedelta(hours=SITE_CHECK_STALE_HOURS)).isoformat(timespec="seconds")
    site_checks = _site_checks()
    candidates = []
    for row in inactive:
        if row.get("kind") != "GTM":
            continue
        key = _skip_key("GTM", row["login"], row["resource"])
        if not full and _cache_fresh(site_checks.get(key), site_cutoff_iso):
            continue
        candidates.append((key, row))

    if candidates:
        to_run = candidates[:SITE_CHECK_BUDGET]
        _progress_update(current_login="", current_stage=f"Checking {len(to_run)} sites for a live GTM tag")

        def _auto_check(item):
            key, row = item
            url, _resolved = _resolve_client_url(row.get("account") or "")
            if not url:
                return key, {
                    "url": "", "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    "found": None, "status": None, "by": "automatic scan",
                    "error": "No website could be resolved from the account name automatically "
                             "-- use Check site to supply one.",
                }
            entry = _run_site_check(row.get("public_id") or "", url)
            entry["by"] = "automatic scan"
            return key, entry

        new_site_checks: dict = {}
        with ThreadPoolExecutor(max_workers=min(SITE_CHECK_WORKERS, len(to_run))) as pool:
            for fut in as_completed({pool.submit(_auto_check, item): item for item in to_run}):
                key, entry = fut.result()
                new_site_checks[key] = entry

        site_checks.update(new_site_checks)
        _save_site_checks(site_checks)

        left = len(candidates) - len(to_run)
        if left:
            _progress_update(current_stage=f"Checked {len(to_run)} sites for a live GTM tag -- "
                                            f"{left} left for the next scan")

    # A confirmed find promotes the row out of the deletable inactive bucket
    # -- the tag is genuinely on the page, so a container GA4 happened not to
    # confirm must not sit next to a Delete button on the strength of that
    # alone. It lands in Needs Review, not Active: nothing here has measured
    # traffic through it, only that the tag is installed, and that is a
    # person's call to make rather than this scan's.
    still_inactive, promoted = [], []
    for row in inactive:
        entry = None
        if row.get("kind") == "GTM":
            entry = site_checks.get(_skip_key("GTM", row["login"], row["resource"]))
        if entry and entry.get("found") is True:
            promoted.append({**row, "status": "review", "site_check": entry,
                              "reason": f"Tag found live on {entry['url']} during an automatic site "
                                        "check -- GA4 activity is not confirmed, so this needs a look "
                                        "before it is deleted."})
        else:
            still_inactive.append(row)
    inactive, review = still_inactive, review + promoted

    # A site check result already on file belongs on the row the next time
    # it is drawn, whether it came from this run's automatic pass or from a
    # person's own press of Check site earlier -- otherwise a check would
    # only ever show on the screen that ran it and reads as gone after.
    def _with_site_check(row: dict) -> dict:
        if row.get("kind") != "GTM" or row.get("site_check"):
            return row
        rec = site_checks.get(_skip_key("GTM", row["login"], row["resource"]))
        return {**row, "site_check": rec} if rec else row

    inactive = [_with_site_check(r) for r in inactive]
    review = [_with_site_check(r) for r in review]

    # Both working lists are partitioned against the skip records, not just
    # the inactive one. A Needs Review row used to be unskippable in effect:
    # the record was written and the next scan never looked at it, so a
    # container whose tag a site check found genuinely live came back every
    # single scan, forever, with no way to say "seen it, leave it". Skipping
    # is the one answer that fits a row nobody is going to delete.
    #
    # `from` is recomputed here rather than stored on the record, so it
    # cannot go stale: it says which bucket the row falls into on *this*
    # scan, which is the honest answer once un-skipping puts it back.
    skip_data = _skips()
    visible, unsure, skipped = [], [], []
    for bucket, rows in (("inactive", inactive), ("review", review)):
        keep = visible if bucket == "inactive" else unsure
        for row in rows:
            rec = skip_data.get(_skip_key(row["kind"], row["login"], row["resource"]))
            if rec:
                skipped.append({**row, "skip": rec, "from": bucket})
            else:
                keep.append(row)
    review = unsure

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


# ---------------------------------------------------------------------------
# Bulk actions
# ---------------------------------------------------------------------------
# Every section of the page works a list, and the list is long: a scan across
# every connected login routinely turns up dozens of dead GA4 properties and
# containers at once, and clearing them one dialog at a time is the reason
# the backlog never got cleared. So each section can act on several rows in
# one press.
#
# Each bulk endpoint takes its own per-request cap rather than one shared
# number, because what a request costs differs by an order of magnitude:
# skip and un-skip are local JSON writes, a delete is one Google API call per
# row, and a site check is a fetch of somebody else's website with a 20s
# timeout on it. gunicorn runs with --timeout 180 (docker-start.sh), and a
# bulk request that blows through that is killed mid-flight -- taking whatever
# else that worker was doing with it -- so the caps below are sized to finish
# well inside it and the page sends a long selection as several requests
# instead. Worst case per request: deletes len * ~20s timeout, site checks
# ceil(len / SITE_CHECK_WORKERS) * _SITE_CHECK_TIMEOUT.
BULK_MAX_ROWS = 500
BULK_DELETE_MAX = 20
BULK_SITE_CHECK_MAX = 12


def _bulk_rows(payload: dict, limit: int) -> tuple[list[dict], str]:
    rows = payload.get("rows")
    if not isinstance(rows, list) or not rows:
        return [], "Select at least one row first."
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        return [], "Select at least one row first."
    if len(rows) > limit:
        return [], f"Too many rows in one request -- {limit} at a time."
    return rows, ""


def _row_label(row: dict) -> str:
    """How a row is named back to the person in a per-row bulk result."""
    name = str(row.get("name") or row.get("resource") or "").strip() or "Unnamed resource"
    kind = str(row.get("kind") or "").upper()
    return f"{kind}: {name}" if kind else name


def _row_identity(row: dict) -> tuple[str, str, str]:
    return (str(row.get("kind") or "").upper(), str(row.get("login") or "").strip(),
            str(row.get("resource") or "").strip())


# A skip is a judgment about a Google *resource*: this property or container
# is dead, or it is alive and we know, either way stop offering it. Needs
# Review also carries rows that are not resources at all -- a connected login
# that needs reconnecting -- and those are deliberately not skippable. The
# scan cannot see a single property behind a login it could not read, so
# skipping one would hide however many resources are behind it, and the
# screen would report a clean sweep of accounts nothing actually looked at.
# Reconnect the login instead; the row leaves on its own once it works.
SKIPPABLE_KINDS = ("GA4", "GTM")
_NOT_SKIPPABLE = ("Only GA4 properties and GTM containers can be skipped. A Google login "
                  "that needs reconnecting hides every resource behind it, so reconnect it "
                  "rather than skipping the row.")


def _skip_refusal(kind: str, login: str, resource: str) -> str:
    """Why this row cannot be skipped, or "" if it can."""
    if not login or not resource:
        return "kind, login and resource are required"
    if kind not in SKIPPABLE_KINDS:
        return _NOT_SKIPPABLE
    return ""


@qa_bp.route("/api/skip", methods=["POST"])
@require_login
def api_skip():
    row = request.get_json(silent=True) or {}
    kind = str(row.get("kind") or "").upper()
    login, resource = str(row.get("login") or "").strip(), str(row.get("resource") or "").strip()
    refusal = _skip_refusal(kind, login, resource)
    if refusal:
        return jsonify(ok=False, error=refusal), 400
    data = _skips()
    data[_skip_key(kind, login, resource)] = {
        "kind": kind, "login": login, "resource": resource, "name": str(row.get("name") or ""),
        "reason": str(row.get("reason") or "").strip(), "by": _actor(),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    _save_skips(data); _audit("skip", row, detail=row.get("reason", "")); _clear_cache()
    return jsonify(ok=True)


@qa_bp.route("/api/skip/bulk", methods=["POST"])
@require_login
def api_skip_bulk():
    """Skip several inactive candidates at once, with one shared reason.

    Exactly what pressing Skip on each row in turn would do -- the same
    record, the same audit entry per resource -- with one write of the skip
    file and one of the audit log instead of one of each per row. A row that
    is missing what a skip is keyed on is reported back by name rather than
    failing the whole batch: the other nineteen were still a deliberate
    press.
    """
    body = request.get_json(silent=True) or {}
    rows, error = _bulk_rows(body, BULK_MAX_ROWS)
    if error:
        return jsonify(ok=False, error=error), 400
    reason = str(body.get("reason") or "").strip()
    data = _skips()
    actor = _actor()
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    entries, failed, done = [], [], 0
    for row in rows:
        kind, login, resource = _row_identity(row)
        refusal = _skip_refusal(kind, login, resource)
        if refusal:
            failed.append({"name": _row_label(row), "error": refusal})
            continue
        data[_skip_key(kind, login, resource)] = {
            "kind": kind, "login": login, "resource": resource,
            "name": str(row.get("name") or ""), "reason": reason,
            "by": actor, "at": stamp,
        }
        entries.append(_audit_entry("skip", row, detail=reason))
        done += 1
    if done:
        _save_skips(data)
        _audit_write(entries)
        _clear_cache()
    return jsonify(ok=True, done=done, failed=failed)


@qa_bp.route("/api/unskip", methods=["POST"])
@require_login
def api_unskip():
    row = request.get_json(silent=True) or {}
    data = _skips()
    data.pop(_skip_key(str(row.get("kind") or ""), str(row.get("login") or ""),
                       str(row.get("resource") or "")), None)
    _save_skips(data); _audit("unskip", row); _clear_cache()
    return jsonify(ok=True)


@qa_bp.route("/api/unskip/bulk", methods=["POST"])
@require_login
def api_unskip_bulk():
    """Put several skipped resources back among the candidates at once."""
    body = request.get_json(silent=True) or {}
    rows, error = _bulk_rows(body, BULK_MAX_ROWS)
    if error:
        return jsonify(ok=False, error=error), 400
    data = _skips()
    entries, failed, done = [], [], 0
    for row in rows:
        kind, login, resource = _row_identity(row)
        if not kind or not login or not resource:
            failed.append({"name": _row_label(row),
                           "error": "kind, login and resource are required"})
            continue
        data.pop(_skip_key(kind, login, resource), None)
        entries.append(_audit_entry("unskip", row))
        done += 1
    if done:
        _save_skips(data)
        _audit_write(entries)
        _clear_cache()
    return jsonify(ok=True, done=done, failed=failed)


@qa_bp.route("/api/gtm/resolve-url", methods=["POST"])
@require_login
def api_gtm_resolve_url():
    """Suggest a site to check, from the GTM account's own name.

    A suggestion only -- the account name is very often the client's
    business name, so `client_key.resolve()` (exact domain or exact
    normalised name, never a substring) can usually offer their website.
    Nothing here is trusted on its own: the URL lands in an editable field
    and the person checking still decides, and picks a different one, before
    anything is fetched.
    """
    row = request.get_json(silent=True) or {}
    account = str(row.get("account") or "").strip()
    if not account:
        return jsonify(ok=True, known=False)
    url, result = _resolve_client_url(account)
    if result.get("error"):
        return jsonify(ok=False, error=result["error"])
    return jsonify(ok=True, known=bool(url), domain=result.get("domain") or "",
                   client=result.get("client") or "", confidence=result.get("confidence") or "",
                   suggested_url=url)


@qa_bp.route("/api/gtm/site-check", methods=["POST"])
@require_login
def api_gtm_site_check():
    """Is this container's own tag on the page, right now.

    GTM has no traffic API, so this is the direct answer instead: fetch a
    real page and look for the container's public ID in its raw HTML. What
    is inside the container -- GA4, another pixel, nothing at all -- is not
    asked about; only whether the tag itself is on the site.
    """
    row = request.get_json(silent=True) or {}
    login = str(row.get("login") or "").strip()
    resource = str(row.get("resource") or "").strip()
    public_id = str(row.get("public_id") or "").strip()
    url = str(row.get("url") or "").strip()
    if not login or not resource or not public_id:
        return jsonify(ok=False, error="login, resource and public_id are required"), 400
    if not url:
        return jsonify(ok=False, error="A website address is required to check."), 400

    entry = _run_site_check(public_id, url)
    entry["by"] = _actor()

    data = _site_checks()
    data[_skip_key("GTM", login, resource)] = entry
    _save_site_checks(data)
    if entry.get("found") is True:
        detail = f"found on {entry['url']}"
    elif entry.get("found") is False:
        detail = f"not found on {entry['url']}"
    else:
        detail = entry.get("error") or "could not check"
    _audit("site_check", {**row, "kind": "GTM"}, result="error" if entry.get("error") else "ok", detail=detail)
    # Deliberately no _clear_cache()/rescan here: the check just ran and its
    # result is returned inline for the page to apply to the one row in
    # place. A rescan is minutes of Google API calls to redraw one line;
    # the persisted record above is what carries the result into the *next*
    # scan's own payload (see _with_site_check in _run_scan).
    return jsonify(ok=True, **entry)


@qa_bp.route("/api/gtm/site-check/bulk", methods=["POST"])
@require_login
def api_gtm_site_check_bulk():
    """Run the on-page tag check across several GTM containers at once.

    The one thing a person cannot supply for a batch is a URL per row, so
    each row's site is worked out the same way the automatic pass in
    `_run_scan()` works it out, in this order: a URL sent with the row (the
    site somebody already checked it against), then an exact client-registry
    match on the GTM account's own name. Never a substring and never a guess
    -- an account that resolves to nothing records exactly that, with the
    error on the row, rather than fetching somebody else's website and
    reporting the answer as this container's.

    GA4 rows are refused rather than quietly counted: a property has no tag
    to look for on a page. Results come back per row for the page to apply
    in place, and are persisted on the same key the single check uses, so the
    next scan reattaches them (and promotes a confirmed find into Needs
    Review) exactly as it does for a check run by hand.
    """
    body = request.get_json(silent=True) or {}
    rows, error = _bulk_rows(body, BULK_SITE_CHECK_MAX)
    if error:
        return jsonify(ok=False, error=error), 400

    stored = _site_checks()
    actor = _actor()
    results: list[dict] = []
    targets: list[tuple[dict, str, str]] = []
    for row in rows:
        kind, login, resource = _row_identity(row)
        public_id = str(row.get("public_id") or "").strip()
        if kind != "GTM":
            results.append({"kind": kind, "login": login, "resource": resource,
                            "name": _row_label(row), "ok": False,
                            "error": "Only GTM containers can be checked on a page."})
            continue
        if not login or not resource or not public_id:
            results.append({"kind": kind, "login": login, "resource": resource,
                            "name": _row_label(row), "ok": False,
                            "error": "login, resource and public_id are required"})
            continue
        url = str(row.get("url") or "").strip()
        if not url:
            url = str((stored.get(_skip_key("GTM", login, resource)) or {}).get("url") or "").strip()
        if not url:
            url, _resolved = _resolve_client_url(row.get("account") or "")
        targets.append((row, public_id, url))

    def _check(item: tuple[dict, str, str]) -> tuple[dict, dict]:
        row, public_id, url = item
        if not url:
            return row, {
                "url": "",
                "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "found": None, "status": None, "by": actor,
                "error": "No website could be resolved from the account name automatically "
                         "-- use Check site to supply one.",
            }
        entry = _run_site_check(public_id, url)
        entry["by"] = actor
        return row, entry

    checked: list[tuple[dict, dict]] = []
    if targets:
        with ThreadPoolExecutor(max_workers=min(SITE_CHECK_WORKERS, len(targets))) as pool:
            for fut in as_completed([pool.submit(_check, item) for item in targets]):
                checked.append(fut.result())

    entries = []
    for row, entry in checked:
        kind, login, resource = _row_identity(row)
        stored[_skip_key("GTM", login, resource)] = entry
        if entry.get("found") is True:
            detail = f"found on {entry['url']}"
        elif entry.get("found") is False:
            detail = f"not found on {entry['url']}"
        else:
            detail = entry.get("error") or "could not check"
        entries.append(_audit_entry("site_check", {**row, "kind": "GTM"},
                                    result="error" if entry.get("error") else "ok", detail=detail))
        results.append({"kind": "GTM", "login": login, "resource": resource,
                        "name": _row_label(row), "ok": True, **entry})
    if checked:
        _save_site_checks(stored)
        _audit_write(entries)
    # No _clear_cache() here, for the same reason the single check does not
    # rescan: a scan is minutes of Google API calls to redraw a few lines,
    # and the persisted record above is what carries these results into the
    # next scan's own payload (see _with_site_check in _run_scan).
    found = sum(1 for _row, e in checked if e.get("found") is True)
    return jsonify(ok=True, checked=len(checked), found=found, results=results)


def _access_token(login: str) -> str:
    gf = _finder()
    accounts, error = gf.connected_accounts_result()
    if error and not accounts:
        raise RuntimeError(error)
    found = next((a for a in accounts if a.get("email", "").lower() == login.lower()), None)
    if not found:
        raise LookupError("Connected Google login not found")
    return gf.refresh_access_token(found["email"], found["refresh_token"])


def _delete_resource(token: str, kind: str, resource: str, account_id: str) -> str:
    """Delete one GA4 property or GTM container. Shared by both delete routes.

    GA4 deletion is Google's own soft delete -- the property lands in the
    Analytics trash can and can be restored from there. A GTM container is
    gone for good. The two are worded differently everywhere for that reason.
    """
    if kind == "GA4":
        url = f"https://analyticsadmin.googleapis.com/v1beta/properties/{quote(resource, safe='')}"
        success = "GA4 property moved to the Analytics trash can."
    else:
        url = ("https://tagmanager.googleapis.com/tagmanager/v2/accounts/"
               f"{quote(account_id, safe='')}/containers/{quote(resource, safe='')}")
        success = "GTM container deleted."
    _delete(token, url)
    return success


def _delete_http_reason(exc: requests.HTTPError, kind: str) -> str:
    detail = _http_reason(exc)
    status = exc.response.status_code if exc.response is not None else 500
    if status in (401, 403):
        needed = ("https://www.googleapis.com/auth/analytics.edit" if kind == "GA4" else
                  "https://www.googleapis.com/auth/tagmanager.delete.containers")
        detail += (f". This Google login may have the older read-only/edit grant. "
                   f"Reconnect it with {needed} permission, then retry.")
    return detail


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
        success = _delete_resource(token, kind, resource, account_id)
    except requests.HTTPError as exc:
        detail = _delete_http_reason(exc, kind)
        status = exc.response.status_code if exc.response is not None else 500
        _audit("delete", row, result="error", detail=detail)
        return jsonify(ok=False, error=detail), status
    except Exception as exc:
        _audit("delete", row, result="error", detail=str(exc))
        return jsonify(ok=False, error=str(exc)), 500

    data = _skips(); data.pop(_skip_key(kind, login, resource), None); _save_skips(data)
    _audit("delete", row, detail=success); _clear_cache()
    return jsonify(ok=True, message=success)


@qa_bp.route("/api/delete/bulk", methods=["POST"])
@require_login
def api_delete_bulk():
    """Delete several inactive candidates in one press.

    The per-row route asks for the resource's own name to be typed, which is
    the right guard for one deletion and no guard at all for twenty -- nobody
    types twenty names, they paste or they stop using the screen. The guard
    that carries over is the count: the page shows every row it is about to
    delete and asks for `DELETE <n>` where n is how many, so the confirmation
    cannot be right unless the person read the number they were shown. `total`
    is that number, checked against the typed phrase; a long selection arrives
    as several requests (BULK_DELETE_MAX at a time) and each one carries the
    selection's own total, never the chunk's.

    A failure is per row, never the batch: one container whose login lost its
    Tag Manager grant does not cancel the nineteen that were fine. Every row
    comes back with its own outcome and every one of them is in the audit log
    by name.
    """
    if not _is_admin():
        return jsonify(ok=False, error="Admin access is required to delete Google resources."), 403
    body = request.get_json(silent=True) or {}
    rows, error = _bulk_rows(body, BULK_DELETE_MAX)
    if error:
        return jsonify(ok=False, error=error), 400
    try:
        total = int(body.get("total") or 0)
    except (TypeError, ValueError):
        total = 0
    if total < len(rows):
        return jsonify(ok=False, error="The confirmation count is smaller than the rows sent."), 400
    if str(body.get("confirm") or "").strip().upper() != f"DELETE {total}":
        noun = "resource" if total == 1 else "resources"
        return jsonify(ok=False,
                       error=f"Type DELETE {total} exactly to confirm deleting {total} {noun}."), 400

    # One access token per Google login rather than one per row: refreshing
    # it is a network call of its own, and twenty rows off one login is the
    # ordinary case. A login whose refresh fails is remembered as failed so
    # the rest of its rows report that immediately instead of each retrying
    # a refresh that has already been answered.
    tokens: dict[str, tuple[bool, str]] = {}

    def _token_for(login: str) -> tuple[bool, str]:
        key = login.lower()
        if key not in tokens:
            try:
                tokens[key] = (True, _access_token(login))
            except Exception as exc:                       # noqa: BLE001
                tokens[key] = (False, str(exc))
        return tokens[key]

    skips = _skips()
    entries: list[dict] = []
    results: list[dict] = []
    deleted = 0
    skips_changed = False
    for row in rows:
        kind, login, resource = _row_identity(row)
        account_id = str(row.get("account_id") or "").strip()
        label = _row_label(row)
        if kind not in ("GA4", "GTM") or not login or not resource:
            results.append({"name": label, "kind": kind, "resource": resource, "ok": False,
                            "error": "kind, login and resource are required"})
            continue
        if kind == "GTM" and not account_id:
            results.append({"name": label, "kind": kind, "resource": resource, "ok": False,
                            "error": "GTM account id is required."})
            continue
        ok, token = _token_for(login)
        if not ok:
            entries.append(_audit_entry("delete", row, result="error", detail=token))
            results.append({"name": label, "kind": kind, "resource": resource, "ok": False,
                            "error": token})
            continue
        try:
            success = _delete_resource(token, kind, resource, account_id)
        except requests.HTTPError as exc:
            detail = _delete_http_reason(exc, kind)
            entries.append(_audit_entry("delete", row, result="error", detail=detail))
            results.append({"name": label, "kind": kind, "resource": resource, "ok": False,
                            "error": detail})
            continue
        except Exception as exc:                           # noqa: BLE001
            entries.append(_audit_entry("delete", row, result="error", detail=str(exc)))
            results.append({"name": label, "kind": kind, "resource": resource, "ok": False,
                            "error": str(exc)})
            continue
        if skips.pop(_skip_key(kind, login, resource), None) is not None:
            skips_changed = True
        entries.append(_audit_entry("delete", row, detail=success))
        results.append({"name": label, "kind": kind, "resource": resource, "ok": True,
                        "message": success})
        deleted += 1

    if skips_changed:
        _save_skips(skips)
    _audit_write(entries)
    if deleted:
        _clear_cache()
    return jsonify(ok=True, deleted=deleted, failed=len(results) - deleted, results=results)


# How many entries the history panel is handed. The store itself keeps 2000
# (see _audit_write); this is what one screen can usefully hold, newest
# first, and the panel says so rather than implying it is the whole log.
AUDIT_PAGE_SIZE = 200


@qa_bp.route("/api/audit")
@require_login
def api_audit():
    """What was skipped, un-skipped, checked and deleted here, newest first.

    `total` is the whole stored log, not this page of it, so the panel can
    say "showing the last 200 of 640" rather than presenting a window as the
    entirety. A store that is not a list is a store this cannot read: it
    answers `ok: false` rather than an empty list, because "nothing has been
    cleaned up" and "the log could not be read" are opposite answers and a
    panel that draws them identically is how a lost audit trail goes
    unnoticed.
    """
    rows = jsonstore.read_json(_path("google_inactive_qa_audit.json"), default=[])
    if not isinstance(rows, list):
        return jsonify(ok=False, rows=[], total=0,
                       error="The cleanup log could not be read.")
    return jsonify(ok=True, total=len(rows), page_size=AUDIT_PAGE_SIZE,
                   rows=list(reversed(rows[-AUDIT_PAGE_SIZE:])))
