"""Commercial Builder — the HeyGen spokesperson path.

    python3 test_commercial_heygen.py

Same shape as test_landing_maker.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one. It runs with NO HeyGen key, which is the mock path
— and mock mode producing no video is itself one of the things asserted,
because a mock that claims a finished clip is indistinguishable from a live
generation that silently failed.

## Why this file exists

A HeyGen clip takes minutes to render. The first version of this integration
fired a job, returned "processing", and nothing ever asked again: the scene
kept `asset_type="spokesperson"` with an empty `asset_url` forever, QC had no
check that a scene owned an asset at all, and `creatomate_service` then built
an element with no `source` — a blank segment in a commercial a client
received, with nothing anywhere reading as an error.

Every check below guards one step of that chain:

  1. a clip is generated at the PROJECT's frame, not a hard-coded 9:16
  2. the background HeyGen is asked for is one HeyGen actually accepts
  3. polling the status WRITES THROUGH, so closing the tab doesn't lose a job
  4. QC refuses to render a scene whose presenter is pending, failed or mock
  5. a keyed presenter is composited OVER its footage, on its own layer
  6. the key colour the clip was made against is the one used to key it
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1heygen_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["SECRET_KEY"] = "heygen-test-secret"
os.environ["PANEL_PASSWORD"] = "heygen-test-password"
for _k in ("HEYGEN_API", "HEYGEN_API_KEY", "HEYGEN_KEY", "SMART1_TALENT_AVATARS"):
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


from modules.commercial_builder import config as cb_config              # noqa: E402
from modules.commercial_builder.services import heygen_service          # noqa: E402
from modules.commercial_builder.services import qc_service              # noqa: E402
from modules.commercial_builder.services import creatomate_service      # noqa: E402


# ------------------------------------------------------- 1. the frame
section("A clip is generated at the project's frame")

# The whole reason this is derived rather than constant: the dimension used to
# be hard-coded to 1080x1920, so a 16:9 CTV spot — the format this module
# mostly builds — came back with a vertical presenter in it.
check("16:9 asks for a landscape frame", heygen_service.clip_dimensions("16:9"), (1920, 1080))
check("9:16 asks for a vertical frame", heygen_service.clip_dimensions("9:16"), (1080, 1920))
check("1:1 asks for a square frame", heygen_service.clip_dimensions("1:1"), (1080, 1080))
check("an unknown format falls back to 16:9, not to vertical",
      heygen_service.clip_dimensions("banana"), (1920, 1080))

job = heygen_service.generate_spokesperson_clip("av_1", "Call Riverside today.", format_id="16:9")
check("a generated job carries the frame it was made at",
      (job["width"], job["height"]), (1920, 1080))

for fmt in [f["id"] for f in cb_config.OUTPUT_FORMATS]:
    made = heygen_service.generate_spokesperson_clip("av_1", "Copy.", format_id=fmt)
    check(f"every output format is generatable — {fmt}",
          (made["width"], made["height"]), heygen_service.clip_dimensions(fmt))


# -------------------------------------------------- 2. the background
section("The background is one HeyGen accepts")

# HeyGen v2 takes color/image/video. The old code sent {"type": "transparent"}
# and it was the DEFAULT argument, so the default path was the failing one.
over, keyed = heygen_service.background_for(True)
solid, not_keyed = heygen_service.background_for(False)
check("a presenter over footage gets a colour matte", over["type"], "color")
check("and that colour is the shared chroma constant",
      over["value"], cb_config.CHROMA_KEY_COLOR)
check("and is marked as needing a key", keyed, True)
check("a full-frame presenter gets a solid backdrop", solid["type"], "color")
check("and is not marked for keying", not_keyed, False)
check("neither asks for a background type HeyGen has no name for",
      {over["type"], solid["type"]} <= {"color", "image", "video"}, True)


# ----------------------------------------------- 3. absent, not zero
section("Mock mode says it produced nothing")

check("with no key set the service is not live", heygen_service.is_live(), False)
mock = heygen_service.generate_spokesperson_clip("av_1", "Copy.", format_id="16:9")
check("a mock job is flagged as mock", mock.get("_mock"), True)
check("and reports no video URL rather than a plausible one",
      mock.get("video_url"), None)

# A scene with no narration is a request HeyGen rejects. The reason it gives
# back reads like an outage, so the service names the real problem itself.
empty = heygen_service.generate_spokesperson_clip("av_1", "   ", format_id="16:9")
check("an empty narration fails before it reaches the provider", empty["status"], "failed")
check("and says what is actually wrong", "no narration" in empty["error"], True)

check("a job with no id reports failed, not processing",
      heygen_service.check_status(None)["status"], "failed")


# ------------------------------------------------ 4. the talent roster
section("The talent roster says which of it can be used")

presenters = heygen_service.list_presenters()
talent = presenters["smart1_talent"]
check("the roster is offered", len(talent), len(cb_config.SMART1_TALENT_ROSTER))
check("with no avatar ids set, none of it is available",
      [p["available"] for p in talent], [False] * len(talent))
check("and each says why", all(p.get("unavailable_reason") for p in talent), True)

os.environ["SMART1_TALENT_AVATARS"] = json.dumps({"sarah": "heygen_sarah_01"})
linked = heygen_service.list_presenters()["smart1_talent"]
by_id = {p["id"]: p for p in linked}
check("linking one avatar makes that person available", by_id["sarah"]["available"], True)
check("and hands the picker the real HeyGen id", by_id["sarah"]["avatar_id"], "heygen_sarah_01")
check("the rest stay unavailable", by_id["mike"]["available"], False)
os.environ["SMART1_TALENT_AVATARS"] = "{not json"
check("a malformed override is ignored, not raised",
      heygen_service.list_presenters()["smart1_talent"][0]["available"], False)
os.environ.pop("SMART1_TALENT_AVATARS")

check("a client's own avatar is offered when they have one",
      heygen_service.list_presenters("client_av_9")["client_avatar"]["avatar_id"], "client_av_9")


# -------------------------------------------------------- 5. QC gates
section("QC refuses to render a scene that has nothing on it")


def scene(**kw):
    base = {"id": 1, "start": 0.0, "end": 10.0, "narration": "Copy.",
            "asset_url": "https://cdn.example/a.mp4", "asset_type": "stock",
            "asset_meta": {}, "is_cta": False}
    base.update(kw)
    return base


def assets(scenes):
    return qc_service.run_qc({"length_seconds": 30}, {}, scenes)["scene_assets"]


check("a fully-dressed storyboard passes",
      assets([scene(), scene(id=2)])["passed"], True)

# The exact shape the old code produced: marked spokesperson, no URL, job
# still running. It passed every check there was, and rendered as nothing.
pending = scene(id=3, asset_url=None, asset_type="spokesperson",
                asset_meta={"heygen_job": {"job_id": "hg_1", "status": "processing"}})
check("a clip still generating blocks the render", assets([scene(), pending])["passed"], False)
check("and names the scene it is waiting on", "scene(s) 2" in assets([scene(), pending])["message"], True)

failed = scene(id=4, asset_url=None, asset_type="spokesperson",
               asset_meta={"heygen_job": {"job_id": None, "status": "failed", "error": "boom"}})
check("a failed clip blocks the render", assets([failed])["passed"], False)

mocked = scene(id=5, asset_url=None, asset_type="spokesperson",
               asset_meta={"heygen_job": {"job_id": "mock_1", "status": "completed", "_mock": True}})
check("a mock clip blocks the render — no video exists", assets([mocked])["passed"], False)
check("and says so rather than reporting it complete",
      "mock mode" in assets([mocked])["message"], True)

check("a scene with no asset at all blocks the render",
      assets([scene(id=6, asset_url=None, asset_type=None)])["passed"], False)
check("but a text-only CTA end card is allowed to have no footage",
      assets([scene(id=7, asset_url=None, asset_type="cta", is_cta=True)])["passed"], True)

attached = scene(id=8, asset_url="https://res.cloudinary.com/x/spokesperson.mp4",
                 asset_type="spokesperson",
                 asset_meta={"heygen_job": {"job_id": "hg_2", "status": "completed"},
                             "spokesperson_url": "https://res.cloudinary.com/x/spokesperson.mp4"})
check("an attached clip passes", assets([attached])["passed"], True)

# The gate is only worth anything if the render actually consults it.
check("the whole QC run fails when one scene is unfinished",
      qc_service.run_qc({"length_seconds": 30}, {}, [pending])["_all_passed"], False)


# -------------------------------------------- 6. the presenter renders
section("A keyed presenter is composited over its footage")

keyed_scene = scene(
    id=9, asset_url="https://cdn.example/street.mp4", asset_type="stock",
    asset_meta={"spokesperson_url": "https://res.cloudinary.com/x/pres.mp4",
                "spokesperson_over_footage": True, "chroma_key": True,
                "chroma_key_color": cb_config.CHROMA_KEY_COLOR})
source = creatomate_service.build_source(
    {"length_seconds": 10, "platform": "both", "cta": {}}, [keyed_scene], "16:9")
ids = [e["id"] for e in source["elements"]]
check("the scene's own footage is still in the render", "scene_9" in ids, True)
check("and the presenter is a SECOND element, not a replacement",
      "presenter_9" in ids, True)

presenter = next(e for e in source["elements"] if e["id"] == "presenter_9")
footage = next(e for e in source["elements"] if e["id"] == "scene_9")
check("the presenter sits above the footage it is keyed over",
      presenter["track"] > footage["track"], True)
check("and below the persistent logo bug",
      presenter["track"] < creatomate_service.TRACK_LOGO_BUG, True)
check("it runs for exactly the scene's duration",
      presenter["duration"], round(keyed_scene["end"] - keyed_scene["start"], 2))
check("it is keyed", "chroma_key" in presenter, True)
# The colour the clip was generated against and the colour used to key it are
# the same constant. Drift here is a green rectangle in a client's commercial.
check("against the same colour it was generated against",
      presenter["chroma_key"]["color"], cb_config.CHROMA_KEY_COLOR)

# A full-frame presenter IS the scene, so a second element would double it up.
full_frame = scene(id=10, asset_url="https://res.cloudinary.com/x/pres.mp4",
                   asset_type="spokesperson",
                   asset_meta={"spokesperson_url": "https://res.cloudinary.com/x/pres.mp4",
                               "spokesperson_over_footage": False})
ff = creatomate_service.build_source({"length_seconds": 10, "cta": {}}, [full_frame], "16:9")
check("a full-frame presenter gets no duplicate overlay element",
      [e["id"] for e in ff["elements"]], ["scene_10"])
check("and is typed as video even though the URL carries no suffix",
      creatomate_service._element_type(
          scene(id=11, asset_url="https://res.cloudinary.com/x/upload/v1/pres",
                asset_type="spokesperson")), "video")

# Tracks are layers only when they differ; two things sharing a track play one
# after the other, which would run the presenter after the spot had ended.
tracks = {creatomate_service.TRACK_SCENES, creatomate_service.TRACK_VOICE,
          creatomate_service.TRACK_MUSIC, creatomate_service.TRACK_PRESENTER,
          creatomate_service.TRACK_LOGO_BUG}
check("every layer has a track of its own", len(tracks), 5)


# ------------------------------------------- 7. the write-through path
section("Polling the status finishes the job")

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "app.sqlite3")
from werkzeug.test import Client                                        # noqa: E402
from wsgi import application                                            # noqa: E402
from hub import auth                                                    # noqa: E402
from hub.extensions import db                                           # noqa: E402
from modules.commercial_builder.models import (Client as CBClient,      # noqa: E402
                                               CommercialProject, Scene)

http = Client(application)
http.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Test"), domain="localhost")

# The tool is staff-only and reads client data, so it must be behind the login.
# It is blueprint-registered, not dispatcher-mounted, so wsgi.py's AuthGuard
# never sees it — it answered 200 to anyone with the URL until the blueprint
# grew a guard of its own. These four are the shapes that matters in.
anon = Client(application)
check("the storyboard is gated",
      anon.get("/tools/commercial-builder/").status_code, 302)
check("a JSON route answers 401, not an HTML login page a fetch() cannot read",
      anon.get("/tools/commercial-builder/api/presenters").status_code, 401)
check("a nested route module is covered too",
      anon.get("/tools/commercial-builder/api/clients").status_code, 401)
check("and a POST is refused rather than redirected",
      anon.post("/tools/commercial-builder/api/clients", json={}).status_code, 401)

import wsgi                                                             # noqa: E402
with wsgi.hub_app.app_context():
    db.create_all()
    cb_client = CBClient(name="Riverside HVAC", slug="riverside-hvac")
    db.session.add(cb_client)
    db.session.commit()
    project = CommercialProject(client_id=cb_client.id, title="Summer tune-up",
                                length_seconds=30, status="storyboard")
    project.formats = ["16:9"]
    db.session.add(project)
    db.session.commit()
    sc = Scene(project_id=project.id, order_index=0, start=0.0, end=10.0,
               narration="Call Riverside today for a summer tune-up.")
    db.session.add(sc)
    db.session.commit()
    project_id, scene_id, client_id = project.id, sc.id, cb_client.id

base = f"/tools/commercial-builder/api/projects/{project_id}/scenes/{scene_id}"

r = http.get(f"/tools/commercial-builder/api/presenters?client_id={client_id}")
check("the picker answers", r.status_code, 200)
check("and reports it is running in mock mode", r.get_json()["live"], False)

r = http.get(f"{base}/spokesperson/status")
check("a scene with no job says so rather than inventing one", r.status_code, 404)

r = http.post(f"{base}/spokesperson", json={"avatar_id": "av_1"})
check("generating a clip is accepted", r.status_code, 200)
body = r.get_json()
check("the job is recorded against the scene",
      bool(body["scene"]["asset_meta"]["heygen_job"]), True)
check("at the project's own format", body["job"]["format_id"], "16:9")
check("and the scene is NOT given a URL it does not have yet",
      body["scene"]["asset_url"], None)

r = http.post(f"{base}/spokesperson", json={})
check("generating without an avatar is refused", r.status_code, 400)

# The caller's mistake and the provider's failure are different answers: a 502
# on an empty narration would send someone looking at HeyGen's status page.
with wsgi.hub_app.app_context():
    blank = Scene(project_id=project_id, order_index=1, start=10.0, end=20.0, narration="  ")
    db.session.add(blank)
    db.session.commit()
    blank_id = blank.id
r = http.post(f"/tools/commercial-builder/api/projects/{project_id}/scenes/{blank_id}"
              f"/spokesperson", json={"avatar_id": "av_1"})
check("a scene with no narration is a 400, not a provider error", r.status_code, 400)
check("and says which end the problem is",
      "no narration" in r.get_json()["error"], True)

# The write-through. In mock mode there is no video, so the honest outcome is
# "completed, nothing attached" — and QC above refuses to render that.
r = http.get(f"{base}/spokesperson/status")
check("the status route answers", r.status_code, 200)
check("mock mode attaches nothing", r.get_json()["attached"], False)
check("and says it was mock", r.get_json()["mock"], True)

# The same route with a real URL is what a finished live job takes: applying
# it must survive a reload, which means it is on the row, not in the browser.
r = http.post(f"{base}/spokesperson/apply",
              json={"video_url": "https://res.cloudinary.com/x/pres.mp4"})
check("a recovered clip can be attached by hand", r.status_code, 200)
with wsgi.hub_app.app_context():
    reloaded = db.session.get(Scene, scene_id)
    check("the clip is on the scene row, not only in the response",
          reloaded.asset_url, "https://res.cloudinary.com/x/pres.mp4")
    check("and the meta records it for QC and the compositor",
          (reloaded.asset_meta or {}).get("spokesperson_url"),
          "https://res.cloudinary.com/x/pres.mp4")

r = http.get(f"{base}/spokesperson/status")
check("polling an attached scene reports completed without re-asking HeyGen",
      (r.get_json()["status"], r.get_json()["attached"]), ("completed", True))


# ----------------------------------------------- 8. the key is configured
section("The key is read through the Hub's settings")

from hub.config import Settings                                         # noqa: E402

for spelling in ("HEYGEN_API", "HEYGEN_API_KEY", "HEYGEN_KEY"):
    os.environ[spelling] = "k-" + spelling
    check(f"the service reads {spelling}", heygen_service.is_live(), True)
    os.environ.pop(spelling)
check("and is not live with none of them set", heygen_service.is_live(), False)

os.environ["HEYGEN_API"] = "k-1"
check("hub/config.py accepts the deployment's spelling", Settings().heygen_key, "k-1")
check("and reports HeyGen on /diagnostics",
      any(row["name"] == "HeyGen" for row in Settings().status()), True)
os.environ.pop("HEYGEN_API")


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
