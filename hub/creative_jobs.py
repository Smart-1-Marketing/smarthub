"""A queue between a lead landing and the creative it can start on its own.

## Why this cannot run inside the lead request

`hub/leads.py` already learned this lesson once: a GHL outage must never cost
a lead, so delivery is stored first and pushed second. An OpenAI call is
slower and less reliable than a GHL write, and putting one on the request that
answers a prospect's form is how "your report is ready" prints over a request
that is still hanging. So a lead that qualifies gets a **row**, not a call —
`hub_creative_jobs` — and the Hub scheduler works through the queue on its own
clock. A lead that gets its scripts four minutes later is fine; a lead that
gets none because the web worker was busy answering OpenAI is not.

## One table, more than one kind

`kind` is a string rather than a boolean or a second table, because this is
meant to grow: today it is only `"radio"` (a WO-1 `modules.radio_scripts`
brief), and a later phase adds `"storyboard"` for the Commercial Builder
autostart, triggered from a different place (a client's approval, not a
lead's capture) but drawn on the same queue, the same sweep, and the same
give-up rule.

## Never a second identity for a job nobody is touching

`claim_next()` is only ever called from inside the scheduler's single leader
— the same guarantee `hub/scheduler.py` already gives every job in `JOBS` — so
the claim does not need cross-worker locking the way `hub/suite_panel.py`'s
idempotency claim does. It still moves a row to `running` and commits before
doing any work, so a leader that dies mid-job leaves a `running` row rather
than a `queued` one a second leader might also pick up mid-restart; the sweep
below reclaims a `running` row that has sat for longer than one job could
plausibly take, the same "a crashed leader doesn't wedge the schedule" answer
`hub/scheduler.py`'s own leadership lock gives one layer up.

## Giving up is a state, not a silence

Three attempts, matching the shape `modules/image_picker/vision.py` and
`hub/video_library.py` already use for a backlog that costs a billed call per
item: a source that fails comes straight back, so without a ceiling one bad
lead costs a call a minute for ever, and every individual attempt looks like
an ordinary retry. After the third the job is `failed`, in writing, with the
error kept rather than cleared — `hub/qr_codes.py`'s rule about a blank
control that says why rather than reading as one nobody pressed.

## Nothing here decides who sees the result

Whether a rep edits the set internally or sends it to the client is a press,
not a default this queue makes for them — the build spec's own rule, and the
reason `enqueue_for_lead()` only ever gets a lead to `ready`, never further.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from hub.extensions import db

MAX_ATTEMPTS = 3

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"

# A "running" row older than this was claimed by a worker that died mid-job
# (a deploy, an OOM) rather than one still working — the scheduler ticks every
# minute and a script generation is two OpenAI calls, so five minutes is loose
# enough not to reclaim a job that is simply slow.
STALE_RUNNING_MINUTES = 5

# Gates which lead sources get a job enqueued at all. Comma-separated; default
# is the one source actually live today. The build spec that asked for this
# named "stadium_blueprint" — the tool that emits leads is `modules/stadium`
# and it calls `capture_and_deliver(source="stadium", ...)` (see
# `hub/lead_tags.py`'s SOURCES table), so that is the string that has to be
# here for the gate to ever fire. The other gameplan pages join by adding
# their own source string once this one has been watched work.
AUTOSTART_ENV = "CREATIVE_AUTOSTART_SOURCES"
DEFAULT_AUTOSTART_SOURCES = "stadium"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def autostart_sources() -> set[str]:
    raw = os.environ.get(AUTOSTART_ENV, DEFAULT_AUTOSTART_SOURCES)
    return {s.strip() for s in raw.split(",") if s.strip()}


class CreativeJob(db.Model):
    """One piece of creative a lead's own arrival asked to start."""

    __tablename__ = "hub_creative_jobs"

    id = db.Column(db.Integer, primary_key=True)

    lead_id = db.Column(db.String(80), default="", index=True)
    kind = db.Column(db.String(40), default="", index=True)
    state = db.Column(db.String(20), default=QUEUED, index=True)
    attempts = db.Column(db.Integer, default=0)

    # What the job needs to run, snapshotted at enqueue time rather than
    # re-read from the lead when the job fires — a lead can be merged or
    # converted in the minutes between capture and the next scheduler tick,
    # and the brief this job runs against should be the one true when the
    # lead actually landed, not whatever the row has become by then.
    payload_json = db.Column(db.Text, default="{}")
    # What the job produced — for "radio", the RadioScriptSet id — so the
    # leads panel can link straight to it rather than searching for it.
    result_json = db.Column(db.Text, default="{}")
    error = db.Column(db.Text, default="")

    created_at = db.Column(db.DateTime, default=_now, index=True)
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)

    def payload(self) -> dict:
        try:
            return json.loads(self.payload_json or "{}") or {}
        except (TypeError, ValueError):
            return {}

    def result(self) -> dict:
        try:
            return json.loads(self.result_json or "{}") or {}
        except (TypeError, ValueError):
            return {}

    def as_dict(self) -> dict:
        return {
            "id": self.id, "lead_id": self.lead_id or "", "kind": self.kind or "",
            "state": self.state or QUEUED, "attempts": self.attempts or 0,
            "error": self.error or "", "result": self.result(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ---------------------------------------------------------------------------
# Brief builders, one per kind
# ---------------------------------------------------------------------------
#
# A job's payload is built once, from whatever triggered it, and kept as data
# on the row rather than re-derived at run time. Only "radio" exists today;
# a "storyboard" kind (WO-4) is triggered from an approved radio concept
# rather than from a lead at all, so it does not belong in this table.

def _radio_payload_from_lead(row: dict) -> dict:
    """The `modules.radio_scripts.engine` brief shape, off a Stadium lead row.

    `fields` carries whatever the landing page asked beyond name/email/phone/
    company — for Stadium that is `team`, `market` and `package`
    (`modules/stadium/app.py::_capture`). Local shows, an offer and the
    client's own URL are not on the lead today, so those are left blank
    rather than guessed at: `engine.generate()` reads an empty offer as "no
    offer supplied" and skips the coverage check entirely when it has no
    market, team or show to check for, which is the correct reading of a
    brief that genuinely does not have those facts yet.
    """
    fields = row.get("fields") or {}
    company = str(row.get("company") or row.get("name") or "").strip()
    return {
        "company": company, "client_name": company,
        "market": str(fields.get("market") or "").strip(),
        "team": str(fields.get("team") or "").strip(),
        "package": str(fields.get("package") or "").strip(),
        "local_shows": [], "offer": "",
        "phone": str(row.get("phone") or "").strip(),
        "url": "", "funnel": {},
        "lead_id": str(row.get("id") or "").strip(),
    }


_PAYLOAD_BUILDERS = {"radio": _radio_payload_from_lead}


def enqueue_for_lead(row: dict) -> CreativeJob | None:
    """Queue a job for this lead if, and only if, its source is autostarted.

    Called unconditionally from `hub/leads.py::capture_and_deliver`, after the
    row is stored and delivered — this is the hook point, and every reason it
    might not queue anything (the wrong source, no database, a bad row) is
    swallowed here so a queueing fault can never fail the lead capture that is
    already done by the time this runs.
    """
    try:
        source = str(row.get("source") or "")
        if source not in autostart_sources():
            return None
        # "radio" is the only kind a lead source starts today; a source list
        # that grows still only ever asks for the same one.
        payload = _PAYLOAD_BUILDERS["radio"](row)
        job = CreativeJob(lead_id=str(row.get("id") or ""), kind="radio",
                          state=QUEUED, attempts=0,
                          payload_json=json.dumps(payload))
        db.session.add(job)
        db.session.commit()
        return job
    except Exception:                                     # noqa: BLE001
        try:
            db.session.rollback()
        except Exception:                                 # noqa: BLE001
            pass
        return None


def _reclaim_stale() -> None:
    """A `running` row nobody is actually running any more goes back to the
    queue. Runs ahead of every claim rather than on its own schedule, so a
    worker that died mid-job does not strand its lead for ever."""
    cutoff = _now() - timedelta(minutes=STALE_RUNNING_MINUTES)
    stuck = (CreativeJob.query.filter(CreativeJob.state == RUNNING,
                                      CreativeJob.updated_at < cutoff).all())
    for job in stuck:
        job.state = QUEUED
    if stuck:
        db.session.commit()


def claim_next() -> CreativeJob | None:
    """The oldest queued job, moved to `running` and committed before any
    work starts. Only ever called from the scheduler's single leader."""
    _reclaim_stale()
    job = (CreativeJob.query.filter_by(state=QUEUED)
          .order_by(CreativeJob.created_at.asc()).first())
    if job is None:
        return None
    job.state = RUNNING
    db.session.commit()
    return job


def mark_done(job: CreativeJob, result: dict) -> None:
    job.state = DONE
    job.result_json = json.dumps(result or {})
    job.error = ""
    db.session.commit()


def mark_failed(job: CreativeJob, message: str) -> None:
    """Give up in writing after MAX_ATTEMPTS — never a silent drop.

    A failure that stays `queued` is retried on a later tick; one that has
    used up its attempts is `failed` and stays that way, with the error kept
    rather than cleared, so the leads panel has something to show for it
    rather than a job that simply stopped moving with nothing saying why.
    """
    job.attempts = (job.attempts or 0) + 1
    job.error = str(message or "")[:2000]
    job.state = QUEUED if job.attempts < MAX_ATTEMPTS else FAILED
    db.session.commit()
    if job.state == FAILED:
        try:
            from hub import audit
            payload = job.payload()
            audit.log("creative_jobs", "job_failed",
                     client=payload.get("company") or None,
                     lead=job.lead_id or None, kind=job.kind,
                     attempts=job.attempts, error=job.error[:200])
        except Exception:                                 # noqa: BLE001
            pass


def _run_radio(job: CreativeJob) -> dict:
    from modules.radio_scripts import api as radio_api
    row = radio_api.create_set(job.payload(), actor="scheduler")
    return {"set_id": row.id, "client_name": row.client_name}


_RUNNERS = {"radio": _run_radio}


def run_one() -> dict:
    """Claim and run one job. Called by the scheduler, once a minute.

    One job per tick, deliberately — the spec this queue exists for says so
    in as many words, and it keeps a burst of stadium leads from spending a
    model call a second on the one thread every scheduler job shares.
    """
    job = claim_next()
    if job is None:
        return {"claimed": 0}
    runner = _RUNNERS.get(job.kind)
    if runner is None:
        mark_failed(job, f"No runner registered for kind {job.kind!r}.")
        return {"claimed": 1, "ok": False, "kind": job.kind,
                "error": "unknown kind"}
    try:
        result = runner(job)
    except Exception as exc:                              # noqa: BLE001
        mark_failed(job, f"{type(exc).__name__}: {exc}")
        return {"claimed": 1, "ok": False, "kind": job.kind, "lead_id": job.lead_id,
                "attempts": job.attempts, "gave_up": job.state == FAILED}
    mark_done(job, result)
    return {"claimed": 1, "ok": True, "kind": job.kind, "lead_id": job.lead_id,
            "result": result}


def state_for_leads(lead_ids: list[str]) -> dict[str, dict]:
    """The latest job per lead id, for the leads panel. Never raises.

    One query for a page of leads rather than one per row — the panel shows
    up to a page of leads at once and a per-row round trip is the N+1 shape
    `hub/website_audit.py`'s own upsell report was written to avoid.
    """
    ids = [str(i) for i in lead_ids if i]
    if not ids:
        return {}
    try:
        rows = (CreativeJob.query.filter(CreativeJob.lead_id.in_(ids))
               .order_by(CreativeJob.id.desc()).all())
    except Exception:                                     # noqa: BLE001
        return {}
    out: dict[str, dict] = {}
    for row in rows:
        out.setdefault(row.lead_id, row.as_dict())
    return out


__all__ = ["CreativeJob", "enqueue_for_lead", "claim_next", "mark_done",
          "mark_failed", "run_one", "state_for_leads", "autostart_sources",
          "MAX_ATTEMPTS", "QUEUED", "RUNNING", "DONE", "FAILED"]
