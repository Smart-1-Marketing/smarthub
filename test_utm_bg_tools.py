"""The two small tools nobody had tested.

    python3 test_utm_bg_tools.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, so it never touches /var/data or the real one.

## Why this file exists

UTM Builder and Background Remover were the last two modules in this repo
that no test named except in passing — the styling sweep, the tile sweep, the
spelling sweep. Both are small, both are used weekly, and each carried a
failure that no screen could report:

**A filtered list reporting the length of its own page.** `/api/links`
returned `links` capped at 300 and a `total` of the whole archive, and the
page printed `savedRows.length + ' of ' + d.total`. A search matching 450 of
900 read **"300 of 900"** — the page reporting its own length as the match
count, which is the failure `google_links.orphans()` names ("a page reporting
its own length as the total is how somebody concludes there are 25 orphans").
It is internally consistent on screen, because the table really does hold the
300 rows it drew.

**And the CSV beside it searched a different set of fields.** The table
matched on eleven keys and `/api/links/export` on five. `label` and
`created_by` are on neither the tagged URL nor the shorter list — so
searching for a flyer's name or a colleague's narrowed the table to the rows
you wanted, and the CSV button carrying the same `?q=` handed back **a header
row and nothing else**. Not a subtle divergence: a valid spreadsheet saying
there were none, downloaded on the same press, contradicting the table above
it. `filter_links()` is the one reading now.

**A cut-out filed with no dimensions, measured two functions earlier.**
`api_save` passed `width=res.get("width")` to the client's gallery, and `res`
is built from a `StoredAsset`, which has no `width` field and never has — so
every cut-out this tool has ever filed carried `None` for both, while
`api_remove` measured the identical bytes and threw the answer away.

**A credit cache that was per process, on a paid API.** The module docstring
opens by saying it is deliberately careful with credits and promises that
re-running the same image — a double-click, a retry after a resize tweak — is
free. `_results` is a module-level dict and gunicorn runs two workers, so it
was free about half the time and remove.bg charged for the rest. Every screen
reported a clean success either way; the only evidence was a credit balance
falling faster than the number of cut-outs anybody made. The
`_state`-is-per-process trap the scheduler panel, the client registry cache
and suite_panel's double-submit claim have each had to undo — here it costs
money.

**And two caps that contradicted each other, one of them on screen.** The
page offers ten images at twelve megabytes each; Flask was set to refuse the
body at forty. A batch inside every rule the page states was refused by the
framework before the view ran, as Werkzeug's **HTML** 413 page, which
`.then(r => r.json())` cannot parse — so the tool said "Failed: SyntaxError"
and nothing about sending fewer at a time, with every one of this module's
own carefully worded per-file messages unreachable.
"""
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1utmbg_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "utm-bg-test-secret"
os.environ["AUDIT_LOG_PATH"] = os.path.join(DISK, "audit.jsonl")
os.environ["REMOVE_BG_API"] = "test-key-never-called"

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


from modules.bg_remover import app as bg                       # noqa: E402
from modules.utm_builder import app as utm                     # noqa: E402

# Imported here, before a single request is made. wsgi.py registers an error
# handler on each mounted app, and Flask refuses a setup method once an app
# has served a request -- so importing it further down, where the mount
# assertions read, fails on an AssertionError that says nothing about mounts.
import wsgi                                                    # noqa: E402


def _dispatcher(app):
    """Unwrap to whichever layer holds the mounts.

    wsgi.application is a ProxyFix wrapping NoIndex wrapping ErrorMirror
    wrapping the DispatcherMiddleware -- so a getattr on the outermost object
    answers with the default and the walk finds nothing, which is the sweep
    that quietly stops sweeping that test_blueprint_guards.py names.
    """
    seen = 0
    while app is not None and seen < 12:
        if hasattr(app, "mounts"):
            return app
        app = getattr(app, "app", None) or getattr(app, "wsgi_app", None)
        seen += 1
    return None


DISPATCHER = _dispatcher(wsgi.application)

UTM_TPL = (ROOT / "modules" / "utm_builder" / "templates" / "index.html").read_text()
BG_TPL = (ROOT / "modules" / "bg_remover" / "templates" / "index.html").read_text()

uc = utm.app.test_client()
bc = bg.app.test_client()


def _png(w=321, h=77) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (1, 2, 3, 4)).save(buf, "PNG")
    return buf.getvalue()


# A book with two clients in it, and more matches than one page can hold --
# which is the only shape in which the count bug is visible at all.
ROWS = []
for i in range(450):
    ROWS.append({"id": f"a{i}", "url": "https://acme.test/?utm_campaign=spring",
                 "base_url": "https://acme.test/", "client": "Acme Tyre",
                 "product": "Display", "label": "spring flyer",
                 "created_by": "Ada Lovelace", "utm_campaign": "spring",
                 "utm_source": "google", "utm_medium": "banner"})
for i in range(450):
    ROWS.append({"id": f"b{i}", "url": "https://beta.test/?utm_campaign=always-on",
                 "base_url": "https://beta.test/", "client": "Beta Roofing",
                 "product": "CTV", "label": "brand", "created_by": "Bob Kahn",
                 "utm_campaign": "always-on", "utm_source": "bing",
                 "utm_medium": "cpc"})
utm.save_links(ROWS)


# =====================================================================
section("UTM Builder — a count that says what it counted")
# =====================================================================

# Read with .get() throughout: an assertion that raises on the key it is
# checking for takes every check after it out of the run, and a missing
# `matched` is exactly the regression this section exists to catch.
d = uc.get("/api/links?q=spring+flyer").get_json()
check("the page is capped, as it always was", len(d.get("links", [])), 300)
check("and how many actually matched is on the answer", d.get("matched"), 450)
check("beside the archive total, which is a third question", d.get("total"), 900)
check("so the page can say 450 of 900 rather than 300 of 900",
      f"{d.get('matched')} of {d.get('total')}", "450 of 900")

# The page has to read the right one of the three, or the fix stops at the API.
check("the page reads matched, not the length of what it was sent",
      "d.matched" in UTM_TPL, True)
check("and it no longer prints the page's own length as the count",
      "savedRows.length+' of '+d.total" in UTM_TPL, False)
check("and it says when the table is showing part of what matched",
      "showing the first" in UTM_TPL, True)

# A search narrow enough to fit on one page must not gain a caveat it does
# not need: a warning on every screen is a warning nobody reads.
one = uc.get("/api/links?q=beta.test&limit=2000").get_json()
check("a search that fits reports the same number twice",
      (one.get("matched"), len(one.get("links", []))), (450, 450))

none = uc.get("/api/links?q=nobody-typed-this").get_json()
check("and nothing matching is nought rather than the archive",
      (none.get("matched"), none.get("total")), (0, 900))


# =====================================================================
section("UTM Builder — the CSV is the list you were looking at")
# =====================================================================
# The download button carries the search box's own `?q=`, so the two must
# select the same rows. They matched on eleven fields and five.


def csv_rows(query):
    body = uc.get("/api/links/export" + query).get_data(as_text=True)
    return len(body.strip().splitlines()) - 1        # less the header


check("searching a label matches on screen",
      uc.get("/api/links?q=spring+flyer&limit=2000").get_json().get("matched"), 450)
check("and the CSV holds those rows rather than a bare header",
      csv_rows("?q=spring+flyer"), 450)

check("searching a colleague matches on screen",
      uc.get("/api/links?q=ada&limit=2000").get_json().get("matched"), 450)
check("and the CSV holds those too",
      csv_rows("?q=ada"), 450)

check("a client filter selects the same rows both ways",
      (uc.get("/api/links?client=Beta+Roofing&limit=2000").get_json().get("matched"),
       csv_rows("?client=Beta+Roofing")), (450, 450))
check("and no filter at all exports the whole book",
      csv_rows(""), 900)

# One reading, not two: a second copy is what drifted, and it drifted into
# handing somebody an empty spreadsheet.
check("there is one description of what a search matches",
      utm.SEARCH_FIELDS,
      ("url", "base_url", "client", "product", "utm_campaign", "utm_source",
       "utm_medium", "utm_content", "utm_term", "label", "created_by"))
utm_src = (ROOT / "modules" / "utm_builder" / "app.py").read_text()
check("and both readers go through it", utm_src.count("filter_links("), 3)


# =====================================================================
section("UTM Builder — what it builds, and what it refuses")
# =====================================================================
# The normalising is the whole reason this is more than a concatenator:
# "Paid Social" and "paid-social" are two campaigns in Analytics.

check("spaces and case are one spelling", utm.normalise("Paid Social"), "paid-social")
check("underscores are hyphens too", utm.normalise("grand_opening"), "grand-opening")
check("stray punctuation goes", utm.normalise("Spring!! Promo??"), "spring-promo")
check("and a run of hyphens collapses", utm.normalise("a -- b"), "a-b")

built = uc.post("/api/build", json={
    "url": "acme.test/landing?gclid=abc&utm_source=old",
    "utm_campaign": "Spring Promo", "sources": ["Google", "Facebook"],
    "mediums": ["CPC"]}).get_json()
check("a URL with no scheme is given one and still builds",
      built["links"][0]["url"].startswith("https://acme.test/landing?"), True)
check("existing non-UTM tracking survives",
      all("gclid=abc" in l["url"] for l in built["links"]), True)
check("an existing utm_source is replaced rather than duplicated",
      built["links"][0]["url"].count("utm_source="), 1)
check("two sources and one medium is two links", built["count"], 2)

check("a campaign name is required, because it is what ties them together",
      uc.post("/api/build", json={"url": "acme.test", "sources": ["google"],
                                  "mediums": ["cpc"]}).status_code, 400)
check("and something that is not a URL is refused by name",
      "valid URL" in uc.post("/api/build", json={
          "url": "not a url", "utm_campaign": "x", "sources": ["google"],
          "mediums": ["cpc"]}).get_json()["error"], True)
check("a combination count that would run away is refused",
      uc.post("/api/build", json={
          "url": "acme.test", "utm_campaign": "x",
          "sources": [f"s{i}" for i in range(9)],
          "mediums": [f"m{i}" for i in range(9)]}).status_code, 400)


# =====================================================================
section("UTM Builder — saving files the batch against a client")
# =====================================================================

saved = uc.post("/api/links", json={
    "url": "https://gamma.test/", "client": "Gamma Motors", "product": "CTV",
    "label": "autumn", "links": [
        {"url": "https://gamma.test/?utm_campaign=autumn&utm_source=google",
         "utm_campaign": "autumn", "utm_source": "google", "utm_medium": "cpc"}]
}).get_json()
check("the batch saves", saved["saved"], 1)

again = uc.post("/api/links", json={
    "url": "https://gamma.test/", "client": "Gamma Motors", "links": [
        {"url": "https://gamma.test/?utm_campaign=autumn&utm_source=google"}]
}).get_json()
check("and the same link twice is skipped rather than duplicated",
      (again["saved"], again["skipped"]), (0, 1))

# The archive is capped and new rows go on the front, so a save past the cap
# drops the oldest tracked URLs -- which is the thing this tool exists to
# prevent, arriving as a save that reported success. Bounded, and never in
# silence.
check("the cap is written down rather than inline", utm.MAX_LINKS, 8000)
check("nothing is dropped under it", utm.save_links(ROWS), 0)
_over = ROWS + [{"id": f"pad{i}", "url": f"https://pad.test/{i}"}
                for i in range(utm.MAX_LINKS - len(ROWS) + 120)]
check("and what falls off the end is counted", utm.save_links(_over), 120)
utm.save_links(ROWS)                                   # put the book back
check("the page says so rather than reporting a clean save",
      "of the oldest dropped" in UTM_TPL, True)

# The other half of #264's finding, asserted from this end: a batch saved
# against a client has to reach that client's record.
from hub.client_brand import work_log                            # noqa: E402
check("and it lands on the client's own record",
      work_log("Gamma Motors")["count"], 1)
check("named as the tool a person would know",
      work_log("Gamma Motors")["items"][0]["source"], "UTM Builder")


# =====================================================================
section("Background Remover — the cut-out is measured, not guessed")
# =====================================================================
# api_save asked a StoredAsset for a `width` it does not carry, so every
# cut-out filed into a client gallery had None for both -- while api_remove
# measured the identical bytes two functions earlier and dropped the answer.

check("dimensions are read off the bytes", bg._dimensions(_png(321, 77)),
      (321, 77))
check("and something unreadable is not measured, never zero",
      bg._dimensions(b"this is not an image"), (None, None))
check("neither is an empty upload", bg._dimensions(b""), (None, None))

bg_src = (ROOT / "modules" / "bg_remover" / "app.py").read_text()

# Read through the AST, not by matching text. The comment beside the fix
# explains the bug by quoting `res.get("width")`, and a text match reports the
# explanation of the fix as the defect -- the rule hub/config.py's drift check
# gives at length and the one this repo has now had to apply five times.
import ast                                                      # noqa: E402

_bg_tree = ast.parse(bg_src)
_fns = {n.name: n for n in ast.walk(_bg_tree)
        if isinstance(n, ast.FunctionDef)}


def _calls(fn_name):
    """Every call in one function, as source-ish text, comments excluded."""
    out = []
    for node in ast.walk(_fns[fn_name]):
        if isinstance(node, ast.Call):
            out.append(ast.unparse(node))
    return out


check("the save path no longer asks a StoredAsset for a width",
      [c for c in _calls("api_save") if "res.get('width')" in c], [])
check("it files the measured one from the bytes it is storing",
      any("_dimensions(raw)" in c for c in _calls("api_save")), True)
from hub.storage import StoredAsset                              # noqa: E402
check("because a StoredAsset has never carried one",
      "width" in getattr(StoredAsset, "__dataclass_fields__", {}), False)
check("and the result panel reads the same one function",
      any("_dimensions(out)" in c for c in _calls("api_remove")), True)
check("so neither carries a copy of the measuring",
      sorted(f for f in _fns
             if any("Image.open" in ast.unparse(n) for n in ast.walk(_fns[f])
                    if isinstance(n, ast.Call))),
      ["_dimensions", "_post_resize", "resize_max_edge"])


# =====================================================================
section("Background Remover — a retry is free on either worker")
# =====================================================================
# The promise the module docstring opens with. It was kept by a module-level
# dict, and gunicorn runs two of those.

digest, png = "a" * 64, _png()
bg._cache_put(digest, png)
bg._results.clear()                    # the other worker: it saw none of that
check("a result cached by one worker is found by the other",
      bg._cache_get(digest), png)
check("and reading it warms the worker that had missed",
      digest in bg._results, True)
check("a genuine miss is still a miss", bg._cache_get("b" * 64), None)

check("the shared copy is on the data disk this deployment names",
      bg._cache_dir().startswith(DISK), True)

# Bounded on age as well as on size, and nothing in the sweep may raise: a
# cache that can break the tool it accelerates is worse than no cache.
import time as _time                                             # noqa: E402
old = os.path.join(bg._cache_dir(), "c" * 64 + ".png")
with open(old, "wb") as fh:
    fh.write(png)
os.utime(old, (0, 0))
bg._sweep()
check("an expired entry is swept", os.path.exists(old), False)
check("and the live one survives it",
      os.path.exists(os.path.join(bg._cache_dir(), digest + ".png")), True)
check("the cache is capped in bytes as well as in age",
      bg._CACHE_MAX_BYTES > 0, True)

# A cache is not state: there is nothing to restore, so it deliberately does
# not go through hub/jsonstore.py, which mirrors every write into the
# database. A wiped disk here costs one credit on the next retry.
check("it is not mirrored into the database",
      "jsonstore.write_json" in bg_src, False)


# =====================================================================
section("Background Remover — the caps agree, and refuse in words")
# =====================================================================

check("the batch cap is what the tool actually offers",
      bg.MAX_BATCH_BYTES, bg.MAX_FILES * bg.MAX_BYTES)
check("and the framework no longer refuses inside it",
      bg.app.config["MAX_CONTENT_LENGTH"] > bg.MAX_BATCH_BYTES, True)
check("the page states the same per-file numbers",
      f"up to {bg.MAX_FILES} at once, {bg.MAX_BYTES // (1024 * 1024)} MB each"
      in BG_TPL, True)

over = bc.post("/api/remove", content_type="multipart/form-data", data={
    "images": [(io.BytesIO(b"x" * (bg.MAX_BATCH_BYTES + 4 * 1024 * 1024)),
                "huge.jpg")]})
check("a batch genuinely over the cap answers 413", over.status_code, 413)
check("as JSON the page can read, not Werkzeug's HTML",
      over.headers.get("Content-Type", "").startswith("application/json"), True)
check("saying what to do about it",
      "Send fewer" in over.get_json()["error"], True)
check("and naming the limit it hit",
      f"{bg.MAX_BATCH_BYTES // (1024 * 1024)} MB" in over.get_json()["error"],
      True)

check("/health reports all three, so a screen need not restate them",
      {k: v for k, v in bc.get("/health").get_json().items()
       if k.startswith("max_")},
      {"max_files": 10, "max_file_mb": 12, "max_batch_mb": 120})


# =====================================================================
section("Background Remover — what it refuses before spending a credit")
# =====================================================================
# Every one of these has to be reached, which is what the HTML 413 was
# standing in front of.

check("nothing chosen is refused by name",
      "at least one" in bc.post("/api/remove", data={},
                                content_type="multipart/form-data")
      .get_json()["error"], True)

many = bc.post("/api/remove", content_type="multipart/form-data", data={
    "images": [(io.BytesIO(_png(4, 4)), f"f{i}.png")
               for i in range(bg.MAX_FILES + 1)]})
check("more than the batch allows is refused with the number",
      f"Up to {bg.MAX_FILES}" in many.get_json()["error"], True)

one_bad = bc.post("/api/remove", content_type="multipart/form-data", data={
    "images": [(io.BytesIO(b"%PDF-1.4"), "a.pdf")]})
check("a file that is not an image is named, not sent",
      "only JPG, PNG and WebP" in one_bad.get_json()["error"], True)

empty = bc.post("/api/remove", content_type="multipart/form-data", data={
    "images": [(io.BytesIO(b""), "empty.png")]})
check("and an empty file is named too",
      "empty file" in empty.get_json()["error"], True)

# A key that is not set is a 503 pointing at what to do, never a spent call.
_key = os.environ.pop("REMOVE_BG_API")
try:
    from hub import config as _cfg
    _cfg.settings.__dict__.pop("remove_bg_key", None)
    unset = bc.post("/api/remove", content_type="multipart/form-data", data={
        "images": [(io.BytesIO(_png(4, 4)), "a.png")]})
    check("no key answers 503 rather than failing at the provider",
          unset.status_code, 503)
finally:
    os.environ["REMOVE_BG_API"] = _key
    try:
        _cfg.settings.__dict__.pop("remove_bg_key", None)
    except Exception:                                            # noqa: BLE001
        pass


# =====================================================================
section("Both tools are reachable, tiled, and behind the login")
# =====================================================================

check("the composed app was found rather than defaulted to empty",
      DISPATCHER is not None, True)
mounts = getattr(DISPATCHER, "mounts", {})
check("UTM Builder is mounted", "/tools/utm" in mounts, True)
check("Background Remover is mounted", "/tools/bg-remover" in mounts, True)

tools_tpl = (ROOT / "hub" / "templates" / "tools.html").read_text()
creative_tpl = (ROOT / "hub" / "templates" / "creative.html").read_text()
check("UTM Builder is tiled once",
      (tools_tpl + creative_tpl).count('href="/tools/utm/"'), 1)
check("Background Remover is tiled once",
      (tools_tpl + creative_tpl).count('href="/tools/bg-remover/"'), 1)

# Neither declares a public prefix, so every route in both is staff-only.
# Asserted rather than assumed: a client's name and every tagged URL we have
# ever built are behind these.
check("UTM Builder declares nothing public",
      getattr(utm, "PUBLIC_PREFIXES", ()), ())
check("Background Remover declares nothing public",
      getattr(bg, "PUBLIC_PREFIXES", ()), ())


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
