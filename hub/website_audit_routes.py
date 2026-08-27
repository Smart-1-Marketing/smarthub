"""The Website Audit tool — Tools › Sales, at /tools/website-audit.

One screen that answers "what is going on with this business's marketing?"
for a client we already have and for a prospect nobody has ever scanned.

The three things it does that no existing screen did:

* **It pulls, rather than asking somebody to go and look.** Pick a client and
  the tool finds their website through the registry and the discovered-URL
  overlay, then reads whatever the last audit found. Nothing is typed twice.
* **What they are already spending is the first thing on it.** The rest of
  the audit sits underneath, in the same shape Client 360's *What we know
  about this business* card uses — because it is literally the same reading of
  the same audit, and a second description of it would drift.
* **Every scan is a lead.** Somebody typed a business and a website into this
  Hub, which is a prospect whatever else it is. `hub/leads.py` owns the
  writing and the panel; there is no second lead book here.

A blueprint, so it shares the hub's Jinja environment and its client APIs.
Which means the login gate has to be **on the blueprint** — `wsgi.py`'s
AuthGuard only covers dispatcher-mounted modules, and the hub app guards its
own pages one view at a time. Commercial Builder shipped forty unguarded
routes for exactly this reason and `hub/auth.py` names the failure in its own
docstring; the next route added here must not have to remember.

Nothing here starts an audit. Starting one spends an Insites credit and
belongs to the module that owns scans — the browser posts to
`/scans/api/scans` and this page watches for the result, because reaching
into a dispatcher-mounted module from a hub route is the `flask.g` trap
CLAUDE.md names at length.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, redirect, render_template, request

bp = Blueprint("website_audit_tool", __name__)

STORE = "website_audit"


# --------------------------------------------------------------------- gate
@bp.before_request
def _require_login():
    """One check in front of every route on this blueprint.

    This tool names clients, what they spend and what we think is wrong with
    their websites, and it carries write routes that create leads. A guard
    written per view is a guard the next route does not have to remember.
    """
    from hub import access, current_user
    if current_user():
        return None
    if access.wants_json(request.path or "/", request.headers.get("Accept", "")):
        return jsonify({"ok": False,
                        "error": "Sign in to use the website audit."}), 401
    return redirect("/login?next=" + (request.path or "/"))


def _actor() -> str:
    try:
        from hub import current_user
        return current_user() or ""
    except Exception:                                   # noqa: BLE001
        return ""


def _log(event: str, **extra):
    try:
        from hub import audit
        audit.log("website_audit", event, actor=_actor(), **extra)
    except Exception:                                   # noqa: BLE001
        pass


# -------------------------------------------------------------------- store
#
# The intake is what a person typed, so it goes through `hub/jsonstore.py`:
# the Render disk is not backed up and a module writing its own JSON loses it
# on a plan change with nothing reading as an error. Keyed by canonical
# domain, because that is the join key everything else here uses -- a name is
# a comparison and a domain is a join.

def _intake_path() -> str:
    from hub import jsonstore
    return os.path.join(jsonstore.data_dir(STORE), "intake.json")


def _all_intake() -> dict:
    from hub import jsonstore
    data = jsonstore.read_json(_intake_path(), default={})
    return data if isinstance(data, dict) else {}


def load_intake(domain: str) -> dict:
    from hub.client_context import canonical_domain
    key = canonical_domain(domain or "")
    if not key:
        return {}
    row = _all_intake().get(key)
    return row if isinstance(row, dict) else {}


def save_intake(domain: str, answers: dict) -> dict:
    """Store what somebody typed about this business. Fills, never clears.

    A field left blank on this visit is not an instruction to forget what was
    answered last time — the overlay rule `hub/client_urls.py` works to. To
    remove an answer, type over it.
    """
    from hub import jsonstore
    from hub.client_context import canonical_domain
    from hub.website_audit import INTAKE_INDEX
    key = canonical_domain(domain or "")
    if not key:
        return {}
    rows = _all_intake()
    row = rows.get(key) if isinstance(rows.get(key), dict) else {}
    for field, value in (answers or {}).items():
        if field not in INTAKE_INDEX:
            continue
        value = str(value or "").strip()[:600]
        if value:
            row[field] = value
    row["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row["updated_by"] = _actor()
    rows[key] = row
    jsonstore.write_json(_intake_path(), rows)
    return row


# -------------------------------------------------------------------- pages
@bp.route("/tools/website-audit")
def page_website_audit():
    from hub import current_user
    return render_template("website_audit.html", user=current_user(),
                           active="tools",
                           client=request.args.get("client", ""),
                           domain=request.args.get("domain", ""))


# ---------------------------------------------------------------------- API
@bp.route("/api/website-audit/spec")
def api_spec():
    """The intake questions, and how old an audit may be before it is stale."""
    from hub import website_audit as wa
    return jsonify({"ok": True,
                    "questions": wa.questions("staff"),
                    "customer_questions": wa.questions("customer"),
                    "stale_days": wa.STALE_DAYS})


@bp.route("/api/website-audit/clients")
def api_clients():
    """Client search, so a rep picks a real client rather than typing a name.

    A typed name that matches nothing files the work under a client nothing
    joins to and still reads as a success — the reason `client_key` refuses a
    substring at length, and the reason the Google orphan list stopped using a
    `prompt()` box.
    """
    from hub import clients_registry
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"ok": True, "clients": []})
    try:
        rows = clients_registry.search_clients(q, limit=12)
    except Exception as exc:                            # noqa: BLE001
        return jsonify({"ok": False, "clients": [],
                        "error": f"The client list could not be read "
                                 f"({type(exc).__name__}), so this is not "
                                 f"measured rather than empty."})
    return jsonify({"ok": True, "clients": [
        {"name": r.get("name") or "", "url": r.get("url") or r.get("domain") or "",
         "domain": r.get("domain") or ""} for r in rows]})


@bp.route("/api/website-audit/websites")
def api_websites():
    """Every website on file for a client, so the right one is audited.

    A client has more than one — the shop, the campaign landing pages, the
    microsite for one location — and `hub/client_urls.py` already keeps the
    list. Picking the first silently audits whichever happened to be stored
    first.
    """
    name = (request.args.get("client") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "A client is required."}), 400
    out, error, seen = [], "", set()

    def _add(url: str, where: str):
        from hub.client_context import canonical_domain
        key = canonical_domain(url or "")
        if not key or key in seen:
            return
        seen.add(key)
        out.append({"url": url, "domain": key, "source": where})

    try:
        from hub import client_urls
        for site in client_urls.sites_for(name):
            _add(site.get("url") or site.get("domain") or "", "on file")
    except Exception as exc:                            # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    try:
        from hub import clients_registry
        row = clients_registry.find_client(name) or {}
        for url in (row.get("url"), row.get("domain")):
            _add(url or "", "client record")
    except Exception as exc:                            # noqa: BLE001
        error = error or f"{type(exc).__name__}: {exc}"
    return jsonify({"ok": True, "websites": out, "error": error,
                    "note": ("" if out else
                             "No website is on file for this client. "
                             + ("We could not read the client list, so that is "
                                "not measured rather than none."
                                if error else
                                "Type the address to audit it — accepting it "
                                "here does not write it to their record."))})


@bp.route("/api/website-audit")
def api_audit():
    """The audit itself: spend first, then everything else worth reading."""
    from hub import website_audit as wa
    domain = (request.args.get("domain") or "").strip()
    intake = load_intake(domain)
    payload = wa.audit(domain, intake=intake)
    payload["client"] = (request.args.get("client") or "").strip()
    return jsonify(payload)


@bp.route("/api/website-audit/intake", methods=["POST"])
def api_intake():
    body = request.get_json(silent=True) or {}
    domain = str(body.get("domain") or "").strip()
    if not domain:
        return jsonify({"ok": False, "error": "A website is required."}), 400
    answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
    row = save_intake(domain, answers)
    if not row:
        return jsonify({"ok": False,
                        "error": "That does not read as a website address."}), 422
    return jsonify({"ok": True, "intake": row})


@bp.route("/api/website-audit/lead", methods=["POST"])
def api_lead():
    """File this audit as a lead.

    Every audit somebody runs here is a prospect — that is what running one
    means — so the row goes to `hub/leads.py`, which owns the store, the
    delivery and the panel. Two rules on it:

    * **A lead with neither an email nor a phone number is refused by name**
      rather than created. A contactless lead reads as a live prospect on
      every count that follows it, which is the rule `modules/ads_builder`
      arrived at for the same reason.
    * **An existing client is filed as a lead against their own name**, not
      as a new business. `client` on the row is what ties it back to the
      record, and `hub/leads.mark_converted` is the same field.
    """
    from hub import leads, website_audit as wa
    body = request.get_json(silent=True) or {}
    domain = str(body.get("domain") or "").strip()
    contact = body.get("contact") if isinstance(body.get("contact"), dict) else {}
    if not (str(contact.get("email") or "").strip()
            or str(contact.get("phone") or "").strip()):
        return jsonify({
            "ok": False,
            "error": "An email address or a phone number is required. A lead "
                     "nobody can contact reads as a live prospect on every "
                     "report that counts it."}), 400

    answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
    if answers:
        save_intake(domain, answers)
    payload = wa.audit(domain, intake=load_intake(domain))
    fields = wa.lead_fields(payload, contact)
    client = str(body.get("client") or "").strip()
    result = leads.capture_and_deliver(
        source="website_audit",
        page=str(body.get("page") or "website-audit"),
        fields=fields,
        client=client,
        meta={"domain": payload.get("domain") or domain,
              "scan_public_id": payload.get("public_id") or "",
              "audit_url": payload.get("scan_url") or "",
              "audit_score": payload.get("score"),
              "audit_read_on": (payload.get("age") or {}).get("read_on") or ""})
    _log("audited", client=client or None, detail=payload.get("domain") or domain,
         lead=result.get("lead_id") or "")
    return jsonify(result)


@bp.route("/api/website-audit/proposal-prefill")
def api_proposal_prefill():
    """What the Proposal Builder should start from, for this website."""
    from hub import website_audit as wa
    domain = (request.args.get("domain") or "").strip()
    payload = wa.audit(domain, intake=load_intake(domain))
    out = wa.proposal_prefill(payload)
    out["ok"] = True
    return jsonify(out)


def register_website_audit(app):
    app.register_blueprint(bp)
    return app
