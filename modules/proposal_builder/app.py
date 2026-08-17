"""Smart 1 Proposal Builder — Flask port of server.js, integrated with the Hub.

Authentication is the Hub's shared login: the guard in wsgi.py blocks
unauthenticated requests, so /api/login simply hands the frontend its token
and the login screen never appears. Saves still do the full pipeline:
branded PDF -> Cloudinary, webhook + opportunity -> Smart 1 Suite, searchable
proposal log, and an entry in the Hub-wide activity feed.
"""
import hashlib
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_file, send_from_directory

from hub import audit

from . import ghl, store
from .industries import BLOCKS_SCHEMA, INDUSTRIES, fallback_blocks, industry_list
from .pdfgen import build_proposal_pdf

app = Flask(__name__)
PUBLIC_DIR = Path(__file__).parent / "public"

AUTH_TOKEN = hashlib.sha256((os.environ.get("PANEL_PASSWORD") or "smart1-dev").encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def actor_name():
    return request.environ.get("s1hub.user") or "Unknown"


# The hub guard already authenticated the user; the x-auth token check is kept
# only so direct curl access with a stale token still fails cleanly.
def authed():
    return bool(request.environ.get("s1hub.user")) or request.headers.get("x-auth") == AUTH_TOKEN


def _guard():
    if not authed():
        return jsonify({"error": "Not authorized"}), 401
    return None


@app.route("/")
def index():
    return send_file(PUBLIC_DIR / "index.html")


@app.route("/<path:filename>")
def public_files(filename):
    target = PUBLIC_DIR / filename
    if target.is_file():
        return send_from_directory(PUBLIC_DIR, filename)
    if filename.startswith("api/"):
        return jsonify({"error": "Not found"}), 404
    return send_file(PUBLIC_DIR / "index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "cloudinary": store.cloudinary_ready(),
                    "ghl_api": ghl.api_ready(), "ai": bool(os.environ.get("OPENAI_API_KEY"))})


@app.route("/api/login", methods=["POST"])
def login():
    # Hub login already gates access — always succeed for authenticated staff.
    return jsonify({"ok": True, "token": AUTH_TOKEN})


@app.route("/api/config")
def config():
    gate = _guard()
    if gate:
        return gate
    return jsonify({"industries": industry_list(), "ghl_api": ghl.api_ready(),
                    "ai": bool(os.environ.get("OPENAI_API_KEY"))})


# ---------------- Generate ----------------
def _ai_blocks(industry_key, customer):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return None
    ind = INDUSTRIES[industry_key]
    sys_prompt = (
        "You are a senior media strategist at Smart 1 Marketing, a full-service digital agency "
        "(geofenced display, Connected TV, streaming audio, digital out-of-home, weather-triggered advertising). "
        "Write a persuasive, specific sales proposal for the prospect below as JSON blocks. Rules: use ONLY the "
        "package tiers and prices provided; do not invent statistics or client results; keep body copy tight and "
        'concrete; the first block must be type "heading" with the business name in the title; include one "stats" '
        'block, one "tiers" block (recommended = index of the best-fit tier for their budget), one "table" block '
        'with a first-90-days plan, and end with a "cta" block (url https://smart1marketing.com/free-consultation). '
        "7-10 blocks total."
    )
    import json as _json
    user_prompt = _json.dumps({
        "industry": ind["label"], "industry_intro": ind["intro"], "channels": ind["channels"],
        "demand_triggers": ind["triggers"], "package_tiers": ind["tiers"], "prospect": customer,
    })
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            "temperature": 0.4,
            "max_tokens": 3500,
            "response_format": {"type": "json_schema", "json_schema": BLOCKS_SCHEMA},
            "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_prompt}],
        }, timeout=45,
    )
    if not r.ok:
        raise RuntimeError(f"OpenAI {r.status_code}")
    import json as _json2
    try:  # record spend so /diagnostics doesn't under-report
        from hub import ai as _hub_ai
        _hub_ai.note_usage("proposal_builder", r.json(), purpose="proposal")
    except Exception:  # noqa: BLE001
        pass
    content = (r.json().get("choices") or [{}])[0].get("message", {}).get("content") or "{}"
    blocks = _json2.loads(content).get("blocks")
    return blocks if isinstance(blocks, list) and len(blocks) >= 4 else None


@app.route("/api/generate", methods=["POST"])
def generate():
    gate = _guard()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    industry = body.get("industry")
    customer = body.get("customer") or {}
    if industry not in INDUSTRIES:
        return jsonify({"error": "Pick a valid industry"}), 400
    if not customer.get("business_name"):
        return jsonify({"error": "Business name is required"}), 400
    blocks, engine = None, "template"
    try:
        blocks = _ai_blocks(industry, customer)
        if blocks:
            engine = "ai"
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("AI generation failed, using template: %s", exc)
    if not blocks:
        blocks = fallback_blocks(industry, customer)
    return jsonify({"ok": True, "engine": engine, "industry": industry,
                    "industry_label": INDUSTRIES[industry]["label"], "blocks": blocks})


# ---------------- Save ----------------
def _derive_investment(blocks):
    for b in blocks or []:
        if b.get("type") == "tiers" and isinstance((b.get("data") or {}).get("tiers"), list):
            data = b["data"]
            try:
                i = int(data.get("recommended") or 0)
            except (TypeError, ValueError):
                i = 0
            tiers = data["tiers"]
            t = tiers[i] if 0 <= i < len(tiers) else (tiers[0] if tiers else {})
            num = re.sub(r"[^0-9.]", "", str(t.get("price") or ""))
            try:
                n = float(num)
            except ValueError:
                n = 0
            if n:
                return {"pkg": t.get("name", ""), "monthly": n}
    return {"pkg": "", "monthly": None}


@app.route("/api/proposals", methods=["POST"])
def save_proposal():
    gate = _guard()
    if gate:
        return gate
    body = request.get_json(silent=True) or {}
    pid, mode = body.get("id"), body.get("mode", "new")
    industry = body.get("industry")
    customer = body.get("customer") or {}
    blocks = body.get("blocks") or []
    if industry not in INDUSTRIES:
        return jsonify({"error": "Invalid industry"}), 400
    if not customer.get("business_name"):
        return jsonify({"error": "Business name is required"}), 400
    if not isinstance(blocks, list) or not blocks:
        return jsonify({"error": "Proposal has no blocks"}), 400

    now = _now()
    record = None
    if mode == "update" and pid:
        record = store.get_proposal(pid)
    if not record:
        record = {"id": str(uuid.uuid4()), "created_at": now, "versions": [],
                  "source_id": "" if mode == "update" else (pid or "")}
    record.update({
        "industry": industry, "industry_label": INDUSTRIES[industry]["label"],
        "customer": customer, "blocks": blocks,
        "status": body.get("status") or "saved", "updated_at": now,
    })
    inv = _derive_investment(blocks)
    record["recommended_package"] = inv["pkg"]
    record["monthly_investment"] = inv["monthly"]

    # 1) PDF
    pdf_note = ""
    version = len(record["versions"]) + 1
    try:
        pdf_bytes = build_proposal_pdf(record)
        url = store.upload_pdf(record["id"], version, pdf_bytes)
        if url:
            record["pdf_url"] = url
        else:
            fname = f"{record['id']}-v{version}.pdf"
            with open(os.path.join(store.DATA_DIR, fname), "wb") as fh:
                fh.write(pdf_bytes)
            record["pdf_url"] = request.url_root.rstrip("/") + "/api/pdf/" + fname
            pdf_note = "Cloudinary not configured — PDF stored locally (ephemeral unless the disk is mounted)."
    except Exception as exc:  # noqa: BLE001
        app.logger.error("PDF failed: %s", exc)
        pdf_note = f"PDF generation failed: {exc}"

    record["versions"].append({"v": version, "at": now,
                               "by": customer.get("salesperson", ""), "pdf_url": record.get("pdf_url", "")})

    # 2) Smart 1 Suite
    webhook = ghl.send_webhook(record)
    try:
        ghl_api = ghl.upsert_opportunity(record)
        if ghl_api.get("opportunityId"):
            record["ghl_opportunity_id"] = ghl_api["opportunityId"]
        if ghl_api.get("contactId"):
            record["ghl_contact_id"] = ghl_api["contactId"]
    except Exception as exc:  # noqa: BLE001
        ghl_api = {"created": False, "reason": str(exc)}

    # 3) Log
    store.save_proposal(record)
    audit.log("sales", "proposal_saved", actor=actor_name(),
              name=customer.get("business_name"), version=version,
              monthly=record.get("monthly_investment"))

    return jsonify({"ok": True, "id": record["id"], "version": version,
                    "pdf_url": record.get("pdf_url", ""), "pdf_note": pdf_note,
                    "webhook": webhook, "ghl": ghl_api})


@app.route("/api/pdf/<path:fname>")
def serve_pdf(fname):
    gate = _guard()
    if gate:
        return gate
    if not re.match(r"^[a-z0-9-]+-v\d+\.pdf$", fname, re.I):
        return jsonify({"error": "Bad name"}), 400
    path = os.path.join(store.DATA_DIR, fname)
    if not os.path.isfile(path):
        return jsonify({"error": "Not found"}), 404
    return send_file(path, mimetype="application/pdf")


@app.route("/api/proposals")
def list_proposals():
    gate = _guard()
    if gate:
        return gate
    return jsonify({"ok": True, "results": store.search_proposals(
        q=request.args.get("q", ""), industry=request.args.get("industry", ""),
        salesperson=request.args.get("salesperson", ""), limit=request.args.get("limit", 50))})


@app.route("/api/proposals/<pid>")
def get_proposal(pid):
    gate = _guard()
    if gate:
        return gate
    rec = store.get_proposal(pid)
    if not rec:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"ok": True, "proposal": rec})
