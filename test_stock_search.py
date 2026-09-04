"""Stock photo search: four sources, and what each one is allowed to claim.

    python3 test_stock_search.py

Same shape as the other test files here -- no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one. Every provider call is stubbed: this asserts the
rules, not somebody else's uptime.

## Why this file exists

The tool searches Pexels, Pixabay, Unsplash and our own Cloudinary folders in
one pass. Every failure below is one where the screen goes on looking healthy:

  1. **A folder that does not exist is not a folder with nothing in it.**
     Cloudinary's search returns zero results for both, so a tool that trusted
     the search would report "no photos match" for ever about a library nobody
     had created. At the time this was written *neither* configured folder
     existed in the smart1labs account, so this is the live case rather than a
     hypothetical one. `folder_state()` asks the folders API and answers "ok" /
     "missing" / "error" -- three situations, never a bare boolean.

  2. **A source that refused is not a source with no matches.** One provider
     failing must not cost the other three, and it must not come back as
     silence either: "nothing matched" and "we could not look" are different
     answers and only the first means change your search.

  3. **Keys are read through hub/config.py, at call time.** This deployment
     sets PEXELS_API and PIXABAY_API, not the _KEY spellings much of the code
     was written against. A module reading os.environ at import degrades to an
     empty grid with every screen healthy -- the defect `provider_key_drift`
     is high severity for.

  4. **The subfolder clause matches the folder itself too.** `folder:"X/*"`
     matches what is *beneath* X and not X, so a photo sitting directly in
     "General Stock Photos" -- where most of them will sit -- would be
     invisible with the obvious one-term expression.

  5. **Ours sort first.** A photo we already own costs nothing, needs no
     attribution and is often the client's own brand. Buried under thirty free
     ones, the library goes unused, which is the reason it was unreachable
     before.

  6. **The client picker did not gain our internal folders.** image_picker is
     the client-facing gallery; "General Stock Photos" and "Smart 1 Ads" are
     internal agency stock and have no business in front of a client. It now
     delegates to the shared module, so this is the assertion that keeps the
     delegation honest.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1stock_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "stock-test-secret"
os.environ["PANEL_PASSWORD"] = "stock-test-password"

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


from hub import stock_search                                   # noqa: E402
from modules.stock_photos import app as tool                   # noqa: E402


def item(provider, pid, **kw):
    base = dict(provider=provider, pid=pid, thumb=f"http://t/{pid}",
                preview=f"http://p/{pid}", full=f"http://f/{pid}",
                width=1600, height=900, author="A", author_url="",
                source_url=f"http://s/{pid}", alt="", tags="")
    base.update(kw)
    return stock_search._norm(**base)


# --------------------------------------------------------------------------- #
section("The folders are addressed by name, and subfolders are included")

clause = stock_search._folder_clause(["General Stock Photos", "Smart 1 Ads"])

check("the folder itself is matched",
      'folder="General Stock Photos"' in clause, True)
check("and everything beneath it",
      'folder:"General Stock Photos/*"' in clause, True)
check("the second folder too, both ways",
      'folder="Smart 1 Ads"' in clause and 'folder:"Smart 1 Ads/*"' in clause, True)
check("the two folders are an OR, not an AND",
      " OR " in clause and " AND " not in clause, True)

# Cloudinary publishes the folder under asset_folder in dynamic folder mode and
# folder in fixed. Asking only the wrong one returns zero while folder_state()
# still reports the folder present -- so it reads as "exists and is empty",
# which is the confident wrong answer rather than a visible failure.
check("the folder is asked for under asset_folder too",
      'asset_folder="General Stock Photos"' in clause, True)
check("...and its subfolders under asset_folder",
      'asset_folder:"Smart 1 Ads/*"' in clause, True)
check("four terms per folder, no more",
      clause.count(" OR "), 7)

# A name with a space must survive quoting, or the expression is a parse error
# and every library search comes back empty with nothing reading as broken.
check("a folder name with spaces is quoted",
      stock_search._quoted("Smart 1 Ads"), '"Smart 1 Ads"')

check("the default folders are the two we were asked for",
      stock_search.DEFAULT_LIBRARY_FOLDERS,
      ("General Stock Photos", "Smart 1 Ads"))

os.environ["STOCK_LIBRARY_FOLDERS"] = "Ours, Ours/Deep , "
check("the list is overridable without a deploy",
      stock_search.library_folders(), ["Ours", "Ours/Deep"])
del os.environ["STOCK_LIBRARY_FOLDERS"]
check("and falls back to the two by default",
      stock_search.library_folders(), ["General Stock Photos", "Smart 1 Ads"])

# Cloudinary's expression language treats these as syntax. A query carrying one
# is a parse error, which returns zero results and reads as "nothing matched".
check("a query is stripped of expression syntax",
      stock_search._query_clause('roof: (crew)'), '("roof" AND "crew")')
check("a one-character term is dropped rather than quoted",
      stock_search._query_clause("a roofing"), '("roofing")')
check("an empty query is no clause at all, not an empty one",
      stock_search._query_clause("   "), "")


# --------------------------------------------------------------------------- #
section("A folder that is missing is not a folder that is empty")


class _FakeApi:
    """Stands in for cloudinary.api. 'General Stock Photos' does not exist."""

    def __init__(self, missing=("General Stock Photos",), raising=()):
        self.missing, self.raising = missing, raising
        self.asked = []

    def subfolders(self, path):
        self.asked.append(path)
        if path in self.raising:
            raise RuntimeError("Cloudinary is unreachable")
        if path in self.missing:
            raise Exception("NotFound: Folder not found")
        return {"folders": []}


class _FakeCloudinary:
    def __init__(self, api):
        self.api = api

    def config(self, **kw):
        return None


def with_library(api, fn):
    """Run fn with the library configured and cloudinary stubbed."""
    real_cl, real_ready = stock_search.cloudinary, stock_search._library_ready
    stock_search.cloudinary = _FakeCloudinary(api)
    stock_search._library_ready = lambda: True
    stock_search._cl_configured = True
    stock_search.clear_cache()
    try:
        return fn()
    finally:
        stock_search.cloudinary = real_cl
        stock_search._library_ready = real_ready
        stock_search.clear_cache()


api = _FakeApi()
state = with_library(api, lambda: stock_search.folder_state(
    ["General Stock Photos", "Smart 1 Ads"]))

check("a folder that was never created reads as missing",
      state["General Stock Photos"], "missing")
check("a folder that exists but holds nothing reads as ok",
      state["Smart 1 Ads"], "ok")

api2 = _FakeApi(missing=(), raising=("Smart 1 Ads",))
state2 = with_library(api2, lambda: stock_search.folder_state(
    ["General Stock Photos", "Smart 1 Ads"]))
check("a folder we could not check is an error, never 'missing'",
      state2["Smart 1 Ads"], "error")
check("...and it is not silently reported as ok either",
      state2["Smart 1 Ads"] == "ok", False)

# With no Cloudinary at all the answer is still three-valued, and it is not
# "missing" -- we did not look, so we cannot say the folder is not there.
real_ready = stock_search._library_ready
stock_search._library_ready = lambda: False
stock_search.clear_cache()
check("no Cloudinary configured is an error, not a missing folder",
      stock_search.folder_state(["General Stock Photos"]),
      {"General Stock Photos": "error"})
stock_search._library_ready = real_ready
stock_search.clear_cache()


# --------------------------------------------------------------------------- #
section("The page says which, rather than drawing an empty grid")

state3 = with_library(_FakeApi(), lambda: tool._folders())
missing = [f for f in state3 if f["state"] == "missing"]
check("the missing folder is named on the page", len(missing), 1)
check("and it says what that means, in words",
      "Not in Cloudinary yet" in missing[0]["detail"], True)
check("the folder that exists carries no scary note",
      [f["detail"] for f in state3 if f["state"] == "ok"], [""])


# --------------------------------------------------------------------------- #
section("A library result is built from what Cloudinary actually returns")


class _FakeSearch:
    """Stands in for cloudinary.search.Search(). Payload shape is the real one,
    taken from this account: dynamic-folder mode, so the folder is in
    `asset_folder` and the public_id does NOT carry it."""

    last_expression = ""

    def expression(self, e):
        _FakeSearch.last_expression = e
        return self

    def with_field(self, f):
        return self

    def sort_by(self, f, d):
        return self

    def max_results(self, n):
        return self

    def execute(self):
        return {"resources": [{
            "public_id": "Sell_More_Boats",
            "asset_folder": "General Stock Photos/marine",
            "secure_url": "https://res.cloudinary.com/smart1labs/image/upload/"
                          "v1784755089/Sell_More_Boats.png",
            "width": 1672, "height": 941,
            "tags": ["boat", "summer"],
            "context": {"custom": {"alt": "A boat at a dock"}},
        }, {
            "public_id": "Tall_One",
            "asset_folder": "Smart 1 Ads",
            "secure_url": "https://res.cloudinary.com/smart1labs/image/upload/"
                          "v1/Tall_One.png",
            "width": 800, "height": 1600, "tags": [], "context": {},
        }]}


class _FakeCloudinaryFull(_FakeCloudinary):
    class search:
        Search = _FakeSearch


def library_search(**kw):
    real_cl, real_ready = stock_search.cloudinary, stock_search._library_ready
    stock_search.cloudinary = _FakeCloudinaryFull(_FakeApi(missing=()))
    stock_search._library_ready = lambda: True
    stock_search._cl_configured = True
    stock_search.clear_cache()
    try:
        return stock_search.search_library("boats", per_page=10, page=1, **kw)
    finally:
        stock_search.cloudinary = real_cl
        stock_search._library_ready = real_ready
        stock_search.clear_cache()


rows = library_search(orientation=None)
check("both assets come back", len(rows), 2)
check("the alt comes from Cloudinary context", rows[0]["alt"], "A boat at a dock")
check("the folder is carried from asset_folder",
      rows[0]["folder"], "General Stock Photos/marine")
check("a subfolder asset is found at all -- the whole point of the /* clause",
      rows[0]["folder"].startswith("General Stock Photos/"), True)
check("it is flagged as ours", rows[0]["ours"], True)
check("the full URL is the original", rows[0]["full"].endswith("Sell_More_Boats.png"), True)

# A gallery must never request the full asset: 24 tiles at 1672px is megabytes
# of somebody's bandwidth, and Cloudinary bills a credit per gigabyte
# delivered.
check("the thumbnail is a derived, capped URL",
      "/upload/c_limit,w_400,q_auto,f_auto/" in rows[0]["thumbnail"], True)
check("the preview is derived too, at a larger cap",
      "/upload/c_limit,w_1200,q_auto,f_auto/" in rows[0]["preview"], True)
check("deriving does not damage a URL that has no /upload/ in it",
      stock_search._derive("http://example.com/x.png", "c_limit"),
      "http://example.com/x.png")

# An asset with no alt still needs something readable on the card, and the
# public_id is the only thing that knows anything about it.
check("an asset with no alt falls back to its name, not to blank",
      rows[1]["alt"], "Tall One")

check("the expression asks for images only",
      "resource_type:image" in _FakeSearch.last_expression, True)
check("...and names both folders",
      'folder="General Stock Photos"' in _FakeSearch.last_expression and
      'folder="Smart 1 Ads"' in _FakeSearch.last_expression, True)

# Orientation is applied locally for the library, because the expression
# language has no clean way to say "wider than it is tall".
tall = library_search(orientation="portrait")
check("a portrait filter keeps only the tall one",
      [r["provider_image_id"] for r in tall], ["Tall_One"])
wide = library_search(orientation="landscape")
check("a landscape filter keeps only the wide one",
      [r["provider_image_id"] for r in wide], ["Sell_More_Boats"])


# --------------------------------------------------------------------------- #
section("A source that refused is named, not silently empty")

real_adapters = dict(stock_search._ADAPTERS)
real_configured = stock_search.configured_sources


def stub_sources(**adapters):
    """Configure exactly the named sources, with the given adapters."""
    stock_search.configured_sources = lambda: {
        s: (s in adapters) for s in stock_search.SOURCES}
    for name, fn in adapters.items():
        stock_search._ADAPTERS[name] = fn
    stock_search.clear_cache()


def restore():
    stock_search._ADAPTERS.clear()
    stock_search._ADAPTERS.update(real_adapters)
    stock_search.configured_sources = real_configured
    stock_search.clear_cache()


def ok_adapter(items):
    return lambda q, **kw: list(items)


def dead_adapter(q, **kw):
    raise RuntimeError("provider is down")


stub_sources(pexels=ok_adapter([item("pexels", "p1")]), pixabay=dead_adapter)
found = stock_search.search(["roofing"])
check("the healthy provider still answers", len(found["results"]), 1)
check("the failed one is reported as an error", found["sources"]["pixabay"], "error")
check("the healthy one is reported ok", found["sources"]["pexels"], "ok")
check("a provider with no key is 'off', which is not an error",
      found["sources"]["unsplash"], "off")
restore()

# Every provider failing is data, not an exception -- and it must not look the
# same as a search that genuinely matched nothing.
stub_sources(pexels=dead_adapter, pixabay=dead_adapter)
found = stock_search.search(["roofing"])
check("an all-source failure does not raise", found["results"], [])
check("...and every failure is still named",
      sorted(k for k, v in found["sources"].items() if v == "error"),
      ["pexels", "pixabay"])
restore()


# --------------------------------------------------------------------------- #
section("What we already own comes first")

stub_sources(
    library=ok_adapter([item("library", "own1", folder="General Stock Photos")]),
    pexels=ok_adapter([item("pexels", f"p{i}") for i in range(5)]),
)
found = stock_search.search(["roofing"])
check("the library result is first", found["results"][0]["provider"], "library")
check("it is flagged as ours", found["results"][0]["ours"], True)
check("and it carries the folder it came from",
      found["results"][0]["folder"], "General Stock Photos")
check("a provider result is not flagged as ours",
      any(r["ours"] for r in found["results"] if r["provider"] == "pexels"), False)
check("a provider result carries no folder",
      found["results"][1]["folder"], "")
restore()

# The library is a finite shelf, so "show me everything we have" is a real
# question. The other three are the whole internet, where it is not.
stub_sources(library=ok_adapter([item("library", "own1")]),
             pexels=ok_adapter([item("pexels", "p1")]))
found = stock_search.search([])
check("an empty query browses our library", len(found["results"]), 1)
check("...and only our library", found["results"][0]["provider"], "library")
check("the web providers are idle rather than errored",
      found["sources"]["pexels"], "idle")
restore()

# The same photo comes back under several phrasings; counting it twice makes a
# thin result set look healthy.
stub_sources(pexels=ok_adapter([item("pexels", "same"), item("pexels", "same")]))
found = stock_search.search(["a", "b"])
check("a duplicate is dropped once", len(found["results"]), 1)
restore()


# --------------------------------------------------------------------------- #
section("Keys are read through config, at call time")

src = (ROOT / "hub" / "stock_search.py").read_text()
check("no provider key is read from os.environ directly",
      'os.environ.get("PEXELS' in src or 'os.environ["PEXELS' in src, False)
check("the keys come from hub.config", "from hub.config import settings" in src, True)

import hub.config as _cfg                                       # noqa: E402
for spelling in ("PEXELS_API", "PIXABAY_API", "UNSPLASH_API"):
    check(f"{spelling} is a spelling config already accepts",
          any(spelling in names for names in _cfg.ALIASES.values()), True)

# Read at call time, not bound at import. A module that captured the key at
# import is the defect: on this deployment the key resolves fine and the module
# still reports "not configured", with every screen looking healthy.
class _LateSettings:
    pexels_key = "set-after-import"
    pixabay_key = ""
    unsplash_key = ""
    cloudinary_ready = False


_real_settings = stock_search.settings
try:
    stock_search.settings = _LateSettings()
    check("a key that changed after import is still seen",
          stock_search.configured_sources()["pexels"], True)
    check("...and one that is still unset is still off",
          stock_search.configured_sources()["pixabay"], False)
finally:
    stock_search.settings = _real_settings


# --------------------------------------------------------------------------- #
section("Unsplash's download ping is a condition of the key")

check("a ping with no key or no location is refused rather than attempted",
      stock_search.trigger_unsplash_download(""), False)
check("the ping is called on use, not on browse",
      "trigger_unsplash_download" in (ROOT / "modules" / "stock_photos" /
                                      "app.py").read_text(), True)
tpl = (ROOT / "modules" / "stock_photos" / "templates" /
       "stock_photos.html").read_text()
check("the page routes a download through /api/use rather than linking out",
      'fetch("api/use"' in tpl, True)


# --------------------------------------------------------------------------- #
section("Saving a photo files it under Stock photo picks, in a folder or none")

check("a folder label slugifies to a stable key",
      tool._slug_folder("Homepage Refresh!"), "homepage-refresh")
check("...and collapses runs of punctuation",
      tool._slug_folder("  Q1 / Social  Ads "), "q1-social-ads")

from modules.image_picker import filing as picker_filing        # noqa: E402

calls = []
real_file_asset = picker_filing.file_asset


def fake_file_asset(**kw):
    calls.append(kw)
    return {"ok": True, "image": {}, "gallery_url": "/g/1"}


picker_filing.file_asset = fake_file_asset
try:
    with tool.app.test_request_context():
        out = tool._file_for_client("Acme Roofing", "library", "lib:own1",
                                    "https://res.cloudinary.com/x/own1.jpg",
                                    "Homepage Refresh")
finally:
    picker_filing.file_asset = real_file_asset

check("it filed rather than refusing", out.get("ok"), True)
check("the kind is stock, which names the Stock photo picks group",
      calls[0]["kind"], "stock")
check("the folder label is carried through as the collection label",
      calls[0]["label"], "Homepage Refresh")
check("...and a slug as the collection key",
      calls[0]["key"], "homepage-refresh")

calls.clear()
picker_filing.file_asset = fake_file_asset
try:
    with tool.app.test_request_context():
        tool._file_for_client("Acme Roofing", "library", "lib:own2",
                              "https://res.cloudinary.com/x/own2.jpg", "")
finally:
    picker_filing.file_asset = real_file_asset

check("with no folder chosen, no key is invented", calls[0]["key"], "")
check("...and no label either, so file_asset's own default group name applies",
      calls[0]["label"], "")

check('the gallery group is named "Stock photo picks"',
      picker_filing.KIND_LABELS["stock"], "Stock photo picks")
check("...and the same name reaches the gallery's own source chips",
      picker_filing.SOURCE_LABELS["stock_photos"], "Stock photo picks")


# --------------------------------------------------------------------------- #
section("An existing folder is offered back, not retyped from memory")

from modules.image_picker.filing import folders_for              # noqa: E402

real_file_asset(client_name="Folder Test Co", public_id="stock/testpic1",
                url="https://res.cloudinary.com/x/testpic1.jpg", kind="stock",
                key="homepage-refresh", label="Homepage Refresh",
                provider="pexels", saved_by="tester", push_to_suite=False)

folders = folders_for("Folder Test Co", "stock")
check("the folder we just saved into comes back", len(folders), 1)
check("under the label as typed", folders[0]["label"], "Homepage Refresh")
check("under the derived key", folders[0]["key"], "homepage-refresh")
check("with a count of one", folders[0]["count"], 1)

check("a client with no gallery yet offers no folders",
      folders_for("Nobody Yet LLC", "stock"), [])
check("an empty kind is refused rather than scanning everything",
      folders_for("Folder Test Co", ""), [])

route_client = tool.app.test_client()
r = route_client.get("/api/client-folders?client=" + "Folder+Test+Co")
check("the client-folder route answers", r.status_code, 200)
check("and reports the folder saved above",
      [f["label"] for f in (r.get_json() or {}).get("folders", [])],
      ["Homepage Refresh"])

r = route_client.get("/api/client-folders")
check("with no client at all it answers emptily rather than erroring",
      (r.get_json() or {}).get("folders"), [])

r = route_client.get("/api/clients?q=Folder")
check("the client search route answers", r.status_code, 200)

tpl_stock = (ROOT / "modules" / "stock_photos" / "templates" /
            "stock_photos.html").read_text()
check("the page offers a client field", 'id="client"' in tpl_stock, True)
check("...and a folder field", 'id="folder"' in tpl_stock, True)
check("...and an explicit save action", 'data-act="save"' in tpl_stock, True)
check("the save carries the client and folder to the server",
      "client: client, folder: folder" in tpl_stock, True)


# --------------------------------------------------------------------------- #
section("The client picker did not quietly gain our internal folders")

from modules.image_picker import providers as picker            # noqa: E402

check("the picker asks for the three web providers only",
      sorted(picker.THIRD_PARTY), ["pexels", "pixabay", "unsplash"])
check("'library' is not one of them", "library" in picker.THIRD_PARTY, False)

stub_sources(library=ok_adapter([item("library", "own1")]),
             pexels=ok_adapter([item("pexels", "p1")]))
got = picker.search(["roofing"])
check("a picker search returns no library photo",
      [r["provider"] for r in got["results"]], ["pexels"])
check("it reports under 'providers', the shape it always has",
      sorted(got["providers"]), ["pexels", "pixabay", "unsplash"])
check("...and reports nothing under 'sources'", "sources" in got, False)
restore()

# One implementation, not a fourth copy: the reason the Pexels key had to be
# fixed twice already.
pv = (ROOT / "modules" / "image_picker" / "providers.py").read_text()
check("the picker delegates rather than keeping its own adapters",
      "from hub.stock_search import" in pv, True)
check("...and no longer carries its own alias table",
      "PIXABAY_API_KEY\": (" in pv, False)


# --------------------------------------------------------------------------- #
section("The tool is reachable, and mounted where it says it is")

wsgi_src = (ROOT / "wsgi.py").read_text()
check("it is mounted in wsgi", '"/tools/stock-photos"' in wsgi_src, True)
check("a boot failure serves a fallback rather than taking the Hub down",
      "_fallback_app(\"Stock Photo Search\"" in wsgi_src, True)

tiles = (ROOT / "hub" / "templates" / "creative.html").read_text()
check("it has a tile -- a tool with no tile is invisible",
      'href="/tools/stock-photos/"' in tiles, True)
check("the tile link carries the trailing slash the relative fetches need",
      'href="/tools/stock-photos"' in tiles, False)

client = tool.app.test_client()
r = client.get("/")
check("the page renders", r.status_code, 200)
body = r.get_data(as_text=True)
for name in ("General Stock Photos", "Smart 1 Ads", "Pexels", "Pixabay", "Unsplash"):
    check(f"the page names {name}", name in body, True)

r = client.get("/api/search?q=&sources=pexels")
check("an empty query against the web providers is refused politely",
      r.status_code, 200)
check("...and says what to do instead",
      "Type something to search" in (r.get_json() or {}).get("message", ""), True)


print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
