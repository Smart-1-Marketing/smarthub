"""Smart 1 Hub — Smart 1 Ads module (version 6.1ads).

Google Ads campaign operations, an AI campaign generator, an approval hub and
client-ready proposals, mounted at /tools/ads.

Design choices that matter:

* **Nothing spends money by accident.** Every campaign this module creates is
  created PAUSED, and deployment is a single atomic ``googleAds:mutate`` — if
  any one operation fails Google rolls the whole batch back, so a half-built
  campaign cannot happen. Only proposals marked APPROVED can deploy; the dry
  run (Google's own ``validateOnly``) works at any status.

* **Auth is the Hub's.** The wsgi AuthGuard runs in front of this mount, so
  there is no second password and no second session. The signed-in Hub user is
  read from ``environ['s1hub.user']`` and stamped on every audit row.

* **Storage is SQLite locally, Postgres on Render** — same as scans. The Google
  refresh token is written to the settings table so the connection works the
  moment you authorise it, but GOOGLE_ADS_REFRESH_TOKEN in the environment
  always wins, because that is the copy that survives a redeploy.

* **The generator is the front door, and it needs no Google connection.**
  ``/tools/ads/`` opens on it. Live campaigns sit after the approval hub,
  because reading somebody's live spend is the one screen here that cannot work
  until Google's API does -- opening on it made a tool whose first three steps
  were fully working look dead. The generator is OpenAI, review and approval
  are the Hub's own, and the Ads Editor export is a file: the only thing the
  Google Ads API gates is reading live campaigns and writing a new one.

* **No developer token is not no product.** Google issues the developer token
  on its own timetable, so ``modules/ads_builder/export.py`` writes the same
  campaign as a Google Ads Editor import file. An approved proposal reaches the
  client account today, with the account owner's own sign-in, and the identical
  proposal still deploys through the API later when the token lands.

* **Bing is phase two.** Nothing here assumes Google; the proposal format is
  platform neutral, so Microsoft Advertising slots in as a sibling client
  module without touching the generator, approval hub or proposal export.
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from flask import (Flask, jsonify, make_response, redirect, render_template,
                   request)

from . import VERSION, VERSION_DATE
from . import campaign_ai, client_link, export, google_ads, store
from .campaign_ai import SECTOR_CPC, GenerationError, analyse_budget
from .google_ads import GoogleAdsError

BASE_DIR = Path(__file__).parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))
app.config.update(JSON_SORT_KEYS=False)

MOUNT = "/tools/ads"

OBJECTIVES = [
    "Lead generation", "Phone calls", "Online sales",
    "Bookings / appointments", "Quote requests", "Brand awareness",
]


# ---------------------------------------------------------------- helpers
def current_user() -> str:
    user = request.environ.get("s1hub.user")
    if isinstance(user, dict):
        return user.get("email") or user.get("name") or "Team"
    return str(user or "Team")


def wants_json() -> bool:
    return request.path.startswith("/api/") or "application/json" in (
        request.headers.get("Accept") or ""
    )


@app.errorhandler(GoogleAdsError)
def _google_error(exc: GoogleAdsError):
    payload = {
        "error": exc.message,
        "code": exc.code,
        "field": exc.field,
        "trigger": exc.trigger,
    }
    store.log_event("GOOGLE_API_ERROR", current_user(), error=exc.message, code=exc.code)
    if wants_json():
        return jsonify(payload), (exc.status if 400 <= exc.status < 600 else 500)
    return render_template("ads_error.html", **payload), 500


@app.errorhandler(GenerationError)
def _generation_error(exc: GenerationError):
    return jsonify({"error": str(exc), "code": "GENERATION_FAILED"}), 400


@app.context_processor
def _inject():
    try:
        open_count = len([p for p in store.list_proposals() if p["status"] in store.OPEN_STATUSES])
    except Exception:  # noqa: BLE001 — a template must never 500 over a badge count
        open_count = 0
    return {
        "mount": MOUNT,
        "version": VERSION,
        "version_date": VERSION_DATE,
        "hub_user": current_user(),
        "open_count": open_count,
    }


# ------------------------------------------------------------------ pages
@app.get("/")
def page_generator():
    """The front door. Nothing on it touches Google."""
    return render_template(
        "ads_generator.html",
        sectors=[{"key": k, **v} for k, v in SECTOR_CPC.items()],
        objectives=OBJECTIVES,
        openai_configured=bool(campaign_ai.openai_key()),
    )


@app.get("/generator")
def page_generator_alias():
    """Where the generator used to live. One URL, so a bookmark still lands."""
    return redirect(MOUNT + "/")


@app.get("/campaigns")
def page_campaigns():
    return render_template("ads_campaigns.html", status=google_ads.connection_status(store))


@app.get("/approvals")
def page_approvals():
    return render_template("ads_approvals.html", proposals=store.list_proposals())


@app.get("/proposal/<public_id>")
def page_proposal(public_id):
    proposal = store.get_proposal(public_id)
    if not proposal:
        return render_template("ads_error.html", error="That proposal no longer exists."), 404
    return render_template(
        "ads_proposal.html",
        p=proposal,
        status=google_ads.connection_status(store),
    )


@app.get("/proposal/<public_id>/client")
def page_client_proposal(public_id):
    proposal = store.get_proposal(public_id)
    if not proposal:
        return render_template("ads_error.html", error="That proposal no longer exists."), 404
    today = datetime.now(timezone.utc)
    return render_template(
        "ads_client_proposal.html",
        p=proposal,
        today=f"{today:%B} {today.day}, {today.year}",  # platform-safe, no %-d
    )


# ------------------------------------------------- Ads Editor handoff (no API)
def _download(body: str, filename: str, mimetype: str):
    resp = make_response(body)
    resp.headers["Content-Type"] = f"{mimetype}; charset=utf-8"
    # The name is the deliverable here: a file that lands in Downloads as
    # export.csv has lost which client it belongs to.
    resp.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


@app.get("/proposal/<public_id>/export/campaign.csv")
def export_campaign_csv(public_id):
    proposal = store.get_proposal(public_id)
    if not proposal:
        return render_template("ads_error.html", error="That proposal no longer exists."), 404
    campaign = proposal["campaign"]
    body = export.editor_csv(campaign, search_partners=False)
    store.log_event("EXPORT_ADS_EDITOR_CSV", current_user(),
                    proposal=public_id, client=proposal["client_name"],
                    keywords=proposal["keyword_count"])
    return _download(body, f"{export.slug(proposal['client_name'])}-google-ads-editor.csv",
                     "text/csv")


@app.get("/proposal/<public_id>/export/build-sheet.txt")
def export_build_sheet(public_id):
    proposal = store.get_proposal(public_id)
    if not proposal:
        return render_template("ads_error.html", error="That proposal no longer exists."), 404
    body = export.build_sheet(proposal["campaign"])
    store.log_event("EXPORT_BUILD_SHEET", current_user(),
                    proposal=public_id, client=proposal["client_name"])
    return _download(body, f"{export.slug(proposal['client_name'])}-build-sheet.txt",
                     "text/plain")


@app.get("/api/proposals/<public_id>/export")
def api_export_summary(public_id):
    """What the handoff will and will not carry, before anybody downloads it."""
    proposal = store.get_proposal(public_id)
    if not proposal:
        return jsonify({"error": "Proposal not found."}), 404
    campaign = proposal["campaign"]
    assets = campaign.get("adAssets") or {}
    return jsonify({
        "campaign_name": export.default_campaign_name(campaign),
        "daily_budget": export.daily_budget(campaign),
        "negatives": len(export.negatives_of(campaign)),
        "problems": export.problems(campaign),
        "by_hand": {
            "sitelinks": len(assets.get("sitelinks") or []),
            "callouts": len(assets.get("callouts") or []),
            "structured_snippets": len((assets.get("structuredSnippets") or {}).get("values") or []),
        },
        "csv_url": f"{MOUNT}/proposal/{public_id}/export/campaign.csv",
        "build_sheet_url": f"{MOUNT}/proposal/{public_id}/export/build-sheet.txt",
    })


@app.get("/activity")
def page_activity():
    return render_template("ads_activity.html", events=store.list_events(300))


@app.get("/settings")
def page_settings():
    return render_template(
        "ads_settings.html",
        status=google_ads.connection_status(store),
        openai_configured=bool(campaign_ai.openai_key()),
        openai_model=campaign_ai.openai_model(),
        expected_redirect=(os.environ.get("PUBLIC_BASE_URL", "").rstrip("/") + MOUNT
                           + "/oauth/callback"),
    )


# ------------------------------------------------------------------ OAuth
@app.get("/connect")
def oauth_connect():
    status = google_ads.connection_status(store)
    if status["missing"]:
        return render_template(
            "ads_error.html",
            error="Google sign-in cannot start until these are set: "
                  + ", ".join(status["missing"]),
        ), 400
    state = secrets.token_hex(16)
    resp = make_response(redirect(google_ads.build_auth_url(state)))
    resp.set_cookie("s1ads_oauth_state", state, httponly=True, samesite="Lax", max_age=600)
    return resp


@app.get("/oauth/callback")
def oauth_callback():
    error = request.args.get("error")
    code = request.args.get("code")
    state = request.args.get("state")

    if error:
        store.log_event("GOOGLE_OAUTH_DENIED", current_user(), error=error)
        return render_template("ads_error.html", error=f"Google sign-in was cancelled: {error}"), 400
    if not code:
        return render_template("ads_error.html", error="Google did not return an authorisation code."), 400

    expected = request.cookies.get("s1ads_oauth_state")
    if expected and state != expected:
        return render_template(
            "ads_error.html",
            error="Sign-in state mismatch. Start the connection again from Settings.",
        ), 400

    tokens = google_ads.exchange_code(code)
    refresh = tokens.get("refresh_token", "")
    if refresh:
        store.set_setting("google_refresh_token", refresh)

    store.log_event("GOOGLE_OAUTH_SUCCESS", current_user(), got_refresh_token=bool(refresh))

    resp = make_response(render_template(
        "ads_connected.html",
        refresh_token=refresh,
        pinned=bool(os.environ.get("GOOGLE_ADS_REFRESH_TOKEN", "").strip()),
    ))
    resp.delete_cookie("s1ads_oauth_state")
    return resp


@app.post("/api/disconnect")
def api_disconnect():
    store.set_setting("google_refresh_token", "")
    google_ads.forget_tokens()
    store.log_event("GOOGLE_DISCONNECTED", current_user())
    return jsonify({
        "ok": True,
        "note": "Also clear GOOGLE_ADS_REFRESH_TOKEN in the environment if it is set there.",
    })


# -------------------------------------------------------------------- API
@app.get("/api/version")
def api_version():
    return jsonify({"module": "ads_builder", "version": VERSION, "date": VERSION_DATE})


@app.get("/api/status")
def api_status():
    google = google_ads.connection_status(store)
    generator_ready = bool(campaign_ai.openai_key())
    return jsonify({
        "version": VERSION,
        "google": google,
        "openai": {
            "configured": generator_ready,
            "model": campaign_ai.openai_model(),
        },
        # Said per capability rather than as one "connected" flag, because
        # three of the four work with no Google credentials at all and a single
        # flag reports the whole tool as down when only the last step is.
        "capabilities": {
            "generate": {"ready": generator_ready,
                         "needs": [] if generator_ready else ["OPENAI_API_KEY"]},
            "approve": {"ready": True, "needs": []},
            "export_to_ads_editor": {"ready": True, "needs": [],
                                     "note": "Needs no Google API access at all."},
            "read_live_campaigns": {"ready": google["deploy_ready"], "needs": google["missing"]},
            "deploy_via_api": {"ready": google["deploy_ready"], "needs": google["missing"]},
        },
        "bing": {"configured": False, "connected": False, "note": "Phase 2 — not wired yet"},
    })


@app.get("/api/clients")
def api_clients():
    """Existing clients, for the lookup on the generator.

    Served from the module rather than fetched from the Hub's own
    /api/clients/search so the call stays inside this mount — and so the page
    behaves the same when the module is run standalone, where it reports the
    list as unavailable instead of 404ing into the Hub app.
    """
    return jsonify(client_link.search(request.args.get("q", ""),
                                      limit=min(int(request.args.get("limit") or 12), 50)))


@app.get("/api/budget-check")
def api_budget_check():
    return jsonify(analyse_budget(request.args.get("budget"), request.args.get("sector", "general")))


@app.get("/api/accounts")
def api_accounts():
    return jsonify({"accounts": google_ads.list_client_accounts(store)})


@app.get("/api/campaigns")
def api_campaigns():
    customer_id = request.args.get("customer_id", "")
    if not customer_id:
        return jsonify({"error": "customer_id is required."}), 400
    return jsonify({
        "customer_id": google_ads.digits(customer_id),
        "campaigns": google_ads.list_campaigns(
            customer_id, request.args.get("date_range", "LAST_30_DAYS"), store
        ),
    })


@app.get("/api/campaigns/<campaign_id>")
def api_campaign_detail(campaign_id):
    customer_id = request.args.get("customer_id", "")
    if not customer_id:
        return jsonify({"error": "customer_id is required."}), 400
    return jsonify(google_ads.campaign_detail(customer_id, campaign_id, store))


@app.post("/api/campaigns/<campaign_id>/status")
def api_campaign_status(campaign_id):
    body = request.get_json(silent=True) or {}
    customer_id, status = body.get("customer_id"), body.get("status")
    if not customer_id or not status:
        return jsonify({"error": "customer_id and status are required."}), 400

    google_ads.set_campaign_status(customer_id, campaign_id, status, store)
    store.log_event("CAMPAIGN_STATUS_CHANGE", current_user(),
                    customer_id=google_ads.digits(customer_id),
                    campaign_id=campaign_id, status=str(status).upper())
    return jsonify({"ok": True, "campaign_id": campaign_id, "status": str(status).upper()})


@app.post("/api/generate")
def api_generate():
    body = request.get_json(silent=True) or {}
    missing = [f for f in ("businessName", "websiteUrl", "budget") if not body.get(f)]
    if missing:
        return jsonify({"error": "Required: " + ", ".join(missing)}), 400

    store.log_event("GENERATION_START", current_user(),
                    client=body.get("businessName"), budget=body.get("budget"))

    # No key from the browser: the Hub's OPENAI_API_KEY is the only one used.
    campaign = campaign_ai.generate_campaign(body)

    # Who this is for, decided by the browser (picked from the lookup, or
    # explicitly new) and checked here against the Hub's own client list.
    # Recorded inside the campaign JSON rather than in new columns: create_all()
    # adds no column to an existing table, so a column added here would be
    # silently absent on the live Postgres while every local test passed.
    is_new = bool(body.get("isNewClient"))
    contact = body.get("contact") or {}
    campaign["clientLink"] = {
        "is_new_client": is_new,
        "picked_from_lookup": bool(body.get("clientPicked")),
        "contact": {k: str(contact.get(k) or "").strip()
                    for k in ("name", "email", "phone")} if is_new else {},
    }

    proposal = store.create_proposal(
        client_name=body["businessName"],
        campaign=campaign,
        created_by=current_user(),
        google_customer_id=body.get("googleCustomerId", ""),
    )

    # client= is what puts this on the client's work log: hub/client_brand.py
    # lists ads_builder in WORK_KINDS and matches on that field.
    work = store.log_event(
        "GENERATION_SUCCESS", current_user(),
        proposal=proposal["id"], client=body["businessName"],
        detail=f"Campaign generated — {proposal['ad_group_count']} ad groups, "
               f"{proposal['keyword_count']} keywords",
        ad_groups=proposal["ad_group_count"], keywords=proposal["keyword_count"])

    link = client_link.attach(proposal, MOUNT, is_new_client=is_new,
                              contact=contact, actor=current_user(), work=work)
    proposal = store.record_client_link(proposal["id"], link) or proposal
    store.log_event("CLIENT_LINKED", current_user(),
                    proposal=proposal["id"], client=body["businessName"],
                    filed=link["filed"]["ok"], lead=link["lead"].get("created"),
                    known_client=link["known_client"]["known"])

    return jsonify({"proposal": proposal, "url": f"{MOUNT}/proposal/{proposal['id']}",
                    "client_link": link})


@app.get("/api/proposals")
def api_proposals():
    return jsonify({"proposals": store.list_proposals()})


@app.post("/api/proposals/<public_id>/status")
def api_proposal_status(public_id):
    body = request.get_json(silent=True) or {}
    status = str(body.get("status", "")).upper()
    try:
        proposal = store.set_status(public_id, status)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if not proposal:
        return jsonify({"error": "Proposal not found."}), 404

    store.log_event("PROPOSAL_STATUS_CHANGE", current_user(), proposal=public_id, status=status)
    return jsonify({"proposal": proposal})


@app.post("/api/proposals/<public_id>/comments")
def api_proposal_comment(public_id):
    body = request.get_json(silent=True) or {}
    text = str(body.get("text", "")).strip()
    if not text:
        return jsonify({"error": "Comment text is required."}), 400

    proposal = store.add_comment(public_id, current_user(), text)
    if not proposal:
        return jsonify({"error": "Proposal not found."}), 404

    store.log_event("PROPOSAL_COMMENT", current_user(), proposal=public_id)
    return jsonify({"proposal": proposal})


@app.delete("/api/proposals/<public_id>")
def api_proposal_delete(public_id):
    if not store.delete_proposal(public_id):
        return jsonify({"error": "Proposal not found."}), 404
    store.log_event("PROPOSAL_DELETED", current_user(), proposal=public_id)
    return jsonify({"ok": True})


@app.post("/api/deploy")
def api_deploy():
    body = request.get_json(silent=True) or {}
    public_id = body.get("proposal_id")
    customer_id = body.get("customer_id")
    validate_only = bool(body.get("validate_only"))

    proposal = store.get_proposal(public_id)
    if not proposal:
        return jsonify({"error": "Proposal not found."}), 404
    if not customer_id:
        return jsonify({"error": "Pick a Google Ads account first."}), 400
    if not validate_only and proposal["status"] != "APPROVED":
        return jsonify({
            "error": "Only approved proposals can deploy. Approve it in the Approval Hub first.",
            "code": "NOT_APPROVED",
        }), 400
    if not validate_only and proposal["status"] == "DEPLOYED":
        return jsonify({"error": "This proposal has already been deployed."}), 400

    result = google_ads.deploy_proposal(
        customer_id, proposal["campaign"], store=store,
        campaign_name=body.get("campaign_name"),
        search_partners=bool(body.get("search_partners")),
        validate_only=validate_only,
    )

    if validate_only:
        store.log_event("DEPLOY_DRY_RUN", current_user(),
                        proposal=public_id, operations=result["operation_count"])
    else:
        result["deployed_at"] = datetime.now(timezone.utc).isoformat()
        result["deployed_by"] = current_user()
        store.set_customer_id(public_id, google_ads.digits(customer_id))
        store.mark_deployed(public_id, result)
        store.log_event("CAMPAIGN_DEPLOYED", current_user(),
                        proposal=public_id, customer_id=result["customer_id"],
                        campaign_id=result.get("campaign_id"),
                        campaign=result["campaign_name"],
                        ad_groups=result["ad_group_count"], keywords=result["keyword_count"])

    return jsonify({"result": result})


# --------------------------------------------------------- Bing (phase 2)
@app.route("/api/bing/<path:_rest>", methods=["GET", "POST", "PUT", "DELETE"])
def api_bing(_rest):
    return jsonify({
        "error": "Microsoft Advertising (Bing) is not implemented yet. Google Ads is live.",
        "code": "NOT_IMPLEMENTED",
    }), 501


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "module": "ads_builder", "version": VERSION})


if __name__ == "__main__":  # standalone dev run, outside the Hub
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8010")), debug=False)
