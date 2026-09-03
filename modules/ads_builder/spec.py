"""What a Smart 1 Ads campaign is asked, and how each answer is said.

One vocabulary, read by the generator form, the AI prompt, the internal
proposal, the client estimate and the export alike — the same reason
``hub/proposal_spec.py`` exists. Changing what a campaign captures is one edit
here rather than five in sync.

Rules this file exists to hold:

* **An intake answer that was captured must be shown.** The estimate a client
  reads used to open on a budget and a keyword list, with none of what the rep
  actually asked them at the start — so the client could not tell that the
  campaign had been built around their answers, and the rep could not point at
  them. ``sections()`` is what the estimate prints, in the order it prints it.

* **An average CPC is an industry estimate, always labelled.** It is a sector
  benchmark, not this client's measured cost, and it appears on a document a
  client makes a spending decision from. ``CPC_NOTE`` is one string so no
  screen can show the number without the caveat — ``test_ads_module.py``
  asserts every template that prints an avg CPC carries it.

* **"Not asked" and "no" are different answers.** A campaign generated before
  a question existed has no answer to it, and rendering that as "No" invents a
  fact the rep never heard. Everything here distinguishes empty from false.
"""
from __future__ import annotations

# ---------------------------------------------------------------- audiences
B2B, B2C, BOTH = "B2B", "B2C", "Both"
AUDIENCE_TYPES = (B2B, B2C, BOTH)

AUDIENCE_GUIDANCE = {
    B2B: "Business buyers. Expect longer consideration, weekday business-hours "
         "traffic, job-title and industry language, and 'for business' / 'commercial' "
         "qualifiers. Exclude consumer and DIY intent.",
    B2C: "Consumers. Expect evenings and weekends, price and speed language, "
         "near-me intent and mobile-first behavior. Exclude wholesale, bulk and "
         "trade-account intent.",
    BOTH: "Both, so keep them in SEPARATE ad groups with their own copy. One "
          "blended ad group serves consumer copy to a purchasing manager and "
          "commercial copy to a homeowner, and neither converts.",
}

# ------------------------------------------------------------- conversions
# The eight the rep can pick, in the order they appear on the form. Each
# carries what it costs the campaign if chosen, because a conversion action is
# a structural decision -- call-only wants call assets and hours, purchases
# want a shopping-shaped funnel -- not a checkbox.
CONVERSION_ACTIONS = (
    ("calls", "Calls", "Call extensions and call-only ads, ad scheduling matched to "
                       "when somebody answers the phone."),
    ("email_leads", "Email leads", "A visible address and mailto path; slower intent, "
                                   "so weight toward research-stage terms."),
    ("form_submissions", "Form submissions", "The form must be above the fold and short; "
                                             "count fields as friction."),
    ("appointment_bookings", "Appointment bookings", "A live booking tool on the page. "
                                                     "Without one this is a form, not a booking."),
    ("purchases", "Purchases", "Transactional intent, product and price terms, and "
                               "conversion value tracking rather than lead counting."),
    ("quote_requests", "Quote requests", "Estimate, cost and pricing intent; expect a "
                                         "longer form and a higher CPA than a call."),
    ("chat_conversations", "Chat conversations", "A chat widget that is actually staffed "
                                                 "in the hours the ads run."),
    ("directions", "Directions", "Location extensions and a Business Profile; near-me "
                                 "and 'open now' intent, heavily mobile."),
)

CONVERSION_KEYS = tuple(k for k, _, _ in CONVERSION_ACTIONS)
CONVERSION_LABELS = {k: label for k, label, _ in CONVERSION_ACTIONS}
CONVERSION_GUIDANCE = {k: note for k, _, note in CONVERSION_ACTIONS}


# ------------------------------------------------------------ budget tiers
# Good / Better / Best. Named on the estimate the client reads, so they are
# words a client can choose between rather than internal labels.
TIERS = ("good", "better", "best")
TIER_LABELS = {"good": "Good", "better": "Better", "best": "Best"}
TIER_BLURB = {
    "good": "The smallest budget that can still be optimized — one tight ad group, "
            "the highest-intent terms only.",
    "better": "Enough room for the full keyword set and a real testing cadence.",
    "best": "Full coverage with headroom to bid competitively on the terms that convert.",
}

# The floor below which a search campaign cannot be read, whatever the tier.
# ~30 clicks a month is the point at which a week's data is noise; the budget
# analyser already says so, and the tiers must not contradict it.
MIN_READABLE_CLICKS = 30


# ------------------------------------------------- the caveat on every CPC
CPC_NOTE = "industry estimate"
CPC_NOTE_LONG = (
    "Average CPC figures are industry estimates for this sector, not a measured "
    "cost for this account. Actual cost per click is set at auction and is known "
    "only once the campaign runs."
)

# Where a CPC on this document came from, and what each one actually measures.
#
# There are three, and conflating any two of them overstates or understates a
# number a client makes a spending decision from:
#
#   benchmark        our sector range. An opening number, nothing more.
#   top_of_page_bid  Google's answer to "what would I have to BID to show at
#                    the top of the page" — not what you pay per click, and
#                    always the higher figure of the two Google returns.
#   forecast         Google's forecast average cost per click for this keyword
#                    set at the budget quoted. The only one of the three that
#                    is genuinely a cost per click.
#
# ``modules/ads_builder/keyword_plan.py`` reads these rather than restating
# them, so a label on a screen cannot drift from the call that produced the
# number under it.
CPC_SOURCES = {
    "benchmark": {
        "label": "Industry estimate",
        "short": CPC_NOTE,
        "long": CPC_NOTE_LONG,
    },
    "top_of_page_bid": {
        "label": "Google top-of-page bid",
        "short": "Google bid estimate",
        "long": "This is Google's estimate of what you would need to bid to "
                "show at the top of the page for these keywords in your target "
                "areas. It is a bid, not a cost per click — what you actually "
                "pay is usually lower, and is set at auction.",
    },
    "forecast": {
        "label": "Google forecast",
        "short": "Google forecast",
        "long": "This is Google's own forecast of average cost per click for "
                "this keyword set, in your target areas, at the budget shown. "
                "A forecast is not a guarantee — actual cost per click is set "
                "at auction once the campaign runs.",
    },
}

CPC_SOURCE_KEYS = tuple(CPC_SOURCES)


def cpc_provenance(campaign: dict) -> dict:
    """Which of the three the CPC on this document is, with its caveat.

    Always answers. A campaign nobody has measured is ``benchmark`` — the
    honest description of the number that has been there all along — rather
    than an empty block a template then has to decide what to do about.
    """
    measured = (campaign or {}).get("cpcMeasured") or {}
    source = measured.get("source") if measured.get("measured") else "benchmark"
    if source not in CPC_SOURCES:
        source = "benchmark"
    row = CPC_SOURCES[source]
    return {
        "source": source,
        "measured": source != "benchmark",
        "label": row["label"],
        "short": row["short"],
        "long": row["long"],
        "value": measured.get("cpc") if source != "benchmark" else None,
        "at": measured.get("at", ""),
        "customer_id": measured.get("customer_id", ""),
        # Named rather than counted: a CPC measured across three of a client's
        # five counties is not this campaign's CPC, and nothing else on the
        # page would say so.
        "areas_unresolved": ((measured.get("geo") or {}).get("unresolved") or []),
        "keywords_priced": len(((measured.get("ideas") or {}).get("keywords")) or []),
    }


# --------------------------------------------------------- review outcomes
# What a client can answer on the estimate they are sent, and the colour it
# comes back as in the approval hub. Three, deliberately: "yes", "yes with
# changes" and "let's talk" are the three real answers, and a two-way
# approve/reject forces the middle one into whichever end is nearest.
OUTCOMES = (
    ("approved", "I approve the campaign outline", "green",
     "Approved as presented."),
    ("approved_with_changes", "I approve the campaign outline with my changes attached",
     "yellow", "Approved, with change requests to apply."),
    ("discuss", "I need to schedule a time to discuss this outline", "red",
     "Wants a conversation before approving."),
)

OUTCOME_KEYS = tuple(k for k, _, _, _ in OUTCOMES)
OUTCOME_LABELS = {k: label for k, label, _, _ in OUTCOMES}
OUTCOME_COLOURS = {k: colour for k, _, colour, _ in OUTCOMES}
OUTCOME_NOTES = {k: note for k, _, _, note in OUTCOMES}

# The colour a proposal shows in the approval hub before a client has answered.
NO_RESPONSE_COLOUR = "gray"


def outcome_colour(outcome: str) -> str:
    return OUTCOME_COLOURS.get(str(outcome or ""), NO_RESPONSE_COLOUR)


# --------------------------------------------------------------- the intake
def _text(value, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


# What kind of campaign this proposal is. SEARCH is the default and stays the
# default: every proposal written before this existed is one, and a campaign
# type that had to be back-filled onto stored records would be a migration
# wearing a feature. Performance Max is a genuinely different product -- no
# keywords, asset groups instead of ad groups, a different generation prompt
# and a different mutate -- so it is a choice made before generation rather
# than a switch flipped afterwards.
CAMPAIGN_TYPES = ("SEARCH", "PERFORMANCE_MAX")
DEFAULT_CAMPAIGN_TYPE = "SEARCH"

CAMPAIGN_TYPE_LABELS = {
    "SEARCH": "Search",
    "PERFORMANCE_MAX": "Performance Max",
}


def campaign_type_of(value) -> str:
    """The campaign type on a proposal, defaulting rather than failing.

    Read from the campaign rather than stored in a column: create_all() adds
    no column to an existing table, so one here would be silently absent on
    the live Postgres while every local test passed -- and a stored proposal
    written before this existed carries nothing, which is Search.
    """
    if isinstance(value, dict):
        value = value.get("campaignType")
    text = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
    return text if text in CAMPAIGN_TYPES else DEFAULT_CAMPAIGN_TYPE


def normalise_intake(body: dict) -> dict:
    """Everything the rep answered at the start, in one shape.

    Stored on the campaign and reprinted on the estimate. Unanswered stays
    empty rather than becoming a default: "they did not say" and "they said no"
    are different, and only one of them is safe to put in front of a client.
    """
    body = body or {}
    audience = _text(body.get("audienceType"), 20)
    if audience not in AUDIENCE_TYPES:
        audience = ""

    wanted = [k for k in CONVERSION_KEYS if k in (body.get("conversionActions") or [])]

    seasonal = body.get("seasonal")
    return {
        "audienceType": audience,
        # Tri-state on purpose: "" is unanswered, True/False are answers.
        "seasonal": None if seasonal in (None, "") else bool(seasonal),
        "seasonalNotes": _text(body.get("seasonalNotes"), 600),
        "locallyOwned": None if body.get("locallyOwned") in (None, "") else bool(body.get("locallyOwned")),
        "productOrService": _text(body.get("productOrService"), 1200),
        "competitors": _text(body.get("competitors"), 1200),
        "promotion": _text(body.get("promotion"), 800),
        "conversionActions": wanted,
        "doNotTarget": _text(body.get("doNotTarget"), 1200),
        "phone": _text(body.get("phone"), 40),
    }


def conversion_labels(keys) -> list[str]:
    return [CONVERSION_LABELS[k] for k in (keys or []) if k in CONVERSION_LABELS]


def _yes_no(value) -> str:
    if value is None:
        return "Not asked"
    return "Yes" if value else "No"


def sections(campaign: dict) -> list[dict]:
    """The campaign detail rows the estimate prints, in order.

    Rows with no answer are dropped rather than printed as blanks — a client
    document listing eight "—" reads as a form somebody abandoned. What is
    deliberately kept even when negative is the pair a campaign is shaped by:
    the audience and the do-not-target list.
    """
    campaign = campaign or {}
    intake = campaign.get("intake") or {}
    rows = []

    def add(key, label, value, note=""):
        if value in ("", None, []):
            return
        rows.append({"key": key, "label": label, "value": value, "note": note})

    add("audience", "Who we are targeting", intake.get("audienceType"),
        AUDIENCE_GUIDANCE.get(intake.get("audienceType"), ""))
    add("product", "Product or service", intake.get("productOrService"))
    add("goals", "What counts as a result",
        " · ".join(conversion_labels(intake.get("conversionActions"))))
    add("promotion", "Promotion running with this campaign", intake.get("promotion"))
    if intake.get("seasonal") is not None:
        add("seasonal", "Seasonal",
            _yes_no(intake.get("seasonal"))
            + (f" — {intake['seasonalNotes']}" if intake.get("seasonalNotes") else ""))
    if intake.get("locallyOwned") is not None:
        add("local", "Locally owned", _yes_no(intake.get("locallyOwned")))
    add("competitors", "Competitors named by the client", intake.get("competitors"))
    add("exclusions", "Not to be targeted", intake.get("doNotTarget"),
        "Written into the campaign's negative keywords.")
    add("phone", "Contact number on the ads", intake.get("phone"))
    return rows


def for_prompt(campaign: dict) -> str:
    """The intake as instruction text for the model.

    Every answer carries *what to do about it* rather than only the answer: a
    model told "B2B" writes B2B-flavoured adjectives, while one told to keep
    consumer intent out of the keyword set builds a different campaign.
    """
    intake = (campaign or {}).get("intake") or {}
    lines = []

    if intake.get("audienceType"):
        lines.append(f"Audience: {intake['audienceType']}. "
                     + AUDIENCE_GUIDANCE.get(intake["audienceType"], ""))
    if intake.get("productOrService"):
        lines.append(f"What they sell: {intake['productOrService']}")
    if intake.get("conversionActions"):
        lines.append("The client counts these as a result, so build for them: "
                     + "; ".join(f"{CONVERSION_LABELS[k]} — {CONVERSION_GUIDANCE[k]}"
                                 for k in intake["conversionActions"]))
    if intake.get("promotion"):
        lines.append(f"Promotion to feature in the copy: {intake['promotion']}")
    if intake.get("seasonal"):
        lines.append("This business or campaign is SEASONAL"
                     + (f": {intake['seasonalNotes']}" if intake.get("seasonalNotes") else "")
                     + ". Say plainly in the strategy when the spend should be concentrated, "
                       "and do not build for even year-round delivery.")
    if intake.get("locallyOwned"):
        lines.append("Locally owned and operated — usable in ad copy, and worth an "
                     "explicit headline, because it is a real differentiator against "
                     "national competitors bidding the same terms.")
    if intake.get("competitors"):
        lines.append(f"Competitors the client named: {intake['competitors']}. "
                     "Take these as fact, and add any others you know of in this sector "
                     "and geography, clearly marked as your own research rather than theirs.")
    if intake.get("doNotTarget"):
        lines.append(f"DO NOT TARGET — the client has explicitly excluded this: "
                     f"{intake['doNotTarget']}. Reflect it in the negative keyword vault "
                     f"AND keep it out of the positive keywords. This is a client "
                     f"instruction, not a preference.")
    if intake.get("phone"):
        lines.append(f"Phone number for call assets: {intake['phone']}")
    return "\n".join(f"- {line}" for line in lines)
