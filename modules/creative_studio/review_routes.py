"""The client-facing half of a review link -- WO-CS6.

Bare `/review/<token>`, not `/creative-studio/review/<token>`. That is a
deliberate departure from the main `api.bp`, which is guarded and mounted at
`/creative-studio`: a client has no Hub login and no reason to be under that
prefix at all, and a bare address is also what keeps this from colliding
with `modules.commercial_builder.routes.review`'s own `/review/<token>`,
which lives under `/tools/commercial-builder/` and is a completely different
blueprint answering a completely different prefix.

**Its own blueprint, its own guard.** This module is registered directly on
the hub app (see `modules/creative_studio/__init__.py`), the same shape
`modules.commercial_builder` uses for its own public review page, so
`wsgi.py`'s `PUBLIC_PREFIXES` -- which only ever sees dispatcher-mounted
modules -- never reaches it. `hub.blueprint_guard.install()` is what has to
know instead, with `PUBLIC_PATHS` read from here.

**The chrome is the hub app's, and needs telling separately.** `/review/` is
in `hub/__init__.py`'s `CHROMELESS` for exactly the reason
`modules.commercial_builder.routes.review`'s own docstring gives at length:
a page exempted from the login guard and not from the chrome injector
arrives at a client wearing the staff sidebar.

**Revoked reads differently here than on Commercial Builder's own page.**
That page answers 404 for revoked, deleted and never-existed alike, so a
probe cannot tell which strings are real tokens. WO-CS6 asks for something
narrower and more useful: a revoked Creative Studio link means a *newer*
round exists, which is worth saying, so a share that is genuinely revoked
answers 410 with a page that says so -- while a token this table has never
heard of still answers a bare 404, exactly as before, because that is the
case a probe is trying to distinguish.

**Nothing on the public side reads a Hub session.** The reviewer is named
because they typed their name; a decision or comment with no name attached
is refused, the same line `modules.commercial_builder.routes.review` draws
and for the same reason -- this page is reached with nothing but the token,
so a name is not otherwise knowable.

**Rate-limited per IP**, `hub.leads.rate_limited` -- the same shared limiter
every other public endpoint in this Hub uses, keyed on the last
(server-appended) hop of X-Forwarded-For rather than the spoofable first
one.
"""
from __future__ import annotations

import secrets
from datetime import datetime

from flask import Blueprint, abort, jsonify, render_template, request

from .db import db
from .models import CsProject, CsProjectVersion, CsShare, CsShareComment, CsShareDecision

try:
    from modules.commercial_builder import review_spec
except Exception:                                        # noqa: BLE001
    review_spec = None

try:
    from hub import audit as _hub_audit
    _cs_log = _hub_audit.for_module("creative_studio")
except Exception:                                        # noqa: BLE001
    def _cs_log(*_a, **_k):
        return None

try:
    from hub import leads as _hub_leads
except Exception:                                        # noqa: BLE001
    _hub_leads = None

# The two answers this page offers. Approve and Request Changes -- WO-CS6's
# own words -- rather than the three-way "approved with changes" middle
# ground Commercial Builder's own review page carries: this is a still or a
# finished video version, and there is no half-approved state worth a third
# button for. The underlying vocabulary, the round cap and the precedence
# rule that resolves several answers into one are read from review_spec
# rather than restated -- the 2026-08-28 decision this work order's own
# reading list names -- so a change to either cannot drift the two apart.
SHOWN_OUTCOMES = ("approved", "changes_required")

bp = Blueprint("cs_review", __name__)

# Relative to `mount=""` below -- this blueprint carries no url_prefix, so a
# path here is the whole address. Read by the guard installed just below it,
# the same shape `modules.commercial_builder.__init__` uses for its own
# review blueprint.
PUBLIC_PATHS = ("/review/",)


def _guard() -> None:
    from hub.blueprint_guard import install
    install(bp, mount="", public=PUBLIC_PATHS)


_guard()


def _rate_limited() -> bool:
    if _hub_leads is None:
        return False
    try:
        return _hub_leads.rate_limited("creative_review", request, 120, 600)
    except Exception:                                     # noqa: BLE001
        return False


def _client_ip() -> str:
    try:
        return _hub_leads.client_ip(request)
    except Exception:                                     # noqa: BLE001
        return request.remote_addr or ""


def _log(event, project=None, detail=""):
    """Never costs the write it describes -- `audit.log()`'s first
    positional is `module`, and `for_module` binds it; the detail is built
    here, inside the try, for the reason `routes/render.py::_log_render`
    gives: an f-string over an attribute the model does not have raises
    before the swallow can apply."""
    try:
        client = getattr(project, "client_name", "") or "" if project else ""
        _cs_log(event, client=client, detail=detail,
                project=getattr(project, "id", None))
    except Exception:                                     # noqa: BLE001
        pass


def _live_share(token):
    return CsShare.query.filter_by(token=str(token or "")).first()


def _outcomes_shown():
    if review_spec is None:
        return ()
    return tuple(o for o in review_spec.OUTCOMES if o[0] in SHOWN_OUTCOMES)


@bp.get("/review/<token>")
def client_review(token):
    """The page a client opens. No Hub login, no staff nav, no Hub session."""
    if _rate_limited():
        return render_template("cs_review_gone.html", title="Please try again shortly",
                               heading="Please try again shortly",
                               body="This link has been opened a lot in a short "
                                    "time. Wait a few minutes and try again."), 429

    share = _live_share(token)
    if share is None:
        abort(404)
    if share.revoked:
        # A real page rather than a bare 404 -- a revoked Creative Studio
        # link means a newer round exists, which is worth telling somebody
        # who followed an old email rather than leaving them to wonder.
        return render_template("cs_review_gone.html", title="This link has been replaced",
                               heading="This link has been replaced",
                               body="A newer version has been sent for review. "
                                    "Please check your email for the latest link, "
                                    "or ask your account manager to resend it."), 410

    project = CsProject.query.get(share.project_id)
    version = CsProjectVersion.query.get(share.subject_id) if share.kind == "render" else None
    if project is None or (share.kind == "render" and version is None):
        abort(404)

    share.opened_count = (share.opened_count or 0) + 1
    share.last_opened_at = datetime.utcnow()
    db.session.commit()

    round_state = review_spec.round_state(share.round_no) if review_spec else {
        "label": f"Round {share.round_no or 1}", "client_note": "", "over": False}

    return render_template(
        "cs_review.html", token=token, project=project.as_dict(),
        version=version.as_dict() if version else None,
        message=share.message or "", round_state=round_state,
        outcomes=_outcomes_shown(),
        said=[{"outcome_label": (review_spec.OUTCOME_LABELS.get(d.outcome, "") if review_spec else d.outcome),
               "reviewer_name": d.reviewer_name or "Someone", "note": d.note or ""}
              for d in share.decisions.all()],
        comments=[c.to_dict() for c in share.comments.all()],
    )


@bp.post("/review/<token>/comment")
def client_comment(token):
    """A note, optionally at a point in the version. Does not decide anything
    -- kept apart from `/decide` for `routes/review.py::client_comment`'s own
    reason: a client leaves three notes and then answers, and folding the two
    together would mean the first note counted as a rejection."""
    if _rate_limited():
        return jsonify({"ok": False, "error": "Too many requests. Please wait a few minutes."}), 429
    share = _live_share(token)
    if share is None or share.revoked:
        abort(404)
    clean = review_spec.clean_comment(request.get_json(silent=True) or {}) if review_spec else {}
    if not clean.get("text"):
        return jsonify({"ok": False, "error": "Type your note first."}), 400
    if not clean.get("reviewer_name"):
        return jsonify({"ok": False, "error": (
            "Please add your name -- a note nobody can attribute is one we "
            "cannot come back to you about.")}), 400

    comment = CsShareComment(share_id=share.id, text=clean["text"],
                             reviewer_name=clean["reviewer_name"],
                             reviewer_email=clean.get("reviewer_email") or "",
                             at_seconds=clean.get("at_seconds"), ip=_client_ip())
    db.session.add(comment)
    db.session.commit()
    _log("creative_review_commented", project=CsProject.query.get(share.project_id),
         detail=f"{clean['reviewer_name']} left a note on round {share.round_no}")
    return jsonify({"ok": True, "comment": comment.to_dict()})


@bp.post("/review/<token>/decide")
def client_decide(token):
    """Approve, or Request Changes, with a name against it.

    Answering again REPLACES that person's own previous answer and touches
    nobody else's -- `routes/review.py::client_decide`'s own rule, kept
    verbatim: a reviewer who pressed the wrong button must be able to
    correct it and must not be able to correct a colleague.
    """
    if _rate_limited():
        return jsonify({"ok": False, "error": "Too many requests. Please wait a few minutes."}), 429
    share = _live_share(token)
    if share is None or share.revoked:
        abort(404)
    if review_spec is None:
        return jsonify({"ok": False, "error": "Review is unavailable right now."}), 503

    body = request.get_json(silent=True) or {}
    outcome = str(body.get("outcome") or "")
    if outcome not in SHOWN_OUTCOMES:
        return jsonify({"ok": False, "error": "Choose Approve or Request Changes."}), 400

    name = str(body.get("name") or "").strip()[:200]
    email = str(body.get("email") or "").strip()[:200]
    if review_spec.decision_requires_name(outcome) and (not name or not email):
        return jsonify({"ok": False, "error": (
            "Please add your name and email. We record who signed a version "
            "off, and an answer nobody can be named for is one we cannot act on.")}), 400

    existing = next((d for d in share.decisions.all()
                     if (d.reviewer_email or "").strip().lower() == email.lower()), None)
    ip = _client_ip()
    if existing:
        existing.outcome = outcome
        existing.reviewer_name = name
        existing.note = str(body.get("note") or "").strip()[:2000]
        existing.ip = ip
        existing.created_at = datetime.utcnow()
        decision = existing
    else:
        decision = CsShareDecision(share_id=share.id, outcome=outcome,
                                   reviewer_name=name, reviewer_email=email,
                                   note=str(body.get("note") or "").strip()[:2000], ip=ip)
        db.session.add(decision)
    db.session.commit()

    project = CsProject.query.get(share.project_id)
    resolved = review_spec.verdict([d.to_dict() for d in share.decisions.all()])
    # Internal Review -> Client Review happened on send; this is the other
    # half -- WO-CS6 item 5. Approval sets the outcome only: it never
    # enqueues a render, which is the one thing a public page with no QC
    # gate in front of it must never be able to trigger.
    if project is not None:
        if resolved["outcome"] == "changes_required":
            project.status = "Changes Requested"
        elif resolved["outcome"] == "approved":
            project.status = "Approved"
        db.session.commit()

    _log("creative_review_answered", project=project,
         detail=f"{review_spec.OUTCOME_LABELS.get(outcome, outcome)} — {name}")
    return jsonify({"ok": True, "decision": decision.to_dict(), "verdict": resolved,
                    "label": review_spec.OUTCOME_LABELS.get(outcome, "")})
