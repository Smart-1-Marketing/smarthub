"""Amazon Ads API: the connection, the DSP entity, and the async DSP report.

The sibling of ``google_ads.py`` and ``bing_ads.py``, for the platform the
sales kit sells as *Amazon DSP*. What is built here is the half that reports:
consenting once through Login with Amazon, keeping the refresh token the way
Google's and Microsoft's are kept, discovering the entity's profile and its
advertisers, and submitting-polling-downloading the daily DSP report the
reports module files. Campaign management is deliberately NOT here --
``/api/amazon/*`` answers 501 in words that say so, the ``/api/bing/*`` rule --
because nothing sells it yet and a write path with no caller is the
declared-and-unwired failure CLAUDE.md counts a dozen of.

## "We have Amazon API access" is five separate claims

Each fails in a way that looks like one of the others, which is why
``preflight()`` asks all of them at once and names every unmet one rather than
stopping at the first (the ``api_readiness`` rule):

1. **the variables are set** -- and are not a quoted value or a placeholder,
   both of which ``_env()`` refuses the way ``hub/config.py`` does;
2. **the refresh token mints an access token** -- ``invalid_grant`` here is a
   consent that was revoked or belongs to another LWA app, *not* a bad
   secret, and reading it as a bad secret sends somebody to rotate a key
   that was never wrong;
3. **the entity's profile is visible to this consent** -- a token inherits
   the access of whoever pressed Connect, so a rep's own Amazon login
   reaches nothing. An empty match is a consent given by a non-admin or the
   wrong entity id, and both answer the same way: nothing in the list;
4. **the advertisers are readable** -- a ``403`` here is the Amazon Ads API
   *application* not being approved for this entity, which reads exactly
   like a wrong key. ``AmazonApiError.kind == "not_permitted"`` keeps the
   two apart, the Google Explorer-tier lesson one platform over;
5. **writes are allowed in this region** -- not probed here at all. Nothing
   in this file writes a campaign, so the claim is left to the module that
   would, and ``preflight()`` says ``unprobed`` rather than implying it
   asked.

## Refreshed per call, never cached across a deploy

``refresh_token_value()`` is ``bing_ads.refresh_token_value()``'s rule: the
environment (``AMAZON_ADS_REFRESH_TOKEN``) wins because it is the copy that
survives a redeploy, and the settings table (``amazon_refresh_token``,
written by the callback) is the copy that works the moment somebody presses
Connect. The access token is minted from it and held in memory only.

## Endpoint paths are confirmed, not assumed

Amazon is mid-way through moving DSP onto the ``/adsApi/v1`` model; the
legacy ``/dsp/`` paths and the v1 ones both answer today, for different
things. ``ENDPOINTS`` is the one place a path lives, and each carries
``confirmed``, which a person flips on ``/reports/amazon-check`` after
seeing the live response shape -- the ``groundtruth_map.py`` discipline.
Until then the reports index says the pull is reading a claim.

## Transcribed, not exercised

No live Amazon entity has answered this code yet. The shapes below are
transcribed from the Amazon Ads API reference and are read defensively: a
response missing a field it should carry is a refusal in words, never a
KeyError.

## What never leaves

The client secret, the refresh token and the access token: ``_redact()``
strips every one of them from any provider message before it reaches a
result, an error, a status line or a log line, and
``test_reports_amazon_dsp.py`` reads every string a pull produces to prove
it. The pre-signed download URL is fetched with **no** Authorization header
-- it is S3, and a bearer token sent to a third-party host is a leak.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field

import requests

MOUNT = "/tools/ads"
CALLBACK_PATH = MOUNT + "/oauth/amazon/callback"
TIMEOUT = 60

# Login with Amazon. These two are stable and documented.
LWA_AUTHORIZE_URL = "https://www.amazon.com/ap/oa"
LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
LWA_SCOPE = "advertising::campaign_management"

# The API host is per region. The entity lives in one, and a token consented
# against NA does not read an EU entity -- which answers as an empty profile
# list rather than as a region error, so the region is named on the status
# line instead of being inferred from a refusal.
API_HOSTS = {
    "NA": "https://advertising-api.amazon.com",
    "EU": "https://advertising-api-eu.amazon.com",
    "FE": "https://advertising-api-fe.amazon.com",
}

# Every path in one place. ``confirmed`` flips to True from
# /reports/amazon-check once a person has seen the live response shape.
ENDPOINTS = {
    # Legacy profile listing: one profile per entity/advertiser the token can
    # see. Still the way to discover the profileId that goes in the
    # Amazon-Advertising-API-Scope header.
    "profiles": {"method": "GET", "path": "/v2/profiles", "confirmed": False,
                 "api": "profiles"},
    # DSP advertisers under the entity. Scope header = the entity's profile.
    "dsp_advertisers": {"method": "GET", "path": "/dsp/advertisers",
                        "confirmed": False, "api": "advertisers"},
    # Async DSP reporting: submit, poll, download. accountId is the DSP
    # advertiser id (or the entity id for an entity-wide report).
    "dsp_report_submit": {"method": "POST", "path": "/accounts/{adv}/dsp/reports",
                          "confirmed": False, "api": "reporting"},
    "dsp_report_status": {"method": "GET",
                          "path": "/accounts/{adv}/dsp/reports/{report_id}",
                          "confirmed": False, "api": "reporting"},
    # The v1 model -- forecasting is GA and the campaign objects live here
    # too. Its header is Amazon-Ads-AccountId rather than the Scope header,
    # which is the whole reason both families are declared in one table.
    "dsp_forecast": {"method": "POST",
                     "path": "/adsApi/v1/retrieve/campaignForecasts/dsp",
                     "confirmed": False, "api": "forecast"},
    "dsp_guidance_orders": {"method": "POST", "path": "/dsp/v1/guidance/orders/list",
                            "confirmed": False, "api": "guidance"},
    "dsp_quick_action": {"method": "POST",
                         "path": "/dsp/v1/quickactions/{action_id}/executions",
                         "confirmed": False, "api": "guidance"},
}

# The hosts hub/quotas._PROVIDER_MARKERS watches, listed here so the two
# files cannot disagree about what an Amazon call looks like.
PROVIDER_HOSTS = tuple(urllib.parse.urlsplit(h).netloc for h in API_HOSTS.values())

# The wall clock one pull may spend waiting on a report before it goes
# *pending* and is asked for again next tick -- stackadapt.py's budget rule,
# for the same shared scheduler thread.
BUDGET_SECONDS = 20
POLL_EVERY = 4

# Spellings exactly as set on Render. No AMAZON_DSP_ twin beside an
# AMAZON_ADS_ one: hub/config.py's ALIASES table is spellings in use, not
# spellings that seem tidy, and a speculative second name is how thirteen
# correct modules once became findings.
ENV = {
    "client_id": "AMAZON_ADS_CLIENT_ID",
    "client_secret": "AMAZON_ADS_CLIENT_SECRET",
    "refresh_token": "AMAZON_ADS_REFRESH_TOKEN",
    "entity_id": "AMAZON_DSP_ENTITY_ID",
    "profile_id": "AMAZON_DSP_ENTITY_PROFILE_ID",
    "region": "AMAZON_ADS_REGION",
    "redirect_uri": "AMAZON_ADS_REDIRECT_URI",
}

REQUIRED = (ENV["client_id"], ENV["client_secret"], ENV["entity_id"])

BLOCKS = {
    ENV["client_id"]: "the Login with Amazon security profile's client id "
                      "(developer.amazon.com -> Login with Amazon)",
    ENV["client_secret"]: "the same security profile's client secret",
    ENV["entity_id"]: "the DSP entity id -- the agency seat every advertiser "
                      "hangs off (the ENTITY... value in the DSP console URL)",
}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _env(name: str) -> str:
    """One setting, read at call time through hub/config.py -- the copy
    somebody corrects mid-incident -- with the two traps that make a set
    variable behave like an unset one refused here rather than at Amazon:

    * the literal quotes Render stores when a value is pasted with them, and
    * the placeholder strings copied out of env.example, which are set, so
      nothing reports them missing, and fail later as an auth error.

    A bare os.environ read only where the Hub is not importable, since this
    module runs standalone too.
    """
    try:
        from hub import config as _config
        value = _config._s(name)
        placeholder = _config.settings.is_placeholder(value)
    except Exception:                                   # noqa: BLE001
        value, placeholder = (os.environ.get(name) or "").strip(), False
    value = value.strip().strip('"').strip("'")
    return "" if placeholder else value


def redirect_uri() -> str:
    """The callback, built from PUBLIC_BASE_URL's origin at call time.

    ``AMAZON_ADS_REDIRECT_URI`` pins it for the day the two have to differ.
    One reading, which is what lets hub/oauth_redirects.py print the string
    somebody pastes into the LWA console and this send the same string:
    ``config.public_base_origin()`` trims a path off the variable, so a
    PUBLIC_BASE_URL that has ever carried a callback of its own does not put
    it in the middle of this one.
    """
    pinned = _env(ENV["redirect_uri"])
    if pinned:
        return pinned
    try:
        from hub import config as _config
        origin = _config.public_base_origin()
    except Exception:                                   # noqa: BLE001
        origin = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return (origin + CALLBACK_PATH) if origin else ""


@dataclass
class AmazonConfig:
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""
    entity_id: str = ""          # the DSP entity: the agency's seat
    entity_profile_id: str = ""  # profileId for the Scope header; discovered if blank
    region: str = "NA"
    redirect_uri: str = ""
    missing: list[str] = field(default_factory=list)

    @property
    def host(self) -> str:
        return API_HOSTS.get(self.region.upper(), API_HOSTS["NA"])

    @property
    def configured(self) -> bool:
        """Credentials and the entity, with no consent needed yet."""
        return not [m for m in self.missing if not m.startswith(ENV["refresh_token"])]

    @property
    def deploy_ready(self) -> bool:
        return not self.missing


def load_config(store=None) -> AmazonConfig:
    """The connection as it stands. Never raises, never probes."""
    cfg = AmazonConfig(
        client_id=_env(ENV["client_id"]),
        client_secret=_env(ENV["client_secret"]),
        refresh_token=refresh_token_value(store),
        entity_id=_env(ENV["entity_id"]),
        entity_profile_id=_env(ENV["profile_id"]),
        region=(_env(ENV["region"]) or "NA").upper(),
        redirect_uri=redirect_uri(),
    )
    for key, name in (("client_id", ENV["client_id"]), ("client_secret", ENV["client_secret"]),
                      ("entity_id", ENV["entity_id"])):
        if not getattr(cfg, key):
            cfg.missing.append(name)
    if cfg.region not in API_HOSTS:
        cfg.missing.append(f"{ENV['region']} is {cfg.region!r}; it is one of "
                           + ", ".join(API_HOSTS))
    if not cfg.redirect_uri:
        cfg.missing.append("PUBLIC_BASE_URL (or " + ENV["redirect_uri"] + ")")
    if not cfg.refresh_token:
        cfg.missing.append(ENV["refresh_token"] + " (or Connect on " + MOUNT + "/settings)")
    return cfg


def refresh_token_value(store=None) -> str:
    """Environment wins -- it is the copy that survives a redeploy."""
    env = _env(ENV["refresh_token"])
    if env:
        return env
    if store is None:
        try:
            from modules.ads_builder import store as _store
            store = _store
        except Exception:                               # noqa: BLE001
            return ""
    try:
        return (store.get_setting("amazon_refresh_token") or "").strip()
    except Exception:                                   # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# Errors, and what never leaves
# ---------------------------------------------------------------------------

class AmazonAuthError(RuntimeError):
    """The connection itself refused: credentials, consent, or a revoked
    token. Already redacted."""


class AmazonApiError(RuntimeError):
    """The API answered, and the answer was a refusal. ``kind`` separates the
    ones that look alike from a traceback: ``auth``, ``not_permitted`` (the
    API application is not approved for this entity), ``not_found``,
    ``not_in_region``, ``throttled``, ``shape``. Already redacted."""

    def __init__(self, message: str, *, kind: str = "error", status: int = 0):
        super().__init__(message)
        self.kind = kind
        self.status = status


_access = {"value": "", "expires_at": 0.0}


def _redact(text) -> str:
    """Never let a credential out, however a provider message quotes it."""
    s = str(text or "")
    for secret in (_env(ENV["client_secret"]), refresh_token_value(), _access["value"]):
        if secret and secret in s:
            s = s.replace(secret, "[Amazon credential redacted]")
    return s[:500]


def forget_tokens() -> None:
    _access["value"] = ""
    _access["expires_at"] = 0.0


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------

def build_auth_url(state: str = "") -> str:
    """The consent URL. Sign in as the account that **administers the DSP
    entity**, not whoever is at the keyboard: the token inherits that
    person's access, and a rep's own Amazon login reaches nothing."""
    cfg = load_config()
    return LWA_AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": cfg.client_id,
        "scope": LWA_SCOPE,
        "response_type": "code",
        "redirect_uri": cfg.redirect_uri,
        "state": state,
    })


def _token_post(data: dict) -> dict:
    try:
        resp = requests.post(LWA_TOKEN_URL, data=data, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise AmazonAuthError("Login with Amazon could not be reached: "
                              f"{type(exc).__name__}")
    payload = _json(resp)
    if not resp.ok or not payload.get("access_token"):
        raise AmazonAuthError(_lwa_error(resp, payload))
    return payload


def exchange_code(code: str) -> dict:
    """Trade the consent code for a refresh token. The caller stores it."""
    cfg = load_config()
    data = _token_post({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": cfg.redirect_uri,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
    })
    _access["value"] = data.get("access_token", "")
    _access["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
    return data


def access_token(cfg: AmazonConfig | None = None) -> str:
    """A bearer token, minted from the refresh token and held in memory
    until it expires. Raises AmazonAuthError naming the *kind* of refusal."""
    cfg = cfg or load_config()
    if _access["value"] and time.time() < _access["expires_at"]:
        return _access["value"]
    if not cfg.deploy_ready:
        raise AmazonAuthError("not configured: " + ", ".join(cfg.missing))
    data = _token_post({
        "grant_type": "refresh_token",
        "refresh_token": cfg.refresh_token,
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
    })
    _access["value"] = data["access_token"]
    _access["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
    return _access["value"]


def connection_status(store=None) -> dict:
    """What the Amazon connection can do right now, in the shape
    ``google_ads.connection_status()`` and ``bing_ads.connection_status()``
    answer in, so the three cards on Settings draw alike. ``configured`` is
    the credentials, ``connected`` is a refresh token from somewhere,
    ``deploy_ready`` is both -- and here it means *the reports module can
    pull*, since nothing else is built on it."""
    cfg = load_config(store)
    from_env = bool(_env(ENV["refresh_token"]))
    from_db = bool(not from_env and cfg.refresh_token)
    missing = [n for n in REQUIRED if not _env(n)]
    if not cfg.redirect_uri:
        missing.append("PUBLIC_BASE_URL")
    region_problem = "" if cfg.region in API_HOSTS else (
        f"is {cfg.region!r}; it is one of " + ", ".join(API_HOSTS))
    blocks = [{"name": n, "why": BLOCKS.get(n, "")} for n in missing if n in BLOCKS]
    if "PUBLIC_BASE_URL" in missing:
        blocks.append({"name": "PUBLIC_BASE_URL",
                       "why": "the hostname Amazon sends the consent back to"})
    if region_problem:
        blocks.append({"name": ENV["region"], "why": "it " + region_problem})
    usable = not missing and not region_problem
    return {
        "configured": usable,
        "connected": from_env or from_db,
        "deploy_ready": usable and (from_env or from_db),
        "entity_id": cfg.entity_id,
        "entity_profile_id": cfg.entity_profile_id,
        "region": cfg.region,
        "region_problem": region_problem,
        "refresh_token_source": (
            "environment" if from_env
            else f"hub database (set {ENV['refresh_token']} to pin it)" if from_db
            else "none"),
        "redirect_uri": cfg.redirect_uri,
        "missing": missing,
        "blocks": blocks,
    }


# ---------------------------------------------------------------------------
# The API: one seam, and every call recorded
# ---------------------------------------------------------------------------

def _http(method: str, url: str, *, headers: dict, json=None, timeout=TIMEOUT):
    """The one call that reaches the network. Replaced whole by the test."""
    return requests.request(method, url, headers=headers, json=json, timeout=timeout)


def _record(url: str, ok: bool, api: str, module: str = "reports") -> None:
    try:
        from hub import quotas
        quotas.record_amazon_ads(url, module=module, api=api, ok=ok)
    except Exception:                                   # noqa: BLE001
        pass


def _headers(cfg: AmazonConfig, token: str, *, scope: str = "",
             account_id: str = "") -> dict:
    h = {
        "Authorization": "Bearer " + token,
        "Amazon-Advertising-API-ClientId": cfg.client_id,
        "Content-Type": "application/json",
    }
    if scope:
        h["Amazon-Advertising-API-Scope"] = str(scope)   # the legacy profile scope
    if account_id:
        h["Amazon-Ads-AccountId"] = str(account_id)      # the v1 model
        h["Amazon-Ads-ClientId"] = cfg.client_id
    return h


def call(name: str, *, scope: str = "", account_id: str = "", payload: dict | None = None,
         module: str = "reports", timeout=TIMEOUT, **path_args):
    """One call to a *named* endpoint. Never guesses a path: a name not in
    ENDPOINTS raises here rather than reaching Amazon as a 404."""
    ep = ENDPOINTS[name]
    cfg = load_config()
    token = access_token(cfg)
    url = cfg.host + ep["path"].format(**path_args)
    try:
        resp = _http(ep["method"], url, json=payload,
                     headers=_headers(cfg, token, scope=scope, account_id=account_id),
                     timeout=timeout)
    except requests.RequestException as exc:
        _record(url, False, ep["api"], module)
        raise AmazonApiError(f"Amazon could not be reached: {type(exc).__name__}",
                             kind="unreachable")
    data = _json(resp)
    _record(url, bool(resp.ok), ep["api"], module)
    if resp.ok:
        return data
    raise AmazonApiError(_api_error(resp, data), kind=_classify(resp, data),
                         status=resp.status_code)


def _classify(resp, data) -> str:
    """The refusals that look the same from a traceback, told apart."""
    code = str((data or {}).get("code", "")) if isinstance(data, dict) else ""
    if resp.status_code == 401:
        return "auth"
    if resp.status_code == 403:
        # UNAUTHORIZED here is usually the API application not being approved
        # for this entity rather than a bad key -- the one refusal that sends
        # people to rotate a credential that was never wrong.
        return "not_permitted"
    if resp.status_code == 404:
        return "not_found"
    if resp.status_code == 425 or code == "UNSUPPORTED_REGION":
        return "not_in_region"
    if resp.status_code == 429:
        return "throttled"
    return "error"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def list_profiles(module: str = "reports") -> list:
    """Every profile this consent can see."""
    data = call("profiles", module=module)
    return data if isinstance(data, list) else []


def entity_profile_id(cfg: AmazonConfig | None = None, module: str = "reports") -> str:
    """The profileId that goes in the Scope header, pinned or discovered.

    Empty is an answer and not an error: it is what a consent given by
    somebody who does not administer the entity looks like, and what a wrong
    AMAZON_DSP_ENTITY_ID looks like. The callers say both.
    """
    cfg = cfg or load_config()
    if cfg.entity_profile_id:
        return cfg.entity_profile_id
    for p in list_profiles(module=module):
        if not isinstance(p, dict):
            continue
        info = p.get("accountInfo") or {}
        ids = {str(info.get("id") or ""), str(info.get("marketplaceStringId") or ""),
               str(p.get("profileId") or "")}
        if cfg.entity_id and cfg.entity_id in ids:
            return str(p.get("profileId") or "")
    return ""


def list_advertisers(cfg: AmazonConfig | None = None, module: str = "reports") -> dict:
    """The DSP advertisers under the entity, with the assumptions named.

    ``{"ok", "advertisers": [{id, name, currency, timezone}], "profile_id",
    "note"}``. The field names are a transcription from the documentation;
    ``note`` says so until ``/reports/amazon-check`` confirms them.
    """
    cfg = cfg or load_config()
    profile = entity_profile_id(cfg, module=module)
    if not profile:
        return {"ok": False, "profile_id": "", "advertisers": [], "error": (
            "The entity's profile is not among the profiles this consent can see. "
            "Either Connect was pressed by an account that does not administer the "
            f"entity, or {ENV['entity_id']} is not this entity's id "
            f"(it is set to {cfg.entity_id or 'nothing'}).")}
    data = call("dsp_advertisers", scope=profile, module=module)
    rows = data.get("response", data) if isinstance(data, dict) else data
    out = []
    for a in rows or []:
        if not isinstance(a, dict):
            continue
        aid = str(a.get("advertiserId") or a.get("id") or "").strip()
        if not aid:
            continue
        out.append({"id": aid, "name": str(a.get("name") or "").strip(),
                    "currency": str(a.get("currency") or "").strip(),
                    "timezone": str(a.get("timezone") or "").strip()})
    return {"ok": True, "profile_id": profile, "advertisers": out,
            "note": "" if ENDPOINTS["dsp_advertisers"]["confirmed"] else
                    "the advertiser field names are a transcription; confirm them on "
                    "/reports/amazon-check"}


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def preflight(store=None, module: str = "reports") -> dict:
    """Every unmet condition at once, in the order each bites.

    ``rung`` is how far the ladder was climbed, so a page can say *which*
    claim failed rather than printing a refusal and leaving the reader to
    guess which of the five it was. Rung 5 is write access, which is
    **not probed**: nothing here writes, so nothing here may claim it.
    """
    out = {"ok": False, "problems": [], "rung": 1, "region": "",
           "entity_profile_id": "", "advertisers": [], "write_access": "unprobed"}
    cfg = load_config(store)
    out["region"] = cfg.region
    if cfg.missing:
        out["problems"].append("Not configured: " + ", ".join(cfg.missing))
        return out
    out["rung"] = 2
    try:
        access_token(cfg)
    except AmazonAuthError as exc:
        out["problems"].append(
            f"Login with Amazon refused the refresh token: {_redact(exc)}. "
            f"Press Connect on {MOUNT}/settings as the entity admin.")
        return out
    out["rung"] = 3
    try:
        adv = list_advertisers(cfg, module=module)
    except AmazonApiError as exc:
        out["problems"].append(f"The entity could not be read ({exc.kind}): {_redact(exc)}"
                               + (" -- the Amazon Ads API application is not approved for "
                                  "this entity yet, which is not a wrong key."
                                  if exc.kind == "not_permitted" else ""))
        return out
    if not adv.get("ok"):
        out["problems"].append(adv.get("error") or "the entity's profile is not visible")
        return out
    out["entity_profile_id"] = adv["profile_id"]
    out["rung"] = 4
    out["advertisers"] = adv["advertisers"]
    if not adv["advertisers"]:
        out["problems"].append(
            "The entity is reachable and has no advertisers on it yet -- one is created "
            "per client in the DSP console; the API lists them and does not create them.")
        return out
    out["rung"] = 5
    out["ok"] = not out["problems"]
    return out


# ---------------------------------------------------------------------------
# The async DSP report
# ---------------------------------------------------------------------------

REPORT_METRICS = ["totalCost", "impressions", "clickThroughs", "totalPurchases",
                  "videoComplete", "totalDetailPageViews"]
REPORT_DIMENSIONS = ["ORDER", "LINE_ITEM"]


def report_request(start, end) -> dict:
    """The body of a daily order/line-item report. Dates are YYYYMMDD in the
    advertiser's own time zone -- one reading, so the submit and the check
    page cannot shape the request two ways."""
    return {
        "startDate": str(start).replace("-", ""),
        "endDate": str(end).replace("-", ""),
        "type": "CAMPAIGN",
        "dimensions": list(REPORT_DIMENSIONS),
        "metrics": list(REPORT_METRICS),
        "timeUnit": "DAILY",
        "format": "JSON",
    }


def submit_report(advertiser_id: str, start, end, *, profile_id: str,
                  module: str = "reports") -> str:
    """Ask for the report. Returns the reportId."""
    data = call("dsp_report_submit", scope=profile_id, payload=report_request(start, end),
                adv=advertiser_id, module=module)
    rid = str((data or {}).get("reportId") or "") if isinstance(data, dict) else ""
    if not rid:
        raise AmazonApiError("the report was submitted and the answer carried no reportId",
                             kind="shape")
    return rid


def poll_report(advertiser_id: str, report_id: str, *, profile_id: str,
                budget: float | None = None, module: str = "reports",
                sleep=None, clock=None) -> dict:
    """Wait inside the budget. ``{"status": SUCCESS|PENDING|FAILURE, ...}``.

    *Pending* is not a failure: nothing is stamped on the watermark and the
    next tick asks again with the same reportId, which the caller keeps.
    ``sleep`` and ``clock`` are injectable so a test drives the wait rather
    than sitting through it.
    """
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    budget = BUDGET_SECONDS if budget is None else float(budget)
    started = clock()
    while True:
        data = call("dsp_report_status", scope=profile_id, adv=advertiser_id,
                    report_id=report_id, module=module)
        status = str((data or {}).get("status") or "").upper() if isinstance(data, dict) else ""
        if status == "SUCCESS":
            return {"status": "SUCCESS", "report_id": report_id,
                    "location": str((data or {}).get("location") or "")}
        if status in ("FAILURE", "FAILED"):
            return {"status": "FAILURE", "report_id": report_id,
                    "error": str((data or {}).get("statusDetails")
                                 or "the report failed at Amazon")}
        if clock() - started + POLL_EVERY > budget:
            return {"status": "PENDING", "report_id": report_id}
        sleep(POLL_EVERY)


def download_report(location: str, module: str = "reports") -> list:
    """The report body: a pre-signed URL to a gzip'd JSON array.

    **No Authorization header goes to that host.** It is S3, and a bearer
    token sent to somebody else's host is a leak -- the one rule in this
    file that is not about reading a refusal correctly.
    """
    try:
        resp = requests.get(location, timeout=(10, 120))
    except requests.RequestException as exc:
        _record(location, False, "download", module)
        raise AmazonApiError(f"the report download could not be reached: {type(exc).__name__}",
                             kind="unreachable")
    _record(location, bool(getattr(resp, "ok", False)), "download", module)
    if not getattr(resp, "ok", False):
        raise AmazonApiError(f"the report download answered HTTP "
                             f"{getattr(resp, 'status_code', '?')}", kind="error",
                             status=int(getattr(resp, "status_code", 0) or 0))
    raw = resp.content or b""
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    try:
        data = json.loads(raw.decode("utf-8") or "[]")
    except ValueError:
        raise AmazonApiError("the report body is not JSON", kind="shape")
    if isinstance(data, list):
        return data
    return data.get("rows") or [] if isinstance(data, dict) else []


# ---------------------------------------------------------------------------
# Forecasting
# ---------------------------------------------------------------------------

def forecast(advertiser_id: str, campaign_spec: dict, module: str = "ads_builder") -> dict:
    """A flight-level forecast for the estimate step: Amazon's own projection.

    Answered as Amazon's number and labeled as Amazon's, never blended with
    the sector benchmark -- the two-numbers rule from
    docs/google-ads-api-integration.md. Nothing calls this yet; it is here
    because the endpoint belongs in ENDPOINTS with the rest, and the caller
    is the estimate step when somebody builds it.
    """
    data = call("dsp_forecast", account_id=advertiser_id, module=module,
                payload={"campaignForecastDescriptions": [campaign_spec]})
    return data if isinstance(data, dict) else {"raw": data}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json(resp):
    try:
        return resp.json()
    except ValueError:
        return {"raw": (getattr(resp, "text", "") or "")[:300]}


def _lwa_error(resp, data) -> str:
    err = str((data or {}).get("error") or "")
    desc = str((data or {}).get("error_description") or "")
    if err == "invalid_grant":
        return ("invalid_grant -- the refresh token was revoked, or it belongs to another "
                "LWA application. This is a consent to give again, not a key to rotate.")
    if err == "invalid_client":
        return (f"invalid_client -- {ENV['client_id']} and {ENV['client_secret']} do not "
                "match one Login with Amazon security profile.")
    return _redact(f"{getattr(resp, 'status_code', '?')} {err} {desc}".strip())


def _api_error(resp, data) -> str:
    status = getattr(resp, "status_code", "?")
    if isinstance(data, dict):
        detail = data.get("details") or data.get("message") or data.get("raw") or ""
        return _redact(f"{status} {data.get('code', '')} {detail}".strip())
    return _redact(f"{status} {str(data)[:200]}")
