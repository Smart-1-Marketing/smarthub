"""hub/seo_queue.py -- the SEO client page's "what needs doing" rows.

    python3 test_seo_queue.py
"""
import os
import shutil
import sys
import tempfile
from unittest import mock

TMP = tempfile.mkdtemp(prefix="s1seoqueue_test_")
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


from hub import seo_queue


NO_SCAN_BRIEF = {"client": "Nothing Scanned Co", "seo": {"measured": False}}

FULL_BRIEF = {
    "client": "Acme Plumbing",
    "seo": {
        "images_no_alt_count": {"value": 12, "source": "scan"},
        "images_to_optimise_count": {"value": 3, "source": "scan"},
        "pages_missing_title_count": {"value": 2, "source": "scan"},
        "pages_missing_description_count": {"value": 4, "source": "scan"},
        "pages_missing_h1_count": {"value": 1, "source": "scan"},
        "missing_schema_items": {"value": 6, "source": "scan"},
        "has_sitemap": {"value": False, "source": "scan"},
        "has_og_tags": {"value": False, "source": "scan"},
        "is_voice_search_optimised": {"value": False, "source": "scan"},
        "best_keyword_opportunities": {"value": "gutter cleaning, roof repair", "source": "scan"},
        "target_keywords": {"value": "emergency plumber", "source": "scan"},
    },
}

CLEAN_BRIEF = {
    "client": "Clean Co",
    "seo": {
        "images_no_alt_count": {"value": 0, "source": "scan"},
        "has_sitemap": {"value": True, "source": "scan"},
        "has_og_tags": {"value": True, "source": "scan"},
        "is_voice_search_optimised": {"value": True, "source": "scan"},
    },
}


# ---------------------------------------------------------------------------
section("A client with no scan gets [] and reads as not-measured")
with mock.patch("hub.client_brief.build", return_value=NO_SCAN_BRIEF):
    rows = seo_queue.rows("Nothing Scanned Co")
    measured = seo_queue.measured_for("Nothing Scanned Co")
check("rows() is empty", rows == [], rows)
check("measured_for() is False -- 'not measured', never 'nothing to do'",
      measured is False)


# ---------------------------------------------------------------------------
section("A scanned client with real fix counts gets striped rows")
with mock.patch("hub.client_brief.build", return_value=FULL_BRIEF), \
     mock.patch("hub.seo.load_store", return_value={}), \
     mock.patch("hub.llms_hosting.published", return_value={}), \
     mock.patch("hub.seo.blogs_health", return_value={"overdue": 0}):
    rows = seo_queue.rows("Acme Plumbing")
    measured = seo_queue.measured_for("Acme Plumbing")

check("measured_for() is True", measured is True)
sections = {r["section"] for r in rows}
check("alt-text row present", "alt" in sections, sections)
check("images-to-optimise row present", "images" in sections, sections)
check("missing-title row present", "titles" in sections, sections)
check("missing-description row present", "descriptions" in sections, sections)
check("missing-h1 row present", "headings" in sections, sections)
check("missing-schema row present", "schema" in sections, sections)
check("no-sitemap row present", "sitemap" in sections, sections)
check("no-og-tags row present", "og" in sections, sections)
check("not-voice-optimised row present", "voice" in sections, sections)

title_row = next(r for r in rows if r["section"] == "titles")
check("counts appear in the row's own title", "2" in title_row["title"], title_row)
check("every row names a tool", all(r.get("tool") for r in rows))
check("every row carries a link", all(r.get("link") for r in rows))
check("rows are sorted worst-first",
      [r["level"] for r in rows] == sorted([r["level"] for r in rows],
                                           key=lambda l: {"bad": 0, "warn": 1, "info": 2}[l]))


# ---------------------------------------------------------------------------
section("A clean site produces no rows for what it does not fail")
with mock.patch("hub.client_brief.build", return_value=CLEAN_BRIEF), \
     mock.patch("hub.seo.load_store", return_value={}), \
     mock.patch("hub.llms_hosting.published", return_value={}), \
     mock.patch("hub.seo.blogs_health", return_value={"overdue": 0}):
    rows2 = seo_queue.rows("Clean Co")
check("no alt-text row for a zero count", not any(r["section"] == "alt" for r in rows2), rows2)
check("no sitemap/og/voice rows when all True",
      not any(r["section"] in ("sitemap", "og", "voice") for r in rows2), rows2)


# ---------------------------------------------------------------------------
section("Every row's link resolves to an existing route")
# Mirrors the linkcheck convention: every literal path a row can produce
# must be a real, mounted route.
KNOWN_LINK_BASES = ("/tools/seo-images/", "/seo/client")
for r in rows + rows2:
    check(f"{r['section']} link starts with a known route",
          any(r["link"].startswith(b) for b in KNOWN_LINK_BASES), r["link"])


# ---------------------------------------------------------------------------
section("topic_ideas() lists suggested topics, AI-assisted and unpublished")
with mock.patch("hub.client_brief.build", return_value=FULL_BRIEF):
    topics = seo_queue.topic_ideas("Acme Plumbing")
check("topics measured when the scan carried keyword facts", topics["measured"] is True)
check("gutter cleaning is offered as a topic", "gutter cleaning" in topics["topics"], topics)
check("emergency plumber is offered as a topic", "emergency plumber" in topics["topics"], topics)

with mock.patch("hub.client_brief.build", return_value=NO_SCAN_BRIEF):
    topics2 = seo_queue.topic_ideas("Nothing Scanned Co")
check("no topics, and not measured, with no scan", topics2 == {"topics": [], "measured": False})


# ---------------------------------------------------------------------------
section("broken_link_count() reads the same audit field seo_queue's rows do")
BROKEN_REPORT = {"broken_links": {"num_broken_links": 5}}
with mock.patch("hub.scan_facts.latest_report", return_value=(BROKEN_REPORT, {}, "")):
    n = seo_queue.broken_link_count("Acme Plumbing", "acmeplumbing.com")
check("broken link count read from the scan", n == 5, n)

with mock.patch("hub.scan_facts.latest_report", return_value=({}, {}, "no scan")):
    n2 = seo_queue.broken_link_count("Whoever", "")
check("no scan -> None, never zero", n2 is None)


# ---------------------------------------------------------------------------
section("rows() never raises on a broken brief")
with mock.patch("hub.client_brief.build", side_effect=RuntimeError("boom")):
    rows3 = seo_queue.rows("Whatever")
check("empty list on a brief that raised", rows3 == [])
check("no client given -> []", seo_queue.rows("") == [])


shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
