"""What is wrong with this read, said as named checks before it is recorded.

The Commercial Builder has had a QC panel since it was written: twenty-odd
checks, each named, each individually pass/warn/fail, run on the screen where
the work is done. Radio Promo had two halves of one and no panel. The content
non-negotiables were a list of strings assembled inside `_required_script_gaps`
and thrown at a 422; the timing verdict was `speech.grade_duration`, which is
excellent and ran **after** the render — so the way to find out a :30 read
short was to spend the ElevenLabs characters and listen to the dead air.

So the checks live here, they run on the copy, and they run before anybody
pays for a voice. Same shape as `services/qc_service.py` deliberately — a
`{"passed", "level", "message"}` per named check, `_all_passed` meaning
*nothing failed* rather than *everything passed*, and `_warnings` beside it —
because a rep moving between the two tools should be reading one idea, and
because the next fix to how a QC row is drawn should land in one stylesheet.

## Advisory is the load-bearing half

`ADVISORY_CHECKS` is what may not refuse a render. A check that refuses
correct copy is a check somebody switches off, and switching this one off
would take the content rules down with it — the note `QR_CODE_RULES` carries
in the Commercial Builder, one medium over. So a brand said once where the
craft rule wants twice is amber and a read with no address in it is red, and
those are different colors on purpose.

## Nothing here re-derives a rule it can read

The content rules are `hub/script_contents.py`, the same reader the Commercial
Builder's CTA check uses. The invented-claim rules are
`hub/social_plan.validate_copy`, which `hub/gpt_ads_spec.py` already imports
for the same job one medium over: a price, a percentage, a deadline or a phone
number in the copy that traces back to nothing a human typed. The budgets and
the read floor are `catalog.DURATIONS`. Three tables, no fourth copy.

## And a check that could not look says so

An empty slot is `not measured`, never a pass and never a failure: "this read
omits the phone number" and "there is no read yet" are different sentences and
only the first is somebody's to fix. `_all_passed` is false while anything is
unmeasured, because a panel that goes green over a script nobody has written
is the confident wrong answer this Hub keeps having to undo.
"""

from __future__ import annotations

import re

from hub import script_contents
from hub.social_plan import validate_copy

from . import speech
from .catalog import duration_by_key, structure_for

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

# Anything that looks like a script somebody forgot to take the labels out of.
# The speech pass strips these defensively before the words reach ElevenLabs,
# which is right and is also why nobody ever saw them: they stay in the
# written script, which is the copy a client reads and approves.
_STAGE_DIRECTION = re.compile(
    r"(?:^|\n)\s*(?:VO|ANNCR|SFX|MUSIC|TAG|NARRATOR)\s*:|\[[^\]]*\]|\((?:SFX|VO|MUSIC)[^)]*\)",
    re.I)


# What may refuse a billed render, and what may only report.
#
# The line is not severity, it is **certainty**. A missing disclaimer, an
# invented price and an address the read never says are facts about the text:
# they cannot be wrong, and every one of them is worse discovered after the
# characters are spent. The timing verdict is an *estimate* — words divided by
# a read pace — so a :30 estimated at 30.5 seconds may well measure 29.8, and
# refusing that render would be refusing a correct read, which is the crying
# wolf that gets a panel switched off. It is reported loudly and the render
# still goes; `speech.grade_duration` then measures the real file and offers
# the tighten, exactly as it did before.
# What each check is called on screen. Served with the panel rather than
# restated in the template, because a check absent from a label map is skipped
# silently by the loop that draws it -- the failure `scene_assets` had in the
# Commercial Builder, where the one check written to catch an unfinished scene
# never appeared on the panel it was written for. `test_radio_parity.py`
# asserts this map covers everything `run_slot()` returns.
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


def blocking(panel: dict) -> list:
    """The fail-level checks that may refuse a render, named."""
    return [key for key in BLOCKS_RENDER if key in (panel or {}).get("failed", [])]


def _unmeasured(message: str) -> dict:
    return {"passed": None, "level": LEVEL_UNKNOWN, "message": message}


def facts_for(row: dict) -> dict:
    """The three things this project's copy has to name.

    One reading, here rather than in app.py as well: `_check_contents` and
    `_check_brand_mentions` both need the business name, and the routes need
    the same dict to refuse a write with. Three descriptions of "which field
    holds the company" is how one of them comes to read `client` and the
    others `company`.
    """
    return {"company": row.get("company") or row.get("client") or "",
            "url": row.get("landing_url") or row.get("home_url") or "",
            "phone": row.get("phone") or ""}


def required_for(row: dict) -> tuple:
    """Which of them this project actually asked for.

    The phone number is only owed where the intake asked for it — a spot
    deliberately built without a phone response is not one missing it.
    """
    keys = ["company", "url"]
    if row.get("include_phone"):
        keys.append("phone")
    return tuple(keys)


def _facts(row: dict) -> dict:
    """What a human actually typed for this project.

    `validate_copy` permits a claim that traces back to one of these and flags
    everything else. The brief the model wrote is deliberately **not** in here:
    it is the model's own reading of the client's website, so treating it as
    authorisation would let one model call authorise the next one's invention.
    """
    return {
        "offers": row.get("promotion") or "",
        "notes": row.get("disclaimer") or "",
        "phone": row.get("phone") or "",
        "url": " ".join(filter(None, [row.get("home_url") or "",
                                      row.get("landing_url") or ""])),
        "must_include": [facts_for(row)["company"]],
    }


def _check_contents(row: dict, script: str, required) -> dict:
    result = script_contents.check(facts_for(row), spoken=script,
                                   require=required)
    if not result["measured"]:
        return _unmeasured("No read written yet.")
    sentence = script_contents.sentence(result)
    if sentence:
        return {"passed": False, "level": LEVEL_FAIL, "message": sentence}
    carried = ", ".join(item["label"] for item in result["carried"])
    return {"passed": True, "level": LEVEL_PASS,
            "message": f"The read says {carried or 'everything it has to'}."}


def _check_read_length(slot_key: str, script: str) -> dict:
    """Long enough to fill the slot, short enough to fit in it.

    `speech.grade_duration` already answers this and already words it well;
    what is new is that it is asked of the *estimate* here, before the render,
    rather than of the measured file afterwards. The estimate is named as an
    estimate, because it is one — the render still measures.
    """
    slot = duration_by_key(slot_key) or {}
    seconds = slot.get("seconds")
    if not script.strip():
        return _unmeasured("No read written yet.")
    estimate = speech.estimate_seconds(script)
    grade = speech.grade_duration(estimate, seconds)
    if grade["status"] == "long":
        return {"passed": False, "level": LEVEL_FAIL,
                "message": f"Estimated {grade['label']} Tighten it before recording.",
                "trim_words": grade.get("trim_words")}
    floor = slot.get("min_seconds")
    if floor and estimate < floor:
        return {"passed": False, "level": LEVEL_FAIL,
                "message": f"Estimated {estimate:.1f}s against a {floor:g}-second "
                           f"floor for a :{seconds}. That is air somebody paid for."}
    if grade["status"] == "short":
        return {"passed": False, "level": LEVEL_WARN,
                "message": f"Estimated {grade['label']}"}
    return {"passed": True, "level": LEVEL_PASS,
            "message": f"Estimated {estimate:.1f}s against a :{seconds}."}


def _check_word_budget(slot_key: str, script: str) -> dict:
    slot = duration_by_key(slot_key) or {}
    if not script.strip():
        return _unmeasured("No read written yet.")
    words = speech.count_words(script)
    low, high = slot.get("low"), slot.get("high")
    if high and words > high:
        return {"passed": False, "level": LEVEL_FAIL,
                "message": f"{words} words against a {slot.get('word_target')} budget."}
    if low and words < low:
        return {"passed": False, "level": LEVEL_WARN,
                "message": f"{words} words against a {slot.get('word_target')} budget — "
                           "there is room left."}
    return {"passed": True, "level": LEVEL_PASS,
            "message": f"{words} words, inside the {slot.get('word_target')} budget."}


def _check_brand_mentions(row: dict, slot_key: str, script: str) -> dict:
    """The craft rule the prompt states and nothing checked.

    Twice in a :30 or longer, once in anything shorter. Advisory: a read that
    names the business once and lands beautifully is not a defect, and a check
    that refuses it teaches people to ignore the panel.
    """
    company = str(facts_for(row)["company"]).strip()
    if not company:
        return _unmeasured("No business name on the project to count.")
    if not script.strip():
        return _unmeasured("No read written yet.")
    seconds = (duration_by_key(slot_key) or {}).get("seconds") or 30
    wanted = 2 if seconds >= 30 else 1
    said = len(re.findall(re.escape(company.split(",")[0].strip()), script, re.I))
    if said >= wanted:
        return {"passed": True, "level": LEVEL_PASS,
                "message": f"Says the name {said} time{'' if said == 1 else 's'}."}
    return {"passed": False, "level": LEVEL_WARN,
            "message": f"Says the name {said} time{'' if said == 1 else 's'}; a "
                       f":{seconds} usually wants {wanted}."}


def _check_disclaimer(row: dict, script: str) -> dict:
    """A disclaimer is reproduced word for word or it is not a disclaimer.

    The prompt asks for it verbatim and nothing read the answer back. This is
    the one check here that is about somebody else's legal obligation, so it
    fails rather than warns: a required disclaimer that quietly did not make
    the cut is the whole reason it was typed in.
    """
    wanted = str(row.get("disclaimer") or "").strip()
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


def _check_invented(row: dict, script: str) -> tuple:
    """A price, a deadline or a phone number nobody supplied.

    hub/social_plan.validate_copy is the reader — the same one the Social
    Planner and the GPT Ads builder use, so a claim ruled out in one tool is
    ruled out in all three. Its own two levels are kept: a price nobody
    supplied blocks, a superlative advises.
    """
    if not script.strip():
        return _unmeasured("No read written yet."), _unmeasured("No read written yet.")
    flags = validate_copy(script, facts=_facts(row))
    blocking = [f for f in flags if f.get("level") == "block"]
    soft = [f for f in flags if f.get("level") != "block"]
    hard_row = ({"passed": False, "level": LEVEL_FAIL,
                 "message": " ".join(f["message"] for f in blocking)}
                if blocking else
                {"passed": True, "level": LEVEL_PASS,
                 "message": "Every price, deadline and number traces back to "
                            "something somebody typed."})
    soft_row = ({"passed": False, "level": LEVEL_WARN,
                 "message": " ".join(f["message"] for f in soft)}
                if soft else
                {"passed": True, "level": LEVEL_PASS,
                 "message": "No unsubstantiated claims."})
    return hard_row, soft_row


def _check_beats(slot_key: str, script: str) -> dict:
    """Roughly the shape the beat rail asks for.

    Deliberately crude and deliberately advisory: this counts sentences
    against beats, which is not what a beat is. What it catches is the read
    that is one long paragraph where the plan wanted three movements, and it
    says so as a suggestion, because a good writer breaking the shape on
    purpose is not something a panel should argue with.
    """
    beats = structure_for(slot_key)
    if not script.strip():
        return _unmeasured("No read written yet.")
    sentences = [s for s in re.split(r"[.!?]+", script) if s.strip()]
    if len(sentences) >= len(beats):
        return {"passed": True, "level": LEVEL_PASS,
                "message": f"Room for all {len(beats)} beat"
                           f"{'' if len(beats) == 1 else 's'}."}
    return {"passed": False, "level": LEVEL_WARN,
            "message": f"{len(sentences)} sentence"
                       f"{'' if len(sentences) == 1 else 's'} against "
                       f"{len(beats)} beats — check the shape against the rail."}


def run_slot(row: dict, slot_key: str, required=None) -> dict:
    """Every check for one read, named."""
    script = str(((row.get("scripts") or {}).get(slot_key) or {}).get("script") or "")
    invented_hard, invented_soft = _check_invented(row, script)
    checks = {
        "script_contents": _check_contents(row, script, required),
        "read_length": _check_read_length(slot_key, script),
        "word_budget": _check_word_budget(slot_key, script),
        "disclaimer": _check_disclaimer(row, script),
        "invented_claims": invented_hard,
        "invented_claims_soft": invented_soft,
        "brand_mentions": _check_brand_mentions(row, slot_key, script),
        "stage_directions": _check_stage_directions(script),
        "beat_coverage": _check_beats(slot_key, script),
    }
    for key, result in checks.items():
        if result["passed"] is False and key in ADVISORY_CHECKS:
            result["level"] = LEVEL_WARN
    failed = [k for k, c in checks.items() if c.get("level") == LEVEL_FAIL]
    unmeasured = [k for k, c in checks.items() if c.get("level") == LEVEL_UNKNOWN]
    return {
        "slot": slot_key,
        "seconds": (duration_by_key(slot_key) or {}).get("seconds"),
        "checks": checks,
        "failed": sorted(failed),
        "warnings": sorted(k for k, c in checks.items() if c.get("level") == LEVEL_WARN),
        "unmeasured": sorted(unmeasured),
        # Nothing failed, and there was something to read. A panel that goes
        # green over a script nobody has written is worse than no panel.
        "ready": not failed and bool(script.strip()),
    }


def run(row: dict, slots, required=None) -> dict:
    """The panel for a whole project, slot by slot."""
    slot_rows = [run_slot(row, key, required) for key in slots]
    return {
        "slots": slot_rows,
        "failed": sorted({k for s in slot_rows for k in s["failed"]}),
        "warnings": sorted({k for s in slot_rows for k in s["warnings"]}),
        "ready": bool(slot_rows) and all(s["ready"] for s in slot_rows),
    }
