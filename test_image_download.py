"""SEO Image Pipeline downloads and the shared zip builder — test harness.

    python3 test_image_download.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway SQLite database and a temporary data directory, so it never touches
/var/data or the real one. Cloudinary is not configured and no network call is
made — the one fetch the zip builder does is stubbed at the requests seam.

## What is worth asserting

  * **A download has to actually download.** The `download` attribute on an
    <a> is ignored cross-origin, so a plain Cloudinary link opens the image in
    a tab and the button looks broken. `fl_attachment` is the only thing that
    works, and the name after it is the SEO filename — which in this tool is
    the deliverable. A file that lands in Downloads as "v1699_xk3.webp" has
    lost the work the tool exists to do.

  * **A partial zip beats an error page.** One unreachable file must not fail
    the other nineteen, and what is missing has to be named rather than
    silently absent — a zip that is quietly short is worse than one that says
    so.

  * **Two files can share a name.** Without de-duplication the zip keeps only
    the last one and the count drops with nothing reporting it.

  * **Every archive row is addressable.** The row's buttons all address it by
    id, and the Image Optimizer's save path wrote rows without one — those
    images appeared in the archive and then ignored every button on their row.

  * **A gallery draws a preview, not the original.** Every tile in this Hub
    asked for the full asset to fill a box a fraction of its size, so a client
    who uploads forty phone photographs made the staff gallery deliver about a
    hundred and sixty megabytes to draw forty 64x48 boxes. Nothing errors and
    the pictures are right; the cost is a slow page and a Cloudinary bill,
    which is charged in credits of a gigabyte delivered. `thumb_url()` existed
    for exactly this and had no caller at all.

    What is asserted is the four ways rewriting a URL breaks the tile it was
    meant to speed up — something that is not ours, something that is not an
    image, doing it twice, and upscaling — and the one place it must NOT
    happen: the copy button and the CSV export hand out markup that goes onto
    the client's own website, and a gallery thumbnail in it is the wrong file
    on their page for ever.
"""
import io
import os
import shutil
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-imgdl-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ.setdefault("SECRET_KEY", "image-download-test")
os.environ.setdefault("PANEL_PASSWORD", "test")
os.environ["HUB_DATA_DIR"] = _TMP

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


# The composed app is imported once, up front. wsgi.py attaches an error
# handler to every mounted app at import time, and Flask refuses that after an
# app has served its first request — so importing it later, after a module's
# own test client has been used, fails with an unrelated-looking AssertionError.
from wsgi import application                              # noqa: E402
from werkzeug.test import Client as _WClient              # noqa: E402

from hub import storage                                   # noqa: E402

CDN = "https://res.cloudinary.com/demo/image/upload/v1/smart1-seo-images/acme/spring/"


# ------------------------------------------------------- attachment_url
print("\na link that downloads instead of opening")
url = CDN + "ac-repair-columbus.webp"
att = storage.attachment_url(url, "ac-repair-columbus.webp")
check("fl_attachment is inserted into the delivery URL",
      "/upload/fl_attachment:ac-repair-columbus/" in att, att)
check("the SEO filename is what the file is called on the way down",
      att.split("fl_attachment:")[1].split("/")[0] == "ac-repair-columbus", att)
check("the rest of the URL is untouched", att.endswith("ac-repair-columbus.webp"), att)
check("a URL with no filename still downloads",
      "fl_attachment/" in storage.attachment_url(url, ""),
      storage.attachment_url(url, ""))
check("a non-Cloudinary URL is returned unchanged, not rewritten into a 404",
      storage.attachment_url("https://example.com/a/b.png", "b.png")
      == "https://example.com/a/b.png")
check("an empty URL stays empty", storage.attachment_url("", "x.webp") == "")


# ------------------------------------------------------------ bundle_zip
print("\nthe shared zip builder")


class _Resp:
    def __init__(self, content, ok=True):
        self.content = content
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("404")


_FETCHED = []


def _fake_get(url, timeout=25):
    _FETCHED.append(url)
    if "broken" in url:
        return _Resp(b"", ok=False)
    return _Resp(b"PNGDATA-" + url.encode()[-12:])


import requests                                            # noqa: E402
_real_get = requests.get
requests.get = _fake_get
try:
    blob, missing = storage.bundle_zip([
        {"url": CDN + "one.webp", "filename": "one.webp"},
        {"url": CDN + "two.webp", "filename": "one.webp"},      # same name
        {"url": CDN + "broken.webp", "filename": "gone.webp"},
        {"url": "", "filename": "norecord.webp"},
    ], bucket="seo_images")
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    check("both files are in the zip despite sharing a name",
          "one.webp" in names and "one-2.webp" in names, names)
    check("an unreachable file does not fail the whole download",
          "one.webp" in names, names)
    check("what is missing is named in the zip", "MISSING.txt" in names, names)
    check("and returned to the caller so the page can say so too",
          sorted(missing) == ["gone.webp", "norecord.webp"], missing)
    note = zipfile.ZipFile(io.BytesIO(blob)).read("MISSING.txt").decode()
    check("MISSING.txt names each one", "gone.webp" in note and "norecord.webp" in note, note)
    check("a row with no URL is not fetched",
          not any(u == "" for u in _FETCHED), _FETCHED)

    empty, missing2 = storage.bundle_zip([])
    check("an empty selection produces an empty zip, not a crash",
          zipfile.ZipFile(io.BytesIO(empty)).namelist() == [] and missing2 == [])

    over = storage.bundle_zip([{"url": CDN + f"{i}.webp", "filename": f"{i}.webp"}
                               for i in range(storage.ZIP_MAX_FILES + 25)])[0]
    check("the file count is capped rather than unbounded",
          len(zipfile.ZipFile(io.BytesIO(over)).namelist()) <= storage.ZIP_MAX_FILES,
          len(zipfile.ZipFile(io.BytesIO(over)).namelist()))
finally:
    requests.get = _real_get


# -------------------------------------------------- the archive and routes
print("\nthe archive rows and the download routes")
from modules.seo_images import app as seo_images           # noqa: E402

seo_images.save_archive([
    {"id": "aaa", "company": "Acme HVAC", "project": "Spring refresh",
     "url": CDN + "one.webp", "filename": "ac-repair-columbus.webp",
     "seo_filename": "ac-repair-columbus", "alt_text": "AC repair van",
     "bytes": 41000, "saved_at": "2026-08-01 10:00"},
    {"id": "bbb", "company": "Acme HVAC", "project": "Spring refresh",
     "url": CDN + "two.webp", "filename": "furnace-tune-up.webp",
     "seo_filename": "furnace-tune-up", "alt_text": "Technician",
     "bytes": 38000, "saved_at": "2026-08-01 10:01"},
    # exactly the shape the Image Optimizer used to write: no id at all
    {"company": "Acme HVAC", "url": CDN + "three.webp",
     "filename": "optimized.webp", "alt_text": "", "project": "Optimized"},
])

rows = seo_images.load_archive()
check("a row saved without an id is given one on read",
      all(r.get("id") for r in rows), [r.get("id") for r in rows])
first_backfill = [r["id"] for r in rows]
check("and the id it was given is stable across reads",
      [r["id"] for r in seo_images.load_archive()] == first_backfill)

# Through the composed app at its real mount, not the bare module app: a
# module route written as a bare path works standalone and 404s under the
# mount, which is the trap tools/linkcheck.py exists for.
client = _WClient(application)
client.post("/login", data={"password": os.environ["PANEL_PASSWORD"], "name": "Tester"})
SEO = "/tools/seo-images"

r = client.get(SEO + "/api/gallery/download?id=aaa")
check("a single download redirects to the CDN, not through the Hub",
      r.status_code in (301, 302), r.status_code)
check("and the redirect carries fl_attachment with the SEO filename",
      "fl_attachment:ac-repair-columbus" in r.headers.get("Location", ""),
      r.headers.get("Location"))

r = client.get(SEO + "/api/gallery/download?id=nope")
check("an unknown id is a 404, not a redirect to nowhere", r.status_code == 404)

r = client.get(SEO + "/api/gallery/download.zip")
check("a zip with no ids is refused with a reason",
      r.status_code == 400 and b"error" in r.data, r.status_code)
r = client.get(SEO + "/api/gallery/download.zip?ids=nope,alsonope")
check("a zip of ids that are not in the archive is a 404", r.status_code == 404)

requests.get = _fake_get
try:
    r = client.get(SEO + "/api/gallery/download.zip?ids=aaa,bbb")
    check("a multi-file download returns a zip",
          r.status_code == 200 and r.mimetype == "application/zip", r.status_code)
    check("named for the client when they all belong to one",
          "acme-hvac-images-" in r.headers.get("Content-Disposition", ""),
          r.headers.get("Content-Disposition"))
    inner = zipfile.ZipFile(io.BytesIO(r.data)).namelist()
    check("the files inside carry their SEO filenames",
          sorted(inner) == ["ac-repair-columbus.webp", "furnace-tune-up.webp"], inner)
finally:
    requests.get = _real_get

check("the download routes are on the module's own app, under its mount",
      {"/api/gallery/download", "/api/gallery/download.zip"} <=
      {str(rule) for rule in seo_images.app.url_map.iter_rules()})


# ------------------------------------- the image picker uses the same builder
# The picker's gallery download was the only copy of this zip builder before
# it moved to hub/storage.py. Exercised end to end, because "it still imports"
# is not evidence that the route still returns a zip.
print("\nthe image picker's gallery download, on the shared builder")
try:
    from modules.image_picker import app as picker
    from modules.image_picker.models import PickerClient, SavedImage

except Exception as exc:                                   # noqa: BLE001
    check("the image picker still imports", False, exc)
else:
    db = picker.session()
    row_client = PickerClient(name="Zip Test Co", slug="zip-test-co",
                              kind="prospect", industry_key="general")
    db.add(row_client)
    db.commit()
    img = SavedImage(client_id=row_client.id, provider="pexels",
                     provider_image_id="1", filename="hero-shot.jpg",
                     cloudinary_url=CDN + "one.webp",
                     cloudinary_public_id="x/hero-shot", resource_type="image")
    db.add(img)
    db.commit()

    requests.get = _fake_get
    try:
        r = client.get(f"/tools/image-picker/api/saved/download.zip"
                       f"?client_id={row_client.id}&ids={img.id}")
        check("the picker still returns a zip after the migration",
              r.status_code == 200 and r.mimetype == "application/zip",
              f"{r.status_code} {r.data[:120]!r}")
        if r.status_code == 200:
            check("with the row's own filename inside",
                  "hero-shot.jpg" in zipfile.ZipFile(io.BytesIO(r.data)).namelist(),
                  zipfile.ZipFile(io.BytesIO(r.data)).namelist())
    finally:
        requests.get = _real_get


# --------------------------------------------- the page still offers them
print("\nthe page")
r = client.get(SEO + "/")
html = r.get_data(as_text=True)
check("the pipeline page renders", r.status_code == 200, r.status_code)
for marker, label in [
        ("btnArchDl", "the archive has a download-selected button"),
        ("btnDlAll", "the saved step has a download-all button"),
        ("btnDlSel", "the saved step has a download-selected button"),
        ("archSelAll", "the archive has a select-all checkbox"),
        ("api/gallery/download.zip", "the zip route is wired to the page"),
        ('class="tip"', "the row actions carry rollover tooltips"),
        ("class=\"icon\"", "the row actions are icons")]:
    check(label, marker in html)

# The icon is a guess unless the rollover says what it does, so each of the
# four carries a titled bubble — not merely the word somewhere on the page.
for word in ("Download", "Copy URL", "Edit alt", "Delete"):
    idx = html.find(f"<b>{word}</b>")
    check(f"the {word} icon has a rollover that explains it",
          idx > 0 and len(html[idx:idx + 260].split("</span>")[0]) > len(word) + 20,
          html[idx:idx + 120] if idx > 0 else "no tooltip found")

# =====================================================================
# A gallery draws a preview, and the deliverable keeps the original
# =====================================================================
print("\nA gallery draws a preview, not the original")
print("-------------------------------------------")

_img = "https://res.cloudinary.com/demo/image/upload/v1/smart1/acme/hero.jpg"

_t = storage.preview_url(_img)
check("an image URL of ours is capped", _t != _img and "c_limit" in _t, _t)
check("with no upscale in it", "c_fill" not in _t and "c_scale" not in _t, _t)
check("and the format left to the CDN", "f_auto" in _t and "q_auto" in _t, _t)
check("the public_id survives the rewrite", _t.endswith("/v1/smart1/acme/hero.jpg"), _t)

# Rewriting a URL we do not own turns a picture into a 404. The same answer
# attachment_url() gives, for the same reason.
for _foreign in ("https://images.pexels.com/photos/1/x.jpg",
                 "https://drive.google.com/file/d/abc/view",
                 "data:image/png;base64,AAA", ""):
    check(f"left alone: {_foreign[:34] or '(empty)'}",
          storage.preview_url(_foreign) == _foreign)

# Cloudinary keeps images, raw files and video in separate namespaces, so an
# image transformation on a PDF comes back "not found" — the lesson
# cloudinary_sink.destroy() paid for. Believed from the row's own column, and
# from the URL's own segment when the row says nothing.
_pdf = "https://res.cloudinary.com/demo/raw/upload/v1/smart1/acme/spec.pdf"
_vid = "https://res.cloudinary.com/demo/video/upload/v1/smart1/acme/spot.mp4"
check("a raw file is not transformed", storage.preview_url(_pdf) == _pdf)
check("nor a video", storage.preview_url(_vid) == _vid)
check("a row that declares raw is believed over the URL",
      storage.preview_url(_img, "raw") == _img)
check("and one that declares image is still rewritten",
      storage.preview_url(_img, "image") != _img)

# A row rewritten here and handed to a caller that rewrites again must not
# chain two transformations.
check("rewriting twice changes nothing", storage.preview_url(_t) == _t, _t)

# The archive derives it on read rather than storing it: this index is
# mirrored into the database, so a stored preview would be restored rather
# than recomputed and would outlive the size it was computed at.
from modules.seo_images import app as _seo                 # noqa: E402
_seo.save_archive([
    {"id": "aa", "company": "Acme", "url": _img, "seo_filename": "hero.jpg"},
    {"id": "bb", "company": "Acme", "url": "https://images.pexels.com/p/2.jpg"},
])
_rows = {r["id"]: r for r in _seo.load_archive()}
check("an archive row carries a preview", "c_limit" in _rows["aa"]["thumb"])
check("and its full asset is untouched", _rows["aa"]["url"] == _img)
check("a row that is not ours previews as itself",
      _rows["bb"]["thumb"] == _rows["bb"]["url"])
check("nothing is written back into the stored index",
      all("thumb" not in r for r in
          __import__("hub.jsonstore", fromlist=["x"]).read_json(_seo._INDEX_PATH, default=[])))

# The tiles ask for it, and the two places that hand out markup do not: a
# copy button and the CSV export are read by the client's own website, and a
# 400px gallery thumbnail pasted there is the wrong file on their page.
_gal = open(os.path.join(ROOT, "modules/seo_images/templates/gallery.html"),
            encoding="utf-8").read()
check("the gallery tile draws the preview", "esc(r.thumb||r.url)" in _gal)
check("its link still opens the original",
      'href="${esc(r.url)}"' in _gal)
check("the copy button still emits the original",
      'copy(\'<img src="\'+r.url+' in _gal)

_c360 = open(os.path.join(ROOT, "hub/templates/client360.html"),
             encoding="utf-8").read()
check("the client record's tiles draw the preview",
      "esc(r.thumb||r.url)" in _c360 and "esc(item.thumb||item.url)" in _c360)

_pick = open(os.path.join(ROOT, "modules/image_picker/templates/picker_gallery.html"),
             encoding="utf-8").read()
check("so does the client gallery", "esc(im.thumb || im.url)" in _pick)

# Falling back rather than branching: a row from a producer nothing has wired
# yet draws exactly what it drew before. Written so a regression is reported
# rather than raised -- a check that dies takes every check after it with it,
# which is how the completeness scan below came to be silently skipped.
check("a row with no preview falls back to the asset",
      "esc(im.thumb || im.url)" in _pick)


# =====================================================================
# And the next tile somebody adds
# =====================================================================
print("\nEvery tile bound to a stored URL, and why each is what it is")
print("-----------------------------------------------------------")

# Every <img> bound to a stored URL either draws a preview or is named here
# with the reason it does not -- a screen silently missing from a completeness
# report is the same failure the report is about. No counts in this comment on
# purpose: the first draft carried two, they were wrong, and a paragraph that
# contradicts the rows beneath it costs the rows their credibility.
#
# Keyed on a marker from the line itself, so an entry cannot go on covering
# whatever is written at that path next -- which is not hypothetical. The
# Display Ad Builder's background grid was exempted here as "the one this
# change cannot reach from Python", and when its rows turned out to come from
# hub/ad_builder_link.py after all, this check is what reported the exemption
# as stale rather than letting it sit.
_LOGO = ("a logo, drawn at its own size, one per page, and as often observed "
         "off the client's own site as stored by us")

FULL_ASSET_ON_PURPOSE = {
    ("modules/seo_images/templates/gallery.html", "copy('<img"):
        "the copy button hands out markup for the client's own website, and a "
        "400px gallery thumbnail pasted there is the wrong file on their page",
    ("modules/seo_images/templates/index.html", "return '<img src=\"'+s.url"):
        "same: the snippet a rep copies into a page",
    ("hub/templates/seo_client.html", "max-width:100%"):
        "the lightbox — asking for the full asset is what it is for",
    ("modules/page_image_optimizer/templates/page_images.html", "esc(row.url)"):
        "the just-optimized results, reported beside their own byte counts; "
        "the file being shown is the deliverable being measured",
    ("modules/image_picker/templates/_upload_panel.html", "info.secure_url"):
        "the strip of what was just uploaded. That URL comes back from the "
        "Cloudinary widget in the browser and never passes through a row here, "
        "so previewing it would mean a copy of the rule in JavaScript",
    ("hub/templates/client360.html", "Logo on their"): _LOGO,
    ("modules/gpt_ads/templates/index.html", "esc(g.url)"):
        "the chosen 1:1 ad image, where the exact asset is the point",
    ("modules/landing_ads/templates/index.html", "max-width:300px"):
        "one preview per generated ad, with no per-row producer wired yet",
    ("modules/social_planner/templates/index.html", "esc(s.image_url)"):
        "one image per post card; the planner's rows do not carry a preview yet",
    ("modules/social_planner/templates/client_approve.html", "esc(p.image_url)"):
        "same, on the client's approval page",

    # A logo is small, is drawn at close to its own size, appears once rather
    # than in a grid, and is as often observed off the client's own site as
    # stored by us -- so there is nothing here to cap and possibly nothing of
    # ours to cap it with.
    ("hub/templates/ad_builder_start.html", "saved_logo.url"): _LOGO,
    ("hub/templates/client360.html", 'alt="Brand logo"'): _LOGO,
    ("modules/ad_builder/public/embed.html", "primary.url"): _LOGO,
    ("modules/ad_builder/public/embed.html", "l.kind || 'logo'"): _LOGO,
    ("modules/ads_builder/templates/_estimate_doc.html", "logo.url"): _LOGO,
    ("modules/ads_builder/templates/ads_proposal.html", 'alt="logo"'): _LOGO,
    ("modules/scans/templates/scan_detail.html", "mark.url"): _LOGO,
    ("modules/scans/templates/scan_detail.html", "preview.url"):
        "the Insites scan screenshot is hosted by the scan provider, not our "
        "Cloudinary account, so the Hub cannot request a derived thumbnail",

}

import pathlib
import re as _re
_BOUND = _re.compile(r"<img[^>]*\bsrc=[^>]*?(?:\.url|image_url|secure_url)")
_found, _unexplained = set(), []
for _dir in ("hub/templates", "modules"):
    for _f in sorted(pathlib.Path(os.path.join(ROOT, _dir)).rglob("*.html")):
        _rel = _f.relative_to(ROOT).as_posix()
        for _line in _f.read_text(encoding="utf-8", errors="ignore").split("\n"):
            if not _BOUND.search(_line) or "thumb" in _line:
                continue
            _hit = next((k for k in FULL_ASSET_ON_PURPOSE
                         if k[0] == _rel and k[1] in _line), None)
            if _hit:
                _found.add(_hit)
            else:
                _unexplained.append(f"{_rel}: {_line.strip()[:90]}")

check("no tile draws the full asset without a reason on file",
      not _unexplained, "\n         ".join(_unexplained))
check("every reason has a real line behind it",
      set(FULL_ASSET_ON_PURPOSE) == _found,
      "stale: " + str(sorted(set(FULL_ASSET_ON_PURPOSE) - _found)))
check("and every one of them says why",
      all(len(v) > 30 for v in FULL_ASSET_ON_PURPOSE.values()))


print(f"\n{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
