"""Radio Promo's half of the builder-parity list.

The gap list this works from named twenty-four differences between the two
builders, and its own build order put four of them first. The Commercial
Builder's three shipped in #339 and are asserted in `test_commercial_parity.py`;
this file is the rest, and each is asserted in the direction it was missing:

* **The length menu.** `DURATIONS` held the :15/:30 pair, so the :10
  sponsorship tag and the :60 -- the classic radio unit, the one length with
  room for a story rather than an offer -- were unbuildable.
* **Its cost notes.** ElevenLabs bills the character, so a :60 is about twice
  a :30 to make and twice as much again on every re-record. Only the :60 warns:
  a note on every length is a note nobody reads, and then the one that mattered
  goes past unread too.
* **Its beat rail.** The beats lived in the prompt's own prose, where a rep
  could not see them and a script that had wandered from the plan read exactly
  like one written to it.
* **A named QC panel on the copy**, rather than a list of strings thrown at a
  422 after the render. The line between what may refuse a billed record and
  what may only report is asserted directly, because it is *certainty* rather
  than severity: a missing disclaimer is a fact about the text, and a read
  length is words divided by a pace.

This does **not** re-assert `hub/script_contents.py`. That rule is shared by
both builders and `test_commercial_parity.py` already holds it; a second copy
of those assertions is a second thing to keep in step, which is the failure
this whole piece of work exists to undo.

`hub/radio_spec.qc()` is left alone. It judges the **mix** -- the bed's source,
the loudness, the length measured off the stored WAV -- and this panel judges
the **script**, before anybody has paid for a voice. They are neighbours rather
than two readings of one question, and running both is the point.

    python3 test_radio_parity.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1parity_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)
os.environ["HUB_DATA_DIR"] = DISK
# Both, deliberately. A fresh data directory in front of an inherited
# DATABASE_URL is refilled from the last run's mirror -- the trap
# test_jsonstore.py sweeps for.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "parity-test-secret"
os.environ["PANEL_PASSWORD"] = "parity-test-password"
for _k in ("OPENAI_API_KEY", "ELEVENLABS_API", "ELEVENLABS_API_KEY",
           "CLOUDINARY_URL", "GHL_PRIVATE_TOKEN", "GHL_OPPORTUNITY_WEBHOOK_URL"):
    os.environ.pop(_k, None)

# The composed app is imported first, deliberately. `wsgi._mount` installs an
# error handler on every module app, and Flask refuses that on an app that has
# already served a request -- so driving a module's own test client before this
# import breaks the mount rather than the test. Everything below then goes
# through the app as it is actually served.
import werkzeug.test                                                    # noqa: E402
from wsgi import application, hub_app                                   # noqa: E402

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")



# =====================================================================
section("Radio Promo sells the two units either side of the pair")
# =====================================================================
from modules.radio_promo import catalog                                # noqa: E402

check("the :10 sponsorship tag and the :60 are buildable",
      [d["seconds"] for d in catalog.DURATIONS], [10, 15, 30, 60])
check("every slot carries a word budget",
      [k for k in catalog.SLOT_KEYS
       if not (catalog.duration_by_key(k) or {}).get("word_target")], [])
check("and a note saying what it is for",
      [k for k in catalog.SLOT_KEYS
       if not (catalog.duration_by_key(k) or {}).get("note")], [])
check("and what it costs, because ElevenLabs bills the character",
      [k for k in catalog.SLOT_KEYS
       if not (catalog.duration_by_key(k) or {}).get("cost")], [])

# A warning on every length is a warning nobody reads, so only the one with
# money behind it carries one.
check("only the :60 warns", [k for k in catalog.SLOT_KEYS
                             if catalog.length_warning(k)], ["sixty"])
check("and the warning is about what it costs to make",
      "character" in catalog.length_warning("sixty"), True)

# The read floor is on the long slots only: a :10 or a :15 is a tag and is
# naturally tight, and a floor there would refuse correct copy.
check("only the slots sold by the second carry a read floor",
      [k for k in catalog.SLOT_KEYS
       if (catalog.duration_by_key(k) or {}).get("min_seconds")],
      ["thirty", "sixty"])
check("and the :30's floor is exactly the rule it replaces",
      catalog.duration_by_key("thirty")["min_seconds"], 25)

check("every slot has beats to build on",
      [k for k in catalog.SLOT_KEYS if not catalog.structure_for(k)], [])
check("a :10 is one beat and a :60 is four",
      (len(catalog.structure_for("ten")), len(catalog.structure_for("sixty"))),
      (1, 4))
check("and every beat says where it sits and what it does",
      [b["label"] for slot in catalog.SLOT_KEYS for b in catalog.structure_for(slot)
       if not (b.get("guidance") and b.get("end_pct"))], [])

# `min_seconds` is the ONE floor: the prompt states it and `qc.py` refuses
# against it. Before this the prompt derived its own from `low`, so the number
# the writer was given and the number the checker used could differ -- and did,
# by a second, on the :60.
check("the prompt states the floor where there is one",
      "at least 25 seconds" in catalog.budget_line(), True)
check("and the :60's, which is the one the checker uses",
      f"at least {catalog.duration_by_key('sixty')['min_seconds']:g} seconds"
      in catalog.budget_line(), True)
check("and says nothing about a floor on a tag, which has none",
      "a :15 runs 35-42 words;" in catalog.budget_line(), True)
check("the per-slot line is its own reader, for the screen",
      catalog.slot_budget_line("fifteen"), "35-42 words")

# One rule for which lengths a project writes, two entry points onto it: a row
# and a bare list. A second reading of that question is how the store and the
# writer come to disagree about what is being written.
check("an unknown slot is dropped rather than reaching the prompt",
      list(catalog.normalize_slots(["thirty", "ninety"])), ["thirty"])
check("nothing ticked falls back to the pair the studio shipped",
      list(catalog.normalize_slots([])), list(catalog.DEFAULT_SLOTS))
check("and slots come back in catalog order, not the order they were ticked",
      list(catalog.normalize_slots(["sixty", "ten"])), ["ten", "sixty"])
check("slots_of() is the row-shaped way of asking the same rule",
      list(catalog.slots_of({"slots": ["sixty", "ten"]})), ["ten", "sixty"])
check("and a row saved before the menu widened still writes the pair",
      list(catalog.slots_of({})), list(catalog.DEFAULT_SLOTS))


# =====================================================================
section("The prompt writes whichever lengths were chosen")
# =====================================================================
from modules.radio_promo import ai as radio_ai                         # noqa: E402

_LEN = [catalog.duration_by_key(k) for k in ("ten", "sixty")]

# The JSON shape is built from the slots in play, so the model is never asked
# for a length nobody wants nor left to guess at one.
schema = radio_ai._slot_schema(_LEN)
check("the model is asked for exactly the lengths chosen",
      '"ten"' in schema and '"sixty"' in schema, True)
check("and not for one nobody chose",
      '"fifteen"' in schema or '"thirty"' in schema, False)

# The budgets the writer is held to are the catalog's, stated once.
check("the budgets in the prompt are the table's",
      "a :10 runs 22-28 words" in catalog.budget_line(), True)

# The beat rail reaches the writer, not just the screen. Before this the shape
# of a read lived only in the prompt's own prose, so a script that had wandered
# from the plan read exactly like one written to it.
beats = radio_ai._slot_beats([catalog.duration_by_key("fifteen")])
check("the beats reach the writer as well as the screen",
      "Hook" in beats and "Offer" in beats and "Call" in beats, True)
check("and they are the same table the screen draws",
      all(b["label"] in beats for b in catalog.structure_for("fifteen")), True)

# A truncated response arrives as an empty text body, which every caller reads
# as its own kind of nothing rather than as the ceiling it is -- so the room
# scales with the words asked for.
check("a longer set is given more room than the pair",
      radio_ai._script_tokens([catalog.duration_by_key(k)
                               for k in ("fifteen", "thirty", "sixty")])
      > radio_ai._script_tokens([catalog.duration_by_key(k)
                                 for k in ("fifteen", "thirty")]), True)


# =====================================================================
section("The QC panel runs on the copy, before anybody pays for a voice")
# =====================================================================
from modules.radio_promo import qc as radio_qc, store as radio_store    # noqa: E402

project = radio_store.create({
    "company": "Acme Plumbing, LLC", "home_url": "https://acme.com",
    "include_phone": True, "phone": "(317) 555-0142",
    "disclaimer": "Offer ends October 31.",
    "promotion": "$99 tune-up, offer ends October 31", "slots": ["thirty"]})
PID = project["id"]
check("the chosen slots are recorded on the project", project["slots"], ["thirty"])

# 72 words, which estimates at 27.7 seconds: inside the 65-85 budget and
# clear of the 25-second floor a :30 is sold against.
GOOD = (
    "The heat goes out on the coldest night of the year, and the house is "
    "cold before anybody is awake to notice it. Acme Plumbing has somebody on "
    "the road before you have finished the phone call, and the ninety-nine "
    "dollar tune-up is what keeps that night from happening at all. Twenty "
    "years of neighbors have called them first. Offer ends October 31. Call "
    "317-555-0142 or go to acme.com. That is Acme Plumbing, on the road now.")
radio_store.update(PID, {"scripts": {"thirty": {"script": GOOD}}})
panel = radio_qc.run_slot(radio_store.get(PID), "thirty",
                          required=radio_qc.required_for(radio_store.get(PID)))
check("a good read passes every check", panel["failed"], [])
check("and is ready to record", panel["ready"], True)
check("the address is found even though the read says the domain only",
      panel["checks"]["script_contents"]["passed"], True)
check("the disclaimer is checked word for word",
      panel["checks"]["disclaimer"]["passed"], True)

# Each failure, one at a time, so a passing panel cannot be the reason.
def panel_for(script, **row_changes):
    radio_store.update(PID, dict({"scripts": {"thirty": {"script": script}}},
                                 **row_changes))
    return radio_qc.run_slot(radio_store.get(PID), "thirty",
                             required=radio_qc.required_for(radio_store.get(PID)))

no_url = panel_for(GOOD.replace("go to acme.com", "go online"))
check("a read that never says the address fails",
      "script_contents" in no_url["failed"], True)
check("and that refusal reaches the record button",
      radio_qc.blocking(no_url), ["script_contents"])

no_disclaimer = panel_for(GOOD.replace("Offer ends October 31. ", ""))
check("a required disclaimer left out fails",
      "disclaimer" in no_disclaimer["failed"], True)

invented = panel_for(GOOD.replace("ninety-nine dollar", "$49 half-price"))
check("a price nobody supplied fails",
      "invented_claims" in invented["failed"], True)
check("and it refuses the record, because a wrong fact is money spent twice",
      "invented_claims" in radio_qc.blocking(invented), True)

stagey = panel_for("ANNCR: " + GOOD)
check("a stage direction warns rather than failing",
      (radio_qc.LEVEL_WARN, "stage_directions" in stagey["failed"]),
      (stagey["checks"]["stage_directions"]["level"], False))

thin = panel_for("Acme Plumbing. Offer ends October 31. "
                 "Call 317-555-0142 or go to acme.com.")
check("a read well under the floor is reported",
      "read_length" in thin["failed"], True)
# The line is certainty, not severity: the timing verdict is words divided by
# a read pace, so a :30 estimated at 30.5s may measure 29.8, and refusing that
# render would be refusing a correct read.
check("but the estimate never refuses the record",
      radio_qc.blocking(thin), [])
check("because only text facts may block",
      sorted(radio_qc.BLOCKS_RENDER),
      ["disclaimer", "invented_claims", "script_contents"])

blank = radio_qc.run_slot(radio_store.get(PID), "sixty",
                          required=radio_qc.required_for(radio_store.get(PID)))
check("a slot with no read is unmeasured, never a wall of failures",
      (blank["failed"], blank["ready"]), ([], False))
check("and every check says so rather than drawing a tick",
      sorted({c["level"] for c in blank["checks"].values()}), ["unknown"])

# A brand named once in a :30 is a craft note, not a refusal.
once = panel_for(GOOD.replace("Acme Plumbing, on the road now.", "On the road now."))
check("a brand said once in a :30 advises rather than refusing",
      once["checks"]["brand_mentions"]["level"], radio_qc.LEVEL_WARN)


# =====================================================================
section("Radio Promo's own routes read the project's slots")
# =====================================================================
RADIO = "/tools/radio-promo"
rc = werkzeug.test.Client(application)
rc.post("/login", data={"password": os.environ["PANEL_PASSWORD"]})


class _Mounted:
    """The module through its own mount, so the paths under test are the
    paths a browser actually asks for."""

    def get(self, path):
        return rc.get(RADIO + path)

    def post(self, path, json=None):
        return rc.post(RADIO + path, json=json or {})


rc_mount = _Mounted()
page = rc_mount.get("/").data.decode()
check("the length picker is on the intake", 'id="slotPicks"' in page, True)
check("every unit is named on it",
      all(d["label"] in page for d in catalog.DURATIONS), True)
check("the :60 warning is drawn where it is picked",
      "twice a :30" in page, True)
check("and the beats reach the browser",
      '"beats"' in page or "beats" in page, True)

made = rc_mount.post("/api/projects", json={
    "company": "Acme", "home_url": "https://acme.com", "slots": ["sixty"]}).get_json()
# The store normalises on the way in rather than trusting its caller. The
# merge that brought the two builders' slot work together left this key
# written twice and the second one -- which only listed what it was handed --
# silently won, so a slot the catalog cannot describe reached the store in
# whatever order it had been ticked. A dict cannot hold one key twice, and
# nothing errored: the normalising half was simply never the value.
# Asserted against the STORE, not the route. The route sanitises too, so a
# request cannot show this -- which is exactly why the dead normalisation sat
# there passing every test until a duplicate-key check read the literal.
_dirty = radio_store.create({"company": "Acme", "home_url": "https://acme.com",
                             "slots": ["ninety", "sixty", "ten"]})
check("a slot the catalog cannot describe never reaches the store",
      _dirty["slots"], ["ten", "sixty"])
check("and a project that asked for none is the pair",
      radio_store.create({"company": "Acme"})["slots"], ["fifteen", "thirty"])

check("a project can be created as a :60 alone", made["project"]["slots"], ["sixty"])
sixty_id = made["project"]["id"]
# A slot the catalog cannot describe is refused; one the project simply did
# not tick is NOT, and that is deliberate rather than an oversight. The routes
# guard on `SLOT_KEYS` because a project's slot list can be edited after a
# script exists, and refusing on it would strand the read already written
# against a length somebody has since unticked.
check("a slot the catalog cannot describe is refused by the edit route",
      rc_mount.post(f"/api/projects/{sixty_id}/script/edit",
              json={"slot": "ninety", "script": "x"}).get_json()["error"],
      "Unknown slot.")
# A check missing from the label map is skipped silently by the loop that
# draws it -- the failure `scene_assets` had in the Commercial Builder, where
# the one check written to catch an unfinished scene never appeared on the
# panel it was written for. Asserted over what run_slot() actually returns
# rather than over a list somebody kept in step by hand.
_row = radio_qc.run_slot(radio_store.get(PID), "thirty",
                         radio_qc.required_for(radio_store.get(PID)))
check("every check the panel returns has a label to draw it under",
      sorted(k for k in _row["checks"] if k not in radio_qc.CHECK_LABELS), [])
check("and no label outlives the check it named",
      sorted(k for k in radio_qc.CHECK_LABELS if k not in _row["checks"]), [])
check("the checks that may refuse a record are the text facts",
      list(radio_qc.BLOCKS_RENDER),
      ["script_contents", "disclaimer", "invented_claims"])

check("the panel has a route of its own",
      rc_mount.get(f"/api/projects/{PID}/script-qc").get_json()["ok"], True)
check("and it answers for every slot the project writes",
      [s["slot"] for s in rc_mount.get(f"/api/projects/{sixty_id}/script-qc")
       .get_json()["qc"]["slots"]], ["sixty"])




print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
