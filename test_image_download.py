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

print(f"\n{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
