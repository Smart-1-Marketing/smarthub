"""Quality checks + Creatomate rendering (spec sections 12-13)."""

from flask import Blueprint, jsonify, request

from ..config import OUTPUT_FORMATS, build_sort_key
from ..db import db
from ..models import (Client, CommercialProject, RenderApproval, RenderJob,
                       Scene)
from ..services import qc_service, creatomate_service, cloudinary_service

bp = Blueprint("cb_render", __name__, url_prefix="/api/projects/<int:project_id>")

# A rendered commercial is a deliverable, so it belongs on the client's 360
# record alongside their images, quotes and scans. Guarded so the module still
# runs standalone.
try:
    from hub import audit as _hub_audit
    _cb_log = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001
    def _cb_log(*_a, **_k):
        return None


@bp.post("/qc")
def run_qc(project_id):
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    scenes = [s.to_dict() for s in project.scenes.order_by(Scene.order_index).all()]
    results = qc_service.run_qc(project.to_dict(include_scenes=False), client.to_dict(), scenes)
    project.qc_results = results
    project.status = "qc"
    db.session.commit()
    return jsonify({"ok": True, "qc_results": results})


@bp.post("/render")
def submit_render(project_id):
    """Renders one job per requested output format (spec section 15: one
    commercial can fan out to 16:9 / 9:16 / 1:1 from the same storyboard)."""
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(force=True) or {}
    # One size at a time, on purpose.
    #
    # This used to loop every ticked format and fire them all at once. Three
    # renders in flight is three ways to be wrong before anybody has looked at
    # one: the second and third are built from the same storyboard, so if the
    # first comes back with a scene that reads badly at 9:16 or a CTA that
    # crops, the other two have already been paid for and carry it too. Render
    # one, watch it, approve it or change something — then the next.
    requested = data.get("formats")
    if requested is None:
        one = data.get("format")
        requested = [one] if one else (project.formats or ["16:9"])[:1]
    requested = [f for f in requested if f]
    if len(requested) != 1:
        return jsonify({"ok": False, "error": (
            "Render one size at a time. Pick a single format — once it comes "
            "back and you've approved it, the others render from the same "
            "storyboard.")}), 400

    fmt = requested[0]
    if fmt not in {f["id"] for f in OUTPUT_FORMATS}:
        return jsonify({"ok": False, "error": f"{fmt} is not an output format."}), 400
    formats = [fmt]
    force = data.get("force_despite_qc_failures", False)

    scenes = [s.to_dict() for s in project.scenes.order_by(Scene.order_index).all()]
    qc = qc_service.run_qc(project.to_dict(include_scenes=False), client.to_dict(), scenes)
    project.qc_results = qc
    if not qc["_all_passed"] and not force:
        db.session.commit()
        return jsonify({"ok": False, "error": "QC checks failed. Fix the flagged items or "
                                               "resubmit with force_despite_qc_failures=true.",
                         "qc_results": qc}), 409

    voice_track_url = (project.music or {}).get("voice_track_url")
    music_track_url = (project.music or {}).get("music_track_url")

    jobs = []
    for fmt in formats:
        source = creatomate_service.build_source(project.to_dict(include_scenes=False), scenes, fmt,
                                                   voice_track_url, music_track_url)
        result = creatomate_service.submit_render(source)
        job = RenderJob(project_id=project.id, format=fmt, provider_render_id=result.get("id"),
                         status=result.get("status", "queued"), output_url=result.get("url"),
                         error=result.get("error"))
        db.session.add(job)
        jobs.append(job)

    project.status = "rendering"
    db.session.commit()

    _log_render(project, client, formats)
    return jsonify({"ok": True, "render_jobs": [j.to_dict() for j in jobs],
                    "live": creatomate_service.is_live(),
                    # Mock mode returns a job id and no file. Saying so here is
                    # what stops the panel reporting "succeeded" over nothing.
                    "note": ("" if creatomate_service.is_live() else
                             "No CREATOMATE_API_KEY is set, so these jobs are mock: "
                             "they will report success and no file will exist.")})


def _log_render(project, client, formats):
    """Record the render on the client's 360 record. Never raises.

    This call is why no commercial has ever rendered. It read `project.name`
    and `project.length` — attributes `CommercialProject` does not have; it has
    `title` and `length_seconds` — so it raised AttributeError at the very top
    of the route, before the QC gate, before Creatomate, before anything. The
    500 came back as HTML, `CB.api` failed to parse it as JSON and toasted
    "Bad response from server" for three seconds, and the render panel stayed
    empty. Press Render, nothing happens, no trace anywhere.

    Two things follow, and the second is the reason this is a function.
    `hub/audit.py` swallows what it is given — but the f-string was evaluated
    by the caller *before* the logger could swallow anything, so the guard that
    was supposed to make logging safe never saw it. Building the detail inside
    the try is what actually makes it safe. And it runs after the render is
    committed rather than before the QC gate, so a spot refused by QC is no
    longer logged as submitted work.
    """
    try:
        _cb_log("render_submitted", client=client.name,
                detail=f"{project.title or 'Commercial'} · "
                       f":{project.length_seconds:02d} · {', '.join(formats)}",
                project=project.id)
    except Exception:  # noqa: BLE001 — a log must never cost a render
        pass


@bp.get("/render-jobs")
def list_render_jobs(project_id):
    """Every render for this project, with its approval alongside it.

    The approval travels with the job rather than in a second call: the panel
    draws "rendered" and "approved" as different states, and two fetches is two
    chances for it to draw one while the other is still in flight.
    """
    project = CommercialProject.query.get_or_404(project_id)
    jobs = project.render_jobs.all()
    approvals = {a.render_job_id: a.to_dict() for a in
                 RenderApproval.query.filter_by(project_id=project.id).all()}
    rows = []
    for job in jobs:
        row = job.to_dict()
        row["approval"] = approvals.get(job.id)
        rows.append(row)
    approved_formats = sorted({j.format for j in jobs if j.id in approvals})
    return jsonify({"ok": True, "render_jobs": rows,
                    "approved_formats": approved_formats,
                    "requested_formats": project.formats or [],
                    "remaining_formats": [f for f in (project.formats or [])
                                          if f not in approved_formats],
                    "live": creatomate_service.is_live()})


@bp.get("/render-jobs/<int:job_id>/status")
def check_render_job(project_id, job_id):
    job = RenderJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    if job.status not in ("succeeded", "failed"):
        status = creatomate_service.check_render(job.provider_render_id)
        job.status = status.get("status", job.status)
        job.output_url = status.get("url") or job.output_url
        job.error = status.get("error")
        db.session.commit()

        # Deliberately no filing here any more. This used to copy the finished
        # video into the client's library the moment Creatomate said
        # "succeeded" — before anybody had watched it. A cut nobody has looked
        # at is not a deliverable, and one already sitting in the client's
        # gallery is one somebody can send. Filing is what Approve does now.
    return jsonify({"ok": True, "render_job": job.to_dict(),
                    "approval": _approval_of(job),
                    "live": creatomate_service.is_live()})


def _approval_of(job):
    approval = RenderApproval.query.filter_by(render_job_id=job.id).first()
    return approval.to_dict() if approval else None


@bp.post("/render-jobs/<int:job_id>/approve")
def approve_render(project_id, job_id):
    """A human says this cut is good — and only then is it filed.

    Two writes, reported separately, for the reason `hub/domain_links.py`
    gives at length: "filed" and "filed in one of two places" are different
    outcomes, and one tick over both is how somebody learns not to trust the
    tick. The video goes into the client's Cloudinary library, and the
    approval goes into the Hub activity log, which is what puts it on the
    client's 360 record.
    """
    job = RenderJob.query.filter_by(id=job_id, project_id=project_id).first_or_404()
    project = CommercialProject.query.get_or_404(project_id)
    client = Client.query.get_or_404(project.client_id)
    data = request.get_json(silent=True) or {}

    if job.status != "succeeded":
        return jsonify({"ok": False, "error": (
            f"This render is {job.status}, so there is nothing to approve yet.")}), 400
    if not job.output_url:
        # Mock mode reports "succeeded" and produces no file. Approving that
        # would file nothing into the client's library and log it as a
        # delivered commercial — a clean tick over an empty gallery.
        return jsonify({"ok": False, "error": (
            "This render reported success but produced no file, so there is "
            "nothing to file. That is what happens with no CREATOMATE_API_KEY "
            "set — the job is a mock.")}), 400

    # What the client said, if they were asked. A cut they explicitly refused
    # must not reach their library and their 360 record: filing is what makes
    # it a deliverable, and "we sent it anyway" is the one outcome the whole
    # review feature exists to make impossible.
    #
    # Only a refusal blocks. "Approved with changes" IS an approval, and
    # blocking it would teach people to answer "approved" to get past the
    # gate — and a project nobody ever sent for review is unchanged, because
    # an internal-only sign-off is still how most of these are built.
    # Which published rules the copy engages, and whether anybody has said out
    # loud that they were looked at. Asked BEFORE the client verdict, because
    # a spot the client loves can still be one nobody checked Reg Z against —
    # and filing is what makes it a deliverable.
    compliance = _compliance_gate(project, client)
    if compliance["blocks_filing"] and not data.get("acknowledge_compliance"):
        return jsonify({"ok": False, "error": compliance["message"],
                        "compliance": compliance,
                        "needs_acknowledgment": True}), 409

    standing = _client_verdict(project)
    if standing["blocks_filing"] and not data.get("override"):
        return jsonify({"ok": False, "error": (
            "The client asked for changes on this spot"
            + (f" ({standing['by']})" if standing["by"] else "")
            + ", so it is not filed. Make the changes and send them a new "
              "round, or file it anyway if you have settled it with them "
              "another way."),
            "client_verdict": standing, "can_override": True}), 409

    existing = RenderApproval.query.filter_by(render_job_id=job.id).first()
    if existing:
        return jsonify({"ok": True, "approval": existing.to_dict(),
                        "already": True, "next": _next_in_campaign(project)})

    approval = RenderApproval(render_job_id=job.id, project_id=project.id,
                              approved_by=_actor())

    stored = cloudinary_service.upload_asset(
        job.output_url, client.slug, "commercial",
        public_id=f"project-{project_id}-{job.format.replace(':', 'x')}")
    approval.stored_url = stored.get("secure_url") or ""
    approval.stored_public_id = stored.get("public_id") or ""
    if not approval.stored_url:
        # Named, not swallowed. A commercial approved and not stored is one
        # whose only copy is a Creatomate URL that expires.
        approval.filing_error = (stored.get("error")
                                 or "The video could not be copied into the client's library.")

    try:
        # An override is named in the log rather than filed as an ordinary
        # approval. Somebody decided to ship a cut the client had refused, and
        # a record that does not say so is one nobody can reconstruct later —
        # which is the argument this whole review feature exists to settle.
        detail = (f"{project.title or 'Commercial'} · "
                  f":{project.length_seconds:02d} · {job.format}")
        if standing["blocks_filing"]:
            detail += " · filed despite the client asking for changes"
        _cb_log("commercial_approved", client=client.name, detail=detail,
                project=project.id, url=approval.stored_url or job.output_url)
        approval.filed_to_client = True
    except Exception as exc:  # noqa: BLE001
        approval.filed_to_client = False
        approval.filing_error = ((approval.filing_error or "") + " " + str(exc)).strip()

    db.session.add(approval)

    # The project is complete when every size somebody asked for has been
    # approved — not when Creatomate stopped working.
    approved = {a.render_job_id for a in
                RenderApproval.query.filter_by(project_id=project.id).all()} | {job.id}
    done = {j.format for j in project.render_jobs.all() if j.id in approved}
    if done >= set(project.formats or []):
        project.status = "complete"
    db.session.commit()

    return jsonify({"ok": True, "approval": approval.to_dict(),
                    "project": project.to_dict(include_scenes=False),
                    # Carried so the panel can say "filed, and the client had
                    # approved it" rather than only "filed". They are
                    # different sentences and only one of them is a sign-off.
                    "client_verdict": standing,
                    "compliance": compliance,
                    "filed_over_client_objection": bool(standing["blocks_filing"]),
                    "remaining_formats": [f for f in (project.formats or [])
                                          if f not in done],
                    "next": _next_in_campaign(project)})


def _compliance_gate(project, client):
    """Whether the published-rule findings have been acknowledged for THIS copy.

    Not a compliance verdict — `compliance_spec.py` says at length why this
    tool must never produce one. What it gates on is narrower and checkable:
    were the findings put in front of a named person, and were they the same
    findings that stand now.

    Rewriting the offer after somebody signed off retires that sign-off, which
    is the `findings_key` and the rule `modules/ads_builder` applies when a
    material edit supersedes an approved estimate. An acknowledgment of an
    earlier cut is reported as superseded rather than accepted or ignored —
    "nobody has looked" and "somebody looked at a different script" are
    different situations and only the second has a name to go back to.

    Never raises and never blocks on its own failure: a scanner that cannot
    run must not stop a rep filing a commercial, so an error answers "nothing
    to acknowledge", which is the honest degradation — it is the same answer
    as a spot that genuinely engages no rule, and both mean there is no
    finding on file.
    """
    try:
        from .. import compliance_spec
        from ..models import ComplianceAck
        result = compliance_spec.scan(
            script=project.script, brief=project.brief, cta=project.cta,
            client=client.to_dict(), commercial_type=project.commercial_type or "")
        if not compliance_spec.needs_acknowledgment(result):
            return {"blocks_filing": False, "regimes": [], "superseded": False,
                    "message": "", "acknowledged_by": ""}
        key = compliance_spec.findings_key(result)
        ack = (ComplianceAck.query.filter_by(project_id=project.id)
               .order_by(ComplianceAck.id.desc()).first())
        if ack and ack.findings_key == key:
            return {"blocks_filing": False,
                    "regimes": result.get("regimes", []), "superseded": False,
                    "message": "", "acknowledged_by": ack.acknowledged_by or ""}
        names = ", ".join(compliance_spec.REGIMES[r]["label"]
                          for r in result.get("regimes", []))
        superseded = bool(ack)
        return {
            "blocks_filing": True,
            "regimes": result.get("regimes", []),
            "superseded": superseded,
            "acknowledged_by": (ack.acknowledged_by or "") if ack else "",
            "message": (
                (f"The copy has changed since {ack.acknowledged_by or 'somebody'} "
                 f"acknowledged these findings, so that sign-off no longer covers "
                 f"this cut. " if superseded else "")
                + f"This spot engages published advertising rules ({names}). "
                  "Read what they require on the Blueprint step and acknowledge "
                  "them there before filing — that is a record of who checked, "
                  "not a judgment that the spot complies."),
        }
    except Exception:  # noqa: BLE001
        return {"blocks_filing": False, "regimes": [], "superseded": False,
                "message": "", "acknowledged_by": ""}


def _client_verdict(project):
    """What the client answered on the live review round, resolved.

    Never raises and never blocks on its own failure: a review table that
    cannot be read must not stop a rep filing a commercial, so an error here
    answers "nobody was asked", which is the same answer as a project that
    genuinely never had a review — and that is the honest degradation,
    because both mean there is no refusal on file.
    """
    try:
        from .. import review_spec
        from ..models import ReviewShare
        live = (ReviewShare.query.filter_by(project_id=project.id, revoked=False)
                .order_by(ReviewShare.round_no.desc()).first())
        if not live:
            return review_spec.verdict([])
        return review_spec.verdict([d.to_dict() for d in live.decisions.all()])
    except Exception:  # noqa: BLE001
        from .. import review_spec
        return review_spec.verdict([])


def _actor():
    try:
        from hub import auth as _hub_auth
        user = _hub_auth.user_from_environ(request.environ)
        return (getattr(user, "name", None) or getattr(user, "email", None)
                or str(user) or "")[:200]
    except Exception:  # noqa: BLE001
        return ""


def _next_in_campaign(project):
    """The next spot to work on once this one is approved, or None.

    Several lengths started together share a campaign, and they are built in
    config.BUILD_ORDER — the :30 first, because the others are cut down from
    its storyboard. Approving one should hand somebody the next one's
    Blueprint rather than leaving them on a finished Preview screen wondering
    what happens now.

    Returns the URL as well as the length: the caller is a browser, and
    working out a wizard URL from a project id is not its job.
    """
    if not project.campaign_id:
        return None
    siblings = (CommercialProject.query
                .filter_by(campaign_id=project.campaign_id)
                .filter(CommercialProject.id != project.id).all())
    unfinished = [p for p in siblings if p.status != "complete"]
    if not unfinished:
        return None
    unfinished.sort(key=lambda p: build_sort_key(p.length_seconds))
    nxt = unfinished[0]
    try:
        from flask import url_for
        url = url_for("commercial_builder.cb_pages.blueprint", project_id=nxt.id)
    except Exception:  # noqa: BLE001 — standalone, no such endpoint
        url = f"/tools/commercial-builder/project/{nxt.id}/blueprint"
    return {"project_id": nxt.id, "length_seconds": nxt.length_seconds,
            "title": nxt.title or "", "url": url}
