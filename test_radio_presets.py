"""The reusable-read library, offered by both radio builders and saved once.

This existed as `modules/fan_radio/script_presets.py`: four default reads, a
custom library on the disk, a route that served both, a test that drove the
route -- and **no screen in either tool**. `grep -i preset` over both builders'
templates returned nothing. So the feature was unreachable in the tool that had
it and absent from the tool that did not, which is the failure CLAUDE.md names
as "a tool with no tile is invisible -- six were, for weeks", wearing an API.

A test that drives a route proves the route answers. It cannot notice that
nobody can press it, which is why this file asserts the screen as well as the
store.

What is asserted, and why each one is here rather than assumed:

* **One library, both tools.** `hub/radio_presets.py` is the store and both
  builders read it, so a read saved in one is offered in the other. Porting the
  old module into the Radio Ad Creator instead would have been a second copy of
  a library, which is the drift `hub/radio_spec.py` exists to stop one table
  down -- and a rep who saved "our standard closing line" in one tool and could
  not find it in the other would be the symptom.

* **`{business}` is filled in, and put back on the way in.** The four shipped
  defaults are written with a placeholder and nothing ever substituted it, so
  every one of them would have gone to air saying "Welcome to {business}".
  The save path is the harder half: a read saved off a live project carries
  that client's name, and reusing it on the next project would read out
  somebody else's business. `generalize()` puts the name back to the
  placeholder, so the library stays reusable after its first save.

* **Presets saved before the move are still offered.** Writes go to the shared
  store; reads are the union of that and the legacy `fan_radio` file. Nothing
  is migrated, because a rewrite-on-read races the second gunicorn worker. A
  preset somebody saved last month has to survive this change, and the only way
  to know it does is to put one there and look.

* **A brace in somebody's prose does not take the library down.** `str.format`
  would raise on "Ask for {the usual}" and the whole picker would fail to draw.

    python3 test_radio_presets.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1presets_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)
os.environ["HUB_DATA_DIR"] = DISK
# Both, deliberately -- a fresh data directory in front of an inherited
# DATABASE_URL is refilled from the last run's mirror, the trap
# test_jsonstore.py sweeps for.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "presets-test-secret"
os.environ["PANEL_PASSWORD"] = "presets-test-password"
for _k in ("OPENAI_API_KEY", "ELEVENLABS_API", "ELEVENLABS_API_KEY",
           "CLOUDINARY_URL", "GHL_PRIVATE_TOKEN", "GHL_OPPORTUNITY_WEBHOOK_URL"):
    os.environ.pop(_k, None)

# The composed app first -- `wsgi._mount` installs an error handler on every
# module app and Flask refuses that on an app that has already served a
# request. It is also what this file drives: a route is not reachable just
# because the module registered it.
import wsgi                                                       # noqa: E402

from werkzeug.test import Client                                  # noqa: E402

from hub import jsonstore, radio_presets                          # noqa: E402
from modules.fan_radio import script_presets as fan_presets       # noqa: E402
from modules.fan_radio import store as fan_store                  # noqa: E402
from modules.radio_promo import store as promo_store              # noqa: E402

PASS = FAIL = 0


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


client = Client(wsgi.application)
client.post("/login", data={"password": os.environ["PANEL_PASSWORD"]},
            follow_redirects=True)

FAN = fan_store.create({"company": "Ridgeline Tire", "home_url": "ridgelinetire.com"},
                       "tester")
PROMO = promo_store.create({"client": "Boehm Heating", "company": "Boehm Heating",
                            "home_url": "boehm.com"})
FR = "/tools/fan-radio/api/script-presets"
RP = "/tools/radio-promo/api/script-presets"


def get(path, project=""):
    return client.get(f"{path}?project={project}").get_json()


# ===========================================================================
section("One library, and both builders offer it")
# ===========================================================================
_fan = get(FR, FAN["id"])
_promo = get(RP, PROMO["id"])
check("Fan Radio serves the library", _fan.get("ok"), True)
check("and the Radio Ad Creator serves it too -- it had none", _promo.get("ok"), True)
check("both offer the same defaults",
      [r["id"] for r in _fan["defaults"]], [r["id"] for r in _promo["defaults"]])
check("which are the shared module's, not a copy",
      [r["id"] for r in _fan["defaults"]],
      [r["id"] for r in radio_presets.DEFAULTS])
check("Fan Radio's own module is a re-export, not a second implementation",
      (fan_presets.save is radio_presets.save,
       fan_presets.library is radio_presets.library), (True, True))


# ===========================================================================
section("The placeholder is filled in, so a default is speakable")
# ===========================================================================
# Every one of the four shipped reads would have gone to air saying
# "Welcome to {business}".
check("a default arrives with this client's name in it",
      _fan["defaults"][0]["script"].startswith("Welcome to Ridgeline Tire."), True)
check("and the other tool's with its own client's",
      _promo["defaults"][0]["script"].startswith("Welcome to Boehm Heating."), True)
check("the saved template is served beside it, still unfilled",
      radio_presets.PLACEHOLDER in _fan["defaults"][0]["template"], True)
check("a library asked for with no project keeps the placeholder standing",
      radio_presets.PLACEHOLDER in get(FR)["defaults"][0]["script"], True)
# "Welcome to ." reads like a defect somebody has to diagnose; the placeholder
# reads like a field nobody filled in, which is what it is.
check("rather than reading as an empty gap",
      "Welcome to ." in get(FR)["defaults"][0]["script"], False)


# ===========================================================================
section("A read saved in one tool is offered in the other")
# ===========================================================================
_saved = client.post(f"{FR}?project={FAN['id']}",
                     json={"name": "Closing line",
                           "script": "That is Ridgeline Tire. We will see you soon."})
check("saving answers ok", _saved.status_code, 200)
_rows = [r for r in get(RP, PROMO["id"])["custom"] if r["name"] == "Closing line"]
check("and it reaches the other builder", len(_rows), 1)
# The half that matters: the library stays reusable after its first save.
check("this client's name was put back to the placeholder on the way in",
      _rows[0]["template"], "That is {business}. We will see you soon.")
check("so it reads out the *other* client, not the one who saved it",
      _rows[0]["script"], "That is Boehm Heating. We will see you soon.")

_back = client.post(f"{RP}?project={PROMO['id']}",
                    json={"name": "Boehm opener",
                          "script": "Boehm Heating keeps your house warm."})
check("and the other direction saves too", _back.status_code, 200)
_rows2 = [r for r in get(FR, FAN["id"])["custom"] if r["name"] == "Boehm opener"]
check("reaching Fan Radio, filled for its own client",
      [r["script"] for r in _rows2], ["Ridgeline Tire keeps your house warm."])
check("the save response carries the fresh library, so the picker redraws once",
      "custom" in _back.get_json(), True)


# ===========================================================================
section("A preset saved before the move is still offered")
# ===========================================================================
# Writes go to the shared store; reads are the union with the legacy file.
# Nothing is migrated -- a rewrite on read races the second worker.
_legacy = os.path.join(jsonstore.data_dir("fan_radio"), "script_presets.json")
jsonstore.write_json(_legacy, [{"id": "old-1", "name": "Saved before the move",
                                "script": "Old read for {business}."}])
_names = [r["name"] for r in get(FR, FAN["id"])["custom"]]
check("the legacy file is still read", "Saved before the move" in _names, True)
check("and it is offered in the tool that never had the file",
      "Saved before the move" in
      [r["name"] for r in get(RP, PROMO["id"])["custom"]], True)
check("filled in like any other",
      [r["script"] for r in get(FR, FAN["id"])["custom"] if r["id"] == "old-1"],
      ["Old read for Ridgeline Tire."])
check("and it is not rewritten out from under itself",
      [r["id"] for r in jsonstore.read_json(_legacy, default=[])], ["old-1"])
# A row in both files is offered once, not twice.
check("nothing is listed twice",
      len([r for r in get(FR, FAN["id"])["custom"] if r["id"] == "old-1"]), 1)


# ===========================================================================
section("Somebody's prose does not take the picker down")
# ===========================================================================
# `str.format` would raise on this and the whole library would fail to draw.
check("a brace in a saved read survives being filled",
      radio_presets.fill("Ask for {the usual} at {business}.", "Acme"),
      "Ask for {the usual} at Acme.")
check("and generalizing is case-insensitive, because a record and a script "
      "spell a name differently",
      radio_presets.generalize("Visit ACME TIRE today.", "Acme Tire"),
      "Visit {business} today.")
check("a project with no name on it generalizes to itself rather than emptying",
      radio_presets.generalize("Visit Acme Tire.", ""), "Visit Acme Tire.")


# ===========================================================================
section("Both tools refuse the same copy, and say so")
# ===========================================================================
for _label, _path in (("Fan Radio", FR), ("the Radio Ad Creator", RP)):
    for _payload in ({"name": "", "script": "x"},
                     {"name": "n", "script": ""},
                     {"name": "n", "script": "x" * 4001},
                     {"name": "x" * 81, "script": "ok"}):
        check(f"{_label} refuses {list(_payload)[0]}={_payload[list(_payload)[0]][:12]!r}"
              f" / {list(_payload)[1]}=len {len(str(_payload[list(_payload)[1]]))}",
              client.post(_path, json=_payload).status_code, 400)


# ===========================================================================
section("And a rep can actually reach it")
# ===========================================================================
# The half the old test could not see. A route that answers and a screen that
# offers it are different claims, and only the second one is a feature.
_fan_page = client.get("/tools/fan-radio/").get_data(as_text=True)
_promo_page = client.get("/tools/radio-promo/").get_data(as_text=True)
check("Fan Radio draws the picker", "function presetControls(" in _fan_page, True)
check("and offers saving a read to the library",
      'data-a="save-preset"' in _fan_page, True)
check("and inserts one into the copy",
      "insert-preset" in _fan_page, True)
check("the Radio Ad Creator draws the picker",
      "function presetControls(" in _promo_page, True)
check("and offers saving", "savePreset(" in _promo_page, True)
check("and inserting", "insertPreset(" in _promo_page, True)
# Both read the library from the server rather than restating the defaults.
check("neither page hardcodes the default reads",
      ("Stop in and see us today" in _fan_page,
       "Stop in and see us today" in _promo_page), (False, False))


print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    print("\nthe reusable-read library is one store both builders offer, the "
          "placeholder is filled on the way out and put back on the way in, "
          "and a route nobody can press is not a feature")
sys.exit(1 if FAIL else 0)
