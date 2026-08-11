"""Smart 1 Suite — Control Panel (GHL sub-accounts).

Python port of the original Node/Express server, integrated with the Hub:
authentication is the Hub's shared login (the guard in wsgi.py blocks
unauthenticated requests before they reach this app), and every create /
delete is written to the Hub-wide activity log.

The GHL Private Integration token stays server-side, exactly as before.
"""
import os
import re
import threading
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, request, send_file

from hub import audit
from hub import auth as hub_auth

app = Flask(__name__)

PUBLIC_DIR = Path(__file__).parent / "public"

GHL_BASE = "https://services.leadconnectorhq.com"
BRANDFETCH_BASE = "https://api.brandfetch.io/v2"


def _env(name, default=""):
    return os.environ.get(name, default)


def actor_name() -> str:
    user = request.environ.get("s1hub.user") or hub_auth.user_from_environ(request.environ)
    return user or "Unknown"


# ---------------- GHL request helper ----------------
class GhlError(Exception):
    def __init__(self, message, status=502, details=None):
        super().__init__(message)
        self.status = status
        self.details = details


def ghl(pathname, method="GET", body=None, query=None, timeout=30):
    token = _env("GHL_PRIVATE_TOKEN")
    if not token:
        raise GhlError("GHL_PRIVATE_TOKEN is not configured on the server.", 500)
    params = {k: v for k, v in (query or {}).items() if v not in (None, "")}
    resp = requests.request(
        method, GHL_BASE + pathname, params=params, json=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Version": _env("GHL_API_VERSION", "2021-07-28"),
            "Accept": "application/json",
        },
        timeout=timeout,
    )
    try:
        data = resp.json() if resp.text else {}
    except ValueError:
        data = {"raw": resp.text}
    if not resp.ok:
        message = data.get("message") or data.get("error") or f"GHL API error (HTTP {resp.status_code})"
        if isinstance(message, list):
            message = ", ".join(str(m) for m in message)
        raise GhlError(message, resp.status_code, data)
    return data


def send_error(err):
    status = err.status if isinstance(err, GhlError) and 400 <= (err.status or 0) < 600 else 502
    details = err.details if isinstance(err, GhlError) else None
    return jsonify({"error": str(err) or "Upstream error", "details": details}), status


# ---------------- Idempotency (double-submit protection) ----------------
IDEMPOTENCY_TTL = 5 * 60
_idem: dict[str, dict] = {}
_idem_lock = threading.Lock()


def idem_get(key):
    if not key:
        return None
    with _idem_lock:
        rec = _idem.get(key)
        if rec and rec["expires"] > time.time():
            return rec
        _idem.pop(key, None)
    return None


def idem_set(key, status, body):
    if not key:
        return
    with _idem_lock:
        _idem[key] = {"status": status, "body": body, "expires": time.time() + IDEMPOTENCY_TTL}
        if len(_idem) > 2000:
            _idem.pop(next(iter(_idem)))


# ---------------- Logo hosting ----------------
def upload_logo_to_media(location_id, image_url, name):
    """Download an image and upload it into the sub-account's media library."""
    img = requests.get(image_url, timeout=10)
    if not img.ok:
        raise RuntimeError(f"download failed (HTTP {img.status_code})")
    content_type = (img.headers.get("content-type") or "image/png").split(";")[0].strip()
    data = img.content
    if not data:
        raise RuntimeError("empty image")
    if len(data) > 5 * 1024 * 1024:
        raise RuntimeError("image larger than 5MB")

    ext = {
        "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
        "image/svg+xml": "svg", "image/webp": "webp", "image/gif": "gif",
    }.get(content_type, "png")

    resp = requests.post(
        f"{GHL_BASE}/medias/upload-file",
        params={"altId": location_id, "altType": "location"},
        headers={
            "Authorization": f"Bearer {_env('GHL_PRIVATE_TOKEN')}",
            "Version": _env("GHL_API_VERSION", "2021-07-28"),
            "Accept": "application/json",
        },
        files={"file": (f"logo.{ext}", data, content_type)},
        data={"hosted": "false", "name": f"{name or 'Account'} logo"},
        timeout=30,
    )
    try:
        body = resp.json() if resp.text else {}
    except ValueError:
        body = {}
    if not resp.ok:
        raise RuntimeError(body.get("message") or f"media upload failed (HTTP {resp.status_code})")
    return body.get("url") or body.get("fileUrl") or body.get("link") or body.get("location")


# ================= Frontend + health =================
@app.route("/")
def index():
    return send_file(PUBLIC_DIR / "index.html")


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})


# ================= Auth compatibility routes =================
# The Hub owns login — these keep the existing frontend working unchanged.
@app.route("/api/session")
def api_session():
    return jsonify({"authenticated": True, "name": actor_name()})


@app.route("/api/login", methods=["POST"])
def api_login():
    return jsonify({"ok": True, "name": actor_name()})


@app.route("/api/logout", methods=["POST"])
def api_logout():
    return jsonify({"ok": True})


# ================= GHL proxy routes =================
@app.route("/api/snapshots")
def api_snapshots():
    try:
        data = ghl("/snapshots/", query={"companyId": _env("GHL_COMPANY_ID")})
        snapshots = [
            {"id": s.get("id") or s.get("_id"), "name": s.get("name")}
            for s in (data.get("snapshots") or data.get("data") or [])
        ]
        return jsonify({"snapshots": snapshots})
    except Exception as err:  # noqa: BLE001
        return send_error(err)


@app.route("/api/brand")
def api_brand():
    key = _env("BRANDFETCH_API_KEY")
    if not key:
        return jsonify({"error": "Brandfetch is not configured. Set BRANDFETCH_API_KEY to enable brand lookup."}), 400

    domain = (request.args.get("domain") or "").strip().lower()
    if not domain:
        return jsonify({"error": "A domain is required."}), 400
    domain = re.sub(r"^https?://", "", domain).removeprefix("www.").split("/")[0].split("?")[0]
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        return jsonify({"error": "That does not look like a valid domain (e.g. acme.com)."}), 400

    try:
        r = requests.get(
            f"{BRANDFETCH_BASE}/brands/{domain}",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=15,
        )
        try:
            data = r.json() if r.text else {}
        except ValueError:
            data = {}
        if r.status_code == 404:
            return jsonify({"error": f"No brand data found for {domain}."}), 404
        if r.status_code == 429:
            return jsonify({"error": "Brandfetch quota exceeded. Try again later."}), 429
        if not r.ok:
            return jsonify({"error": data.get("message") or f"Brandfetch error (HTTP {r.status_code})"}), 502

        def pick_image(type_):
            items = [l for l in (data.get("logos") or []) if l.get("type") == type_]
            items.sort(key=lambda l: 0 if l.get("theme") == "light" else 1)
            for it in items:
                fmts = sorted(
                    it.get("formats") or [],
                    key=lambda f: {"png": 0, "svg": 1}.get(f.get("format"), 2),
                )
                if fmts and fmts[0].get("src"):
                    return fmts[0]["src"]
            return None

        link_map = {"facebook": "facebookUrl", "twitter": "twitter", "linkedin": "linkedIn",
                    "instagram": "instagram", "youtube": "youtube", "pinterest": "pinterest"}
        social = {}
        for link in data.get("links") or []:
            k = link_map.get((link.get("name") or "").lower())
            if k and link.get("url"):
                social[k] = link["url"]

        loc = (data.get("company") or {}).get("location") or {}
        return jsonify({
            "name": data.get("name"),
            "domain": data.get("domain") or domain,
            "description": data.get("description"),
            "logo": pick_image("logo") or pick_image("icon"),
            "icon": pick_image("icon"),
            "colors": [{"hex": c.get("hex"), "type": c.get("type")} for c in data.get("colors") or [] if c.get("hex")],
            "social": social,
            "location": {
                "city": loc.get("city") or "",
                "state": loc.get("state") or "",
                "country": loc.get("countryCode") or "",
                "countryName": loc.get("country") or "",
            },
            "website": f"https://{data.get('domain') or domain}",
        })
    except requests.RequestException as exc:
        return jsonify({"error": f"Could not reach Brandfetch: {exc}"}), 502


@app.route("/api/locations")
def api_locations():
    try:
        data = ghl("/locations/search", query={
            "companyId": _env("GHL_COMPANY_ID"),
            "limit": request.args.get("limit", "100"),
            "skip": request.args.get("skip", "0"),
            "order": "asc",
            "query": request.args.get("search") or None,
            "email": request.args.get("email") or None,
        })
        locations = data.get("locations") or []
        return jsonify({"locations": locations, "count": data.get("count", len(locations))})
    except Exception as err:  # noqa: BLE001
        return send_error(err)


@app.route("/api/locations/<loc_id>")
def api_location(loc_id):
    try:
        data = ghl(f"/locations/{loc_id}")
        return jsonify(data.get("location") or data)
    except Exception as err:  # noqa: BLE001
        return send_error(err)


@app.route("/api/locations", methods=["POST"])
def api_create_location():
    b = request.get_json(silent=True) or {}
    idem_key = (b.get("idempotencyKey") or "")[:100] if isinstance(b.get("idempotencyKey"), str) else None
    cached = idem_get(idem_key)
    if cached:
        return jsonify(cached["body"]), cached["status"]

    try:
        name = (b.get("name") or "").strip()
        if not name:
            return jsonify({"error": "Account name is required."}), 400

        if not b.get("confirmDuplicate"):
            try:
                dup = ghl("/locations/search", query={"companyId": _env("GHL_COMPANY_ID"), "limit": "5", "query": name})
                matches = [
                    loc for loc in dup.get("locations") or []
                    if str(loc.get("name") or "").strip().lower() == name.lower()
                ]
                if matches:
                    return jsonify({
                        "error": "duplicate",
                        "message": f'An account named "{name}" already exists.',
                        "duplicates": [{"id": m.get("id") or m.get("_id"), "name": m.get("name")} for m in matches],
                    }), 409
            except Exception as dup_err:  # noqa: BLE001 — never block creation on a flaky check
                app.logger.warning("duplicate check failed, proceeding: %s", dup_err)

        payload = {"name": name, "companyId": _env("GHL_COMPANY_ID")}
        for k in ("phone", "address", "city", "state", "country", "postalCode", "website", "timezone", "snapshotId"):
            if b.get(k):
                payload[k] = b[k]

        prospect = {}
        if b.get("prospectFirstName"):
            prospect["firstName"] = b["prospectFirstName"]
        if b.get("prospectLastName"):
            prospect["lastName"] = b["prospectLastName"]
        if b.get("prospectEmail"):
            prospect["email"] = b["prospectEmail"]
        if prospect:
            payload["prospectInfo"] = prospect

        if isinstance(b.get("social"), dict):
            social = {k: b["social"][k] for k in
                      ("facebookUrl", "twitter", "linkedIn", "instagram", "youtube", "pinterest", "blogRss")
                      if b["social"].get(k)}
            if social:
                payload["social"] = social

        location = ghl("/locations/", method="POST", body=payload)
        new_id = location.get("id") or location.get("_id") or (location.get("location") or {}).get("id")
        result = {"location": location, "locationId": new_id}

        # Optional logo: host inside GHL media, fall back to the source URL.
        if b.get("logoUrl") and new_id:
            logo_to_set, hosted = None, False
            try:
                hosted_url = upload_logo_to_media(new_id, b["logoUrl"], name)
                if hosted_url:
                    logo_to_set, hosted = hosted_url, True
            except Exception as up_err:  # noqa: BLE001
                result["logoUploadWarning"] = (
                    f"Couldn't host the logo in GHL media ({up_err}); used the source URL instead."
                )
            if not logo_to_set:
                logo_to_set = b["logoUrl"]
            try:
                ghl(f"/locations/{new_id}", method="PUT",
                    body={"companyId": _env("GHL_COMPANY_ID"), "name": name, "logoUrl": logo_to_set})
                result["logoSet"] = True
                result["logoHosted"] = hosted
            except Exception as logo_err:  # noqa: BLE001
                result["logoWarning"] = f"Account created, but setting the logo failed: {logo_err}"

        # Optional login user.
        if b.get("createUser") and new_id:
            if not b.get("userEmail") or not b.get("userFirstName"):
                result["userWarning"] = "Account created, but user was skipped: user first name and email are required."
            else:
                try:
                    user_payload = {
                        "companyId": _env("GHL_COMPANY_ID"),
                        "firstName": b["userFirstName"],
                        "lastName": b.get("userLastName", ""),
                        "email": b["userEmail"],
                        "type": "account",
                        "role": "user" if b.get("userRole") == "user" else "admin",
                        "locationIds": [new_id],
                        "permissions": {
                            "campaignsEnabled": True, "contactsEnabled": True, "workflowsEnabled": True,
                            "opportunitiesEnabled": True, "dashboardStatsEnabled": True,
                            "appointmentsEnabled": True, "conversationsEnabled": True, "settingsEnabled": True,
                        },
                    }
                    if b.get("userPassword"):
                        user_payload["password"] = b["userPassword"]
                    if b.get("userPhone"):
                        user_payload["phone"] = b["userPhone"]
                    result["user"] = ghl("/users/", method="POST", body=user_payload)
                except Exception as user_err:  # noqa: BLE001
                    result["userWarning"] = f"Account created, but creating the login user failed: {user_err}"

        idem_set(idem_key, 201, result)
        audit.log("suite", "account_created", actor=actor_name(), name=name, locationId=new_id)
        return jsonify(result), 201
    except Exception as err:  # noqa: BLE001
        return send_error(err)


@app.route("/api/locations/<loc_id>", methods=["DELETE"])
def api_delete_location(loc_id):
    try:
        delete_twilio = str(request.args.get("deleteTwilioAccount")) == "true"
        target_name = (request.args.get("name") or "")[:200]
        data = ghl(f"/locations/{loc_id}", method="DELETE", query={"deleteTwilioAccount": str(delete_twilio).lower()})
        audit.log("suite", "account_deleted", actor=actor_name(), name=target_name, locationId=loc_id)
        return jsonify({"ok": True, "result": data})
    except Exception as err:  # noqa: BLE001
        return send_error(err)


@app.route("/api/audit")
def api_audit():
    limit = min(int(request.args.get("limit", 300) or 300), 1000)
    return jsonify({"entries": audit.read(limit=limit, module="suite")})


@app.route("/api/diagnostics")
def api_diagnostics():
    checks = []
    pw = os.environ.get("PANEL_PASSWORD", "")
    if not pw:
        checks.append({"name": "Hub password", "status": "error", "message": "PANEL_PASSWORD is not set."})
    elif pw == "change-me-to-something-strong":
        checks.append({"name": "Hub password", "status": "warn",
                       "message": "Still set to the example placeholder — change it to something unique."})
    else:
        checks.append({"name": "Hub password", "status": "ok", "message": "Configured."})

    checks.append(
        {"name": "Session secret", "status": "ok", "message": "Configured — logins survive restarts."}
        if os.environ.get("SECRET_KEY") or os.environ.get("SESSION_SECRET")
        else {"name": "Session secret", "status": "warn",
              "message": "Not set — everyone is logged out on every restart or redeploy."}
    )

    token, company = _env("GHL_PRIVATE_TOKEN"), _env("GHL_COMPANY_ID")
    if not token or not company:
        checks.append({"name": "GoHighLevel API", "status": "error",
                       "message": "GHL_PRIVATE_TOKEN and/or GHL_COMPANY_ID is not set."})
        checks.append({"name": "Snapshots scope", "status": "skipped", "message": "Skipped — GHL is not configured."})
    else:
        try:
            ghl("/locations/search", query={"companyId": company, "limit": "1"})
            checks.append({"name": "GoHighLevel API", "status": "ok",
                           "message": "Token is valid and can read sub-accounts."})
        except Exception as err:  # noqa: BLE001
            checks.append({"name": "GoHighLevel API", "status": "error", "message": f"Token check failed: {err}"})
        try:
            ghl("/snapshots/", query={"companyId": company})
            checks.append({"name": "Snapshots scope", "status": "ok", "message": "Snapshot picker will work."})
        except Exception as err:  # noqa: BLE001
            checks.append({"name": "Snapshots scope", "status": "warn",
                           "message": f'Snapshot lookup failed ({err}). Check the "View Snapshots" scope on your token.'})

    bkey = _env("BRANDFETCH_API_KEY")
    if not bkey:
        checks.append({"name": "Brandfetch API", "status": "skipped",
                       "message": 'Not configured — "Fetch brand info" is disabled. This is optional.'})
    else:
        try:
            r = requests.get(f"{BRANDFETCH_BASE}/brands/brandfetch.com",
                             headers={"Authorization": f"Bearer {bkey}", "Accept": "application/json"}, timeout=15)
            if r.ok:
                checks.append({"name": "Brandfetch API", "status": "ok", "message": "Key is valid."})
            elif r.status_code == 429:
                checks.append({"name": "Brandfetch API", "status": "warn",
                               "message": "Key is valid, but the quota is exhausted right now."})
            else:
                checks.append({"name": "Brandfetch API", "status": "error",
                               "message": f"Key check failed (HTTP {r.status_code})."})
        except requests.RequestException as err:
            checks.append({"name": "Brandfetch API", "status": "error", "message": f"Could not reach Brandfetch: {err}"})

    checks.append({
        "name": "Media upload scope", "status": "info",
        "message": 'Not live-tested. Needs the "Edit Medias" scope for logos to host inside GHL — '
                   "otherwise falls back to linking the source image.",
    })
    from datetime import datetime, timezone
    return jsonify({"checks": checks, "checkedAt": datetime.now(timezone.utc).isoformat()})
