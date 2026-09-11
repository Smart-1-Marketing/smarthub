"""Inactive Google Analytics / Tag Manager QA.

This is an internal housekeeping screen. It reuses the agency Google accounts
already connected in Google Finder, measures GA4 activity for the previous 60
days, and uses those GA4 measurements as the traffic signal for GTM wherever a
container exposes a GA4 measurement id.

Important: Tag Manager has no traffic/activity reporting API. A GTM container
is therefore only called inactive when one of these statements is defensible:

* it has no live version / no live tags; or
* every GA4 measurement id found in its live tags resolves to an accessible
  GA4 property that recorded zero events in the same 60-day window.

Everything else is returned as ``review`` rather than guessed inactive.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from typing import Any
from urllib.parse import quote

import requests
from flask import Blueprint, jsonify, render_template, request

from hub import jsonstore

from .app import require_login

qa_bp = Blueprint(
    "google_inactive_qa",
    __name__,
    url_prefix="/tools/google-access/qa-inactive",
    template_folder="templates",
)

WINDOW_DAYS = 60
CACHE_SECONDS = 10 * 60
_GA_RE = re.compile(r"\bG-[A-Z0-9]{4,}\b", re.I)
_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_CACHE_LOCK = threading.Lock()


def _finder():
    """Import lazily so a Google Finder startup issue does not break the Hub."""
    from modules.google_finder import app as gf
    return gf


def _data_path(name: str) -> str:
    return os.path.join(jsonstore.data_root(), name)


def _skip_key(kind: str, login: str, resource: str) -> str:
    return f"{kind}:{login.lower()}:{resource}"


def _read_skips() -> dict:
    value = jsonstore.read_json(_data_path("google_inactive_qa_skips.json"), default={})
    return value if isinstance(value, dict) else {}


def _write_skips(value: dict) -> None:
    jsonstore.write_json(_data_path("google_inactive_qa_skips.json"), value)


def _audit(action: str, *, kind: str, login: str, resource: str,
           name: str = "", result: str = "ok", detail: str = "") -> None:
    path = _data_path("google_inactive_qa_audit.json")
    rows = jsonstore.read_json(path, default=[])
    if not isinstance(rows, list):
        rows = []
    rows.append({
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "actor": _actor(),
        "action": action,
        "kind": kind,
        "google_login": login,
        "resource": resource,
        "name": name,
        "result": result,
        "detail": detail[:500],
    })
    # This is an audit trail, not an analytics warehouse. Keep enough history
    # to answer who removed what without letting a housekeeping file grow
    # forever.
    jsonstore.write_json(path, rows[-2000:])


def _actor() -> str:
    try:
        from hub.users_routes import session_from_environ
        data = session_from_environ(request.environ) or {}
        return str(data.get("e") or data.get("email") or data.get("n") or "")
    except Exception:
        return str(request.environ.get("s1hub.user") or "")


def _admin() -> bool:
    """Destructive actions are Admin/Super Admin only.

    The legacy shared-password session is treated as admin elsewhere in the
    Hub and remains so here; a signed account session must explicitly carry an
    admin role.
    """
    try:
        from hub.users_routes import session_from_environ
        data = session_from_environ(request.environ) or {}
        if not data:
            return True
        return data.get("r") in ("admin", "super_admin")
    except Exception:
        return False


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _get(token: str, url: str, params: dict | None = None) -> dict:
    r = requests.get(url, headers=_headers(token), params=params or {}, timeout=20)
    r.raise_for_status()
    return r.json() if r.content else {}


def _post(token: str, url: str, body: dict) -> dict:
    r = requests.post(url, headers=_headers(token), json=body, timeout=25)
    r.raise_for_status()
    return r.json() if r.content else {}


def _delete(token: str, url: str) -> requests.Response:
    r = requests.delete(url, headers=_headers(token), timeout=20)
    r.raise_for_status()
    return r


def _pages(token: str, url: str, key: str, params: dict | None = None):
    params = dict(params or {})
    while True:
        payload = _get(token, url, params)
        for row in payload.get(key, []) or []:
            yield row
        page = payload.get("nextPageToken")
        if not page:
            break
        params["pageToken"] = page


def _ga_properties(token: str) -> list[dict]:
    """Flatten Analytics Admin accountSummaries into active GA4 properties."""
    out = []
    url = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
    for account in _pages(token, url, "accountSummaries", {"pageSize": 200}):
        account_name = account.get("displayName") or account.get("account") or "Google Analytics"
        account_id = str(account.get("account") or "").split("/")[-1]
        for prop in account.get("propertySummaries", []) or []:
            pid = str(prop.get("property") or "").split("/")[-1]
            if not pid:
                continue
            out.append({
                "account": account_name,
                "account_id": account_id,
                "property": prop.get("displayName") or pid,
                "property_id": pid,
            })
    return out


def _ga_activity(token: str, property_id: str) -> dict:
    url = f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport"
    payload = _post(token, url, {
        "dateRanges": [{"startDate": f"{WINDOW_DAYS}daysAgo", "endDate": "yesterday"}],
        "metrics": [{"name": "eventCount"}, {"name": "sessions"}],
        "limit": "1",
    })
    values = ((payload.get("rows") or [{}])[0].get("metricValues") or [])
    def val(i):
        try:
            return int(float(values[i].get("value") or 0))
        except (IndexError, TypeError, ValueError, AttributeError):
            return 0
    return {"events": val(0), "sessions": val(1)}


def _ga_measurement_ids(token: str, property_id: str) -> set[str]:
    ids: set[str] = set()
    url = f"https://analyticsadmin.googleapis.com/v1beta/properties/{property_id}/dataStreams"
    try:
        for stream in _pages(token, url, "dataStreams", {"pageSize": 200}):
            web = stream.get("webStreamData") or {}
            mid = str(web.get("measurementId") or "").upper().strip()
            if mid:
                ids.add(mid)
    except requests.HTTPError:
        # A property can be visible through the summary yet not allow stream
        # detail to this login. That does not invalidate the GA activity read.
        pass
    return ids


def _gtm_accounts(token: str):
    return list(_pages(token,
                       "https://tagmanager.googleapis.com/tagmanager/v2/accounts",
                       "account", {"pageSize": 200}))


def _gtm_containers(token: str, account_id: str):
    url = f"https://tagmanager.googleapis.com/tagmanager/v2/accounts/{account_id}/containers"
    return list(_pages(token, url, "container", {"pageSize": 200}))


def _live_tags(token: str, account_id: str, container_id: str) -> tuple[list[dict], str]:
    """Return live tags and a reason when a live version does not exist."""
    url = ("https://tagmanager.googleapis.com/tagmanager/v2/accounts/"
           f"{account_id}/containers/{container_id}/versions:live")
    try:
        version = _get(token, url)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (404, 400):
            return [], "No published container version"
        raise
    return list(version.get("tag") or []), ""


def _measurement_ids(value: Any) -> set[str]:
    """Find GA4 measurement IDs anywhere in a GTM tag payload."""
    try:
        blob = json.dumps(value, separators=(",", ":"), ensure_ascii=True)
    except (TypeError, ValueError):
        blob = str(value)
    return {m.upper() for m in _GA_RE.findall(blob)}


def _scan_login(login: str, refresh_token: str) -> tuple[list[dict], list[dict], list[dict]]:
    gf = _finder()
    token = gf.refresh_access_token(login, refresh_token)

    inactive: list[dict] = []
    review: list[dict] = []
    active: list[dict] = []
    ga_by_mid: dict[str, list[dict]] = {}

    # GA4 is the authoritative activity signal.
    for prop in _ga_properties(token):
        try:
            activity = _ga_activity(token, prop["property_id"])
        except requests.HTTPError as exc:
            review.append({
                "kind": "GA4", "login": login, "account": prop["account"],
                "name": prop["property"], "resource": prop["property_id"],
                "status": "review", "reason": f"Could not read activity ({exc.response.status_code})",
                "events": None, "sessions": None,
            })
            continue

        row = {
            "kind": "GA4", "login": login, "account": prop["account"],
            "account_id": prop["account_id"], "name": prop["property"],
            "resource": prop["property_id"], "events": activity["events"],
            "sessions": activity["sessions"],
            "status": "inactive" if activity["events"] == 0 and activity["sessions"] == 0 else "active",
            "reason": (f"0 events and 0 sessions in the last {WINDOW_DAYS} days"
                       if activity["events"] == 0 and activity["sessions"] == 0
                       else "Activity detected"),
        }
        (inactive if row["status"] == "inactive" else active).append(row)
        for mid in _ga_measurement_ids(token, prop["property_id"]):
            ga_by_mid.setdefault(mid, []).append(row)

    # GTM does not report traffic. Inspect the live version and bind any GA4
    # measurement ids back to the already measured properties above.
    try:
        gtm_accounts = _gtm_accounts(token)
    except requests.HTTPError as exc:
        review.append({
            "kind": "GTM", "login": login, "account": "Tag Manager",
            "name": "Could not list GTM accounts", "resource": "",
            "status": "review", "reason": f"GTM API returned {exc.response.status_code}",
            "events": None, "sessions": None,
        })
        gtm_accounts = []

    for account in gtm_accounts:
        aid = str(account.get("accountId") or account.get("path") or "").split("/")[-1]
        aname = account.get("name") or aid or "Tag Manager"
        if not aid:
            continue
        try:
            containers = _gtm_containers(token, aid)
        except requests.HTTPError as exc:
            review.append({
                "kind": "GTM", "login": login, "account": aname,
                "name": "Could not list containers", "resource": aid,
                "status": "review", "reason": f"GTM API returned {exc.response.status_code}",
                "events": None, "sessions": None,
            })
            continue

        for container in containers:
            cid = str(container.get("containerId") or "")
            name = container.get("name") or cid
            if not cid:
                continue
            base = {
                "kind": "GTM", "login": login, "account": aname,
                "account_id": aid, "name": name, "resource": cid,
                "public_id": container.get("publicId") or "",
                "events": None, "sessions": None,
            }
            try:
                tags, no_live_reason = _live_tags(token, aid, cid)
            except requests.HTTPError as exc:
                review.append({**base, "status": "review",
                               "reason": f"Could not inspect live container ({exc.response.status_code})"})
                continue

            if no_live_reason or not tags:
                inactive.append({**base, "status": "inactive",
                                 "reason": no_live_reason or "Published container has no tags"})
                continue

            mids: set[str] = set()
            for tag in tags:
                mids.update(_measurement_ids(tag))
            if not mids:
                review.append({**base, "status": "review",
                               "reason": "Live tags exist, but no GA4 measurement ID can be resolved"})
                continue

            linked = [r for mid in mids for r in ga_by_mid.get(mid, [])]
            # de-dupe in case two IDs point to the same property.
            linked = list({(r["login"], r["resource"]): r for r in linked}.values())
            if not linked:
                review.append({**base, "status": "review",
                               "reason": "GA4 ID found, but its property is not visible to this Google login"})
                continue

            if all(r["status"] == "inactive" for r in linked):
                inactive.append({**base, "status": "inactive",
                                 "events": sum((r.get("events") or 0) for r in linked),
                                 "sessions": sum((r.get("sessions") or 0) for r in linked),
                                 "reason": "Linked GA4 property/properties have no activity for 60 days"})
            elif any(r["status"] == "active" for r in linked):
                active.append({**base, "status": "active", "reason": "Linked GA4 activity detected"})
            else:
                review.append({**base, "status": "review",
                               "reason": "Linked GA4 activity could not be measured"})

    return inactive, review, active


def scan(force: bool = False) -> dict:
    now = time.time()
    with _CACHE_LOCK:
        cached = _CACHE.get("payload")
        if not force and cached and now - float(_CACHE.get("at") or 0) < CACHE_SECONDS:
            return cached

    gf = _finder()
    accounts, source_error = gf.connected_accounts_result()
    inactive: list[dict] = []
    review: list[dict] = []
    active_count = 0
    account_errors = []

    for account in accounts:
        if str(account.get("status") or "ACTIVE") != "ACTIVE":
            review.append({
                "kind": "Google", "login": account.get("email") or "",
                "account": "Connected Google account", "name": account.get("email") or "",
                "resource": account.get("email") or "", "status": "review",
                "reason": "Google login requires reconnection", "events": None, "sessions": None,
            })
            continue
        try:
            dead, needs_review, alive = _scan_login(account["email"], account["refresh_token"])
            inactive.extend(dead)
            review.extend(needs_review)
            active_count += len(alive)
        except Exception as exc:  # one login never prevents checking the rest
            account_errors.append(f"{account.get('email')}: {type(exc).__name__}: {exc}")

    skips = _read_skips()
    visible, skipped = [], []
    for row in inactive:
        key = _skip_key(row["kind"], row["login"], row["resource"])
        if key in skips:
            row = dict(row)
            row["skip"] = skips[key]
            skipped.append(row)
        else:
            visible.append(row)

    payload = {
        "ok": not bool(source_error) and not (not accounts and source_error),
        "window_days": WINDOW_DAYS,
        "scanned_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "connected_logins": len(accounts),
        "inactive": sorted(visible, key=lambda r: (r["kind"], r["account"].lower(), r["name"].lower())),
        "review": sorted(review, key=lambda r: (r["kind"], r["account"].lower(), r["name"].lower())),
        "skipped": sorted(skipped, key=lambda r: (r["kind"], r["name"].lower())),
        "active_count": active_count,
        "source_error": source_error,
        "errors": account_errors,
    }
    with _CACHE_LOCK:
        _CACHE["at"] = now
        _CACHE["payload"] = payload
    return payload


def _clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE["at"] = 0.0
        _CACHE["payload"] = None


@qa_bp.route("/")
@require_login
def page():
    return render_template("qa_inactive.html", window_days=WINDOW_DAYS)


@qa_bp.route("/api/scan")
@require_login
def api_scan():
    force = str(request.args.get("force") or "").lower() in ("1", "true", "yes")
    return jsonify(scan(force=force))


@qa_bp.route("/api/skip", methods=["POST"])
@require_login
def api_skip():
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "").upper()
    login = str(body.get("login") or "").strip()
    resource = str(body.get("resource") or "").strip()
    name = str(body.get("name") or "").strip()
    reason = str(body.get("reason") or "").strip()
    if kind not in ("GA4", "GTM") or not login or not resource:
        return jsonify(ok=False, error="kind, login and resource are required"), 400
    data = _read_skips()
    data[_skip_key(kind, login, resource)] = {
        "kind": kind, "login": login, "resource": resource, "name": name,
        "reason": reason, "by": _actor(),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    _write_skips(data)
    _audit("skip", kind=kind, login=login, resource=resource, name=name, detail=reason)
    _clear_cache()
    return jsonify(ok=True)


@qa_bp.route("/api/unskip", methods=["POST"])
@require_login
def api_unskip():
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "").upper()
    login = str(body.get("login") or "").strip()
    resource = str(body.get("resource") or "").strip()
    data = _read_skips()
    data.pop(_skip_key(kind, login, resource), None)
    _write_skips(data)
    _audit("unskip", kind=kind, login=login, resource=resource,
           name=str(body.get("name") or ""))
    _clear_cache()
    return jsonify(ok=True)


def _account_token(login: str) -> str:
    gf = _finder()
    accounts, error = gf.connected_accounts_result()
    if error and not accounts:
        raise RuntimeError(error)
    match = next((a for a in accounts if a.get("email", "").lower() == login.lower()), None)
    if not match:
        raise LookupError("Connected Google login not found")
    return gf.refresh_access_token(match["email"], match["refresh_token"])


@qa_bp.route("/api/delete", methods=["POST"])
@require_login
def api_delete():
    if not _admin():
        return jsonify(ok=False, error="Admin access is required to delete Google resources."), 403

    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "").upper()
    login = str(body.get("login") or "").strip()
    resource = str(body.get("resource") or "").strip()
    name = str(body.get("name") or "").strip()
    account_id = str(body.get("account_id") or "").strip()
    confirm = str(body.get("confirm") or "").strip()

    if kind not in ("GA4", "GTM") or not login or not resource:
        return jsonify(ok=False, error="kind, login and resource are required"), 400
    if not name or confirm != name:
        return jsonify(ok=False, error="Type the resource name exactly to confirm deletion."), 400
    if kind == "GTM" and not account_id:
        return jsonify(ok=False, error="GTM account id is required."), 400

    try:
        token = _account_token(login)
        if kind == "GA4":
            # Google calls this delete, but Analytics moves the property to its
            # trash can rather than immediately erasing it.
            url = f"https://analyticsadmin.googleapis.com/v1beta/properties/{quote(resource, safe='')}"
        else:
            url = ("https://tagmanager.googleapis.com/tagmanager/v2/accounts/"
                   f"{quote(account_id, safe='')}/containers/{quote(resource, safe='')}")
        _delete(token, url)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 500
        detail = ""
        try:
            detail = (exc.response.json().get("error") or {}).get("message") or ""
        except Exception:
            detail = str(exc)
        if kind == "GA4" and status in (401, 403):
            detail = (detail + " The connected Google account may have the older read-only Analytics "
                      "grant. Reconnect it with Analytics edit permission before deleting.").strip()
        _audit("delete", kind=kind, login=login, resource=resource, name=name,
               result="error", detail=detail)
        return jsonify(ok=False, error=detail or f"Google returned HTTP {status}"), status
    except Exception as exc:
        _audit("delete", kind=kind, login=login, resource=resource, name=name,
               result="error", detail=str(exc))
        return jsonify(ok=False, error=str(exc)), 500

    skips = _read_skips()
    skips.pop(_skip_key(kind, login, resource), None)
    _write_skips(skips)
    _audit("delete", kind=kind, login=login, resource=resource, name=name,
           detail="Moved to Analytics trash" if kind == "GA4" else "Deleted GTM container")
    _clear_cache()
    return jsonify(ok=True,
                   message=("GA4 property moved to trash." if kind == "GA4"
                            else "GTM container deleted."))


@qa_bp.route("/api/audit")
@require_login
def api_audit():
    rows = jsonstore.read_json(_data_path("google_inactive_qa_audit.json"), default=[])
    if not isinstance(rows, list):
        rows = []
    return jsonify(rows=list(reversed(rows[-200:])))
