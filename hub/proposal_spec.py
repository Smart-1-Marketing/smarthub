"""The Smart 1 proposal specification — what a proposal must contain.

Every proposal this Hub produces follows one structure, uses one set of
audience partners, and obeys the same handful of standing rules. Those things
were previously nowhere: the builder had eight generic sections it had made
up, and the AI writer was told to sound persuasive and left to guess the rest.
So two reps produced two differently-shaped documents, and neither matched
what the sales team actually pitches.

This module is that specification as data. It is the single place to change
the outline, the directives, the audience taxonomy or the operating stats —
the wizard, the PDF, the Word export and the AI prompt all read it.

## The four standing directives

These are enforced, not suggested, and each one exists because of a specific
way a generated proposal can hurt us:

  * **No Smart 1 Labs.** Excluded from every output until further notice.
  * **The Smart 1 Suite is the central nervous system.** Paid media is never
    pitched as a standalone; it feeds the Suite, which captures and nurtures
    what the media produces. A proposal that pitches channels alone sells the
    least differentiated half of what we do.
  * **Absolute client confidentiality.** Never name another Smart 1 account in
    a prospect's proposal — not as a case study, not as a credential. Naming
    the winery to the marina is how you lose both. Anonymised vertical
    benchmarks instead.
  * **Expected Results & ROI is mandatory.** Every proposal ends with it, and
    it is computed rather than written by the model, so a projection can never
    contradict the media plan printed above it.
  * **The rate card is never mentioned to a client.** It is our internal
    pricing, and naming it on a proposal invites the one question the document
    cannot answer — "can I see it?" — while making a quoted price read as a
    list price somebody might have marked up. `client_safe()` strips the
    phrase from generated copy, because a directive is a request and this one
    had been in the prompt while three of our own strings said it anyway.

## What is deliberately *not* here

Impression and reach projections. Those come from `hub.rate_card` and the
campaign's own line items, because a number in a proposal has to be the same
number the insertion order will bill.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# The 13-part master outline
# ---------------------------------------------------------------------------
# `kind` decides how a section renders:
#   text      prose the rep can edit and the AI can write
#   areas     the target-area table, generated
#   reach     the audience/reach estimate, generated
#   mediaplan the line items and rates, generated
#   packages  the three investment options, generated
#   kpis      the KPI list, generated
#   roi       Expected Results & ROI, computed from the rate card
#
# A generated section may carry editable intro copy above its table; it can
# never have its numbers written by a model.
OUTLINE = [
    {
        "id": "cover", "title": "Cover", "kind": "cover", "enabled": True,
        "purpose": "Establish professionalism and visual authority.",
        "guidance": "",
    },
    {
        "id": "summary", "title": "Executive Summary", "kind": "text", "enabled": True,
        "purpose": "Secure a yes from the C-suite even if they read nothing else.",
        "guidance": "One page maximum. Summarize where the client is now, the single "
                    "largest growth opportunity, and the strategic direction. Write about "
                    "business outcomes — lead growth, cost control, conversion efficiency — "
                    "not click rates or impressions.",
    },
    {
        "id": "objectives", "title": "Objectives & Success Criteria", "kind": "text",
        "enabled": True,
        "purpose": "Show we listened.",
        "guidance": "Split into Primary objectives (booked consultations, retail velocity, "
                    "signed cases — whatever this business actually sells) and Secondary "
                    "(lower cost per acquisition, fewer vendors, transparent reporting). "
                    "Use the client's own stated goals where they gave them.",
    },
    {
        # `friction` rather than `text`: the section renders the kept
        # "We suggest they should" recommendations underneath its copy, so
        # the advice reaches the client rather than staying in the wizard.
        "id": "friction", "title": "Current Friction Points", "kind": "friction",
        "enabled": True,
        "purpose": "Name where the current marketing is losing money.",
        "guidance": "Identify structural roadblocks, not personal criticism: a bolted-on "
                    "tech stack with separate logins, no central CRM so hot leads cool "
                    "before anyone follows up, fixed-schedule advertising that ignores "
                    "weather, foot traffic and real-time intent. Only raise a friction "
                    "point the intake actually evidences.",
    },
    {
        "id": "areas", "title": "Audience & Market Strategy", "kind": "areas", "enabled": True,
        "purpose": "Precision targeting, area by area.",
        "guidance": "Introduce the targeting above the table. Name the data partners and "
                    "the specific segments being used. Be concrete about geography.",
    },
    {
        "id": "reach", "title": "Addressable Audience", "kind": "reach", "enabled": True,
        "purpose": "How many people this actually reaches.",
        "guidance": "",
    },
    {
        "id": "channels", "title": "Recommended Channel Strategy", "kind": "channels",
        "enabled": True,
        "purpose": "Match each tactic to a stage of the customer journey.",
        "guidance": "For every channel state its role in the funnel, why it fits this "
                    "client specifically, and its primary KPI. Listing channels without "
                    "that mapping is not acceptable. Explain the ad tech in plain terms a "
                    "business owner would use.",
    },
    {
        "id": "mediaplan", "title": "Media Mix & Budget Allocation", "kind": "mediaplan",
        "enabled": True,
        "purpose": "A transparent breakdown of where the money goes.",
        "guidance": "Above the table, explain the split between top-funnel awareness, "
                    "mid-funnel consideration and bottom-funnel conversion, and why this "
                    "client's split is weighted the way it is.",
    },
    {
        "id": "creative", "title": "Creative & Messaging Strategy", "kind": "creative",
        "enabled": True,
        "purpose": "Connect the media buy to consumer psychology.",
        "guidance": "Give three specific messaging themes using AIDA or PAS, written for "
                    "this business rather than the category. Name the creative formats the "
                    "plan needs and the refresh cadence.",
    },
    {
        "id": "technology", "title": "Smart 1 Technology", "kind": "text", "enabled": True,
        "purpose": "The Smart 1 Suite as the client's command center.",
        "guidance": "Explain that the channels above feed the Smart 1 Suite, which captures "
                    "and nurtures every lead — Missed Call Text Back, unified inbox, "
                    "automated text and email workflows, reputation and review automation. "
                    "Name the integrations this client needs: call tracking, lead form "
                    "mapping, review monitoring. Never mention Smart 1 Labs.",
    },
    {
        "id": "reporting", "title": "Reporting, Optimization & Transparency", "kind": "text",
        "enabled": True,
        "purpose": "Remove the fear of where the money went.",
        "guidance": "State the optimization protocol — bid adjustments, negative keyword "
                    "updates, creative performance checks — and the cadence. One real-time "
                    "dashboard inside the Smart 1 Suite, which they can open themselves.",
    },
    {
        "id": "packages", "title": "Investment Summary", "kind": "packages", "enabled": True,
        "purpose": "Total financial clarity.",
        "guidance": "Recurring platform fees, media spend and one-time setup are shown "
                    "separately below. Above the table, say plainly what is recurring and "
                    "what is not.",
    },
    {
        "id": "kpis", "title": "How We Measure Success", "kind": "kpis", "enabled": True,
        "purpose": "The metrics we will be judged on.",
        "guidance": "",
    },
    {
        "id": "roi", "title": "Expected Results & ROI", "kind": "roi", "enabled": True,
        "purpose": "Connect the spend to the client's revenue. Mandatory.",
        "guidance": "The framework below sets out what this campaign is measured on "
                    "and what a normal result looks like for each product. Above it, "
                    "state how the Smart 1 Suite is the single source of truth for what "
                    "the media produced. Be conservative. Never state a conversion rate, "
                    "a lead count or a revenue figure the intake does not support, and "
                    "never present a benchmark range as a promise.",
    },
    {
        "id": "next", "title": "Next Steps", "kind": "text", "enabled": True,
        "purpose": "Prompt action.",
        "guidance": "A three-step checklist: sign the agreement, complete the onboarding "
                    "questionnaire, book the 30-minute kickoff call.",
    },
    {
        # After Next Steps, before the trafficking appendix. A client who says
        # yes asks "and what would more look like?", and until now the answer
        # was improvised on a call. It belongs on the document -- but only
        # with numbers that are already defensible, which is why neither route
        # invents one: raising a line quotes the Accelerated package printed
        # two sections above, and adding one quotes our own listed minimum for
        # that product.
        "id": "growth", "title": "Recommended Budgets", "kind": "growth",
        "enabled": True,
        "purpose": "What more would look like: raise a budget, or add what "
                   "discovery says is missing.",
        "guidance": "Introduce the options in a sentence or two. Never argue "
                    "for them and never imply the plan above is inadequate — "
                    "the plan above is the recommendation. These are what a "
                    "client who asks for more should be shown. Do not restate "
                    "the figures; the table carries them.",
    },
    {
        # Last on purpose. The ZIP list is the one part of a proposal nobody
        # reads and trafficking cannot launch without -- a hundred five-digit
        # numbers in the middle of the audience section buries the strategy
        # it sits inside, and dropping them means the IO gets built from a
        # radius somebody re-derives by hand. At the back it is reference,
        # which is what it is.
        "id": "zips", "title": "ZIP Codes Targeted", "kind": "zips", "enabled": True,
        "purpose": "The trafficking reference: every ZIP each target area covers.",
        "guidance": "Generated, not written. One line per target area, listing the "
                    "ZIP Codes it covers. Say nothing above it beyond what it is for.",
    },
]

# Sections that are never removed. `roi` is mandatory by directive; without
# `mediaplan` and `packages` the document is not a proposal.
REQUIRED = ("roi", "mediaplan", "packages")

# Sections the specification has RETIRED, keyed on the section kind with the
# reason. Removing one from OUTLINE only stops new proposals getting it;
# every quote already saved still carries the section, so `ensure_sections`
# strips these on the way through — which covers the PDF and the Word export
# too, since both run the state through it before rendering. Named with a
# reason rather than deleted in silence, the NOT_ENFORCED / NOT_REQUESTED
# rule: a section absent on purpose must never be ambiguous with one nobody
# thought of.
RETIRED_SECTION_KINDS = {
    "timeline": "The Implementation Timeline came off every quote by request: "
                "a generic 30-day blueprint on a sales document promises "
                "pacing nobody has scheduled, and the real kickoff dates are "
                "set at onboarding.",
}

# The one tool that must never appear. Checked on generated copy rather than
# only asked for in the prompt, because a prompt is a request and this is a
# rule — see `violations()`.
FORBIDDEN_TERMS = ("smart 1 labs", "smart1 labs", "s1 labs")


def default_sections(*, description: str = "", landing: str = "") -> list[dict]:
    """The outline as a fresh, editable section list."""
    out = []
    for spec in OUTLINE:
        section = {"id": spec["id"], "title": spec["title"], "kind": spec["kind"],
                   "enabled": spec["enabled"], "body": "",
                   "required": spec["id"] in REQUIRED}
        if spec["id"] == "summary" and description:
            section["body"] = description
        out.append(section)
    return out


def guidance_for(section_id: str) -> str:
    for spec in OUTLINE:
        if spec["id"] == section_id:
            return spec["guidance"]
    return ""


# ---------------------------------------------------------------------------
# Standing directives
# ---------------------------------------------------------------------------
# The standing directives, each with the short name a screen can show.
#
# Structured rather than a flat list of paragraphs because the rules are now
# read in two places: the AI prompt, which wants the full sentence, and the
# wizard's rules panel, which wants a heading a rep can scan. Two copies of
# the same rule is how the panel comes to describe a directive the prompt no
# longer carries -- so `DIRECTIVES` is derived from this rather than written
# beside it.
#
# `enforcement` is the honest answer to "and what happens if the model
# ignores it": `checked` is verified in code on the way back, `computed`
# means the model never writes those numbers at all, and `asked` means the
# instruction is in the prompt and nothing verifies it. A rep reading the
# panel should be able to tell which is which, because that is the difference
# between a rule and a hope.
DIRECTIVE_RULES = [
    {
        "key": "labs",
        "title": "No Smart 1 Labs",
        "enforcement": "checked",
        "text": "Never reference Smart 1 Labs. Technology recommendations cover "
                "Smart 1 Sites, Smart 1 Snap and the Smart 1 Suite's operational "
                "tools only.",
    },
    {
        "key": "suite",
        "title": "The Smart 1 Suite is the central nervous system",
        "enforcement": "asked",
        "text": "Position the Smart 1 Suite as the central nervous system of the "
                "campaign. Paid media is never a standalone solution: the channels "
                "feed traffic into the Suite, which captures and nurtures those leads "
                "through Missed Call Text Back, unified communications and automated "
                "text and email workflows. Frame the Suite as what turns awareness "
                "into measurable revenue.",
    },
    {
        "key": "confidentiality",
        "title": "Absolute client confidentiality",
        "enforcement": "asked",
        "text": "Absolute client confidentiality. Never name another Smart 1 Marketing "
                "client, current or former, in this proposal — not as a case study, "
                "not as a credential, not as an example. Use anonymised industry "
                "benchmarks instead.",
    },
    {
        "key": "facts",
        "title": "Only the facts supplied",
        "enforcement": "asked",
        "text": "Use only the facts supplied. Do not invent statistics, client results, "
                "awards, years in business, service areas, conversion rates or "
                "capabilities. Every price, product name and delivery figure is "
                "already computed in the tables the client will read next to your "
                "copy; contradicting one is worse than omitting it.",
    },
    {
        "key": "ratecard",
        "title": "Never name the rate card",
        "enforcement": "checked",
        "text": "Never mention a rate card, a price list or any internal pricing "
                "document. The client sees the prices quoted to them, and naming the "
                "sheet they came off invites a question this document cannot answer.",
    },
    {
        "key": "voice",
        "title": "Write as a Smart 1 senior strategist",
        "enforcement": "asked",
        "text": "Write as a Smart 1 senior strategist: consultative, confident, "
                "specific. Never open with 'Based on the information provided' or "
                "'It is important to note'. Explain programmatic, CTV, DOOH and IP "
                "targeting in terms a business owner would use, not in ad-tech "
                "vocabulary.",
    },
]

# What the prompt has always been given: the directive sentences, in order.
DIRECTIVES = [rule["text"] for rule in DIRECTIVE_RULES]


def rules() -> list[dict]:
    """Every standing rule this proposal is held to, for display.

    The wizard's rules panel reads this, so a rep can see what the document
    is being held to instead of only meeting a rule when copy is discarded
    for breaking one. Built here rather than mirrored in JavaScript: a
    directive edited in this file and not in the panel would leave the screen
    describing a rule that no longer exists, which is worse than no panel.
    """
    listed = [dict(rule) for rule in DIRECTIVE_RULES]
    listed.append({
        "key": "numbers",
        "title": "Every number is computed, never written",
        "enforcement": "computed",
        "text": "Impressions, reach, rates, package pricing and Expected Results & "
                "ROI come from the rate card and this campaign's own line items. The "
                "AI writes the prose above those tables and never the figures inside "
                "them, so a projection cannot contradict the media plan printed with "
                "it.",
    })
    listed.append({
        "key": "required",
        "title": "Three sections cannot be removed",
        "enforcement": "checked",
        "text": "Expected Results & ROI, the Media Mix & Budget Allocation and the "
                "Investment Summary are on every Smart 1 proposal. They can be "
                "re-worded and reordered; a quote saved without one gets it back on "
                "the next save.",
    })
    listed.append({
        "key": "formatting",
        "title": "Plain prose, cleaned on the way through",
        "enforcement": "checked",
        "text": FORMATTING_DIRECTIVE + " Generated copy and anything pasted into a "
                "section are both run through the cleaner, so Markdown or an emoji "
                "cannot reach the PDF, the Word export or the client.",
    })
    return listed


def violations(text: str) -> list[str]:
    """Directive breaches in generated copy.

    Only the mechanically checkable ones. A prompt asks; this verifies — the
    Smart 1 Labs exclusion is a standing instruction from the business, and
    "the model was told not to" is not evidence that it did not.
    """
    lowered = str(text or "").lower()
    found = []
    for term in FORBIDDEN_TERMS:
        if term in lowered:
            found.append("Mentions Smart 1 Labs, which is excluded from every proposal.")
            break
    return found


# ---------------------------------------------------------------------------
# Audience data partners
# ---------------------------------------------------------------------------
# Named segments, so the Audience section says what we are actually buying
# rather than "targeted adults 25-54".
AUDIENCE_PARTNERS = {
    "Experian": {
        "Syndicated — Sports & Outdoor": [
            "Ski & Snowboard Intenders", "Golf Club Members",
            "Boating & Marina Enthusiasts", "Hunting & Fishing Enthusiasts",
            "RV & Camping Enthusiasts",
        ],
        "ConsumerView® Demographics": [
            "Flourishing Families", "Power Elite", "High net worth households",
            "High discretionary income brackets",
        ],
        "Behavioral Segments": [
            "Online Job Seekers", "Recent New Movers (5x more likely to buy home "
            "services within 6 months of moving)", "In-market home improvers",
        ],
    },
    "TrueData": {
        "Brand Affinity": [
            "Regular visitors to named competitor locations (Venue Replay / "
            "Location Lookback conquesting)",
        ],
        "High-Intent Purchase Predictors": [
            "In-Market for New Vehicle Lease", "Active in-market buyers from "
            "real-time digital behavior, app usage and transaction history",
        ],
    },
    "Proximic by Comscore": {
        "Syndicated Contextual": [
            "Premium-site placement matched to surrounding editorial "
            "(e.g. legal creative alongside local traffic and collision reporting)",
        ],
        "Political & Civic Playbook": [
            "Localised voter profiles", "Regional policy intenders",
            "Civic-minded households",
        ],
    },
}


def audience_segments_for(industry: str = "") -> list[str]:
    """Segments worth naming for this industry, most specific first.

    Returns a shortlist rather than the whole taxonomy: an audience section
    listing forty segments reads as a data dump, and a rep cannot defend one
    they did not choose.
    """
    key = str(industry or "").lower()
    picks: list[str] = []

    def add(*items):
        for item in items:
            if item not in picks:
                picks.append(item)

    if any(w in key for w in ("boat", "marine", "marina")):
        add("Experian — Boating & Marina Enthusiasts",
            "TrueData — Brand Affinity: competitor dealership visitors")
    if any(w in key for w in ("rv", "camp", "outdoor")):
        add("Experian — RV & Camping Enthusiasts")
    if "ski" in key or "resort" in key:
        add("Experian — Ski & Snowboard Intenders")
    if any(w in key for w in ("legal", "law", "attorney", "injury")):
        add("Proximic — contextual placement alongside local collision and "
            "traffic reporting",
            "TrueData — Brand Affinity: competitor firm and courthouse visitors")
    if any(w in key for w in ("auto", "car", "dealer", "vehicle")):
        add("TrueData — In-Market for New Vehicle Lease",
            "TrueData — Brand Affinity: competitor dealership visitors")
    if any(w in key for w in ("recruit", "hiring", "staffing", "talent")):
        add("Experian — Online Job Seekers",
            "TrueData — Brand Affinity: competitor employer locations")
    if any(w in key for w in ("hvac", "home", "roof", "plumb", "contractor")):
        add("Experian — Recent New Movers (5x more likely to buy home services "
            "within 6 months)", "Experian — In-market home improvers")
    if any(w in key for w in ("restaurant", "food", "hospitality", "tourism")):
        add("TrueData — Brand Affinity: competitor venue visitors",
            "Proximic — contextual placement on dining and travel editorial")

    # Every campaign gets these; they are the base layer, not the whole plan.
    add("Experian ConsumerView® — household income and demographic filters",
        "TrueData — high-intent in-market purchase predictors",
        "Website retargeting and CRM lookalike match")
    return picks[:8]


# ---------------------------------------------------------------------------
# Operating knowledge the strategists actually pitch
# ---------------------------------------------------------------------------
# Each entry is a fact with a named condition. The AI is given only the ones
# that apply to the campaign in front of it, so a display-only plan is not
# handed streaming-audio statistics it has no business quoting.
NUANCES = [
    {
        "key": "audio_ctv_lift",
        "when": ("audio", "video"),
        "fact": "Digital audio takes 1 in every 5 minutes of consumer digital attention "
                "but historically receives about 1/16th of ad budgets, and 79% of digital "
                "audio is consumed with no screen at all — an audience visual banners "
                "cannot reach. Adding targeted digital audio alongside a Connected TV "
                "campaign drives a 21.8% gain in local market share and 11.5–13% "
                "incremental reach against video-only budgets.",
    },
    {
        "key": "answer_economy",
        "when": ("seo",),
        "fact": "Search is becoming an answer economy: users increasingly take the "
                "generated answer rather than clicking a link. Being cited requires "
                "accurate schema markup, fast page loads (compressed images, Brotli or "
                "GZIP), and clean directory synchronisation across 50+ local platforms. "
                "Automated 5-star review generation through the Smart 1 Suite directly "
                "strengthens local map and AI-pack ranking signals.",
    },
    {
        "key": "brand_vs_inventory",
        "when": ("auto",),
        "fact": "Transaction-heavy retailers commonly spend 70–95% of budget on "
                "third-party listing sites, which forces them to compete as a commodity "
                "on price. Inventory starts conversations; brand trust finishes deals. A "
                "60-30-10 split — 60% inventory demand capture, 30% brand awareness, 10% "
                "trust and reputation — lowers cost per acquisition and protects margin.",
    },
]


def nuances_for(state) -> list[str]:
    """The operating facts that apply to this campaign, and only those."""
    state = state or {}
    items = state.get("items") or []
    haystack = " ".join(str(i.get("category", "")) + " " + str(i.get("product", ""))
                        for i in items).lower()
    industry = str(state.get("industry") or "").lower()

    present = set()
    if any(w in haystack for w in ("radio", "podcast", "audio")):
        present.add("audio")
    if any(w in haystack for w in ("video", "ott", "youtube", "ctv", "tv")):
        present.add("video")
    if any(w in haystack for w in ("search engine optimization", "seo",
                                   "social media management")):
        present.add("seo")
    if any(w in industry for w in ("auto", "car", "dealer", "vehicle", "boat", "rv")):
        present.add("auto")

    return [n["fact"] for n in NUANCES if present.intersection(n["when"])]


# ---------------------------------------------------------------------------
# Smart 1 Suite licensing
# ---------------------------------------------------------------------------
# The platform fee is a recurring SaaS charge and must be shown separately
# from media spend in the Investment Summary — a client who reads one blended
# number cannot tell what stops if they pause the media.
SAAS_TIERS = [
    # `specs` is what a client reads beside the tier name, on the wizard's
    # tier cards and on the Investment Summary line alike. It names USERS and
    # nothing else, deliberately: the contact, email and text allowances are
    # operating limits that read as products on a quote -- a client comparing
    # "1,500 emails" against "5,000 emails" is shopping the plumbing rather
    # than the platform, and the allowances move with the vendor's own plans.
    {
        "name": "Smart 1", "monthly": 199,
        "specs": "5 users",
        "features": "Unified message center, media library, email center, texting "
                    "center, call center, online scheduling, reputation center with "
                    "automated Google review requests, social planner, Smart 1 Sites.",
    },
    {
        "name": "Smarter", "monthly": 599,
        "specs": "10 users",
        "features": "Everything in Smart 1, plus the AI writing assistant, review "
                    "widget with sentiment analysis, Facebook and TikTok lead form "
                    "integration, smart webchat, and member courses.",
    },
    {
        "name": "Smartest", "monthly": 999,
        "specs": "Unlimited users",
        "features": "Everything in Smarter, plus AI image generation, advanced CRM "
                    "automations, multi-market directory syncing and priority support.",
    },
]


def suggested_tier(monthly_media: float) -> dict:
    """The Suite tier that fits a campaign of this size.

    Scaled to media spend because that is what predicts lead volume, and lead
    volume is what exhausts a contact or texting allowance. A recommendation,
    not a rule — the rep can pick any tier.
    """
    try:
        spend = float(monthly_media or 0)
    except (TypeError, ValueError):
        spend = 0
    if spend >= 15000:
        return SAAS_TIERS[2]
    if spend >= 5000:
        return SAAS_TIERS[1]
    return SAAS_TIERS[0]


# ---------------------------------------------------------------------------
# Industry trends on the cover
#
# Every proposal opens with what is happening in this client's category, how
# Smart 1 answers it, and an example of how businesses like theirs usually
# craft a budget. It is a table rather than a model call, because this is the
# first thing a client reads and a trend invented per proposal is a claim
# nobody here can stand behind -- the audit-summary rule, one document over.
# Two rules on the copy. **No statistics**: a percentage of consumers nobody
# measured is exactly the confident wrong number this codebase keeps undoing,
# so the trends are qualitative and checkable against how the category
# plainly behaves. And the **budget example is named as Smart 1's own
# guidance** (TRENDS_NOTE) -- it is how we craft budgets in the category, not
# a published benchmark, and dressing it up as one invites the question the
# document cannot answer.
#
# Keyed on the wizard's own INDUSTRIES spellings. A campaign whose industry
# is unset, or one the table does not know, gets GENERAL rather than nothing:
# the cover "always" carries this block, and an empty cover on the one
# industry nobody wrote up would be a silent hole on the first page.
# ---------------------------------------------------------------------------
TRENDS_NOTE = ("Budget guidance is Smart 1's own, from running campaigns in this "
               "category — an example of how the budget is usually crafted, not a "
               "published benchmark, and the media plan in this proposal is the "
               "number that governs.")

GENERAL_TRENDS = {
    "trends": [
        "Buyers research before they reach out — they search, compare, and read "
        "reviews long before the first call, and AI-generated answers are joining "
        "the map pack and organic results as places that research happens.",
        "Attention is fragmenting across streaming TV, streaming audio, social "
        "feeds, and search — so a single-channel budget reaches a shrinking slice "
        "of the same audience it used to cover.",
    ],
    "help": "Smart 1 meets that behavior across the channels at once — search where "
            "the demand already exists, social and retargeting to stay in front of "
            "the people still comparing, and streaming TV and audio for the reach "
            "traditional buys used to own — with every response landing in the "
            "Smart 1 Suite, where it is answered, nurtured, and measured.",
    "budget": "Businesses in this position usually anchor the budget on the channel "
              "closest to the sale (often search), keep a steady share on staying "
              "visible to people still deciding (social and retargeting), and hold "
              "a portion for awareness that fills the funnel next quarter — then "
              "commit to at least a quarter of consistent spend, because a budget "
              "that starts and stops never exits the learning phase.",
}

INDUSTRY_TRENDS = {
    "Healthcare / Dental": {
        "trends": [
            "Patients choose a provider the way they shop: they search, read "
            "reviews, and compare two or three practices before they ever call — "
            "and map results and AI answers now absorb much of that research "
            "before a website is even visited.",
            "The practices winning new patients are the ones that answer fast: a "
            "missed call or an unanswered form sends the patient to the next "
            "practice on the list.",
        ],
        "help": "Smart 1 puts the practice in the search and map results patients "
                "actually use, keeps the review profile working for the ads, and "
                "routes every call and form into the Smart 1 Suite so the front "
                "desk never loses a lead — with call tracking proving which "
                "channel produced each patient.",
        "budget": "Practices usually anchor on search, where patients with intent "
                  "already are — commonly around half the budget — keep a steady "
                  "share on social and retargeting to stay in front of people "
                  "still comparing, and put the remainder into reputation and the "
                  "pages the ads land on.",
    },
    "Home Services": {
        "trends": [
            "Home service jobs start with an urgent search and end with whoever "
            "answers first — the search results, the review stars, and the speed "
            "of the callback decide the job more than the brand does.",
            "Seasonality punishes stop-start marketing: the companies that stay "
            "visible in the slow months own the search results in the busy ones.",
        ],
        "help": "Smart 1 captures the urgent demand with search, backs it with "
                "review generation so the stars support the click, and answers "
                "every inquiry through the Suite — missed-call text back alone "
                "recovers jobs that used to go to the next truck in the results.",
        "budget": "Home service budgets are usually crafted search-first — often "
                  "half or more of the spend where demand is urgent — with "
                  "retargeting and social keeping the brand in front of "
                  "homeowners between needs, and a seasonal reserve so the busy "
                  "season is bought before it starts.",
    },
    "Legal": {
        "trends": [
            "Legal clients compare quietly and decide quickly: they search, read "
            "reviews and settlements coverage, and shortlist firms before making "
            "one contact — often through an AI answer or a map listing rather "
            "than a firm's own site.",
            "Cost per click in legal categories is among the highest anywhere, "
            "which makes the landing page and the intake process — not the ad — "
            "where cases are won or lost.",
        ],
        "help": "Smart 1 pairs precisely targeted search with landing pages built "
                "to convert the click a firm just paid a premium for, and the "
                "Suite's intake — call tracking, texting, scheduling — makes sure "
                "an inquiry at 8pm is a consultation, not a voicemail.",
        "budget": "Firms usually concentrate the budget on the practice areas that "
                  "pay — a focused search budget beats a thin one spread across "
                  "every service — with a share on retargeting the visitors who "
                  "did not call, and brand awareness added once intake is "
                  "converting.",
    },
    "Automotive": {
        "trends": [
            "Car buyers arrive at the lot having already decided most of the "
            "purchase online — inventory search, reviews, and video walk-arounds "
            "have replaced the second and third dealership visit.",
            "Service revenue is won locally and repeatedly: the dealership that "
            "stays in front of its own buyers keeps the service work that used "
            "to walk to independents.",
        ],
        "help": "Smart 1 keeps the inventory and the offers in front of "
                "in-market shoppers with search, social, and streaming TV, "
                "fences the competition's lots where that fits, and uses the "
                "Suite to keep sold customers coming back for service.",
        "budget": "Dealers usually split between conquesting in-market shoppers "
                  "(search plus geo-targeted display and video) and defending "
                  "their own customers (retargeting, email, service offers) — "
                  "with the awareness share carried by streaming TV, where the "
                  "traditional TV budget used to sit.",
    },
    "Real Estate": {
        "trends": [
            "Buyers and sellers start with the portals, but they choose the "
            "agent — and the agent they choose is the one whose name, reviews, "
            "and local content they kept meeting while they browsed.",
            "Video has become the listing: neighborhoods, walk-throughs, and "
            "market updates now do the work open houses used to.",
        ],
        "help": "Smart 1 builds the local presence that converts portal browsing "
                "into a signed agreement — search for the high-intent moments, "
                "social video for the neighborhood authority, retargeting for "
                "the long decision cycle — with every inquiry nurtured in the "
                "Suite through a decision that can take months.",
        "budget": "Real estate budgets usually run steadier and longer than most: "
                  "a consistent social and video presence carries the brand, a "
                  "focused search budget catches the ready-now moments, and "
                  "retargeting plus email nurture the months in between.",
    },
    "Retail / E-comm": {
        "trends": [
            "Discovery has moved into the feed: shoppers meet products on social "
            "and video before they ever search for them, and the search click "
            "increasingly just confirms a decision the feed already made.",
            "First-party data is the new shelf space — the retailers growing are "
            "the ones who own their customer list and can reach it without "
            "renting the audience every time.",
        ],
        "help": "Smart 1 runs the feed-to-search loop as one campaign — social "
                "and video to create the demand, search and retargeting to "
                "close it — and the Suite turns buyers into a list the store "
                "owns: email, texting, and reviews that compound instead of "
                "renting reach.",
        "budget": "Retail budgets usually lead with social and video where the "
                  "discovery happens, keep search funded to catch the demand "
                  "those create, and scale retargeting with traffic — with a "
                  "promotional reserve for the calendar moments the category "
                  "lives on.",
    },
    "Restaurant / Hospitality": {
        "trends": [
            "Guests decide from a phone in the moment: the map listing, the "
            "photos, the stars, and whether booking takes one tap decide who "
            "gets the table.",
            "The feed fills the room midweek — local social video and offers "
            "reach regulars and nearby diners at a fraction of what broadcast "
            "used to cost.",
        ],
        "help": "Smart 1 keeps the listing, the reviews, and the photos working "
                "as hard as the food, puts offers in the local feed on the days "
                "that need filling, and the Suite answers, books, and brings "
                "guests back with the list the restaurant owns.",
        "budget": "Hospitality budgets usually stay local and visual — social "
                  "and geo-targeted display carrying most of the weight, search "
                  "funded for the 'near me' moments, and a steady share on "
                  "reputation and re-engaging past guests, where the cheapest "
                  "covers are.",
    },
    "Financial Services": {
        "trends": [
            "Trust is researched before it is given: prospects read reviews, "
            "compare rates, and consume educational content long before a "
            "meeting — and compliance-shaped categories reward the firms whose "
            "content answers questions honestly.",
            "The decision cycle is long, so the winners are the firms still "
            "present months after the first click.",
        ],
        "help": "Smart 1 builds the presence that earns the meeting — search for "
                "the comparison moments, content and retargeting for the long "
                "middle, reputation for the trust check — and the Suite nurtures "
                "a lead through a decision measured in months, not sessions.",
        "budget": "Financial budgets usually favor patience: a moderate, "
                  "consistent search budget on the services that pay, a durable "
                  "retargeting and email layer for the long cycle, and awareness "
                  "added once the funnel below it converts.",
    },
    "Fitness / Wellness": {
        "trends": [
            "Memberships are sold on proof: transformations, classes, and "
            "community in the feed convert better than any offer — and the "
            "trial-to-member step is won by follow-up, not by the ad.",
            "January demand is real but loyalty is built the other eleven "
            "months, on retention and referral.",
        ],
        "help": "Smart 1 fills the top with local social and video, catches the "
                "ready-to-join moments on search, and the Suite runs the "
                "follow-up that turns a trial into a member — scheduling, "
                "texting, and the win-back campaigns to the list the gym "
                "already owns.",
        "budget": "Fitness budgets usually lead with social where the proof "
                  "lives, keep search funded for the join-now moments, spike "
                  "for the seasonal windows, and spend deliberately on "
                  "retention — a member kept costs a fraction of a member "
                  "acquired.",
    },
    "Education": {
        "trends": [
            "Enrollment journeys are family decisions researched over months — "
            "programs are compared, reviews are read, and campuses are toured "
            "online before an inquiry is ever sent.",
            "Video and social proof now carry the open house: student stories "
            "reach the next cohort where they already are.",
        ],
        "help": "Smart 1 keeps the program in front of searching families, tells "
                "the story in the feed with video, and the Suite nurtures an "
                "inquiry through a decision cycle that outlasts any single "
                "campaign — with every touch attributed to the term it "
                "enrolled.",
        "budget": "Education budgets are usually crafted around the enrollment "
                  "calendar: search and social funded steadily through the "
                  "research months, a heavier push into decision windows, and "
                  "retargeting plus email carrying inquiries between them.",
    },
    "B2B / Professional": {
        "trends": [
            "B2B buying committees do most of the evaluation anonymously — "
            "content, reviews, and peers — and surface only when a shortlist "
            "already exists.",
            "The sales cycle is long and multi-threaded, which rewards being "
            "usefully present for months rather than loudly present for weeks.",
        ],
        "help": "Smart 1 builds the presence that gets a firm onto the "
                "shortlist — search for the moments a need is named, targeted "
                "display and social for the long anonymous middle — and the "
                "Suite keeps every contact warm across a cycle that outlives "
                "any one campaign.",
        "budget": "B2B budgets usually put a focused share on high-intent search "
                  "terms, sustain an always-on retargeting and content layer for "
                  "the committee, and measure in pipeline rather than clicks — "
                  "crafted for consistency over quarters, not bursts.",
    },
}


def industry_trends(industry: str) -> dict:
    """The cover's trends block for this campaign's industry.

    Always answers. An unknown or unset industry gets the general entry with
    ``matched`` False, so the cover can carry the block on every proposal and
    a screen can still tell a category we wrote up from the fallback.
    """
    name = str(industry or "").strip()
    entry = INDUSTRY_TRENDS.get(name)
    return {
        "industry": name or "your category",
        "matched": bool(entry),
        "note": TRENDS_NOTE,
        **(entry or GENERAL_TRENDS),
    }



NEXT_STEPS = [
    "Approve this proposal and sign the digital agreement.",
    "Complete the Smart 1 onboarding questionnaire — access, tracking and creative.",
    "Book the 30-minute campaign kickoff call.",
]


# ---------------------------------------------------------------------------
# The AI system prompt
# ---------------------------------------------------------------------------
def system_prompt(state=None) -> str:
    """The instruction block prefixed to every proposal-copy request."""
    lines = [
        "You are the Smart 1 Marketing Proposal Architect. Smart 1 is a "
        "channel-agnostic, data-driven agency with 22 years of performance data — not "
        "a commission-biased shop selling broad-reach schedules. You write the "
        "narrative copy for a client-facing proposal whose numbers are already fixed.",
        "",
        "Standing directives — these are rules, not preferences:",
    ]
    # The formatting rule is a directive like the rest, and it is defined
    # below beside the cleaner that enforces it -- a rule stated in one place
    # and checked in another is how the two come to disagree.
    lines += [f"{i}. {d}" for i, d in
              enumerate(list(DIRECTIVES) + [FORMATTING_DIRECTIVE], 1)]

    facts = nuances_for(state)
    if facts:
        lines += ["", "Operating facts you may cite for this campaign (and only these):"]
        lines += [f"- {f}" for f in facts]

    segments = audience_segments_for((state or {}).get("industry", ""))
    if segments:
        lines += ["", "Audience segments this plan is built on — name them specifically "
                      "rather than describing 'targeted adults':"]
        lines += [f"- {s}" for s in segments]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# What generated copy is allowed to look like
#
# The models write Markdown by habit. Nothing downstream renders it: the
# preview HTML-escapes the body, the PDF escapes it into a reportlab
# Paragraph, and python-docx writes it as literal text -- so `**Reach**`
# reached the client as `**Reach**`, three ways, on a document quoting five
# figures. Emoji arrived the same way, in a proposal.
#
# This is the Smart 1 Labs rule one step on: a prompt is a request, and "the
# model was told to write plain prose" is not evidence that it did. So the
# instruction goes in the prompt *and* `clean_ai_text()` runs over whatever
# comes back, and over anything typed into the section editor by hand -- or
# the rule holds only until somebody pastes.
#
# Bold survives, because bold is legitimate and three renderers can do it.
# It is normalised to `<b>…</b>`, which is what reportlab reads natively,
# what `rich_runs()` turns into a bold run for Word, and what the preview
# un-escapes deliberately and alone. Everything else -- headings, italics,
# code ticks, bullet asterisks, emoji -- is removed rather than passed
# through, because a character the renderer will not interpret is a character
# the client reads.
# ---------------------------------------------------------------------------
FORMATTING_DIRECTIVE = (
    "Write plain professional prose. No Markdown: no asterisks, no "
    "underscores for emphasis, no # headings, no backticks, no tables and no "
    "emoji or decorative symbols of any kind. Bold is available and is the "
    "only formatting there is — write it as <b>text</b> and use it sparingly, "
    "for a term worth stressing rather than for every noun. Where the copy "
    "needs a list, write one item per line, each line starting with the "
    "bullet character • and nothing else — never run the items together "
    "inside a sentence or a paragraph."
)

# Ranges that are emoji, pictographs, dingbats and the variation selectors and
# skin-tone modifiers that ride with them. Deliberately not "anything
# non-ASCII": the proposal legitimately carries en dashes, curly quotes and
# the • it is told to use, and stripping those would mangle correct copy.
_EMOJI_RE = re.compile(
    "[" 
    "\U0001F000-\U0001FAFF"      # pictographs, symbols, transport, faces
    "\U00002190-\U000021FF"      # arrows
    "\U00002300-\U000023FF"      # technical (⌚ ⏰ …)
    "\U00002600-\U000027BF"      # misc symbols and dingbats
    "\U0000FE00-\U0000FE0F"      # variation selectors
    "\U0001F3FB-\U0001F3FF"      # skin tones
    "\U00002B00-\U00002BFF"
    "\U0000200D"                 # zero-width joiner
    "]+")

_BOLD_MD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)
_BOLD_TAG_RE = re.compile(r"<\s*/?\s*(b|strong)\s*>", re.I)


# A bullet that shares a line with other bullets is not a list.
#
# The directive above asks for one item per line and a model obliges most of
# the time, which is the problem: the times it does not are indistinguishable
# from correct copy until the document is read. What comes back is
# "We will reach three areas: • Carmel • Fishers • Noblesville" -- one
# paragraph, rendered by all three renderers as one paragraph, and a client
# reads a sentence with three dots in it rather than a list of three places.
#
# So the shape is enforced rather than requested, the Smart 1 Labs rule one
# step on. A bullet anywhere but the start of a line begins a new line, and
# the text in front of the first one stays where it is -- it is the lead-in
# ("We will reach three areas:") and deleting it would lose a clause.
_INLINE_BULLET_RE = re.compile(r"[ \t]*(?<=\S)[ \t]+•[ \t]*")


def _one_bullet_per_line(text: str) -> str:
    """Every bullet at the start of its own line, its lead-in kept above it."""
    lines = []
    for line in str(text or "").split("\n"):
        # Only lines that carry a bullet *after* something else are touched;
        # a line that is already one item is left exactly as it is, because
        # this runs on every save as well as on every generation.
        lines.append(_INLINE_BULLET_RE.sub("\n• ", line))
    return "\n".join(lines)


def _line_runs(line: str) -> list[tuple[str, bool]]:
    """One line of cleaned copy as (text, bold) runs."""
    runs, bold = [], False
    for piece in re.split(r"(<b>|</b>)", line):
        if piece == "<b>":
            bold = True
        elif piece == "</b>":
            bold = False
        elif piece:
            runs.append((piece, bold))
    return runs or [("", False)]


def bullets(items, lead: str = "") -> str:
    """A list of things as bullet lines, for anywhere a document prints one.

    Copy written by a model goes through `_one_bullet_per_line` above; this is
    the other half — the lists the *code* prints. KPIs, success metrics, the
    audience layers, the products left out of a total: each was rendered as
    ", ".join(...) into a sentence, so a client read six KPIs as a comma
    string and skimmed past four of them. A list of things a client is meant
    to check off is a list.

    Returns a string rather than markup, so it goes through `blocks()` like
    every other piece of copy and the preview, the PDF and the Word export
    each draw it as the list they already know how to draw.
    """
    rows = [str(i).strip() for i in (items or []) if str(i).strip()]
    if not rows:
        return ""
    body = "\n".join(f"• {row}" for row in rows)
    return f"{lead.strip()}\n{body}" if lead.strip() else body


def blocks(text) -> list[dict]:
    """Cleaned copy as the blocks a renderer actually has to draw.

    Three renderers read this -- the preview builds a <ul>, the PDF gives
    each item its own bullet-indented Paragraph, and Word writes a
    List Bullet paragraph -- so that "a list is a list" is decided once
    rather than three times, and a fourth renderer added later cannot quietly
    go back to printing the bullet character inside a sentence.

        [{"kind": "para", "text": "...", "runs": [(text, bold), ...]},
         {"kind": "list", "items": [{"text": ..., "runs": [...]}, ...]}]
    """
    out: list[dict] = []
    for line in clean_ai_text(text).split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("•"):
            item = stripped.lstrip("•").strip()
            if not item:
                continue        # a bullet with nothing after it is not an item
            entry = {"text": item, "runs": _line_runs(item)}
            if out and out[-1]["kind"] == "list":
                out[-1]["items"].append(entry)
            else:
                out.append({"kind": "list", "items": [entry]})
            continue
        out.append({"kind": "para", "text": stripped, "runs": _line_runs(stripped)})
    return out


# The internal pricing sheet, in the spellings copy actually uses.
#
# All four of the mentions this had to remove were *ours* — the PDF's rate
# note, the seeded ROI copy, the preview's default and the growth note — which
# is the point: the directive telling the model not to name the rate card had
# been in the prompt the whole time, while the document said it anyway in
# words nobody had generated. So the phrase is rewritten rather than
# requested, and a price stays a quoted price rather than reading as a list
# price somebody may have marked up.
# Both orders, because the document said it the other way round. The rule was
# written against "the rate card" and the media plan's own seeded copy said
# "every rate is the Smart 1 card rate" -- which passed the check, named our
# internal pricing on a client document, and was additionally false the day
# sell_rate() started quoting CPM at 2x. A price a client reads is a quoted
# price; where it sits against a sheet of ours is not their side of the
# conversation.
_RATE_CARD_RE = re.compile(
    r"\b(?:the\s+|our\s+|a\s+)?(?:current\s+)?(?:Smart\s*1\s+)?"
    r"(?:rate[-\s]card|card[-\s]rates?)\b",
    re.I)


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def client_safe(text) -> str:
    """Copy with anything the client should not read about our pricing removed.

    The whole sentence goes, not the phrase. Swapping "the rate card" for
    "our rates" leaves copy that is grammatical about half the time -- "Rates
    follow our rates", "adding one starts at our rates minimum" -- and a
    client reads the mangling, not the intent. A sentence whose subject is
    our internal pricing sheet has nothing to say to a client in the first
    place, so its neighbours are better off without it. The Smart 1 Labs
    precedent: discard, rather than paraphrase into something nobody wrote.

    A bullet item is a line rather than a sentence, so it is dropped whole.
    """
    if not _RATE_CARD_RE.search(str(text or "")):
        return str(text or "")            # by far the common case
    kept_lines = []
    for line in str(text).split("\n"):
        stripped = line.strip()
        if stripped.startswith("•"):
            if not _RATE_CARD_RE.search(stripped):
                kept_lines.append(line)
            continue
        sentences = [part for part in _SENTENCE_SPLIT.split(line)
                     if not _RATE_CARD_RE.search(part)]
        rebuilt = " ".join(part.strip() for part in sentences if part.strip())
        # A line that was only that sentence is dropped rather than left as a
        # blank one, and a paragraph that loses everything takes its own
        # blank line with it.
        if rebuilt or not stripped:
            kept_lines.append(rebuilt if stripped else line)
    return "\n".join(kept_lines)


def clean_ai_text(text) -> str:
    """Generated or pasted copy, in the only shape the renderers agree on.

    Idempotent: running it over already-clean copy changes nothing, which
    matters because it runs on every save as well as on every generation.
    """
    out = str(text or "")
    if not out.strip():
        return ""

    # Bold first, before the loose asterisks below eat its markers.
    out = _BOLD_MD_RE.sub(lambda m: f"<b>{(m.group(1) or m.group(2)).strip()}</b>", out)
    # <strong> is the same intent written differently; normalise, then drop
    # every other tag -- a model that decides to emit <ul> must not reach a
    # renderer that would print the angle brackets.
    out = _BOLD_TAG_RE.sub(lambda m: "</b>" if "/" in m.group(0) else "<b>", out)
    out = re.sub(r"<(?!/?b>)[^>]{0,120}>", "", out)

    out = _EMOJI_RE.sub("", out)
    out = re.sub(r"^\s{0,3}#{1,6}\s*", "", out, flags=re.M)     # headings
    out = re.sub(r"^\s{0,3}[-*+]\s+", "• ", out, flags=re.M)    # bullet markers
    out = re.sub(r"^\s{0,3}>\s?", "", out, flags=re.M)          # block quotes
    out = out.replace("`", "")
    out = re.sub(r"(?<!<)\*+(?!/?b>)", "", out)                 # stray asterisks
    out = re.sub(r"_{2,}", "", out)
    out = re.sub(r"^\s*[-–—_]{3,}\s*$", "", out, flags=re.M)    # rules

    # The rate card is ours, not the client's. Applied here because this is
    # the one function every renderer's copy already passes through -- the AI
    # write, the section editor and anything pasted into it -- so the rule
    # cannot hold only until somebody pastes.
    out = client_safe(out)

    out = re.sub(r"[ \t]{2,}", " ", out)
    out = _one_bullet_per_line(out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def rich_runs(text) -> list[list[tuple[str, bool]]]:
    """Cleaned copy as paragraphs of (text, bold) runs, for python-docx.

    Word has no way to read `<b>` out of a string, so the split has to happen
    before the paragraph is written. Returning runs rather than a marked-up
    string is what stops the DOCX export being the one of the three renderers
    that prints the tag.
    """
    cleaned = clean_ai_text(text)
    if not cleaned:
        return []
    return [_line_runs(block) for block in cleaned.split("\n")]


def plain_text(text) -> str:
    """Cleaned copy with the bold markers removed as well.

    For anywhere that cannot render bold at all — a CSV cell, a webhook
    payload, the Suite opportunity note — so those carry prose rather than a
    tag nobody there interprets.
    """
    return _BOLD_TAG_RE.sub("", clean_ai_text(text))
