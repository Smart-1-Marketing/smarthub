"""Commercial Builder <-> Radio Promo parity: the four gaps closed here.

The gap list this works from named twenty-four differences between the two
builders. What is asserted here is the first four of its own build order, and
each is asserted in the direction it was missing:

* **One reader for "the copy has to actually say it."** Radio Promo checked
  its scripts literally; the Commercial Builder asked whether a field was set
  on a client record, which is a much easier question with a much happier
  answer. `hub/script_contents.py` is the shared rule now, and the three false
  positives it closes are asserted beside the finding it must keep making.
* **Radio Promo gets a named QC panel**, run on the copy rather than after the
  render, and the line between what may refuse a billed record and what may
  only report is asserted directly — it is *certainty*, not severity.
* **The length menu**, its cost notes and its beat rail: a :10 and a :60 that
  were unbuildable, and the slot keys that were hardcoded in six places.
* **The Commercial Builder reaches Smart 1 Suite**, through the Hub's one
  contact write path rather than a second raw webhook.

Every check that could pass by accident is confirmed against the shape that
was live before it: a matcher is handed the copy that used to be rejected AND
the copy that must still be, because a rule loosened until it stops crying
wolf and then loosened once more is a rule that passes on everything.
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
section("One reader for 'the copy has to actually say it'")
# =====================================================================
from hub import script_contents                                        # noqa: E402

FACTS = {"company": "Acme Plumbing, LLC", "url": "https://acme.com/spring",
         "phone": "(317) 555-0142"}

# The three false positives. Each of these is correct copy that the exact
# substring test this replaces refused, sending the writer round again.
check("a legal suffix is not part of the read",
      script_contents.says_company("Acme Plumbing keeps the heat on.",
                                   "Acme Plumbing, LLC"), True)
check("a phone number is compared on its digits, not its punctuation",
      script_contents.says_phone("Call 317-555-0142 today.", "(317) 555-0142"), True)
check("and on either side of a country code",
      script_contents.says_phone("Call 1-317-555-0142.", "(317) 555-0142"), True)
check("a script written the way it is read still says the address",
      script_contents.says_url("Visit acme dot com slash spring.",
                               "https://acme.com/spring"), True)

# And the findings it must keep making. A rule loosened until it stops crying
# wolf and then loosened once more is a rule that passes on everything.
check("a different business is still a different business",
      script_contents.says_company("Riverside Plumbing is here.", "Acme Plumbing"),
      False)
check("a different phone number is still missing",
      script_contents.says_phone("Call 317-555-9999.", "(317) 555-0142"), False)
check("the bare domain does not satisfy a campaign path",
      script_contents.says_url("Visit acme.com today.", "https://acme.com/spring"),
      False)
check("and a www nobody wrote is not a missing address",
      script_contents.says_url("Visit acme.com.", "https://www.acme.com"), True)

# A fact nobody supplied is not a gap -- the commonest way a check like this
# invents an absence.
result = script_contents.check({"company": "Acme", "url": "", "phone": ""},
                               spoken="Acme is open.")
check("nothing on file is 'not supplied', never a gap",
      (script_contents.gap_labels(result), len(result["not_supplied"])), ([], 2))

# Spoken and shown are taken apart, which is the whole reason one function
# serves both media.
tv = script_contents.check(FACTS, spoken="Spring is here.",
                           shown="Acme Plumbing acme.com/spring (317) 555-0142")
check("a television spot may show what it never says",
      script_contents.gap_labels(tv), [])
check("and the row says which channel carried it",
      sorted({row["where"] for row in tv["carried"]}), ["shown"])
radio = script_contents.check(FACTS, spoken="Spring is here.", shown="")
check("radio has one channel, so nothing shown is nothing carried",
      len(script_contents.gap_labels(radio)), 3)

# No copy at all is a different sentence from copy that omits something.
empty = script_contents.check(FACTS, spoken="", shown="")
check("no copy yet is unmeasured rather than three failures",
      (empty["measured"], empty["gaps"]), (False, []))
check("and a caller assembling a sentence gets nothing to print",
      script_contents.sentence(empty), "")
sentence = script_contents.sentence(radio)
check("a sentence names every gap, not the first",
      [label in sentence for label in script_contents.gap_labels(radio)],
      [True, True, True])

# `require` is how a project that deliberately asked for no phone response
# stops being told it is missing one.
no_phone = script_contents.check(FACTS, spoken="Acme Plumbing, acme.com/spring.",
                                 require=("company", "url"))
check("a fact this project never asked for is not owed",
      script_contents.gap_labels(no_phone), [])


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

# The budget is composed from the table rather than restated in `word_target`,
# so the screen and the prompt cannot quote different numbers.
check("the budget line carries the floor where there is one",
      "25 seconds" in catalog.budget_line("thirty"), True)
check("and says nothing about a floor where there is none",
      catalog.budget_line("fifteen"), "35-42 words")

check("an unknown slot is dropped rather than reaching the prompt",
      catalog.normalize_slots(["thirty", "ninety"]), ["thirty"])
check("nothing ticked falls back to the pair the studio shipped",
      catalog.normalize_slots([]), list(catalog.DEFAULT_SLOTS))
check("and slots come back in catalog order, not the order they were ticked",
      catalog.normalize_slots(["sixty", "ten"]), ["ten", "sixty"])


# =====================================================================
section("The prompt writes whichever lengths were chosen")
# =====================================================================
from modules.radio_promo import ai as radio_ai                         # noqa: E402

rules = radio_ai._slot_rules(["ten", "sixty"])
check("the budgets in the prompt are the table's", ":10 runs 22-28 words" in rules, True)
check("and so is the read floor", "at least 52 seconds" in rules, True)
check("a slot nobody chose is not described",
      ":15" in rules or ":30" in rules, False)
beats = radio_ai._slot_beats(["fifteen"])
check("the beats reach the writer as well as the screen",
      "Hook" in beats and "Offer" in beats and "Call" in beats, True)


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
check("the length picker is on the intake", 'id="slotPicker"' in page, True)
check("every unit is named on it",
      all(d["label"] in page for d in catalog.DURATIONS), True)
check("the :60 warning is drawn where it is picked",
      "twice a :30" in page, True)
check("and the beats reach the browser",
      '"beats"' in page or "beats" in page, True)

made = rc_mount.post("/api/projects", json={
    "company": "Acme", "home_url": "https://acme.com", "slots": ["sixty"]}).get_json()
check("a project can be created as a :60 alone", made["project"]["slots"], ["sixty"])
sixty_id = made["project"]["id"]
check("and a slot it did not choose is refused by the edit route",
      rc_mount.post(f"/api/projects/{sixty_id}/script/edit",
              json={"slot": "fifteen", "script": "x"}).get_json()["error"],
      "Unknown slot.")
check("the panel has a route of its own",
      rc_mount.get(f"/api/projects/{PID}/qc").get_json()["ok"], True)
check("and it answers for every slot the project writes",
      [s["slot"] for s in rc_mount.get(f"/api/projects/{sixty_id}/qc")
       .get_json()["qc"]["slots"]], ["sixty"])


# =====================================================================
section("The Commercial Builder asks whether the spot carries the contact")
# =====================================================================
from modules.commercial_builder.services import qc_service              # noqa: E402

CLIENT = {"name": "Acme Plumbing", "website": "https://acme.com/spring",
          "phone": "317-555-0142", "logo_url": "https://acme.com/logo.png"}
PROJ = {"length_seconds": 30, "title": "Acme Spring",
        "cta": {"website": "https://acme.com/spring", "phone": "317-555-0142",
                "headline": "Book today", "offer": "$99 tune-up"}}

with_card = qc_service._check_cta(
    PROJ, CLIENT, [{"is_cta": False, "narration": "Spring is here."},
                   {"is_cta": True, "narration": ""}])
check("an end card carries the contact", with_card["passed"], True)
check("and the message says it was shown rather than said",
      "shown" in with_card["message"], True)

# The defect this replaces: the old check read the client record, and the
# renderer reads project.cta and the scene list. Deleting the end card left
# the record untouched and the check green.
no_card = qc_service._check_cta(
    PROJ, CLIENT, [{"is_cta": False, "narration": "Spring is here."}])
check("a deleted end card is caught", no_card["passed"], False)
check("and the message says which half is missing",
      "no end card" in no_card["message"].lower(), True)

spoken_only = qc_service._check_cta(
    PROJ, CLIENT, [{"is_cta": False,
                    "narration": "Call 317-555-0142 or visit acme.com today."}])
check("a spot that says it without an end card still passes",
      spoken_only["passed"], True)

nothing = qc_service._check_cta({"length_seconds": 30, "cta": {}},
                                {"name": "Acme"}, [])
check("nothing on file at all is still the finding it was",
      nothing["passed"], False)

bumper = qc_service._check_cta({"length_seconds": 5, "cta": {}}, CLIENT, [])
check("a :05 bumper is judged on brand recall, as it always was",
      bumper["passed"], True)

check("the check is handed the scenes it now reads",
      "scenes" in qc_service.run_qc.__code__.co_varnames, True)


# =====================================================================
section("The finished spot reaches Smart 1 Suite")
# =====================================================================
from modules.commercial_builder.db import db as cb_db                   # noqa: E402
from modules.commercial_builder import teardown as cb_teardown          # noqa: E402
from modules.commercial_builder.models import (                         # noqa: E402
    Client as Client_cls, CommercialProject as Project_cls,
    RenderApproval as Approval_cls, RenderJob as Job_cls,
    SuiteDelivery as Delivery_cls)
from modules.commercial_builder.routes import suite as cb_suite         # noqa: E402
from hub import suite_opportunity                                       # noqa: E402

cb = werkzeug.test.Client(application)
cb.post("/login", data={"password": os.environ["PANEL_PASSWORD"]})
BASE = "/tools/commercial-builder/api/projects"

with hub_app.app_context():
    cb_db.create_all()
    cust = Client_cls(name="Acme Plumbing", slug="acme-parity",
                      website="https://acme.com")
    cb_db.session.add(cust)
    cb_db.session.commit()
    proj = Project_cls(client_id=cust.id, title="Spring Spot", length_seconds=30,
                       platform="ctv", formats=["16:9"])
    cb_db.session.add(proj)
    cb_db.session.commit()
    CB_PID = proj.id
    job = Job_cls(project_id=CB_PID, format="16:9", status="succeeded",
                  output_url="https://provider.example/expires-soon.mp4")
    cb_db.session.add(job)
    cb_db.session.commit()
    CB_JOB = job.id

state = cb.get(f"{BASE}/{CB_PID}/suite").get_json()
check("nothing approved is not something to push", state["can_push"], False)
check("and the count says why", state["approved_cuts"], 0)
check("nobody has pushed it", state["delivery"], None)

pushed = cb.post(f"{BASE}/{CB_PID}/suite", json={})
check("a spot with no approved cut is refused", pushed.status_code, 422)
check("and told what to do about it",
      "Approve a rendered cut first" in pushed.get_json()["error"], True)

with hub_app.app_context():
    cb_db.session.add(Approval_cls(
        render_job_id=CB_JOB, project_id=CB_PID, approved_by="rep@smart1.test",
        stored_url="https://res.cloudinary.com/acme/spring-16x9.mp4",
        filed_to_client=True))
    cb_db.session.commit()

state = cb.get(f"{BASE}/{CB_PID}/suite").get_json()
check("an approved cut is countable", state["approved_cuts"], 1)
check("but an unconfigured Suite is still not a button", state["can_push"], False)
check("and it says which half is missing rather than drawing nothing",
      bool(state["problems"]), True)

# Suite stubbed. What is worth asserting is what the route does with each
# answer, which is the reason none of these reaches GoHighLevel.
_calls = []


def _fake_push(**kwargs):
    _calls.append(kwargs)
    return _fake_push.answer


_fake_push.answer = {"ok": True, "opportunity_id": "opp_1", "created": True,
                     "contact": {"id": "c_1", "name": "Dana Reed"}}
cb_suite.suite_opportunity = type(
    "Stub", (), {"push_proposal": staticmethod(_fake_push),
                 "configured": staticmethod(lambda: True),
                 "status": staticmethod(lambda: {"problems": []})})

state = cb.get(f"{BASE}/{CB_PID}/suite").get_json()
check("a configured Suite with an approved cut is a button", state["can_push"], True)

sent = cb.post(f"{BASE}/{CB_PID}/suite", json={}).get_json()
check("the push reports success", sent["ok"], True)
check("and how many cuts went", sent["cuts"], 1)
check("the opportunity id is kept", sent["delivery"]["opportunity_id"], "opp_1")
check("with the name of whoever pressed it",
      bool(sent["delivery"]["pushed_by"]), True)

note = "\n".join(_calls[-1]["note_lines"])
check("the note names the deliverable rather than a proposal",
      note.startswith("Commercial delivered:"), True)
check("and carries the stored copy, never the provider URL that expires",
      ("res.cloudinary.com" in note, "provider.example" in note), (True, False))
check("the client is named from the record, not from the request",
      _calls[-1]["client"], "Acme Plumbing")

# A second press must revise the opportunity rather than opening another.
cb.post(f"{BASE}/{CB_PID}/suite", json={})
check("a second push revises the opportunity it already opened",
      _calls[-1]["opportunity_id"], "opp_1")
with hub_app.app_context():
    check("and leaves one delivery row, not two",
          Delivery_cls.query.filter_by(project_id=CB_PID).count(), 1)

# A refusal is recorded as a refusal.
_fake_push.answer = {"ok": False, "needs_contact": True,
                     "reason": "No Smart 1 Suite contact matches Acme Plumbing.",
                     "suggest": {"name": "", "email": "", "phone": ""}}
refused = cb.post(f"{BASE}/{CB_PID}/suite", json={})
check("Suite refusing is a 422, not a silent success", refused.status_code, 422)
body = refused.get_json()
check("needs_contact is passed through rather than read as an error",
      body["needs_contact"], True)
with hub_app.app_context():
    row = Delivery_cls.query.filter_by(project_id=CB_PID).first()
    check("the refusal is written down", (row.ok, bool(row.reason)), (False, True))
    check("and the opportunity id it already had is not thrown away",
          row.opportunity_id, "opp_1")

check("the delivery is one of the tables a delete has to account for",
      Delivery_cls in cb_teardown.ORPHANABLE, True)
check("and it is named in the sentence somebody reads",
      "suite_deliveries" in [k for k, _s, _p in cb_teardown.NAMED], True)
with hub_app.app_context():
    check("work_behind counts it",
          cb_teardown.work_behind([CB_PID])["suite_deliveries"], 1)

# The route is on the composed app, behind the login, like the rest of the
# module -- this blueprint is registered on the hub app, so nothing in
# wsgi.py's AuthGuard covers it and the guard on the blueprint is what does.
anon = werkzeug.test.Client(application)
check("a stranger cannot read a client's Suite state",
      anon.get(f"{BASE}/{CB_PID}/suite").status_code in (302, 401, 403), True)
check("nor push one", anon.post(f"{BASE}/{CB_PID}/suite", json={}).status_code
      in (302, 401, 403), True)

# push_proposal's own default is unchanged for the caller it was written for.
check("a proposal still gets a proposal's note",
      "note_lines" in suite_opportunity.push_proposal.__code__.co_varnames, True)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
