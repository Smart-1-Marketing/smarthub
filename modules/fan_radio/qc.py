"""Fan Radio's reading of its own row, over the shared script panel.

The nine checks are `hub/radio_script_qc.py` — the panel the Radio Ad Creator
was carrying alone. Fan Radio had two of them: a word count against the budget
and a trademark scan. So a :30 that never said the client's web address was a
named finding in one tool and silence in the other; a required disclaimer that
had quietly not made the cut was nothing here at all, because there was nowhere
to type one; and the way to find out a :30 read short was to spend the
ElevenLabs characters and listen to the dead air.

Two of these checks are **this tool's own** and are on the same panel rather
than beside it, because a rep reading a list of nine green rows and a separate
red banner reads the nine:

* **`trademark`** is `phrases.py` — 125 registered marks plus this project's
  own team name, which is context for the writer and never copy. It blocks,
  and it already blocked at the record button; what is new is that it is named
  on the panel before anybody presses it.
* **`result_neutral`** is the post-game rule. A spot booked to air after the
  final whistle is voiced days earlier and cannot know the score, so copy that
  quietly assumes one is a finding on a neutral post-game script and allowed
  on the two alternates written for a known result.

Both are added through the shared panel's ``extra``, so they count toward
`failed` and `ready` like any other row, and both are named in
`CHECK_LABELS` below — a check absent from the label map is skipped silently
by the loop that draws it, which is the failure `scene_assets` had in the
Commercial Builder.

`hub/radio_spec.qc()` is still a different question and still runs beside
this: it judges the **mix** — the bed's source, the loudness, the length
measured off the stored WAV — and this judges the **script**, before anybody
has paid for a voice.
"""

from __future__ import annotations

from hub import radio_script_qc

from . import phrases

LEVEL_FAIL = radio_script_qc.LEVEL_FAIL
LEVEL_WARN = radio_script_qc.LEVEL_WARN
LEVEL_PASS = radio_script_qc.LEVEL_PASS
LEVEL_UNKNOWN = radio_script_qc.LEVEL_UNKNOWN

# This tool's two rows, added to the shared nine. `radio_script_qc` knows
# nothing about either: it takes them through `extra`, counts their levels
# toward `failed` and `ready`, and never re-reads them. Each row is built below
# already carrying the level it means, which is why there is no second advisory
# set here to drift against the shared one.
TRADEMARK = "trademark"
RESULT_NEUTRAL = "result_neutral"

CHECK_LABELS = dict(radio_script_qc.CHECK_LABELS, **{
    TRADEMARK: "Nobody else's trademark in the copy",
    RESULT_NEUTRAL: "Says nothing about a result it cannot know",
})

# A registered mark is a fact about the copy and it is somebody else's lawyer,
# so the trademark row joins the three text facts that may refuse a billed
# render. It already refused one at the record button; what is new is that it
# is named on the panel before anybody presses it.
BLOCKS_RENDER = radio_script_qc.BLOCKS_RENDER + (TRADEMARK,)


def blocking(panel: dict) -> list:
    """The fail-level checks that may refuse a billed render, named.

    Not `radio_script_qc.blocking`, which reads the shared tuple and would
    drop the trademark row — the one finding here that has always refused a
    record.
    """
    return [key for key in BLOCKS_RENDER if key in (panel or {}).get("failed", [])]


def facts_for(project: dict) -> dict:
    """The three things this project's copy has to name.

    One reading, here rather than in app.py as well. `home_url` is the field
    this tool has always called its address — there is no separate landing
    page, because a Fan Radio spot points at the business rather than at a
    campaign.
    """
    return {"company": project.get("company") or project.get("client") or "",
            "url": project.get("home_url") or "",
            "phone": project.get("phone") or ""}


def required_for(project: dict) -> tuple:
    """Which of them this project actually asked for.

    Same rule as the Radio Ad Creator's, deliberately: the phone number is
    only owed where the intake asked for it, because a spot built with no
    phone response is not one missing it — and a :10 tag cannot carry a
    response somebody acts on at all, which is why the length table says so.
    """
    keys = ["company", "url"]
    if project.get("include_phone"):
        keys.append("phone")
    return tuple(keys)


def claim_facts(project: dict) -> dict:
    """What a human actually typed for this project.

    `validate_copy` permits a claim that traces back to one of these and flags
    everything else. The brief is deliberately **not** in here: it is the
    model's own reading of the client's website, so treating it as
    authorisation would let one model call authorise the next one's invention.
    The team name is not in here either — it is context for the writer, and
    every word of it is on this project's block list.
    """
    return {
        "offers": project.get("promotion") or "",
        "notes": " ".join(filter(None, [project.get("notes") or "",
                                        project.get("disclaimer") or ""])),
        "phone": project.get("phone") or "",
        "url": project.get("home_url") or "",
        "must_include": [facts_for(project)["company"]],
    }


def _unmeasured(message: str) -> dict:
    return {"passed": None, "level": LEVEL_UNKNOWN, "message": message}


def _trademark_row(scan: dict, script: str) -> dict:
    if not str(script or "").strip():
        return _unmeasured("No read written yet.")
    blocked = scan.get("blocked") or []
    if blocked:
        hits = ", ".join(h["term"] for h in blocked)
        return {"passed": False, "level": LEVEL_FAIL,
                "message": f"This script still says: {hits}. That is a "
                           "trademark — fix it before spending a render.",
                "terms": [h["term"] for h in blocked]}
    advisory = scan.get("advisory") or []
    if advisory:
        return {"passed": False, "level": LEVEL_WARN,
                "message": " ".join(f'“{a["term"]}” — {a["why"]}' for a in advisory),
                "terms": [a["term"] for a in advisory]}
    return {"passed": True, "level": LEVEL_PASS,
            "message": "No registered mark and nothing off this project's own "
                       "block list."}


def _result_row(scan: dict, script: str, spot: dict) -> dict:
    """The post-game rule, and it only ever advises.

    It is a reading of intent rather than a fact about the text — a post-game
    script that says "what a season" on purpose is not a defect — so it warns
    where the three text facts fail. The line is certainty rather than
    severity, which is the rule `hub/radio_script_qc.py` states in full.
    """
    if not str(script or "").strip():
        return _unmeasured("No read written yet.")
    if (spot.get("daypart") or "") != "postgame":
        return _unmeasured("Only a post-game spot has a result to assume.")
    if (spot.get("outcome") or "neutral") != "neutral":
        return {"passed": True, "level": LEVEL_PASS,
                "message": "Written for a known result, so the language is "
                           "allowed — the station swaps this one in."}
    hits = scan.get("outcome") or []
    if hits:
        return {"passed": False, "level": LEVEL_WARN,
                "message": " ".join(f'“{h["term"]}” — {h["why"]}' for h in hits),
                "terms": [h["term"] for h in hits]}
    return {"passed": True, "level": LEVEL_PASS,
            "message": "Nothing in the read assumes how the game went."}


def run_spot(project: dict, spot: dict, banned=None, required=None) -> dict:
    """Every check for one spot, named.

    ``banned`` is this project's own block list, which the caller already
    computes for the record gate — passed in rather than recomputed, so the
    panel and the gate cannot disagree about which terms are blocked.

    The two extra rows arrive already carrying the level they mean: the
    trademark row fails on a registered mark and warns on the softer list, and
    the result-neutral row only ever warns. So nothing re-levels them on the
    way through. The shared panel's own fold is a *downgrade* of its own
    advisory keys, neither of which these are, and a second fold here reading
    a second advisory set is exactly the two-tables drift this whole module
    exists to undo.
    """
    script = str(spot.get("script") or "")
    scan = phrases.scan(script, list(banned or []),
                        spot.get("daypart") or "",
                        spot.get("outcome") or "neutral")
    panel = radio_script_qc.run(
        script, seconds=spot.get("seconds"),
        facts=facts_for(project),
        require=required if required is not None else required_for(project),
        disclaimer=project.get("disclaimer") or "",
        claim_facts=claim_facts(project),
        extra={TRADEMARK: _trademark_row(scan, script),
               RESULT_NEUTRAL: _result_row(scan, script, spot)})
    panel["spot"] = spot.get("id")
    panel["daypart"] = spot.get("daypart") or ""
    return panel


def run(project: dict, spots, banned=None, required=None) -> dict:
    """The panel for a whole project, spot by spot."""
    rows = [run_spot(project, spot, banned, required) for spot in spots]
    return dict(radio_script_qc.summarize(rows),
                spots={r["spot"]: r for r in rows})
