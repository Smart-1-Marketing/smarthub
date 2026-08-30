"""Which prospect to call today, and why.

    python3 test_prospect_queue.py

Same shape as the other test files here — no pytest, no new dependencies, a
temporary data directory and a throwaway SQLite database.

## Why this file exists

`hub/prospect.py` built a record worth opening and nothing said which one to
open: the Leads panel is sorted by date and its five figures are all about
delivery, and every other QA report is about clients. Every failure below is
one where the queue would look healthy while sending somebody at the wrong
prospect — or at none.

  1. **The bands are the work, in the order it has to happen.** A prospect who
     never reached the CRM is invisible to every follow-up that lives there,
     so nothing else about them matters yet. A business in the panel twice
     wastes the call whichever row is worked. Only then is "audited and
     nothing quoted" the top of the list.

  2. **A converted prospect is a client.** They belong on Client 360, not in a
     chase queue — but they are *counted*, because a queue that silently drops
     rows cannot be told from one that failed to read them.

  3. **Suite is deliberately not read.** One HTTP call per prospect on the
     first open of the day is a report somebody turns off. The note says so,
     rather than leaving a rep to wonder where the pipeline stage is.

  4. **A source that fails is named and does not empty the queue.** A
     proposal store that will not answer must not move every quoted prospect
     into "nothing quoted" in silence.

  5. **A run that could not look is never the day's answer.**
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

TMP = tempfile.mkdtemp(prefix="s1queue_test_")
DISK = os.path.join(TMP, "disk")
os.makedirs(DISK, exist_ok=True)

os.environ["HUB_DATA_DIR"] = DISK
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "t.db")
os.environ["SECRET_KEY"] = "queue-test-secret"
os.environ["PANEL_PASSWORD"] = "queue-test-password"
os.environ["HUB_LEADS_FILE"] = os.path.join(DISK, "leads.jsonl")

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


from hub import leads, prospect_queue                         # noqa: E402
from modules.scans import app as scans_app                    # noqa: E402

NOW = datetime.now(timezone.utc)
GAPS = {"retargeting": {"has_facebook_pixel": False, "has_google_pixel": False},
        "analytics": {"has_analytics": False},
        "google_business_profile": {"is_listing_found": True,
                                    "is_listing_claimed": False,
                                    "review_count": 4}}


def audit(domain, days=3):
    db = scans_app.SessionLocal()
    try:
        db.add(scans_app.Scan(
            public_id=f"q-{domain}", domain_key=domain, input_url=domain,
            overall_score=51, status="complete", raw_report=json.dumps(GAPS),
            created_at=NOW - timedelta(days=days),
            completed_at=NOW - timedelta(days=days)))
        db.commit()
    finally:
        db.close()


def lead(company, *, email="", website="", delivered=True, days=2,
         source="website_audit", page="tool"):
    row = leads.capture(source, page,
                        {"company": company, "email": email or
                         f"{company.split()[0].lower()}@example.com",
                         "phone": "3175550100", "website": website})
    stored = next(r for r in leads._read_all() if r["id"] == row["id"])
    stored["created"] = (NOW - timedelta(days=days)).isoformat(timespec="seconds")
    if delivered:
        stored["contact_id"] = "c-" + row["id"][:6]
        stored["delivered"] = True
    leads._update(stored)
    return row["id"]


audit("audited.com")
ready_id = lead("Audited Co", website="audited.com", days=9)
fresh_id = lead("Fresh Co", website="fresh.com", days=4)
lost_id = lead("Lost Co", delivered=False, days=20)
twin_a = lead("Twin Co", email="twin@twin.com", days=6)
twin_b = lead("Twin Co", email="twin@twin.com", days=1, source="scan_widget",
              page="home")


def bands(out):
    return [r[0]["text"] for r in out["rows"]
            if isinstance(r[0], dict) and r[0].get("group")]


def body(out):
    return [r for r in out["rows"]
            if not (isinstance(r[0], dict) and r[0].get("group"))]


def band_of(out, lead_id):
    """Which band a prospect landed in — the assertion that actually matters."""
    current = ""
    for r in out["rows"]:
        if isinstance(r[0], dict) and r[0].get("group"):
            current = r[0]["text"]
        elif r[0].get("href", "").endswith(lead_id):
            return current
    return ""


out = prospect_queue.build()


# =====================================================================
section("The bands are the work, in the order it has to happen")
# =====================================================================

check("the queue measures", out["measured"], True)
check("it answers in the QA report shape", out["columns"][0], "Prospect")
check("one style per row", len(out["row_styles"]), len(out["rows"]))

check("a prospect who never reached the CRM comes first",
      band_of(out, lost_id).startswith("Not in Smart 1 Suite"), True)
check("because nothing else about them reaches anybody",
      "nothing else reaches them" in bands(out)[0], True)
check("the same business twice is next, before either is worked",
      band_of(out, twin_a).startswith("Two rows, one business"), True)
check("both halves of the pair, not just one",
      band_of(out, twin_b).startswith("Two rows, one business"), True)
check("audited and unquoted is the band to work",
      band_of(out, ready_id).startswith("Audited and nothing quoted"), True)
check("never audited sits below it",
      band_of(out, fresh_id).startswith("Never audited"), True)
check("and says a credit is what moves them up",
      "one credit turns these" in band_of(out, fresh_id), True)

order = bands(out)
check("the bands come out in that order",
      [b.split(" (")[0].split(" —")[0] for b in order],
      ["Not in Smart 1 Suite", "Two rows, one business",
       "Audited and nothing quoted", "Never audited"])


# =====================================================================
section("Every row says what we know and what to do about it")
# =====================================================================

rows = {r[0]["href"].rsplit("/", 1)[-1]: r for r in body(out)}
check("every row opens the prospect record",
      all(r[0]["href"].startswith("/prospect/") for r in body(out)), True)

ready = rows[ready_id]
check("an audited prospect leads with the count of findings",
      ready[2]["text"].startswith("4 findings:"), True)
check("and the first finding itself, not a product name",
      "retargeting pixel" in ready[2]["text"], True)
check("the next step is to quote it", "Quote it" in ready[3]["text"], True)
check("it carries no audit button — it has one",
      ready[4]["actions"], [])

fresh = rows[fresh_id]
check("a never-audited prospect says so", fresh[2]["text"], "Never audited")
check("its next step is the audit", fresh[3]["text"], "Audit the website")
check("and it carries the button", fresh[4]["actions"][0]["action"],
      "upsell_rescan")
check("carrying the domain, because a scan is keyed on one",
      fresh[4]["actions"][0]["client"], "fresh.com")
check("and confirming, because it spends a credit",
      "credit" in fresh[4]["actions"][0]["confirm"], True)

lost = rows[lost_id]
check("a prospect in no CRM is told to retry the delivery",
      "Retry the delivery" in lost[3]["text"], True)
check("one with no website is told to find it first",
      rows[twin_a][3]["text"], "Find their website first")
check("the age is on every row", ready[1]["text"], "9d ago")


# =====================================================================
section("A converted prospect is a client, and is counted rather than dropped")
# =====================================================================

leads.mark_converted(ready_id, "Audited Co", actor="tester")
after = prospect_queue.build()
check("they leave the queue", band_of(after, ready_id), "")
check("and the note says how many did",
      "converted and" in after["note"], True)
check("the band they were in is gone with them",
      any(b.startswith("Audited and nothing quoted") for b in bands(after)),
      False)


# =====================================================================
section("A quoted prospect waits, and a store that will not answer says so")
# =====================================================================

import hub.proposals as proposals_mod                          # noqa: E402

_real_all = proposals_mod.all_proposals
proposals_mod.all_proposals = lambda limit=200, q="": [{"client": "Fresh Co"}]
try:
    quoted = prospect_queue.build()
    check("a prospect with a proposal on file moves to waiting",
          band_of(quoted, fresh_id).startswith("Quoted and waiting"), True)
    check("and is told to chase it",
          "Chase the proposal" in
          [r[3].get("text") for r in body(quoted)], True)
finally:
    proposals_mod.all_proposals = _real_all

proposals_mod.all_proposals = lambda limit=200, q="": (_ for _ in ()).throw(
    RuntimeError("store unreadable"))
try:
    broken = prospect_queue.build()
    check("a proposal store that fails does not empty the queue",
          len(body(broken)) > 0, True)
    check("and is named rather than moving everybody to 'nothing quoted'",
          "Not measured:" in broken["note"], True)
    check("with what actually failed in it",
          "store unreadable" in broken["note"], True)
finally:
    proposals_mod.all_proposals = _real_all


# =====================================================================
section("What it deliberately does not read, and what it cannot")
# =====================================================================

check("the note says the Suite stage is not on this page",
      "not on this page" in out["note"], True)
check("and why — a call per prospect",
      "per prospect" in out["note"], True)
check("and where it is read instead",
      "the record reads it" in out["note"], True)

src = (ROOT / "hub" / "prospect_queue.py").read_text()
check("the queue makes no Suite call at all",
      "suite_opportunity" in src, False)
check("and resolves a lead's domain through the one place that decides it",
      "prospect._lead_domain" in src, True)

_real = leads.listing
leads.listing = lambda **kw: (_ for _ in ()).throw(RuntimeError("no store"))
try:
    dead = prospect_queue.build()
    check("a lead store that will not answer is not measured",
          dead["measured"], False)
    check("and says so rather than reporting an empty pipeline",
          "not measured" in dead["note"], True)
    from hub import report_cache                               # noqa: E402
    check("so report_cache refuses to make it the day's answer",
          report_cache.is_answer(dead), False)
finally:
    leads.listing = _real


# =====================================================================
section("It is on the QA page, in a group of its own")
# =====================================================================

from hub import qa                                            # noqa: E402

check("the report is registered", "prospect-queue" in qa.REPORTS, True)
check("under Sales", qa.REPORTS["prospect-queue"]["group"], "Sales")
check("titled as the question it answers",
      qa.REPORTS["prospect-queue"]["title"], "Prospects To Chase")
check("and it is the first report, so Sales is the first group",
      list(qa.REPORTS)[0], "prospect-queue")
check("cached per day like every other report",
      qa.cache_key("prospect-queue"), ("qa:prospect-queue", ""))

page = (ROOT / "hub" / "templates" / "qa.html").read_text()
check("the group has a blurb, like the others", '"Sales":' in page, True)


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
