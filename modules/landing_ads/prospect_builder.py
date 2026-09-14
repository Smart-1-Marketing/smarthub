"""Industry Prospect Builder for Landing Page Ads.

Turns a purchased B2B CSV into a controlled Smart 1 Suite import:

* normalises the common Apollo / UpLead / Data Axle / Salesgenie columns;
* deduplicates on email before any CRM write;
* previews and stages the list before importing;
* upserts each contact into the selected HighLevel sub-account;
* ADDs campaign/source/industry tags after the upsert so existing tags are
  preserved (HighLevel's upsert `tags` field replaces the current tag set);
* optionally adds one explicit workflow-trigger tag only after the contact and
  its tracking tags are confirmed in Suite.

The staged rows live in hub.jsonstore only until the import completes (or 24
hours), which gives browser-sized batches without keeping a purchased list as
another permanent CRM. The CRM is Smart 1 Suite; the Hub owns the import run.
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

from hub import jsonstore

API_BASE = (os.environ.get("GHL_API_BASE") or
            "https://services.leadconnectorhq.com").rstrip("/")
API_VERSION = os.environ.get("GHL_API_VERSION") or "2021-07-28"
TIMEOUT = int(os.environ.get("GHL_API_TIMEOUT") or 20)
MAX_ROWS = int(os.environ.get("PROSPECT_BUILDER_MAX_ROWS") or 25000)
BATCH_SIZE = max(1, min(25, int(os.environ.get("PROSPECT_BUILDER_BATCH_SIZE") or 10)))
JOB_TTL = 24 * 60 * 60
STORE = "industry_prospect_builder"

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_NON_SLUG = re.compile(r"[^a-z0-9]+")

# Canonical field -> header spellings after _header_key() normalisation.
ALIASES = {
    "email": ("email", "emailaddress", "businessemail", "workemail", "primaryemail"),
    "first_name": ("firstname", "first", "contactfirstname"),
    "last_name": ("lastname", "last", "contactlastname"),
    "name": ("name", "fullname", "contactname", "personname"),
    "company": ("company", "companyname", "business", "businessname", "organization", "organisation"),
    "phone": ("phone", "phonenumber", "mobile", "mobilephone", "directphone", "workphone"),
    "website": ("website", "websiteurl", "url", "domain", "companywebsite"),
    "address": ("address", "address1", "street", "streetaddress", "companyaddress"),
    "city": ("city", "companycity"),
    "state": ("state", "region", "province", "companystate"),
    "postal_code": ("zip", "zipcode", "postal", "postalcode", "companyzipcode"),
    "country": ("country", "countrycode"),
    "title": ("title", "jobtitle", "contacttitle", "position"),
    "industry": ("industry", "companyindustry"),
    "employees": ("employees", "employeecount", "headcount", "numberofemployees"),
    "revenue": ("revenue", "annualrevenue", "companyrevenue"),
}

_COUNTRY_CODES = {
    "united states": "US", "united states of america": "US", "usa": "US", "us": "US",
    "canada": "CA", "ca": "CA",
    "united kingdom": "GB", "great britain": "GB", "uk": "GB", "gb": "GB",
    "australia": "AU", "au": "AU",
    "new zealand": "NZ", "nz": "NZ",
    "ireland": "IE", "ie": "IE",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(value: str, fallback: str = "prospect") -> str:
    value = _NON_SLUG.sub("-", str(value or "").strip().lower()).strip("-")
    return (value or fallback)[:80]


def _header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _job_path(job_id: str) -> str:
    safe = re.sub(r"[^a-f0-9]", "", str(job_id or "").lower())
    if len(safe) != 32:
        raise ValueError("That import job id is not valid.")
    return os.path.join(jsonstore.data_dir(STORE), safe + ".json")


def _read_job(job_id: str) -> dict | None:
    try:
        row = jsonstore.read_json(_job_path(job_id), default=None)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def _write_job(job: dict) -> None:
    jsonstore.write_json(_job_path(job["id"]), job, indent=1)


def purge_expired() -> int:
    """Drop staged PII after 24 hours. A completed job is reduced immediately."""
    folder = jsonstore.data_dir(STORE)
    dropped = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    cutoff = time.time() - JOB_TTL
    for name in names:
        if not name.endswith(".json"):
            continue
        path = os.path.join(folder, name)
        try:
            if os.path.getmtime(path) < cutoff:
                jsonstore.delete_json(path)
                dropped += 1
        except OSError:
            continue
    return dropped


def _decode(raw: bytes) -> str:
    if not raw:
        raise ValueError("The CSV is empty.")
    if len(raw) > 20 * 1024 * 1024:
        raise ValueError("That CSV is larger than 20 MB. Split it into smaller lists.")
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("The CSV text encoding could not be read.")


def _column_map(fieldnames: list[str]) -> dict[str, str]:
    lookup = {_header_key(h): h for h in fieldnames if h}
    found = {}
    for canonical, aliases in ALIASES.items():
        for alias in aliases:
            if alias in lookup:
                found[canonical] = lookup[alias]
                break
    return found


def _normal_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    return value[:500]


def _normal_email(value: str) -> str:
    value = str(value or "").strip().lower()
    return value if _EMAIL.match(value) else ""


def _normal_country(value: str) -> str:
    """HighLevel requires ISO 3166-1 alpha-2, never the first two letters.

    Apollo and Data Axle commonly export full country names (for example,
    ``United States``). Sending ``UN`` would make an otherwise good contact
    fail its upsert. Preserve valid two-letter codes, map the common names we
    expect in these lists, and omit an unknown value rather than inventing one.
    """
    raw = re.sub(r"\s+", " ", str(value or "").strip())
    if not raw:
        return ""
    key = raw.lower().rstrip(".")
    if key in _COUNTRY_CODES:
        return _COUNTRY_CODES[key]
    if re.fullmatch(r"[A-Za-z]{2}", raw):
        return raw.upper()
    return ""


def parse_csv(raw: bytes) -> dict:
    text = _decode(raw)
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = [str(h or "").strip() for h in (reader.fieldnames or [])]
    if not headers:
        raise ValueError("The CSV has no header row.")
    cmap = _column_map(headers)
    if "email" not in cmap:
        raise ValueError("I could not find an email column. Use a header such as Email, Business Email, or Work Email.")

    rows = []
    invalid = 0
    duplicate = 0
    seen = set()
    for source_row in reader:
        if len(rows) >= MAX_ROWS:
            raise ValueError(f"This builder accepts up to {MAX_ROWS:,} usable contacts per import. Split this list into another campaign.")
        row = {}
        for canonical, source_header in cmap.items():
            row[canonical] = str(source_row.get(source_header) or "").strip()
        row["email"] = _normal_email(row.get("email", ""))
        if not row["email"]:
            invalid += 1
            continue
        if row["email"] in seen:
            duplicate += 1
            continue
        seen.add(row["email"])
        row["website"] = _normal_url(row.get("website", ""))
        row["country"] = _normal_country(row.get("country", ""))
        # Keep only fields the preview/import actually uses. This avoids
        # turning the staging store into a copy of every vendor column.
        rows.append({k: str(row.get(k) or "")[:500] for k in ALIASES})

    if not rows:
        raise ValueError("No usable email contacts were found in the CSV.")
    return {"rows": rows, "headers": headers, "mapped": cmap,
            "invalid": invalid, "duplicate": duplicate}


def stage(raw: bytes, *, filename: str, source: str, campaign: str,
          industry: str, landing_url: str) -> dict:
    purge_expired()
    parsed = parse_csv(raw)
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "created_at": _now(),
        "updated_at": _now(),
        "filename": str(filename or "prospects.csv")[:180],
        "source": str(source or "CSV").strip()[:80],
        "campaign": str(campaign or "").strip()[:120],
        "industry": str(industry or "").strip()[:120],
        "landing_url": str(landing_url or "").strip()[:500],
        "rows": parsed["rows"],
        "count": len(parsed["rows"]),
        "invalid": parsed["invalid"],
        "duplicate": parsed["duplicate"],
        "mapped": parsed["mapped"],
        "cursor": 0,
        "imported": 0,
        "failed": [],
        "completed": False,
    }
    _write_job(job)
    return {
        "id": job_id,
        "count": job["count"],
        "invalid": job["invalid"],
        "duplicate": job["duplicate"],
        "mapped": job["mapped"],
        "sample": parsed["rows"][:20],
        "source": job["source"], "campaign": job["campaign"],
        "industry": job["industry"], "landing_url": job["landing_url"],
    }


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Version": API_VERSION,
            "Accept": "application/json", "Content-Type": "application/json"}


def _message(resp: requests.Response) -> str:
    try:
        data = resp.json() if resp.text else {}
    except ValueError:
        data = {}
    msg = data.get("message") if isinstance(data, dict) else ""
    if isinstance(msg, list):
        msg = ", ".join(str(x) for x in msg)
    return str(msg or resp.text or f"HTTP {resp.status_code}")[:260]


def locations() -> list[dict]:
    """All Suite sub-accounts visible to the connected Marketplace app."""
    from hub import ghl_oauth
    token = ghl_oauth.agency_token()
    company_id = ghl_oauth.company_id()
    if not company_id:
        raise RuntimeError("The connected Suite app has no agency/company id.")
    rows = []
    skip = 0
    limit = 100
    for _ in range(30):
        r = requests.get(API_BASE + "/locations/search",
                         params={"companyId": company_id, "skip": skip,
                                 "limit": limit, "order": "asc"},
                         headers=_headers(token), timeout=TIMEOUT)
        if not r.ok:
            raise RuntimeError("HighLevel could not list sub-accounts: " + _message(r))
        data = r.json() if r.text else {}
        page = data.get("locations") or []
        rows.extend(x for x in page if isinstance(x, dict) and x.get("id"))
        if len(page) < limit:
            break
        skip += limit
    rows.sort(key=lambda x: str(x.get("name") or "").lower())
    return [{"id": str(x.get("id")), "name": str(x.get("name") or "Unnamed"),
             "email": str(x.get("email") or ""), "city": str(x.get("city") or ""),
             "state": str(x.get("state") or "")} for x in rows]


def _split_name(row: dict) -> tuple[str, str]:
    first = str(row.get("first_name") or "").strip()
    last = str(row.get("last_name") or "").strip()
    if first or last:
        return first, last
    full = str(row.get("name") or "").strip().split()
    return (full[0] if full else "", " ".join(full[1:]) if len(full) > 1 else "")


def _contact_id(data) -> str:
    if not isinstance(data, dict):
        return ""
    for holder in (data.get("contact"), data.get("data"), data):
        if not isinstance(holder, dict):
            continue
        for key in ("id", "_id", "contactId", "contact_id"):
            value = holder.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _contact_payload(row: dict, location_id: str, source: str) -> dict:
    first, last = _split_name(row)
    body = {
        "locationId": location_id,
        "firstName": first,
        "lastName": last,
        "email": row.get("email") or "",
        "phone": row.get("phone") or "",
        "companyName": row.get("company") or "",
        "website": row.get("website") or "",
        "address1": row.get("address") or "",
        "city": row.get("city") or "",
        "state": row.get("state") or "",
        "postalCode": row.get("postal_code") or "",
        "country": _normal_country(row.get("country") or ""),
        "source": f"Smart 1 Hub · Prospect Builder · {source}"[:300],
        # Force the normal duplicate-safe path even if this location allows
        # duplicates. The list should update a known person, not multiply them.
        "createNewIfDuplicateAllowed": False,
    }
    return {k: v for k, v in body.items() if v not in ("", None)} | {
        "locationId": location_id, "createNewIfDuplicateAllowed": False}


def _upsert_one(row: dict, *, token: str, location_id: str,
                source: str, tags: list[str]) -> dict:
    r = requests.post(API_BASE + "/contacts/upsert",
                      json=_contact_payload(row, location_id, source),
                      headers=_headers(token), timeout=TIMEOUT)
    if not r.ok:
        return {"ok": False, "email": row.get("email"),
                "error": "Contact upsert failed: " + _message(r),
                "status": r.status_code}
    try:
        data = r.json() if r.text else {}
    except ValueError:
        data = {}
    cid = _contact_id(data)
    if not cid:
        return {"ok": False, "email": row.get("email"),
                "error": "HighLevel accepted the contact but returned no contact id.",
                "status": r.status_code}

    # Do this separately. `tags` on /contacts/upsert overwrites the entire tag
    # set in current HighLevel API behavior; /contacts/:id/tags adds them.
    tr = requests.post(f"{API_BASE}/contacts/{cid}/tags", json={"tags": tags},
                       headers=_headers(token), timeout=TIMEOUT)
    if not tr.ok:
        return {"ok": False, "email": row.get("email"), "contact_id": cid,
                "partial": True,
                "error": "Contact saved, but tags failed: " + _message(tr),
                "status": tr.status_code}
    return {"ok": True, "email": row.get("email"), "contact_id": cid}


def _clean_tags(values) -> list[str]:
    out = []
    seen = set()
    for value in values or []:
        tag = re.sub(r"\s+", " ", str(value or "").strip())[:100]
        if tag and tag.lower() not in seen:
            seen.add(tag.lower())
            out.append(tag)
    return out[:20]


def tags_for(job: dict, custom_tags=None, *, activate=False,
             trigger_tag="s1-outbound-ready") -> list[str]:
    landing = ""
    if job.get("landing_url"):
        try:
            landing = urlparse(job["landing_url"]).path.strip("/").split("/")[-1]
        except Exception:  # noqa: BLE001
            landing = ""
    base = [
        "s1-prospect",
        f"source-{_slug(job.get('source'), 'csv')}",
        f"industry-{_slug(job.get('industry'), 'general')}",
        f"campaign-{_slug(job.get('campaign'), 'prospecting')}",
        "s1-email-verification",
    ]
    if landing:
        base.append(f"landing-{_slug(landing, 'page')}")
    base.extend(custom_tags or [])
    if activate:
        base.append(trigger_tag or "s1-outbound-ready")
    return _clean_tags(base)


def import_batch(job_id: str, *, location_id: str, custom_tags=None,
                 activate=False, trigger_tag="s1-outbound-ready") -> dict:
    job = _read_job(job_id)
    if not job:
        raise ValueError("That staged list expired or could not be found. Upload it again.")
    if job.get("completed"):
        return {"ok": True, "done": True, "cursor": job.get("cursor", 0),
                "count": job.get("count", 0), "imported": job.get("imported", 0),
                "failed": job.get("failed", [])}
    location_id = str(location_id or "").strip()
    if not location_id:
        raise ValueError("Choose the Smart 1 Suite sub-account first.")

    from hub import ghl_oauth
    token = ghl_oauth.location_token(location_id)
    tags = tags_for(job, custom_tags, activate=bool(activate), trigger_tag=trigger_tag)
    rows = job.get("rows") or []
    start = max(0, int(job.get("cursor") or 0))
    batch = rows[start:start + BATCH_SIZE]
    results = []
    for row in batch:
        try:
            result = _upsert_one(row, token=token, location_id=location_id,
                                 source=job.get("source") or "CSV", tags=tags)
        except requests.RequestException as exc:
            result = {"ok": False, "email": row.get("email"),
                      "error": f"HighLevel connection error: {type(exc).__name__}"}
        results.append(result)

    job["cursor"] = start + len(batch)
    successes = sum(1 for x in results if x.get("ok"))
    job["imported"] = int(job.get("imported") or 0) + successes
    for result in results:
        if not result.get("ok"):
            job.setdefault("failed", []).append({
                "email": result.get("email") or "",
                "error": result.get("error") or "Unknown error",
                "partial": bool(result.get("partial")),
            })
    done = job["cursor"] >= len(rows)
    if done:
        job["completed"] = True
        job["completed_at"] = _now()
        # The CRM now owns these people. Keep the run totals/errors but no
        # permanent shadow copy of the purchased contact list in SmartHub.
        job["rows"] = []
    job["updated_at"] = _now()
    job["location_id"] = location_id
    job["tags"] = tags
    _write_job(job)
    return {
        "ok": True, "done": done, "cursor": job["cursor"],
        "count": job["count"], "imported": job["imported"],
        "batch_imported": successes,
        "batch_failed": len(results) - successes,
        "failed": job.get("failed", [])[-50:], "tags": tags,
    }


def run_summary(job_id: str) -> dict | None:
    job = _read_job(job_id)
    if not job:
        return None
    return {k: job.get(k) for k in (
        "id", "created_at", "updated_at", "completed_at", "filename", "source",
        "campaign", "industry", "landing_url", "count", "invalid", "duplicate",
        "cursor", "imported", "completed", "location_id", "tags", "failed")}


def cost_estimate(contacts: int, sends_per_contact: int = 1) -> dict:
    contacts = max(0, int(contacts or 0))
    sends = max(1, min(20, int(sends_per_contact or 1)))
    return {
        "contacts": contacts, "sends_per_contact": sends,
        "ghl_verification": round(contacts * 0.0025, 2),
        "ghl_email": round(contacts * sends * 0.000675, 2),
        "ghl_total": round(contacts * 0.0025 + contacts * sends * 0.000675, 2),
    }


def register(app, *, pages_fn, version_fn, log_fn=None) -> None:
    """Attach the builder to the already-mounted Landing Page Ads Flask app."""
    from flask import jsonify, render_template, request

    def log(event: str, **extra):
        if log_fn:
            try:
                log_fn(event, **extra)
            except Exception:  # noqa: BLE001
                pass

    @app.route("/prospects")
    def prospect_page():
        return render_template("prospect_builder.html", pages=pages_fn(),
                               version=version_fn(), batch_size=BATCH_SIZE)

    @app.route("/api/prospects/locations")
    def prospect_locations():
        try:
            return jsonify({"ok": True, "locations": locations()})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)[:300]}), 502

    @app.route("/api/prospects/preview", methods=["POST"])
    def prospect_preview():
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"ok": False, "error": "Choose a CSV list first."}), 400
        try:
            result = stage(upload.read(), filename=upload.filename,
                           source=request.form.get("source", "CSV"),
                           campaign=request.form.get("campaign", ""),
                           industry=request.form.get("industry", ""),
                           landing_url=request.form.get("landing_url", ""))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        log("prospects.preview", source=result.get("source"), count=result.get("count"))
        return jsonify({"ok": True, "job": result})

    @app.route("/api/prospects/import", methods=["POST"])
    def prospect_import():
        body = request.get_json(silent=True) or {}
        try:
            result = import_batch(
                body.get("job_id", ""), location_id=body.get("location_id", ""),
                custom_tags=body.get("tags") or [], activate=bool(body.get("activate")),
                trigger_tag=str(body.get("trigger_tag") or "s1-outbound-ready"))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"Suite import stopped: {type(exc).__name__}: {exc}"[:400]}), 502
        if result.get("done"):
            log("prospects.import", count=result.get("imported"),
                failed=len(result.get("failed") or []))
        return jsonify(result)

    @app.route("/api/prospects/job/<job_id>")
    def prospect_job(job_id):
        result = run_summary(job_id)
        if not result:
            return jsonify({"ok": False, "error": "Import run not found."}), 404
        return jsonify({"ok": True, "job": result})

    @app.route("/api/prospects/cost")
    def prospect_cost():
        return jsonify({"ok": True, "estimate": cost_estimate(
            request.args.get("contacts", 0), request.args.get("sends", 1))})
