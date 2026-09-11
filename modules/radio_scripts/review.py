"""The client review link for a radio script set: the staff half, and the
half a client opens.

Two audiences on one blueprint, and the split is the whole care in this file
— the same one `modules/commercial_builder/routes/review.py` documents at
length, and it applies here for the identical reason.

**`PUBLIC_PATHS` is read by the login guard in `modules/radio_scripts/
__init__.py`.** This module is registered as a *blueprint on the hub app*,
not dispatcher-mounted, so `wsgi.py`'s `PUBLIC_PREFIXES` mechanism — which
`modules/ads_builder` and `modules/scans` use for exactly this — never sees
it: that is handed to `AuthGuard` and `HubBar` by `_mount()`, and nothing
here is mounted. The guard on this blueprint is what has to know, and the
list lives beside the routes it describes so a route added here cannot be
public in one place and refused in the other.

**The chrome is the hub app's, and it needed telling too.** A hub route's
sidebar, help layer and feedback tab are injected by the `after_request` in
`hub/__init__.py`, which is right for a staff page and wrong for a page a
client opens — `"/tools/radio-scripts/review/"` is in `CHROMELESS` for
exactly this. Both halves are needed: a page exempted from the guard but not
from the chrome arrives at the client wearing the staff nav.

**Revoked, deleted and never-existed all answer the same 404.** A
client-facing URL that says "this link has expired" tells somebody probing
which tokens are real — `modules/ads_builder` settled that for the estimate.

**Nothing on the public side reads a Hub session.** The reviewer is named
because they typed their name, and a decision with no name is refused: this
page is reached with nothing but the token, so a name claimed here is one
anybody holding the link could claim, and that is exactly why it has to be
*asked for* rather than inferred.

**No Suite delivery.** `modules/commercial_builder`'s review link files the
reviewer as a Suite lead so a workflow can email it. That half is genuinely
optional here: `hub/lead_tags.py`'s `EVENT_TAGS["radio_script_ready"]` names
the review link as the thing a future workflow would send, but the workflow
and the custom field it would ride in do not exist yet (the same open gap
that entry already names) — so this route's whole job is producing the
shareable link, and a rep copies and sends it by hand exactly as they would
today. Building that delivery is additive later, not a piece missing now.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, render_template, request, url_for

from . import review_spec
from .db import db
from .models import RadioReviewComment, RadioReviewDecision, RadioReviewShare, RadioScriptSet

# Every path under this blueprint's mount a client with no Hub login may
# reach. Read by `modules/radio_scripts/__init__.py::_guard`. A tuple of path
# *segments* under the mount rather than full URLs, so the mount can move
# without this list going quietly stale.
PUBLIC_PATHS = ("/review/",)


def _actor() -> str:
    try:
        from hub import current_user
        return str(current_user() or "")[:200]
    except Exception:  # noqa: BLE001
        return ""


try:
    from hub import audit as _hub_audit
    _log_fn = _hub_audit.for_module("radio_scripts", _actor)
except Exception:  # noqa: BLE001 — standalone, no Hub to log into
    def _log_fn(*_a, **_k):
        return None


def _log(event, row=None, detail=""):
    """Never costs the write it describes.

    `audit.log()`'s first positional is `module`, and `for_module` binds it —
    the trap CLAUDE.md names repeatedly. The detail is built by the caller
    and passed in rather than computed inside the try, because an f-string
    over an attribute a row does not have raises before the guard can apply.
    """
    try:
        _log_fn(event, client=(getattr(row, "client_name", "") or None),
               set_id=getattr(row, "id", None), detail=detail)
    except Exception:  # noqa: BLE001
        pass


def _share_url(token: str) -> str:
    """The absolute link a rep copies and sends.

    Built from `request.host_url` rather than by pasting a path onto
    whatever root this request arrived on — `modules/image_picker/
    provisioning.py` says at length why: a mounted module's `url_root`
    carries its own mount, and concatenating the two builds a 404 the client
    meets and nobody else does. This module is a blueprint rather than
    mounted, so `url_root` carries none of that risk, but the reasoning
    still argues for `url_for` over string-pasting.
    """
    try:
        path = url_for("radio_scripts.client_review", token=token)
    except Exception:  # noqa: BLE001
        path = f"/tools/radio-scripts/review/{token}"
    return request.host_url.rstrip("/") + path


def attach(bp: Blueprint) -> None:
    """Hang this file's routes on the blueprint `__init__.py` builds."""

    # -----------------------------------------------------------------
    # The staff half — behind the blueprint's login guard like everything
    # else `modules/radio_scripts/api.py` attaches.
    # -----------------------------------------------------------------
    @bp.get("/api/sets/<int:set_id>/reviews")
    def list_reviews(set_id: int):
        """Every round sent on this set, newest first, with the verdict on each."""
        row = RadioScriptSet.query.get(set_id)
        if not row:
            return jsonify({"ok": False, "error": "No such script set."}), 404
        shares = (RadioReviewShare.query.filter_by(set_id=set_id)
                 .order_by(RadioReviewShare.round_no.desc(), RadioReviewShare.id.desc()).all())
        rounds = []
        for share in shares:
            item = share.to_dict()
            item["url"] = _share_url(share.token)
            item["verdict"] = review_spec.verdict(item["decisions"])
            rounds.append(item)
        live = next((r for r in rounds if not r["revoked"]), None)
        return jsonify({
            "ok": True, "reviews": rounds, "current": live,
            "standing": (live or {}).get("verdict") or review_spec.verdict([]),
        })

    @bp.post("/api/sets/<int:set_id>/reviews")
    def send_for_review(set_id: int):
        """Issue a link for the next round.

        A new token every time, never a reopened one — see the module
        docstring on `RadioReviewShare`. The previous round is revoked so a
        client working from an old email cannot answer about scripts that
        have since changed.
        """
        row = RadioScriptSet.query.get(set_id)
        if not row:
            return jsonify({"ok": False, "error": "No such script set."}), 404
        if not row.concepts():
            # Refused by name rather than served as an empty page — a review
            # link with nothing on it is worse than no link: the client
            # opens it, sees nothing, and the rep finds out days later.
            return jsonify({"ok": False, "error": (
                "There is nothing generated to review yet. Generate a set "
                "first.")}), 400

        body = request.get_json(silent=True) or {}
        previous = RadioReviewShare.query.filter_by(set_id=set_id).all()
        round_no = len(previous) + 1
        for old in previous:
            old.revoked = True

        share = RadioReviewShare(token=secrets.token_urlsafe(24), set_id=set_id,
                                 round_no=round_no, created_by=_actor(),
                                 message=str(body.get("message") or "").strip()[:2000])
        db.session.add(share)
        db.session.commit()

        _log("radio_review_sent", row,
             detail=f"{review_spec.round_label(round_no)} sent for {row.client_name or 'a script set'}")

        item = share.to_dict()
        item["url"] = _share_url(share.token)
        return jsonify({"ok": True, "review": item})

    @bp.post("/api/sets/<int:set_id>/reviews/<int:share_id>/revoke")
    def revoke_review(set_id: int, share_id: int):
        """Switch a link off. What was said on it is kept.

        Revoking is not deleting: the decisions and comments are the record
        of what a client asked for, and a rep who revokes a link sent to the
        wrong address must not lose the round before it.
        """
        share = RadioReviewShare.query.filter_by(id=share_id, set_id=set_id).first()
        if not share:
            return jsonify({"ok": False, "error": "No such review link."}), 404
        share.revoked = True
        db.session.commit()
        _log("radio_review_revoked", RadioScriptSet.query.get(set_id),
             detail=f"{review_spec.round_label(share.round_no)} link revoked.")
        return jsonify({"ok": True, "review": share.to_dict()})

    # -----------------------------------------------------------------
    # The public half — reached with nothing but the token.
    # -----------------------------------------------------------------
    def _live_share(token):
        share = RadioReviewShare.query.filter_by(token=str(token or "")).first()
        if not share or share.revoked:
            return None
        return share

    @bp.get("/review/<token>")
    def client_review(token):
        """The page a client opens. No Hub login, no staff chrome.

        Revoked, deleted and never-existed all answer the same 404.
        """
        share = _live_share(token)
        if not share:
            abort(404)
        row = RadioScriptSet.query.get(share.set_id)
        if not row:
            abort(404)

        share.opened_count = (share.opened_count or 0) + 1
        share.last_opened_at = datetime.now(timezone.utc)
        db.session.commit()

        return render_template(
            "radio_review.html",
            token=token,
            row=row,
            concepts=row.concepts(),
            message=share.message or "",
            outcomes=review_spec.OUTCOMES,
            round_label=review_spec.round_label(share.round_no),
            length_labels=review_spec.LENGTH_LABELS,
            # What has already been said on THIS round, so a second reviewer
            # sees the first one's notes rather than repeating them. Names
            # only — no email addresses, and nothing about any other round.
            said=[{"outcome_label": review_spec.OUTCOME_LABELS.get(d.outcome, ""),
                   "reviewer_name": d.reviewer_name or "Someone",
                   "note": d.note or ""}
                  for d in share.decisions.all()],
            comments=[{"concept_label": review_spec.concept_label(c.concept_index),
                       "length_label": review_spec.length_label(c.length_key),
                       "text": c.text or "",
                       "reviewer_name": c.reviewer_name or "Someone"}
                      for c in share.comments.all()],
        )

    @bp.post("/review/<token>/comment")
    def client_comment(token):
        """A note, optionally against one script in the set. Does not decide
        anything — kept separate on purpose, the way Commercial Builder's
        review link does: a client leaves three notes over ten minutes and
        then answers, and folding the two together would mean either the
        first note counted as a rejection or the notes were lost when they
        finally pressed a button.
        """
        share = _live_share(token)
        if not share:
            abort(404)
        clean = review_spec.clean_comment(request.get_json(silent=True) or {})
        if not clean["text"]:
            return jsonify({"ok": False, "error": "Type your note first."}), 400
        if not clean["reviewer_name"]:
            return jsonify({"ok": False, "error": (
                "Please add your name — a note nobody can attribute is one "
                "we cannot come back to you about.")}), 400

        comment = RadioReviewComment(share_id=share.id, text=clean["text"],
                                     reviewer_name=clean["reviewer_name"],
                                     reviewer_email=clean["reviewer_email"],
                                     concept_index=clean["concept_index"],
                                     length_key=clean["length_key"])
        db.session.add(comment)
        db.session.commit()
        _log("radio_review_commented", RadioScriptSet.query.get(share.set_id),
             detail=(f"{clean['reviewer_name']} left a note on "
                     f"{review_spec.round_label(share.round_no)}"))
        return jsonify({"ok": True, "comment": comment.to_dict()})

    @bp.post("/review/<token>/decide")
    def client_decide(token):
        """One of the three answers, with a name against it.

        Answering again REPLACES that person's own previous answer and
        touches nobody else's — matched by email, the same rule Commercial
        Builder's review link uses.
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
        if review_spec.decision_requires_name(outcome) and (not name or not email):
            return jsonify({"ok": False, "error": (
                "Please add your name and email. We record who signed scripts "
                "off, and an answer nobody can be named for is one we cannot "
                "act on.")}), 400

        existing = next((d for d in share.decisions.all()
                         if (d.reviewer_email or "").strip().lower() == email.lower()), None)
        if existing:
            existing.outcome = outcome
            existing.reviewer_name = name
            existing.note = str(body.get("note") or "").strip()[:2000]
            existing.created_at = datetime.now(timezone.utc)
            decision = existing
        else:
            decision = RadioReviewDecision(share_id=share.id, outcome=outcome,
                                           reviewer_name=name, reviewer_email=email,
                                           note=str(body.get("note") or "").strip()[:2000])
            db.session.add(decision)
        db.session.commit()

        row = RadioScriptSet.query.get(share.set_id)
        _log("radio_review_answered", row,
             detail=f"{review_spec.OUTCOME_LABELS.get(outcome, outcome)} — {name}")

        return jsonify({"ok": True, "decision": decision.to_dict(),
                        "verdict": review_spec.verdict([d.to_dict() for d in share.decisions.all()]),
                        "label": review_spec.OUTCOME_LABELS.get(outcome, "")})
