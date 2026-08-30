"""hub/scheduler.py — whether the jobs are working, not just whether the loop is.

    python3 test_scheduler_health.py

The scheduler now runs eleven jobs sharing one thread: the backups, the Google
sweep, the Knack pulls, the domain registry, the video backlog and the weekly
social idea batches. `status()` answered one question well — is a worker
holding the lock — and three others not at all.

## The three it could not answer

**Is a job overdue?** The panel drew a green pill for any job whose last run
succeeded, however long ago. A job on an hourly interval that last ran three
days ago read as healthy, and a loop stuck on one long job stops every job
behind it with nothing on the page saying so.

**Has it been failing, or did it just blip?** `_state[name]` was overwritten
each run, so fourteen consecutive failures and one failure a minute ago
rendered identically.

**Can this worker even see?** `_state` is per-process and there are two
gunicorn workers, so the standby holds nothing. Every job there read "Not run
yet this boot" behind a grey pill — indistinguishable from a scheduler that
has never run. The same page said two different things depending on which
worker answered, and one of them was alarming and wrong.

Absent is not zero: on standby the timings are *not measured here*, which is
its own answer and not a row of nevers.
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-sched-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["SECRET_KEY"] = "sched-test"
os.environ["PANEL_PASSWORD"] = "test"
os.environ["HUB_DATA_DIR"] = _TMP

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok    " + label)
    else:
        FAIL += 1
        print("  FAIL  " + label + (("  — " + str(detail)) if detail else ""))


from hub import scheduler as sched                                # noqa: E402


def ago(minutes):
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def job(**kw):
    row = {"last_run": None, "ok": None, "fails": 0, "last_ok": None}
    row.update(kw)
    return row


# ---------------------------------------------------------------------------
print("\nOverdue is measured, and only where it can be")
# ---------------------------------------------------------------------------
late, by = sched._overdue(job(last_run=ago(600), ok=True), 60, True)   # noqa: SLF001
check("a job an hour apart that last ran 10 hours ago is overdue", late is True)
check("and it says how late, so the row can print it", by and by > 500, by)

fine, _ = sched._overdue(job(last_run=ago(70), ok=True), 60, True)     # noqa: SLF001
check("one that has just slipped past its interval is not overdue yet",
      fine is False, "jobs share a thread; a little late is normal")
check("but well past twice it is",
      sched._overdue(job(last_run=ago(200), ok=True), 60, True)[0] is True)  # noqa: SLF001

# A job that ticks every minute must not be called overdue at 61 seconds.
check("a fast job gets a floor, so it does not cry wolf",
      sched._overdue(job(last_run=ago(3), ok=True), 1, True)[0] is False)    # noqa: SLF001
check("and is still caught when genuinely stopped",
      sched._overdue(job(last_run=ago(90), ok=True), 1, True)[0] is True)    # noqa: SLF001

# The two unknowables. A confident "not overdue" about a job this process
# cannot see is exactly the wrong answer.
check("a job never run this boot is not called overdue",
      sched._overdue(job(), 60, True)[0] is None)                     # noqa: SLF001
check("nor is anything on a worker that cannot see the timings",
      sched._overdue(job(last_run=ago(9999), ok=True), 60, False)[0] is None)  # noqa: SLF001
check("a corrupt timestamp answers not-known rather than raising",
      sched._overdue(job(last_run="not a date", ok=True), 60, True)[0] is None)  # noqa: SLF001


# ---------------------------------------------------------------------------
print("\nA failure streak is not a failure")
# ---------------------------------------------------------------------------
name = next(iter(sched.JOBS))


def run_with(result_ok):
    def fn(_app):
        if not result_ok:
            raise RuntimeError("provider is down")
        return {"did": "something"}
    every, _old, desc = sched.JOBS[name]
    sched.JOBS[name] = (every, fn, desc)
    sched._run_job(None, name)                                        # noqa: SLF001
    return sched._state[name]                                         # noqa: SLF001


_original = sched.JOBS[name]
row = run_with(True)
check("a good run records success", row["ok"] is True and row["fails"] == 0)
check("and stamps when it last worked", bool(row["last_ok"]))
first_ok = row["last_ok"]

row = run_with(False)
check("one failure counts one", row["fails"] == 1 and row["ok"] is False)
check("and the last good run is kept, so the row can say how long it has been broken",
      row["last_ok"] == first_ok, row)

row = run_with(False)
row = run_with(False)
check("three failures in a row count three", row["fails"] == 3, row)
check("which is what makes them distinguishable from one blip",
      row["fails"] > 1)

row = run_with(True)
check("a success clears the streak", row["fails"] == 0)
# Not "the stamp changed": _now() has second precision and these runs land
# inside one second, so comparing to the earlier value tests the clock rather
# than the code. What matters is that a successful run stamps last_ok as now,
# which is exactly last_run.
check("and re-stamps the last-good time to this run",
      row["last_ok"] == row["last_run"], row)
sched.JOBS[name] = _original


# ---------------------------------------------------------------------------
print("\nstatus() answers whether the jobs are working")
# ---------------------------------------------------------------------------
st = sched.status()
check("every job is described", len(st["jobs"]) == len(sched.JOBS))
check("the counts are computed server-side, not left to the page",
      "failing" in st and "overdue" in st, sorted(st))
check("and whether this worker can see timings at all is on the answer",
      "timings_visible" in st)
for row in st["jobs"]:
    ok = set(row) >= {"name", "every_minutes", "visible", "overdue", "fails"}
    if not ok:
        check("every job row carries the new fields", False, row)
        break
else:
    check("every job row carries the new fields", True)

# Standby: the timings genuinely are not here, and that is not "never ran".
saved_leader, saved_thread = sched._is_leader, sched._thread          # noqa: SLF001
sched._is_leader = False                                              # noqa: SLF001
standby = sched.status()
check("a worker that is not leading reports its timings as not visible",
      standby["timings_visible"] is False, standby["state"])
check("and calls no job overdue on the strength of what it cannot see",
      all(j["overdue"] is None for j in standby["jobs"]))
sched._is_leader = saved_leader                                       # noqa: SLF001


# ---------------------------------------------------------------------------
print("\nThe panel renders what status() reports")
# ---------------------------------------------------------------------------
tpl = os.path.join(ROOT, "hub", "templates", "diagnostics.html")
with open(tpl, encoding="utf-8") as fh:
    page = fh.read()
check("an overdue job is drawn as a fault, not a green pill",
      "j.overdue) ? \"error\"" in page.replace("'", '"'), "see renderScheduler")
check("the standby worker is told why it has no timings",
      "the other one holds the lock" in page)
check("a repeated failure says how many runs in a row",
      "runs in a row" in page)
check("and when it last worked", "last worked" in page)
check("the headline counts come from the server, not a second count here",
      "d.failing" in page and "d.overdue" in page)


# ---------------------------------------------------------------------------
print("\nNothing here raises")
# ---------------------------------------------------------------------------
for bad in ({}, {"last_run": None}, {"last_run": ""}, {"last_run": 12345},
            {"last_run": "2026-13-45T99:99:99"}):
    try:
        out = sched._overdue(bad, 60, True)                           # noqa: SLF001
        ok = isinstance(out, tuple) and len(out) == 2
    except Exception as exc:                                          # noqa: BLE001
        ok, out = False, f"{type(exc).__name__}: {exc}"
    check(f"  _overdue({str(bad)[:26]}) answers rather than raising", ok, out)

try:
    sched.status()
    sched.status(None)
    ok = True
except Exception as exc:                                              # noqa: BLE001
    ok = exc
check("status() survives being called with and without an app", ok is True, ok)


print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
