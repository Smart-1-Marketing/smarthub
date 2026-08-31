"""One image per post, and a badge that counts posts you can reach.

    python3 test_blog_images.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, so it never touches /var/data or the real one. Nothing here
reaches OpenAI or Cloudinary: both are stubbed, because what is worth
asserting is what this module does around them.

## Why this file exists

`hub/blog_images.py` generates the featured image every blog post needs before
it can be published, holds it as `pending` until somebody looks at it, and
files the approved one into the client's gallery. It is the last step of a
month of blog work and no test named it. Five things it did quietly:

**Two posts, one picture.** The Cloudinary object was named from the post's
*title* with `overwrite=True` and `unique_filename=False`. `hub/seo.py`'s plan
tops a short model answer up from a list of **six** fallback titles and
**cycles** it, so a client on twelve posts a month gets each of those titles
twice, verbatim, in one plan. Both posts then generate into the same object:
post 3's featured image becomes post 9's picture, at the same URL, in the
store, in the gallery and on the client's live site. Approving the second
overwrote the first's approved, filed copy as well. A long title reached the
same collision through the 60-character truncation.

**A badge counting posts that had left the list.** `/api/seo/blogs` filters
`archived` out of the working list and says so in a comment; `status()` did
not, and its number is drawn as a badge on a Blogs section that is collapsed
by default. Archiving a post with a pending image left "1 image to approve"
above a table with no row to click — amber for ever, with nothing to clear it.

**A 3 MB hero, filed silently.** `_optimise_bytes()` returns nothing at all
when Pillow cannot read the bytes and `staged or raw` fell back to the
original, which is right. Saying nothing was not: the module's own docstring
calls a 3 MB PNG "a Core Web Vitals problem on the very page the post was
written to rank", and it went into the gallery with every screen reporting a
clean success.

**An unapproved image offered as a finished asset.** Pending images sit in
`seo_images/<client>/Blogs/pending/`, which `hub/image_audit.reconcile()`
lists — and no store here had a row for one, so it read as an orphan, with a
client picker beside it. One press files the six-fingered plumber into the
client's gallery. The `pending/` folder exists to prevent exactly that.

**A folder written after the save.** `approve()` assigned `gallery_folder`
onto a reference into the store *after* `save_store()`, so the value reached
the browser and the next read of the record had never heard of it.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1blogimg_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "blog-images-test-secret"

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


from hub import blog_images as BI                             # noqa: E402
from hub import seo as _seo                                   # noqa: E402


def named(post):
    """The Cloudinary name, guarded: a regression must name itself rather than
    ending the run, which is the shape every test file here now uses."""
    try:
        return BI.image_name(post)
    except Exception as exc:                                  # noqa: BLE001
        return f"raised {type(exc).__name__}"


def stat(store):
    _seo.load_store = lambda client: store
    try:
        return BI.status("Acme Tyre")
    except Exception as exc:                                  # noqa: BLE001
        return {"raised": type(exc).__name__}


# =====================================================================
section("Two posts, one picture")
# =====================================================================
# hub/seo.py: `while len(posts_meta) < len(slots): posts_meta.append(
#     {"title": fillers[i % len(fillers)], ...})` -- six titles, cycled.
FILLERS = ["How to choose the right hvac provider",
           "5 questions to ask before hiring a hvac company",
           "Seasonal hvac checklist",
           "The real cost of hvac: what to expect",
           "Common hvac mistakes and how to avoid them",
           "Hvac FAQs answered by the pros"]
plan = [{"id": i + 1, "title": FILLERS[i % len(FILLERS)]} for i in range(12)]

check("a twelve-post plan can hold six distinct titles",
      len({p["title"] for p in plan}), 6)
check("post 3 and post 9 are titled the same thing",
      plan[2]["title"] == plan[8]["title"], True)
check("and they are two different posts",
      plan[2]["id"] == plan[8]["id"], False)
check("so they must not share one image",
      named(plan[2]) == named(plan[8]), False)
check("every post in the plan gets its own name",
      len({named(p) for p in plan}), 12)

# The 60-character truncation is a second route to the same collision, and
# closing it was not a separate fix: the id is what makes the name unique, so
# two titles that truncate alike are still two names.
LONG_A = {"id": 4, "title":
          "How Often Should You Replace Your HVAC Air Filter in Indianapolis"}
LONG_B = {"id": 5, "title":
          "How Often Should You Replace Your HVAC Air Filter in Indiana"}
check("two long titles still slugify alike",
      named(LONG_A)[:-2], named(LONG_B)[:-2])
check("and are still two objects", named(LONG_A) == named(LONG_B), False)

check("the slug leads, so the account is readable",
      named({"id": 3, "title": "Seasonal HVAC Checklist"}),
      "seasonal-hvac-checklist-3")
check("a post with no title is still named by its id",
      named({"id": 7}), "post-7")
# A row written before this carries a public_id derived from the title alone,
# and `_promote()` / `_file_in_gallery()` read the STORED id rather than
# deriving one -- so nothing is re-keyed. A post with no id at all falls back
# to exactly the old spelling rather than inventing one.
check("and a post with no id keeps the old spelling",
      named({"title": "Seasonal HVAC Checklist"}), "seasonal-hvac-checklist")
check("nothing is ever named for nothing", named({}), "post")


# =====================================================================
section("A badge counts the posts you can click")
# =====================================================================
# /api/seo/blogs: "Archived posts stay in the store but leave the working
# list." The badge is drawn on the head of a section that shows that list.

STORE = {"blogs": {"posts": [
    {"id": 1, "status": "written", "image": {"status": "pending"}},
    {"id": 2, "status": "written", "archived": True,
     "image": {"status": "pending"}},
    {"id": 3, "status": "written", "archived": True},
    {"id": 4, "status": "written", "image": {"status": "approved"}},
    {"id": 5, "status": "planned"},
]}}
s = stat(STORE)
check("one pending image is reachable", s.get("pending"), 1)
check("and it is the post the working list shows", s.get("pending_ids"), [1])
# Five posts, two archived and one still planned: posts 1 and 4 are what the
# working list shows as written, and they are what the badge is about.
check("an archived post is not counted as written", s.get("written"), 2)
check("nor as one without an image", s.get("without_image"), 0)
check("an approved image still counts", s.get("approved"), 1)

# Dropping the archived one silently would be the other failure: a badge that
# quietly gets shorter cannot be told from one that failed to load, and the
# file it left in pending/ is real.
check("what left the badge is counted rather than dropped",
      s.get("archived_pending"), 1)
check("and the note says nobody is going to approve those",
      "archived posts" in s.get("note", ""), True)

# A book with nothing archived reads exactly as it did before.
PLAIN = {"blogs": {"posts": [
    {"id": 1, "status": "written", "image": {"status": "approved"}},
    {"id": 2, "status": "written", "image": {"status": "approved"}},
]}}
p = stat(PLAIN)
check("every written post done reads as done",
      p.get("note"), "Every written post has an approved image.")
check("and says nothing about archived posts",
      "archived" in p.get("note", ""), False)
check("an empty book does not raise", stat({}).get("written"), 0)


# =====================================================================
section("An image that could not be optimized says so")
# =====================================================================
check("Pillow answering with nothing is what it answers",
      BI._optimise_bytes(b"not an image"), b"")

_uploaded = {}


def _fake_stage(client, post, data, pending=False):
    _uploaded["bytes"] = len(data)
    _uploaded["name"] = BI.image_name(post)
    return {"url": "https://res.cloudinary.test/x.png",
            "public_id": f"seo/acme/Blogs/pending/{BI.image_name(post)}"}


BI._stage = _fake_stage
_store = {"blogs": {"posts": [
    {"id": 3, "title": "Seasonal HVAC Checklist", "status": "written",
     "content": "<p>Check the filter.</p>"}]}}
_seo.load_store = lambda client: _store
_seo.save_store = lambda client, s: None

import hub.ai as _ai                                          # noqa: E402
_ai.image = lambda *a, **kw: b"x" * 3_000_000                 # unreadable bytes

out = BI.generate("Acme Tyre", 3)
img = out.get("image") or {}
check("an unreadable image is still stored", out.get("ok"), True)
check("at its real size", img.get("bytes"), 3_000_000)
check("and it says the run is on the record", img.get("optimised"), False)
check("in words, on the record", "not optimized" in img.get("note", ""), True)
check("naming the size, because that is the number to act on",
      "2929 KB" in img.get("note", ""), True)

# A picture Pillow CAN read is smaller and claims nothing.
try:
    import io as _io
    from PIL import Image as _Image
    _b = _io.BytesIO()
    _Image.new("RGB", (2000, 1400), (30, 90, 140)).save(_b, "PNG")
    _ai.image = lambda *a, **kw: _b.getvalue()
    _store["blogs"]["posts"][0].pop("image", None)
    ok = BI.generate("Acme Tyre", 3)
    oimg = ok.get("image") or {}
    check("a readable image is shrunk", oimg.get("optimised"), True)
    check("and says nothing about full size",
          "not optimized" in oimg.get("note", ""), False)
    check("and is capped rather than left at 2000px",
          _uploaded["bytes"] < len(_b.getvalue()), True)
except ImportError:
    # Said out loud rather than skipped in silence: an unrun path reported as
    # a clean run is the thing this repo keeps having to undo.
    print("  note  Pillow is not installed, so the shrinking half was not run")


# =====================================================================
section("The upload is named for the post, not the title")
# =====================================================================
_store["blogs"]["posts"] = [
    {"id": 3, "title": "Seasonal HVAC Checklist", "status": "written",
     "content": "<p>a</p>"},
    {"id": 9, "title": "Seasonal HVAC Checklist", "status": "written",
     "content": "<p>b</p>"}]
_ai.image = lambda *a, **kw: b"x" * 1000
BI.generate("Acme Tyre", 3)
first = _uploaded["name"]
BI.generate("Acme Tyre", 9)
check("the two identically-titled posts upload to two names",
      first == _uploaded["name"], False)
check("the second is its own object", _uploaded["name"],
      "seasonal-hvac-checklist-9")
by_id = {p["id"]: p for p in _store["blogs"]["posts"]}
check("and each post keeps its own picture",
      by_id[3]["image"]["public_id"] == by_id[9]["image"]["public_id"], False)


# =====================================================================
section("Approving writes down where it was filed")
# =====================================================================
# `img` is a reference INTO the store, so assigning to it after save_store()
# left the value in memory alone: the browser was handed a gallery_folder the
# next read of the record had never heard of. The save that comes first is
# kept, because a gallery that is unavailable must not cost an approval.

_saves = []
_store["blogs"]["posts"] = [
    {"id": 3, "title": "Seasonal HVAC Checklist", "status": "written",
     "image": {"status": "pending", "url": "https://x/p.webp",
               "public_id": "seo/acme/Blogs/pending/seasonal-hvac-checklist-3"}}]
_seo.load_store = lambda client: _store
_seo.save_store = lambda client, s: _saves.append(
    __import__("copy").deepcopy(s))
BI._promote = lambda c, p, i: {"url": "https://x/final.webp",
                               "public_id": "seo/acme/Blogs/s-3",
                               "note": "Filed under Blogs."}
BI._file_in_gallery = lambda c, p, i, s: "Blogs"

res = BI.approve("Acme Tyre", 3, actor="todd@smart1marketing.com")
check("approving reports the gallery it filed into", res.get("gallery"), "Blogs")
check("and the store remembers it",
      _saves[-1]["blogs"]["posts"][0]["image"].get("gallery_folder"), "Blogs")
check("the approval itself was written before the gallery was touched",
      _saves[0]["blogs"]["posts"][0]["image"].get("status"), "approved")

# A gallery that will not answer must not cost the approval, and must not
# write a folder nothing was filed into.
_saves.clear()
_store["blogs"]["posts"][0]["image"] = {
    "status": "pending", "url": "https://x/p.webp",
    "public_id": "seo/acme/Blogs/pending/s-3"}
BI._file_in_gallery = lambda c, p, i, s: ""
res = BI.approve("Acme Tyre", 3, actor="todd@smart1marketing.com")
check("a gallery that answered nothing still leaves an approval",
      res.get("ok"), True)
check("recorded in the store",
      _saves[-1]["blogs"]["posts"][0]["image"].get("status"), "approved")
check("and claims no folder", "gallery_folder" in
      _saves[-1]["blogs"]["posts"][0]["image"], False)


# =====================================================================
section("A pending image is not an unattached asset")
# =====================================================================
# hub/image_audit.reconcile() lists seo_images/<client>/Blogs/pending/ like
# any other folder. Nothing had a row for what is in it, so an image nobody
# has approved was offered with a client picker beside it.
from hub import image_audit                                   # noqa: E402
from hub import jsonstore                                     # noqa: E402

os.makedirs(_seo._store_base(), exist_ok=True)
jsonstore.write_json(
    os.path.join(_seo._store_base(), "acme-tyre.json"),
    {"client": "Acme Tyre", "blogs": {"posts": [
        {"id": 3, "title": "Seasonal HVAC Checklist",
         "image": {"status": "pending", "url": "https://x/1.webp",
                   "public_id": "seo/acme-tyre/Blogs/pending/s-3"}},
        {"id": 4, "title": "Spring Tune-Up",
         "image": {"status": "approved", "url": "https://x/2.webp",
                   "public_id": "seo/acme-tyre/Blogs/s-4",
                   "approved_at": "2026-08-01T00:00:00+00:00"}},
        {"id": 5, "title": "Nothing generated yet"},
    ]}})

check("the audit has a reader for blog images",
      any(s["key"] == "blog_images" for s in image_audit.STORES), True)
try:
    rows = list(image_audit._blog_images())
except Exception as exc:                                      # noqa: BLE001
    rows = [{"raised": type(exc).__name__}]
ids = {r.get("public_id") for r in rows}
check("a pending image is known to it",
      "seo/acme-tyre/Blogs/pending/s-3" in ids, True)
check("so is an approved one", "seo/acme-tyre/Blogs/s-4" in ids, True)
check("a post with no image contributes nothing", len(rows), 2)
check("and each row names its client",
      {r.get("client") for r in rows}, {"Acme Tyre"})
check("the status travels, so the report can say which is which",
      sorted(r.get("where") for r in rows), ["approved", "pending"])

known, failed = image_audit.known_public_ids()
check("the reader reaches known_public_ids",
      "seo/acme-tyre/Blogs/pending/s-3" in known, True)
check("and it is not reported as a store that would not answer",
      [f for f in failed if "Blog" in f.get("label", "")], [])


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
