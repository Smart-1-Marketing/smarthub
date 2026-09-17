"""hub/scheduler.py — whether the jobs are working, not just whether the loop is.

    python3 test_scheduler_health.py

The scheduler runs its background jobs in one shared thread: the backups, the
Google sweep, the Knack pulls, SmartForecast checks, the domain registry, the
video backlog and the weekly social idea batches. `status()` answered one
question well — is a worker
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


# ---------------------------------------------------------------------------
print("\nThe integrity sweep runs where some of its checks can answer")
# ---------------------------------------------------------------------------
# tools/integritycheck.py exists because the check "lived behind a login on a
# page somebody had to remember to open, which is the same as not having it",
# and the command line answered that -- for the checks that read the SOURCE,
# which CI runs on every pull request. `plaintext_credentials` reads the JSON
# stores on the data disk, and in CI that disk is empty by construction. So
# the one check whose answer exists only in production was the one nothing in
# production ran.
os.environ["HUB_SKIP_SCHEDULER"] = "1"
from hub import (audit as _audit, create_hub_app,                 # noqa: E402
                 seo as _seo)

_app = create_hub_app()
SECRET = "PLAINTEXT-LOGIN-IN-PROD"

check("the sweep is a registered job", "integrity_audit" in sched.JOBS)
check("it runs on the leader like every other job, not a raw timer",
      sched.JOBS["integrity_audit"][1].__name__ == "job_integrity_audit")

# Plant the exact defect #692 fixed, in the shape it was really on the disk.
_seo.save_store("Leaky Client", {
    "client": "Leaky Client",
    "setup": {"login": "rep@leaky.test", "password": SECRET},
    "business_info": {}, "questions": [], "answers": {},
    "pages": {}, "sitemap": []})

_first = sched.job_integrity_audit(_app)
check("a credential left readable on the disk is found", _first["blocking"] >= 1,
      _first)
check("and is reported as newly appeared, so it writes a row",
      _first["appeared"] >= 1, _first)
check("the sweep says how many checks it ran, so a zero is never bare",
      _first["checks"] > 10, _first)
check("and counts the medium/low findings rather than dropping them",
      "reported" in _first, _first)

# A heartbeat fills the activity log until nobody reads it -- which is the
# failure rotate_audit_log beside it exists because of.
_second = sched.job_integrity_audit(_app)
check("an unchanged finding is NOT written again",
      _second["appeared"] == 0, _second)
check("and it is still counted as open, so silence is not 'resolved'",
      _second["blocking"] == _first["blocking"], _second)

# Fixing it is a transition too: a finding that goes away says so.
os.remove(os.path.join(_TMP, "seo", "leaky-client.json"))
_third = sched.job_integrity_audit(_app)
check("a finding that is gone is recorded as cleared", _third["cleared"] >= 1,
      _third)
check("and is no longer counted as open", _third["blocking"] < _first["blocking"],
      _third)
# High severity is the whole selection rule, and it was unasserted until a
# mutation removing the filter stayed green. This repo carries standing
# medium/low findings, so with the leak gone `blocking` must be nought while
# `reported` is not -- which is only true if the filter is doing its job.
check("medium and low are counted, never treated as blocking",
      _third["blocking"] == 0 and _third["reported"] > 0, _third)

with _app.app_context():
    _rows = _audit.read(200, module="integrity")
_types = [r.get("type") for r in _rows]
check("the activity log carries the finding", "finding" in _types, _types)
check("and the clearing", "cleared" in _types, _types)
check("each row names the check, so it can be looked up",
      all(r.get("check") for r in _rows), _rows[:1])
check("and no medium/low check ever wrote one",
      {r.get("check") for r in _rows} == {"plaintext_credentials"},
      {r.get("check") for r in _rows})

# The one that would undo the whole point: the activity log is itself mirrored
# into Postgres, so a credential detector that logged the credential would put
# the password in the backup it was written to keep it out of.
check("NO row carries the password itself",
      not any(SECRET in str(r) for r in _rows))


# ---------------------------------------------------------------------------
# The background lane: a long job runs on its own thread, the loop keeps going
# ---------------------------------------------------------------------------
print("\n-- the background lane")
import threading as _threading                                    # noqa: E402

check("the two long jobs are on the lane",
      sched.BACKGROUND_JOBS == frozenset({"google_index", "reports_native"}), sched.BACKGROUND_JOBS)

_gate = _threading.Event()
_ran = []


def _slow_job(app, **kw):
    _ran.append(kw)
    _gate.wait(10)
    return {"done": True}


_saved_jobs = dict(sched.JOBS)
sched.JOBS["google_index"] = (60, _slow_job, "test stand-in")
try:
    started = sched._start_background("google_index", lambda: sched._run_job(None, "google_index"))  # noqa: SLF001
    check("a background start returns at once", started is True)
    check("...and the job is running", sched.running("google_index"))
    row = next(j for j in sched.status()["jobs"] if j["name"] == "google_index")
    check("status shows it running, since when", row["running"] and bool(row.get("running_since")), row)
    check("...and not overdue while it runs", row["overdue"] is None)
    second = sched._start_background("google_index", lambda: sched._run_job(None, "google_index"))  # noqa: SLF001
    check("a second start while it runs is refused", second is False)
    rn = sched.run_now("google_index", None)
    check("run_now on a running background job does not start another",
          rn["started"] is False and "Already running" in rn["note"], rn)
    _gate.set()
    sched._background["google_index"].join(5)                        # noqa: SLF001
    check("when it finishes the state is the run's", not sched.running("google_index")
          and sched._state["google_index"].get("ok") is True and "running_since" not in sched._state["google_index"])  # noqa: SLF001
    row = next(j for j in sched.status()["jobs"] if j["name"] == "google_index")
    check("...and status no longer says running", row["running"] is False and "running_since" not in row)
    # run_now forces the sweep past its overnight gate and returns immediately.
    _ran.clear()
    _gate.set()
    rn = sched.run_now("google_index", None)
    sched._background["google_index"].join(5)                        # noqa: SLF001
    check("run_now starts a background job and says so", rn["started"] is True and "background" in rn["note"], rn)
    check("...forcing the Google sweep past its overnight gate", _ran == [{"force": True}], _ran)
finally:
    sched.JOBS.clear(); sched.JOBS.update(_saved_jobs)

# The Google sweep's gate: overnight, once, never at midday after a deploy.
print("\n-- the Google sweep runs overnight")
import types as _types                                            # noqa: E402
import sys as _sys                                                # noqa: E402

_fake = _types.ModuleType("hub.google_index")
_fake.age = None
_fake.built = []
_fake.age_seconds = lambda: _fake.age
_fake.build = lambda force=True: (_fake.built.append(force) or {"ok": True, "built": True})
_real_gi = _sys.modules.get("hub.google_index")
_sys.modules["hub.google_index"] = _fake
import hub as _hub                                                # noqa: E402
_real_attr = getattr(_hub, "google_index", None)
_hub.google_index = _fake
_real_hour = sched._eastern_hour                                  # noqa: SLF001
try:
    sched._eastern_hour = lambda: 14                              # noqa: SLF001
    _fake.age = None
    r = sched.job_refresh_google_index(None)
    check("an index that has never been built is built at once, whatever the hour", r.get("built") and _fake.built == [True], r)
    _fake.built.clear(); _fake.age = 3 * 3600
    r = sched.job_refresh_google_index(None)
    check("a three-hour-old index is left alone", "rebuilt 3.0 hours ago" in r.get("skipped", "") and not _fake.built, r)
    _fake.age = 20 * 3600
    r = sched.job_refresh_google_index(None)
    check("a twenty-hour-old index at 2 PM waits for the window",
          "overnight (1-6 AM Eastern)" in r.get("skipped", "") and "20.0 hours old" in r["skipped"] and not _fake.built, r)
    check("...and the skip says the button sweeps now", "Run now" in r["skipped"])
    sched._eastern_hour = lambda: 3                               # noqa: SLF001
    r = sched.job_refresh_google_index(None)
    check("the same index at 3 AM is swept", r.get("built") and _fake.built == [True], r)
    _fake.built.clear(); _fake.age = 2 * 3600
    r = sched.job_refresh_google_index(None)
    check("a deploy at 5 AM after the 3 AM sweep does not sweep again", "rebuilt" in r.get("skipped", "") and not _fake.built, r)
    sched._eastern_hour = lambda: 14                              # noqa: SLF001
    r = sched.job_refresh_google_index(None, force=True)
    check("force (the button) sweeps at 2 PM with a fresh index", r.get("built") and _fake.built == [True], r)
    every, _fn, desc = sched.JOBS["google_index"]
    check("the job is checked hourly and its description says overnight", every == 60 and "overnight" in desc, (every, desc))
finally:
    sched._eastern_hour = _real_hour                              # noqa: SLF001
    if _real_gi is not None:
        _sys.modules["hub.google_index"] = _real_gi
    else:
        _sys.modules.pop("hub.google_index", None)
    if _real_attr is not None:
        _hub.google_index = _real_attr
    elif hasattr(_hub, "google_index"):
        del _hub.google_index

# The Reports refresh: the note on the shared disk, and a refusal while running.
print("\n-- the Reports refresh note")
from hub import jsonstore as _js                                  # noqa: E402
_js.write_json(sched._refresh_note_path(), {}, durable=False)     # noqa: SLF001
check("no refresh yet reads as not running", sched.refresh_note()["running"] is False)
_js.write_json(sched._refresh_note_path(),                        # noqa: SLF001
               {"started_at": sched._now().isoformat(timespec="seconds"), "actor": "Todd",  # noqa: SLF001
                "platforms": ["ttd"], "finished_at": ""}, durable=False)
n = sched.refresh_note()
check("a refresh started just now and not finished is running", n["running"] is True and not n.get("stalled"))
r = sched.refresh_native(["ttd"], actor="Ann")
check("...so another is refused, naming who started it", r["started"] is False and "by Todd" in r["note"], r)
_js.write_json(sched._refresh_note_path(),                        # noqa: SLF001
               {"started_at": (sched._now() - timedelta(minutes=90)).isoformat(timespec="seconds"),  # noqa: SLF001
                "actor": "Todd", "platforms": ["ttd"], "finished_at": ""}, durable=False)
n = sched.refresh_note()
check("one started 90 minutes ago and never finished is stalled, not running",
      n["running"] is False and n.get("stalled") is True)
r = sched.refresh_native(["nope"], actor="Ann")
check("an unknown platform is refused by name", r["started"] is False and "No such platform" in r["note"], r)
_js.write_json(sched._refresh_note_path(), {}, durable=False)     # noqa: SLF001


print("\n" + "-" * 60)
print(f"{PASS} passed, {FAIL} failed")
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
