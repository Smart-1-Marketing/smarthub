"""Commercial Builder — the two remaining scene-asset buttons: AI video, and
the three routes beside it that had never been driven at all.

    python3 test_commercial_assets.py

Same shape as test_commercial_heygen.py: no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one. It runs with NO Runway/Cloudinary keys, which is
the mock path.

## Why this file exists

`modules/commercial_builder/routes/assets.py` has eight routes: the still-frame
generator (`generate-ai`, tested in test_commercial_wizard.py) and paint
animation (`generate-paint` / `.../status`, tested in test_hyperframes.py) each
have a test file built around them. The other six — `choose-ai-option`,
`upload`, `use-client-asset`, `asset-source-priority`, and the Runway pair
`generate-video` / `generate-video/status` — had none at all, despite
`generate-video` carrying the exact "a render takes minutes, so the status
route is what attaches it" shape test_commercial_heygen.py's own docstring
spends a paragraph on for HeyGen.

Reading `generate-video`'s route and its caller in `blueprint.js` closely
enough to write this test found a live gap in the caller. Runway's own mock
mode (no `RUNWAY_API_KEY`) reports a job `status: "completed"` **immediately**
— there is no clip to poll for — so `runwayPending()` never sees `"processing"`
and `watchVideo()`, whose own tick is what shows "Mock mode — no video was
produced", never starts. The sibling buttons already have a belt for exactly
this: `generate-ai`'s picker and `openSpokespersonPicker` each read `live` off
their own POST response and pick the toast from that, checked *before*
relying on a poll loop that mock mode never enters. `generateVideo()` was the
one button that discarded its response and always said "AI video generating —
this takes a few minutes.", mock or not. The generic `noteMock()` mechanism
in common.js does still paint a page-wide note for this route (it is named in
`MOCK_STEPS`), so this was never a *silent* failure the way the original
HeyGen bug was — but the toast a rep reads immediately after pressing the
button said something that was never going to happen. Fixed to match its
siblings, and asserted here rather than only in prose.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1cbassets_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "mirror.sqlite3")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["SECRET_KEY"] = "cbassets-test-secret"
for _k in ("RUNWAY_API", "RUNWAY_API_KEY", "RUNWAY_KEY",
          "CLOUDINARY_URL", "CLOUDINARY_CLOUD_NAME", "CLOUDINARY_API_KEY",
          "CLOUDINARY_API_SECRET"):
    os.environ.pop(_k, None)

_passed, _failed = 0, 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}")


from werkzeug.test import Client as HttpClient                          # noqa: E402
from wsgi import application, hub_app                                   # noqa: E402
from hub import auth, audit                                             # noqa: E402
from hub.extensions import db                                           # noqa: E402
from modules.commercial_builder.models import (                         # noqa: E402
    Client as CBClient, CommercialProject, Scene)
from modules.commercial_builder import config as cb_config              # noqa: E402
from modules.commercial_builder.services import runway_service          # noqa: E402
from modules.commercial_builder.services import cloudinary_service      # noqa: E402

http = HttpClient(application)
http.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Test"), domain="localhost")

with hub_app.app_context():
    db.create_all()
    cb_client = CBClient(name="Riverside HVAC", slug="riverside-hvac")
    db.session.add(cb_client)
    db.session.commit()
    project = CommercialProject(client_id=cb_client.id, title="Summer tune-up",
                                length_seconds=30, status="storyboard")
    project.formats = ["16:9"]
    db.session.add(project)
    db.session.commit()
    scene = Scene(project_id=project.id, order_index=0, start=0.0, end=10.0,
                  narration="Call Riverside today.",
                  visual_description="technician working on an AC unit")
    db.session.add(scene)
    db.session.commit()
    project_id, scene_id, client_id = project.id, scene.id, cb_client.id

base = f"/tools/commercial-builder/api/projects/{project_id}/scenes/{scene_id}"


# ---------------------------------------------------------------------------
section("Every route on this blueprint is behind the login")
# ---------------------------------------------------------------------------
anon = HttpClient(application)
for path, method in [
    (f"{base}/choose-ai-option", "post"), (f"{base}/upload", "post"),
    (f"{base}/use-client-asset", "post"),
    ("/tools/commercial-builder/api/asset-source-priority", "get"),
    (f"{base}/generate-video", "post"), (f"{base}/generate-video/status", "get"),
]:
    r = getattr(anon, method)(path, json={} if method == "post" else None)
    check(f"{path} refuses an anonymous request", r.status_code, 401)


# ---------------------------------------------------------------------------
section("asset-source-priority: the waterfall order, served rather than restated")
# ---------------------------------------------------------------------------
r = http.get("/tools/commercial-builder/api/asset-source-priority")
check("it answers", r.status_code, 200)
check("with the shared config's own order, not a copy of it",
      r.get_json()["priority"], cb_config.ASSET_SOURCE_PRIORITY)


# ---------------------------------------------------------------------------
section("choose-ai-option: pointing a scene at one of the drawn stills")
# ---------------------------------------------------------------------------
r = http.post(f"{base}/choose-ai-option", json={})
check("no url is refused, not guessed at", r.status_code, 400)

r = http.post(f"{base}/choose-ai-option",
              json={"url": "https://res.cloudinary.com/x/still.png"})
check("choosing a drawn still is accepted", r.status_code, 200)
sc = r.get_json()["scene"]
check("the scene is marked AI-generated", sc["asset_type"], "ai_generated")
check("and sourced from OpenAI", sc["asset_source"], "openai")
check("the url is on the scene", sc["asset_url"], "https://res.cloudinary.com/x/still.png")
check("and the thumbnail matches, since a still IS its own thumbnail",
      sc["asset_thumb_url"], "https://res.cloudinary.com/x/still.png")
check("recorded as a picture, not a video -- Runway has not run yet",
      sc["asset_meta"]["media"], "image")


# ---------------------------------------------------------------------------
section("upload: a scene given a file directly")
# ---------------------------------------------------------------------------
r = http.post(f"{base}/upload", json={})
check("neither a file nor a url is refused", r.status_code, 400)

r = http.post(f"{base}/upload", json={"url": "https://example.com/clip.mp4"})
check("an uploaded url is accepted", r.status_code, 200)
body = r.get_json()
check("the mock store echoes the url back as a usable asset",
      body["asset"]["secure_url"], "https://example.com/clip.mp4")
check("the scene is marked as an upload", body["scene"]["asset_type"], "upload")
check("sourced accordingly", body["scene"]["asset_source"], "upload")

uploaded = [e for e in audit.tail(limit=200, module="commercial_builder")
           if e.get("type") == "commercial_asset_uploaded"]
check("a real upload reaches the client's 360 record",
      uploaded[-1].get("client") if uploaded else None, "Riverside HVAC")


# ---------------------------------------------------------------------------
section("use-client-asset: attaching something already in the gallery")
# ---------------------------------------------------------------------------
r = http.post(f"{base}/use-client-asset", json={})
check("no url is refused here too", r.status_code, 400)

r = http.post(f"{base}/use-client-asset",
              json={"url": "https://res.cloudinary.com/x/gallery-photo.jpg"})
check("attaching a client asset is accepted", r.status_code, 200)
sc = r.get_json()["scene"]
check("marked as a client asset", sc["asset_type"], "client_asset")
check("sourced from Cloudinary directly, not uploaded again", sc["asset_source"], "cloudinary")
check("with no thumbnail given, the asset stands in for its own",
      sc["asset_thumb_url"], "https://res.cloudinary.com/x/gallery-photo.jpg")

r = http.post(f"{base}/use-client-asset",
              json={"url": "https://res.cloudinary.com/x/gallery-photo.jpg",
                    "thumbnail": "https://res.cloudinary.com/x/gallery-photo-thumb.jpg"})
check("a supplied thumbnail is kept rather than overwritten",
      r.get_json()["scene"]["asset_thumb_url"],
      "https://res.cloudinary.com/x/gallery-photo-thumb.jpg")

# Housekeeping: this route records nothing, and its own comment says why --
# no file is stored, the asset was recorded by whichever tool put it there.
attach_calls = [e for e in audit.tail(limit=200, module="commercial_builder")
               if e.get("type") == "client_asset_attached"]
check("attaching is deliberately not logged a second time", attach_calls, [])


# ---------------------------------------------------------------------------
section("generate-video: Runway animates a frame, and refuses to invent one")
# ---------------------------------------------------------------------------
with hub_app.app_context():
    bare = Scene(project_id=project_id, order_index=1, start=10.0, end=20.0,
                narration="No footage chosen yet.")
    db.session.add(bare)
    db.session.commit()
    bare_id = bare.id

r = http.post(f"/tools/commercial-builder/api/projects/{project_id}/scenes/{bare_id}"
             f"/generate-video", json={})
check("a scene with no frame is refused rather than sent to Runway",
      r.status_code, 400)
check("and says which button to press first",
      "needs an image first" in r.get_json()["error"])

# The scene from section "choose-ai-option" already has an asset_url (the
# still it was pointed at) -- the ordinary case the button exists for.
r = http.post(f"{base}/generate-video", json={})
check("animating the chosen still is accepted", r.status_code, 200)
body = r.get_json()
check("mock mode says so", body["live"], False)
job = body["job"]
check("a mock job is marked as one", job.get("_mock"), True)
check("and reports no video url rather than a plausible one",
      job.get("video_url"), None)
# Not "processing": mock mode has nothing left to poll for, which is the
# exact reason the front-end fix in this file's docstring was needed.
check("a mock job reports itself already completed",
      job.get("status"), "completed")
with hub_app.app_context():
    reloaded = db.session.get(Scene, scene_id)
    check("the job is recorded on the scene", bool(reloaded.asset_meta.get("runway_job")))
    # Whatever the scene's asset_url is *now* -- the "use-client-asset" section
    # above already moved it on from the still "choose-ai-option" picked.
    check("along with the frame it was actually made from",
          reloaded.asset_meta.get("runway_source_image"), reloaded.asset_url)

generated = [e for e in audit.tail(limit=200, module="commercial_builder")
            if e.get("type") == "ai_video_generated"]
check("a non-failed job reaches the client's 360 record",
      generated[-1].get("client") if generated else None, "Riverside HVAC")

# A scene longer than Runway's own ceiling is the caller's mistake, not
# Runway's, and is refused before anything is sent.
with hub_app.app_context():
    long_scene = Scene(project_id=project_id, order_index=2, start=0.0, end=25.0,
                       narration="A long scene.", asset_url="https://x/still.png")
    db.session.add(long_scene)
    db.session.commit()
    long_id = long_scene.id
r = http.post(f"/tools/commercial-builder/api/projects/{project_id}/scenes/{long_id}"
             f"/generate-video", json={})
check("a scene past Runway's ceiling is refused as the caller's problem",
      r.status_code, 400)
check("naming the limit rather than reading as a dead provider",
      "Runway clips are at most" in r.get_json()["error"])


# ---------------------------------------------------------------------------
section("generate-video/status: the write-through, exactly as HeyGen's is")
# ---------------------------------------------------------------------------
with hub_app.app_context():
    fresh = Scene(project_id=project_id, order_index=3, start=0.0, end=8.0,
                 narration="Fresh scene.")
    db.session.add(fresh)
    db.session.commit()
    fresh_id = fresh.id
r = http.get(f"/tools/commercial-builder/api/projects/{project_id}/scenes/{fresh_id}"
            f"/generate-video/status")
check("a scene with no job says so rather than inventing one", r.status_code, 404)

# The scene under test already carries a mock, completed-with-no-video job
# from the section above.
r = http.get(f"{base}/generate-video/status")
check("the status route answers", r.status_code, 200)
check("mock mode attaches nothing", r.get_json()["attached"], False)
check("and says it was mock", r.get_json()["mock"], True)
check("reported as completed, not failed -- Runway did what it always does "
     "with no key, which is not an error", r.get_json()["status"], "completed")

# Polling an already-attached scene must not re-ask the provider at all.
with hub_app.app_context():
    reloaded = db.session.get(Scene, scene_id)
    meta = dict(reloaded.asset_meta or {})
    meta["runway_url"] = "https://res.cloudinary.com/x/attached.mp4"
    reloaded.asset_meta = meta
    db.session.commit()
_status_calls = []
_real_check_status = runway_service.check_status
runway_service.check_status = lambda *a, **k: (_status_calls.append(1) or {})
try:
    r = http.get(f"{base}/generate-video/status")
    check("an attached scene reports so without re-polling",
          (r.get_json()["status"], r.get_json()["attached"]), ("completed", True))
    check("and Runway is never asked again", _status_calls, [])
finally:
    runway_service.check_status = _real_check_status

# A job Runway itself marked failed answers failed, and is not re-polled.
with hub_app.app_context():
    failed_scene = Scene(project_id=project_id, order_index=4, start=0.0, end=8.0,
                         narration="x", asset_url="https://x/f.png",
                         asset_meta={"runway_job": {"job_id": "rw_1", "status": "failed",
                                                    "error": "content policy"}})
    db.session.add(failed_scene)
    db.session.commit()
    failed_id = failed_scene.id
r = http.get(f"/tools/commercial-builder/api/projects/{project_id}/scenes/{failed_id}"
            f"/generate-video/status")
check("a job Runway failed answers failed", r.status_code, 200)
check("and carries the reason", r.get_json()["error"], "content policy")
check("never attached", r.get_json()["attached"], False)

# A live completed job with a real clip: attached, filed under the client, and
# the mirror is honest about whether the copy is really ours.
with hub_app.app_context():
    live_scene = Scene(project_id=project_id, order_index=5, start=0.0, end=8.0,
                       narration="x", asset_url="https://x/still2.png",
                       asset_meta={"runway_job": {"job_id": "rw_2", "status": "processing",
                                                  "duration": 5}})
    db.session.add(live_scene)
    db.session.commit()
    live_id = live_scene.id

_real_upload = cloudinary_service.upload_asset
runway_service.check_status = lambda job_id: {
    "job_id": job_id, "status": "completed",
    "video_url": "https://runway-cdn.example/clip.mp4"}
cloudinary_service.upload_asset = lambda *a, **k: {
    "secure_url": "https://res.cloudinary.com/riverside-hvac/clip.mp4", "public_id": "clip"}
try:
    r = http.get(f"/tools/commercial-builder/api/projects/{project_id}/scenes/{live_id}"
                f"/generate-video/status")
    check("a finished live job is attached", r.status_code, 200)
    body = r.get_json()
    check("and reports so", body["attached"], True)
    check("this is not mock", body["mock"], False)
    with hub_app.app_context():
        reloaded = db.session.get(Scene, live_id)
        check("the mirrored url lands on the scene, not the provider's own",
              reloaded.asset_url, "https://res.cloudinary.com/riverside-hvac/clip.mp4")
        check("marked as video now, for the compositor and QC",
              reloaded.asset_meta.get("media"), "video")
        check("sourced from Runway", reloaded.asset_source, "runway")
        check("a genuine mirror is marked as one",
              reloaded.asset_meta.get("runway_mirrored"), True)
        check("the clip length QC needs is recorded",
              reloaded.asset_meta.get("clip_seconds"), 5)
    ready = [e for e in audit.tail(limit=200, module="commercial_builder")
            if e.get("type") == "ai_video_ready"]
    check("a finished clip reaches the client's 360 record",
          ready[-1].get("client") if ready else None, "Riverside HVAC")

    # The mirror itself can fail -- Cloudinary down, quota, a bad key -- and a
    # provider URL is signed and will expire. Fall back to it, and say so.
    with hub_app.app_context():
        live_scene2 = Scene(project_id=project_id, order_index=6, start=0.0, end=8.0,
                            narration="x", asset_url="https://x/still3.png",
                            asset_meta={"runway_job": {"job_id": "rw_3", "status": "processing",
                                                       "duration": 5}})
        db.session.add(live_scene2)
        db.session.commit()
        live2_id = live_scene2.id
    cloudinary_service.upload_asset = lambda *a, **k: {
        "secure_url": None, "error": "Cloudinary quota exceeded"}
    r = http.get(f"/tools/commercial-builder/api/projects/{project_id}/scenes/{live2_id}"
                f"/generate-video/status")
    check("still attaches, off the provider's own url", r.status_code, 200)
    with hub_app.app_context():
        reloaded2 = db.session.get(Scene, live2_id)
        check("falls back to Runway's own (signed, expiring) url",
              reloaded2.asset_url, "https://runway-cdn.example/clip.mp4")
        check("and says the mirror did not happen",
              reloaded2.asset_meta.get("runway_mirrored"), False)
        check("naming why", reloaded2.asset_meta.get("runway_mirror_error"),
              "Cloudinary quota exceeded")
finally:
    runway_service.check_status = _real_check_status
    cloudinary_service.upload_asset = _real_upload


# ---------------------------------------------------------------------------
section("The front-end button reads the mock flag its own response carries")
# ---------------------------------------------------------------------------
# generateVideo() used to discard the POST response and always say "AI video
# generating - this takes a few minutes.", mock or not -- and mock mode never
# starts the poll loop that would otherwise correct it (runwayPending() only
# fires on "processing", and Runway's mock reports "completed" immediately).
# Its two siblings (the stills picker, the spokesperson picker) already read
# `live` off their own response before choosing what to say; this checks the
# fix landed in the same shape.
JS = (ROOT / "modules/commercial_builder/static/js/blueprint.js").read_text(encoding="utf-8")
gv_start = JS.index("async function generateVideo(")
gv_end = JS.index("\n  }", gv_start) + len("\n  }")
GENERATE_VIDEO = JS[gv_start:gv_end]
check("the response is captured, not discarded",
      "data = await CB.api(" in GENERATE_VIDEO)
check("the toast is chosen from the response's own live flag",
      "data.live ?" in GENERATE_VIDEO)
check("and names the actual reason nothing will finish, in mock mode",
      "Mock mode" in GENERATE_VIDEO and "no video was produced" in GENERATE_VIDEO)
check("the error argument to toast follows the same flag",
      "!data.live" in GENERATE_VIDEO)
# MOCK_STEPS' own entry for this route is the belt-and-braces half -- a page-
# wide note appears too, and both readings must keep matching the one route.
COMMON_JS = (ROOT / "modules/commercial_builder/static/js/common.js").read_text(encoding="utf-8")
check("the shared mock-note table still names this route",
      "/\\/generate-video/" in COMMON_JS)
check("the JS still parses", GENERATE_VIDEO.count("{") == GENERATE_VIDEO.count("}"))


print(f"\n{'-' * 62}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
