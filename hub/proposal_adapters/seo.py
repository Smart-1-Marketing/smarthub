"""Adapter: ``seo_audit`` -> the client's own real Insites scan, read rather
than imagined; ``seo_workplan`` -> the same findings, prioritized.

``hub/website_audit.py`` already turns a completed scan into real findings
each carrying its own evidence (``opportunities()``) -- the exact shape a
"discovery audit brief" needs, and it is already how Client 360 answers
the identical question for a client we manage. This reads it rather than
asking a model to write a plausible-sounding SEO audit with no site behind
it, which would be the ``brief`` adapter's own failure wearing a research
report.

**Deliberately read-only: this never starts a scan.** A real Insites audit
bills a credit and runs for minutes, which does not fit inside one scheduler
tick, and this graph has no polling state for "come back once an external
job finishes" -- `run_one()`'s only two outcomes for a task that cannot
proceed are requeue (three tries, roughly three minutes apart, since the
scheduler advances one task a minute) or FAILED. Retrying a real scan into
that window would either bill a rep's proposal three times over for scans
that never had time to finish, or fail every audit task whose client
happens not to have been scanned in the last five minutes -- worse than the
brief it replaces. So when nothing has been scanned yet, the task
completes anyway and says so in as many words, naming where a rep runs one:
the honest answer, not an invented one.
"""
from __future__ import annotations

from urllib.parse import quote

from hub.proposal_execution import Adapter, register_adapter


def run(run, task):
    from hub import website_audit

    landing_url = str((run.inputs() or {}).get("landing_url") or "").strip()
    if not landing_url:
        raise ValueError(
            "The SEO + AI discovery audit needs the primary landing URL. "
            "Answer it under · 2 · Shared information needed, then re-run this task.")

    result = website_audit.audit(landing_url)
    artifact_url = f"/client360?q={quote(run.client)}"

    if not result.get("measured"):
        return {
            "summary": (f"No website audit exists yet for {run.client}'s site — "
                        + (result.get("note") or "nothing has been scanned.")),
            "found": False,
            "needs_scan": True,
            "artifact_url": "/scans",
            "qa": ["Run a Site Scan for this client's website, then re-run this task."],
        }

    opps = result.get("opportunities") or []
    age = result.get("age") or {}
    return {
        "summary": (f"Read {run.client}'s most recent site scan ({age.get('note') or 'age not measured'}) "
                    f"— {len(opps)} opportunit{'y' if len(opps) == 1 else 'ies'} found."),
        "found": True,
        "domain": result.get("domain"),
        "score": result.get("score"),
        "scanned_at": result.get("scanned_at"),
        "stale": age.get("stale"),
        "opportunities": opps,
        "spend": result.get("spend"),
        "artifact_url": artifact_url,
        "qa": ["These findings come straight off the last scan — confirm it is still current before quoting from it, "
               "and re-scan first if the age note above says it is stale."],
    }


def workplan_run(run, task):
    """seo_workplan depends on seo_audit and re-reads its real findings --
    the audit already carries everything a workplan needs (a finding, what
    it means and what fixes it), so writing a second, AI-imagined workplan
    on top of it would be the same finding, restated less reliably.
    """
    from hub.proposal_execution import tasks_for_run

    by_key = {t.task_key: t for t in tasks_for_run(run.id)}
    audit_task = by_key.get("seo_audit")
    result = audit_task.result() if audit_task else {}
    if not isinstance(result, dict) or not result.get("found"):
        raise ValueError(
            "The SEO + AI discovery audit has not found a site reading yet. "
            "Run a Site Scan for this client, then approve or re-run that task first.")

    opps = result.get("opportunities") or []
    plan = [{"priority": i + 1, "finding": o.get("finding"), "means": o.get("means"),
            "recommend": o.get("sells")}
            for i, o in enumerate(opps)]

    summary = (f"No gaps were found in {run.client}'s last scan — nothing here needs prioritizing."
              if not plan else
              f"{len(plan)} prioritized action(s) for {run.client}, in the order the audit surfaced them.")
    return {
        "summary": summary,
        "workplan": plan,
        "artifact_url": f"/client360?q={quote(run.client)}",
        "qa": ["Confirm this priority order still matches what the client actually wants fixed first."],
    }


register_adapter(Adapter("seo_scan", "SEO + AI Discovery Audit", "auto", run))
register_adapter(Adapter("seo_workplan", "SEO + AI Prioritized Workplan", "auto", workplan_run))
