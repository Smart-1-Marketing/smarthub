"""Inbound webhooks from Smart 1 Suite (GoHighLevel).

Until now the flow was one-way: the Proposal Builder pushes an opportunity
*into* GoHighLevel. Nothing came back. So a proposal created or updated in
Suite — which is where sales actually happens — never appeared on the client
record, and the value of an open pipeline lived in two places that disagreed.

This is the other direction. Point a GoHighLevel workflow at:

    POST {PUBLIC_BASE_URL}/api/hooks/ghl?token=GHL_WEBHOOK_TOKEN

and opportunity created / updated / status-changed events land on the matching
client's Proposals card with their value attached.

## Security

The endpoint is outside the Hub login by necessity — GoHighLevel can't sign in.
So it is protected the same way the Insites callback is, and with the same
lessons applied:

  * **Fails closed.** No token configured means every request is rejected. The
    Insites callback originally failed *open* — anyone who guessed an id could
    POST junk and flip a record — and that is the mistake not to repeat.
  * **Constant-time comparison**, and the token is accepted from a header as
    well as the query string, because query strings land in proxy logs.
  * **A malformed body leaves the record untouched** rather than writing a
    half-parsed one. A bad webhook should be a no-op, not a corruption.
  * **Nothing is created for an unmatched client.** An opportunity that
    doesn't match anyone is recorded as unmatched for review, not filed
    against a guess.
"""
from __future__ import annotations

import hmac
import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from hub import audit, clients_registry, proposals

bp = Blueprint("ghl_hooks", __name__)

# GoHighLevel opportunity stages -> our proposal statuses. Anything we don't
# recognise stays "sent" rather than being guessed at.
STAGE_MAP = {
    "open": "sent", "new": "sent", "quoted": "sent", "proposal": "sent",
    "viewed": "viewed", "opened": "viewed",
    "won": "won", "closed won": "won", "closedwon": "won",
    "lost": "lost", "closed lost": "lost", "closedlost": "lost",
    "abandoned": "lost",
}


# What a Suite stage change is allowed to do to a quote in the Proposal
# Builder. Deliberately only the two decided outcomes: "open", "quoted" and
# "viewed" tell us nothing the Hub does not already know better -- the Hub
# knows whether the link has been opened and whether the client pressed
# accept -- and letting them write would walk an approved quote backwards to
# Sent because somebody dragged a card in a pipeline.
QUOTE_STATUS_FROM_SUITE = {"won": "Approved", "lost": "Lost"}
# A quote with an insertion order behind it is finished. Suite does not know
# the IO exists, so a stage change must never move it.
QUOTE_STATUS_FINAL = ("Converted",)


def _quote_module():
    """The Proposal Builder's module, as the app actually loaded it.

    `wsgi.py` imports it under the name `salesb_app`, so a plain
    `import modules.sales_builder.app` here would create a *second* instance
    with its own declarative mapping of the same tables. Reuse the loaded one
    where there is one -- the fallback is for the tests and for the module
    running on its own.
    """
    import sys
    mod = sys.modules.get("salesb_app")
    if mod is not None:
        return mod
    from modules.sales_builder import app as mod   # noqa: PLC0415
    return mod


def sync_quote_status(opportunity_id: str, suite_stage: str) -> dict:
    """Move the Proposal Builder's own row when Suite decides a deal.

    The push into Suite has always recorded `suite_opportunity_id` on the
    quote, and nothing ever read it back: a deal marked Won in Suite updated
    the client's Proposals card and left the Proposal Builder's dashboard --
    where the rep actually looks -- still saying Sent. Two systems disagreeing
    about whether a proposal was won, with neither screen saying so.

    Four rules, each a way to be confidently wrong:

      * **Matched on the opportunity id and on nothing else.** Never on the
        client name: a client with three quotes would get whichever came
        first, which is the substring guess `hub/client_key.py` exists to
        refuse.
      * **Only the decided outcomes write.** See `QUOTE_STATUS_FROM_SUITE`.
      * **Converted is never moved.** An insertion order exists; Suite has no
        way to know that, and walking it back is the one change nobody could
        undo from either screen.
      * **A status that changed by itself has to say who changed it.** It goes
        in the quote's own activity strip as well as the Hub log, or a rep
        looking at a quote that says Lost has no way to find out why.
    """
    out = {"matched": False, "changed": False, "quote": "", "status": "",
           "reason": ""}
    opportunity_id = str(opportunity_id or "").strip()
    if not opportunity_id:
        out["reason"] = "no opportunity id on the payload"
        return out
    want = QUOTE_STATUS_FROM_SUITE.get(str(suite_stage or "").strip().lower())
    if not want:
        out["reason"] = f"{suite_stage or 'that stage'} is not a decided outcome"
        return out
    try:
        mod = _quote_module()
        db = mod.SessionLocal()
    except Exception as exc:                    # noqa: BLE001
        out["reason"] = f"the Proposal Builder could not be read ({type(exc).__name__})"
        return out
    try:
        q = (db.query(mod.Quote)
             .filter(mod.Quote.suite_opportunity_id == opportunity_id)
             .order_by(mod.Quote.id.desc()).first())
        if q is None:
            out["reason"] = "no quote here was pushed as that opportunity"
            return out
        out.update({"matched": True, "quote": q.quote_number or "",
                    "status": q.status or ""})
        if (q.status or "") in QUOTE_STATUS_FINAL:
            out["reason"] = (f"{q.quote_number} is {q.status} — an insertion "
                             f"order exists, so Suite does not move it")
            return out
        if (q.status or "") == want:
            out["reason"] = "already " + want
            return out
        was = q.status or ""
        q.status = want
        try:
            mod.log_activity(db, q.id, "🔁",
                             f"Smart 1 Suite marked this {suite_stage} — "
                             f"{was or 'no status'} → {want}")
        except Exception:                       # noqa: BLE001
            pass        # the status is the point; its note must not cost it
        db.commit()
        out.update({"changed": True, "status": want, "was": was})
        return out
    except Exception as exc:                    # noqa: BLE001
        try:
            db.rollback()
        except Exception:                       # noqa: BLE001
            pass
        out["reason"] = f"could not update the quote ({type(exc).__name__})"
        return out
    finally:
        try:
            db.close()
        except Exception:                       # noqa: BLE001
            pass


def _token() -> str:
    return (os.environ.get("GHL_WEBHOOK_TOKEN") or "").strip()


def _authorised() -> bool:
    expected = _token()
    if not expected:
        return False                    # fails closed, deliberately
    supplied = (request.headers.get("X-Webhook-Token")
                or request.args.get("token") or "")
    return hmac.compare_digest(supplied.strip(), expected)


def _match_client(payload: dict) -> str:
    """Find our client for this opportunity. Never guesses."""
    candidates = [
        payload.get("clientName"), payload.get("contactName"),
        payload.get("companyName"), payload.get("locationName"),
        (payload.get("contact") or {}).get("companyName"),
        (payload.get("contact") or {}).get("name"),
    ]
    for name in candidates:
        if not name:
            continue
        try:
            hit = clients_registry.find_client(str(name))
        except Exception:               # noqa: BLE001
            hit = None
        if hit:
            return str(hit.get("name") or "").strip()
    return ""


def _value_of(payload: dict) -> float:
    for key in ("monetaryValue", "value", "amount", "opportunityValue"):
        if payload.get(key) not in (None, ""):
            try:
                return round(float(payload[key]), 2)
            except (TypeError, ValueError):
                continue
    return 0.0


@bp.route("/api/hooks/ghl", methods=["POST"])
def ghl_hook():
    if not _authorised():
        audit.log("hooks", "ghl_rejected",
                  reason="no token" if not _token() else "bad token")
        # Same response either way: a caller shouldn't be able to tell whether
        # the endpoint is unconfigured or their token was simply wrong.
        return jsonify({"error": "Not authorized."}), 401

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict) or not payload:
        audit.log("hooks", "ghl_malformed")
        # 200 on purpose: GoHighLevel retries non-2xx, and retrying a body we
        # can never parse just fills the log.
        return jsonify({"ok": True, "ignored": "empty or unparseable body"}), 200

    event = str(payload.get("type") or payload.get("event") or "opportunity").lower()
    opp_id = str(payload.get("id") or payload.get("opportunityId") or "").strip()
    stage = str(payload.get("status") or payload.get("pipelineStageName") or "").lower().strip()
    status = STAGE_MAP.get(stage, "sent")
    value = _value_of(payload)
    client = _match_client(payload)
    name = (payload.get("name") or payload.get("opportunityName")
            or "Suite opportunity")

    if not client:
        audit.log("hooks", "ghl_unmatched", event=event, opportunity=opp_id,
                  name=str(name)[:120], value=value)
        return jsonify({"ok": True, "matched": False,
                        "note": "No client matched — recorded for review on "
                                "the Activity Log rather than filed against a "
                                "guess."}), 200

    try:
        record = proposals.upsert_from_ghl(
            client=client, opportunity_id=opp_id, title=str(name)[:200],
            value=value, status=status,
            when=payload.get("dateAdded") or payload.get("updatedAt") or "",
        )
    except Exception as exc:            # noqa: BLE001
        audit.log("hooks", "ghl_failed", client=client, opportunity=opp_id,
                  error=type(exc).__name__)
        return jsonify({"error": "Could not record that opportunity."}), 500

    # And the Proposal Builder's own row, which is the screen a rep reads.
    # Never allowed to fail the webhook: the client card is written either
    # way, and GoHighLevel retries a non-2xx.
    try:
        quote = sync_quote_status(opp_id, stage)
    except Exception as exc:            # noqa: BLE001
        quote = {"matched": False, "changed": False,
                 "reason": f"{type(exc).__name__}"}

    audit.log("hooks", "ghl_received", client=client, opportunity=opp_id,
              status=status, value=value, event=event,
              quote=quote.get("quote") or "",
              quote_status=(quote.get("status") if quote.get("changed") else ""))
    return jsonify({"ok": True, "matched": True, "client": client,
                    "proposal": record.get("id"), "status": status,
                    "value": value, "quote": quote}), 200


@bp.route("/api/hooks/ghl/health")
def ghl_hook_health():
    """Is the hook configured? Readable without a Suite payload."""
    from hub.users_routes import current_account
    if not current_account():
        return jsonify({"error": "Not signed in."}), 401
    # The origin, through hub.config: this string is printed for somebody to
    # paste into a GoHighLevel workflow, and a PUBLIC_BASE_URL carrying a path
    # would put that path in the middle of the webhook URL -- which then 404s
    # on every opportunity event, silently, since nobody watches a webhook that
    # was never delivered.
    from .config import public_base_origin
    base = public_base_origin()
    return jsonify({
        "configured": bool(_token()),
        "endpoint": f"{base}/api/hooks/ghl?token=…" if base else
                    "Set PUBLIC_BASE_URL to generate the URL.",
        "note": "Point a GoHighLevel workflow webhook at this URL on "
                "opportunity created, updated and stage-changed. Without "
                "GHL_WEBHOOK_TOKEN the endpoint rejects everything — it fails "
                "closed on purpose.",
        "recent": audit.tail(limit=10, module="hooks"),
    })


def register_ghl_hooks(app) -> None:
    if "ghl_hooks" not in app.blueprints:
        app.register_blueprint(bp)
