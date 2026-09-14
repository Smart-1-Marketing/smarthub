"""The lead-triggered creative queue (build spec WO-2).

    python3 test_creative_jobs.py

Same shape as test_radio_scripts.py: no pytest, a throwaway SQLite database
and a temporary data directory, and every OpenAI call replaced with a stub —
this file never reaches the network.

## What this file is protecting

**An OpenAI call must never sit inside the lead request.** That is the whole
reason this queue exists rather than `capture_and_deliver` calling the radio
engine directly: a lead is stored and delivered first, and enqueuing a job
happens only after, wrapped so a queueing fault can never fail a capture that
has already succeeded.

**The gate is on the real source string, not the one the build spec
guessed.** The spec that asked for this named `CREATIVE_AUTOSTART_SOURCES`'s
default `"stadium_blueprint"` — and `modules/stadium/app.py` actually calls
`capture_and_deliver(source="stadium", ...)`, the name `hub/lead_tags.py`'s
own SOURCES table registers. A default that never matches a real source is a
trigger that silently never fires, on a feature whose whole premise is that
it runs by itself; this asserts the default is the string that is actually
live.

**Giving up is a state, not a silence.** Three attempts, then `failed`, with
the error kept rather than cleared, so a lead that could not get its scripts
is visible in the leads panel rather than a job that stopped moving with
nothing on any screen saying why.

**A stale `running` row is reclaimed, never stranded.** A worker that dies
mid-job must not leave a lead's job invisible to every later tick.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1creativejobs_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ.setdefault("SECRET_KEY", "creative-jobs-test-secret")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-a-real-key")

_passed = _failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def check_true(label, got):
    check(label, bool(got), True)


def section(title):
    print(f"\n{title}\n{'-' * len(title)}")


# ---------------------------------------------------------------------------
section("The default source is the one that is actually live")

from hub import creative_jobs, lead_tags                            # noqa: E402

check("the default autostart source is the real Stadium source string",
      creative_jobs.DEFAULT_AUTOSTART_SOURCES, "stadium")
check("...and that source is a registered one, not an invented one",
      lead_tags.known("stadium"), True)

os.environ["CREATIVE_AUTOSTART_SOURCES"] = "stadium, boat "
check("the env var is read as a comma-separated set, trimmed",
      creative_jobs.autostart_sources(), {"stadium", "boat"})
del os.environ["CREATIVE_AUTOSTART_SOURCES"]
check("with nothing set, the default is the sole source",
      creative_jobs.autostart_sources(), {"stadium"})


# ---------------------------------------------------------------------------
section("Booting the composed app -- every table it needs exists")

import wsgi                                                          # noqa: E402
from hub.extensions import db                                        # noqa: E402
from hub import auth                                                 # noqa: E402

with wsgi.hub_app.app_context():
    db.create_all()

STADIUM_ROW = {
    "id": "lead-stadium-1", "source": "stadium", "company": "Monogram Homes",
    "name": "Pat Rivera", "email": "pat@monogramhomes.com", "phone": "5135550100",
    "fields": {"team": "Bengals", "market": "Cincinnati", "package": "Growth",
              "monthly": "1500"},
}
BOAT_ROW = {"id": "lead-boat-1", "source": "boat", "company": "Lakeside Marine",
           "fields": {}}


# ---------------------------------------------------------------------------
section("enqueue_for_lead: gated on source, never raises")

with wsgi.hub_app.app_context():
    job = creative_jobs.enqueue_for_lead(STADIUM_ROW)
    check_true("a Stadium lead gets a queued job", job is not None)
    check("...kind is radio", job.kind, "radio")
    check("...state starts queued", job.state, creative_jobs.QUEUED)
    check("...attempts starts at zero", job.attempts, 0)
    payload = job.payload()
    check("...the brief carries the company as client_name",
          payload["client_name"], "Monogram Homes")
    check("...and the fields Stadium actually sends",
          (payload["market"], payload["team"], payload["package"]),
          ("Cincinnati", "Bengals", "Growth"))
    check("...and the lead id, for the set to be traced back",
          payload["lead_id"], "lead-stadium-1")

    none_job = creative_jobs.enqueue_for_lead(BOAT_ROW)
    check("a source not in the autostart set gets nothing queued",
          none_job, None)

    # A malformed row must not raise past this function -- the whole point is
    # that a queueing fault can never cost the lead capture that already
    # succeeded by the time this runs.
    bad = creative_jobs.enqueue_for_lead({"source": "stadium"})
    check_true("a lead with no usable fields still enqueues without raising",
               bad is not None)


# ---------------------------------------------------------------------------
section("claim_next / mark_done: the ordinary path")

with wsgi.hub_app.app_context():
    claimed = creative_jobs.claim_next()
    check_true("the oldest queued job is claimed", claimed is not None)
    check("...and moved to running", claimed.state, creative_jobs.RUNNING)

    nxt = creative_jobs.claim_next()
    check_true("the second queued job (the malformed one) is claimed next",
               nxt is not None)
    check("no third job to claim", creative_jobs.claim_next(), None)

    creative_jobs.mark_done(claimed, {"set_id": 42, "client_name": "Monogram Homes"})
    check("mark_done sets state done", claimed.state, creative_jobs.DONE)
    check("...and records the result", claimed.result(), {"set_id": 42,
                                                          "client_name": "Monogram Homes"})
    check("...and clears any error", claimed.error, "")


# ---------------------------------------------------------------------------
section("mark_failed: three attempts, then given up on in writing")

with wsgi.hub_app.app_context():
    job = creative_jobs.enqueue_for_lead(dict(STADIUM_ROW, id="lead-stadium-2"))
    claimed = creative_jobs.claim_next()
    check("...matches the job just enqueued", claimed.id, job.id)

    creative_jobs.mark_failed(claimed, "OpenAI returned no text.")
    check("first failure: attempts is 1", claimed.attempts, 1)
    check("...and it goes back to queued for a retry", claimed.state,
          creative_jobs.QUEUED)
    check("...with the error kept", claimed.error, "OpenAI returned no text.")

    claimed = creative_jobs.claim_next()
    creative_jobs.mark_failed(claimed, "OpenAI returned no text.")
    check("second failure: attempts is 2, still queued", (claimed.attempts, claimed.state),
          (2, creative_jobs.QUEUED))

    claimed = creative_jobs.claim_next()
    creative_jobs.mark_failed(claimed, "OpenAI returned no text.")
    check(f"third failure: gives up at MAX_ATTEMPTS={creative_jobs.MAX_ATTEMPTS}",
          claimed.attempts, creative_jobs.MAX_ATTEMPTS)
    check("...and the state is failed, not queued forever or silently dropped",
          claimed.state, creative_jobs.FAILED)
    check("no job left to claim -- a failed job is not retried again",
          creative_jobs.claim_next(), None)

    # Visible: a permanent give-up writes an activity-log line naming the lead.
    from hub import audit
    log_path = audit._path()
    with open(log_path, encoding="utf-8") as fh:
        lines = [json.loads(l) for l in fh if l.strip()]
    gave_up = [l for l in lines if l.get("type") == "job_failed"
              and l.get("module") == "creative_jobs"]
    check_true("the permanent give-up is logged", bool(gave_up))
    check("...naming the lead", gave_up[-1].get("lead"), "lead-stadium-2")
    check("...and the client", gave_up[-1].get("client"), "Monogram Homes")


# ---------------------------------------------------------------------------
section("A crashed leader's stale running row is reclaimed, not stranded")

with wsgi.hub_app.app_context():
    creative_jobs.enqueue_for_lead(dict(STADIUM_ROW, id="lead-stadium-3"))
    claimed = creative_jobs.claim_next()
    check("the stuck job is claimed (running)", claimed.state, creative_jobs.RUNNING)
    # Backdate updated_at past the staleness window, simulating a worker that
    # died mid-job rather than one still working.
    import datetime as _dt
    claimed.updated_at = (_dt.datetime.now(_dt.timezone.utc)
                          - _dt.timedelta(minutes=creative_jobs.STALE_RUNNING_MINUTES + 1))
    db.session.commit()

    reclaimed = creative_jobs.claim_next()
    check("the stale running row is reclaimed on the next claim",
          reclaimed.id, claimed.id)
    check("...and is running again rather than stranded",
          reclaimed.state, creative_jobs.RUNNING)


# ---------------------------------------------------------------------------
section("state_for_leads: one query, the latest job per lead")

with wsgi.hub_app.app_context():
    ids = ["lead-stadium-1", "lead-stadium-2", "lead-stadium-3", "lead-boat-1",
          "no-such-lead"]
    states = creative_jobs.state_for_leads(ids)
    check("the done lead reports done", states["lead-stadium-1"]["state"], "done")
    check("the failed lead reports failed", states["lead-stadium-2"]["state"], "failed")
    check("a lead with no job at all is simply absent", "no-such-lead" in states, False)
    check("a lead never gated in is absent too", "lead-boat-1" in states, False)
    check("state_for_leads([]) answers empty rather than raising",
          creative_jobs.state_for_leads([]), {})


# ---------------------------------------------------------------------------
section("run_one: the whole path, with the model stubbed")

import modules.radio_scripts.engine as _live_engine                  # noqa: E402

clean_answer = json.dumps({"concepts": [
    {"idea": "Idea " + str(i), "talent_direction": "Warm.", "sfx_notes": "",
     "scripts": {"60": " ".join(["word"] * 150), "30": " ".join(["word"] * 70),
                "15": " ".join(["word"] * 37)},
     "tag": {"name": "Monogram Homes", "offer": "", "cta": "Call now",
             "phone_spoken": "five one three", "url_spoken": ""},
     "legal_line": ""} for i in range(3)]})
_orig_ask = _live_engine._ask
_live_engine._ask = lambda prompt, **kw: clean_answer

try:
    with wsgi.hub_app.app_context():
        creative_jobs.enqueue_for_lead(dict(STADIUM_ROW, id="lead-stadium-4"))
        result = creative_jobs.run_one()
        check("run_one claims and runs exactly one job", result["claimed"], 1)
        check("...and reports ok", result.get("ok"), True)
        check("...naming the lead", result.get("lead_id"), "lead-stadium-4")

        job = creative_jobs.state_for_leads(["lead-stadium-4"])["lead-stadium-4"]
        check("the job is done", job["state"], "done")
        set_id = job["result"].get("set_id")
        check_true("...and carries the set id it wrote", bool(set_id))

        from modules.radio_scripts.models import RadioScriptSet
        row = db.session.get(RadioScriptSet, set_id)
        check_true("a real RadioScriptSet exists at that id", row is not None)
        check("...carrying the lead id back to where it started",
              row.lead_id, "lead-stadium-4")
        check("...attributed to the scheduler, not a signed-in user",
              row.actor, "scheduler")

        check("with nothing left queued, run_one claims nothing",
              creative_jobs.run_one(), {"claimed": 0})
finally:
    _live_engine._ask = _orig_ask


# ---------------------------------------------------------------------------
section("A failing engine call is caught by run_one, not raised through it")

_live_engine._ask = lambda prompt, **kw: (_ for _ in ()).throw(
    RuntimeError("The model returned no text."))
try:
    with wsgi.hub_app.app_context():
        creative_jobs.enqueue_for_lead(dict(STADIUM_ROW, id="lead-stadium-5"))
        result = creative_jobs.run_one()
        check("a failing generation is reported, not raised", result.get("ok"), False)
        check("...naming which lead", result.get("lead_id"), "lead-stadium-5")
        check("...and it is queued for a retry rather than lost",
              creative_jobs.state_for_leads(["lead-stadium-5"])["lead-stadium-5"]["state"],
              "queued")
finally:
    _live_engine._ask = _orig_ask


# ---------------------------------------------------------------------------
section("modules.radio_scripts.api.create_set is the one path both callers use")

from modules.radio_scripts import api as radio_api                   # noqa: E402

_live_engine._ask = lambda prompt, **kw: clean_answer
try:
    with wsgi.hub_app.app_context():
        row = radio_api.create_set(
            {"client_name": "Direct Call Co", "market": "Dayton", "team": "",
            "package": "", "local_shows": [], "offer": "", "phone": "",
            "url": "", "funnel": {}, "lead_id": ""},
            actor="a-rep")
        check("create_set persists a real row", row.client_name, "Direct Call Co")
        check("...with the actor it was given", row.actor, "a-rep")
finally:
    _live_engine._ask = _orig_ask


# ---------------------------------------------------------------------------
section("hub/leads.py: capture_and_deliver enqueues after the lead is stored")

from hub import leads                                                # noqa: E402

os.environ["GHL_LOCATION_ID"] = ""  # delivery stays "none"; irrelevant here

with wsgi.hub_app.app_context():
    before = creative_jobs.CreativeJob.query.count()
    result = leads.capture_and_deliver(
        "stadium", "Stadium to Screen",
        {"name": "Ari Cohen", "email": "ari@example.com", "phone": "5135550199",
        "company": "Ari's Roofing", "team": "Bengals", "market": "Cincinnati",
        "package": "Growth"})
    check_true("the lead capture itself still succeeds", result["ok"])
    PANEL_LEAD_ID = result["lead_id"]
    after = creative_jobs.CreativeJob.query.count()
    check("a job was queued for the autostarted source", after - before, 1)

    queued = (creative_jobs.CreativeJob.query
             .filter_by(lead_id=result["lead_id"]).first())
    check_true("...tied to the lead capture_and_deliver just created",
               queued is not None)
    check("...carrying the brief built from that capture",
          queued.payload()["client_name"], "Ari's Roofing")

    before2 = creative_jobs.CreativeJob.query.count()
    result2 = leads.capture_and_deliver(
        "boat", "Boat Dealer Weather Marketing",
        {"name": "Sam", "email": "sam@example.com", "company": "Sam's Marina"})
    check_true("a non-autostarted source still captures fine", result2["ok"])
    check("...and queues nothing",
          creative_jobs.CreativeJob.query.count() - before2, 0)

    # A queueing fault must never cost the capture that already happened.
    _orig_enqueue = creative_jobs.enqueue_for_lead
    creative_jobs.enqueue_for_lead = lambda row: (_ for _ in ()).throw(
        RuntimeError("boom"))
    try:
        result3 = leads.capture_and_deliver(
            "stadium", "Stadium to Screen",
            {"name": "X", "email": "x@example.com", "company": "X Co"})
        check_true("a raising enqueue still leaves the capture successful",
                   result3["ok"])
    finally:
        creative_jobs.enqueue_for_lead = _orig_enqueue


# ---------------------------------------------------------------------------
section("hub/lead_tags.py: the two event tags, declared with their workflow")

check_true("radio_script_ready is declared",
           "radio_script_ready" in lead_tags.EVENT_TAGS)
check_true("spot_review_ready is declared",
           "spot_review_ready" in lead_tags.EVENT_TAGS)
for name in ("radio_script_ready", "spot_review_ready"):
    entry = lead_tags.EVENT_TAGS[name]
    check_true(f"{name} names what it is for", bool(entry.get("what")))
    check(f"{name} has no Suite workflow built yet (correctly None)",
          entry.get("workflow"), None)


# ---------------------------------------------------------------------------
section("hub/client_brand.py: creative_jobs is declared, not silently unattributed")

from hub import client_brand                                         # noqa: E402

check_true("creative_jobs is in NOT_WORK (queue bookkeeping, not a deliverable)",
           "creative_jobs" in client_brand.NOT_WORK)
check("...and radio_scripts is still the deliverable's own name",
      "radio_scripts" in client_brand.WORK_KINDS, True)


# ---------------------------------------------------------------------------
section("hub/scheduler.py: the sweep is registered and actually runs")

from hub import scheduler                                            # noqa: E402

check_true("creative_jobs is a registered scheduler job",
           "creative_jobs" in scheduler.JOBS)
every, fn, _desc = scheduler.JOBS["creative_jobs"]
check("...ticking every minute, as the spec asks for", every, 1)
check("...pointing at the sweep function", fn, scheduler.job_creative_jobs_sweep)

with wsgi.hub_app.app_context():
    creative_jobs.enqueue_for_lead(dict(STADIUM_ROW, id="lead-stadium-6"))
_live_engine._ask = lambda prompt, **kw: clean_answer
try:
    out = scheduler.job_creative_jobs_sweep(wsgi.hub_app)
    check("the job function claims and runs the queued job", out.get("claimed"), 1)
finally:
    _live_engine._ask = _orig_ask


# ---------------------------------------------------------------------------
section("The leads panel: script state and the two buttons")

from werkzeug.test import Client                                     # noqa: E402

client = Client(wsgi.application)
client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Todd"), domain="localhost")

resp = client.get("/api/leads?days=730&source=stadium")
check("the leads API answers for a signed-in visitor", resp.status_code, 200)
rows = {r["id"]: r for r in resp.get_json()["leads"]}
check_true("a lead with a queued job carries its radio_job on the panel row",
           "radio_job" in rows.get(PANEL_LEAD_ID, {}))
if PANEL_LEAD_ID in rows:
    check("...state queued (nothing has run it yet)",
          rows[PANEL_LEAD_ID]["radio_job"]["state"], "queued")

template = (ROOT / "hub" / "templates" / "leads.html").read_text(encoding="utf-8")
check_true("the panel has a Radio script column", "Radio script" in template)
check_true("...and offers Edit internally once a set exists",
           "Edit internally" in template)
check_true("...and Send to client wired to the WO-3 review link",
           "Send to client" in template and "sendScriptForReview" in template)
check_true("...pointed at the tool's own set-opening hash route",
           "/tools/radio-scripts/#set=" in template)


print(f"\n{_passed} passed, {_failed} failed")
if _failed:
    sys.exit(1)
