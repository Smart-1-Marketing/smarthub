"""Leads written straight into Smart 1 Suite (GoHighLevel) over the API.

## Why this replaces the webhook rather than joining it

Delivery used to be a POST to an inbound webhook URL. That works, but the only
thing it can tell you is that GoHighLevel accepted an HTTP request — not that a
contact exists. `leads.deliver()` recorded `delivered: True` on any 2xx, which
is the weakest possible confirmation and exactly the kind of confident-looking
zero this codebase keeps getting caught by.

The Contacts API returns the contact id. That is proof, and it is what makes
the lead panel's "delivered" column mean something.

**There is deliberately no webhook fallback per lead.** It is tempting: if the
API call fails, fire the webhook instead. It is also wrong. The failure that
matters most — a timeout, or a 5xx after GoHighLevel has already written the
record — is precisely the case where you cannot tell whether the contact
exists. Falling back there creates the duplicate, and there is nothing to
reconcile the two writes against afterwards. So the mode is chosen once, from
configuration, and one lead is only ever written down one path. See
`leads.delivery_mode()`.

The safety net is the one already in `hub/leads.py`: the row is stored before
anything is sent, and `retry_undelivered()` retries down the *same* path.

## What makes a retry safe

Two things, belt and braces:

1. **We record the contact id.** A row that already has one is delivered; a
   retry is a no-op rather than a second write. This is the guarantee we
   control, and it holds whatever GoHighLevel is configured to do.
2. **Upsert, not create.** `POST /contacts/upsert` matches an existing contact
   on email/phone within the location and updates it instead of adding another.
   Worth knowing: it honours the location's own **Allow Duplicate Contact**
   setting, so if that is set to permit duplicates, upsert will happily make
   one. `preflight()` reports this so it is a decision rather than a surprise.

## companyId is not locationId

These are different id spaces and mixing them is a live bug in this codebase:
`qa._accounting_location()` accepted `SUITE_COMPANY_ID` as a *location*
override, and `ghl_forms.summary()` passed `GHL_COMPANY_ID` as a *location*.
Where those two env vars hold the same value — which is how this deployment is
set up — both silently address the agency instead of the sub-account.

So the location id here is its own setting, and `location_id()` refuses a value
that matches the company id rather than writing your leads somewhere nobody
will look for them.
"""
from __future__ import annotations

import os
import re

BASE = (os.environ.get("GHL_API_BASE") or "https://services.leadconnectorhq.com").rstrip("/")
VERSION = os.environ.get("GHL_API_VERSION") or "2021-07-28"
UPSERT_PATH = os.environ.get("GHL_CONTACT_UPSERT_PATH") or "/contacts/upsert"
TIMEOUT = int(os.environ.get("GHL_API_TIMEOUT") or 20)

# The token is read under the same names the rest of the Hub already uses, so
# there is one credential to rotate rather than a new one for this path.
TOKEN_ENV = ("SMART1SUITE_PRIVATE_TOKEN", "GHL_PRIVATE_TOKEN")
COMPANY_ENV = ("GHL_COMPANY_ID", "SUITE_COMPANY_ID")
# Deliberately its own name. Reusing a *_COMPANY_ID here is the bug above.
LOCATION_ENV = ("GHL_LEAD_LOCATION_ID", "SMART1_MARKETING_LOCATION_ID",
                "GHL_ACCOUNTING_LOCATION_ID")

# The sub-account leads belong to, when it has to be found by name.
LOCATION_NAME = os.environ.get("GHL_LEAD_LOCATION_NAME") or "Smart 1 Marketing"

_cache: dict = {}


class NotConfigured(RuntimeError):
    """Raised with a message safe to show a member of staff."""


def _env(*names: str) -> str:
    for n in names:
        # Render stores quotes literally, which has silently broken token and
        # callback matching here before.
        v = (os.environ.get(n) or "").strip().strip('"').strip("'")
        if v and not v.startswith(("pit-...", "<", "your-")):
            return v
    return ""


def token() -> str:
    return _env(*TOKEN_ENV)


def company_id() -> str:
    return _env(*COMPANY_ENV)


def location_id() -> str:
    """The sub-account leads are written into.

    Raises rather than guessing. A lead written to the wrong location is worse
    than one that failed loudly: nobody goes looking for it.
    """
    loc = _env(*LOCATION_ENV)
    if not loc:
        raise NotConfigured(
            "No lead location is set. Put the Smart 1 Marketing sub-account id "
            "in GHL_LEAD_LOCATION_ID. It is the location (sub-account) id, not "
            "the agency company id — they are different ids and the API will "
            "not tell you which one you sent.")
    company = company_id()
    if company and loc == company:
        raise NotConfigured(
            "GHL_LEAD_LOCATION_ID is set to the same value as the agency "
            f"company id ({_mask(loc)}). A companyId used as a locationId "
            "addresses the agency, not the Smart 1 Marketing sub-account, so "
            "leads would not arrive where anyone is looking. Use the "
            "sub-account id from Settings → Business Profile, or call "
            "/api/leads/ghl/preflight?find=1 to look it up by name.")
    return loc


def _mask(v: str) -> str:
    v = str(v or "")
    return (v[:4] + "…" + v[-3:]) if len(v) > 9 else "set"


def configured() -> bool:
    """Is API delivery usable? Never raises — this decides the delivery mode."""
    if not token():
        return False
    try:
        return bool(location_id())
    except NotConfigured:
        return False


def why_not() -> str:
    """The reason API delivery is unavailable, for the panel. "" when it is."""
    if not token():
        return ("No Smart 1 Suite token — set GHL_PRIVATE_TOKEN (or "
                "SMART1SUITE_PRIVATE_TOKEN) with the contacts.write scope.")
    try:
        location_id()
    except NotConfigured as exc:
        return str(exc)
    return ""


def _headers() -> dict:
    return {"Authorization": f"Bearer {token()}", "Version": VERSION,
            "Accept": "application/json", "Content-Type": "application/json"}


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def contact_id_from(data) -> str:
    """Pull the contact id out of whatever shape came back.

    Deliberately tolerant. The id is the entire point of using the API — it is
    what lets a lead be marked delivered honestly — so it is worth reading a
    few plausible shapes rather than marking a real delivery as failed because
    a key was nested one level differently than expected.
    """
    if not isinstance(data, dict):
        return ""
    for holder in (data.get("contact"), data.get("data"), data):
        if not isinstance(holder, dict):
            continue
        for key in ("id", "_id", "contactId", "contact_id"):
            v = holder.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _was_new(data) -> bool | None:
    """True/False if GoHighLevel said whether it created or matched; else None.

    None means "it didn't say" and is reported as such rather than as False.
    """
    if not isinstance(data, dict):
        return None
    for holder in (data, data.get("contact") or {}):
        if isinstance(holder, dict):
            for key in ("new", "isNew", "created"):
                if isinstance(holder.get(key), bool):
                    return holder[key]
    return None


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------

_NAME_SPLIT = re.compile(r"\s+")


def _split_name(full: str) -> tuple[str, str]:
    parts = [p for p in _NAME_SPLIT.split(str(full or "").strip()) if p]
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def report_field_ids() -> dict:
    """The custom-field ids the report links are written into, read at call
    time under the names hub/config.py declares. Empty where unset."""
    from hub.config import LEAD_PDF_FIELD_ENV, LEAD_REPORT_FIELD_ENV
    return {"report_url": _env(LEAD_REPORT_FIELD_ENV),
            "pdf_url": _env(LEAD_PDF_FIELD_ENV)}


def report_links(row: dict) -> dict:
    """What a lead row carries that a Suite workflow would want to email,
    and whether it can be sent.

    `report_url` is the audit or scan page (the row's `meta`), `pdf_url` the
    row's own field. `sent` names the ones a configured custom field will
    carry; `dropped` names the ones the row holds and no field is configured
    for -- which is the state the leads panel and the status page say out
    loud, because a workflow emailing a blank link is worse than one that
    never fires.
    """
    meta = row.get("meta") or {}
    links = {"report_url": str(meta.get("report_url") or meta.get("audit_url") or "").strip()[:500],
             "pdf_url": str(row.get("pdf_url") or "").strip()[:500]}
    ids = report_field_ids()
    return {
        **links,
        "sent": [k for k, v in links.items() if v and ids.get(k)],
        "dropped": [k for k, v in links.items() if v and not ids.get(k)],
        "field_ids": ids,
    }


def payload_for(row: dict) -> dict:
    """The upsert body for one stored lead row.

    Only fields GoHighLevel has a real home for are mapped. Anything else stays
    in the Hub's own store, which already has it — inventing custom fields here
    would need their ids and would silently drop the value when they don't
    exist. The two links a workflow emails are the exception, and only under
    ids somebody configured: see `report_links()`.
    """
    from hub import lead_tags
    loc = location_id()
    fields = row.get("fields") or {}

    def pick(*names: str) -> str:
        for n in names:
            v = row.get(n) or fields.get(n)
            if v:
                return str(v).strip()[:300]
        return ""

    first, last = _split_name(pick("name", "full_name"))
    body = {
        "locationId": loc,
        "firstName": pick("first_name", "firstName") or first,
        "lastName": pick("last_name", "lastName") or last,
        "email": pick("email"),
        "phone": pick("phone"),
        "companyName": pick("company", "companyName", "business"),
        "website": pick("website", "url", "domain"),
        "address1": pick("address", "address1"),
        "city": pick("city"),
        "state": pick("state"),
        "postalCode": pick("zip", "postcode", "postalCode"),
        # Where it came from, so Suite workflows can route on it exactly as
        # they did on the webhook's tags.
        "source": (f"Smart 1 Hub · {row.get('source') or 'lead'}"
                   + (f" · {row.get('page')}" if row.get("page") else ""))[:300],
        "tags": lead_tags.tags_for(row),
    }
    links = report_links(row)
    custom = [{"id": links["field_ids"][k], "field_value": links[k]}
              for k in links["sent"]]
    if custom:
        body["customFields"] = custom
    return {k: v for k, v in body.items() if v not in ("", None, [])} | {"locationId": loc}


# ---------------------------------------------------------------------------
# The write
# ---------------------------------------------------------------------------

def upsert(row: dict) -> dict:
    """Create or update one contact. Returns a result dict; never raises.

    `ok` is True only when a contact id came back. Anything else — including a
    2xx with an unreadable body — is a failure, because without the id there is
    nothing to prove the lead landed and nothing to make a retry safe.
    """
    out = {"ok": False, "contact_id": "", "status": 0, "error": "",
           "was_new": None, "retryable": True}
    try:
        body = payload_for(row)
    except NotConfigured as exc:
        out["error"] = str(exc)
        out["retryable"] = False           # config, not weather
        return out

    if not (body.get("email") or body.get("phone")):
        out["error"] = "The lead has neither an email nor a phone, so there is nothing to match a contact on."
        out["retryable"] = False
        return out

    try:
        import requests
        r = requests.post(BASE + UPSERT_PATH, json=body, headers=_headers(),
                          timeout=TIMEOUT)
    except Exception as exc:                              # noqa: BLE001
        # Includes the timeout case. We genuinely do not know whether the write
        # landed — which is why the retry goes back through upsert, and why
        # there is no second channel to fall back to.
        out["error"] = f"Couldn't reach Smart 1 Suite ({type(exc).__name__}). Will retry."
        return out

    out["status"] = r.status_code
    try:
        data = r.json() if r.text else {}
    except ValueError:
        data = {}

    if r.ok:
        cid = contact_id_from(data)
        if cid:
            out.update(ok=True, contact_id=cid, was_new=_was_new(data))
            return out
        out["error"] = (f"Smart 1 Suite returned HTTP {r.status_code} but no contact "
                        f"id, so the lead can't be confirmed as delivered. "
                        f"Body: {str(r.text)[:200]}")
        return out

    # 401/403 is a token or scope problem, 422 is a bad payload — retrying
    # those on a timer just burns the rate limit and hides the real cause.
    if r.status_code in (400, 401, 403, 422):
        out["retryable"] = False
    hint = ""
    if r.status_code in (401, 403):
        hint = (" Check the token has the contacts.write scope and covers this "
                "location.")
    out["error"] = (f"Smart 1 Suite rejected the contact (HTTP {r.status_code})."
                    f"{hint} {str(r.text)[:200]}").strip()
    return out


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def find_location_by_name(name: str = "") -> dict:
    """Look the sub-account up by name, so the id doesn't have to be hunted.

    Uses companyId — which is the one place a company id genuinely belongs.
    """
    want = (name or LOCATION_NAME).strip().lower()
    company = company_id()
    if not token():
        return {"ok": False, "error": "No Smart 1 Suite token is set."}
    if not company:
        return {"ok": False, "error": "GHL_COMPANY_ID is not set, so the "
                                      "sub-accounts can't be listed."}
    try:
        import requests
        r = requests.get(f"{BASE}/locations/search",
                         params={"companyId": company, "limit": "500"},
                         headers=_headers(), timeout=TIMEOUT)
        if not r.ok:
            return {"ok": False,
                    "error": f"Listing sub-accounts failed (HTTP {r.status_code}): "
                             f"{str(r.text)[:180]}"}
        locs = (r.json() or {}).get("locations") or []
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "error": f"Couldn't reach Smart 1 Suite ({type(exc).__name__})."}

    matches = [{"id": l.get("id") or l.get("_id"), "name": l.get("name")}
               for l in locs if want in str(l.get("name") or "").lower()]
    return {"ok": True, "looking_for": want, "matches": matches,
            "sub_accounts": len(locs),
            "note": ("Put the id of the right one in GHL_LEAD_LOCATION_ID."
                     if matches else
                     f'No sub-account name contains "{want}".')}


def custom_fields(loc: str) -> dict:
    """The contact custom fields on the lead location: id, name, key, type.

    Read-only. `GET /locations/{id}/customFields` — the same call the Hub's
    own field-creation script makes to check its work. A refusal is
    `{"ok": False, "error": ...}` rather than an empty list, because "there
    are no custom fields" and "the token cannot list them" are different
    situations and the second is the one somebody has to fix first.
    """
    try:
        import requests
        r = requests.get(f"{BASE}/locations/{loc}/customFields",
                         headers=_headers(), timeout=TIMEOUT)
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "fields": [], "error": f"Couldn't reach Smart 1 Suite ({type(exc).__name__})."}
    if not r.ok:
        return {"ok": False, "fields": [],
                "error": f"HTTP {r.status_code}: {str(r.text)[:180]}. The token may lack "
                         "the locations/customFields.readonly scope."}
    try:
        data = r.json() if r.text else {}
    except ValueError:
        data = {}
    raw = data.get("customFields") if isinstance(data, dict) else data
    fields = []
    for f in raw or []:
        if not isinstance(f, dict):
            continue
        model = str(f.get("model") or "contact").lower()
        fields.append({"id": str(f.get("id") or ""), "name": str(f.get("name") or ""),
                       "key": str(f.get("fieldKey") or f.get("key") or ""),
                       "type": str(f.get("dataType") or ""), "model": model})
    return {"ok": True, "fields": [f for f in fields if f["model"] == "contact"],
            "count": len(fields)}


def preflight(find: bool = False) -> dict:
    """Check the configuration against the live API before trusting it.

    Written to be run on the deployment, because that is the only place the
    credentials exist. It reads; it never writes a contact.
    """
    out = {
        "token_set": bool(token()),
        "company_id": _mask(company_id()) if company_id() else "",
        "base": BASE, "version": VERSION, "upsert_path": UPSERT_PATH,
        "checks": [], "ready": False,
    }

    def check(name, ok, detail=""):
        out["checks"].append({"check": name, "ok": bool(ok), "detail": detail})
        return ok

    if not check("Suite token is set", token(),
                 "GHL_PRIVATE_TOKEN or SMART1SUITE_PRIVATE_TOKEN, with contacts.write."):
        return out
    try:
        loc = location_id()
        check("Lead location id is set and is not the company id", True, _mask(loc))
    except NotConfigured as exc:
        check("Lead location id is set and is not the company id", False, str(exc))
        if find:
            out["lookup"] = find_location_by_name()
        return out

    # Does the token actually reach that location? A read is the cheapest way
    # to find out, and it costs nothing if it fails.
    try:
        import requests
        r = requests.get(f"{BASE}/locations/{loc}", headers=_headers(), timeout=TIMEOUT)
        if r.ok:
            body = r.json() if r.text else {}
            loc_obj = body.get("location") or body
            check("Token can read the lead location", True,
                  str(loc_obj.get("name") or "")[:120])
            dup = loc_obj.get("settings") or {}
            if isinstance(dup, dict) and "allowDuplicateContact" in dup:
                allow = bool(dup.get("allowDuplicateContact"))
                check("Location does not allow duplicate contacts", not allow,
                      "Allow Duplicate Contact is ON in this sub-account, so "
                      "upsert can still create a second record. Turn it off if "
                      "you want retries to be strictly de-duplicated."
                      if allow else "Duplicates are de-duplicated on upsert.")
        else:
            check("Token can read the lead location", False,
                  f"HTTP {r.status_code}: {str(r.text)[:180]}. If this is 401/403 "
                  f"the token doesn't cover this sub-account.")
            if find:
                out["lookup"] = find_location_by_name()
            return out
    except Exception as exc:                              # noqa: BLE001
        check("Token can read the lead location", False,
              f"Couldn't reach Smart 1 Suite ({type(exc).__name__}).")
        return out

    # The contact custom fields, so the two ids the report links need are
    # read off this answer rather than guessed. A read, like the rest; a
    # location whose fields cannot be listed is said, not skipped.
    out["custom_fields"] = custom_fields(loc)
    ids = report_field_ids()
    known = {f.get("id"): f for f in out["custom_fields"].get("fields") or []}
    if ids["report_url"] or ids["pdf_url"]:
        bad = [k for k, v in ids.items() if v and known and v not in known]
        if bad:
            check("Report-link custom fields exist on the location", False,
                  "Configured but not on this sub-account: " + ", ".join(
                      f"{k}={_mask(ids[k])}" for k in bad)
                  + ". Pick ids from the custom_fields list below.")
        else:
            check("Report-link custom fields exist on the location", True,
                  ", ".join(f"{k} → {(known.get(v) or {}).get('name') or _mask(v)}"
                            for k, v in ids.items() if v)
                  + ("" if ids["report_url"] and ids["pdf_url"]
                     else ". One of the two is unset; that link stays on the Hub row."))
    else:
        check("Report-link custom fields are configured", False,
              "GHL_LEAD_REPORT_URL_FIELD_ID and GHL_LEAD_PDF_URL_FIELD_ID are unset, "
              "so a workflow emailing the report has no link. Create two contact "
              "custom fields in Suite and set their ids from the list below.")

    if find:
        out["lookup"] = find_location_by_name()
    # The custom-field check is a to-do rather than a failure: leads deliver
    # without it, and it is only the emailed link that is missing.
    out["ready"] = all(c["ok"] for c in out["checks"]
                       if not c["check"].startswith("Report-link"))
    out["note"] = ("Reads only — no contact was created. A green preflight means "
                   "the token reaches the right sub-account; the first real lead "
                   "is still what proves the write."
                   if out["ready"] else
                   "Fix the failed checks above; leads are stored either way and "
                   "can be retried once this is green.")
    return out
