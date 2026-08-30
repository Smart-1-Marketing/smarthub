"""Background jobs, run once across all workers.

## The problem this has to solve first

Render runs gunicorn with two workers. A naive `threading.Timer` in each one
means every job runs twice: two nightly backups, two sets of quota alerts,
two attempts to clear the same stuck scans. Worse, it's intermittent — the
duplicates interleave differently each boot, so it looks like flakiness rather
than a design fault.

So exactly one worker holds a **leader lock** and runs jobs; the others idle.
On Postgres that's `pg_try_advisory_lock`, which is held for the life of the
connection and released automatically if that worker dies — a crashed leader
doesn't wedge the schedule. On SQLite (local development) leadership falls
back to a lock file, which is fine for a single process.

## Why not APScheduler

It would work, but it's another dependency and its own persistence model,
and this needs maybe eighty lines. The standing preference on this project is
no new Python dependencies unless unavoidable.

## Job contract

Jobs are plain functions returning a short dict summary. They must be safe to
run late, safe to skip, and safe to run twice — the leader lock makes double
runs unlikely, not impossible (a worker can lose and regain leadership across
a restart mid-job). Anything that must never double-run needs its own guard.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone

_LOCK_KEY = 918_273_645          # arbitrary, must be stable across deploys
_thread: threading.Thread | None = None
_started = False
_is_leader = False
_lock_conn = None
_state: dict[str, dict] = {}
_state_lock = threading.Lock()

TICK_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enabled() -> bool:
    """Off by default in development, on in production.

    A scheduler that starts during a local test run will happily fire real
    outbound jobs against real client systems. Opt-in is the safe default.
    """
    v = (os.environ.get("HUB_SCHEDULER") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return os.environ.get("RENDER") is not None


# ---------------------------------------------------------------------------
# Leadership
# ---------------------------------------------------------------------------

LOCK_STALE_SECONDS = 300


def _lock_path() -> str:
    """Where the leader lock lives.

    The fallback used to be `"."` -- the *current working directory*, which is
    a fourth spelling of the data root and the only one that depends on where
    somebody happened to start the process. data_root() is the answer, and it
    is the same file on Render.
    """
    try:
        from . import jsonstore
        base = jsonstore.data_root()
    except Exception:  # noqa: BLE001 — leadership must not fail to resolve
        base = "/var/data" if os.path.isdir("/var/data") else "."
    return os.path.join(base, "hub-scheduler.lock")


def _claim_file_lock() -> bool:
    """Leadership without Postgres, for local development.

    The first version leaked: it wrote a lock file and, if one already
    existed, called os.replace(path, path) — which does nothing. So after any
    restart the file persisted and NO worker ever became leader again. A
    scheduler that silently stops scheduling is the worst kind of broken,
    because everything still looks fine.

    Now the holder heartbeats the file, and a lock older than the stale window
    is taken over. The window is deliberately short: running a job twice is
    recoverable, never running it again is not.
    """
    path = _lock_path()
    mine = str(os.getpid())
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, mine.encode())
        os.close(fd)
        return True
    except FileExistsError:
        pass
    except OSError:
        return False

    try:
        age = time.time() - os.path.getmtime(path)
        with open(path, encoding="utf-8") as fh:
            holder = fh.read().strip()
    except OSError:
        return False

    if holder == mine:
        return True                       # already ours
    if age <= LOCK_STALE_SECONDS:
        return False                      # someone else is alive and leading

    # Stale. Claim it by writing our pid, then re-read to settle any race —
    # last writer wins and everyone else stands down.
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(mine)
        time.sleep(0.05)
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() == mine
    except OSError:
        return False


def _heartbeat() -> None:
    """Keep the file lock fresh so another worker doesn't steal it."""
    if _is_leader and not _lock_conn:
        try:
            os.utime(_lock_path(), None)
        except OSError:
            pass


def _claim_leadership(app) -> bool:
    """Try to become the one worker that runs jobs."""
    global _lock_conn, _is_leader
    try:
        from hub.extensions import db
        with app.app_context():
            engine = db.engine
            if not engine.dialect.name.startswith("postgres"):
                # Single-process development: a lock file is enough.
                _is_leader = _claim_file_lock()
                return _is_leader

            from sqlalchemy import text
            conn = engine.connect()
            got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                               {"k": _LOCK_KEY}).scalar()
            if got:
                # Hold the connection open: the lock lives as long as it does,
                # and is released by Postgres if this worker dies.
                _lock_conn = conn
                _is_leader = True
            else:
                conn.close()
                _is_leader = False
    except Exception:                                   # noqa: BLE001
        _is_leader = False
    return _is_leader


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

def job_clear_stuck_scans(app) -> dict:
    """Resolve or error any scan running longer than the grace window.

    An Insites audit takes one to four minutes. Anything past thirty is
    stalled, and a stalled scan is worse than a failed one because it reads as
    work in progress and nobody investigates it.
    """
    grace = int(os.environ.get("SCANS_STUCK_MINUTES") or 30)
    try:
        from modules.scans.app import Scan, SessionLocal, _try_immediate_fetch
    except Exception as exc:                            # noqa: BLE001
        return {"skipped": f"scans unavailable ({type(exc).__name__})"}

    cutoff = _now() - timedelta(minutes=grace)
    resolved = errored = 0
    db = SessionLocal()
    try:
        for s in db.query(Scan).filter(Scan.status == "running").all():
            created = s.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created and created > cutoff:
                continue
            if s.insites_report_id:
                # Give Insites one more chance before writing it off.
                try:
                    _try_immediate_fetch(db, s)
                    if s.status == "complete":
                        resolved += 1
                        continue
                except Exception:                       # noqa: BLE001
                    pass
            s.status = "error"
            s.error_message = (
                f"Stopped automatically after {grace} minutes. An Insites "
                f"audit normally takes one to four. Re-run it if you still "
                f"need the report.")
            errored += 1
        db.commit()
    finally:
        db.close()
    return {"resolved": resolved, "errored": errored, "grace_minutes": grace}


def job_rotate_audit_log(app) -> dict:
    """Stop the activity log filling the disk that also holds uploads."""
    from hub import audit
    return {"rotated": bool(audit.rotate(max_mb=64, keep=5))}


def job_quota_warnings(app) -> dict:
    """Record any provider past its monthly warning mark.

    Written to the activity log rather than emailed: the Hub has no mail
    sender, and a warning nobody can deliver is better recorded than lost.
    """
    from hub import audit, quotas
    warns = quotas.warnings()
    for w in warns:
        audit.log("quota", "threshold", provider=w["key"], state=w["state"],
                  used=w["used"], limit=w["limit"], message=w["message"])
    return {"warnings": len(warns),
            "providers": [w["key"] for w in warns]}


def job_refresh_invoice_links(app) -> dict:
    """Cache the public QuickBooks invoice links clients can open without a
    login. Runs three times a day so a newly issued invoice is shareable the
    same working day."""
    try:
        from hub import quickbooks as qb
    except Exception as exc:                            # noqa: BLE001
        return {"skipped": f"quickbooks unavailable ({type(exc).__name__})"}
    fn = getattr(qb, "refresh_public_links", None)
    if not callable(fn):
        return {"skipped": "no refresh_public_links() in hub.quickbooks yet"}
    try:
        return fn()
    except Exception as exc:                            # noqa: BLE001
        return {"error": type(exc).__name__}


def job_refresh_knack_products(app) -> dict:
    """Re-pull IO products from Knack.

    These were served from a hand-made JSON export that nothing refreshed, so
    Client 360 showed whatever was true on the day of the last export. Three
    hours is frequent enough that a product going live today is visible today,
    without hammering the API.
    """
    try:
        from hub import knack_products
    except Exception as exc:                            # noqa: BLE001
        return {"skipped": f"unavailable ({type(exc).__name__})"}
    return knack_products.refresh()


def job_backup_json(app) -> dict:
    """Mirror the durable JSON on the disk into the database.

    Every module that has moved onto ``hub.jsonstore`` already mirrors on each
    write, so on a healthy Hub this sweep finds nothing to do. It is here for
    the two cases where per-write mirroring is not enough:

      * a module still writing its own ``json.dump`` — covered from the next
        sweep rather than from whenever somebody gets round to editing it;
      * a write whose mirror was skipped because the database was asleep and
        the circuit breaker was open — otherwise that file stays stale in the
        backup until the next time a person happens to save it.

    Hourly. The work is proportional to what has *changed* since the last pass,
    not to the size of the disk, because each file's mtime is checked against
    when it was last mirrored.
    """
    from hub import jsonstore
    return jsonstore.sweep()


# name -> (every N minutes, function, human description)
def job_refresh_google_index(app) -> dict:
    """Rebuild the Google account index — the one place the sweep happens.

    This job is why the sweep is affordable at all. It runs under the leader
    lock, so exactly one worker talks to Google: the Tag Manager pacing in
    google_finder is a per-process timer, and two workers sweeping meant two
    independent pacers against a limit Google applies per user. It also means
    no page ever pays for the sweep — Client 360 and the tool lookups read the
    stored index, which is a dictionary scan.

    Three hours, offset from the Knack pull so the two are not competing for
    the same worker: a property created this morning is findable this
    afternoon. Eight sweeps a day is not free, and the figure is worth having
    rather than assuming — this login carries 180 Tag Manager accounts, so a
    clean sweep is a little over 180 requests and eight of them are ~1,500
    against a daily project quota of 10,000. The margin is in the retries: at
    two and a half requests per account, which is what a fixed pace was
    costing, the same eight sweeps are ~3,600.
    """
    try:
        from hub import google_index
    except Exception as exc:                            # noqa: BLE001
        return {"skipped": f"google_index unavailable ({type(exc).__name__})"}

    # Every job starts due, so a redeploy re-ran this one however recently it
    # had finished — and this one is 180 rate-limited Tag Manager calls and
    # seven minutes. On a day of three deploys that is three extra sweeps of
    # the same accounts, each one hammering the per-user limit the last had
    # just annoyed, for no information the index did not already hold. Half
    # the interval: a genuine three-hourly tick always clears it, a restart
    # minutes after a good sweep never does, and the skip is reported with
    # the age rather than passed off as a run.
    every, _fn, _desc = JOBS["google_index"]
    min_age = every * 60 * 0.5
    if not google_index.due_for_refresh(min_age):
        age = google_index.age_seconds()
        return {"skipped": (f"The index was rebuilt {round((age or 0) / 60)} "
                            f"minutes ago; the sweep is expensive and nothing "
                            f"is due yet."),
                "age_seconds": round(age or 0)}
    try:
        return google_index.build(force=True)
    except Exception as exc:                            # noqa: BLE001
        # A Google outage must not take the scheduler down with it; the stored
        # index simply ages, and status() reports it as stale rather than
        # letting a page believe it is current.
        return {"ok": False, "error": type(exc).__name__}


def job_refresh_purchased_domains(app) -> dict:
    """Re-pull the two sources behind /tools/domains, once a night.

    The page used to pull object_153 in full on every visit to answer a
    question whose answer changes when somebody buys a domain. This ticks
    hourly and the module decides: `refresh(force=False)` returns without
    touching Knack unless the nightly window has passed, so a leader that
    restarted through that window picks the pull up on its next tick rather
    than skipping a day in silence.

    The QuickBooks renewal charges ride on the same tick, because "was this
    billed?" is read off them and a page whose two halves are pulled on
    different schedules reports one age for an answer that came from the
    other.
    """
    try:
        from hub import domain_purchase
    except Exception as exc:                            # noqa: BLE001
        return {"skipped": f"unavailable ({type(exc).__name__})"}
    try:
        out = domain_purchase.refresh(force=False)
    except Exception as exc:                            # noqa: BLE001
        # A Knack outage must not take the scheduler down with it. The stored
        # snapshot simply ages, and the page says how old it is.
        out = {"ok": False, "error": type(exc).__name__}

    # The billed column comes from QuickBooks and is cached the same way, so
    # it is pulled on the same night. Only when the registry pull actually
    # ran: `refresh(force=False)` returns "not due yet" on every other tick,
    # and re-reading a year of invoices hourly for an answer that moves once a
    # month is the per-visit pull wearing a schedule.
    if out.get("ok") and not out.get("skipped"):
        try:
            import datetime as _dt

            from hub import domain_renewals
            qb = domain_renewals.charges(_dt.date.today().year, refresh=True)
            # Named, never counted as a failure of this job: the registry half
            # succeeded, and reporting the whole tick as failed would hide it.
            out["quickbooks"] = {"charges": len(qb.get("lines") or []),
                                 "error": qb.get("error") or ""}
        except Exception as exc:                        # noqa: BLE001
            out["quickbooks"] = {"charges": 0,
                                 "error": f"{type(exc).__name__}: {exc}"}
    return out


def job_index_video_backlog(app) -> dict:
    """Describe another bounded batch of the video background library.

    The library is a back catalogue of a few thousand clips carrying no usable
    filenames, and each one costs a vision call to describe, so this works
    through it rather than doing it in one go: twenty clips an hour, with a
    four-minute wall-clock budget so a slow provider cannot hold up every job
    behind it on this one thread.

    Safe to run late, skip and repeat, as the job contract requires. Progress
    lives on the assets themselves as Cloudinary tags rather than in a cursor
    here, so a missed hour costs an hour and a double run re-reads a handful of
    already-tagged clips and describes none of them twice.
    """
    try:
        from hub import video_library
    except Exception as exc:                            # noqa: BLE001
        return {"skipped": f"unavailable ({type(exc).__name__})"}
    if not video_library.can_index():
        # Not an error and not silence: an unconfigured Hub would otherwise
        # write an identical failure into the activity log every hour for ever,
        # which is the noise hub/google_index.py had to learn to stop making.
        return {"skipped": "CLOUDINARY_URL or OPENAI_API_KEY is not set"}
    try:
        return video_library.index_backlog(actor="scheduler")
    except Exception as exc:                            # noqa: BLE001
        # A provider outage must not take the scheduler down with it. The
        # untagged clips simply come back next hour.
        return {"ok": False, "error": type(exc).__name__}


def job_describe_client_uploads(app) -> dict:
    """Describe another batch of the photographs clients have sent us.

    The same shape as the video sweep above and for the same reason: a client
    who uploads forty photographs is never going to type forty descriptions,
    and without one the gallery is forty thumbnails nobody can search. Bounded
    by a count *and* a wall clock, because scheduler jobs share one thread and
    a vision call has no useful ceiling on how long it takes.
    """
    try:
        from modules.image_picker import vision
    except Exception as exc:                            # noqa: BLE001
        return {"skipped": f"unavailable ({type(exc).__name__})"}
    if not vision.can_describe():
        # Not an error and not silence, per job_index_video_backlog above.
        return {"skipped": "OPENAI_API_KEY is not set"}
    try:
        return vision.describe_backlog(actor="scheduler")
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": type(exc).__name__}


def job_social_idea_batches(app) -> dict:
    """Offer another week's ideas to the clients who are actually swiping.

    `ideas.generate()` was reachable only from a button in the staff queue, so
    a client who opened their swipe link saw "Nothing to look at just yet" —
    for ever, unless a strategist had remembered that week. A link nobody has
    a reason to open is a link nobody opens, and this one is the client's own.

    Hourly ticks, weekly per client: the interval is decided inside
    `ideas.sweep()` from each client's own last sweep rather than from this
    job's schedule, so a redeploy cannot offer two batches in a day and a
    leader that restarted through the window picks the week up rather than
    skipping it in silence. Same shape as `purchased_domains`, for the same
    reason.

    Bounded on both axes — eight clients and three minutes — because these are
    model calls, they share this one thread, and a call has no useful ceiling.
    Whatever is left is simply due again next hour.

    Safe to run late, skip and repeat, as the job contract requires: a client
    is marked swept when they are offered a batch, so a double run generates
    nothing twice.
    """
    try:
        from modules.social_planner import ideas
    except Exception as exc:                            # noqa: BLE001
        return {"skipped": f"unavailable ({type(exc).__name__})"}
    with app.app_context():
        # An app context, because the client lookup this enriches a batch with
        # reads the shared engine — the flask.g trap that had the Google sweep
        # reporting an empty book from a background thread.
        result = ideas.sweep(actor="scheduler")
    if result.get("ok") and not result.get("generated"):
        # Nothing due is a *state*, not a failure, and an unconfigured Hub
        # would otherwise write an identical line into the activity log every
        # hour for ever — the noise hub/google_index.py had to learn to stop
        # making.
        return {"skipped": f"no client due ({result.get('clients', 0)} on file)"}
    return result


def job_verify_llms_txt(app) -> dict:
    """Re-check every published client llms.txt overnight.

    The redirect that carries this lives on the *client's* website, in a
    builder we do not control, and the file it points at is on our host behind
    a robots.txt and an X-Robots-Tag that other work here edits. Every one of
    those can be broken by somebody who has never heard of this feature, and
    the failure is completely silent: the Hub goes on reporting a clean
    publish while a crawler reads a 404, a login page or a refusal.

    So the only thing that makes this feature real is asking. It reaches the
    client's site and two robots.txt files per client, which is why it is a
    nightly job and a button rather than anything a page load does.

    Bounded, and only over the clients with something published: a client with
    no live file has nothing that can have broken, and walking the whole book
    would spend four outbound requests each on several hundred businesses to
    learn nothing.

    Safe to run late, skip and repeat, as the job contract requires: it writes
    each client's own last result and nothing else.
    """
    try:
        from . import llms_hosting
    except Exception as exc:                            # noqa: BLE001
        return {"skipped": f"unavailable ({type(exc).__name__})"}
    with app.app_context():
        # An app context: the client's domain is read through
        # hub/client_context.py, which reaches the shared engine -- the
        # flask.g trap that had the Google sweep reporting an empty book from
        # a background thread.
        result = llms_hosting.sweep(actor="scheduler")
    if not result.get("checked"):
        # Nothing published is a *state*, not a failure. Logged as a skip so
        # an unconfigured Hub does not write an identical line every night for
        # ever -- the noise hub/google_index.py had to learn to stop making.
        return {"skipped": "no client has a published llms.txt"}
    return result


JOBS = {
    "backup_json":       (60, job_backup_json,
                          "Mirror disk JSON into the database backup."),
    "clear_stuck_scans": (15, job_clear_stuck_scans,
                          "Resolve or error scans running past 30 minutes."),
    "rotate_audit_log":  (720, job_rotate_audit_log,
                          "Roll the activity log before it fills the disk."),
    "quota_warnings":    (240, job_quota_warnings,
                          "Record providers past their monthly warning mark."),
    "knack_products":    (180, job_refresh_knack_products,
                          "Re-pull IO products and campaigns from Knack."),
    "invoice_links":     (480, job_refresh_invoice_links,
                          "Refresh public QuickBooks invoice links (3x daily)."),
    "google_index":      (180, job_refresh_google_index,
                          "Re-sweep Google and re-join every account to a client."),
    "purchased_domains": (60, job_refresh_purchased_domains,
                          "Re-pull the purchased-domain registry once a night."),
    "video_backlog":     (60, job_index_video_backlog,
                          "Describe another batch of the video background library."),
    "picker_describe":   (60, job_describe_client_uploads,
                          "Describe another batch of the photos clients sent us."),
    "social_ideas":      (60, job_social_idea_batches,
                          "Offer another week's ideas to clients who are swiping."),
    "llms_verify":       (720, job_verify_llms_txt,
                          "Re-check every published client llms.txt end to end."),
}


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------

def _run_job(app, name: str) -> None:
    every, fn, _ = JOBS[name]
    started = time.time()
    try:
        result = fn(app) or {}
        ok, err = True, ""
    except Exception as exc:                            # noqa: BLE001
        result, ok, err = {}, False, f"{type(exc).__name__}: {exc}"
    ms = int((time.time() - started) * 1000)
    with _state_lock:
        # Carried forward rather than overwritten wholesale, because two of
        # these answer questions a single latest-run snapshot cannot.
        #
        # `fails` is the consecutive run of failures: a job that has failed
        # fourteen times running and one that blipped once just now are
        # different situations, and overwriting the row made them render
        # identically. `last_ok` is when it last actually worked, so a job
        # that is failing can still say how long it has been broken rather
        # than only that it is.
        prev = _state.get(name) or {}
        _state[name] = {
            "last_run": _now().isoformat(timespec="seconds"),
            "ok": ok, "ms": ms, "result": result, "error": err,
            "fails": 0 if ok else int(prev.get("fails") or 0) + 1,
            "last_ok": (_now().isoformat(timespec="seconds") if ok
                        else prev.get("last_ok")),
        }
    try:
        from hub import audit
        audit.log("scheduler", "job", job=name, ok=ok, ms=ms,
                  result=str(result)[:200], error=err[:200] or None)
    except Exception:                                   # noqa: BLE001
        pass


def _loop(app) -> None:
    # Stagger the first pass so a redeploy doesn't fire everything at once.
    time.sleep(20)
    if not _claim_leadership(app):
        return                                          # another worker leads
    due = {name: 0.0 for name in JOBS}
    while True:
        now = time.time()
        _heartbeat()
        for name, (every, _fn, _desc) in JOBS.items():
            if now >= due[name]:
                _run_job(app, name)
                due[name] = now + every * 60
        time.sleep(TICK_SECONDS)


def start(app) -> bool:
    """Start the scheduler thread. Safe to call more than once."""
    global _thread, _started
    if _started or not enabled():
        return False
    _started = True
    _thread = threading.Thread(target=_loop, args=(app,), daemon=True,
                               name="s1hub-scheduler")
    _thread.start()
    return True


def _leader_exists(app) -> bool:
    """Is some worker holding leadership right now?

    A follower's thread exits on purpose (``_loop`` returns the moment it
    loses the election), so "my thread isn't alive" says nothing about whether
    jobs are being run — and Diagnostics was reporting that as a red **down**
    on whichever worker happened to serve the page. Half the time that is a
    healthy scheduler being called broken, which also means a genuinely dead
    one looks identical and gets ignored.

    Probing the lock is the honest answer. If we cannot take it, someone else
    holds it and the jobs are covered. If we can, nobody is leading — so we
    release it immediately and report the fault.
    """
    if _is_leader:
        return True
    try:
        from hub.extensions import db
        with app.app_context():
            engine = db.engine
            if not engine.dialect.name.startswith("postgres"):
                try:                       # local: a fresh lock file means alive
                    return (time.time() - os.path.getmtime(_lock_path())
                            ) <= LOCK_STALE_SECONDS
                except OSError:
                    return False
            from sqlalchemy import text
            with engine.connect() as conn:
                got = conn.execute(text("SELECT pg_try_advisory_lock(:k)"),
                                   {"k": _LOCK_KEY}).scalar()
                if got:
                    conn.execute(text("SELECT pg_advisory_unlock(:k)"),
                                 {"k": _LOCK_KEY})
                return not got
    except Exception:                                   # noqa: BLE001
        return False


# How far past its interval a job may run before it is called overdue. Jobs
# share one thread, so a slow one legitimately pushes the next along; two
# intervals plus a five-minute floor is late enough to mean something and
# loose enough not to cry wolf on a job that ticks every minute.
OVERDUE_FACTOR = 2.0
OVERDUE_FLOOR_MINUTES = 5.0


def _overdue(row: dict, every_minutes: float, visible: bool):
    """(overdue, minutes late) — or (None, None) when it cannot be known.

    The gap this closes: the panel drew a green pill for any job whose last
    run succeeded, however long ago that was. A job whose interval is an hour
    and which last ran three days ago read as healthy, and with eleven jobs
    sharing one thread the reader would have had to do that arithmetic eleven
    times. A loop stuck on one long job stops every job behind it and nothing
    on the page said so.

    `None` rather than `False` where it is unknowable — on the standby worker,
    or before a job's first run this boot. A confident "not overdue" about a
    job whose timings this process cannot see is exactly the wrong answer.
    """
    if not visible:
        return None, None
    last = str(row.get("last_run") or "")
    if not last:
        # Never run *in this process*. On the leader that is honest — it has
        # not come round yet — and it is not overdue, because there is no
        # previous run to be late relative to.
        return None, None
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        return None, None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    late = (_now() - when).total_seconds() / 60.0
    allowed = max(float(every_minutes) * OVERDUE_FACTOR, OVERDUE_FLOOR_MINUTES)
    return (late > allowed), round(late, 1)


def status(app=None) -> dict:
    """What the scheduler is doing, for Diagnostics."""
    with _state_lock:
        runs = dict(_state)
    alive = bool(_thread and _thread.is_alive())
    if not enabled():
        state = "off"
    elif _is_leader and alive:
        state = "leading"
    elif app is not None and _leader_exists(app):
        state = "standby"          # healthy: the other worker has the jobs
    else:
        state = "down"             # nobody is running jobs — a real fault
    # Whether this process can *see* the job timings at all. `_state` is
    # per-process and in memory, so the standby worker holds nothing — and
    # with two gunicorn workers that is half of all page loads. The panel used
    # to render every job there as "Not run yet this boot" behind a grey pill,
    # which is indistinguishable from a scheduler that has genuinely never
    # run: the same page said two different things depending on which worker
    # answered, and one of them was alarming and wrong.
    #
    # Absent is not zero. On standby the timings are *not measured here*, and
    # that is said rather than drawn as a row of nevers.
    visible = state == "leading"

    jobs = []
    for name, (every, _fn, desc) in JOBS.items():
        row = {"name": name, "every_minutes": every, "description": desc,
               "visible": visible, "overdue": None, "overdue_by": None,
               "fails": 0, "last_ok": None,
               **runs.get(name, {"last_run": None, "ok": None})}
        row["overdue"], row["overdue_by"] = _overdue(row, every, visible)
        jobs.append(row)

    return {
        "enabled": enabled(),
        "running": alive,
        "is_leader": _is_leader,
        "state": state,
        "timings_visible": visible,
        # Counted here rather than in the browser so the page and anything
        # else reading this API cannot disagree about what "a problem" is.
        "failing": sum(1 for j in jobs if j.get("ok") is False),
        "overdue": sum(1 for j in jobs if j.get("overdue")),
        "jobs": jobs,
        "note": {
            "off": "Disabled. Set HUB_SCHEDULER=true to turn the jobs on.",
            "leading": "This worker holds the lock and is running the jobs.",
            "standby": "The other worker holds the lock and is running the "
                       "jobs. Exactly one does, by design — nothing is wrong.",
            "down": "No worker holds the scheduler lock, so no background job "
                    "is running. Redeploy; if it persists, check the database "
                    "connection the lock depends on.",
        }[state],
    }


def run_now(name: str, app) -> dict:
    """Run one job immediately, for the button in Diagnostics."""
    if name not in JOBS:
        return {"error": "No such job."}
    _run_job(app, name)
    with _state_lock:
        return _state.get(name, {})
