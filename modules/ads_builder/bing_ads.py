"""Microsoft Advertising (Bing Ads): the connection, the token and every call.

The sibling of ``google_ads.py``, for the platform the module's own docstring
has called *phase two* since it was written. What is built here is the half
that reports: authorizing a Microsoft account once, keeping its refresh
token the way Google's is kept, and reading the manager's client accounts
and their campaign figures through the Reporting API. Campaign management
(create, pause, budgets) is deliberately NOT here -- ``/api/bing/*`` still
answers 501 for that -- because nothing sells it yet and a write path with
no caller is the declared-and-unwired failure CLAUDE.md counts a dozen of.

## The four things every call carries

Microsoft Advertising authenticates a request with four values, and a
missing or wrong one of any of them answers the same bare
``InvalidCredentials`` -- which is why each is named here rather than
discovered from the refusal:

* **an access token** -- OAuth 2.0 on the Microsoft identity platform,
  ``login.microsoftonline.com/common/oauth2/v2.0``. ``/common/`` because a
  Microsoft Advertising login is as often a personal Microsoft account as
  a work one, so the app registration has to allow both. Scope
  ``https://ads.microsoft.com/msads.manage`` plus ``offline_access`` for
  the refresh token. Refreshed per call from the refresh token, never
  cached across a deploy;
* **the developer token** -- ``BING_AD_DEVELOPER_TOKEN``, one per app;
* **the customer id** -- ``BING_MANAGER_ACCOUNT_ID``, the manager
  (agency) customer id, sent as the ``CustomerId`` header. Refused by name
  when it is not numeric, because the id and the account NUMBER are easy to
  paste the wrong way round and the API's own answer to a wrong customer id
  is the same bare authorization failure as a wrong token;
* **the account id** -- ``CustomerAccountId``, per client account, which
  the reporting request scopes on instead.

``BING_MANAGER_ACCOUNT_NUMBER`` is the number printed beside the id in the
Microsoft Advertising UI. It is kept for the settings row and sent nowhere.

The spellings are exactly the ones set on Render. ``hub/config.py``'s
ALIASES rule is only spellings in use, and inventing a ``BING_ADS_`` twin is
how thirteen correct modules once became findings. Every one is read through
``hub/config.py`` at call time (``_env()``), with a bare ``os.environ`` read
only where the Hub is not importable -- the module runs standalone too.

## Where the token lives

``refresh_token_value()`` is ``google_ads.refresh_token_value()``'s rule:
the environment (``BING_AD_REFRESH_TOKEN``) wins because it is the copy that
survives a redeploy, and the settings table (``bing_refresh_token``, written
by the callback) is the copy that works the moment somebody connects.

## Production or sandbox

Microsoft issues a *Universal* developer token for production and a
different one for the sandbox, and each answers only its own host --
Google's Explorer-tier lesson one platform over, and Microsoft publishes
the tier nowhere an API can read either. ``BING_AD_ENVIRONMENT`` picks the
host pair (``production`` unless it says ``sandbox``), and a refusal names
the environment it was refused on rather than reading as a bad key.

## What never leaves

The client secret, the developer token, the refresh token and the access
token: ``_redact()`` strips every one of them from any provider message
before it reaches a result, an error, a watermark or a log line, and
``test_reports_bing.py`` reads every string a pull produces to prove it.

## Transcribed, not exercised

No live Microsoft account has answered this code yet. The REST shapes below
are transcribed from the Bing Ads API v13 reference -- the Reporting
service's ``GenerateReport/Submit`` and ``GenerateReport/Poll`` with a
``ReportRequest`` carrying a ``Type`` discriminator, and Customer
Management's ``Accounts/Search`` -- and each is read defensively: a
response missing a field it should carry is a refusal in words, never a
KeyError. The first live pull is where the transcription is checked, and
the test file says which assertions are about the wire.
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
import zipfile
from threading import Lock
from urllib.parse import urlencode, urlsplit

import requests

MOUNT = "/tools/ads"
CALLBACK_PATH = MOUNT + "/oauth/bing/callback"
TIMEOUT = 60

AUTH_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
SCOPE = "https://ads.microsoft.com/msads.manage offline_access"

# The two REST hosts the pull reaches, per environment. Both go into
# hub/quotas._PROVIDER_MARKERS, or the usage page could not name a call.
HOSTS = {
    "production": {
        "reporting": "https://reporting.api.bingads.microsoft.com/Reporting/v13",
        "customer": "https://clientcenter.api.bingads.microsoft.com/CustomerManagement/v13",
    },
    "sandbox": {
        "reporting": "https://reporting.api.sandbox.bingads.microsoft.com/Reporting/v13",
        "customer": "https://clientcenter.api.sandbox.bingads.microsoft.com/CustomerManagement/v13",
    },
}

# The variables, in the order the settings page names them. Exactly as set
# on Render -- see the module docstring.
REQUIRED = ("BING_AD_CLIENT_ID", "BING_AD_CLIENT_SECRET", "BING_AD_DEVELOPER_TOKEN",
            "BING_MANAGER_ACCOUNT_ID")

BLOCKS = {
    "BING_AD_CLIENT_ID": "the app registration on the Microsoft identity platform (Azure portal → App registrations)",
    "BING_AD_CLIENT_SECRET": "the same registration's client secret (Certificates & secrets)",
    "BING_AD_DEVELOPER_TOKEN": "the developer token from the Microsoft Advertising manager account (Tools → Developer token)",
    "BING_MANAGER_ACCOUNT_ID": "the manager account's customer id, digits only -- the id, not the account number beside it",
    "PUBLIC_BASE_URL": "the Hub's own origin, which the callback is built from",
}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _env(name: str) -> str:
    """One setting, read at call time through hub/config.py -- the copy
    somebody corrects mid-incident -- and straight off the environment
    only where the Hub is not importable (the module runs standalone)."""
    try:
        from hub import config as _config
        return _config._s(name)
    except Exception:                                   # noqa: BLE001
        return (os.environ.get(name) or "").strip()


def _origin(value: str) -> str:
    value = (value or "").strip().strip('"').strip("'").rstrip("/")
    if not value:
        return ""
    parts = urlsplit(value if "://" in value else "https://" + value)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else value


def redirect_uri() -> str:
    """The callback, built from PUBLIC_BASE_URL's origin at call time.

    The one reading, which is what lets hub/oauth_redirects.py print the
    string somebody pastes into the Azure portal and this send the same
    string: ``config.public_base_origin()`` trims a path off the variable,
    so a PUBLIC_BASE_URL that has ever carried a callback of its own does
    not put it in the middle of this one. Empty when the variable is unset,
    which ``connection_status()`` reports by name rather than sending a
    consent request nowhere.
    """
    try:
        from hub import config as _config
        origin = _config.public_base_origin()
    except Exception:                                   # noqa: BLE001
        origin = _origin(os.environ.get("PUBLIC_BASE_URL", ""))
    return (origin + CALLBACK_PATH) if origin else ""


def digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def environment() -> str:
    env = _env("BING_AD_ENVIRONMENT").lower()
    return "sandbox" if env == "sandbox" else "production"


def hosts() -> dict:
    return HOSTS[environment()]


def cfg() -> dict:
    manager = _env("BING_MANAGER_ACCOUNT_ID")
    return {
        "client_id": _env("BING_AD_CLIENT_ID"),
        "client_secret": _env("BING_AD_CLIENT_SECRET"),
        "developer_token": _env("BING_AD_DEVELOPER_TOKEN"),
        "manager_id": manager,
        "manager_id_problem": manager_id_problem(manager),
        "manager_number": _env("BING_MANAGER_ACCOUNT_NUMBER"),
        "environment": environment(),
        "redirect_uri": redirect_uri(),
    }


def manager_id_problem(value: str) -> str:
    """Why BING_MANAGER_ACCOUNT_ID cannot be sent, or "".

    A customer id is a run of digits. The account NUMBER printed beside it
    in the UI is letters and digits (``X0123456``), and the two are pasted
    the wrong way round often enough that refusing here by name beats the
    API's answer, which is the same InvalidCredentials a bad token gets.
    """
    v = (value or "").strip()
    if not v:
        return ""
    if not v.isdigit():
        return (f"is {v[:24]!r}, which is not a customer id: a customer id is digits only. "
                "The letters-and-digits value beside it in the Microsoft Advertising UI is the "
                "account NUMBER, which goes in BING_MANAGER_ACCOUNT_NUMBER.")
    return ""


# ---------------------------------------------------------------------------
# Errors, and what never leaves
# ---------------------------------------------------------------------------

class BingAdsError(Exception):
    def __init__(self, message: str, status: int = 500, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class NotConfigured(BingAdsError):
    pass


class NotConnected(BingAdsError):
    pass


_access_token = {"value": "", "expires_at": 0.0}
_access_token_lock = Lock()


def _redact(text) -> str:
    """Never let a credential out, however a provider message quotes it."""
    s = str(text or "")
    c = cfg()
    secrets = [c["client_secret"], c["developer_token"], _access_token["value"],
               (os.environ.get("BING_AD_REFRESH_TOKEN") or "").strip()]
    for value in secrets:
        if value and len(value) >= 8 and value in s:
            s = s.replace(value, "[redacted]")
    return s[:500]


def _error_text(resp) -> str:
    """The provider's own sentence out of whichever shape it answered in.

    The REST API wraps a fault three ways -- ``OperationErrors``,
    ``Errors``, or an ``AdApiFaultDetail`` / ``ApiFaultDetail`` object
    holding one of those -- and the identity platform answers
    ``error_description``. Every list of dicts carrying a ``Message`` is
    read, so a fourth wrapping still yields the message rather than
    ``HTTP 400``.
    """
    try:
        payload = resp.json()
    except Exception:                                   # noqa: BLE001
        return (resp.text or "")[:300] or f"HTTP {resp.status_code}"
    if not isinstance(payload, dict):
        return f"HTTP {resp.status_code}"
    if payload.get("error_description") or payload.get("error"):
        return str(payload.get("error_description") or payload.get("error"))[:300]
    found: list[str] = []

    def walk(node, depth=0):
        if depth > 4 or len(found) >= 3:
            return
        if isinstance(node, dict):
            msg = node.get("Message") or node.get("message")
            code = node.get("ErrorCode") or node.get("Code")
            if msg:
                found.append(f"{code}: {msg}" if code else str(msg))
                return
            for v in node.values():
                walk(v, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, depth + 1)
    walk(payload)
    return "; ".join(str(f)[:200] for f in found) or f"HTTP {resp.status_code}"


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def assert_configured() -> None:
    c = cfg()
    missing = [n for n in REQUIRED if not _env(n)]
    if missing:
        raise NotConfigured("Missing environment variables: " + ", ".join(missing),
                            status=400, code="NOT_CONFIGURED")
    if c["manager_id_problem"]:
        raise NotConfigured("BING_MANAGER_ACCOUNT_ID " + c["manager_id_problem"],
                            status=400, code="NOT_CONFIGURED")
    if not c["redirect_uri"]:
        raise NotConfigured("PUBLIC_BASE_URL is not set, so the Microsoft callback has no "
                            "hostname to come back to.", status=400, code="NOT_CONFIGURED")


def build_auth_url(state: str = "") -> str:
    c = cfg()
    return AUTH_URL + "?" + urlencode({
        "client_id": c["client_id"],
        "response_type": "code",
        "redirect_uri": c["redirect_uri"],
        "response_mode": "query",
        "scope": SCOPE,
        "state": state,
        # Ask which account, every time: a rep with a personal and a work
        # Microsoft login must be able to pick the one that can see the
        # manager account, and a silent re-use of whichever is signed in
        # is how the wrong account gets connected with no error.
        "prompt": "select_account",
    })


def _token_post(data: dict) -> dict:
    try:
        resp = requests.post(TOKEN_URL, data=data, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise BingAdsError(f"Microsoft's token endpoint could not be reached: {type(exc).__name__}",
                           status=502, code="OAUTH_UNREACHABLE")
    if not resp.ok:
        raise BingAdsError("Microsoft refused the token request: " + _redact(_error_text(resp)),
                           status=resp.status_code, code="OAUTH_REFUSED")
    try:
        payload = resp.json()
    except ValueError:
        raise BingAdsError("Microsoft's token endpoint answered with something that is not JSON",
                           status=502, code="OAUTH_BAD_ANSWER")
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise BingAdsError("Microsoft's token answer carried no access token",
                           status=502, code="OAUTH_BAD_ANSWER")
    return payload


def exchange_code(code: str) -> dict:
    c = cfg()
    data = _token_post({
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "code": code,
        "redirect_uri": c["redirect_uri"],
        "grant_type": "authorization_code",
        "scope": SCOPE,
    })
    with _access_token_lock:
        _access_token["value"] = data.get("access_token", "")
        _access_token["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
    return data


def refresh_token_value(store=None) -> str:
    """Environment wins -- it is the copy that survives a redeploy."""
    env = (os.environ.get("BING_AD_REFRESH_TOKEN") or "").strip()
    if env:
        return env
    if store is not None:
        return (store.get_setting("bing_refresh_token") or "").strip()
    return ""


def access_token(store=None) -> str:
    with _access_token_lock:
        if _access_token["value"] and time.time() < _access_token["expires_at"]:
            return _access_token["value"]
        refresh = refresh_token_value(store)
        if not refresh:
            raise NotConnected(
                "Microsoft Advertising is not connected. Open the Smart 1 Ads settings and "
                "click Connect Microsoft Ads, or set BING_AD_REFRESH_TOKEN.",
                status=400, code="NOT_CONNECTED")
        c = cfg()
        try:
            data = _token_post({
                "client_id": c["client_id"],
                "client_secret": c["client_secret"],
                "refresh_token": refresh,
                "grant_type": "refresh_token",
                "scope": SCOPE,
            })
        except BingAdsError as exc:
            raise NotConnected("Could not refresh the Microsoft token: " + exc.message,
                               status=401, code="REFRESH_FAILED")
        _access_token["value"] = data["access_token"]
        _access_token["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
        return _access_token["value"]


def forget_tokens() -> None:
    with _access_token_lock:
        _access_token["value"] = ""
        _access_token["expires_at"] = 0.0


def connection_status(store=None) -> dict:
    """What the Microsoft Advertising connection can do right now.

    The shape ``google_ads.connection_status()`` answers in, so the
    settings page and ``/api/status`` draw the two side by side:
    ``configured`` is the credentials, ``connected`` is a refresh token
    from somewhere, ``deploy_ready`` is both -- and here it means *the
    reports module can pull*, since nothing else is built on it.
    """
    c = cfg()
    from_env = bool((os.environ.get("BING_AD_REFRESH_TOKEN") or "").strip())
    from_db = bool(store and (store.get_setting("bing_refresh_token") or "").strip())
    required = [(n, _env(n)) for n in REQUIRED] + [("PUBLIC_BASE_URL", c["redirect_uri"])]
    missing = [n for n, v in required if not v]
    usable = not missing and not c["manager_id_problem"]
    blocks = [{"name": n, "why": BLOCKS[n]} for n, v in required if not v]
    if c["manager_id_problem"]:
        blocks.append({"name": "BING_MANAGER_ACCOUNT_ID",
                       "why": "it is set, but " + c["manager_id_problem"]})
    return {
        "configured": usable,
        "connected": from_env or from_db,
        "deploy_ready": usable and (from_env or from_db),
        "developer_token": bool(c["developer_token"]),
        "refresh_token_source": (
            "environment" if from_env
            else "hub database (set BING_AD_REFRESH_TOKEN to pin it)" if from_db
            else "none"),
        "environment": c["environment"],
        "manager_id": c["manager_id"] if not c["manager_id_problem"] else "",
        "manager_id_problem": c["manager_id_problem"],
        "manager_number": c["manager_number"],
        "redirect_uri": c["redirect_uri"],
        "missing": missing,
        "blocks": blocks,
    }


# ---------------------------------------------------------------------------
# The API: one seam, four headers, every call recorded
# ---------------------------------------------------------------------------

def _http(method: str, url: str, *, headers: dict, json=None, timeout: int = TIMEOUT):
    """The one call that reaches the network. Replaced whole by the test."""
    return requests.request(method, url, headers=headers, json=json, timeout=timeout)


def _record(url: str, ok: bool, api: str, module: str = "ads_builder") -> None:
    """One recorded call. Microsoft bills nothing for the API and limits by
    request, so this is counted in calls and the row on the usage page
    reads *not measured* against a ceiling until somebody sets one."""
    try:
        from hub import quotas
        quotas.record("microsoft_ads", module=module, units=1, api=api,
                      detail=str(url or "")[:120], ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


def api_headers(store=None, account_id: str = "") -> dict:
    c = cfg()
    h = {
        "Authorization": "Bearer " + access_token(store),
        "DeveloperToken": c["developer_token"],
        "CustomerId": c["manager_id"],
        "Content-Type": "application/json",
    }
    if account_id:
        h["CustomerAccountId"] = str(account_id)
    return h


def call(store, service: str, path: str, body: dict, *, account_id: str = "",
         module: str = "ads_builder") -> dict:
    """POST ``body`` to ``<host>/<path>`` on the reporting or customer
    service and answer the JSON, or raise a BingAdsError carrying the
    provider's own sentence -- redacted, and naming the environment it was
    refused on, because a sandbox token against the production host and a
    revoked token look identical from here."""
    assert_configured()
    base = hosts()[service]
    url = base.rstrip("/") + "/" + path.lstrip("/")
    try:
        resp = _http("POST", url, headers=api_headers(store, account_id), json=body)
    except requests.RequestException as exc:
        _record(url, False, service, module)
        raise BingAdsError(f"Microsoft Advertising could not be reached: {type(exc).__name__}",
                           status=502, code="UNREACHABLE")
    ok = 200 <= resp.status_code < 300
    _record(url, ok, service, module)
    if not ok:
        msg = _redact(_error_text(resp))
        if resp.status_code in (401, 403) or "InvalidCredentials" in msg or "105" in msg.split(":")[0]:
            raise BingAdsError(
                f"Microsoft Advertising refused the credentials on the {environment()} host "
                f"({msg}). A token or developer token issued for the other environment answers "
                "exactly this; so does a revoked one.", status=resp.status_code, code="REFUSED")
        raise BingAdsError(f"HTTP {resp.status_code} from Microsoft Advertising: {msg}",
                           status=resp.status_code, code="HTTP")
    try:
        payload = resp.json() if resp.content else {}
    except ValueError:
        raise BingAdsError("Microsoft Advertising answered with something that is not JSON",
                           status=502, code="BAD_ANSWER")
    if not isinstance(payload, dict):
        raise BingAdsError("Microsoft Advertising answered in a shape this module does not read",
                           status=502, code="BAD_ANSWER")
    return payload


def list_accounts(store=None, *, module: str = "ads_builder") -> list[dict]:
    """Every advertiser account under the manager, from Customer
    Management's ``Accounts/Search`` -- read on each pull, so an account
    added to the manager next month is swept without anybody typing its
    id. ASSUMED, until a live account answers: that a ``CustomerId``
    predicate on the manager's id lists the accounts it manages, and that
    each carries ``Id``, ``Name``, ``Number``, ``AccountLifeCycleStatus``
    and ``CurrencyCode``."""
    c = cfg()
    payload = call(store, "customer", "Accounts/Search", {
        "Predicates": [{"Field": "CustomerId", "Operator": "Equals", "Value": c["manager_id"]}],
        "Ordering": None,
        "PageInfo": {"Index": 0, "Size": 1000},
    }, module=module)
    out = []
    for acct in payload.get("Accounts") or []:
        if not isinstance(acct, dict) or acct.get("Id") in (None, ""):
            continue
        out.append({
            "id": str(acct.get("Id")),
            "name": str(acct.get("Name") or "").strip(),
            "number": str(acct.get("Number") or "").strip(),
            "status": str(acct.get("AccountLifeCycleStatus") or "").strip(),
            "currency": str(acct.get("CurrencyCode") or "").strip(),
        })
    return out


# ---------------------------------------------------------------------------
# Reporting v13: submit, poll, download
# ---------------------------------------------------------------------------

CAMPAIGN_COLUMNS = ("TimePeriod", "AccountId", "AccountName", "AccountNumber",
                    "CampaignId", "CampaignName", "CampaignType", "CampaignStatus",
                    "CurrencyCode", "Spend", "Impressions", "Clicks", "Conversions")
ACCOUNT_COLUMNS = ("AccountId", "AccountName", "CurrencyCode", "Spend", "Impressions", "Clicks")


def _date_parts(d) -> dict:
    return {"Day": d.day, "Month": d.month, "Year": d.year}


def report_request(kind: str, start, end, account_ids: list[str], *,
                   aggregation: str, columns: tuple[str, ...], name: str) -> dict:
    """The ``ReportRequest`` body, one place. ``Type`` is the REST
    discriminator for the request class; the report header and footer are
    excluded so the CSV starts at its column row; ``ReturnOnlyCompleteData``
    is off because a partial today is still today's spend and the next
    pull restates it."""
    return {"ReportRequest": {
        "Type": kind,
        "ExcludeColumnHeaders": False,
        "ExcludeReportFooter": True,
        "ExcludeReportHeader": True,
        "Format": "Csv",
        "FormatVersion": "2.0",
        "ReportName": name,
        "ReturnOnlyCompleteData": False,
        "Aggregation": aggregation,
        "Columns": list(columns),
        "Scope": {"AccountIds": [int(a) if str(a).isdigit() else a for a in account_ids]},
        "Time": {"CustomDateRangeStart": _date_parts(start),
                 "CustomDateRangeEnd": _date_parts(end)},
    }}


def submit_report(store, body: dict, *, module: str = "reports") -> str:
    payload = call(store, "reporting", "GenerateReport/Submit", body, module=module)
    rid = str(payload.get("ReportRequestId") or "").strip()
    if not rid:
        raise BingAdsError("the report was submitted and Microsoft answered with no "
                           "ReportRequestId", status=502, code="BAD_ANSWER")
    return rid


def poll_report(store, request_id: str, *, module: str = "reports") -> dict:
    """``{"status": "Success"|"Pending"|"Error", "url": ...}`` off
    ``GenerateReport/Poll``. A status this module does not know reads as
    Pending -- treating it as finished attaches nothing while reporting
    success, the HeyGen lesson."""
    payload = call(store, "reporting", "GenerateReport/Poll",
                   {"ReportRequestId": request_id}, module=module)
    st = payload.get("ReportRequestStatus") or {}
    status = str(st.get("Status") or "").strip() if isinstance(st, dict) else ""
    url = str(st.get("ReportDownloadUrl") or "").strip() if isinstance(st, dict) else ""
    if status not in ("Success", "Error"):
        status = "Pending"
    return {"status": status, "url": url}


def download_report(url: str, *, module: str = "reports") -> str:
    """The finished report: a ZIP holding one CSV, or the CSV itself.
    Fetched with no credential -- the download URL is pre-signed and
    short-lived -- and read into text."""
    try:
        resp = requests.get(url, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise BingAdsError(f"the report could not be downloaded: {type(exc).__name__}",
                           status=502, code="UNREACHABLE")
    _record(url.split("?")[0], resp.ok, "download", module)
    if not resp.ok:
        raise BingAdsError(f"HTTP {resp.status_code} downloading the report", status=resp.status_code)
    return report_text(resp.content)


def report_text(data: bytes) -> str:
    """CSV text out of the download: a ZIP's first .csv member, or the
    bytes as they are."""
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")] or zf.namelist()
            if not names:
                raise BingAdsError("the report ZIP holds no file", status=502, code="BAD_ANSWER")
            data = zf.read(names[0])
    return data.decode("utf-8-sig", errors="replace")


def rows_of(text: str) -> list[list[str]]:
    """The CSV as rows, the report's own header lines and footer dropped:
    everything before the column row (the one naming a *Spend* or an *Id*
    column) and any row that opens with the copyright mark."""
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    rows = []
    for row in reader:
        if not row or not any(str(c).strip() for c in row):
            continue
        if str(row[0]).strip().startswith(("©", "(c)", "Copyright")):
            continue
        rows.append(row)
    return rows
