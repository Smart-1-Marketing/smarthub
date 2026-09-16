"""What is wrong with this read, said as named checks before it is recorded.

This is the panel `modules/radio_promo/qc.py` was written as, moved here so the
Hub's **two** radio builders read one copy of it. Fan Radio wrote the same
reads, for the same clients, against the same clock, and had two of these nine
checks: a word budget and a trademark scan. It could not tell you that a :30
read never said the web address, that a required disclaimer had quietly not
made the cut, or that a price in the copy traced back to nothing anybody
typed -- and the way to find out a :30 ran short was to spend the ElevenLabs
characters and listen to the dead air.

That is the shape of defect this module already exists to undo one level down.
`hub/radio_spec.py` holds the lengths because Fan Radio's :15 was 30-38 words
against 35-42 in the Radio Ad Creator, each screen internally consistent, with
Fan Radio's README promising they agreed. A second copy of a *panel* is the
same bet on a larger surface: nine checks, each with a level, a label and a
sentence, is nine more chances for the two tools to disagree about whether a
script is ready while both look right.

## Nothing here is row-shaped, and that is the whole port

The version this came from read a Radio Promo project row: `row["scripts"]
[slot]["script"]`, `row["disclaimer"]`, `row["include_phone"]`. Fan Radio has
no slots and no such row -- a project holds *spots*, keyed by daypart and
length -- so a shared module that took a row would have taken one tool's
shape and left the other converting into it. It takes the copy, the length and
the facts instead. Each builder keeps its own one-line reading of its own row,
which is the part that genuinely differs, and the nine judgements are here.

## Advisory is the load-bearing half

`ADVISORY_CHECKS` is what may not refuse a render. A check that refuses correct
copy is a check somebody switches off, and switching this one off would take
the content rules down with it -- the note `QR_CODE_RULES` carries in the
Commercial Builder, one medium over. So a brand said once where the craft rule
wants twice is amber and a read with no address in it is red, and those are
different colors on purpose.

## What may refuse a billed render, and what may only report

The line is not severity, it is **certainty**. A missing disclaimer, an
invented price and an address the read never says are facts about the text:
they cannot be wrong, and every one of them is worse discovered after the
characters are spent. The timing verdict is an *estimate* -- words divided by
a read pace -- so a :30 estimated at 30.5 seconds may well measure 29.8, and
refusing that render would be refusing a correct read, which is the crying
wolf that gets a panel switched off. It is reported loudly and the render still
goes; the measured file is then graded by `radio_spec.grade_duration()`,
exactly as before.

## Nothing here re-derives a rule it can read

The content rules are `hub/script_contents.py`, the same reader the Commercial
Builder's CTA check uses. The invented-claim rules are
`hub/social_plan.validate_copy`, which `hub/gpt_ads_spec.py` already imports
for the same job one medium over. The budgets, the read floor, the read pace
and the beats are `hub/radio_spec.py`. Four tables, no fifth copy.

## And a check that could not look says so

An empty script is `not measured`, never a pass and never a failure: "this
read omits the phone number" and "there is no read yet" are different
sentences and only the first is somebody's to fix. `ready` is false while
anything is unmeasured, because a panel that goes green over a script nobody
has written is the confident wrong answer this Hub keeps having to undo.
"""

from __future__ import annotations

import re

from hub import radio_spec, script_contents
from hub.social_plan import validate_copy

LEVEL_FAIL = "fail"
LEVEL_WARN = "warn"
LEVEL_PASS = "pass"
LEVEL_UNKNOWN = "unknown"

# Checks that can only ever advise. Each is a craft rule or a house
# preference; none of them is a reason to refuse a read somebody wrote on
# purpose.
ADVISORY_CHECKS = {
    "brand_mentions", "read_length_short", "stage_directions",
    "invented_claims_soft", "beat_coverage",
}

# What each check is called on screen. Served with the panel rather than
# restated in the template, because a check absent from a label map is skipped
# silently by the loop that draws it -- the failure `scene_assets` had in the
# Commercial Builder, where the one check written to catch an unfinished scene
# never appeared on the panel it was written for. `test_radio_parity.py`
# asserts this map covers everything `run()` returns, for both builders.
CHECK_LABELS = {
    "script_contents": "Says the brand, the address and the number",
    "read_length": "Fills its slot without running over",
    "word_budget": "Inside the word budget",
    "disclaimer": "Required disclaimer, word for word",
    "invented_claims": "No price, offer or deadline nobody supplied",
    "invented_claims_soft": "No superlative nobody can stand behind",
    "brand_mentions": "The brand said often enough for the length",
    "stage_directions": "Words to be spoken, and nothing else",
    "beat_coverage": "Built on the beats this length is planned around",
}

BLOCKS_RENDER = ("script_contents", "disclaimer", "invented_claims")

# Anything that looks like a script somebody forgot to take the labels out of.
# The speech pass strips these defensively before the words reach ElevenLabs,
# which is right and is also why nobody ever saw them: they stay in the
# written script, which is the copy a client reads and approves.
_STAGE_DIRECTION = re.compile(
    r"(?:^|\n)\s*(?:VO|ANNCR|SFX|MUSIC|TAG|NARRATOR)\s*:|\[[^\]]*\]|\((?:SFX|VO|MUSIC)[^)]*\)",
    re.I)


def blocking(panel: dict) -> list:
    """The fail-level checks that may refuse a billed render, named."""
    return [key for key in BLOCKS_RENDER if key in (panel or {}).get("failed", [])]


def _unmeasured(message: str) -> dict:
    return {"passed": None, "level": LEVEL_UNKNOWN, "message": message}


def duration_for(*, slot_key: str = "", seconds=None) -> dict:
    """The length row this read is written against, however the caller keys it.

    The Radio Ad Creator keys its lengths by name (`thirty`) and Fan Radio by
    the number of seconds, because each grew that way. Both resolve to the one
    row in `radio_spec.DURATIONS`, and a caller that hands over neither gets an
    empty row rather than the :30 -- a panel that silently grades a :60 against
    a :30 budget is worse than one that declines to grade it.
    """
    row = None
    if slot_key:
        row = radio_spec.duration_by_key(slot_key)
    if row is None and seconds is not None:
        row = radio_spec.duration_by_seconds(seconds)
    return row or {}


# The facts that are a *response* rather than an identity. A length that
# cannot carry one is not a length missing one.
RESPONSE_FACTS = ("url", "phone")


def narrow_require(require, duration: dict) -> tuple:
    """Which non-negotiables this length actually owes.

    A :10 is a sponsorship tag read against a live announcer: the shared table
    has said since it was written that ten seconds "cannot carry a response
    somebody acts on", and nothing read it. So the content check demanded the
    whole web address in a tag, which is a check refusing correct copy — the
    crying wolf that gets a panel switched off, and switching this one off
    would take the disclaimer and the invented-price rules with it.

    The business name is never narrowed away. A tag that names nobody is the
    one thing a tag cannot be.
    """
    require = tuple(require or ())
    if duration.get("carries_response", True):
        return require
    return tuple(k for k in require if k not in RESPONSE_FACTS)


# ------------------------------------------------------------------- checks
def _check_contents(script: str, facts: dict, require) -> dict:
    result = script_contents.check(facts or {}, spoken=script, require=require)
    if not result["measured"]:
        return _unmeasured("No read written yet.")
    sentence = script_contents.sentence(result)
    if sentence:
        return {"passed": False, "level": LEVEL_FAIL, "message": sentence}
    carried = ", ".join(item["label"] for item in result["carried"])
    return {"passed": True, "level": LEVEL_PASS,
            "message": f"The read says {carried or 'everything it has to'}."}


def _check_read_length(script: str, duration: dict) -> dict:
    """Long enough to fill the slot, short enough to fit in it.

    `radio_spec.grade_duration()` already answers this and already words it
    well; what is new is that it is asked of the *estimate* here, before the
    render, rather than of the measured file afterwards. The estimate is named
    as an estimate, because it is one -- the render still measures.
    """
    seconds = duration.get("seconds")
    if not script.strip():
        return _unmeasured("No read written yet.")
    if not seconds:
        return _unmeasured("No word budget is on file for that length.")
    estimate = radio_spec.estimate_seconds(script)
    grade = radio_spec.grade_duration(estimate, seconds)
    if grade["status"] == "long":
        return {"passed": False, "level": LEVEL_FAIL,
                "message": f"Estimated {grade['label']} Tighten it before recording.",
                "trim_words": grade.get("trim_words")}
    floor = duration.get("min_seconds")
    if floor and estimate < floor:
        return {"passed": False, "level": LEVEL_FAIL,
                "message": f"Estimated {estimate:.1f}s against a {floor:g}-second "
                           f"floor for a :{seconds}. That is air somebody paid for."}
    if grade["status"] == "short":
        return {"passed": False, "level": LEVEL_WARN,
                "message": f"Estimated {grade['label']}"}
    return {"passed": True, "level": LEVEL_PASS,
            "message": f"Estimated {estimate:.1f}s against a :{seconds}."}


def _check_word_budget(script: str, duration: dict) -> dict:
    if not script.strip():
        return _unmeasured("No read written yet.")
    if not duration.get("seconds"):
        return _unmeasured("No word budget is on file for that length.")
    words = radio_spec.count_words(script)
    low, high = duration.get("low"), duration.get("high")
    target = duration.get("word_target")
    if high and words > high:
        return {"passed": False, "level": LEVEL_FAIL,
                "message": f"{words} words against a {target} budget."}
    if low and words < low:
        return {"passed": False, "level": LEVEL_WARN,
                "message": f"{words} words against a {target} budget — "
                           "there is room left."}
    return {"passed": True, "level": LEVEL_PASS,
            "message": f"{words} words, inside the {target} budget."}


def _check_brand_mentions(script: str, company: str, duration: dict) -> dict:
    """The craft rule the prompt states and nothing checked.

    Twice in a :30 or longer, once in anything shorter. Advisory: a read that
    names the business once and lands beautifully is not a defect, and a check
    that refuses it teaches people to ignore the panel.
    """
    company = str(company or "").strip()
    if not company:
        return _unmeasured("No business name on the project to count.")
    if not script.strip():
        return _unmeasured("No read written yet.")
    seconds = duration.get("seconds") or 30
    wanted = 2 if seconds >= 30 else 1
    said = len(re.findall(re.escape(company.split(",")[0].strip()), script, re.I))
    if said >= wanted:
        return {"passed": True, "level": LEVEL_PASS,
                "message": f"Says the name {said} time{'' if said == 1 else 's'}."}
    return {"passed": False, "level": LEVEL_WARN,
            "message": f"Says the name {said} time{'' if said == 1 else 's'}; a "
                       f":{seconds} usually wants {wanted}."}


def _check_disclaimer(script: str, disclaimer: str) -> dict:
    """A disclaimer is reproduced word for word or it is not a disclaimer.

    The prompt asks for it verbatim and nothing read the answer back. This is
    the one check here that is about somebody else's legal obligation, so it
    fails rather than warns: a required disclaimer that quietly did not make
    the cut is the whole reason it was typed in.
    """
    wanted = str(disclaimer or "").strip()
    if not wanted:
        return _unmeasured("No disclaimer required on this project.")
    if not script.strip():
        return _unmeasured("No read written yet.")
    squash = lambda t: re.sub(r"[^a-z0-9]+", "", t.lower())          # noqa: E731
    if squash(wanted) in squash(script):
        return {"passed": True, "level": LEVEL_PASS,
                "message": "The disclaimer is in the read word for word."}
    return {"passed": False, "level": LEVEL_FAIL,
            "message": "The required disclaimer is not in this read word for word."}


def _check_stage_directions(script: str) -> dict:
    if not script.strip():
        return _unmeasured("No read written yet.")
    found = _STAGE_DIRECTION.search(script)
    if not found:
        return {"passed": True, "level": LEVEL_PASS,
                "message": "Spoken words only — nothing for the voice to read aloud by mistake."}
    return {"passed": False, "level": LEVEL_WARN,
            "message": f"“{found.group(0).strip()}” reads like a stage direction. "
                       "It is stripped before recording, and the client still "
                       "sees it on the script."}


def _check_invented(script: str, claim_facts: dict) -> tuple:
    """A price, a deadline or a phone number nobody supplied.

    `hub/social_plan.validate_copy` is the reader — the same one the Social
    Planner and the GPT Ads builder use, so a claim ruled out in one tool is
    ruled out in all of them. Its own two levels are kept: a price nobody
    supplied blocks, a superlative advises.
    """
    if not script.strip():
        return _unmeasured("No read written yet."), _unmeasured("No read written yet.")
    flags = validate_copy(script, facts=claim_facts or {})
    hard = [f for f in flags if f.get("level") == "block"]
    soft = [f for f in flags if f.get("level") != "block"]
    hard_row = ({"passed": False, "level": LEVEL_FAIL,
                 "message": " ".join(f["message"] for f in hard)}
                if hard else
                {"passed": True, "level": LEVEL_PASS,
                 "message": "Every price, deadline and number traces back to "
                            "something somebody typed."})
    soft_row = ({"passed": False, "level": LEVEL_WARN,
                 "message": " ".join(f["message"] for f in soft)}
                if soft else
                {"passed": True, "level": LEVEL_PASS,
                 "message": "No unsubstantiated claims."})
    return hard_row, soft_row


def _check_beats(script: str, beats: list) -> dict:
    """Roughly the shape the beat rail asks for.

    Deliberately crude and deliberately advisory: this counts sentences
    against beats, which is not what a beat is. What it catches is the read
    that is one long paragraph where the plan wanted three movements, and it
    says so as a suggestion, because a good writer breaking the shape on
    purpose is not something a panel should argue with.
    """
    beats = list(beats or [])
    if not script.strip():
        return _unmeasured("No read written yet.")
    if not beats:
        return _unmeasured("No beat plan is on file for that length.")
    sentences = [s for s in re.split(r"[.!?]+", script) if s.strip()]
    if len(sentences) >= len(beats):
        return {"passed": True, "level": LEVEL_PASS,
                "message": f"Room for all {len(beats)} beat"
                           f"{'' if len(beats) == 1 else 's'}."}
    return {"passed": False, "level": LEVEL_WARN,
            "message": f"{len(sentences)} sentence"
                       f"{'' if len(sentences) == 1 else 's'} against "
                       f"{len(beats)} beats — check the shape against the rail."}


# --------------------------------------------------------------------- panel
def run(script: str, *, slot_key: str = "", seconds=None, facts: dict | None = None,
        require=None, disclaimer: str = "", claim_facts: dict | None = None,
        extra: dict | None = None) -> dict:
    """Every check for one read, named.

    ``script`` is the written copy — the thing a client reads and approves,
    before the pronunciation pass. ``slot_key`` or ``seconds`` says which
    length it is written against. ``facts`` is what this project actually has
    for `hub/script_contents.py` (``company``, ``url``, ``phone``) and
    ``require`` narrows that set. ``claim_facts`` is what a human typed, for
    `validate_copy`. ``extra`` lets a builder add its own named rows — Fan
    Radio's trademark verdict is its own and belongs on the same panel.
    """
    script = str(script or "")
    duration = duration_for(slot_key=slot_key, seconds=seconds)
    facts = facts or {}
    # Narrowed here rather than by each caller, because which facts a length
    # can carry is a fact about the length and the length table is shared.
    # `require=None` still means "every fact that was supplied", which is
    # `script_contents.check`'s own default and is narrowed the same way.
    require = narrow_require(
        require if require is not None else tuple(facts), duration)
    invented_hard, invented_soft = _check_invented(script, claim_facts)
    checks = {
        "script_contents": _check_contents(script, facts, require),
        "read_length": _check_read_length(script, duration),
        "word_budget": _check_word_budget(script, duration),
        "disclaimer": _check_disclaimer(script, disclaimer),
        "invented_claims": invented_hard,
        "invented_claims_soft": invented_soft,
        "brand_mentions": _check_brand_mentions(script, facts.get("company"), duration),
        "stage_directions": _check_stage_directions(script),
        # Only where the length actually resolved. `structure_for()` answers
        # the :30's beats for a key it does not know, which is the right
        # default for a prompt and the wrong one here: grading a length we
        # could not identify against the :30's plan is the confident wrong
        # answer, and `duration_for()` above declined to guess for the same
        # reason.
        "beat_coverage": _check_beats(
            script, radio_spec.structure_for(duration["key"])
            if duration.get("key") else []),
    }
    # A builder's own rows, added before the levels are read so an extra check
    # counts toward `failed` and `ready` like any other. Nothing here knows
    # what they are; `CHECK_LABELS` is extended beside whatever supplies them.
    for key, row in (extra or {}).items():
        checks[key] = row
    for key, result in checks.items():
        if result.get("passed") is False and key in ADVISORY_CHECKS:
            result["level"] = LEVEL_WARN
    failed = [k for k, c in checks.items() if c.get("level") == LEVEL_FAIL]
    unmeasured = [k for k, c in checks.items() if c.get("level") == LEVEL_UNKNOWN]
    return {
        "slot": slot_key or "",
        "seconds": duration.get("seconds"),
        "checks": checks,
        "failed": sorted(failed),
        "warnings": sorted(k for k, c in checks.items() if c.get("level") == LEVEL_WARN),
        "unmeasured": sorted(unmeasured),
        # Nothing failed, and there was something to read. A panel that goes
        # green over a script nobody has written is worse than no panel.
        "ready": not failed and bool(script.strip()),
    }


def summarize(rows: list) -> dict:
    """One verdict over several reads — a project's worth of panels.

    Both builders write more than one length at a time, so both need this and
    neither should be folding the levels up itself.
    """
    rows = list(rows or [])
    return {
        "failed": sorted({k for r in rows for k in r.get("failed") or []}),
        "warnings": sorted({k for r in rows for k in r.get("warnings") or []}),
        "ready": bool(rows) and all(r.get("ready") for r in rows),
    }
