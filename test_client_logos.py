"""A logo we found is a logo the client's gallery holds.

    python3 test_client_logos.py

Same shape as the other test files: no pytest, no new dependencies, and it
runs against a temporary data directory and a throwaway SQLite database.

## Why this file exists

Two things in this Hub find a client's logo and neither kept it anywhere a
person could use it. Brandfetch is billed against a hundred calls a month and
the answer was stored as JSON; `scan_facts.brand_observed()` reads the logo
off the last Insites audit, which is where it comes from for the majority of
local businesses. So the Hub knew the logo and the client's own gallery -- the
one place a rep opens to put a mark into an ad, a commercial or a proposal --
did not have it, and every one of those tools asked somebody to go and find a
logo that had been on file for months.

What this holds is the four ways filing it could go quietly wrong:

* **the same logo filed twice**, because Brandfetch and Insites hand back one
  mark under two URLs far more often than not, so the dedupe is on the bytes;
* **two genuinely different logos collapsed into one**, which loses the
  stacked mark or the horizontal lockup a rep was about to need;
* **a dead URL filed as a tile**, which draws a broken image a rep reports as
  the gallery being broken -- so the bytes are fetched here rather than handed
  to Cloudinary, precisely so a 404 is an answer;
* **a provider called on a page load**, which spends a plan of a hundred
  calls a month on nobody pressing anything.
"""
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1logos_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ.setdefault("SECRET_KEY", "client-logos-test-secret")

from hub import client_logos as cl  # noqa: E402

_passed = _failed = 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got  {got!r}\n          want {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


PNG_A = b"\x89PNG\r\n\x1a\n" + b"AAAA" * 40
PNG_B = b"\x89PNG\r\n\x1a\n" + b"BBBB" * 40


class FakeStored:
    def __init__(self, public_id, url):
        self.public_id, self.url = public_id, url


def install(fetches, candidates_out):
    """Stand in for the network, Cloudinary and the gallery."""
    filed = []

    cl._fetch = lambda url: fetches.get(url, (b"", "", "not stubbed"))
    # candidates() answers (found, notes) — "nothing on file" and "we
    # could not look" are different answers all the way through.
    cl.candidates = lambda client, domain="": (candidates_out, [])

    import types
    storage = types.SimpleNamespace(
        settings=types.SimpleNamespace(folder=lambda k: "smart1-client-logos"),
        slug=lambda v, d: "riverside-hvac",
        put=lambda kind, name, data, **kw: FakeStored(
            kw.get("public_id") or name, "https://cdn.example/" + name),
    )
    filing = types.SimpleNamespace(
        file_asset=lambda **kw: (filed.append(kw) or {"ok": True}))

    import hub as hub_pkg
    import modules.image_picker as ip
    hub_pkg.storage = storage
    ip.filing = filing
    return filed


# ------------------------------------------------------- 1. what it reads
section("It reads what has already been paid for, and calls nobody")

src = (ROOT / "hub" / "client_logos.py").read_text(encoding="utf-8")
check("no provider is called from here",
      "brand_lookup.lookup" in src or "requests.post" in src, False)
check("the brand record is read as already stored",
      "client_brand.brand_kit" in src)
check("...and so is the last site scan",
      "scan_facts.brand_observed" in src)

lk = (ROOT / "hub" / "brand_lookup.py").read_text(encoding="utf-8")
check("filing hangs off the branch that already spent a call",
      lk.index("client_logos.file_logos") > lk.index('"source": "lookup"')
      - 900)
hub = (ROOT / "hub" / "__init__.py").read_text(encoding="utf-8")
check("and the button that files without paying is a POST",
      '@app.route("/api/client/logos", methods=["POST"])' in hub)


# ------------------------------------------------------ 2. the dedupe
section("Two sources, one mark — and two sources, two marks")

filed = install(
    {"https://brand.example/logo.png": (PNG_A, ".png", ""),
     "https://scanned.example/logo.png": (PNG_A, ".png", "")},
    [{"url": "https://brand.example/logo.png", "source": "brand", "kind": "logo"},
     {"url": "https://scanned.example/logo.png", "source": "scan", "kind": "logo"}])
res = cl.file_logos("Riverside HVAC")
check("the same bytes under two URLs are filed once", len(res["filed"]), 1)
check("...from the brand record, which is the stronger claim",
      res["filed"][0]["source"], "brand")
check("...and the agreement is reported rather than dropped",
      res["sources_agreed"], True)
check("the summary says so in words",
      "same mark" in cl.summary(res))

filed = install(
    {"https://brand.example/logo.png": (PNG_A, ".png", ""),
     "https://scanned.example/other.png": (PNG_B, ".png", "")},
    [{"url": "https://brand.example/logo.png", "source": "brand", "kind": "logo"},
     {"url": "https://scanned.example/other.png", "source": "scan", "kind": "logo"}])
res = cl.file_logos("Riverside HVAC")
check("two different logos are both filed", len(res["filed"]), 2)
check("...and are not reported as agreeing", res["sources_agreed"], False)
check("each says where it came from, in words a rep can act on",
      sorted(r["from"] for r in res["filed"]),
      ["seen on their website", "their brand record"])


# ------------------------------------------------------- 3. what it files
section("What lands in the gallery")

check("it goes in under a Logo collection",
      [f["kind"] for f in filed], ["logo", "logo"])
check("...with the folder named rather than left as a bare key",
      [f["label"] for f in filed], ["Logo", "Logo"])
# A mark lifted off a home page is a candidate. That claim has to travel with
# the file or somebody puts it on a document a client reads.
check("a logo seen on a website says so on the file itself",
      any("seen on their website" in f["alt"] for f in filed))
check("...and one from the brand record says that",
      any("their brand record" in f["alt"] for f in filed))
# The Suite media library is what a live funnel draws from.
check("nothing is pushed into the client's Suite library",
      all(f["push_to_suite"] is False for f in filed))
# Content-addressed, so a second run overwrites nothing and creates nothing.
digest = hashlib.sha256(PNG_A).hexdigest()[:16]
check("the stored id is derived from the content, so a re-run is a no-op",
      any(digest in f["public_id"] for f in filed))


# --------------------------------------------------- 4. the failures
section("A URL that will not fetch is named, not skipped")

filed = install(
    {"https://brand.example/gone.png": (b"", "", "the link is dead (404)")},
    [{"url": "https://brand.example/gone.png", "source": "brand", "kind": "logo"}])
res = cl.file_logos("Riverside HVAC")
check("nothing is filed", res["filed"], [])
check("nothing broken is filed either", filed, [])
check("the failure is named with its reason",
      res["failed"][0]["error"], "the link is dead (404)")
check("...and the summary carries it rather than reading as nothing found",
      "the link is dead (404)" in cl.summary(res))

# "No logo on file" and "we could not look" are different answers.
filed = install({}, [])
res = cl.file_logos("Riverside HVAC")
check("a client with no logo is told so", cl.summary(res).startswith(
    "No logo was found to file"))
res = cl.file_logos("")
check("and no client named files nothing", res["filed"], [])


shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "-" * 60)
print(f"{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
