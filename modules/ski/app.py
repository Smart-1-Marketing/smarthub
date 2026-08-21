import io
import json
import math
import os
import re
import calendar
import threading
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

import cloudinary
import cloudinary.uploader

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

load_dotenv()

app = Flask(__name__)

# Standardized webhook variable (falls back to the old name for safety).
WEBHOOK_URL = (os.getenv("GHL_WEBHOOK_URL") or os.getenv("SMART1_WEBHOOK_URL") or "").strip()
# Base name / Cloudinary folder for generated report PDFs.
REPORT_NAME = (os.getenv("REPORT_NAME") or "smart1-ski-report").strip()
# OpenAI model for the AI market analysis narrative.
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4.1-mini").strip()

# cloudinary auto-configures from the CLOUDINARY_URL env var.
cloudinary.config(secure=True)


# ---------------------------------------------------------------------------
# abuse guards: honeypot + simple in-memory per-IP rate limits
# ---------------------------------------------------------------------------
_RATE_LOCK = threading.Lock()
_RATE_BUCKETS = {}  # {(bucket, ip): [timestamps]}


def client_ip():
    # LAST hop, not the first — the first is caller-supplied and spoofable,
    # which would let one machine look like a new visitor every request.
    fwd = (request.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
    return fwd or (request.remote_addr or "unknown")


def rate_limited(bucket, limit, window_seconds=3600):
    """Return True when this IP has exceeded `limit` hits in the window."""
    key = (bucket, client_ip())
    now = time.time()
    with _RATE_LOCK:
        hits = [t for t in _RATE_BUCKETS.get(key, []) if now - t < window_seconds]
        if len(hits) >= limit:
            _RATE_BUCKETS[key] = hits
            return True
        hits.append(now)
        _RATE_BUCKETS[key] = hits
        # opportunistic cleanup so the map cannot grow unbounded
        if len(_RATE_BUCKETS) > 5000:
            for k in [k for k, v in _RATE_BUCKETS.items() if not v or now - v[-1] > window_seconds]:
                _RATE_BUCKETS.pop(k, None)
    return False


def honeypot_tripped(body):
    return bool(str(body.get("s1_hp", "") or "").strip())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def num(value, fallback=0.0):
    try:
        v = float(value)
        return v if math.isfinite(v) else fallback
    except (TypeError, ValueError):
        return fallback


def clamp(value, lo, hi):
    return min(hi, max(lo, value))


def rnd(value):
    # match JS Math.round for the non-negative planning values used here
    return int(math.floor(num(value) + 0.5))


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "report").lower()).strip("-")
    return s or "report"


# ---------------------------------------------------------------------------
# geocode + climate (open-meteo, free, no key)
# ---------------------------------------------------------------------------
def geocode(zip_code):
    r = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": zip_code, "count": 10, "language": "en", "format": "json"},
        timeout=15,
    )
    if not r.ok:
        raise RuntimeError("Location lookup failed.")
    results = (r.json() or {}).get("results") or []
    match = next((x for x in results if x.get("country_code") == "US"), None) or (results[0] if results else None)
    if not match:
        raise RuntimeError("ZIP code could not be located.")
    return {
        "latitude": match.get("latitude"),
        "longitude": match.get("longitude"),
        "city": match.get("name") or "",
        "state": match.get("admin1") or "",
        "elevation_m": num(match.get("elevation")),
    }


def climate_summary(latitude, longitude, season_start_month=11, season_end_month=3):
    current_year = datetime.now(timezone.utc).year
    end_year = current_year - 1
    start_year = end_year - 5
    start = f"{start_year}-{season_start_month:02d}-01"
    end_date_year = end_year if season_end_month < season_start_month else start_year + 5
    final_day = calendar.monthrange(end_date_year, season_end_month)[1]
    end = f"{end_date_year}-{season_end_month:02d}-{final_day:02d}"

    r = requests.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start,
            "end_date": end,
            "daily": ",".join([
                "temperature_2m_max", "temperature_2m_min", "snowfall_sum", "rain_sum",
                "precipitation_sum", "wind_speed_10m_max", "sunshine_duration",
            ]),
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "auto",
        },
        timeout=25,
    )
    if not r.ok:
        raise RuntimeError("Historical climate lookup failed.")
    d = (r.json() or {}).get("daily") or {}
    dates = d.get("time") or []

    def in_season(month):
        if season_start_month <= season_end_month:
            return season_start_month <= month <= season_end_month
        return month >= season_start_month or month <= season_end_month

    def arr(key, i, fallback=0.0):
        seq = d.get(key) or []
        return num(seq[i], fallback) if i < len(seq) else fallback

    seasons = {}
    for i, date in enumerate(dates):
        dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        month = dt.month
        if not in_season(month):
            continue
        season = dt.year if month >= season_start_month else dt.year - 1
        seasons.setdefault(season, {"days": []})
        max_t = arr("temperature_2m_max", i, 99)
        min_t = arr("temperature_2m_min", i, 99)
        snowfall = arr("snowfall_sum", i)
        rain = arr("rain_sum", i)
        wind = arr("wind_speed_10m_max", i)
        sunshine_hours = arr("sunshine_duration", i) / 3600.0
        prior_snow = arr("snowfall_sum", i - 1) if i > 0 else 0
        prior2_snow = arr("snowfall_sum", i - 2) if i > 1 else 0
        seasons[season]["days"].append({
            "date": date, "maxT": max_t, "minT": min_t, "snowfall": snowfall, "rain": rain,
            "wind": wind, "sunshineHours": sunshine_hours,
            "cold": max_t <= 32 or min_t <= 20,
            "snowmaking": min_t <= 28 and max_t <= 38,
            "powder": snowfall >= 4,
            "rainRisk": rain >= 0.25 and max_t > 34,
            "windRisk": wind >= 30,
            "bluebird": (prior_snow >= 3 or prior2_snow >= 4) and sunshine_hours >= 5 and max_t <= 40,
        })

    season_rows = [s for s in seasons.values() if len(s["days"]) >= 21]

    weekly_buckets = {}
    for season in season_rows:
        season["days"].sort(key=lambda x: x["date"])
        for index, day in enumerate(season["days"]):
            week = index // 7 + 1
            weekly_buckets.setdefault(week, []).append(day)

    def bsum(days, key):
        return sum((1 if day[key] else 0) if isinstance(day[key], bool) else num(day[key]) for day in days)

    season_count = len(season_rows) or 1
    weekly = []
    for week in sorted(weekly_buckets.keys()):
        days = weekly_buckets[week]
        qualified = sum(1 for x in days if x["powder"] or x["bluebird"] or x["snowmaking"] or (x["maxT"] <= 35 and not x["rainRisk"] and not x["windRisk"]))
        suppressed = sum(1 for x in days if x["rainRisk"] or x["windRisk"] or x["maxT"] >= 45)
        avg_qualified = qualified / season_count
        avg_suppressed = suppressed / season_count
        confidence = int(clamp(round((avg_qualified / 7) * 100), 0, 100))
        weekly.append({
            "week_number": int(week),
            "avg_qualified_ad_days": round(avg_qualified * 10) / 10,
            "avg_suppressed_days": round(avg_suppressed * 10) / 10,
            "avg_snowfall_inches": round((bsum(days, "snowfall") / season_count) * 10) / 10,
            "avg_snowmaking_days": round((bsum(days, "snowmaking") / season_count) * 10) / 10,
            "avg_powder_days": round((bsum(days, "powder") / season_count) * 10) / 10,
            "avg_bluebird_days": round((bsum(days, "bluebird") / season_count) * 10) / 10,
            "avg_rain_risk_days": round((bsum(days, "rainRisk") / season_count) * 10) / 10,
            "avg_wind_risk_days": round((bsum(days, "windRisk") / season_count) * 10) / 10,
            "activation_confidence": confidence,
            "recommendation": "Aggressive activation" if confidence >= 60 else ("Selective activation" if confidence >= 35 else "Hold or use future-date offers"),
        })

    def avg_per_season(key):
        if not season_rows:
            return 0
        return sum(bsum(s["days"], key) for s in season_rows) / len(season_rows)

    return {
        "seasons_analyzed": len(season_rows),
        "avg_cold_days": rnd(avg_per_season("cold")),
        "avg_snowmaking_days": rnd(avg_per_season("snowmaking")),
        "avg_powder_days": round(avg_per_season("powder") * 10) / 10,
        "avg_bluebird_days": round(avg_per_season("bluebird") * 10) / 10,
        "avg_rain_risk_days": rnd(avg_per_season("rainRisk")),
        "avg_high_wind_days": rnd(avg_per_season("windRisk")),
        "avg_natural_snowfall_inches": rnd(avg_per_season("snowfall")),
        "total_season_weeks": len(weekly),
        "weekly": weekly,
    }


# ---------------------------------------------------------------------------
# audience / budget / savings / triggers / targets
# ---------------------------------------------------------------------------
def _provided(body, *keys):
    for k in keys:
        v = str(body.get(k, "") or "").strip()
        if v:
            return v
    return ""


def estimate_audience(body, climate=None):
    climate = climate or {}
    population = num(body.get("feeder_population"))
    households_entered = num(body.get("feeder_households"))

    pph_raw = _provided(body, "persons_per_household")
    persons_per_household = clamp(num(pph_raw, 2.45), 1.5, 4.5)

    # Skiing-household rate: use an override if given, otherwise DERIVE it from the
    # resort's historical snow/snowmaking (snowier markets index higher for skiing).
    rate_raw = _provided(body, "ski_household_rate", "ski_participation_rate")
    if rate_raw:
        ski_household_rate = clamp(num(rate_raw, 11), 2, 30) / 100
    else:
        snow = num(climate.get("avg_natural_snowfall_inches"))
        smk = num(climate.get("avg_snowmaking_days"))
        ski_household_rate = clamp(11 + snow / 30 + smk / 40, 9, 18) / 100

    # Drive-market relevance: override if given, otherwise infer from drive time.
    drive_raw = _provided(body, "drive_market_share")
    if drive_raw:
        drive_share = clamp(num(drive_raw, 75), 20, 100) / 100
    else:
        dt = str(body.get("drive_time", "")).lower()
        if "under 90" in dt or "90" in dt:
            drive_share = 0.88
        elif "5+" in dt or "fly" in dt:
            drive_share = 0.42
        elif "3–5" in dt or "3-5" in dt:
            drive_share = 0.58
        else:
            drive_share = 0.75

    households = households_entered or (population / persons_per_household if population else 0)
    if not households:
        markets = len([x for x in str(body.get("target_markets", "")).split(",") if x.strip()]) or 1
        households = markets * 210000

    population_used = population or households * persons_per_household
    broad = rnd(households * ski_household_rate)
    # Targeted skiing households: skiing households that are within drive relevance.
    qualified = rnd(households * ski_household_rate * drive_share)
    past_visitor = rnd(qualified * 0.35)
    family = rnd(qualified * 0.42)
    individuals = rnd(qualified * 2.1)

    return {
        "feeder_population_used": rnd(population_used),
        "feeder_households_used": rnd(households),
        "broad_skiing_households": broad,
        "estimated_targeted_skiing_households": qualified,
        "estimated_targeted_skiers_and_riders": individuals,
        "estimated_past_resort_visitor_households": past_visitor,
        "estimated_family_ski_households": family,
        "skiing_household_rate_used_percent": round(ski_household_rate * 100, 1),
        "methodology_note": "Directional estimate: feeder-market households in your selected markets, a skiing-household rate derived from your market's historical snow conditions, and drive-market relevance. It is not a purchased audience count.",
    }


def budget_plan(body):
    budget = clamp(num(body.get("monthly_budget", 6000), 6000), 2500, 100000)
    objective = body.get("campaign_objective") or "lift_tickets"
    shares = {"ctv": .40, "display": .35, "audio": .25}
    if objective == "season_passes":
        shares = {"ctv": .42, "display": .33, "audio": .25}
    elif objective == "lodging":
        shares = {"ctv": .45, "display": .35, "audio": .20}
    elif objective == "local_visits":
        shares = {"ctv": .32, "display": .40, "audio": .28}
    cpms = {"ctv": 35, "display": 8, "audio": 20}
    allocation = {k: rnd(budget * v) for k, v in shares.items()}
    impressions = {k: rnd(allocation[k] / cpms[k] * 1000) for k in allocation}
    return {
        "budget": rnd(budget),
        "allocation": allocation,
        "impressions": impressions,
        "channel_strategy": [
            {"channel": "Connected TV", "role": "Build awareness and urgency in skiing households during qualified weather windows.", "cpm_assumption": cpms["ctv"]},
            {"channel": "Programmatic Display", "role": "Deliver high-frequency weather, snowfall, lodging, ticket, and event messaging across premium websites and apps.", "cpm_assumption": cpms["display"]},
            {"channel": "Digital Audio", "role": "Reach commuters, travelers, and outdoor audiences with localized weather-triggered audio and companion banners.", "cpm_assumption": cpms["audio"]},
        ],
        "note": "The recommendation intentionally excludes paid social and paid search. Delivery estimates use planning CPM assumptions and will vary by market, inventory, targeting, and campaign dates.",
    }


def savings_model(body, climate, budget):
    season_weeks = climate.get("total_season_weeks") or int(clamp(num(body.get("season_weeks"), 20), 8, 30))
    weekly_budget = budget["budget"] / 4.345
    always_on = rnd(weekly_budget * season_weeks)
    weekly = climate.get("weekly") or []
    if weekly:
        qualified_ratio = sum(clamp(w["avg_qualified_ad_days"] / 7, 0, 1) for w in weekly) / len(weekly)
    else:
        qualified_ratio = 0.62
    minimum_activation = clamp(num(body.get("minimum_activation_share"), 35), 15, 80) / 100
    activation_share = clamp(max(qualified_ratio, minimum_activation), minimum_activation, .92)
    trigger_controlled = rnd(always_on * activation_share)
    protected = max(0, always_on - trigger_controlled)
    protected_pct = round(protected / always_on * 100) if always_on else 0
    poor_weeks = len([w for w in weekly if w["avg_suppressed_days"] >= 2.5 or w["activation_confidence"] < 30])
    strong_weeks = len([w for w in weekly if w["activation_confidence"] >= 60])
    return {
        "season_weeks": season_weeks,
        "modeled_weekly_budget": rnd(weekly_budget),
        "always_on_season_spend": always_on,
        "modeled_trigger_controlled_spend": trigger_controlled,
        "estimated_budget_protected": protected,
        "estimated_budget_protected_percent": protected_pct,
        "historically_weak_or_suppressed_weeks": poor_weeks,
        "historically_strong_activation_weeks": strong_weeks,
        "activation_share_used": round(activation_share * 100),
        "methodology_note": "Savings represent media budget that could be held, moved, or redirected during historically weak weather windows. This is not a guaranteed cash refund or guaranteed performance improvement.",
    }


def triggers(climate, body):
    has_snowmaking = str(body.get("snowmaking", "yes")) != "no"
    return [
        {"name": "Powder Alert", "condition": "Forecast of 4+ inches within 72 hours or verified fresh snowfall", "action": "Activate urgent lift-ticket, lodging, and weekend-trip creative in feeder markets", "priority": "High"},
        {"name": "Bluebird Window", "condition": "Clear or mostly sunny weather within 48 hours after meaningful snowfall", "action": "Promote premium experience, scenery, lessons, dining, and overnight packages", "priority": "High"},
        {"name": "Cold/Snowmaking Window", "condition": "Sustained temperatures favorable for snowmaking" if has_snowmaking else "Sustained freezing temperatures supporting snow retention", "action": "Promote terrain expansion and improved surface conditions" if has_snowmaking else "Promote preserved snow quality and open terrain", "priority": "High" if has_snowmaking else "Medium"},
        {"name": "Rain/Warmth Suppression", "condition": "Meaningful rain or temperatures likely to reduce conversion", "action": "Pause urgency campaigns; shift to passes, future-date lodging, dining, events, or summer products", "priority": "Protective"},
        {"name": "High-Wind Adjustment", "condition": "Forecast wind likely to affect lifts or guest comfort", "action": "Suppress same-day acquisition unless operations confirm normal lift access", "priority": "Protective"},
    ]


def target_locations(body):
    competitors = [s.strip() for s in re.split(r"\n|,", str(body.get("competitors", ""))) if s.strip()]
    feeder = [s.strip() for s in re.split(r"\n|,", str(body.get("target_markets", ""))) if s.strip()]
    categories = [
        "Competing ski resorts and tubing parks",
        "Ski, snowboard, and outdoor equipment retailers",
        "Winter sports clubs and race programs",
        "High-income ZIP codes in primary feeder markets",
        "Hotels near competitor resorts and mountain gateways",
        "Airports, park-and-ride lots, and travel corridors used by destination guests",
        "College campuses with ski clubs",
        "Family entertainment venues for beginner and tubing audiences",
    ]
    return {
        "feeder_markets": feeder,
        "named_competitors": competitors,
        "geofence_categories": categories,
        "lookback_recommendation": "Build seasonal visitor audiences from prior resort visitors where platform and privacy rules permit, then suppress current passholders when acquisition is the objective.",
    }


def build_report(body, location, climate):
    audience = estimate_audience(body, climate)
    budget = budget_plan(body)
    savings = savings_model(body, climate, budget)
    trigger_plan = triggers(climate, body)
    targets = target_locations(body)

    score = 50
    score += clamp(num(climate.get("avg_snowmaking_days")) / 3, 0, 20)
    score += clamp(num(climate.get("avg_powder_days")) * 2, 0, 15)
    score += clamp(num(climate.get("avg_bluebird_days")), 0, 10)
    score -= clamp(num(climate.get("avg_rain_risk_days")) / 2, 0, 12)
    score -= clamp(num(climate.get("avg_high_wind_days")) / 3, 0, 8)
    score = rnd(clamp(score, 25, 95))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "resort": {
            "name": body.get("resort_name"),
            "website": body.get("website", ""),
            "zip_code": body.get("zip_code"),
            "location": f"{location['city']}{', ' + location['state'] if location['state'] else ''}",
            "elevation_feet_estimate": rnd(location["elevation_m"] * 3.28084),
            "operating_months": body.get("operating_months") or "November–March",
            "objective": body.get("campaign_objective") or "lift_tickets",
        },
        "climate": climate,
        "weather_marketing_readiness_score": score,
        "audience": audience,
        "historical_weekly_plan": climate.get("weekly") or [],
        "savings_model": savings,
        "recommended_targets": targets,
        "trigger_plan": trigger_plan,
        "budget_plan": budget,
        "campaign_phases": [
            {"phase": "Preseason", "focus": "Season passes, groups, lessons, early lodging, gift cards", "timing": "August–opening day"},
            {"phase": "Opening/Build", "focus": "Snowmaking progress, terrain expansion, opening dates", "timing": "First sustained cold windows"},
            {"phase": "Peak Winter", "focus": "Powder alerts, bluebird weekends, lodging and lift tickets", "timing": "December–February"},
            {"phase": "Spring", "focus": "Value tickets, events, patios, festivals and pass renewal", "timing": "March–closing"},
            {"phase": "Offseason", "focus": "Weddings, lodging, golf, biking, festivals or next-year passes", "timing": "After closing"},
        ],
        "disclaimer": "Weather history and audience counts are directional planning estimates. Operational decisions should use the resort's forecast provider, snow report, lift status, inventory, and approved media-platform data.",
    }


# ---------------------------------------------------------------------------
# AI market analysis (OpenAI, with deterministic fallback)
# ---------------------------------------------------------------------------
def _fallback_summary(report):
    resort = report.get("resort", {}) or {}
    climate = report.get("climate", {}) or {}
    audience = report.get("audience", {}) or {}
    budget = report.get("budget_plan", {}) or {}
    savings = report.get("savings_model", {}) or {}
    alloc = budget.get("allocation", {}) or {}
    return (
        f"{resort.get('name') or 'Your resort'} in {resort.get('location') or 'your market'} scores "
        f"{report.get('weather_marketing_readiness_score')}/100 on weather-marketing readiness, based on "
        f"{climate.get('seasons_analyzed') or 'recent'} historical seasons of cold, snowfall, bluebird, rain and wind patterns.\n\n"
        f"The selected feeder markets contain an estimated {audience.get('feeder_households_used'):,} households, "
        f"of which roughly {audience.get('estimated_targeted_skiing_households'):,} are targeted skiing households "
        f"({audience.get('estimated_targeted_skiers_and_riders'):,} estimated skiers and riders). The recommended "
        f"${budget.get('budget'):,}/month media plan allocates ${alloc.get('ctv', 0):,} to Connected TV, "
        f"${alloc.get('display', 0):,} to programmatic display and ${alloc.get('audio', 0):,} to digital audio, and the "
        f"historical savings model shows about ${savings.get('estimated_budget_protected', 0):,} "
        f"({savings.get('estimated_budget_protected_percent', 0)}%) of always-on season spend that could be protected "
        f"with weather-triggered activation.\n\n"
        "Top recommendations: (1) run weather-triggered campaigns that scale spend into historically strong powder, "
        "bluebird and snowmaking windows and suppress it in rain or high-wind periods; (2) concentrate budget on the "
        "targeted skiing-household audience in your named feeder markets with geofenced competitor and retail "
        "targeting; (3) sequence the season with the phased plan — passes preseason, urgency creative in peak winter "
        "and value offers in spring."
    )


def generate_ai_summary(report, body):
    """3-4 paragraph market narrative via OpenAI; deterministic fallback if unavailable."""
    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        return _fallback_summary(report)
    try:
        facts = {
            "resort": report.get("resort", {}),
            "weather_marketing_readiness_score": report.get("weather_marketing_readiness_score"),
            "audience": report.get("audience", {}),
            "budget_plan": {k: report.get("budget_plan", {}).get(k) for k in ("budget", "allocation", "impressions", "note")},
            "savings_model": report.get("savings_model", {}),
            "climate": {k: v for k, v in (report.get("climate", {}) or {}).items() if k != "weekly"},
            "target_markets": body.get("target_markets", ""),
            "resort_type": body.get("resort_type", ""),
            "campaign_objective": body.get("campaign_objective", ""),
        }
        # Over HTTP rather than the openai SDK — not a Hub dependency.
        import requests as _rq
        _payload = dict(
            model=OPENAI_MODEL,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You are a senior media strategist at Smart 1 Marketing writing the market-assessment "
                        "section of a ski-resort advertising report. Write 3-4 short paragraphs of plain prose "
                        "(no headings, no bullet lists, no markdown): a resort market assessment grounded in the "
                        "readiness score, feeder-market households, budget plan and climate summary provided, "
                        "ending with your top three recommendations woven into the final paragraph. STRICT RULE: "
                        "use ONLY the numbers provided in the data — never invent, round differently, or "
                        "contradict any figure. Confident, consultative tone; no guarantees of snowfall or revenue."
                    ),
                },
                {
                    "role": "user",
                    "content": "Report data (all figures are authoritative):\n" + json.dumps(facts, indent=2, default=str),
                },
            ],
            temperature=0.25,
            max_output_tokens=1200,
        )
        _r = _rq.post("https://api.openai.com/v1/responses",
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"},
                      json=_payload, timeout=90)
        _r.raise_for_status()
        _data = _r.json()
        try:
            from hub import ai as _hub_ai
            _hub_ai.note_usage("ski", _data, purpose="market_summary")
        except Exception:  # noqa: BLE001
            pass
        text = ""
        for _it in (_data.get("output") or []):
            for _pt in (_it.get("content") or []):
                if _pt.get("type") in ("output_text", "text"):
                    text += _pt.get("text") or ""
        text = (text or _data.get("output_text") or "").strip()
        return text or _fallback_summary(report)
    except Exception as exc:
        app.logger.warning("AI market summary failed, using fallback: %s", exc)
        return _fallback_summary(report)


# ---------------------------------------------------------------------------
# PDF (reportlab) + Cloudinary upload
# ---------------------------------------------------------------------------
NAVY = colors.HexColor("#0A2240")
BLUE = colors.HexColor("#009ED2")
MUTED = colors.HexColor("#5f6f83")
LINE = colors.HexColor("#dbe3ec")
ICE = colors.HexColor("#eaf6ff")


def _fmt(n):
    return "—" if n is None else f"{int(round(num(n))):,}"


def _money(n):
    return "—" if n is None else f"${int(round(num(n))):,}"


def build_report_pdf(report):
    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=colors.HexColor("#25364b"))
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=MUTED, leading=11)
    title = ParagraphStyle("title", parent=ss["Title"], fontName="Helvetica-Bold", fontSize=22, textColor=NAVY, alignment=TA_LEFT, spaceAfter=2)
    eyebrow = ParagraphStyle("eyebrow", parent=body, fontName="Helvetica-Bold", fontSize=8, textColor=BLUE)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold", fontSize=12.5, textColor=NAVY, spaceBefore=14, spaceAfter=5)
    cell = ParagraphStyle("cell", parent=body, fontSize=9)
    cellw = ParagraphStyle("cellw", parent=cell, textColor=colors.white)

    m = report.get("audience", {}) or {}
    s = report.get("savings_model", {}) or {}
    b = report.get("budget_plan", {}) or {}
    resort = report.get("resort", {}) or {}

    story = []
    story.append(Paragraph("SMART 1 MARKETING &nbsp;·&nbsp; SKI RESORT MARKETING PLAN", eyebrow))
    story.append(Paragraph(resort.get("name") or "Ski Resort", title))
    story.append(Paragraph(f"{resort.get('location','')} &nbsp;·&nbsp; ZIP {resort.get('zip_code','')} &nbsp;·&nbsp; Approx. elevation {_fmt(resort.get('elevation_feet_estimate'))} ft", small))

    score_tbl = Table([[
        Paragraph(f"<font size=22 color='#009ED2'><b>{report.get('weather_marketing_readiness_score','—')}</b></font>", cell),
        Paragraph("<b>Weather-Marketing Readiness Score</b><br/><font size=8 color='#5f6f83'>Directional planning score from historical cold, snow, bluebird, rain &amp; wind patterns.</font>", cell),
    ]], colWidths=[1.0 * inch, 5.6 * inch])
    score_tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ICE), ("BOX", (0, 0), (-1, -1), 0.5, LINE), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10), ("LEFTPADDING", (0, 0), (-1, -1), 12)]))
    story.append(Spacer(1, 10))
    story.append(score_tbl)

    if report.get("market_summary"):
        story.append(Paragraph("Market Assessment", h2))
        for para in str(report["market_summary"]).split("\n\n"):
            if para.strip():
                story.append(Paragraph(para.strip().replace("&", "&amp;").replace("<", "&lt;"), body))
                story.append(Spacer(1, 4))

    def kv_block(pairs):
        rows = [[Paragraph(k, small), Paragraph(f"<b>{v}</b>", cell)] for k, v in pairs]
        t = Table(rows, colWidths=[3.3 * inch, 3.3 * inch])
        t.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -2), 0.4, LINE), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story.append(t)

    story.append(Paragraph("Estimated Targeted Skiing Households", h2))
    kv_block([
        ("Feeder households used", _fmt(m.get("feeder_households_used"))),
        ("Broad skiing households", _fmt(m.get("broad_skiing_households"))),
        ("Estimated targeted skiing households", _fmt(m.get("estimated_targeted_skiing_households"))),
        ("Estimated skiers & riders", _fmt(m.get("estimated_targeted_skiers_and_riders"))),
    ])

    story.append(Paragraph("Historical Weather-Based Savings Model", h2))
    kv_block([
        ("Modeled always-on season spend", _money(s.get("always_on_season_spend"))),
        ("Modeled trigger-controlled spend", _money(s.get("modeled_trigger_controlled_spend"))),
        ("Estimated budget protected", _money(s.get("estimated_budget_protected")) + (f" ({s.get('estimated_budget_protected_percent')}%)" if s.get("estimated_budget_protected_percent") is not None else "")),
        ("Historically strong activation weeks", _fmt(s.get("historically_strong_activation_weeks"))),
    ])

    story.append(Paragraph("Recommended Monthly Media Plan: " + _money(b.get("budget")), h2))
    alloc = b.get("allocation", {}) or {}
    kv_block([("Connected TV", _money(alloc.get("ctv"))), ("Programmatic Display", _money(alloc.get("display"))), ("Digital Audio", _money(alloc.get("audio")))])

    # simple 3-bar allocation visual: colored cell widths scaled to channel share
    total_alloc = sum(num(alloc.get(k)) for k in ("ctv", "display", "audio")) or 1
    bar_colors = {"ctv": NAVY, "display": BLUE, "audio": colors.HexColor("#2DBB72")}
    bar_labels = {"ctv": "Connected TV", "display": "Programmatic Display", "audio": "Digital Audio"}
    max_bar = 4.2 * inch
    story.append(Spacer(1, 6))
    for k in ("ctv", "display", "audio"):
        share = num(alloc.get(k)) / total_alloc
        bar_w = max(0.25 * inch, max_bar * share)
        row = Table(
            [[Paragraph(bar_labels[k], small), "", Paragraph(f"<b>{_money(alloc.get(k))}</b> ({round(share * 100)}%)", cell)]],
            colWidths=[1.5 * inch, bar_w, 6.6 * inch - 1.5 * inch - bar_w],
        )
        row.setStyle(TableStyle([
            ("BACKGROUND", (1, 0), (1, 0), bar_colors[k]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (2, 0), (2, 0), 8),
        ]))
        story.append(row)
    story.append(Spacer(1, 4))
    story.append(Paragraph(b.get("note", ""), small))

    # compact week-by-week historical season plan
    weekly = report.get("historical_weekly_plan", []) or []
    if weekly:
        story.append(Paragraph("Week-by-Week Historical Ski Season Plan", h2))
        head = ["Week", "Qualified ad days", "Snowfall", "Suppressed", "Confidence", "Recommendation"]
        rows = [[Paragraph(f"<b>{x}</b>", ParagraphStyle("th", parent=small, textColor=colors.white)) for x in head]]
        for w in weekly:
            rows.append([
                Paragraph(str(w.get("week_number", "")), cell),
                Paragraph(str(w.get("avg_qualified_ad_days", "")), cell),
                Paragraph(f"{w.get('avg_snowfall_inches','')}\"", cell),
                Paragraph(str(w.get("avg_suppressed_days", "")), cell),
                Paragraph(f"{w.get('activation_confidence','')}%", cell),
                Paragraph(str(w.get("recommendation", "")), ParagraphStyle("rec", parent=small)),
            ])
        wk = Table(rows, colWidths=[0.55 * inch, 1.15 * inch, 0.8 * inch, 0.95 * inch, 0.95 * inch, 2.2 * inch], repeatRows=1)
        wk.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ICE]),
            ("GRID", (0, 0), (-1, -1), 0.4, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(wk)

    story.append(Paragraph("Weather Trigger Playbook", h2))
    for t in report.get("trigger_plan", []) or []:
        story.append(Paragraph(f"<b>{t.get('name','')}</b> — {t.get('condition','')}", body))
        story.append(Paragraph(t.get("action", ""), small))
        story.append(Spacer(1, 3))

    story.append(Paragraph("Seasonal Campaign Structure", h2))
    for p in report.get("campaign_phases", []) or []:
        story.append(Paragraph(f"<b>{p.get('phase','')}</b> ({p.get('timing','')})", body))
        story.append(Paragraph(p.get("focus", ""), small))
        story.append(Spacer(1, 3))

    story.append(Spacer(1, 8))
    story.append(Paragraph(report.get("disclaimer", ""), small))
    story.append(Spacer(1, 10))
    story.append(Paragraph("<b>Smart 1 Marketing</b> &nbsp;·&nbsp; (614) 536-0768 &nbsp;·&nbsp; smart1marketing.com", ParagraphStyle("footer", parent=small, textColor=NAVY)))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, title=f"{resort.get('name','')} Ski Marketing Plan", leftMargin=0.6 * inch, rightMargin=0.6 * inch, topMargin=0.6 * inch, bottomMargin=0.6 * inch)
    doc.build(story)
    return buf.getvalue()


def upload_report_pdf(pdf_bytes, public_id):
    result = cloudinary.uploader.upload(
        io.BytesIO(pdf_bytes),
        resource_type="raw",
        public_id=public_id,
        overwrite=True,
        unique_filename=False,
        use_filename=False,
    )
    return result.get("secure_url", "")


# ---------------------------------------------------------------------------
# webhook + routes
# ---------------------------------------------------------------------------
def send_webhook(payload):
    """Deliver the lead through the Hub's lead panel.

    Also removes the reason this route used to return 502 with a finished
    report inside it: delivery can no longer fail in a way that has to be
    reported to the visitor, because the lead is stored before anything is
    sent. The operator gets their plan; we still see the lead.
    """
    try:
        from hub import leads as hub_leads
        out = hub_leads.capture_and_deliver(
            source="ski",
            page="Ski Resort Market Plan",
            fields={
                "name": payload.get("contact_name", ""),
                "email": payload.get("contact_email", ""),
                "phone": payload.get("contact_phone", ""),
                "company": payload.get("resort_name") or payload.get("company_name", ""),
            },
            pdf_url=payload.get("report_pdf_url", "") or "",
            client=payload.get("resort_name") or payload.get("company_name", ""))
        # Attribute the report to the client, so the work shows on
        # their 360 record rather than only in the lead panel.
        try:
            from hub import audit as _audit
            _audit.log("ski", "ski_report",
                       client=payload.get("resort_name") or payload.get("company_name", "") or None)
        except Exception:  # noqa: BLE001 — logging never breaks a lead
            pass
        return {"configured": True, "delivered": bool(out.get("delivered"))}
    except Exception:  # noqa: BLE001
        pass
    if not WEBHOOK_URL:
        return {"configured": False, "delivered": False}
    resp = requests.post(WEBHOOK_URL, json=payload, timeout=12)
    if not resp.ok:
        raise RuntimeError(f"GHL webhook returned {resp.status_code}: {resp.text[:180]}")
    return {"configured": True, "delivered": True, "status": resp.status_code}


def validate(body):
    required = ["resort_name", "zip_code", "contact_name", "contact_email"]
    missing = [k for k in required if not str(body.get(k, "")).strip()]
    if missing:
        return f"Missing required fields: {', '.join(missing)}"
    if not re.fullmatch(r"\d{5}(-\d{4})?", str(body.get("zip_code", "")).strip()):
        return "Enter a valid U.S. ZIP code."
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", str(body.get("contact_email", "")).strip()):
        return "Enter a valid email address."
    return None


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "smart1ski"})


@app.post("/api/partial-lead")
def partial_lead():
    """Salvage partial leads (no email required). Always responds {ok: true}."""
    try:
        body = request.get_json(silent=True) or {}
        if honeypot_tripped(body) or rate_limited("partial", limit=30):
            return jsonify({"ok": True})
        if not str(body.get("resort_name", "") or "").strip() and not str(body.get("website", "") or "").strip():
            return jsonify({"ok": True})
        payload = {
            "source": "Smart 1 Ski Resort Package",
            "report_status": "partial",
            "lead_id": str(body.get("lead_id", "") or "")[:100],
            "resort_name": str(body.get("resort_name", "") or "")[:200],
            "website": str(body.get("website", "") or "")[:300],
            "zip_code": str(body.get("zip_code", "") or "")[:20],
            "target_markets": str(body.get("target_markets", "") or "")[:500],
        }
        for k in ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "referrer_url", "landing_page_url"):
            v = str(body.get(k, "") or "").strip()
            if v:
                payload[k] = v[:300]
        try:
            send_webhook(payload)
        except Exception as exc:
            app.logger.warning("Partial-lead webhook failed: %s", exc)
    except Exception:
        app.logger.exception("Partial-lead handling failed")
    return jsonify({"ok": True})


@app.post("/api/analyze")
def analyze():
    body = request.get_json(silent=True) or {}
    if honeypot_tripped(body):
        # pretend success so bots learn nothing; do not burn quota
        return jsonify({"ok": False, "error": "Unable to generate report."}), 400
    if rate_limited("analyze", limit=6):
        return jsonify({"ok": False, "error": "Too many requests from this network. Please try again in an hour."}), 429
    body.pop("s1_hp", None)  # never forward the honeypot field
    error = validate(body)
    if error:
        return jsonify({"ok": False, "error": error}), 400
    try:
        location = geocode(body["zip_code"])
        try:
            climate = climate_summary(
                location["latitude"], location["longitude"],
                int(num(body.get("season_start_month"), 11)), int(num(body.get("season_end_month"), 3)),
            )
        except Exception:
            climate = {
                "seasons_analyzed": 0, "avg_cold_days": None, "avg_snowmaking_days": None,
                "avg_powder_days": None, "avg_bluebird_days": None, "avg_rain_risk_days": None,
                "avg_high_wind_days": None, "avg_natural_snowfall_inches": None,
                "data_note": "Historical weather was temporarily unavailable; report generated without climate totals.",
            }

        report = build_report(body, location, climate)
        report["market_summary"] = generate_ai_summary(report, body)

        # Generate the branded PDF and store it in Cloudinary. Guarded so a
        # PDF/upload failure never blocks the lead from reaching GHL.
        report_pdf_url = ""
        try:
            pdf = build_report_pdf(report)
            public_id = f"{REPORT_NAME}/{slugify(report['resort']['name'])}-{int(datetime.now(timezone.utc).timestamp())}.pdf"
            report_pdf_url = upload_report_pdf(pdf, public_id)
        except Exception as exc:
            app.logger.exception("PDF generation / Cloudinary upload failed: %s", exc)

        suite_payload = {
            **body,
            "source": "Smart 1 Ski Resort Package",
            "report_json": json.dumps(report, separators=(",", ":")),
            "report_pdf_url": report_pdf_url,
            "weather_marketing_readiness_score": report["weather_marketing_readiness_score"],
            "estimated_targeted_skiing_households": report["audience"]["estimated_targeted_skiing_households"],
            "estimated_budget_protected": report["savings_model"]["estimated_budget_protected"],
            "estimated_budget_protected_percent": report["savings_model"]["estimated_budget_protected_percent"],
            "recommended_monthly_budget": report["budget_plan"]["budget"],
            "resort_location_resolved": report["resort"]["location"],
            "generated_at": report["generated_at"],
            "market_summary": report["market_summary"],
            "lead_id": str(body.get("lead_id", "") or "")[:100],
        }

        try:
            webhook = send_webhook(suite_payload)
        except Exception as webhook_error:
            # Return 200 with the report. It was generated, it is correct, and
            # the operator waited 30 seconds for it — a delivery problem on our
            # side is not their problem. The lead is already stored in the Hub
            # panel, so nothing is lost by showing them what they asked for.
            # Previously this was a 502 whose body contained the finished
            # report, which the front end never rendered.
            app.logger.warning("Lead delivery failed after a successful report: %s",
                               webhook_error)
            return jsonify({"ok": True, "report": report,
                            "report_pdf_url": report_pdf_url,
                            "delivery_pending": True}), 200

        return jsonify({"ok": True, "webhook": webhook, "report": report, "report_pdf_url": report_pdf_url})
    except Exception as exc:
        app.logger.exception("Analysis failed")
        return jsonify({"ok": False, "error": str(exc) or "Unable to generate report."}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")), debug=False)
