"""Fan Radio's half of the builder-parity list: the script panel.

`test_radio_parity.py` asserts the Radio Ad Creator's half — the lengths, their
cost notes, the beat rail and a named QC panel on the copy. Every one of those
landed in that tool and none of them in Fan Radio, which writes the same reads
for the same clients against the same clock. So this file is the other
direction, and each item is asserted where it was missing:

* **The script panel.** Fan Radio had two of the nine checks: a word count
  against the budget and a trademark scan. A :30 that never said the client's
  web address was a named finding one tool over and silence here; a required
  disclaimer that quietly did not make the cut was nothing at all, because
  there was nowhere to type one.
* **Before anybody pays for a voice.** The way to find out a :30 read short was
  to spend the ElevenLabs characters and listen to the dead air —
  `estimate_seconds` lived in `modules/radio_promo/speech.py`, where this tool
  could not reach it.
* **Its own two rows, on the same panel.** The trademark verdict and the
  post-game result rule are Fan Radio's own and are rows rather than a separate
  red banner, because a rep reading nine green rows and a banner reads the
  nine.
* **The beat rail.** The beats reached the prompt and not the screen, so a
  script that had wandered from the plan read exactly like one written to it.

The one rule this file holds above the others is **which findings may refuse a
billed render**, because it is the rule that decides what a rep is stopped for.
The line is *certainty* rather than severity: a registered mark, a missing
disclaimer, an invented price and an address the read never says are facts
about the text. A read estimate is words over a read pace, so it reports
loudly and the render still goes — refusing that would be refusing a correct
read, which is how a panel comes to be switched off, and switching this one
off would cost the trademark check with it.

What this does **not** do is re-assert the checks themselves. They are
`hub/radio_script_qc.py` and both builders read them;
`test_radio_parity.py` already holds their behavior through the Radio Ad
Creator. A second copy of those assertions is a second thing to keep in step,
which is the failure this whole piece of work exists to undo. What is asserted
here is that Fan Radio genuinely reads them, over its own row, and that the
one table is read twice rather than two that agree today.

    python3 test_radio_feature_parity.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1frparity_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)
os.environ["HUB_DATA_DIR"] = DISK
# Both, deliberately. A fresh data directory in front of an inherited
# DATABASE_URL is refilled from the last run's mirror -- the trap
# test_jsonstore.py sweeps for.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "fr-parity-test-secret"
os.environ["PANEL_PASSWORD"] = "fr-parity-test-password"
for _k in ("OPENAI_API_KEY", "ELEVENLABS_API", "ELEVENLABS_API_KEY",
           "CLOUDINARY_URL", "GHL_PRIVATE_TOKEN", "GHL_OPPORTUNITY_WEBHOOK_URL",
           "FAN_RADIO_NOTIFY_URL"):
    os.environ.pop(_k, None)

# The composed app is imported first, deliberately -- `wsgi._mount` installs an
# error handler on every module app, and Flask refuses that on an app that has
# already served a request, so driving a module's own test client before this
# import turns a real failure into a confusing one. The same order
# `test_radio_parity.py` opens with, for the same reason.
import wsgi                                                       # noqa: E402

from werkzeug.test import Client                                  # noqa: E402

from hub import radio_script_qc, radio_spec                      # noqa: E402
from modules.fan_radio import app as fan_app                      # noqa: E402
from modules.fan_radio import catalog as fan_catalog              # noqa: E402
from modules.fan_radio import qc as fan_qc                        # noqa: E402
from modules.fan_radio import store as fan_store                  # noqa: E402
from modules.radio_promo import qc as promo_qc                    # noqa: E402

PASS = FAIL = 0
fan = fan_app.app.test_client()


def section(title):
    print(f"\n{title}\n" + "-" * len(title))


def check(label, got, want):
    global PASS, FAIL
    if got == want:
        PASS += 1
        print(f"  ok    {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


# ---------------------------------------------------------------------------
# A project with all three non-negotiables on it, and a disclaimer.
# ---------------------------------------------------------------------------
PROJECT = fan_store.create({
    "scope": "spec", "company": "Ridgeline Tire, LLC",
    "home_url": "ridgelinetire.com/fall",
    "phone": "(317) 555-0142", "include_phone": True,
    "disclaimer": "Offer ends Sunday. See store for details.",
    "promotion": "$99 fall alignment", "team_context": "Cincinnati Bengals",
    "tone": "warm",
}, "tester")
PID = PROJECT["id"]

# A read that carries everything: the name twice, the whole address, the
# number, the disclaimer word for word, and enough words for a :30.
COMPLETE = (
    "Winter is coming and your tires are the only thing touching the road. "
    "Ridgeline Tire has a ninety nine dollar fall alignment that keeps you "
    "straight and safe through the season. Our people have been doing this "
    "for thirty years and they will tell you honestly what you need. "
    "Visit ridgelinetire dot com slash fall, or call three one seven, "
    "five five five, zero one four two. That is Ridgeline Tire. "
    "Offer ends Sunday. See store for details.")

PROJECT = fan_store.load(PID)
PROJECT["spots"] = [
    {"id": "sp30", "daypart": "gameday", "seconds": 30, "outcome": "neutral",
     "script": COMPLETE, "status": "draft"},
    {"id": "spThin", "daypart": "pregame", "seconds": 30, "outcome": "neutral",
     "script": "Come see us before the game.", "status": "draft"},
    {"id": "spBlank", "daypart": "postgame", "seconds": 60, "outcome": "neutral",
     "script": "", "status": "draft"},
]
fan_store.save(PROJECT)


def panel(spot_id, project=None):
    row = project or fan_store.load(PID)
    spot = [s for s in row["spots"] if s["id"] == spot_id][0]
    return fan_qc.run_spot(row, spot, fan_app.banned_terms(row))


# ===========================================================================
section("Fan Radio reads the shared panel rather than resembling it")
# ===========================================================================
# The point of the port. Two panels that agree today is the bet
# hub/radio_spec.py exists to stop, one table down.
check("the checks are the shared module's, not a second copy",
      [k for k in radio_script_qc.CHECK_LABELS
       if k not in fan_qc.CHECK_LABELS], [])
check("and the Radio Ad Creator reads the same nine",
      sorted(promo_qc.CHECK_LABELS), sorted(radio_script_qc.CHECK_LABELS))
check("so every check the Radio Ad Creator makes, Fan Radio makes",
      [k for k in promo_qc.CHECK_LABELS if k not in fan_qc.CHECK_LABELS], [])

_complete = panel("sp30")
check("every check the panel returns has a label to draw it under",
      sorted(k for k in _complete["checks"] if k not in fan_qc.CHECK_LABELS), [])
check("and no label outlives the check it named",
      sorted(k for k in fan_qc.CHECK_LABELS if k not in _complete["checks"]), [])

# The read pace and its three functions were radio_promo's alone, which is why
# this tool had no answer before a render.
check("the read estimate is one function, not two that agree",
      fan_app.speech.__dict__.get("estimate_seconds") is None
      or radio_spec.estimate_seconds is fan_app.speech.estimate_seconds, True)
check("and the Radio Ad Creator reads the same one",
      __import__("modules.radio_promo.speech", fromlist=["x"]).estimate_seconds
      is radio_spec.estimate_seconds, True)


# ===========================================================================
section("A complete read passes, and a thin one is told exactly what is wrong")
# ===========================================================================
check("a read carrying all three facts passes the content check",
      _complete["checks"]["script_contents"]["level"], radio_script_qc.LEVEL_PASS)
check("the disclaimer is found word for word",
      _complete["checks"]["disclaimer"]["level"], radio_script_qc.LEVEL_PASS)
check("a legal suffix nobody reads aloud is not a missing name",
      _complete["checks"]["brand_mentions"]["level"], radio_script_qc.LEVEL_PASS)
check("a phone written the way it is read is the same number",
      "phone number" in _complete["checks"]["script_contents"]["message"], True)
check("and nothing blocks the record",
      fan_qc.blocking(_complete), [])

_thin = panel("spThin")
check("a read that names nobody and goes nowhere fails the content check",
      _thin["checks"]["script_contents"]["level"], radio_script_qc.LEVEL_FAIL)
check("and says which facts it never says",
      ("the business name" in _thin["checks"]["script_contents"]["message"]
       and "the web address" in _thin["checks"]["script_contents"]["message"]), True)
check("a disclaimer that did not make the cut fails",
      _thin["checks"]["disclaimer"]["level"], radio_script_qc.LEVEL_FAIL)
check("the read estimate is reported on the copy, before any render",
      _thin["checks"]["read_length"]["level"], radio_script_qc.LEVEL_FAIL)


# ===========================================================================
section("Certainty rather than severity decides what may refuse a render")
# ===========================================================================
# The rule that decides what a rep is stopped for. A read estimate is words
# over a read pace; refusing that would be refusing a correct read.
check("the text facts and the trademark may refuse a record",
      sorted(fan_qc.BLOCKS_RENDER),
      sorted(("script_contents", "disclaimer", "invented_claims", "trademark")))
check("the read estimate never does, though it fails loudly",
      "read_length" in fan_qc.BLOCKS_RENDER, False)
check("and it really did fail on the thin read",
      "read_length" in _thin["failed"], True)
check("the word budget never refuses either",
      "word_budget" in fan_qc.BLOCKS_RENDER, False)
check("the shared tuple is read rather than restated",
      list(fan_qc.BLOCKS_RENDER[:3]), list(radio_script_qc.BLOCKS_RENDER))
# The one row this tool adds to the blocking set, and the reason it is not
# `radio_script_qc.blocking`: that reads the shared tuple and would drop it.
check("the shared blocking reader would have dropped the trademark",
      radio_script_qc.blocking({"failed": ["trademark"]}), [])
check("and this tool's own reader keeps it",
      fan_qc.blocking({"failed": ["trademark"]}), ["trademark"])


# ===========================================================================
section("The panel refuses the record, and reaches no provider to do it")
# ===========================================================================
_r = fan.post(f"/api/projects/{PID}/spots/spThin/record", json={})
check("a read the panel refuses never asks for a voice", _r.status_code, 422)
_body = _r.get_json()
check("and the refusal names what is wrong rather than being generic",
      "the web address" in _body["error"], True)
check("the panel travels with the refusal so the page can draw it",
      sorted(_body["stopped"]), ["disclaimer", "script_contents"])
check("with the labels to draw it under",
      "script_contents" in (_body.get("labels") or {}), True)
check("and the whole panel, so the rows that refused can be drawn",
      sorted((_body.get("qc_panel") or {}).get("checks") or {}),
      sorted(fan_qc.CHECK_LABELS))
# A refusal that ships the panel and then discards it is the panel not
# arriving: `api()` carries the body on the error the way `postForm` already
# did, and the record button draws it.
_page_js = fan.get("/").get_data(as_text=True)
check("the builder reads the panel off the refusal rather than dropping it",
      ("err.payload = j" in _page_js
       and ".qc_panel" in _page_js), True)
check("and writes the refusal into the redrawn card, not the detached one",
      '[data-m="1"]' in _page_js.split(".qc_panel")[1][:900], True)
# The order is this tool's own and is written down: the copy is the problem
# that has to be fixed either way, and it costs nothing to find.
check("the copy is judged before the voice is even looked for",
      "Cast a voice" in _body["error"], False)


# ===========================================================================
section("Fan Radio's own two rows are on the same panel")
# ===========================================================================
_marked = fan_store.load(PID)
_marked["spots"][0]["script"] = COMPLETE + " Proud supporter of the Bengals."
fan_store.save(_marked)
_tm = panel("sp30")
check("a registered mark fails its own row",
      _tm["checks"]["trademark"]["level"], radio_script_qc.LEVEL_FAIL)
check("and it refuses the record, because a mark is somebody else's lawyer",
      "trademark" in fan_qc.blocking(_tm), True)
check("the row names the word rather than the rule",
      "Bengals" in _tm["checks"]["trademark"]["message"], True)

# The project's own team is context for the writer and never copy, so every
# word of it is on this project's block list.
check("the project's own team name is blocked too",
      "cincinnati bengals" in fan_app.banned_terms(fan_store.load(PID)), True)

_post = fan_store.load(PID)
_post["spots"][2]["script"] = COMPLETE + " After that big win, come celebrate."
_post["spots"][2]["seconds"] = 30
fan_store.save(_post)
_pg = panel("spBlank")
check("post-game copy that assumes a result is flagged",
      _pg["checks"]["result_neutral"]["level"], radio_script_qc.LEVEL_WARN)
check("but it advises rather than refusing — it is a reading of intent",
      fan_qc.blocking(_pg), [])
check("and an alternate written for a known result is allowed the language",
      fan_qc.run_spot(
          dict(fan_store.load(PID)),
          dict(_post["spots"][2], outcome="win"), [])
      ["checks"]["result_neutral"]["level"], radio_script_qc.LEVEL_PASS)


# ===========================================================================
section("A spot with no read is unmeasured, never a wall of failures")
# ===========================================================================
_blank = fan_qc.run_spot(fan_store.load(PID),
                         {"id": "x", "seconds": 30, "daypart": "gameday",
                          "outcome": "neutral", "script": ""}, [])
check("nothing failed, because there is nothing to read", _blank["failed"], [])
check("every check says so rather than drawing a tick",
      len(_blank["unmeasured"]), len(_blank["checks"]))
check("and the spot is not reported ready", _blank["ready"], False)


# ===========================================================================
section("The panel has a route of its own, and it is not the mix panel")
# ===========================================================================
# Sharing a URL would have made whichever registered second the only one
# anybody could reach.
_sq = fan.get(f"/api/projects/{PID}/script-qc").get_json()
check("the script panel answers on its own path", _sq.get("ok"), True)
check("and it answers for every spot the project holds",
      sorted(_sq["spots"]), sorted(s["id"] for s in fan_store.load(PID)["spots"]))
check("it serves the labels rather than leaving them to the template",
      sorted(_sq["labels"]), sorted(fan_qc.CHECK_LABELS))
check("and says which findings stop a record",
      sorted(_sq["blocks_render"]), sorted(fan_qc.BLOCKS_RENDER))

# And it is reachable through the composed app, not just the module's own
# client. DispatcherMiddleware routes by URL prefix, so a route that answers
# here can still be unreachable once mounted -- the trap CLAUDE.md names, and
# the reason `wsgi` is imported at the top of this file rather than only the
# module.
_mounted = wsgi.application
_composed = Client(_mounted)
_composed.post("/login", data={"password": os.environ["PANEL_PASSWORD"]},
               follow_redirects=True)
check("the panel answers under the mount, not only on the module",
      _composed.get(f"/tools/fan-radio/api/projects/{PID}/script-qc")
      .status_code, 200)
check("and the mix panel still answers beside it under the mount",
      _composed.get(f"/tools/fan-radio/api/projects/{PID}/qc").status_code, 200)

_mq = fan.get(f"/api/projects/{PID}/qc").get_json()
check("the mix panel still answers on its own path", _mq.get("ok"), True)
check("and it is a different reading — the mix, not the script",
      "reports" in _mq and "spots" not in _mq, True)

# It reaches no provider, which is the whole point of running it on the copy.
check("one spot can be asked for on its own",
      list(fan.get(f"/api/projects/{PID}/script-qc?spot=sp30")
           .get_json()["spots"]), ["sp30"])
check("a project that does not exist 404s rather than answering empty",
      fan.get("/api/projects/nope/script-qc").status_code, 404)


# ===========================================================================
section("The beats reach the screen, not only the prompt")
# ===========================================================================
_cat = fan.get("/api/catalog").get_json()
_lengths = {row["seconds"]: row for row in _cat["lengths"]}
check("every length the menu offers carries its beats",
      [s for s, row in _lengths.items() if not row.get("beats")], [])
check("and they are the shared table's, not a second plan",
      [b["label"] for b in _lengths[30]["beats"]],
      [b["label"] for b in radio_spec.structure_for("thirty")])
check("a :10 is planned as one beat and a :60 as four",
      (len(_lengths[10]["beats"]), len(_lengths[60]["beats"])), (1, 4))
check("each beat says where it sits and what it does",
      sorted(_lengths[30]["beats"][0]),
      sorted(["end_pct", "guidance", "label", "start_pct"]))

# The shape is drawn from that, and the length badge reads the same table --
# a local two-entry map is how the :10 and the :60 came to be drawn as bare
# numbers once the shared menu brought them in.
#
# It is a chip beside each heading opening a modal now, rather than a rail in
# the middle of the card: it is reference rather than work, and six copies of
# it pushed the script somebody is actually here to edit further down every
# card. What matters to this check is unchanged and is what it still asserts --
# the beats come from the shared table, not from a map kept here.
_page = fan.get("/").get_data(as_text=True)
check("the builder draws the shape from the shared table",
      ("function beatsFor(" in _page and "len.beats" in _page), True)
check("...as a chip that opens it rather than a rail down every card",
      ('data-a="shape"' in _page and "function openShape(" in _page), True)
check("and draws the script panel", "function scriptQcRows(" in _page, True)
check("and re-asks it wherever the copy changes",
      _page.count("refreshScriptQc()") >= 4, True)
check("the length badge reads the shared table rather than a local map",
      ('{15:":15",30:":30"}' in _page, "function lengthLabel(" in _page),
      (False, True))


# ===========================================================================
section("The prompt asks for what the panel checks")
# ===========================================================================
# Asking the panel's question of the model rather than only of its output. A
# prompt that never mentioned the address produced scripts without it, and the
# check would then have been a wall of red on every first draft.
_musts = fan_app.write_musts(fan_store.load(PID))
check("the non-negotiables are one reading, shared with the panel",
      (_musts["company"], _musts["url"], _musts["phone"]),
      ("Ridgeline Tire, LLC", "ridgelinetire.com/fall", "(317) 555-0142"))
check("and which of them this project owes",
      sorted(_musts["require"]), ["company", "phone", "url"])

_prompt = fan_app.ai._spot_user(
    {"summary": "tires"}, fan_catalog.daypart("gameday"), 30,
    fan_catalog.tone("warm"), "neutral", ["kickoff"], [], "", _musts)
check("the prompt asks for the whole address, not the bare domain",
      "ridgelinetire.com/fall" in _prompt, True)
check("it asks for the disclaimer word for word",
      "word for word" in _prompt and "Offer ends Sunday" in _prompt, True)
check("it asks for the name twice in a :30",
      "twice" in _prompt, True)
check("and the beats are stated from the shared table",
      all(b["label"] in _prompt for b in radio_spec.structure_for("thirty")), True)

# A phone the intake did not ask for is not one the read owes -- and a length
# with no response mechanism is told so rather than left to invent one.
_no_phone = dict(fan_store.load(PID), include_phone=False)
check("a phone the project did not ask for is not required",
      "phone" in fan_app.write_musts(_no_phone)["require"], False)
# A :10 is a sponsorship tag, and the shared table has always said ten seconds
# carries no response. The prompt is narrowed by the same function the panel
# is, so a tag is neither asked for an address nor judged for leaving one out.
_tag_prompt = fan_app.ai._spot_user(
    {"summary": "tires"}, fan_catalog.daypart("gameday"), 10,
    fan_catalog.tone("warm"), "neutral", ["kickoff"], [],
    "", fan_app.write_musts(fan_store.load(PID)))
check("a :10 tag is still asked to name the business",
      "Ridgeline Tire, LLC" in _tag_prompt, True)
check("but is not asked to read an address ten seconds cannot carry",
      "ridgelinetire.com/fall" in _tag_prompt, False)
check("and is told not to read one rather than left to invent one",
      "Do not read" in _tag_prompt, True)
check("while a :30 of the same project is asked for all three",
      fan_app.ai._spot_user(
          {"summary": "tires"}, fan_catalog.daypart("gameday"), 30,
          fan_catalog.tone("warm"), "neutral", ["kickoff"], [],
          "", fan_app.write_musts(fan_store.load(PID))).count("say the") >= 3, True)
# And the panel agrees with the prompt, which is the point of one function.
check("the panel does not judge a :10 for the address either",
      radio_script_qc.run("Ridgeline Tire. Tires done right.", seconds=10,
                          facts=fan_qc.facts_for(fan_store.load(PID)),
                          require=fan_qc.required_for(fan_store.load(PID)))
      ["checks"]["script_contents"]["level"], radio_script_qc.LEVEL_PASS)
check("but a :30 of that same copy is refused for it",
      radio_script_qc.run("Ridgeline Tire. Tires done right.", seconds=30,
                          facts=fan_qc.facts_for(fan_store.load(PID)),
                          require=fan_qc.required_for(fan_store.load(PID)))
      ["checks"]["script_contents"]["level"], radio_script_qc.LEVEL_FAIL)


print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    print("\nthe script panel is one table read by both builders, and what may "
          "refuse a billed render is decided by certainty rather than severity")
sys.exit(1 if FAIL else 0)
