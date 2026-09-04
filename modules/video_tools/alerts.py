"""Telling somebody their edit is ready, when they are no longer looking at it.

The first version of these tools polled from the browser and only from the
browser, which meant a job advanced only while the tab that started it stayed
open. Close the tab and the edit finished at Cloudinary and nothing here ever
noticed: the row stayed `building` for ever, the result URL was never written
down, and the person came back to a job that looked stuck. That is the same
gap the project docs name against the Commercial Builder -- "nothing notifies
when a render finishes" -- and it is worse here, because these edits are the
kind somebody kicks off and walks away from.

Three pieces, in the order they run:

  * `sweep()` is the scheduler's minute job. It asks Cloudinary about every
    job still building and writes down the answer. Nothing else in this module
    can now depend on a browser being open.
  * `ready_for()` is what the dashboard card and the popup read: edits that
    have finished and that this person has not been shown yet.
  * `mark_seen()` is written when the popup is SHOWN, not when it is
    dismissed -- the rule hub/static/hub-cheers.js arrived at, and for the
    same reason: a reload must not bring the same interruption back.

Server-side rather than in localStorage, unlike the birthday popup. A
birthday is the same for everybody and can be re-shown harmlessly; "your edit
is ready" is one person's and has to survive them opening it on a different
machine, so the marker belongs on the row.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import config, edits, sources
from .db import db
from .models import VideoJob

# How far back the popup and the card will look. A finished edit nobody has
# opened after two days is not news any more, and a dashboard card that
# accumulates for ever becomes a card people stop reading. The job itself
# stays in "Recent edits" on the tool page either way.
NOTICE_DAYS = 2

# How long a job may sit in `building` before the sweep stops asking. Same
# number the page uses, so a job the page gave up on is a job the sweep gives
# up on, rather than the two disagreeing about whether it is still running.
STALE_SECONDS = config.JOB_TIMEOUT_SECONDS


def sweep(limit: int = 40) -> dict:
    """Ask Cloudinary about everything still building, and write it down.

    Bounded, because this runs on the scheduler's one thread and every job is
    an API round trip: forty is more than this tool will ever have in flight
    at once, and a run that would exceed it leaves the rest for the next
    minute rather than holding up every job behind it.

    Never raises. A Cloudinary outage must not take the scheduler down with
    it, and the jobs are still there next minute.
    """
    if not sources.ready():
        return {"skipped": "CLOUDINARY_URL is not set"}

    cutoff = datetime.utcnow() - timedelta(seconds=STALE_SECONDS)
    rows = (VideoJob.query
            .filter(VideoJob.status == "building")
            .order_by(VideoJob.id.asc()).limit(limit).all())
    done = failed = still = 0
    for job in rows:
        if job.created_at and job.created_at < cutoff:
            job.status = "failed"
            job.error = ("Cloudinary has not finished this edit after "
                         f"{STALE_SECONDS // 60} minutes.")
            job.finished_at = datetime.utcnow()
            failed += 1
            continue
        try:
            state = edits.poll(job.source_public_id, job.transformation)
        except Exception as exc:            # noqa: BLE001 — provider, network
            # Left `building` on purpose. A transient failure here is not an
            # answer about the edit, and writing one down would turn a blip
            # into a job somebody has to re-run.
            job.error = str(exc)[:500]
            still += 1
            continue
        status = state.get("status") or job.status
        if status == "building":
            still += 1
            continue
        job.status = status
        job.error = state.get("error") or None
        if state.get("url"):
            job.result_url = state["url"]
        job.finished_at = job.finished_at or datetime.utcnow()
        if status == "done":
            done += 1
        else:
            failed += 1
    if rows:
        db.session.commit()
    return {"checked": len(rows), "done": done, "failed": failed,
            "building": still}


def ready_for(actor: str) -> list[dict]:
    """Finished edits this person has not been told about.

    Matched on the actor who started the job. An anonymous session -- the
    shared password, which hub/access.py treats as Admin anyway -- has no name
    to match on and sees everything, because the alternative is that the one
    kind of session that cannot be told apart is also the one that never gets
    told anything.
    """
    since = datetime.utcnow() - timedelta(days=NOTICE_DAYS)
    query = (VideoJob.query
             .filter(VideoJob.status.in_(("done", "failed")),
                     VideoJob.seen_at.is_(None),
                     VideoJob.created_at >= since))
    name = (actor or "").strip()
    if name:
        query = query.filter(VideoJob.actor == name)
    rows = query.order_by(VideoJob.id.desc()).limit(10).all()
    return [_notice(r) for r in rows]


def mark_seen(job_ids, actor: str) -> int:
    """Stamp the rows the popup just showed. Returns how many were stamped.

    Stamped only for the person they belong to, so one browser cannot silence
    somebody else's notice -- the ids come from the page, and a page can be
    edited.
    """
    ids = [int(i) for i in (job_ids or []) if str(i).isdigit()]
    if not ids:
        return 0
    query = VideoJob.query.filter(VideoJob.id.in_(ids),
                                  VideoJob.seen_at.is_(None))
    name = (actor or "").strip()
    if name:
        query = query.filter(VideoJob.actor == name)
    rows = query.all()
    for row in rows:
        row.seen_at = datetime.utcnow()
    if rows:
        db.session.commit()
    return len(rows)


TOOL_PAGES = {
    "dead_air": ("/tools/dead-air/", "Dead Air Cutter"),
    "reframe": ("/tools/vertical-reframe/", "Vertical Reframe"),
}


def _notice(job) -> dict:
    """One row as the popup and the dashboard card both want it.

    Shaped here rather than in either of them, because two screens that
    compose the same sentence from the same row are two sentences that come to
    disagree -- and the one that matters is the link, which has to arrive at
    the job rather than at the tool's front page.
    """
    mount, label = TOOL_PAGES.get(job.tool, ("/creative", "Video Tools"))
    stem = str(job.source_public_id or "").rsplit("/", 1)[-1]
    if job.status == "failed":
        headline = f"{label} could not finish {stem}"
    elif job.tool == "reframe":
        ratio = str((job.options or {}).get("ratio") or "9:16")
        headline = f"Your {ratio} cut of {stem} is ready"
    else:
        removed = (job.plan or {}).get("removed")
        cut = f" — {removed:.0f}s of dead air removed" if removed else ""
        headline = f"Your tightened cut of {stem} is ready{cut}"
    return {
        "id": job.id,
        "tool": job.tool,
        "tool_label": label,
        "status": job.status,
        "headline": headline,
        "error": job.error or "",
        "url": f"{mount}?job={job.id}",
        "result_url": job.result_url or "",
        "saved": bool(job.saved_public_id),
        "client_name": job.client_name or "",
        "finished_at": job.finished_at.isoformat() if job.finished_at else "",
    }
