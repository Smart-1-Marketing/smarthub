"""Display Ad Builder <-> client records.

The renderer knows how to make an ad. It does not know who Smart 1's clients
are, which of them has a gallery, or that a proposal exists -- and it should
not learn, because that knowledge lives in one place in this codebase and
putting a second copy of it in TypeScript is exactly the duplication CLAUDE.md
is about.

So the two joins live here, in Python, next to the client registry:

  start   -- begin a build for a client or a prospect, optionally from one of
             their uploaded proposals, so a new account's first creative comes
             out of the proposal that won it rather than a blank form.
  attach  -- take the finished ads off a project and file them into that
             client's gallery, tagged with date, size, weight and whether they
             meet the spec.

Nothing is re-uploaded. The renderer has already put every finished ad in
Cloudinary, in the same account this Hub uses, so filing means recording the
public_id that already exists. Downloading and re-uploading would double the
storage and break the URL the renderer is holding.
"""
from __future__ import annotations

import logging
import os

import requests
from flask import (Blueprint, jsonify, redirect, render_template, request)

from hub import ad_builder_proxy

logger = logging.getLogger(__name__)

# Ads are filed under their own kind so a gallery can show "the display ads we
# made" separately from stock picks and client uploads.
GALLERY_KIND = "display_ad"


# --------------------------------------------------------------- renderer API

def _api(method: str, path: str, payload: dict | None = None,
         timeout: tuple = (10, 60)):
    """One call to the renderer, with the admin token attached.

    Returns (ok, data). Never raises: every caller is inside a request that
    should explain the problem rather than 500.
    """
    if not ad_builder_proxy.ADMIN_TOKEN:
        return False, {"error": "ADBUILDER_ADMIN_TOKEN is not set, so the "
                                "renderer refuses every request."}
    try:
        r = requests.request(
            method, f"{ad_builder_proxy.AD_BUILDER_URL}{path}",
            json=payload,
            headers={"X-Admin-Token": ad_builder_proxy.ADMIN_TOKEN,
                     "X-Intake-Code": os.environ.get("INTAKE_CODE", "")},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return False, {"error": "The ad builder is not answering "
                                f"({exc.__class__.__name__})."}
    try:
        data = r.json()
    except ValueError:
        data = {"error": (r.text or "")[:300]}
    return r.ok, data


def _project(project_id: str) -> dict | None:
    ok, data = _api("GET", f"/api/project/{project_id}")
    return data if ok and isinstance(data, dict) and data.get("projectId") else None


def finished_ads(project: dict) -> list[dict]:
    """Every finished ad on a project, newest render of each size winning.

    A project is rendered repeatedly while it is being worked on, so the same
    300x250 appears in several batches. Filing all of them would put five
    near-identical files in the client's gallery, which is how a gallery stops
    being useful. Batches are in render order, so later ones overwrite earlier.
    """
    by_size: dict[str, dict] = {}
    for batch in project.get("batches") or []:
        if not isinstance(batch, dict):
            continue
        for ad in batch.get("ads") or []:
            if not isinstance(ad, dict):
                continue
            # Only ads that actually rendered and reached Cloudinary. A failed
            # size has a status and no URL, and filing it would put a broken
            # image in front of a client.
            url = str(ad.get("url") or "")
            if not url.startswith("https://") or not ad.get("publicId"):
                continue
            size = str(ad.get("size") or "").strip()
            by_size[size] = {
                "size": size,
                "public_id": str(ad.get("publicId")),
                "url": url,
                "bytes": int(ad.get("bytes") or 0),
                "status": str(ad.get("status") or ""),
                "concept": str(batch.get("conceptId") or ""),
                "platform": str(batch.get("platform") or ""),
                "rendered_at": str(batch.get("renderedAt") or ""),
            }
    return sorted(by_size.values(), key=lambda a: a["size"])


def _dimensions(size: str) -> tuple[int, int]:
    """'300x250' -> (300, 250). Zeroes when the size is not a pixel pair."""
    try:
        w, h = size.lower().split("x", 1)
        return int(w.strip()), int(h.strip())
    except (ValueError, AttributeError):
        return 0, 0


# ------------------------------------------------------------------ attaching

def attach_ads(*, project: dict, client_name: str, sizes: list | None = None,
               actor: str = "") -> dict:
    """File a project's finished ads into a client's gallery.

    `sizes` limits it to particular ads; empty means all of them. Returns a
    per-ad result rather than a single ok/failed, because filing eight ads
    where one is a duplicate is a success with a note, not a failure.
    """
    from modules.image_picker import filing

    client_name = str(client_name or "").strip()
    if not client_name:
        return {"ok": False, "error": "A client is required."}

    wanted = {str(s).strip() for s in (sizes or []) if str(s).strip()}
    ads = [a for a in finished_ads(project) if not wanted or a["size"] in wanted]
    if not ads:
        return {"ok": False, "error": "This project has no finished ads yet. "
                                      "Render a concept first."}

    campaign = str(project.get("campaignName") or project.get("projectName") or "")
    results, filed, duplicates = [], 0, 0

    for ad in ads:
        width, height = _dimensions(ad["size"])
        fmt = (ad["public_id"].rsplit(".", 1)[-1].lower()
               if "." in ad["public_id"] else "png")

        # Date, product, dimensions, weight, pass or fail -- recorded at filing
        # time so the gallery answers "does this meet spec?" without anyone
        # re-measuring the file.
        try:
            from hub import creative_specs
            spec = creative_specs.check(width=width, height=height,
                                        size_bytes=ad["bytes"], fmt=fmt,
                                        product="Display")
        except Exception as exc:                       # noqa: BLE001
            logger.warning("spec check failed for %s: %s", ad["size"], exc)
            spec = {}

        label = f"Display ads - {campaign}" if campaign else "Display ads"
        res = filing.file_asset(
            client_name=client_name,
            public_id=ad["public_id"],
            url=ad["url"],
            kind=GALLERY_KIND,
            key=str(project.get("projectId") or ""),
            label=label,
            filename=f"{ad['size']}.{fmt}",
            alt=f"{campaign} display ad, {ad['size']}".strip(", "),
            width=width or None, height=height or None,
            size_bytes=ad["bytes"] or None,
            spec=spec,
            provider=GALLERY_KIND,
            saved_by=actor or "display-ad-builder",
        )
        if res.get("ok") and res.get("duplicate"):
            duplicates += 1
        elif res.get("ok"):
            filed += 1
        results.append({"size": ad["size"], **res})

    gallery_url = next((r.get("gallery_url") for r in results
                        if r.get("gallery_url")), "")

    try:
        from hub import audit
        audit.log("display_ads", "creative_attached", actor=actor,
                  client=client_name, project=project.get("projectId"),
                  campaign=campaign, filed=filed, duplicates=duplicates)
    except Exception:                                  # noqa: BLE001
        pass

    # A note on the project itself, so the builder screen shows where the
    # creative went. Best effort -- the ads are already in the gallery and a
    # failed note is not worth failing the attach for.
    note = f"Attached {filed} ad(s) to the gallery for {client_name}"
    if duplicates:
        note += f" ({duplicates} already there)"
    _api("POST", f"/api/project/{project.get('projectId')}/note", {"note": note})

    return {"ok": True, "filed": filed, "duplicates": duplicates,
            "client": client_name, "gallery_url": gallery_url,
            "results": results}


# ------------------------------------------------------------------- starting

def _known_client(name: str) -> dict | None:
    """The registry row for this name, or None. Never raises."""
    try:
        from hub import clients_registry
        return clients_registry.find_client(name)
    except Exception:                                  # noqa: BLE001
        return None


def _recent_prospect(business: str, minutes: int = 60) -> dict | None:
    """A prospect for the same business captured here very recently."""
    key = str(business or "").strip().lower()
    if not key:
        return None
    try:
        from datetime import datetime, timedelta, timezone

        from hub import leads
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        for row in leads.listing(days=2, source="display_ads")["leads"]:
            if str(row.get("company") or "").strip().lower() != key:
                continue
            if row.get("converted_at"):
                continue        # already a client; start a fresh record
            try:
                when = datetime.fromisoformat(row.get("created", ""))
            except ValueError:
                continue
            if when >= cutoff:
                return row
    except Exception:                                  # noqa: BLE001
        return None
    return None


def _capture_prospect(*, business: str, website: str, campaign: str,
                      contact: str, email: str, phone: str,
                      actor: str) -> str:
    """Write a new prospect into the one lead store, and return its id.

    Through hub.leads rather than a table of its own: that module exists
    precisely because every tool used to keep its own list of who had been in
    touch, and the answer to "where did this account come from" depended on
    which tool you asked. A prospect who reached us by having creative built
    is a lead like any other, so it lands in the same panel, is searchable
    beside the rest, and rides the same delivery to GoHighLevel.

    Never raises. A lead store that is unwritable must not stop the build --
    the person is standing in front of the form, and losing their work to fix
    a filing problem helps nobody. The build then simply has no lead attached,
    which the audit entry shows.
    """
    try:
        from hub import leads
        # A build that fails at the renderer leaves a lead behind and sends the
        # person back to the form -- which is right, a lost lead is worse than
        # an orphan one, but pressing the button twice must not produce two
        # prospects. Reuse a matching one from the last hour instead. Duplicate
        # near-identical records are the "same client three times under
        # slightly different names" problem this Hub already has.
        recent = _recent_prospect(business)
        if recent:
            return str(recent.get("id") or "")
        row = leads.capture(
            source="display_ads",
            page="Display Ad Builder — new prospect",
            fields={"name": contact, "email": email, "phone": phone,
                    "company": business, "website": website,
                    "campaign": campaign, "opened_by": actor},
            client=business,
            meta={"kind": "prospect", "tool": "display_ads"},
        )
        return str(row.get("id") or "")
    except Exception as exc:                           # noqa: BLE001
        logger.warning("display_ads: prospect not recorded as a lead: %s", exc)
        return ""


def start_project(*, client_name: str, campaign: str, website: str = "",
                  promoting: str = "", contact: str = "", email: str = "",
                  phone: str = "", kind: str = "", proposal_id: str = "",
                  actor: str = "") -> dict:
    """Create a build in the renderer, prefilled from what the Hub knows.

    The renderer's intake wants a business, a website, a contact and something
    to promote. For an existing client the Hub already holds the first two, and
    for a new client the proposal usually holds the rest -- so the person
    starting a build types a campaign name rather than re-keying the account.

    ``kind`` says which of the two this is, and it is asked rather than
    guessed. The form used to be one free-text box: typing an existing client
    with a different spelling silently started a build nobody could find on
    that account, and typing a genuinely new name recorded nothing at all --
    the prospect existed only as a string on one ad project. Now a client is
    checked against the registry, and a prospect becomes a lead in the one
    lead store before the build is opened.
    """
    client_name = str(client_name or "").strip()
    campaign = str(campaign or "").strip()
    if not client_name or not campaign:
        return {"ok": False, "error": "A client and a campaign name are required."}

    kind = str(kind or "").strip().lower()
    if kind not in ("client", "prospect"):
        # Older callers (and the JSON API) may not send it. Decide from the
        # registry rather than refusing, but never invent a client: an unknown
        # name is a prospect, which is the safe direction to be wrong in.
        kind = "client" if _known_client(client_name) else "prospect"

    website = str(website or "").strip()
    registry_row = None
    if kind == "client":
        registry_row = _known_client(client_name)
        if registry_row is None:
            return {"ok": False,
                    "error": f"“{client_name}” is not in the client registry. "
                             f"Pick the account from the list, or start it as "
                             f"a new prospect."}
        # Use the registry's spelling, not what was typed. Name drift is what
        # puts one account's creative in another account's gallery.
        client_name = str(registry_row.get("name") or client_name).strip()
        if not website:
            website = str(registry_row.get("domain") or "").strip()

    if website and not website.startswith(("http://", "https://")):
        website = "https://" + website

    lead_id = ""
    if kind == "prospect":
        lead_id = _capture_prospect(
            business=client_name, website=website, campaign=campaign,
            contact=contact, email=email, phone=phone, actor=actor)

    payload = {
        "business": client_name,
        "website": website or "https://smart1marketing.com",
        "contact": str(contact or actor or "Smart 1 Marketing").strip(),
        "email": str(email or os.environ.get("ADBUILDER_DEFAULT_EMAIL")
                     or "creative@smart1marketing.com").strip(),
        "campaignName": campaign,
        "promoting": str(promoting or campaign).strip(),
        # Submitted by signed-in staff, not the public form: the honeypot is
        # empty and the elapsed time is deliberately above the bot threshold so
        # a server-side submission is not read as a script.
        "honeypot": "",
        "elapsedMs": 5000,
    }
    ok, data = _api("POST", "/api/requests", payload, timeout=(10, 120))
    if not ok:
        return {"ok": False,
                "error": str(data.get("error")
                             or "The ad builder refused that request.")}

    request_id = str(data.get("requestId") or "")
    try:
        from hub import audit
        audit.log("display_ads", "build_started", actor=actor,
                  client=client_name, campaign=campaign, request=request_id,
                  proposal=proposal_id or None, kind=kind,
                  lead=lead_id or None)
    except Exception:                                  # noqa: BLE001
        pass
    return {"ok": True, "request_id": request_id, "client": client_name,
            "kind": kind, "lead_id": lead_id}


# --------------------------------------------------------------------- routes

def register(app, url_prefix: str = "/tools/display-ads") -> None:
    """Hub-side routes, on the same prefix as the proxy.

    Werkzeug prefers a static rule over the proxy's ``/<path:path>`` catch-all,
    so these are reached rather than forwarded. ``_hub`` is a segment the
    renderer does not use and will not start using, which keeps that
    distinction obvious to the next person reading either file.
    """
    bp = Blueprint("display_ads_link", __name__,
                   url_prefix=f"{url_prefix}/_hub")

    def _user():
        from hub import current_user
        return current_user()

    @bp.before_request
    def _guard():
        if not _user():
            return redirect(f"/login?next={request.path}")
        return None

    @bp.route("/start", methods=["GET"])
    def start_form():
        """Pick a client -- or a prospect -- and open a build for them."""
        client = str(request.args.get("client") or "").strip()
        proposals = []
        if client:
            try:
                from hub import proposals as proposals_mod
                proposals = proposals_mod.list_proposals(client)
            except Exception:                          # noqa: BLE001
                proposals = []
        return render_template("ad_builder_start.html", client=client,
                               kind="client" if client else "",
                               form={}, proposals=proposals,
                               url_prefix=url_prefix, error="")

    @bp.route("/start", methods=["POST"])
    def start_submit():
        body = request.form if request.form else (request.get_json(silent=True) or {})
        kind = str(body.get("kind") or "").strip().lower()
        # Each half of the form has its own name box, so switching between them
        # cannot carry a half-typed client name into a prospect.
        name = (body.get("prospect_name") if kind == "prospect"
                else body.get("client")) or body.get("client", "")
        res = start_project(
            client_name=name,
            campaign=body.get("campaign", ""),
            website=body.get("website", ""),
            promoting=body.get("promoting", ""),
            contact=body.get("contact", ""),
            email=body.get("email", ""),
            phone=body.get("phone", ""),
            kind=kind,
            proposal_id=body.get("proposal", ""),
            actor=_user() or "",
        )
        if request.form:
            if res.get("ok"):
                return redirect(f"{url_prefix}/build?request={res['request_id']}")
            # Hand back what they typed, so a rejected name is corrected
            # rather than re-keyed.
            return render_template("ad_builder_start.html",
                                   client=body.get("client", ""),
                                   kind=kind, form=body, proposals=[],
                                   url_prefix=url_prefix,
                                   error=res.get("error", "")), 400
        return jsonify(res), (200 if res.get("ok") else 400)

    @bp.route("/attach", methods=["GET"])
    def attach_form():
        project_id = str(request.args.get("project") or "").strip()
        project = _project(project_id) if project_id else None
        if project is None:
            return render_template(
                "ad_builder_attach.html", project=None, ads=[],
                url_prefix=url_prefix,
                error="That build could not be found. Open it from the "
                      "builder and use Attach to client from there."), 404
        return render_template("ad_builder_attach.html", project=project,
                               ads=finished_ads(project),
                               url_prefix=url_prefix, error="")

    @bp.route("/attach", methods=["POST"])
    def attach_submit():
        body = request.form if request.form else (request.get_json(silent=True) or {})
        project = _project(str(body.get("project") or "").strip())
        if project is None:
            return jsonify({"ok": False,
                            "error": "That build could not be found."}), 404
        sizes = (request.form.getlist("sizes") if request.form
                 else (body.get("sizes") or []))
        res = attach_ads(project=project,
                         client_name=body.get("client") or project.get("client", ""),
                         sizes=sizes, actor=_user() or "")
        return jsonify(res), (200 if res.get("ok") else 400)

    @bp.route("/clients", methods=["GET"])
    def client_search():
        """Type-ahead for the client box, from the one client registry."""
        try:
            from hub import clients_registry
            rows = clients_registry.search_clients(request.args.get("q", ""),
                                                   limit=10)
        except Exception:                              # noqa: BLE001
            rows = []
        return jsonify({"clients": [{"name": r.get("name", ""),
                                     "domain": r.get("domain", "")}
                                    for r in rows]})

    app.register_blueprint(bp)
