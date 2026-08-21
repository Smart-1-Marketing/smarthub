import io
import json
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file

# reportlab is pure-Python (no system libraries) so the PDF builder deploys
# cleanly on Render's native Python runtime with no Docker/apt changes.
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Flowable,
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

load_dotenv()

app = Flask(__name__)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
# Standardized single webhook target.
WEBHOOK_URL = os.getenv("GHL_WEBHOOK_URL", "").strip()
ENABLE_PDF = os.getenv("ENABLE_PDF", "1").strip() not in ("0", "false", "False", "")

# Standardized report name — every generated PDF is stored in Cloudinary under this.
REPORT_NAME = "legal-conquesting-report"

# Closing CTA shown on the PDF's "Next Steps" page.
CONSULT_URL = os.getenv("CONSULT_URL", "https://smart1marketing.com/legalmarketingconsult").strip()
CONTACT_PHONE = os.getenv("CONTACT_PHONE", "").strip()

# Per-IP rate limiting (in-memory; resets on deploy — plenty for lead-form abuse control).
RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "6"))       # submissions per window
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "3600"))  # seconds
_rate_buckets: dict = defaultdict(deque)

# Separate, lighter bucket for /api/partial-lead so partial pings never consume the
# expensive AI endpoint's budget.
PARTIAL_RATE_LIMIT_MAX = 30
_partial_rate_buckets: dict = defaultdict(deque)


def _client_ip() -> str:
    # LAST hop, not the first. The first is supplied by the caller, so a
    # header of "1.2.3.4" would present as a new visitor on every request
    # and walk straight past this limit. Render appends the real address.
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[-1].strip() if fwd else request.remote_addr) or "unknown"


def _rate_limited(ip: str, buckets: dict = None, max_hits: int = None, window: int = None) -> bool:
    buckets = _rate_buckets if buckets is None else buckets
    max_hits = RATE_LIMIT_MAX if max_hits is None else max_hits
    window = RATE_LIMIT_WINDOW if window is None else window
    now = time.time()
    # Evict stale/empty buckets so the in-memory map can't grow unbounded.
    for key, dq in list(buckets.items()):
        if not dq or now - dq[-1] > window:
            del buckets[key]
    bucket = buckets[ip]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if len(bucket) >= max_hits:
        return True
    bucket.append(now)
    return False
# NOTE: Cloudinary is imported lazily inside upload_pdf_to_cloudinary() (never at module
# import) so a missing or misformatted CLOUDINARY_URL can never crash app startup.

# ---------------------------------------------------------------------------
# Smart 1 "Smart Signage" Legal Conquesting package menu. The model must choose
# ONE of these tiers (exact name + price) — it may not invent prices.
# ---------------------------------------------------------------------------
PACKAGE_MENU = [
    ("$3,500/month", "Local Docket Starter"),
    ("$5,000/month", "Case Conquesting Growth"),
    ("$7,500/month", "Market Domination"),
    ("$10,000/month", "Total Legal Saturation"),
]

SYSTEM_PROMPT = """
You are the Smart 1 Marketing Legal Conquesting Market Intelligence Architect.
Create a practical, sales-oriented Digital Out-of-Home (DOOH) + mobile conquesting
market plan for a law firm. The core product is "Smart Signage": programmatic DOOH
across the FULL network of digital screen formats, activated ONLY when and where the
firm's ideal clients are present, then bridged to the prospect's phone with
location-based mobile retargeting.

CORE PHILOSOPHY — "EVERY BOARD, ONLY WHEN IT WORKS"
Do NOT limit the plan to big highway/roadside boards. The whole advantage of Smart
Signage is running the ENTIRE screen mix — large-format boards AND small place-based
and transit screens — but only firing each screen when context makes it relevant
(right audience, right location, right moment/trigger). Big boards build local fame;
place-based screens catch people at the exact moment of legal need. Recommend a
diverse mix and say plainly when each format runs.

DOOH SCREEN FORMATS (build screen_network from the full menu — never big boards alone)
- Large Format / Roadside: highway & arterial digital billboards, building-facade
  displays, screens outside stadiums/arenas, airport & train-station displays. Role:
  broad awareness and local fame across daily commuters.
- Place-Based / Captive: screens inside hospital & medical waiting rooms, pharmacies,
  gyms & fitness centers, grocery stores, gas-pump toppers, convenience stores,
  shopping malls & retail, coffee shops, restaurants & bars, office-building lobbies &
  elevators, airport lounges, golf courses. Role: repeated, contextual exposure at the
  moment of need or high dwell time.
- Transit / Street Furniture: bus shelters, transit hubs, subway entrances, rideshare/
  taxi-top screens, digital street kiosks. Role: reach commuters and pedestrians in
  transitional moments.
Match formats to the practice area (see mapping below), and note the daypart/trigger.

WHY THIS BEATS A TRADITIONAL STATIC BILLBOARD (frame the plan around this "leg up")
- A static billboard charges a flat fee for 100% of passing traffic even though the
  vast majority never need a lawyer, can't be changed without a crew, has no audience
  data, and no call attribution.
- Smart Signage: (a) runs the full screen mix and activates each screen by location,
  audience, and real-time trigger; (b) swaps creative instantly with zero production/
  install delay; (c) layers third-party in-market legal-intent data; (d) is ad-block-
  proof and highly viewable in the real world; (e) captures anonymous mobile device
  IDs near the screens and retargets those phones; (f) measures impressions, location
  visits, and leads. Industry context you may reference: ~73% of U.S. consumers view
  DOOH favorably, and roughly half who notice a DOOH ad take action on it — a level of
  flexibility, targeting, and measurement a painted board simply cannot match.

DOOH CAMPAIGN BEST PRACTICES (reflect these in creative_tips and the plan)
- Programmatic triggers: fire ads on real-world signals — weather (PI/auto), time-of-
  day/daypart, nearby events, commute windows.
- Concise creative: DOOH is for brand recall, not fine print. Lead with the firm name +
  one memorable hook and contact ("Injured? Call ..."). Use QR codes only on
  pedestrian/dwell screens, never on highway boards.
- Pair with search: DOOH lifts branded search — keep the Google Business Profile and
  site sharp so the firm is easy to find the moment their name is recalled.

IMPORTANT ACCURACY RULES
- You do NOT have live access to maps, court dockets, or exact census/claims tables
  unless supplied in the request.
- Use geographic knowledge and conservative planning assumptions. Never claim a
  location or statistic was live-verified.
- Clearly label all population, household, and case/claim figures as AI planning
  estimates. Give ranges, a confidence level, and short assumptions.
- Do not invent precise street addresses. Use recognizable place names + city/state.
  An address field may be null.
- Prefer real, well-known venues/POIs you are reasonably confident exist. If
  uncertain, lower the confidence.
- Avoid duplicate locations. Favor practical, geofenceable points (buildings,
  facilities, intersections, venues) over vague open areas.

PRACTICE-AREA HIGH-INTENT TARGETING (mix large-format, place-based AND transit screens)
- Personal Injury / Auto Accident: hospital & ER waiting-room screens, urgent care,
  orthopedic & chiropractic clinics, pharmacies, auto body / collision repair shops,
  tow yards & impound lots, high-crash intersections & interstate merge points, gas-
  pump toppers, sports arenas; plus roadside boards near crash corridors and competitor
  PI firms (conquesting).
- Criminal Defense / DUI-DWI: county jail & detention centers, courthouses, police
  stations, bail bond offices, probation/parole offices; for DUI add bar & nightlife
  districts, stadiums, concert venues, and commuter-route roadside boards (evening/
  weekend dayparts).
- Family Law / Divorce: family & domestic court, high-dwell place-based screens in
  grocery stores, gas pumps, and shopping malls, marriage & family counseling offices,
  apartment/relocation complexes, competitor family firms.
- Workers' Compensation: industrial parks, warehouses & distribution centers,
  construction sites, manufacturing plants, occupational-health / urgent-care clinics,
  gas pumps and transit near job sites.
- Mass Tort / Class Action: nursing homes & assisted living, hospitals, pharmacies,
  dialysis/oncology centers.
- Medical Malpractice: hospital & specialty-clinic waiting rooms, pharmacies, senior
  communities.
- Employment / Labor: large employers, business parks, office-building lobbies &
  elevators, staffing offices, transit hubs.
- Immigration: consulates, community centers, ethnic retail corridors, ESL/community
  colleges, transit.
- Estate / Probate / Elder Law: senior living, hospitals/hospice, financial-planning
  office lobbies, golf courses, airport lounges, churches.
- Business / Tax / Corporate: office-building lobbies & elevators, executive suites,
  airport lounges, golf courses, business-district roadside boards.
- Bankruptcy: courthouses, check-cashing/payday locations, grocery & gas-pump screens
  in foreclosure-heavy ZIPs.
If a practice area is not listed, choose the closest analog and explain briefly.

MEDIA & TARGETING RULES (ALLOWED channels ONLY)
- ALWAYS include "Digital Out-of-Home (DOOH) Smart Signage" as the anchor channel. This
  channel spans the FULL screen mix (large-format/roadside + place-based + transit) —
  not just highway boards.
- ALWAYS include "Location Look-Back Mobile Retargeting" (capture anonymous device
  IDs seen near the screens/high-intent locations, then serve clickable display ads
  to those phones and bridge them to the firm's site/intake).
- ALWAYS include "In-Market Legal Intent Audience Data" (layer third-party data —
  e.g. Experian, TrueData, Proximic — for high-risk drivers, commuters, injury/legal
  in-market households, income/demographic filters).
- Then choose additional relevant chips from: "Point-Radius Proximity Geofencing",
  "Data-Driven Targeted Display", "Connected TV (CTV/OTT)", "Streaming Audio",
  "YouTube / Online Video", "Website Retargeting".
- NEVER recommend traditional/static billboards, print, newspapers, direct mail,
  terrestrial/broadcast radio, or linear/broadcast TV. NEVER recommend paid search,
  email, SMS, or ANY social media channel (Facebook, Instagram, TikTok, LinkedIn,
  Snapchat, Pinterest, X). Do not mention them anywhere.
- Return 5-7 media chips total.

WEATHER-TRIGGERED ACTIVATION
- weather_triggers ONLY meaningfully apply to Personal Injury / Auto Accident / DUI
  markets (crashes spike in rain, snow, ice, fog, first-freeze, holiday travel).
- For PI/auto/DUI firms, return 4-7 short punchy trigger labels (e.g. "Heavy rain",
  "Snow / ice event", "Ice storm", "Freeze warning", "Frost warning", "Dense fog",
  "First freeze", "Holiday travel weekend", "Rush-hour storm"). Include freeze warnings,
  ice storms, and frost warnings whenever the market's climate makes them relevant.
  weather_triggers_applicable = true.
- For non-injury practice areas (family, immigration, estate, bankruptcy, employment),
  set weather_triggers_applicable = false and return an EMPTY weather_triggers list;
  do not force weather logic where it does not fit.

OUTPUT
Return only valid JSON matching the requested schema. Do not use markdown fences.
"""

REPORT_SCHEMA = {
    "name": "legal_conquesting_report",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "market_summary": {"type": "string"},
            "practice_area": {"type": "string"},
            "market_type": {"type": "string"},
            "market_type_description": {"type": "string"},
            "market_opportunity": {"type": "string"},
            "billboard_comparison": {"type": "string"},
            "screen_network": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "format": {
                            "type": "string",
                            "enum": ["Large Format / Roadside", "Place-Based / Captive", "Transit / Street Furniture"],
                        },
                        "venues": {"type": "string"},
                        "when_it_runs": {"type": "string"},
                        "role": {"type": "string"},
                    },
                    "required": ["format", "venues", "when_it_runs", "role"],
                },
            },
            "dooh_advantages": {
                "type": "array",
                "minItems": 4,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["title", "detail"],
                },
            },
            "creative_tips": {
                "type": "array",
                "minItems": 3,
                "maxItems": 5,
                "items": {"type": "string"},
            },
            "how_it_works": {
                "type": "array",
                "minItems": 5,
                "maxItems": 6,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "detail": {"type": "string"},
                    },
                    "required": ["title", "detail"],
                },
            },
            "expected_outcomes": {
                "type": "array",
                "minItems": 3,
                "maxItems": 5,
                "items": {"type": "string"},
            },
            "market_profile": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "estimated_population_low": {"type": "integer"},
                    "estimated_population_base": {"type": "integer"},
                    "estimated_population_high": {"type": "integer"},
                    "estimated_households_low": {"type": "integer"},
                    "estimated_households_base": {"type": "integer"},
                    "estimated_households_high": {"type": "integer"},
                    "estimated_annual_cases_low": {"type": "integer"},
                    "estimated_annual_cases_base": {"type": "integer"},
                    "estimated_annual_cases_high": {"type": "integer"},
                    "case_volume_label": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "estimated_population_low",
                    "estimated_population_base",
                    "estimated_population_high",
                    "estimated_households_low",
                    "estimated_households_base",
                    "estimated_households_high",
                    "estimated_annual_cases_low",
                    "estimated_annual_cases_base",
                    "estimated_annual_cases_high",
                    "case_volume_label",
                    "confidence",
                    "assumptions",
                ],
            },
            "recommended_package": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "package_name": {"type": "string"},
                    "monthly_investment": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["package_name", "monthly_investment", "description"],
            },
            "pricing_scenarios": {
                "type": "array",
                "minItems": 3,
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tier": {"type": "string", "enum": ["Good", "Better", "Best"]},
                        "package_name": {"type": "string"},
                        "monthly_investment": {"type": "string"},
                        "coverage": {"type": "string"},
                    },
                    "required": ["tier", "package_name", "monthly_investment", "coverage"],
                },
            },
            "media_channels": {"type": "array", "items": {"type": "string"}},
            "mobile_retargeting_note": {"type": "string"},
            "weather_triggers_applicable": {"type": "boolean"},
            "weather_triggers": {"type": "array", "items": {"type": "string"}},
            "monthly_plan": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "month": {"type": "string"},
                        "focus": {"type": "string"},
                        "message": {"type": "string"},
                        "triggers": {"type": "array", "items": {"type": "string"}},
                        "pacing": {"type": "string"},
                    },
                    "required": ["month", "focus", "message", "triggers", "pacing"],
                },
            },
            "geofence_locations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "city_state": {"type": "string"},
                        "address": {"type": ["string", "null"]},
                        "category": {"type": "string"},
                        "screen_format": {
                            "type": "string",
                            "enum": ["Large Format / Roadside", "Place-Based / Captive", "Transit / Street Furniture", "Point-of-Interest Geofence"],
                        },
                        "priority": {"type": "integer", "enum": [1, 2, 3]},
                        "recommended_method": {
                            "type": "string",
                            "enum": ["location_lookback", "real_time_proximity", "both"],
                        },
                        "recommended_radius_miles": {"type": "number"},
                        "audience_reason": {"type": "string"},
                        "best_message": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    },
                    "required": [
                        "name",
                        "city_state",
                        "address",
                        "category",
                        "screen_format",
                        "priority",
                        "recommended_method",
                        "recommended_radius_miles",
                        "audience_reason",
                        "best_message",
                        "confidence",
                    ],
                },
            },
            "disclaimer": {"type": "string"},
        },
        "required": [
            "market_summary",
            "practice_area",
            "market_type",
            "market_type_description",
            "market_opportunity",
            "billboard_comparison",
            "screen_network",
            "dooh_advantages",
            "creative_tips",
            "how_it_works",
            "expected_outcomes",
            "market_profile",
            "recommended_package",
            "pricing_scenarios",
            "media_channels",
            "mobile_retargeting_note",
            "weather_triggers_applicable",
            "weather_triggers",
            "monthly_plan",
            "geofence_locations",
            "disclaimer",
        ],
    },
    "strict": True,
}


EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def clean_payload(data: dict) -> dict:
    fields = [
        "lead_id",
        "firm_name",
        "website",
        "firm_zip",
        "target_radius",
        "practice_area",
        "secondary_practice_areas",
        "primary_goal",
        "contact_name",
        "contact_email",
        "contact_phone",
        "proposal_recipient_email",
        "notes",
        # Attribution passthrough — captured by the form from the page URL/referrer.
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "referrer_url",
        "landing_page_url",
    ]
    cleaned = {k: str(data.get(k, "")).strip()[:1500] for k in fields}
    if not re.fullmatch(r"\d{5}(-\d{4})?", cleaned["firm_zip"]):
        raise ValueError("A valid U.S. ZIP code is required.")
    if not cleaned["contact_name"]:
        raise ValueError("Your name is required so we can send the plan.")
    if not EMAIL_RE.fullmatch(cleaned["contact_email"]):
        raise ValueError("A valid contact email is required so we can send the plan.")
    if not cleaned["practice_area"]:
        cleaned["practice_area"] = "Personal Injury"
    # Where the finished plan should be sent — defaults to the contact email.
    if not cleaned["proposal_recipient_email"]:
        cleaned["proposal_recipient_email"] = cleaned["contact_email"]
    return cleaned


def _package_menu_text() -> str:
    return "\n".join(f"    * {price} — {name}" for price, name in PACKAGE_MENU)


def generate_report(payload: dict) -> Any:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
        user_prompt = (
        "\nBuild a Smart Signage Legal Conquesting DOOH + mobile plan from these inputs:\n"
        f"{json.dumps(payload, indent=2)}\n\n"
        "The firm gave only a ZIP code, a practice area, and a target radius. You must "
        "supply everything else yourself:\n"
        "- Derive the state, city area, and region from the ZIP code and base all "
        "geography, density, and seasonality on it.\n"
        "- Identify the local high-intent venues/POIs for this practice area yourself "
        "(hospitals, body shops, courthouses, jails, industrial parks, etc. as relevant) "
        "and include the best of them in geofence_locations.\n\n"
        "Populate every field of the schema:\n"
        "- market_summary: one or two sentences framing the DOOH conquesting opportunity "
        "for this firm and market (reference the firm name, practice area, and area).\n"
        "- practice_area: echo the firm's primary practice area.\n"
        "- market_type: a short badge label, e.g. 'Major Metro Personal-Injury Market' or "
        "'Suburban Family-Law Market'. market_type_description: one sentence on the demand pattern.\n"
        "- billboard_comparison: 1-2 punchy sentences contrasting a wasteful static billboard "
        "flat-fee buy with targeted Smart Signage for THIS firm's practice area.\n"
        "- screen_network: EXACTLY 3 items, one per format — 'Large Format / Roadside', "
        "'Place-Based / Captive', and 'Transit / Street Furniture'. For each, fill venues (the "
        "specific screen locations for THIS practice area, e.g. 'ER & urgent-care waiting rooms, "
        "pharmacies, gyms' for place-based PI), when_it_runs (the daypart/trigger/context — e.g. "
        "'rain & rush-hour triggers', 'weekday high-dwell hours'), and role (one short line on what "
        "that format does — fame vs. moment-of-need vs. commuter reach). This section proves we run "
        "EVERY board type, activated only when it works — never big boards alone.\n"
        "- dooh_advantages: 4-6 short {title, detail} cards on why DOOH gives this firm a leg up over "
        "a static billboard (e.g. Full Screen Mix, Instant Creative Swaps, In-Market Intent Data, "
        "Ad-Block-Proof Attention, Weather/Trigger Activation, Foot-Traffic & Lead Measurement). Keep "
        "each detail to one plain sentence; you may reference that ~73% of consumers view DOOH "
        "favorably and about half who notice a DOOH ad act on it.\n"
        "- creative_tips: 3-5 short DOOH creative/best-practice bullets for this firm — concise brand-"
        "recall creative (firm name + one hook + memorable contact), QR only on dwell/pedestrian "
        "screens, programmatic triggers, and pairing DOOH with a sharp Google Business Profile/search "
        "presence.\n"
        "- how_it_works: 5-6 ordered steps that walk THIS firm through exactly how their campaign "
        "operates, from setup to signed client. Make it concrete and practice-area specific — name the "
        "real local place types where their screens light up, describe the mobile look-back bridge, and "
        "end at the intake/phone call. Each step is a short title (3-6 words) + a 1-2 sentence detail "
        "written in plain, client-facing language (\"we\"/\"your firm\"). A strong default arc: "
        "(1) map your market & high-intent zones, (2) activate Smart Signage on nearby digital screens, "
        "(3) layer in-market legal-intent audience data, (4) capture anonymous device IDs near those "
        "screens/locations, (5) retarget those phones and route them to your intake, (6) you get the "
        "call / signed case — with reporting on impressions, visits, and leads.\n"
        "- expected_outcomes: 3-5 short, benefit-oriented projection bullets for this firm (e.g. more "
        "signed cases from high-intent moments, lower cost per lead than a static billboard, always-on "
        "attribution). Frame as marketing projections, NOT guarantees; avoid specific promised numbers.\n"
        "- market_profile: low/base/high estimates for population, households, and estimated "
        "annual case/claim volume for this practice area in the radius; set case_volume_label to "
        "what is being counted (e.g. 'estimated annual injury claims', 'estimated annual DUI "
        "arrests', 'estimated annual divorce filings'); include confidence and short assumptions. "
        "Present ownership/claim figures as percentages/decimals where natural, not as verified counts.\n"
        "- market_opportunity: ONE short, plain sentence on the firm's opportunity in this market.\n"
        "- recommended_package: choose the best-fit tier from the Smart 1 legal package menu below. "
        "Use its EXACT name and price as monthly_investment, and write a short description of what "
        "that level buys. Pick the tier from market size, competition, and case volume.\n"
        "  SMART 1 LEGAL PACKAGE MENU (use these, do not invent prices):\n"
        f"{_package_menu_text()}\n"
        "- pricing_scenarios: EXACTLY 3 tiers — Good, Better, Best — drawn from the package menu above "
        "and SCALED TO THIS MARKET'S POPULATION AND SIZE (small/rural market → the three lower tiers; "
        "major metro → the three higher tiers). The recommended_package must be one of the three "
        "(typically the Better tier). For each, coverage is one plain sentence describing what that "
        "spend level buys in THIS market — how much of the screen network, how many high-intent zones, "
        "and how aggressively triggers/conquesting run. Use exact menu names and prices.\n"
        "- media_channels: ALLOWED channels only, per the system rules. ALWAYS include the three "
        "anchor chips ('Digital Out-of-Home (DOOH) Smart Signage', 'Location Look-Back Mobile "
        "Retargeting', 'In-Market Legal Intent Audience Data'), then 2-4 more relevant chips. Return "
        "5-7 total. NEVER include static billboards, print, broadcast, paid search, email, SMS, or social.\n"
        "- mobile_retargeting_note: 1-2 sentences describing the physical-screen-to-phone bridge: "
        "capture anonymous device IDs seen near the screens/high-intent locations, then serve clickable "
        "display ads to those phones and route them to the firm's site/intake.\n"
        "- weather_triggers_applicable + weather_triggers: per the system rules — populate triggers for "
        "PI/auto/DUI markets, otherwise set applicable=false and return an empty list.\n"
        "- monthly_plan: all 12 months (January-December). Each month: a focus title, a short client-facing "
        "message, 1-2 relevant trigger labels (only if weather applies; otherwise use a short seasonal/legal "
        "hook like 'Post-holiday divorce season' or 'Back-to-work injury spike' or leave triggers empty), and "
        "a 'pacing' string.\n"
        "  BUDGET PACING RULE for 'pacing': the recommended_package monthly_investment is the PEAK monthly "
        "budget (100%). In shoulder months spend 60%; in lower-demand months spend 40%. Classify each month "
        "as Peak, Shoulder, or Low based on this practice area's demand cycle, and set pacing to a short "
        "string with tier, percent, and the computed dollar amount — e.g. 'Peak — 100% ($7,500)', "
        "'Shoulder — 60% ($4,500)', 'Low — 40% ($3,000)'. Compute dollars from the chosen package price.\n"
        "- geofence_locations: 12-18 high-intent locations tuned to the practice area, and DELIBERATELY "
        "MIXED across formats — set screen_format on each to 'Large Format / Roadside', 'Place-Based / "
        "Captive', 'Transit / Street Furniture', or 'Point-of-Interest Geofence'. Include place-based "
        "venues (waiting rooms, pharmacies, gyms, grocery, gas pumps, malls, office lobbies/elevators, "
        "airport lounges, golf) and transit — not just roadside boards. Prioritize locations inside the "
        "target radius; lower confidence for uncertain ones. Keep text concise.\n"
        "- disclaimer: a short note that figures are AI planning estimates for the market, not exact counts.\n"
    )
    # Called over HTTP rather than through the openai SDK — every other Hub
    # module does it this way, and the SDK isn't in requirements.txt.
    import requests as _rq
    _resp = _rq.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "input": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "text": {"format": {"type": "json_schema", **REPORT_SCHEMA}},
            "temperature": 0.25,
            "max_output_tokens": 8000,
        },
        timeout=120,
    )
    _resp.raise_for_status()
    _data = _resp.json()
    try:
        from hub import ai as _hub_ai
        _hub_ai.note_usage("legal", _data, purpose="market_report")
    except Exception:  # noqa: BLE001
        pass
    text = ""
    for _item in (_data.get("output") or []):
        for _part in (_item.get("content") or []):
            if _part.get("type") in ("output_text", "text"):
                text += _part.get("text") or ""
    text = (text or _data.get("output_text") or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# PDF report — reportlab, pure Python. Produces a hosted PDF the team can send
# from Smart1Suite. Guarded so any failure never blocks the lead/webhook.
# ---------------------------------------------------------------------------

# Canonical Smart 1 brand tokens (kept in sync with templates/index.html CSS vars).
NAVY = colors.HexColor("#0A2240")
BLUE = colors.HexColor("#009ED2")
GOLD = colors.HexColor("#B8892B")
LINE = colors.HexColor("#dfe3ea")
MUTED = colors.HexColor("#687386")
MIST = colors.HexColor("#f2f5fa")


class BridgeDiagram(Flowable):
    """A branded 'physical screen -> phone -> your intake' flow diagram."""

    def __init__(self, width, height=118):
        super().__init__()
        self.width = width
        self.height = height

    def _node(self, c, x, w, title, sub):
        y = 30
        h = 58
        c.setFillColor(MIST)
        c.setStrokeColor(NAVY)
        c.setLineWidth(1.2)
        c.roundRect(x, y, w, h, 8, stroke=1, fill=1)
        c.setFillColor(NAVY)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawCentredString(x + w / 2, y + h - 22, title)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 7.3)
        for i, line in enumerate(sub):
            c.drawCentredString(x + w / 2, y + h - 36 - i * 9, line)

    def _arrow(self, c, x1, x2):
        y = 59
        c.setStrokeColor(GOLD)
        c.setLineWidth(2)
        c.line(x1, y, x2 - 6, y)
        c.setFillColor(GOLD)
        c.setLineWidth(0)
        c.saveState()
        c.translate(x2, y)
        c.lines([(-8, 4, 0, 0), (-8, -4, 0, 0)])
        c.restoreState()

    def draw(self):
        c = self.canv
        total = self.width
        gap = 34
        w = (total - 2 * gap) / 3.0
        x0 = 0
        x1 = w + gap
        x2 = 2 * (w + gap)
        # top label
        c.setFillColor(GOLD)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(0, self.height - 12, "THE SMART SIGNAGE → MOBILE BRIDGE")
        self._node(c, x0, w, "1 · Digital Screen", ["Your ad lights up on", "screens near high-intent", "locations"])
        self._node(c, x1, w, "2 · Their Phone", ["We capture the anonymous", "device ID and retarget", "it the same day"])
        self._node(c, x2, w, "3 · Your Intake", ["Clickable ad routes them", "to your site & phone —", "you get the lead"])
        self._arrow(c, x0 + w, x1)
        self._arrow(c, x1 + w, x2)


class PacingBars(Flowable):
    """Compact horizontal bars showing Peak/Shoulder/Low monthly spend pacing."""

    def __init__(self, width, rows):
        super().__init__()
        self.width = width
        self.rows = rows  # list of (label, dollars, tier)
        self.height = 20 * len(rows) + 8

    def draw(self):
        c = self.canv
        rows = self.rows
        maxd = max((d for _, d, _ in rows), default=1) or 1
        label_w = 92
        bar_max = self.width - label_w - 70
        tier_color = {"Peak": GOLD, "Shoulder": BLUE, "Low": colors.HexColor("#9fb0cd")}
        y = self.height - 16
        for label, dollars, tier in rows:
            c.setFillColor(NAVY)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(0, y + 2, label)
            bw = max(bar_max * (dollars / maxd), 2)
            c.setFillColor(tier_color.get(tier, BLUE))
            c.roundRect(label_w, y, bw, 10, 3, stroke=0, fill=1)
            c.setFillColor(MUTED)
            c.setFont("Helvetica", 7.6)
            c.drawString(label_w + bw + 6, y + 2, f"${dollars:,.0f} · {tier}")
            y -= 20


def _money_to_int(value: str):
    """'$7,500/month' -> 7500 (int) or None."""
    digits = re.sub(r"[^\d]", "", (value or "").split("/")[0])
    return int(digits) if digits else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "report").lower()).strip("-") or "report"


def _pdf_styles():
    ss = getSampleStyleSheet()
    body = ParagraphStyle("s1body", parent=ss["Normal"], fontName="Helvetica",
                          fontSize=9.5, leading=14, textColor=colors.HexColor("#25364b"))
    # keepWithNext prevents a section heading from being orphaned at the bottom of a page.
    h2 = ParagraphStyle("s1h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                        fontSize=13, leading=16, textColor=NAVY, spaceBefore=16, spaceAfter=6,
                        keepWithNext=1)
    title = ParagraphStyle("s1title", parent=ss["Title"], fontName="Helvetica-Bold",
                           fontSize=22, leading=25, textColor=NAVY, alignment=TA_LEFT, spaceAfter=4,
                           keepWithNext=1)
    eyebrow = ParagraphStyle("s1eye", parent=body, fontName="Helvetica-Bold",
                             fontSize=8, textColor=GOLD, spaceAfter=2)
    small = ParagraphStyle("s1small", parent=body, fontSize=8, textColor=MUTED, leading=11)
    cell = ParagraphStyle("s1cell", parent=body, fontSize=8, leading=10.5)
    cellw = ParagraphStyle("s1cellw", parent=cell, textColor=colors.white)
    return dict(body=body, h2=h2, title=title, eyebrow=eyebrow, small=small, cell=cell, cellw=cellw)


def _pdf_footer(canvas_, doc_):
    """Brand footer drawn on every PDF page."""
    canvas_.saveState()
    canvas_.setFont("Helvetica", 7.5)
    canvas_.setFillColor(MUTED)
    canvas_.drawCentredString(letter[0] / 2, 0.35 * inch,
                              "Smart 1 Marketing · (614) 536-0768 · smart1marketing.com")
    canvas_.restoreState()


def build_report_pdf(report: dict, firm: str) -> bytes:
    """Render the report JSON to a branded PDF and return the raw PDF bytes (or b'' on failure)."""
    if not ENABLE_PDF:
        return b""
    try:
        st = _pdf_styles()
        fmt = lambda n: f"{int(n):,}" if n is not None else "—"
        rng = lambda a, b: f"{fmt(a)}–{fmt(b)}"
        m = report.get("market_profile", {}) or {}
        rp = report.get("recommended_package", {}) or {}

        buffer = io.BytesIO()

        story = []

        # ---------- COVER PAGE ----------
        cover_title = ParagraphStyle("s1cover", parent=st["title"], fontSize=30, leading=34)
        cover_firm = ParagraphStyle("s1coverfirm", parent=st["title"], fontSize=20, leading=24,
                                    textColor=GOLD)
        story.append(Spacer(1, 1.5 * inch))
        story.append(Paragraph("SMART 1 MARKETING", st["eyebrow"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph("Legal Conquesting Plan", cover_title))
        story.append(Spacer(1, 4))
        story.append(HRFlowable(width="35%", thickness=2.4, color=GOLD, hAlign="LEFT",
                                spaceBefore=8, spaceAfter=16))
        story.append(Paragraph(f"Prepared exclusively for", st["small"]))
        story.append(Spacer(1, 2))
        story.append(Paragraph(firm or "Your Firm", cover_firm))
        cover_meta = " &nbsp;·&nbsp; ".join(x for x in [
            report.get("practice_area", ""),
            time.strftime("%B %d, %Y"),
        ] if x)
        story.append(Spacer(1, 6))
        story.append(Paragraph(cover_meta, st["body"]))
        story.append(Spacer(1, 1.9 * inch))
        story.append(Paragraph(
            "Digital Out-of-Home Smart Signage &nbsp;·&nbsp; High-Intent Geofencing &nbsp;·&nbsp; "
            "Mobile Retargeting", st["small"]))
        story.append(Paragraph(
            "Every board, only when it works — the leg up static billboards can't give you.",
            st["small"]))
        story.append(PageBreak())

        # ---------- REPORT ----------
        story.append(Paragraph("SMART 1 MARKETING &nbsp;|&nbsp; LEGAL CONQUESTING PLAN", st["eyebrow"]))
        story.append(Paragraph(firm or "Legal Market Report", st["title"]))
        pa = report.get("practice_area", "")
        if pa:
            story.append(Paragraph(f"<b>Practice Area:</b> {pa}", st["small"]))
        story.append(Spacer(1, 4))
        story.append(Paragraph(report.get("market_summary", ""), st["body"]))
        story.append(Spacer(1, 6))

        if report.get("market_type"):
            story.append(Paragraph(f"<b>{report.get('market_type')}</b> — {report.get('market_type_description','')}", st["small"]))

        cvl = (m.get("case_volume_label") or "ESTIMATED ANNUAL CASES").upper()
        stat_data = [[
            Paragraph(f"<b>{rng(m.get('estimated_population_low'), m.get('estimated_population_high'))}</b><br/><font size=7 color='#68798c'>ESTIMATED POPULATION</font>", st["cell"]),
            Paragraph(f"<b>{rng(m.get('estimated_households_low'), m.get('estimated_households_high'))}</b><br/><font size=7 color='#68798c'>ESTIMATED HOUSEHOLDS</font>", st["cell"]),
            Paragraph(f"<b>{rng(m.get('estimated_annual_cases_low'), m.get('estimated_annual_cases_high'))}</b><br/><font size=7 color='#68798c'>{cvl}</font>", st["cell"]),
        ]]
        stat = Table(stat_data, colWidths=[2.4 * inch] * 3)
        stat.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), MIST),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(Spacer(1, 8))
        story.append(stat)

        if report.get("billboard_comparison"):
            story.append(Paragraph("Why DOOH Beats a Static Billboard", st["h2"]))
            story.append(Paragraph(report.get("billboard_comparison", ""), st["body"]))

        # --- DOOH advantages (the "leg up") ---
        advs = report.get("dooh_advantages", []) or []
        if advs:
            adv_cells = [
                Paragraph(f"<b><font color='#0A2240'>{a.get('title','')}</font></b><br/>"
                          f"<font size=8.3 color='#68798c'>{a.get('detail','')}</font>", st["cell"])
                for a in advs
            ]
            rows = []
            for i in range(0, len(adv_cells), 2):
                pair = adv_cells[i:i + 2]
                if len(pair) == 1:
                    pair.append(Paragraph("", st["cell"]))
                rows.append(pair)
            adv_tbl = Table(rows, colWidths=[3.42 * inch, 3.42 * inch])
            adv_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), MIST),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]))
            story.append(Spacer(1, 4))
            story.append(adv_tbl)

        # --- Full screen network: every board, only when it works ---
        net = report.get("screen_network", []) or []
        if net:
            story.append(Paragraph("The Full Screen Network — Every Board, Only When It Works", st["h2"]))
            story.append(Paragraph(
                "We don't just buy big highway boards. We run every relevant screen format and activate "
                "each one only when the context is right for your practice area:", st["body"]))
            story.append(Spacer(1, 4))
            hdr = [Paragraph(f"<b>{h}</b>", st["cellw"]) for h in ("Screen Format", "Where It Runs", "When It Fires", "Role")]
            net_rows = [hdr]
            for n in net:
                net_rows.append([
                    Paragraph(f"<b>{n.get('format','')}</b>", st["cell"]),
                    Paragraph(n.get("venues", ""), st["cell"]),
                    Paragraph(n.get("when_it_runs", ""), st["cell"]),
                    Paragraph(n.get("role", ""), st["cell"]),
                ])
            net_tbl = Table(net_rows, colWidths=[1.5 * inch, 2.3 * inch, 1.7 * inch, 1.4 * inch], repeatRows=1)
            net_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, MIST]),
                ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ]))
            story.append(net_tbl)

        story.append(Paragraph("Your Market Opportunity", st["h2"]))
        story.append(Paragraph(report.get("market_opportunity", ""), st["body"]))

        # --- How Your Campaign Works (numbered steps) ---
        steps = report.get("how_it_works", []) or []
        if steps:
            story.append(Paragraph("How Your Campaign Works", st["h2"]))
            step_rows = []
            for i, s in enumerate(steps, 1):
                num = Paragraph(f"<b>{i}</b>", st["cellw"])
                txt = Paragraph(
                    f"<b>{s.get('title','')}</b><br/><font size=8.5 color='#68798c'>{s.get('detail','')}</font>",
                    st["cell"],
                )
                step_rows.append([num, txt])
            tbl = Table(step_rows, colWidths=[0.34 * inch, 6.5 * inch])
            style = [
                ("BACKGROUND", (0, 0), (0, -1), GOLD),
                ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (1, 0), (1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (1, 0), (1, -2), 0.5, LINE),
            ]
            tbl.setStyle(TableStyle(style))
            story.append(tbl)
            story.append(Spacer(1, 10))
            content_w = 6.9 * inch
            story.append(BridgeDiagram(content_w))

        # --- What to Expect (outcomes) ---
        outcomes = report.get("expected_outcomes", []) or []
        if outcomes:
            story.append(Paragraph("What to Expect", st["h2"]))
            oc_cells = [Paragraph(f"<font color='#3ba776'><b>✓</b></font>&nbsp; {o}", st["cell"]) for o in outcomes]
            # two-column grid
            rows = []
            for i in range(0, len(oc_cells), 2):
                pair = oc_cells[i:i + 2]
                if len(pair) == 1:
                    pair.append(Paragraph("", st["cell"]))
                rows.append(pair)
            oc_tbl = Table(rows, colWidths=[3.42 * inch, 3.42 * inch])
            oc_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0f9f4")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbe8d6")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0f0e7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ]))
            story.append(oc_tbl)

        story.append(Paragraph("Recommended Package", st["h2"]))
        story.append(Paragraph(f"<b>{rp.get('monthly_investment','')} — {rp.get('package_name','')}</b>", st["body"]))
        story.append(Paragraph(rp.get("description", ""), st["small"]))

        # --- Good / Better / Best pricing scenarios (scaled to market size) ---
        scenarios = report.get("pricing_scenarios", []) or []
        if scenarios:
            story.append(Paragraph("Good · Better · Best — Sized to Your Market", st["h2"]))
            story.append(Paragraph(
                "Three ways to enter this market, scaled to its population and competition:", st["small"]))
            story.append(Spacer(1, 4))
            rec_name = (rp.get("package_name") or "").strip().lower()
            hdr_cells, body_cells = [], []
            rec_col = -1
            for i, sc in enumerate(scenarios):
                is_rec = (sc.get("package_name", "").strip().lower() == rec_name)
                if is_rec:
                    rec_col = i
                tier_label = sc.get("tier", "") + (" ★ RECOMMENDED" if is_rec else "")
                hdr_cells.append(Paragraph(f"<b>{tier_label}</b>", st["cellw"]))
                body_cells.append(Paragraph(
                    f"<b><font size=13>{sc.get('monthly_investment','')}</font></b><br/>"
                    f"<b>{sc.get('package_name','')}</b><br/>"
                    f"<font size=8 color='#68798c'>{sc.get('coverage','')}</font>", st["cell"]))
            sc_tbl = Table([hdr_cells, body_cells], colWidths=[2.3 * inch] * len(scenarios))
            sc_style = [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 1), (-1, 1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
            if rec_col >= 0:
                sc_style.append(("BACKGROUND", (rec_col, 0), (rec_col, 0), GOLD))
                sc_style.append(("BACKGROUND", (rec_col, 1), (rec_col, 1), colors.HexColor("#fffdf6")))
                sc_style.append(("BOX", (rec_col, 0), (rec_col, 1), 1.4, GOLD))
            sc_tbl.setStyle(TableStyle(sc_style))
            story.append(sc_tbl)
            story.append(Spacer(1, 6))
        story.append(Paragraph(
            "<i>These figures are a suggested starting budget based on your market's size. In a free "
            "consultation we'll tailor a budget that works for your firm — every plan can flex up or "
            "down with your caseload goals.</i>", st["small"]))

        # --- How this investment saves money vs. traditional advertising ---
        story.append(Paragraph("How This Investment Saves You Money", st["h2"]))
        story.append(Paragraph(
            "Compared to a traditional static billboard buy, Smart Signage removes the fixed costs and "
            "the waste:", st["body"]))
        story.append(Spacer(1, 4))
        save_rows = [
            [Paragraph("<b></b>", st["cellw"]),
             Paragraph("<b>Traditional Static Billboard</b>", st["cellw"]),
             Paragraph("<b>Smart Signage DOOH</b>", st["cellw"])],
            ["What you pay for",
             "One location — 100% of passing traffic, relevant or not",
             "The full screen network — activated only when your audience is present"],
            ["Creative changes",
             "Production + install crew each change (typically $500–$1,500)",
             "Included — creative swapped digitally in minutes, at no cost"],
            ["Commitment",
             "Flat rate, often locked in for 6–12 months",
             "Budget flexes month to month with real demand"],
            ["Audience data",
             "None — everyone who drives by",
             "In-market legal-intent data targets likely claimants"],
            ["Proof it worked",
             "No attribution",
             "Impressions, location visits & leads reported monthly"],
        ]
        save_cells = [save_rows[0]] + [
            [Paragraph(f"<b>{r[0]}</b>", st["cell"]),
             Paragraph(r[1], st["cell"]),
             Paragraph(r[2], st["cell"])] for r in save_rows[1:]
        ]
        sv_tbl = Table(save_cells, colWidths=[1.15 * inch, 2.85 * inch, 2.9 * inch], repeatRows=1)
        sv_tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, MIST]),
            ("GRID", (0, 0), (-1, -1), 0.5, LINE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(sv_tbl)

        # Demand-paced savings, computed from the month-by-month plan's pacing dollars.
        plan_rows = report.get("monthly_plan", []) or []
        pacing_dollars = []
        for row_ in plan_rows:
            m_ = re.search(r"\$([\d,]+)", row_.get("pacing", "") or "")
            if m_:
                pacing_dollars.append(int(m_.group(1).replace(",", "")))
        if len(pacing_dollars) == 12 and max(pacing_dollars) > 0:
            peak_ = max(pacing_dollars)
            paced_total = sum(pacing_dollars)
            flat_total = peak_ * 12
            saved = flat_total - paced_total
            if saved > 0:
                usd = lambda v: "$" + f"{v:,}"
                story.append(Spacer(1, 8))
                sv_note = Paragraph(
                    f"<b><font color='#d1a542'>Demand-paced budgeting saves {usd(saved)} a year.</font></b><br/>"
                    f"<font size=9 color='#ffffff'>A flat always-on buy at your peak rate would run "
                    f"{usd(flat_total)}/year. Your demand-paced plan invests {usd(paced_total)} — spend steps "
                    f"down when cases slow, and no production or install fees ever apply.</font>", st["cell"])
                sv_box = Table([[sv_note]], colWidths=[6.9 * inch])
                sv_box.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                    ("TOPPADDING", (0, 0), (-1, -1), 12),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                    ("LEFTPADDING", (0, 0), (-1, -1), 16),
                ]))
                story.append(sv_box)
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "Traditional-billboard cost ranges are industry-typical figures for comparison, not a quote.",
            st["small"]))

        chans = report.get("media_channels", []) or []
        if chans:
            story.append(Paragraph("Recommended Media & Targeting", st["h2"]))
            story.append(Paragraph(" &nbsp;•&nbsp; ".join(chans), st["body"]))

        tips = report.get("creative_tips", []) or []
        if tips:
            story.append(Paragraph("Creative & Campaign Best Practices", st["h2"]))
            for tp in tips:
                story.append(Paragraph(f"<font color='#B8892B'><b>›</b></font>&nbsp; {tp}", st["body"]))

        if report.get("mobile_retargeting_note"):
            story.append(Paragraph("The Mobile Retargeting Bridge", st["h2"]))
            story.append(Paragraph(report.get("mobile_retargeting_note", ""), st["body"]))

        trigs = report.get("weather_triggers", []) or []
        if report.get("weather_triggers_applicable") and trigs:
            story.append(Paragraph("Weather-Triggered Activation", st["h2"]))
            story.append(Paragraph(" &nbsp;•&nbsp; ".join(trigs), st["body"]))

        plan = report.get("monthly_plan", []) or []
        if plan:
            story.append(Paragraph("Month-by-Month Campaign Plan", st["h2"]))
            # Budget-flex visual: representative dollars per tier (Peak / Shoulder / Low).
            tier_dollars = {}
            for row in plan:
                pac = row.get("pacing", "") or ""
                tier = pac.split()[0] if pac else ""
                dmatch = re.search(r"\$([\d,]+)", pac)
                if tier and dmatch:
                    tier_dollars.setdefault(tier, int(dmatch.group(1).replace(",", "")))
            bar_rows = [(t, tier_dollars[t], t) for t in ["Peak", "Shoulder", "Low"] if t in tier_dollars]
            if bar_rows:
                story.append(Paragraph(
                    "Spend flexes with demand — highest in peak months, stepped down when demand cools:",
                    st["small"]))
                story.append(Spacer(1, 4))
                story.append(PacingBars(6.9 * inch, bar_rows))
                story.append(Spacer(1, 8))
            rows = [[Paragraph("<b>Month</b>", st["cellw"]), Paragraph("<b>Focus</b>", st["cellw"]), Paragraph("<b>Budget Pacing</b>", st["cellw"])]]
            for x in plan:
                rows.append([
                    Paragraph(x.get("month", ""), st["cell"]),
                    Paragraph(x.get("focus", ""), st["cell"]),
                    Paragraph(x.get("pacing", ""), st["cell"]),
                ])
            t = Table(rows, colWidths=[1.1 * inch, 3.3 * inch, 2.8 * inch], repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, MIST]),
                ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(t)

        geo = sorted(report.get("geofence_locations", []) or [], key=lambda g: g.get("priority", 3))
        if geo:
            story.append(Paragraph("High-Intent Geofence & DOOH Targeting Locations", st["h2"]))
            fmt_short = {
                "Large Format / Roadside": "Roadside",
                "Place-Based / Captive": "Place-Based",
                "Transit / Street Furniture": "Transit",
                "Point-of-Interest Geofence": "POI Geofence",
            }
            conf_short = {"high": "High", "medium": "Med", "low": "Low"}
            rows = [[Paragraph(f"<b>{h}</b>", st["cellw"]) for h in ("Pri", "Location", "Category", "Screen", "Method", "Mi", "Conf")]]
            for g in geo:
                rows.append([
                    Paragraph(f"P{g.get('priority','')}", st["cell"]),
                    Paragraph(f"<b>{g.get('name','')}</b><br/>{g.get('city_state','')}", st["cell"]),
                    Paragraph(g.get("category", ""), st["cell"]),
                    Paragraph(fmt_short.get(g.get("screen_format", ""), g.get("screen_format", "")), st["cell"]),
                    Paragraph(str(g.get("recommended_method", "")).replace("_", " "), st["cell"]),
                    Paragraph(f"{g.get('recommended_radius_miles','')}", st["cell"]),
                    Paragraph(conf_short.get(g.get("confidence", ""), g.get("confidence", "")), st["cell"]),
                ])
            t = Table(rows, colWidths=[0.38 * inch, 1.75 * inch, 1.2 * inch, 1.0 * inch, 1.05 * inch, 0.4 * inch, 0.62 * inch], repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, MIST]),
                ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(t)

        story.append(Spacer(1, 12))
        story.append(Paragraph(report.get("disclaimer", ""), st["small"]))

        # ---------- NEXT STEPS / CTA PAGE ----------
        story.append(PageBreak())
        story.append(Paragraph("YOUR NEXT STEPS", st["eyebrow"]))
        story.append(Paragraph("From This Plan to Signed Cases", st["title"]))
        story.append(HRFlowable(width="35%", thickness=2.4, color=GOLD, hAlign="LEFT",
                                spaceBefore=8, spaceAfter=14))
        next_steps = [
            ("Book your free strategy session",
             "We walk through this plan together, refine the target locations, and answer every question — "
             "no cost, no obligation."),
            ("We build your campaign (first 14 days)",
             "Creative designed for brand recall, your screen map finalized across roadside, place-based, "
             "and transit formats, and intent-data audiences layered in — all for your approval before launch."),
            ("Launch & first report (day 30)",
             "Your Smart Signage goes live with trigger-based activation, and you receive your first monthly "
             "report on impressions, location visits, and generated leads."),
        ]
        for i, (t, d) in enumerate(next_steps, 1):
            num = Paragraph(f"<b>{i}</b>", st["cellw"])
            txt = Paragraph(f"<b>{t}</b><br/><font size=9 color='#68798c'>{d}</font>", st["cell"])
            tbl = Table([[num, txt]], colWidths=[0.34 * inch, 6.5 * inch])
            tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), GOLD),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (1, 0), (1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 8))
        cta_lines = [
            "<b><font size=13 color='#d1a542'>Ready when you are.</font></b>",
            f"<font size=10 color='#ffffff'>Book your free consultation: "
            f"<link href='{CONSULT_URL}'><u>{CONSULT_URL}</u></link></font>",
        ]
        if CONTACT_PHONE:
            cta_lines.append(f"<font size=10 color='#c8d2e6'>Or call us directly: <b>{CONTACT_PHONE}</b></font>")
        cta_cell = Paragraph("<br/>".join(cta_lines), st["cell"])
        cta = Table([[cta_cell]], colWidths=[6.9 * inch])
        cta.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), NAVY),
            ("TOPPADDING", (0, 0), (-1, -1), 16),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 16),
            ("LEFTPADDING", (0, 0), (-1, -1), 18),
        ]))
        story.append(Spacer(1, 10))
        story.append(cta)

        doc = SimpleDocTemplate(buffer, pagesize=letter, title=f"{firm} Legal Conquesting Plan",
                                leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                                topMargin=0.6 * inch, bottomMargin=0.6 * inch)
        doc.build(story, onFirstPage=_pdf_footer, onLaterPages=_pdf_footer)
        return buffer.getvalue()
    except Exception:
        app.logger.exception("PDF generation failed")
        return b""


def upload_pdf_to_cloudinary(pdf_bytes: bytes, firm: str):
    """Upload the proposal PDF to Cloudinary and return {url, download_url, public_id}, or None.

    Stored under the "legal-conquesting-report/" folder with a unique per-firm id, and
    delivered with a "legal-conquesting-report.pdf" attachment filename. Cloudinary is
    imported lazily here (never at module import) so a missing/misformatted CLOUDINARY_URL
    can never crash startup. Returns None if the URL isn't a valid cloudinary:// URL or the
    upload fails — the lead/webhook is never blocked."""
    if not pdf_bytes:
        return None
    cloud_url = os.getenv("CLOUDINARY_URL", "").strip().strip('"').strip("'")
    if not cloud_url.startswith("cloudinary://"):
        if cloud_url:
            app.logger.warning(
                "CLOUDINARY_URL is set but is not a valid 'cloudinary://<key>:<secret>@<cloud>' "
                "URL; skipping PDF upload."
            )
        return None
    # Normalize (strip stray quotes/whitespace) so the cloudinary SDK parses it cleanly.
    os.environ["CLOUDINARY_URL"] = cloud_url
    try:
        import cloudinary
        import cloudinary.uploader
        cloudinary.config(secure=True)
        public_id = f"{REPORT_NAME}/{_slug(firm)}-{int(time.time())}.pdf"
        # Through hub.storage. Same public_id and tags, so the asset lands
        # where it always has; the resource type now comes from the .pdf on the
        # id rather than being asserted, which is the check that keeps a PDF
        # from being stored as an image and then refusing to deliver.
        from hub import storage
        asset = storage.put("legal_reports", public_id, pdf_bytes,
                            public_id=public_id, overwrite=False,
                            tags=["smart1legal", REPORT_NAME])
        result = {"secure_url": asset.url, "public_id": asset.public_id}
    except Exception:
        app.logger.exception("Cloudinary upload failed")
        return None
    secure_url = result.get("secure_url", "") or ""
    download_url = (
        secure_url.replace("/upload/", f"/upload/fl_attachment:{REPORT_NAME}/") if secure_url else ""
    )
    return {
        "url": secure_url,
        "download_url": download_url or secure_url,
        "public_id": result.get("public_id", ""),
    }


# ---------------------------------------------------------------------------
# Local PDF fallback — used only when Cloudinary is unconfigured/fails, so the
# on-screen "Download PDF" button still works.
# NOTE: this store is per-worker, in-memory only. With multiple gunicorn workers
# a /pdf/<id> request can land on a worker that didn't generate the file, and
# everything is lost on restart/redeploy. It's a graceful fallback, not storage —
# configure CLOUDINARY_URL for durable hosted PDFs.
# ---------------------------------------------------------------------------
_LOCAL_PDF_TTL = 6 * 3600  # seconds
_local_pdfs: dict = {}     # report_id -> (pdf_bytes, created_ts)
_local_pdfs_lock = threading.Lock()


def _store_local_pdf(pdf_bytes: bytes) -> str:
    report_id = uuid.uuid4().hex
    now = time.time()
    with _local_pdfs_lock:
        # Simple TTL cleanup on each insert.
        for k in [k for k, (_, ts) in _local_pdfs.items() if now - ts > _LOCAL_PDF_TTL]:
            _local_pdfs.pop(k, None)
        _local_pdfs[report_id] = (pdf_bytes, now)
    return report_id


@app.get("/pdf/<report_id>")
def serve_local_pdf(report_id: str):
    with _local_pdfs_lock:
        item = _local_pdfs.get(report_id)
    if not item or time.time() - item[1] > _LOCAL_PDF_TTL:
        return jsonify({"ok": False, "error": "Report not found or expired."}), 404
    return send_file(io.BytesIO(item[0]), mimetype="application/pdf",
                     as_attachment=False, download_name=f"{REPORT_NAME}.pdf")


def _report_json_str(report: dict) -> str:
    """Serialize the report for the webhook, guaranteed to remain valid JSON.

    A blind [:60000] slice could cut mid-token and break downstream parsing, so we
    only send the full string when it fits, otherwise progressively drop the
    heaviest sections and re-serialize."""
    full = json.dumps(report, separators=(",", ":"))
    if len(full) <= 60000:
        return full
    trimmed = dict(report)
    for key in ("geofence_locations", "monthly_plan", "how_it_works", "dooh_advantages",
                "creative_tips", "expected_outcomes"):
        trimmed.pop(key, None)
        s = json.dumps(trimmed, separators=(",", ":"))
        if len(s) <= 60000:
            return s
    return json.dumps({
        "note": "report exceeded webhook size limit; sections omitted",
        "market_summary": str(report.get("market_summary", ""))[:2000],
    })


def _post_webhook(body: dict) -> None:
    if not WEBHOOK_URL:
        return
    try:
        requests.post(WEBHOOK_URL, json=body, timeout=12)
    except requests.RequestException:
        app.logger.exception("Webhook delivery failed")


def send_webhook(payload: dict, report: Any, status: str, pdf_url: str = "",
                 download_url: str = "", public_id: str = "") -> None:
    """Deliver the lead through the Hub's lead panel.

    Was a fire-and-forget POST that returned silently with no WEBHOOK_URL —
    so a blank env var discarded every lead while the visitor saw success.
    The panel stores first and forwards second.
    """
    _rep = report or {}
    try:
        from hub import leads as hub_leads
        hub_leads.capture_and_deliver(
            source="legal",
            page="Legal Market Plan",
            fields={
                "name": payload.get("contact_name", ""),
                "email": payload.get("contact_email", ""),
                "phone": payload.get("contact_phone", ""),
                "company": payload.get("firm_name", ""),
                "report_status": status,
            },
            pdf_url=pdf_url or "",
            client=payload.get("firm_name", ""))
        # Attribute the report to the client, so the work shows on
        # their 360 record rather than only in the lead panel.
        try:
            from hub import audit as _audit
            _audit.log("legal", "legal_report",
                       client=payload.get("firm_name", "") or None)
        except Exception:  # noqa: BLE001 — logging never breaks a lead
            pass
        return
    except Exception:  # noqa: BLE001
        pass

    if not WEBHOOK_URL:
        return
    report = report or {}
    mp = report.get("market_profile", {}) or {}
    rp = report.get("recommended_package", {}) or {}
    monthly = _money_to_int(rp.get("monthly_investment", ""))
    body = {
        # --- Contact / lead fields ---
        **payload,
        "source": "Smart 1 Legal Conquesting Market Intelligence",
        "report_status": status,
        # --- Opportunity fields ---
        "opportunity_name": f"{payload.get('firm_name', 'Lead')} — Legal Conquesting Plan",
        "recommended_package": rp.get("package_name", ""),
        "recommended_investment": rp.get("monthly_investment", ""),
        "opportunity_value_monthly": monthly,
        "opportunity_value_annual": monthly * 12 if monthly else None,
        # --- Report custom fields ---
        "practice_area": report.get("practice_area", payload.get("practice_area", "")),
        "market_type": report.get("market_type", ""),
        "market_summary": report.get("market_summary", ""),
        "estimated_annual_cases_base": mp.get("estimated_annual_cases_base"),
        "case_volume_label": mp.get("case_volume_label", ""),
        "weather_triggers": ", ".join(report.get("weather_triggers", []) or []),
        # --- Standardized report (stored in Cloudinary, named "legal-conquesting-report") ---
        "report_name": REPORT_NAME,
        "report_pdf_url": pdf_url,
        "report_pdf_download_url": download_url or pdf_url,
        "report_pdf_public_id": public_id,
        "report_json": _report_json_str(report),
    }
    _post_webhook(body)


def send_webhook_async(payload: dict, report: Any, status: str, pdf_url: str = "",
                       download_url: str = "", public_id: str = "") -> None:
    """Fire-and-forget webhook post so the API response never waits on GHL."""
    threading.Thread(
        target=send_webhook,
        args=(payload, report, status, pdf_url, download_url, public_id),
        daemon=True,
    ).start()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "smart1legal"})


# This used to promise the plan would be "emailed to you shortly". Nothing in
# this codebase sends an email — so a firm that handed over their name, email
# and ZIP was told to wait for something that would never arrive, and the
# first they'd know was silence.
#
# A person is the delivery mechanism, so the message now says that. Leads
# reach the Hub's lead panel even when the AI call fails, so someone can
# actually see who is waiting and follow up. Change this back only when there
# is a mail step that genuinely fires.
QUEUED_MESSAGE = (
    "Thanks — we have your details. Something went wrong finishing your market "
    "plan automatically, so a Smart 1 strategist will put it together and get "
    "in touch directly. You don't need to do anything else."
)


@app.post("/api/partial-lead")
def partial_lead():
    """Partial lead capture (SPEC §3): fired by the client when the user advances past
    the firm/market step or abandons the page. No email or ZIP requirement — salvage
    whatever came in. Always responds {ok:true}."""
    raw = request.get_json(silent=True) or {}

    # Honeypot: bots fill the hidden "fax" field. Pretend success; never forward.
    if str(raw.get("fax", "")).strip():
        return jsonify({"ok": True})

    # Light, SEPARATE rate limit — must never consume the /api/analyze budget.
    if _rate_limited(_client_ip(), _partial_rate_buckets, PARTIAL_RATE_LIMIT_MAX, RATE_LIMIT_WINDOW):
        return jsonify({"ok": True})

    fields = [
        "lead_id",
        "practice_area",
        "secondary_practice_areas",
        "firm_name",
        "website",
        "firm_zip",
        "target_radius",
        "primary_goal",
        "notes",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "gclid",
        "fbclid",
        "referrer_url",
        "landing_page_url",
    ]
    body = {k: str(raw.get(k, "")).strip()[:1500] for k in fields}
    # Need at least something identifying to be worth forwarding.
    if not body["website"] and not body["firm_name"]:
        return jsonify({"ok": True})
    body["source"] = "Smart 1 Legal Conquesting Market Intelligence"
    body["report_status"] = "partial"
    # Fire-and-forget (same pattern as send_webhook_async) so the beacon returns fast.
    threading.Thread(target=_post_webhook, args=(body,), daemon=True).start()
    return jsonify({"ok": True})


@app.post("/api/analyze")
def analyze():
    raw = request.get_json(silent=True) or {}

    # --- Honeypot: bots fill the hidden "fax" field. Pretend success; never call OpenAI. ---
    if str(raw.get("fax", "")).strip():
        return jsonify({"ok": True, "queued": True, "message": QUEUED_MESSAGE})

    # --- Per-IP rate limit ---
    if _rate_limited(_client_ip()):
        return jsonify({
            "ok": False,
            "error": "Too many requests from this connection. Please wait a bit and try again, "
                     "or book a free consultation and we'll build your plan for you.",
        }), 429

    # --- Validate ---
    try:
        payload = clean_payload(raw)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    # --- CAPTURE-FIRST: the lead reaches GHL the moment they submit, before any AI work. ---
    send_webhook_async(payload, None, "captured")

    firm = payload.get("firm_name", "Legal Market Report")

    # --- Generate. On failure the lead is already safe — return a friendly queued message. ---
    try:
        report = generate_report(payload)
    except Exception:
        app.logger.exception("Report generation failed (lead already captured)")
        send_webhook_async(payload, None, "generation_failed")
        return jsonify({"ok": True, "queued": True, "message": QUEUED_MESSAGE})

    # --- PDF + Cloudinary (guarded; never blocks) then the completed webhook. ---
    pdf_bytes = build_report_pdf(report, firm)
    pdf_info = upload_pdf_to_cloudinary(pdf_bytes, firm) or {}
    pdf_url = pdf_info.get("url", "")
    download_url = pdf_info.get("download_url", "")
    if pdf_bytes and not pdf_url:
        # Cloudinary unconfigured or upload failed — serve the PDF from this process
        # so the on-screen download button still works (see _local_pdfs note above).
        local_id = _store_local_pdf(pdf_bytes)
        pdf_url = request.host_url.rstrip("/") + f"/pdf/{local_id}"
        download_url = pdf_url
    send_webhook_async(payload, report, "completed", pdf_url, download_url, pdf_info.get("public_id", ""))
    return jsonify({
        "ok": True,
        "report": report,
        "report_name": REPORT_NAME,
        "report_pdf_url": pdf_url,
        "report_pdf_download_url": download_url,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
