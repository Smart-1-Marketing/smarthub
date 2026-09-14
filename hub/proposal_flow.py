"""Presentation helpers for the proposal-to-order handoff."""
from copy import deepcopy


SCOPE_FIELDS = {
    "client": "Client", "selectedPackage": "Selected package", "items": "Products and prices",
    "months": "Campaign duration", "suiteTier": "Platform licensing", "consulting": "Consulting",
    "creativePlan": "Creative scope", "creativeFee": "Production fee", "targetAreas": "Geography",
    "objectives": "Goals", "kpis": "Measurement", "landingUrl": "Landing page",
    "sections": "Proposal wording", "startDate": "Start date", "endDate": "End date",
    "trackingPlan": "Tracking plan",
}


def scope(state):
    return deepcopy({key: state.get(key) for key in SCOPE_FIELDS})


def approval_changes(state):
    baseline = state.get("_approvedScope")
    if not isinstance(baseline, dict):
        return None
    return [label for key, label in SCOPE_FIELDS.items() if baseline.get(key) != state.get(key)]


def checklist(state, issues):
    """Route existing delivery failures to their editor; IO additions stay advisory."""
    delivery = []
    for message in issues:
        lower = message.lower()
        step = 12 if any(w in lower for w in ("section", "internal pricing")) else (
            11 if "package" in lower else 10 if "creative" in lower else 1 if "client name" in lower else 9)
        delivery.append({"label": message, "step": step})
    io = []
    def need(condition, label, field=None, step=None):
        if condition:
            io.append({"label": label, "field": field, "step": step})
    need(not (state.get("clientContactName") or state.get("clientContactEmail")), "Add a client contact", "clientContactName")
    need(not state.get("startDate"), "Confirm the start date", "startDate")
    need(bool(state.get("endDate") and state.get("startDate") and state["endDate"] < state["startDate"]), "Correct the end date", "endDate")
    tracking = state.get("trackingPlan") or {}
    for key, label in [("primaryConversion", "Define the primary conversion"), ("ga4", "Record GA4 status"),
                       ("callTracking", "Record call tracking status"), ("verifier", "Name the tracking verifier")]:
        need(not tracking.get(key), label, key)
    need(not state.get("managementFeeAck"), "Confirm management fees", "managementFeeAck")
    need(not state.get("creativeSource") and not any(row.get("answer") for row in (state.get("creativePlan") or {}).values()),
         "Confirm the creative source", "creativeSource")
    areas = state.get("targetAreas") or []
    need(not areas and not state.get("geo"), "Confirm target geography", step=5)
    need(any(a.get("zips") and (a.get("zipVerified") is not True or not a.get("zipSource") or "unverified" in a.get("zipSource", "").lower()) for a in areas),
         "Verify ZIP geography and record its source", step=5)
    return {"delivery": delivery, "io": io}
