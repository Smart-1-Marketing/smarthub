"""hub/partner_pages/*.html — the five self-contained pages a partner is sent.

    python3 test_partner_pages.py

hub/partner.py's own docstring says these were "rewritten once, on the way
into the repo, to /partner/sales-kit" and that "nothing rewrites at request
time" — a claim that was false for the one value every page actually reads
its cross-links from. `LINKS` was left with the pre-rewrite, site-root paths
(`/sales-kit`, `/creative-specs`, ...), so the top nav on all five pages and
the "Learn more" / "Creative specs" callouts on the rate card 404'd for
anyone who clicked one, silently: every link resolves as a URL, the page
renders, and it simply lands on nothing this Hub serves.

Only `rate-card-universal.html` calls `LINKS.kit` / `LINKS.specs` today; the
other four declare the same object and never read it, which is why the drift
was invisible on them — this asserts the object's *value* is right
everywhere it is declared, not only where a bug happened to surface first.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parent
sys.path.insert(0, str(ROOT))

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok   {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}\n         got:  {got!r}\n         want: {want!r}")


def check_true(label, got):
    check(label, bool(got), True)


print("\nPartner pages\n" + "=" * 60)

PAGES = ("rate-card-universal", "creative-specs", "sales-kit",
        "learning-library", "digital-dictionary")
DIR = ROOT / "hub/partner_pages"

EXPECTED_LINKS = {
    "rate": "/partner/rate-card-universal", "specs": "/partner/creative-specs",
    "kit": "/partner/sales-kit", "library": "/partner/learning-library",
    "dict": "/partner/digital-dictionary",
}

print("\n-- every page's LINKS object resolves under /partner/ --")
for slug in PAGES:
    text = (DIR / f"{slug}.html").read_text(encoding="utf-8")
    m = re.search(r"const LINKS\s*=\s*(\{[^}]*\})", text)
    check_true(f"{slug}.html declares LINKS", m is not None)
    links = json.loads(m.group(1))
    check(f"{slug}.html: LINKS resolves to the served /partner/ prefix",
          links, EXPECTED_LINKS)

print("\n-- the rate card's own cross-links use it, and land under /partner/ --")
rate_card = (DIR / "rate-card-universal.html").read_text(encoding="utf-8")
check_true("the 'Learn more' link is built from LINKS.kit",
           "LINKS.kit+'#'" in rate_card or "LINKS.kit + '#'" in rate_card)
check_true("the 'Creative specs' link is built from LINKS.specs",
           "LINKS.specs+'#'" in rate_card or "LINKS.specs + '#'" in rate_card)

print("\n-- the top nav on every page is data-driven from the same object --")
for slug in PAGES:
    text = (DIR / f"{slug}.html").read_text(encoding="utf-8")
    check_true(f"{slug}.html wires .topnav a[data-link] from LINKS",
               "a.href=LINKS[a.dataset.link]" in text.replace(" ", ""))

print("\n-- a mailto that may fire silently keeps a visible fallback --")
for slug in PAGES:
    text = (DIR / f"{slug}.html").read_text(encoding="utf-8")
    check_true(f"{slug}.html: the pricing-help button carries a visible address",
               'class="help-addr"' in text
               and "clientsuccess@smart1marketing.com" in text.split(
                   'class="help-addr"')[1][:200])

print("\n" + "=" * 60)
print(f"{_passed} passed, {_failed} failed")
if _failed:
    sys.exit(1)
