"""Proposals into Smart 1 Suite — find the contact first, then the opportunity.

## What this replaces

`modules/proposal_builder/ghl.py` already did this, against
`GHL_PRIVATE_INTEGRATION_TOKEN`, `GHL_LOCATION_ID`, `GHL_PIPELINE_ID` and
`GHL_PIPELINE_STAGE_ID`. None of those four are set on this deployment, and
none appear in `env.example` or `render.yaml` — the Suite token here is
`GHL_PRIVATE_TOKEN`. So `api_ready()` returned False on every single save and
the tool reported `{"created": false, "reason": "GHL API env vars not fully
set"}` into a response nobody reads. The feature has never fired in
production. It is the same env-name drift that broke the Pexels key, with the
same shape: the code was fine, the name was not.

The token and the sub-account id come from `hub.ghl_contacts`, which is the
one place this codebase agrees on how to address GoHighLevel — and which
refuses a company id used as a location id, a mix-up that would file every
opportunity against the agency instead of the Smart 1 Marketing sub-account.
The pipeline and its stage are discovered from the API rather than requiring
two more ids nobody has set.

## Contact before opportunity

An opportunity in GoHighLevel needs a contact. Creating one from a business
name alone produces a contact with no email and no phone that a salesperson
can do nothing with, and duplicates the real contact the next time anyone
looks the client up. So the order is:

  1. Search Suite for an existing contact — by email, then phone, then the
     business name.
  2. Found one → attach the opportunity to it. Nobody is asked anything.
  3. Found none → return `needs_contact`, and the caller asks the rep for
     name/email/phone rather than inventing a contact.

`needs_contact` is not an error. The proposal is already saved by the time
this runs; the Suite push is a second step that can be completed a moment
later without losing the upload.
"""
from __future__ import annotations

import os
import re

import requests

BASE = "https://services.leadconnectorhq.com"
VERSION = os.environ.get("GHL_API_VERSION", "2021-07-28")
TIMEOUT = 20

# The stage a new proposal lands in, matched case-insensitively against the
# pipeline's real stage names. Falls back to the pipeline's first stage, which
# is the correct "we just opened this" position in every pipeline we run.
PREFERRED_STAGES = ("proposal sent", "proposal", "quoted", "quote sent",
                    "presentation", "new opportunity", "new lead")


class SuiteError(RuntimeError):
    """Message is safe to show a user — never contains the token."""


def _env(*names: str) -> str:
    for name in names:
        # Render stores quotes literally, so SCANS_CALLBACK_TOKEN="abc"
        # arrives with the quotes attached. Strip them here too.
        value = (os.environ.get(name) or "").strip().strip('"').strip("'")
        if value and value not in ("pit-...", "sk-...", "CHANGEME"):
            return value
    return ""


def _token() -> str:
    """The Suite token, from `hub.ghl_contacts`.

    Not read here any more. That module is the one place this codebase agrees
    on how to talk to GoHighLevel, and two modules resolving the same
    credential from different name lists is how the Pexels key ended up fixed
    twice.
    """
    from . import ghl_contacts
    tok = ghl_contacts.token()
    if tok:
        return tok
    # GHL_PRIVATE_INTEGRATION_TOKEN is the retired Proposal Builder's spelling,
    # kept so an installation still carrying it does not silently stop working.
    return _env("GHL_PRIVATE_INTEGRATION_TOKEN")


def location_id() -> str:
    """The Smart 1 Marketing sub-account every proposal opportunity goes into.

    Delegated to `hub.ghl_contacts.location_id()`, which refuses a value that
    matches the agency company id.

    This function used to fall back to GHL_COMPANY_ID / SUITE_COMPANY_ID, and
    on this deployment those hold the same value — so every opportunity would
    have been filed against the *agency* rather than the sub-account, where
    nobody would go looking for it. `ghl_contacts` documents that companyId and
    locationId are different id spaces; this now honours it.

    Returns "" rather than raising, because `status()` and `push_proposal()`
    both report configuration problems in words instead of failing.
    """
    try:
        from . import ghl_contacts
        return ghl_contacts.location_id()
    except Exception:                                   # noqa: BLE001 — NotConfigured
        return ""


def location_problem() -> str:
    """Why there is no usable location id, in words a person can act on."""
    try:
        from . import ghl_contacts
        ghl_contacts.location_id()
        return ""
    except Exception as exc:                            # noqa: BLE001
        return str(exc)


def configured() -> bool:
    return bool(_token() and location_id())


def status() -> dict:
    """Why this is or is not going to work, in words. Feeds /status."""
    token, loc = bool(_token()), location_id()
    problems = []
    if not token:
        problems.append("No Suite token — set GHL_PRIVATE_TOKEN.")
    if not loc:
        problems.append(location_problem() or
                        "No Smart 1 Marketing location id — set "
                        "GHL_LEAD_LOCATION_ID to the sub-account id.")
    return {"ok": not problems, "token_set": token, "location_id": loc,
            "pipeline": _env("GHL_PIPELINE_ID") or "(discovered from the API)",
            "problems": problems}


def _call(method: str, path: str, *, body=None, params=None):
    token = _token()
    if not token:
        raise SuiteError("Smart 1 Suite is not configured — set GHL_PRIVATE_TOKEN.")
    try:
        r = requests.request(
            method, BASE + path, json=body,
            params={k: v for k, v in (params or {}).items() if v not in (None, "")},
            headers={"Authorization": f"Bearer {token}", "Version": VERSION,
                     "Accept": "application/json", "Content-Type": "application/json"},
            timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SuiteError(f"Couldn't reach Smart 1 Suite ({type(exc).__name__}).") from exc
    if r.status_code in (401, 403):
        # Never echoes the body: HighLevel errors have included token
        # fragments, and this response reaches a browser.
        raise SuiteError(f"Smart 1 Suite rejected the request ({r.status_code}). "
                         f"The token is missing a contacts or opportunities scope.")
    try:
        data = r.json() if r.text else {}
    except ValueError:
        data = {}
    if not r.ok:
        message = data.get("message") or data.get("error") or f"HTTP {r.status_code}"
        if isinstance(message, list):
            message = ", ".join(str(m) for m in message)
        raise SuiteError(f"Smart 1 Suite: {str(message)[:200]}")
    return data


def _digits(value) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _contact_row(raw: dict) -> dict:
    name = " ".join(x for x in [raw.get("firstName"), raw.get("lastName")] if x)
    return {
        "id": raw.get("id") or "",
        "name": (raw.get("contactName") or raw.get("name") or name or "").strip(),
        "email": raw.get("email") or "",
        "phone": raw.get("phone") or "",
        "company": raw.get("companyName") or "",
    }


# --------------------------------------------------------------- contacts ---
def find_contact(email: str = "", phone: str = "", company: str = "",
                 name: str = "") -> dict | None:
    """The existing Suite contact for this client, or None.

    Searched most-identifying first. Email and phone identify a person;
    a company name does not — two contacts at the same business both match it
    — so a company hit is only used when it is unambiguous.
    """
    loc = location_id()
    if not loc:
        raise SuiteError("No Smart 1 Marketing location id is configured.")

    for term in [str(email or "").strip(), _digits(phone)]:
        if not term:
            continue
        try:
            data = _call("GET", "/contacts/", params={"locationId": loc,
                                                      "query": term, "limit": 20})
        except SuiteError:
            raise
        for raw in (data.get("contacts") or []):
            row = _contact_row(raw)
            if email and row["email"].lower() == str(email).strip().lower():
                return row
            if phone and _digits(row["phone"]) and _digits(row["phone"]) == _digits(phone):
                return row

    term = str(company or name or "").strip()
    if not term:
        return None
    data = _call("GET", "/contacts/", params={"locationId": loc,
                                              "query": term, "limit": 20})
    rows = [_contact_row(c) for c in (data.get("contacts") or [])]
    exact = [r for r in rows
             if r["company"].strip().lower() == term.lower()
             or r["name"].strip().lower() == term.lower()]
    # Several contacts at one business is the normal case, and picking one of
    # them is picking wrong four times out of five. Ask instead.
    return exact[0] if len(exact) == 1 else None


def create_contact(business: str, contact_name: str = "", email: str = "",
                   phone: str = "", website: str = "", city: str = "",
                   state: str = "", postal: str = "", source: str = "") -> dict:
    """Create the contact a rep just supplied details for.

    Written through `hub.ghl_contacts.upsert()` rather than a second
    POST /contacts/upsert of our own. One write path per contact is the rule
    that module was built around: it upserts (so a retry updates rather than
    duplicates), and it treats a 2xx with no contact id as a failure, because
    without the id there is nothing to prove the contact exists.
    """
    from . import ghl_contacts
    result = ghl_contacts.upsert({
        "name": str(contact_name or "").strip() or str(business or "").strip(),
        "email": str(email or "").strip(),
        "phone": str(phone or "").strip(),
        "company": str(business or "").strip(),
        "website": str(website or "").strip(),
        "city": str(city or "").strip(),
        "state": str(state or "").strip(),
        "zip": str(postal or "").strip(),
        "source": source or "proposal-builder",
    })
    if not result.get("ok"):
        raise SuiteError(result.get("error") or
                         "Smart 1 Suite created no contact id, so the contact "
                         "cannot be confirmed.")
    return {"id": result["contact_id"],
            "name": str(contact_name or "").strip(),
            "email": str(email or "").strip(),
            "phone": str(phone or "").strip(),
            "company": str(business or "").strip()}


# -------------------------------------------------------------- pipelines ---
def _pipeline_and_stage() -> tuple[str, str]:
    """The pipeline and stage a new proposal opportunity belongs in.

    Discovered rather than configured. The old module required GHL_PIPELINE_ID
    and GHL_PIPELINE_STAGE_ID, neither of which anyone ever set, so it refused
    to do anything. An explicit id still wins when it is set.
    """
    pipeline = _env("GHL_PIPELINE_ID")
    stage = _env("GHL_PIPELINE_STAGE_ID")
    if pipeline and stage:
        return pipeline, stage

    data = _call("GET", "/opportunities/pipelines",
                 params={"locationId": location_id()})
    pipelines = data.get("pipelines") or []
    if not pipelines:
        raise SuiteError("No pipelines exist in the Smart 1 Marketing "
                         "sub-account, so an opportunity has nowhere to go.")
    chosen = None
    if pipeline:
        chosen = next((p for p in pipelines if p.get("id") == pipeline), None)
    if chosen is None:
        # Prefer a pipeline that sounds like sales; otherwise the first one,
        # which is the one a single-pipeline account has.
        chosen = next((p for p in pipelines
                       if any(w in str(p.get("name", "")).lower()
                              for w in ("sales", "proposal", "new business"))),
                      pipelines[0])
    stages = chosen.get("stages") or []
    if not stages:
        raise SuiteError(f"Pipeline '{chosen.get('name')}' has no stages.")
    if stage and any(s.get("id") == stage for s in stages):
        return chosen["id"], stage
    for want in PREFERRED_STAGES:
        hit = next((s for s in stages
                    if want in str(s.get("name", "")).lower()), None)
        if hit:
            return chosen["id"], hit["id"]
    return chosen["id"], stages[0]["id"]


# ----------------------------------------------------------- opportunities ---
def upsert_opportunity(contact_id: str, name: str, value: float = 0.0,
                       opportunity_id: str = "", notes: str = "") -> dict:
    """Create the opportunity, or update the one this proposal already made.

    `opportunity_id` is what keeps a revised proposal from opening a second
    deal for the same client — GoHighLevel has no natural key for "the
    opportunity this proposal made", so the caller stores ours.
    """
    pipeline, stage = _pipeline_and_stage()
    body = {
        "locationId": location_id(),
        "pipelineId": pipeline,
        "pipelineStageId": stage,
        "contactId": contact_id,
        "name": str(name or "Proposal")[:200],
        "status": "open",
        "monetaryValue": round(float(value or 0), 2),
    }
    data = None
    if opportunity_id:
        try:
            data = _call("PUT", f"/opportunities/{opportunity_id}", body={
                "name": body["name"], "monetaryValue": body["monetaryValue"],
                "status": "open", "pipelineId": pipeline,
                "pipelineStageId": stage})
        except SuiteError:
            data = None                 # deleted in Suite — make a new one
    if data is None:
        data = _call("POST", "/opportunities/", body=body)
    raw = data.get("opportunity") or data
    opp_id = raw.get("id") or opportunity_id or ""

    if notes and contact_id:
        try:
            _call("POST", f"/contacts/{contact_id}/notes", body={"body": notes[:2000]})
        except SuiteError:
            pass                        # a missing note is not a failed push
    return {"opportunity_id": opp_id, "pipeline_id": pipeline,
            "stage_id": stage, "created": not opportunity_id}


# ------------------------------------------------------------------ facade ---
def push_proposal(*, client: str, title: str = "", value: float = 0.0,
                  contact: dict | None = None, website: str = "",
                  city: str = "", state: str = "", postal: str = "",
                  pdf_url: str = "", opportunity_id: str = "",
                  note_lines: list | None = None,
                  source: str = "Smart 1 Proposal Builder") -> dict:
    """File one piece of work as a Smart 1 Suite opportunity.

    `note_lines` replaces the composed note for a caller whose work is not a
    proposal — the Commercial Builder files a delivered commercial through
    here, and a note headed "Proposal:" against a finished spot is the wrong
    noun on the one record a salesperson reads. Everything else about the push
    is identical, deliberately: the contact search, the three-answer shape and
    the refusal to invent a contact are what this function is for, and a
    second copy of them for the second caller is how the two come to disagree
    about what "no contact" means.

    Returns one of three shapes, and never raises for a configuration problem:

      {"ok": True,  "opportunity_id": ..., "contact": {...}, "created": bool}
      {"ok": False, "needs_contact": True, "reason": "...", "suggest": {...}}
      {"ok": False, "reason": "..."}                     # not configured, or
                                                         # Suite said no

    The caller has already saved the proposal. Nothing here may lose it.
    """
    client = str(client or "").strip()
    if not client:
        return {"ok": False, "reason": "A client is required."}
    if not configured():
        return {"ok": False, "reason": "; ".join(status()["problems"])
                or "Smart 1 Suite is not configured."}

    supplied = {k: str((contact or {}).get(k) or "").strip()
                for k in ("id", "name", "email", "phone")}
    try:
        found = None
        if supplied["id"]:
            found = {"id": supplied["id"], "name": supplied["name"],
                     "email": supplied["email"], "phone": supplied["phone"],
                     "company": client}
        else:
            found = find_contact(email=supplied["email"], phone=supplied["phone"],
                                 company=client, name=supplied["name"])
        if found is None:
            # Enough to create one? A name plus a way to reach them is the
            # bar -- an opportunity attached to a nameless, contactless
            # record is one no salesperson can act on.
            if supplied["name"] and (supplied["email"] or supplied["phone"]):
                found = create_contact(
                    business=client, contact_name=supplied["name"],
                    email=supplied["email"], phone=supplied["phone"],
                    website=website, city=city, state=state, postal=postal,
                    source=source)
            else:
                return {"ok": False, "needs_contact": True,
                        "reason": f"No Smart 1 Suite contact matches {client}. "
                                  f"Add the contact's name and an email or "
                                  f"phone number and this will create both.",
                        "suggest": {"name": supplied["name"],
                                    "email": supplied["email"],
                                    "phone": supplied["phone"]}}

        if note_lines:
            note = "\n".join(str(line) for line in note_lines if str(line or "").strip())
        else:
            note = f"Proposal: {title or client}"
            if value:
                note += f"\nValue: ${float(value):,.2f}"
            if pdf_url:
                note += f"\nPDF: {pdf_url}"
        result = upsert_opportunity(
            contact_id=found["id"],
            name=title or f"{client} — Marketing Proposal",
            value=value, opportunity_id=opportunity_id, notes=note)
    except SuiteError as exc:
        return {"ok": False, "reason": str(exc)}
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "reason": f"Suite push failed ({type(exc).__name__})."}

    return {"ok": True, "contact": found, **result}


# ==========================================================================
# Reading a prospect back out of the Suite
# ==========================================================================
#
# Everything above writes. These read, and they exist because the Hub needs to
# *show* where a prospect has got to without becoming a second place that
# decides it. Smart 1 Suite is the CRM: it holds the stage, the owner, the
# calls and the texts, and a second pipeline in the Hub would be two systems
# answering "what stage is this at" differently, with nothing on either screen
# saying which to believe — the failure `jsonstore.unmirrored_json_writers()`
# exists to close, wearing a sales pipeline.
#
# So the Hub reads and never writes a stage. Three rules follow:
#
# * **Every one of these raises `SuiteError` rather than returning empty.**
#   "This prospect has no opportunity" and "we could not ask" are different
#   answers, and only the first means there is nothing to chase. The caller
#   turns the exception into *not measured*.
# * **A stage id is not a stage.** `opportunities_for()` resolves the id
#   against the pipeline's own stage names, because an id on a screen names
#   nothing a person can act on — the note `hub/domain_links.py` makes about
#   printing `field_3298` at a rep.
# * **Nothing here is cached across requests.** A stage a rep just moved in
#   Suite has to read back on the next refresh, and a five-minute memo on this
#   is a page confidently showing yesterday's answer.

def contact_snapshot(contact_id: str) -> dict:
    """The Suite contact behind a lead: tags, owner, source, when it landed."""
    contact_id = str(contact_id or "").strip()
    if not contact_id:
        raise SuiteError("No Suite contact id on this lead.")
    data = _call("GET", f"/contacts/{contact_id}")
    raw = data.get("contact") or data
    row = _contact_row(raw)
    row.update({
        "tags": [str(t) for t in (raw.get("tags") or []) if t],
        "source": str(raw.get("source") or ""),
        "assigned_to": str(raw.get("assignedTo") or ""),
        "created": str(raw.get("dateAdded") or "")[:19].replace("T", " "),
        "updated": str(raw.get("dateUpdated") or "")[:19].replace("T", " "),
        "url": _contact_url(contact_id),
    })
    return row


def _contact_url(contact_id: str) -> str:
    """Where a rep opens this contact in Suite.

    Built from the location id, because that is the only part of the address
    that varies. `SUITE_APP_BASE` overrides it for a white-labelled domain —
    a link that 404s is worse than no link, and this deployment's Suite is
    reached on more than one hostname.
    """
    base = (os.environ.get("SUITE_APP_BASE")
            or "https://app.gohighlevel.com").rstrip("/")
    loc = location_id()
    if not (loc and contact_id):
        return ""
    return f"{base}/v2/location/{loc}/contacts/detail/{contact_id}"


def _stage_names() -> dict:
    """`{stage_id: (pipeline name, stage name)}` for this location."""
    data = _call("GET", "/opportunities/pipelines",
                 params={"locationId": location_id()})
    out = {}
    for p in (data.get("pipelines") or []):
        for s in (p.get("stages") or []):
            if s.get("id"):
                out[s["id"]] = (str(p.get("name") or ""), str(s.get("name") or ""))
    return out


def opportunities_for(contact_id: str) -> list[dict]:
    """Every open or closed deal against this contact, newest first."""
    contact_id = str(contact_id or "").strip()
    loc = location_id()
    if not contact_id:
        raise SuiteError("No Suite contact id on this lead.")
    if not loc:
        raise SuiteError("No Smart 1 Marketing location id is configured.")
    data = _call("GET", "/opportunities/search",
                 params={"location_id": loc, "contact_id": contact_id,
                         "limit": 20})
    try:
        names = _stage_names()
    except SuiteError:
        # The deals are worth showing even when the pipeline names are not
        # readable; the stage then says so rather than printing a raw id.
        names = {}
    out = []
    for raw in (data.get("opportunities") or []):
        stage_id = str(raw.get("pipelineStageId") or "")
        pipeline, stage = names.get(stage_id, ("", ""))
        out.append({
            "id": raw.get("id") or "",
            "name": str(raw.get("name") or ""),
            "status": str(raw.get("status") or ""),
            "value": raw.get("monetaryValue"),
            "pipeline": pipeline,
            "stage": stage,
            "stage_measured": bool(stage),
            "updated": str(raw.get("updatedAt") or raw.get("dateUpdated")
                           or "")[:19].replace("T", " "),
        })
    out.sort(key=lambda o: o.get("updated") or "", reverse=True)
    return out


def notes_for(contact_id: str, limit: int = 20) -> list[dict]:
    """The notes on a Suite contact, newest first."""
    contact_id = str(contact_id or "").strip()
    if not contact_id:
        raise SuiteError("No Suite contact id on this lead.")
    data = _call("GET", f"/contacts/{contact_id}/notes")
    out = []
    for raw in (data.get("notes") or []):
        out.append({
            "id": raw.get("id") or "",
            "body": str(raw.get("body") or "")[:2000],
            "created": str(raw.get("dateAdded") or "")[:19].replace("T", " "),
            "by": str(raw.get("userId") or ""),
        })
    out.sort(key=lambda n: n.get("created") or "", reverse=True)
    return out[:max(1, limit)]


def add_note(contact_id: str, body: str) -> dict:
    """Write a note onto the Suite contact.

    The one write in this section, and it is deliberate: notes belong with the
    conversation, which is in Suite, so a note typed on a Hub prospect record
    goes *there* rather than into a second notebook only this Hub can read.
    """
    contact_id = str(contact_id or "").strip()
    body = str(body or "").strip()
    if not contact_id:
        raise SuiteError("No Suite contact id on this lead.")
    if not body:
        raise SuiteError("A note needs some words in it.")
    data = _call("POST", f"/contacts/{contact_id}/notes",
                 body={"body": body[:2000]})
    raw = data.get("note") or data
    return {"id": raw.get("id") or "", "body": body[:2000]}
