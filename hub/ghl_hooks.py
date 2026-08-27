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

    audit.log("hooks", "ghl_received", client=client, opportunity=opp_id,
              status=status, value=value, event=event)
    return jsonify({"ok": True, "matched": True, "client": client,
                    "proposal": record.get("id"), "status": status,
                    "value": value}), 200


@bp.route("/api/hooks/ghl/health")
def ghl_hook_health():
    """Is the hook configured? Readable without a Suite payload."""
    from hub.users_routes import current_account
    if not current_account():
        return jsonify({"error": "Not signed in."}), 401
    base = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
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
