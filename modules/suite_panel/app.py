"""Smart 1 Suite — Control Panel (GHL sub-accounts).

Python port of the original Node/Express server, integrated with the Hub:
authentication is the Hub's shared login (the guard in wsgi.py blocks
unauthenticated requests before they reach this app), and every create /
delete is written to the Hub-wide activity log.

The GHL Private Integration token stays server-side, exactly as before.
"""
import os
import re
import secrets
import threading
import time
from pathlib import Path

import requests
from flask import Flask, jsonify, redirect, request, send_file

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


def _token_for(location_id=None):
    """Pick the credential a call should use.

    ``location_id`` asks for a sub-account-scoped token, which is the only kind
    that can read location resources like Forms. Those are minted from the
    Marketplace app install (hub.ghl_oauth). Everything else keeps using the
    agency Private Integration Token, so nothing changes for calls that already
    work — and the whole panel still runs before the app is connected.
    """
    if location_id:
        try:
            from hub import ghl_oauth
            if ghl_oauth.connected():
                return ghl_oauth.location_token(location_id)
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001 — surface it as an API error
            raise GhlError(str(exc), 502) from exc
    token = _env("GHL_PRIVATE_TOKEN")
    if not token:
        raise GhlError("GHL_PRIVATE_TOKEN is not configured on the server.", 500)
    return token


def ghl(pathname, method="GET", body=None, query=None, timeout=30, location_id=None):
    token = _token_for(location_id)
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


def _page_arg(name, default, lo=1, hi=500):
    """A paging value from the query string, clamped and never non-numeric.

    Passing ?limit= straight through hands a stranger's number to the upstream
    API — ?limit=-1 or ?limit=999999 is their problem to reject, and how they
    reject it is not something this panel should discover in production.
    """
    try:
        return str(max(lo, min(hi, int(request.args.get(name, default)))))
    except (TypeError, ValueError):
        return str(default)


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
        try:  # counts against the monthly Brandfetch allowance
            from hub import quotas as _q
            _q.record('brandfetch', module='suite_panel', detail=domain)
        except Exception:  # noqa: BLE001
            pass
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
        payload = {
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
        }
        # Persist for the whole hub: any client form can autofill from this.
        try:
            from hub import seo as _hub_seo
            _hub_seo.save_brandfetch(payload["domain"], payload,
                                     client=(request.args.get("client") or "").strip())
        except Exception:  # noqa: BLE001 — persistence is best-effort
            pass
        return jsonify(payload)
    except requests.RequestException as exc:
        return jsonify({"error": f"Could not reach Brandfetch: {exc}"}), 502


@app.route("/api/locations")
def api_locations():
    try:
        data = ghl("/locations/search", query={
            "companyId": _env("GHL_COMPANY_ID"),
            "limit": _page_arg("limit", 100),
            "skip": _page_arg("skip", 0, lo=0, hi=100000),
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


@app.route("/api/locations/<loc_id>/analytics")
def api_location_analytics(loc_id):
    """Everything we can read about a sub-account in one call. Each metric
    degrades gracefully — a scope missing from the Private Integration token
    shows as a note instead of failing the whole panel."""
    try:
        loc = ghl(f"/locations/{loc_id}")
        loc = loc.get("location") or loc
    except Exception as err:  # noqa: BLE001
        return send_error(err)

    metrics = {}

    def metric(key, fn):
        try:
            metrics[key] = {"value": fn()}
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "401" in msg or "403" in msg or "scope" in msg.lower():
                msg = "add this scope to your Private Integration token"
            metrics[key] = {"error": msg[:140]}

    metric("contacts", lambda: (
        ghl("/contacts/", query={"locationId": loc_id, "limit": "1"})
        .get("meta", {}).get("total", 0)))
    metric("opportunities", lambda: (
        ghl("/opportunities/search", query={"location_id": loc_id, "limit": "1"})
        .get("meta", {}).get("total", 0)))
    metric("pipelines", lambda: len(
        ghl("/opportunities/pipelines", query={"locationId": loc_id}).get("pipelines") or []))
    metric("conversations", lambda: (
        ghl("/conversations/search", query={"locationId": loc_id, "limit": "1"})
        .get("total", 0)))
    metric("calendars", lambda: len(
        ghl("/calendars/", query={"locationId": loc_id}).get("calendars") or []))
    metric("users", lambda: len(
        ghl("/users/", query={"locationId": loc_id}).get("users") or []))
    metric("tags", lambda: len(
        ghl(f"/locations/{loc_id}/tags").get("tags") or []))
    metric("custom_fields", lambda: len(
        ghl(f"/locations/{loc_id}/customFields").get("customFields") or []))
    # Forms needs a sub-account token, so it is the one metric that routes
    # through the Marketplace app install rather than the agency token.
    metric("forms", lambda: len(
        ghl("/forms/", query={"locationId": loc_id, "limit": "100"},
            location_id=loc_id).get("forms") or []))

    app_base = _env("GHL_APP_BASE", "https://app.gohighlevel.com").rstrip("/")
    return jsonify({
        "location": {
            "id": loc.get("id") or loc_id,
            "name": loc.get("name"),
            "email": loc.get("email"),
            "phone": loc.get("phone"),
            "website": loc.get("website"),
            "address": loc.get("address"),
            "city": loc.get("city"),
            "state": loc.get("state"),
            "postalCode": loc.get("postalCode"),
            "country": loc.get("country"),
            "timezone": loc.get("timezone"),
            "dateAdded": loc.get("dateAdded"),
            "logoUrl": loc.get("logoUrl"),
        },
        "metrics": metrics,
        "dashboard_url": f"{app_base}/v2/location/{loc_id}/dashboard",
    })


@app.route("/api/locations/<loc_id>/forms")
def api_location_forms(loc_id):
    """Forms for one sub-account.

    Location-scoped, so this is the call that needed a per-client token and
    could never work on the agency Private Integration Token alone.
    """
    try:
        data = ghl("/forms/", query={
            "locationId": loc_id,
            "limit": _page_arg("limit", 100),
            "skip": _page_arg("skip", 0, lo=0, hi=100000),
        }, location_id=loc_id)
        forms = data.get("forms") or []
        return jsonify({"forms": forms, "total": data.get("total", len(forms))})
    except Exception as err:  # noqa: BLE001
        return send_error(err)


@app.route("/api/locations/<loc_id>/form-submissions")
def api_location_form_submissions(loc_id):
    try:
        data = ghl("/forms/submissions", query={
            "locationId": loc_id,
            "formId": request.args.get("formId") or None,
            "limit": _page_arg("limit", 100),
            "page": _page_arg("page", 1),
        }, location_id=loc_id)
        return jsonify(data)
    except Exception as err:  # noqa: BLE001
        return send_error(err)


def _session_secret() -> str:
    """The Hub's signing secret, under every name it answers to.

    Two reads in this file knew SECRET_KEY and SESSION_SECRET; hub/config.py
    also accepts FLASK_SECRET_KEY, which this deployment sets. The OAuth state
    signature has to survive both gunicorn workers, so a secret that resolves
    on one screen and not the next is a callback that fails about half the
    time with nothing in the log but a bad signature.
    """
    try:
        from hub.config import settings
        return settings.secret_key
    except Exception:                                 # noqa: BLE001
        return (os.environ.get("SECRET_KEY") or os.environ.get("FLASK_SECRET_KEY")
                or os.environ.get("SESSION_SECRET") or "")


# ---------------- Marketplace app (OAuth) ----------------
def _state_serializer():
    """Signed, expiring OAuth state — not a server-side session.

    Gunicorn runs two workers, so the callback often lands on a different
    process than the one that started the flow; anything held in memory would
    fail about half the time. A signature over the shared SECRET_KEY is
    checkable by whichever worker answers.
    """
    from itsdangerous import URLSafeTimedSerializer
    key = (_session_secret() or _env("PANEL_PASSWORD") or "s1hub-suite-oauth")
    return URLSafeTimedSerializer(key, salt="ghl-oauth-state")


def _sign_state() -> str:
    return _state_serializer().dumps({"n": secrets.token_urlsafe(16)})


def _check_state(state: str) -> bool:
    if not state:
        return False
    try:
        from itsdangerous import BadSignature, SignatureExpired
        try:
            _state_serializer().loads(state, max_age=900)
            return True
        except (BadSignature, SignatureExpired):
            return False
    except ImportError:                     # itsdangerous ships with Flask
        return False


@app.route("/oauth/start")
def oauth_start():
    """One-time agency consent. Everything after this is automatic."""
    from hub import ghl_oauth
    if not ghl_oauth.configured():
        return ("Set GHL_CLIENT_ID and GHL_CLIENT_SECRET on the service first, "
                "then reload this page.", 500)
    return redirect(ghl_oauth.authorize_url(_sign_state()))


@app.route("/oauth/callback")
def oauth_callback():
    from hub import ghl_oauth
    error = request.args.get("error")
    if error:
        return (f"HighLevel returned an error: {error}", 400)
    if not _check_state(request.args.get("state")):
        return ("That authorisation link didn't come from this Hub, or it sat "
                "unused too long. Start again from Suite.", 400)
    code = request.args.get("code")
    if not code:
        return ("HighLevel didn't send an authorisation code.", 400)
    try:
        ghl_oauth.exchange_code(code)
    except Exception as exc:  # noqa: BLE001
        return (f"Could not finish connecting: {exc}", 502)
    audit.log("suite", "ghl_app_connected", actor=actor_name())
    return redirect("/suite/")


@app.route("/api/oauth/status")
def api_oauth_status():
    from hub import ghl_oauth
    return jsonify(ghl_oauth.status())


@app.route("/api/oauth/installed-locations")
def api_oauth_installed_locations():
    from hub import ghl_oauth
    try:
        return jsonify({"locations": ghl_oauth.installed_locations()})
    except Exception as err:  # noqa: BLE001
        return send_error(err)


@app.route("/api/oauth/disconnect", methods=["POST"])
def api_oauth_disconnect():
    from hub import ghl_oauth
    ghl_oauth.disconnect()
    audit.log("suite", "ghl_app_disconnected", actor=actor_name())
    return jsonify({"ok": True})


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
    limit = max(1, min(1000, int(request.args.get("limit") or 300)))
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
        if _session_secret()
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
