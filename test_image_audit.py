"""Every image attached to somebody, and a gallery you can actually read.

    python3 test_image_audit.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

Twelve tools in this Hub create, upload or choose an image. Each wrote it to
Cloudinary and filed a record of its own, and the question anybody actually
asks — *what have we made for this client?* — was answerable only from the
ones that recorded a client. Six of the twelve recorded none.

Two of those were invisible in the worst way. **Page Image Optimizer** shipped
with an ">>> INTEGRATION POINT <<<" naming three candidate writers —
`modules.seo_images.store.add_record` and two others — and not one of those
names has ever existed, so `_resolve_hook()` returned None from the day it was
written and every image it saved went to a private JSON file nothing reads.
And **`io_creative`** sat in the gallery's own label table with no writer at
all, which is the `display_ads` failure this codebase has already paid for
once.

The audit therefore has two halves, and the second is the reason a row count
alone would mislead:

  1. **The stores** — records that exist — split into filed, unfiled, and
     *not measured* where a store would not answer.

  2. **The producers** — the code paths that make an image — checked for
     whether they reach a client gallery at all. A tool that has never filed
     anything has no unfiled rows to count: it is invisible to a data audit
     and reads as the cleanest tool in the building.

That producer check reads the **AST**, not the text, for the reason
`hub/config.py`'s drift check gives at length — several files here explain
this trap by naming `file_asset` in prose, and a text match would report the
explanation as the defect. It also accepts filing done over HTTP, because the
IO Builder uploads straight from the browser and files from there, and an
AST-only check called the one tool that does file its worst offender.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1imgaudit_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "image-audit-test-secret"
os.environ["PANEL_PASSWORD"] = "image-audit-test-password"

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


from wsgi import application                                  # noqa: E402
from werkzeug.test import Client as WClient                   # noqa: E402
from hub import image_audit                                   # noqa: E402
from modules.image_picker import filing                       # noqa: E402

http = WClient(application)
http.post("/login", data={"password": os.environ["PANEL_PASSWORD"]})

GALLERY = (ROOT / "modules" / "image_picker" / "templates"
           / "picker_gallery.html").read_text()


# =====================================================================
section("Every tool that makes an image files it against somebody")
# =====================================================================

prod = image_audit.producers()
check("every producer is accounted for", len(prod) >= 12, True)
not_filing = [p["label"] for p in prod if not p["files"]]
# This is the whole point of the file. It started at six.
check("none of them files nothing", not_filing, [])

by_key = {p["key"]: p for p in prod}
# The three verbs the audit is asked about: created, uploaded, chosen.
for verb in ("create", "upload", "choose"):
    check(f"a producer that {verb}s an image is covered",
          any(p["makes"] == verb for p in prod), True)

# Each of the six that were silent, named, so a regression is a named failure
# rather than a number going down by one.
for key in ("bg_remover", "image_creator", "page_image_optimizer",
            "stock_photos", "io_builder", "commercial_builder"):
    check(f"{key} files what it makes", by_key[key]["files"], True)

# "A client or a lead" is two right answers, not one. A prospect's files are
# attached to the prospect record, keyed on the lead id -- a rule demanding
# file_asset of everything would report the lead half as unfiled, which is the
# exact thing this audit is about.
check("a prospect's files count as attached",
      by_key["prospect_assets"]["files"], True)
check("through the lead store, not a client gallery",
      "add_asset" in by_key["prospect_assets"]["evidence"], True)

# The IO Builder files over HTTP from the browser, so an AST-only check calls
# the one tool that does file its worst offender.
check("filing over the route counts as filing",
      "posts to" in by_key["io_builder"]["evidence"], True)
# And the module that DEFINES the filing path is not its own worst offender.
check("the filing path itself is not a finding",
      "defines" in by_key["image_picker"]["evidence"], True)


# =====================================================================
section("The producer check reads code, not prose")
# =====================================================================

fake = Path(TMP) / "prose.py"
fake.write_text('"""This module used to forget to call file_asset()."""\n'
                "def go():\n    return 1\n")
_real_root = image_audit.ROOT
image_audit.ROOT = Path(TMP)
try:
    files, why = image_audit._files("prose.py")
finally:
    image_audit.ROOT = _real_root
check("a docstring naming file_asset is not a call site", files, False)
check("and the answer says what it looked for", "no call to" in why, True)

image_audit.ROOT = Path(TMP)
try:
    (Path(TMP) / "real.py").write_text(
        "def go():\n    return file_asset(client_name='x')\n")
    files2, why2 = image_audit._files("real.py")
    missing, why3 = image_audit._files("not-here.py")
finally:
    image_audit.ROOT = _real_root
check("a real call is found", files2, True)
check("with the line named", "line 2" in why2, True)
# A file that is not in the checkout is a different answer from one that
# does not file.
check("an absent file says so", why3, "that file is not in this checkout")


# =====================================================================
section("Absent is not zero")
# =====================================================================

_real = image_audit.STORES[0]["reader"]


def _boom():
    raise RuntimeError("Knack timed out")
    yield  # pragma: no cover


image_audit.STORES[0]["reader"] = _boom
try:
    broken = image_audit.audit()
finally:
    image_audit.STORES[0]["reader"] = _real

first = broken["stores"][0]
check("an unreadable store is not measured", first["measured"], False)
check("its counts are None, never nought", first["total"], None)
check("and the reason travels with it", "Knack timed out" in first["error"], True)
# A total that quietly counts an unreadable store as nought is the confident
# wrong answer this whole file exists to find.
check("the headline says the total is incomplete", broken["measured"], False)
check("and names which store could not answer",
      broken["unmeasured"][0]["label"], image_audit.STORES[0]["label"])

clean = image_audit.audit()
check("with every store readable it says so", clean["measured"], True)
# Cloudinary is the ground truth for what EXISTS; this audits what was
# recorded, and says so rather than implying it surveyed the account.
check("the scope is stated rather than implied",
      "outside this report" in clean["note"], True)


# =====================================================================
section("An unattached image can be attached from the row it is on")
# =====================================================================

from modules.seo_images.app import load_archive, save_archive   # noqa: E402

save_archive([
    {"id": "orphan1", "company": "", "filename": "roof-panels.webp",
     "public_id": "smart1-seo-images/roof", "project": "Website",
     "url": "https://res.cloudinary.com/x/roof.webp"},
    {"id": "nopid", "company": "", "filename": "old.webp", "project": "Website",
     "url": "https://res.cloudinary.com/x/old.webp"},
    {"id": "owned1", "company": "Icon Solar", "filename": "van.webp",
     "public_id": "smart1-seo-images/van", "project": "Website",
     "url": "https://res.cloudinary.com/x/van.webp"},
])
a = image_audit.audit()
seo = next(s for s in a["stores"] if s["key"] == "seo_images")
check("the store counts them all", seo["total"], 3)
check("and separates the ones nobody owns", seo["unfiled"], 2)
check("the unattached rows are the ones offered",
      sorted(r["id"] for r in seo["rows"]), ["nopid", "orphan1"])

# A guess is worse than a blank: the whole report is about images filed to
# nobody, and one filed to the wrong client cannot be undone by editing a row.
check("attaching to nothing is refused",
      image_audit.attach("seo_images", "orphan1", "  ")["ok"], False)
check("and to a store that has no way to attach",
      image_audit.attach("nonesuch", "x", "Icon Solar")["ok"], False)
check("a row that has since gone is named, not crashed on",
      image_audit.attach("seo_images", "vanished", "Icon Solar")["ok"], False)

out = image_audit.attach("seo_images", "orphan1", "Icon Solar", actor="todd")
check("attaching works", out["ok"], True)
check("the tool's own record is updated", out["store_updated"], True)
# Two writes, reported separately: "attached" and "attached in one of two
# places" are different outcomes, and one tick for both is how somebody learns
# not to trust the tick.
check("and the gallery write is reported on its own", out["gallery_filed"], True)
check("the row now names the client",
      next(r["company"] for r in load_archive() if r["id"] == "orphan1"),
      "Icon Solar")
check("so the audit no longer counts it",
      next(s for s in image_audit.audit()["stores"]
           if s["key"] == "seo_images")["unfiled"], 1)

# A row written before public_ids were kept can still be given a client -- the
# tool's own record is what the audit counts -- but there is nothing to file
# into the gallery, and that is said rather than reported as a clean success.
half = image_audit.attach("seo_images", "nopid", "Icon Solar")
check("a row with no stored id still gets its client", half["store_updated"], True)
check("but the gallery write is honestly reported", half["gallery_filed"], False)
check("with the reason", "no stored URL" in half["gallery_error"], True)


# =====================================================================
section("The report is a page somebody can reach, and it is guarded")
# =====================================================================

anon = WClient(application)
check("the page refuses an anonymous visitor",
      anon.get("/qa/unattached-images").status_code, 302)
check("and so does the API",
      anon.get("/api/image-audit",
               headers={"Accept": "application/json"}).status_code, 401)
check("the page loads for staff",
      http.get("/qa/unattached-images").status_code, 200)
check("the API answers", http.get("/api/image-audit").get_json()["measured"], True)
# Attaching writes two stores, so it is a POST rather than a GET a prefetch
# could fire.
check("attaching is a POST",
      http.get("/api/image-audit/attach").status_code, 405)
# A tool with no tile is invisible; this file counts six that were.
check("it is tiled on QA Reports",
      "/qa/unattached-images" in http.get("/qa").get_data(as_text=True), True)
# Named for the finding, like every other report here ("No Dashboards",
# "Stale Creative") -- and deliberately not "Image Audit", which tied with
# Image Creator on the bare query "image" and took the top slot off it on
# nothing but the alphabet.
check("and named for what it lists",
      "Unattached Images" in http.get("/qa").get_data(as_text=True), True)


# =====================================================================
section("A gallery you can read: one label table, and a search")
# =====================================================================

labels = filing.source_tiers()["labels"]
# The template used to keep its own copy, so a kind added since showed up in a
# client's gallery as a bare key under no heading at all.
check("the labels come from the module that files",
      "var SOURCES = " in GALLERY, True)
check("and the template keeps no second copy",
      "Uploaded from their device" in GALLERY, False)
for kind in filing.KIND_LABELS:
    check(f"the gallery can name a {kind!r} file", kind in labels, True)

# Every label declared must have a writer -- `io_creative` sat here with none.
# Every `provider` value a producer writes must have a heading, or it reaches
# the client's gallery as a bare key under no heading -- the state
# `io_creative` was in for as long as it existed.
written = sorted({v for p in image_audit.PRODUCERS for v in p.get("provider", [])})
check("every producer declares what it writes",
      all(p.get("provider") for p in image_audit.PRODUCERS), True)
check("and every value it writes has a heading",
      [v for v in written if v not in labels], [])

check("the gallery has a search box", 'id="gq"' in GALLERY, True)
check("and group chips with counts", 'id="chips"' in GALLERY, True)
check("the search is debounced", "setTimeout(redraw" in GALLERY, True)
# A filtered list that reports an unfiltered total is a wrong answer with two
# right ones either side of it.
check("a filtered view says it is filtered", '" of " + ALL.length' in GALLERY, True)
check("their own files sort first", "SOURCES.theirs" in GALLERY, True)

check("nothing matching reads differently from nothing saved",
      "Nothing matches" in GALLERY and "Nothing saved yet" in GALLERY, True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
