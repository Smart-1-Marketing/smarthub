"""Real cost-per-click, from Google's own planning services.

Until now every CPC on a Smart 1 Ads estimate came from ``campaign_ai.SECTOR_CPC``
-- twelve hand-written sector ranges, labelled *industry estimate* everywhere
they appear precisely because that is all they are. They are a reasonable
opening number and they are not this client's keywords in this client's
counties. This module is the other answer: Google's, for the keyword set we
actually built, in the areas we actually target.

Two services, and **they do not return the same number**::

    KeywordPlanIdeaService:generateKeywordIdeas
        per keyword: avgMonthlySearches, competition, and the TOP-OF-PAGE BID
        range -- lowTopOfPageBidMicros (20th percentile) and
        highTopOfPageBidMicros (80th). That is what you would have to BID to
        show at the top of the page. It is not what you PAY per click.

    KeywordPlanIdeaService:generateKeywordForecastMetrics
        for a whole campaign at a stated bid, budget, geography and network:
        clicks, impressions, costMicros and averageCpcMicros over a date range
        you name. *That* is a cost per click.

Reporting a top-of-page bid as an average CPC would overstate every estimate
this tool produces, by a margin that grows with how competitive the sector is,
and it would look exactly like a better number than the benchmark it replaced.
So the two are carried separately all the way to the screen, each with its own
label, and ``CPC_SOURCES`` names what each one actually measures.

**The access level is the whole story.** A developer token is not one thing:

    Test        test accounts only. No planning services, no production data.
    Explorer    the tier Google grants automatically now. Production accounts,
                2,880 operations a day -- and the planning services are
                *excluded*. generateKeywordIdeas returns
                DEVELOPER_TOKEN_NOT_APPROVED.
    Basic       15,000 operations a day, and the planning services work. This
                is the first tier at which anything in this file returns a
                number, and it is applied for and reviewed, not automatic.
    Standard    no daily cap.

So "we have the API key now" does not imply a measured CPC, and a tool that
assumed it did would answer DEVELOPER_TOKEN_NOT_APPROVED into a try/except and
quietly show the benchmark while a rep believed they were reading Google.
``planning_available()`` asks the question explicitly, the refusal is
translated into the tier that would fix it, and every number returned from here
carries ``source`` so no screen can print one without knowing which it has.

Nothing in this file is called during generation. It is one button, because a
keyword plan is up to a few hundred operations against a daily cap that a
deploy also has to fit inside -- and because a CPC that silently re-fetched on
every page load would change under a client mid-conversation.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from hub import target_areas

from . import google_ads, spec
from .google_ads import GoogleAdsError, digits, micros

# Google's own id for "United States". Used when a campaign's areas cannot be
# resolved to anything narrower, and named rather than hard-coded at the call
# site so the one place it is assumed is visible.
US_GEO_TARGET = "2840"
DEFAULT_LANGUAGE = "1000"          # English
KEYWORD_PLAN_NETWORK = "GOOGLE_SEARCH"      # not the search partner network

# How many keyword seeds one ideas call may carry. Google's own limit is 20
# seed keywords per request; a campaign here routinely has 120, so the seeds
# are the highest-intent slice rather than everything, and what was left out
# is reported rather than dropped in silence.
MAX_SEEDS = 20

# The forecast window. A month is what every other number on the estimate is
# expressed in, so the forecast is asked for one and the period it covers
# travels with the answer instead of being assumed by whatever renders it.
FORECAST_DAYS = 30

# What each number in this module actually measures lives in spec.py, with the
# rest of the module's vocabulary, and is imported rather than restated — the
# label on the screen and the call that produced the number under it must not
# be able to drift apart.
CPC_SOURCES = spec.CPC_SOURCES

# The access tiers, in the order Google grants them, with what each one leaves
# this module able to answer. Explorer is the tier a new token lands on.
ACCESS_TIERS = (
    ("test", "Test account access",
     "Test accounts only. No production data and no planning services."),
    ("explorer", "Explorer access",
     "Production accounts, 2,880 operations a day. Keyword planning is NOT "
     "included — measured CPC is unavailable at this tier."),
    ("basic", "Basic access",
     "15,000 operations a day, and the keyword planning services. This is the "
     "first tier at which a measured CPC is possible."),
    ("standard", "Standard access",
     "No daily operation cap."),
)

TIER_LABELS = {key: label for key, label, _ in ACCESS_TIERS}
TIER_NOTES = {key: note for key, _, note in ACCESS_TIERS}

# The error codes Google returns when the token is real but the tier is wrong.
# Distinguished from a bad token on purpose: "apply for Basic access" and
# "check the token you pasted" send somebody to two different places, and only
# one of them is a mistake.
NOT_APPROVED_CODES = {
    "DEVELOPER_TOKEN_NOT_APPROVED",
    "DEVELOPER_TOKEN_PROHIBITED",
}


class PlanningUnavailable(GoogleAdsError):
    """The call was refused for a reason a different key would not fix.

    Carries ``tier_needed`` so a page can say *apply for Basic access* rather
    than printing a Google error code at a rep who cannot act on one.
    """

    def __init__(self, message, *, tier_needed="basic", **kw):
        super().__init__(message, **kw)
        self.tier_needed = tier_needed


# ------------------------------------------------------------------ geography
def _area_query(area: dict) -> str:
    """The location text to ask Google to resolve, or "" if there is none.

    Deliberately conservative. A blank query matches everything, and a geo
    target we guessed at is a CPC measured somewhere the campaign does not run
    — which is worse than the benchmark it replaced, because it looks measured.
    """
    kind = area.get("type") or ""
    if kind == target_areas.NATIONAL:
        return ""                                   # handled by the US default
    for field in ("origin", "dma", "state", "other", "name"):
        value = str(area.get(field) or "").strip()
        if value:
            return value
    return ""


def geo_targets(areas, *, store=None, customer_id=None) -> dict:
    """Resolve a campaign's target areas to Google geo target constants.

    Returns the resolved constants **and the areas that resolved to nothing**,
    because a forecast run against three of a client's five counties is not
    this campaign's forecast and nothing else would say so. An area Google
    cannot place is named, never silently dropped and never widened to the
    state it sits in.
    """
    rows = target_areas.normalize(areas or [])
    resolved, unresolved, national = [], [], False

    for area in rows:
        if (area.get("type") or "") == target_areas.NATIONAL:
            national = True
            continue
        query = _area_query(area)
        if not query:
            unresolved.append(target_areas.label(area) or "(unnamed area)")
            continue
        try:
            found = suggest_geo_target(query, store=store, customer_id=customer_id)
        except PlanningUnavailable:
            raise
        except GoogleAdsError:
            found = None
        if found:
            resolved.append(found)
        else:
            unresolved.append(target_areas.label(area) or query)

    # Only where the campaign is genuinely national, or where it named no
    # geography at all. Never as a stand-in for an area that failed to
    # resolve: "we could not find Carmel" must not become "we priced the
    # United States".
    if national or (not resolved and not unresolved):
        resolved.append({"id": US_GEO_TARGET, "name": "United States",
                         "type": "Country"})

    seen, unique = set(), []
    for row in resolved:
        if row["id"] not in seen:
            seen.add(row["id"])
            unique.append(row)

    return {
        "targets": unique,
        "unresolved": unresolved,
        "complete": not unresolved,
        "resource_names": [f"geoTargetConstants/{r['id']}" for r in unique],
    }


def suggest_geo_target(query: str, *, store=None, customer_id=None):
    """The one geo target constant a location name can only mean, or None.

    Google returns its suggestions best-first, and taking the first is the
    ordinary use — but only when it is a place type a campaign targets. A
    query that comes back as a neighbourhood or a postal code when the rep
    typed a city is not the same area, so the accepted types are listed.
    """
    body = {"locationNames": {"names": [str(query or "").strip()[:120]]},
            "countryCode": "US", "locale": "en"}
    data = google_ads.request("post", "/geoTargetConstants:suggest", body,
                              store=store, customer_id=customer_id)
    for row in data.get("geoTargetConstantSuggestions") or []:
        constant = row.get("geoTargetConstant") or {}
        if (constant.get("status") or "ENABLED") != "ENABLED":
            continue
        name = constant.get("resourceName") or ""
        cid = name.rsplit("/", 1)[-1] if name else ""
        if not cid:
            continue
        return {"id": cid,
                "name": constant.get("name") or query,
                "type": constant.get("targetType") or ""}
    return None


# -------------------------------------------------------------- keyword seeds
def campaign_keywords(campaign: dict) -> list[str]:
    """Every keyword in the campaign, match-type markers stripped.

    The planning services want the term, not ``[the term]`` — a bracketed seed
    is treated as literal text and comes back with no volume at all, which
    reads on screen as a keyword nobody searches for.
    """
    out, seen = [], set()
    for group in campaign.get("adGroups") or []:
        for raw in group.get("keywords") or []:
            # parse_keyword returns a dict, or None for a blank. Unpacking it
            # as a pair yields its keys, so every keyword becomes the literal
            # string "text" — priced, charted and invisible.
            parsed = google_ads.parse_keyword(raw) or {}
            text = str(parsed.get("text") or "").strip().lower()
            if text and text not in seen:
                seen.add(text)
                out.append(text)
    return out


def _seeds(keywords: list[str]) -> dict:
    """The seeds one request may carry, and what was left out.

    Truncated rather than paged: the ideas call is priced per operation
    against a daily cap the deploy also needs, and twenty of a campaign's
    highest-intent terms price the campaign as well as a hundred do. What was
    left out is returned so the screen can say so.
    """
    kept = keywords[:MAX_SEEDS]
    return {"seeds": kept, "left_out": max(0, len(keywords) - len(kept)),
            "total": len(keywords)}


# ------------------------------------------------------------------ the calls
def _translate(exc: GoogleAdsError) -> GoogleAdsError:
    """A refusal that a different tier would fix, said in those words."""
    code = str(getattr(exc, "code", "") or "")
    if code in NOT_APPROVED_CODES or "NOT_APPROVED" in code:
        return PlanningUnavailable(
            "Google refused the keyword planning services for this developer "
            "token. That is the access tier, not the token: keyword planning "
            "needs Basic access, and a new token is granted Explorer access, "
            "which excludes it. Apply for Basic access in the manager account "
            "under Tools → API Center.",
            status=403, code=code or "DEVELOPER_TOKEN_NOT_APPROVED",
            tier_needed="basic", raw=getattr(exc, "raw", None))
    return exc


def keyword_ideas(customer_id, keywords, *, geo_resource_names=None,
                  language=DEFAULT_LANGUAGE, store=None) -> dict:
    """Search volume, competition and top-of-page bid range, per keyword.

    Returns only keywords Google answered for. A keyword it has no data on is
    counted and named rather than being carried at zero — a keyword with "0"
    beside it reads as a term nobody searches, and "we have no data for this"
    is a different statement.
    """
    cid = digits(customer_id)
    seeds = _seeds(list(keywords or []))
    if not seeds["seeds"]:
        return {"measured": False, "reason": "This campaign has no keywords yet.",
                "keywords": [], "seeds": seeds}

    body = {
        "keywordSeed": {"keywords": seeds["seeds"]},
        "geoTargetConstants": list(geo_resource_names or [f"geoTargetConstants/{US_GEO_TARGET}"]),
        "language": f"languageConstants/{language}",
        "keywordPlanNetwork": KEYWORD_PLAN_NETWORK,
        "includeAdultKeywords": False,
        "pageSize": 200,
    }
    try:
        data = google_ads.request(
            "post", f"/customers/{cid}:generateKeywordIdeas", body,
            store=store, customer_id=cid)
    except GoogleAdsError as exc:
        raise _translate(exc) from exc

    asked = {k.lower() for k in seeds["seeds"]}
    rows, no_data = [], []
    for result in data.get("results") or []:
        text = (result.get("text") or "").strip()
        metrics = result.get("keywordIdeaMetrics") or {}
        low = micros(metrics.get("lowTopOfPageBidMicros"))
        high = micros(metrics.get("highTopOfPageBidMicros"))
        searches = metrics.get("avgMonthlySearches")
        if not low and not high and searches in (None, "", 0):
            if text.lower() in asked:
                no_data.append(text)
            continue
        rows.append({
            "keyword": text,
            "in_campaign": text.lower() in asked,
            # None, never 0 — "Google has no volume for this" and "nobody
            # searches this" are different answers and only one is measured.
            "monthly_searches": int(searches) if searches not in (None, "") else None,
            "competition": metrics.get("competition") or "",
            "competition_index": metrics.get("competitionIndex"),
            "bid_low": round(low, 2) if low else None,
            "bid_high": round(high, 2) if high else None,
        })

    rows.sort(key=lambda r: (not r["in_campaign"], -(r["monthly_searches"] or 0)))
    return {
        "measured": bool(rows),
        "keywords": rows,
        "no_data": no_data,
        "seeds": seeds,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _forecast_period(days: int = FORECAST_DAYS) -> dict:
    """A date range starting tomorrow. Google refuses a period in the past."""
    start = date.today() + timedelta(days=1)
    return {"dateInterval": {"startDate": start.isoformat(),
                             "endDate": (start + timedelta(days=days - 1)).isoformat()}}


def forecast(customer_id, campaign: dict, *, geo_resource_names=None,
             max_cpc=None, daily_budget=None, language=DEFAULT_LANGUAGE,
             store=None) -> dict:
    """Google's forecast for this campaign: clicks, cost and an average CPC.

    This is the number worth putting on an estimate, and the only one in this
    module that is genuinely a cost per click. It is a forecast, so it is
    labelled as one everywhere it lands.
    """
    cid = digits(customer_id)
    keywords = campaign_keywords(campaign)
    if not keywords:
        return {"measured": False, "reason": "This campaign has no keywords yet."}

    bid = float(max_cpc or 0)
    if bid <= 0:
        # Google needs a bid to forecast against. Absent an explicit one the
        # sector's mid-point is used and *said*, rather than a number being
        # picked silently — the forecast is only as meaningful as the bid it
        # assumed.
        from .campaign_ai import SECTOR_CPC
        sector = SECTOR_CPC.get(campaign.get("sectorKey") or "general") or SECTOR_CPC["general"]
        bid = (sector["low"] + sector["high"]) / 2
        bid_source = f"the {sector['label']} sector mid-point (no max CPC set)"
    else:
        bid_source = "the max CPC on this campaign"

    monthly = float(campaign.get("monthlyBudget") or 0)
    daily = float(daily_budget or 0) or (monthly / 30.4 if monthly else bid * 10)

    body = {
        "campaign": {
            "keywordPlanNetwork": KEYWORD_PLAN_NETWORK,
            "biddingStrategy": {
                "manualCpcBiddingStrategy": {
                    "maxCpcBidMicros": str(int(round(bid * 1_000_000))),
                    "dailyBudgetMicros": str(int(round(daily * 1_000_000))),
                }
            },
            "geoModifiers": [
                {"geoTargetConstant": name}
                for name in (geo_resource_names or [f"geoTargetConstants/{US_GEO_TARGET}"])
            ],
            "languageConstants": [f"languageConstants/{language}"],
            "adGroups": [{
                "maxCpcBidMicros": str(int(round(bid * 1_000_000))),
                "biddableKeywords": [
                    {"keyword": {"text": text, "matchType": "PHRASE"}}
                    for text in keywords[:1000]
                ],
            }],
        },
        "forecastPeriod": _forecast_period(),
    }

    try:
        data = google_ads.request(
            "post", f"/customers/{cid}:generateKeywordForecastMetrics", body,
            store=store, customer_id=cid)
    except GoogleAdsError as exc:
        raise _translate(exc) from exc

    m = data.get("campaignForecastMetrics") or {}
    avg_cpc = micros(m.get("averageCpcMicros"))
    cost = micros(m.get("costMicros"))
    clicks = float(m.get("clicks") or 0)
    period = _forecast_period()["dateInterval"]

    return {
        "measured": bool(avg_cpc or clicks),
        "avg_cpc": round(avg_cpc, 2) if avg_cpc else None,
        "clicks": round(clicks) if clicks else None,
        "impressions": round(float(m.get("impressions") or 0)) or None,
        "cost": round(cost, 2) if cost else None,
        "ctr": m.get("clickThroughRate"),
        "bid_assumed": round(bid, 2),
        "bid_source": bid_source,
        "daily_budget_assumed": round(daily, 2),
        "keywords_forecast": len(keywords),
        # The window the numbers cover, carried rather than assumed: a screen
        # that prints a 30-day forecast under a "per month" heading is a
        # number nobody chose.
        "period": {"start": period["startDate"], "end": period["endDate"],
                   "days": FORECAST_DAYS},
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ------------------------------------------------------------------ the block
def measure(customer_id, campaign: dict, *, store=None) -> dict:
    """Everything Google will tell us about this campaign's cost, in one call.

    The shape written onto the campaign JSON as ``cpcMeasured``. It always
    answers: where planning is unavailable it says which tier would fix it and
    the estimate goes on showing the benchmark, labelled as the benchmark. It
    never returns a number without ``source``.
    """
    out = {
        "measured": False,
        "source": "benchmark",
        "reason": "",
        "tier_needed": "",
        "customer_id": google_ads.format_customer_id(customer_id),
        "at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        geo = geo_targets(campaign.get("targetAreas"), store=store,
                          customer_id=customer_id)
    except PlanningUnavailable as exc:
        return {**out, "reason": exc.message, "tier_needed": exc.tier_needed}
    except GoogleAdsError as exc:
        return {**out, "reason": f"Could not resolve the target areas with "
                                 f"Google: {exc.message}"}

    out["geo"] = geo
    names = geo["resource_names"]

    try:
        ideas = keyword_ideas(customer_id, campaign_keywords(campaign),
                              geo_resource_names=names, store=store)
    except PlanningUnavailable as exc:
        return {**out, "reason": exc.message, "tier_needed": exc.tier_needed}
    except GoogleAdsError as exc:
        return {**out, "reason": f"Google refused the keyword ideas request: {exc.message}"}

    out["ideas"] = ideas
    bids = [r["bid_high"] or r["bid_low"] for r in ideas.get("keywords") or []
            if (r["bid_high"] or r["bid_low"])]
    lows = [r["bid_low"] for r in ideas.get("keywords") or [] if r["bid_low"]]
    if bids:
        out["top_of_page_bid"] = {
            "low": round(sum(lows) / len(lows), 2) if lows else None,
            "high": round(sum(bids) / len(bids), 2),
            "keywords": len(bids),
        }

    # The forecast is the one that yields a cost per click, so a failure here
    # is not fatal — the bid range above is still a real, measured answer and
    # is better than the benchmark for sizing a bid.
    try:
        fc = forecast(customer_id, campaign, geo_resource_names=names, store=store)
        out["forecast"] = fc
        if fc.get("avg_cpc"):
            out.update(measured=True, source="forecast", cpc=fc["avg_cpc"])
    except PlanningUnavailable as exc:
        out["forecast"] = {"measured": False, "reason": exc.message}
        out["tier_needed"] = exc.tier_needed
    except GoogleAdsError as exc:
        out["forecast"] = {"measured": False, "reason": exc.message}

    if not out["measured"] and out.get("top_of_page_bid", {}).get("high"):
        out.update(measured=True, source="top_of_page_bid",
                   cpc=out["top_of_page_bid"]["high"])

    if not out["measured"] and not out["reason"]:
        out["reason"] = ("Google returned no cost data for these keywords in "
                         "these areas. That is not measured, not zero.")
    return out


def summary_line(measured: dict) -> str:
    """One sentence naming what the number on screen actually is."""
    if not measured or not measured.get("measured"):
        return CPC_SOURCES["benchmark"]["long"]
    return CPC_SOURCES.get(measured.get("source"), CPC_SOURCES["benchmark"])["long"]


def planning_available(store=None) -> dict:
    """Whether the keyword planning services answer for this token.

    One cheap call, behind a button — never on page load. The four outcomes
    are deliberately distinct: a refusal names the tier that fixes it, an
    unreachable Google is not a bad token, and no token at all is *not
    measured* rather than a cross.
    """
    status = google_ads.connection_status(store)
    if not status["configured"]:
        return {"available": False, "state": "not_configured",
                "detail": "No developer token is set on this deployment.",
                "missing": status["missing"]}
    if not status["connected"]:
        return {"available": False, "state": "not_connected",
                "detail": "Google Ads is not authorised yet — connect it in Settings."}

    try:
        customers = google_ads.list_accessible_customers(store)
    except GoogleAdsError as exc:
        return {"available": False, "state": "unreachable",
                "detail": f"Could not reach Google Ads: {exc.message}"}
    if not customers:
        return {"available": False, "state": "no_accounts",
                "detail": "This login can reach no Google Ads accounts."}

    cid = digits(customers[0])          # already digits-only from google_ads
    try:
        keyword_ideas(cid, ["plumber"], store=store)
    except PlanningUnavailable as exc:
        return {"available": False, "state": "tier_too_low",
                "tier_needed": exc.tier_needed, "detail": exc.message}
    except GoogleAdsError as exc:
        return {"available": False, "state": "refused", "detail": exc.message}
    return {"available": True, "state": "ok",
            "detail": "Keyword planning answered — measured CPC is available.",
            "checked_with": google_ads.format_customer_id(cid)}
