"""Adapter: ``social_calendar`` -> a real month of social posts, built and
drafted by the same tool ``/tools/social`` uses.

``social_calendar`` and ``social_posts`` used to both run as ``brief`` — an
AI-written paragraph describing a content calendar somebody should go build,
never a post anybody could actually approve. This calls
``modules.social_planner.app.create_batch()`` and ``draft_slot()`` — the
route's own extracted writers, the exact shape ``hub/proposal_adapters/utm.py``
already established for ``save_batch()`` — so what lands here is a real batch
in the same store the Social Content Planner reads, findable by the same
client search, with real copy on every non-holiday slot.

**One adapter, one real call, covering both tasks.** A month plan with slots
but no copy is not a deliverable a strategist can review — so unlike
``tracking_plan``, which genuinely is one call, this adapter builds the
calendar *and* drafts every slot in the same run, and ``social_posts``
(which depends on ``social_calendar``) reuses this exact function: the work
is already done by the time it runs, so it re-reads the same batch and
reports the posting checklist rather than repeating the AI spend.

No new shared input is asked of the run: this graph's ``INPUT_CATALOG`` has
nothing that names a channel list or a month, and adding one would newly
block every existing proposal with a social line on two questions nobody
has had to answer before. Instead this reads ``social_plan.DEFAULT_CHANNELS``
-- the module's own default, chosen for the trades and local-service clients
that make up most of the book -- and plans the next calendar month, which is
never in the past relative to today. ``execution_mode="approval"``: this is
AI-drafted copy that reaches a client's own social accounts, never something
that publishes itself.
"""
from __future__ import annotations

from datetime import date
from urllib.parse import quote

from hub.proposal_execution import Adapter, register_adapter


def _next_month(today: date | None = None) -> str:
    today = today or date.today()
    year, month = (today.year, today.month + 1) if today.month < 12 else (today.year + 1, 1)
    return f"{year:04d}-{month:02d}"


def run(run, task):
    from modules.social_planner import app as social_app
    from hub import social_plan
    from hub.client_context import tool_context

    inputs = run.inputs() or {}
    month = _next_month()
    channels = list(social_plan.DEFAULT_CHANNELS)

    context = tool_context(run.client, "")
    industries = [context.get("industry", "")] if context.get("industry") else []
    holidays = social_plan.holidays_for(month, industries)

    promote = []
    primary_cta = str(inputs.get("primary_cta") or "").strip()
    if primary_cta:
        promote.append(primary_cta)
    offer = str(inputs.get("offer") or "").strip()

    batch = social_app.create_batch(
        run.client, month, channels, per_week=3, holidays=holidays,
        brief={"promote": promote, "offers": offer}, use_holidays=bool(holidays),
        url=str(inputs.get("landing_url") or ""), actor="proposal-execution")

    drafted, failed = _draft_all(social_app, batch)

    return {
        "summary": (f"Built a {len(batch['slots'])}-post social calendar for "
                    f"{run.client} ({month}) across {', '.join(social_plan.channel_label(c) for c in channels)} "
                    f"— {drafted} post(s) drafted" + (f", {failed} could not be written" if failed else "") + "."),
        "batch_id": batch["id"],
        "month": month,
        "channels": channels,
        "slots": len(batch["slots"]),
        "drafted": drafted,
        "failed": failed,
        "artifact_url": f"/tools/social?client={quote(run.client)}",
        "qa": ["Read every drafted post before it reaches the client's real social accounts — this is AI-drafted copy.",
               "Confirm the offer and call to action match what this proposal actually sells.",
               "Assign a photo to any post whose channel requires one before scheduling."],
    }


def _draft_all(social_app, batch: dict) -> tuple[int, int]:
    """Draft every non-holiday, non-empty-type slot. One bad slot costs
    only itself -- draft_slot() never raises, the same guarantee the
    browser's own one-request-per-slot loop relies on.
    """
    drafted = failed = 0
    for slot in list(batch["slots"]):
        if slot.get("status") == "approved":
            continue
        _, error, _status = social_app.draft_slot(batch, slot["id"])
        if error:
            failed += 1
        else:
            drafted += 1
    social_app.save_batch(batch)
    return drafted, failed


def _social_posts_run(run, task):
    """social_posts depends on social_calendar, whose own adapter already
    built and drafted the batch -- there is nothing left to write. This
    re-reads that real batch and reports what a strategist still has to do
    (assign photos), rather than spending a second round of AI calls
    re-describing work the calendar task already did for real.
    """
    from modules.social_planner import app as social_app
    from hub import social_plan

    by_key = {t.task_key: t for t in _tasks_for_run(run.id)}
    calendar_task = by_key.get("social_calendar")
    result = calendar_task.result() if calendar_task else {}
    batch_id = result.get("batch_id") if isinstance(result, dict) else None
    batch = social_app.load_batch(batch_id) if batch_id else None
    if not batch:
        raise ValueError("The social calendar has not been built yet. Approve "
                         "or re-run that task first.")

    needs_image = [s["id"] for s in batch["slots"]
                  if not s.get("image_url")
                  and any(social_plan.CHANNELS.get(c, {}).get("asset") == "required"
                          for c in s.get("channels") or [])]
    return {
        "summary": (f"{len(batch['slots'])} post(s) drafted for {run.client}'s "
                    f"{batch.get('month')} calendar — "
                    f"{len(needs_image)} still need a photo before they can go out."),
        "batch_id": batch["id"],
        "needs_image": len(needs_image),
        "artifact_url": f"/tools/social?client={quote(run.client)}",
        "qa": ["Assign a photo to every post whose channel requires one.",
               "Read the drafted copy once more before scheduling — a second read catches what the first missed."],
    }


def _tasks_for_run(run_id):
    from hub.proposal_execution import tasks_for_run
    return tasks_for_run(run_id)


register_adapter(Adapter("social_plan", "Social Content Calendar", "approval", run))
register_adapter(Adapter("social_plan_posts", "Social Post Checklist", "approval", _social_posts_run))
