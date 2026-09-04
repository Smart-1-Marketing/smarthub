"""Video Search: a minimized status card, Pexels/Pixabay video shelves, and
saving a clip -- owned or from a live provider -- into a client's gallery.

    python3 test_video_search_gallery.py

Same shape as the other test files here -- no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

Three things this asserts that a screenshot would not catch:

  1. **Pexels and Pixabay never call their own mock fallback here.** Both
     services degrade to placehold.co cards when no key is configured, which
     is right on the Commercial Builder (a producer can keep building without
     one) and wrong on a tool whose whole job is a working URL to real
     footage -- the same argument this page already makes about Coverr. The
     route must never reach that branch, key or no key.
  2. **A folder is `collection_label`, and it groups by client and kind.**
     Two clips saved under the same typed name land in one folder; asking for
     a client's existing folders answers only from what that client already
     has, never another client's.
  3. **Nothing here re-uploads footage that is already in Cloudinary.** An
     owned library clip is filed with the public_id it already has; only a
     Coverr/Pexels/Pixabay clip -- which lives on somebody else's CDN -- goes
     through storage first.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1vsgallery_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "vsgallery-test-secret"
os.environ["IMAGE_PICKER_SIGNING_KEY"] = "vsgallery-test-signing-key"
# No Cloudinary and no provider keys: the environment a developer runs this
# in ordinarily, and the one every "must not call the real thing" assertion
# below depends on.
for _k in ("CLOUDINARY_URL", "PEXELS_API", "PEXELS_API_KEY", "PIXABAY_API",
          "PIXABAY_API_KEY", "COVERR_API", "COVERR_API_KEY"):
    os.environ.pop(_k, None)

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def check_true(label, got):
    check(label, bool(got), True)


def check_in(label, needle, haystack):
    if needle in haystack:
        _passed_ok(label)
    else:
        _fail(label, f"{needle!r} not in {haystack!r}")


def _passed_ok(label):
    global _passed
    _passed += 1
    print(f"  ok    {label}")


def _fail(label, detail):
    global _failed
    _failed += 1
    print(f"  FAIL  {label}\n          {detail}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


import flask                                                      # noqa: E402

from hub import client_brand, image_audit                         # noqa: E402
from hub import storage as hub_storage                            # noqa: E402
from modules.commercial_builder.services import (                 # noqa: E402
    pexels_service, pixabay_service,
)
from modules.image_picker import filing                           # noqa: E402
from modules.video_backgrounds import app as vb_app                # noqa: E402

flask_app = flask.Flask(__name__)
flask_app.config["SECRET_KEY"] = "vsgallery-test-secret"
vb_app.register_video_backgrounds(flask_app)
http = flask_app.test_client()

TEXT = ROOT / "modules" / "video_backgrounds" / "templates" / "video_backgrounds.html"
PAGE = TEXT.read_text()

CLOUDINARY_ITEM = {
    "id": "cloudinary_abc123", "provider": "cloudinary",
    "public_id": "Video Backgrounds/hd0021", "full_url": "https://res.cloudinary.com/x/video/upload/hd0021.mp4",
    "background_url": "https://res.cloudinary.com/x/video/upload/f_auto/hd0021.mp4",
    "width": 1920, "height": 1080, "duration": 12.5,
}
PEXELS_ITEM = {
    "id": "pexels_555", "provider": "pexels", "author": "Some Photographer",
    "full_url": "https://videos.pexels.com/full.mp4",
    "preview_url": "https://videos.pexels.com/preview.mp4",
    "width": 1280, "height": 720, "duration": 9,
}


# =====================================================================
section("The status card is minimized until asked for")
# =====================================================================

check_in("the toggle button is on the page", 'id="vb-status-toggle"', PAGE)
check_in("the detail starts hidden", 'id="vb-status-body" hidden', PAGE)
check_in("a summary renders beside the toggle without opening it",
        'id="vb-status-summary"', PAGE)
check_in("the toggle actually flips the hidden attribute",
        "statusBody.hidden = !opening;", PAGE)


# =====================================================================
section("Pexels and Pixabay are searched live, each its own shelf")
# =====================================================================

check_true("Pexels is not configured in this test environment",
           not pexels_service.is_live())
check_true("...nor Pixabay", not pixabay_service.is_live())

with flask_app.test_request_context("/tools/video-backgrounds/api/search?q=drone"):
    payload = vb_app.api_search().get_json()

check("Pexels answers honestly with no key configured",
      payload["pexels"]["live"], False)
check("...and returns nothing -- never the mock placeholders that service "
      "falls back to elsewhere",
      payload["pexels"]["results"], [])
check_in("...saying the key is missing rather than 'no matches'",
        "PEXELS_API is not set", payload["pexels"]["note"])
check("Pixabay answers the same way", payload["pixabay"]["live"], False)
check("...with no results", payload["pixabay"]["results"], [])
check_in("...and its own note", "PIXABAY_API is not set", payload["pixabay"]["note"])
check_true("the owned-library and Coverr keys are untouched by any of this",
           "results" in payload and "coverr" in payload)

with flask_app.test_request_context("/tools/video-backgrounds/api/search?q="):
    empty_payload = vb_app.api_search().get_json()
check("an empty query never calls Pexels at all",
      empty_payload["pexels"]["results"], [])
check_in("...and says why", "Type a search", empty_payload["pexels"]["note"])
check("...nor Pixabay", empty_payload["pixabay"]["results"], [])

check_in("the template carries a Pexels shelf", 'id="vb-pexels-results"', PAGE)
check_in("...and a Pixabay one", 'id="vb-pixabay-results"', PAGE)
check_in("each labeled so nobody mistakes it for indexed, owned footage",
        "free stock, searched live", PAGE)
check_in("Pexels and Pixabay share the Coverr shape through one card function",
        "function stockCard(item, fallbackName)", PAGE)


# =====================================================================
section("The note helpers agree with the ones already asserted for Coverr")
# =====================================================================

check_in("no query typed", "Type a search", vb_app._pexels_note("", False, False))
check_in("no key set", "PEXELS_API is not set", vb_app._pexels_note("drone", False, True))
check("a live search with results carries no note",
      vb_app._pexels_note("drone", True, True), "")
check("a live search with nothing found says so",
      vb_app._pixabay_note("drone", True, False), "No Pixabay matches for this search.")


# =====================================================================
section("Saving is one filing path, whatever the source")
# =====================================================================

check_in("a client is required", "No client chosen",
        vb_app._save_clip_to_gallery(client_name="", folder="", provider="cloudinary",
                                     item=CLOUDINARY_ITEM, actor="tester")["error"])

out = vb_app._save_clip_to_gallery(
    client_name="Riverbend Marine", folder="Homepage hero", provider="cloudinary",
    item=CLOUDINARY_ITEM, actor="tester@smart1marketing.com")
check_true("an owned clip is filed", out.get("ok"))
image = out.get("image") or {}
check("filed with the public_id it already has in Cloudinary",
      image.get("public_id"), CLOUDINARY_ITEM["public_id"])
check("under the video_search kind", image.get("collection_kind"), "video_search")
check("the folder typed becomes the collection label",
      image.get("collection_label"), "Homepage hero")
check("recorded against the owned-library provider, not 'cloudinary' verbatim",
      image.get("provider"), "video_library")
check("filed as a video, not an image", image.get("resource_type"), "video")

# Called as a plain function, inside only a request context -- the same shape
# test_video_library.py already uses for api_search(): it reaches the route's
# own logic without going through the blueprint's login gate, which is
# exercised separately, over real HTTP, further down.
with flask_app.test_request_context("/tools/video-backgrounds/api/gallery/folders"
                                    "?client=Riverbend+Marine"):
    folders_resp = vb_app.api_gallery_folders().get_json()
check("the saved folder shows up for that client",
      folders_resp["folders"], ["Homepage hero"])

with flask_app.test_request_context("/tools/video-backgrounds/api/gallery/folders"
                                    "?client=A+Client+With+Nothing+Saved"):
    other_resp = vb_app.api_gallery_folders().get_json()
check("a client with nothing saved has no folders offered",
      other_resp["folders"], [])

# A second save with no folder falls back to a real, named bucket rather than
# an empty collection_label -- an empty label reads as "no folder", which
# duplicate save requests would otherwise scatter clips across.
out2 = vb_app._save_clip_to_gallery(
    client_name="Riverbend Marine", folder="", provider="cloudinary",
    item={**CLOUDINARY_ITEM, "public_id": "Video Backgrounds/hd0099"},
    actor="tester@smart1marketing.com")
check_true("a blank folder still saves", out2.get("ok"))
check("...into a named default rather than an empty one",
      (out2.get("image") or {}).get("collection_label"), "Unsorted")


# --- a provider clip is stored before it is filed ---------------------- #

_real_put_remote = hub_storage.put_remote
_calls = []


def _fake_put_remote(kind, url, **kw):
    _calls.append({"kind": kind, "url": url, **kw})
    return hub_storage.StoredAsset(
        public_id="smart1-video_search/riverbend-marine/pexels-555",
        url="https://res.cloudinary.com/x/video/upload/pexels-555.mp4",
        resource_type="video", bytes=1234, backend="cloudinary",
        folder="smart1-video_search/riverbend-marine", checksum="")


hub_storage.put_remote = _fake_put_remote
try:
    provider_out = vb_app._save_clip_to_gallery(
        client_name="Riverbend Marine", folder="Homepage hero", provider="pexels",
        item=PEXELS_ITEM, actor="tester@smart1marketing.com")
finally:
    hub_storage.put_remote = _real_put_remote

check_true("a Pexels clip is stored, then filed", provider_out.get("ok"))
check("exactly one storage call was made", len(_calls), 1)
check("stored under this Hub's own video_search bucket, not the indexed "
      "Video Backgrounds folder tree hub/video_library.py sweeps",
      _calls[0]["kind"], "video_search")
check("the client rides along so the copy lands in their own folder",
      _calls[0]["client"], "Riverbend Marine")
provider_image = provider_out.get("image") or {}
check("filed with the STORED public_id, not the provider's own id",
      provider_image.get("public_id"),
      "smart1-video_search/riverbend-marine/pexels-555")
check("recorded under the real provider", provider_image.get("provider"), "pexels")
check("the source's own author rides along on the alt text",
      "Some Photographer" in (provider_image.get("alt_text") or ""), True)

# A provider clip with nowhere to fetch it from is refused rather than filed
# with an empty URL.
no_url_out = vb_app._save_clip_to_gallery(
    client_name="Riverbend Marine", folder="x", provider="pexels",
    item={"id": "pexels_0", "provider": "pexels"}, actor="tester")
check("a clip with no address is refused", no_url_out.get("ok"), False)

# Storage failing (no Cloudinary configured, the ordinary case in this test
# environment) must read as a real failure, not a silent skip.
real_fail_out = vb_app._save_clip_to_gallery(
    client_name="Riverbend Marine", folder="x", provider="pexels",
    item=PEXELS_ITEM, actor="tester")
check("with Cloudinary unset, storing fails and says so", real_fail_out.get("ok"), False)
check_in("...naming what went wrong", "could not be stored", real_fail_out["error"])


# =====================================================================
section("Saving is filed against a client, so it is client work")
# =====================================================================

check_true("video_backgrounds is registered as work, not left for "
          "check_work_kinds() to find missing",
          "video_backgrounds" in client_brand.WORK_KINDS)
check("check_work_kinds() finds nothing outstanding",
      [f["module"] for f in client_brand.check_work_kinds()
       if f["module"] == "video_backgrounds"], [])
check("stale_work_exemptions() finds a real call site behind the entry",
      [m for m in client_brand.stale_work_exemptions()
       if m == "video_backgrounds"], [])


# =====================================================================
section("Every provider a save can write has a heading in the gallery")
# =====================================================================

labels = filing.source_tiers()["labels"]
for kind in ("video_library", "pexels", "pixabay", "coverr"):
    check_true(f"the gallery can name a {kind!r} file", kind in labels)
check_true("Video Searches is its own kind heading",
          filing.KIND_LABELS.get("video_search") == "Video Searches")

by_key = {p["key"]: p for p in image_audit.PRODUCERS}
check_true("video_backgrounds is a declared image producer", "video_backgrounds" in by_key)
prod_rows = image_audit.producers()
vb_row = next(p for p in prod_rows if p["key"] == "video_backgrounds")
check_true("...and it actually calls the filing path", vb_row["files"])
check("nothing this module writes escaped declaration",
      image_audit.undeclared_providers(), [])


# =====================================================================
section("The route requires staff, like the rest of the module")
# =====================================================================

check("an anonymous save is refused", http.post("/tools/video-backgrounds/api/gallery/save",
                                                json={}).status_code, 401)
check("an anonymous folder lookup is refused",
      http.get("/tools/video-backgrounds/api/gallery/folders?client=x").status_code, 401)
check("an anonymous client search is refused",
      http.get("/tools/video-backgrounds/api/clients?q=x").status_code, 401)


# =====================================================================
shutil.rmtree(TMP, ignore_errors=True)
print()
if _failed:
    print(f"{_failed} FAILED, {_passed} passed\n")
    sys.exit(1)
print(f"All {_passed} video search gallery checks passed.")
