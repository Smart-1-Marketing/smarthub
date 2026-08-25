"""
Routes.

Two blueprints, deliberately separate:

  public_bp  at /connect          -- no login. This is what a client opens.
  admin_bp   at /tools/google-access -- Hub login required.

Keeping the client flow off /tools means the Hub's AuthGuard cannot
accidentally bounce a paying client's Google redirect to a login screen.
"""

import hmac
import json
import logging
import time
from collections import defaultdict, deque
from functools import wraps

from flask import (
    Blueprint, abort, jsonify, redirect, render_template, request, url_for,
)

from . import config
from . import google_client as gc
from .grants import audit, expiry_from_now, run_grants
from .models import (
    AccessGrant, AccessRequest, OAuthState, EXPIRED, GRANTED, PENDING,
    REVOKED, SKIPPED, WAITING, db, new_token, utcnow,
)

log = logging.getLogger(__name__)

public_bp = Blueprint(
    "google_access_public", __name__,
    url_prefix="/connect", template_folder="templates",
)
admin_bp = Blueprint(
    "google_access", __name__,
    url_prefix="/tools/google-access", template_folder="templates",
)


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------

_hits = defaultdict(deque)


def _client_ip():
    # Last hop, not the first. A client-supplied first hop is spoofable and
    # the suite audit found exactly that bypass in three other apps.
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[-1].strip()
    return request.remote_addr or "unknown"


def rate_limited(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        ip = _client_ip()
        now = time.time()
        bucket = _hits[ip]
        while bucket and now - bucket[0] > 3600:
            bucket.popleft()
        if len(bucket) >= config.PUBLIC_RATE_LIMIT:
            return render_template(
                "connect_error.html",
                headline="Too many attempts",
                body="Wait a few minutes and open your link again.",
                cfg=config,
            ), 429
        bucket.append(now)
        return fn(*a, **kw)
    return wrapper


def require_login(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        try:  # pragma: no cover - Hub only
            from hub.auth import login_required  # type: ignore
            return login_required(fn)(*a, **kw)
        except ImportError:
            return fn(*a, **kw)
    return wrapper


def _lookup(token):
    """Constant-time token lookup that fails closed."""
    if not token or len(token) < 20:
        abort(404)
    row = AccessRequest.query.filter_by(token=token).first()
    if not row or not hmac.compare_digest(row.token, token):
        abort(404)
    return row


# --------------------------------------------------------------------------
# Public: the client-facing flow
# --------------------------------------------------------------------------

@public_bp.route("/<token>")
@rate_limited
def connect(token):
    req = _lookup(token)

    if req.expired():
        if req.status != EXPIRED:
            req.status = EXPIRED
            db.session.commit()
        return render_template(
            "connect_error.html",
            headline="This link has expired",
            body=(
                f"Links stay live for {config.INVITE_TTL_DAYS} days. "
                "Ask us for a fresh one and we will send it straight over."
            ),
            cfg=config,
        ), 410

    missing = config.configured()
    if missing:
        log.error("google_access: not configured: %s", ", ".join(missing))
        return render_template(
            "connect_error.html",
            headline="Not quite ready",
            body="This link is not switched on yet. Give us a call and we will sort it.",
            cfg=config,
        ), 503

    if not req.opened_at:
        req.opened_at = utcnow()
        db.session.commit()
        audit("invite_opened", request_id=req.id, client=req.client_name)

    services = [k for k in config.SERVICE_ORDER if k in req.services]
    return render_template(
        "connect.html",
        req=req,
        services=services,
        cfg=config,
        SERVICES=config.SERVICES,
        done=req.status in (GRANTED, WAITING),
    )


@public_bp.route("/<token>/start", methods=["POST"])
@rate_limited
def start(token):
    req = _lookup(token)
    if req.expired():
        return redirect(url_for("google_access_public.connect", token=token))

    # `.get` rather than `[...]`: a request created before Google Ads was
    # parked still carries "ads" in its stored list, and a KeyError here would
    # take out the button for the whole invite rather than one dead row.
    chosen = [k for k in request.form.getlist("service")
              if k in req.services and k in config.SERVICES]
    # Search Console has no API path, so it never needs consent.
    consenting = [k for k in chosen if config.SERVICES.get(k, {}).get("scopes")]

    if not consenting:
        # Only manual items were ticked -- skip Google entirely.
        _record_manual_only(req, chosen)
        return redirect(url_for("google_access_public.done", token=token))

    state = OAuthState(state=new_token(), request_id=req.id,
                       services=json.dumps(chosen))
    db.session.add(state)
    db.session.commit()

    url = gc.authorization_url(
        state.state,
        config.scopes_for(consenting),
        login_hint=req.client_email or None,
    )
    return redirect(url)


def _record_manual_only(req, chosen):
    for key in req.services:
        row = req.grant_for(key)
        if not row:
            row = AccessGrant(request_id=req.id, service=key)
            db.session.add(row)
            req.grants.append(row)
        row.status = WAITING if key in chosen else SKIPPED
        row.checked_at = utcnow()
    req.status = req.rollup()
    db.session.commit()


@public_bp.route("/callback")
@rate_limited
def callback():
    error = request.args.get("error")
    state_value = request.args.get("state", "")
    code = request.args.get("code", "")

    state = OAuthState.query.filter_by(state=state_value).first() if state_value else None
    if not state or state.used:
        return render_template(
            "connect_error.html",
            headline="That sign-in could not be verified",
            body="Open your original link again and retry. Nothing was changed.",
            cfg=config,
        ), 400

    age = (utcnow() - state.created_at).total_seconds() / 60.0
    if age > config.STATE_TTL_MINUTES:
        state.used = True
        db.session.commit()
        return render_template(
            "connect_error.html",
            headline="That sign-in timed out",
            body="Open your original link again and retry. Nothing was changed.",
            cfg=config,
        ), 400

    state.used = True
    db.session.commit()

    req = AccessRequest.query.get(state.request_id)
    if not req:
        abort(404)

    if error or not code:
        audit("consent_declined", request_id=req.id, error=error)
        return render_template(
            "connect_error.html",
            headline="Sign-in was cancelled",
            body="Nothing changed. Open your link again whenever you are ready.",
            back=url_for("google_access_public.connect", token=req.token),
            cfg=config,
        ), 200

    token = None
    try:
        payload = gc.exchange_code(code)
        token = payload.get("access_token")
        if not token:
            raise gc.GoogleError("no access_token in exchange response")
        req.consent_email = gc.userinfo_email(token) or req.consent_email
        db.session.commit()
        run_grants(req, token, state.service_list)
    except gc.GoogleError as exc:
        log.warning("google_access: callback failed: %s", exc.detail)
        return render_template(
            "connect_error.html",
            headline="We could not finish that",
            body=exc.public,
            back=url_for("google_access_public.connect", token=req.token),
            cfg=config,
        ), 200
    finally:
        # The whole point of the design: the credential does not survive the
        # request that created it.
        gc.revoke(token)

    return redirect(url_for("google_access_public.done", token=req.token))


@public_bp.route("/<token>/done")
@rate_limited
def done(token):
    req = _lookup(token)
    grants = {g.service: g for g in req.grants}
    services = [k for k in config.SERVICE_ORDER if k in req.services]
    return render_template(
        "connect_done.html",
        req=req,
        grants=grants,
        services=services,
        cfg=config,
        SERVICES=config.SERVICES,
    )


# --------------------------------------------------------------------------
# Admin: inside the Hub
# --------------------------------------------------------------------------

@admin_bp.route("/")
@require_login
def index():
    rows = AccessRequest.query.order_by(AccessRequest.created_at.desc()).limit(200).all()
    return render_template(
        "admin_index.html",
        requests=rows,
        cfg=config,
        SERVICES=config.SERVICES,
        SERVICE_ORDER=config.SERVICE_ORDER,
        missing=config.configured(),
        oauth_source=config.oauth_client_source(),
    )


@admin_bp.route("/api/requests", methods=["POST"])
@require_login
def create_request():
    """Create one invite link.

    The form asks whether this is an existing client or a new business, and
    that answer is required rather than defaulted. It decides two different
    things:

      existing -- the name must resolve in the Hub's client registry, so the
                  invite joins onto the Client 360 record. A typed name that
                  matches nothing is refused, and pointed back at the lookup,
                  rather than accepted: an invite filed against a client
                  nobody can find is how a granted property ends up reported
                  as "no access on file".
      new      -- there is no client record yet, so the prospect is written
                  through `hub.leads` on the way past. Otherwise the only
                  trace of a business we just asked for Google access is a
                  row in this module that nothing else reads.

    There is no Hub client ID field any more. It was optional, typed by hand,
    and therefore blank on nearly every row -- see `client_status` below for
    what that cost. The join is derived from the name and website instead.
    """
    data = request.get_json(silent=True) or request.form
    name = (data.get("client_name") or "").strip()
    kind = (data.get("client_type") or "").strip().lower()
    email = (data.get("client_email") or "").strip()
    website = (data.get("website") or "").strip()

    if kind not in ("existing", "new"):
        return jsonify(ok=False, error="Say whether this is an existing client or a new one."), 400
    if not name:
        return jsonify(ok=False, error="Client name is required."), 400

    services = data.getlist("service") if hasattr(data, "getlist") else data.get("services")
    services = [s for s in (services or []) if s in config.SERVICES]
    if not services:
        return jsonify(ok=False, error="Pick at least one service."), 400

    match, lookup_error = _registry_match(name)

    if kind == "existing":
        if lookup_error:
            # "The client list is down" and "that client does not exist" are
            # different answers. Blocking on the first one strands the rep.
            log.warning("google_access: client lookup failed: %s", lookup_error)
        elif not match:
            return jsonify(
                ok=False,
                error=("No client called that. Pick one from the lookup, or "
                       "choose New if this is not a client yet."),
            ), 400
        if match:
            website = website or match.get("url") or match.get("domain") or ""
    elif match:
        # Filed as new against a name the registry already knows. Creating a
        # lead for a live client duplicates them in the Suite, which is the
        # one outcome the Leads panel cannot undo for you.
        return jsonify(
            ok=False,
            error=f"{match['name']} is already a client. Switch to Existing client.",
        ), 400

    req = AccessRequest(
        token=new_token(),
        client_name=name,
        client_email=email or None,
        website=website or None,
        created_by=_current_user(),
        expires_at=expiry_from_now(),
        status=PENDING,
    )
    req.services = services
    db.session.add(req)
    db.session.commit()

    lead = _store_lead(req) if kind == "new" else None

    audit("invite_created", request_id=req.id, client=name,
          client_type=kind, services=services, actor=req.created_by,
          lead_id=(lead or {}).get("lead_id") or None)

    return jsonify(
        ok=True,
        id=req.id,
        client_type=kind,
        link=f"{config.PUBLIC_BASE_URL}/connect/{req.token}",
        lead=lead,
    )


def _registry_match(name):
    """Exact client-registry hit for `name`, or (None, reason-it-could-not-look).

    Exact and nothing else. `clients_registry.find_client` lower-cases and
    compares whole names, which is the rule the rest of the Hub follows --
    "Riverside HVAC" must never collect "Riverside HVAC Supply".
    """
    try:
        from hub import clients_registry
        return clients_registry.find_client(name), ""
    except Exception as exc:                              # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _store_lead(req):
    """Write a new business to the Hub's lead store. Never raises.

    Delivery to Smart 1 Suite only runs when we have an email address. A
    contact with no way to reach them is worse than no contact: it lands in
    the Suite, looks handled, and nobody can call it. Without one the lead is
    still stored and still visible in the Leads panel.
    """
    fields = {"name": req.client_name, "company": req.client_name}
    if req.client_email:
        fields["email"] = req.client_email
    if req.website:
        fields["website"] = req.website
    try:
        from hub import leads
        meta = {"google_access_request_id": req.id,
                "services": req.services}
        if req.client_email:
            out = leads.capture_and_deliver(
                "google_access", "access-request", fields,
                client=req.client_name, meta=meta)
            return {"lead_id": out.get("lead_id"),
                    "delivered": bool(out.get("delivered")),
                    "note": out.get("note") or ""}
        row = leads.capture("google_access", "access-request", fields,
                            client=req.client_name, meta=meta)
        return {"lead_id": row.get("id"), "delivered": False,
                "note": "Saved as a lead. Add an email to push it to Smart 1 Suite."}
    except Exception as exc:                              # noqa: BLE001
        # The invite is already committed and is the thing the rep is waiting
        # on. A lead-store fault is reported, not raised over the top of it.
        log.warning("google_access: lead capture failed: %s", exc)
        return {"lead_id": "", "delivered": False,
                "note": "The link was created, but the lead could not be saved."}


@admin_bp.route("/r/<int:request_id>")
@require_login
def detail(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    return render_template(
        "admin_detail.html",
        req=req,
        grants={g.service: g for g in req.grants},
        cfg=config,
        SERVICES=config.SERVICES,
        SERVICE_ORDER=config.SERVICE_ORDER,
        link=f"{config.PUBLIC_BASE_URL}/connect/{req.token}",
    )


@admin_bp.route("/api/requests/<int:request_id>/mark", methods=["POST"])
@require_login
def mark(request_id):
    """Manually confirm a step we cannot verify by API (Search Console)."""
    req = AccessRequest.query.get_or_404(request_id)
    data = request.get_json(silent=True) or request.form
    service = data.get("service")
    status = data.get("status")
    # RETIRED_SERVICES too: a request created before Google Ads was parked
    # still shows an Ads row, and refusing to let anyone close it off would
    # leave those invites reading "waiting" for good.
    known = service in config.SERVICES or service in config.RETIRED_SERVICES
    if not known or status not in (GRANTED, WAITING, REVOKED, SKIPPED):
        return jsonify(ok=False, error="Unknown service or status."), 400
    row = req.grant_for(service)
    if not row:
        row = AccessGrant(request_id=req.id, service=service)
        db.session.add(row)
        req.grants.append(row)
    row.status = status
    row.checked_at = utcnow()
    if status == GRANTED:
        row.granted_at = utcnow()
        row.role_granted = config.SERVICES.get(service, {}).get("role_text")
    req.status = req.rollup()
    db.session.commit()
    audit("grant_marked", request_id=req.id, service=service,
          status=status, actor=_current_user())
    return jsonify(ok=True, status=req.status)


@admin_bp.route("/api/requests/<int:request_id>/extend", methods=["POST"])
@require_login
def extend(request_id):
    req = AccessRequest.query.get_or_404(request_id)
    req.expires_at = expiry_from_now()
    if req.status == EXPIRED:
        req.status = req.rollup()
    db.session.commit()
    return jsonify(ok=True, expires_at=req.expires_at.isoformat())


@admin_bp.route("/api/client/<hub_client_id>/status")
@require_login
def client_status(hub_client_id):
    """Feed for the Client 360 access card.

    `hub_client_id` is matched first, then the derived client key. The second
    lookup is what makes this card work at all: the id was an optional field
    on the invite form, so almost every request in the table has it blank and
    this endpoint answered "no access on file" for clients whose Analytics we
    had been granted months earlier. The form no longer asks for it, so the
    first lookup now only ever matches a legacy row; the derived key is the
    join. The path segment keeps the name because Client 360 links to it that
    way; it accepts a client name or a URL just as happily.
    """
    rows = (
        AccessRequest.query
        .filter_by(hub_client_id=hub_client_id)
        .order_by(AccessRequest.created_at.desc())
        .all()
    )
    matched_on = "hub_client_id"
    if not rows:
        try:
            from hub.client_key import resolve
            want = resolve(name=hub_client_id, url=hub_client_id)["key"]
        except Exception:                                 # noqa: BLE001
            want = ""
        if want:
            from hub.client_key import same_client
            rows = [r for r in (AccessRequest.query
                                .order_by(AccessRequest.created_at.desc())
                                .limit(500).all())
                    if same_client(hub_client_id, hub_client_id,
                                   r.client_name or "", r.website or "")]
            matched_on = "client_key"
    if not rows:
        return jsonify(ok=True, found=False, services={})
    latest = rows[0]
    return jsonify(
        ok=True,
        found=True,
        matched_on=matched_on,
        client_name=latest.client_name,
        request_id=latest.id,
        status=latest.status,
        consent_email=latest.consent_email,
        services={
            g.service: {
                "status": g.status,
                "role": g.role_granted,
                "resource": g.resource_name,
                "granted_at": g.granted_at.isoformat() if g.granted_at else None,
            }
            for g in latest.grants
        },
    )


@admin_bp.route("/api/health")
def health():
    return jsonify(
        ok=True,
        module="google_access",
        configured=not config.configured(),
        missing=config.configured(),
        oauth_client_from=config.oauth_client_source(),
        gbp_enabled=config.GBP_ENABLED,
    )


def _current_user():
    try:  # pragma: no cover - Hub only
        from flask import session
        return session.get("user") or session.get("email") or "hub"
    except Exception:
        return "hub"
