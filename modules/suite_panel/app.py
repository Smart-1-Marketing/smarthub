"""Smart 1 Suite — Control Panel (GHL sub-accounts).

Python port of the original Node/Express server, integrated with the Hub:
authentication is the Hub's shared login (the guard in wsgi.py blocks
unauthenticated requests before they reach this app), and every create /
delete is written to the Hub-wide activity log.

The GHL Private Integration token stays server-side, exactly as before.
"""
import hashlib
import json
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
from hub.webargs import clamp_int

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

    The parse itself is `hub/webargs.py`'s -- this had worked the rule out
    independently and correctly, which is exactly the drift that file exists
    to stop: the next improvement to it should land once. Still a string,
    because what this returns is forwarded as a query parameter.
    """
    return str(clamp_int(request.args.get(name), default, lo, hi))


def send_error(err):
    status = err.status if isinstance(err, GhlError) and 400 <= (err.status or 0) < 600 else 502
    details = err.details if isinstance(err, GhlError) else None
    return jsonify({"error": str(err) or "Upstream error", "details": details}), status


# ---------------- Idempotency (double-submit protection) ----------------
#
# This guard creates GoHighLevel sub-accounts, so the thing it is protecting
# against is a client ending up with two of them. It failed at that two ways,
# and each was invisible: the caller got a clean 201 either time.
#
# **It was a dict in memory, and gunicorn runs two workers.** A resubmitted
# key that landed on the worker which had not seen the first one found nothing
# cached and created a second account — the `_state`-is-per-process trap
# CLAUDE.md names for the scheduler, on the route where it costs a duplicate
# client account. The claim is a file on the shared data disk now, so both
# workers are looking at the same answer.
#
# **And it was written after the work, so it never covered a double-click at
# all.** `idem_get` read at the top and `idem_set` wrote after the account
# existed, so two requests arriving together both found nothing and both
# created one — which is precisely the shape a double-submit is. The key is
# **claimed before the work starts**, with O_EXCL so the claim is atomic
# between workers, and filled in afterwards.
#
# Nothing in here may raise. A guard that breaks the thing it guards is worse
# than none, so every path answers, and a store that cannot be read or written
# degrades to the in-process dict rather than to no protection at all.
IDEMPOTENCY_TTL = 5 * 60
_idem: dict[str, dict] = {}
_idem_lock = threading.Lock()


def _idem_dir():
    from hub import jsonstore
    return Path(jsonstore.data_dir("suite_panel", "idempotency"))


def _idem_path(key):
    # The key is a caller's string; it never reaches the filesystem as itself.
    return _idem_dir() / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:40] + ".json")


def _idem_read(key):
    """The record for this key, from the shared disk, or None."""
    try:
        path = _idem_path(key)
        rec = json.loads(path.read_text(encoding="utf-8"))
        if float(rec.get("expires") or 0) > time.time():
            return rec
        path.unlink(missing_ok=True)
    except (OSError, ValueError, TypeError):
        pass
    return None


def idem_claim(key):
    """Claim a key before the work starts.

    Returns ``(state, record)``: ``"free"`` when this request owns the key and
    should do the work, ``"done"`` with the earlier answer to replay, or
    ``"running"`` when another request holds the claim and has not finished.
    An unusable key (none given) is ``"free"`` with no claim, which is the
    behaviour a caller that sends no key has always had.
    """
    if not key:
        return "free", None
    rec = _idem_read(key)
    if rec:
        return ("done", rec) if rec.get("done") else ("running", rec)
    try:
        path = _idem_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL is what makes this atomic between the two workers: exactly one
        # of two simultaneous submits creates the file, and the other is told
        # its twin is already running rather than creating a second account.
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"done": False, "expires": time.time() + IDEMPOTENCY_TTL}, fh)
        return "free", None
    except FileExistsError:
        rec = _idem_read(key)
        if rec:
            return ("done", rec) if rec.get("done") else ("running", rec)
        return "free", None
    except OSError:
        pass                        # no shared disk — fall back to memory
    with _idem_lock:
        rec = _idem.get(key)
        if rec and rec["expires"] > time.time():
            return ("done", rec) if rec.get("done") else ("running", rec)
        _idem[key] = {"done": False, "expires": time.time() + IDEMPOTENCY_TTL}
        if len(_idem) > 2000:
            _idem.pop(next(iter(_idem)))
    return "free", None


def idem_set(key, status, body):
    """Record the answer against a claim, so a resubmission replays it."""
    if not key:
        return
    rec = {"done": True, "status": status, "body": body,
           "expires": time.time() + IDEMPOTENCY_TTL}
    try:
        path = _idem_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec), encoding="utf-8")
        os.replace(str(tmp), str(path))
        return
    except (OSError, TypeError, ValueError):
        pass
    with _idem_lock:
        _idem[key] = rec
        if len(_idem) > 2000:
            _idem.pop(next(iter(_idem)))


def idem_release(key):
    """Drop a claim whose work did not produce an answer to replay.

    A create that failed must not leave the key claimed for five minutes: the
    rep's next press is a new attempt, not a duplicate of one that never
    happened.
    """
    if not key:
        return
    try:
        _idem_path(key).unlink(missing_ok=True)
    except OSError:
        pass
    with _idem_lock:
        rec = _idem.get(key)
        if rec and not rec.get("done"):
            _idem.pop(key, None)


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
    # Through hub/brand_lookup.py, which fixes three things this route had.
    #
    # It asked Brandfetch on every request without ever consulting what the
    # Hub already holds, so a client whose brand was fetched this morning cost
    # another of the plan's hundred monthly lookups this afternoon.
    #
    # It read BRANDFETCH_API_KEY alone while hub/config.py accepts
    # BRANDFETCH_API too -- the spelling the rest of this deployment's provider
    # keys use -- so this panel could report "not configured" over a key that
    # was drawing logos in Image Creator and Smart 1 Ads.
    #
    # And it saved its OWN reshaped payload rather than what Brandfetch
    # returned. Every other caller stores the raw shape, which is what
    # hub/client_brand.py walks -- `logos` as a list of objects each carrying
    # `formats`. This route stored a bare `logo` URL string and no `logos` key
    # at all, so a client whose brand was last saved here showed colours and NO
    # LOGO on their Client 360 card, and since the save is keyed on the domain
    # it overwrote a good raw payload with one the card cannot read. Nothing
    # errored at either end. lookup() saves the raw payload; the reshaping
    # below stays here, where it is this panel's own response format.
    from hub import brand_lookup

    domain = (request.args.get("domain") or "").strip().lower()
    if not domain:
        return jsonify({"error": "A domain is required."}), 400
    domain = re.sub(r"^https?://", "", domain).removeprefix("www.").split("/")[0].split("?")[0]
    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        return jsonify({"error": "That does not look like a valid domain (e.g. acme.com)."}), 400

    try:
        result = brand_lookup.lookup(
            domain, client=(request.args.get("client") or "").strip(),
            module="suite_panel")
        data = result.get("payload") or {}
        if not result.get("found"):
            note = result.get("note") or ""
            if result.get("unconfigured"):
                return jsonify({"error": "Brandfetch is not configured. Set BRANDFETCH_API_KEY "
                                         "to enable brand lookup."}), 400
            if "Nothing is published" in note:
                return jsonify({"error": f"No brand data found for {domain}."}), 404
            # lookup() reports a refusal by status; 429 keeps its own wording
            # because "try again later" is different advice from "the key was
            # refused", and this panel has always drawn that line.
            if "429" in note:
                return jsonify({"error": "Brandfetch quota exceeded. Try again later."}), 429
            return jsonify({"error": note or "Brandfetch error"}), 502

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
        # Deliberately no save here. lookup() has already stored what
        # Brandfetch returned, in the raw shape hub/client_brand.py reads.
        # Saving `payload` as well would put this panel's own response format
        # back over it -- which is precisely the bug above.
        return jsonify(payload)
    except Exception as exc:  # noqa: BLE001
        # Was `except requests.RequestException`, which nothing in this route
        # can raise any more: lookup() catches its own network error and hands
        # back a note, so that handler had become a 502 that could never fire.
        # What is left to guard is the reshaping below the lookup, so the guard
        # says that rather than claiming Brandfetch was unreachable.
        return jsonify({"error": f"The brand data could not be read: {exc}"}), 502


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

    name = (b.get("name") or "").strip()
    if not name:
        # Refused before the key is claimed: nothing happened, so the rep's
        # next press with the same key is a first attempt rather than a replay.
        return jsonify({"error": "Account name is required."}), 400

    state, rec = idem_claim(idem_key)
    if state == "done":
        return jsonify(rec["body"]), rec["status"]
    if state == "running":
        # The twin of this request is mid-flight, most likely on the other
        # worker. Answering 409 is the whole point: creating a second
        # sub-account for one client is what this guard exists to prevent, and
        # it is not undoable from this panel.
        return jsonify({
            "error": "in_progress",
            "message": "That submission is already being processed. "
                       "Give it a moment rather than sending it again.",
        }), 409

    try:
        # "No duplicate" and "we could not look" are different answers, and
        # only the first is a reason to go ahead without thinking about it.
        # This used to log a warning and return a clean 201, so a rep whose
        # check never ran could not tell that from a clear one -- and the
        # confirm-and-resubmit path turns the check off entirely, so on the
        # retry both guards are down at once.
        duplicate_check = "clear"
        duplicate_check_error = ""
        if b.get("confirmDuplicate"):
            duplicate_check = "skipped"
        else:
            try:
                dup = ghl("/locations/search", query={"companyId": _env("GHL_COMPANY_ID"), "limit": "5", "query": name})
                matches = [
                    loc for loc in dup.get("locations") or []
                    if str(loc.get("name") or "").strip().lower() == name.lower()
                ]
                if matches:
                    idem_release(idem_key)
                    return jsonify({
                        "error": "duplicate",
                        "message": f'An account named "{name}" already exists.',
                        "duplicates": [{"id": m.get("id") or m.get("_id"), "name": m.get("name")} for m in matches],
                    }), 409
            except Exception as dup_err:  # noqa: BLE001 — never block creation on a flaky check
                app.logger.warning("duplicate check failed, proceeding: %s", dup_err)
                duplicate_check = "not_measured"
                duplicate_check_error = str(dup_err)

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

        result["duplicateCheck"] = duplicate_check
        if duplicate_check == "not_measured":
            # Said in the answer, not only in a server log nobody is reading:
            # the panel can offer the search again rather than the rep learning
            # about the second account from the client.
            result["duplicateCheckWarning"] = (
                "The duplicate check could not run, so this account was created "
                "without one" + (f" ({duplicate_check_error})" if duplicate_check_error else "")
                + ". Check the account list for another of the same name.")
        idem_set(idem_key, 201, result)
        audit.log("suite", "account_created", actor=actor_name(), name=name,
                  locationId=new_id, duplicateCheck=duplicate_check)
        return jsonify(result), 201
    except Exception as err:  # noqa: BLE001
        # The claim goes with it: nothing was created, so the next press is a
        # new attempt rather than a replay of one that never happened.
        idem_release(idem_key)
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
    # Through hub/signing.py. PANEL_PASSWORD is a shared login rather than a
    # signing key, and the literal behind it is in this file -- either one
    # lets somebody forge the state parameter that is the whole CSRF defence
    # on an install flow that creates client sub-accounts.
    try:
        from hub import signing as _signing
        return _signing.timed_serializer("ghl-oauth-state")
    except Exception:                       # noqa: BLE001  standalone module
        from itsdangerous import URLSafeTimedSerializer
        import secrets as _secrets
        return URLSafeTimedSerializer(_session_secret() or _secrets.token_hex(32),
                                      salt="ghl-oauth-state")


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
        return ("That authorization link didn't come from this Hub, or it sat "
                "unused too long. Start again from Suite.", 400)
    code = request.args.get("code")
    if not code:
        return ("HighLevel didn't send an authorization code.", 400)
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
    """Delete a sub-account, and write down which one.

    This is the most destructive thing the Hub can do, it is not undoable from
    here, and the activity entry is the only record that it happened. That
    entry used to carry `?name=` from the query string — the name the *browser*
    claimed, never checked against the account being deleted. Delete
    `loc_9` while passing somebody else's name and that is what the log said;
    omit the parameter and it recorded an empty one. A record of a deletion
    that names the wrong account is worse than one that names none, because it
    is the thing somebody reconstructs the incident from.

    So the account is read first and **its own name** is what is recorded. A
    read that fails does not stop the deletion — the rep asked for it and GHL
    is the authority on whether it can happen — but then the claimed name is
    recorded as *claimed*, never as fact, and the entry says the name was not
    confirmed.
    """
    delete_twilio = str(request.args.get("deleteTwilioAccount")) == "true"
    claimed = (request.args.get("name") or "")[:200]

    confirmed_name, name_source = "", "not confirmed"
    try:
        current = ghl(f"/locations/{loc_id}") or {}
        loc = current.get("location") if isinstance(current.get("location"), dict) else current
        confirmed_name = str((loc or {}).get("name") or "")[:200]
        if confirmed_name:
            name_source = "confirmed"
    except Exception as read_err:  # noqa: BLE001
        app.logger.warning("could not read %s before deleting it: %s", loc_id, read_err)

    try:
        data = ghl(f"/locations/{loc_id}", method="DELETE", query={"deleteTwilioAccount": str(delete_twilio).lower()})
    except Exception as err:  # noqa: BLE001
        return send_error(err)

    audit.log("suite", "account_deleted", actor=actor_name(),
              name=confirmed_name or None, claimedName=claimed or None,
              nameSource=name_source, locationId=loc_id,
              deleteTwilioAccount=delete_twilio or None)
    return jsonify({"ok": True, "result": data, "name": confirmed_name,
                    "nameSource": name_source})


@app.route("/api/audit")
def api_audit():
    # Both bounds were here and the try was not, so ?limit=abc was a 500 on
    # the activity log of the panel that creates and deletes client
    # sub-accounts -- the one screen somebody reads to reconstruct what
    # happened. This module already clamps the limits it forwards upstream
    # through _bounded(); this is the same rule on its own route.
    limit = clamp_int(request.args.get("limit"), 300, 1, 1000)
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
