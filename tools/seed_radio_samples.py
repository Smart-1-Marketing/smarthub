"""Sample campaigns for the two radio builders.

    python3 tools/seed_radio_samples.py            # create (or refresh) them
    python3 tools/seed_radio_samples.py --remove   # take them away again
    python3 tools/seed_radio_samples.py --list     # what is there now

Both radio tools open on an empty library, so there is nothing to look at
until somebody spends money: Fan Radio's writer is an OpenAI call per spot
and Radio Promo's brief reads the client's website before it writes a word.
That makes the two screens hardest to judge exactly when somebody is deciding
whether to use them. This puts one finished campaign in each, written by hand
to the real word budgets, so both libraries have something in them that costs
nothing and reaches no provider.

Four rules, each a way sample data becomes a liability.

**They are spec projects, attached to no client.** `scope: "spec"` in Fan
Radio and `spec: True` in Radio Promo. That is the one property that matters:
`hub/client_brand.work_log()` files work against whatever client a module
names, so a sample attached to a real client would appear on that client's own
360 record as a radio spot somebody made for them. Nothing here names a
client, so nothing here can reach one.

**Every row is marked.** `sample: True` on the project and `SAMPLE_MARK` in
the notes, which is what `--remove` matches on and what stops the next person
reading a demo as a client's approved script. The business is fictional.

**Nothing is invented that a provider would have measured.** No audio is
attached, because a spot with no MP3 behind it is what an unrendered spot
genuinely looks like, and pointing one at a file that does not exist is worse
than leaving it empty. `ai: False` on every spot with the reason, so no screen
reports these as a model's work.

**It is idempotent.** Re-running replaces the samples rather than adding a
second pair, and it removes by the mark rather than by a stored id, so a
half-finished run does not strand rows nothing can find again.

The scripts are written to each catalog's own budget and then *graded by that
catalog* rather than trusted, so a sample cannot sit in the library carrying
the over-budget warning the tool exists to raise.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SAMPLE_MARK = "[SMART 1 SAMPLE]"

# A business that does not exist. Deliberately not a real client and
# deliberately not a name a real client could be confused with.
COMPANY = "Northgate Tire & Auto"
HOME_URL = "https://example.com/northgate-tire"
PROMOTION = ("Fall alignment and tire check, $59 through the end of "
             "November. Free rotation with any four-tire purchase.")


# --------------------------------------------------------------- fan radio
# Budgets from modules/fan_radio/catalog.py: :15 is 30-38 words, :30 is 65-75.
FAN_SPOTS = [
    ("pregame", 15, "neutral",
     "Big weekend ahead. Before the tailgate, before the drive, get the "
     "truck looked at. Northgate Tire and Auto has your alignment and tire "
     "check for fifty-nine dollars. Beat the Saturday rush. Northgate Tire "
     "and Auto, on Northgate Road."),
    ("pregame", 30, "neutral",
     "You have got the cooler sorted and the chairs in the back. What about "
     "the tires? Northgate Tire and Auto has kept this town rolling for "
     "twenty-two years, and right now an alignment and a full tire check is "
     "fifty-nine dollars. Buy four tires and the rotation is on us. Get it "
     "handled before the weekend, and enjoy your Saturday. Northgate Tire and "
     "Auto, on Northgate Road."),
    ("gameday", 15, "neutral",
     "It is game day and we are open. Northgate Tire and Auto, alignment and "
     "tire check, fifty-nine dollars, right now. In and out before kickoff. "
     "Northgate Tire and Auto, on Northgate Road."),
    ("gameday", 30, "neutral",
     "Game day. You are up early, the grill is already going, and there is a "
     "shimmy in the front end you have been ignoring since August. Northgate "
     "Tire and Auto is open this morning. Alignment and full tire check, "
     "fifty-nine dollars. Four new tires and the rotation is free. We will "
     "have you out well before kickoff. Northgate Tire and Auto, on Northgate "
     "Road, open till two."),
    ("postgame", 15, "neutral",
     "However it went out there, Monday still comes. Northgate Tire and Auto "
     "has your alignment and tire check for fifty-nine dollars, through "
     "November. Northgate Tire and Auto, on Northgate Road. Come see us this "
     "week."),
    ("postgame", 30, "neutral",
     "Whatever happened out there, the drive to work on Monday is the same "
     "drive. Northgate Tire and Auto has an alignment and a full tire check "
     "for fifty-nine dollars, running through the end of November, and a free "
     "rotation with any four tires you buy. Twenty-two years on Northgate "
     "Road, same family, same garage, same people who answer the phone. Come "
     "and see us this week. Northgate Tire and Auto."),
]

FAN_BRIEF = {
    "ai": False,
    "ai_reason": f"{SAMPLE_MARK} written by hand; no model was called.",
    "summary": ("Northgate Tire & Auto is a family-run tire and service "
                "garage that has been on Northgate Road for twenty-two "
                "years. Alignments, tires, brakes and general service for "
                "everyday drivers and work trucks."),
    "audience": ("Local drivers and tradespeople who use their vehicle every "
                 "day and put off maintenance until something goes wrong."),
    "offer": PROMOTION,
    "differentiators": [
        "Twenty-two years at the same address",
        "Family owned and operated",
        "Free rotation with any four-tire purchase",
        "Open Saturday mornings",
    ],
    "callToAction": "Stop in at Northgate Tire and Auto on Northgate Road.",
    "mustSay": ["Northgate Tire and Auto", "fifty-nine dollars"],
    "avoid": [
        "Do not name any team, school, league or broadcaster",
        "Do not imply any sponsorship or official status",
        "Do not promise a repair time we have not agreed",
    ],
    "recommendedTones": [
        {"toneId": "warm", "why": "Family business, long-standing, "
                                  "neighborly rather than hard-sell."},
        {"toneId": "underdog", "why": "Blue-collar audience who value "
                                      "somebody who works for a living."},
        {"toneId": "value", "why": "The price is the hook on this promotion."},
    ],
    "site_note": f"{SAMPLE_MARK} no site was fetched for this sample.",
}


class SampleOutOfBudget(Exception):
    """A sample that would sit in the library carrying the tool's own warning.

    Raised rather than written. The whole value of a sample is that it shows
    the tool working, and one flagged long or short demonstrates the failure
    the grader exists to raise -- while reading, to anybody who did not write
    it, as the tool getting it wrong. Four of the first eight scripts here
    were out of budget, so this is checked rather than eyeballed.
    """


def seed_fan_radio(verbose: bool = True) -> dict:
    from modules.fan_radio import store, catalog, phrases, speech

    project = store.create({
        "scope": "spec",                       # never a client -- see module docstring
        "client": "",
        "company": COMPANY,
        "home_url": HOME_URL,
        "promotion": PROMOTION,
        "tone": "warm",
        "notes": (f"{SAMPLE_MARK} Demonstration campaign. Fictional business, "
                  "no client attached, no audio rendered."),
        "team_context": ("Local garage on the station's own street. Wants to "
                         "be in the football window without sounding like a "
                         "sponsor."),
        "banned": ["official", "sponsor", "partner"],
        "pronunciation": [{"from": "Northgate", "to": "north gate"}],
    }, actor=SAMPLE_MARK)

    project["sample"] = True
    project["brief"] = dict(FAN_BRIEF)

    for daypart, seconds, outcome, script in FAN_SPOTS:
        spot = {
            "id": store.spot_id(),
            "daypart": daypart, "seconds": seconds, "outcome": outcome,
            "tone": "warm", "script": script,
            "hook": script.split(".")[0].strip() + ".",
            "notes": f"{SAMPLE_MARK} hand-written to the {seconds}s budget.",
            "ai": False,
            "ai_reason": f"{SAMPLE_MARK} written by hand; no model was called.",
            "phrases_used": [], "status": "draft", "versions": [],
        }
        # Graded by the module's own catalog rather than trusted -- a sample
        # sitting in the library carrying the over-budget warning would be
        # demonstrating the failure instead of the feature.
        spot["grade"] = catalog.grade(script, seconds)
        if spot["grade"]["state"] != "ok":
            raise SampleOutOfBudget(
                f"fan_radio {daypart} :{seconds} is {spot['grade']['words']} "
                f"words -- {spot['grade']['note']}")
        spot["scan"] = phrases.scan(script, project["banned"], daypart, outcome)
        # A blocked phrase is the one finding this module refuses to deliver
        # on, so a sample carrying one must not reach the library either.
        if spot["scan"].get("blocked"):
            raise SampleOutOfBudget(
                f"fan_radio {daypart} :{seconds} trips the phrase guard: "
                f"{spot['scan']['blocked']}")
        spoken = speech.normalize_for_speech(script, project["pronunciation"])
        spot["spoken"] = spoken["spoken"]
        spot["speech_changes"] = spoken["changes"]
        store.push_version(spot, "sample seed", SAMPLE_MARK)
        store.upsert_spot(project, spot)

    # One approved and one changes-requested, so the library counts and the
    # customer page both have something other than a column of drafts.
    spots = project["spots"]
    spots[0]["status"] = "approved"
    spots[1]["status"] = "changes"
    store.add_feedback(project, {
        "name": "Sample reviewer", "spot_id": spots[1]["id"],
        "action": "changes",
        "comment": ("Can we say 'four tires' rather than 'four new tires'? "
                    "We fit used sets too."),
    })

    project["share"].update({
        "enabled": True,
        "headline": f"{COMPANY} — radio scripts for your review",
        "intro": ("Here are the spots we have written. Approve the ones you "
                  "are happy with, or tell us what to change."),
        "cta_label": "Talk to us", "cta_url": HOME_URL,
    })

    store.sort_spots(project)
    store.save(project)
    if verbose:
        print(f"  fan_radio    {project['id']}  {len(project['spots'])} spots"
              f"  share token {project['share']['token']}")
    return project


# ------------------------------------------------------------- radio promo
# Budgets from modules/radio_promo/catalog.py: :15 is 35-42, :30 is 70-85.
# Deliberately not the same numbers as Fan Radio's -- see the note this
# script prints at the end.
PROMO_FIFTEEN = (
    "That shimmy in the steering wheel? Northgate Tire and Auto will "
    "straighten it out. Alignment and full tire check, fifty-nine dollars. "
    "Offer ends November 30. See store for details. Northgate Tire and Auto, "
    "on Northgate Road.")

PROMO_THIRTY = (
    "You feel it at about fifty. That little pull to the right, the shimmy in "
    "the wheel, the tire that always looks low. You have been meaning to get "
    "it looked at since summer. Northgate Tire and Auto has been on Northgate "
    "Road twenty-two years, same family, same garage. Right now an alignment "
    "and full tire check is fifty-nine dollars, and four tires gets a free "
    "rotation. Offer ends November 30. See store for details. Northgate Tire "
    "and Auto. Come see us this week.")

PROMO_ANALYSIS = {
    "summary": ("Northgate Tire & Auto is a family-owned tire and service "
                "garage on Northgate Road, twenty-two years in the same "
                "location. They sell and fit tires and handle alignments, "
                "brakes and routine service for local drivers and work "
                "vehicles."),
    "audience": ("Drivers in the surrounding few miles who use the vehicle "
                 "daily and have been putting off a job they already know "
                 "about."),
    "offer": PROMOTION,
    "differentiators": [
        "Twenty-two years at the same address",
        "Family owned and operated",
        "Free rotation with any four-tire purchase",
        "Alignment and tire check bundled at one price",
    ],
    "callToAction": "Come in to Northgate Tire and Auto on Northgate Road.",
    "mustSay": ["Northgate Tire and Auto", "fifty-nine dollars"],
    "avoid": [
        "Do not claim a same-day turnaround",
        "Do not name a tire brand we have not agreed",
        "Do not describe the price as a sale or a discount",
    ],
    "recommendedTones": [
        {"toneId": "conversational",
         "why": "The hook is a thing the listener already feels in their own "
                "car; it needs to sound like a person, not an announcer."},
        {"toneId": "warm",
         "why": "Family business trading on twenty-two years of being the "
                "same people at the same address."},
        {"toneId": "urgent",
         "why": "The offer has an end date, which gives a reason to act."},
    ],
    "sources": {
        "home": {"url": HOME_URL, "ok": False,
                 "note": f"{SAMPLE_MARK} no page was fetched for this sample."},
        "landing": {"url": "", "ok": False,
                    "note": f"{SAMPLE_MARK} no landing page on this sample."},
    },
}


def seed_radio_promo(verbose: bool = True) -> dict:
    from modules.radio_promo import store, speech
    from modules.radio_promo.catalog import duration_by_key

    row = store.create({
        "spec": True,                          # never a client -- see docstring
        "client": "",
        "project_name": "Fall alignment promotion",
        "team_member": SAMPLE_MARK,
        "company": COMPANY,
        "home_url": HOME_URL,
        "landing_url": "",
        "promotion": PROMOTION,
        "disclaimer": "Offer ends November 30. See store for details.",
        "pronunciations": [{"from": "Northgate", "to": "north gate"}],
        "brand": {
            "name": COMPANY,
            "industry": "Automotive service and tire retail",
            "location": "Northgate Road",
            "description": ("Family-owned tire and service garage, twenty-two "
                            "years at the same address."),
        },
    })

    prons = row["pronunciations"]

    def decorate(slot_key: str, script: str, notes: str) -> dict:
        """The module's own _decorate, minus the Flask context around it."""
        slot = duration_by_key(slot_key) or {"seconds": 30}
        words = speech.count_words(script)
        spoken = speech.normalize_for_speech(script, prons)
        if not (slot.get("low", 0) <= words <= slot.get("high", 85)):
            raise SampleOutOfBudget(
                f"radio_promo :{slot['seconds']} is {words} words -- the "
                f"budget is {slot.get('low')}-{slot.get('high')}")
        # The module's own writer requires the disclaimer verbatim in BOTH
        # lengths. A sample that skips it in the :15 to fit the budget is
        # demonstrating the corner the prompt exists to close.
        disclaimer = (row.get("disclaimer") or "").strip()
        if disclaimer and disclaimer not in script:
            raise SampleOutOfBudget(
                f"radio_promo :{slot['seconds']} omits the required "
                f"disclaimer")
        return {
            "script": script,
            "word_count": words,
            "estimated_seconds": speech.estimate_seconds(script),
            "target_seconds": slot["seconds"],
            "over_budget": words > slot.get("high", 85),
            "spoken": spoken["spoken"], "changes": spoken["changes"],
            "notes": notes,
        }

    scripts = {
        "hook": "That shimmy in the steering wheel is not going to fix itself.",
        "fifteen": decorate("fifteen", PROMO_FIFTEEN,
                            f"{SAMPLE_MARK} hand-written to the :15 budget."),
        "thirty": decorate("thirty", PROMO_THIRTY,
                           f"{SAMPLE_MARK} hand-written to the :30 budget."),
    }

    store.update(row["id"], {
        "sample": True,
        "analysis": PROMO_ANALYSIS,
        "tone_id": "conversational",
        # Which lengths this sample writes, said rather than left to the
        # default. The tool sells four now -- a :10 tag and a :60 either side
        # of the pair -- and a sample carrying two scripts with no slot list
        # on it is one every reader has to infer the shape of.
        "slots": ["fifteen", "thirty"],
        "scripts": scripts,
        "status": "scripted",
        "voice_want": {"gender": "male", "age": "middle_aged",
                       "accent": "american", "energy": "conversational"},
        # No spots: nothing here has been rendered, and a spot row with no
        # audio behind it is what an unrendered slot genuinely looks like.
        "spots": [],
    })
    store.add_version(row["id"], "draft",
                      {"tone_id": "conversational", "scripts": scripts,
                       "note": f"{SAMPLE_MARK} seeded sample"}, SAMPLE_MARK)

    row = store.get(row["id"])
    if verbose:
        f, t = scripts["fifteen"], scripts["thirty"]
        print(f"  radio_promo  {row['id']}  :15 {f['word_count']}w  "
              f":30 {t['word_count']}w")
    return row


# ----------------------------------------------------------------- removal
def remove(verbose: bool = True) -> tuple[int, int]:
    from modules.fan_radio import store as fan
    from modules.radio_promo import store as promo

    fan_gone = 0
    for summary in list(fan.index()):
        project = fan.load(summary.get("id") or "")
        if project and _is_sample(project):
            if fan.delete(project["id"]):
                fan_gone += 1

    promo_gone = 0
    for project in list(promo.all_projects()):
        if _is_sample(project):
            if promo.delete(project["id"]):
                promo_gone += 1

    if verbose:
        print(f"  removed {fan_gone} Fan Radio and {promo_gone} "
              f"Radio Promo sample(s)")
    return fan_gone, promo_gone


def _is_sample(project: dict) -> bool:
    """Marked, and still nobody's work.

    The mark is checked two ways because the flag is what this script writes
    and the string is what survives a row being edited by hand in the tool.

    **A row with a client on it is never a sample, whatever it is marked.**
    Both modules exist partly to support exactly that promotion -- Fan Radio's
    store says "a spec spot that wins the business becomes that client's first
    spot", and Radio Promo's says a spec project "can be attached to a client
    later without losing anything". The mark survives that adoption, so
    matching on it alone would let ``--remove`` delete a real client's work
    the moment somebody took the sample and ran with it. That is the one
    outcome here that cannot be undone, so the client is checked first and it
    overrules the mark rather than the other way round.
    """
    if _adopted(project):
        return False
    if project.get("sample") is True:
        return True
    for key in ("notes", "team_member", "created_by"):
        if SAMPLE_MARK in str(project.get(key) or ""):
            return True
    return False


def _adopted(project: dict) -> bool:
    """Has this stopped being a spec piece and become somebody's?

    Read from both spellings, because the two modules record it differently:
    Fan Radio moves ``scope`` from "spec" to "client", Radio Promo clears
    ``spec`` and fills ``client``. Either is enough on its own -- a row that
    says it belongs to somebody is treated as theirs even if the other field
    was never updated.
    """
    if str(project.get("client") or "").strip():
        return True
    if project.get("scope") == "client":
        return True
    if "spec" in project and project.get("spec") is False:
        return True
    return False


def listing() -> None:
    from modules.fan_radio import store as fan
    from modules.radio_promo import store as promo

    rows = [r for r in fan.index()
            if _is_sample(fan.load(r.get("id") or "") or {})]
    print(f"Fan Radio: {len(rows)} sample project(s)")
    for r in rows:
        print(f"  {r['id']}  {r.get('company')}  {r.get('spots')} spots  "
              f"{r.get('approved')} approved")

    promos = [r for r in promo.all_projects() if _is_sample(r)]
    print(f"Radio Promo: {len(promos)} sample project(s)")
    for r in promos:
        print(f"  {r['id']}  {r.get('company')}  {r.get('project_name')}  "
              f"{r.get('status')}")


def main(argv: list[str]) -> int:
    if "--list" in argv:
        listing()
        return 0

    if "--remove" in argv:
        print("Removing radio samples")
        remove()
        return 0

    print(f"Seeding radio samples into {os.environ.get('HUB_DATA_DIR') or 'the default data root'}")
    # Replace rather than add, so running this twice does not leave two.
    remove(verbose=False)
    fan = seed_fan_radio()
    promo = seed_radio_promo()

    print()
    print("Both are spec projects with no client attached, so nothing here "
          "reaches a client's 360 record.")
    print(f"  Fan Radio library   /tools/fan-radio/")
    print(f"  approval page       /tools/fan-radio/r/{fan['share']['token']}")
    print(f"  Radio Promo library /tools/radio-promo/")
    print()
    print("Remove them again with:  python3 tools/seed_radio_samples.py --remove")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
