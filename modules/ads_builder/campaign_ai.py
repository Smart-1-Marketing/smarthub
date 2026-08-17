"""AI campaign generator and budget viability engine.

Calls OpenAI over plain ``requests`` so the module adds no new dependency to
the Hub — it reuses OPENAI_API_KEY / OPENAI_MODEL exactly like the SEO, FAQ
and proposal tools do.
"""
from __future__ import annotations

import json
import os

import requests

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


def analyse_budget(monthly_budget, sector_key="general") -> dict:
    """Below ~100 clicks/month you cannot optimise; below ~30 you are donating."""
    sector = SECTOR_CPC.get(sector_key) or SECTOR_CPC["general"]
    try:
        budget = float(monthly_budget or 0)
    except (TypeError, ValueError):
        budget = 0.0

    mid_cpc = (sector["low"] + sector["high"]) / 2
    clicks = budget / mid_cpc if mid_cpc else 0
    worst_case = budget / sector["high"] if sector["high"] else 0

    if clicks < 30:
        status = "CRITICAL"
        advice = (
            f"At roughly ${mid_cpc:.2f} average CPC in {sector['label']}, ${budget:,.0f}/mo buys "
            f"about {clicks:.0f} clicks. That is not enough traffic for Google to optimise or for "
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
8. A categorised negative keyword vault, specific to this business. Be thorough.
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


def generate_campaign(payload: dict, api_key: str = None, model: str = None) -> dict:
    key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    if not key:
        raise GenerationError(
            "No OpenAI API key. Set OPENAI_API_KEY in the Hub environment, or paste a key "
            "in the generator."
        )

    viability = analyse_budget(payload.get("budget"), payload.get("sector") or "general")

    user_prompt = f"""Build a Google Ads search campaign for:

Business name: {payload.get('businessName', '')}
Website / landing page: {payload.get('websiteUrl', '')}
Sector: {viability['sector']}
Primary objective: {payload.get('objective', '')}
Monthly budget: ${payload.get('budget', 0)}
Target audience: {payload.get('targetAudience') or 'not specified'}
Geography: {payload.get('geography') or 'not specified'}
{('Additional context: ' + payload['notes']) if payload.get('notes') else ''}

Independent budget check already run (use it, do not contradict it):
{viability['status']} — {viability['advice']}
Typical CPC range for this sector: ${viability['cpc_low']} to ${viability['cpc_high']}.

Remember: 20 to 50 keywords in EVERY ad group, with match types tagged."""

    resp = requests.post(
        OPENAI_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.7,
            "max_tokens": 8000,
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
        _hub_ai.note_usage("ads_builder", resp.json(), purpose="campaign")
    except Exception:  # noqa: BLE001
        pass

    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        raise GenerationError("OpenAI returned an unexpected response shape.")

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise GenerationError("The model returned malformed JSON. Try again.")

    return normalise(data, payload, viability)


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
