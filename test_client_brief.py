"""hub/client_brief.py — the one client record every AI feature reads.

    python3 test_client_brief.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1brief_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "client-brief-test-secret"

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


import hub.client_brief as client_brief                              # noqa: E402
from hub import scan_facts                                           # noqa: E402


# ---------------------------------------------------------------------------
# A canned Insites-shaped report for a fixture "smart1marketing.com" scan.
# ---------------------------------------------------------------------------
FIXTURE_REPORT = {
    "meta": {
        "detected_name": "Smart 1 Marketing",
        "detected_address": "123 Main St, Indianapolis, IN 46204",
        "detected_phone": "(317) 555-0100",
        "google_maps_place_id": "ChIJfixture",
        "primary_industry": "Marketing Agency",
    },
    "local_presence": {
        "business_city": "Indianapolis",
        "business_state": "IN",
        "business_zip": "46204",
        "business_street": "123 Main St",
        "business_name": "Smart 1 Marketing",
        "business_phone": "(317) 555-0100",
        "business_email": "hello@smart1marketing.com",
    },
    "colour_scheme": {
        "primary_accent_colour": "1a73e8",
        "primary_background_colour": "ffffff",
    },
    "logo": {"logo_url": "https://smart1marketing.com/logo.png",
             "has_detected_logo": True},
    "headings": {"homepage_h1_content": "Marketing that works"},
    "page_titles_and_descriptions": {
        "homepage_title_tag": "Smart 1 Marketing — Local Advertising",
    },
    "google_business_profile": {
        "review_rating": 4.8,
        "review_count": 212,
        "is_listing_claimed": True,
        "is_listing_complete": True,
        "listing_url": "https://maps.google.com/fixture",
    },
    "reviews": {"total_reviews_count": 300, "average_review_rating": 4.7,
               "competitors_more_reviews_percentage": 12,
               "competitors_higher_rating_percentage": 8,
               "competitors_better_reviews_percentage": 5},
    "analytics": {"analytics_tool": "GA4"},
    "google_ads_readiness": {"has_google_tag": True, "is_google_ads_ready": True},
    "retargeting": {"has_facebook_pixel": True},
    "paid_search": {"has_adwords_spend": True, "average_adspend": 2400,
                    "average_adtraffic": 900},
    "organic_search": {"average_monthly_traffic": 5200,
                       "num_keywords_ranked_for": 340},
    "sitemap": {"has_sitemap": True},
    "blog": {"has_blog": True, "blog_post_count": 42},
}

FIXTURE_META = {
    "domain": "smart1marketing.com",
    "score": 92,
    "tier": "gold",
    "scanned_at": "2026-09-01 12:00:00",
    "public_id": "fixture-scan-1",
    "scan_url": "/scans/scan/fixture-scan-1",
}

FIXTURE_ROW = {
    "public_id": "fixture-scan-1",
    "domain_key": "smart1marketing.com",
    "overall_score": 92,
    "tier": "gold",
    "completed_at": "2026-09-01 12:00:00",
    "created_at": "2026-09-01 12:00:00",
}


def _patch_scan(report=None, row=None, err=""):
    """Stand in for scan_facts._latest(), which every reader in this module
    goes through, so the test never needs a real sqlite Scan row."""
    def _fake(domain):
        return (report or {}, row or {}, err)
    scan_facts._latest = _fake


_real_latest = scan_facts._latest


def _restore_scan():
    scan_facts._latest = _real_latest


# ---------------------------------------------------------------------------
section("build() with nothing on file, never raises")
# ---------------------------------------------------------------------------

_patch_scan(report={}, row={}, err="")
brief = client_brief.build("Nobody Ever Heard Of Inc")
check("identity is measured False", brief["identity"].get("measured"), False)
check("location is measured False", brief["location"].get("measured"), False)
check("contact is measured False", brief["contact"].get("measured"), False)
check("brand is measured False", brief["brand"].get("measured"), False)
check("scan is measured False", brief["scan"].get("measured"), False)
check("unknown is populated", len(brief["unknown"]) > 0, True)
check("never raised", True, True)


# ---------------------------------------------------------------------------
section("build() against the smart1marketing.com fixture")
# ---------------------------------------------------------------------------

_patch_scan(report=FIXTURE_REPORT, row=FIXTURE_ROW, err="")
brief = client_brief.build("Smart 1 Marketing", "smart1marketing.com")

check("location.city comes from local_presence.business_city",
      brief["location"].get("city", {}).get("value"), "Indianapolis")
check("location.state comes from local_presence.business_state",
      brief["location"].get("state", {}).get("value"), "IN")
check("proof.review_count is present",
      brief["proof"].get("review_count", {}).get("value"), 212)
check("proof.competitors_more_reviews_pct is present",
      brief["proof"].get("competitors_more_reviews_pct", {}).get("value"), 12)
check("identity.site_name carries a source",
      "source" in brief["identity"].get("site_name", {}), True)
check("brand.palette has at least one hex", len(brief["brand"].get("palette", [])) >= 1, True)
check("scan.score carries through", brief["scan"].get("score", {}).get("value"), 92)


# ---------------------------------------------------------------------------
section("A failing source loses only its own section")
# ---------------------------------------------------------------------------

_patch_scan(report={}, row={}, err="db exploded")
brief = client_brief.build("Smart 1 Marketing", "smart1marketing.com")
check("scan reports the error rather than raising",
      brief["scan"].get("measured"), False)
check("scan names the error", "db exploded" in str(brief["scan"].get("error", "")), True)
check("identity is still a dict (did not raise)", isinstance(brief["identity"], dict), True)

_restore_scan()


# ---------------------------------------------------------------------------
section('render(..., "image") never mentions spend, pixels, reviews or keywords')
# ---------------------------------------------------------------------------

_patch_scan(report=FIXTURE_REPORT, row=FIXTURE_ROW, err="")
brief = client_brief.build("Smart 1 Marketing", "smart1marketing.com")
image_text = client_brief.render(brief, "image")
for banned in ("spend", "pixel", "review", "keyword"):
    check(f'"image" render does not mention "{banned}"',
          banned in image_text.lower(), False)

strategy_text = client_brief.render(brief, "strategy")
check('"strategy" render mentions spend',
      "spend" in strategy_text.lower(), True)
check('"strategy" render mentions review',
      "review" in strategy_text.lower(), True)
check('"strategy" render mentions keyword',
      "keyword" in strategy_text.lower(), True)

_restore_scan()


# ---------------------------------------------------------------------------
section("render() returns \"\" when nothing was looked up")
# ---------------------------------------------------------------------------

empty_brief = {"client": "Ghost Co", "unknown": []}
check('render() of a brief with nothing measured is ""',
      client_brief.render(empty_brief, "copy"), "")


# ---------------------------------------------------------------------------
section("build_from_fields() — a prospect, not a client")
# ---------------------------------------------------------------------------

prospect = client_brief.build_from_fields({
    "company": "Riverside Boats", "zip": "46032",
    "phone": "(317) 555-0199", "website": "riversideboats.example",
})
check("identity.name source is 'form'",
      prospect["identity"]["name"]["source"], "form")
check("location.zip source is 'form'",
      prospect["location"]["zip"]["source"], "form")
prospect_text = client_brief.render(prospect, "copy")
check("prospect render carries 'the form' as the source",
      "the form" in prospect_text, True)


# ---------------------------------------------------------------------------
section("for_prompt() is byte-identical, source-shape unchanged")
# ---------------------------------------------------------------------------

_patch_scan(report=FIXTURE_REPORT, row=FIXTURE_ROW, err="")
out1 = client_brief.for_prompt("Smart 1 Marketing", "smart1marketing.com")
out2 = client_brief.for_prompt("Smart 1 Marketing", "smart1marketing.com")
check("for_prompt() is deterministic across two calls", out1 == out2, True)
check("for_prompt() opens with the standard heading",
      out1.startswith("What we know about"), True)
check("for_prompt() ends with the standard instruction",
      out1.rstrip().endswith("that is not listed above."), True)
check("for_prompt(heading=...) replaces only the heading line",
      client_brief.for_prompt("Smart 1 Marketing", "smart1marketing.com",
                              heading="CUSTOM HEADING").split("\n")[0],
      "CUSTOM HEADING")

_restore_scan()


# ---------------------------------------------------------------------------
section("for_prompt() on an unknown client still returns \"\"")
# ---------------------------------------------------------------------------

_patch_scan(report={}, row={}, err="")
check('for_prompt() of a totally unknown business is ""',
      client_brief.for_prompt("Totally Unknown Business Xyz", ""), "")
_restore_scan()


shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
