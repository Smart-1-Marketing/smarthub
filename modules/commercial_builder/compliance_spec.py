"""Which published advertising rules a spot engages, and who says so.

Data and arithmetic, no Flask, the way `review_spec.py` and
`services/abcd_service.py` are: the Blueprint panel, the QC check, the filing
gate and the test all read one description.

## Why this exists

This tool renders finished, deliverable video. Two of the commercial types it
offers on the Start page walk straight into published rules — `testimonial` is
a literal option, and the offer field invites exactly the copy Reg Z triggers
on ("$79 a month", "0% APR", "no money down"). Nothing anywhere asked. The
first person to find out was whoever had to answer for the spot after it ran,
and by then it had run.

## The one thing this module must never do

**It never says a spot is compliant.** Nothing here is legal advice, nothing
here is a clearance, and no output of this file may read as one. Compliance is
a judgment about a specific ad, in a specific state, for a specific client,
made by somebody qualified — and a tool that draws a green tick over that
question is worse than a tool that says nothing, because the tick is what
somebody relies on.

What it does instead is narrower and actually useful: it says **which
published rule is in play and what that rule requires**, with the citation, so
the conversation happens before the render rather than after the flight. Every
finding is phrased as *this engages X* and never as *this violates X* — the
first is a fact about the copy, the second is a legal conclusion.

That is also why nothing here blocks a render. `QR_CODE_RULES` learned this
the expensive way: a check that refuses the correct thing is a check somebody
switches off, and switching it off costs every other finding it would have
raised. What a finding does instead is require an **acknowledgment** — one
explicit "we have checked this", recorded against a name — the shape
`hub/creative_needs.py` uses for a comp confirmation on a low-spend medium.

## Every rule names its authority

The `abcd_service.py` rule, and for the same reason: "16 CFR 255.5 requires a
material connection to be disclosed" is a citation somebody can look up and
act on, where "our tool thinks you need a disclaimer" is an opinion a rep gets
argued out of by a client who wants the spot to run.

## What is deliberately NOT here

**The FTC CARS Rule (16 CFR Part 463).** It was vacated in its entirety by the
Fifth Circuit in January 2025, so a tool flagging it would be raising a rule
that does not exist — the confidently wrong answer this codebase keeps having
to undo, wearing a regulation. Named here rather than silently omitted, so
nobody adds it back from memory.

**Anything measured off the rendered file.** Whether a super is legible for
long enough is a question about pixels and duration; this reads the plan. A
disclosure the script does not mention is a finding; one that is there but too
small to read is not something this can see, and it says so.

**Fifty states.** Attorney advertising is genuinely state-by-state, and
encoding one state's rules as *the* rules would be worse than a pointer:
`state_bar` says which state's rules govern is the question and does not
pretend to answer it.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# The regimes. `citation` is what a person looks up; `authority` is who wrote
# it. Both are printed on every finding.
# ---------------------------------------------------------------------------
REGIMES = {
    "reg_z": {
        "label": "Truth in Lending (Regulation Z)",
        "authority": "Consumer Financial Protection Bureau",
        "citation": "12 CFR 1026.24 (closed-end credit advertising); 1026.16 (open-end)",
        "applies_to": ("Any advertisement for consumer credit — a payment, a rate, "
                       "a down payment or a term."),
    },
    "ftc_endorsements": {
        "label": "Endorsements and testimonials",
        "authority": "Federal Trade Commission",
        "citation": "16 CFR Part 255 (Guides Concerning Use of Endorsements and "
                    "Testimonials in Advertising, revised 2023)",
        "applies_to": ("Any ad carrying a customer testimonial, a review, an "
                       "influencer, or an actor presented as a real customer."),
    },
    "finra_2210": {
        "label": "Communications with the public",
        "authority": "Financial Industry Regulatory Authority",
        "citation": "FINRA Rule 2210",
        "applies_to": ("Broker-dealers and their registered representatives. A "
                       "broadcast or streaming spot is a retail communication."),
    },
    "state_bar": {
        "label": "Attorney advertising",
        "authority": "The state bar with jurisdiction — rules vary by state",
        "citation": "ABA Model Rules 7.1-7.3, as adopted and amended by each state",
        "applies_to": "Law firms and anyone advertising legal services.",
    },
    "ttb": {
        "label": "Alcohol beverage advertising",
        "authority": "Alcohol and Tobacco Tax and Trade Bureau",
        "citation": "27 CFR Part 4 (wine), Part 5 (distilled spirits), Part 7 "
                    "(malt beverages) — advertising subparts",
        "applies_to": "Producers, importers and wholesalers of alcohol beverages.",
    },
}

# Vacated, and named so nobody adds it back from memory. See the module
# docstring: flagging a rule that no longer exists is the same failure as
# missing one that does.
NOT_ENFORCED = {
    "cars_rule": {
        "label": "FTC CARS Rule (motor vehicle dealers)",
        "citation": "16 CFR Part 463",
        "why": ("Vacated in its entirety by the Fifth Circuit in January 2025. It "
                "is not in force, so this tool does not raise it. Ordinary FTC "
                "deception law and Regulation Z still apply to a vehicle ad, and "
                "both are covered above."),
    },
}


# ---------------------------------------------------------------------------
# Which regime a client's industry engages.
#
# Matched on words in the client's own industry text, which is free text and
# often empty. That last part is the important one: an EMPTY industry is "not
# measured", never "no rules apply" — the absent-is-not-zero rule, on the one
# question where a confident zero is a spot going out unchecked.
# ---------------------------------------------------------------------------
INDUSTRY_SIGNALS = {
    "finra_2210": ("broker", "broker-dealer", "securities", "investment",
                   "wealth", "financial advisor", "financial adviser",
                   "financial planning", "brokerage", "mutual fund", "annuity"),
    "state_bar": ("law", "lawyer", "attorney", "legal", "law firm", "counsel",
                  "litigation", "solicitor"),
    "ttb": ("brewery", "brewing", "winery", "wine", "distillery", "distilled",
            "spirits", "beer", "cidery", "taproom", "liquor", "alcohol"),
    # Reg Z is not an industry — it is a thing the COPY says. A furniture shop
    # advertising "$40 a month" engages it and a bank advertising its brand
    # does not, so it is detected from the script and never from the client.
}


# ---------------------------------------------------------------------------
# The patterns. Each is deliberately narrow: a rule that fires on every spot
# is a rule people stop reading, which is the note QR_CODE_RULES carries.
# ---------------------------------------------------------------------------
# Reg Z 1026.24(d)(1) triggering terms. Any ONE of these in an ad for consumer
# credit obliges the additional disclosures in (d)(2).
_TRIGGER_DOWN = re.compile(
    r"\b(?:no (?:money )?down|zero down|\$\s?\d[\d,]*\s+down|"
    r"\d{1,3}\s?%\s+down)\b", re.I)
_TRIGGER_PAYMENT = re.compile(
    # `month` before `mo`, or the alternation takes the short branch and the
    # evidence reads "$40 a mo" — a quotation a reader cannot find in the
    # script is not evidence.
    r"(?:\$\s?\d[\d,]*(?:\.\d{2})?\s*(?:/|\bper\b|\ba\b)\s*(?:months?|mo\b|"
    r"weeks?|payment)|\bpayments? of \$\s?\d[\d,]*)", re.I)
_TRIGGER_TERM = re.compile(
    r"\b(?:\d{1,3}\s*(?:month|months|year|years)\s*(?:to pay|term|financing|"
    r"payments)|\d{1,3}\s+(?:monthly|weekly)\s+payments)\b", re.I)
_TRIGGER_FINANCE_CHARGE = re.compile(r"\bfinance charge\b", re.I)
# 1026.24(c): stating a rate of finance charge obliges the APR, in those words.
#
# The credit word is REQUIRED, on either side. A bare percentage is not a rate
# of finance charge — "20% off everything" is the commonest line in retail
# copy, and reporting it as a Truth in Lending finding is the rule that fires
# on every spot, which is the note QR_CODE_RULES carries about a check people
# stop reading.
_RATE_STATED = re.compile(
    r"(?:\b(?:apr|interest|financing|finance rate)[^.!?\n]{0,20}?"
    r"\d{1,2}(?:\.\d+)?\s?%"
    r"|\b\d{1,2}(?:\.\d+)?\s?%\s*(?:apr|interest|financing|"
    r"a\.?p\.?r\.?|for \d+ month)"
    r"|\b(?:0|zero)\s?%\s*(?:financing|interest|apr|for \d+ month))", re.I)
_APR_PRESENT = re.compile(r"\bA\.?P\.?R\.?\b|\bannual percentage rate\b", re.I)

# FTC 255. A testimonial is the trigger; a disclosure of the connection and of
# typical results is what 255.5 and 255.2 require.
_TESTIMONIAL_WORDS = re.compile(
    r"\b(?:testimonial|real customer|actual customer|our customers say|"
    r"here'?s what .{0,20}(?:say|said)|review from|five[- ]star review|"
    r"i (?:switched|called|hired|bought) )", re.I)
_MATERIAL_CONNECTION = re.compile(
    r"\b(?:paid (?:actor|spokesperson|endorsement|partnership)|sponsored|"
    r"#ad\b|compensated|dramatization|actor portrayal|"
    r"actual customer compensated)\b", re.I)
# No trailing \b: "saved $400" matched "saved $4" and then wanted a word
# boundary before the "0", so the whole alternation failed. Silent, and it
# cost the typical-results rule every time.
_RESULTS_CLAIM = re.compile(
    r"\b(?:saved \$?\d[\d,]*|lost \d+\s*(?:lb|pound|kg)|"
    r"(?:earned|made) \$\s?\d[\d,]*|"
    r"\d+\s?%\s+(?:more|less|increase|decrease|savings)|\bresults\b)", re.I)
_TYPICALITY = re.compile(
    r"\b(?:results (?:may |will )?(?:vary|not be typical)|"
    r"not typical|typical results|individual results)\b", re.I)

# FINRA 2210(d)(1): no predictions or projections of performance.
_PERFORMANCE_CLAIM = re.compile(
    r"\b(?:guaranteed? (?:return|income|growth)|\d{1,2}(?:\.\d+)?\s?%\s*"
    r"(?:return|yield|growth|gain)|risk[- ]free|beat the market|"
    r"outperform|double your (?:money|investment))\b", re.I)

# State bar: past results, and specialisation claims.
# Every branch carries the figure through to the end. "recovered $" is not a
# quotation anybody can find in a script, which is the whole job of evidence.
_PAST_RESULTS = re.compile(
    r"(?:\$\s?\d[\d,.]*\s*(?:million|billion|k\b)?\s*"
    r"(?:recovered|verdict|settlement|won|awarded)"
    r"|(?:won|recovered|awarded|secured)\s+(?:over\s+|more than\s+)?"
    r"\$\s?\d[\d,.]*\s*(?:million|billion|k\b)?"
    r"|\b\d+\s+(?:cases|verdicts|settlements)\s+won)", re.I)
_PAST_RESULTS_DISCLAIMER = re.compile(
    r"\b(?:prior results|past results)\b.{0,60}\b(?:guarantee|predict|"
    r"similar outcome)\b", re.I)
_SPECIALIST_CLAIM = re.compile(
    r"\b(?:specialists?|specializing in|experts? in|certified expert|"
    r"(?:best|top|#1|number one|leading) (?:lawyers?|attorneys?|law firms?))\b",
    re.I)

# TTB: health and therapeutic claims are prohibited outright; the mandatory
# statements are what an ad has to carry.
_HEALTH_CLAIM = re.compile(
    r"\b(?:healthy|health benefit|heart[- ]healthy|good for you|"
    r"antioxidant|low[- ]calorie diet|cure|remedy|therapeutic|"
    r"reduces? (?:the )?risk)\b", re.I)
_RESPONSIBLE_ADVERTISER = re.compile(
    r"\b(?:bottled by|produced by|imported by|distilled by|brewed by)\b", re.I)


# ---------------------------------------------------------------------------
# The rules themselves. `requires` is what the cited rule obliges; `found` is
# what was seen in the copy. Both are printed, because the second is checkable
# and the first is the reason.
# ---------------------------------------------------------------------------
def _finding(rule_id, regime, headline, requires, evidence="", satisfied=None):
    spec = REGIMES[regime]
    return {
        "id": rule_id,
        "regime": regime,
        "regime_label": spec["label"],
        "authority": spec["authority"],
        "citation": spec["citation"],
        "headline": headline,
        "requires": requires,
        # What in the copy put this in play. "This ad states a payment" and
        # "this ad says '$79/month'" are different claims, and only the second
        # can be checked by the person reading the panel.
        "evidence": evidence,
        # Tri-state, and the middle one is the point: we can sometimes see
        # that the script already carries what the rule asks for. We can never
        # see that the spot is compliant.
        "addressed": satisfied,
    }


def _text_of(script, brief, cta):
    """Everything a viewer hears or reads, as one string.

    The narration, the on-screen text, the offer as the rep typed it and the
    end card. A disclosure that exists only in the brief is not in the ad, but
    an OFFER typed into the brief is what the script gets written from, so it
    counts as copy for the purpose of asking which rules are in play.
    """
    parts = []
    for scene in (script or {}).get("scenes", []) or []:
        for key in ("voiceover", "visual", "on_screen_text", "text"):
            value = (scene or {}).get(key)
            if value:
                parts.append(str(value))
    for key in ("what_advertising", "offer", "key_message", "disclaimer",
                "legal_line", "notes"):
        value = (brief or {}).get(key)
        if value:
            parts.append(str(value))
    for key in ("headline", "subhead", "offer", "disclaimer", "legal_line"):
        value = (cta or {}).get(key)
        if value:
            parts.append(str(value))
    return "\n".join(parts)


def industries_engaged(industry):
    """Which regimes this client's industry puts in play, and whether we know.

    Returns `(regimes, known)`. `known` is False when the client has no
    industry recorded — and that is reported as *not measured* rather than as
    "no rules apply", because those are different answers and only one of them
    is a reason to stop looking. It is the same tri-state
    `connected_accounts_result()` draws in Google Finder, on the question
    where a confident empty answer is a spot going out unchecked.
    """
    text = str(industry or "").strip().lower()
    if not text:
        return [], False
    hit = [regime for regime, words in INDUSTRY_SIGNALS.items()
           if any(word in text for word in words)]
    return sorted(hit), True


def scan(script=None, brief=None, cta=None, client=None, commercial_type=""):
    """Which published rules this spot engages, with the citation on each.

    Never raises: this runs inside QC and a scanning bug must not take down
    the panel it reports on. Never returns a verdict — see the module
    docstring. `findings` is what is in play; `unknown_industry` says when the
    client's industry could not be read, which is a finding of its own.
    """
    try:
        return _scan(script, brief, cta, client, commercial_type)
    except Exception as exc:  # noqa: BLE001
        return {"findings": [], "regimes": [], "industry_known": False,
                "measured": False,
                "note": f"The compliance scan could not run: {exc}",
                "disclaimer": DISCLAIMER}


DISCLAIMER = (
    "This is not legal advice and it is not a clearance. It says which "
    "published rule the copy puts in play and what that rule requires — "
    "whether this particular spot complies is a judgment for the client's "
    "own counsel or compliance officer.")


def _scan(script, brief, cta, client, commercial_type):
    text = _text_of(script, brief, cta)
    industry = (client or {}).get("industry") if isinstance(client, dict) else \
        getattr(client, "industry", "")
    by_industry, industry_known = industries_engaged(industry)
    findings = []

    # --- Regulation Z ------------------------------------------------------
    # Detected from the copy and never from the industry: a furniture shop
    # advertising "$40 a month" engages it and a bank advertising its brand
    # does not.
    triggers = []
    for pattern, name in ((_TRIGGER_DOWN, "a down payment"),
                          (_TRIGGER_PAYMENT, "a payment amount"),
                          (_TRIGGER_TERM, "a repayment term"),
                          (_TRIGGER_FINANCE_CHARGE, "a finance charge")):
        found = pattern.search(text)
        if found:
            triggers.append((name, found.group(0).strip()))
    if triggers:
        findings.append(_finding(
            "reg_z_triggering_term", "reg_z",
            "This ad states a credit triggering term",
            "12 CFR 1026.24(d)(2): an ad stating any triggering term must also "
            "state the amount or percentage of the down payment, the terms of "
            "repayment, and the annual percentage rate — using that phrase, and "
            "saying so if the rate may increase.",
            evidence="; ".join(f"{name} (“{seen}”)" for name, seen in triggers),
            satisfied=bool(_APR_PRESENT.search(text)) or None))

    rate = _RATE_STATED.search(text)
    if rate and not _APR_PRESENT.search(text):
        findings.append(_finding(
            "reg_z_rate_without_apr", "reg_z",
            "A rate is stated without the words “annual percentage rate”",
            "12 CFR 1026.24(c): an ad stating a rate of finance charge must "
            "state it as an annual percentage rate, using that term or "
            "“APR”.",
            evidence=f"“{rate.group(0).strip()}”",
            satisfied=False))

    # --- FTC endorsements --------------------------------------------------
    is_testimonial = (str(commercial_type or "") == "testimonial"
                      or bool(_TESTIMONIAL_WORDS.search(text)))
    if is_testimonial:
        findings.append(_finding(
            "ftc_material_connection", "ftc_endorsements",
            "This spot carries a testimonial or endorsement",
            "16 CFR 255.5: any connection between the endorser and the "
            "advertiser that a viewer would not expect — payment, free "
            "product, employment, a family tie — must be disclosed clearly "
            "and conspicuously. 16 CFR 255.1: an endorsement must reflect the "
            "endorser's honest opinion and actual experience, and an actor "
            "presented as a real customer must be identified as a "
            "dramatization.",
            evidence=("the commercial type is Testimonial"
                      if str(commercial_type or "") == "testimonial"
                      else f"“{_TESTIMONIAL_WORDS.search(text).group(0).strip()}”"),
            satisfied=bool(_MATERIAL_CONNECTION.search(text)) or None))

        results = _RESULTS_CLAIM.search(text)
        if results:
            findings.append(_finding(
                "ftc_typical_results", "ftc_endorsements",
                "A testimonial that describes a result",
                "16 CFR 255.2: where the endorser's experience is not what "
                "consumers generally achieve, the advertiser must disclose the "
                "results consumers can generally expect. The 2023 revision "
                "removed reliance on a bare “results not typical” "
                "disclaimer as a cure.",
                evidence=f"“{results.group(0).strip()}”",
                satisfied=bool(_TYPICALITY.search(text)) or None))

    # --- FINRA 2210 --------------------------------------------------------
    if "finra_2210" in by_industry:
        findings.append(_finding(
            "finra_principal_approval", "finra_2210",
            "A retail communication for a broker-dealer",
            "FINRA Rule 2210(b)(1)(A): an appropriately qualified registered "
            "principal must approve a retail communication before the earlier "
            "of its use or its filing with FINRA. 2210(c) requires certain "
            "retail communications to be filed with FINRA's Advertising "
            "Regulation Department, generally within 10 business days of first "
            "use. A broadcast or streaming spot is a retail communication.",
            evidence=f"the client's industry reads “{industry}”"))
        claim = _PERFORMANCE_CLAIM.search(text)
        if claim:
            findings.append(_finding(
                "finra_performance_claim", "finra_2210",
                "The copy predicts or guarantees performance",
                "FINRA Rule 2210(d)(1): communications must be fair and "
                "balanced, may not be false, exaggerated or promissory, and "
                "may not project or predict investment performance.",
                evidence=f"“{claim.group(0).strip()}”",
                satisfied=False))

    # --- Attorney advertising ---------------------------------------------
    if "state_bar" in by_industry:
        findings.append(_finding(
            "state_bar_jurisdiction", "state_bar",
            "An advertisement for legal services",
            "ABA Model Rules 7.1-7.3 as adopted by each state. Which state's "
            "rules govern is the first question and this tool does not answer "
            "it — requirements differ on labeling the ad as attorney "
            "advertising, naming a responsible lawyer and office, disclaimers "
            "on dramatizations, and what may be said about outcomes. Check the "
            "rules of every state the media plan reaches.",
            evidence=f"the client's industry reads “{industry}”"))
        results = _PAST_RESULTS.search(text)
        if results:
            findings.append(_finding(
                "state_bar_past_results", "state_bar",
                "The copy states a past result",
                "Most states require a past result to be accompanied by a "
                "disclaimer that prior results do not guarantee a similar "
                "outcome, and require the claim itself to be substantiated and "
                "not misleading as to what the firm can achieve.",
                evidence=f"“{results.group(0).strip()}”",
                satisfied=bool(_PAST_RESULTS_DISCLAIMER.search(text)) or None))
        claim = _SPECIALIST_CLAIM.search(text)
        if claim:
            findings.append(_finding(
                "state_bar_specialist", "state_bar",
                "The copy claims specialization or superiority",
                "ABA Model Rule 7.1 and its state adoptions: a lawyer may not "
                "state or imply certification as a specialist unless certified "
                "by an approved organization, and comparative or superlative "
                "claims must be capable of substantiation.",
                evidence=f"“{claim.group(0).strip()}”",
                satisfied=False))

    # --- TTB ---------------------------------------------------------------
    if "ttb" in by_industry:
        findings.append(_finding(
            "ttb_mandatory_statements", "ttb",
            "An advertisement for an alcohol beverage",
            "27 CFR Parts 4, 5 and 7: an advertisement must carry the name and "
            "address of the responsible advertiser and identify the class or "
            "type of the product, and must not make false or misleading "
            "statements.",
            evidence=f"the client's industry reads “{industry}”",
            satisfied=bool(_RESPONSIBLE_ADVERTISER.search(text)) or None))
        health = _HEALTH_CLAIM.search(text)
        if health:
            findings.append(_finding(
                "ttb_health_claim", "ttb",
                "The copy makes a health or curative claim",
                "27 CFR Parts 4, 5 and 7 prohibit health-related statements, "
                "and curative or therapeutic claims, in alcohol beverage "
                "advertising.",
                evidence=f"“{health.group(0).strip()}”",
                satisfied=False))

    regimes = sorted({f["regime"] for f in findings})
    return {
        "findings": findings,
        "regimes": regimes,
        "industry_known": industry_known,
        "measured": True,
        "note": ("" if industry_known else
                 "This client has no industry recorded, so the rules that depend "
                 "on it — attorney advertising, broker-dealer communications, "
                 "alcohol — were not checked at all. That is not the same as "
                 "them not applying."),
        "disclaimer": DISCLAIMER,
    }


def findings_key(scan_result) -> str:
    """A stable fingerprint of the findings, so an edit retires the sign-off.

    An acknowledgment is a statement about the copy as it was. Rewriting the
    offer afterwards must not leave somebody's name attached to a spot they
    never read — the rule `modules/ads_builder` applies when a material edit
    supersedes an approved estimate.

    Keyed on the rule ids and the evidence quoted, not on the whole payload:
    rewording a `requires` sentence in this file is our edit, not the client's
    copy changing, and it must not silently invalidate every sign-off on the
    book.
    """
    import hashlib
    findings = (scan_result or {}).get("findings") or []
    seed = "|".join(sorted(f"{f.get('id')}::{f.get('evidence', '')}"
                           for f in findings))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def needs_acknowledgment(scan_result) -> bool:
    """Whether somebody has to say out loud that this was checked.

    Any finding at all. Not a severity ladder: every regime here is one where
    the consequence of getting it wrong lands on the client, and grading them
    would mean this file deciding which law matters less.
    """
    return bool((scan_result or {}).get("findings"))


def summary(scan_result) -> str:
    """One line for a panel header. Never claims a result.

    "Nothing engaged" is a statement about what was scanned; it is not a pass,
    and the wording says so rather than reading as a clearance.
    """
    result = scan_result or {}
    if not result.get("measured", True):
        return result.get("note") or "The compliance scan could not run."
    findings = result.get("findings") or []
    if not findings:
        if not result.get("industry_known"):
            return ("Nothing in the copy engaged a rule — but this client has no "
                    "industry on file, so the industry-based rules were not checked.")
        return "Nothing in the copy or the client's industry engaged one of these rules."
    regimes = ", ".join(REGIMES[r]["label"] for r in result.get("regimes", []))
    return (f"{len(findings)} thing(s) to check before this runs — {regimes}. "
            "Each names the rule and what it requires.")
