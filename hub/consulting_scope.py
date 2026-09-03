"""What the Strategy Engagement line covers, and why each row is or isn't in it.

Transcribed from the agency's own "Strategy & Consulting Service" scope
table -- twenty rows describing everything a consulting engagement can cover,
from the overall marketing plan down to executive guidance. Every consulting
line quoted here used to carry the same blank description until a rep typed
one from memory, so the same engagement read differently depending on who
built the proposal and a plan built from stock creative alone could be sold
"SEO / GEO / Local Strategy" it had no page to act on.

`description()` is a suggestion, never a spec: it only ever seeds a *blank*
description field, the overlay rule this codebase applies everywhere --
`hub/client_urls.py`'s contact fields, `hub/schema_questions.py`'s reviewer
answers -- a value someone typed is the better source and is never offered
over.
"""
from __future__ import annotations

# category (rate-card CATEGORY string, upper-cased) -> scope keys it engages.
# Only spellings this Hub's own rate card actually uses -- the ALIASES rule
# `hub/config.py` applies to env var names, one table over: a speculative
# category costs nothing to add and would never fire.
_CATEGORY_SCOPE = {
    "SEARCH ENGINE MARKETING / PAY PER CLICK": ("channel", "lead_journey"),
    "SEO": ("seo",),
    "META": ("channel", "creative"),
    "SOCIAL ADS - VIDEO": ("channel", "creative"),
    "OTT": ("channel", "creative", "seasonal", "vendor"),
    "OTT/CTV": ("channel", "creative", "seasonal", "vendor"),
    "DIGITAL RADIO": ("channel", "seasonal", "vendor"),
    "DOOH": ("channel", "seasonal", "vendor"),
    "YOUTUBE": ("channel", "creative"),
    "RETARGETING": ("audience", "channel"),
    "DATA TARGETED DISPLAY": ("audience", "channel"),
    "DISPLAY": ("channel", "creative"),
    "LOCATION LOOKBACK": ("audience",),
    "MOBILE ONLY": ("audience", "channel"),
    "EMAIL MARKETING": ("lead_journey",),
    "WEBSITE": ("website",),
}

# objective (the wizard's own Goal step values) -> scope keys it engages.
_OBJECTIVE_SCOPE = {
    "Lead Generation": ("lead_journey", "audience"),
    "Website Traffic": ("website",),
    "Conversions": ("lead_journey", "website"),
    "Phone Calls": ("lead_journey",),
    "Event Promotion": ("seasonal",),
    "Store Visits": ("seasonal", "audience"),
    "Recruitment": ("audience",),
    "Brand Awareness": ("creative",),
}

# key -> (title, detail), transcribed from the scope table, table order.
SCOPE_ITEMS = [
    ("overall", "Overall Marketing Strategy",
     "The overall marketing plan, priorities and opportunities, and the "
     "role each channel plays toward the business objectives."),
    ("budget", "Budget Planning & Allocation",
     "Recommended investment level and allocation across channels, "
     "shifted for performance, seasonality and opportunity."),
    ("audience", "Audience & Market Strategy",
     "Customer segments, demographics, behaviors, geography and audience "
     "targeting across the campaign."),
    ("research", "Competitive & Market Research",
     "Competitor positioning, market conditions, messaging and media "
     "activity, and the gaps this campaign can exploit."),
    ("channel", "Channel Strategy",
     "The combination of channels this campaign runs and why each one "
     "earns its place in the plan."),
    ("planning", "Campaign Planning",
     "Campaign structure, targeting, timing, offers and how each channel "
     "works together rather than in isolation."),
    ("seasonal", "Seasonal / Promotional Planning",
     "A calendar built around seasons, promotions, events and demand "
     "cycles specific to this business."),
    ("oversight", "Performance Oversight",
     "Ongoing review of pacing, lead volume, cost per lead and channel "
     "performance against the plan."),
    ("optimization", "Optimization Direction",
     "Budget reallocation, targeting refinement, creative rotation and "
     "landing-page direction as the campaign runs."),
    ("creative", "Creative & Messaging Direction",
     "Positioning, offers, campaign themes and creative priorities across "
     "every channel in the plan."),
    ("website", "Website / CRO Strategy",
     "Landing pages reviewed as part of the marketing system, with "
     "conversion, form and funnel recommendations."),
    ("seo", "SEO / GEO / Local Strategy",
     "Keyword and content priorities, local search and Google Business "
     "Profile direction, and AI/GEO visibility."),
    ("lead_journey", "Lead Journey & CRM Strategy",
     "How leads are captured, routed and followed up, including nurture "
     "and Smart 1 Suite recommendations."),
    ("tracking", "Tracking & Measurement Strategy",
     "Conversions and KPIs defined up front, with the tracking each one "
     "needs to be measured honestly."),
    ("reporting", "Reporting & Business Insights",
     "Dashboards plus the commentary and next actions behind the "
     "numbers -- not simply a report."),
    ("meetings", "Strategic Meetings",
     "Recurring strategy meetings with the client, on a bi-monthly "
     "cadence, to review results and direction together."),
    ("coordination", "Client / Internal Coordination",
     "Coordinating the client's team, Smart 1 specialists and any "
     "outside vendors around one strategy."),
    ("vendor", "Vendor & Media Management",
     "Evaluating and coordinating the outside media or vendors this plan "
     "touches."),
    ("calendar", "Marketing Calendar / Project Priorities",
     "What gets worked on each month, launch schedules and priority "
     "projects."),
    ("executive", "Executive Marketing Guidance",
     "Guidance for ownership on where to spend, what to stop, and what "
     "opportunities deserve investment next."),
]
_BY_KEY = {k: (title, detail) for k, title, detail in SCOPE_ITEMS}

# Always in scope: deciding the mix, watching it and reporting on it is what
# "strategy and consulting" means whatever is actually being bought --
# including the bi-monthly review meetings, which are the deliverable this
# was written for.
_ALWAYS = ("overall", "budget", "research", "channel", "planning",
           "oversight", "optimization", "creative", "tracking",
           "reporting", "meetings", "coordination", "calendar", "executive")


def applicable_keys(state) -> list:
    """Which scope rows this campaign's own channels and goals engage.

    The baseline rows apply to every engagement. The rest are conditioned
    on what is actually on the plan, so a display-only campaign is not
    handed "SEO / GEO / Local Strategy" it has no page to act on.
    """
    keys = list(_ALWAYS)
    cats = {str((i or {}).get("category") or "").strip().upper()
            for i in (state or {}).get("items") or []}
    objs = set((state or {}).get("objectives") or [])
    for cat in cats:
        for k in _CATEGORY_SCOPE.get(cat, ()):
            if k not in keys:
                keys.append(k)
    for obj in objs:
        for k in _OBJECTIVE_SCOPE.get(obj, ()):
            if k not in keys:
                keys.append(k)
    order = [k for k, _t, _d in SCOPE_ITEMS]
    return [k for k in order if k in keys]


def description(state) -> str:
    """A description built from what this campaign actually engages.

    Bulleted, in the shape `hub/proposal_spec.py`'s `bullets()` reads back
    into a list block on the client's own document, rather than one long
    sentence that renders as a paragraph nobody can skim.
    """
    keys = applicable_keys(state)
    return "\n".join(f"• {_BY_KEY[k][0]} — {_BY_KEY[k][1]}" for k in keys)
