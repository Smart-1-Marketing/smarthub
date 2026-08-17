"""
Orchestration: take a consented client token, do everything we can with it,
record what happened, then throw the token away.

The ordering rule is that a partial success is still a success. If Analytics
works and Tag Manager fails, the client sees Analytics confirmed and one
honest line about the other -- not a blanket error that makes them think
nothing worked and click the link again.
"""

import datetime as _dt
import logging

from . import config
from . import google_client as gc
from .models import (
    AccessGrant, GRANTED, FAILED, WAITING, PENDING, SKIPPED, db, utcnow,
)

log = logging.getLogger(__name__)


def audit(event, **extra):
    """Write to the Hub activity log if it is there, otherwise the app log."""
    try:  # pragma: no cover - Hub only
        from hub import audit as hub_audit  # type: ignore
        hub_audit.log("google_access", event, **extra)
        return
    except Exception:
        pass
    log.info("google_access:%s %s", event, extra)


def _grant(request, service):
    existing = request.grant_for(service)
    if existing:
        return existing
    row = AccessGrant(request_id=request.id, service=service, status=PENDING)
    db.session.add(row)
    request.grants.append(row)
    return row


def _ok(row, resource_id=None, resource_name=None, role=None):
    row.status = GRANTED
    row.resource_id = resource_id
    row.resource_name = resource_name
    row.role_granted = role
    row.granted_at = utcnow()
    row.checked_at = utcnow()
    row.error_detail = None


def _fail(row, detail):
    row.status = FAILED
    row.error_detail = str(detail)[:2000]
    row.checked_at = utcnow()


def run_grants(request, token, selected_services):
    """
    Execute every automated grant the client consented to.

    Returns a list of per-service result dicts for the receipt page. Each has
    a `public` message that is safe to render; `error_detail` stays in the
    database for staff.
    """
    results = []
    scopes = gc.granted_scopes(token)

    def has_scope(service_key):
        needed = config.SERVICES[service_key]["scopes"]
        return all(s in scopes for s in needed) if scopes else True

    # --- Analytics -----------------------------------------------------
    if "ga4" in selected_services:
        row = _grant(request, "ga4")
        try:
            if not has_scope("ga4"):
                raise gc.GoogleError(
                    "analytics scope not granted",
                    "The Analytics permission was unchecked on the Google screen.",
                )
            props = gc.retry(lambda: gc.ga4_list_properties(token))
            if not props:
                raise gc.GoogleError(
                    "no GA4 properties visible",
                    "That Google account cannot see any Analytics properties.",
                )
            done, names = [], []
            for prop in props:
                gc.retry(lambda p=prop: gc.ga4_add_user(
                    token, p["resource"], config.AGENCY_EMAIL,
                    config.SERVICES["ga4"]["role_code"],
                ))
                done.append(prop["resource"])
                names.append(prop["name"])
            _ok(row, ",".join(done), "; ".join(names), "Administrator")
            results.append({
                "service": "ga4", "status": GRANTED,
                "public": f"Added to {len(done)} propert{'y' if len(done) == 1 else 'ies'}.",
                "names": names,
            })
        except gc.GoogleError as exc:
            _fail(row, exc.detail)
            results.append({"service": "ga4", "status": FAILED, "public": exc.public})
        except Exception as exc:  # pragma: no cover
            _fail(row, repr(exc))
            results.append({"service": "ga4", "status": FAILED,
                            "public": "Something went wrong adding us to Analytics."})

    # --- Tag Manager ---------------------------------------------------
    if "gtm" in selected_services:
        row = _grant(request, "gtm")
        try:
            if not has_scope("gtm"):
                raise gc.GoogleError(
                    "tagmanager scope not granted",
                    "The Tag Manager permission was unchecked on the Google screen.",
                )
            accounts = gc.retry(lambda: gc.gtm_list_accounts(token))
            if not accounts:
                raise gc.GoogleError(
                    "no GTM accounts visible",
                    "That Google account cannot see any Tag Manager accounts.",
                )
            done, names = [], []
            for acct in accounts:
                gc.retry(lambda a=acct: gc.gtm_add_user(
                    token, a["account_id"], config.AGENCY_EMAIL,
                    config.SERVICES["gtm"]["role_code"],
                ))
                done.append(acct["account_id"])
                names.append(acct["name"])
            _ok(row, ",".join(done), "; ".join(names), "Administrator")
            results.append({
                "service": "gtm", "status": GRANTED,
                "public": f"Added to {len(done)} account{'' if len(done) == 1 else 's'}.",
                "names": names,
            })
        except gc.GoogleError as exc:
            _fail(row, exc.detail)
            results.append({"service": "gtm", "status": FAILED, "public": exc.public})
        except Exception as exc:  # pragma: no cover
            _fail(row, repr(exc))
            results.append({"service": "gtm", "status": FAILED,
                            "public": "Something went wrong adding us to Tag Manager."})

    # --- Business Profile ----------------------------------------------
    if "gbp" in selected_services and config.GBP_ENABLED:
        row = _grant(request, "gbp")
        try:
            accounts = gc.retry(lambda: gc.gbp_list_accounts(token))
            if not accounts:
                raise gc.GoogleError(
                    "no GBP accounts visible",
                    "That Google account cannot see any Business Profiles.",
                )
            done, names = [], []
            for acct in accounts:
                gc.retry(lambda a=acct: gc.gbp_add_admin(
                    token, a["resource"], config.AGENCY_EMAIL,
                    config.SERVICES["gbp"]["role_code"],
                ))
                done.append(acct["resource"])
                names.append(acct["name"])
            _ok(row, ",".join(done), "; ".join(names), "Manager")
            results.append({
                "service": "gbp", "status": GRANTED,
                "public": f"Added to {len(done)} profile{'' if len(done) == 1 else 's'}.",
                "names": names,
            })
        except gc.GoogleError as exc:
            _fail(row, exc.detail)
            results.append({"service": "gbp", "status": FAILED, "public": exc.public})

    # --- Google Ads ----------------------------------------------------
    # Read their customer IDs with the client token, then invite from our
    # manager account. The client still accepts inside Google Ads.
    if "ads" in selected_services:
        row = _grant(request, "ads")
        try:
            customers = gc.retry(lambda: gc.ads_list_accessible_customers(token))
            if not customers:
                raise gc.GoogleError(
                    "no accessible Ads customers",
                    "That Google account cannot see any Google Ads accounts.",
                )
            invited, failed = [], []
            for cid in customers:
                try:
                    gc.ads_send_link_invitation(cid)
                    invited.append(cid)
                except gc.GoogleError as exc:
                    if "already" in str(exc.detail).lower():
                        invited.append(cid)
                    else:
                        failed.append(f"{cid}: {exc.detail}")
            if not invited:
                raise gc.GoogleError("; ".join(failed) or "no invitations sent",
                                     "We could not send the Google Ads invitation.")
            row.status = WAITING
            row.resource_id = ",".join(invited)
            row.resource_name = ", ".join(_pretty_cid(c) for c in invited)
            row.role_granted = "Manager account link (pending)"
            row.error_detail = "; ".join(failed) if failed else None
            row.checked_at = utcnow()
            results.append({
                "service": "ads", "status": WAITING,
                "public": (
                    "Invitation sent. Open Google Ads and accept it under "
                    "Admin \u2192 Access and security \u2192 Managers."
                ),
                "names": [_pretty_cid(c) for c in invited],
            })
        except gc.GoogleError as exc:
            _fail(row, exc.detail)
            results.append({"service": "ads", "status": FAILED, "public": exc.public})

    # --- Manual services -------------------------------------------------
    # Search Console has no user-management API at all. Business Profile has
    # one, but only after Google approves the allowlist -- until then it is
    # manual too. Either way: record the task and say so on the page. The
    # failure mode to avoid is quietly marking these "skipped" and letting
    # everyone believe access exists.
    for key in selected_services:
        if config.SERVICES.get(key, {}).get("mode") != "manual":
            continue
        row = _grant(request, key)
        if row.status != GRANTED:
            row.status = WAITING
            row.role_granted = config.SERVICES[key]["role_text"]
            row.checked_at = utcnow()
        results.append({
            "service": key, "status": row.status,
            "public": "Needs one manual step. Instructions below.",
            "names": [],
        })

    # Anything requested but not selected is explicitly declined, so the Hub
    # can tell "they said no" apart from "we never asked".
    for key in request.services:
        if key not in selected_services:
            row = _grant(request, key)
            if row.status == PENDING:
                row.status = SKIPPED
                row.checked_at = utcnow()

    request.status = request.rollup()
    if request.status == GRANTED and not request.completed_at:
        request.completed_at = utcnow()
    db.session.commit()

    audit(
        "grant_run",
        request_id=request.id,
        client=request.client_name,
        consent_email=request.consent_email,
        granted=[r["service"] for r in results if r["status"] == GRANTED],
        waiting=[r["service"] for r in results if r["status"] == WAITING],
        failed=[r["service"] for r in results if r["status"] == FAILED],
    )
    return results


def _pretty_cid(cid):
    cid = str(cid)
    if len(cid) == 10:
        return f"{cid[0:3]}-{cid[3:6]}-{cid[6:]}"
    return cid


def refresh_ads_status(request):
    """Poll our manager account to see if pending Ads links were accepted."""
    row = request.grant_for("ads")
    if not row or row.status != WAITING or not row.resource_id:
        return row
    accepted, still_pending = [], []
    for cid in row.resource_id.split(","):
        cid = cid.strip()
        if not cid:
            continue
        try:
            status = gc.ads_link_status(cid)
        except gc.GoogleError as exc:
            row.error_detail = str(exc.detail)[:2000]
            continue
        if status == "ACTIVE":
            accepted.append(cid)
        else:
            still_pending.append(cid)
    row.checked_at = utcnow()
    if accepted and not still_pending:
        row.status = GRANTED
        row.granted_at = utcnow()
        row.role_granted = "Manager account link"
        row.error_detail = None
    request.status = request.rollup()
    db.session.commit()
    audit("ads_status_refresh", request_id=request.id,
          accepted=len(accepted), pending=len(still_pending))
    return row


def expiry_from_now(days=None):
    days = days or config.INVITE_TTL_DAYS
    return utcnow() + _dt.timedelta(days=days)
