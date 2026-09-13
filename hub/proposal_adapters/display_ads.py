"""Adapters: ``retargeting_creative`` and ``stadium_banners`` -> a real
build in the Display Ad Builder, the same tool ``/tools/display-ads`` uses.

``hub/ad_builder_link.start_project()`` already is the plain, non-Flask
function this needs: it validates the client against the registry, reads
the landing page, and calls the renderer's own ``POST /api/requests`` --
the exact call the Hub-side "Start a build" form makes. Nothing here is
reimplemented; this is one more caller of an already-real entry point, the
same shape ``modules.utm_builder.app.save_batch()`` and
``modules.social_planner.app.create_batch()`` are for their own adapters.

**Deliberately stops at starting the build.** Finishing one means picking
sizes, generating AI stills and video, and reviewing every rendered frame --
each of those is its own billed provider call and its own judgment about
what looks right, which is why the Display Ad Builder is a wizard a person
drives rather than a button. What is genuinely "real work done" here is the
part that has no judgment in it: the client is validated against the
registry (never a guessed or duplicated prospect for a business already on
file), the landing page is read, and a real project exists in the same
store the renderer's own UI opens. ``execution_mode="approval"``: a person
finishes the build from the link this hands back.

Always ``kind="client"``. A Proposal Execution run only exists because
``create_run()`` was handed an already-known client and a real proposal on
that client's record, so letting ``start_project()`` guess would risk the
one thing its own docstring warns against -- an unknown spelling reads as a
prospect and would file a new lead for a business we already have.

**One shared starter, two labelled callers.** ``retargeting_creative`` and
``stadium_banners`` (the Stadium to Screen "300x250 companion banner brief")
are the same tool doing the same kind of work -- start a project, pick
sizes, review every rendered size -- so the request-building and
error-handling live in ``_start_build()`` once, and each adapter supplies
only what actually differs: the deliverable's own name, so the summary and
QA checklist a reviewer reads say "companion banner" rather than
"retargeting creative" on the task that is not retargeting.
"""
from __future__ import annotations

from urllib.parse import quote

from hub.proposal_execution import Adapter, register_adapter


def _start_build(run, task, *, deliverable: str, default_channel_name: str):
    from hub import ad_builder_link

    inputs = run.inputs() or {}
    landing_url = str(inputs.get("landing_url") or "").strip()
    if not landing_url:
        raise ValueError(
            f"{deliverable[0].upper()}{deliverable[1:]} needs the primary landing "
            "URL. Answer it under · 2 · Shared information needed, then re-run "
            "this task.")

    channel = task.payload().get("channel") or {}
    campaign_name = f"{run.client} — {channel.get('name') or default_channel_name}"
    promoting = str(inputs.get("primary_cta") or "").strip()

    result = ad_builder_link.start_project(
        client_name=run.client, campaign=campaign_name, website=landing_url,
        promoting=promoting, kind="client", proposal_id=str(run.proposal_id or ""),
        actor="proposal-execution")

    if not result.get("ok"):
        # start_project() never raises -- it names the reason a browser-driven
        # start would have shown on the form, and that is exactly what belongs
        # on this task: "not in the client registry", "the renderer is not
        # answering", "ADBUILDER_ADMIN_TOKEN is not set".
        raise ValueError(result.get("error") or "The ad builder could not start this build.")

    request_id = str(result.get("request_id") or "")
    return {
        "summary": (f"Started a real Display Ad Builder build for {run.client}'s "
                    f"{deliverable} -- pick sizes, generate the creative and "
                    "review every rendered size before approving."),
        "request_id": request_id,
        "lead_id": str(result.get("lead_id") or ""),
        "artifact_url": f"/tools/display-ads/build?request={quote(request_id)}",
        "qa": ["Pick the sizes this buy actually needs, generate the creative, and review every size before approving.",
               "Confirm the destination URL and call to action match this proposal.",
               "Nothing here has rendered, generated an image or spent a provider credit -- opening the build is the next step."],
    }


def run(run, task):
    return _start_build(run, task, deliverable="retargeting creative", default_channel_name="Retargeting")


def run_banner(run, task):
    return _start_build(run, task, deliverable="companion banner", default_channel_name="Stadium to Screen")


register_adapter(Adapter("display_ads", "Display Ad Builder", "approval", run))
register_adapter(Adapter("display_ads_banner", "Display Ad Builder — Companion Banner", "approval", run_banner))
