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
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from flask import (Flask, jsonify, make_response, redirect, render_template,
                   request)

from . import VERSION, VERSION_DATE
from hub import target_areas

from . import (ad_intel, api_readiness, campaign_ai, client_link, copy_ideas, export,
               google_ads, keyword_plan, landing_page, logo as logo_lookup,
               monitoring, optimization, spec, store)
from .campaign_ai import SECTOR_CPC, GenerationError, analyse_budget
from .google_ads import GoogleAdsError
from hub.webargs import clamp_int

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
        audience_types=spec.AUDIENCE_TYPES,
        conversion_actions=spec.CONVERSION_ACTIONS,
        area_types=list(target_areas.TYPES),
        openai_configured=bool(campaign_ai.openai_key()),
    )


@app.get("/generator")
def page_generator_alias():
    """Where the generator used to live. One URL, so a bookmark still lands."""
    return redirect(MOUNT + "/")


@app.get("/campaigns")
def page_campaigns():
    return render_template("ads_campaigns.html", status=google_ads.connection_status(store))


@app.get("/optimization")
def page_optimization():
    return render_template("ads_optimization.html", status=google_ads.connection_status(store))


@app.get("/approvals")
def page_approvals():
    rows = store.list_proposals()
    for row in rows:
        row["review"] = store.review_state(row["id"])
    return render_template("ads_approvals.html", proposals=rows, spec=spec)


@app.get("/proposal/<public_id>")
def page_proposal(public_id):
    proposal = store.get_proposal(public_id)
    if not proposal:
        return render_template("ads_error.html", error="That proposal no longer exists."), 404
    campaign = proposal["campaign"]
    return render_template(
        "ads_proposal.html",
        p=proposal,
        status=google_ads.connection_status(store),
        spec=spec,
        sections=spec.sections(campaign),
        areas=target_areas.normalize(campaign.get("targetAreas") or []),
        area_label=target_areas.label,
        area_population=target_areas.estimated_population,
        shares=[{**r, "url": _share_url(r["token"]),
                 "outcome_label": spec.OUTCOME_LABELS.get(r["outcome"], ""),
                 "color": spec.outcome_colour(r["outcome"])}
                for r in store.shares_for(public_id)],
        review=store.review_state(public_id),
        cpc=spec.cpc_provenance(campaign),
        needs_recheck=any(e.get("material") and not e.get("rechecked")
                          for e in campaign.get("editLog") or []),
    )


@app.get("/proposal/<public_id>/client")
def page_client_proposal(public_id):
    proposal = store.get_proposal(public_id)
    if not proposal:
        return render_template("ads_error.html", error="That proposal no longer exists."), 404
    today = datetime.now(timezone.utc)
    campaign = proposal["campaign"]
    return render_template(
        "ads_client_proposal.html",
        p=proposal,
        spec=spec,
        sections=spec.sections(campaign),
        areas=target_areas.normalize(campaign.get("targetAreas") or []),
        area_label=target_areas.label,
        area_population=target_areas.estimated_population,
        cpc=spec.cpc_provenance(campaign),
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


# ================================================================ PUBLIC
# Everything under /estimate/ is served to a client who has no Hub login. It
# is listed in PUBLIC_PREFIXES, which wsgi.py hands to BOTH the AuthGuard (so
# it is reachable) and HubBar (so the Hub's sidebar, help layer and feedback
# tab are not injected into a document a prospect reads). One list, so the
# mount and the module can never disagree about what is public — the same
# arrangement modules/scans uses.
#
# It is read-only except for two writes, both scoped to one token: a change
# request and a response. Neither can reach another proposal, and neither can
# edit the campaign — a client asks for a change, a rep makes it.
PUBLIC_PREFIXES = ("/estimate/",)


def _share_or_none(token):
    share = store.get_share(token)
    if not share or share["revoked"]:
        return None, None
    proposal = store.get_proposal(share["proposal_id"])
    if not proposal:
        return None, None
    return share, proposal


@app.get("/estimate/<token>")
def page_client_estimate(token):
    share, proposal = _share_or_none(token)
    if not share:
        # Deliberately the same answer for revoked, deleted and never-existed:
        # a client-facing page must not confirm which tokens are real.
        return render_template("ads_estimate_gone.html"), 404
    store.note_share_opened(token)
    today = datetime.now(timezone.utc)
    return render_template(
        "ads_estimate.html",
        p=proposal,
        share=share,
        token=token,
        spec=spec,
        sections=spec.sections(proposal["campaign"]),
        areas=target_areas.normalize(proposal["campaign"].get("targetAreas") or []),
        area_label=target_areas.label,
        area_population=target_areas.estimated_population,
        mount=MOUNT,
        cpc=spec.cpc_provenance(proposal["campaign"]),
        outcomes=spec.OUTCOMES,
        today=f"{today:%B} {today.day}, {today.year}",
    )


@app.post("/estimate/<token>/change")
def api_client_change(token):
    """One change request, against one section, from a named person.

    The name and email are required and are not decoration: "the client wants
    the budget lower" is not actionable, and three people at one company will
    disagree with each other. Every request is stamped with who asked.
    """
    share, proposal = _share_or_none(token)
    if not share:
        return jsonify({"error": "This estimate is no longer available."}), 404

    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    email = str(body.get("email") or "").strip()
    text = str(body.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Tell us what you would like changed."}), 400
    if not name or "@" not in email:
        return jsonify({"error": "Please give your name and email so we know who asked."}), 400

    updated = store.add_change_request(token, body.get("section"), text, name, email)
    store.log_event("ESTIMATE_CHANGE_REQUESTED", name or "Client",
                    proposal=share["proposal_id"], client=proposal["client_name"],
                    detail=f"{body.get('section') or 'general'}: {text[:160]}",
                    section=str(body.get("section") or ""), email=email)
    return jsonify({"ok": True, "changes": (updated or {}).get("changes", [])})


@app.post("/estimate/<token>/respond")
def api_client_respond(token):
    share, proposal = _share_or_none(token)
    if not share:
        return jsonify({"error": "This estimate is no longer available."}), 404

    body = request.get_json(silent=True) or {}
    outcome = str(body.get("outcome") or "")
    if outcome not in spec.OUTCOME_KEYS:
        return jsonify({"error": "Choose one of the three options."}), 400
    name = str(body.get("name") or "").strip()
    email = str(body.get("email") or "").strip()
    if not name or "@" not in email:
        return jsonify({"error": "Please give your name and email."}), 400

    updated = store.record_response(token, outcome, name, email, body.get("note"))
    store.log_event("ESTIMATE_RESPONSE", name,
                    proposal=share["proposal_id"], client=proposal["client_name"],
                    detail=spec.OUTCOME_LABELS[outcome], outcome=outcome, email=email)
    return jsonify({
        "ok": True,
        "outcome": outcome,
        "label": spec.OUTCOME_LABELS[outcome],
        "color": spec.outcome_colour(outcome),
        "changes": (updated or {}).get("changes", []),
    })


# ================================================================ /PUBLIC


@app.get("/activity")
def page_activity():
    return render_template("ads_activity.html", events=store.list_events(300))


@app.get("/settings")
def page_settings():
    return render_template(
        "ads_settings.html",
        status=google_ads.connection_status(store),
        # Read, never probed: the probe costs an operation, and a page that
        # spent one on rendering is a page that cannot deploy at 4pm. What is
        # shown is the last answer, stamped, and the button re-asks.
        tier=api_readiness.tier(store),
        tiers=[{"key": k, "label": l, "note": n} for k, l, n in keyword_plan.ACCESS_TIERS],
        openai_configured=bool(campaign_ai.openai_key()),
        openai_model=campaign_ai.openai_model(),
        # Named on the one screen somebody can act on it from -- the variable
        # is here rather than in front of a rep who cannot set it, which is
        # the rule modules/ads_builder/logo.py already works to.
        ad_intel=ad_intel.status(),
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
        return render_template("ads_error.html", error="Google did not return an authorization code."), 400

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
    # An upper bound and no lower one: search_clients ends `[:limit]`, so
    # ?limit=-5 returned every client except the last five, as a clean answer
    # with nothing saying anything was wrong. hub/webargs.py names that as the
    # second of the three faults it exists to end, and `int()` outside a try
    # as the first.
    return jsonify(client_link.search(
        request.args.get("q", ""),
        limit=clamp_int(request.args.get("limit"), 12, 1, 50)))


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

    google_ads.set_campaign_status(
        customer_id, campaign_id, status, store,
        confirmation=body.get("confirmation"),
    )
    store.log_event("CAMPAIGN_STATUS_CHANGE", current_user(),
                    customer_id=google_ads.digits(customer_id),
                    campaign_id=campaign_id, status=str(status).upper())
    return jsonify({"ok": True, "campaign_id": campaign_id, "status": str(status).upper()})


@app.get("/api/optimization/monitor")
def api_optimization_monitor():
    """What the scheduled sweep already found, before anybody presses Scan.

    Reads the stored runs rather than reaching Google: this loads with the
    page, and a panel that costs six operations per account on every visit is
    the per-visit pull the daily quota cannot afford.
    """
    return jsonify(monitoring.account_panel())


@app.post("/api/optimization/auto-apply")
def api_optimization_auto_apply():
    """Turn unattended changes on or off for one Google Ads account.

    A real control rather than a database field nobody can see: auto-apply
    changes a client's live account with nobody pressing anything at the
    moment it happens, so the one thing that must be visible is whether it is
    on. It defaults to off and the write refuses a category outside the
    allowlist rather than storing one the sweep would then have to re-check.
    """
    body = request.get_json(silent=True) or {}
    customer_id = google_ads.digits(body.get("customer_id"))
    if not customer_id:
        return jsonify({"error": "customer_id is required."}), 400
    settings = store.set_auto_apply(
        customer_id, enabled=bool(body.get("enabled")),
        categories=body.get("categories") or [], actor=current_user(),
    )
    store.log_event("OPTIMIZATION_AUTO_APPLY_SET", current_user(),
                    customer_id=customer_id, enabled=settings["enabled"],
                    categories=",".join(settings["categories"]))
    return jsonify({"ok": True, "settings": settings,
                    "categories": list(store.AUTO_APPLY_CATEGORIES)})


@app.get("/api/optimization/summary")
def api_optimization_summary():
    customer_id = request.args.get("customer_id", "")
    if not customer_id:
        return jsonify({"error": "customer_id is required."}), 400
    return jsonify(optimization.account_summary(customer_id, store))


@app.get("/api/optimization/scan")
def api_optimization_scan():
    customer_id = request.args.get("customer_id", "")
    if not customer_id:
        return jsonify({"error": "customer_id is required."}), 400
    result = optimization.scan_account(
        customer_id, request.args.get("date_range", "LAST_30_DAYS"), store
    )
    store.log_event(
        "OPTIMIZATION_SCAN", current_user(), customer_id=google_ads.digits(customer_id),
        date_range=result["date_range"], items=result["item_count"],
    )
    return jsonify(result)


@app.post("/api/optimization/action")
def api_optimization_action():
    body = request.get_json(silent=True) or {}
    customer_id, action = body.get("customer_id"), body.get("action")
    if not customer_id or not action:
        return jsonify({"error": "customer_id and action are required."}), 400
    result = optimization.apply_action(customer_id, action, body, store)
    # Log the normalized result, never the submitted base64 image or other raw
    # browser payload. One event means one approved Google Ads mutation.
    store.log_event(
        "OPTIMIZATION_APPLIED", current_user(), customer_id=result["customer_id"],
        optimization_action=result["action"], **result["detail"],
    )
    return jsonify(result)


@app.post("/api/optimization/ai")
def api_optimization_ai():
    body = request.get_json(silent=True) or {}
    drafts = optimization.ai_drafts({
        "account_name": body.get("account_name"), "focus": body.get("focus"),
        "campaigns": body.get("campaigns") or [],
        "winning_terms": body.get("winning_terms") or [],
        "selected_items": body.get("selected_items") or [],
    })
    store.log_event(
        "OPTIMIZATION_AI_DRAFT", current_user(),
        customer_id=google_ads.digits(body.get("customer_id")),
        focus=str(body.get("focus") or "all")[:40],
        selected_items=min(len(body.get("selected_items") or []), 20),
        ai_used=bool(drafts.get("ai_used")),
    )
    return jsonify(drafts)


@app.post("/api/generate")
def api_generate():
    body = request.get_json(silent=True) or {}
    # The budget is no longer required. A client who does not know what to spend
    # is the ordinary case, and refusing to build anything until they name a
    # number is how the conversation stops before it starts — the AI sizes
    # good/better/best instead, and the campaign is built at the recommended one.
    missing = [f for f in ("businessName", "websiteUrl") if not body.get(f)]
    if missing:
        return jsonify({"error": "Required: " + ", ".join(missing)}), 400

    store.log_event("GENERATION_START", current_user(),
                    client=body.get("businessName"), budget=body.get("budget"))

    # Read the landing page BEFORE writing anything, so the model is given
    # facts about it rather than a URL to imagine. A page that could not be
    # fetched is passed through as "not measured" — never silently skipped,
    # because the prompt then tells the model not to describe it at all.
    observed = landing_page.observe(body.get("websiteUrl"))

    # No key from the browser: the Hub's OPENAI_API_KEY is the only one used.
    campaign = campaign_ai.generate_campaign(body, observed_page=observed)
    campaign["landingPageObserved"] = observed

    # Tiers are asked for either way: with no budget they are the only way to
    # open the conversation, and with one they show what the next step up buys.
    try:
        campaign["budgetTiers"] = campaign_ai.budget_tiers(
            campaign, campaign.get("sectorKey") or "general")
    except GenerationError as exc:
        campaign["budgetTiers"] = {"tiers": [], "error": str(exc)}

    # With no stated budget, the campaign is costed at the recommended tier and
    # says so in as many words — a proposal carrying a number the client never
    # gave, unlabelled, is the confident wrong answer this codebase keeps
    # having to undo.
    if not campaign.get("monthlyBudget"):
        recommended = next((t for t in (campaign["budgetTiers"].get("tiers") or [])
                            if t.get("recommended")), None)
        if recommended:
            campaign["monthlyBudget"] = recommended["monthly"]
            campaign["budgetSource"] = {
                "stated": False, "tier": recommended["key"],
                "note": f"The client has not named a budget. Costed at the "
                        f"{recommended['label']} tier we recommend "
                        f"(${recommended['monthly']:,.0f}/month).",
            }
        else:
            campaign["budgetSource"] = {
                "stated": False, "tier": "",
                "note": "The client has not named a budget and no tier could be sized.",
            }
    else:
        campaign["budgetSource"] = {"stated": True, "tier": "", "note": ""}

    campaign["createdBy"] = current_user()
    campaign["editLog"] = []

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


# ------------------------------------------------------------ target areas
@app.post("/api/areas/preview")
def api_areas_preview():
    """Normalise and size the areas the browser is holding.

    Server-side on purpose. Target areas already carry one JavaScript mirror in
    the Proposal Builder, and ``test_target_areas.py`` exists solely to prove
    the two halves still agree; a second mirror here would need a second such
    test and would drift the first time either side was edited. The browser
    keeps the raw rows and renders whatever this returns — the same choice
    Social Planner made about its calendar, for the same reason.
    """
    body = request.get_json(silent=True) or {}
    rows = target_areas.normalize(body.get("areas") or [])
    out = []
    for area in rows:
        population = target_areas.estimated_population(area)
        out.append({
            **area,
            "label": target_areas.label(area),
            "describe": target_areas.describe(area),
            "population": population,          # None = not measured, never 0
            "complete": not target_areas.is_empty(area),
        })
    total = target_areas.total_population(rows)
    return jsonify({
        "areas": out,
        "summary": target_areas.summary(rows),
        "total_population": total,
        "unsized": target_areas.unsized(rows),
        "types": list(target_areas.TYPES),
        # Said in words rather than left to the reader: a total that omits
        # three unsized areas is not the reach of the campaign.
        "note": ("Reach is estimated and areas are added up without deducting "
                 "overlap." if total else
                 "No area here can be sized yet — that is not measured, not zero."),
    })


# ---------------------------------------------------------------- analysis
def _campaign_or_404(public_id):
    proposal = store.get_proposal(public_id)
    if not proposal:
        return None, (jsonify({"error": "Proposal not found."}), 404)
    return proposal, None


def _save(public_id, campaign):
    return store.update_campaign(public_id, campaign) or store.get_proposal(public_id)


@app.post("/api/proposals/<public_id>/analyse/landing-page")
def api_analyse_landing_page(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    campaign = proposal["campaign"]
    # Re-fetch rather than reuse what generation saw: the whole point of
    # running this again is that somebody changed the page.
    observed = landing_page.observe(campaign.get("websiteUrl"))
    campaign["landingPageObserved"] = observed
    analysis = campaign_ai.analyse_landing_page(campaign, observed)
    analysis["missingForGoals"] = landing_page.missing_for(
        observed, (campaign.get("intake") or {}).get("conversionActions"))
    campaign["landingPageAnalysis"] = analysis
    _save(public_id, campaign)
    store.log_event("LANDING_PAGE_ANALYSED", current_user(),
                    proposal=public_id, client=proposal["client_name"],
                    measured=observed.get("measured"),
                    conversion_points=len(observed.get("conversion_points") or []))
    return jsonify({"analysis": analysis, "observed": observed})


@app.post("/api/proposals/<public_id>/analyse/competitors")
def api_analyse_competitors(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    campaign = proposal["campaign"]
    research = campaign_ai.research_competitors(campaign)
    # Every researched name arrives UNACCEPTED. The client named theirs and
    # those are fact; these are the model's suggestion, and one of them on a
    # client document is us telling them who their competitors are on no
    # authority at all. A person ticks the ones that are real.
    for row in research.get("researched") or []:
        row["accepted"] = False
    campaign["competitorResearch"] = research
    _save(public_id, campaign)
    store.log_event("COMPETITORS_RESEARCHED", current_user(),
                    proposal=public_id, client=proposal["client_name"],
                    found=len(research.get("researched") or []))
    return jsonify({"research": research})


@app.post("/api/proposals/<public_id>/competitors/accept")
def api_accept_competitors(public_id):
    """Which researched competitors a person has vouched for."""
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    wanted = {str(n) for n in (body.get("accepted") or [])}
    campaign = proposal["campaign"]
    research = campaign.get("competitorResearch") or {}
    for row in research.get("researched") or []:
        row["accepted"] = row.get("name") in wanted
    campaign["competitorResearch"] = research
    _save(public_id, campaign)
    accepted = [r["name"] for r in (research.get("researched") or []) if r.get("accepted")]
    store.log_event("COMPETITORS_ACCEPTED", current_user(),
                    proposal=public_id, client=proposal["client_name"],
                    detail=", ".join(accepted)[:200] or "none", count=len(accepted))
    return jsonify({"research": research, "accepted": accepted})


@app.post("/api/proposals/<public_id>/analyse/budget-tiers")
def api_analyse_tiers(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    campaign = proposal["campaign"]
    tiers = campaign_ai.budget_tiers(campaign, campaign.get("sectorKey") or "general")
    campaign["budgetTiers"] = tiers
    _save(public_id, campaign)
    return jsonify({"tiers": tiers})


# ------------------------------------------------------- the Pickaxe workshop
# Ad copy ideas, extension ideas and SEM quote help — internal working notes
# on the proposal screen, saved on the campaign so a colleague opening the
# same proposal reads what was already drafted rather than paying for a second
# draft. Nothing any of them writes reaches the client estimate.

@app.post("/api/proposals/<public_id>/analyse/ad-copy")
def api_ad_copy_ideas(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    campaign = proposal["campaign"]
    ideas = copy_ideas.ad_copy_ideas(campaign)
    ideas["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    campaign["adCopyIdeas"] = ideas
    _save(public_id, campaign)
    store.log_event("AD_COPY_DRAFTED", current_user(),
                    proposal=public_id, client=proposal["client_name"])
    return jsonify({"ideas": ideas})


@app.post("/api/proposals/<public_id>/analyse/ad-extensions")
def api_ad_extension_ideas(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    campaign = proposal["campaign"]
    # Re-fetch rather than reuse what generation saw, the analyse/landing-page
    # rule: the extension ideas are read off the page's own copy, and the
    # point of pressing the button is the page as it stands now.
    observed = landing_page.observe(campaign.get("websiteUrl"))
    campaign["landingPageObserved"] = observed
    ideas = copy_ideas.extension_ideas(campaign, observed)
    ideas["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    campaign["adExtensionIdeas"] = ideas
    _save(public_id, campaign)
    store.log_event("AD_EXTENSIONS_DRAFTED", current_user(),
                    proposal=public_id, client=proposal["client_name"])
    return jsonify({"ideas": ideas})


@app.post("/api/proposals/<public_id>/analyse/sem-quote")
def api_sem_quote(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    campaign = proposal["campaign"]
    answer = copy_ideas.sem_quote(campaign, user=current_user())
    answer["generated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    campaign["semQuote"] = answer
    _save(public_id, campaign)
    store.log_event("SEM_QUOTE_DRAFTED", current_user(),
                    proposal=public_id, client=proposal["client_name"],
                    source=answer.get("source"))
    return jsonify({"answer": answer})


# --------------------------------------------------- measured cost per click
@app.post("/api/proposals/<public_id>/measure-cpc")
def api_measure_cpc(public_id):
    """Ask Google what these keywords cost in these areas.

    Behind a button, never on load: this is a handful of operations against a
    daily cap the deploy also has to fit inside, and a CPC that re-fetched
    itself would change under a client mid-conversation.

    A refusal is saved as well as a success. "We asked Google and the token's
    access tier does not allow it" is a fact the estimate should carry, and
    dropping it would leave the page showing the benchmark with nothing saying
    the measured number had been tried for.
    """
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    customer_id = (request.get_json(silent=True) or {}).get("customer_id") \
        or proposal.get("google_customer_id")
    if not customer_id:
        return jsonify({
            "error": "Pick the Google Ads account to price this against. "
                     "Keyword planning is run through an account, and the "
                     "numbers are the ones that account can see.",
            "code": "NO_CUSTOMER_ID",
        }), 400

    campaign = proposal["campaign"]
    measured = keyword_plan.measure(customer_id, campaign, store=store)
    campaign["cpcMeasured"] = measured
    # A measured CPC that left the tiers costed at the sector rate would show a
    # client two different campaigns on one page. Recomputed, never re-asked:
    # the wording a rep edited and a client may already have read survives.
    campaign["budgetTiers"] = campaign_ai.retier(campaign)
    _save(public_id, campaign)
    store.log_event("CPC_MEASURED", current_user(),
                    proposal=public_id, client=proposal["client_name"],
                    detail=(f"Measured {measured.get('cpc')} via "
                            f"{measured.get('source')}" if measured.get("measured")
                            else f"Not measured: {measured.get('reason', '')[:120]}"),
                    source=measured.get("source"), ok=measured.get("measured"))
    return jsonify({"measured": measured,
                    "provenance": spec.cpc_provenance(campaign)})


@app.get("/api/proposals/<public_id>/preflight")
def api_preflight(public_id):
    """Everything that must be true before this campaign can be deployed."""
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    proposal = {**proposal, "review": store.review_state(public_id)}
    return jsonify(api_readiness.preflight(
        store=store,
        customer_id=request.args.get("customer_id") or proposal.get("google_customer_id"),
        proposal=proposal))


@app.post("/api/planning-check")
def api_planning_check():
    """Does this developer token's access tier allow keyword planning?

    One cheap call, behind a button on Settings. Google publishes the access
    tier nowhere an API can read it, so the only way to know is to make a
    planning call and read the refusal — which is what this does.
    """
    result = api_readiness.check_planning(store)
    store.log_event("PLANNING_CHECK", current_user(),
                    detail=result.get("detail", "")[:200],
                    state=result.get("state"), ok=result.get("available"))
    return jsonify({"planning": result, "tier": api_readiness.tier(store)})


@app.get("/api/access-tier")
def api_access_tier():
    return jsonify({"tier": api_readiness.tier(store),
                    "tiers": [{"key": k, "label": l, "note": n}
                              for k, l, n in keyword_plan.ACCESS_TIERS],
                    "probe": api_readiness.last_probe(store)})


@app.post("/api/proposals/<public_id>/recheck")
def api_recheck(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    campaign = proposal["campaign"]
    pending = [e["what"] for e in (campaign.get("editLog") or []) if not e.get("rechecked")]
    review = campaign_ai.recheck_campaign(campaign, pending)
    campaign["lastRecheck"] = {**review, "at": datetime.now(timezone.utc).isoformat(),
                               "by": current_user(), "changes": pending}
    for entry in campaign.get("editLog") or []:
        entry["rechecked"] = True
    _save(public_id, campaign)
    store.log_event("CAMPAIGN_RECHECKED", current_user(),
                    proposal=public_id, client=proposal["client_name"],
                    verdict=review["verdict"], findings=len(review["findings"]))
    return jsonify({"review": review})


# ------------------------------------------------------------------ edits
def _note_edit(campaign: dict, what: str, *, material: bool = True) -> None:
    """Record what a person changed, and whether it needs a re-check.

    ``material`` is the distinction that matters: a budget change or a stripped
    ad group changes what the campaign will do, and the estimate must not be
    approved without the model looking again. Fixing a typo in the promotion
    text does not.
    """
    campaign.setdefault("editLog", []).append({
        "what": what,
        "at": datetime.now(timezone.utc).isoformat(),
        "by": current_user(),
        "material": bool(material),
        "rechecked": not material,
    })


@app.post("/api/proposals/<public_id>/edit")
def api_edit_campaign(public_id):
    """Edit campaign details, budget, keywords and negatives, in one call.

    One endpoint because one screen does all of it, and because the edit log
    and the "this now needs re-checking" flag have to be written by whichever
    part of the edit was material. Two endpoints meant two places that could
    forget.
    """
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    if proposal["status"] == "DEPLOYED":
        return jsonify({"error": "This campaign has been deployed. Editing it here would "
                                 "no longer describe what is live in Google Ads.",
                        "code": "ALREADY_DEPLOYED"}), 400

    body = request.get_json(silent=True) or {}
    campaign = proposal["campaign"]
    changed = []

    if "monthlyBudget" in body:
        try:
            new_budget = round(float(body.get("monthlyBudget") or 0))
        except (TypeError, ValueError):
            return jsonify({"error": "That budget is not a number."}), 400
        old_budget = round(float(campaign.get("monthlyBudget") or 0))
        if new_budget != old_budget:
            if new_budget <= 0:
                return jsonify({"error": "A budget of zero cannot be quoted. Leave the "
                                         "budget as it is, or pick one of the tiers."}), 400
            campaign["monthlyBudget"] = new_budget
            campaign["budgetSource"] = {"stated": True, "tier": body.get("tier") or "",
                                        "note": ""}
            # The viability line is recomputed here rather than left stale: it
            # is printed beside the budget, and a HEALTHY badge over a budget
            # somebody just quartered is the worst kind of wrong.
            viability = analyse_budget(new_budget, campaign.get("sectorKey") or "general")
            campaign.setdefault("costEstimation", {})["budgetViability"] = {
                "status": viability["status"], "advice": viability["advice"]}
            changed.append(f"Monthly budget ${old_budget:,.0f} → ${new_budget:,.0f}")
            _note_edit(campaign, changed[-1])

    if "intake" in body:
        before = campaign.get("intake") or {}
        after = spec.normalise_intake({**{k: before.get(k) for k in before},
                                       **(body.get("intake") or {})})
        diffs = [k for k in after if after.get(k) != before.get(k)]
        if diffs:
            campaign["intake"] = after
            changed.append("Campaign details edited: " + ", ".join(diffs))
            # Changing who we target or what must not be targeted changes the
            # campaign; changing the phone number does not.
            material = bool({"audienceType", "conversionActions", "doNotTarget",
                             "productOrService", "seasonal"} & set(diffs))
            _note_edit(campaign, changed[-1], material=material)

    if "targetAreas" in body:
        areas = target_areas.normalize(body.get("targetAreas") or [])
        if areas != (campaign.get("targetAreas") or []):
            campaign["targetAreas"] = areas
            changed.append("Target areas: " + (target_areas.summary(areas) or "none"))
            _note_edit(campaign, changed[-1])

    for field in ("businessName", "websiteUrl", "objective", "strategySummary"):
        if field in body:
            value = str(body.get(field) or "").strip()
            if value and value != campaign.get(field):
                campaign[field] = value
                changed.append(f"{field}: {value[:80]}")
                _note_edit(campaign, changed[-1], material=(field == "websiteUrl"))

    # --- keyword and negative removal -------------------------------------
    remove_kw = body.get("removeKeywords") or []
    if remove_kw:
        wanted = {(str(r.get("group", "")), str(r.get("keyword", ""))) for r in remove_kw
                  if isinstance(r, dict)}
        removed = 0
        for group in campaign.get("adGroups") or []:
            keep = []
            for kw in group.get("keywords") or []:
                if (group.get("name", ""), str(kw)) in wanted:
                    removed += 1
                    continue
                keep.append(kw)
            group["keywords"] = keep
        if removed:
            changed.append(f"{removed} keyword(s) removed")
            _note_edit(campaign, changed[-1])

    # --- adding keywords by hand ------------------------------------------
    # A rep knows terms the model does not: the phrase the client's customers
    # actually use, a service line that was missed. Match type is respected as
    # typed — [exact], "phrase", bare — through the same parse_keyword the
    # deploy and the CSV use, so a hand-typed keyword cannot mean one thing
    # here and another in Google.
    add_kw = body.get("addKeywords") or []
    if add_kw:
        added = 0
        for row in add_kw:
            if not isinstance(row, dict):
                continue
            group_name = str(row.get("group") or "")
            terms = row.get("keywords")
            if isinstance(terms, str):
                terms = [t for t in re.split(r"[\n,]", terms)]
            for raw in terms or []:
                parsed = google_ads.parse_keyword(raw)
                if not parsed or not parsed["text"]:
                    continue
                for group in campaign.get("adGroups") or []:
                    if group.get("name") != group_name:
                        continue
                    existing = {str(k).strip().lower() for k in group.get("keywords") or []}
                    text = str(raw).strip()
                    if text.lower() in existing:
                        continue
                    group.setdefault("keywords", []).append(text)
                    added += 1
        if added:
            changed.append(f"Added {added} keyword(s) by hand")
            _note_edit(campaign, changed[-1])

    remove_neg = body.get("removeNegatives") or []
    if remove_neg:
        wanted = {(str(r.get("bucket", "")), str(r.get("term", ""))) for r in remove_neg
                  if isinstance(r, dict)}
        vault = campaign.get("negativeKeywordVault") or {}
        removed = 0
        for bucket, terms in list(vault.items()):
            keep = []
            for term in terms or []:
                if (bucket, str(term)) in wanted:
                    removed += 1
                    continue
                keep.append(term)
            vault[bucket] = keep
        if removed:
            campaign["negativeKeywordVault"] = vault
            changed.append(f"{removed} negative keyword(s) removed")
            # Removing a negative reopens spend the vault existed to stop, so
            # this is always material however small it looks.
            _note_edit(campaign, changed[-1])

    add_neg = body.get("addNegatives") or []
    if add_neg:
        terms = add_neg if isinstance(add_neg, list) else re.split(r"[\n,]", str(add_neg))
        vault = campaign.get("negativeKeywordVault") or {}
        bucket = vault.setdefault("addedByHand", [])
        existing = {str(t).strip().lower() for v in vault.values() for t in v or []}
        added = 0
        for raw in terms:
            text = str(raw).strip()
            if not text or text.lower() in existing:
                continue
            bucket.append(text)
            existing.add(text.lower())
            added += 1
        if added:
            campaign["negativeKeywordVault"] = vault
            changed.append(f"Added {added} negative keyword(s) by hand")
            _note_edit(campaign, changed[-1], material=False)

    if not changed:
        return jsonify({"proposal": proposal, "changed": [], "needs_recheck": False,
                        "note": "Nothing changed."})

    # An edit invalidates an approval. Approving is a statement about a
    # specific document, and letting it survive a budget change would mean the
    # tick on screen refers to something nobody approved.
    estimate = campaign.get("estimate") or {}
    if estimate.get("approved_at"):
        estimate["approved_at"] = ""
        estimate["superseded"] = True
        campaign["estimate"] = estimate

    updated = _save(public_id, campaign)
    store.log_event("CAMPAIGN_EDITED", current_user(),
                    proposal=public_id, client=proposal["client_name"],
                    detail="; ".join(changed)[:300], changes=len(changed))
    return jsonify({
        "proposal": updated,
        "changed": changed,
        "needs_recheck": any(e.get("material") and not e.get("rechecked")
                             for e in campaign.get("editLog") or []),
    })


# ------------------------------------------------------------------- logo
@app.get("/api/proposals/<public_id>/logo")
def api_logo(public_id):
    """What the Hub already has. Never a live lookup on a page load."""
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    campaign = proposal["campaign"]
    if (campaign.get("logo") or {}).get("url"):
        return jsonify({"logo": campaign["logo"]})
    found = logo_lookup.resolve(proposal["client_name"], campaign.get("websiteUrl"))
    if found.get("found"):
        campaign["logo"] = found
        _save(public_id, campaign)
    return jsonify({"logo": found})


@app.post("/api/proposals/<public_id>/logo/lookup")
def api_logo_lookup(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    campaign = proposal["campaign"]
    found = logo_lookup.from_brandfetch(campaign.get("websiteUrl"), proposal["client_name"])
    if found.get("found"):
        campaign["logo"] = found
        _save(public_id, campaign)
        store.log_event("LOGO_LOOKED_UP", current_user(), proposal=public_id,
                        client=proposal["client_name"])
    return jsonify({"logo": found})


@app.post("/api/proposals/<public_id>/logo/upload")
def api_logo_upload(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    upload = request.files.get("logo")
    if not upload or not upload.filename:
        return jsonify({"error": "Choose a file to upload."}), 400
    data = upload.read()
    stored = logo_lookup.store_uploaded(data, upload.filename, proposal["client_name"])
    if not stored.get("found"):
        return jsonify({"error": stored.get("note") or "The logo could not be stored."}), 400
    campaign = proposal["campaign"]
    campaign["logo"] = stored
    _save(public_id, campaign)
    store.log_event("LOGO_UPLOADED", current_user(), proposal=public_id,
                    client=proposal["client_name"], filename=upload.filename)
    return jsonify({"logo": stored})


@app.delete("/api/proposals/<public_id>/logo")
def api_logo_clear(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    campaign = proposal["campaign"]
    campaign.pop("logo", None)
    _save(public_id, campaign)
    return jsonify({"ok": True})


# -------------------------------------------------------- estimate approval
@app.post("/api/proposals/<public_id>/estimate/approve")
def api_approve_estimate(public_id):
    """Approve the estimate — through the model first if it has been edited.

    Two presses, deliberately, and only when something material changed. The
    first returns the re-check rather than approving, so a rep who quartered a
    budget sees what that does to the plan *before* the document they approve
    becomes the one a client reads. Pressing approve again with the re-check on
    screen is an informed second decision, which is the point; approving
    silently through a warning would make the first press meaningless.
    """
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    campaign = proposal["campaign"]
    pending = [e["what"] for e in (campaign.get("editLog") or [])
               if e.get("material") and not e.get("rechecked")]

    if pending and not body.get("acknowledged"):
        review = campaign_ai.recheck_campaign(campaign, pending)
        campaign["lastRecheck"] = {**review, "at": datetime.now(timezone.utc).isoformat(),
                                   "by": current_user(), "changes": pending}
        for entry in campaign.get("editLog") or []:
            if entry.get("material"):
                entry["rechecked"] = True
        _save(public_id, campaign)
        store.log_event("ESTIMATE_RECHECKED", current_user(),
                        proposal=public_id, client=proposal["client_name"],
                        verdict=review["verdict"], changes=len(pending))
        return jsonify({
            "approved": False,
            "needs_review": True,
            "review": review,
            "changes": pending,
            "note": "You changed the estimate, so it went back through the AI review. "
                    "Read what it found, then approve again.",
        })

    campaign["estimate"] = {
        **(campaign.get("estimate") or {}),
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approved_by": current_user(),
        "superseded": False,
    }
    _save(public_id, campaign)
    store.log_event("ESTIMATE_APPROVED", current_user(),
                    proposal=public_id, client=proposal["client_name"])
    return jsonify({"approved": True, "needs_review": False,
                    "estimate": campaign["estimate"]})


# ------------------------------------------------- the client-facing link
@app.post("/api/proposals/<public_id>/share")
def api_create_share(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    if not (proposal["campaign"].get("estimate") or {}).get("approved_at"):
        return jsonify({
            "error": "Approve the estimate before sending it. A client reading a "
                     "version nobody signed off is the one thing this link must not do.",
            "code": "NOT_APPROVED",
        }), 400
    share = store.create_share(public_id, current_user())
    store.log_event("ESTIMATE_SHARED", current_user(),
                    proposal=public_id, client=proposal["client_name"])
    return jsonify({"share": share, "url": _share_url(share["token"])})


@app.get("/api/proposals/<public_id>/shares")
def api_list_shares(public_id):
    proposal, err = _campaign_or_404(public_id)
    if err:
        return err
    rows = store.shares_for(public_id)
    return jsonify({
        "shares": [{**r, "url": _share_url(r["token"]),
                    "outcome_label": spec.OUTCOME_LABELS.get(r["outcome"], ""),
                    "color": spec.outcome_colour(r["outcome"])} for r in rows],
        "review": store.review_state(public_id),
    })


@app.post("/api/proposals/<public_id>/shares/<token>/revoke")
def api_revoke_share(public_id, token):
    if not store.revoke_share(token):
        return jsonify({"error": "No such link."}), 404
    store.log_event("ESTIMATE_LINK_REVOKED", current_user(), proposal=public_id)
    return jsonify({"ok": True})


def _share_url(token: str) -> str:
    """Absolute where the Hub knows its own address, root-relative otherwise.

    This URL is pasted into an email to a client, so a relative path is useless
    — but a guessed host is worse, and PUBLIC_BASE_URL unset means we do not
    know it. The page says so rather than printing a link to nowhere.
    """
    base = (os.environ.get("PUBLIC_BASE_URL", "") or "").rstrip("/")
    return f"{base}{MOUNT}/estimate/{token}" if base else f"{MOUNT}/estimate/{token}"


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
    # The preflight is the refusal, not a second opinion after it. It names
    # every unmet condition at once rather than one per press: a rep who fixes
    # the status only to be told the account is unreachable has been sent round
    # the loop twice for something one screen could have said.
    #
    # The dry run deliberately skips it. Validating is how somebody finds out
    # what is wrong, so gating it behind the same conditions would make the
    # diagnostic unavailable exactly when it is needed.
    if not validate_only:
        checks = api_readiness.preflight(
            store=store, customer_id=customer_id,
            proposal={**proposal, "review": store.review_state(public_id)})
        if not checks["ready"]:
            store.log_event("DEPLOY_BLOCKED", current_user(),
                            proposal=public_id, client=proposal["client_name"],
                            detail="; ".join(checks["blocked"])[:200])
            return jsonify({
                "error": "This campaign is not ready to deploy: "
                         + "; ".join(checks["blocked"]) + ".",
                "code": "PREFLIGHT_FAILED",
                "preflight": checks,
            }), 400

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
