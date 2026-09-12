"""The inactive-accounts QA scan runs in the background and says so.

    python3 test_google_inactive_qa_progress.py

Same shape as the other test files here: no pytest, no new dependencies, a
throwaway data directory, so it never touches /var/data or the real one.
Nothing reaches Google -- `_scan_login` and the Google Finder lookups it
calls are stubbed.

## What this is asserting

The scan across every connected login's GA4 properties and GTM containers
is minutes, not seconds, and slower still on a day this account's Tag
Manager quota is already stretched -- the scan paces itself now rather than
burning through it faster and wrongly. Running it inline on the request
thread left the page with nothing to show but a static "Scanning..." for
the entire wait, indistinguishable from a hung request. It runs on a
background thread now, with progress written to a small file on the shared
data disk rather than a module-level dict, because this Hub runs two
gunicorn workers and a browser's poll can land on either one regardless of
which worker started the scan.

**A `running: true` flag with no recent heartbeat is a dead worker, not a
live scan.** Otherwise a crash mid-scan would mean nobody could ever start
another one without restarting the whole Hub.

**Starting twice does not run twice.** A second `POST /api/scan/start`
while one is already in flight -- a second browser tab, a doubled click --
reports `already_running` rather than kicking off a competing scan against
the same Google accounts.

**A GET can never start work.** `/api/scan` only ever reads the last
completed result (or says none exists yet); the one thing that starts a
scan is the POST.
"""
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="s1-googleaccess-progress-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP, "t.db")
os.environ["HUB_DATA_DIR"] = _TMP
os.environ["HUB_LEADS_FILE"] = os.path.join(_TMP, "leads.jsonl")
os.environ.setdefault("SECRET_KEY", "google-access-progress-test")
os.environ.setdefault("PANEL_PASSWORD", "test")

PASS = FAIL = 0


def check(label, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + (("  — " + str(detail)) if detail else ""))


def section(title):
    print("\n" + title)
    print("-" * 60)


from flask import Flask  # noqa: E402

from modules.google_access import qa_inactive as qa  # noqa: E402


class _FakeFinder:
    """Stands in for modules.google_finder.app: one call each, no network."""

    def connected_accounts_result(self):
        return (
            [
                {"email": "a@example.com", "refresh_token": "r1", "status": "ACTIVE"},
                {"email": "b@example.com", "refresh_token": "r2", "status": "ACTIVE"},
            ],
            "",
        )

    def gtm_pace_state(self):
        return {"interval": 0.35, "floor": 0.35, "ceiling": 8.0, "throttled": 0,
                "calls": 0, "ok_streak": 0, "last": 0.0}


_SCAN_DELAY = 0.35


def _fake_scan_login(login, refresh, on_progress=None, **_ignored):
    if on_progress:
        on_progress(login, None, "Listing GA4 properties")
    time.sleep(_SCAN_DELAY)
    inactive = [{"kind": "GA4", "login": login, "account": "Acme",
                 "account_id": "1", "name": "acme.com", "resource": f"{login}-p1",
                 "public_id": "", "events": 0, "sessions": 0,
                 "status": "inactive", "reason": "0 events and 0 sessions"}]
    review = []
    active = [{"kind": "GA4", "login": login, "account": "Acme",
               "account_id": "1", "name": "acme-live.com", "resource": f"{login}-p2",
               "public_id": "", "events": 12, "sessions": 4,
               "status": "active", "reason": "Activity detected"}]
    if on_progress:
        on_progress(login, "inactive", "GA4 property 1 of 2: acme.com")
        on_progress(login, "active", "GA4 property 2 of 2: acme-live.com")
    return inactive, review, active


app = Flask(__name__)
qa._finder = _FakeFinder
qa._scan_login = _fake_scan_login


# ---------------------------------------------------------------------------
section("A stale running flag reads as not running")
# ---------------------------------------------------------------------------
check("a fresh heartbeat is running",
      qa._progress_is_running({"running": True, "heartbeat_at": time.time()}))
check("a heartbeat older than the stale window is not",
      not qa._progress_is_running(
          {"running": True, "heartbeat_at": time.time() - qa._HEARTBEAT_STALE_SECONDS - 1}))
check("running: false is never running regardless of heartbeat",
      not qa._progress_is_running({"running": False, "heartbeat_at": time.time()}))


# ---------------------------------------------------------------------------
section("Starting a scan runs it on a background thread, live")
# ---------------------------------------------------------------------------
with app.app_context():
    with app.test_request_context():
        result = qa.start_scan_async(force=True)
    check("the first start actually starts one", result == {
        "ok": True, "started": True, "already_running": False})

    # The fake logins each take _SCAN_DELAY; the background thread needs a
    # moment to be scheduled, so poll for "running" to appear rather than
    # checking exactly once and racing the thread's own startup.
    deadline = time.time() + 2
    prog = qa._progress_read()
    while time.time() < deadline and not qa._progress_is_running(prog):
        time.sleep(0.02)
        prog = qa._progress_read()
    check("progress reports running before the fakes finish",
          qa._progress_is_running(prog), prog)
    check("it names the total logins up front", prog.get("total_logins") == 2, prog)

    with app.test_request_context():
        second = qa.start_scan_async(force=True)
    check("a second start while one is running does not race it",
          second == {"ok": True, "started": False, "already_running": True}, second)

    deadline = time.time() + 5
    while time.time() < deadline and qa._progress_is_running(qa._progress_read()):
        time.sleep(0.05)
    prog = qa._progress_read()
    check("the scan finishes within the test's patience", prog.get("done") is True, prog)
    check("and it is no longer running", not qa._progress_is_running(prog), prog)
    check("both fake logins were counted",
          prog.get("completed_logins") == 2, prog)
    check("the running counts matched what the fakes returned",
          prog.get("inactive_count") == 2 and prog.get("active_count") == 2, prog)


# ---------------------------------------------------------------------------
section("A GET never starts work; a POST is the only thing that does")
# ---------------------------------------------------------------------------
with app.app_context():
    with app.test_request_context():
        # Call the undecorated view directly: require_login wraps in
        # hub.auth.login_required, which this standalone test has no
        # session for and no reason to exercise here.
        payload = qa.api_scan.__wrapped__().get_json()
    check("the completed scan's own result is now what GET answers",
          payload.get("connected_logins") == 2, payload)
    check("it carries the two inactive rows the fakes produced",
          len(payload.get("inactive") or []) == 2, payload)
    check("and the two active ones, counted but not listed",
          payload.get("active_count") == 2, payload)


# A fresh, never-scanned world (no _CACHE, no result file) must say so
# rather than blocking to run one -- reading is not the same as starting.
# A genuinely fresh worker -- new data directory *and* new database, never
# a fresh HUB_DATA_DIR in front of the same DATABASE_URL, which is the
# jsonstore trap hub/jsonstore.py's own tests are built around: the mirror
# keys by path relative to the root, so an unchanged database answers with
# the previous run's row under this file's name even though the directory
# is empty. A subprocess is the clean way to get both at once.
import subprocess  # noqa: E402

_TMP2 = tempfile.mkdtemp(prefix="s1-googleaccess-progress2-")
probe = subprocess.run(
    [sys.executable, "-c", (
        "import os, sys, json\n"
        "sys.path.insert(0, %r)\n"
        "from flask import Flask\n"
        "from modules.google_access import qa_inactive as qa\n"
        "app = Flask(__name__)\n"
        "with app.app_context(), app.test_request_context():\n"
        "    print(json.dumps(qa.api_scan.__wrapped__().get_json()))\n"
        "    print(json.dumps(qa._progress_read()))\n"
    ) % ROOT],
    env={**os.environ, "HUB_DATA_DIR": _TMP2,
         "DATABASE_URL": "sqlite:///" + os.path.join(_TMP2, "t.db")},
    capture_output=True, text=True, timeout=30,
)
check("the fresh-world probe ran cleanly", probe.returncode == 0, probe.stderr[-2000:])
lines = probe.stdout.strip().splitlines()
pending = json.loads(lines[0]) if lines else {}
fresh_prog = json.loads(lines[1]) if len(lines) > 1 else {}
check("a world with no completed scan answers pending rather than blocking",
      pending.get("pending") is True, pending)
check("...and does not silently start one",
      not fresh_prog.get("running"), fresh_prog)


print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
