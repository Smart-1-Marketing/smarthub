"""What we could sell each client, out of audits already paid for.

    python3 test_upsell_report.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database, so it never touches
/var/data or the real one.

## Why this file exists

`hub/website_audit.py` turns an audit into findings that carry their own
evidence, and nothing read that across the client book. This report does. Every
failure below is one where it would go on looking like a healthy report while
telling somebody something false about a client who pays us:

  1. **Coverage is the honest half.** A client nobody has audited is *not
     measured*, never a clean bill, and one whose reading has gone stale is
     named as stale. Without that the report gets quieter the worse our
     coverage gets — the one direction a sales report must not fail in.

  2. **Recorded and observed are different claims.** The two reports next door
     read what we have *attached*; this reads what is *on the site*. Where
     they disagree, that is the finding — folding them together destroys the
     only evidence of it.

  3. **Tri-state throughout.** A check a plan did not run answers `None`, and
     `None` read as "no" tells a rep a tag is missing when nobody looked.

  4. **The finding leads, the product follows.** What is in the cell survives
     being read out to the client; the product it points at is on the tooltip.

  5. **A report that could not look is never the day's answer.** `measured:
     False` is what stops `hub/report_cache.py` freezing "we could not read
     the audits" into the shape of "there is nothing to sell" until tomorrow.

  6. **One query per batch, not one per client.** Several hundred clients is
     several hundred round trips and several hundred 440-field blobs held at
     once if this is done a domain at a time.
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1upsell_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "upsell-test-secret"
os.environ["PANEL_PASSWORD"] = "upsell-test-password"

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


from hub import upsell                                        # noqa: E402


# =====================================================================
section("A source that cannot be read is never an empty book")
# =====================================================================

found, error = upsell.audits_for(["example.com"])
check("with no scans table there is an error", bool(error), True)
check("and no rows pretending to be an answer", found, {})
check("no domains at all is not an error", upsell.audits_for([]), ({}, ""))
check("nor is a list of things that are not domains",
      upsell.audits_for(["", "  ", "not a domain"]), ({}, ""))


# The scans table exists once the module that owns it is imported.
from modules.scans import app as scans_app                    # noqa: E402

NOW = datetime.now(timezone.utc)

GAPS = {
    "retargeting": {"has_facebook_pixel": False, "has_google_pixel": False},
    "analytics": {"has_analytics": False, "uses_universal_ga": True},
    "google_business_profile": {"is_listing_found": True,
                                "is_listing_claimed": False, "review_count": 8},
}
# A plan that ran none of those checks. Every field absent, so every rule has
# to answer "not measured" rather than "no".
QUIET = {"meta": {"detected_name": "Quiet Co"}}


def add_scan(public_id, domain, report, *, days=5, status="complete", score=55):
    db = scans_app.SessionLocal()
    try:
        db.add(scans_app.Scan(
            public_id=public_id, domain_key=domain, input_url=domain,
            overall_score=score, status=status, raw_report=json.dumps(report),
            created_at=NOW - timedelta(days=days),
            completed_at=NOW - timedelta(days=days)))
        db.commit()
    finally:
        db.close()


add_scan("s-old", "acme.com", GAPS, days=200)
add_scan("s-new", "acme.com", GAPS, days=4)
add_scan("s-run", "acme.com", GAPS, days=1, status="running")
add_scan("s-stale", "stale.com", GAPS, days=120)
add_scan("s-quiet", "quiet.com", QUIET, days=3)


# =====================================================================
section("The newest completed audit, and only that one")
# =====================================================================

found, error = upsell.audits_for(["acme.com", "stale.com", "quiet.com",
                                  "never-scanned.com"])
check("the read succeeds", error, "")
check("a domain nobody scanned is absent, not empty",
      "never-scanned.com" in found, False)
check("the newest complete scan wins", found["acme.com"]["public_id"], "s-new")
check("a scan still running is not taken as the answer",
      found["acme.com"]["public_id"] != "s-run", True)
check("the reading's age comes with it", found["acme.com"]["age"]["age_days"], 4)
check("and says whether it is still worth quoting from",
      found["acme.com"]["age"]["stale"], False)
check("an old one says so", found["stale.com"]["age"]["stale"], True)

keys = {f["key"] for f in found["acme.com"]["findings"]}
check("the findings come off the audit", "no_retargeting" in keys, True)
check("including the unclaimed listing", "gbp_unclaimed" in keys, True)
check("and thin reviews", "reviews_thin" in keys, True)
check("every finding carries what it costs them",
      all(f["means"] for f in found["acme.com"]["findings"]), True)
check("and the product it points at",
      all(f["sells"] for f in found["acme.com"]["findings"]), True)

check("a plan that checked nothing produces no findings",
      found["quiet.com"]["findings"], [])
check("and no observed facts either, rather than a row of no's",
      {k: v for k, v in found["quiet.com"]["observed"].items() if v is not None},
      {})


# =====================================================================
section("Batched, not one query per client")
# =====================================================================

many = [f"c{i}.com" for i in range(upsell.CHUNK + 25)]
for i in range(0, 5):
    add_scan(f"m{i}", f"c{i}.com", GAPS, days=3)
found, error = upsell.audits_for(many)
check("a book bigger than one chunk still reads", error, "")
check("and finds what is in it", len(found), 5)
check("the chunker covers every item without overlap",
      [x for c in upsell._chunks(list(range(7)), 3) for x in c],
      list(range(7)))
check("and never returns an empty chunk",
      all(c for c in upsell._chunks(list(range(7)), 3)), True)


# =====================================================================
section("Recorded and observed are different claims")
# =====================================================================

d = upsell._disagreements

check("a property on file with no tag on the site is a finding",
      [x["key"] for x in d({"has_ga": True}, {"analytics": False})], ["analytics"])
check("a tag on the site we have never attached is one too",
      [x["key"] for x in d({"has_ga": False}, {"analytics": True})], ["analytics"])
check("and it says somebody else may be administering it",
      "administering" in d({"has_ga": False}, {"analytics": True})[0]["text"], True)
check("the same both ways for Tag Manager",
      [x["key"] for x in d({"has_gtm": True}, {"gtm": False})], ["gtm"])
check("agreement raises nothing", d({"has_ga": True}, {"analytics": True}), [])
check("and so does the other kind of agreement",
      d({"has_ga": False}, {"analytics": False}), [])
check("a check the plan never ran raises nothing at all",
      d({"has_ga": True}, {"analytics": None}), [])
check("Universal Analytics is its own finding",
      [x["key"] for x in d({}, {"universal_ga": True})], ["universal_ga"])
check("and 2023 is in the sentence, because that is the argument",
      "2023" in d({}, {"universal_ga": True})[0]["text"], True)

# The recorded side uses _google_coverage's own spelling. Guessing the key
# reads every client as "no disagreement" and kills the comparison silently.
from hub import qa                                            # noqa: E402
import inspect                                                # noqa: E402
cov_src = inspect.getsource(qa._google_coverage)
check("the keys this compares against are the ones that helper returns",
      all(k in cov_src for k in ('"has_ga"', '"has_gtm"')), True)


# =====================================================================
section("The report itself")
# =====================================================================

# The bands only appear for domains real clients actually have, so the
# fixtures above (which belong to nobody) cannot produce them. Seed against
# two live clients out of the committed export: one read recently, one long
# enough ago to have gone stale.
_live = []
for _name in sorted(qa._client_groups(), key=str.lower):
    _g = qa._client_groups()[_name]
    if not qa._active_within(_g, 60):
        continue
    _dom = qa._google_coverage(_name, _g).get("domain")
    if _dom and _dom not in [d for _, d in _live]:
        _live.append((_name, _dom))
    if len(_live) == 2:
        break
check("the client export has active clients with websites to audit",
      len(_live), 2)
add_scan("live-fresh", _live[0][1], GAPS, days=6)
add_scan("live-stale", _live[1][1], GAPS, days=140)

out = upsell.build()
check("it measures", out["measured"], True)
check("it answers in the QA report shape",
      sorted(k for k in ("columns", "rows", "note", "row_styles")
             if k in out),
      ["columns", "note", "row_styles", "rows"])
check("one style per row", len(out["row_styles"]), len(out["rows"]))
check("the first column is the client", out["columns"][0], "Client")

bands = [r[0]["text"] for r in out["rows"]
         if isinstance(r[0], dict) and r[0].get("group")]
check("the never-audited are a band of their own, not left off",
      any(b.startswith("Never audited") for b in bands), True)
check("and so are the stale",
      any("days old" in b for b in bands), True)
check("the note counts what was measured and what was not",
      "Not measured:" in out["note"], True)
check("and says coverage is not the same as nothing to sell",
      "not clients with nothing to sell" in out["note"], True)

body = [r for r in out["rows"]
        if not (isinstance(r[0], dict) and r[0].get("group"))]
check("every row names its client with a link to the record",
      all(isinstance(r[0], dict) and r[0].get("href", "").startswith("/client360")
          for r in body), True)
never = [r for r in body if "Not measured" in str(r[2].get("text", ""))]
check("a client nobody audited says not measured, never 'nothing found'",
      bool(never), True)
check("and is drawn as muted rather than as a finding",
      all(r[2].get("muted") for r in never), True)

# The finding is the cell; the product is the tooltip. A product name in the
# cell is what a rep gets argued with over.
sellable = [r for r in body
            if r[2].get("text") and not r[2].get("muted")]
if sellable:
    cell = sellable[0][2]
    check("the finding leads the cell", cell["text"][0].isupper(), True)
    check("and the product it points at is on the tooltip",
          cell.get("title", "").startswith("Points at:"), True)

acts = [r[5] for r in body if isinstance(r[5], dict) and r[5].get("actions")]
check("rows carry an action, so the report is a queue and not a list",
      bool(acts), True)
first = acts[0]["actions"][0]
check("which is the rescan", first["action"], "upsell_rescan")
check("carrying the domain, because a scan is keyed on one",
      "." in first["client"], True)
check("and confirming first, because it spends a credit",
      "credit" in first["confirm"], True)


# =====================================================================
section("A run that could not look is not the day's answer")
# =====================================================================

from hub import report_cache                                  # noqa: E402

bad = upsell._unmeasured("Knack refused.")
check("an unmeasured run says so", bad["measured"], False)
check("and report_cache refuses to store it", report_cache.is_answer(bad), False)
check("while a real run is storable", report_cache.is_answer(out), True)
check("an empty finding list is still an answer",
      report_cache.is_answer({"rows": [], "measured": True}), True)

# A client list that cannot be read must not read as a book with nothing in it.
_real = qa._client_groups
qa._client_groups = lambda: (_ for _ in ()).throw(RuntimeError("Knack down"))
try:
    broke = upsell.build()
    check("a client list that will not answer is not measured",
          broke["measured"], False)
    check("and names what failed", "could not be read" in broke["note"], True)
finally:
    qa._client_groups = _real


# =====================================================================
section("It is a QA report, and it does not replace the two beside it")
# =====================================================================

check("the report is registered", "sell-to-clients" in qa.REPORTS, True)
meta = qa.REPORTS["sell-to-clients"]
check("with a title somebody would look for", meta["title"],
      "What We Could Sell Each Client")
check("in the Clients group", meta["group"], "Clients")
check("the two it disagrees with are still there",
      all(k in qa.REPORTS for k in ("no-analytics", "no-gtm")), True)
check("it is cached like every other report, per day and not per open",
      qa.cache_key("sell-to-clients"), ("qa:sell-to-clients", ""))

# The rescan button has to exist on the page that renders these rows.
page = (ROOT / "hub" / "templates" / "qa_report.html").read_text()
check("the report page handles the row action", "upsell_rescan" in page, True)
check("posting to the module that owns scans",
      "'/scans/api/scans'" in page, True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
