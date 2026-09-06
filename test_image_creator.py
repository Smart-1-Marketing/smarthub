"""Image Creator's "Client gallery" chip actually opens the client's gallery.

    python3 test_image_creator.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway SQLite database and its own data directory, so it never touches
/var/data or the real one.

## What this file is protecting

The Assets panel offered a chip labelled "Client gallery" that read
`modules.seo_images.load_archive()` — the SEO Image Pipeline's own archive,
matched on an exact company name. That is one of a dozen producers that file
into a client's actual gallery (`hub/image_audit.py`), so a photo a client
uploaded through their own picker link, or one saved by Ad Builder, GPT Ads
or Social Planner, never showed up there despite the label promising the
whole thing.

`assets.gallery_assets()` reads the real shared gallery now, the way
`hub/client_context.gallery_images()` does — directly against
`modules.image_picker`'s own tables — and the panel is handed a link to the
full gallery that is offered whether or not this search found anything, so
there is always a way to reach the actual client gallery from here.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1imgcreator_test_")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "image-creator-test-secret")

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from modules.image_creator import assets                         # noqa: E402
from modules.image_picker import filing                          # noqa: E402

# ---------------------------------------------------------------------------
section("A client with no gallery yet")
# ---------------------------------------------------------------------------

check("gallery_assets returns nothing rather than raising",
      assets.gallery_assets("Nobody Yet LLC"), [])
check("and a blank client is refused rather than listing everybody",
      assets.gallery_assets(""), [])

# ---------------------------------------------------------------------------
section("Every producer's work is in the same gallery, and this reads it")
# ---------------------------------------------------------------------------
# The old code read modules.seo_images.load_archive() alone, so an asset
# filed by any other tool -- Ad Builder, GPT Ads, a client's own upload --
# was invisible here despite the chip's label. file_asset() is the one path
# every producer uses (hub/image_audit.py), so filing through it twice under
# two different providers is the fair test of "this reads the WHOLE gallery."

CLIENT = "Riverside HVAC"
filed_seo = filing.file_asset(
    client_name=CLIENT, public_id="seo_images/riverside-hvac/hero", url="https://example.test/hero.jpg",
    kind="seo_image", filename="hero.jpg", alt="A technician on a service call",
    provider="seo_image", saved_by="test")
filed_ads = filing.file_asset(
    client_name=CLIENT, public_id="display_ads/riverside-hvac/banner", url="https://example.test/banner.jpg",
    kind="display_ad", filename="banner.jpg", label="Fall tune-up banner",
    provider="display_ads", saved_by="test")

check("the SEO image filed cleanly", filed_seo.get("ok"), True)
check("and so did the display ad, a different tool entirely", filed_ads.get("ok"), True)

found = assets.gallery_assets(CLIENT)
check("both producers' work comes back from one gallery read", len(found), 2)
check("newest first", found[0]["name"], "banner.jpg")

# ---------------------------------------------------------------------------
section("The search box searches the gallery, not a page of results")
# ---------------------------------------------------------------------------

check("a filename match", [a["name"] for a in assets.gallery_assets(CLIENT, "hero")],
      ["hero.jpg"])
check("a label match", [a["name"] for a in assets.gallery_assets(CLIENT, "tune-up")],
      ["banner.jpg"])
check("no match is an empty list, not an error",
      assets.gallery_assets(CLIENT, "nonexistent"), [])

# ---------------------------------------------------------------------------
section("A near name is not this client's gallery")
# ---------------------------------------------------------------------------
# The exact-slug rule filing.gallery_for_name() already enforces elsewhere in
# this Hub: "Riverside HVAC" and "Riverside HVAC Supply" are different
# businesses, and returning one for the other is the worst outcome available
# to a tool that places images onto a client's creative.

check("a client that merely contains the name gets nothing",
      assets.gallery_assets("Riverside HVAC Supply"), [])

# ---------------------------------------------------------------------------
section("The panel is always handed a way to the full gallery")
# ---------------------------------------------------------------------------

from modules.image_creator import app as image_creator_app        # noqa: E402

http = image_creator_app.app.test_client()
resp = http.get(f"/api/assets/gallery?client={CLIENT}")
body = resp.get_json()
check("the route answers", resp.status_code, 200)
check("carrying a link to the real client-gallery resolver",
      body.get("gallery_url"),
      "/tools/image-picker/gallery/for-client?name=Riverside%20HVAC")

empty = http.get("/api/assets/gallery?client=Nobody+Yet+LLC&q=anything")
check("even a client with nothing found still gets the link",
      bool(empty.get_json().get("gallery_url")), True)

no_client = http.get("/api/assets/gallery?client=")
check("no client named means no link to fabricate one",
      no_client.get_json().get("gallery_url"), "")


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
