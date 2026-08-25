"""Google Ads integration for the Smart 1 Ads Hub module.

Talks to the Google Ads API over REST through ``requests`` — no SDK, so the
API version is one environment variable rather than a dependency bump.

    Base URL : https://googleads.googleapis.com/<version>
    Headers  : Authorization: Bearer <access token>
               developer-token: <approved developer token>
               login-customer-id: <manager / MCC id, digits only>

Everything this module writes is created PAUSED. Nothing here can start
spending money until a person enables it.
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import requests

OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
ADS_SCOPE = "https://www.googleapis.com/auth/adwords"

TIMEOUT = 60


class GoogleAdsError(Exception):
    """A Google Ads API failure, translated into something actionable."""

    def __init__(self, message, status=500, code=None, field=None, trigger=None, raw=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.field = field
        self.trigger = trigger
        self.raw = raw


class NotConnected(GoogleAdsError):
    pass


class NotConfigured(GoogleAdsError):
    pass


# ---------------------------------------------------------------- config
def digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def format_customer_id(value) -> str:
    d = digits(value)
    return f"{d[:3]}-{d[3:6]}-{d[6:]}" if len(d) == 10 else d


def cfg() -> dict:
    return {
        "client_id": os.environ.get("GOOGLE_ADS_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID", ""),
        "client_secret": os.environ.get("GOOGLE_ADS_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "developer_token": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
        "login_customer_id": digits(os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "")),
        "redirect_uri": os.environ.get("GOOGLE_ADS_REDIRECT_URI", ""),
        "version": os.environ.get("GOOGLE_ADS_API_VERSION", "v25"),
    }


def base_url() -> str:
    return f"https://googleads.googleapis.com/{cfg()['version']}"


def assert_configured():
    c = cfg()
    missing = [
        name
        for name, value in (
            ("GOOGLE_ADS_CLIENT_ID", c["client_id"]),
            ("GOOGLE_ADS_CLIENT_SECRET", c["client_secret"]),
            ("GOOGLE_ADS_DEVELOPER_TOKEN", c["developer_token"]),
        )
        if not value
    ]
    if missing:
        raise NotConfigured(
            "Missing environment variables: " + ", ".join(missing), status=400, code="NOT_CONFIGURED"
        )


# ---------------------------------------------------------------- OAuth
# The access token is short lived, so an in-process cache is enough. The
# refresh token is the durable half and lives in the module's settings table
# (and, better, in GOOGLE_ADS_REFRESH_TOKEN).
_access_token = {"value": "", "expires_at": 0.0}


def build_auth_url(state: str = "") -> str:
    c = cfg()
    return OAUTH_AUTH_URL + "?" + urlencode({
        "client_id": c["client_id"],
        "redirect_uri": c["redirect_uri"],
        "response_type": "code",
        "scope": ADS_SCOPE,
        "access_type": "offline",
        "include_granted_scopes": "true",
        # force consent so Google always returns a refresh_token on re-auth
        "prompt": "consent",
        "state": state,
    })


def exchange_code(code: str) -> dict:
    c = cfg()
    resp = requests.post(OAUTH_TOKEN_URL, data={
        "code": code,
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "redirect_uri": c["redirect_uri"],
        "grant_type": "authorization_code",
    }, timeout=TIMEOUT)
    if not resp.ok:
        raise GoogleAdsError(_oauth_error(resp), status=resp.status_code, code="OAUTH_EXCHANGE_FAILED")
    data = resp.json()
    _access_token["value"] = data.get("access_token", "")
    _access_token["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
    return data


def _oauth_error(resp) -> str:
    try:
        payload = resp.json()
        return payload.get("error_description") or payload.get("error") or resp.text[:400]
    except Exception:  # noqa: BLE001
        return resp.text[:400] or f"HTTP {resp.status_code}"


def refresh_token_value(store=None) -> str:
    """Environment wins — it is the copy that survives a redeploy."""
    env = os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "").strip()
    if env:
        return env
    if store is not None:
        return (store.get_setting("google_refresh_token") or "").strip()
    return ""


def access_token(store=None) -> str:
    if _access_token["value"] and time.time() < _access_token["expires_at"]:
        return _access_token["value"]

    refresh = refresh_token_value(store)
    if not refresh:
        raise NotConnected(
            "Google Ads is not connected. Open the Smart 1 Ads settings and click "
            "Connect Google Ads, or set GOOGLE_ADS_REFRESH_TOKEN.",
            status=400, code="NOT_CONNECTED",
        )

    c = cfg()
    resp = requests.post(OAUTH_TOKEN_URL, data={
        "client_id": c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": refresh,
        "grant_type": "refresh_token",
    }, timeout=TIMEOUT)
    if not resp.ok:
        raise NotConnected(
            "Could not refresh the Google token: " + _oauth_error(resp),
            status=401, code="REFRESH_FAILED",
        )
    data = resp.json()
    _access_token["value"] = data["access_token"]
    _access_token["expires_at"] = time.time() + int(data.get("expires_in", 3600)) - 60
    return _access_token["value"]


def forget_tokens():
    _access_token["value"] = ""
    _access_token["expires_at"] = 0.0


# ---------------------------------------------------------------- requests
def _raise_for_google(resp):
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        raise GoogleAdsError(resp.text[:500] or f"HTTP {resp.status_code}", status=resp.status_code)

    err = payload.get("error", {}) if isinstance(payload, dict) else {}
    detail = {}
    try:
        detail = err["details"][0]["errors"][0]
    except Exception:  # noqa: BLE001
        pass

    message = detail.get("message") or err.get("message") or f"HTTP {resp.status_code}"
    code = None
    if isinstance(detail.get("errorCode"), dict) and detail["errorCode"]:
        code = list(detail["errorCode"].values())[0]

    field = None
    try:
        field = ".".join(f["fieldName"] for f in detail["location"]["fieldPathElements"])
    except Exception:  # noqa: BLE001
        pass

    if resp.status_code == 401:
        message += " (access token rejected — reconnect Google Ads)"
    if code and "DEVELOPER_TOKEN" in str(code):
        message += " — check GOOGLE_ADS_DEVELOPER_TOKEN and that it can reach this account."

    raise GoogleAdsError(
        message, status=resp.status_code, code=code, field=field,
        trigger=(detail.get("trigger") or {}).get("stringValue"), raw=payload,
    )


def request(method: str, path: str, body=None, *, store=None, customer_id=None,
            login_customer_id=None):
    assert_configured()
    c = cfg()
    headers = {
        "Authorization": f"Bearer {access_token(store)}",
        "developer-token": c["developer_token"],
        "Content-Type": "application/json",
    }
    login = digits(login_customer_id or c["login_customer_id"])
    if login:
        headers["login-customer-id"] = login

    url = base_url() + path
    resp = requests.request(method, url, json=body, headers=headers, timeout=TIMEOUT)
    # Google Ads bills nothing for API access and limits operations per day
    # instead — Basic access allows 15,000, and a deploy that runs out at 4pm
    # is blocked until midnight Pacific. Counted whether or not it succeeded:
    # a refused operation still spent one. /diagnostics compares the daily
    # total against the ceiling.
    try:
        from hub import quotas as _q
        _q.record_google(url, module="ads_builder", ok=resp.ok)
    except Exception:                                   # noqa: BLE001
        pass
    if not resp.ok:
        _raise_for_google(resp)
    return resp.json() if resp.content else {}


def search(customer_id, query: str, *, store=None, login_customer_id=None, page_size=1000):
    """Run a GAQL query, following pagination to the end."""
    cid = digits(customer_id)
    rows, page_token = [], None
    while True:
        body = {"query": query, "pageSize": page_size}
        if page_token:
            body["pageToken"] = page_token
        data = request("post", f"/customers/{cid}/googleAds:search", body,
                       store=store, customer_id=cid, login_customer_id=login_customer_id)
        rows.extend(data.get("results", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            return rows


# ---------------------------------------------------------------- reads
def micros(value) -> float:
    return (float(value) / 1_000_000) if value not in (None, "") else 0.0


def list_accessible_customers(store=None):
    data = request("get", "/customers:listAccessibleCustomers", store=store)
    return [digits(rn.split("/")[1]) for rn in data.get("resourceNames", [])]


def list_client_accounts(store=None):
    """Expand the manager account into a real account picker."""
    c = cfg()
    roots = [c["login_customer_id"]] if c["login_customer_id"] else list_accessible_customers(store)
    seen = {}

    for root in roots:
        try:
            rows = search(root, """
                SELECT customer_client.id,
                       customer_client.descriptive_name,
                       customer_client.currency_code,
                       customer_client.time_zone,
                       customer_client.manager,
                       customer_client.level
                FROM customer_client
                WHERE customer_client.status = 'ENABLED'
            """, store=store, login_customer_id=root)
        except GoogleAdsError as exc:
            # One unreachable root must not blank the whole picker.
            seen.setdefault(root, {
                "id": root, "formatted_id": format_customer_id(root),
                "name": f"Account {format_customer_id(root)}", "currency": "USD",
                "time_zone": "", "is_manager": True, "level": 0,
                "manager_id": root, "error": exc.message,
            })
            continue

        for row in rows:
            cc = row.get("customerClient", {})
            cid = digits(cc.get("id"))
            if not cid or cid in seen:
                continue
            seen[cid] = {
                "id": cid,
                "formatted_id": format_customer_id(cid),
                "name": cc.get("descriptiveName") or f"Account {format_customer_id(cid)}",
                "currency": cc.get("currencyCode", "USD"),
                "time_zone": cc.get("timeZone", ""),
                "is_manager": bool(cc.get("manager")),
                "level": int(cc.get("level") or 0),
                "manager_id": root,
            }

    return sorted(seen.values(), key=lambda a: (not a["is_manager"], a["name"].lower()))


DATE_RANGES = {
    "TODAY", "YESTERDAY", "LAST_7_DAYS", "LAST_14_DAYS", "LAST_30_DAYS",
    "THIS_MONTH", "LAST_MONTH", "LAST_90_DAYS",
}

_CAMPAIGN_FIELDS = """
    campaign.id, campaign.name, campaign.status,
    campaign.advertising_channel_type, campaign.bidding_strategy_type,
    campaign.start_date, campaign.end_date,
    campaign_budget.amount_micros, campaign_budget.id
"""


def list_campaigns(customer_id, date_range="LAST_30_DAYS", store=None):
    if date_range not in DATE_RANGES:
        date_range = "LAST_30_DAYS"
    cid = digits(customer_id)

    # Every non-removed campaign, including ones with no traffic in the window.
    base_rows = search(cid, f"""
        SELECT {_CAMPAIGN_FIELDS}
        FROM campaign
        WHERE campaign.status != 'REMOVED'
    """, store=store)

    by_id = {}
    for row in base_rows:
        c = row.get("campaign", {})
        by_id[str(c.get("id"))] = {
            "platform": "google",
            "customer_id": cid,
            "id": str(c.get("id")),
            "name": c.get("name", ""),
            "status": c.get("status", ""),
            "channel": c.get("advertisingChannelType", ""),
            "bidding_strategy": c.get("biddingStrategyType", ""),
            "start_date": c.get("startDate", ""),
            "end_date": c.get("endDate", ""),
            "daily_budget": micros((row.get("campaignBudget") or {}).get("amountMicros")),
            "cost": 0.0, "impressions": 0, "clicks": 0, "ctr": 0.0,
            "avg_cpc": 0.0, "conversions": 0.0, "cost_per_conversion": 0.0,
        }

    metric_rows = search(cid, f"""
        SELECT campaign.id,
               metrics.cost_micros, metrics.impressions, metrics.clicks,
               metrics.ctr, metrics.average_cpc, metrics.conversions,
               metrics.cost_per_conversion
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND segments.date DURING {date_range}
    """, store=store)

    for row in metric_rows:
        entry = by_id.get(str(row.get("campaign", {}).get("id")))
        if not entry:
            continue
        m = row.get("metrics", {})
        entry.update(
            cost=micros(m.get("costMicros")),
            impressions=int(m.get("impressions") or 0),
            clicks=int(m.get("clicks") or 0),
            ctr=float(m.get("ctr") or 0),
            avg_cpc=micros(m.get("averageCpc")),
            conversions=float(m.get("conversions") or 0),
            cost_per_conversion=micros(m.get("costPerConversion")),
        )

    return sorted(by_id.values(), key=lambda c: c["cost"], reverse=True)


def campaign_detail(customer_id, campaign_id, store=None):
    cid = digits(customer_id)
    camp = int(campaign_id)

    ad_groups = search(cid, f"""
        SELECT ad_group.id, ad_group.name, ad_group.status, ad_group.cpc_bid_micros
        FROM ad_group
        WHERE campaign.id = {camp} AND ad_group.status != 'REMOVED'
    """, store=store)

    keywords = search(cid, f"""
        SELECT ad_group.id,
               ad_group_criterion.criterion_id,
               ad_group_criterion.keyword.text,
               ad_group_criterion.keyword.match_type,
               ad_group_criterion.status,
               ad_group_criterion.negative
        FROM keyword_view
        WHERE campaign.id = {camp} AND ad_group_criterion.status != 'REMOVED'
    """, store=store)

    groups = []
    for row in ad_groups:
        g = row["adGroup"]
        gid = str(g["id"])
        groups.append({
            "id": gid,
            "name": g.get("name", ""),
            "status": g.get("status", ""),
            "cpc_bid": micros(g.get("cpcBidMicros")),
            "keywords": [
                {
                    "id": str(k["adGroupCriterion"].get("criterionId")),
                    "text": (k["adGroupCriterion"].get("keyword") or {}).get("text", ""),
                    "match_type": (k["adGroupCriterion"].get("keyword") or {}).get("matchType", ""),
                    "negative": bool(k["adGroupCriterion"].get("negative")),
                }
                for k in keywords if str(k.get("adGroup", {}).get("id")) == gid
            ],
        })

    return {"customer_id": cid, "campaign_id": str(campaign_id), "ad_groups": groups}


# ---------------------------------------------------------------- status
ALLOWED_STATUS = {"ENABLED", "PAUSED", "REMOVED"}


def set_campaign_status(customer_id, campaign_id, status, store=None):
    status = str(status or "").upper()
    if status not in ALLOWED_STATUS:
        raise GoogleAdsError(
            f'Invalid status "{status}". Use ENABLED, PAUSED or REMOVED.', status=400
        )
    cid = digits(customer_id)
    resource = f"customers/{cid}/campaigns/{campaign_id}"
    operation = ({"remove": resource} if status == "REMOVED"
                 else {"update": {"resourceName": resource, "status": status},
                       "updateMask": "status"})
    return request("post", f"/customers/{cid}/campaigns:mutate",
                   {"operations": [operation]}, store=store, customer_id=cid)


# ---------------------------------------------------------------- deploy
MATCH_TYPES = {"EXACT": "EXACT", "PHRASE": "PHRASE", "BROAD": "BROAD"}


def parse_keyword(raw):
    """``[roof repair]`` → EXACT, ``"roof repair"`` → PHRASE, else PHRASE."""
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("[") and text.endswith("]"):
        return {"text": text[1:-1].strip(), "match_type": "EXACT"}
    if text.startswith('"') and text.endswith('"'):
        return {"text": text[1:-1].strip(), "match_type": "PHRASE"}
    tagged = re.match(r"^(.*?)\s*\((exact|phrase|broad)\)$", text, re.I)
    if tagged:
        return {"text": tagged.group(1).strip(), "match_type": tagged.group(2).upper()}
    return {"text": text, "match_type": "PHRASE"}


def normalise_url(url) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc or " " in parsed.netloc or "." not in parsed.netloc:
        return ""
    return raw


def _clamp(value, length):
    return str(value or "").strip()[:length]


def _to_micros(dollars) -> str:
    return str(int(round(float(dollars or 0) * 1_000_000)))


def _build_rsa(proposal: dict, group: dict):
    """Google needs >= 3 headlines (<=30 chars) and >= 2 descriptions (<=90)."""
    brand = _clamp(proposal.get("businessName"), 30)
    ads = group.get("ads") or {}

    headlines, seen = [], set()
    for text in list(ads.get("headlines") or []) + [brand, _clamp(group.get("name"), 30),
                                                    "Get A Free Quote Today"]:
        t = _clamp(text, 30)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            headlines.append(t)

    descriptions, seen_d = [], set()
    fallbacks = [
        _clamp(f"{proposal.get('businessName', '')} — {group.get('theme') or 'trusted local experts'}.", 90),
        "Fast response, clear pricing and work you can rely on. Contact us today.",
    ]
    for text in list(ads.get("descriptions") or []) + fallbacks:
        t = _clamp(text, 90)
        if t and t.lower() not in seen_d:
            seen_d.add(t.lower())
            descriptions.append(t)

    return ([{"text": t} for t in headlines[:15]],
            [{"text": t} for t in descriptions[:4]])


def _stamp(offset_days=0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=offset_days)).strftime("%Y%m%d")


def deploy_proposal(customer_id, proposal: dict, *, store=None, campaign_name=None,
                    search_partners=False, validate_only=False):
    """Push an approved proposal into Google Ads as one atomic mutate.

    Created PAUSED. If any single operation fails Google rolls the whole batch
    back, so a half-built campaign is structurally impossible.
    """
    cid = digits(customer_id)
    if not cid:
        raise GoogleAdsError("Pick a Google Ads account first.", status=400)

    final_url = normalise_url(proposal.get("websiteUrl"))
    if not final_url:
        raise GoogleAdsError("The proposal needs a valid website URL before it can deploy.", status=400)

    monthly = float(proposal.get("monthlyBudget") or 0)
    if monthly <= 0:
        raise GoogleAdsError("The proposal needs a monthly budget above zero.", status=400)
    daily = monthly / 30.4

    ad_groups = [g for g in (proposal.get("adGroups") or []) if g.get("keywords")]
    if not ad_groups:
        raise GoogleAdsError("The proposal has no ad groups with keywords.", status=400)

    name = _clamp(campaign_name or
                  f"{proposal.get('businessName', 'Campaign')} | Search | "
                  f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}", 255)

    counter = {"n": 0}

    def temp():
        counter["n"] -= 1
        return counter["n"]

    ops = []

    budget_rn = f"customers/{cid}/campaignBudgets/{temp()}"
    ops.append({"campaignBudgetOperation": {"create": {
        "resourceName": budget_rn,
        "name": _clamp(f"{name} Budget", 255),
        "amountMicros": _to_micros(round(daily, 2)),
        "deliveryMethod": "STANDARD",
        "explicitlyShared": False,
    }}})

    campaign_rn = f"customers/{cid}/campaigns/{temp()}"
    ops.append({"campaignOperation": {"create": {
        "resourceName": campaign_rn,
        "name": name,
        "status": "PAUSED",
        "advertisingChannelType": "SEARCH",
        "campaignBudget": budget_rn,
        "manualCpc": {"enhancedCpcEnabled": False},
        "startDate": _stamp(1),
        "networkSettings": {
            "targetGoogleSearch": True,
            "targetSearchNetwork": bool(search_partners),
            "targetContentNetwork": False,
        },
    }}})

    keyword_count = 0
    for group in ad_groups:
        group_rn = f"customers/{cid}/adGroups/{temp()}"
        bid = float(group.get("avgCPC") or (proposal.get("costEstimation") or {}).get("avgCPC") or 2)
        ops.append({"adGroupOperation": {"create": {
            "resourceName": group_rn,
            "name": _clamp(group.get("name") or "Ad Group", 255),
            "campaign": campaign_rn,
            "status": "PAUSED",
            "type": "SEARCH_STANDARD",
            "cpcBidMicros": _to_micros(round(max(bid, 0.05), 2)),
        }}})

        seen = set()
        for raw in group.get("keywords") or []:
            kw = parse_keyword(raw)
            if not kw or not kw["text"]:
                continue
            key = (kw["match_type"], kw["text"].lower())
            if key in seen:
                continue
            seen.add(key)
            keyword_count += 1
            ops.append({"adGroupCriterionOperation": {"create": {
                "adGroup": group_rn,
                "status": "ENABLED",
                "keyword": {"text": _clamp(kw["text"], 80), "matchType": kw["match_type"]},
            }}})

        headlines, descriptions = _build_rsa(proposal, group)
        if len(headlines) >= 3 and len(descriptions) >= 2:
            ops.append({"adGroupAdOperation": {"create": {
                "adGroup": group_rn,
                "status": "PAUSED",
                "ad": {
                    "finalUrls": [final_url],
                    "responsiveSearchAd": {"headlines": headlines, "descriptions": descriptions},
                },
            }}})

    # Campaign-level negatives from the vault
    vault = proposal.get("negativeKeywordVault") or {}
    negatives, seen_neg = [], set()
    for bucket in vault.values():
        for term in bucket or []:
            kw = parse_keyword(term)
            if not kw or not kw["text"] or kw["text"].lower() in seen_neg:
                continue
            seen_neg.add(kw["text"].lower())
            negatives.append(kw["text"])

    for term in negatives[:5000]:
        ops.append({"campaignCriterionOperation": {"create": {
            "campaign": campaign_rn,
            "negative": True,
            "keyword": {"text": _clamp(term, 80), "matchType": "BROAD"},
        }}})

    assets = proposal.get("adAssets") or {}

    for sl in (assets.get("sitelinks") or [])[:20]:
        link_text = _clamp(sl.get("title") or sl.get("text"), 25)
        if not link_text:
            continue
        asset_rn = f"customers/{cid}/assets/{temp()}"
        # Sitelink descriptions must come as a pair or not at all.
        d1, d2 = _clamp(sl.get("desc1"), 35), _clamp(sl.get("desc2"), 35)
        sitelink = {"linkText": link_text}
        if d1 and d2:
            sitelink.update(description1=d1, description2=d2)
        ops.append({"assetOperation": {"create": {
            "resourceName": asset_rn,
            "finalUrls": [normalise_url(sl.get("url")) or final_url],
            "sitelinkAsset": sitelink,
        }}})
        ops.append({"campaignAssetOperation": {"create": {
            "campaign": campaign_rn, "asset": asset_rn, "fieldType": "SITELINK"}}})

    for co in (assets.get("callouts") or [])[:20]:
        text = _clamp(co if isinstance(co, str) else co.get("text"), 25)
        if not text:
            continue
        asset_rn = f"customers/{cid}/assets/{temp()}"
        ops.append({"assetOperation": {"create": {
            "resourceName": asset_rn, "calloutAsset": {"calloutText": text}}}})
        ops.append({"campaignAssetOperation": {"create": {
            "campaign": campaign_rn, "asset": asset_rn, "fieldType": "CALLOUT"}}})

    snip = assets.get("structuredSnippets") or {}
    values = [_clamp(v, 25) for v in (snip.get("values") or [])]
    values = [v for v in values if v][:10]
    if snip.get("header") and len(values) >= 3:
        asset_rn = f"customers/{cid}/assets/{temp()}"
        ops.append({"assetOperation": {"create": {
            "resourceName": asset_rn,
            "structuredSnippetAsset": {"header": _clamp(snip["header"], 25), "values": values},
        }}})
        ops.append({"campaignAssetOperation": {"create": {
            "campaign": campaign_rn, "asset": asset_rn, "fieldType": "STRUCTURED_SNIPPET"}}})

    if validate_only:
        request("post", f"/customers/{cid}/googleAds:mutate",
                {"mutateOperations": ops, "validateOnly": True, "partialFailure": False},
                store=store, customer_id=cid)
        return {"validated": True, "operation_count": len(ops), "campaign_name": name,
                "customer_id": cid, "ad_group_count": len(ad_groups),
                "keyword_count": keyword_count, "negative_count": len(negatives)}

    result = request("post", f"/customers/{cid}/googleAds:mutate",
                     {"mutateOperations": ops, "partialFailure": False,
                      "responseContentType": "RESOURCE_NAME_ONLY"},
                     store=store, customer_id=cid)

    campaign_resource = next(
        (r["campaignResult"]["resourceName"]
         for r in result.get("mutateOperationResponses", [])
         if r.get("campaignResult", {}).get("resourceName")), None)
    new_id = campaign_resource.split("/")[-1] if campaign_resource else None

    return {
        "validated": False,
        "operation_count": len(ops),
        "campaign_name": name,
        "customer_id": cid,
        "campaign_id": new_id,
        "campaign_resource_name": campaign_resource,
        "ad_group_count": len(ad_groups),
        "keyword_count": keyword_count,
        "negative_count": len(negatives),
        "manage_url": (f"https://ads.google.com/aw/campaigns?campaignId={new_id}&__e={cid}"
                       if new_id else f"https://ads.google.com/aw/campaigns?__e={cid}"),
    }


# What each missing variable actually costs, so a page can say it rather than
# printing a list of names. The developer token is the one with lead time on
# it -- Google approves it separately, and it can be pending for days -- so the
# module has to keep working without it: everything except reading and writing
# the Google Ads API does, and modules/ads_builder/export.py is the way an
# approved campaign still reaches the account meanwhile.
BLOCKS = {
    "GOOGLE_ADS_CLIENT_ID": "the Google sign-in cannot start",
    "GOOGLE_ADS_CLIENT_SECRET": "the Google sign-in cannot start",
    "GOOGLE_ADS_DEVELOPER_TOKEN": "Google approves this separately, in the manager "
                                  "account under Tools → API Center. Until it "
                                  "arrives the API cannot be called at all",
    "GOOGLE_ADS_REDIRECT_URI": "Google has nowhere to send you back to",
}


def connection_status(store=None) -> dict:
    """What the Google Ads API can do right now, and what it cannot.

    ``missing`` and ``connected`` describe the API half only. Nothing else in
    this module reads them: the campaign generator is OpenAI, review and
    approval are the Hub's own, and the Ads Editor export is a file. A page
    that greys itself out because this says "not connected" is describing the
    wrong thing -- ask ``deploy_ready``.
    """
    c = cfg()
    from_env = bool(os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "").strip())
    from_db = bool(store and (store.get_setting("google_refresh_token") or "").strip())
    configured = bool(c["client_id"] and c["client_secret"] and c["developer_token"])
    return {
        "configured": configured,
        "connected": from_env or from_db,
        "deploy_ready": configured and (from_env or from_db),
        "developer_token": bool(c["developer_token"]),
        "refresh_token_source": (
            "environment" if from_env
            else "hub database (set GOOGLE_ADS_REFRESH_TOKEN to pin it)" if from_db
            else "none"
        ),
        "api_version": c["version"],
        "login_customer_id": format_customer_id(c["login_customer_id"]) if c["login_customer_id"] else None,
        "redirect_uri": c["redirect_uri"],
        "missing": [name for name, value in (
            ("GOOGLE_ADS_CLIENT_ID", c["client_id"]),
            ("GOOGLE_ADS_CLIENT_SECRET", c["client_secret"]),
            ("GOOGLE_ADS_DEVELOPER_TOKEN", c["developer_token"]),
            ("GOOGLE_ADS_REDIRECT_URI", c["redirect_uri"]),
        ) if not value],
        "blocks": [
            {"name": name, "why": BLOCKS[name]}
            for name, value in (
                ("GOOGLE_ADS_CLIENT_ID", c["client_id"]),
                ("GOOGLE_ADS_CLIENT_SECRET", c["client_secret"]),
                ("GOOGLE_ADS_DEVELOPER_TOKEN", c["developer_token"]),
                ("GOOGLE_ADS_REDIRECT_URI", c["redirect_uri"]),
            ) if not value
        ],
    }
