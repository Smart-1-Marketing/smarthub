"""The radio sample seeder: what it writes, and what it must never delete.

    python3 test_radio_samples.py

Same shape as the other suites here: no pytest, a temporary data directory and
its own SQLite mirror, so it never touches /var/data or the real one. Nothing
reaches a provider -- the seeder is hand-written copy by design.

## Why this file exists

`tools/seed_radio_samples.py` puts a demonstration campaign in each radio
builder. Two things about it are worth holding to an assertion.

**It must not write a sample the tool would flag.** Every script is graded by
the module's own catalog, and four of the first eight written for it were long
or short. A sample sitting in the library carrying the over-budget warning is
demonstrating the failure the grader exists to raise, and reads -- to anybody
who did not write it -- as the tool getting it wrong.

**`--remove` must never delete somebody's work.** This is the one outcome here
that cannot be undone, and it is a live risk rather than a hypothetical one:
both modules exist partly to support promoting a spec piece into a client's,
and the sample mark survives that promotion. Fan Radio's store says "a spec
spot that wins the business becomes that client's first spot"; Radio Promo's
says a spec project "can be attached to a client later without losing
anything". So a rep who takes the sample, attaches it to a real client and
builds on it has made real work out of a row still carrying the mark -- and a
remover matching on the mark alone would delete it on the next run.

The production database has three Radio Promo projects in it, two attached to
real clients, so this is asserted rather than reasoned about.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1radiosample_")
os.makedirs(os.path.join(TMP, "disk"), exist_ok=True)
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "radio-sample-test-secret"

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from tools import seed_radio_samples as seed                        # noqa: E402
from modules.fan_radio import store as fan_store                    # noqa: E402
from modules.fan_radio import catalog as fan_catalog                # noqa: E402
from modules.radio_promo import store as promo_store                # noqa: E402
from modules.radio_promo.catalog import (duration_by_key,           # noqa: E402
                                         slots_of)


# ---------------------------------------------------------------------------
section("The samples are written, and they are what the tools produce")
# ---------------------------------------------------------------------------
fan = seed.seed_fan_radio(verbose=False)
promo = seed.seed_radio_promo(verbose=False)

check("Fan Radio gets a project", bool(fan.get("id")))
check("  with six spots -- three dayparts, two lengths",
      len(fan.get("spots") or []), 6)
check("  and it is a spec piece, so no client can be reached from it",
      fan.get("scope"), "spec")
check("  with no client name on it either", fan.get("client"), "")

check("Radio Promo gets a project", bool(promo.get("id")))
check("  marked spec", promo.get("spec"), True)
check("  with no client name on it", promo.get("client"), "")
check("  carrying both lengths",
      sorted(k for k in (promo.get("scripts") or {}) if k != "hook"),
      ["fifteen", "thirty"])


# ---------------------------------------------------------------------------
section("Every script is inside the budget its own catalog sets")
# ---------------------------------------------------------------------------
# Graded by the module rather than restated here: a copy of the numbers in the
# test is a third thing to keep in step, and it is the catalog's answer that
# decides what the library draws.
for spot in fan["spots"]:
    grade = fan_catalog.grade(spot["script"], spot["seconds"])
    check(f"fan {spot['daypart']} :{spot['seconds']} is on the clock "
          f"({grade['words']}w)", grade["state"], "ok")
    check(f"  and its stored grade agrees",
          (spot.get("grade") or {}).get("state"), "ok")

# The slots this sample actually writes, not every slot the catalog sells. The
# :60 is opt-in -- each length is a model call and a slot somebody then has to
# record -- so a seeded pair carries no :60 script and asking for one here was
# reading the catalog as a promise about every project.
for slot in [duration_by_key(k) for k in slots_of(promo)]:
    part = promo["scripts"][slot["key"]]
    inside = slot["low"] <= part["word_count"] <= slot["high"]
    check(f"promo :{slot['seconds']} is inside {slot['low']}-{slot['high']} "
          f"({part['word_count']}w)", inside)
    check(f"  and is not flagged over budget", part["over_budget"], False)

# The module's own writer requires the disclaimer verbatim in BOTH lengths,
# and the :15 was at its ceiling before it was added -- so this is the
# assertion that stops it being quietly dropped to make the count fit.
disclaimer = promo.get("disclaimer") or ""
check("the disclaimer is a real one to look for", bool(disclaimer.strip()))
for key in ("fifteen", "thirty"):
    check(f"  and it is read verbatim in the {key} script",
          disclaimer in promo["scripts"][key]["script"])

# A blocked phrase is the one finding Fan Radio refuses to deliver on.
check("no sample spot trips the phrase guard",
      [s["id"] for s in fan["spots"] if (s.get("scan") or {}).get("blocked")], [])


# ---------------------------------------------------------------------------
section("Nothing claims to be a model's work, or to have audio behind it")
# ---------------------------------------------------------------------------
check("every spot says it was not written by a model",
      all(s.get("ai") is False for s in fan["spots"]))
check("  and says why, rather than leaving a bare False",
      all(s.get("ai_reason") for s in fan["spots"]))
check("no spot points at audio that does not exist",
      [s["id"] for s in fan["spots"] if s.get("audio_url")], [])
check("and Radio Promo has no rendered spots either",
      promo.get("spots"), [])


# ---------------------------------------------------------------------------
section("Running it twice replaces rather than adding a second pair")
# ---------------------------------------------------------------------------
seed.remove(verbose=False)
fan2 = seed.seed_fan_radio(verbose=False)
promo2 = seed.seed_radio_promo(verbose=False)

fan_samples = [r for r in fan_store.index()
               if seed._is_sample(fan_store.load(r["id"]) or {})]
promo_samples = [r for r in promo_store.all_projects() if seed._is_sample(r)]
check("one Fan Radio sample, not two", len(fan_samples), 1)
check("one Radio Promo sample, not two", len(promo_samples), 1)


# ---------------------------------------------------------------------------
section("A sample somebody adopted is their work, and is never removed")
# ---------------------------------------------------------------------------
# The failure this guards: both modules support promoting a spec piece into a
# client's, and the mark survives it. Matching on the mark alone would delete
# a real client's spot the next time the seeder ran.
promo_store.update(promo2["id"], {"client": "Icon Solar"})
adopted_promo = promo_store.get(promo2["id"])
check("the adopted row still carries the sample mark",
      adopted_promo.get("sample"), True)
check("  and is nonetheless not a sample any more",
      seed._is_sample(adopted_promo), False)

fan_adopted = fan_store.load(fan2["id"])
fan_adopted["scope"] = "client"
fan_adopted["client"] = "Buckeye Marina"
fan_store.save(fan_adopted)
check("the same holds for Fan Radio, on its own spelling of it",
      seed._is_sample(fan_store.load(fan2["id"])), False)

removed_fan, removed_promo = seed.remove(verbose=False)
check("so removing takes neither of them", (removed_fan, removed_promo), (0, 0))
check("  the Fan Radio project is still there",
      bool(fan_store.load(fan2["id"])))
check("  and so is the Radio Promo one",
      bool(promo_store.get(promo2["id"])))
check("  with the client still on it",
      (promo_store.get(promo2["id"]) or {}).get("client"), "Icon Solar")

# And a genuine, unadopted sample is still removed -- a guard that refused
# everything would be a remover that does not remove.
fresh_fan = seed.seed_fan_radio(verbose=False)
fresh_promo = seed.seed_radio_promo(verbose=False)
gone_fan, gone_promo = seed.remove(verbose=False)
check("an unadopted sample is still removed", (gone_fan, gone_promo), (1, 1))
check("  and the adopted rows survived that too",
      bool(fan_store.load(fan2["id"])) and bool(promo_store.get(promo2["id"])))
check("  while the fresh samples are gone",
      bool(fan_store.load(fresh_fan["id"])) or
      bool(promo_store.get(fresh_promo["id"])), False)


# ---------------------------------------------------------------------------
section("A project nobody marked is never touched")
# ---------------------------------------------------------------------------
# The rows this ran against in production: three Radio Promo projects, two
# with a client on them, none of them anything to do with this script.
real = promo_store.create({"spec": True, "client": "",
                           "project_name": "Somebody's own spec piece",
                           "team_member": "A real person",
                           "company": "A real business"})
check("an unmarked spec project is not a sample", seed._is_sample(real), False)
seed.remove(verbose=False)
check("  and survives a removal run", bool(promo_store.get(real["id"])))


# ---------------------------------------------------------------------------
section("An out-of-budget sample is refused rather than written")
# ---------------------------------------------------------------------------
original = seed.PROMO_THIRTY
try:
    seed.PROMO_THIRTY = "Too short."
    raised = False
    try:
        seed.seed_radio_promo(verbose=False)
    except seed.SampleOutOfBudget:
        raised = True
    check("a :30 that misses the budget raises rather than seeding", raised)
finally:
    seed.PROMO_THIRTY = original

original_spots = seed.FAN_SPOTS
try:
    seed.FAN_SPOTS = [("gameday", 15, "neutral", "Far too short a script.")]
    raised = False
    try:
        seed.seed_fan_radio(verbose=False)
    except seed.SampleOutOfBudget:
        raised = True
    check("and so does a Fan Radio spot that misses its own", raised)
finally:
    seed.FAN_SPOTS = original_spots

seed.remove(verbose=False)

print(f"\n{'-' * 62}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
