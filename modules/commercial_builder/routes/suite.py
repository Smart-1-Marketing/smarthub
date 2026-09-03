"""The finished spot reaches Smart 1 Suite (§2.7 of the parity list).

Radio Promo has been able to do this since it was ported: approve a spot,
press once, and the client's opportunity in the Suite carries the audio, the
script and the voice. The Commercial Builder — which produces the more
expensive deliverable of the two — ended at the client's Cloudinary library
and the Hub's own activity log, so the finished commercial reached the CRM
where the calls, the texts and the pipeline live only if somebody pasted a
link into it by hand.

## Through the Hub's one contact write path, not a second webhook

Radio Promo posts a payload of its own to `GHL_OPPORTUNITY_WEBHOOK_URL`. That
works and it is not what this copies, because `hub/ghl_contacts.py` is
described in this codebase as "one token, one location id and one contact
write path for the whole Hub" and a second raw webhook here would be a third
answer to *how do we reach GoHighLevel*. `hub/suite_opportunity.push_proposal`
already finds the contact, refuses to invent one, discovers the pipeline and
its stage, and answers in three shapes rather than two — so this route
composes the note and lets that function do the talking.

## What may be pushed

**An approved cut, and nothing else.** `RenderApproval` is the record that a
human watched the video; a render that merely succeeded is a file nobody has
seen, which is the distinction `approve_render` already draws and this reads
from the other end. Pushing an unwatched cut would put it in front of the
client through their own CRM.

**A cut that is actually somewhere.** An approval whose Cloudinary write
failed carries `stored_url` empty, and a note naming a provider URL that
expires is worse than a note naming nothing.

## Pushing twice must not open a second opportunity

`cb_suite_deliveries` keeps the `opportunity_id`, and it is handed back to
`upsert_opportunity` on every later press, which updates rather than creates.
Without it, a rep who pushed on Tuesday and again on Thursday leaves two
opportunities for one spot on one client's pipeline with no way to tell which
is current — the duplicate `upsert_from_ghl` learned about from GoHighLevel
first, and the reason `hub/io_records.py` revises a row rather than appending
one.

## A refusal is recorded as a refusal

The row is written whether or not Suite took it. "Nobody has pushed this",
"we pushed it and Suite refused" and "Suite has it" are three states, and the
middle one is the one somebody has to act on — collapsing it into the first
means the button reads as never pressed.
"""

from flask import Blueprint, jsonify, request

from ..db import db
from ..models import (Client, CommercialProject, RenderApproval, RenderJob,
                      SuiteDelivery)

bp = Blueprint("cb_suite", __name__, url_prefix="/api/projects/<int:project_id>")

# Writes on this blueprint that deliberately record nothing, with the reason.
HOUSEKEEPING_ROUTES = {}

try:
    from hub import audit as _hub_audit
    # Bound with the actor rather than passed one per call: `for_module`
    # supplies `actor` itself, so a call site adding `actor=` raises a
    # TypeError inside a swallow — the first trap `hub/audit.py` names. The
    # lambda defers to call time because `_actor` is defined below.
    _cb_log = _hub_audit.for_module("commercial_builder", lambda: _actor())
except Exception:                                        # noqa: BLE001
    def _cb_log(*_a, **_k):
        return None

try:
    from hub import suite_opportunity
except Exception:                                        # noqa: BLE001 — standalone
    suite_opportunity = None


def _actor():
    """Who pressed it. The record's whole value is the name on it.

    Read from the environment the Hub's own guard put there rather than from
    anything the browser sent: a name a request body can carry is a name
    anybody can claim, which is the rule `recordDecision` works to in the
    display ad builder.
    """
    user = request.environ.get("s1hub.user")
    if isinstance(user, dict):
        return user.get("email") or user.get("name") or "Team"
    return str(user or "Team")


def _delivery(project_id):
    return SuiteDelivery.query.filter_by(project_id=project_id).first()


def _approved_cuts(project):
    """Approved cuts that actually have a stored file behind them.

    Returned with the job beside the approval, because the note names the
    format and the length and those live on the job.
    """
    approvals = {a.render_job_id: a for a in
                 RenderApproval.query.filter_by(project_id=project.id).all()}
    out = []
    for job in project.render_jobs.all():
        approval = approvals.get(job.id)
        if approval and (approval.stored_url or job.output_url):
            out.append((job, approval))
    return out


def _note_lines(project, client, cuts):
    lines = [f"Commercial delivered: {project.title or client.name}",
             f"Length: :{project.length_seconds}"]
    if project.platform:
        lines.append(f"Platform: {project.platform}")
    for job, approval in cuts:
        url = approval.stored_url or job.output_url
        lines.append(f"{job.format}: {url}")
    return lines


@bp.get("/suite")
def suite_state(project_id):
    """What the Suite holds for this spot, and whether there is anything to send.

    Four answers rather than two, for the reason `connected_accounts_result()`
    gives one module over: the Suite not being configured, nothing approved
    yet, a push that Suite refused and a spot already filed send somebody to
    four different places, and only one of them is a button to press.
    """
    project = CommercialProject.query.get_or_404(project_id)
    cuts = _approved_cuts(project)
    row = _delivery(project.id)
    configured = bool(suite_opportunity and suite_opportunity.configured())
    problems = ([] if configured else
                (suite_opportunity.status()["problems"] if suite_opportunity
                 else ["Smart 1 Suite is not available in this build."]))
    return jsonify({
        "ok": True,
        "configured": configured,
        "problems": problems,
        "approved_cuts": len(cuts),
        "delivery": row.to_dict() if row else None,
        # Only a button when there is genuinely something to press. A control
        # that can only ever refuse is one people learn to skip past.
        "can_push": bool(configured and cuts),
    })


@bp.post("/suite")
def push_to_suite(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    if suite_opportunity is None:
        return jsonify({"ok": False,
                        "error": "Smart 1 Suite is not available in this build."}), 503

    cuts = _approved_cuts(project)
    if not cuts:
        return jsonify({
            "ok": False,
            "error": "Approve a rendered cut first — the Suite gets the video "
                     "somebody has actually watched, not one that merely "
                     "finished rendering."}), 422

    body = request.get_json(silent=True) or {}
    row = _delivery(project.id)
    result = suite_opportunity.push_proposal(
        client=client.name,
        title=f"{client.name} — {project.title or 'Commercial'}",
        contact=body.get("contact") or {},
        website=client.website or "",
        # The opportunity we already opened for this spot, so a second press
        # revises it rather than opening a second one.
        opportunity_id=(row.opportunity_id if row else "") or "",
        note_lines=_note_lines(project, client, cuts),
        source="Smart 1 Hub — Commercial Builder")

    contact = result.get("contact") or {}
    if row is None:
        row = SuiteDelivery(project_id=project.id)
        db.session.add(row)
    row.opportunity_id = result.get("opportunity_id") or row.opportunity_id
    row.contact_id = contact.get("id") or row.contact_id
    row.contact_name = contact.get("name") or contact.get("email") or row.contact_name
    row.pushed_by = _actor()
    row.cut_count = len(cuts)
    row.ok = bool(result.get("ok"))
    row.reason = "" if result.get("ok") else str(result.get("reason") or "")
    db.session.commit()

    if result.get("ok"):
        # Logged only where it actually landed. A green row over a refusal is
        # the confident wrong answer this corner keeps having to undo.
        _cb_log("commercial_delivered_to_suite", client=client.name,
                detail=f"{len(cuts)} cut(s) — {project.title or 'Commercial'}",
                project=project.id)
        return jsonify({"ok": True, "delivery": row.to_dict(),
                        "contact": contact, "cuts": len(cuts)})

    # `needs_contact` is not an error: the cut is filed and approved either
    # way, and the rep supplies a name and an email a moment later.
    return jsonify({"ok": False, "needs_contact": bool(result.get("needs_contact")),
                    "suggest": result.get("suggest") or {},
                    "delivery": row.to_dict(),
                    "error": result.get("reason") or "Smart 1 Suite refused the push."}), 422
