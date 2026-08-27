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
    {"key": "socialScheduling", "label": "Scheduling those posts ahead with a tool?"},
    {"key": "paidSocial", "label": "Running paid social media ads?"},
    {"key": "retargeting", "label": "Retargeting visitors to their website?"},
    {"key": "aiOptimized", "label": "Optimizing for AI search (AI Overviews, ChatGPT)?"},
    {"key": "websiteHappy", "label": "Happy with their website?"},
    {"key": "reputation", "label": "Managing reviews and reputation?"},
    {"key": "email", "label": "Running email campaigns?"},
    {"key": "chat", "label": "Chat widget on the site?"},
    {"key": "callTracking", "label": "Tracking inbound calls?"},
    {"key": "texting", "label": "Texting customers?"},
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
        "products": ["Smart 1 Site / 2-5 pages", "WordPress Website"],
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
        "products": ["Pay Per Click"],
    },
    {
        "key": "paidSocial", "when": (NO,),
        "title": "Re-engage in feed",
        "detail": "Paid social is where an audience that has met the brand "
                  "once gets met again, cheaply, with a different message.",
        "products": ["Facebook | Instagram - Targeted Paid Social Media Advertising"],
    },
]

SUGGESTION_RULES += [
    {
        "key": "callTracking", "when": (NO, UNKNOWN),
        "title": "Count the calls the media produces",
        "detail": "A local campaign's best leads arrive as phone calls, and an "
                  "untracked call is a conversion the report cannot show. "
                  "Without it the plan gets judged on the half of the response "
                  "that happens to leave a form fill behind.",
        "products": ["Call Tracking", "Smart 1 Suite"],
    },
    {
        "key": "reputation", "when": (NO, UNKNOWN),
        "title": "Get the reviews working for the ads",
        "detail": "Star rating is the first thing a click sees and it feeds "
                  "the same local signals AI search reads. Media pointed at a "
                  "three-star listing pays more for every conversion it gets.",
        "products": ["Local Business Boost", "Smart 1 Suite"],
    },
    {
        "key": "chat", "when": (NO,),
        "title": "Answer the visitors who will not fill in a form",
        "detail": "Chat catches the people who want an answer now and would "
                  "otherwise leave. It is the cheapest lift available to a "
                  "landing page that traffic is already being bought for.",
        "products": ["Smart 1 Suite", "Smart 1 Site / 2-5 pages"],
    },
    {
        "key": "texting", "when": (NO,),
        "title": "Reply the way the lead expects",
        "detail": "Speed to lead decides most local sales, and a text is "
                  "answered where a voicemail is not. The Suite already holds "
                  "the numbers the campaign produces.",
        "products": ["Smart 1 Suite", "Snap Management Fee & Texting for Snap"],
    },
    {
        "key": "email", "when": (NO,),
        "title": "Use the list they already own",
        "detail": "Email reaches people who have already bought or inquired, "
                  "at no media cost per impression. It is the one channel "
                  "here that does not need a budget to reach an audience.",
        "products": ["List Provided Email", "Email Template Creation"],
    },
]

# Two questions were being asked and read by nothing at all. `socialPosting`
# had no rule under it from the day it was written, so a client who posts
# nothing produced a proposal that never mentioned it; `socialScheduling` is
# new, and is the one of the twelve the Suite answers most directly. Every
# question on the discovery step now has a rule, and `unanswered_keys()` below
# is what keeps it that way -- a thirteenth added without one is a question
# whose answer changes nothing, which is where this module started.
SUGGESTION_RULES += [
    {
        "key": "socialPosting", "when": (NO, UNKNOWN),
        "title": "Give the paid social somewhere to land",
        "detail": "An ad clicks through to a profile that has not posted since "
                  "last year, and the click is where the interest stops. A page "
                  "that looks maintained is what makes paid social worth "
                  "running at all, and it is the cheapest half of it.",
        "products": ["Social Media Management", "Smart 1 Suite"],
    },
    {
        "key": "socialScheduling", "when": (NO, UNKNOWN),
        "title": "Schedule the month instead of remembering it",
        "detail": "Posting by hand is what stops first when the business gets "
                  "busy, which is exactly when the campaign is working. A "
                  "month planned and queued in one sitting keeps posting "
                  "through the weeks nobody has time for it.",
        "products": ["Smart 1 Suite", "Social Media Management"],
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


def unanswered(state) -> list[dict]:
    """The discovery questions still blank.

    Every one of them is required. A blank is not a "no" and it is not an
    "unknown" either -- it is nobody having asked, and it was reaching the
    proposal as though the client simply did not do that thing. Unknown is on
    the form precisely so there is an honest answer available when the rep
    genuinely does not know.
    """
    mkt = _answers(state)
    return [q for q in QUESTIONS if mkt.get(q["key"]) not in ANSWERS]


def complete(state) -> bool:
    """Whether discovery can be left behind."""
    return not unanswered(state)


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


# ---------------------------------------------------------------------------
# What the Suite covers, and what nothing on the plan does
#
# Six of the twelve discovery questions describe work the Smart 1 Suite does
# out of the box -- the missed call text back, the review requests, the social
# planner, the scheduler, the inbox. The Suite was being quoted on every
# proposal anyway, at a tier picked purely from media spend, with the client
# never told which of the things they said they were not doing it closes. So
# the one line on the Investment Summary that recurs for ever was the one line
# with no stated reason for being there.
#
# `suite_coverage()` is that reason, built from their own answers rather than
# from a feature list: a client who is already texting, already scheduling and
# already asking for reviews gets a shorter list, honestly, and a rep can see
# the tier is carrying less than it looks.
#
# `MIN_TIER` is which tier actually has the feature. Smart webchat is Smarter
# and up -- offering it against a Smart 1 licence is selling something the
# client cannot switch on.
# ---------------------------------------------------------------------------
SUITE_FEATURES = [
    {"key": "callTracking", "tier": "Smart 1",
     "feature": "Call tracking and the call center",
     "detail": "Every campaign number tracked, recorded and attributed to the "
               "channel that produced it, with Missed Call Text Back on the "
               "ones nobody picks up."},
    {"key": "texting", "tier": "Smart 1",
     "feature": "Two-way texting",
     "detail": "The texting center, so a lead is answered the way they expect "
               "rather than with a voicemail nobody returns."},
    {"key": "reputation", "tier": "Smart 1",
     "feature": "Reputation center",
     "detail": "Automated Google review requests after a job, and every "
               "review answered from one inbox."},
    # Both social questions are answered by the same part of the Suite, so
    # they share a group and are claimed once. Listed separately they read as
    # two things the licence buys -- "Social planner" directly above "Social
    # planner and media library" -- which makes the whole list look padded,
    # on the one panel whose job is to justify a recurring charge.
    {"key": "socialScheduling", "tier": "Smart 1", "group": "social",
     "feature": "Social planner",
     "detail": "A month of posts written, queued and scheduled in one sitting "
               "across every connected channel, with the campaign's own "
               "creative already in the media library."},
    {"key": "socialPosting", "tier": "Smart 1", "group": "social",
     "feature": "Social planner",
     "detail": "Somewhere for the posting to actually happen, with the "
               "campaign's own creative already in it."},
    {"key": "email", "tier": "Smart 1",
     "feature": "Email center",
     "detail": "Campaigns and automated follow-up to the list they already "
               "own, at no media cost per send."},
    {"key": "chat", "tier": "Smarter",
     "feature": "Smart webchat",
     "detail": "Catches the visitors who will not fill in a form, and turns "
               "the conversation into a text thread that survives the visit."},
]

# Tier order, weakest first, so "does this tier include it" is a comparison
# rather than a table of every combination.
_TIER_ORDER = ["Smart 1", "Smarter", "Smartest"]


def unanswered_keys() -> list[str]:
    """Discovery questions with no suggestion rule and no Suite feature.

    An answer that changes nothing is the defect this whole module exists to
    close, so it is reported rather than left to be noticed. `/api/integrity`
    and test_proposal_spec.py both read this; it returns an empty list today
    and a thirteenth question added without a rule is what makes it stop.
    """
    covered = {rule["key"] for rule in SUGGESTION_RULES}
    covered |= {f["key"] for f in SUITE_FEATURES}
    return [q["key"] for q in QUESTIONS if q["key"] not in covered]


def gaps_named(state) -> list[dict]:
    """Every discovery answer that is a No or an Unknown, with its label.

    The single reading of "what are they not doing", so the suggestion list,
    the Suite panel and the proposal copy cannot disagree about it.
    """
    mkt = _answers(state)
    return [{"key": q["key"], "label": q["label"].rstrip("?"),
             "answer": mkt.get(q["key"])}
            for q in QUESTIONS if mkt.get(q["key"]) in (NO, UNKNOWN)]


def suite_coverage(state, tier_name: str = "") -> dict:
    """Which of their gaps the quoted Suite tier closes, and which it cannot.

    Three answers, never two. `covered` is what this tier does today.
    `needs_upgrade` is a gap a higher tier closes -- named with the tier, so
    the choice is a decision rather than a discovery six weeks in. And
    `not_measured` is the honest one: a question nobody answered is not a gap
    the Suite can be credited with closing.
    """
    mkt = _answers(state)
    tier = str(tier_name or "").strip() or _TIER_ORDER[0]
    try:
        have = _TIER_ORDER.index(tier)
    except ValueError:
        have = 0

    covered, needs_upgrade, not_measured = [], [], []
    for feature in SUITE_FEATURES:
        answer = mkt.get(feature["key"])
        if answer == YES:
            continue
        row = {k: feature[k] for k in ("key", "feature", "detail", "tier")}
        row["group"] = feature.get("group") or feature["key"]
        row["answer"] = answer or ""
        if answer not in (NO, UNKNOWN):
            not_measured.append(row)
            continue
        try:
            need = _TIER_ORDER.index(feature["tier"])
        except ValueError:
            need = 0
        (covered if need <= have else needs_upgrade).append(row)

    def once(rows):
        """One row per capability. Two questions can want the same feature."""
        seen, out = set(), []
        for row in rows:
            if row["group"] in seen:
                continue
            seen.add(row["group"])
            out.append(row)
        return out

    covered = once(covered)
    return {"tier": tier, "covered": covered,
            "needs_upgrade": once(needs_upgrade),
            "not_measured": [r for r in once(not_measured)
                             if r["group"] not in {c["group"] for c in covered}],
            "ok": bool(covered) or bool(needs_upgrade)}


def suite_line(state, tier_name: str = "") -> str:
    """One sentence for the proposal: what the licence is doing here.

    Empty when discovery says they already have all of it -- a Suite sentence
    listing nothing is worse than no sentence, and the tier may genuinely be
    carrying less than the rep assumed.
    """
    result = suite_coverage(state, tier_name)
    names = [row["feature"] for row in result["covered"]]
    if not names:
        return ""
    if len(names) == 1:
        listed = names[0]
    else:
        listed = ", ".join(names[:-1]) + " and " + names[-1]
    return (f"Against what this client told us they are not doing today, the "
            f"{result['tier']} license covers {listed.lower()}.")
