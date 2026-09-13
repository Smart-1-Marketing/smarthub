"""Suppression-first audiences, paid reveal ledger, verification and GHL handoff."""
import hashlib
import os
import re
import time
import uuid
from urllib.parse import urlsplit

from hub import industry_prospect_providers as provider
from hub import industry_prospect_store as store
from hub.industry_prospect_store import ProspectError

INDUSTRIES = {"hvac": "HVAC", "roofing": "Roofing", "rv-dealers": "RV dealers",
              "restaurants": "Restaurants", "boat-dealers": "Boat dealers and marinas",
              "legal": "Law firms", "dental": "Dentists", "other": "Other"}
MAX_AGE = 3600


def email(value):
    value = str(value or "").strip().lower()
    return value if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value) else ""


def norm(value):
    return " ".join(re.findall(r"\w+", str(value or "").casefold()))


def domain(value):
    value = str(value or "").strip().lower()
    try:
        host = urlsplit(value if "://" in value else "https://" + value).hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def compact(person, ghl=False):
    organization = person.get("organization") or {}
    first = person.get("firstName" if ghl else "first_name") or ""
    last = person.get("lastName" if ghl else "last_name") or ""
    company = person.get("companyName") if ghl else organization.get("name") or person.get("organization_name")
    website = person.get("website") if ghl else organization.get("primary_domain") or organization.get("website_url") or person.get("website_url")
    emails = [email(person.get("email"))]
    for item in person.get("additionalEmails") or []:
        emails.append(email(item.get("email") if isinstance(item, dict) else item))
    return {"id": str(person.get("id") or ""), "person_id": str(person.get("person_id") or (person.get("person") or {}).get("id") or ""),
            "first": first, "last": last, "name": " ".join(x for x in [first, last] if x),
            "company": company or "", "domain": domain(website),
            "emails": list(dict.fromkeys(x for x in emails if x)),
            "dnd": bool(person.get("dnd") or (person.get("dndSettings") or {}).get("email", {}).get("status") == "active")}


def scope():
    # A credential/location change invalidates coverage without exposing keys.
    return hashlib.sha256((provider.apollo_key() + "|" + provider.ghl.token() + "|" +
                           ",".join(provider.locations())).encode()).hexdigest()


def ready():
    sync = store.get("sync", {})
    if sync.get("phase") != "complete" or sync.get("scope") != scope() or time.time() - sync.get("completed", 0) > MAX_AGE:
        raise ProspectError("Complete a fresh suppression sync before searching or buying (valid for one hour).")
    return sync


def status():
    missing = []
    if not provider.apollo_key():
        missing.append("APOLLO_API_KEY")
    if not provider.ghl.configured():
        missing.append(provider.ghl.why_not())
    sync = store.get("sync", {})
    try:
        ready()
        fresh = True
    except (ProspectError, provider.ghl.NotConfigured):
        fresh = False
    return {"missing": missing, "sync": {k: v for k, v in sync.items() if k not in ("scope", "last_ids")},
            "fresh": fresh, "paid_enabled": os.environ.get("PROSPECT_PAID_ENABLED") == "1",
            "auto_sync": os.environ.get("PROSPECT_AUTO_SYNC") == "1",
            "campaigns": sorted(store.rows("campaign"), key=lambda r: r["created"], reverse=True)[:30],
            "lock": store.get("operation-lock"), "industries": INDUSTRIES}


def start_sync(actor):
    if not provider.apollo_key() or not provider.ghl.configured():
        raise ProspectError("Configure Apollo and the Smart 1 Marketing GHL connection first.")
    sync = {"id": uuid.uuid4().hex, "phase": "ghl", "location_index": 0,
            "locations": provider.locations(), "page": 1, "read": 0, "saved": 0,
            "unmatchable": 0, "started": time.time(), "actor": actor, "scope": scope()}
    store.put("sync", "state", sync)
    return sync


def sync_step():
    sync = store.get("sync", {})
    if sync.get("phase") == "complete":
        return sync
    if not sync or sync.get("scope") != scope():
        raise ProspectError("Start a new suppression sync for this connection.")
    if sync["phase"] == "ghl":
        location = sync["locations"][sync["location_index"]]
        data = provider.ghl_page(location, sync["page"])
        contacts = data["contacts"]
        ids = [str(c.get("id") or "") for c in contacts]
        if any(not x for x in ids) or (ids and ids == sync.get("last_ids")):
            raise ProspectError("GHL pagination did not advance. Suppression is incomplete.")
        if len(contacts) < min(100, max(0, data["total"] - (sync["page"] - 1) * 100)):
            raise ProspectError("GHL returned an incomplete contact page.")
        batch = []
        for contact in contacts:
            row = compact(contact, ghl=True)
            row.update(location=location, generation=sync["id"])
            # Retain deleted contacts conservatively: deleting a CRM row does
            # not grant permission to buy it again or erase DND history.
            old = store.get("ghl:" + location + ":" + row["id"], {})
            row["dnd"] = row["dnd"] or old.get("dnd", False)
            row["emails"] = list(dict.fromkeys(row["emails"] + old.get("emails", [])))
            store.put("ghl:" + location + ":" + row["id"], "suppression", row)
            if row["emails"] or (row["first"] and row["last"] and row["company"]):
                payload = {"first_name": row["first"], "last_name": row["last"],
                           "organization_name": row["company"], "email": next(iter(row["emails"]), ""),
                           "website_url": ("https://" + row["domain"]) if row["domain"] else ""}
                batch.append({k: v for k, v in payload.items() if v})
            else:
                sync["unmatchable"] += 1
        if batch:
            for saved in provider.bulk_save(batch):
                row = compact(saved)
                if not row["id"]:
                    raise ProspectError("Apollo returned a saved contact without an ID.")
                store.put("apollo:" + row["id"], "saved", row)
        sync["read"] += len(contacts)
        sync["last_ids"] = ids
        if sync["page"] * 100 >= data["total"]:
            sync["location_index"] += 1
            sync["page"] = 1
            sync["last_ids"] = []
            if sync["location_index"] >= len(sync["locations"]):
                sync["phase"] = "apollo"
        else:
            sync["page"] += 1
    elif sync["phase"] == "apollo":
        contacts, total = provider.saved_page(sync["page"])
        ids = [str(c.get("id") or "") for c in contacts]
        if any(not x for x in ids) or (ids and ids == sync.get("last_ids")) or (len(contacts) < min(100, max(0, total - (sync["page"] - 1) * 100))):
            raise ProspectError("Apollo saved-contact pagination is incomplete.")
        for contact in contacts:
            row = compact(contact)
            store.put("apollo:" + row["id"], "saved", row)
        sync["saved"] += len(contacts)
        sync["last_ids"] = ids
        if sync["page"] * 100 >= total:
            sync.update(phase="complete", completed=time.time())
        else:
            sync["page"] += 1
    else:
        raise ProspectError("Unknown sync phase. Start a new sync.")
    store.put("sync", "state", sync)
    return sync


def suppression_reason(person, own_id=""):
    row = compact(person)
    pid = str(person.get("id") or "")
    if pid != own_id and store.get("purchase:" + pid):
        return "Previously purchased or attempted"
    full_name = norm(row["name"]) if row["first"] and row["last"] else ""
    for existing in store.rows("suppression") + store.rows("saved"):
        if pid and pid == existing.get("person_id"):
            return "Existing GHL / Apollo contact"
        if set(row["emails"]) & set(existing.get("emails", [])):
            return "Existing email / do not contact" if existing.get("dnd") else "Existing email"
        if full_name and full_name == norm(existing.get("name")) and (
                (row["domain"] and row["domain"] == existing.get("domain")) or
                (row["company"] and norm(row["company"]) == norm(existing.get("company")))):
            return "Existing name and company"
    return ""


def integer(body, key, default, low, high):
    try:
        value = int(body.get(key, default))
    except (TypeError, ValueError):
        raise ProspectError(f"{key} must be a whole number.")
    if not low <= value <= high:
        raise ProspectError(f"{key} must be between {low} and {high}.")
    return value


def new_campaign(body, actor):
    industry = body.get("industry")
    if industry not in INDUSTRIES:
        raise ProspectError("Choose an industry.")
    keywords = str(body.get("keywords") or "").strip()[:150]
    geography = str(body.get("geography") or "").strip()[:150]
    titles = [s.strip()[:80] for s in str(body.get("titles") or "").split(",") if s.strip()][:15]
    landing = str(body.get("landing_page") or "").strip()[:1000]
    try:
        parsed = urlsplit(landing)
    except ValueError as exc:
        raise ProspectError("Enter a valid HTTPS landing page URL.") from exc
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ProspectError("Enter the HTTPS industry landing page URL.")
    if not keywords or not geography or not titles:
        raise ProspectError("Enter industry keywords, company headquarters geography and decision-maker titles.")
    minimum = integer(body, "employees_min", 5, 1, 1000000)
    maximum = integer(body, "employees_max", 100, minimum, 1000000)
    revenue_min = integer(body, "revenue_min", 1000000, 0, 1000000000000)
    revenue_max = integer(body, "revenue_max", 30000000, revenue_min, 1000000000000)
    cid = uuid.uuid4().hex
    campaign = {"id": cid, "created": time.time(), "actor": actor, "industry": industry,
                "name": str(body.get("name") or INDUSTRIES[industry] + " — " + geography)[:120],
                "landing_page": landing, "owner": str(body.get("owner") or "").strip()[:100],
                "filters": {"q_keywords": keywords, "organization_locations[]": [geography],
                            "person_titles[]": titles, "include_similar_titles": "false",
                            "organization_num_employees_ranges[]": [f"{minimum},{maximum}"],
                            "revenue_range[min]": revenue_min, "revenue_range[max]": revenue_max,
                            "contact_email_status[]": ["verified"]}}
    store.put("campaign:" + cid, "campaign", campaign)
    return campaign


def campaign(cid):
    found = store.get("campaign:" + cid)
    if not found:
        raise ProspectError("Audience not found.")
    return found


def search(cid, page):
    ready()
    camp = campaign(cid)
    data = provider.apollo("mixed_people/api_search", params={**camp["filters"], "page": page, "per_page": 25})
    if not isinstance(data.get("people"), list) or not isinstance(data.get("total_entries"), int):
        raise ProspectError("Apollo did not return a valid audience page.")
    results = []
    for person in data["people"]:
        pid = str(person.get("id") or "")
        if not pid or len(pid) > 100:
            raise ProspectError("Apollo candidate has no usable ID.")
        reason = suppression_reason(person)
        store.put("candidate:" + cid + ":" + pid, "candidate",
                  {"campaign": cid, "person": person, "seen": time.time(), "reason": reason})
        row = compact(person)
        row.update(id=pid, title=person.get("title") or "", reason=reason,
                   name=row["name"] or person.get("first_name") or "Name hidden",
                   last_name_obfuscated=person.get("last_name_obfuscated") or "")
        results.append(row)
    return {"campaign": camp, "people": results, "page": page,
            "total": data["total_entries"], "eligible_on_page": sum(not r["reason"] for r in results)}


def paid_enabled():
    if os.environ.get("PROSPECT_PAID_ENABLED") != "1":
        raise ProspectError("Paid operations are disabled. An administrator must enable PROSPECT_PAID_ENABLED after checking provider access and billing.")


def quote(cid, ids, actor):
    paid_enabled()
    ready()
    campaign(cid)
    if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(not isinstance(pid, str) for pid in ids) or len(set(ids)) != len(ids):
        raise ProspectError("Select 1–100 distinct candidates.")
    for pid in ids:
        if not isinstance(pid, str):
            raise ProspectError("Invalid candidate ID.")
        row = store.get("candidate:" + cid + ":" + pid)
        if not row or time.time() - row["seen"] > MAX_AGE:
            raise ProspectError("Search again; one of these candidates is missing or stale.")
        reason = suppression_reason(row["person"])
        if reason:
            raise ProspectError(reason + ". Refresh the audience before purchase.")
    plan = {"id": uuid.uuid4().hex, "campaign": cid, "ids": ids, "actor": actor,
            "created": time.time(), "scope": scope(), "approved": False,
            "max_email_credits": len(ids), "note": "Up to one Apollo credit per selected person; no phone or waterfall enrichment. GHL verification is separately billed at your account rate."}
    store.put("plan:" + plan["id"], "plan", plan)
    return plan


def approve(plan_id, actor, confirmed):
    plan = store.get("plan:" + plan_id)
    if not plan or plan["actor"] != actor or time.time() - plan["created"] > 900 or plan["scope"] != scope():
        raise ProspectError("Purchase review expired. Review your selection again.")
    if confirmed is not True:
        raise ProspectError("Confirm the reviewed Apollo credits and separate GHL verification charges.")
    paid_enabled()
    ready()
    plan["approved"] = True
    store.put("plan:" + plan_id, "plan", plan)
    return plan


def buy_one(plan_id, pid, actor):
    plan = store.get("plan:" + plan_id)
    if not plan or not plan["approved"] or plan["actor"] != actor or pid not in plan["ids"] or plan["scope"] != scope():
        raise ProspectError("This person is not in your approved purchase.")
    previous = store.get("purchase:" + pid)
    if previous:
        return previous
    if time.time() - plan["created"] > MAX_AGE:
        raise ProspectError("Purchase approval expired. Review the remaining candidates again.")
    paid_enabled()
    ready()
    candidate = store.get("candidate:" + plan["campaign"] + ":" + pid)
    if not candidate:
        raise ProspectError("Candidate is missing. Search again.")
    reason = suppression_reason(candidate["person"])
    if reason:
        return {"id": pid, "status": "suppressed", "reason": reason}
    purchase = {"id": pid, "campaign": plan["campaign"], "actor": actor, "created": time.time(),
                "scope": scope(), "status": "reveal_pending", "plan": plan_id}
    store.put("purchase:" + pid, "purchase", purchase)
    try:
        person = provider.enrich(pid).get("person")
        if not isinstance(person, dict) or str(person.get("id")) != pid:
            raise ProspectError("Apollo did not return the selected person. The attempt is held for review.")
        address = email(person.get("email"))
        purchase.update(person=person, email=address, status="revealed")
        store.put("purchase:" + pid, "purchase", purchase)
        reason = suppression_reason(person, own_id=pid)
        if reason or (address and provider.duplicate(address)):
            purchase.update(status="suppressed", reason=reason or "Exact email already in GHL")
        elif not address or person.get("email_status") != "verified":
            purchase.update(status="held", reason="Apollo did not supply a verified business email")
        else:
            purchase["status"] = "verify_pending"
            store.put("purchase:" + pid, "purchase", purchase)
            verdict = provider.verify(address)
            purchase["verification"] = {k: verdict.get(k) for k in ("result", "risk", "address")}
            if provider.deliverable(verdict, address):
                purchase.update(status="ready", verified_at=time.time())
            else:
                purchase.update(status="held", reason="GHL did not confirm a deliverable, low-risk email")
    except ProspectError as exc:
        purchase.update(status="review_required", reason=str(exc))
    store.put("purchase:" + pid, "purchase", purchase)
    return purchase


def import_one(pid, actor):
    row = store.get("purchase:" + pid)
    if not row or row.get("scope") != scope():
        raise ProspectError("Purchase not found for this connection.")
    if row["status"] == "imported":
        return row
    if row["status"] != "ready" or time.time() - row.get("verified_at", 0) > 86400:
        raise ProspectError("Only recently verified, ready prospects can be imported.")
    ready()
    reason = suppression_reason(row["person"], own_id=pid)
    if reason or provider.duplicate(row["email"]):
        row.update(status="suppressed", reason=reason or "Exact email already in GHL")
    else:
        row.update(status="import_pending", imported_by=actor)
        store.put("purchase:" + pid, "purchase", row)
        try:
            row["contact_id"] = provider.import_contact(row["person"], row["email"], campaign(row["campaign"]))
            row.update(status="imported", imported_at=time.time())
            # Local email suppression closes the gap until the next GHL sync.
            saved = compact(row["person"])
            saved["person_id"] = pid
            store.put("imported:" + pid, "suppression", saved)
        except ProspectError as exc:
            row.update(status="review_required", reason=str(exc))
    store.put("purchase:" + pid, "purchase", row)
    return row


def history(cid):
    campaign(cid)
    return [{k: row.get(k) for k in ("id", "campaign", "status", "email", "reason", "contact_id", "created")}
            for row in store.rows("purchase") if row["campaign"] == cid]


def scheduled_step(app):
    if os.environ.get("PROSPECT_AUTO_SYNC") != "1":
        return {"skipped": "Automatic suppression sync is off"}
    with app.app_context():
        with store.operation("scheduler", "suppression sync"):
            sync = store.get("sync", {})
            if not sync or (sync.get("phase") == "complete" and time.time() - sync.get("completed", 0) > 1800):
                start_sync("scheduler")
            elif sync.get("phase") == "complete":
                return {"fresh": True}
            result = sync_step()
            return {"phase": result["phase"], "read": result["read"], "saved": result["saved"]}
