"""What the client is already doing, and what we would tell them to do.

The discovery step used to ask four yes/no questions — SEO, paid search,
organic social, paid social — and then do nothing with the answers. They were
captured and never read: not by the proposal copy, not by the recommendation,
not by anything. A rep could answer all four and the document came out
identical.

This module makes those answers mean something. It adds the four questions
that actually change what we recommend, and it turns the whole set into two
outputs the rest of the builder consumes:

  * `suggestions()` — the **We Suggest They Should** list. Plain
    recommendations derived from the gaps the answers expose, shown to the rep
    in its own colour so it reads as our advice rather than as another form
    field. What the rep keeps gets worked into the proposal.

  * `for_prompt()` / `roi_note()` — the same picture, phrased for the AI
    writer and for Expected Results & ROI.

## Traditional media

The last question is the one with money behind it: are they running
traditional media, and if so, do we *supplement* it or *move* the budget into
digital.

That is a strategy decision, not a fact, so it is asked rather than assumed —
and the answer changes the proposal's posture. "Supplement" means digital is
positioned as the measurable layer around media they are already committed to.
"Shift" means the proposal argues for reallocation, with the ROI section
contrasting what the traditional spend can prove against what the digital
spend can.

**Neither is pushed hard.** A proposal that opens by telling a client their
radio buy is wasted loses the room. The posture tunes the language; it does
not turn the document into an argument. The `guidance()` text says so
explicitly, because a model given "move their budget to digital" with no
further instruction will write exactly that argument.
"""
from __future__ import annotations

YES, NO, UNKNOWN = "Yes", "No", "Unknown"
ANSWERS = (YES, NO, UNKNOWN)

SUPPLEMENT = "supplement"
SHIFT = "shift"

# The discovery questions, in the order they are asked. `key` is where the
# answer lives on the quote's `mkt` object.
QUESTIONS = [
    {"key": "seo", "label": "Doing SEO / local listings?"},
    {"key": "paidSearch", "label": "Running paid search (Google / Bing)?"},
    {"key": "socialPosting", "label": "Posting on social media regularly?"},
    {"key": "paidSocial", "label": "Running paid social media ads?"},
    {"key": "retargeting", "label": "Retargeting visitors to their website?"},
    {"key": "aiOptimized", "label": "Optimizing for AI search (AI Overviews, ChatGPT)?"},
    {"key": "websiteHappy", "label": "Happy with their website?"},
]

# One suggestion per gap. `when` is the answers that trigger it: a "No" is a
# gap, and an "Unknown" is a gap we have not confirmed — both are worth
# raising, so both are included, and the copy is written to be true of either.
#
# `products` names what on the rate card would deliver it, so a suggestion the
# rep keeps can be traced to something sellable rather than being advice we
# then have no way to act on.
SUGGESTION_RULES = [
    {
        "key": "retargeting", "when": (NO, UNKNOWN),
        "title": "Retarget the people who already visited",
        "detail": "Someone who has already been to the site is the cheapest "
                  "conversion available and the easiest audience to reach again. "
                  "Website retargeting runs at $4.75 CPM on the card and needs "
                  "only a pixel on the site.",
        "products": ["Website Retargeting"],
    },
    {
        "key": "aiOptimized", "when": (NO, UNKNOWN),
        "title": "Get the site readable by AI search",
        "detail": "Search is turning into an answer engine — AI Overviews, "
                  "ChatGPT and Siri increasingly answer instead of linking. "
                  "Being cited needs accurate schema markup, fast pages and "
                  "clean directory listings across the local platforms.",
        "products": ["Local Business Boost", "Search Engine Optimization"],
    },
    {
        "key": "websiteHappy", "when": (NO,),
        "title": "Fix the page the media points at",
        "detail": "Paid media cannot outrun a landing page that does not "
                  "convert — it just buys more people to lose. If they are not "
                  "happy with the site, that is the first thing to fix, before "
                  "the budget scales.",
        "products": ["Smart 1 Sites", "Web Development"],
    },
    {
        "key": "seo", "when": (NO, UNKNOWN),
        "title": "Claim the organic and map results",
        "detail": "Paid media stops the day the budget stops. Local listings "
                  "and organic visibility compound, and they feed the same "
                  "review and ranking signals AI search reads.",
        "products": ["Search Engine Optimization", "Local Business Boost"],
    },
    {
        "key": "paidSearch", "when": (NO,),
        "title": "Capture the demand already searching",
        "detail": "Everything else in this plan creates demand. Paid search "
                  "catches the people who already have it and are typing the "
                  "words right now — usually the lowest cost per lead on the "
                  "plan.",
        "products": ["PPC Google & Bing Campaign Management"],
    },
    {
        "key": "paidSocial", "when": (NO,),
        "title": "Re-engage in feed",
        "detail": "Paid social is where an audience that has met the brand "
                  "once gets met again, cheaply, with a different message.",
        "products": ["Facebook | Instagram - Targeted Paid Social Media"],
    },
]

# Raised from the traditional-media answer rather than from a gap.
TRADITIONAL_SUGGESTION = {
    SUPPLEMENT: {
        "key": "traditional",
        "title": "Make the traditional buy measurable",
        "detail": "They are already spending on traditional media. Digital "
                  "around it is what makes the whole thing attributable: the "
                  "same households reached on connected TV and streaming "
                  "audio, with every response landing in the Smart 1 Suite "
                  "where it can be counted.",
        "products": ["Connected TV - Targeted", "Programmatic - Targeted"],
    },
    SHIFT: {
        "key": "traditional",
        "title": "Move some of the traditional budget where it can be counted",
        "detail": "The traditional spend reaches people; what it cannot do is "
                  "tell them which half worked. Shifting a portion into "
                  "targeted digital keeps the reach and adds attribution — "
                  "worth testing with a share of the budget rather than all "
                  "of it.",
        "products": ["Connected TV - Targeted", "Advanced TV - Targeted"],
    },
}


def _answers(state) -> dict:
    mkt = (state or {}).get("mkt")
    return dict(mkt) if isinstance(mkt, dict) else {}


def _traditional(state) -> dict:
    raw = (state or {}).get("traditional")
    return dict(raw) if isinstance(raw, dict) else {}


def answered(state) -> int:
    """How many discovery questions have an answer. Feeds the step's validity."""
    mkt = _answers(state)
    return sum(1 for q in QUESTIONS if mkt.get(q["key"]) in ANSWERS)


def suggestions(state) -> list[dict]:
    """The We Suggest They Should list for this client.

    Dismissed suggestions are excluded, so the rep's own judgement is what
    reaches the proposal. Order follows SUGGESTION_RULES, which is roughly
    cheapest-and-most-obvious first — a rep reading down the list should meet
    the easy wins before the projects.
    """
    mkt = _answers(state)
    dismissed = set((state or {}).get("suggestDismissed") or [])
    out = []
    for rule in SUGGESTION_RULES:
        if rule["key"] in dismissed:
            continue
        if mkt.get(rule["key"]) in rule["when"]:
            out.append({k: rule[k] for k in ("key", "title", "detail", "products")})

    trad = _traditional(state)
    if trad.get("running") == YES and "traditional" not in dismissed:
        posture = trad.get("posture")
        if posture in TRADITIONAL_SUGGESTION:
            out.append(dict(TRADITIONAL_SUGGESTION[posture]))
    return out


def traditional_summary(state) -> str:
    """What they told us about their traditional media, in one line."""
    trad = _traditional(state)
    running = trad.get("running")
    if running == NO:
        return "No traditional media."
    if running not in (YES, UNKNOWN):
        return ""
    if running == UNKNOWN:
        return "Traditional media: not established."
    bits = ["Running traditional media"]
    if trad.get("detail"):
        bits.append(str(trad["detail"]).strip())
    if trad.get("budget"):
        bits.append(f"about {str(trad['budget']).strip()}")
    posture = trad.get("posture")
    if posture == SUPPLEMENT:
        bits.append("Smart 1 to supplement it with measurable digital")
    elif posture == SHIFT:
        bits.append("considering moving some of that budget into digital")
    return " · ".join(bits)


def guidance(state) -> str:
    """What the AI writer is allowed to do with the traditional-media answer.

    The instruction not to argue is as important as the posture itself. A
    model handed "they want to shift budget to digital" writes a case against
    radio; a proposal that opens by telling a client their existing media is
    wasted loses the room before the media plan is read.
    """
    trad = _traditional(state)
    if trad.get("running") != YES:
        return ""
    posture = trad.get("posture")
    detail = str(trad.get("detail") or "").strip()
    budget = str(trad.get("budget") or "").strip()

    known = "They are already running traditional media"
    if detail:
        known += f": {detail}"
    if budget:
        known += f" (about {budget})"
    known += "."

    if posture == SHIFT:
        stance = ("They are open to moving some of that budget into digital. "
                  "Position digital as the part that can be measured and "
                  "attributed, and frame any reallocation as worth testing "
                  "with a share of the budget.")
    elif posture == SUPPLEMENT:
        stance = ("The plan supplements that spend rather than replacing it. "
                  "Position digital as the measurable layer around media they "
                  "are already committed to, reaching the same households on "
                  "screens the traditional buy cannot follow them to.")
    else:
        stance = ("No decision has been made about that budget, so describe "
                  "how digital works alongside it and leave the question open.")

    return (known + " " + stance + " Mention this once, where it is relevant — "
            "do not argue against their traditional media, do not open with "
            "it, and never claim their existing spend is wasted.")


def roi_note(state) -> str:
    """The line Expected Results & ROI adds about traditional media, if any."""
    trad = _traditional(state)
    if trad.get("running") != YES:
        return ""
    posture = trad.get("posture")
    if posture == SHIFT:
        return ("Every figure above is attributable to the channel that produced "
                "it. That is the difference worth weighing against the "
                "traditional spend: not whether it reaches people, but whether "
                "what it reached can be counted.")
    if posture == SUPPLEMENT:
        return ("These figures cover the digital portion of the plan. They sit "
                "alongside the traditional buy rather than replacing it, and "
                "they are the part of the program that reports a number.")
    return ""


def for_prompt(state) -> str:
    """The whole discovery picture, for the AI writer's fact block."""
    mkt = _answers(state)
    lines = []
    for q in QUESTIONS:
        value = mkt.get(q["key"])
        if value in ANSWERS:
            lines.append(f"{q['label']} {value}")
    summary = traditional_summary(state)
    if summary:
        lines.append(summary)
    return " | ".join(lines)


def gaps(state) -> list[dict]:
    """Discovery the proposal is missing, in the builder's gap-check shape.

    Only the traditional-media posture, and only once they have said they run
    it: everything else here is genuinely optional intelligence, while an
    unanswered supplement-or-shift means the proposal has no position on the
    largest number in the room.
    """
    trad = _traditional(state)
    if trad.get("running") == YES and trad.get("posture") not in (SUPPLEMENT, SHIFT):
        return [{"key": "traditional_posture",
                 "label": "Whether we supplement their traditional media or "
                          "move some of that budget into digital"}]
    return []
