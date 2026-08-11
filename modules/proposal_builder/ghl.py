"""Smart 1 Suite (GHL) delivery — inbound webhook + optional direct API
opportunity creation. Port of lib/ghl.js. Payload keys follow the shared
master field map (smart1-suite-fields/FIELD-MAP.csv)."""
import json
import os

import requests


def _env(name, default=""):
    return os.environ.get(name, default)


def _api(path, method, body=None):
    base = _env("GHL_BASE_URL", "https://services.leadconnectorhq.com")
    r = requests.request(
        method, base + path, json=body,
        headers={
            "Authorization": f"Bearer {_env('GHL_PRIVATE_INTEGRATION_TOKEN')}",
            "Version": _env("GHL_API_VERSION", "2021-07-28"),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }, timeout=20,
    )
    try:
        data = r.json() if r.text else {}
    except ValueError:
        data = {"raw": r.text}
    if not r.ok:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {data.get('message') or str(data)[:200]}")
    return data


def suite_payload(record):
    c = record.get("customer") or {}
    monthly = record.get("monthly_investment")
    return {
        "source": "Smart 1 Proposal Builder",
        "report_status": record.get("status") or "proposal_saved",
        "lead_id": record.get("id"),
        "industry": record.get("industry_label") or record.get("industry"),
        "salesperson": c.get("salesperson", ""),
        "contact_name": c.get("contact_name", ""),
        "contact_email": c.get("contact_email", ""),
        "contact_phone": c.get("contact_phone", ""),
        "business_name": c.get("business_name", ""),
        "website": c.get("website", ""),
        "zip_code": c.get("zip", ""),
        "city": c.get("city", ""),
        "state": c.get("state", ""),
        "notes": c.get("notes", ""),
        "opportunity_name": f"{c.get('business_name') or 'New Prospect'} — {record.get('industry_label') or 'Marketing'} Proposal",
        "recommended_package": record.get("recommended_package", ""),
        "recommended_monthly_investment": monthly,
        "opportunity_value_annual": monthly * 12 if monthly else None,
        "report_pdf_url": record.get("pdf_url", ""),
        "report_generated_at": record.get("updated_at"),
        "proposal_version": len(record.get("versions") or []),
        "report_json": json.dumps({"industry": record.get("industry"), "blocks": record.get("blocks")})[:60000],
    }


def send_webhook(record):
    url = _env("GHL_WEBHOOK_URL")
    if not url:
        return {"sent": False, "reason": "GHL_WEBHOOK_URL not set"}
    try:
        r = requests.post(url, json=suite_payload(record), timeout=12)
        return {"sent": r.ok, "status": r.status_code}
    except requests.RequestException as exc:
        return {"sent": False, "reason": str(exc)}


def api_ready():
    return all(_env(k) for k in (
        "GHL_PRIVATE_INTEGRATION_TOKEN", "GHL_LOCATION_ID", "GHL_PIPELINE_ID", "GHL_PIPELINE_STAGE_ID"))


def upsert_opportunity(record):
    if not api_ready():
        return {"created": False, "reason": "GHL API env vars not fully set (token/location/pipeline/stage)"}
    c = record.get("customer") or {}
    location_id = _env("GHL_LOCATION_ID")
    parts = str(c.get("contact_name", "")).strip().split()
    first, rest = (parts[0] if parts else ""), " ".join(parts[1:])

    contact = _api("/contacts/upsert", "POST", {
        "locationId": location_id,
        "firstName": first or c.get("business_name") or "Prospect",
        "lastName": rest,
        "email": c.get("contact_email") or None,
        "phone": c.get("contact_phone") or None,
        "companyName": c.get("business_name") or None,
        "website": c.get("website") or None,
        "postalCode": c.get("zip") or None,
        "city": c.get("city") or None,
        "state": c.get("state") or None,
        "source": "Smart 1 Proposal Builder",
        "tags": ["proposal-builder", f"industry-{record.get('industry')}"],
    })
    contact_id = (contact.get("contact") or {}).get("id") or contact.get("id")
    if not contact_id:
        return {"created": False, "reason": "contact upsert returned no id"}

    opp_body = {
        "locationId": location_id,
        "pipelineId": _env("GHL_PIPELINE_ID"),
        "pipelineStageId": _env("GHL_PIPELINE_STAGE_ID"),
        "contactId": contact_id,
        "name": f"{c.get('business_name') or 'New Prospect'} — {record.get('industry_label') or 'Marketing'} Proposal",
        "status": "open",
        "monetaryValue": float(record.get("monthly_investment") or 0),
    }
    opp = None
    if record.get("ghl_opportunity_id"):
        try:
            opp = _api(f"/opportunities/{record['ghl_opportunity_id']}", "PUT", {
                "name": opp_body["name"], "monetaryValue": opp_body["monetaryValue"], "status": "open"})
        except RuntimeError:
            opp = _api("/opportunities/", "POST", opp_body)
    else:
        opp = _api("/opportunities/", "POST", opp_body)
    opportunity_id = (opp.get("opportunity") or {}).get("id") or opp.get("id") or record.get("ghl_opportunity_id") or ""

    if record.get("pdf_url"):
        try:
            _api(f"/contacts/{contact_id}/notes", "POST", {
                "body": f"Proposal PDF (v{len(record.get('versions') or [])}): {record['pdf_url']}\n"
                        f"Industry: {record.get('industry_label')}\nMonthly: ${record.get('monthly_investment') or 0}"})
        except RuntimeError:
            pass  # non-fatal
    return {"created": True, "contactId": contact_id, "opportunityId": opportunity_id}
