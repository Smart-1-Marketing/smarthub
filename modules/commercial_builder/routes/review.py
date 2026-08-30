"""The client review link: the staff half, and the half a client opens.

Two audiences on one blueprint, and the split is the whole care in this file.

**`PUBLIC_PATHS` is read by the login guard in `modules/commercial_builder/
__init__.py`.** This module is registered as a *blueprint on the hub app*, not
dispatcher-mounted, so `wsgi.py`'s `PUBLIC_PREFIXES` mechanism — which
`modules/ads_builder` and `modules/scans` use for exactly this — never sees it:
that is handed to `AuthGuard` and `HubBar` by `_mount()`, and nothing here is
mounted. The guard on this blueprint is what has to know, and the list lives
beside the routes it describes so a route added here cannot be public in one
place and refused in the other.

**The chrome is the hub app's, and it needed telling too.** A hub route's
sidebar, help layer and feedback tab are injected by the `after_request` in
`hub/__init__.py`, which is right for a staff page and wrong for a page a
client opens — the rule the built landing pages and the display-ad proof are
already in `CHROMELESS` for. Both halves are needed: a page exempted from the
guard but not from the chrome arrives at the client wearing the staff nav.

**Revoked, expired-round and never-existed all answer the same 404.** A
client-facing URL that says "this link has expired" tells somebody probing
which tokens are real — `modules/ads_builder` settled that for the estimate.

**Nothing on the public side reads a Hub session.** The reviewer is named
because they typed their name, and a decision with no name is refused: this
page is reached with nothing but the token, so a name claimed here is one
anybody holding the link could claim, and that is exactly why it has to be
*asked for* rather than inferred. `modules/ad_builder`'s proof route draws the
same line.
"""

from __future__ import annotations

import secrets
from datetime import datetime

from flask import Blueprint, abort, jsonify, render_template, request, url_for

from .. import review_spec
from ..db import db
from ..models import (Client, CommercialProject, RenderJob,
                      ReviewComment, ReviewDecision, ReviewShare)

try:
    from hub import audit as _hub_audit
    _cb_log = _hub_audit.for_module("commercial_builder")
except Exception:  # noqa: BLE001 — standalone, no Hub to log into
    def _cb_log(*_a, **_k):
        return None

bp = Blueprint("cb_review", __name__)

# Every path on this blueprint a client with no Hub login may reach. Read by
# `_install_login_guard`; see the module docstring. Deliberately a tuple of
# path *segments* under the module mount rather than full URLs, so the mount
# can move without this list going quietly stale.
PUBLIC_PATHS = ("/review/",)


# ---------------------------------------------------------------------------
# The staff half — behind the blueprint's login guard like everything else
# ---------------------------------------------------------------------------
def _actor():
    try:
        from hub import auth as _hub_auth
        user = _hub_auth.user_from_environ(request.environ)
        return (getattr(user, "name", None) or getattr(user, "email", None)
                or str(user) or "")[:200]
    except Exception:  # noqa: BLE001
        return ""


def _share_url(token):
    """The absolute link a rep copies and sends.

    Built from `request.host_url` rather than by pasting a path onto whatever
    root this request arrived on — `modules/image_picker/provisioning.py` says
    at length why: a mounted module's `url_root` carries its own mount, and
    concatenating the two builds a 404 the client meets and nobody else does.
    """
    try:
        path = url_for("commercial_builder.cb_review.client_review", token=token)
    except Exception:  # noqa: BLE001 — standalone, no such endpoint
        path = f"/tools/commercial-builder/review/{token}"
    return request.host_url.rstrip("/") + path


def _reviewable_cuts(project):
    """The rendered cuts a client is being asked about.

    Only renders that actually produced a file. A mock render reports success
    and has no video behind it, and putting one in front of a client is a page
    with an empty player on it and nothing saying why — the rule
    `approve_render` already applies to filing.
    """
    jobs = (RenderJob.query.filter_by(project_id=project.id)
            .order_by(RenderJob.created_at.asc()).all())
    latest = {}
    for job in jobs:
        if job.status == "succeeded" and job.output_url:
            latest[job.format] = job          # last good render per format wins
    return [latest[f] for f in sorted(latest)]


@bp.get("/api/projects/<int:project_id>/reviews")
def list_reviews(project_id):
    """Every round on this spot, newest first, with the verdict on each."""
    project = CommercialProject.query.get_or_404(project_id)
    shares = (ReviewShare.query.filter_by(project_id=project.id)
              .order_by(ReviewShare.round_no.desc(), ReviewShare.id.desc()).all())
    rows = []
    for share in shares:
        row = share.to_dict()
        row["url"] = _share_url(share.token)
        row["verdict"] = review_spec.verdict(row["decisions"])
        row["round_state"] = review_spec.round_state(share.round_no)
        rows.append(row)
    live = next((r for r in rows if not r["revoked"]), None)
    return jsonify({
        "ok": True,
        "reviews": rows,
        # What the Preview panel and the filing gate both read, so neither has
        # to re-derive "which round are we on" from a list.
        "current": live,
        "standing": (live or {}).get("verdict") or review_spec.verdict([]),
        "next_round": review_spec.round_state(len(rows) + 1),
        "cuts": [{"format": j.format, "url": j.output_url} for j in _reviewable_cuts(project)],
    })


@bp.post("/api/projects/<int:project_id>/reviews")
def send_for_review(project_id):
    """Issue a link for the next round.

    A new token every time, never a reopened one: a link that has been
    answered is the record of that answer, and handing the same URL out for
    round two would overwrite round one's decision with no trace there had
    been one. The previous round is revoked so a client working from an old
    email cannot answer about a cut that has been replaced.
    """
    project = CommercialProject.query.get_or_404(project_id)
    body = request.get_json(silent=True) or {}

    cuts = _reviewable_cuts(project)
    if not cuts:
        # Refused by name rather than served as an empty page. A review link
        # with nothing to watch is worse than no link: the client opens it,
        # sees nothing, and the rep finds out days later.
        return jsonify({"ok": False, "error": (
            "There is nothing rendered to review yet. Render a size first — a "
            "review link with no video on it is a page the client cannot "
            "answer.")}), 400

    previous = ReviewShare.query.filter_by(project_id=project.id).all()
    round_no = len(previous) + 1
    for old in previous:
        old.revoked = True

    share = ReviewShare(token=secrets.token_urlsafe(24), project_id=project.id,
                        round_no=round_no, created_by=_actor(),
                        message=str(body.get("message") or "").strip()[:2000])
    db.session.add(share)
    db.session.commit()

    state = review_spec.round_state(round_no)
    if state["over"]:
        # The client is served exactly as before; the Hub is told. Stopping
        # the client is what pushes the whole conversation back into email,
        # where none of this is recorded — see review.py's docstring.
        _log("commercial_review_rounds_exceeded", project=project,
             detail=f"Round {round_no} on {project.title or 'a commercial'}")

    _log("commercial_review_sent", project=project,
         detail=f"{state['label']} · {project.title or 'Commercial'}")

    row = share.to_dict()
    row["url"] = _share_url(share.token)
    row["round_state"] = state
    return jsonify({"ok": True, "review": row})


@bp.post("/api/projects/<int:project_id>/reviews/<int:share_id>/revoke")
def revoke_review(project_id, share_id):
    """Switch a link off. What was said on it is kept.

    Revoking is not deleting: the decisions and comments are the record of
    what a client asked for, and a rep who revokes a link they sent to the
    wrong address must not lose the round before it.
    """
    share = ReviewShare.query.filter_by(id=share_id, project_id=project_id).first_or_404()
    share.revoked = True
    db.session.commit()
    return jsonify({"ok": True, "review": share.to_dict()})


def _log(event, project=None, detail=""):
    """Never costs the write it describes.

    `audit.log()`'s first positional is `module` and `for_module` binds it —
    the trap CLAUDE.md names twice. The detail is built by the caller and
    passed in, because `submit_render` proved the swallow protects the call
    and not the arguments: an f-string over an attribute the model does not
    have raises before the guard can apply.
    """
    try:
        client = ""
        if project is not None:
            client = getattr(getattr(project, "client", None), "name", "") or ""
        _cb_log(event, client=client, detail=detail,
                project=getattr(project, "id", None))
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# The public half — reached with nothing but the token
# ---------------------------------------------------------------------------
def _live_share(token):
    share = ReviewShare.query.filter_by(token=str(token or "")).first()
    if not share or share.revoked:
        return None
    return share


@bp.get("/review/<token>")
def client_review(token):
    """The page a client opens. No Hub login, no staff chrome.

    Revoked, deleted and never-existed all answer the same 404 — a page that
    says "this link expired" tells somebody probing which tokens are real.
    """
    share = _live_share(token)
    if not share:
        abort(404)
    project = CommercialProject.query.get(share.project_id)
    if not project:
        abort(404)
    client = Client.query.get(project.client_id)

    share.opened_count = (share.opened_count or 0) + 1
    share.last_opened_at = datetime.utcnow()
    db.session.commit()

    return render_template(
        "commercial_review.html",
        token=token,
        project=project,
        client=client,
        cuts=[{"format": j.format, "url": j.output_url} for j in _reviewable_cuts(project)],
        message=share.message or "",
        outcomes=review_spec.OUTCOMES,
        round_state=review_spec.round_state(share.round_no),
        # What has already been said on THIS round, so a second reviewer can
        # see the first one's notes rather than repeating them. Names only —
        # no email addresses, and nothing about any other round or any other
        # client. `_posts_for_client()` in the social planner builds its
        # payload server-side for the same reason: a subset a renderer merely
        # happens to omit is one the next renderer prints.
        said=[{"outcome_label": review_spec.OUTCOME_LABELS.get(d.outcome, ""),
               "reviewer_name": d.reviewer_name or "Someone",
               "note": d.note or ""}
              for d in share.decisions.all()],
        comments=[{"timecode": review_spec.timecode(c.at_seconds),
                   "text": c.text or "",
                   "reviewer_name": c.reviewer_name or "Someone",
                   "format": c.format or ""}
                  for c in share.comments.all()],
    )


@bp.post("/review/<token>/comment")
def client_comment(token):
    """A note, optionally at a point in the cut. Does not decide anything.

    Kept separate from the decision on purpose: a client leaves three comments
    over ten minutes and then answers, and folding the two together would mean
    either the first comment counted as a rejection or the notes were lost
    when they finally pressed a button.
    """
    share = _live_share(token)
    if not share:
        abort(404)
    clean = review_spec.clean_comment(request.get_json(silent=True) or {})
    if not clean["text"]:
        return jsonify({"ok": False, "error": "Type your note first."}), 400
    if not clean["reviewer_name"]:
        return jsonify({"ok": False, "error": (
            "Please add your name — a note nobody can attribute is one we "
            "cannot come back to you about.")}), 400

    comment = ReviewComment(share_id=share.id, text=clean["text"],
                            reviewer_name=clean["reviewer_name"],
                            reviewer_email=clean["reviewer_email"],
                            at_seconds=clean["at_seconds"],
                            format=clean["format"])
    db.session.add(comment)
    db.session.commit()
    return jsonify({"ok": True, "comment": comment.to_dict()})


@bp.post("/review/<token>/decide")
def client_decide(token):
    """One of the three answers, with a name against it.

    A name and an email are required, and this is stricter than the comment
    above for a reason `modules/ads_builder/spec.py` gives about change
    requests: "the client approved it" is the thing somebody is held to later,
    and three people at one company will disagree with each other.

    Answering again REPLACES that person's own previous answer and touches
    nobody else's — a reviewer who pressed the wrong button must be able to
    correct it, and must not be able to correct a colleague.
    """
    share = _live_share(token)
    if not share:
        abort(404)
    body = request.get_json(silent=True) or {}
    outcome = str(body.get("outcome") or "")
    if not review_spec.is_outcome(outcome):
        return jsonify({"ok": False, "error": "Choose one of the three answers."}), 400

    name = str(body.get("name") or "").strip()[:200]
    email = str(body.get("email") or "").strip()[:200]
    if not name or not email:
        return jsonify({"ok": False, "error": (
            "Please add your name and email. We record who signed a spot off, "
            "and an answer nobody can be named for is one we cannot act on.")}), 400

    existing = next((d for d in share.decisions.all()
                     if (d.reviewer_email or "").strip().lower() == email.lower()), None)
    if existing:
        existing.outcome = outcome
        existing.reviewer_name = name
        existing.note = str(body.get("note") or "").strip()[:2000]
        existing.created_at = datetime.utcnow()
        decision = existing
    else:
        decision = ReviewDecision(share_id=share.id, outcome=outcome,
                                  reviewer_name=name, reviewer_email=email,
                                  note=str(body.get("note") or "").strip()[:2000])
        db.session.add(decision)
    db.session.commit()

    project = CommercialProject.query.get(share.project_id)
    resolved = review_spec.verdict([d.to_dict() for d in share.decisions.all()])
    _log("commercial_review_answered", project=project,
         detail=f"{review_spec.OUTCOME_LABELS.get(outcome, outcome)} — {name}")

    return jsonify({"ok": True, "decision": decision.to_dict(),
                    "verdict": resolved,
                    "label": review_spec.OUTCOME_LABELS.get(outcome, "")})
