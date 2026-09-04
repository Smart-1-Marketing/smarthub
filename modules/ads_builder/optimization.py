"""Read-only Google Ads optimization scans and individually approved writes.

The scanner deliberately keeps Google queries independent. A search-term
permission or resource mismatch can therefore be shown as one unavailable
section instead of turning the whole account workspace into an error page.

AI is advisory here. It may draft keywords, sitelinks and image directions,
but every Google Ads mutation still comes through :func:`apply_action`, one
item at a time, with an exact confirmation word.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import statistics
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

from . import google_ads
from .google_ads import GoogleAdsError

# Pausing is not approving. The vocabulary is google_ads.STATUS_CONFIRMATIONS'
# -- "PAUSE" is what a rep already types to pause a campaign -- rather than the
# blanket "APPROVE" the additive actions share, because this one stops
# something that is currently running and the word somebody types should say
# which of those two they are doing.
PAUSE_CONFIRMATION = google_ads.STATUS_CONFIRMATIONS["PAUSED"]


SUMMARY_QUERY = """
    SELECT customer.id, customer.descriptive_name, customer.currency_code,
           customer.optimization_score, customer.optimization_score_weight,
           metrics.optimization_score_uplift, metrics.optimization_score_url
    FROM customer
"""

RECOMMENDATIONS_QUERY = """
    SELECT recommendation.resource_name, recommendation.type,
           recommendation.campaign, recommendation.ad_group,
           recommendation.dismissed
    FROM recommendation
    WHERE recommendation.dismissed = FALSE
"""

CAMPAIGNS_QUERY = """
    SELECT campaign.id, campaign.name, campaign.status,
           campaign.advertising_channel_type, campaign.bidding_strategy_type,
           campaign.bidding_strategy, campaign.optimization_score,
           campaign.maximize_conversions.target_cpa_micros,
           metrics.cost_micros, metrics.impressions, metrics.clicks,
           metrics.average_cpc, metrics.ctr, metrics.conversions,
           metrics.cost_per_conversion, metrics.search_impression_share,
           metrics.search_budget_lost_impression_share,
           metrics.search_rank_lost_impression_share
    FROM campaign
    WHERE campaign.status != 'REMOVED'
      AND segments.date DURING {date_range}
"""

SEARCH_TERMS_QUERY = """
    SELECT campaign.id, campaign.name, ad_group.id, ad_group.name,
           search_term_view.search_term, search_term_view.status,
           metrics.cost_micros, metrics.impressions, metrics.clicks,
           metrics.average_cpc, metrics.ctr, metrics.conversions,
           metrics.cost_per_conversion
    FROM search_term_view
    WHERE campaign.status = 'ENABLED'
      AND segments.date DURING {date_range}
"""

KEYWORDS_QUERY = """
    SELECT campaign.id, campaign.name, ad_group.id, ad_group.name,
           ad_group_criterion.criterion_id,
           ad_group_criterion.keyword.text,
           ad_group_criterion.keyword.match_type,
           ad_group_criterion.status, ad_group_criterion.negative,
           metrics.cost_micros, metrics.impressions, metrics.clicks,
           metrics.ctr, metrics.conversions
    FROM keyword_view
    WHERE ad_group_criterion.status != 'REMOVED'
      AND segments.date DURING {date_range}
"""

SCHEDULE_QUERY = """
    SELECT campaign.id, campaign.name, segments.day_of_week, segments.hour,
           metrics.cost_micros, metrics.impressions, metrics.clicks,
           metrics.conversions
    FROM campaign
    WHERE campaign.status = 'ENABLED'
      AND segments.date DURING {date_range}
"""


SUMMARY_CACHE_SECONDS = 600
_summary_cache: dict[str, dict] = {}
_summary_lock = Lock()


def clear_cache() -> None:
    with _summary_lock:
        _summary_cache.clear()


def _number(value, default=0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _integer(value) -> int:
    return int(_number(value))


def _id_from_resource(value) -> str:
    return str(value or "").rsplit("/", 1)[-1]


def _stable_id(value) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:12]


def _recommendation(row: dict) -> dict:
    rec = row.get("recommendation") or {}
    resource = str(rec.get("resourceName") or "")
    return {
        "id": _id_from_resource(resource),
        "resource_name": resource,
        "type": str(rec.get("type") or "UNKNOWN"),
        "campaign_id": _id_from_resource(rec.get("campaign")),
        "ad_group_id": _id_from_resource(rec.get("adGroup")),
    }


def account_summary(customer_id, store=None, *, force=False) -> dict:
    """Return Google's score and live recommendation count for one account."""
    cid = google_ads.digits(customer_id)
    now = time.monotonic()
    with _summary_lock:
        cached = _summary_cache.get(cid)
        if not force and cached and cached["expires_at"] > now:
            return json.loads(json.dumps(cached["value"]))

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="ads-opt-summary") as pool:
        score_future = pool.submit(google_ads.search, cid, SUMMARY_QUERY, store=store)
        rec_future = pool.submit(google_ads.search, cid, RECOMMENDATIONS_QUERY, store=store)
        score_rows = score_future.result()
        rec_rows = rec_future.result()

    row = score_rows[0] if score_rows else {}
    customer = row.get("customer") or {}
    metrics = row.get("metrics") or {}
    score = customer.get("optimizationScore")
    recommendations = [_recommendation(r) for r in rec_rows]
    counts = Counter(r["type"] for r in recommendations)
    result = {
        "customer_id": cid,
        "account_name": customer.get("descriptiveName") or "",
        "currency": customer.get("currencyCode") or "USD",
        "score": None if score in (None, "") else _number(score),
        "score_percent": None if score in (None, "") else round(_number(score) * 100),
        "score_weight": _number(customer.get("optimizationScoreWeight")),
        "score_uplift": _number(metrics.get("optimizationScoreUplift")),
        "optimization_url": str(metrics.get("optimizationScoreUrl") or ""),
        "recommendation_count": len(recommendations),
        "recommendation_types": dict(sorted(counts.items())),
    }
    with _summary_lock:
        _summary_cache[cid] = {"expires_at": now + SUMMARY_CACHE_SECONDS, "value": result}
    return json.loads(json.dumps(result))


def _campaign_rows(rows: list[dict]) -> list[dict]:
    campaigns = []
    for row in rows:
        c, m = row.get("campaign") or {}, row.get("metrics") or {}
        clicks = _integer(m.get("clicks"))
        cost = google_ads.micros(m.get("costMicros"))
        campaigns.append({
            "id": str(c.get("id") or ""),
            "name": str(c.get("name") or "Unnamed campaign"),
            "status": str(c.get("status") or ""),
            "channel": str(c.get("advertisingChannelType") or ""),
            "bidding_strategy": str(c.get("biddingStrategyType") or ""),
            "portfolio_strategy": str(c.get("biddingStrategy") or ""),
            "target_cpa": google_ads.micros(
                (c.get("maximizeConversions") or {}).get("targetCpaMicros")
            ),
            "optimization_score": (
                None if c.get("optimizationScore") in (None, "")
                else _number(c.get("optimizationScore"))
            ),
            "cost": cost,
            "impressions": _integer(m.get("impressions")),
            "clicks": clicks,
            "avg_cpc": google_ads.micros(m.get("averageCpc")) or (cost / clicks if clicks else 0),
            "ctr": _number(m.get("ctr")),
            "conversions": _number(m.get("conversions")),
            "cpa": google_ads.micros(m.get("costPerConversion")),
            "search_share": _number(m.get("searchImpressionShare")),
            "budget_lost_share": _number(m.get("searchBudgetLostImpressionShare")),
            "rank_lost_share": _number(m.get("searchRankLostImpressionShare")),
        })
    return campaigns


def _search_term_rows(rows: list[dict]) -> list[dict]:
    terms = []
    for row in rows:
        c, g = row.get("campaign") or {}, row.get("adGroup") or {}
        term, m = row.get("searchTermView") or {}, row.get("metrics") or {}
        clicks = _integer(m.get("clicks"))
        cost = google_ads.micros(m.get("costMicros"))
        terms.append({
            "campaign_id": str(c.get("id") or ""), "campaign_name": str(c.get("name") or ""),
            "ad_group_id": str(g.get("id") or ""), "ad_group_name": str(g.get("name") or ""),
            "text": str(term.get("searchTerm") or "").strip(),
            "status": str(term.get("status") or ""),
            "cost": cost, "impressions": _integer(m.get("impressions")), "clicks": clicks,
            "avg_cpc": google_ads.micros(m.get("averageCpc")) or (cost / clicks if clicks else 0),
            "ctr": _number(m.get("ctr")), "conversions": _number(m.get("conversions")),
            "cpa": google_ads.micros(m.get("costPerConversion")),
        })
    return [t for t in terms if t["text"] and t["ad_group_id"]]


def _keyword_rows(rows: list[dict]) -> list[dict]:
    keywords = []
    for row in rows:
        c, g = row.get("campaign") or {}, row.get("adGroup") or {}
        criterion, m = row.get("adGroupCriterion") or {}, row.get("metrics") or {}
        keyword = criterion.get("keyword") or {}
        text = str(keyword.get("text") or "").strip()
        if not text:
            continue
        keywords.append({
            "campaign_id": str(c.get("id") or ""), "campaign_name": str(c.get("name") or ""),
            "ad_group_id": str(g.get("id") or ""), "ad_group_name": str(g.get("name") or ""),
            "criterion_id": str(criterion.get("criterionId") or ""),
            "text": text, "match_type": str(keyword.get("matchType") or "PHRASE"),
            "status": str(criterion.get("status") or ""), "negative": bool(criterion.get("negative")),
            "cost": google_ads.micros(m.get("costMicros")),
            "impressions": _integer(m.get("impressions")), "clicks": _integer(m.get("clicks")),
            "ctr": _number(m.get("ctr")), "conversions": _number(m.get("conversions")),
        })
    return keywords


def _schedule_rows(rows: list[dict]) -> list[dict]:
    slots = []
    for row in rows:
        c, s, m = row.get("campaign") or {}, row.get("segments") or {}, row.get("metrics") or {}
        slots.append({
            "campaign_id": str(c.get("id") or ""), "campaign_name": str(c.get("name") or ""),
            "day": str(s.get("dayOfWeek") or ""), "hour": _integer(s.get("hour")),
            "cost": google_ads.micros(m.get("costMicros")),
            "impressions": _integer(m.get("impressions")), "clicks": _integer(m.get("clicks")),
            "conversions": _number(m.get("conversions")),
        })
    return slots


RECOMMENDATION_COPY = {
    "KEYWORD": ("Add Google's suggested keywords", "keywords", "Review relevance and match type before applying."),
    "SITELINK_ASSET": ("Add sitelinks", "assets", "Give searchers useful paths beyond the landing page."),
    "TARGET_CPA_OPT_IN": ("Set a Target CPA", "bidding", "Review Google's forecast and conversion volume first."),
    "SET_TARGET_CPA": ("Set a Target CPA", "bidding", "Review Google's suggested target before applying."),
    "FORECASTING_SET_TARGET_CPA": ("Set a forecasted Target CPA", "bidding", "Review the forecast before applying."),
    "DYNAMIC_IMAGE_EXTENSION_OPT_IN": ("Add dynamic image assets", "assets", "Confirm the site imagery fits the brand."),
    "IMPROVE_GOOGLE_TAG_COVERAGE": ("Improve Google tag coverage", "tracking", "Open GTM Tools and confirm each missing page."),
    "MAXIMIZE_CONVERSIONS_OPT_IN": ("Improve conversion bidding", "bidding", "Review Google's predicted impact first."),
    "RESPONSIVE_SEARCH_AD_IMPROVE_AD_STRENGTH": ("Improve ad strength", "assets", "Add useful, distinct creative assets."),
    "CAMPAIGN_BUDGET": ("Review a budget-limited campaign", "diagnostics", "Compare the proposed spend with the approved budget."),
}

# Google recommendation resources do not always include the proposed content in
# the list response. Route those types into a Hub editor so the trafficker sees
# and can change the exact value before AI review and approval.
RECOMMENDATION_BUILDERS = {
    "KEYWORD": "search_terms",
    "SITELINK_ASSET": "sitelink",
    "TARGET_CPA_OPT_IN": "cpa",
    "SET_TARGET_CPA": "cpa",
    "FORECASTING_SET_TARGET_CPA": "cpa",
    "DYNAMIC_IMAGE_EXTENSION_OPT_IN": "image",
    "IMPROVE_GOOGLE_TAG_COVERAGE": "tracking",
}
RECOMMENDATION_GUIDANCE_ONLY = {
    "RESPONSIVE_SEARCH_AD_IMPROVE_AD_STRENGTH",
    "CAMPAIGN_BUDGET",
}


def _recommendation_items(recommendations: list[dict], campaign_names=None) -> list[dict]:
    campaign_names = campaign_names or {}
    items = []
    for rec in recommendations:
        title, category, next_step = RECOMMENDATION_COPY.get(
            rec["type"],
            (rec["type"].replace("_", " ").title(), "diagnostics", "Review Google's recommendation and forecast."),
        )
        builder = RECOMMENDATION_BUILDERS.get(rec["type"], "")
        action = "" if builder or rec["type"] in RECOMMENDATION_GUIDANCE_ONLY else "apply_recommendation"
        items.append({
            "id": f"google-{rec['id']}", "source": "google", "category": category,
            "severity": "medium", "title": title,
            "why": "Google Ads has an active recommendation for this account.",
            "next_step": next_step, "action": action, "builder": builder,
            "confirmation": "APPROVE", "data": {
                **rec,
                "campaign_name": campaign_names.get(rec["campaign_id"], ""),
            },
        })
    return items


def analyse_rows(customer_id: str, date_range: str, datasets: dict, errors=None) -> dict:
    """Turn raw Google rows into a small, deterministic trafficker queue."""
    campaigns = _campaign_rows(datasets.get("campaigns") or [])
    terms = _search_term_rows(datasets.get("search_terms") or [])
    keywords = _keyword_rows(datasets.get("keywords") or [])
    schedules = _schedule_rows(datasets.get("schedule") or [])
    recommendations = [_recommendation(r) for r in (datasets.get("recommendations") or [])]
    summary_rows = datasets.get("summary") or []
    summary_row = summary_rows[0] if summary_rows else {}
    customer = summary_row.get("customer") or {}
    metrics = summary_row.get("metrics") or {}

    total_cost = sum(c["cost"] for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_conversions = sum(c["conversions"] for c in campaigns)
    account_cpc = total_cost / total_clicks if total_clicks else 0
    score = customer.get("optimizationScore")
    campaign_names = {c["id"]: c["name"] for c in campaigns}
    items = _recommendation_items(recommendations, campaign_names)

    # High click cost: require a useful sample and compare to the account, not
    # a hard-coded industry price. Zero-conversion spend is also surfaced.
    campaign_cpcs = [c["avg_cpc"] for c in campaigns if c["clicks"] >= 5 and c["avg_cpc"] > 0]
    cpc_baseline = statistics.median(campaign_cpcs) if campaign_cpcs else account_cpc
    threshold = cpc_baseline * 1.35
    high_cost = [c for c in campaigns if c["clicks"] >= 5 and (
        (threshold > 0 and c["avg_cpc"] > threshold)
        or (c["conversions"] == 0 and c["cost"] >= max(25, account_cpc * 5))
    )]
    for c in sorted(high_cost, key=lambda x: (x["avg_cpc"], x["cost"]), reverse=True)[:8]:
        items.append({
            "id": f"cpc-{c['id']}", "source": "smart1", "category": "click_costs",
            "severity": "high" if c["conversions"] == 0 else "medium",
            "title": f"High click cost in {c['name']}",
            "why": f"Average CPC is ${c['avg_cpc']:.2f} on {c['clicks']} clicks; the account baseline is ${cpc_baseline:.2f}.",
            "next_step": "Review search terms, match types, bids, and landing-page relevance before changing the campaign.",
            "action": "", "data": c,
        })

    negative_floor = max(10, account_cpc * 3)
    negatives = [t for t in terms if t["clicks"] >= 3 and t["conversions"] == 0
                 and t["cost"] >= negative_floor]
    for term in sorted(negatives, key=lambda x: x["cost"], reverse=True)[:20]:
        items.append({
            "id": f"negative-{term['ad_group_id']}-{_stable_id(term['text'])}",
            "source": "smart1", "category": "search_terms", "severity": "high",
            "title": f'Consider excluding “{term["text"]}”',
            "why": f"{term['clicks']} clicks and ${term['cost']:.2f} spend produced no conversions in this period.",
            "next_step": "Check intent, then add this exact search term as an ad-group negative.",
            "action": "add_negative_keyword", "confirmation": "APPROVE",
            "data": {**term, "match_type": "EXACT"},
        })

    active_text = {re.sub(r"\s+", " ", k["text"].strip().lower()) for k in keywords if not k["negative"]}
    winners = [t for t in terms if t["conversions"] >= 1
               and re.sub(r"\s+", " ", t["text"].lower()) not in active_text]
    for term in sorted(winners, key=lambda x: (x["conversions"], x["clicks"]), reverse=True)[:12]:
        items.append({
            "id": f"keyword-{term['ad_group_id']}-{_stable_id(term['text'])}",
            "source": "smart1", "category": "keywords", "severity": "medium",
            "title": f'Add converting term “{term["text"]}”',
            "why": f"This search term produced {term['conversions']:g} conversion(s) but is not an active keyword in the scan.",
            "next_step": "Review its landing page and match type. Approved keywords are created paused for a final Google Ads review.",
            "action": "add_keyword", "confirmation": "APPROVE",
            "data": {**term, "match_type": "EXACT"},
        })

    grouped = defaultdict(list)
    for keyword in keywords:
        if not keyword["negative"]:
            key = (keyword["ad_group_id"], re.sub(r"\s+", " ", keyword["text"].strip().lower()), keyword["match_type"])
            grouped[key].append(keyword)
    for duplicates in grouped.values():
        if len(duplicates) < 2:
            continue
        keep, *remove = sorted(duplicates, key=lambda x: (x["conversions"], x["clicks"], x["impressions"]), reverse=True)
        for keyword in remove:
            items.append({
                "id": f"redundant-{keyword['ad_group_id']}-{keyword['criterion_id']}",
                "source": "smart1", "category": "keywords", "severity": "low",
                "title": f'Remove redundant keyword “{keyword["text"]}”',
                "why": f"The same {keyword['match_type'].lower()} keyword appears more than once in {keyword['ad_group_name']}; keep criterion {keep['criterion_id']}.",
                "next_step": "Confirm the duplicate, then remove only this criterion. Removed criteria cannot be restored.",
                "action": "remove_keyword", "confirmation": "REMOVE", "data": keyword,
            })

    # A KEYWORD that spends and never converts, which is a different finding
    # from the search-term one above: that adds a term as a negative and never
    # touches an existing criterion, so a keyword we are bidding on ourselves
    # went on spending with nothing here able to say so. Floored against the
    # account's own CPC rather than a hard-coded price, the way the click-cost
    # and negative-keyword detectors already are -- a fixed dollar figure means
    # something different on a $2 CPC than on a $40 one.
    pause_floor = max(20, account_cpc * 6)
    pausable = [k for k in keywords
                if not k["negative"] and k["status"] == "ENABLED"
                and k["clicks"] >= 5 and k["conversions"] == 0
                and k["cost"] >= pause_floor]
    for keyword in sorted(pausable, key=lambda x: x["cost"], reverse=True)[:20]:
        items.append({
            "id": f"pause-{keyword['ad_group_id']}-{keyword['criterion_id']}",
            "source": "smart1", "category": "keyword_pauses", "severity": "high",
            "title": f'Pause “{keyword["text"]}”',
            "why": (f"{keyword['clicks']} clicks and ${keyword['cost']:.2f} spend produced "
                    f"no conversions in this period."),
            "next_step": ("Pausing keeps the keyword and its history; it can be enabled "
                          "again from Google Ads at any time."),
            "action": "pause_keyword", "confirmation": PAUSE_CONFIRMATION,
            "data": keyword,
        })

    weak_slots = [s for s in schedules if s["clicks"] >= 5 and s["conversions"] == 0
                  and s["cost"] >= max(15, account_cpc * 5)]
    for slot in sorted(weak_slots, key=lambda x: x["cost"], reverse=True)[:8]:
        hour = slot["hour"]
        label = f"{slot['day'].replace('_', ' ').title()} {hour:02d}:00–{(hour + 1) % 24:02d}:00"
        items.append({
            "id": f"schedule-{slot['campaign_id']}-{slot['day']}-{hour}",
            "source": "smart1", "category": "schedule", "severity": "medium",
            "title": f"Review {label}",
            "why": f"{slot['clicks']} clicks and ${slot['cost']:.2f} spend produced no conversions.",
            "next_step": "Compare several periods before changing an ad schedule. This item is guidance only.",
            "action": "", "data": slot,
        })

    if campaigns and total_clicks >= 10 and total_conversions == 0:
        items.insert(0, {
            "id": "diagnostic-no-conversions", "source": "smart1", "category": "diagnostics",
            "severity": "high", "title": "No recorded conversions",
            "why": f"The account spent ${total_cost:.2f} across {total_clicks} clicks without a recorded conversion.",
            "next_step": "Verify conversion actions and Google tag coverage before making bidding decisions.",
            "action": "", "data": {},
        })
    if not any(c["status"] == "ENABLED" for c in campaigns):
        items.insert(0, {
            "id": "diagnostic-no-enabled", "source": "smart1", "category": "diagnostics",
            "severity": "high", "title": "No enabled campaign has traffic",
            "why": "No enabled campaign returned performance in the selected period.",
            "next_step": "Open Live campaigns and confirm the account is expected to be advertising.",
            "action": "", "data": {},
        })

    account_name = str(customer.get("descriptiveName") or "")
    term_actions = {}
    for item in items:
        data = item.get("data") or {}
        campaign_id = str(data.get("campaign_id") or "")
        item["account_name"] = account_name
        item["campaign_id"] = campaign_id
        item["campaign_name"] = str(
            data.get("campaign_name") or campaign_names.get(campaign_id) or "Account-wide"
        )
        if item.get("action") in {"add_negative_keyword", "add_keyword"}:
            term_key = (
                str(data.get("ad_group_id") or ""),
                re.sub(r"\s+", " ", str(data.get("text") or "").strip().lower()),
            )
            term_actions[term_key] = {
                "item_id": item["id"], "action": item["action"],
                "recommendation": (
                    "Add as a negative keyword"
                    if item["action"] == "add_negative_keyword"
                    else "Add as a paused keyword"
                ),
            }

    search_term_scan = []
    for term in sorted(terms, key=lambda x: (x["cost"], x["clicks"], x["conversions"]), reverse=True):
        term_key = (
            term["ad_group_id"],
            re.sub(r"\s+", " ", term["text"].strip().lower()),
        )
        suggested = term_actions.get(term_key) or {
            "item_id": "", "action": "", "recommendation": "Review only"
        }
        search_term_scan.append({
            **term, **suggested, "account_name": account_name,
        })

    category_counts = Counter(item["category"] for item in items)
    return {
        "customer_id": google_ads.digits(customer_id),
        "date_range": date_range,
        "account_name": account_name,
        "currency": str(customer.get("currencyCode") or "USD"),
        "score": None if score in (None, "") else _number(score),
        "score_percent": None if score in (None, "") else round(_number(score) * 100),
        "score_uplift": _number(metrics.get("optimizationScoreUplift")),
        "optimization_url": str(metrics.get("optimizationScoreUrl") or ""),
        "recommendation_count": len(recommendations),
        "item_count": len(items), "category_counts": dict(category_counts),
        "totals": {"cost": total_cost, "clicks": total_clicks, "conversions": total_conversions,
                   "avg_cpc": account_cpc, "campaigns": len(campaigns)},
        "campaigns": campaigns, "winning_terms": winners[:20], "items": items,
        "search_terms": search_term_scan[:500],
        "search_term_count": len(search_term_scan),
        "search_term_candidate_count": sum(1 for row in search_term_scan if row["action"]),
        "errors": errors or {},
    }


def scan_account(customer_id, date_range="LAST_30_DAYS", store=None) -> dict:
    cid = google_ads.digits(customer_id)
    if date_range not in google_ads.DATE_RANGES:
        date_range = "LAST_30_DAYS"
    queries = {
        "summary": SUMMARY_QUERY,
        "recommendations": RECOMMENDATIONS_QUERY,
        "campaigns": CAMPAIGNS_QUERY.format(date_range=date_range),
        "search_terms": SEARCH_TERMS_QUERY.format(date_range=date_range),
        "keywords": KEYWORDS_QUERY.format(date_range=date_range),
        "schedule": SCHEDULE_QUERY.format(date_range=date_range),
    }
    datasets, errors = {}, {}
    with ThreadPoolExecutor(max_workers=len(queries), thread_name_prefix="ads-opt-scan") as pool:
        futures = {pool.submit(google_ads.search, cid, query, store=store): name
                   for name, query in queries.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                datasets[name] = future.result()
            except GoogleAdsError as exc:
                datasets[name] = []
                errors[name] = {"message": exc.message, "code": exc.code}
    # If Google rejected every section, preserve the normal API error behavior.
    if errors and len(errors) == len(queries):
        first = next(iter(errors.values()))
        raise GoogleAdsError(first["message"], code=first.get("code"))
    return analyse_rows(cid, date_range, datasets, errors)


ACTION_CONFIRMATIONS = {
    "apply_recommendation": "APPROVE", "add_negative_keyword": "APPROVE",
    "add_keyword": "APPROVE", "remove_keyword": "REMOVE",
    "pause_keyword": PAUSE_CONFIRMATION,
    "add_sitelink": "APPROVE", "add_image": "APPROVE", "set_target_cpa": "APPROVE",
}


def _confirmed(action, payload):
    expected = ACTION_CONFIRMATIONS.get(action)
    if not expected:
        raise GoogleAdsError("That optimization action is not supported.", status=400)
    if str(payload.get("confirmation") or "").strip().upper() != expected:
        raise GoogleAdsError(f'Type "{expected}" to approve this one change.', status=400)


def _required_id(value, label):
    result = google_ads.digits(value)
    if not result:
        raise GoogleAdsError(f"{label} is required.", status=400)
    return result


def _keyword_action(cid, payload, store=None, *, negative=False):
    ad_group_id = _required_id(payload.get("ad_group_id"), "ad_group_id")
    text = re.sub(r"\s+", " ", str(payload.get("text") or "").strip())[:80]
    if not text:
        raise GoogleAdsError("Keyword text is required.", status=400)
    match_type = str(payload.get("match_type") or "EXACT").upper()
    if match_type not in google_ads.MATCH_TYPES:
        raise GoogleAdsError("Match type must be EXACT, PHRASE, or BROAD.", status=400)
    create = {
        "adGroup": f"customers/{cid}/adGroups/{ad_group_id}",
        "status": "ENABLED" if negative else "PAUSED",
        "negative": bool(negative), "keyword": {"text": text, "matchType": match_type},
    }
    result = google_ads.request("post", f"/customers/{cid}/adGroupCriteria:mutate",
                                {"operations": [{"create": create}]}, store=store,
                                customer_id=cid)
    return result, {"ad_group_id": ad_group_id, "text": text, "match_type": match_type,
                    "created_status": create["status"]}


def _remove_keyword(cid, payload, store=None):
    ad_group_id = _required_id(payload.get("ad_group_id"), "ad_group_id")
    criterion_id = _required_id(payload.get("criterion_id"), "criterion_id")
    resource = f"customers/{cid}/adGroupCriteria/{ad_group_id}~{criterion_id}"
    result = google_ads.request("post", f"/customers/{cid}/adGroupCriteria:mutate",
                                {"operations": [{"remove": resource}]}, store=store,
                                customer_id=cid)
    return result, {"ad_group_id": ad_group_id, "criterion_id": criterion_id}


def _pause_keyword(cid, payload, store=None):
    """Pause one ad-group criterion, mirroring set_campaign_status' shape.

    An update with an explicit updateMask rather than a remove: pausing keeps
    the keyword, its quality score and its history, so a keyword paused in
    error is one press in Google Ads to undo. `remove_keyword` beside this one
    is the destructive answer and says so.
    """
    ad_group_id = _required_id(payload.get("ad_group_id"), "ad_group_id")
    criterion_id = _required_id(payload.get("criterion_id"), "criterion_id")
    resource = f"customers/{cid}/adGroupCriteria/{ad_group_id}~{criterion_id}"
    operation = {"update": {"resourceName": resource, "status": "PAUSED"},
                 "updateMask": "status"}
    result = google_ads.request("post", f"/customers/{cid}/adGroupCriteria:mutate",
                                {"operations": [operation]}, store=store,
                                customer_id=cid)
    return result, {"ad_group_id": ad_group_id, "criterion_id": criterion_id,
                    "text": str(payload.get("text") or "")[:80], "status": "PAUSED"}


def _apply_recommendation(cid, payload, store=None):
    resource = str(payload.get("resource_name") or "").strip()
    if not re.fullmatch(rf"customers/{re.escape(cid)}/recommendations/[A-Za-z0-9_~.-]+", resource):
        raise GoogleAdsError("That recommendation does not belong to this account.", status=400)
    body = {"operations": [{"resourceName": resource}]}
    result = google_ads.request("post", f"/customers/{cid}/recommendations:apply",
                                body, store=store, customer_id=cid)
    clear_cache()
    return result, {"resource_name": resource}


def _add_sitelink(cid, payload, store=None):
    campaign_id = _required_id(payload.get("campaign_id"), "campaign_id")
    link_text = str(payload.get("link_text") or "").strip()[:25]
    final_url = google_ads.normalise_url(payload.get("final_url"))
    if not link_text or not final_url:
        raise GoogleAdsError("Sitelink text and a valid final URL are required.", status=400)
    sitelink = {"linkText": link_text}
    description1 = str(payload.get("description1") or "").strip()[:35]
    description2 = str(payload.get("description2") or "").strip()[:35]
    if description1 and description2:
        sitelink.update(description1=description1, description2=description2)
    asset_resource = f"customers/{cid}/assets/-1"
    body = {"mutateOperations": [
        {"assetOperation": {"create": {"resourceName": asset_resource,
                                         "finalUrls": [final_url], "sitelinkAsset": sitelink}}},
        {"campaignAssetOperation": {"create": {
            "campaign": f"customers/{cid}/campaigns/{campaign_id}",
            "asset": asset_resource, "fieldType": "SITELINK"}}},
    ], "partialFailure": False}
    result = google_ads.request("post", f"/customers/{cid}/googleAds:mutate", body,
                                store=store, customer_id=cid)
    return result, {"campaign_id": campaign_id, "link_text": link_text, "final_url": final_url}


def _add_image(cid, payload, store=None):
    campaign_id = _required_id(payload.get("campaign_id"), "campaign_id")
    raw = str(payload.get("image_data") or "")
    match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=\r\n]+)", raw)
    if not match:
        raise GoogleAdsError("Upload a PNG or JPEG image.", status=400)
    try:
        decoded = base64.b64decode(match.group(2), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise GoogleAdsError("The image data is not valid.", status=400) from exc
    if not decoded or len(decoded) > 5 * 1024 * 1024:
        raise GoogleAdsError("The image must be no larger than 5 MB.", status=400)
    if not (decoded.startswith(b"\x89PNG\r\n\x1a\n") or decoded.startswith(b"\xff\xd8\xff")):
        raise GoogleAdsError("The uploaded file is not a valid PNG or JPEG.", status=400)
    name = str(payload.get("name") or "Smart 1 Ads image").strip()[:128]
    asset_resource = f"customers/{cid}/assets/-1"
    body = {"mutateOperations": [
        {"assetOperation": {"create": {"resourceName": asset_resource, "name": name,
                                         "imageAsset": {"data": base64.b64encode(decoded).decode("ascii")}}}},
        {"campaignAssetOperation": {"create": {
            "campaign": f"customers/{cid}/campaigns/{campaign_id}",
            "asset": asset_resource, "fieldType": "AD_IMAGE"}}},
    ], "partialFailure": False}
    result = google_ads.request("post", f"/customers/{cid}/googleAds:mutate", body,
                                store=store, customer_id=cid)
    return result, {"campaign_id": campaign_id, "name": name, "bytes": len(decoded)}


def _set_target_cpa(cid, payload, store=None):
    campaign_id = _required_id(payload.get("campaign_id"), "campaign_id")
    target = _number(payload.get("target_cpa"))
    if target <= 0 or target > 1_000_000:
        raise GoogleAdsError("Enter a positive Target CPA amount.", status=400)
    rows = google_ads.search(cid, f"""
        SELECT campaign.id, campaign.bidding_strategy_type, campaign.bidding_strategy
        FROM campaign WHERE campaign.id = {int(campaign_id)}
    """, store=store)
    campaign = (rows[0].get("campaign") if rows else {}) or {}
    if campaign.get("biddingStrategy"):
        raise GoogleAdsError("This campaign uses a portfolio strategy. Change its Target CPA in Google Ads.", status=400)
    if campaign.get("biddingStrategyType") != "MAXIMIZE_CONVERSIONS":
        raise GoogleAdsError("Direct Target CPA edits are limited to standard Maximize Conversions campaigns. Apply Google's bidding recommendation instead.", status=400)
    resource = f"customers/{cid}/campaigns/{campaign_id}"
    operation = {"update": {"resourceName": resource,
                             "maximizeConversions": {"targetCpaMicros": str(round(target * 1_000_000))}},
                 "updateMask": "maximize_conversions.target_cpa_micros"}
    result = google_ads.request("post", f"/customers/{cid}/campaigns:mutate",
                                {"operations": [operation]}, store=store, customer_id=cid)
    return result, {"campaign_id": campaign_id, "target_cpa": target}


def apply_action(customer_id, action, payload, store=None) -> dict:
    cid = google_ads.digits(customer_id)
    if not cid:
        raise GoogleAdsError("customer_id is required.", status=400)
    action = str(action or "").strip()
    payload = dict(payload or {})
    _confirmed(action, payload)
    if action == "apply_recommendation":
        result, detail = _apply_recommendation(cid, payload, store)
    elif action == "add_negative_keyword":
        result, detail = _keyword_action(cid, payload, store, negative=True)
    elif action == "add_keyword":
        result, detail = _keyword_action(cid, payload, store, negative=False)
    elif action == "remove_keyword":
        result, detail = _remove_keyword(cid, payload, store)
    elif action == "pause_keyword":
        result, detail = _pause_keyword(cid, payload, store)
    elif action == "add_sitelink":
        result, detail = _add_sitelink(cid, payload, store)
    elif action == "add_image":
        result, detail = _add_image(cid, payload, store)
    elif action == "set_target_cpa":
        result, detail = _set_target_cpa(cid, payload, store)
    else:  # pragma: no cover - _confirmed rejects this first
        raise GoogleAdsError("That optimization action is not supported.", status=400)
    return {"ok": True, "customer_id": cid, "action": action, "detail": detail,
            "google_result": result}


def ai_drafts(context: dict) -> dict:
    """Generate bounded drafts; fall back to measured converting terms."""
    context = dict(context or {})
    winning_terms = []
    for row in (context.get("winning_terms") or [])[:20]:
        text = re.sub(r"\s+", " ", str((row or {}).get("text") or "").strip())[:80]
        if text:
            winning_terms.append({
                "text": text, "campaign_id": google_ads.digits((row or {}).get("campaign_id")),
                "ad_group_id": google_ads.digits((row or {}).get("ad_group_id")),
            })
    selected_items = []
    for raw in (context.get("selected_items") or [])[:20]:
        raw = raw or {}
        data = raw.get("data") or {}
        selected_items.append({
            "id": str(raw.get("id") or "")[:120],
            "title": str(raw.get("title") or "")[:240],
            "category": str(raw.get("category") or "")[:40],
            "action": str(raw.get("action") or "")[:60],
            "account_name": str(raw.get("account_name") or context.get("account_name") or "")[:120],
            "campaign_name": str(raw.get("campaign_name") or "")[:160],
            "data": {
                "campaign_id": google_ads.digits(data.get("campaign_id")),
                "ad_group_id": google_ads.digits(data.get("ad_group_id")),
                "text": re.sub(r"\s+", " ", str(data.get("text") or "").strip())[:80],
                "match_type": str(data.get("match_type") or "")[:20].upper(),
                "link_text": str(data.get("link_text") or "")[:25],
                "description1": str(data.get("description1") or "")[:35],
                "description2": str(data.get("description2") or "")[:35],
                "final_url": google_ads.normalise_url(data.get("final_url")),
                "clicks": _integer(data.get("clicks")),
                "cost": _number(data.get("cost")),
                "conversions": _number(data.get("conversions")),
            },
        })
    fallback = {
        "keywords": [{**row, "match_type": "EXACT",
                      "reason": "Measured converting search term; review before adding."}
                     for row in winning_terms[:5]],
        "sitelinks": [],
        "image_prompts": [{"prompt": f"Authentic, brand-safe campaign image for {str(context.get('account_name') or 'this advertiser')[:100]}; no text, logos, prices, offers, or unverifiable claims."}],
        "reviews": [{
            "id": row["id"], "verdict": "hold",
            "suggested_text": row["data"]["text"],
            "suggested_match_type": row["data"]["match_type"],
            "rationale": "AI review is unavailable. Keep this item on hold until it can be reviewed.",
        } for row in selected_items if row["id"]],
        "notes": ["AI was unavailable, so Smart 1 Ads returned only measured search-term drafts."],
        "ai_used": False,
    }
    try:
        from hub import ai
        output = ai.chat_json([
            {"role": "system", "content": (
                "You assist a Google Ads trafficker. Return JSON only with keys keywords, sitelinks, "
                "image_prompts, reviews, notes. Never invent performance, offers, prices, claims, URLs, campaign IDs, "
                "or ad-group IDs. Keywords: text, match_type, campaign_id, ad_group_id, reason. Sitelinks: "
                "link_text, description1, description2, final_url, campaign_id, reason. Image prompts: prompt. "
                "Reviews: id, verdict (approve, edit, or hold), suggested_text, suggested_match_type, rationale. "
                "Review every selected item using only the supplied performance evidence. Search-term reviews must "
                "check intent before recommending a positive or negative keyword. Make at most 20 reviews and 5 of "
                "each draft type. Every output is editable and requires human approval." )},
            {"role": "user", "content": json.dumps({
                "account_name": str(context.get("account_name") or "")[:120],
                "focus": str(context.get("focus") or "all")[:40],
                "campaigns": (context.get("campaigns") or [])[:20],
                "measured_winning_terms": winning_terms,
                "selected_items": selected_items,
            }, default=str)[:12000]},
        ], module="ads_builder", purpose="optimization_drafts", max_tokens=1400, temperature=0.2)
    except Exception:  # AIUnavailable plus a defensive fallback for optional Hub imports
        return fallback

    drafts = {"keywords": [], "sitelinks": [], "image_prompts": [], "reviews": [],
              "notes": [], "ai_used": True}
    allowed_groups = {row["ad_group_id"] for row in winning_terms if row["ad_group_id"]}
    allowed_campaigns = {google_ads.digits((c or {}).get("id")) for c in (context.get("campaigns") or [])[:20]}
    for row in (output.get("keywords") or [])[:5]:
        text = re.sub(r"\s+", " ", str((row or {}).get("text") or "").strip())[:80]
        gid = google_ads.digits((row or {}).get("ad_group_id"))
        camp = google_ads.digits((row or {}).get("campaign_id"))
        if text and gid and gid in allowed_groups and (not camp or camp in allowed_campaigns):
            drafts["keywords"].append({"text": text, "match_type": str((row or {}).get("match_type") or "EXACT").upper(),
                                       "campaign_id": camp, "ad_group_id": gid,
                                       "reason": str((row or {}).get("reason") or "AI draft")[:240]})
    for row in (output.get("sitelinks") or [])[:5]:
        camp = google_ads.digits((row or {}).get("campaign_id"))
        link_text = str((row or {}).get("link_text") or "").strip()[:25]
        if link_text and camp in allowed_campaigns:
            drafts["sitelinks"].append({
                "campaign_id": camp, "link_text": link_text,
                "description1": str((row or {}).get("description1") or "")[:35],
                "description2": str((row or {}).get("description2") or "")[:35],
                "final_url": google_ads.normalise_url((row or {}).get("final_url")),
                "reason": str((row or {}).get("reason") or "AI draft")[:240],
            })
    drafts["image_prompts"] = [{"prompt": str((row or {}).get("prompt") or "")[:700]}
                               for row in (output.get("image_prompts") or [])[:5]
                               if str((row or {}).get("prompt") or "").strip()]
    allowed_review_ids = {row["id"] for row in selected_items if row["id"]}
    for row in (output.get("reviews") or [])[:20]:
        review_id = str((row or {}).get("id") or "")[:120]
        if review_id not in allowed_review_ids:
            continue
        verdict = str((row or {}).get("verdict") or "hold").strip().lower()
        if verdict not in {"approve", "edit", "hold"}:
            verdict = "hold"
        drafts["reviews"].append({
            "id": review_id, "verdict": verdict,
            "suggested_text": re.sub(r"\s+", " ", str((row or {}).get("suggested_text") or "").strip())[:80],
            "suggested_match_type": str((row or {}).get("suggested_match_type") or "").upper()[:20],
            "rationale": str((row or {}).get("rationale") or "AI review completed.")[:500],
        })
    reviewed_ids = {row["id"] for row in drafts["reviews"]}
    for row in selected_items:
        if row["id"] and row["id"] not in reviewed_ids:
            drafts["reviews"].append({
                "id": row["id"], "verdict": "hold", "suggested_text": row["data"]["text"],
                "suggested_match_type": row["data"]["match_type"],
                "rationale": "AI did not return a review for this item; keep it on hold.",
            })
    drafts["notes"] = [str(n)[:300] for n in (output.get("notes") or [])[:6]]
    if not drafts["image_prompts"]:
        drafts["image_prompts"] = fallback["image_prompts"]
    if not drafts["keywords"]:
        drafts["keywords"] = fallback["keywords"]
    return drafts
