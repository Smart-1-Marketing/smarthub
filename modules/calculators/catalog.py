"""Calculator definitions and math.

Every calculator is declared here as data (fields) plus one compute function.
The templates render the fields; the browser never runs the math. That is what
makes the lead gate real: the teaser metrics come back from the server and the
detailed plan is withheld until a contact is captured.

To add a calculator: append an entry to CALCULATORS with a compute function.
No template or JavaScript changes are needed.
"""

# --- Model assumptions -------------------------------------------------------
# Every number a calculator can quote is in this block. Change it here, not in
# the compute functions, so the assumptions stay auditable and quotable.

ASSUMPTIONS = {
    "weeks_per_month": 4.345,
    "audio_listen_through": 0.95,
    "ctv_completion_rate": 0.95,
    "ctv_youtube_cpm_adder": 8.0,
    "ctv_retargeting_budget_pct": 0.10,
    "dooh_cpm": 18.0,
    "dooh_view_rate": 0.70,
    "dooh_min_venues": 3,
    "female_channels": [
        ("Native Display", 13.50),
        ("Contextual", 4.00),
        ("Video", 12.00),
        ("Site Targeting", 4.00),
        ("DOOH", 18.00),
    ],
}


# --- Small helpers -----------------------------------------------------------

def _money(n):
    return "${:,.0f}".format(round(n))


def _num(n):
    return "{:,.0f}".format(round(n))


def _f(payload, key, default=0.0):
    """Float from the payload, tolerant of blanks, commas and stray currency."""
    raw = payload.get(key, default)
    if raw is None or raw == "":
        return float(default)
    if isinstance(raw, (int, float)):
        return float(raw)
    cleaned = "".join(c for c in str(raw) if c.isdigit() or c in ".-")
    # Guard against the "$2,500-$5,000" -> 25005000 bug found in the suite audit:
    # keep only the first number in a range.
    for sep in ("-", "\u2013"):
        if sep in cleaned[1:]:
            cleaned = cleaned[0] + cleaned[1:].split(sep)[0]
            break
    try:
        return float(cleaned)
    except ValueError:
        return float(default)


def _s(payload, key, default=""):
    v = payload.get(key, default)
    return "" if v is None else str(v).strip()


def _list(payload, key):
    v = payload.get(key) or []
    if isinstance(v, str):
        v = [v]
    return [str(x).strip() for x in v if str(x).strip()]


def _label_for(fields, field_id, value):
    """Human label for a select value, so results read the way the form did."""
    for f in fields:
        if f["id"] == field_id:
            for opt in f.get("options", []):
                if str(opt["value"]) == str(value):
                    return opt["label"]
    return str(value)


class CalcError(ValueError):
    """Raised when input can't produce a sensible estimate. Shown to the user."""


# --- 1. Digital Audio --------------------------------------------------------

AUDIO_FIELDS = [
    {"id": "budget", "label": "Monthly advertising budget", "type": "number",
     "default": 3000, "min": 500, "step": 100, "prefix": "$"},
    {"id": "weeks", "label": "Campaign length (weeks)", "type": "number",
     "default": 4, "min": 1, "max": 52, "step": 1},
    {"id": "placement", "label": "Audio placement", "type": "select", "default": "Streaming Audio",
     "options": [{"value": v, "label": v} for v in ("Streaming Audio", "Podcasts", "Both")]},
    {"id": "tier", "label": "Targeting tier", "type": "select", "default": "22", "options": [
        {"value": "18", "label": "Run of Network"},
        {"value": "22", "label": "Targeted Audience"},
        {"value": "26", "label": "Highly Targeted"},
    ]},
    {"id": "radius", "label": "Geographic radius", "type": "select", "default": "25 miles",
     "options": [{"value": v, "label": v} for v in
                 ("5 miles", "10 miles", "25 miles", "50 miles", "Custom market")]},
    {"id": "goal", "label": "Campaign goal", "type": "select", "default": "Awareness",
     "options": [{"value": v, "label": v} for v in
                 ("Awareness", "Store Traffic", "Website Visits", "Lead Generation", "Event Promotion")]},
    {"id": "frequency", "label": "Desired frequency", "type": "range", "default": 6,
     "min": 1, "max": 15, "step": 1, "width": "full", "suffix": "x per person"},
]


def compute_audio(p, fields):
    monthly = _f(p, "budget")
    weeks = _f(p, "weeks", 4) or 4
    cpm = _f(p, "tier", 22) or 22
    freq = max(1.0, _f(p, "frequency", 6))
    if monthly <= 0:
        raise CalcError("Enter a monthly budget to estimate delivery.")

    campaign = monthly * (weeks / ASSUMPTIONS["weeks_per_month"])
    impressions = campaign / cpm * 1000
    reach = impressions / freq
    listens = impressions * ASSUMPTIONS["audio_listen_through"]
    tier_label = _label_for(fields, "tier", _s(p, "tier", "22"))

    return {
        "metrics": [
            {"label": "Campaign budget", "value": _money(campaign)},
            {"label": "Impressions", "value": _num(impressions)},
            {"label": "Estimated reach", "value": _num(reach)},
            {"label": "Completed listens", "value": _num(listens)},
        ],
        "teaser_note": "Reach is the number of separate people we expect to hit at "
                       "{:.0f}x each. Completed listens assume a 95% listen-through rate.".format(freq),
        "detail": {
            "summary": "{} on {} across a {} radius, {:.0f}x frequency over {:.0f} week(s). "
                       "Goal: {}.".format(_s(p, "placement"), tier_label.lower(), _s(p, "radius"),
                                          freq, weeks, _s(p, "goal")),
            "table": {
                "headers": ["Line", "Estimate"],
                "rows": [
                    ["Placement", _s(p, "placement")],
                    ["Targeting tier", tier_label],
                    ["Working media", _money(campaign)],
                    ["Impressions", _num(impressions)],
                    ["Unique reach", _num(reach)],
                    ["Completed listens", _num(listens)],
                    ["Frequency target", "{:.0f}x".format(freq)],
                ],
            },
            "next_steps": [
                "Confirm the market and radius against real household counts.",
                "Split delivery between streaming audio and podcast inventory.",
                "Set companion banner and call-tracking before launch.",
            ],
        },
        "lead_value": campaign,
    }


# --- 2. Connected TV ---------------------------------------------------------

CTV_FIELDS = [
    {"id": "budget", "label": "Monthly advertising budget", "type": "number",
     "default": 5000, "min": 1000, "step": 250, "prefix": "$"},
    {"id": "weeks", "label": "Campaign length (weeks)", "type": "number",
     "default": 4, "min": 1, "max": 52, "step": 1},
    {"id": "tier", "label": "Targeting tier", "type": "select", "default": "28", "options": [
        {"value": "22", "label": "Run of Network"},
        {"value": "28", "label": "Targeted Audience"},
        {"value": "35", "label": "Highly Targeted"},
        {"value": "45", "label": "Premium Inventory"},
    ]},
    {"id": "market", "label": "Geographic market", "type": "text",
     "placeholder": "ZIP, city, DMA or service area"},
    {"id": "goal", "label": "Campaign goal", "type": "select", "default": "Awareness",
     "options": [{"value": v, "label": v} for v in
                 ("Awareness", "Website Visits", "Lead Generation", "Store Traffic", "Event Promotion")]},
    {"id": "frequency", "label": "Desired frequency", "type": "select", "default": "5",
     "options": [{"value": v, "label": v + "x per person"} for v in ("3", "5", "7", "10")]},
    {"id": "yt", "label": "Include YouTube TV / premium TV inventory", "type": "checkbox"},
    {"id": "retarget", "label": "Include a retargeting layer", "type": "checkbox"},
]


def compute_ctv(p, fields):
    monthly = _f(p, "budget")
    weeks = _f(p, "weeks", 4) or 4
    base = _f(p, "tier", 28) or 28
    freq = max(1.0, _f(p, "frequency", 5))
    if monthly <= 0:
        raise CalcError("Enter a monthly budget to estimate delivery.")

    yt = bool(p.get("yt"))
    retarget = bool(p.get("retarget"))
    campaign = monthly * (weeks / ASSUMPTIONS["weeks_per_month"])
    cpm = base + (ASSUMPTIONS["ctv_youtube_cpm_adder"] if yt else 0)
    media = campaign * (1 - ASSUMPTIONS["ctv_retargeting_budget_pct"] if retarget else 1)
    imp = media / cpm * 1000
    reach = imp / freq
    views = imp * ASSUMPTIONS["ctv_completion_rate"]
    tier_label = _label_for(fields, "tier", _s(p, "tier", "28"))

    rows = [
        ["Targeting", tier_label],
        ["Market", _s(p, "market") or "To be confirmed"],
        ["Working media", _money(media)],
        ["Impressions", _num(imp)],
        ["Unique households reached", _num(reach)],
        ["Completed views", _num(views)],
    ]
    if yt:
        rows.append(["Premium / YouTube TV", "Included"])
    if retarget:
        rows.append(["Retargeting layer",
                     "{} of budget ({})".format(
                         "{:.0f}%".format(ASSUMPTIONS["ctv_retargeting_budget_pct"] * 100),
                         _money(campaign - media))])

    return {
        "metrics": [
            {"label": "Campaign budget", "value": _money(campaign)},
            {"label": "Estimated impressions", "value": _num(imp)},
            {"label": "Estimated reach", "value": _num(reach)},
            {"label": "Completed views", "value": _num(views)},
        ],
        "teaser_note": "Completed views assume a 95% completion rate, which is typical for "
                       "non-skippable CTV inventory.",
        "detail": {
            "summary": "{} in {} at {:.0f}x frequency{}{}. Goal: {}.".format(
                tier_label, _s(p, "market") or "your selected market", freq,
                ", with premium/YouTube TV inventory" if yt else "",
                ", plus retargeting" if retarget else "", _s(p, "goal")),
            "table": {"headers": ["Line", "Estimate"], "rows": rows},
            "next_steps": [
                "Lock the market definition to a DMA or radius before pricing.",
                "Confirm creative lengths — :15 and :30 deliver differently.",
                "Add a QR or clean-domain end card; CTV has no clickthrough.",
            ],
        },
        "lead_value": campaign,
    }


# --- 3. DOOH -----------------------------------------------------------------

DOOH_VENUES = ["Gas Stations", "Gyms", "Bars & Restaurants", "Grocery", "Retail",
               "Medical", "Entertainment", "Transit", "Office / Business"]

DOOH_FIELDS = [
    {"id": "budget", "label": "Campaign budget", "type": "number",
     "default": 5000, "min": 1000, "step": 250, "prefix": "$"},
    {"id": "frequency", "label": "Desired frequency", "type": "number",
     "default": 6, "min": 1, "max": 20, "step": 1, "suffix": "x per person"},
    {"id": "market", "label": "Market or geography", "type": "text", "width": "full",
     "placeholder": "ZIP, city, DMA or target area"},
    {"id": "venues", "label": "Venue types", "type": "checkgroup", "width": "full",
     "help": "Choose at least three. A mix beats a single venue type on both reach and cost.",
     "options": [{"value": v, "label": v} for v in DOOH_VENUES]},
]


def compute_dooh(p, fields):
    budget = _f(p, "budget")
    freq = max(1.0, _f(p, "frequency", 6))
    venues = [v for v in _list(p, "venues") if v in DOOH_VENUES]
    if budget <= 0:
        raise CalcError("Enter a campaign budget to estimate delivery.")
    if len(venues) < ASSUMPTIONS["dooh_min_venues"]:
        raise CalcError("Choose at least {} venue types to build a diversified plan.".format(
            ASSUMPTIONS["dooh_min_venues"]))

    imp = budget / ASSUMPTIONS["dooh_cpm"] * 1000
    view = imp * ASSUMPTIONS["dooh_view_rate"]
    reach = view / freq
    per_venue = view / len(venues)

    return {
        "metrics": [
            {"label": "Media impressions", "value": _num(imp)},
            {"label": "Viewable impressions", "value": _num(view)},
            {"label": "Estimated reach", "value": _num(reach)},
            {"label": "Venue types", "value": str(len(venues))},
        ],
        "teaser_note": "Viewable impressions apply a 70% view rate to raw plays. Reach divides "
                       "those by your {:.0f}x frequency target.".format(freq),
        "detail": {
            "summary": "{}. Delivery spreads across {} venue categories at a {:.0f}x "
                       "frequency target.".format(_s(p, "market") or "Selected market",
                                                  len(venues), freq),
            "table": {
                "headers": ["Venue type", "Share of viewable impressions"],
                "rows": [[v, _num(per_venue)] for v in venues],
            },
            "next_steps": [
                "Confirm screen availability by venue in your market.",
                "Set dayparts — gyms and transit peak at opposite ends of the day.",
                "Pair with mobile retargeting of devices seen near the screens.",
            ],
        },
        "lead_value": budget,
    }


# --- 4. IMS Advertising Trade ------------------------------------------------

TRADE_SERVICES = [
    ("Website Design / Site Remodeling", "trade"),
    ("Smart 1 Suite", "trade"),
    ("Monthly Blog Creation", "trade"),
    ("Smart 1 SNAP", "trade"),
    ("Social Media Content Calendar", "trade"),
    ("Digital Audit", "trade"),
    ("Paid Search", "cash"),
    ("Streaming / CTV", "cash"),
    ("Paid Social", "cash"),
    ("DOOH", "cash"),
    ("Streaming Radio / Podcast", "cash"),
]
TRADE_TYPE = dict(TRADE_SERVICES)

TRADE_FIELDS = [
    {"id": "trade", "label": "Trade value available", "type": "number",
     "default": 5000, "min": 0, "step": 100, "prefix": "$"},
    {"id": "cash", "label": "Monthly cash media budget", "type": "number",
     "default": 2500, "min": 0, "step": 100, "prefix": "$"},
    {"id": "services", "label": "What are you interested in?", "type": "checkgroup", "width": "full",
     "help": "Smart 1 services can be structured as 100% trade. Media bought from an outside "
             "network carries a cash cost.",
     "options": [{"value": name, "label": name} for name, _ in TRADE_SERVICES]},
]


def compute_trade(p, fields):
    trade_value = _f(p, "trade")
    cash_value = _f(p, "cash")
    picked = [s for s in _list(p, "services") if s in TRADE_TYPE]
    if not picked:
        raise CalcError("Choose at least one service to structure.")

    trade_only = [s for s in picked if TRADE_TYPE[s] == "trade"]
    cash_media = [s for s in picked if TRADE_TYPE[s] == "cash"]

    rows = []
    for s in picked:
        if TRADE_TYPE[s] == "trade":
            rows.append([s, "Eligible for 100% trade consideration"])
        else:
            rows.append([s, "Trade offsets the Smart 1 management fee; outside media cost is cash"])

    return {
        "metrics": [
            {"label": "Trade available", "value": _money(trade_value)},
            {"label": "Cash media budget", "value": _money(cash_value)},
            {"label": "100% trade services", "value": str(len(trade_only))},
            {"label": "Trade + cash services", "value": str(len(cash_media))},
        ],
        "teaser_note": "Trade covers Smart 1 work. Cash is needed wherever an outside "
                       "advertising network or media vendor invoices us.",
        "detail": {
            "summary": "{} service(s) can be structured as 100% trade and {} media service(s) "
                       "carry an outside cash component. Final eligibility and valuation are "
                       "confirmed in your written proposal.".format(len(trade_only), len(cash_media)),
            "table": {"headers": ["Selected service", "How it can be structured"], "rows": rows},
            "next_steps": [
                "Confirm the trade inventory and its retail valuation.",
                "Set the monthly cash floor for outside media.",
                "Sign the trade agreement before the first flight.",
            ],
        },
        "lead_value": trade_value + cash_value,
    }


# --- 5. Female 18–34 market --------------------------------------------------

FEMALE_FIELDS = [
    {"id": "market", "label": "Market or ZIP", "type": "text", "placeholder": "ZIP or market name"},
    {"id": "radius", "label": "Radius", "type": "select", "default": "20 miles",
     "options": [{"value": v, "label": v} for v in
                 ("10 miles", "20 miles", "25 miles", "50 miles")]},
    {"id": "femalePop", "label": "Females age 18–34 in the market", "type": "number",
     "default": 25000, "min": 0, "step": 100},
    {"id": "collegePop", "label": "Of those, enrolled in higher education", "type": "number",
     "default": 7500, "min": 0, "step": 100},
    {"id": "audience", "label": "Audience to model", "type": "select", "default": "femalePop",
     "width": "full", "options": [
         {"value": "femalePop", "label": "All females age 18–34"},
         {"value": "collegePop", "label": "Higher-education estimate only"},
     ]},
]


def _female_spend(pop, freq, cpm, channel_count):
    return (pop * freq / channel_count) / 1000 * cpm


def compute_female(p, fields):
    key = _s(p, "audience", "femalePop")
    if key not in ("femalePop", "collegePop"):
        key = "femalePop"
    pop = _f(p, key)
    if pop <= 0:
        raise CalcError("Enter an audience size above zero to model spend.")

    channels = ASSUMPTIONS["female_channels"]
    n = len(channels)

    def total(freq):
        return sum(_female_spend(pop, freq, cpm, n) for _, cpm in channels)

    rows = []
    for name, cpm in channels:
        rows.append([
            name, "${:,.2f}".format(cpm),
            _money(_female_spend(pop, 4, cpm, n)),
            _money(_female_spend(pop, 8, cpm, n)),
            _money(_female_spend(pop, 12, cpm, n)),
        ])
    rows.append(["Total", "—", _money(total(4)), _money(total(8)), _money(total(12))])

    audience_label = _label_for(fields, "audience", key)

    return {
        "metrics": [
            {"label": "Target audience", "value": _num(pop)},
            {"label": "Good · 4x", "value": _money(total(4))},
            {"label": "Better · 8x", "value": _money(total(8))},
            {"label": "Best · 12x", "value": _money(total(12))},
        ],
        "teaser_note": "Each level buys the same audience at a higher frequency. Impressions "
                       "split evenly across five tactics.",
        "detail": {
            "summary": "{} · {} · {} — modeled audience {}. Good targets 4x frequency, "
                       "Better 8x, Best 12x.".format(_s(p, "market") or "Selected market",
                                                     _s(p, "radius"), audience_label.lower(),
                                                     _num(pop)),
            "table": {"headers": ["Channel", "CPM", "Good 4x", "Better 8x", "Best 12x"], "rows": rows},
            "next_steps": [
                "Validate the population estimate against census data for the radius.",
                "Decide whether the higher-education segment runs as its own flight.",
                "Weight the channel split once creative formats are set.",
            ],
        },
        "lead_value": total(8),
    }


# --- Registry ----------------------------------------------------------------

CALCULATORS = {
    "digital-audio": {
        "slug": "digital-audio",
        "title": "Digital Audio Reach & Budget Calculator",
        "short_title": "Digital Audio",
        "tagline": "Estimate streaming audio and podcast reach, frequency and completed listens.",
        "gate_heading": "See your full audio plan",
        "gate_blurb": "The line-by-line delivery estimate and launch checklist are in the full plan.",
        "cta": "Show my audio plan",
        "fields": AUDIO_FIELDS,
        "compute": compute_audio,
    },
    "ctv": {
        "slug": "ctv",
        "title": "Connected TV Reach & Budget Calculator",
        "short_title": "Connected TV",
        "tagline": "Estimate CTV impressions, household reach and completed views for a market.",
        "gate_heading": "See your full CTV plan",
        "gate_blurb": "The full plan breaks out working media, inventory mix and what to set up before launch.",
        "cta": "Show my CTV plan",
        "fields": CTV_FIELDS,
        "compute": compute_ctv,
    },
    "dooh": {
        "slug": "dooh",
        "title": "DOOH Reach Calculator",
        "short_title": "DOOH",
        "tagline": "Build a diversified digital out-of-home venue mix and estimate viewable reach.",
        "gate_heading": "See your venue-level plan",
        "gate_blurb": "The full plan splits delivery across every venue type you picked.",
        "cta": "Show my venue plan",
        "fields": DOOH_FIELDS,
        "compute": compute_dooh,
    },
    "trade": {
        "slug": "trade",
        "title": "IMS Advertising Trade Calculator",
        "short_title": "Advertising Trade",
        "tagline": "See what can be structured through advertising trade and where cash is required.",
        "gate_heading": "See how your trade is structured",
        "gate_blurb": "The full breakdown shows service by service what trade covers and what stays cash.",
        "cta": "Show my trade structure",
        "fields": TRADE_FIELDS,
        "compute": compute_trade,
    },
    "female-18-34": {
        "slug": "female-18-34",
        "title": "Female 18–34 Market Calculator",
        "short_title": "Female 18–34",
        "tagline": "Model Good / Better / Best media investment for females age 18–34 in a market.",
        "gate_heading": "See the channel-by-channel numbers",
        "gate_blurb": "The full plan prices every tactic at all three frequency levels.",
        "cta": "Show my channel plan",
        "fields": FEMALE_FIELDS,
        "compute": compute_female,
    },
}

ORDER = ["digital-audio", "ctv", "dooh", "trade", "female-18-34"]


def get(slug):
    return CALCULATORS.get(slug)


def all_calculators():
    return [CALCULATORS[s] for s in ORDER if s in CALCULATORS]


def run(slug, payload):
    """Compute a result. Raises CalcError with a message safe to show a customer."""
    calc = CALCULATORS.get(slug)
    if not calc:
        raise CalcError("Unknown calculator.")
    return calc["compute"](payload or {}, calc["fields"])
