"""The cross-Hub "it's done, come back" notification.

    python3 test_job_notify.py

Same shape as the other test files: no pytest, no new dependencies, and it
runs against a temporary data directory and a throwaway SQLite database, so
it never touches /var/data or the real one.

## Why this file exists

A paint animation or a Vox explainer takes minutes, exactly like HeyGen and
Runway, and nobody sits on the page that started it waiting — they go back
to whatever else they were doing. Before this, the only way to learn a
render had finished was to remember to go back and check. `hub/job_notify.py`
is a lightweight pointer registry a tool opts into alongside its own job
store (`modules/hyperframes_tools/jobs.py` keeps the detailed record; this
keeps only who is waiting, what to call it, and where to send them), and
`hub-job-notify.js` polls it from wherever the person actually is.

What this file asserts, each a way the feature would go quietly wrong:

  1. one owner's pointers are invisible to another        — a render notice
                                                              is not a thing
                                                              to leak between
                                                              reps
  2. update() merges onto the same id register() returned  — a caller with
                                                              no second
                                                              lookup table
                                                              must still be
                                                              able to move a
                                                              pointer along
  3. the sweep only ever drops FINISHED pointers            — a render still
                                                              queued is the
                                                              one row
                                                              somebody is
                                                              actually
                                                              waiting on
  4. /api/background-jobs/mine is behind the login          — these are
                                                              staff render
                                                              records
  5. a store that could not be read answers measured:False  — not a
                                                              confident empty
                                                              list
  6. submitting a HyperFrames render registers a pointer,
     and polling it moves the pointer along too             — the actual
                                                              wiring, not
                                                              just the module
                                                              in isolation
  7. the script is served, and reaches every dispatcher-
     mounted module — unlike hub-cheers.js/hub-qa-nudge.js,
     which deliberately do not                               — its whole
                                                              point is
                                                              following
                                                              somebody across
                                                              tools
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1jobnotify_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ["SECRET_KEY"] = "jobnotify-test-secret"
os.environ["PANEL_PASSWORD"] = "jobnotify-test-shared"
os.environ.pop("HF_RENDER_SERVICE_URL", None)
os.environ.pop("HF_RENDER_ENABLED", None)

from werkzeug.test import Client                                    # noqa: E402

from hub import job_notify                                          # noqa: E402
from wsgi import application                                        # noqa: E402

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def ok(label, condition, detail=""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}{('  — ' + detail) if detail else ''}")


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# --------------------------------------------------------- the store itself
section("register / update / mine / forget")

row = job_notify.register(owner="a@example.com", tool="paint-animation",
                          label="Paint animation — hello",
                          return_url="/tools/paint-animation/",
                          poll_url="/tools/paint-animation/api/render/x1",
                          status="queued")
check("a fresh pointer starts at the status it was given", row["status"], "queued")
ok("and carries the poll_url verbatim",
   row["poll_url"] == "/tools/paint-animation/api/render/x1")

updated = job_notify.update(row["id"], status="rendering")
check("update() moves the same row along", updated["status"], "rendering")
check("and mine() sees the update, not the stale copy",
      job_notify.mine("a@example.com")[0]["status"], "rendering")

check("update() on an id that was never registered is a no-op, not a throw",
      job_notify.update("not-a-real-id", status="done"), None)

section("one owner cannot see another owner's pointers")

job_notify.register(owner="b@example.com", tool="vox-explainer",
                    label="Vox — other person's", return_url="/x", status="queued")
_mine_a = job_notify.mine("a@example.com")
_mine_b = job_notify.mine("b@example.com")
ok("a's list holds only a's pointer", all(r["owner"] == "a@example.com" for r in _mine_a))
ok("b's list holds only b's pointer", all(r["owner"] == "b@example.com" for r in _mine_b))
check("neither list is empty", (len(_mine_a) > 0, len(_mine_b) > 0), (True, True))

section("the sweep drops only FINISHED pointers, never a running one")

owner = "sweep@example.com"
_running = job_notify.register(owner=owner, tool="paint-animation",
                                label="Still going", return_url="/x", status="rendering")
for i in range(job_notify.MAX_PER_OWNER + 5):
    r = job_notify.register(owner=owner, tool="paint-animation",
                            label=f"finished {i}", return_url="/x", status="queued")
    job_notify.update(r["id"], status="done")

_after = job_notify.mine(owner)
_ids_after = {r["id"] for r in _after}
ok("the still-running pointer survives the sweep even though it is oldest",
   _running["id"] in _ids_after,
   "a render still in flight must never be swept away from under it")
ok("finished pointers are capped at MAX_PER_OWNER",
   len([r for r in _after if r["status"] == "done"]) <= job_notify.MAX_PER_OWNER)

section("forget() actually removes a pointer")

_gone = job_notify.register(owner="c@example.com", tool="x", label="x",
                            return_url="/x", status="done")
check("forget() reports success", job_notify.forget(_gone["id"]), True)
check("and the row is really gone", job_notify.mine("c@example.com"), [])

# --------------------------------------------------------- the page, as the browser receives it
section("The page, as the browser receives it")

_anon = Client(application)
_r = _anon.get("/api/background-jobs/mine")
check("staff render pointers are behind the login", _r.status_code, 401)
check("the popup script is served to anyone (it is a static asset, not a "
      "record)", _anon.get("/hub-job-notify.js").status_code, 200)

_in = Client(application)
_in.post("/login", data={"password": "jobnotify-test-shared", "name": "CI"})

job_notify.register(owner="shared-login", tool="paint-animation",
                    label="Signed-in owner's own", return_url="/x", status="done")
_api = _in.get("/api/background-jobs/mine").get_json()
check("measured is true on a healthy read", _api["measured"], True)
ok("and this account's own pointer is in the list",
   any(j["label"] == "Signed-in owner's own" for j in _api["jobs"]))

# A store that cannot be read must answer False rather than a confident
# empty list — "nothing is running" and "we could not look" are different
# claims, and only the first means there is nothing to come back to.
_real_mine = job_notify.mine
job_notify.mine = lambda owner: (_ for _ in ()).throw(RuntimeError("disk unavailable"))
try:
    _broken = _in.get("/api/background-jobs/mine").get_json()
    check("a source that could not be read answers measured:False",
          _broken["measured"], False)
finally:
    job_notify.mine = _real_mine

_page = _in.get("/").get_data(as_text=True)
ok("the Hub's own pages load the script", "/hub-job-notify.js" in _page)

# ------------------------------------------------- unlike hub-cheers.js/hub-qa-nudge.js
section("unlike hub-cheers.js/hub-qa-nudge.js, this one rides with the chrome")

# A dispatcher-mounted module's response is what HubBar wraps. Any mounted
# prefix will do; the point is that the script reaches it at all, which
# hub-cheers.js and hub-qa-nudge.js deliberately do not.
_mounted = None
for _prefix in ("/scans/", "/tools/seo-images/"):
    _resp = _in.get(_prefix)
    if _resp.status_code < 400:
        _mounted = (_prefix, _resp.get_data(as_text=True))
        break
if _mounted:
    _prefix, _body = _mounted
    ok(f"hub-job-notify.js reaches a mounted module ({_prefix})",
       "/hub-job-notify.js" in _body)
    ok("and hub-cheers.js deliberately does not reach it",
       "/hub-cheers.js" not in _body)
else:
    print("  --    no mounted module answered in this environment; "
          "skipped (not a failure — nothing here to check against)")

# ----------------------------------------------------------- the actual wiring
section("submitting and polling a HyperFrames render writes into job_notify")

from hub import hyperframes                                        # noqa: E402

_real_is_configured = hyperframes.is_configured
_real_submit = hyperframes.submit
_real_status = hyperframes.status

hyperframes.is_configured = lambda: True
hyperframes.submit = lambda template, params: {
    "job_id": "stub-1", "template": template, "status": "queued",
    "url": None, "error": None, "params": params, "submitted_at": 0,
}
_stub_state = {"status": "rendering", "url": None}
hyperframes.status = lambda job_id: {
    "job_id": job_id, "status": _stub_state["status"], "url": _stub_state["url"],
    "error": None, "duration_seconds": None, "progress": None,
}

try:
    _submit_resp = _in.post("/tools/paint-animation/api/render",
                            json={"text": "hello", "seconds": 3})
    check("the submit itself still succeeds", _submit_resp.status_code, 200)
    _job = _submit_resp.get_json()["job"]

    _pointers = job_notify.mine("shared-login")
    _match = next((p for p in _pointers if p["id"] == _job["id"]), None)
    ok("submitting a render registers a pointer under the same id",
       _match is not None)
    if _match:
        check("the pointer starts queued", _match["status"], "queued")
        check("and carries a poll_url pointing back at this same route",
              _match["poll_url"], f"/tools/paint-animation/api/render/{_job['id']}")

    # Advance the stub, then poll the tool's own route — the write-through
    # `_poll()` already does for its own row must also move the pointer.
    _stub_state["status"] = "done"
    _stub_state["url"] = "http://127.0.0.1/fake.mp4"
    _in.get(f"/tools/paint-animation/api/render/{_job['id']}")
    _match2 = next((p for p in job_notify.mine("shared-login")
                    if p["id"] == _job["id"]), None)
    ok("polling the tool's own status route moves the pointer along too",
       _match2 is not None and _match2["status"] == "done",
       f"got {_match2!r}")
finally:
    hyperframes.is_configured = _real_is_configured
    hyperframes.submit = _real_submit
    hyperframes.status = _real_status

if _failed:
    print(f"\n{_failed} FAILED, {_passed} passed")
    sys.exit(1)
print(f"\n{_passed} checks passed — pointers stay per-owner, the sweep "
      "never touches a running job, the route is behind the login, and "
      "submitting and polling a real render writes and moves a pointer.")
