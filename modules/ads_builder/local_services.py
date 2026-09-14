"""Local Services onboarding and live reads using the existing Ads credentials.

Setup plans are durable Hub records, never claims that Google accepted an ad.
Google campaign creation and verification remain an explicit Google handoff.
"""
from __future__ import annotations

import json
import re
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Blueprint, jsonify, request, render_template
from sqlalchemy import select, update

from . import google_ads, store
from .google_ads import GoogleAdsError

bp = Blueprint("local_services", __name__)
PREFIX = "lsa_setup:"
FIELDS = {
    "business_name": 300, "phone": 60, "website": 500, "country": 2,
    "postal_code": 30, "services": 2000, "areas": 2000,
    "schedule": 1000, "weekly_budget": 30,
}


def actor():
    user = request.environ.get("s1hub.user")
    return (user.get("email") or user.get("name") or "Team") if isinstance(user, dict) else str(user or "Team")


def customer_id(value):
    raw = str(value or "").strip()
    if not re.fullmatch(r"(?:\d{10}|\d{3}-\d{3}-\d{4})", raw):
        raise GoogleAdsError("Choose a Google Ads account or enter its 10-digit account ID.", status=400)
    return google_ads.digits(raw)


def normalize_plan(body):
    if not isinstance(body, dict):
        raise GoogleAdsError("The setup could not be read. Please try saving again.", status=400)
    plan = {}
    for name, limit in FIELDS.items():
        value = body.get(name, "")
        if not isinstance(value, str) or len(value) > limit:
            raise GoogleAdsError(f"Please shorten or correct {name.replace('_', ' ')}.", status=400)
        plan[name] = value.strip()
    plan["mode"] = body.get("mode", "new")
    if plan["mode"] not in {"new", "existing"}:
        raise GoogleAdsError("Choose new or existing account setup.", status=400)
    plan["customer_id"] = customer_id(body["customer_id"]) if body.get("customer_id") else ""
    if plan["country"] and not re.fullmatch(r"[A-Za-z]{2}", plan["country"]):
        raise GoogleAdsError("Use a two-letter country code, such as US or CA.", status=400)
    plan["country"] = plan["country"].upper()
    if plan["weekly_budget"]:
        try:
            budget = Decimal(plan["weekly_budget"])
            if not budget.is_finite() or budget <= 0 or budget > 1000000:
                raise InvalidOperation
        except InvalidOperation:
            raise GoogleAdsError("Enter a positive weekly planning budget up to 1,000,000.", status=400)
    if plan["website"] and not re.match(r"^https?://[^\s/]+", plan["website"]):
        raise GoogleAdsError("Start the website address with https:// or http://.", status=400)
    step = body.get("step", 1)
    if type(step) is not int or not 1 <= step <= 5:
        raise GoogleAdsError("Choose a setup step from 1 to 5.", status=400)
    plan["step"] = step
    return plan


@bp.get("/local-services")
def page():
    return render_template("ads_local_services.html")


@bp.get("/api/local-services/connection")
def connection():
    # A fresh successful Google read is evidence; a stored token alone isn't.
    accounts = google_ads.list_client_accounts(store, force=True)
    errors = [a["error"] for a in accounts if a.get("error")]
    clients = [a for a in accounts if not a.get("is_manager") and not a.get("error")]
    return jsonify({"verified": not errors, "accounts": clients,
                    "errors": errors, "checked_at": datetime.now(timezone.utc).isoformat(),
                    "manager_id": google_ads.format_customer_id(google_ads.cfg()["login_customer_id"])})


@bp.get("/api/local-services/setups")
def setups():
    with store.SessionLocal() as session:
        values = session.execute(select(store.Setting.value).where(
            store.Setting.key.like(PREFIX + "%"))).scalars().all()
    rows = sorted((json.loads(value) for value in values),
                  key=lambda r: r.get("updated_at", ""), reverse=True)
    return jsonify({"setups": rows})


@bp.post("/api/local-services/setups")
def save_setup():
    body = request.get_json(silent=True)
    plan = normalize_plan(body)
    supplied_id = body.get("id")
    if supplied_id and not re.fullmatch(r"[a-f0-9]{24}", str(supplied_id)):
        raise GoogleAdsError("That saved setup could not be found.", status=404)
    pid = supplied_id or secrets.token_hex(12)
    key = PREFIX + pid
    with store.SessionLocal() as session:
        row = session.get(store.Setting, key)
        if supplied_id and not row:
            raise GoogleAdsError("That saved setup could not be found.", status=404)
        previous = json.loads(row.value) if row else {}
        revision = previous.get("revision", 0)
        if row and body.get("revision") != revision:
            raise GoogleAdsError("Someone updated this setup. Reopen the saved setup before editing it.", status=409)
        plan.update(id=pid, revision=revision + 1, updated_by=actor(),
                    updated_at=datetime.now(timezone.utc).isoformat())
        encoded = json.dumps(plan)
        if row:
            changed = session.execute(update(store.Setting).where(
                store.Setting.key == key, store.Setting.value == row.value
            ).values(value=encoded))
            if changed.rowcount != 1:
                raise GoogleAdsError("This setup changed. Reopen it before saving.", status=409)
        else:
            session.add(store.Setting(key=key, value=encoded))
        session.commit()
    store.log_event("LSA_SETUP_SAVED", actor(), setup_id=pid, customer_id=plan["customer_id"], step=plan["step"])
    return jsonify(plan)


CAMPAIGN_FIELDS = """campaign.id, campaign.name, campaign.status,
    campaign.advertising_channel_type, campaign.bidding_strategy_type,
    campaign_budget.amount_micros, campaign_budget.period"""


def review_account(cid, days=30):
    cid = customer_id(cid)
    account_rows = google_ads.search(cid, """SELECT customer.id,
        customer.descriptive_name, customer.currency_code, customer.time_zone
        FROM customer""", store=store)
    if not account_rows:
        raise GoogleAdsError("Google returned no account details. Check account access.", status=502)
    account = account_rows[0].get("customer", {})
    try:
        tz = ZoneInfo(account.get("timeZone") or "UTC")
    except ZoneInfoNotFoundError:
        raise GoogleAdsError("The account time zone could not be read. Please try again.", status=502)
    today = datetime.now(tz).date()
    start, end = (today - timedelta(days=days)).isoformat(), today.isoformat()
    # Complete days only, with identical bounds for spend and lead creation.
    queries = {
        "campaigns": f"SELECT {CAMPAIGN_FIELDS} FROM campaign WHERE campaign.advertising_channel_type = 'LOCAL_SERVICES' AND campaign.status != 'REMOVED'",
        "local_services_pmax": f"SELECT {CAMPAIGN_FIELDS} FROM campaign WHERE campaign.pmax_campaign_settings.local_services_enabled = true AND campaign.status != 'REMOVED'",
        "spend": f"SELECT campaign.id, metrics.cost_micros FROM campaign WHERE segments.date >= '{start}' AND segments.date < '{end}' AND campaign.status != 'REMOVED'",
        "leads": f"""SELECT local_services_lead.id, local_services_lead.lead_type,
            local_services_lead.lead_status, local_services_lead.creation_date_time,
            local_services_lead.service_id, local_services_lead.lead_charged,
            local_services_lead.credit_details.credit_state
            FROM local_services_lead
            WHERE local_services_lead.creation_date_time >= '{start} 00:00:00'
              AND local_services_lead.creation_date_time < '{end} 00:00:00'
            ORDER BY local_services_lead.creation_date_time DESC""",
        "verification": """SELECT local_services_verification_artifact.id,
            local_services_verification_artifact.creation_date_time,
            local_services_verification_artifact.artifact_type,
            local_services_verification_artifact.status
            FROM local_services_verification_artifact
            ORDER BY local_services_verification_artifact.creation_date_time DESC LIMIT 50""",
    }
    datasets, errors = {}, {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(google_ads.search, cid, q, store=store): name for name, q in queries.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                datasets[name] = future.result()
            except GoogleAdsError as exc:
                errors[name] = exc.message
                datasets[name] = []
    campaigns = {}
    for row in datasets["campaigns"] + datasets["local_services_pmax"]:
        c, b = row.get("campaign", {}), row.get("campaignBudget", {})
        campaigns[str(c.get("id"))] = {"id": str(c.get("id")), "name": c.get("name") or "Local Services campaign",
            "status": c.get("status"), "channel": c.get("advertisingChannelType"),
            "bidding": c.get("biddingStrategyType"), "budget": google_ads.micros(b.get("amountMicros")),
            "budget_period": b.get("period", "UNKNOWN")}
    leads = [r.get("localServicesLead", {}) for r in datasets["leads"]]
    charged = sum(bool(l.get("leadCharged")) for l in leads)
    discovery_complete = not any(k in errors for k in ("campaigns", "local_services_pmax"))
    spend = None if "spend" in errors or not discovery_complete else sum(
        google_ads.micros(r.get("metrics", {}).get("costMicros"))
        for r in datasets["spend"] if str(r.get("campaign", {}).get("id")) in campaigns)
    # LSA PMax lead attribution differs; do not imply legacy lead rows cover it.
    pmax = any(c["channel"] == "PERFORMANCE_MAX" for c in campaigns.values())
    advice = []
    if not campaigns:
        advice.append("Campaign lookup is incomplete. Retry before starting another campaign." if not discovery_complete else
                      "No Local Services campaign was found. Complete setup in Google, then check again here.")
    if any(c["status"] == "PAUSED" for c in campaigns.values()):
        advice.append("A Local Services campaign is paused. Review its budget and Google verification before enabling it.")
    pending = sum(l.get("leadStatus") == "NEW" for l in leads)
    if pending:
        advice.append(f"{pending} leads still have New status in Google. Review them for follow-up; the status alone does not prove a missed call.")
    if not leads and "leads" not in errors:
        advice.append("No lead records were returned for this period. Check service coverage, schedule and verification in Google before increasing the budget.")
    if pmax:
        advice.append("This account has Local Services Performance Max. Lead records below may not cover that campaign; review its lead reporting in Google.")
    advice.append("Review services, coverage areas and answering hours against the work your team can handle.")
    return {"account": account, "customer_id": cid, "campaigns": list(campaigns.values()),
            "discovery_complete": discovery_complete, "spend": spend,
            "lead_count": None if "leads" in errors else len(leads),
            "charged_leads": None if "leads" in errors else charged,
            "cost_per_charged_lead": spend / charged if spend is not None and charged and "leads" not in errors and not pmax else None,
            "leads": leads[:100], "verification": [r.get("localServicesVerificationArtifact", {}) for r in datasets["verification"]],
            "errors": errors, "advice": advice, "start_date": start, "end_date_exclusive": end,
            "checked_at": datetime.now(timezone.utc).isoformat()}


@bp.get("/api/local-services/review")
def review():
    cid = customer_id(request.args.get("customer_id"))
    days = request.args.get("days", "30")
    if days not in {"7", "30", "90"}:
        raise GoogleAdsError("Choose 7, 30 or 90 days.", status=400)
    return jsonify(review_account(cid, int(days)))


@bp.after_request
def private_response(response):
    response.headers["Cache-Control"] = "no-store"
    return response
