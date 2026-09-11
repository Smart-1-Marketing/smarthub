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
from typing import Any
from urllib.parse import quote

import requests
from flask import Blueprint, jsonify, render_template, request

from hub import jsonstore
from .app import require_login

qa_bp = Blueprint(
    "google_inactive_qa", __name__,
    url_prefix="/tools/google-access/qa-inactive",
    template_folder="templates",
)

WINDOW_DAYS = 60
CACHE_SECONDS = 600
_GA_RE = re.compile(r"\bG-[A-Z0-9]{4,}\b", re.I)
_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_LOCK = threading.Lock()


def _finder():
    # Imported lazily so this QA page cannot make Hub startup depend on the
    # standalone Google Finder Flask app.
    from modules.google_finder import app as gf
    return gf


def _path(name: str) -> str:
    return os.path.join(jsonstore.data_root(), name)


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


def _get(token: str, url: str, params=None) -> dict:
    r = requests.get(url, headers=_headers(token), params=params or {}, timeout=20)
    r.raise_for_status()
    return r.json() if r.content else {}


def _post(token: str, url: str, body: dict) -> dict:
    r = requests.post(url, headers=_headers(token), json=body, timeout=25)
    r.raise_for_status()
    return r.json() if r.content else {}


def _delete(token: str, url: str) -> None:
    r = requests.delete(url, headers=_headers(token), timeout=20)
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
    return list(_pages(token,
        "https://tagmanager.googleapis.com/tagmanager/v2/accounts", "account"))


def _gtm_containers(token: str, account_id: str) -> list[dict]:
    return list(_pages(token,
        f"https://tagmanager.googleapis.com/tagmanager/v2/accounts/{account_id}/containers",
        "container"))


def _live_tags(token: str, account_id: str, container_id: str) -> tuple[list[dict], str]:
    url = ("https://tagmanager.googleapis.com/tagmanager/v2/accounts/"
           f"{account_id}/containers/{container_id}/versions:live")
    try:
        version = _get(token, url)
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


def _scan_login(login: str, refresh: str) -> tuple[list[dict], list[dict], list[dict]]:
    gf = _finder()
    token = gf.refresh_access_token(login, refresh)
    inactive, review, active = [], [], []
    by_mid: dict[str, list[dict]] = {}

    for prop in _ga_properties(token):
        base = {"kind": "GA4", "login": login, "account": prop["account"],
                "account_id": prop["account_id"], "name": prop["name"],
                "resource": prop["property_id"], "public_id": ""}
        try:
            a = _ga_activity(token, prop["property_id"])
        except requests.HTTPError as exc:
            review.append({**base, "status": "review", "events": None, "sessions": None,
                           "reason": "Could not read GA4 activity — " + _http_reason(exc)})
            continue
        dead = a["events"] == 0 and a["sessions"] == 0
        row = {**base, **a, "status": "inactive" if dead else "active",
               "reason": (f"0 events and 0 sessions in the last {WINDOW_DAYS} days"
                          if dead else "Activity detected")}
        (inactive if dead else active).append(row)
        for mid in _ga_measurement_ids(token, prop["property_id"]):
            by_mid.setdefault(mid, []).append(row)

    try:
        accounts = _gtm_accounts(token)
    except requests.HTTPError as exc:
        review.append({"kind": "GTM", "login": login, "account": "Tag Manager",
                       "account_id": "", "name": "Could not list GTM accounts", "resource": "",
                       "public_id": "", "status": "review", "events": None, "sessions": None,
                       "reason": _http_reason(exc)})
        return inactive, review, active

    for acc in accounts:
        aid = str(acc.get("accountId") or acc.get("path") or "").split("/")[-1]
        aname = acc.get("name") or aid or "Tag Manager"
        if not aid:
            continue
        try:
            containers = _gtm_containers(token, aid)
        except requests.HTTPError as exc:
            review.append({"kind": "GTM", "login": login, "account": aname,
                           "account_id": aid, "name": "Could not list containers", "resource": "",
                           "public_id": "", "status": "review", "events": None, "sessions": None,
                           "reason": _http_reason(exc)})
            continue

        for c in containers:
            cid = str(c.get("containerId") or "")
            if not cid:
                continue
            base = {"kind": "GTM", "login": login, "account": aname,
                    "account_id": aid, "name": c.get("name") or cid, "resource": cid,
                    "public_id": c.get("publicId") or "", "events": None, "sessions": None}
            try:
                tags, why_no_live = _live_tags(token, aid, cid)
            except requests.HTTPError as exc:
                review.append({**base, "status": "review",
                               "reason": "Could not inspect live container — " + _http_reason(exc)})
                continue
            if why_no_live or not tags:
                inactive.append({**base, "status": "inactive",
                                 "reason": why_no_live or "Published container has no tags"})
                continue

            mids = set().union(*(_measurement_ids(tag) for tag in tags)) if tags else set()
            if not mids:
                review.append({**base, "status": "review",
                               "reason": "Live tags exist, but no GA4 measurement ID can be resolved"})
                continue
            linked = [r for mid in mids for r in by_mid.get(mid, [])]
            linked = list({r["resource"]: r for r in linked}.values())
            if not linked:
                review.append({**base, "status": "review",
                               "reason": "GA4 ID found, but its property is not visible to this login"})
            elif all(r["status"] == "inactive" for r in linked):
                inactive.append({**base, "status": "inactive",
                                 "events": sum(r.get("events") or 0 for r in linked),
                                 "sessions": sum(r.get("sessions") or 0 for r in linked),
                                 "reason": "Linked GA4 property/properties have no activity for 60 days"})
            elif any(r["status"] == "active" for r in linked):
                active.append({**base, "status": "active", "reason": "Linked GA4 activity detected"})
            else:
                review.append({**base, "status": "review",
                               "reason": "Linked GA4 activity could not be measured"})

    return inactive, review, active


def _dedupe(rows: list[dict]) -> list[dict]:
    out = {}
    for r in rows:
        key = (r.get("kind"), r.get("account_id") or r.get("account"), r.get("resource"), r.get("name"))
        out.setdefault(key, r)
    return list(out.values())


def scan(force=False) -> dict:
    now = time.time()
    with _LOCK:
        if (not force and _CACHE.get("payload") and
                now - float(_CACHE.get("at") or 0) < CACHE_SECONDS):
            return _CACHE["payload"]

    gf = _finder()
    accounts, source_error = gf.connected_accounts_result()
    inactive, review, active = [], [], []
    errors = []
    for account in accounts:
        login = account.get("email") or ""
        if str(account.get("status") or "ACTIVE") != "ACTIVE":
            review.append({"kind": "Google", "login": login, "account": "Connected login",
                           "account_id": "", "name": login, "resource": login, "public_id": "",
                           "status": "review", "events": None, "sessions": None,
                           "reason": "Google login requires reconnection"})
            continue
        try:
            dead, unsure, alive = _scan_login(login, account["refresh_token"])
            inactive += dead; review += unsure; active += alive
        except Exception as exc:
            errors.append(f"{login}: {type(exc).__name__}: {exc}")

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
        _CACHE.update(at=now, payload=payload)
    return payload


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
    force = str(request.args.get("force") or "").lower() in ("1", "true", "yes")
    return jsonify(scan(force=force))


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
