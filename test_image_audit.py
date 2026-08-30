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

# The search is the SERVER's: it is the only half that can see the vision
# descriptions, and the only one that searches the whole library rather than
# the rows this page happened to load. The chips filter what comes back.
check("the gallery has a search box", 'id="gSearch"' in GALLERY, True)
check("and it asks the server", '"&q=" + encodeURIComponent(QUERY)' in GALLERY, True)
check("with group chips over the result", 'id="chips"' in GALLERY, True)
check("counted off what the search returned, not the whole library",
      "redraw();" in GALLERY and "ALL = d.images" in GALLERY, True)
# A chip from the previous result set would show an empty gallery with no
# sign of why.
check("a new search clears the chip", 'GROUP = "";' in GALLERY, True)
# A filtered list that reports an unfiltered total is a wrong answer with two
# right ones either side of it.
check("a filtered view says it is filtered", '" of " + ALL.length' in GALLERY, True)
check("their own files sort first", "SOURCES.theirs" in GALLERY, True)

check("nothing matching reads differently from nothing saved",
      "Nothing matches" in GALLERY and "Nothing saved yet" in GALLERY, True)


# =====================================================================
section("The other direction: in Cloudinary, known to nothing")
# =====================================================================

from hub import storage                                       # noqa: E402

# hub/storage.manifest() was written for exactly this and had no caller: its
# docstring says it "feeds the orphaned-asset audit", and that audit did not
# exist. The third declared-but-unwired integration point here.
check("the manifest carries a URL, not just an id",
      "secure_url" in (ROOT / "hub" / "storage.py").read_text(), True)

# Unconfigured is NOT MEASURED. Reporting an account nobody can list as a
# clean bill is the confident wrong answer this whole file is about.
_real_ready = storage.ready
storage.ready = lambda: False
try:
    unconfigured = image_audit.reconcile()
finally:
    storage.ready = _real_ready
check("an unlistable account is not measured", unconfigured["measured"], False)
check("and says which kind of not-measured", unconfigured["configured"], False)
check("with no folders invented", unconfigured["folders"], [])

save_archive([
    {"id": "a1", "company": "Icon Solar", "public_id": "smart1-seo-images/known",
     "url": "https://res.cloudinary.com/x/known.webp", "filename": "k.webp"},
])

FAKE = {
    "seo_images": [
        {"public_id": "smart1-seo-images/known", "bytes": 10,
         "secure_url": "https://c/k.webp"},
        {"public_id": "smart1-seo-images/orphan", "bytes": 20,
         "secure_url": "https://c/o.webp"},
    ],
    "cutouts": [
        {"public_id": "smart1-cutouts/icon-solar/van", "bytes": 5,
         "secure_url": "https://c/v.png"},
        {"public_id": "smart1-cutouts/unfiled/x", "bytes": 5,
         "secure_url": "https://c/x.png"},
    ],
}

_real_manifest = storage.manifest
storage.ready = lambda: True
storage.manifest = lambda kind, max_results=500: FAKE.get(kind, [])
try:
    rec = image_audit.reconcile()
finally:
    storage.ready, storage.manifest = _real_ready, _real_manifest

check("everything listed is counted", rec["total"], 4)
# The one the archive has a row for is not an orphan -- that is the whole join.
check("an asset a store knows about is not an orphan", rec["orphans"], 3)
seo_f = next(f for f in rec["folders"] if f["key"] == "seo_images")
check("the known one is excluded by public_id",
      [r["public_id"] for r in seo_f["rows"]], ["smart1-seo-images/orphan"])

cut = next(f for f in rec["folders"] if f["key"] == "cutouts")
by_pid = {r["public_id"]: r for r in cut["rows"]}
# A folder path is evidence, and it says so. Nothing is applied on it.
check("a client folder proposes its client",
      by_pid["smart1-cutouts/icon-solar/van"]["proposed"], "Icon Solar")
check("and says how it was arrived at",
      "folder" in by_pid["smart1-cutouts/icon-solar/van"]["proposed_how"], True)
# bg_remover puts a cut-out with no client under unfiled/, which names nobody.
check("an unfiled folder proposes nobody",
      by_pid["smart1-cutouts/unfiled/x"]["proposed"], "")

# A folder left out of a completeness report silently is the same failure the
# report is about, so the ones that are skipped are named with the reason.
check("what is deliberately not listed is named",
      sorted(x["key"] for x in rec["not_reconciled"]), ["backups", "proposals"])

# A store that will not answer makes everything it knows about look orphaned.
_broken = image_audit.STORES[1]["reader"]


def _bang():
    raise RuntimeError("gallery down")
    yield  # pragma: no cover


image_audit.STORES[1]["reader"] = _bang
storage.ready = lambda: True
storage.manifest = lambda kind, max_results=500: FAKE.get(kind, [])
try:
    shaky = image_audit.reconcile()
finally:
    image_audit.STORES[1]["reader"] = _broken
    storage.ready, storage.manifest = _real_ready, _real_manifest
check("an unreadable store makes the answer not measured", shaky["measured"], False)
check("and is named, so its assets are not read as orphans",
      bool(shaky["stores_unread"]), True)


# =====================================================================
section("Forty orphans is not forty presses")
# =====================================================================

save_archive([
    {"id": "b1", "company": "", "public_id": "smart1-seo-images/b1",
     "url": "https://res.cloudinary.com/x/b1.webp", "filename": "b1.webp"},
    {"id": "b2", "company": "", "public_id": "smart1-seo-images/b2",
     "url": "https://res.cloudinary.com/x/b2.webp", "filename": "b2.webp"},
    {"id": "b3", "company": "", "public_id": "", "filename": "b3.webp",
     "url": "https://res.cloudinary.com/x/b3.webp"},
])
bulk = image_audit.attach_many([
    {"store": "seo_images", "id": "b1", "client": "Icon Solar"},
    {"store": "seo_images", "id": "b2", "client": "Icon Solar"},
    {"store": "seo_images", "id": "gone", "client": "Icon Solar"},
], actor="todd")
check("the ones that worked are counted", bulk["attached"], 2)
# A bulk action that reports one number hides the two that failed.
check("and the one that did not is counted apart", bulk["failed"], 1)
check("with its own reason", bool(bulk["failures"][0]["error"]), True)
# "Attached" and "attached in both places" are different outcomes.
check("reaching a gallery is counted separately", bulk["gallery_filed"], 2)

check("an empty selection is refused",
      http.post("/api/image-audit/attach-many",
                json={"items": []}).status_code, 400)

# Filing a Cloudinary orphan is a different job from attaching a store row:
# there is no store row, and its absence is the finding.
check("an orphan with no client is refused",
      image_audit.file_orphan("smart1-cutouts/x", "https://c/x.png", " ")["ok"],
      False)
check("an orphan with no stored URL is refused",
      image_audit.file_orphan("smart1-cutouts/x", "notaurl", "Icon Solar")["ok"],
      False)
filed = image_audit.file_orphan("smart1-cutouts/icon/van",
                                "https://res.cloudinary.com/x/van.png",
                                "Icon Solar", "cutouts", actor="todd")
check("a real one is filed into the gallery", filed["ok"], True)
check("and reports the gallery write", filed["gallery_filed"], True)

# Both write, so both are POSTs a prefetch cannot fire.
for path in ("/api/image-audit/attach-many", "/api/image-audit/reconcile",
             "/api/image-audit/file-orphan"):
    check(f"{path} is a POST", http.get(path).status_code, 405)
check("and the page offers the bulk bar and the account listing",
      'id="bulkBar"' in (ROOT / "hub" / "templates" / "image_audit.html").read_text()
      and 'id="reconGo"' in (ROOT / "hub" / "templates" / "image_audit.html").read_text(),
      True)


# ---------------------------------------------------------------------------
section("An attached orphan is filed the way its own tool files")
# ---------------------------------------------------------------------------
# `file_orphan()` ran two vocabularies through one door: it handed the
# RECONCILE_KINDS *folder key* through as the provider, and looked the kind up
# in `_KIND_FOR`, which is keyed on STORES names. Only `seo_images` is in both,
# so eight of the nine fell through to "upload" -- which filing.SOURCE_LABELS
# calls "Client upload". Attaching an orphaned commercial put it in the
# client's gallery labelled as a file the client sent us, under a bare
# `commercials` chip the gallery has no heading for, in the tier that claims
# nothing. A sweep, not a list of the eight: the gallery must be able to name
# whatever any folder key files under.
from hub.image_audit import RECONCILE_KINDS, _FOLDER_FILING       # noqa: E402
from modules.image_picker.filing import (                         # noqa: E402
    SOURCE_LABELS, THEIRS, WE_MADE)

_unnamed, _as_client_upload = [], []
for _key, _label in RECONCILE_KINDS:
    _kind, _provider = _FOLDER_FILING.get(_key, ("upload", "cloudinary"))
    if _provider not in SOURCE_LABELS:
        _unnamed.append(_key)
    if SOURCE_LABELS.get(_provider) == "Client upload":
        _as_client_upload.append(_key)

check("every reconcilable folder files under a heading the gallery has",
      _unnamed, [])
check("and nothing we made is filed as a file the client sent",
      _as_client_upload, [])
check("a commercial is filed as commercial stills",
      SOURCE_LABELS.get(_FOLDER_FILING["commercials"][1]), "Commercial stills")
check("and a photo sent with a social request is theirs, not stock",
      _FOLDER_FILING["social_requests"][1] in THEIRS, True)

# The live producer writes this provider and the table never named it, so a
# photograph a location manager sent in arrived as a bare key under no heading.
check("the provider modules/social_planner actually files under is named",
      SOURCE_LABELS.get("social_request"), "Sent with a social request")

# ---------------------------------------------------------------------------
# ...and the direction that assertion cannot cover.
#
# Every check above runs from the TABLE outwards: what PRODUCERS declares must
# have a heading. That catches a label somebody forgot to write. It cannot
# catch a value somebody forgot to declare -- and those are different
# failures, of which only the second is silent, because the file is filed and
# every count on every screen stays correct while the gallery draws it under a
# bare key.
#
# It has happened twice. `social_request` was found by somebody opening a
# client's gallery, and is recorded above as one assertion about one string;
# `animated_ad` arrived the same way one release later. A list of the two we
# fixed proves nothing about the third, so this asks every producer module.
check("nothing files under a provider this table never declared",
      image_audit.undeclared_providers(), [])

# The Display Ad Builder is the one that found it: it files stills and, since
# animations are delivered one approved file at a time, animated versions too.
check("the animated provider is declared on the tool that writes it",
      "animated_ad" in dict((p["key"], p["provider"])
                            for p in image_audit.PRODUCERS)["display_ads"], True)
check("and the gallery can name it", SOURCE_LABELS.get("animated_ad"),
      "Animated display ads")

# A value decided at runtime is NAMED as unknowable rather than guessed at --
# the rule tools/linkcheck.py applies to a URL built by concatenation. What is
# knowable is a literal, and a module-level constant holding one, which is how
# hub/ad_builder_link.py actually writes it.
_written = image_audit.written_providers("hub/ad_builder_link.py")
check("a module constant used as provider= is resolved",
      "animated_ad" in _written, True)
check("and so is a literal", "display_ad" in _written, True)

# ...and the check bites. Take the declaration away and the finding comes
# back, naming the tool and the value -- a check that reads green either way
# is one nobody can trust.
_row = next(p for p in image_audit.PRODUCERS if p["key"] == "display_ads")
_keep = _row["provider"]
_row["provider"] = [v for v in _keep if v != "animated_ad"]
try:
    _found = image_audit.undeclared_providers()
finally:
    _row["provider"] = _keep
check("undeclaring one brings the finding back",
      [(f["producer"], f["provider"]) for f in _found],
      [("display_ads", "animated_ad")])
check("and it says what it costs",
      "bare key" in (_found[0]["cost"] if _found else ""), True)
check("with the declaration restored, it is green again",
      image_audit.undeclared_providers(), [])


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
