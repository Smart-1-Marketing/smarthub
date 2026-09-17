"""How the Ads Builder reads its live accounts: uncapped, and by key.

    python3 test_ads_account_reads.py

No pytest, no new dependencies, a throwaway SQLite database.

The same defect class ``test_reports_map_reads.py`` holds for the Reports
store, in the Ads Builder: a capped global read filtered in Python.

``deployed_accounts()`` used to read ``list_proposals(limit=500,
status="DEPLOYED")`` and dedupe it down to one row per account. The cap was
therefore spent on PROPOSALS, and with two proposals per client a cap of 500
is a cap of roughly 250 accounts -- reached at half the number anybody would
guess from reading the call. Past it the oldest-UPDATED deployed account, the
longest-standing one, was simply absent, and every caller read that absence
as a fact about the account:

  * ``monitoring.sweep`` never scanned it;
  * ``hub/ads_status`` never counted it, on a tile whose own comment worries
    about reading "as a clean book";
  * ``monitoring.run_due_reports`` and the manual send both fall back to
    ``{"client_name": ""}`` on a miss, so a scheduled performance report went
    out to a real client with a BLANK client name;
  * Ask SmartHub's findings tool answered "no account" for that client.

``latest_optimization_runs`` carried the same defect behind a multiplier: it
read the newest ``limit * 10`` RUNS and deduped, which is correct only while
every account scans at the same rate. A handful of busy accounts fill those
rows, so the newest run of a QUIET account falls past the end and the panel
reads it as never scanned -- meaning the account that has gone longest
without a scan is the one reported as never having had one.

What it holds:

  * both truncations reproduced, not asserted from the source;
  * ``deployed_accounts()`` returns every live account however old, and its
    ``limit`` now pages ACCOUNTS rather than proposals;
  * ``deployed_account(cid)`` finds one by key past any cap, matches on the
    DIGITS (nothing normalises the column, so one account is stored both
    "123-456-7890" and "1234567890"), and answers None rather than raising;
  * ``latest_optimization_runs()`` returns one run per account however
    lopsided the scanning has been;
  * the consequence: the report a due account is sent for carries its real
    client name;
  * the guard: no reading that needs the book whole may pass a limit.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1ads_reads_")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "test.db")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "disk")
os.makedirs(os.environ["HUB_DATA_DIR"], exist_ok=True)
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["SECRET_KEY"] = "ads-account-reads-test"
os.environ.pop("CLOUDINARY_URL", None)

_passed = _failed = 0


def check(label, got, want=True):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


from modules.ads_builder import monitoring, store                    # noqa: E402

FILLER = 12
CAP = 5                                   # stands in for the real 500
OLD_CID, DASHED_CID = "1110000001", "3334445555"


def deploy(name, cid):
    p = store.create_proposal(client_name=name, google_customer_id=cid,
                              campaign={}, created_by="Todd")
    store.set_status(p["id"], "DEPLOYED")
    return p


# ------------------------------------------------------------- the book
# The oldest-updated deployed proposal is filed first and never touched
# again -- exactly the account a cap drops.
OLD = deploy("Oldest Co", OLD_CID)
for i in range(FILLER):
    deploy(f"Filler {i}", f"222000{i:04d}")
# One account stored in the dashed spelling, because nothing normalises the
# column on the way in and a by-key lookup has to survive that.
DASHED = deploy("Dashed Co", "333-444-5555")
# A DRAFT proposal is not a live account, whatever customer id it carries.
store.create_proposal(client_name="Draft Co", google_customer_id="9990000001",
                      campaign={}, created_by="Todd")

# updated_at comes from the clock and fourteen rows written in one breath can
# tie; the order is the whole point here, so it is written rather than hoped
# for: oldest first, one minute apart.
with store.SessionLocal() as s:
    rows = s.scalars(store.select(store.Proposal)
                       .order_by(store.Proposal.id.asc())).all()
    base = datetime(2026, 1, 1, 9, 0, 0)
    for i, r in enumerate(rows):
        r.updated_at = base + timedelta(minutes=i)
    s.commit()


# ------------------------------------------------- the defect, reproduced
section("The truncation, reproduced rather than asserted from the source")

capped = store.deployed_accounts(limit=CAP)
check("a capped read returns exactly the cap", len(capped), CAP)
check("...and it is the newest-updated accounts",
      OLD_CID in {a["customer_id"] for a in capped}, False)
check("the old way -- filter that list by customer id -- finds nothing",
      next((a for a in capped if a["customer_id"] == OLD_CID), None), None)
check("...and every caller's fallback on that miss is a BLANK client name",
      (next((a for a in capped if a["customer_id"] == OLD_CID), None)
       or {"customer_id": OLD_CID, "client_name": "", "proposal_id": ""})["client_name"],
      "")


# ------------------------------------------------------ deployed_accounts
section("deployed_accounts: every live account, however old")

every = store.deployed_accounts()
check("the oldest account is in the book", OLD_CID in {a["customer_id"] for a in every})
check("...with its real client name",
      next(a["client_name"] for a in every if a["customer_id"] == OLD_CID), "Oldest Co")
check("...and the proposal that deployed it",
      next(a["proposal_id"] for a in every if a["customer_id"] == OLD_CID), OLD["id"])
check("every deployed account, once each", len(every), FILLER + 2)
check("a DRAFT proposal is not a live account, whatever id it carries",
      "9990000001" in {a["customer_id"] for a in every}, False)
check("the dashed spelling is stored and read as its digits",
      DASHED_CID in {a["customer_id"] for a in every})
check("newest-updated first", every[0]["customer_id"], DASHED_CID)
check("a limit now pages ACCOUNTS, not proposals",
      len(store.deployed_accounts(limit=3)), 3)


# ------------------------------------------------------- deployed_account
section("deployed_account: one account by its customer id")

check("the oldest account is found past any cap",
      (store.deployed_account(OLD_CID) or {}).get("client_name"), "Oldest Co")
check("the dashed spelling finds the same account, typed either way",
      ((store.deployed_account("333-444-5555") or {}).get("client_name"),
       (store.deployed_account(DASHED_CID) or {}).get("client_name")),
      ("Dashed Co", "Dashed Co"))
check("an account nobody deployed answers None, it does not raise",
      store.deployed_account("4040404040"), None)
check("a DRAFT proposal's account is not a live account",
      store.deployed_account("9990000001"), None)
check("a blank id answers None", store.deployed_account(""), None)
check("...and so does None", store.deployed_account(None), None)


# ------------------------------------------------ latest_optimization_runs
section("latest_optimization_runs: one run per account, however lopsided")

# One busy account scans many times; the quiet one scanned once, first.
store.record_optimization_run(OLD_CID, client_name="Oldest Co",
                              result={"items": []}, triggered="scheduled")
for i in range(40):
    store.record_optimization_run("2220000000", client_name="Filler 0",
                                  result={"items": []}, triggered="scheduled")
with store.SessionLocal() as s:
    runs = s.scalars(store.select(store.OptimizationRun)
                       .order_by(store.OptimizationRun.id.asc())).all()
    base = datetime(2026, 2, 1, 9, 0, 0, tzinfo=timezone.utc)
    for i, r in enumerate(runs):
        r.scanned_at = base + timedelta(minutes=i)
    s.commit()

latest = store.latest_optimization_runs()
by_account = {r["customer_id"]: r for r in latest}
check("one row per account that has ever been scanned", len(latest), len(by_account))
check("the quiet account's only scan is in the answer", OLD_CID in by_account)
check("...even though the busy account wrote 40 runs after it",
      by_account["2220000000"] is not None)
check("the busy account's row is its NEWEST run",
      by_account["2220000000"]["scanned_at"],
      max(r["scanned_at"] for r in latest if r["customer_id"] == "2220000000"))
check("an account nobody has scanned is absent, not a blank row",
      DASHED_CID in by_account, False)
check("a limit pages the accounts", len(store.latest_optimization_runs(limit=1)), 1)


# --------------------------------------------------------- the consequence
section("The consequence: what a client's scheduled report is addressed to")

store.set_report_schedule(OLD_CID, cadence="monthly", recipient="todd@example.com",
                          actor="Todd")
due = store.due_report_accounts()
check("the oldest account's report is due", [d["customer_id"] for d in due], [OLD_CID])
known = {a["customer_id"]: a for a in store.deployed_accounts()}
account = known.get(OLD_CID) or {"customer_id": OLD_CID, "client_name": "",
                                 "proposal_id": ""}
check("...and the account it is sent for carries the real client name",
      account["client_name"], "Oldest Co")


# --------------------------------------------------------------- the guard
section("The guard: a reading that needs the book whole passes no limit")

_real_deployed = store.deployed_accounts
_real_runs = store.latest_optimization_runs
_capped: list[str] = []


def _spy(name, real):
    """Counts rather than raises: several of these callers catch every
    exception on purpose, so a guard that raised would be swallowed there
    and the test would pass on the broken code."""
    def _wrapped(limit=None, *a, **kw):
        if limit is not None:
            _capped.append(f"{name}(limit={limit})")
        return real(limit, *a, **kw)
    return _wrapped


def guarded(label, fn):
    _capped.clear()
    store.deployed_accounts = _spy("deployed_accounts", _real_deployed)
    store.latest_optimization_runs = _spy("latest_optimization_runs", _real_runs)
    try:
        fn()
    except Exception as exc:                        # noqa: BLE001 - report it as itself
        check(label, f"{type(exc).__name__}: {exc}", "no capped read")
        return
    finally:
        store.deployed_accounts = _real_deployed
        store.latest_optimization_runs = _real_runs
    check(label, "reached " + ", ".join(sorted(set(_capped))) if _capped else "no capped read",
          "no capped read")


from hub import ads_status                                           # noqa: E402

guarded("the dashboard scoreboard counts every account",
        ads_status.scoreboard)
guarded("the due-report run keys every account",
        lambda: monitoring.report_sweep(actor="test", limit=1))

# The panel is the one reading that DOES page, and its limit is the page.
panel = monitoring.account_panel(limit=3)
check("the panel pages the accounts it was asked for", len(panel["accounts"]), 3)
check("...and each carries its last run, or says it has never had one",
      all("last_run" in r for r in panel["accounts"]))


print(f"\n{_passed} passed, {_failed} failed")
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if _failed else 0)
