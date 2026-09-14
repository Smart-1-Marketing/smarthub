"""Dedicated paid YouTube workspace using the shared Google Ads connection."""
from __future__ import annotations

import csv
import io
import json
import math
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

from flask import Blueprint, jsonify, render_template, request, session, Response
from sqlalchemy import Column, String, Text, select, update
from sqlalchemy.orm import declarative_base

from .extensions import session_factory, create_all_metadata

bp = Blueprint("youtube_ads", __name__, url_prefix="/tools/youtube-ads")
Base = declarative_base()


class Draft(Base):
    __tablename__ = "youtube_ad_drafts"
    id = Column(String(40), primary_key=True)
    customer_id = Column(String(10), nullable=False, index=True)
    payload = Column(Text, nullable=False)
    state = Column(String(30), nullable=False, default="DRAFT")
    result = Column(Text, default="")
    created_at = Column(String(40), nullable=False)


def now():
    return datetime.now(timezone.utc).isoformat()


def services():
    from modules.ads_builder import google_ads, store
    return google_ads, store


def customer(value):
    value = str(value or "").replace("-", "").strip()
    if not re.fullmatch(r"\d{10}", value):
        raise ValueError("Choose a valid 10-digit Google Ads customer ID.")
    return value


def video_id(value):
    value = str(value or "").strip()
    if re.fullmatch(r"[\w-]{11}", value, re.ASCII):
        return value
    url = urlparse(value)
    if url.hostname in {"youtu.be", "www.youtu.be"}:
        value = url.path.strip("/")
    elif url.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        value = parse_qs(url.query).get("v", [""])[0] if url.path == "/watch" else url.path.split("/")[-1]
    else:
        raise ValueError("Enter a YouTube video URL or video ID.")
    if not re.fullmatch(r"[\w-]{11}", value, re.ASCII):
        raise ValueError("The YouTube video ID must contain 11 characters.")
    return value


def validate(data):
    if not isinstance(data, dict):
        raise ValueError("A campaign object is required.")
    out = {"customer_id": customer(data.get("customer_id")), "video_id": video_id(data.get("video_id"))}
    for key, limit in {"name": 128, "business_name": 25, "headline": 40,
                       "long_headline": 90, "description": 90, "audience_notes": 2000}.items():
        text = str(data.get(key) or "").strip()
        if (not text and key != "audience_notes") or len(text) > limit:
            raise ValueError(f"{key.replace('_', ' ').title()} is required and must be at most {limit} characters.")
        out[key] = text
    url = str(data.get("final_url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a valid HTTPS landing page URL without credentials.")
    out["final_url"] = url
    try:
        budget = float(data.get("daily_budget", 0))
    except (ValueError, TypeError):
        raise ValueError("Enter a positive daily budget.")
    if not math.isfinite(budget) or not 1 <= budget <= 100000:
        raise ValueError("Daily budget must be between 1 and 100,000 in the account currency.")
    out["daily_budget"] = round(budget, 2)
    for key in ("logo_asset_id", "location_id", "language_id"):
        val = str(data.get(key) or "").strip()
        if not re.fullmatch(r"\d{1,20}", val):
            raise ValueError(f"Enter a valid {key.replace('_', ' ')}.")
        out[key] = val
    placements = data.get("placements", [])
    if not isinstance(placements, list) or not placements or any(not isinstance(p, str) or p not in {"youtubeInStream", "youtubeInFeed", "youtubeShorts"} for p in placements):
        raise ValueError("Select at least one YouTube placement.")
    out["placements"] = placements
    if data.get("eu_political") not in ("DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING", "CONTAINS_EU_POLITICAL_ADVERTISING"):
        raise ValueError("Specify whether this campaign contains EU political advertising.")
    out["eu_political"] = data["eu_political"]
    return out


def operations(d):
    root = f"customers/{d['customer_id']}"
    budget, campaign, group, video = (f"{root}/{kind}/{id}" for kind, id in
        [("campaignBudgets", -1), ("campaigns", -2), ("adGroups", -3), ("assets", -4)])
    def create(kind, body):
        return {kind + "Operation": {"create": body}}
    return [
        create("campaignBudget", {"resourceName": budget, "name": d["name"], "amountMicros": str(round(d["daily_budget"] * 1000000)), "explicitlyShared": False}),
        create("campaign", {"resourceName": campaign, "name": d["name"], "status": "PAUSED", "advertisingChannelType": "DEMAND_GEN", "campaignBudget": budget, "maximizeConversions": {}, "containsEuPoliticalAdvertising": d["eu_political"]}),
        create("adGroup", {"resourceName": group, "name": d["name"], "campaign": campaign, "status": "ENABLED", "demandGenAdGroupSettings": {"channelControls": {"selectedChannels": {key: key in d["placements"] for key in ["youtubeInStream", "youtubeInFeed", "youtubeShorts", "discover", "gmail", "display", "maps"]}}}}),
        create("adGroupCriterion", {"adGroup": group, "location": {"geoTargetConstant": f"geoTargetConstants/{d['location_id']}"}}),
        create("adGroupCriterion", {"adGroup": group, "language": {"languageConstant": f"languageConstants/{d['language_id']}"}}),
        create("asset", {"resourceName": video, "youtubeVideoAsset": {"youtubeVideoId": d["video_id"]}}),
        create("adGroupAd", {"adGroup": group, "status": "PAUSED", "ad": {"name": d["name"], "finalUrls": [d["final_url"]], "demandGenVideoResponsiveAd": {"businessName": {"text": d["business_name"]}, "headlines": [{"text": d["headline"]}], "longHeadlines": [{"text": d["long_headline"]}], "descriptions": [{"text": d["description"]}], "videos": [{"asset": video}], "logoImages": [{"asset": f"{root}/assets/{d['logo_asset_id']}"}]}}}),
    ]


def report(cid, days):
    if days not in {"LAST_7_DAYS", "LAST_30_DAYS"}:
        raise ValueError("Choose a 7-day or 30-day reporting window.")
    ga, store = services()
    account = ga.search(cid, "SELECT customer.currency_code, customer.time_zone FROM customer LIMIT 1", store=store)
    rows = ga.search(cid, f"""SELECT campaign.id, campaign.name, campaign.status,
        campaign.advertising_channel_type, metrics.impressions, metrics.clicks,
        metrics.cost_micros, metrics.conversions, metrics.conversions_value,
        metrics.video_trueview_views, metrics.video_trueview_view_rate
        FROM campaign WHERE campaign.advertising_channel_type IN ('VIDEO', 'DEMAND_GEN')
        AND campaign.status != 'REMOVED' AND segments.date DURING {days}""", store=store)
    results = []
    for row in rows:
        c, m = row.get("campaign", {}), row.get("metrics", {})
        cost, conv = float(m.get("costMicros", 0)) / 1e6, float(m.get("conversions", 0))
        impressions, clicks = int(m.get("impressions", 0)), int(m.get("clicks", 0))
        value = float(m.get("conversionsValue", 0))
        advice = []
        if impressions == 0:
            advice.append("No delivery in this window: review status, policy approval, audience and bids.")
        if cost > 0 and conv == 0:
            advice.append("Spend without recorded conversions: verify conversion tracking and landing page before changing bids.")
        if impressions >= 1000 and clicks / impressions < .005:
            advice.append("CTR below the workspace's 0.5% review threshold: test a new hook and call to action. This is a heuristic, not a Google benchmark.")
        if not advice:
            advice.append("Compare creative variants and conversion quality before changing budget; allow for conversion reporting delay.")
        results.append({"id": c.get("id"), "name": c.get("name"), "status": c.get("status"), "type": c.get("advertisingChannelType"), "impressions": impressions, "clicks": clicks, "cost": round(cost, 2), "conversions": conv, "ctr": clicks / impressions * 100 if impressions else None, "cpa": cost / conv if conv else None, "roas": value / cost if cost else None, "recommendations": advice})
        results[-1]["trueview_views"] = int(m["videoTrueviewViews"]) if "videoTrueviewViews" in m else None
        results[-1]["view_rate"] = float(m["videoTrueviewViewRate"]) * 100 if "videoTrueviewViewRate" in m else None
    return {"customer_id": cid, "range": days, "checked_at": now(), "account": account[0].get("customer", {}) if account else {}, "campaigns": results, "scope": "Campaign totals for Video and Demand Gen. Existing campaigns may include non-YouTube placements. Conversion data can lag."}


def install(app, current_user_fn):
    create_all_metadata(Base.metadata)
    app.config["YOUTUBE_ADS_CURRENT_USER"] = current_user_fn
    app.register_blueprint(bp)


@bp.before_request
def guard():
    from flask import current_app
    if not current_app.config["YOUTUBE_ADS_CURRENT_USER"]():
        return jsonify(error="Sign in to use YouTube Ads."), 401
    if request.method == "POST":
        token = session.get("youtube_ads_csrf", "")
        if not token or not secrets.compare_digest(token, request.headers.get("X-CSRF-Token", "")):
            return jsonify(error="Reload the page before making changes."), 403
    return None


@bp.errorhandler(ValueError)
def invalid(exc):
    return jsonify(error=str(exc)), 400


@bp.errorhandler(Exception)
def failed(exc):
    from werkzeug.exceptions import HTTPException
    from flask import current_app
    if isinstance(exc, HTTPException):
        return jsonify(error=exc.description), exc.code
    ga, _ = services()
    if isinstance(exc, ga.GoogleAdsError):
        return jsonify(error=exc.message), 502
    current_app.logger.exception("YouTube Ads request failed")
    return jsonify(error="YouTube Ads could not finish this request. Reload to check the saved state."), 500


@bp.get("/")
def index():
    session.setdefault("youtube_ads_csrf", secrets.token_urlsafe(32))
    ga, store = services()
    return render_template("youtube_ads.html", csrf=session["youtube_ads_csrf"], connection=ga.connection_status(store), active="youtube_ads")


@bp.get("/api/accounts")
def accounts():
    ga, store = services()
    return jsonify(accounts=ga.list_client_accounts(store))


@bp.route("/api/drafts", methods=["GET", "POST"])
def drafts():
    with session_factory()() as db:
        if request.method == "POST":
            data = validate(request.get_json(silent=True))
            row = Draft(id=secrets.token_hex(16), customer_id=data["customer_id"], payload=json.dumps(data), state="DRAFT", created_at=now())
            db.add(row)
            db.commit()
            return jsonify(id=row.id), 201
        cid = customer(request.args.get("customer_id"))
        rows = db.execute(select(Draft).where(Draft.customer_id == cid).order_by(Draft.created_at.desc())).scalars()
        return jsonify(drafts=[{"id": r.id, "state": r.state, "created_at": r.created_at, "data": json.loads(r.payload), "result": json.loads(r.result) if r.result else None} for r in rows])


@bp.post("/api/drafts/<draft_id>/<action>")
def deploy(draft_id, action):
    if action not in {"validate", "create"}:
        raise ValueError("Unknown campaign action.")
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        raise ValueError("A campaign action object is required.")
    cid = customer(body.get("customer_id"))
    with session_factory()() as db:
        row = db.get(Draft, draft_id)
        if not row or row.customer_id != cid:
            return jsonify(error="Draft not found for this account."), 404
        if row.state not in {"DRAFT", "VALIDATED"}:
            return jsonify(error="This draft was already submitted. Check Google Ads before creating a replacement."), 409
        data = json.loads(row.payload)
        if action == "create":
            if row.state != "VALIDATED" or body.get("confirmation") != "CREATE_PAUSED":
                return jsonify(error="Validate this draft and confirm paused creation first."), 409
            claimed = db.execute(update(Draft).where(Draft.id == draft_id, Draft.state == "VALIDATED").values(state="SUBMITTING"))
            if claimed.rowcount != 1:
                return jsonify(error="This draft is already being submitted."), 409
            db.commit()
    ga, store = services()
    try:
        result = ga.request("post", f"/customers/{cid}/googleAds:mutate", {"mutateOperations": operations(data), "partialFailure": False, "validateOnly": action == "validate"}, store=store, customer_id=cid)
    except Exception:
        if action == "create":
            with session_factory()() as db:
                db.execute(update(Draft).where(Draft.id == draft_id).values(state="CHECK_GOOGLE_ADS"))
                db.commit()
        raise
    with session_factory()() as db:
        query = update(Draft).where(Draft.id == draft_id)
        if action == "validate":
            query = query.where(Draft.state.in_(["DRAFT", "VALIDATED"]))
        db.execute(query.values(state="VALIDATED" if action == "validate" else "CREATED_PAUSED", result=json.dumps(result)))
        db.commit()
    return jsonify(state="VALIDATED" if action == "validate" else "CREATED_PAUSED")


@bp.get("/api/report")
def get_report():
    return jsonify(report(customer(request.args.get("customer_id")), request.args.get("range", "LAST_30_DAYS")))


@bp.get("/report.csv")
def export_report():
    data = report(customer(request.args.get("customer_id")), request.args.get("range", "LAST_30_DAYS"))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Account", data["customer_id"], "Currency", data["account"].get("currencyCode"), "Time zone", data["account"].get("timeZone"), "Window", data["range"], "Fetched UTC", data["checked_at"]])
    writer.writerow([data["scope"]])
    fields = ["id", "name", "status", "type", "impressions", "clicks", "cost", "conversions", "ctr", "cpa", "roas", "trueview_views", "view_rate"]
    writer.writerow(fields)
    for row in data["campaigns"]:
        writer.writerow([("'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value) for value in [row[key] for key in fields]])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": 'attachment; filename="youtube-ads-report.csv"', "Cache-Control": "no-store"})
