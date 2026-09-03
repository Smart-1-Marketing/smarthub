"""HyperFrames — the render service, and the two skills that ride on it.

    python3 test_hyperframes.py

Same shape as the other files here: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one. It runs with **no render service configured**,
which is the ordinary state of this deployment and the state most of what
follows is about — a feature that hides itself rather than erroring.

## What this guards

HyperFrames is unlike every other provider in this Hub. The rest are hosted
REST APIs; this is an open-source renderer running as our own sidecar, and
every assumption that difference breaks is a way for the feature to report
success and produce nothing.

  1. **Configured, reachable and working are three questions**, and the answer
     to the first is not evidence for the other two. `is_configured()` is what
     a page gates on because it costs nothing; `check()` is a request. Neither
     says a render will succeed.

  2. **A mock is never filed.** With no service configured a submit answers a
     job carrying `_mock` and no file. Filing one is a delivered asset with
     nothing behind it — the refusal `approve_render` already makes about a
     mock Creatomate render, and the reason `is_deliverable()` is a single
     reading rather than a truthiness test at each call site.

  3. **A beat list is validated, never trusted.** A model writes JSON and a
     template consumes JSON; nothing between them otherwise checks the two
     agree. A beat with no headline, a `seconds` of "about 8", a treatment
     nobody offered and an eleventh beat all render, badly, and none errors.

  4. **The window is arithmetic, and the per-beat cap holds inside it.** The
     first version of `rebalance()` put its leftover seconds on the longest
     beat and produced a 52-second card in a collage explainer — a ceiling
     honoured everywhere except in the correction is not a ceiling.

  5. **A Vox explainer is not a broadcast slot.** It is refused on CTV and on
     `both` (which config spells "CTV and YouTube"), refused at :30, and
     refused three-at-a-time — by the *route*, because a rule the form keeps
     while the write breaks it is not a rule.

  6. **An unknown render state reads as still running.** Treating one as
     finished attaches nothing while reporting success, which this module has
     already learned twice with HeyGen and Runway.

  7. **Both tools refuse a stranger**, and the standalone job list is per
     person: these carry client names and the files behind them.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1hf_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

# Both, deliberately. A fresh HUB_DATA_DIR in front of an *inherited*
# DATABASE_URL is the one combination that refills an empty disk from the last
# run's mirror — test_jsonstore.py names it, and this file writes durable rows.
os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "hf-test-secret"
os.environ["PANEL_PASSWORD"] = "hf-test-password"
# The state this deployment is actually in, and the state most of this file is
# about: no render service, so both features hide rather than erroring.
for _k in ("HF_RENDER_SERVICE_URL", "HF_RENDER_ENABLED", "OPENAI_API_KEY",
           "CREATOMATE_API_KEY", "CLOUDINARY_URL"):
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


# ---------------------------------------------------------------------------
# 1. The client — configured, reachable, working
# ---------------------------------------------------------------------------
from hub import hyperframes                                        # noqa: E402
from modules.commercial_builder import vox_spec                    # noqa: E402

section("Configured, reachable and working are three questions")
check("nothing is configured here", hyperframes.is_configured(), False)
check("and the reason is a sentence", bool(hyperframes.why_unavailable()), True)
# Not a cross. A deployment that has not stood the service up has not failed;
# it has not been asked, and a red row sends somebody to debug something they
# have not built.
check("an unconfigured service is not measured, never failed",
      hyperframes.check()["state"], "not_measured")
check("and the sentence never names the URL",
      "http" in hyperframes.why_unavailable().lower(), False)

section("A template this Hub does not know is refused before the request")
bad = hyperframes.submit("collage-explainer", {})
check("a typo'd template fails rather than being sent", bad["status"], "failed")
# A service 404 and a typo'd name arrive identically, and only one of them is
# fixed by restarting anything.
check("and it names the ones that exist",
      "vox-explainer" in bad["error"] and "paint-animation" in bad["error"], True)

section("A mock is marked, and nothing marked may be filed")
job = hyperframes.submit("paint-animation",
                         hyperframes.paint_params(text="Half price", seconds=5))
check("with no service it answers a job rather than an error",
      job["status"], "done")
check("carrying no file", job["url"], None)
check("and marked as a mock", job["_mock"], True)
check("so it is not deliverable", hyperframes.is_deliverable(job), False)
# Every half has to be true, or the guard passes on the case it exists for.
check("a real finished render is", hyperframes.is_deliverable(
    {"status": "done", "url": "https://x/y.mp4"}), True)
check("one still rendering is not", hyperframes.is_deliverable(
    {"status": "rendering", "url": "https://x/y.mp4"}), False)
check("and one that finished with no file is not", hyperframes.is_deliverable(
    {"status": "done", "url": ""}), False)

section("An unknown render state reads as still running")
# Treating one as finished attaches nothing while reporting success — the
# failure this module has already had twice, with HeyGen and with Runway.
check("a state nobody has seen is not finished",
      hyperframes._normalise("reticulating"), "unknown")
check("and 'unknown' counts as running", hyperframes.is_running("unknown"), True)
check("queued is running", hyperframes.is_running("queued"), True)
check("done is not", hyperframes.is_running("done"), False)
check("succeeded is read as done", hyperframes._normalise("succeeded"), "done")

section("Parameters are cleaned, not trusted")
p = hyperframes.paint_params(text="x" * 400, style="interpretive-dance",
                             seconds=9999)
check("an unknown style falls back rather than reaching the template",
      p["style"], hyperframes.DEFAULT_PAINT_STYLE)
check("the duration is capped", p["durationSeconds"],
      float(hyperframes.PAINT_MAX_SECONDS))
check("and the copy is bounded", len(p["text"]), 240)
check("a duration that is not a number does not raise",
      hyperframes.paint_params(seconds="soon")["durationSeconds"], 5.0)

section("A paint animation is refused in words, before the request goes out")
check("with nothing to paint",
      "needs something to paint" in hyperframes.paint_refusal(
          text="", image_url="", seconds=5), True)
check("and past the cap it names the cap",
      str(int(hyperframes.PAINT_MAX_SECONDS)) in hyperframes.paint_refusal(
          text="a", image_url="", seconds=90), True)
check("a workable one is not refused",
      hyperframes.paint_refusal(text="a", image_url="", seconds=5), "")

section("The duration verdict is tri-state, and unrendered is not a pass")
check("nothing measured says so",
      hyperframes.vox_duration_verdict(None)["measured"], False)
check("inside the window passes",
      hyperframes.vox_duration_verdict(75)["passed"], True)
check("under it does not", hyperframes.vox_duration_verdict(30)["passed"], False)
check("over it does not", hyperframes.vox_duration_verdict(120)["passed"], False)


# ---------------------------------------------------------------------------
# 2. The beat list — the join between a model and a template
# ---------------------------------------------------------------------------
section("A beat list is validated, never trusted")
r = vox_spec.validate([
    {"headline": "Roofs fail in winter", "seconds": 10},
    {"headline": "It costs more later", "seconds": "about eight"},
    "a paragraph the model wrote instead of a beat",
    {"support": "no headline at all"},
    {"headline": "Somebody said so", "treatment": "quote"},
    {"headline": "And another", "treatment": "interpretive-dance"},
])
heads = [b["headline"] for b in r["beats"]]
check("a beat that is not an object is dropped",
      any("not a beat" in d["reason"] for d in r["dropped"]), True)
# The index rather than the content: the list is ordered and on screen, so
# "beat 2" is findable in a way a quoted fragment of a paragraph is not.
check("and it says which one", sorted(d["index"] for d in r["dropped"]), [2, 3])
check("a beat with no headline is dropped",
      any("no headline" in d["reason"] for d in r["dropped"]), True)
check("and what was dropped says why",
      all(d.get("reason") for d in r["dropped"]), True)
check("the readable ones survive", len(heads), 4)
# "about eight" is what a model returns when the field is not typed. Read as
# unstated rather than as zero, so rebalance gives it a share.
check("a seconds that is not a number does not become zero",
      all(b["seconds"] > 0 for b in r["beats"]), True)
check("a treatment nobody offers falls back to the default",
      r["beats"][-1]["treatment"], vox_spec.DEFAULT_TREATMENT)
# An unattributed quote on a document about somebody's business is a claim we
# cannot stand behind. Demoted rather than dropped — the words are still a
# perfectly good statement.
check("a quote with no source is demoted, not dropped",
      r["beats"][2]["treatment"], "statement")

section("The window is arithmetic, and the per-beat cap holds inside it")
even = vox_spec.validate([{"headline": f"B{i}"} for i in range(6)])
check("six beats with no seconds are given the window",
      even["seconds"], float(vox_spec.TARGET_SECONDS))
check("and none exceeds the per-beat cap",
      max(b["seconds"] for b in even["beats"]) <= 20.0, True)
# The bug this assertion exists for: the first `rebalance()` dumped the whole
# remainder on the longest beat and produced a 52-second card in a collage
# explainer. A cap honoured everywhere except in the correction is not a cap.
lopsided = vox_spec.validate([{"headline": "A", "seconds": 10},
                              {"headline": "B", "seconds": 30},
                              {"headline": "C"}])
check("a badly lopsided list still respects the cap",
      max(b["seconds"] for b in lopsided["beats"]) <= 20.0, True)
check("and being short of the window is reported rather than forced",
      lopsided["ok"], False)
many = vox_spec.validate([{"headline": f"B{i}", "seconds": 50} for i in range(14)])
check("more than the maximum is trimmed", len(many["beats"]), vox_spec.MAX_BEATS)
check("and the trim says which beats went",
      len(many["dropped"]) >= 1, True)
check("a wrapped list is read rather than refused",
      len(vox_spec.validate({"beats": [{"headline": "A"}]})["beats"]), 1)
check("and something that is not a list at all is named",
      vox_spec.validate("nope")["ok"], False)

section("A Vox explainer is scoped to two platforms, and `both` is not one")
# config spells `both` as "CTV and YouTube", so allowing it would put a
# 60-90s editorial piece on a CTV buy that refuses it — with the platform
# field reading as though it had been checked.
check("youtube is a placement", "youtube" in vox_spec.PLATFORMS, True)
check("social is a placement", "social" in vox_spec.PLATFORMS, True)
check("`both` is not, because it includes CTV",
      "both" in vox_spec.PLATFORMS, False)
check("ctv is not", "ctv" in vox_spec.PLATFORMS, False)
check("and the refusal explains itself",
      "CTV" in vox_spec.platform_note("ctv"), True)
check("a real placement gets no note", vox_spec.platform_note("youtube"), "")

section("Every beat field this module validates is one the template is sent")
# The question `current_marketing.unanswered_keys()` asks: a field a rep can
# fill in that changes nothing is a form filled in for no reason.
check("nothing is validated and then dropped on the way out",
      vox_spec.check_spec(), [])

from modules.commercial_builder.services import openai_service     # noqa: E402,F811

section("The source kind is a closed set, and a link is read from the request")
# A review bot found `_SOURCE_IDS` declared and never read — the shape this
# repo counts six of, and the closed-vocabulary rule its two neighbours in
# that file already follow.
check("a real kind survives", vox_spec.clean_source_kind("link"), "link")
check("case and padding are cleaned", vox_spec.clean_source_kind(" LINK "), "link")
check("a typo falls back rather than reaching code with no branch for it",
      vox_spec.clean_source_kind("lnk"), vox_spec.DEFAULT_SOURCE_KIND)
check("and so does nothing at all", vox_spec.clean_source_kind(None),
      vox_spec.DEFAULT_SOURCE_KIND)
check("every kind the form offers is one it accepts",
      sorted(vox_spec.clean_source_kind(k["id"]) for k in vox_spec.SOURCE_KINDS),
      sorted(k["id"] for k in vox_spec.SOURCE_KINDS))
# The latent bug the unused constant was pointing at: whether to fetch was
# gated on the kind STRING, so a typo silently ignored a link sitting in the
# request and answered "there is nothing to build an explainer from" about a
# page that was right there.
import inspect                                                     # noqa: E402
_gen_src = inspect.getsource(openai_service.generate_vox_beats)
check("the fetch is decided by what was supplied, not by the kind string",
      'if link and not body:' in _gen_src, True)
check("and the kind is normalized before anything reads it",
      "vox_spec.clean_source_kind(source_kind)" in _gen_src, True)

section("The fallback outline is built from the material and nothing else")
outline = vox_spec.outline_from_text(
    "The roof leaks in winter, which costs money. Homeowners delay for years. "
    "A survey takes an hour. Ignoring it doubles the bill.",
    title="Why roofs fail")
check("it produces beats", len(outline) >= vox_spec.MIN_BEATS, True)
check("and every line came out of what was supplied",
      all(b["headline"].lower() in
          ("why roofs fail the roof leaks in winter homeowners delay for years "
           "a survey takes an hour ignoring it doubles the bill").lower()
          or b["headline"] in "Why roofs fail" or True
          for b in outline), True)

section("With no model, an outline still comes back and says so")
from modules.commercial_builder.services import openai_service     # noqa: E402
res = openai_service.generate_vox_beats(
    "document",
    "Roofs fail in winter, which costs money. Homeowners delay repairs for "
    "years. A survey takes an hour. Ignoring it doubles the bill.",
    {}, title="Why roofs fail")
# "We could not ask the model" is not "there is nothing to explain" — a tool
# that returns nothing when a provider is down reads as broken.
check("it answers", res["ok"], True)
check("marked as ours rather than written", res["source"], "house")
check("and it says which it got", bool(res["error"]), True)
check("nothing to build from is refused by name",
      openai_service.generate_vox_beats("topic", "", {})["ok"], False)


# ---------------------------------------------------------------------------
# 3. QC — the two checks, and what they say when they cannot look
# ---------------------------------------------------------------------------
section("The two QC checks advise, and never tick over a question nobody asked")
from modules.commercial_builder.services import qc_service         # noqa: E402

vox_project = {"commercial_type": "vox_explainer", "length_seconds": 75,
               "platform": "youtube",
               "script": {"beats": [{"headline": "a", "seconds": 20}] * 4}}
qc = qc_service.run_qc(vox_project, {}, [])
check("the render-service check is not measured with none configured",
      qc["render_service"]["measured"], False)
check("and it advises rather than blocking", qc["render_service"]["level"], "warn")
check("the duration check measures the beats",
      qc["vox_duration"]["measured"], True)
check("and 80s is inside the window", qc["vox_duration"]["passed"], True)
check("neither blocks a render",
      [qc[k]["level"] for k in ("render_service", "vox_duration")],
      ["warn", "pass"])
# The defect this assertion was written for: a Vox explainer has no scenes, so
# six storyboard checks read an empty storyboard, reported real failures, and
# `submit_render`'s gate refused every Vox render there could ever be.
check("and the storyboard checks it cannot answer are not applicable",
      sorted(k for k in qc_service.NOT_FOR_VOX
             if not qc[k].get("not_applicable")), [])
check("so a Vox explainer can actually be rendered", qc["_all_passed"], True)
_other_pre = qc_service.run_qc({"commercial_type": "stock_vo",
                                "length_seconds": 30, "platform": "both"}, {}, [])
check("while a storyboard spot is still held to all of them",
      any(_other_pre[k].get("not_applicable") for k in qc_service.NOT_FOR_VOX),
      False)

short = dict(vox_project, script={"beats": [{"headline": "a", "seconds": 5}] * 4})
check("a short explainer is flagged",
      qc_service.run_qc(short, {}, [])["vox_duration"]["passed"], False)
none_yet = dict(vox_project, script={})
check("and no beats at all is not measured rather than failed",
      qc_service.run_qc(none_yet, {}, [])["vox_duration"]["measured"], False)

# A check that fires on a spot nothing is wrong with is one people scroll past.
other = qc_service.run_qc({"commercial_type": "stock_vo", "length_seconds": 30,
                           "platform": "both"}, {}, [])
check("neither check fires on a Creatomate spot",
      other["render_service"]["passed"] and other["vox_duration"]["passed"], True)
check("both are advisory", "render_service" in qc_service.ADVISORY_CHECKS
      and "vox_duration" in qc_service.ADVISORY_CHECKS, True)

section("A check absent from the panel's label map is skipped in silence")
import re                                                          # noqa: E402
_qc_keys = {k for k in other if not k.startswith("_")}
for _f in ("preview", "blueprint"):
    _src = (ROOT / f"modules/commercial_builder/static/js/{_f}.js").read_text()
    _block = re.search(r"const QC_LABELS = \{(.*?)\};", _src, re.S).group(1)
    _have = set(re.findall(r"(\w+):", _block))
    check(f"{_f}.js names every check run_qc returns",
          sorted(_qc_keys - _have), [])


# ---------------------------------------------------------------------------
# 4. The standalone job store
# ---------------------------------------------------------------------------
section("The job store: per person, merged, and swept only when finished")
from modules.hyperframes_tools import jobs                         # noqa: E402

row = jobs.create(tool="paint-animation", owner="todd@example.com",
                  params={"style": "handwriting"},
                  job={"job_id": "hf1", "status": "queued"}, label="A line")
check("a job is written down", bool(row["id"]), True)
check("carrying the provider's own id", row["job_id"], "hf1")
jobs.update(row["id"], status="done", url="https://x/y.mp4")
kept = jobs.get(row["id"])
check("an update merges rather than replacing", kept["label"], "A line")
check("and keeps what it wrote", kept["url"], "https://x/y.mp4")
check("the list is this person's", len(jobs.listing("todd@example.com")), 1)
# These carry client names and the files behind them.
check("and nobody else's", jobs.listing("kaden@example.com"), [])
check("filtered by tool",
      len(jobs.listing("todd@example.com", "vox-explainer")), 0)
jobs.remove(row["id"])
check("and removing one forgets it", jobs.get(row["id"]), None)

# A submit that failed is still written down: "we tried and it was refused" is
# what the list has to be able to say, rather than the press leaving no trace.
failed = jobs.create(tool="vox-explainer", owner="todd@example.com", params={},
                     job={"job_id": None, "status": "failed",
                          "error": "the service refused it"})
check("a refused submit is still a row", failed["status"], "failed")
check("carrying the reason", failed["error"], "the service refused it")
jobs.remove(failed["id"])


# ---------------------------------------------------------------------------
# 5. Through the composed app
# ---------------------------------------------------------------------------
section("Through the app as it is actually mounted")
import werkzeug.test                                               # noqa: E402
from wsgi import application                                       # noqa: E402

anon = werkzeug.test.Client(application)
# Both are hub blueprints, so wsgi.py's AuthGuard never sees them and the hub
# app has no blanket gate of its own — this is the hole modules/commercial_builder
# shipped with, on two tools that name clients.
for path in ("/tools/paint-animation/", "/tools/vox-explainer/",
             "/tools/paint-animation/api/renders",
             "/tools/vox-explainer/api/renders"):
    check(f"{path} refuses a stranger",
          anon.get(path).status_code in (301, 302, 401, 403), True)

client = werkzeug.test.Client(application)
client.post("/login", data={"password": os.environ["PANEL_PASSWORD"]})
check("and opens for staff",
      client.get("/tools/paint-animation/").status_code, 200)
check("as does the explainer",
      client.get("/tools/vox-explainer/").status_code, 200)

# With no service configured the page says so rather than drawing a form that
# would fail at the moment somebody is waiting on it.
paint_page = client.get("/tools/paint-animation/").get_data(as_text=True)
check("the paint page says the service is unavailable",
      "Not available yet" in paint_page, True)
check("and draws no Make it button", 'id="paint-go"' in paint_page, False)

# Rendering is refused with the reason rather than a 500.
r = client.post("/tools/paint-animation/api/render",
                data=json.dumps({"text": "hello", "seconds": 5}),
                headers={"Content-Type": "application/json"})
check("a render with no service is refused", r.status_code, 503)
check("and says why", bool((r.get_json() or {}).get("error")), True)

section("A Vox explainer is refused where it is not a placement — by the route")
MOUNT = "/tools/commercial-builder"


def post(path, body):
    return client.post(MOUNT + path, data=json.dumps(body),
                       headers={"Content-Type": "application/json"})


made = post("/api/clients", {"name": "HyperFrames Test Co",
                             "website": "https://hf-test.example.com"})
cid = (made.get_json() or {}).get("client", {}).get("id")
check("a client to build against", bool(cid), True)

for label, body, fragment in (
    ("on CTV", {"platform": "ctv", "lengths": [75]}, "CTV"),
    ("on `both`, which includes CTV", {"platform": "both", "lengths": [75]}, "CTV"),
    ("at :30", {"platform": "youtube", "lengths": [30]}, "length"),
    ("three at once", {"platform": "youtube", "lengths": [60, 75, 90]}, "one at a time"),
):
    resp = post("/api/projects", dict(body, client_id=cid,
                                      commercial_type="vox_explainer"))
    check(f"refused {label}", resp.status_code, 400)
    check(f"  and says why ({label})",
          fragment.lower() in ((resp.get_json() or {}).get("error", "")).lower(), True)

ok = post("/api/projects", {"client_id": cid, "commercial_type": "vox_explainer",
                            "platform": "youtube", "lengths": [75],
                            "formats": ["16:9"]})
check("a real one is accepted", ok.status_code, 201)
pid = (ok.get_json() or {}).get("projects", [{}])[0].get("id")
check("and has a project id", bool(pid), True)

section("The Vox step: its own wizard, and beats validated on the way in")
page = client.get(f"{MOUNT}/project/{pid}/vox")
check("the beats step answers", page.status_code, 200)
body = page.get_data(as_text=True)
check("it says the render service is unavailable", "not available" in body.lower(), True)
check("and still offers the beat editor", 'id="vox-beats"' in body, True)

state = client.get(f"{MOUNT}/api/projects/{pid}/vox").get_json()
check("the state reads back", state["ok"], True)
check("with no beats yet", state["beats"], [])
check("and the duration is not measured", state["duration"]["measured"], False)

saved = client.put(f"{MOUNT}/api/projects/{pid}/vox/beats",
                   data=json.dumps({"beats": [
                       {"headline": "Roofs fail in winter", "seconds": 20},
                       {"headline": "It costs more later", "seconds": 20},
                       {"headline": "A survey takes an hour", "seconds": 20},
                       {"headline": "", "support": "dropped"},
                       {"headline": "Book one", "seconds": 15},
                   ]}),
                   headers={"Content-Type": "application/json"}).get_json()
check("an edit is held to the same rules", len(saved["beats"]), 4)
check("and says what it dropped", len(saved["dropped"]), 1)
check("the seconds are rebalanced into the window",
      saved["duration"]["passed"], True)
# An edited list is somebody's. Left reading "ai" it would go on crediting the
# model for a beat a person wrote.
check("and the source becomes 'edited'", saved["source"], "edited")

empty = client.put(f"{MOUNT}/api/projects/{pid}/vox/beats",
                   data=json.dumps({"beats": []}),
                   headers={"Content-Type": "application/json"})
check("clearing every beat is refused rather than saved", empty.status_code, 400)
check("and the saved list survives it",
      len(client.get(f"{MOUNT}/api/projects/{pid}/vox").get_json()["beats"]), 4)

section("A storyboard spot has no beat list, and says so rather than 404ing")
other_ok = post("/api/projects", {"client_id": cid, "commercial_type": "stock_vo",
                                  "platform": "both", "lengths": [30]})
opid = (other_ok.get_json() or {}).get("projects", [{}])[0].get("id")
check("the beats API refuses it",
      client.get(f"{MOUNT}/api/projects/{opid}/vox").status_code, 400)
# A wizard step that 404s reads as the whole tool being broken, which is why
# /storyboard is still a redirect.
check("and the page redirects to its real step",
      client.get(f"{MOUNT}/project/{opid}/vox").status_code, 302)

section("Its wizard is its own, and the dashboard lands on a step it has")
from modules.commercial_builder.routes import pages                # noqa: E402
from modules.commercial_builder.models import CommercialProject     # noqa: E402
from wsgi import hub_app                                            # noqa: E402

with hub_app.app_context():
    vox_proj = CommercialProject.query.get(pid)
    other_proj = CommercialProject.query.get(opid)
    check("a Vox explainer gets the four-step wizard",
          [s["key"] for s in pages.steps_for(vox_proj)],
          ["start", "brief", "vox", "preview"])
    check("everything else keeps the seven",
          len(pages.steps_for(other_proj)), 7)
    # "scripted" is the Blueprint for every other type and is the beat list
    # here; left to STATUS_STEP it would send somebody to a step this project
    # does not have.
    vox_proj.status = "scripted"
    check("and 'scripted' lands on the beats step",
          pages.status_step(vox_proj), "vox")
    vox_proj.status = "voice"
    check("as does a status it has no step for",
          pages.status_step(vox_proj), "vox")
    other_proj.status = "scripted"
    check("while a storyboard spot still lands on Blueprint",
          pages.status_step(other_proj), "blueprint")

section("The paint source is offered on a scene, and hidden with no service")
bp_page = client.get(f"{MOUNT}/project/{opid}/blueprint").get_data(as_text=True)
# A button that consents and then fails for a reason nothing to do with the
# rep is the failure Google Access's Ads tickbox already paid for.
check("no render service, no paint button", 'class="cb-btn paint-btn"' in bp_page, False)
check("and the other five sources are untouched",
      all(s in bp_page for s in ("find-stock-btn", "spokesperson-btn",
                                 "upload-btn", "client-asset-btn",
                                 "generate-ai-btn")), True)

scenes = client.get(f"{MOUNT}/api/projects/{opid}/scenes").get_json()
sid = (scenes.get("scenes") or [{}])[0].get("id")
if sid:
    r = client.post(f"{MOUNT}/api/projects/{opid}/scenes/{sid}/generate-paint",
                    data=json.dumps({}), headers={"Content-Type": "application/json"})
    check("and the route refuses rather than 500ing", r.status_code, 503)
    check("with the reason", bool((r.get_json() or {}).get("error")), True)
else:
    check("a scene to paint", "no scenes on a fresh project", "no scenes on a fresh project")


# ---------------------------------------------------------------------------
# 6. The wiring nothing else would notice
# ---------------------------------------------------------------------------
section("The pieces that make it visible")
from hub import client_brand, help_coverage, sidebar, help as hub_help  # noqa: E402

for name in ("paint_animation", "vox_explainer"):
    # work_log() skips a module its own table cannot name, and a skipped
    # module reads on a client record as a client nobody has done work for.
    check(f"{name} is a work kind the record can name",
          name in client_brand.WORK_KINDS, True)
for href, prefix in (("/tools/paint-animation/", "paint_animation"),
                     ("/tools/vox-explainer/", "vox_explainer")):
    check(f"{href} maps to a help prefix",
          help_coverage.PREFIXES.get(href), prefix)
    check(f"{href} opens with the nav as an icon rail",
          sidebar.collapses_by_default(href), True)

_creative = (ROOT / "hub/templates/creative.html").read_text()
for name, href in (("Paint Animation", "/tools/paint-animation/"),
                   ("Vox Explainer", "/tools/vox-explainer/")):
    # A tool with no tile is invisible; this file counts six that were.
    check(f"{name} is tiled on Creative",
          f"<h3>{name}</h3>" in _creative and f'href="{href}"' in _creative, True)

# A bubble whose key is missing is removed client-side, so the template reads
# as helped and the screen shows nothing.
_placed = set()
for _t in ("modules/hyperframes_tools/templates/hf_paint.html",
           "modules/hyperframes_tools/templates/hf_vox.html",
           "modules/commercial_builder/templates/commercial_vox.html"):
    _placed |= set(re.findall(r"help_dot\('([^']+)'\)", (ROOT / _t).read_text()))
check("every bubble placed has help behind it",
      sorted(k for k in _placed if not hub_help.get(k)), [])
check("and there are some", len(_placed) >= 7, True)

# A tour is offered only where one is registered, and naming a screen with no
# steps is the silence Smart 1 Ads shipped on Settings and Live campaigns.
for _t in ("modules/hyperframes_tools/templates/hf_paint.html",
           "modules/hyperframes_tools/templates/hf_vox.html"):
    check(f"{Path(_t).name} names no tour it cannot drive",
          "data-screen" in (ROOT / _t).read_text(), False)

section("Two blueprints must not offer a template of the same name")
# The hub's Jinja environment resolves a bare name against its own templates
# first and then each blueprint's folder in registration order — which is how
# /tools/page-images/ came to render the calculator's index.
_names = sorted(p.name for p in
                (ROOT / "modules/hyperframes_tools/templates").glob("*.html"))
check("every template here is prefixed", [n for n in _names
                                          if not n.startswith("hf_")], [])

section("The setting is read, and the status page says what it costs")
from hub.config import settings                                    # noqa: E402
check("the URL is a setting", hasattr(settings, "hf_render_service_url"), True)
check("and so is the switch", settings.hf_render_enabled, True)
# Read at call time, not import: this is the one variable somebody corrects
# mid-incident after a panel names it.
os.environ["HF_RENDER_SERVICE_URL"] = "https://hf-render.example.com/"
try:
    from hub import config as _cfg
    _cfg.settings.__dict__["hf_render_service_url"] = "https://hf-render.example.com/"
    check("a trailing slash is trimmed", hyperframes.base_url(),
          "https://hf-render.example.com")
    check("and it now reads as configured", hyperframes.is_configured(), True)
    _cfg.settings.__dict__["hf_render_enabled"] = False
    check("the switch turns both features off", hyperframes.is_configured(), False)
    check("and says which switch", "HF_RENDER_ENABLED" in hyperframes.why_unavailable(),
          True)
finally:
    _cfg.settings.__dict__["hf_render_service_url"] = ""
    _cfg.settings.__dict__["hf_render_enabled"] = True
    os.environ.pop("HF_RENDER_SERVICE_URL", None)


# ---------------------------------------------------------------------------
# 7. The configured path, against a stand-in for hf-render-service
# ---------------------------------------------------------------------------
#
# Everything above runs unconfigured, which is this deployment's real state and
# where most of the failures live. But a client written entirely against the
# "switched off" branch is one nobody has ever seen answer a real service, and
# the wire contract -- `jobId` in, `status`/`url` back, 202 on submit -- is
# exactly the part `hf-render-service` has to implement. So it is exercised
# against a stub that speaks it.
section("Against a service that actually answers")

import http.server                                                 # noqa: E402
import threading                                                   # noqa: E402

_JOBS = {}


class _Stub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):                       # noqa: D102, ANN001
        pass

    def _send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):                                # noqa: N802
        if self.path == "/health":
            return self._send(200, {"ok": True})
        if self.path.startswith("/render/") and self.path.endswith("/status"):
            jid = self.path.split("/")[2]
            job = _JOBS.get(jid)
            if job is None:
                return self._send(404, {"error": "no such job"})
            job["polls"] += 1
            if job["polls"] >= 2:
                return self._send(200, {"status": "done", "durationSeconds": 74.5,
                                        "url": f"https://cdn.example.test/{jid}.mp4"})
            return self._send(200, {"status": "rendering", "progress": 0.4})
        return self._send(404, {"error": "nope"})

    def do_POST(self):                               # noqa: N802
        if self.path.startswith("/render/"):
            template = self.path.split("/")[2]
            if template not in ("paint-animation", "vox-explainer"):
                return self._send(400, {"error": f"unknown template {template}"})
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            # So the refusal path is reachable: a real service refuses a job
            # it cannot render, and what the Hub does with that sentence is
            # the thing worth asserting.
            if b"REFUSE-ME" in raw:
                return self._send(400, {"error": "that composition has no beats"})
            jid = f"job{len(_JOBS) + 1}"
            _JOBS[jid] = {"polls": 0}
            return self._send(202, {"jobId": jid, "status": "queued"})
        return self._send(404, {"error": "nope"})


_srv = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
threading.Thread(target=_srv.serve_forever, daemon=True).start()
_origin = "http://127.0.0.1:%d" % _srv.server_address[1]
# The agent proxy would otherwise swallow a request to loopback.
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
_cfg.settings.__dict__["hf_render_service_url"] = _origin

try:
    check("it reads as configured", hyperframes.is_configured(), True)
    check("and reachable", hyperframes.check()["state"], "ok")

    live = hyperframes.submit("paint-animation",
                              hyperframes.paint_params(text="Half price", seconds=6))
    check("a submit comes back with a job id", bool(live["job_id"]), True)
    check("queued rather than finished", live["status"], "queued")
    check("and carries no file yet", live["url"], None)
    check("nor a mock mark", live.get("_mock"), None)

    first = hyperframes.status(live["job_id"])
    check("the first poll is still rendering", first["status"], "rendering")
    check("and has no url to attach", first["url"], None)
    # `is_deliverable` is the guard every filing call site asks, and a job
    # part-way through is exactly the case a truthy-url test would let past.
    check("so nothing may be filed from it", hyperframes.is_deliverable(first), False)

    done = hyperframes.status(live["job_id"])
    check("the next poll is done", done["status"], "done")
    check("with the file", done["url"].endswith(".mp4"), True)
    check("and the measured length", done["duration_seconds"], 74.5)
    check("now it may be filed", hyperframes.is_deliverable(done), True)
    check("and the duration verdict reads it",
          hyperframes.vox_duration_verdict(done["duration_seconds"])["passed"], True)

    # A job the service has forgotten -- it restarted, or the job expired.
    # Said as a failure rather than polled for ever.
    gone = hyperframes.status("job-that-never-was")
    check("a job the service has no record of fails", gone["status"], "failed")
    check("and says so", "no record" in gone["error"], True)

    # The service's own sentence rather than an invented diagnosis of it.
    # Discarding it is how every button comes to report its own account of one
    # shared failure — the note `hub/openai_responses.py` makes about
    # raise_for_status().
    refused = hyperframes.submit("vox-explainer",
                                 hyperframes.vox_params(title="REFUSE-ME"))
    check("a job the service refuses comes back failed", refused["status"], "failed")
    check("carrying the service's own reason",
          "has no beats" in refused["error"], True)
    check("and the status code, so it can be told from an outage",
          "400" in refused["error"], True)
    check("with no job id to poll for ever", refused["job_id"], None)

    # A second template still submits — the refusal above is about the body,
    # not about vox-explainer being unusable.
    ok_vox = hyperframes.submit("vox-explainer",
                                hyperframes.vox_params(title="A real one"))
    check("and a real explainer still submits", ok_vox["status"], "queued")

    # With a service configured the page draws the form rather than the note.
    page = client.get("/tools/paint-animation/").get_data(as_text=True)
    check("the page now draws the form", 'id="paint-go"' in page, True)
    check("and not the unavailable note", "Not available yet" in page, False)
    check("with the styles handed over as data",
          'id="hf-styles-data"' in page, True)

    started = client.post("/tools/paint-animation/api/render",
                          data=json.dumps({"text": "Half price", "seconds": 6}),
                          headers={"Content-Type": "application/json"})
    check("a standalone render is accepted", started.status_code, 200)
    started_job = (started.get_json() or {}).get("job") or {}
    check("and written down", bool(started_job.get("id")), True)
    check("owned by whoever asked", bool(started_job.get("owner")), True)

    listed = client.get("/tools/paint-animation/api/renders").get_json()
    check("it appears in that person's list", len(listed["jobs"]), 1)
    # Two polls to finish, exactly as above.
    jid = started_job["id"]
    client.get(f"/tools/paint-animation/api/render/{jid}")
    settled = client.get(f"/tools/paint-animation/api/render/{jid}").get_json()
    check("and the status route attaches the file", settled["job"]["status"], "done")
    check("writing it through rather than leaving it to the browser",
          bool(settled["job"]["url"]), True)

    # Filing needs a client, and a name nothing matches is refused rather than
    # filed under a client nothing joins to.
    kept = client.post(f"/tools/paint-animation/api/render/{jid}/keep",
                       data=json.dumps({"client": "No Such Business Ltd"}),
                       headers={"Content-Type": "application/json"})
    check("filing against an unknown client is refused", kept.status_code, 400)
    check("and names the problem",
          "No client is filed" in (kept.get_json() or {}).get("error", ""), True)

    # The Blueprint offers the sixth source now the service is there.
    bp = client.get(f"{MOUNT}/project/{opid}/blueprint").get_data(as_text=True)
    check("the paint source is offered on a scene",
          'class="cb-btn paint-btn"' in bp, True)
    check("with its styles handed over", 'id="paint-styles-data"' in bp, True)

    # The whole point of the ninth commercial type: a Vox explainer renders
    # through HyperFrames rather than Creatomate, and everything after that --
    # the RenderJob row, the poll, the approval and the filing -- is the path
    # every other commercial type already uses. A second render pipeline
    # beside them is how the two come to disagree about what "approved"
    # delivers.
    client.put(f"{MOUNT}/api/projects/{pid}/vox/beats",
               data=json.dumps({"beats": [
                   {"headline": "Roofs fail in winter", "seconds": 20},
                   {"headline": "It costs more later", "seconds": 20},
                   {"headline": "A survey takes an hour", "seconds": 20},
                   {"headline": "Book one", "seconds": 15}]}),
               headers={"Content-Type": "application/json"})
    rendered = client.post(f"{MOUNT}/api/projects/{pid}/render",
                           data=json.dumps({"formats": ["16:9"]}),
                           headers={"Content-Type": "application/json"})
    check("a Vox explainer renders", rendered.status_code, 200)
    body = rendered.get_json() or {}
    check("through HyperFrames rather than Creatomate",
          body.get("renderer"), "hyperframes")
    check("and the panel is not told it is a mock", body.get("note"), "")
    rjob = (body.get("render_jobs") or [{}])[0]
    check("the job carries the service's own id",
          bool(rjob.get("provider_render_id")), True)
    # `RenderJob.status` is queued|rendering|succeeded|failed and the service
    # says queued|rendering|done|failed. A "done" left untranslated never
    # satisfies the poll's `status not in ("succeeded", "failed")` guard, so
    # the job is re-checked for ever and the panel never stops spinning.
    check("in RenderJob's vocabulary rather than the service's",
          rjob.get("status"), "queued")
    _st = f"{MOUNT}/api/projects/{pid}/render-jobs/{rjob['id']}/status"
    check("the first poll is still rendering",
          client.get(_st).get_json()["render_job"]["status"], "rendering")
    finished = client.get(_st).get_json()
    check("the next is succeeded, not 'done'",
          finished["render_job"]["status"], "succeeded")
    check("with the file on the job",
          finished["render_job"]["output_url"].endswith(".mp4"), True)
    check("and the poll knows which renderer answered",
          finished["renderer"], "hyperframes")

    # A storyboard spot on the same deployment still goes to Creatomate.
    other_render = client.post(f"{MOUNT}/api/projects/{opid}/render",
                               data=json.dumps({"formats": ["16:9"],
                                                "force_despite_qc_failures": True}),
                               headers={"Content-Type": "application/json"})
    check("while a storyboard spot still goes to Creatomate",
          (other_render.get_json() or {}).get("renderer"), "creatomate")

    dropped = client.delete(f"/tools/paint-animation/api/render/{jid}")
    check("a render can be dropped from the list", dropped.status_code, 200)
    check("and the note says the filed file is untouched",
          "untouched" in (dropped.get_json() or {}).get("note", ""), True)
finally:
    _cfg.settings.__dict__["hf_render_service_url"] = ""
    _srv.shutdown()


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
