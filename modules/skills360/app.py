"""360 Skills -- switch a client's skills on, and the routes those skills add
to Client 360.

Three blueprints, one module, the modules/weather_setup shape:

  * ``bp`` at ``/tools/360-skills`` -- the staff tool: pick a client, enter
    what a skill needs, verify it against the live source, switch it on.
    Gated by hub/blueprint_guard.
  * ``bp_client`` with no prefix -- the ``/api/client/skills/...`` routes
    Client 360's gated cards read and write. Under ``/api/client/`` because
    that is the prefix hub/suite_embed.EMBEDDABLE allowlists (the reason
    ``/api/client/health`` gives). Gated the same way.
  * ``bp_hot`` at ``/hot`` -- the client's own hotsheet link, no login, no
    Hub chrome (an entry in hub/__init__.CHROMELESS), reached by an
    unguessable token that dies with the skill.

A blueprint on the hub app gets no blanket exception handler, so every
route below answers JSON itself on every path.
"""
from __future__ import annotations

import re

from flask import Blueprint, jsonify, render_template, request

from . import ecwid, registry, store, suite_email

MOUNT = "/tools/360-skills"
HOT_MOUNT = "/hot"

bp = Blueprint("skills360", __name__, template_folder="templates")
bp_client = Blueprint("skills360_client", __name__, template_folder="templates")
bp_hot = Blueprint("skills360_hot", __name__, template_folder="templates")

try:
    from hub.blueprint_guard import install as _install_guard
    _install_guard(bp, mount=MOUNT)
    _install_guard(bp_client, mount="")
except Exception:                                          # noqa: BLE001
    pass                                                   # standalone, no Hub

try:
    from hub import audit as hub_audit
except Exception:                                          # noqa: BLE001
    hub_audit = None

_VIEW_LIMIT = 240
_WRITE_LIMIT = 90
_SEND_LIMIT = 30
_WINDOW = 3600
_TOKEN_RE = re.compile(r"[^0-9A-Za-z_-]+")


def _limited(bucket: str, limit: int) -> bool:
    try:
        from hub import leads
        return leads.rate_limited(f"skills360_{bucket}", request, limit, _WINDOW)
    except Exception:                                      # noqa: BLE001
        return False


def _too_many():
    return jsonify({"ok": False, "error": "Too many requests. Try again in a few minutes."}), 429


def _actor() -> str:
    for key in ("X-Hub-User", "X-Hub-Actor"):
        val = (request.headers.get(key) or "").strip()
        if val:
            return val[:60]
    try:
        from hub import auth
        user = auth.verify_cookie_value(request.cookies.get(auth.COOKIE_NAME))
        if user:
            return str(user)[:60]
    except Exception:                                      # noqa: BLE001
        pass
    try:
        return str(request.environ.get("smart1.user") or request.environ.get("s1hub.user") or "")[:60]
    except Exception:                                      # noqa: BLE001
        return ""


def _log(event: str, **extra) -> None:
    if hub_audit is None:
        return
    try:
        hub_audit.log("skills360", event, actor=_actor(), **{k: v for k, v in extra.items() if v})
    except Exception:                                      # noqa: BLE001
        pass


def _version() -> str:
    try:
        from hub import version
        return version.label()
    except Exception:                                      # noqa: BLE001
        return ""


def _client_arg() -> str:
    body = request.get_json(silent=True) or {}
    return str(body.get("client") or request.args.get("client") or request.args.get("name") or "").strip()[:200]


def _client_url(client: str) -> str:
    try:
        from hub import clients_registry
        row = clients_registry.find_client(client)
        return (row or {}).get("url") or (row or {}).get("domain") or ""
    except Exception:                                      # noqa: BLE001
        return ""


def _skill_or_404(key: str):
    if not registry.known(key):
        return jsonify({"ok": False, "error": "No such skill."}), 404
    return None


# ==========================================================================
# Staff tool
# ==========================================================================

@bp.route("/")
def index():
    return render_template("skills360.html", version=_version(), skills=registry.public(),
                           client=request.args.get("client", "")[:200])


@bp.route("/health")
def health():
    return jsonify({"ok": True, "version": _version(), "skills": [s["key"] for s in registry.SKILLS],
                    "clients_with_skills": len(store.all_active())})


@bp.route("/api/clients")
def api_clients():
    """The shared client picker -- modules/utm_builder/app.py's, verbatim."""
    try:
        from hub import clients_registry
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"clients": [], "error": type(exc).__name__})
    try:
        rows = clients_registry.search_clients(request.args.get("q", ""), limit=12)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"clients": [], "error": type(exc).__name__})
    return jsonify({"clients": [{
        "name": r["name"], "slug": r["slug"], "domain": r.get("domain", ""),
        "url": r.get("url", ""), "is_house": r.get("is_house", False),
        "products": r.get("products", [])} for r in rows]})


@bp.route("/api/index")
def api_index():
    try:
        return jsonify({"ok": True, "clients": store.all_active()})
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "clients": [], "error": type(exc).__name__})


@bp.route("/api/status")
def api_status():
    client = _client_arg()
    if not client:
        return jsonify({"ok": False, "error": "Which client?"}), 400
    try:
        rec = store.get(client)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "error": type(exc).__name__}), 200
    base = request.host_url.rstrip("/")
    for s in rec["shares"]:
        s["url"] = f"{base}{HOT_MOUNT}/{s['token']}"
    return jsonify({"ok": True, "client": client, "record": rec, "skills": registry.public()})


@bp.route("/api/<key>/save", methods=["POST"])
def api_save(key):
    """Store what a skill needs, without switching it on."""
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    bad = _skill_or_404(key)
    if bad:
        return bad
    try:
        client = _client_arg()
        if not client:
            return jsonify({"ok": False, "error": "Which client?"}), 400
        body = request.get_json(silent=True) or {}
        spec = registry.BY_KEY[key]
        fields = {f["name"]: str(body.get(f["name"]) or "").strip()[:300] for f in spec["fields"]}
        secret_fields = tuple(f["name"] for f in spec["fields"] if f.get("secret"))
        rec = store.set_skill(client, key, fields, by=_actor(), url=_client_url(client),
                              secret_fields=secret_fields)
        if key == "ecwid":
            ecwid.forget(fields.get("store_id") or "")
        _log(f"{key}_saved", client=client)
        return jsonify({"ok": True, "skill": rec})
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "error": f"Could not save ({type(exc).__name__})."}), 500


def _verify(client: str, key: str) -> dict:
    """Prove the skill works with what is stored. The activation gate."""
    rec = store.skill(client, key)
    if key == "ecwid":
        return ecwid.verify(rec.get("store_id") or "", store.secret(client, "ecwid", "token"))
    if key == "email":
        r = suite_email.readiness(client, _client_url(client), from_email=rec.get("from_email") or "",
                                  from_name=rec.get("from_name") or "")
        r["ok"] = bool(r.get("ready"))
        r["error"] = "" if r["ok"] else (r.get("detail") or "Not ready to send.")
        return r
    return {"ok": False, "error": "This skill has no verifier."}


@bp.route("/api/<key>/verify", methods=["POST"])
def api_verify(key):
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    bad = _skill_or_404(key)
    if bad:
        return bad
    try:
        client = _client_arg()
        if not client:
            return jsonify({"ok": False, "error": "Which client?"}), 400
        out = _verify(client, key)
        _log(f"{key}_verified" if out.get("ok") else f"{key}_verify_failed", client=client)
        return jsonify(out)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "error": f"Could not verify ({type(exc).__name__})."}), 500


@bp.route("/api/<key>/activate", methods=["POST"])
def api_activate(key):
    """Verify, then switch on. Never the other way round: the card on
    Client 360 appears only for a skill that has been shown to work."""
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    bad = _skill_or_404(key)
    if bad:
        return bad
    try:
        client = _client_arg()
        if not client:
            return jsonify({"ok": False, "error": "Which client?"}), 400
        out = _verify(client, key)
        if not out.get("ok"):
            _log(f"{key}_activate_refused", client=client)
            return jsonify({"ok": False, "error": out.get("error") or "Verification failed.", "verify": out}), 200
        verified = {}
        if key == "ecwid":
            verified = {k: out.get(k) for k in ("store_name", "store_url", "currency", "order_count")}
        elif key == "email":
            verified = {"location_id": (out.get("account") or {}).get("location_id") or "",
                        "check": {k: out.get(k) for k in ("checked_at", "ready", "from", "domain", "builder", "scopes", "detail")}}
        rec = store.activate(client, key, by=_actor(), url=_client_url(client), verified=verified)
        _log(f"{key}_activated", client=client)
        return jsonify({"ok": True, "skill": rec, "verify": out})
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "error": f"Could not activate ({type(exc).__name__})."}), 500


@bp.route("/api/<key>/deactivate", methods=["POST"])
def api_deactivate(key):
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    bad = _skill_or_404(key)
    if bad:
        return bad
    try:
        client = _client_arg()
        if not client:
            return jsonify({"ok": False, "error": "Which client?"}), 400
        body = request.get_json(silent=True) or {}
        rec = store.deactivate(client, key, by=_actor(), forget=bool(body.get("forget")))
        _log(f"{key}_deactivated", client=client, forgot=bool(body.get("forget")))
        return jsonify({"ok": True, "skill": rec})
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "error": f"Could not deactivate ({type(exc).__name__})."}), 500


@bp.route("/api/share", methods=["POST"])
def api_share():
    """A client-facing link for a shareable, active skill."""
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    try:
        client = _client_arg()
        body = request.get_json(silent=True) or {}
        key = str(body.get("skill") or "").strip()
        if not client or not registry.known(key):
            return jsonify({"ok": False, "error": "Which client and skill?"}), 400
        if not registry.BY_KEY[key].get("shareable"):
            return jsonify({"ok": False, "error": "That skill has no client-facing page."}), 400
        if not store.skill(client, key).get("active"):
            return jsonify({"ok": False, "error": "Switch the skill on first -- a link to an inactive skill is a dead link."}), 400
        out = store.add_share(client, key, by=_actor(), label=str(body.get("label") or ""))
        if out.get("ok"):
            out["url"] = f"{request.host_url.rstrip('/')}{HOT_MOUNT}/{out['token']}"
            _log("share_created", client=client, skill=key)
        return jsonify(out)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "error": f"Could not make a link ({type(exc).__name__})."}), 500


@bp.route("/api/share/revoke", methods=["POST"])
def api_share_revoke():
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    try:
        client = _client_arg()
        body = request.get_json(silent=True) or {}
        token = _TOKEN_RE.sub("", str(body.get("token") or ""))[:80]
        if not client or not token:
            return jsonify({"ok": False, "error": "Which link?"}), 400
        out = store.revoke_share(client, token, by=_actor())
        if out.get("ok"):
            _log("share_revoked", client=client)
        return jsonify(out)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "error": f"Could not revoke ({type(exc).__name__})."}), 500


@bp.route("/api/email/set-business-email", methods=["POST"])
def api_set_business_email():
    """Write the from-address onto the Suite sub-account itself."""
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    try:
        client = _client_arg()
        body = request.get_json(silent=True) or {}
        email = str(body.get("email") or "").strip()[:200]
        if not client or not email:
            return jsonify({"ok": False, "error": "Which client and address?"}), 400
        out = suite_email.set_business_email(client, email, _client_url(client))
        _log("suite_business_email_set" if out.get("ok") else "suite_business_email_refused", client=client)
        return jsonify(out)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "error": f"Could not update ({type(exc).__name__})."}), 500


# ==========================================================================
# Client 360's routes -- read by the gated cards
# ==========================================================================

def skills_for(client: str) -> list[str]:
    """What ``/api/c360`` decorates each group with. Never raises."""
    try:
        return store.active_keys(client)
    except Exception:                                      # noqa: BLE001
        return []


@bp_client.route("/api/client/skills")
def api_client_skills():
    client = _client_arg()
    if not client:
        return jsonify({"client": "", "active": [], "skills": {}})
    try:
        rec = store.get(client)
        return jsonify({"client": client, "active": store.active_keys(client), "skills": rec["skills"],
                        "shares": rec["shares"], "catalog": registry.public()})
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"client": client, "active": [], "skills": {}, "error": type(exc).__name__})


@bp_client.route("/api/client/skills/ecwid/dashboard")
def api_ecwid_dashboard():
    """The hotsheet for Client 360. Three empties kept apart: skill off,
    store answered nothing, store could not be read."""
    if _limited("view", _VIEW_LIMIT):
        return _too_many()
    client = _client_arg()
    try:
        rec = store.skill(client, "ecwid")
        if not rec.get("active"):
            return jsonify({"ok": False, "state": "off", "error": "The Ecommerce skill is not switched on for this client."})
        token = store.secret(client, "ecwid", "token")
        if not token:
            return jsonify({"ok": False, "state": "unreadable",
                            "error": "The stored Ecwid token cannot be read (encryption key changed?). Re-enter it in 360 Skills."})
        out = ecwid.dashboard(rec.get("store_id") or "", token, fresh=request.args.get("fresh") == "1")
        out["state"] = "ok" if out.get("ok") else "unreadable"
        out["store"] = {k: rec.get(k) for k in ("store_id", "store_name", "store_url", "currency")}
        return jsonify(out)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "state": "unreadable", "error": f"Could not read the store ({type(exc).__name__})."})


def _email_ctx(client: str) -> dict:
    rec = store.skill(client, "email")
    return {"rec": rec, "from_email": rec.get("from_email") or "", "from_name": rec.get("from_name") or ""}


@bp_client.route("/api/client/skills/email/readiness")
def api_email_readiness():
    if _limited("view", _VIEW_LIMIT):
        return _too_many()
    client = _client_arg()
    try:
        rec = store.skill(client, "email")
        if not rec.get("active"):
            return jsonify({"ok": False, "state": "off", "error": "The Email Creator skill is not switched on for this client."})
        r = suite_email.readiness(client, _client_url(client), from_email=rec.get("from_email") or "",
                                  from_name=rec.get("from_name") or "")
        r["ok"] = True
        r["state"] = "ready" if r.get("ready") else "blocked"
        r["settings_url"] = suite_email.suite_settings_url((r.get("account") or {}).get("location_id") or "")
        r["tool_url"] = f"{MOUNT}/?client={client}"
        return jsonify(r)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "state": "unreadable", "error": f"Could not check ({type(exc).__name__})."})


def _compose(client: str, body: dict) -> dict:
    """The message as the rep wrote it, rendered into the client's brand."""
    brand = {}
    try:
        from hub import brand_template
        brand = brand_template.get(client)
    except Exception:                                      # noqa: BLE001
        brand = {}
    fields = {k: str(body.get(k) or "").strip() for k in
              ("subject", "preview", "headline", "body", "cta_text", "cta_url", "hero_url", "footer")}
    if fields["cta_url"] and not re.match(r"^https?://", fields["cta_url"], re.I):
        fields["cta_url"] = "https://" + fields["cta_url"]
    if fields["hero_url"] and not re.match(r"^https?://", fields["hero_url"], re.I):
        fields["hero_url"] = ""
    html = suite_email.render_html(subject=fields["subject"] or "(no subject)", headline=fields["headline"],
                                   body=fields["body"], cta_text=fields["cta_text"], cta_url=fields["cta_url"],
                                   hero_url=fields["hero_url"], brand=brand, business=client,
                                   footer=fields["footer"], preview=fields["preview"])
    return {"fields": fields, "html": html, "brand": {"picked": bool(brand.get("picked")), "logo": bool(brand.get("logo_url"))}}


@bp_client.route("/api/client/skills/email/preview", methods=["POST"])
def api_email_preview():
    if _limited("view", _VIEW_LIMIT):
        return _too_many()
    client = _client_arg()
    try:
        body = request.get_json(silent=True) or {}
        c = _compose(client, body)
        return jsonify({"ok": True, "html": c["html"], "brand": c["brand"]})
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "error": f"Could not render ({type(exc).__name__})."})


@bp_client.route("/api/client/skills/email/draft", methods=["POST"])
def api_email_draft():
    """AI-assisted draft. Explicit action, labelled, with the plain form as
    the non-AI path -- hub/ai's rule. The client brief rides in through
    hub.ai's own wrapper, never a second prompt."""
    if _limited("send", _SEND_LIMIT):
        return _too_many()
    client = _client_arg()
    try:
        body = request.get_json(silent=True) or {}
        brief = str(body.get("brief") or "").strip()[:1500]
        if not brief:
            return jsonify({"ok": False, "error": "Say what the email is about first."}), 400
        from hub import ai
        if not ai.ready():
            return jsonify({"ok": False, "error": "AI drafting is not configured on this Hub. Write the email by hand below."})
        out = ai.chat_json(
            [{"role": "system", "content": (
                "You write marketing emails for a small business's customers. Return JSON with keys "
                "subject (under 60 chars), preview (under 90 chars), headline (under 50 chars), body "
                "(2-4 short paragraphs of plain text separated by blank lines, warm, specific, no "
                "placeholders except {{contact.first_name}}), cta_text (2-4 words). No markdown.")},
             {"role": "user", "content": f"Business: {client}\nWhat this email is about: {brief}"}],
            module="skills360", purpose="email_draft", client=client, domain=_client_url(client))
        keep = {k: str(out.get(k) or "").strip() for k in ("subject", "preview", "headline", "body", "cta_text")}
        _log("email_drafted", client=client)
        return jsonify({"ok": True, "draft": keep})
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "error": f"Drafting failed ({type(exc).__name__}). Write it by hand below."})


def _send_guard(client: str):
    rec = store.skill(client, "email")
    if not rec.get("active"):
        return None, jsonify({"ok": False, "error": "The Email Creator skill is not switched on for this client."})
    return rec, None


@bp_client.route("/api/client/skills/email/template", methods=["POST"])
def api_email_template():
    if _limited("send", _SEND_LIMIT):
        return _too_many()
    client = _client_arg()
    try:
        rec, refused = _send_guard(client)
        if refused:
            return refused
        body = request.get_json(silent=True) or {}
        c = _compose(client, body)
        title = str(body.get("title") or c["fields"]["subject"] or "Hub email").strip()
        out = suite_email.push_template(client, title=title, html=c["html"], url=_client_url(client), by=_actor())
        _log("email_template_pushed" if out.get("ok") else "email_template_refused", client=client)
        return jsonify(out)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "detail": f"Could not save the template ({type(exc).__name__})."})


def _from(client: str, rec: dict) -> tuple[str, str, str]:
    """(from_email, from_name, why-not) -- the skill's, else the Suite account's."""
    email, name = rec.get("from_email") or "", rec.get("from_name") or ""
    if email:
        return email, name or client, ""
    r = suite_email.readiness(client, _client_url(client))
    fr = r.get("from") or {}
    if fr.get("email"):
        return fr["email"], name or fr.get("name") or client, ""
    return "", "", fr.get("detail") or r.get("detail") or "No from-address."


@bp_client.route("/api/client/skills/email/test", methods=["POST"])
def api_email_test():
    if _limited("send", _SEND_LIMIT):
        return _too_many()
    client = _client_arg()
    try:
        rec, refused = _send_guard(client)
        if refused:
            return refused
        body = request.get_json(silent=True) or {}
        to = str(body.get("to") or "").strip()[:200]
        c = _compose(client, body)
        from_email, from_name, why = _from(client, rec)
        if not from_email:
            return jsonify({"ok": False, "detail": why})
        out = suite_email.send_test(client, to=to, subject=c["fields"]["subject"] or "(no subject)", html=c["html"],
                                    from_email=from_email, from_name=from_name, url=_client_url(client))
        _log("email_test_sent" if out.get("ok") else "email_test_refused", client=client)
        return jsonify(out)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "detail": f"Could not send the test ({type(exc).__name__})."})


@bp_client.route("/api/client/skills/email/contacts")
def api_email_contacts():
    if _limited("view", _VIEW_LIMIT):
        return _too_many()
    client = _client_arg()
    try:
        rec, refused = _send_guard(client)
        if refused:
            return refused
        out = suite_email.search_contacts(client, q=str(request.args.get("q") or "")[:100],
                                          tag=str(request.args.get("tag") or "")[:80], url=_client_url(client),
                                          limit=int(request.args.get("limit") or 50))
        return jsonify(out)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "detail": f"Could not search contacts ({type(exc).__name__}).", "contacts": []})


@bp_client.route("/api/client/skills/email/send", methods=["POST"])
def api_email_send():
    """To chosen Suite contacts. The browser confirms the count first; this
    end re-checks that the skill is on and a from-address exists, and never
    sends to an address that is not a contact on the client's sub-account."""
    if _limited("send", _SEND_LIMIT):
        return _too_many()
    client = _client_arg()
    try:
        rec, refused = _send_guard(client)
        if refused:
            return refused
        body = request.get_json(silent=True) or {}
        ids = [str(i) for i in (body.get("contact_ids") or []) if i]
        if not ids:
            return jsonify({"ok": False, "detail": "Choose at least one contact."})
        if not body.get("confirmed"):
            return jsonify({"ok": False, "detail": "Confirm the send first."})
        c = _compose(client, body)
        from_email, from_name, why = _from(client, rec)
        if not from_email:
            return jsonify({"ok": False, "detail": why})
        out = suite_email.send_batch(client, contact_ids=ids, subject=c["fields"]["subject"] or "(no subject)",
                                     html=c["html"], from_email=from_email, from_name=from_name, url=_client_url(client))
        _log("email_batch_sent" if out.get("ok") else "email_batch_refused", client=client,
             sent=out.get("sent"), failed=out.get("failed"))
        return jsonify(out)
    except Exception as exc:                               # noqa: BLE001
        return jsonify({"ok": False, "detail": f"Could not send ({type(exc).__name__})."})


# ==========================================================================
# The client's own link
# ==========================================================================

@bp_hot.route("/<token>")
def hot_page(token):
    found = store.resolve_share(token)
    if not found:
        return render_template("hotsheet_public.html", found=None, token=""), 404
    spec = registry.BY_KEY.get(found["skill"]) or {}
    return render_template("hotsheet_public.html", found=found, token=_TOKEN_RE.sub("", token)[:80],
                           skill=spec, client=found["client"])


@bp_hot.route("/<token>/data")
def hot_data(token):
    if _limited("hot", _VIEW_LIMIT):
        return _too_many()
    try:
        found = store.resolve_share(token)
        if not found:
            return jsonify({"ok": False, "error": "This link is no longer active."}), 404
        client = found["client"]
        if found["skill"] == "ecwid":
            rec = store.skill(client, "ecwid")
            secret = store.secret(client, "ecwid", "token")
            if not secret:
                return jsonify({"ok": False, "error": "This dashboard is not available right now."})
            out = ecwid.dashboard(rec.get("store_id") or "", secret)
            # A client page: the store's own id and any error wording stay off it.
            out.pop("store", None)
            if not out.get("ok"):
                out["error"] = "The store did not answer just now. Try again in a few minutes."
            out["client"] = client
            out["store_name"] = rec.get("store_name") or client
            return jsonify(out)
        return jsonify({"ok": False, "error": "This link has nothing to show."}), 404
    except Exception:                                      # noqa: BLE001
        return jsonify({"ok": False, "error": "This dashboard is not available right now."})
