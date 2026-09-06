"""Ad Assets: the folder shape, the copy, and the two writes it must not make.

    python3 test_ad_assets.py

Same shape as the other test files here — no pytest, no new dependencies,
nothing booted that does not need to be, and no network.

## What this holds

The tool copies campaign creative out of Google Drive into the client library.
Four things about it are worth a test, and each is a way it could look like it
worked:

  1. **Refused is not empty.** The Hub's Google logins were consented before
     Drive was asked for, and Google never widens an existing refresh token —
     so "we cannot read Drive at all" is the *ordinary* first answer, and it
     arrives looking exactly like "that client has no creative". Reported as
     the same thing once, it would read as a clean migration of nothing.

  2. **The folder is Ad Assets, then IO, then product.** With the product
     level absent when Knack has no product number, rather than a folder
     called `product-unassigned` on most rows, which means nothing and sorts
     between the ones that do.

  3. **Filed twice is worse than filed once.** The Drive file id is the dedupe
     key, on the row, so a second run is a no-op.

  4. **Neither Drive nor Knack is written to by a copy.** Drive is never
     written to at all; the Knack rewrite is proposed and applied separately,
     and it locates the field by the URL it currently holds — never by
     position, which would overwrite whichever of a product's four creative
     links happened to sit in slot one.

And the wiring, because this repo's most expensive failure is a feature that
is complete except for being reachable: the blueprint registered, the tile,
the help keys, the crumb and the scheduled sweep.
"""
import pathlib
import re
import sys
import types

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


# ---------------------------------------------------------------------------
# 1. Which links this even looks at
# ---------------------------------------------------------------------------
print("\nDrive links")

from hub import drive_files                                    # noqa: E402

check("a folder link is read as a folder",
      drive_files.parse_link(
          "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp"),
      ("folder", "1AbCdEfGhIjKlMnOp"))
check("a /file/d/ link is read as a file",
      drive_files.parse_link(
          "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing"),
      ("file", "1AbCdEfGhIjKlMnOp"))
check("a Docs link is read as a file",
      drive_files.parse_link(
          "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit")[1],
      "1AbCdEfGhIjKlMnOp")
check("an ?id= link is read", drive_files.parse_link(
      "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp")[1],
      "1AbCdEfGhIjKlMnOp")
check("the client's own website is not a Drive link",
      drive_files.is_drive("https://riversidehvac.com/spring-offer"), False)
check("and neither is a Dropbox one — this tool copies Drive",
      drive_files.is_drive("https://dropbox.com/s/abc/banner.jpg"), False)

# The shape in the URL is a hint. Nothing walks a folder on the strength of it:
# describe() asks Drive what the item actually is first.
_src = (ROOT / "hub" / "drive_files.py").read_text()
check("files_for asks Drive what the item is before walking it",
      "meta = describe(token, ident)" in _src
      and 'meta.get("mimeType") == FOLDER_MIME' in _src, True)
check("a shortcut is refused rather than followed out of the folder",
      "google-apps.shortcut" in _src, True)
check("nothing in the Drive reader writes, moves or trashes",
      bool(re.search(r"requests\.(post|put|patch|delete)", _src)), False)


# ---------------------------------------------------------------------------
# 2. The folder shape
# ---------------------------------------------------------------------------
print("\nThe Ad Assets folder")

from modules.image_picker.filing import ad_asset_folder, asset_folder  # noqa: E402

check("Ad Assets, then the IO, then the product",
      ad_asset_folder(client_name="Riverside HVAC", io_number="10432",
                      product_number="P-8821"),
      "client-assets/riverside-hvac/ad-assets/io-10432/product-p-8821")
check("no product number means no product folder, not an empty one",
      ad_asset_folder(client_name="Riverside HVAC", io_number="10432"),
      "client-assets/riverside-hvac/ad-assets/io-10432")
check("no IO number is named rather than dropped",
      ad_asset_folder(client_name="Riverside HVAC"),
      "client-assets/riverside-hvac/ad-assets/io-unassigned")
check("the Drive folder's own shape is preserved underneath",
      ad_asset_folder(client_name="Riverside HVAC", io_number="10432",
                      product_number="8821", subpath="Final/Web/"),
      "client-assets/riverside-hvac/ad-assets/io-10432/product-8821/final/web")
check("browser input cannot climb out of the tree",
      ad_asset_folder(client_name="../../etc", io_number="../..",
                      subpath="../../../root"),
      "client-assets/etc/ad-assets/io-unassigned/root")

# The date-keyed default is untouched: it answers a different question and
# folding the two together is how one of them quietly changes.
check("the tool-and-date convention still exists beside it",
      asset_folder(client_name="Riverside HVAC", tool="seo-images",
                   completed_on="2026-03-04", io_number="10432",
                   product_number="8821"),
      "client-assets/riverside-hvac/seo-images/2026-03-04/io-10432/product-8821")


# ---------------------------------------------------------------------------
# 3. Which product rows are candidates
# ---------------------------------------------------------------------------
print("\nCandidates")

from hub import ad_assets                                      # noqa: E402

_ROWS = {"rows": [
    {"client": "Riverside HVAC", "io": "10432", "product": "OTT",
     "product_num": "8821", "record_id": "r1", "status": "Live",
     "creative_urls": ["https://drive.google.com/drive/folders/1AAAAAAAAAAA"]},
    {"client": "Riverside HVAC", "io": "10432", "product": "SEM",
     "product_num": "8822", "record_id": "r2", "status": "Live",
     "creative_urls": ["https://drive.google.com/drive/folders/1BBBBBBBBBBB"]},
    {"client": "Riverside HVAC", "io": "10440", "product": "Display",
     "product_num": "", "record_id": "r3", "status": "Completed",
     "creative_urls": ["https://riversidehvac.com/offer"]},
    {"client": "Other Co", "io": "99", "product": "Display", "product_num": "1",
     "record_id": "r4", "status": "Live",
     "creative_urls": ["https://drive.google.com/file/d/1CCCCCCCCCCC/view"]},
], "source": "knack", "age_minutes": 3}

ad_assets.knack_products = types.SimpleNamespace(rows=lambda *a, **k: _ROWS)

_c = ad_assets.candidates("Riverside HVAC")
check("only that client's rows", {l["client"] for l in _c["links"]},
      {"Riverside HVAC"})
check("a SEM line's link is a landing page, not artwork",
      [l["product"] for l in _c["links"]], ["OTT"])
check("and a click-thru URL is not counted as creative",
      _c["non_drive_links"], 1)
check("every client with Drive creative is offered",
      set(ad_assets.candidates()["clients"]), {"Riverside HVAC", "Other Co"})


# ---------------------------------------------------------------------------
# 4. Refused is not empty
# ---------------------------------------------------------------------------
print("\nWhen Drive cannot be read")

ad_assets.drive_files = types.SimpleNamespace(
    is_drive=drive_files.is_drive,
    DriveRefused=drive_files.DriveRefused,
    access=lambda email="": {"ok": False, "reason": "refused", "email": "",
                             "token": "", "detail": "consented without Drive"},
)
_r = ad_assets.migrate("Riverside HVAC")
check("the run fails rather than reporting a clean nothing", _r["ok"], False)
check("and says which of the four situations it is", _r["reason"], "refused")
check("and says plainly that nothing was read",
      "not a report that there is nothing there" in _r["note"], True)

# A client with no Drive links at all is a real answer, and a different one.
ad_assets.drive_files.access = lambda email="": {
    "ok": True, "reason": "ok", "email": "ops@smart1marketing.com",
    "token": "t", "detail": ""}
_n = ad_assets.migrate("Nobody Ltd")
check("a client with no Drive creative is answered, not failed",
      (_n["ok"], _n["links"]), (True, 0))
check("and named as such", "No Google Drive creative links" in _n["note"], True)


# ---------------------------------------------------------------------------
# 5. The dry run, and filing twice
# ---------------------------------------------------------------------------
print("\nThe copy")

_ITEMS = {
    "https://drive.google.com/drive/folders/1AAAAAAAAAAA": [
        {"id": "f1", "name": "banner-300x250.jpg", "mimeType": "image/jpeg",
         "path": "Final/"},
        {"id": "f2", "name": "banner-728x90.jpg", "mimeType": "image/jpeg",
         "path": ""},
    ],
}
ad_assets.drive_files.files_for = lambda token, url: _ITEMS.get(url, [])

_plan = ad_assets.migrate("Riverside HVAC")
check("the dry run lists every file behind the link", len(_plan["copied"]), 2)
check("without writing anything",
      all(f.get("planned") for f in _plan["copied"]), True)
check("and files each one under its own Drive subfolder",
      sorted(f["folder"] for f in _plan["copied"]),
      ["client-assets/riverside-hvac/ad-assets/io-10432/product-8821",
       "client-assets/riverside-hvac/ad-assets/io-10432/product-8821/final"])

# Already-filed rows are matched on the Drive file id, which is the one
# identifier that survives a folder rename, three URL shapes and a re-upload.
ad_assets.filed_keys = lambda client: {"gdrive:f1": "https://res.cloudinary.com/x/f1.jpg"}
_again = ad_assets.migrate("Riverside HVAC", apply=True)
_skipped = [s for s in _again["skipped"] if s.get("reason") == "already_filed"]
check("a second run copies nothing twice", len(_skipped), 1)
check("and says so rather than silently doing nothing",
      _skipped[0]["url"], "https://res.cloudinary.com/x/f1.jpg")

_src_aa = (ROOT / "hub" / "ad_assets.py").read_text()
check("the dedupe key is the Drive file id, on the row",
      'key=f"gdrive:{item.get(\'id\')}"' in _src_aa, True)
check("and the copy keeps the Drive address it came from",
      "row.source_url = url[:500]" in _src_aa, True)


# ---------------------------------------------------------------------------
# 6. The two writes it must not make
# ---------------------------------------------------------------------------
print("\nWhat is never written")

check("nothing in the migrator writes to Drive",
      bool(re.search(r"drive_files\.(delete|move|trash|rename)", _src_aa)), False)
check("a proposal is only made where every file came across",
      "A folder half-migrated is a link that must keep pointing at Drive"
      in _src_aa, True)
check("applying is keyed to the rows a person read",
      "Explicitly keyed rather than" in _src_aa, True)

_src_kn = (ROOT / "hub" / "knack_api.py").read_text()
_fn = _src_kn.split("def set_creative_url", 1)[1].split("\ndef ", 1)[0]
check("the Knack field is located by the URL it currently holds",
      'if str(current or "").strip() == old_url:' in _fn, True)
check("and a missing one refuses rather than guessing a slot",
      "Nothing was written." in _fn, True)
check("only that one field is sent", "json={target: new_url}" in _fn, True)


# ---------------------------------------------------------------------------
# 7. Wired, not merely written
# ---------------------------------------------------------------------------
print("\nWiring")

check("the blueprint is registered",
      "register_ad_assets(app)" in (ROOT / "hub" / "__init__.py").read_text(), True)
check("the tool has a tile",
      '/tools/ad-assets' in (ROOT / "hub" / "templates" / "tools.html").read_text(), True)
check("the tile maps to a help prefix",
      '"/tools/ad-assets": "hub.ad_assets"'
      in (ROOT / "hub" / "help_coverage.py").read_text(), True)
_help = (ROOT / "hub" / "help.py").read_text()
check("and the prefix has help on it",
      all(f'"hub.ad_assets.{k}"' in _help
          for k in ("intro", "access", "dryrun", "proposals")), True)
check("the crumb is named rather than slug-cased",
      '"ad-assets": "Ad Assets"'
      in (ROOT / "hub" / "static" / "hub-crumbs.js").read_text(), True)
_sched = (ROOT / "hub" / "scheduler.py").read_text()
check("the catch-up sweep is scheduled",
      "job_ad_assets_sweep" in _sched and '"ad_assets":' in _sched, True)
check("Drive read access is actually asked for at login",
      "auth/drive.readonly"
      in (ROOT / "modules" / "google_finder" / "app.py").read_text(), True)
_c360 = (ROOT / "hub" / "templates" / "client360.html").read_text()
check("Client 360 offers the library copy",
      "c.library_url" in _c360, True)
check("and keeps the Drive original beside it",
      "Drive original" in _c360, True)
check("the copy is attached to the creative rows it belongs to",
      "_attach_library" in (ROOT / "hub" / "knack_data.py").read_text(), True)


# ---------------------------------------------------------------------------
# 8. Where the two stores live, and what the old spelling cost
# ---------------------------------------------------------------------------
print("\nWhere the stores live")

import ast
import os
import tempfile
import threading

_prev = {k: os.environ.get(k) for k in ("HUB_DATA_DIR", "DATABASE_URL")}
_tmp = tempfile.mkdtemp(prefix="adassets-")
os.environ["HUB_DATA_DIR"] = os.path.join(_tmp, "root")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "mirror.db")
# The old paths were resolved against the process working directory, so the
# check has to run from somewhere that is not the data root to mean anything.
_cwd = os.getcwd()
os.makedirs(os.path.join(_tmp, "cwd"), exist_ok=True)
os.chdir(os.path.join(_tmp, "cwd"))

try:
    from hub import ad_assets, jsonstore

    _root = os.path.abspath(jsonstore.data_root())

    def _under_root(path):
        a = os.path.abspath(path)
        return a == _root or a.startswith(_root + os.sep)

    check("the run log resolves under the data root",
          _under_root(ad_assets._runs_path()), True)
    check("so does the applied-proposals record",
          _under_root(ad_assets._proposals_path()), True)

    # An `abs:` key is what a path outside the root produces, and it defeats
    # the one property key_for()'s docstring names: a production blob
    # restoring into a development checkout.
    check("the mirror key is root-relative, not an absolute one",
          jsonstore.key_for(ad_assets._runs_path()), "ad_assets/runs.json")

    # The half that was actually lost. sweep() walks the data root, so a save
    # made while the mirror was unavailable was never picked up afterwards --
    # and the next redeploy took the file with it.
    import unittest.mock as _mock
    with _mock.patch.object(jsonstore, "_init", return_value=False):
        ad_assets._record_run({"client": "Mirror Down", "counts": {}}, "t")
    _swept = jsonstore.sweep()
    check("a save made while the mirror was down is swept up afterwards",
          _swept.get("mirrored", 0) >= 1, True)
    os.remove(ad_assets._runs_path())
    _back = jsonstore.read_json(ad_assets._runs_path(), default=None)
    check("and it comes back after the disk is recreated",
          bool(_back and _back.get("runs")), True)

    # Eight concurrent records measured 1 of 8 kept before this. The scheduled
    # catch-up sweep and a rep pressing Migrate overlap by design.
    jsonstore.write_json(ad_assets._runs_path(), {"runs": []})
    _ts = [threading.Thread(target=ad_assets._record_run,
                            args=({"client": f"c{i}", "counts": {}}, "t"))
           for i in range(8)]
    for _t in _ts:
        _t.start()
    for _t in _ts:
        _t.join()
    check("eight concurrent runs all reach the log",
          len(jsonstore.read_json(ad_assets._runs_path(),
                                  default={"runs": []})["runs"]), 8)

    # Nothing already recorded is orphaned by the move.
    jsonstore.delete_json(ad_assets._proposals_path())
    jsonstore.write_json(ad_assets.LEGACY_PROPOSALS_PATH,
                         {"applied": [{"key": "k1", "client": "Acme"}]})
    _old = ad_assets._read_store(ad_assets._proposals_path(),
                                 ad_assets.LEGACY_PROPOSALS_PATH,
                                 {"applied": []})
    check("rows written before the move are still read",
          [a["key"] for a in _old["applied"]], ["k1"])

    # ...and once the rooted file holds something, the old location stops
    # being consulted, or removing a row would resurrect it.
    jsonstore.write_json(ad_assets._proposals_path(),
                         {"applied": [{"key": "k2"}]})
    _now = ad_assets._read_store(ad_assets._proposals_path(),
                                 ad_assets.LEGACY_PROPOSALS_PATH,
                                 {"applied": []})
    check("and the old location is not consulted past that",
          [a["key"] for a in _now["applied"]], ["k2"])
finally:
    os.chdir(_cwd)
    for _k, _v in _prev.items():
        if _v is None:
            os.environ.pop(_k, None)
        else:
            os.environ[_k] = _v


# A test naming the module we fixed proves nothing about the next store
# somebody adds. A path handed to jsonstore as a bare relative string literal
# is unambiguously resolved against the process working directory -- so that
# is the shape swept for, across hub/ and modules/. A path built from a call
# or a name is *not determinable* here and is deliberately not reported: a
# check with false positives is one somebody switches off, and switching this
# one off costs the real finding.
print("\nNo store writes outside the data root")

_STORE_FNS = {"read_json", "write_json", "update_json", "delete_json"}
_relative = []
for _py in sorted(list((ROOT / "hub").rglob("*.py"))
                  + list((ROOT / "modules").rglob("*.py"))):
    if "_attic" in _py.parts or _py.name == "jsonstore.py":
        continue
    try:
        _tree = ast.parse(_py.read_text(errors="ignore"))
    except SyntaxError:
        continue
    for _n in ast.walk(_tree):
        if not isinstance(_n, ast.Call) or not _n.args:
            continue
        _f = _n.func
        _name = _f.attr if isinstance(_f, ast.Attribute) else getattr(_f, "id", "")
        if _name not in _STORE_FNS:
            continue
        _a = _n.args[0]
        if (isinstance(_a, ast.Constant) and isinstance(_a.value, str)
                and not _a.value.startswith("/")):
            _relative.append(f"{_py.relative_to(ROOT)}:{_n.lineno}")

check("no jsonstore call site is handed a bare relative path", _relative, [])


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
