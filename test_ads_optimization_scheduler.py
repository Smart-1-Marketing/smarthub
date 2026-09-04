"""The scheduled Google Ads optimization sweep.

`optimization.scan_account()` could always answer "what is wrong with this
account". What it could not do is run without somebody opening the page and
pressing Scan, so this asserts the half that makes it unattended:

* every live account gets a row, and a live account is a DEPLOYED proposal
  carrying a customer id — never a draft, and never one account twice;
* one account's Google failure does not cost the rest of the book its scan,
  and the failure is a **row** rather than a silence;
* the run paces itself against the day's Google Ads operation budget and
  **skips** the accounts that do not fit rather than spending the last of it;
* a quota nobody published is *not measured*, and must not read as a quota of
  zero that stops the sweep entirely;
* every scan lands on the client's own record, with the client named.

    python3 test_ads_optimization_scheduler.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

# Own directory AND own database. Setting only the first is the trap
# CLAUDE.md names: jsonstore keys its mirror relative to the data root, so a
# fresh disk in front of an inherited DATABASE_URL is refilled with the last
# run's rows.
TMP = tempfile.mkdtemp(prefix="s1ads_sched_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "test.db")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ.pop("CLOUDINARY_URL", None)

from hub import quotas                                    # noqa: E402
from modules.ads_builder import monitoring, store         # noqa: E402
from modules.ads_builder.google_ads import GoogleAdsError  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, extra=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" — {extra}" if extra and not ok else ""))


def scan_result(items):
    return {"item_count": len(items), "items": items, "date_range": "LAST_30_DAYS"}


HIGH = {"id": "x", "severity": "high", "category": "search_terms", "title": "t"}
LOW = {"id": "y", "severity": "low", "category": "keywords", "title": "t"}


def seed():
    """Two live accounts, one of them named by two proposals, plus a draft."""
    a = store.create_proposal("Northside Roofing", {"adGroups": []},
                              google_customer_id="111-111-1111")
    store.mark_deployed(a["id"], {"ok": True})
    b = store.create_proposal("Riverside HVAC", {"adGroups": []},
                              google_customer_id="222-222-2222")
    store.mark_deployed(b["id"], {"ok": True})
    # Same account, second proposal: one account to scan, not two.
    c = store.create_proposal("Northside Roofing Co", {"adGroups": []},
                              google_customer_id="1111111111")
    store.mark_deployed(c["id"], {"ok": True})
    # A draft is not live and is never swept.
    store.create_proposal("Not Deployed Ltd", {"adGroups": []},
                          google_customer_id="333-333-3333")


class Recorder:
    """Stands in for optimization.scan_account, and remembers who it was asked
    about — a sweep that skips an account must be provably not calling it."""

    def __init__(self, raise_for=()):
        self.seen, self.raise_for = [], set(raise_for)

    def __call__(self, cid, date_range, store_arg=None):
        self.seen.append(cid)
        if cid in self.raise_for:
            raise GoogleAdsError("Google refused this account.", code="PERMISSION_DENIED")
        return scan_result([HIGH, LOW])


def run():
    print("\nScheduled Google Ads optimization sweep\n" + "=" * 60)
    seed()

    real_scan = monitoring.optimization.scan_account
    real_headroom = monitoring._headroom

    # ---------------------------------------------------- the ordinary run
    print("\nA sweep of every live account")
    rec = Recorder()
    monitoring.optimization.scan_account = rec
    monitoring._headroom = lambda: (13500, {"measured": True, "remaining": 13500})
    result = monitoring.sweep(actor="test")

    check("only DEPLOYED proposals are swept",
          "3333333333" not in rec.seen, rec.seen)
    check("one account is scanned once however many proposals name it",
          sorted(rec.seen) == ["1111111111", "2222222222"], rec.seen)
    check("every scanned account gets a stored run",
          result["scanned"] == 2 and result["accounts"] == 2, result)
    runs = store.latest_optimization_runs()
    check("the run is readable before anybody presses Scan", len(runs) == 2, runs)
    check("and it counts the high-severity findings",
          all(r["high_severity_count"] == 1 and r["item_count"] == 2 for r in runs), runs)
    check("the run reports the total needing attention",
          result["high_severity"] == 2, result)
    check("the stored row carries the client name, not only the account id",
          {r["client_name"] for r in runs} == {"Northside Roofing Co", "Riverside HVAC"},
          [r["client_name"] for r in runs])

    # The client's own 360 record, via the module's activity mirror.
    from hub import audit
    rows = [r for r in audit.read(limit=200, module="ads_builder")
            if r.get("type") == "optimization_scheduled_scan"]
    check("each scan is logged against its client",
          len(rows) == 2 and all(r.get("client") for r in rows),
          [r.get("client") for r in rows])

    # ------------------------------------------------- one account refusing
    print("\nOne account's failure is a row, not a lost sweep")
    rec = Recorder(raise_for={"1111111111"})
    monitoring.optimization.scan_account = rec
    result = monitoring.sweep(actor="test")
    check("the other account is still scanned",
          "2222222222" in rec.seen and result["scanned"] == 1, result)
    check("and the failure is counted rather than swallowed",
          result["failed"] == 1, result)
    failed = store.latest_optimization_run("1111111111")
    check("the failed account still has a row",
          failed is not None and bool(failed["error"]), failed)
    check("and that row says it was not measured",
          failed["measured"] is False, failed)
    check("while the account that answered is measured",
          store.latest_optimization_run("2222222222")["measured"] is True)

    # ------------------------------------------------------- the day's budget
    print("\nThe daily operation budget stops the run rather than being spent")
    rec = Recorder()
    monitoring.optimization.scan_account = rec
    monitoring._headroom = lambda: (0, {"measured": True, "remaining": 0})
    result = monitoring.sweep(actor="test")
    check("no account is scanned with no headroom left",
          rec.seen == [], rec.seen)
    check("and the skip is reported rather than read as a clean sweep",
          result["quota_skipped"] == 2 and result["scanned"] == 0, result)

    # Enough for exactly one account: the second is skipped, not failed.
    rec = Recorder()
    monitoring.optimization.scan_account = rec
    monitoring._headroom = lambda: (quotas.ADS_QUERIES_PER_SCAN,
                                    {"measured": True, "remaining": 6})
    result = monitoring.sweep(actor="test")
    check("an allowance for one account scans one and skips the rest",
          len(rec.seen) == 1 and result["quota_skipped"] == 1 and result["failed"] == 0,
          result)

    # A refused scan still spends the operations it issued.
    rec = Recorder(raise_for={"1111111111", "2222222222"})
    monitoring.optimization.scan_account = rec
    monitoring._headroom = lambda: (quotas.ADS_QUERIES_PER_SCAN,
                                    {"measured": True, "remaining": 6})
    result = monitoring.sweep(actor="test")
    check("a failed scan spends its share of the budget too",
          len(rec.seen) == 1 and result["quota_skipped"] == 1, result)

    # ---------------------------------------------- not measured is not zero
    print("\nA quota nobody published is not a quota of zero")
    rec = Recorder()
    monitoring.optimization.scan_account = rec
    monitoring._headroom = lambda: (None, {"measured": False, "note": "no ceiling"})
    result = monitoring.sweep(actor="test")
    check("an unmeasurable ceiling does not stop the sweep",
          len(rec.seen) == 2 and result["quota_skipped"] == 0, result)
    check("and the run says the budget was not measured",
          result["quota"]["measured"] is False, result["quota"])

    monitoring.optimization.scan_account = real_scan
    monitoring._headroom = real_headroom

    # ------------------------------------------------------ the helper itself
    print("\nquotas.ads_headroom()")
    head = quotas.ads_headroom(rows=[])
    check("it leaves the last tenth for a rep's own deploy",
          head["measured"] and head["safety_limit"] == int(head["daily_quota"] * 0.9),
          head)
    check("the margin is a named constant rather than a literal",
          abs(quotas.ADS_QUOTA_SAFETY - 0.90) < 1e-9, quotas.ADS_QUOTA_SAFETY)
    os.environ["GOOGLE_ADS_DAILY_QUOTA"] = "0"
    try:
        head = quotas.ads_headroom(rows=[])
        check("with no published ceiling it is not measured, not exhausted",
              head["measured"] is False and head["exhausted"] is False, head)
        check("and it names the variable that would set one",
              "GOOGLE_ADS_DAILY_QUOTA" in head["note"], head["note"])
    finally:
        os.environ.pop("GOOGLE_ADS_DAILY_QUOTA", None)

    # -------------------------------------------------------------- the panel
    print("\nThe panel the page opens on")
    panel = monitoring.account_panel()
    check("it lists every live account", len(panel["accounts"]) == 2, panel["accounts"])
    check("each carrying its last automatic scan",
          all(a["last_run"] for a in panel["accounts"]), panel["accounts"])

    # --------------------------------------------------- nothing to sweep yet
    print("\nNothing deployed is a state, not a failure")
    real_accounts = store.deployed_accounts
    store.deployed_accounts = lambda *a, **k: []
    try:
        result = monitoring.sweep(actor="test")
        check("an empty book is reported as a skip",
              result.get("skipped") and result["accounts"] == 0, result)
    finally:
        store.deployed_accounts = real_accounts

    # ------------------------------------------------------ the job is wired
    print("\nThe scheduler runs it")
    from hub import scheduler
    check("a job is registered", "ads_optimization" in scheduler.JOBS)
    every, fn, desc = scheduler.JOBS["ads_optimization"]
    check("twice a day, not hourly", every == 720, every)
    check("and it describes itself", bool(desc.strip()), desc)
    check("it is the sweep, not a second reading of it",
          fn.__name__ == "job_ads_optimization_scan", fn.__name__)

    print("\n" + "=" * 60)
    print(f"{len(PASS)} passed, {len(FAIL)} failed")
    for name in FAIL:
        print("  FAIL " + name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(run())
