import json
import os
import re
import time
from typing import Any

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request


# Cloudinary is optional at import time; if the package or CLOUDINARY_URL is
# missing the app still runs and simply falls back to local PDF hosting.
try:
    import cloudinary
    import cloudinary.uploader
    _CLOUDINARY_AVAILABLE = True
except Exception:  # pragma: no cover
    _CLOUDINARY_AVAILABLE = False

# reportlab is pure-Python (no system libraries) so the PDF builder deploys
# cleanly on Render's native Python runtime with no Docker/apt changes.
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

load_dotenv()

app = Flask(__name__)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
# Standard GoHighLevel inbound-webhook URL for the Smart 1 Suite.
WEBHOOK_URL = os.getenv("GHL_WEBHOOK_URL", "").strip()
# Absolute base used to build the fallback report_pdf_url (e.g. https://recruitment-j62u.onrender.com).
# If empty, the app derives it from the incoming request. Only used when Cloudinary is not configured.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
ENABLE_PDF = os.getenv("ENABLE_PDF", "1").strip() not in ("0", "false", "False", "")
REPORT_DIR = os.path.join(app.static_folder, "reports")
# Booking / consultation link surfaced as the call-to-action in the report + PDF.
BOOKING_URL = os.getenv("BOOKING_URL", "https://smart1marketing.com/free-consultation").strip()
# Comma-separated list of origins allowed to call the API cross-origin ("*" = any).
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").strip()

# Cloudinary storage. The library auto-reads the CLOUDINARY_URL env var; we just
# confirm it is present so we know whether to upload or fall back to local files.
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL", "").strip()
CLOUDINARY_FOLDER = os.getenv("CLOUDINARY_FOLDER", "recruitment-reports").strip()
if _CLOUDINARY_AVAILABLE and CLOUDINARY_URL:
    try:
        cloudinary.config(secure=True)
    except Exception:  # pragma: no cover
        pass

SYSTEM_PROMPT = """
You are the Smart 1 Marketing Precision Recruitment Intelligence Architect.
Create a practical, sales-oriented talent-market and candidate-targeting report for a company that is hiring.

IMPORTANT ACCURACY RULES
- You do not have live access to payroll data, BLS tables, LinkedIn, or exact census figures unless supplied in the request.
- Use geographic and labor-market knowledge with conservative planning assumptions. Never claim a figure was live-verified.
- Clearly label all workforce, candidate, and job-seeker figures as AI planning estimates.
- Give ranges, confidence levels, and a short explanation of the assumptions.
- Do not invent precise street addresses. Use recognizable place names, employer clusters, or "Within X of ZIP" plus city/state. An address field may be null.
- Prefer real, well-known institutions you are reasonably confident exist (trade schools, community colleges, universities, major employers). If uncertain, lower confidence.
- Avoid duplicate locations. Favor targetable places: competitor offices/branches, campuses, industrial parks, event venues, union/apprenticeship halls.

TARGET TYPES TO CONSIDER
1. Direct competitors' corporate offices and branch locations (IP conquesting of employed talent)
2. Trade schools, technical/vocational schools, and apprenticeship programs
3. Community colleges and universities with relevant programs
4. Industrial parks, office parks, and business districts dense with adjacent-role workers
5. Job fairs, career expos, and industry event venues
6. Union halls and workforce/career centers
7. Large employers in adjacent industries whose workers have transferable skills
8. Affluent or high-density residential ZIP clusters for specific candidate profiles

CANDIDATE ESTIMATION METHOD
Estimate the total working-age labor force reachable within the requested radius, then estimate the pool of QUALIFIED candidates for the specified role categories using a role-sensitive share of the workforce (skilled/technical roles are a smaller share; general labor/customer service are larger). Then estimate ACTIVE job seekers as a smaller subset currently in-market. Adjust for the local industry mix, education institutions, unemployment conditions, remote vs on-site, seasonality, and the requested roles. Return low/base/high estimates. Do not present these as verified counts.

CHANNEL RULES (RECRUITMENT)
- ALLOWED channels/data ONLY: In-Market Job-Seeker Audience Data (Experian/TrueData "Online Job Seekers" and "New Job" propensity segments), LinkedIn Sponsored Content & Message Ads (targeting job titles, skills, and competitor employees), Programmatic Job-Title Display, Competitor IP Conquesting, Job-Seeker Streaming Audio (Spotify/Pandora) with companion banners, Connected TV (CTV/OTT), YouTube/Online Video, and Website Retargeting.
- ALWAYS include "In-Market Job-Seeker Audience Data" as one of the media_channels chips.
- LinkedIn IS allowed and encouraged for professional, managerial, and specialized/technical roles. For high-volume general labor or trades, weight toward programmatic display, competitor IP conquesting, audio, and CTV instead of LinkedIn.
- NEVER recommend generic paid search or job-board reposting as the core strategy. Do not recommend email or SMS as media channels (those live in the Smart 1 Suite nurture layer, not the media plan).

RECRUITMENT ACTIVATION TRIGGERS
- The plan is paced around hiring signals, NOT weather. Build triggers around real hiring dynamics.
- Keep trigger labels short and punchy, e.g. "New-grad season", "Bonus-season churn", "Competitor layoff signals", "Q1 job-switch surge", "Shift-change dayparts", "Seasonal ramp", "Back-to-work window".
- Treat triggers as budget-pacing and timing signals, not guarantees of applicant volume.

OUTPUT
Return only valid JSON matching the requested schema. Do not use markdown fences.
"""

REPORT_SCHEMA = {
    "name": "recruitment_report",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "market_summary": {"type": "string"},
            "market_type": {"type": "string"},
            "market_type_description": {"type": "string"},
            "market_opportunity": {"type": "string"},
            "market_profile": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "estimated_workforce_low": {"type": "integer"},
                    "estimated_workforce_base": {"type": "integer"},
                    "estimated_workforce_high": {"type": "integer"},
                    "estimated_qualified_candidates_low": {"type": "integer"},
                    "estimated_qualified_candidates_base": {"type": "integer"},
                    "estimated_qualified_candidates_high": {"type": "integer"},
                    "estimated_active_jobseekers_low": {"type": "integer"},
                    "estimated_active_jobseekers_base": {"type": "integer"},
                    "estimated_active_jobseekers_high": {"type": "integer"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "estimated_workforce_low",
                    "estimated_workforce_base",
                    "estimated_workforce_high",
                    "estimated_qualified_candidates_low",
                    "estimated_qualified_candidates_base",
                    "estimated_qualified_candidates_high",
                    "estimated_active_jobseekers_low",
                    "estimated_active_jobseekers_base",
                    "estimated_active_jobseekers_high",
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
            "media_channels": {"type": "array", "items": {"type": "string"}},
            "streaming_audio_note": {"type": "string"},
            "hiring_triggers": {"type": "array", "items": {"type": "string"}},
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
            "target_locations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "city_state": {"type": "string"},
                        "address": {"type": ["string", "null"]},
                        "category": {"type": "string"},
                        "industry_or_segment": {"type": "string"},
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
                        "industry_or_segment",
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
            "market_type",
            "market_type_description",
            "market_opportunity",
            "market_profile",
            "recommended_package",
            "media_channels",
            "streaming_audio_note",
            "hiring_triggers",
            "monthly_plan",
            "target_locations",
            "disclaimer",
        ],
    },
    "strict": True,
}


# Marketing-attribution fields captured from the landing page and passed through
# to the webhook so every lead is tied to the campaign that produced it.
TRACKING_FIELDS = [
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "referrer", "page_url",
]

# Hidden honeypot field name. Real users never see or fill it; bots do.
HONEYPOT_FIELD = "website_hp"

# Additive metadata fields passed straight through to the webhook when present.
# lead_id lets GoHighLevel merge the partial, new_lead, and completed events for
# the same visitor; consent records the report-estimates acknowledgement.
META_FIELDS = ["lead_id", "consent"]

LEAD_FIELDS = [
    "company_name",
    "website",
    "company_zip",
    "target_radius",
    "role_types",
    "hiring_volume",
    "campaign_objective",
    "contact_name",
    "contact_email",
    "contact_phone",
    "notes",
]


@app.after_request
def apply_cors(response):
    """Simple CORS middleware driven by ALLOWED_ORIGINS (declared in render.yaml)."""
    origin = request.headers.get("Origin")
    if not origin:
        return response
    allowed = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
    if "*" in allowed:
        response.headers["Access-Control-Allow-Origin"] = "*"
    elif origin in allowed:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    else:
        return response
    response.headers.setdefault("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    response.headers.setdefault("Access-Control-Allow-Headers", "Content-Type")
    return response


def is_spam(data: dict) -> bool:
    """True when the hidden honeypot field was filled (bot submission)."""
    return bool(str(data.get(HONEYPOT_FIELD, "")).strip())


def clean_payload(data: dict) -> dict:
    cleaned = {k: str(data.get(k, "")).strip()[:1500] for k in LEAD_FIELDS}
    # Pass through marketing attribution + additive metadata (best-effort; never required).
    for k in TRACKING_FIELDS + META_FIELDS:
        val = str(data.get(k, "")).strip()[:600]
        if val:
            cleaned[k] = val
    if not re.fullmatch(r"\d{5}(-\d{4})?", cleaned["company_zip"]):
        raise ValueError("A valid U.S. ZIP code is required.")
    return cleaned


def salvage_partial(data: dict) -> dict:
    """Lenient cleaner for partial leads: no email requirement, no strict ZIP
    validation. Keeps every known field that has a value — never drops website."""
    cleaned = {}
    for k in LEAD_FIELDS:
        val = str(data.get(k, "")).strip()[:1500]
        if val:
            cleaned[k] = val
    for k in TRACKING_FIELDS + META_FIELDS:
        val = str(data.get(k, "")).strip()[:600]
        if val:
            cleaned[k] = val
    return cleaned


# Light in-memory rate bucket for /api/partial-lead ONLY. It deliberately does
# NOT share a budget with the expensive AI endpoint.
_PARTIAL_HITS: dict = {}
_PARTIAL_WINDOW_SECONDS = 60
_PARTIAL_MAX_PER_WINDOW = 10


def partial_rate_ok(ip: str) -> bool:
    now = time.time()
    hits = [t for t in _PARTIAL_HITS.get(ip, []) if now - t < _PARTIAL_WINDOW_SECONDS]
    if len(hits) >= _PARTIAL_MAX_PER_WINDOW:
        _PARTIAL_HITS[ip] = hits
        return False
    hits.append(now)
    _PARTIAL_HITS[ip] = hits
    if len(_PARTIAL_HITS) > 5000:  # bound memory
        _PARTIAL_HITS.clear()
    return True


def generate_report(payload: dict) -> Any:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    user_prompt = (
        "\nBuild a Precision Recruitment Demand & Candidate-Targeting Report from these inputs:\n"
        f"{json.dumps(payload, indent=2)}"
        "\n\nThe company did NOT provide a media budget, competitor list, or lists of local talent hubs. "
        "You must supply all of these yourself:\n"
        "- Identify the local competitors, trade/technical schools, community colleges, universities, industrial "
        "parks, job-fair venues, union/apprenticeship halls, and adjacent large employers yourself from geographic "
        "and labor-market knowledge of the area, and include the best of them in target_locations.\n\n"
        "Populate every field of the schema:\n"
        "- market_summary: one or two sentences framing the hiring opportunity for this company and market "
        "(reference the company name, roles, and area).\n"
        "- market_type: a short badge label for the labor market, e.g. 'Competitive Regional Hiring Market', "
        "'Tight Skilled-Trades Market', or 'Nationwide / Remote Talent Market'. market_type_description: one "
        "sentence on the hiring dynamic.\n"
        "- market_profile: low/base/high estimates for the reachable workforce, qualified candidates for the "
        "requested roles, and active job seekers, plus confidence and assumptions.\n"
        "- market_opportunity: keep this SIMPLE — one short, plain sentence on the company's hiring opportunity.\n"
        "- recommended_package: choose the best-fit package for this market and hiring volume from the Smart 1 menu "
        "below. Use its exact NAME and monthly price as monthly_investment (e.g. '$5,000/month'), and write a short "
        "description of what that level buys. Pick the tier based on hiring volume, role difficulty, and market "
        "competition.\n"
        "  SMART 1 RECRUITMENT PACKAGE MENU (use these, do not invent prices):\n"
        "    * $2,500/month — Pipeline Starter\n"
        "    * $5,000/month — Talent Accelerator\n"
        "    * $7,500/month — Hiring Surge\n"
        "    * $10,000/month — Talent Market Dominance\n"
        "- media_channels: ALLOWED channels/data only. ALWAYS include 'In-Market Job-Seeker Audience Data'. Then "
        "choose from: LinkedIn Sponsored Content & Message Ads, Programmatic Job-Title Display, Competitor IP "
        "Conquesting, Job-Seeker Streaming Audio, Connected TV (CTV/OTT), YouTube / Online Video, Website "
        "Retargeting. Return 4-7 chips total. Include LinkedIn for professional/technical/managerial roles; weight "
        "toward display, IP conquesting, audio, and CTV for high-volume labor/trades. NEVER include generic paid "
        "search, email, or SMS.\n"
        "- streaming_audio_note: a short recommendation to geotarget streaming audio (Spotify/Pandora) around "
        "competitor offices, trade schools, and industrial parks, layered with 'Online Job Seekers' segments. "
        "Specify a commute-friendly daypart (e.g. 6-9am and 3-7pm) with a clickable companion banner to open "
        "positions.\n"
        "- hiring_triggers: 5-8 short trigger labels for this market (e.g. 'New-grad season', 'Bonus-season "
        "churn', 'Competitor layoff signals', 'Q1 job-switch surge', 'Shift-change dayparts', 'Seasonal ramp').\n"
        "- monthly_plan: all 12 months (January through December). Each month has a focus title, a short "
        "candidate-facing message, 1-2 relevant trigger labels drawn from hiring_triggers, and a 'pacing' string. "
        "Match focus to the hiring calendar (new-year job-switch surge in Q1, new-grad reach in spring/summer, "
        "back-to-work and fall push in Q3-Q4, pipeline and brand in quieter months).\n"
        "  BUDGET PACING RULE for the 'pacing' field: the recommended_package monthly_investment is the ACTIVE / "
        "in-push monthly budget (100%). In pipeline-building months spend 50% of the package; in maintenance "
        "months spend 25%. Classify each month as Active, Pipeline, or Maintenance based on this company's hiring "
        "calendar, and set pacing to a short string with the tier, percent, and computed dollar amount — e.g. "
        "'Active — 100% ($5,000)', 'Pipeline — 50% ($2,500)', 'Maintenance — 25% ($1,250)'. Compute the dollars "
        "from the chosen package price.\n"
        "- target_locations: 12-18 locations (competitors, trade schools, colleges, industrial parks, event "
        "venues, union/apprenticeship halls, adjacent employers). Prioritize locations inside the target radius; "
        "lower confidence for uncertain ones. For each, set industry_or_segment (e.g. 'Direct competitors', "
        "'Emerging / entry talent', 'Skilled trades', 'In-industry workforce', 'Active job seekers'). Keep text "
        "concise.\n"
        "- disclaimer: a short note that figures are AI planning estimates for the market, not exact counts.\n"
    )
    # Over HTTP rather than the openai SDK — not a Hub dependency.
    import requests as _rq
    _r = _rq.post("https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"model": MODEL,
              "input": [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt}],
              "text": {"format": {"type": "json_schema", **REPORT_SCHEMA}},
              "temperature": 0.25, "max_output_tokens": 8000},
        timeout=120)
    _r.raise_for_status()
    _data = _r.json()
    try:
        from hub import ai as _hub_ai
        _hub_ai.note_usage("recruit", _data, purpose="market_report")
    except Exception:  # noqa: BLE001
        pass
    text = ""
    for _it in (_data.get("output") or []):
        for _pt in (_it.get("content") or []):
            if _pt.get("type") in ("output_text", "text"):
                text += _pt.get("text") or ""
    text = (text or _data.get("output_text") or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


# ---------------------------------------------------------------------------
# PDF report — reportlab, pure Python. Produces a hosted PDF your team can send
# from Smart1Suite. Guarded so any failure never blocks the lead/webhook.
# ---------------------------------------------------------------------------

NAVY = colors.HexColor("#0a2240")
BLUE = colors.HexColor("#009ed2")
GREEN = colors.HexColor("#2dbb72")
LINE = colors.HexColor("#dbe5ed")
MUTED = colors.HexColor("#68798c")
AQUA = colors.HexColor("#eff9fc")


def _money_to_int(value: str):
    """'$5,000/month' -> 5000 (int) or None."""
    digits = re.sub(r"[^\d]", "", (value or "").split("/")[0])
    return int(digits) if digits else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "report").lower()).strip("-") or "report"


def _pdf_styles():
    ss = getSampleStyleSheet()
    body = ParagraphStyle("s1body", parent=ss["Normal"], fontName="Helvetica",
                          fontSize=9.5, leading=14, textColor=colors.HexColor("#25364b"))
    h2 = ParagraphStyle("s1h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                        fontSize=13, leading=16, textColor=NAVY, spaceBefore=16, spaceAfter=6)
    title = ParagraphStyle("s1title", parent=ss["Title"], fontName="Helvetica-Bold",
                           fontSize=22, leading=25, textColor=NAVY, alignment=TA_LEFT, spaceAfter=4)
    eyebrow = ParagraphStyle("s1eye", parent=body, fontName="Helvetica-Bold",
                             fontSize=8, textColor=BLUE, spaceAfter=2)
    small = ParagraphStyle("s1small", parent=body, fontSize=8, textColor=MUTED, leading=11)
    cell = ParagraphStyle("s1cell", parent=body, fontSize=8, leading=10.5)
    cellw = ParagraphStyle("s1cellw", parent=cell, textColor=colors.white)
    return dict(body=body, h2=h2, title=title, eyebrow=eyebrow, small=small, cell=cell, cellw=cellw)


def report_display_name(company: str) -> str:
    """Human-facing report name, e.g. 'Example Manufacturing-Recruitment - report'."""
    name = (company or "Company").strip()
    return f"{name}-Recruitment - report"


def build_report_pdf(report: dict, company: str) -> str:
    """Render the report JSON to a branded PDF saved under static/reports.
    Returns the local file PATH (or '' if disabled/failed). Publishing to a
    public URL (Cloudinary or local) is handled separately by publish_pdf()."""
    if not ENABLE_PDF:
        return ""
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        st = _pdf_styles()
        fmt = lambda n: f"{int(n):,}" if n is not None else "—"
        rng = lambda a, b: f"{fmt(a)}–{fmt(b)}"
        m = report.get("market_profile", {}) or {}
        rp = report.get("recommended_package", {}) or {}

        # Local filename is slugged + timestamped to stay unique on disk; the
        # human-readable name ("<Company>-Recruitment - report") is used for the
        # PDF title metadata and the Cloudinary public_id.
        filename = f"{_slug(company)}-recruitment-report-{int(time.time())}.pdf"
        path = os.path.join(REPORT_DIR, filename)

        story = []
        story.append(Paragraph("SMART 1 MARKETING &nbsp;|&nbsp; PRECISION RECRUITMENT REPORT", st["eyebrow"]))
        story.append(Paragraph(company or "Recruitment Report", st["title"]))
        story.append(Paragraph(report.get("market_summary", ""), st["body"]))
        story.append(Spacer(1, 6))

        if report.get("market_type"):
            story.append(Paragraph(f"<b>{report.get('market_type')}</b> — {report.get('market_type_description','')}", st["small"]))

        stat_data = [[
            Paragraph(f"<b>{rng(m.get('estimated_workforce_low'), m.get('estimated_workforce_high'))}</b><br/><font size=7 color='#68798c'>ESTIMATED LOCAL WORKFORCE</font>", st["cell"]),
            Paragraph(f"<b>{rng(m.get('estimated_qualified_candidates_low'), m.get('estimated_qualified_candidates_high'))}</b><br/><font size=7 color='#68798c'>QUALIFIED CANDIDATES</font>", st["cell"]),
            Paragraph(f"<b>{rng(m.get('estimated_active_jobseekers_low'), m.get('estimated_active_jobseekers_high'))}</b><br/><font size=7 color='#68798c'>ACTIVE JOB SEEKERS</font>", st["cell"]),
        ]]
        stat = Table(stat_data, colWidths=[2.4 * inch] * 3)
        stat.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), AQUA),
            ("BOX", (0, 0), (-1, -1), 0.5, LINE),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, LINE),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(Spacer(1, 8))
        story.append(stat)

        # Talent pool funnel — simple horizontal bars built from base estimates
        # (brand-colored table cells; mirrors the on-screen funnel).
        funnel_items = [
            ("Local workforce", m.get("estimated_workforce_base"), NAVY),
            ("Qualified candidates", m.get("estimated_qualified_candidates_base"), BLUE),
            ("Active job seekers", m.get("estimated_active_jobseekers_base"), GREEN),
        ]
        max_val = max([v for _, v, _ in funnel_items if v] or [0])
        if max_val:
            story.append(Paragraph("Talent Pool Funnel", st["h2"]))
            bar_area = 4.0 * inch
            rows = []
            for label, val, bar_color in funnel_items:
                frac = max(0.04, min(0.985, (val or 0) / max_val))
                bar = Table([["", ""]],
                            colWidths=[bar_area * frac, bar_area * (1 - frac)],
                            rowHeights=[0.18 * inch])
                bar.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (0, 0), bar_color),
                    ("BACKGROUND", (1, 0), (1, 0), AQUA),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]))
                rows.append([Paragraph(label, st["cell"]), bar,
                             Paragraph(f"<b>{fmt(val)}</b>", st["cell"])])
            ft = Table(rows, colWidths=[1.6 * inch, bar_area + 0.1 * inch, 1.2 * inch])
            ft.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]))
            story.append(Spacer(1, 2))
            story.append(ft)
            story.append(Paragraph("Base-case AI planning estimates within the target radius.", st["small"]))

        story.append(Paragraph("Your Hiring Opportunity", st["h2"]))
        story.append(Paragraph(report.get("market_opportunity", ""), st["body"]))

        # Call-to-action + market-exclusivity nudge
        cta = Table([[Paragraph(
            f"<b>Ready to activate this plan?</b> We work with only one employer per market on this program — "
            f"claim yours before a competitor does. Book your strategy call: "
            f"<b>{BOOKING_URL}</b>", st["body"])]], colWidths=[7.3 * inch])
        cta.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), AQUA),
            ("BOX", (0, 0), (-1, -1), 0.5, BLUE),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ]))
        story.append(Spacer(1, 6))
        story.append(cta)

        story.append(Paragraph("Recommended Package", st["h2"]))
        story.append(Paragraph(f"<b>{rp.get('monthly_investment','')} — {rp.get('package_name','')}</b>", st["body"]))
        story.append(Paragraph(rp.get("description", ""), st["small"]))

        chans = report.get("media_channels", []) or []
        if chans:
            story.append(Paragraph("Recommended Recruitment Channels", st["h2"]))
            story.append(Paragraph(" &nbsp;•&nbsp; ".join(chans), st["body"]))

        trigs = report.get("hiring_triggers", []) or []
        if trigs:
            story.append(Paragraph("Recruitment Activation Triggers", st["h2"]))
            story.append(Paragraph(" &nbsp;•&nbsp; ".join(trigs), st["body"]))

        plan = report.get("monthly_plan", []) or []
        if plan:
            story.append(Paragraph("Month-by-Month Hiring Plan", st["h2"]))
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
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fbfc")]),
                ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            story.append(t)

        geo = sorted(report.get("target_locations", []) or [], key=lambda g: g.get("priority", 3))
        if geo:
            story.append(Paragraph("Recommended Conquest &amp; Targeting Locations", st["h2"]))
            rows = [[Paragraph(f"<b>{h}</b>", st["cellw"]) for h in ("P", "Location", "Category", "Segment", "Method", "Radius", "Conf.")]]
            for g in geo:
                rows.append([
                    Paragraph(f"P{g.get('priority','')}", st["cell"]),
                    Paragraph(f"<b>{g.get('name','')}</b><br/>{g.get('city_state','')}", st["cell"]),
                    Paragraph(g.get("category", ""), st["cell"]),
                    Paragraph(g.get("industry_or_segment", ""), st["cell"]),
                    Paragraph(str(g.get("recommended_method", "")).replace("_", " "), st["cell"]),
                    Paragraph(f"{g.get('recommended_radius_miles','')} mi", st["cell"]),
                    Paragraph(g.get("confidence", ""), st["cell"]),
                ])
            t = Table(rows, colWidths=[0.35 * inch, 2.0 * inch, 1.35 * inch, 1.25 * inch, 1.1 * inch, 0.6 * inch, 0.55 * inch], repeatRows=1)
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fbfc")]),
                ("GRID", (0, 0), (-1, -1), 0.5, LINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(t)

        story.append(Spacer(1, 12))
        story.append(Paragraph(report.get("disclaimer", ""), st["small"]))
        story.append(Spacer(1, 6))
        story.append(Paragraph("Smart 1 Marketing · (614) 536-0768 · smart1marketing.com", st["small"]))

        doc = SimpleDocTemplate(path, pagesize=letter, title=report_display_name(company),
                                leftMargin=0.6 * inch, rightMargin=0.6 * inch,
                                topMargin=0.6 * inch, bottomMargin=0.6 * inch)
        doc.build(story)
        return path
    except Exception:
        app.logger.exception("PDF generation failed")
        return ""


def upload_pdf_to_cloudinary(path: str, company: str) -> str:
    """Upload the report PDF to Cloudinary and return its secure URL, or '' on
    failure / when Cloudinary is not configured. The asset is named
    '<Company>-Recruitment - report' inside the CLOUDINARY_FOLDER."""
    if not (path and _CLOUDINARY_AVAILABLE and CLOUDINARY_URL):
        return ""
    try:
        # Timestamp suffix keeps the public_id unique so two leads from the same
        # company never overwrite each other's report.
        result = cloudinary.uploader.upload(
            path,
            resource_type="raw",             # keep the .pdf intact and directly linkable
            folder=CLOUDINARY_FOLDER,
            public_id=f"{report_display_name(company)}-{int(time.time())}",
            use_filename=False,
            unique_filename=False,
            overwrite=True,
        )
        return result.get("secure_url", "") or result.get("url", "")
    except Exception:
        app.logger.exception("Cloudinary upload failed")
        return ""


def publish_pdf(path: str, company: str, base_url: str) -> str:
    """Return a public URL for the generated PDF. Prefers Cloudinary; falls back
    to the local static file URL when Cloudinary is unavailable."""
    if not path:
        return ""
    url = upload_pdf_to_cloudinary(path, company)
    if url:
        return url
    base = base_url or PUBLIC_BASE_URL
    filename = os.path.basename(path)
    return f"{base.rstrip('/')}/static/reports/{filename}" if base else f"/static/reports/{filename}"


def send_webhook(payload: dict, report: Any, status: str, pdf_url: str = "") -> None:
    """Deliver the lead through the Hub's lead panel.

    Was a fire-and-forget POST that returned silently with no WEBHOOK_URL, so
    a blank env var discarded every lead while the visitor saw success. The
    panel stores first and forwards second.
    """
    try:
        from hub import leads as hub_leads
        hub_leads.capture_and_deliver(
            source="recruit",
            page="Recruitment Market Plan",
            fields={
                "name": payload.get("contact_name", ""),
                "email": payload.get("contact_email", ""),
                "phone": payload.get("contact_phone", ""),
                "company": payload.get("company_name", ""),
                "report_status": status,
            },
            pdf_url=pdf_url or "",
            client=payload.get("company_name", ""))
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
        "source": "Smart 1 Precision Recruitment Intelligence",
        "report_status": status,
        # --- Opportunity fields ---
        "opportunity_name": f"{payload.get('company_name', 'Lead')} — Recruitment Plan",
        "recommended_package": rp.get("package_name", ""),
        "recommended_investment": rp.get("monthly_investment", ""),
        "opportunity_value_monthly": monthly,
        "opportunity_value_annual": monthly * 12 if monthly else None,
        # --- Report custom fields ---
        "market_type": report.get("market_type", ""),
        "market_summary": report.get("market_summary", ""),
        "est_qualified_candidates": mp.get("estimated_qualified_candidates_base"),
        "estimated_active_jobseekers_base": mp.get("estimated_active_jobseekers_base"),
        "hiring_triggers": ", ".join(report.get("hiring_triggers", []) or []),
        "report_name": report_display_name(payload.get("company_name", "Company")),
        "report_pdf_url": pdf_url,
        "report_json": json.dumps(report, separators=(",", ":"))[:60000],
    }
    if status == "failed":
        # No report exists on failure — omit empty report keys entirely so the
        # webhook doesn't blank out fields GHL already holds for this contact.
        # Sends only meta + non-empty lead fields + report_status.
        body.pop("report_json", None)
        body = {k: v for k, v in body.items() if v not in ("", None)}
        body["report_status"] = "failed"
    try:
        requests.post(WEBHOOK_URL, json=body, timeout=12)
    except requests.RequestException:
        app.logger.exception("Webhook delivery failed")


@app.get("/")
def index():
    return render_template("index.html", booking_url=BOOKING_URL)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/lead")
def lead():
    """Progressive lead capture. The landing page calls this the instant the form
    is submitted — BEFORE the (slower) AI report call — so a lead lands in the CRM
    even if generation is slow, fails, or the visitor closes the tab. GoHighLevel
    upserts the contact by email and later updates it when '/api/analyze' fires
    'completed'."""
    data = request.get_json(silent=True) or {}
    if is_spam(data):
        return jsonify({"ok": False, "error": "blocked"}), 400
    try:
        payload = clean_payload(data)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    send_webhook(payload, None, "new_lead")
    return jsonify({"ok": True})


@app.post("/api/partial-lead")
def partial_lead():
    """Partial lead capture (SPEC §3). Fired by the page when the visitor
    advances past step 1 or abandons the tab. No email requirement, no strict
    ZIP validation — salvage whatever came in (never drop website). Uses its own
    light rate bucket, never the AI endpoint's budget. Always responds ok:true."""
    data = request.get_json(silent=True, force=True) or {}
    if is_spam(data):
        return jsonify({"ok": True})
    ip = (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or request.remote_addr or "?")
    if not partial_rate_ok(ip):
        return jsonify({"ok": True})
    cleaned = salvage_partial(data)
    # Need at least something identifying before bothering the CRM.
    if not (cleaned.get("website") or cleaned.get("company_name")):
        return jsonify({"ok": True})
    if WEBHOOK_URL:
        body = {
            "source": "Smart 1 Precision Recruitment Intelligence",
            "report_status": "partial",
            "lead_id": cleaned.get("lead_id", ""),
            **cleaned,
        }
        try:
            requests.post(WEBHOOK_URL, json=body, timeout=8)
        except requests.RequestException:
            app.logger.exception("Partial-lead webhook delivery failed")
    return jsonify({"ok": True})


@app.post("/api/analyze")
def analyze():
    try:
        data = request.get_json(silent=True) or {}
        if is_spam(data):
            return jsonify({"ok": False, "error": "blocked"}), 400
        payload = clean_payload(data)
        report = generate_report(payload)
        base_url = PUBLIC_BASE_URL or request.url_root
        company = payload.get("company_name", "Company")
        pdf_path = build_report_pdf(report, company)
        pdf_url = publish_pdf(pdf_path, company, base_url)
        send_webhook(payload, report, "completed", pdf_url)
        return jsonify({"ok": True, "report": report, "report_pdf_url": pdf_url})
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        app.logger.exception("Analysis failed")
        try:
            send_webhook(clean_payload(request.get_json(silent=True) or {}), None, "failed")
        except Exception:
            pass
        return (
            jsonify(
                {
                    "ok": False,
                    "error": "The report could not be generated. Check the server configuration and try again.",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            ),
            500,
        )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
