"""Commercial Builder — what a spot costs, and what has actually been delivered.

    python3 test_commercial_meter.py

Same shape as test_commercial_library.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database.

## Why this file exists

  1. **The module that spends the most was invisible on the usage page.**
     HeyGen, Runway and Creatomate all bill per generation and were recorded
     nowhere — and `quotas._PROVIDER_MARKERS` did not know them, so there was
     no check that could ever have named the gap. That is the confident low
     number `hub/quotas.py` exists to stop, and it stood because the thing
     that would have caught it had no marker to catch it with.

  2. **The image path was billed and uncounted** while every text call was
     tracked: `hub/ai.note_sdk_usage()` reads `.usage`, which an images
     response does not carry.

  3. **A price nobody published is a price this tool would be inventing.**
     A plausible dollar figure on a screen a rep reads is worse than none,
     because it gets repeated.

  4. **A rendered cut is not a delivered one.** The library counts what
     somebody approved, which is the distinction `approve_render` already
     draws, read from the other end.

  5. **A filtered list reporting an unfiltered total** is a wrong answer with
     two right ones either side of it — the SEO gallery paid for that.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1cbmeter_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "cbmeter-test-secret"
os.environ["PANEL_PASSWORD"] = "cbmeter-test-password"
for _k in ("OPENAI_API_KEY", "ELEVENLABS_API", "ELEVENLABS_API_KEY", "HEYGEN_API",
           "RUNWAY_API_KEY", "CREATOMATE_API_KEY", "CLOUDINARY_URL"):
    os.environ.pop(_k, None)

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
    print(f"\n{title}")


MOUNT = "/tools/commercial-builder"

from hub import quotas                                                   # noqa: E402
from modules.commercial_builder import cost_spec as cost                 # noqa: E402


section("Every billed call site records, and the check can see all of them")
# The gap that stood: three providers billed per generation, recorded nowhere,
# and no marker in the scanner — so nothing could have named it.
for provider in ("heygen", "runway", "creatomate"):
    check(f"{provider} is a metered provider now", provider in quotas.QUOTAS, True)
    check(f"and the scanner knows how to spot a {provider} call",
          provider in quotas._PROVIDER_MARKERS, True)
untracked = quotas.untracked_provider_calls(force=True)
for provider, rows in sorted(untracked.items()):
    check(f"nothing spends {provider} without recording it",
          [r["file"] for r in rows], [])

# The markers must actually fire on this module's own services, or they are
# decoration: a check that cannot see the file it was written for is the
# check going quiet again.
import inspect                                                           # noqa: E402
from modules.commercial_builder.services import (heygen_service,          # noqa: E402
                                                 runway_service,
                                                 creatomate_service,
                                                 openai_service)
for provider, mod in (("heygen", heygen_service), ("runway", runway_service),
                      ("creatomate", creatomate_service)):
    src = inspect.getsource(mod)
    check(f"the {provider} marker recognizes its own service as a call site",
          quotas._PROVIDER_MARKERS[provider]["calls"](src), True)
    check(f"and sees that it now records",
          any(m in src for m in quotas._PROVIDER_MARKERS[provider]["recorded"]), True)

# hub/ai.note_sdk_usage() reads `.usage`, which an images response does not
# carry — so this path was billed and counted nowhere.
check("the image path records too",
      "record_image" in inspect.getsource(openai_service), True)


section("A refused call is recorded, not dropped")
# It spent nothing and is excluded from every billable total, but the row
# stays: a wall of them is what a spent allowance looks like from this side.
for name in ("record_clip", "record_render", "record_video", "record_image"):
    fn = getattr(quotas, name)
    check(f"{name} takes an ok flag", "ok" in inspect.signature(fn).parameters, True)
# None of them may raise: an uninstrumented call site is bad, and one that
# takes the render down with it is worse.
quotas.record_clip(module="x", detail="y")
quotas.record_render(module="x", fmt="16:9")
quotas.record_video("runway", module="x", seconds=5)
quotas.record_image(module="x")
check("none of the helpers raises on a normal call", True, True)
check("nor on nonsense", quotas.record_video("runway", module="x",
                                             seconds="banana") is None, True)


section("Runway is counted in seconds, because that is how it bills")
# A :10 clip is twice a :05, and counting requests would make them equal —
# the mistake counting ElevenLabs renders rather than characters would make.
check("its unit says so", quotas.QUOTAS["runway"].unit, "seconds of video")
check("HeyGen's does not", quotas.QUOTAS["heygen"].unit, "clips")
check("nor Creatomate's", quotas.QUOTAS["creatomate"].unit, "renders")
# No plan figure this deployment can cite, so no ceiling is invented.
for provider in ("heygen", "runway", "creatomate"):
    check(f"{provider} claims no limit it cannot support",
          quotas.QUOTAS[provider].limit, 0)
    check(f"and says so in its note",
          "not measured" in quotas.QUOTAS[provider].note
          or "No ceiling" in quotas.QUOTAS[provider].note, True)


section("The estimate counts real units and prices only what it can")
check("nothing hard-codes a price", cost.check_spec(), [])
one = cost.estimate([30], ["16:9"])
check("a :30 is measured", one["measured"], True)
labels = [r["label"] for r in one["rows"]]
check("stills are counted", "AI stills" in labels, True)
check("so is the voiceover", "Voiceover" in labels, True)
check("and the render", "Renders" in labels, True)
# A rep who has not asked for AI video should not be quoted for it.
check("AI video is not counted unless asked for", "AI video" in labels, False)
check("nor a spokesperson", "Spokesperson clips" in labels, False)
withvideo = cost.estimate([30], ["16:9"], ai_video=True)
check("asking for it counts it",
      "AI video" in [r["label"] for r in withvideo["rows"]], True)
check("in seconds", next(r["unit"] for r in withvideo["rows"]
                         if r["label"] == "AI video"), "seconds of video")

# The one real published rate the Hub holds is read from where it lives,
# never restated — a price in two places goes stale in one of them.
stills = next(r for r in one["rows"] if r["label"] == "AI stills")
check("stills carry a real cost", stills["usd"] is not None, True)
check("and name where the rate came from",
      stills["price_source"], "hub/quotas.IMAGE_PRICING")
unpriced = [r for r in one["rows"] if r["usd"] is None]
check("the rest are honestly unpriced", bool(unpriced), True)
check("each saying so in words",
      all("not measured" in r["price_note"] for r in unpriced), True)
# A total that quietly covers two of five rows is the confident low number
# this whole phase is about.
check("the total names what it left out", bool(one["unpriced"]), True)
check("and the note says the total is partial",
      "only what has a published rate" in one["note"], True)


section("It scales with the choice, and never reads as a quote")
three = cost.estimate([30, 15, 60], ["16:9", "9:16"])
check("three lengths cost more than one",
      three["rows"][0]["units"] > one["rows"][0]["units"], True)
check("two formats double the renders",
      next(r["units"] for r in three["rows"] if r["label"] == "Renders"), 6)
check("and the spot count is reported", three["spots"], 3)
# It is what the tools consume, not what a client pays. hub/rate_card.py is
# the other thing, and confusing them on a screen a rep quotes from is the
# failure proposal_spec.py spends a page on.
check("the caveat says it is not a quote",
      "not what the client is charged" in one["caveat"], True)
check("and names the rate card as the thing that is",
      "rate card" in one["caveat"], True)
# The shot count is the same number the Blueprint scores against, so the
# estimate and the thing it estimates cannot drift.
from modules.commercial_builder.services import abcd_service              # noqa: E402
target = abcd_service.shot_targets(30)
check("the shot count comes from the same table the Blueprint uses",
      target["low"] <= one["rows"][0]["units"] / 2 <= target["high"], True)
# Runs on a keystroke on the Start page.
check("nothing picked is not an error", cost.estimate([])["measured"], False)
check("and says what to do", "Pick a length" in cost.estimate([])["note"], True)
check("garbage does not raise", cost.estimate("nonsense")["measured"], False)


# ---------------------------------------------------------------------------
# The app
# ---------------------------------------------------------------------------
import werkzeug.test                                                     # noqa: E402
from wsgi import application, hub_app                                    # noqa: E402
from modules.commercial_builder.db import db                             # noqa: E402
from modules.commercial_builder.models import (Client, RenderApproval,    # noqa: E402
                                               RenderJob)

staff = werkzeug.test.Client(application)
staff.post("/login", data={"password": os.environ["PANEL_PASSWORD"]}, follow_redirects=True)

row = staff.post(MOUNT + "/api/clients",
                 json={"name": "Ridge Roofing", "website": "ridge.test"}
                 ).get_json()["client"]
pid = staff.post(MOUNT + "/api/projects",
                 json={"client_id": row["id"], "lengths": [30], "formats": ["16:9"],
                       "commercial_type": "product_spotlight", "platform": "ctv"}
                 ).get_json()["projects"][0]["id"]


section("The cost is said where the choice is made")
preview = staff.get(MOUNT + "/api/projects/cost-preview"
                            "?lengths=30,15&formats=16:9,9:16&ai_video=1").get_json()
check("the route answers", preview["ok"], True)
check("with rows", len(preview["estimate"]["rows"]) >= 3, True)
check("and AI video when asked for",
      "AI video" in [r["label"] for r in preview["estimate"]["rows"]], True)
start = staff.get(MOUNT + "/new").get_data(as_text=True)
check("the panel is on the Start page", 'id="cost-preview"' in start, True)


section("A rendered cut is not a delivered one")
empty = staff.get(MOUNT + "/api/projects/library").get_json()
check("nothing is delivered yet", empty["total"], 0)
check("and it says why rather than drawing an empty table",
      "not a delivered one" in empty["note"], True)

with hub_app.app_context():
    job = RenderJob(project_id=pid, format="16:9", status="succeeded",
                    output_url="https://example.test/spot.mp4")
    db.session.add(job)
    db.session.commit()
    job_id = job.id

# A succeeded render is a file nobody has watched. RenderApproval is the row
# that says a human signed it off.
rendered = staff.get(MOUNT + "/api/projects/library").get_json()
check("a succeeded render alone does not appear", rendered["total"], 0)

with hub_app.app_context():
    db.session.add(RenderApproval(render_job_id=job_id, project_id=pid,
                                  approved_by="Dana Reyes",
                                  stored_url="https://cdn.test/spot.mp4"))
    db.session.commit()

lib = staff.get(MOUNT + "/api/projects/library").get_json()
check("an approved one does", lib["total"], 1)
spot = lib["spots"][0]
check("carrying the client", spot["client"], "Ridge Roofing")
check("the format", spot["format"], "16:9")
check("who approved it", spot["approved_by"], "Dana Reyes")
# Read through library_spec, so the library and the Brief screen name the
# archetype the same way — including for a project saved before it existed.
check("and the archetype, read from the legacy value",
      spot["archetype_label"], "Demonstration")
check("with the stored copy", spot["url"], "https://cdn.test/spot.mp4")


section("A filtered list reports what was asked for")
# "Showing 1 of 7" about a client with exactly one is the wrong answer with
# two right ones either side of it.
miss = staff.get(MOUNT + "/api/projects/library?length=60").get_json()
check("a filter that matches nothing says nothing matched", miss["total"], 0)
check("but still knows the library is not empty", miss["delivered_total"], 1)
check("a search that matches", staff.get(
    MOUNT + "/api/projects/library?q=ridge").get_json()["total"], 1)
check("a search that does not", staff.get(
    MOUNT + "/api/projects/library?q=nobody").get_json()["total"], 0)
check("filtering by archetype works", staff.get(
    MOUNT + "/api/projects/library?archetype=demonstration").get_json()["total"], 1)
check("and by one it is not", staff.get(
    MOUNT + "/api/projects/library?archetype=testimonial").get_json()["total"], 0)


section("A spot with no stored copy says so rather than offering a dead link")
# The only other copy is a provider URL that expires.
with hub_app.app_context():
    j2 = RenderJob(project_id=pid, format="9:16", status="succeeded",
                   output_url="https://example.test/vertical.mp4")
    db.session.add(j2)
    db.session.commit()
    db.session.add(RenderApproval(render_job_id=j2.id, project_id=pid,
                                  approved_by="Dana Reyes", stored_url=""))
    db.session.commit()
lib = staff.get(MOUNT + "/api/projects/library").get_json()
missing = next(s for s in lib["spots"] if s["format"] == "9:16")
check("the url is empty", missing["url"], "")
check("and the reason is carried", "nothing durable" in missing["url_note"], True)


section("The library is a screen, and it is linked")
page = staff.get(MOUNT + "/library")
check("it renders", page.status_code, 200)
body = page.get_data(as_text=True)
check("with the filters", 'id="lib-archetype"' in body, True)
# A tool with no link is invisible — CLAUDE.md counts six that were.
check("and the tab bar links it",
      "Spot Library" in staff.get(MOUNT + "/").get_data(as_text=True), True)
anon = werkzeug.test.Client(application)
check("it needs a login", anon.get(MOUNT + "/library").status_code in (301, 302), True)
check("and so does the API",
      anon.get(MOUNT + "/api/projects/library").status_code, 401)
check("as does the cost preview",
      anon.get(MOUNT + "/api/projects/cost-preview?lengths=30").status_code, 401)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
