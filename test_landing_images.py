"""Another agency's photograph, captioned as the client's own premises.

    python3 test_landing_images.py

No pytest, no new dependencies, a temporary data directory and a throwaway
SQLite database, so it never touches /var/data or the real one. Nothing here
reaches a scan or a stock provider: both are stubbed, because what is worth
asserting is what this module does with what they return.

## Why this file exists

`hub/landing_images.py` picks the pictures on a landing page a prospect
reads. Its docstring names its first and best source — *"**The client's own
site.** A photo of their actual premises, van or team beats any stock
library, and it is the only source that is genuinely about them"* — and ends
with the rule the whole module is under:

> Stock photography is illustration and is treated as such: it is never
> captioned as the client's own work … a page may be short, it may not lie.

No test named it, and it was breaking both halves of that.

**`from_site()` had no domain check at all.** It regexed every image URL out
of a 400 KB scan payload and labelled all of them `their site`. A scan
payload is 440 fields of whatever the crawler saw, so those URLs belong to
all sorts of people. Against a realistic one, six pictures came back and
**five were somebody else's**: the scan vendor's own screenshot, a Facebook
social card, a Google static map, a Google ad creative, and **another
agency's Cloudinary folder** — any of which could become the hero of a
landing page presented as the client's own premises. That is
`client_urls.NOT_A_WEBSITE` one module over, and it was not hypothetical
there either: on this deployment's own export *every single* click-thru
domain turned out to be a file host.

**And a picture off their site carried no dimensions**, so `pick()`'s
`img.get("wide", True)` read every one of them as hero-worthy — the
`_MIN_HERO_WIDE` floor skipped entirely for the source this module prefers.

**And `source` said "their site" whenever the site search returned
anything**, however much of what was actually picked came from a stock
library. Which is the line above, in one word.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1landimg_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "landing-images-test-secret"

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


from hub import landing_images as LI                           # noqa: E402
import modules.scans.app as _scans                             # noqa: E402


def scan(payload):
    _scans.latest_payload_for_domain = lambda url: payload


def site(brief):
    """Guarded: a regression must name itself rather than ending the run."""
    try:
        return LI.from_site(brief)
    except Exception as exc:                                   # noqa: BLE001
        return {"raised": f"{type(exc).__name__}: {exc}"}


def picked(brief, benefits=0):
    try:
        return LI.pick(brief, benefits=benefits)
    except Exception as exc:                                   # noqa: BLE001
        return {"raised": f"{type(exc).__name__}: {exc}"}


# A scan payload as one actually arrives: their own pictures among everybody
# else's. Every URL here is the shape a real crawler records.
PAYLOAD = {
    "meta": {"og_image": "https://acme-tyre.com/img/forecourt.jpg"},
    "gallery": ["https://cdn.acme-tyre.com/van.jpg",
                "https://www.acme-tyre.com/team.jpg"],
    "screenshots": {"desktop": "https://cdn.insites.example/shot/abc.png"},
    "social": {"facebook_card": "https://scontent.xx.fbcdn.net/v/t1/p.jpg"},
    "embeds": ["https://maps.googleapis.com/staticmap/tile.png",
               "https://res.cloudinary.com/other-agency/image/upload/hero.jpg"],
    "ads": {"creative": "https://tpc.googlesyndication.com/simgad/999.jpg"},
    "logo": {"logo_url": "https://acme-tyre.com/img/logo.png"},
}


# =====================================================================
section("A picture is theirs, or it is not on the page as theirs")
# =====================================================================
scan(PAYLOAD)
got = site({"website": "https://acme-tyre.com/"})
urls = [i["url"] for i in got.get("images", [])]

check("their own domain is theirs",
      "https://acme-tyre.com/img/forecourt.jpg" in urls, True)
check("and a subdomain is too -- cdn. and images. are ordinarily theirs",
      "https://cdn.acme-tyre.com/van.jpg" in urls, True)
check("and www.", "https://www.acme-tyre.com/team.jpg" in urls, True)
check("three of theirs", len(urls), 3)

for stranger, whose in (
        ("https://cdn.insites.example/shot/abc.png", "the scan vendor's own screenshot"),
        ("https://scontent.xx.fbcdn.net/v/t1/p.jpg", "a Facebook social card"),
        ("https://maps.googleapis.com/staticmap/tile.png", "a Google static map"),
        ("https://res.cloudinary.com/other-agency/image/upload/hero.jpg",
         "another agency's Cloudinary folder"),
        ("https://tpc.googlesyndication.com/simgad/999.jpg", "a Google ad creative")):
    check(f"{whose} is not", stranger in urls, False)

# Counted, not silently dropped: a list that quietly gets shorter cannot be
# told from a site that has no pictures on it.
check("and what was refused is counted", got.get("rejected"), 5)
check("the logo is still skipped by name, as it always was",
      "https://acme-tyre.com/img/logo.png" in urls, False)

check("theirs() reads a bare domain", LI.theirs("https://acme-tyre.com/a.jpg",
                                                "acme-tyre.com"), True)
check("and a URL with a path, port and scheme",
      LI.theirs("https://ACME-TYRE.com:443/x/a.jpg", "http://www.acme-tyre.com/"),
      True)
check("a lookalike domain is not theirs",
      LI.theirs("https://acme-tyre.com.evil.test/a.jpg", "acme-tyre.com"), False)
check("nor is a domain that merely ends the same way",
      LI.theirs("https://notacme-tyre.com/a.jpg", "acme-tyre.com"), False)
check("with no website on file, nothing is theirs",
      LI.theirs("https://acme-tyre.com/a.jpg", ""), False)
check("and a brief with no website reads no scan at all",
      site({}), {"images": [], "rejected": 0})


# =====================================================================
section("What the set is, not what it was searched for")
# =====================================================================
STOCK = [{"url": "https://images.pexels.test/1.jpg", "wide": True, "alt": "",
          "credit": "A Photographer", "source": "pexels"},
         {"url": "https://images.pexels.test/2.jpg", "wide": True, "alt": "",
          "credit": "B Photographer", "source": "pexels"}]
LI.stock = lambda brief, want=6: [dict(s) for s in STOCK]

scan(PAYLOAD)
all_theirs = picked({"website": "acme-tyre.com"}, benefits=0)
check("a set entirely off their site says so", all_theirs.get("source"),
      "their site")
check("and carries how many were refused", all_theirs.get("not_theirs"), 5)

# One picture of theirs and a card that has to come from stock.
scan({"meta": {"og_image": "https://acme-tyre.com/img/forecourt.jpg"}})
mixed = picked({"website": "acme-tyre.com"}, benefits=1)
check("the hero is theirs", mixed["hero"]["source"], "their site")
check("the card is not", [c["source"] for c in mixed["cards"]], ["pexels"])
check("so the set is not captioned as their own work",
      mixed.get("source"), "their site and stock")

scan({"embeds": ["https://res.cloudinary.com/other/x.jpg"]})
none_theirs = picked({"website": "acme-tyre.com"}, benefits=0)
check("nothing of theirs is stock, plainly", none_theirs.get("source"), "stock")
check("and the one that was refused is still counted",
      none_theirs.get("not_theirs"), 1)


# =====================================================================
section("A size nobody measured is not a size")
# =====================================================================
scan(PAYLOAD)
_site_imgs = site({"website": "acme-tyre.com"})["images"]
check("a picture off their site carries no measured width",
      _site_imgs[0].get("wide"), None)
check("rather than being absent, which read as True",
      "wide" in _site_imgs[0], True)

# A stock image measured and found narrow is still skipped for the hero and
# the band -- that half worked before and must keep working.
LI.stock = lambda brief, want=6: [
    {"url": "https://images.pexels.test/narrow.jpg", "wide": False, "alt": "",
     "credit": "N", "source": "pexels"},
    {"url": "https://images.pexels.test/wide.jpg", "wide": True, "alt": "",
     "credit": "W", "source": "pexels"}]
scan({})
narrow = picked({"website": "acme-tyre.com"}, benefits=0)
check("a narrow stock picture is not the hero",
      narrow["hero"]["url"], "https://images.pexels.test/wide.jpg")

# Their site still comes first, which is this module's stated order: an
# unmeasured picture of theirs beats a measured stock one.
scan({"meta": {"og_image": "https://acme-tyre.com/img/forecourt.jpg"}})
first = picked({"website": "acme-tyre.com"}, benefits=0)
check("an unmeasured picture of theirs still leads",
      first["hero"]["source"], "their site")


# =====================================================================
section("Nothing at all is a normal state")
# =====================================================================
LI.stock = lambda brief, want=6: []
scan({})
none = picked({"website": "acme-tyre.com"}, benefits=3)
check("no pictures is not an error", none.get("available"), False)
check("the hero is empty rather than invented", none.get("hero"), None)
check("and so are the cards", none.get("cards"), [])
check("the source claims nothing", none.get("source"), "")
check("and a caller can still read the refused count",
      none.get("not_theirs"), 0)

# All-or-nothing on the cards: three photos and one gap reads as a page still
# loading, which is the rule the docstring already gave.
LI.stock = lambda brief, want=6: [
    {"url": f"https://images.pexels.test/{i}.jpg", "wide": True, "alt": "",
     "credit": "", "source": "pexels"} for i in range(2)]
short = picked({"website": "acme-tyre.com"}, benefits=4)
check("four benefits and two pictures gets no cards at all",
      short.get("cards"), [])
check("but still a hero", bool(short.get("hero")), True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
