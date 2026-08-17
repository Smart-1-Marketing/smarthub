"""Insites Audit API client.

Wraps the documented Insites Prospect/Audit API
(https://help.insites.com/en/articles/7994946-audit-api). Auth is a single
header, ``api-key``. The API is a three-step flow:

    1. POST /api/v1/report            -> start an audit, returns a reportId
    2. (audit runs on Insites' side, ~1-4 min)
    3. GET  /api/v1/report/{id}       -> 202 while running, 200 when complete

We support BOTH completion strategies the API offers:

    * callback  - pass ``on_completion`` so Insites POSTs us the finished data.
                  This is what the Scans module uses in production: no polling
                  loop to hang, Insites just delivers when ready.
    * poll      - fetch_report() returns (status, data); callers can loop.
                  Used for manual/debug fetches and as a callback fallback.

Every network call is bounded by a timeout, so nothing here can hang
indefinitely. All methods raise InsitesError (never a bare requests error)
so callers have one exception type to handle.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import requests

API_BASE = os.environ.get("INSITES_API_BASE", "https://api.insites.com/api/v1")
DEFAULT_TIMEOUT = 30  # seconds for any single HTTP call


class InsitesError(Exception):
    """Any failure talking to the Insites API. Carries an HTTP status when known."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


def _api_key() -> str:
    key = (os.environ.get("INSITES_API_KEY") or "").strip()
    if not key:
        raise InsitesError(
            "INSITES_API_KEY is not set. Add your Insites API key to the "
            "environment before running scans."
        )
    return key


def _headers() -> dict:
    return {"api-key": _api_key(), "Content-Type": "application/json"}


def is_configured() -> bool:
    """True if an API key is present — lets the UI degrade gracefully."""
    return bool((os.environ.get("INSITES_API_KEY") or "").strip())


def start_audit(url: str, *, on_completion: Optional[str] = None,
                name: str = "", phone: str = "", address: str = "",
                city: str = "", state: str = "", zip_code: str = "",
                country_code: str = "", products: str = "", locations: str = "",
                dedupe: str = "match_hostname", extra: Optional[dict] = None) -> dict:
    """Start a new audit. Returns the parsed JSON (contains ``reportId``).

    Only ``url`` is strictly required. The optional business fields improve the
    accuracy of local-presence / reviews / local-grid checks, per the API docs.

    Raises InsitesError on any non-2xx (except 303, which means "recent results
    already exist" and is surfaced to the caller as a normal reuse case).
    """
    payload: dict[str, Any] = {"url": url, "dedupe_behaviour": dedupe}
    if on_completion:
        payload["on_completion"] = on_completion
    if name:
        payload["name"] = name
    if phone:
        payload["phone"] = phone
    if address:
        payload["address"] = address
    if city:
        payload["city"] = city
    if state:
        payload["state"] = state
    if zip_code:
        payload["zip"] = zip_code
    if country_code:
        payload["country_code"] = country_code
    if products:
        payload["products"] = products
    if locations:
        payload["locations"] = locations
    if extra:
        payload.update(extra)

    try:
        resp = requests.post(f"{API_BASE}/report", headers=_headers(),
                             json=payload, timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise InsitesError(f"Couldn't reach the Insites API: {exc}") from exc

    # 202 = started; 303 = recent results already exist (reuse, not an error)
    if resp.status_code in (200, 202, 303):
        # A 303 reuses an existing report and does NOT spend a credit, so it is
        # recorded as cached. Counting it as billable would overstate usage and
        # trip the monthly warning early.
        try:
            from hub import quotas as _q
            _q.record("insites", module="scans", detail=url,
                      cached=(resp.status_code == 303))
        except Exception:                             # noqa: BLE001
            pass
        try:
            return resp.json()
        except ValueError:
            raise InsitesError("Insites returned a non-JSON response when "
                               "starting the audit.", status=resp.status_code)
    if resp.status_code == 400:
        raise InsitesError("Insites rejected the request (400) — usually a URL "
                           "with a path, which the audit API can't accept. Send "
                           "just the domain.", status=400)
    if resp.status_code == 422:
        detail = _error_detail(resp)
        raise InsitesError(f"Insites couldn't process that website (422): "
                           f"{detail}", status=422)
    raise InsitesError(f"Insites returned HTTP {resp.status_code} when starting "
                       f"the audit.", status=resp.status_code)


def fetch_report(report_id: str, *, include_datasets: bool = True,
                 categories: bool = True) -> tuple[str, Optional[dict]]:
    """Fetch a report by ID.

    Returns a tuple ``(status, report_data_or_None)`` where status is:
        "complete" - HTTP 200, report_data is the full audit dict
        "running"  - HTTP 202, still processing (report_data may be an older run)
        "not_found"- HTTP 404
    Raises InsitesError only for unexpected/hard failures.
    """
    params = []
    if include_datasets:
        params.append("include_datasets=true")
    if categories:
        params.append("categories=true")
    qs = ("?" + "&".join(params)) if params else ""

    try:
        resp = requests.get(f"{API_BASE}/report/{report_id}{qs}",
                            headers=_headers(), timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise InsitesError(f"Couldn't reach the Insites API: {exc}") from exc

    if resp.status_code == 200:
        body = _json(resp)
        return "complete", body.get("report", body)
    if resp.status_code == 202:
        body = _json(resp, allow_empty=True)
        # 202 can still carry an older run's data; hand it back for reference
        return "running", (body.get("report") if isinstance(body, dict) else None)
    if resp.status_code == 404:
        return "not_found", None
    raise InsitesError(f"Insites returned HTTP {resp.status_code} fetching the "
                       f"report.", status=resp.status_code)


def fetch_llm_report(report_id: str) -> tuple[str, Optional[dict]]:
    """Fetch the LLM-optimised payload (for narrative generation)."""
    try:
        resp = requests.get(f"{API_BASE}/llm/report-fetch/{report_id}",
                            headers=_headers(), timeout=DEFAULT_TIMEOUT)
    except requests.RequestException as exc:
        raise InsitesError(f"Couldn't reach the Insites API: {exc}") from exc
    if resp.status_code == 200:
        body = _json(resp)
        return "complete", body.get("report", body)
    if resp.status_code == 202:
        return "running", None
    if resp.status_code == 404:
        return "not_found", None
    raise InsitesError(f"Insites returned HTTP {resp.status_code} fetching the "
                       f"LLM report.", status=resp.status_code)


def _json(resp, allow_empty: bool = False) -> dict:
    try:
        return resp.json()
    except ValueError:
        if allow_empty:
            return {}
        raise InsitesError("Insites returned a non-JSON response.",
                           status=resp.status_code)


def _error_detail(resp) -> str:
    try:
        body = resp.json()
        return str(body.get("error") or body.get("message") or body)[:200]
    except ValueError:
        return (resp.text or "")[:200]
