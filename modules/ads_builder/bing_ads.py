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
  the app registration itself is ``BING_AD_CLIENT_ID`` /
  ``BING_AD_CLIENT_SECRET`` or, as Render also spells the pair,
  ``MICROSOFT_ADS_CLIENT_ID`` / ``MICROSOFT_ADS_CLIENT_SECRET`` -- both
  are in use, so both are in ``hub/config.py``'s ALIASES, and the first
  that is set answers;
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

Two more settings shape the consent itself. ``MICROSOFT_ADS_TENANT`` is the
identity platform's path segment -- ``common`` when unset, and a tenant id
or ``organizations`` for a registration that allows only work accounts,
because ``/common/`` against a single-tenant registration is refused
(AADSTS50194) before any consent screen. ``MICROSOFT_ADS_REDIRECT_URI``
pins the callback to the exact string pasted into the Azure portal, the
``AMAZON_ADS_REDIRECT_URI`` arrangement; unset, it is ``PUBLIC_BASE_URL``'s
origin plus this mount's callback path. A pinned URI whose path is not
this mount's callback is refused by name in ``connection_status()``,
because Microsoft would send the code to a page this Hub does not answer.

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

## Every account the login can see, under the customer that owns it

``discover_accounts()`` reads the connected user (``User/Query``) and then
``Accounts/Search`` by that user's id -- every advertiser account the
consent reaches, across every customer the agency manages, not only the
accounts the manager customer owns outright. When Microsoft refuses the
``UserId`` predicate or answers nothing, the search is asked again by the
manager's ``CustomerId``, and the answer says which strategy produced it.
Pages are followed. Each account carries ``parent_customer_id``, because a
report is scoped to accounts and authorized by the ``CustomerId`` header,
and the header has to be the customer that owns the accounts in the scope:
``modules/reports/bing.py`` submits one report per owning customer.

## What a transient refusal costs

One retry, two seconds later, on a connection error, a 429 or a 5xx --
never on a 4xx that names the request, which a retry would only repeat.
A download URL that answers HTML or XML (expired, or a sign-in page) is a
refusal in words rather than a parse of nothing.

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
# One retry on a transient answer, this long after the first. A module
# attribute so the test drives it without waiting.
RETRY_WAIT = 2.0
RETRY_STATUSES = (429, 500, 502, 503, 504)
# Customer Management pages accounts; this is the largest page it allows.
PAGE_SIZE = 1000
# Accounts a report cannot be run on. A Draft account has never served.
ACCOUNT_STATUSES_SKIPPED = ("Draft",)

# The /common/ endpoints: what is sent unless MICROSOFT_ADS_TENANT narrows
# the tenant (auth_url() / token_url() below).
IDENTITY_HOST = "https://login.microsoftonline.com"
AUTH_URL = IDENTITY_HOST + "/common/oauth2/v2.0/authorize"
TOKEN_URL = IDENTITY_HOST + "/common/oauth2/v2.0/token"
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

# The names that answer to more than one spelling, keyed by the name this
# module asks for, valued by the hub/config.py ALIASES row that lists every
# spelling. The tuple beside it is the standalone fallback: the same
# spellings, for a run where the Hub is not importable.
ALIAS_KEYS = {
    "BING_AD_CLIENT_ID": ("bing_client_id", ("BING_AD_CLIENT_ID", "MICROSOFT_ADS_CLIENT_ID")),
    "BING_AD_CLIENT_SECRET": ("bing_client_secret", ("BING_AD_CLIENT_SECRET", "MICROSOFT_ADS_CLIENT_SECRET")),
}
TENANT_VAR = "MICROSOFT_ADS_TENANT"
REDIRECT_PIN_VAR = "MICROSOFT_ADS_REDIRECT_URI"

BLOCKS = {
    "BING_AD_CLIENT_ID": "the app registration on the Microsoft identity platform (Azure portal → "
                         "App registrations); MICROSOFT_ADS_CLIENT_ID is read for it too",
    "BING_AD_CLIENT_SECRET": "the same registration's client secret (Certificates & secrets); "
                             "MICROSOFT_ADS_CLIENT_SECRET is read for it too",
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
    only where the Hub is not importable (the module runs standalone).
    A name in ALIAS_KEYS answers under whichever of its spellings is set,
    in the order hub/config.py's ALIASES prefers them."""
    key, spellings = ALIAS_KEYS.get(name, ("", (name,)))
    try:
        from hub import config as _config
        return _config._alias(key) if key else _config._s(name)
    except Exception:                                   # noqa: BLE001
        for n in spellings:
            v = (os.environ.get(n) or "").strip()
            if v:
                return v
        return ""


def tenant() -> str:
    """The identity platform's path segment: ``common`` unless
    MICROSOFT_ADS_TENANT names a tenant id, a verified domain,
    ``organizations`` or ``consumers``. Anything that is not one of those
    shapes (a pasted URL, a space) falls back to ``common`` rather than
    being sent inside a hostname path."""
    v = _env(TENANT_VAR).strip().strip("/")
    if v and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,127}", v):
        return v
    return "common"


def auth_url() -> str:
    return f"{IDENTITY_HOST}/{tenant()}/oauth2/v2.0/authorize"


def token_url() -> str:
    return f"{IDENTITY_HOST}/{tenant()}/oauth2/v2.0/token"


def _origin(value: str) -> str:
    value = (value or "").strip().strip('"').strip("'").rstrip("/")
    if not value:
        return ""
    parts = urlsplit(value if "://" in value else "https://" + value)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else value


def pinned_redirect_uri() -> str:
    """MICROSOFT_ADS_REDIRECT_URI as set, quotes Render stores literally
    stripped, or "" -- the string somebody pasted into the Azure portal."""
    return _env(REDIRECT_PIN_VAR).strip().strip('"').strip("'")


def redirect_uri_problem() -> str:
    """Why the pinned redirect URI cannot work, or "".

    Microsoft sends the authorization code to the registered URI exactly,
    and this Hub answers the Microsoft callback at one path. A pin whose
    path is some other callback -- Google's, or one from a previous
    integration -- lands the code on a page that 404s or, worse, on a
    different provider's handler, with nothing saying why.
    """
    pinned = pinned_redirect_uri()
    if not pinned:
        return ""
    parts = urlsplit(pinned if "://" in pinned else "https://" + pinned)
    if not parts.netloc:
        return f"is {pinned[:80]!r}, which is not a URL."
    if parts.path.rstrip("/") != CALLBACK_PATH:
        return (f"ends in {parts.path or '/'}, but this Hub answers the Microsoft callback "
                f"only at {CALLBACK_PATH}. Register {parts.scheme}://{parts.netloc}{CALLBACK_PATH} "
                "in the Azure portal and set the variable to it, or clear the variable to build "
                "the callback from PUBLIC_BASE_URL.")
    return ""


def redirect_uri() -> str:
    """The callback: MICROSOFT_ADS_REDIRECT_URI when it is set and usable,
    else PUBLIC_BASE_URL's origin plus this mount's path, at call time.

    The one reading, which is what lets hub/oauth_redirects.py print the
    string somebody pastes into the Azure portal and this send the same
    string: ``config.public_base_origin()`` trims a path off the variable,
    so a PUBLIC_BASE_URL that has ever carried a callback of its own does
    not put it in the middle of this one. Empty when the variable is unset,
    which ``connection_status()`` reports by name rather than sending a
    consent request nowhere. A pin at the wrong path is not sent either:
    ``redirect_uri_problem()`` names it and the flow does not start.
    """
    pinned = pinned_redirect_uri()
    if pinned and not redirect_uri_problem():
        return pinned
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
        "tenant": tenant(),
        "redirect_uri": redirect_uri(),
        "redirect_uri_problem": redirect_uri_problem(),
        "redirect_uri_source": REDIRECT_PIN_VAR if pinned_redirect_uri() else "PUBLIC_BASE_URL",
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
    if c["redirect_uri_problem"]:
        raise NotConfigured(REDIRECT_PIN_VAR + " " + c["redirect_uri_problem"],
                            status=400, code="NOT_CONFIGURED")
    if not c["redirect_uri"]:
        raise NotConfigured("PUBLIC_BASE_URL is not set, so the Microsoft callback has no "
                            "hostname to come back to.", status=400, code="NOT_CONFIGURED")


def build_auth_url(state: str = "") -> str:
    c = cfg()
    return auth_url() + "?" + urlencode({
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
        resp = requests.post(token_url(), data=data, timeout=TIMEOUT)
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
    required = [(n, _env(n)) for n in REQUIRED]
    if not c["redirect_uri_problem"]:
        # A pin at the wrong path is its own finding below, not a missing
        # PUBLIC_BASE_URL -- that variable may be set and correct.
        required.append(("PUBLIC_BASE_URL", c["redirect_uri"]))
    missing = [n for n, v in required if not v]
    usable = not missing and not c["manager_id_problem"] and not c["redirect_uri_problem"]
    blocks = [{"name": n, "why": BLOCKS[n]} for n, v in required if not v]
    if c["manager_id_problem"]:
        blocks.append({"name": "BING_MANAGER_ACCOUNT_ID",
                       "why": "it is set, but " + c["manager_id_problem"]})
    if c["redirect_uri_problem"]:
        blocks.append({"name": REDIRECT_PIN_VAR, "why": "it is set, but " + c["redirect_uri_problem"]})
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
        "tenant": c["tenant"],
        "manager_id": c["manager_id"] if not c["manager_id_problem"] else "",
        "manager_id_problem": c["manager_id_problem"],
        "manager_number": c["manager_number"],
        "redirect_uri": c["redirect_uri"],
        "redirect_uri_problem": c["redirect_uri_problem"],
        "redirect_uri_source": c["redirect_uri_source"],
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


def api_headers(store=None, account_id: str = "", customer_id: str = "") -> dict:
    """The four headers. ``customer_id`` overrides the manager's id for a
    call that has to be authorized by the customer that owns the accounts
    it names -- a report over an account the agency manages but does not
    own."""
    c = cfg()
    h = {
        "Authorization": "Bearer " + access_token(store),
        "DeveloperToken": c["developer_token"],
        "CustomerId": digits(customer_id) or c["manager_id"],
        "Content-Type": "application/json",
    }
    if account_id:
        h["CustomerAccountId"] = str(account_id)
    return h


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def call(store, service: str, path: str, body: dict, *, account_id: str = "",
         customer_id: str = "", module: str = "ads_builder") -> dict:
    """POST ``body`` to ``<host>/<path>`` on the reporting or customer
    service and answer the JSON, or raise a BingAdsError carrying the
    provider's own sentence -- redacted, and naming the environment it was
    refused on, because a sandbox token against the production host and a
    revoked token look identical from here. A connection error, a 429 or a
    5xx is asked once more after RETRY_WAIT; a 4xx that names the request
    is not, because a retry would only repeat it."""
    assert_configured()
    base = hosts()[service]
    url = base.rstrip("/") + "/" + path.lstrip("/")
    headers = api_headers(store, account_id, customer_id)
    resp = None
    for attempt in (1, 2):
        try:
            resp = _http("POST", url, headers=headers, json=body)
        except requests.RequestException as exc:
            _record(url, False, service, module)
            if attempt == 1:
                _sleep(RETRY_WAIT)
                continue
            raise BingAdsError(f"Microsoft Advertising could not be reached twice: {type(exc).__name__}",
                               status=502, code="UNREACHABLE")
        if resp.status_code in RETRY_STATUSES and attempt == 1:
            _record(url, False, service, module)
            _sleep(RETRY_WAIT)
            continue
        break
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


def connected_user(store=None, *, module: str = "ads_builder") -> dict:
    """The user the consent belongs to: ``{"id", "name", "customers"}``
    off Customer Management's ``User/Query`` asked with no id, which
    answers the authenticated user. ``customers`` is every customer id the
    user holds a role on, read defensively: a role missing its id is
    skipped, never a KeyError."""
    payload = call(store, "customer", "User/Query", {"UserId": None}, module=module)
    user = payload.get("User") if isinstance(payload.get("User"), dict) else {}
    customers = []
    for role in payload.get("CustomerRoles") or []:
        if isinstance(role, dict) and role.get("CustomerId") not in (None, ""):
            customers.append(str(role["CustomerId"]))
    return {"id": str(user.get("Id") or "").strip(),
            "name": str(user.get("UserName") or user.get("Name") or "").strip(),
            "customers": customers}


def _account_row(acct: dict, manager_id: str) -> dict:
    return {
        "id": str(acct.get("Id")),
        "name": str(acct.get("Name") or "").strip(),
        "number": str(acct.get("Number") or "").strip(),
        "status": str(acct.get("AccountLifeCycleStatus") or "").strip(),
        "currency": str(acct.get("CurrencyCode") or "").strip(),
        "parent_customer_id": digits(acct.get("ParentCustomerId")) or manager_id,
        "timezone": str(acct.get("TimeZone") or "").strip(),
    }


def _search_accounts(store, field: str, value: str, *, module: str) -> tuple[list[dict], list]:
    """``Accounts/Search`` by one predicate, every page followed.
    ``(rows, raw_keys)`` -- the keys of the first raw account, for the
    check page to show against what this reads."""
    c = cfg()
    rows, raw_keys, index = [], [], 0
    while True:
        payload = call(store, "customer", "Accounts/Search", {
            "Predicates": [{"Field": field, "Operator": "Equals", "Value": str(value)}],
            "Ordering": None,
            "PageInfo": {"Index": index, "Size": PAGE_SIZE},
        }, module=module)
        page = [a for a in (payload.get("Accounts") or []) if isinstance(a, dict)]
        if page and not raw_keys:
            raw_keys = sorted(str(k) for k in page[0].keys())
        for acct in page:
            if acct.get("Id") in (None, ""):
                continue
            rows.append(_account_row(acct, c["manager_id"]))
        if len(page) < PAGE_SIZE or index >= 50:
            break
        index += 1
    return rows, raw_keys


def discover_accounts(store=None, *, module: str = "ads_builder") -> dict:
    """Every advertiser account the consent can see, and how they were
    found.

    ``{"accounts": [...], "strategy": "user"|"customer", "user": {...},
    "raw_keys": [...], "notes": [...], "skipped": [...]}``. Strategy
    ``user`` is ``Accounts/Search`` by the connected user's id, which
    reaches accounts under every customer the agency manages; ``customer``
    is the search by the manager's own customer id, asked when the first is
    refused or answers nothing. Accounts in ACCOUNT_STATUSES_SKIPPED are
    listed under ``skipped`` rather than returned, because a report over
    them fails the whole request.
    """
    c = cfg()
    out = {"accounts": [], "strategy": "", "user": {}, "raw_keys": [], "notes": [], "skipped": []}
    found: list[dict] = []
    try:
        out["user"] = connected_user(store, module=module)
    except BingAdsError as exc:
        if exc.code == "REFUSED":
            # The credentials themselves: nothing after this answers
            # differently, and a second refusal would only say it twice.
            raise
        out["notes"].append("the connected user could not be read: " + exc.message)
    if out["user"].get("id"):
        try:
            found, out["raw_keys"] = _search_accounts(store, "UserId", out["user"]["id"], module=module)
            out["strategy"] = "user"
            if not found:
                out["notes"].append("the search by the connected user's id answered no accounts")
        except BingAdsError as exc:
            out["notes"].append("the search by the connected user's id was refused: " + exc.message)
    if not found:
        found, keys = _search_accounts(store, "CustomerId", c["manager_id"], module=module)
        out["strategy"] = "customer"
        out["raw_keys"] = out["raw_keys"] or keys
        if not found:
            out["notes"].append("the search by the manager's customer id answered no accounts either")
    seen: set = set()
    for a in found:
        if a["id"] in seen:
            continue
        seen.add(a["id"])
        if a["status"] in ACCOUNT_STATUSES_SKIPPED:
            out["skipped"].append(a)
        else:
            out["accounts"].append(a)
    return out


def list_accounts(store=None, *, module: str = "ads_builder") -> list[dict]:
    """Every advertiser account the consent can see -- ``discover_accounts()``'s
    list, for the callers that want only that."""
    return discover_accounts(store, module=module)["accounts"]


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


def submit_report(store, body: dict, *, module: str = "reports", customer_id: str = "") -> str:
    payload = call(store, "reporting", "GenerateReport/Submit", body, module=module,
                   customer_id=customer_id)
    rid = str(payload.get("ReportRequestId") or "").strip()
    if not rid:
        raise BingAdsError("the report was submitted and Microsoft answered with no "
                           "ReportRequestId", status=502, code="BAD_ANSWER")
    return rid


def poll_report(store, request_id: str, *, module: str = "reports", customer_id: str = "") -> dict:
    """``{"status": "Success"|"Pending"|"Error", "url": ...}`` off
    ``GenerateReport/Poll``. A status this module does not know reads as
    Pending -- treating it as finished attaches nothing while reporting
    success, the HeyGen lesson. A Success with no URL is a report with no
    rows, which Microsoft answers that way rather than with an empty file;
    the caller reads an empty ``url`` on Success as zero rows."""
    payload = call(store, "reporting", "GenerateReport/Poll",
                   {"ReportRequestId": request_id}, module=module, customer_id=customer_id)
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
    resp = None
    for attempt in (1, 2):
        try:
            resp = requests.get(url, timeout=TIMEOUT)
        except requests.RequestException as exc:
            _record(url.split("?")[0], False, "download", module)
            if attempt == 1:
                _sleep(RETRY_WAIT)
                continue
            raise BingAdsError(f"the report could not be downloaded twice: {type(exc).__name__}",
                               status=502, code="UNREACHABLE")
        if resp.status_code in RETRY_STATUSES and attempt == 1:
            _record(url.split("?")[0], False, "download", module)
            _sleep(RETRY_WAIT)
            continue
        break
    _record(url.split("?")[0], resp.ok, "download", module)
    if not resp.ok:
        raise BingAdsError(f"HTTP {resp.status_code} downloading the report", status=resp.status_code)
    return report_text(resp.content)


def report_text(data: bytes) -> str:
    """CSV text out of the download: a ZIP's first .csv member, or the
    bytes as they are."""
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = [n for n in zf.namelist() if n.lower().endswith(".csv")] or zf.namelist()
                if not names:
                    raise BingAdsError("the report ZIP holds no file", status=502, code="BAD_ANSWER")
                data = zf.read(names[0])
        except zipfile.BadZipFile:
            raise BingAdsError("the report download began like a ZIP and was not one",
                               status=502, code="BAD_ANSWER")
    text = data.decode("utf-8-sig", errors="replace")
    head = text.lstrip("\ufeff \t\r\n")[:64].lower()
    if head.startswith(("<!doctype", "<html", "<?xml", "<s:envelope", "<error")):
        # An expired download URL, or a sign-in page: a refusal in words,
        # never a parse that finds no column row and says so instead.
        raise BingAdsError("the report download answered a web page rather than a report "
                           "(an expired download URL answers this way)", status=502, code="BAD_ANSWER")
    return text


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
