"""Apollo and GHL adapters. No automatic retries for credit-consuming calls."""
import os

import requests

from hub import ghl_contacts as ghl
from hub.industry_prospect_store import ProspectError


def apollo_key():
    return (os.environ.get("APOLLO_API_KEY") or "").strip()


def locations():
    primary = ghl.location_id()
    extra = (os.environ.get("PROSPECT_SUPPRESSION_LOCATION_IDS") or "").split(",")
    result = list(dict.fromkeys([primary] + [x.strip() for x in extra if x.strip()]))
    if len(result) > 20 or ghl.company_id() in result:
        raise ProspectError("Configure at most 20 GHL sub-account IDs, not the agency ID.")
    return result


def request_json(provider, method, url, **kwargs):
    try:
        response = requests.request(method, url, timeout=(5, 20), **kwargs)
    except requests.RequestException as exc:
        raise ProspectError(f"{provider} could not be reached. Paid attempts are held for review, not retried.") from exc
    if not response.ok:
        raise ProspectError(f"{provider} returned HTTP {response.status_code}. Check credentials, permissions or rate limits.")
    try:
        data = response.json()
    except ValueError as exc:
        raise ProspectError(f"{provider} returned an unreadable response.") from exc
    if not isinstance(data, dict):
        raise ProspectError(f"{provider} returned an unexpected response.")
    return data


def apollo(path, body=None, params=None):
    if not apollo_key():
        raise ProspectError("Set APOLLO_API_KEY before using Apollo.")
    return request_json("Apollo", "POST", "https://api.apollo.io/api/v1/" + path,
                        headers={"x-api-key": apollo_key(), "Accept": "application/json"},
                        json=body, params=params)


def ghl_request(method, path, **kwargs):
    if not ghl.configured():
        raise ProspectError(ghl.why_not())
    return request_json("GHL", method, ghl.BASE + path, headers=ghl._headers(), **kwargs)


def ghl_page(location, page):
    data = ghl_request("POST", "/contacts/search", json={
        "locationId": location, "page": page, "pageLimit": 100})
    if not isinstance(data.get("contacts"), list) or not isinstance(data.get("total"), int):
        raise ProspectError("GHL did not return contacts and a total. Suppression remains incomplete.")
    return data


def bulk_save(contacts):
    data = apollo("contacts/bulk_create", {
        "contacts": contacts, "run_dedupe": True,
        "append_label_names": ["Smart 1 - GHL Suppression"]})
    created, existing = data.get("created_contacts"), data.get("existing_contacts")
    if not isinstance(created, list) or not isinstance(existing, list):
        raise ProspectError("Apollo did not confirm the suppression batch. Resume to reconcile it safely.")
    if len(created) + len(existing) != len(contacts):
        raise ProspectError("Apollo only confirmed part of the suppression batch.")
    return created + existing


def saved_page(page):
    data = apollo("contacts/search", {"page": page, "per_page": 100})
    if not isinstance(data.get("contacts"), list) or not isinstance(data.get("pagination"), dict):
        raise ProspectError("Apollo did not return a complete saved-contact page.")
    total = data["pagination"].get("total_entries")
    if not isinstance(total, int) or total > 50000:
        raise ProspectError("Saved-contact coverage cannot be confirmed (Apollo has a 50,000-record display limit).")
    return data["contacts"], total


def enrich(person_id):
    return apollo("people/match", params={"id": person_id,
        "reveal_personal_emails": "false", "reveal_phone_number": "false",
        "run_waterfall_email": "false", "run_waterfall_phone": "false"})


def duplicate(email):
    for location in locations():
        data = ghl_request("GET", "/contacts/search/duplicate",
                           params={"locationId": location, "email": email})
        if "contact" not in data:
            raise ProspectError("GHL duplicate lookup was inconclusive. Import is held.")
        if data["contact"]:
            return True
    return False


def verify(email):
    if not ghl.configured():
        raise ProspectError(ghl.why_not())
    # The deliverable/risk response contract belongs to Email ISV v3.
    headers = {**ghl._headers(), "Version": "v3"}
    return request_json("GHL verification", "POST", ghl.BASE + "/email/verify",
                        headers=headers, params={"locationId": ghl.location_id()},
                        json={"type": "email", "verify": email})


def deliverable(data, email):
    # Accept only an explicit verdict for the actual address. Unknown schemas
    # and recommendations without a deliverable/low-risk result stay held.
    return (data.get("result") == "deliverable" and data.get("risk") == "low"
            and str(data.get("address") or "").strip().lower() == email
            and (data.get("leadConnectorRecommendation") or {}).get("isEmailValid") is not False)


def import_contact(person, email, campaign):
    organization = person.get("organization") or {}
    body = {"locationId": ghl.location_id(), "email": email,
            "firstName": person.get("first_name") or "",
            "lastName": person.get("last_name") or "",
            "companyName": organization.get("name") or "",
            "website": organization.get("website_url") or "",
            "source": "Smart 1 Industry Prospect Builder",
            "tags": ["s1-industry-prospect", "s1-industry-" + campaign["industry"],
                     "s1-audience-" + campaign["id"]]}
    if campaign.get("owner"):
        body["assignedTo"] = campaign["owner"]
    data = ghl_request("POST", "/contacts/upsert", json={k: v for k, v in body.items() if v})
    contact_id = ghl.contact_id_from(data)
    if not contact_id:
        raise ProspectError("GHL did not confirm a contact ID. Reconcile the import before retrying.")
    return contact_id
