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
    it is computed from the rate card rather than written by the model, so a
    projection can never contradict the media plan printed above it.

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
#   timeline  the 30-day implementation blueprint, generated
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
        "id": "timeline", "title": "Implementation Timeline", "kind": "timeline",
        "enabled": True,
        "purpose": "Reduce transition anxiety with predictable pacing.",
        "guidance": "",
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
        "guidance": "The delivery figures below are computed from the rate card. Above "
                    "them, state which metrics we track and how the Smart 1 Suite is the "
                    "single source of truth. Be conservative. Never state a conversion "
                    "rate, a lead count or a revenue figure the intake does not support.",
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
        # two sections above, and adding one quotes the rate card's own
        # minimum for that product.
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
DIRECTIVES = [
    "Never reference Smart 1 Labs. Technology recommendations cover Smart 1 Sites, "
    "Smart 1 Snap and the Smart 1 Suite's operational tools only.",

    "Position the Smart 1 Suite as the central nervous system of the campaign. Paid "
    "media is never a standalone solution: the channels feed traffic into the Suite, "
    "which captures and nurtures those leads through Missed Call Text Back, unified "
    "communications and automated text and email workflows. Frame the Suite as what "
    "turns awareness into measurable revenue.",

    "Absolute client confidentiality. Never name another Smart 1 Marketing client, "
    "current or former, in this proposal — not as a case study, not as a credential, "
    "not as an example. Use anonymised industry benchmarks instead.",

    "Use only the facts supplied. Do not invent statistics, client results, awards, "
    "years in business, service areas, conversion rates or capabilities. Every price, "
    "product name and delivery figure comes from the rate card and is already in the "
    "tables the client will read next to your copy; contradicting one is worse than "
    "omitting it.",

    "Write as a Smart 1 senior strategist: consultative, confident, specific. Never "
    "open with 'Based on the information provided' or 'It is important to note'. "
    "Explain programmatic, CTV, DOOH and IP targeting in terms a business owner would "
    "use, not in ad-tech vocabulary.",
]


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
    {
        "name": "Smart 1", "monthly": 199,
        "specs": "5 users · 250 contacts · 5 forms · 5 pipelines · 1,500 emails · "
                 "500 texts · 1 phone number · 500 calls · 1 workflow",
        "features": "Unified message center, media library, email center, texting "
                    "center, call center, online scheduling, reputation center with "
                    "automated Google review requests, social planner, Smart 1 Sites.",
    },
    {
        "name": "Smarter", "monthly": 599,
        "specs": "10 users · 1,000 contacts · 10 forms · 5 pipelines · 5,000 emails · "
                 "1,500 texts · 1 phone number · 2,500 calls · unlimited workflows",
        "features": "Everything in Smart 1, plus the AI writing assistant, review "
                    "widget with sentiment analysis, Facebook and TikTok lead form "
                    "integration, smart webchat, and member courses.",
    },
    {
        "name": "Smartest", "monthly": 999,
        "specs": "Unlimited users and contacts · 100 forms · 50 pipelines · 100,000 "
                 "emails · 10,000 texts · 5 phone numbers · 10,000 calls · unlimited "
                 "workflows",
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
# The 30-day implementation blueprint
# ---------------------------------------------------------------------------
TIMELINE = [
    {"phase": "Weeks 1–2", "title": "Setup & Onboarding",
     "detail": "Admin access collected, tracking pixels placed, conversion actions "
               "defined, audience and geography built, Smart 1 Suite provisioned."},
    {"phase": "Weeks 3–4", "title": "Production & Integration",
     "detail": "Creative produced or supplied and approved, landing pages reviewed, "
               "workflow automations mapped in the Smart 1 Suite, tracking verified "
               "end to end before a cent is spent."},
    {"phase": "Month 2+", "title": "Execution & Scaling",
     "detail": "Campaigns live, weekly pacing and bid optimization, creative "
               "performance checks, budget shifted toward what proves out, first "
               "full performance review delivered."},
]

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
