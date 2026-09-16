"""hub/proposal_scan_insights.py -- WO-3e: proof lines and scan-based scope.

    python3 test_proposal_scan_insights.py
"""
import os
import shutil
import sys
import tempfile
from unittest import mock

TMP = tempfile.mkdtemp(prefix="s1proposalscan_test_")
os.environ["HUB_DATA_DIR"] = TMP
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_passed = _failed = 0


def check(label, ok, detail=""):
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}  {detail}")


def section(title):
    print(f"\n== {title} ==")


from hub import proposal_scan_insights as psi


def _fact(v, source="scan"):
    return {"value": v, "source": source}


# ---------------------------------------------------------------------------
section("where_you_stand() omits any unmeasured line")
FULL = {
    "proof": {
        "review_count": _fact(40),
        "review_rating": _fact(4.5),
        "competitors_more_reviews_pct": _fact(62),
    },
    "seo": {
        "average_monthly_traffic": _fact(1200),
        "num_keywords_ranked_for": _fact(85),
    },
}
with mock.patch("hub.client_brief.build", return_value=FULL):
    out = psi.where_you_stand("Acme Plumbing", "acmeplumbing.com")
check("measured is True", out["measured"] is True)
check("review line present", any("40 Google reviews" in ln for ln in out["lines"]), out)
check("competitor line present", any("62" in ln for ln in out["lines"]), out)
check("organic traffic+keywords line present",
      any("1,200" in ln and "85" in ln for ln in out["lines"]), out)

PARTIAL = {"proof": {}, "seo": {"average_monthly_traffic": _fact(500)}}
with mock.patch("hub.client_brief.build", return_value=PARTIAL):
    out2 = psi.where_you_stand("Acme", "")
check("only the measured line appears", out2["lines"] == ["Your site draws an estimated 500 organic visits a month."], out2)
check("measured True even with just one line", out2["measured"] is True)

EMPTY = {"proof": {}, "seo": {}}
with mock.patch("hub.client_brief.build", return_value=EMPTY):
    out3 = psi.where_you_stand("Nobody Scanned Co", "")
check("nothing measured -> empty lines, measured False", out3 == {"lines": [], "measured": False})

with mock.patch("hub.client_brief.build", side_effect=RuntimeError("boom")):
    out4 = psi.where_you_stand("Whatever", "")
check("never raises on a broken brief", out4 == {"lines": [], "measured": False})


# ---------------------------------------------------------------------------
section("scope_for() derives small/medium/large from the audit's fix counts")

# smart1marketing.com fixture -- 91 total fixes: comfortably in the medium
# band the work order names for this exact fixture.
FIXTURE_COUNTS = {
    "pages_missing_title_count": _fact(4),
    "pages_missing_description_count": _fact(6),
    "pages_missing_h1_count": _fact(2),
    "missing_schema_items": _fact(8),
    "images_no_alt_count": _fact(3),
}
with mock.patch("hub.client_brief.build", return_value={"seo": FIXTURE_COUNTS}), \
     mock.patch("hub.seo_queue.broken_link_count", return_value=0):
    scope = psi.scope_for("smart1marketing.com", "smart1marketing.com")
check("measured is True", scope["measured"] is True)
check("total is the sum of the counts", scope["total"] == 4 + 6 + 2 + 8 + 3, scope)
check("tier is medium for this fixture", scope["tier"] == "medium", scope)
check("derivation lists every non-zero count",
      len(scope["derivation"]) == 5, scope["derivation"])
check("the derivation note shows the arithmetic and the tier",
      str(scope["total"]) in scope["note"] and "medium" in scope["note"], scope["note"])
check("the note names the house thresholds",
      "small" in scope["note"] and "large" in scope["note"], scope["note"])

with mock.patch("hub.client_brief.build",
                return_value={"seo": {"pages_missing_title_count": _fact(2)}}), \
     mock.patch("hub.seo_queue.broken_link_count", return_value=0):
    small = psi.scope_for("Tiny Co", "")
check("a small total scores small", small["tier"] == "small", small)

big_counts = {k: _fact(20) for k in FIXTURE_COUNTS}
with mock.patch("hub.client_brief.build", return_value={"seo": big_counts}), \
     mock.patch("hub.seo_queue.broken_link_count", return_value=15):
    large = psi.scope_for("Big Mess Co", "")
check("a large total scores large", large["tier"] == "large", large)
check("broken links are counted into the total",
      "broken_links_count" in large["counts"] and large["counts"]["broken_links_count"] == 15,
      large["counts"])

# ---------------------------------------------------------------------------
section("scope_for() never guesses a tier from nothing measured")
with mock.patch("hub.client_brief.build", return_value={"seo": {"measured": False}}), \
     mock.patch("hub.seo_queue.broken_link_count", return_value=None):
    none_scope = psi.scope_for("Nothing Co", "")
check("tier is None, never defaulted", none_scope["tier"] is None, none_scope)
check("measured is False", none_scope["measured"] is False)

with mock.patch("hub.client_brief.build", side_effect=RuntimeError("boom")):
    broken_scope = psi.scope_for("Whatever", "")
check("never raises on a broken brief",
      broken_scope == {"tier": None, "total": None, "counts": {}, "derivation": [],
                       "note": "", "measured": False})


# ---------------------------------------------------------------------------
section("The seo_aeo rate-card entry exists, is placeholder-priced, and is "
        "in sync between the two copies")
from hub import rate_card
rate_card.products.cache_clear()
seo_aeo = next((p for p in rate_card.products()
               if "SEO & AEO" in p.get("label", "")), None)
check("a SEO & AEO product exists on the rate card", seo_aeo is not None)
check("it carries no rate type -- it is a custom quote, never CPM math",
      seo_aeo is not None and seo_aeo.get("rate_type") is None, seo_aeo)
check("it is clearly marked as a placeholder, not yet priced",
      seo_aeo is not None and "PLACEHOLDER" in seo_aeo.get("listed_rate", "").upper(), seo_aeo)
drift = rate_card.check_drift()
check("the IO template's embedded copy carries the same product",
      drift.get("in_sync") is True, drift)


shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
