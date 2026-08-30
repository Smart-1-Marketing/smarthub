"""AI campaign generator and budget viability engine.

Calls OpenAI over plain ``requests`` so the module adds no new dependency to
the Hub — it reuses OPENAI_API_KEY / OPENAI_MODEL exactly like the SEO, FAQ
and proposal tools do.

**The key is the Hub's and is never asked for.** The generator used to carry an
"OpenAI key override" box, which is the wrong question in two directions: it
invites a key from outside the deployment into a form post, and its presence
reads as "this page needs a key from me" on a Hub that has had one set all
along. The key is read through ``hub.config`` at call time — never off
``os.environ`` at import — so a spelling the deployment actually uses is picked
up wherever it is fixed, once. See the provider-key trap in CLAUDE.md: a module
reading one spelling directly is how a key that is set is still not a key that
is read.
"""
from __future__ import annotations

import json
import os

import requests

from hub import target_areas

from . import spec

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT = 180


class GenerationError(Exception):
    pass


# Rough US search CPC benchmarks, USD. These drive the honest warning on the
# budget slider before a single token is spent on generation.
SECTOR_CPC = {
    "legal":        {"low": 8.0,  "high": 90.0, "label": "Legal / Attorney"},
    "insurance":    {"low": 12.0, "high": 65.0, "label": "Insurance"},
    "medical":      {"low": 4.0,  "high": 30.0, "label": "Medical / Dental"},
    "homeservices": {"low": 6.0,  "high": 35.0, "label": "Home Services / Trades"},
    "b2bsaas":      {"low": 5.0,  "high": 45.0, "label": "B2B SaaS / Software"},
    "finance":      {"low": 7.0,  "high": 55.0, "label": "Finance / Lending"},
    "realestate":   {"low": 3.0,  "high": 18.0, "label": "Real Estate"},
    "ecommerce":    {"low": 0.8,  "high": 4.0,  "label": "Ecommerce / Retail"},
    "education":    {"low": 3.0,  "high": 25.0, "label": "Education / Training"},
    "automotive":   {"low": 2.0,  "high": 12.0, "label": "Automotive"},
    "travel":       {"low": 1.2,  "high": 6.0,  "label": "Travel / Hospitality"},
    "general":      {"low": 2.0,  "high": 12.0, "label": "General / Other"},
}


def analyse_budget(monthly_budget, sector_key="general", *,
                   cpc=None, cpc_source="benchmark") -> dict:
    """Below ~100 clicks/month you cannot optimise; below ~30 you are donating.

    ``cpc`` overrides the sector mid-point with a cost per click somebody
    measured — Google's forecast for this keyword set, via
    ``modules/ads_builder/keyword_plan.py``. The arithmetic is identical; what
    changes is the number it runs on and, crucially, the caveat that comes back
    with it. ``cpc_source`` decides that caveat and is carried rather than
    inferred, because a screen printing a measured number under the words
    "industry estimate" is the same wrong answer as the reverse.
    """
    sector = SECTOR_CPC.get(sector_key) or SECTOR_CPC["general"]
    try:
        budget = float(monthly_budget or 0)
    except (TypeError, ValueError):
        budget = 0.0

    try:
        measured_cpc = float(cpc or 0)
    except (TypeError, ValueError):
        measured_cpc = 0.0
    if measured_cpc <= 0:
        measured_cpc, cpc_source = 0.0, "benchmark"

    mid_cpc = measured_cpc or (sector["low"] + sector["high"]) / 2
    clicks = budget / mid_cpc if mid_cpc else 0
    # The pessimistic case still runs on the sector ceiling. A measured CPC is
    # one number, so it cannot describe its own worst case, and dropping the
    # row would quietly remove the only downside figure on the page.
    worst_case = budget / sector["high"] if sector["high"] else 0

    if clicks < 30:
        status = "CRITICAL"
        advice = (
            f"At roughly ${mid_cpc:.2f} per click in {sector['label']}, ${budget:,.0f}/mo buys "
            f"about {clicks:.0f} clicks. That is not enough traffic for Google to optimize or for "
            f"you to read the data. Either raise the budget, or cut scope hard: one tight "
            f"exact-match ad group, a small radius, business-hours-only scheduling."
        )
    elif clicks < 100:
        status = "WARN"
        advice = (
            f"${budget:,.0f}/mo lands around {clicks:.0f} clicks at a ${mid_cpc:.2f} CPC. Workable, "
            f"but thin. Keep to 1-2 ad groups, lean on exact and phrase match, and expect 6-8 weeks "
            f"before conversion data means anything."
        )
    else:
        status = "HEALTHY"
        advice = (
            f"${budget:,.0f}/mo supports roughly {clicks:.0f} clicks at a ${mid_cpc:.2f} CPC in "
            f"{sector['label']}. Enough headroom for 2-3 themed ad groups and a real testing cadence."
        )

    return {
        "status": status,
        "advice": advice,
        "sector": sector["label"],
        "sector_key": sector_key if sector_key in SECTOR_CPC else "general",
        "cpc_low": sector["low"],
        "cpc_high": sector["high"],
        "estimated_clicks": round(clicks),
        "worst_case_clicks": round(worst_case),
        "recommended_minimum": round(mid_cpc * 100),
        # Travels with the numbers rather than being added by each screen: the
        # CPCs above are sector benchmarks, and every place that prints one has
        # to say so. test_ads_estimate.py asserts the templates carry it.
        # Travels with the numbers rather than being added by each screen, and
        # names WHICH of the three CPCs this is. test_ads_estimate.py asserts
        # every template that prints one carries the words for it.
        "cpc_source": cpc_source,
        "cpc_used": round(mid_cpc, 2),
        "cpc_note": spec.CPC_SOURCES[cpc_source]["short"],
        "cpc_note_long": spec.CPC_SOURCES[cpc_source]["long"],
    }


SYSTEM_PROMPT = """You are an elite paid search strategist building Google Ads search campaigns.

RULES
1. Produce 2 to 3 distinct, tightly themed ad groups. Never mix intents in one group.
2. Every ad group must contain between 20 and 50 keywords.
3. Tag every keyword's match type by wrapping it: [exact match] or "phrase match", or leave it
   bare for broad. Lean heavily on phrase and exact. Broad only for genuine discovery terms.
4. Every ad group needs responsive search ad copy: at least 5 headlines (max 30 characters each,
   count them) and at least 3 descriptions (max 90 characters each).
5. At least 4 sitelinks. Titles max 25 characters. Each sitelink must have BOTH description lines
   or NEITHER, each max 35 characters.
6. At least 6 callouts, max 25 characters each.
7. Structured snippets with a valid header and at least 4 values, max 25 characters each.
8. A categorized negative keyword vault, specific to this business. Be thorough.
9. Cost estimates grounded in real CPC ranges for the sector, not optimistic ones.

Respond with pure JSON only, matching this structure exactly:
{
  "businessName": "string",
  "websiteUrl": "string",
  "monthlyBudget": 0,
  "strategySummary": "2-3 sentences on the approach and why",
  "costEstimation": {
    "estimatedMonthlyCost": 0, "avgCPC": 0, "estimatedMonthlyClicks": 0,
    "estimatedConversionRate": 0, "estimatedConversions": 0, "estimatedCPA": 0
  },
  "landingPageAnalysis": {
    "ctaReadiness": "High | Medium | Low",
    "messageMatch": "string",
    "recommendations": ["string"]
  },
  "adGroups": [
    {
      "name": "string", "theme": "string", "avgCPC": 0,
      "keywords": ["[exact term]", "\\"phrase term\\"", "broad term"],
      "ads": { "headlines": ["max 30 chars"], "descriptions": ["max 90 chars"] }
    }
  ],
  "adAssets": {
    "sitelinks": [{"title": "max 25", "desc1": "max 35", "desc2": "max 35", "url": "https://..."}],
    "callouts": ["max 25 chars"],
    "structuredSnippets": {"header": "Services", "values": ["max 25 chars"]}
  },
  "negativeKeywordVault": {
    "freeCheap": ["string"], "jobsCareers": ["string"],
    "educational": ["string"], "irrelevant": ["string"]
  }
}"""


def openai_key() -> str:
    """The Hub's key, read at call time through the shared settings."""
    try:
        from hub.config import settings
        key = (settings.openai_key or "").strip()
        if key:
            return key
    except Exception:  # noqa: BLE001 — the module stays runnable outside the Hub
        pass
    return os.environ.get("OPENAI_API_KEY", "").strip()


def openai_model() -> str:
    try:
        from hub.config import settings
        return settings.openai_model or "gpt-4o-mini"
    except Exception:  # noqa: BLE001
        return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def _chat(system: str, user: str, *, purpose: str, model: str = None,
          max_tokens: int = 8000, temperature: float = 0.7) -> dict:
    """One JSON call to OpenAI, with the failure modes named.

    Every AI feature in this module goes through here — generation, the landing
    page read, competitor research, the budget tiers and the re-check after an
    edit — so retry, cost recording and "the model returned prose" are handled
    once rather than five times differently.
    """
    key = openai_key()
    if not key:
        raise GenerationError(
            "No OpenAI API key on this deployment. Set OPENAI_API_KEY on the Hub service — "
            "the generator uses the Hub's key and does not accept one from the browser."
        )

    resp = requests.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model or openai_model(),
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=TIMEOUT,
    )

    if not resp.ok:
        try:
            detail = resp.json()["error"]["message"]
        except Exception:  # noqa: BLE001
            detail = resp.text[:400]
        raise GenerationError(f"OpenAI rejected the request: {detail}")

    try:  # record spend so /diagnostics doesn't under-report
        from hub import ai as _hub_ai
        _hub_ai.note_usage("ads_builder", resp.json(), purpose=purpose)
    except Exception:  # noqa: BLE001
        pass

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise GenerationError("OpenAI returned an unexpected response shape.")

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise GenerationError("The model returned malformed JSON. Try again.")


def generate_campaign(payload: dict, model: str = None, *,
                      observed_page: dict = None) -> dict:

    viability = analyse_budget(payload.get("budget"), payload.get("sector") or "general")
    intake = spec.normalise_intake(payload)
    areas = target_areas.normalize(payload.get("targetAreas") or [])

    budget = _num(payload.get("budget"))
    budget_line = (f"Monthly budget: ${budget:,.0f}" if budget > 0 else
                   "Monthly budget: NOT SET — the client has not named one. Build the "
                   "campaign at the 'better' tier you recommend below, and say in the "
                   "strategy what it assumes.")

    geography = target_areas.for_prompt(areas) or payload.get("geography") or "not specified"
    area_rule = ("\n\nThis campaign runs in SEVERAL named target areas. Treat them as "
                 "separate places, not one region: local intent language differs per area, "
                 "and a keyword set written for a merged region matches none of them well."
                 if len(areas) > 1 else "")

    page_block = _page_block(observed_page)
    intake_block = spec.for_prompt({"intake": intake})
    client_block = _client_block(payload.get("businessName", ""),
                                 payload.get("websiteUrl", ""))

    user_prompt = f"""Build a Google Ads search campaign for:

Business name: {payload.get('businessName', '')}
Website / landing page: {payload.get('websiteUrl', '')}
Sector: {viability['sector']}
Primary objective: {payload.get('objective', '')}
{budget_line}
Target audience: {payload.get('targetAudience') or 'not specified'}
Target areas: {geography}{area_rule}
{('Additional context: ' + payload['notes']) if payload.get('notes') else ''}

WHAT THE REP ASKED THE CLIENT — build around these, do not restate them back:
{intake_block or '- Nothing further was captured.'}
{client_block}{page_block}
Independent budget check already run (use it, do not contradict it):
{viability['status']} — {viability['advice']}
Typical CPC range for this sector: ${viability['cpc_low']} to ${viability['cpc_high']}.
These CPCs are industry estimates for the sector, not measured costs for this
account — never present them as this client's actual cost per click.

Remember: 20 to 50 keywords in EVERY ad group, with match types tagged."""

    data = _chat(SYSTEM_PROMPT, user_prompt, purpose="campaign", model=model)
    campaign = normalise(data, payload, viability)
    campaign["intake"] = intake
    campaign["targetAreas"] = areas
    return campaign


def _client_block(business: str, url: str) -> str:
    """What the Hub already holds about this client, as facts for the model.

    The form asks a rep for a business name, a URL and a sector. Everything
    else about a client we have had for years — the industry on their Knack
    record, the city they trade in, the palette their own site paints, the
    products already running with us — was on file and reached the model
    never. What comes back then is plausible and generic, which on a keyword
    set is the hardest kind of wrong to notice: every term is a real term, and
    none of them is about this business.

    `hub/client_context.for_prompt()` is the one reader, so a fact added to it
    reaches every AI feature rather than this one. It carries what is *not* on
    file too, and says not to invent it — a gap a model cannot see is a gap it
    fills in, which is the rule `hub/social_plan.py` enforces one step later
    by flagging any claim a human did not supply.
    """
    try:
        from hub.client_context import for_prompt
        block = for_prompt(business, url)
    except Exception:                                   # noqa: BLE001
        return ""
    return f"\nWHAT WE ALREADY HOLD ON THIS CLIENT:\n{block}\n" if block else ""


def _page_block(observed: dict) -> str:
    """The landing page as FACTS, not as a URL for the model to imagine.

    A model handed only a URL writes confident recommendations about a hero it
    has never seen, and the rep repeats them to the client. What goes in here
    is what was actually read off the markup; where nothing was read, it says
    so, and the model is told not to describe the page at all.
    """
    if not observed:
        return ""
    if not observed.get("measured"):
        return (f"\nLANDING PAGE: could not be read ({observed.get('error') or 'no reason given'}). "
                f"Say so. Do NOT describe the page, its layout or its copy — you have "
                f"not seen it. Set landingPageAnalysis.ctaReadiness to \"Unknown\".\n")

    points = observed.get("conversion_points") or []
    lines = [f"  - {p['label']}: {p['evidence']}" for p in points[:25]] or \
            ["  - NONE FOUND. There is no form, no click-to-call, no booking tool and "
             "no chat widget on this page."]
    return (
        "\nLANDING PAGE — read from the page itself, these are facts:\n"
        f"  URL: {observed.get('url')}\n"
        f"  Title: {observed.get('title') or '(none)'}\n"
        f"  Meta description: {observed.get('meta_description') or '(none)'}\n"
        f"  Mobile viewport declared: {'yes' if observed.get('mobile_viewport') else 'no'}\n"
        "  Conversion points found:\n" + "\n".join(lines) + "\n"
        f"  Headings: {'; '.join(h['text'] for h in (observed.get('headings') or [])[:12]) or '(none)'}\n"
        f"  Page text (truncated): {(observed.get('text') or '')[:2500]}\n"
        "Base every landing-page statement on the above. If something is not in it, "
        "you did not observe it — say what you would need to check rather than "
        "asserting it.\n"
    )


PAGE_SYSTEM = """You are a paid search strategist reviewing a landing page that has already
been fetched and parsed for you. You are given the facts read off the page. Judge them.

RULES
1. Never assert anything about the page that is not in the facts given. If you need
   something you were not given, name it as "worth checking" rather than stating it.
2. Separate what the page must fix from what the CAMPAIGN must do about the page as it
   is today. The second is the useful half: a campaign has to run against the page that
   exists, not the one somebody might build.
3. A conversion action the client wants that the page cannot support is the most
   important finding there is. Say it first and say it plainly.
4. Be specific and short. "Add a click-to-call button above the fold on mobile" beats
   "improve the mobile experience".

Respond with pure JSON only:
{
  "ctaReadiness": "High | Medium | Low | Unknown",
  "messageMatch": "one or two sentences on whether the page matches the search intent",
  "conversionPoints": [{"what": "string", "where": "string", "strength": "Strong | Weak"}],
  "gaps": [{"what": "string", "impact": "what it costs the campaign"}],
  "pageRecommendations": ["string"],
  "campaignRecommendations": ["string"]
}"""


def analyse_landing_page(campaign: dict, observed: dict, model: str = None) -> dict:
    """The model's judgment on a page somebody actually fetched.

    The observed half is returned alongside it, unchanged, so the screen can
    keep "we found this on the page" apart from "a model thinks this" — they
    are different kinds of claim and a client asks which is which.
    """
    intake = (campaign or {}).get("intake") or {}
    wanted = spec.conversion_labels(intake.get("conversionActions"))

    user = f"""Business: {campaign.get('businessName', '')}
Sector: {campaign.get('sector', '')}
What they sell: {intake.get('productOrService') or 'not specified'}
Audience: {intake.get('audienceType') or 'not specified'}
The client counts these as a result: {', '.join(wanted) or 'not specified'}
Promotion running: {intake.get('promotion') or 'none'}
{_page_block(observed)}"""

    data = _chat(PAGE_SYSTEM, user, purpose="landing_page", model=model,
                 max_tokens=2500, temperature=0.4)

    return {
        "ctaReadiness": _trunc(data.get("ctaReadiness"), 20) or "Unknown",
        "messageMatch": _trunc(data.get("messageMatch"), 600),
        "conversionPoints": [
            {"what": _trunc(x.get("what"), 160), "where": _trunc(x.get("where"), 160),
             "strength": _trunc(x.get("strength"), 10) or "Weak"}
            for x in (data.get("conversionPoints") or [])[:15] if isinstance(x, dict)
        ],
        "gaps": [
            {"what": _trunc(x.get("what"), 200), "impact": _trunc(x.get("impact"), 300)}
            for x in (data.get("gaps") or [])[:12] if isinstance(x, dict)
        ],
        "pageRecommendations": _dedupe(data.get("pageRecommendations"), 300)[:12],
        "campaignRecommendations": _dedupe(data.get("campaignRecommendations"), 300)[:12],
        "observed": observed,
    }


COMPETITOR_SYSTEM = """You research the competitive set for a paid search campaign.

RULES
1. The client named some competitors. Repeat those back under "named", exactly as given.
2. Add others you have reason to believe compete for the same searches in this sector and
   geography, under "researched". These are YOUR suggestion and will be shown to a person
   as unverified — so include why you think each one competes.
3. Never invent a specific business you are not reasonably confident exists. A national
   chain or a category ("the two national franchises that advertise on TV") is a safer
   and more useful answer than a plausible-sounding local name you made up.
4. Say what the competitive set means for the campaign: where bidding will be expensive,
   what positioning is available, which comparison terms are worth owning.

Respond with pure JSON only:
{
  "named": [{"name": "string", "note": "what the client said or implied"}],
  "researched": [{"name": "string", "why": "why they compete for these searches",
                  "confidence": "High | Medium | Low"}],
  "implications": ["string"],
  "brandTermAdvice": "one or two sentences on bidding competitor brand terms here"
}"""


def research_competitors(campaign: dict, model: str = None) -> dict:
    intake = (campaign or {}).get("intake") or {}
    areas = target_areas.for_prompt(campaign.get("targetAreas") or [])
    user = f"""Business: {campaign.get('businessName', '')}
Website: {campaign.get('websiteUrl', '')}
Sector: {campaign.get('sector', '')}
What they sell: {intake.get('productOrService') or 'not specified'}
Audience: {intake.get('audienceType') or 'not specified'}
Target areas: {areas or campaign.get('geography') or 'not specified'}
Locally owned: {'yes' if intake.get('locallyOwned') else 'not stated'}
Competitors the CLIENT named: {intake.get('competitors') or 'none given'}"""

    data = _chat(COMPETITOR_SYSTEM, user, purpose="competitors", model=model,
                 max_tokens=2000, temperature=0.5)
    return {
        "named": [{"name": _trunc(x.get("name"), 120), "note": _trunc(x.get("note"), 300)}
                  for x in (data.get("named") or [])[:20] if isinstance(x, dict) and x.get("name")],
        "researched": [{"name": _trunc(x.get("name"), 120), "why": _trunc(x.get("why"), 300),
                        "confidence": _trunc(x.get("confidence"), 10) or "Low"}
                       for x in (data.get("researched") or [])[:20]
                       if isinstance(x, dict) and x.get("name")],
        "implications": _dedupe(data.get("implications"), 300)[:10],
        "brandTermAdvice": _trunc(data.get("brandTermAdvice"), 500),
        # Said on every screen that shows this: the researched half is the
        # model's, not the client's, and nobody has checked it.
        "note": "Names under “our research” are the model's suggestion and have not been "
                "verified. Check them before repeating them to the client.",
    }


TIER_SYSTEM = """You size Google Ads search budgets into three tiers a client can choose between.

RULES
1. Ground every tier in the sector CPC range you are given. A tier that cannot buy enough
   clicks to be optimized is not a tier — say so rather than offering it.
2. Below roughly 30 clicks a month a campaign cannot be read at all. Never present a
   budget under that threshold as workable.
3. Say what each tier BUYS and what it gives up, in concrete terms: how many ad groups,
   which match types, how much of the keyword set, whether there is room to test.
4. Recommend exactly one tier and say why that one.

Respond with pure JSON only:
{
  "tiers": [
    {"key": "good | better | best", "monthly": 0, "estimatedClicks": 0,
     "buys": "what this budget covers", "givesUp": "what it does not cover",
     "adGroups": 0, "recommended": true}
  ],
  "rationale": "two or three sentences on how these were sized",
  "floorWarning": "empty string, or a warning if even the smallest tier is too thin"
}"""


def measured_cpc(campaign: dict) -> dict:
    """The measured CPC on a campaign as keyword arguments for analyse_budget.

    Empty when nothing has been measured, so every call site can splat it
    unconditionally rather than branching — and so the default stays the
    benchmark rather than becoming whatever a caller forgot to pass.
    """
    block = (campaign or {}).get("cpcMeasured") or {}
    if not block.get("measured") or not block.get("cpc"):
        return {}
    return {"cpc": block["cpc"], "cpc_source": block.get("source") or "benchmark"}


def retier(campaign: dict) -> dict:
    """Recompute the existing tiers' click estimates against a new CPC.

    Measuring a CPC has to change the tiers, or the page contradicts itself:
    a headline saying $12.40 measured over three tiers costed at the sector's
    $19.00 shows a client two different campaigns. Recomputing rather than
    re-asking the model keeps every tier's *wording* — which a rep may have
    edited and a client may already have read — and changes only the
    arithmetic, which is ours.
    """
    tiers = ((campaign or {}).get("budgetTiers") or {}).get("tiers") or []
    if not tiers:
        return (campaign or {}).get("budgetTiers") or {}
    sector_key = campaign.get("sectorKey") or "general"
    measured = measured_cpc(campaign)
    out = []
    for tier in tiers:
        check = analyse_budget(tier.get("monthly"), sector_key, **measured)
        out.append({**tier,
                    "estimatedClicks": check["estimated_clicks"],
                    "status": check["status"],
                    "belowFloor": check["estimated_clicks"] < spec.MIN_READABLE_CLICKS})
    reference = analyse_budget(campaign.get("monthlyBudget"), sector_key, **measured)
    return {**(campaign.get("budgetTiers") or {}), "tiers": out,
            "cpcSource": reference["cpc_source"],
            "cpcUsed": reference["cpc_used"],
            "cpcNote": reference["cpc_note_long"]}


def budget_tiers(campaign: dict, sector_key: str = "general", model: str = None) -> dict:
    """Good / better / best, offered whether or not the client named a budget.

    Asked for both cases on purpose: with no budget it is the only way to open
    the conversation, and with one it is how a rep shows what the next step up
    would buy. The known budget is passed in so the tiers are anchored to it
    rather than to a number nobody discussed.
    """
    stated = _num((campaign or {}).get("monthlyBudget"))
    measured = measured_cpc(campaign)
    viability = analyse_budget(stated, sector_key, **measured)
    intake = (campaign or {}).get("intake") or {}

    user = f"""Business: {campaign.get('businessName', '')}
Sector: {viability['sector']}
Typical CPC range for the sector: ${viability['cpc_low']} to ${viability['cpc_high']} (industry estimate)
Target areas: {target_areas.for_prompt(campaign.get('targetAreas') or []) or 'not specified'}
What they sell: {intake.get('productOrService') or 'not specified'}
They count these as a result: {', '.join(spec.conversion_labels(intake.get('conversionActions'))) or 'not specified'}
Budget the client has named: {('$%s/month' % format(stated, ',.0f')) if stated > 0 else 'NONE — they do not know yet'}
Independent budget check on the stated budget: {viability['status']} — {viability['advice']}
A campaign needs roughly {spec.MIN_READABLE_CLICKS} clicks a month before its data means anything."""

    data = _chat(TIER_SYSTEM, user, purpose="budget_tiers", model=model,
                 max_tokens=1800, temperature=0.4)

    tiers, seen = [], set()
    for row in (data.get("tiers") or []):
        if not isinstance(row, dict):
            continue
        key = str(row.get("key", "")).strip().lower()
        if key not in spec.TIERS or key in seen:
            continue
        seen.add(key)
        monthly = _num(row.get("monthly"))
        # The click estimate is recomputed here rather than trusted: it is the
        # number a client checks the tier against, and a model that rounds it
        # generously makes the cheapest tier look workable when it is not.
        check = analyse_budget(monthly, sector_key, **measured)
        tiers.append({
            "key": key,
            "label": spec.TIER_LABELS[key],
            "monthly": round(monthly),
            "estimatedClicks": check["estimated_clicks"],
            "status": check["status"],
            "buys": _trunc(row.get("buys"), 400),
            "givesUp": _trunc(row.get("givesUp"), 400),
            "adGroups": int(_num(row.get("adGroups"))) or 0,
            "recommended": bool(row.get("recommended")),
            "blurb": spec.TIER_BLURB[key],
            "belowFloor": check["estimated_clicks"] < spec.MIN_READABLE_CLICKS,
        })
    tiers.sort(key=lambda t: spec.TIERS.index(t["key"]))

    if tiers and not any(t["recommended"] for t in tiers):
        # Never leave a three-way choice with nothing recommended: the middle
        # tier is the one the wording is built around.
        (next((t for t in tiers if t["key"] == "better"), tiers[0]))["recommended"] = True

    return {
        "tiers": tiers,
        "rationale": _trunc(data.get("rationale"), 800),
        "floorWarning": _trunc(data.get("floorWarning"), 500),
        "statedBudget": round(stated),
        "sector": viability["sector"],
        "cpcSource": viability["cpc_source"],
        "cpcUsed": viability["cpc_used"],
        "cpcNote": viability["cpc_note_long"],
    }


RECHECK_SYSTEM = """You re-check a Google Ads search campaign after a human edited it.

You are given the campaign as it now stands and a list of what changed. The campaign was
coherent before the edit. Your job is to find where it is no longer coherent, and nothing
else — do not rewrite it, do not restate what is fine.

RULES
1. A budget change is the one that breaks the most: click volume, the number of ad groups
   the budget can support, and whether the plan is still readable at all.
2. Removed keywords can strand an ad group (too few terms to serve) or remove the intent
   an ad group's copy is written for. Removed negatives can reopen waste the vault existed
   to stop — say which.
3. Report severity honestly. "block" means shipping this is wrong. "warn" means a person
   should look. Do not inflate; a wall of warnings gets ignored.
4. If nothing is wrong, return an empty findings list and say so. That is a real answer.

Respond with pure JSON only:
{
  "verdict": "ok | warn | block",
  "summary": "one or two sentences",
  "findings": [{"severity": "block | warn | note", "what": "string", "fix": "string",
                "where": "which part of the campaign"}],
  "budgetCheck": "what the current budget actually supports, or empty string"
}"""


def recheck_campaign(campaign: dict, changes: list, model: str = None) -> dict:
    """Run an edited campaign back past the model before anybody approves it."""
    groups = campaign.get("adGroups") or []
    vault = campaign.get("negativeKeywordVault") or {}
    sector_key = campaign.get("sectorKey") or "general"
    viability = analyse_budget(campaign.get("monthlyBudget"), sector_key)

    structure = "\n".join(
        f"  - {g.get('name')}: {len(g.get('keywords') or [])} keywords, "
        f"{len((g.get('ads') or {}).get('headlines') or [])} headlines, "
        f"{len((g.get('ads') or {}).get('descriptions') or [])} descriptions"
        for g in groups) or "  (no ad groups)"

    user = f"""Business: {campaign.get('businessName', '')}
Sector: {campaign.get('sector', '')}
Monthly budget now: ${_num(campaign.get('monthlyBudget')):,.0f}
Budget check at that level: {viability['status']} — {viability['advice']}
Target areas: {target_areas.for_prompt(campaign.get('targetAreas') or []) or 'not specified'}

Ad groups as they now stand:
{structure}
Negative keywords remaining: {sum(len(v or []) for v in vault.values())}

WHAT A PERSON CHANGED:
{chr(10).join('  - ' + str(c) for c in (changes or [])) or '  (nothing recorded)'}"""

    data = _chat(RECHECK_SYSTEM, user, purpose="recheck", model=model,
                 max_tokens=2000, temperature=0.3)

    verdict = str(data.get("verdict", "")).strip().lower()
    findings = [
        {"severity": (str(f.get("severity", "note")).lower()
                      if str(f.get("severity", "")).lower() in ("block", "warn", "note") else "note"),
         "what": _trunc(f.get("what"), 300), "fix": _trunc(f.get("fix"), 300),
         "where": _trunc(f.get("where"), 120)}
        for f in (data.get("findings") or [])[:20] if isinstance(f, dict) and f.get("what")
    ]
    # The verdict is derived from the findings rather than taken on trust: a
    # model that lists a blocking finding and then says "ok" is the one case
    # where believing it ships the broken campaign.
    if any(f["severity"] == "block" for f in findings):
        verdict = "block"
    elif any(f["severity"] == "warn" for f in findings):
        verdict = verdict if verdict == "block" else "warn"
    elif verdict not in ("ok", "warn", "block"):
        verdict = "ok"

    return {
        "verdict": verdict,
        "summary": _trunc(data.get("summary"), 600),
        "findings": findings,
        "budgetCheck": _trunc(data.get("budgetCheck"), 500),
        "budgetViability": {"status": viability["status"], "advice": viability["advice"]},
    }


def _trunc(value, length):
    return str(value or "").strip()[:length]


def _dedupe(values, length):
    out, seen = [], set()
    for v in values or []:
        t = _trunc(v, length)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


def normalise(data: dict, payload: dict, viability: dict) -> dict:
    """The model is good but never trusted with Google's hard character limits."""
    ad_groups = []
    for i, g in enumerate(data.get("adGroups") or []):
        ads = g.get("ads") or {}
        ad_groups.append({
            "name": _trunc(g.get("name") or f"Ad Group {i + 1}", 120),
            "theme": _trunc(g.get("theme"), 400),
            "avgCPC": _num(g.get("avgCPC")) or _num((data.get("costEstimation") or {}).get("avgCPC")) or 2.0,
            "keywords": _dedupe(g.get("keywords"), 80)[:50],
            "ads": {
                "headlines": _dedupe(ads.get("headlines"), 30)[:15],
                "descriptions": _dedupe(ads.get("descriptions"), 90)[:4],
            },
        })

    assets = data.get("adAssets") or {}
    sitelinks = []
    for s in (assets.get("sitelinks") or [])[:20]:
        title = _trunc(s.get("title"), 25)
        if not title:
            continue
        sitelinks.append({
            "title": title,
            "desc1": _trunc(s.get("desc1"), 35),
            "desc2": _trunc(s.get("desc2"), 35),
            "url": str(s.get("url") or payload.get("websiteUrl") or "").strip(),
        })

    snip = assets.get("structuredSnippets") or {}
    vault = data.get("negativeKeywordVault") or {}
    est = data.get("costEstimation") or {}

    return {
        "businessName": data.get("businessName") or payload.get("businessName"),
        "websiteUrl": data.get("websiteUrl") or payload.get("websiteUrl"),
        "monthlyBudget": _num(data.get("monthlyBudget")) or _num(payload.get("budget")),
        "objective": payload.get("objective"),
        "targetAudience": payload.get("targetAudience"),
        "geography": payload.get("geography"),
        "sector": viability["sector"],
        "sectorKey": viability["sector_key"],
        "strategySummary": _trunc(data.get("strategySummary"), 1200),
        "costEstimation": {
            "estimatedMonthlyCost": _num(est.get("estimatedMonthlyCost")) or _num(payload.get("budget")),
            "avgCPC": _num(est.get("avgCPC")),
            "estimatedMonthlyClicks": _num(est.get("estimatedMonthlyClicks")),
            "estimatedConversionRate": _num(est.get("estimatedConversionRate")),
            "estimatedConversions": _num(est.get("estimatedConversions")),
            "estimatedCPA": _num(est.get("estimatedCPA")),
            "budgetViability": {"status": viability["status"], "advice": viability["advice"]},
        },
        "landingPageAnalysis": {
            "ctaReadiness": (data.get("landingPageAnalysis") or {}).get("ctaReadiness") or "Unknown",
            "messageMatch": (data.get("landingPageAnalysis") or {}).get("messageMatch") or "",
            "recommendations": ((data.get("landingPageAnalysis") or {}).get("recommendations") or [])[:10],
        },
        "adGroups": ad_groups,
        "adAssets": {
            "sitelinks": sitelinks,
            "callouts": _dedupe(assets.get("callouts"), 25)[:20],
            "structuredSnippets": {
                "header": _trunc(snip.get("header") or "Services", 25),
                "values": _dedupe(snip.get("values"), 25)[:10],
            },
        },
        "negativeKeywordVault": {
            "freeCheap": (vault.get("freeCheap") or [])[:200],
            "jobsCareers": (vault.get("jobsCareers") or [])[:200],
            "educational": (vault.get("educational") or [])[:200],
            "irrelevant": (vault.get("irrelevant") or [])[:200],
        },
    }


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
