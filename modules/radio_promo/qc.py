"""Radio Ad Creator's reading of its own row, over the shared script panel.

The nine checks themselves are `hub/radio_script_qc.py`. They were written
here and moved there when Fan Radio needed them: it writes the same reads for
the same clients against the same clock and had two of the nine, so a :30 that
never said the web address was a finding in one tool and silence in the other.
A second copy of a panel is the bet `hub/radio_spec.py` exists to stop, on a
larger surface -- nine checks, each with a level and a sentence, is nine more
chances for the two builders to disagree about whether a script is ready while
both look right.

What stays here is the part that genuinely differs: **which field on a Radio
Promo project row holds the company, the address, the number and the
disclaimer.** Fan Radio's row has none of those names. That reading is one
function per question, because three descriptions of "which field holds the
company" is how one of them comes to read `client` and the others `company`.

`hub/radio_spec.qc()` is still a different question and still runs beside
this: it judges the **mix** -- the bed's source, the loudness, the length
measured off the stored WAV -- and this judges the **script**, before anybody
has paid for a voice. They are neighbours rather than two readings of one
question, and running both is the point.
"""

from __future__ import annotations

from hub import radio_script_qc

# Re-exported under the names this module has always answered to, so no call
# site and no test had to change when the checks moved. Same arrangement
# `catalog.py` uses over `hub/radio_spec.py`, for the same reason.
LEVEL_FAIL = radio_script_qc.LEVEL_FAIL
LEVEL_WARN = radio_script_qc.LEVEL_WARN
LEVEL_PASS = radio_script_qc.LEVEL_PASS
LEVEL_UNKNOWN = radio_script_qc.LEVEL_UNKNOWN

ADVISORY_CHECKS = radio_script_qc.ADVISORY_CHECKS
CHECK_LABELS = radio_script_qc.CHECK_LABELS
BLOCKS_RENDER = radio_script_qc.BLOCKS_RENDER

blocking = radio_script_qc.blocking


def facts_for(row: dict) -> dict:
    """The three things this project's copy has to name.

    One reading, here rather than in app.py as well: the content check and the
    brand-mention check both need the business name, and the routes need the
    same dict to refuse a write with. Three descriptions of "which field holds
    the company" is how one of them comes to read `client` and the others
    `company`.
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


def claim_facts(row: dict) -> dict:
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


# The name this reading answered to before it moved. Kept because it reads
# better at the `validate_copy` call site than `claim_facts` does, and because
# renaming a private helper is not a fix.
_facts = claim_facts


def run_slot(row: dict, slot_key: str, required=None) -> dict:
    """Every check for one of this project's reads, named."""
    script = str(((row.get("scripts") or {}).get(slot_key) or {}).get("script") or "")
    return radio_script_qc.run(
        script, slot_key=slot_key,
        facts=facts_for(row),
        require=required if required is not None else required_for(row),
        disclaimer=row.get("disclaimer") or "",
        claim_facts=claim_facts(row))


def run(row: dict, slots, required=None) -> dict:
    """The panel for a whole project, slot by slot."""
    slot_rows = [run_slot(row, key, required) for key in slots]
    return dict(radio_script_qc.summarize(slot_rows), slots=slot_rows)
