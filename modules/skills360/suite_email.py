"""Smart 1 Suite email -- the Email Creator skill's send path.

Everything runs on a **sub-account token** minted by
``hub.suite_accounts.token_for(client)``: the location id comes from the
client's own Suite link, never from an env var (hub/ghl_blog.py's
``GHL_BLOG_LOCATION_ID`` is the shape this deliberately avoids -- one client's
email must not be able to go out under another's name).

Three questions this file answers, each tri-state (hub/suite_accounts.py's
"three answers, never two"):

  * **Which sub-account, and will it issue a token?**  ``readiness()``
  * **Can that sub-account send?**  A from-address on file, the email builder
    reachable with the scopes the marketplace app was granted, and the
    from-address's domain carrying the DNS a verified LC Email sending domain
    leaves behind (SPF including mailgun, or a DKIM key at one of LC Email's
    selectors). HighLevel exposes no API for sending domains -- the console
    is the only place one is added -- so the DNS half is *measured from
    outside*, and "not measured" is an answer here, not a failure.
  * **Send.**  A template into the sub-account's Email Builder, a test to a
    staff address, or the same message to chosen contacts, one
    ``/conversations/messages`` call per recipient so a bad address costs
    one line of the result and not the batch.

Nothing here raises to a route. Every function returns a dict with ``ok``
and a ``detail`` a rep can read; no token value ever lands in one.
"""
from __future__ import annotations

import html as _html
import os
import re
from datetime import datetime, timezone

import requests

API_BASE = os.environ.get("GHL_API_BASE", "https://services.leadconnectorhq.com")
API_VERSION = os.environ.get("GHL_API_VERSION", "2021-07-28")
TIMEOUT = 25
MAX_RECIPIENTS = 200           # one call each; keep a mistake bounded
DOH = "https://dns.google/resolve"
DKIM_SELECTORS = ("krs", "mailo", "smtp", "k1", "email", "mail", "lc")
_EMAIL_RE = re.compile(r"^[^@\s]+@([^@\s]+\.[^@\s]+)$")


class SuiteEmailError(RuntimeError):
    """Message is safe to show a rep -- never contains the token."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Version": API_VERSION,
            "Accept": "application/json", "Content-Type": "application/json"}


def _call(token: str, method: str, path: str, *, scope_hint: str = "", **kw) -> dict:
    try:
        r = requests.request(method, f"{API_BASE}{path}", headers=_headers(token),
                             timeout=TIMEOUT, **kw)
    except requests.RequestException as exc:
        raise SuiteEmailError(f"Couldn't reach Smart 1 Suite ({type(exc).__name__}).")
    if r.status_code in (401, 403):
        # Never the body: HighLevel errors have carried token fragments.
        why = f" It needs the {scope_hint} scope, which takes an agency re-consent of the Hub app." if scope_hint else ""
        raise SuiteEmailError(f"Smart 1 Suite rejected the request ({r.status_code}).{why}")
    if r.status_code == 422:
        msg = ""
        try:
            body = r.json()
            m = body.get("message")
            msg = "; ".join(m) if isinstance(m, list) else str(m or "")
        except ValueError:
            pass
        raise SuiteEmailError(f"Smart 1 Suite could not accept that ({msg[:200] or 'HTTP 422'}).")
    if not r.ok:
        raise SuiteEmailError(f"Smart 1 Suite returned HTTP {r.status_code} for {path}.")
    try:
        return r.json() or {}
    except ValueError:
        return {}


# ------------------------------------------------------------------ the account
def account(client: str, url: str = "") -> dict:
    """The client's sub-account and a token for it, or why not."""
    try:
        from hub import suite_accounts
    except Exception as exc:                               # noqa: BLE001
        return {"state": "not_measured", "detail": f"The Suite link could not be read ({type(exc).__name__}).",
                "location_id": "", "token": None}
    try:
        return suite_accounts.token_for(client, url)
    except Exception as exc:                               # noqa: BLE001
        return {"state": "not_measured", "detail": f"The Suite link could not be read ({type(exc).__name__}).",
                "location_id": "", "token": None}


def location(token: str, location_id: str) -> dict:
    data = _call(token, "GET", f"/locations/{location_id}", scope_hint="locations.readonly")
    loc = data.get("location") or data
    biz = loc.get("business") or {}
    return {"id": loc.get("id") or location_id, "name": loc.get("name") or biz.get("name") or "",
            "email": loc.get("email") or "", "website": loc.get("website") or biz.get("website") or "",
            "first_name": loc.get("firstName") or "", "last_name": loc.get("lastName") or ""}


# ------------------------------------------------------------------ DNS
def _txt(name: str) -> list[str] | None:
    """TXT records via DNS-over-HTTPS, or None when DNS could not be asked."""
    try:
        r = requests.get(DOH, params={"name": name, "type": "TXT"}, timeout=8,
                         headers={"Accept": "application/dns-json"})
        if not r.ok:
            return None
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    out = []
    for a in data.get("Answer") or []:
        if a.get("type") == 16:
            out.append(str(a.get("data") or "").replace('" "', "").strip('"'))
    return out


def domain_check(email: str) -> dict:
    """Is this from-address on a domain set up to send?

    ``state``: ``verified`` (SPF names mailgun -- LC Email's carrier -- or a
    DKIM key sits at one of its selectors), ``missing`` (DNS answered and
    neither is there), ``not_measured`` (DNS would not answer), or
    ``no_address``. The records a verified domain needs are listed on a
    ``missing`` answer so the rep can hand them to whoever runs the DNS.
    """
    m = _EMAIL_RE.match(str(email or "").strip().lower())
    if not m:
        return {"state": "no_address", "domain": "", "detail": "No from-address to check."}
    domain = m.group(1)
    spf = _txt(domain)
    if spf is None:
        return {"state": "not_measured", "domain": domain,
                "detail": "DNS could not be read just now, so whether this domain is set up to send is not known."}
    spf_rec = next((t for t in spf if t.lower().startswith("v=spf1")), "")
    spf_ok = bool(spf_rec) and ("mailgun" in spf_rec.lower() or "leadconnector" in spf_rec.lower()
                                or "msgsndr" in spf_rec.lower())
    dkim_sel = ""
    for sel in DKIM_SELECTORS:
        recs = _txt(f"{sel}._domainkey.{domain}")
        if recs and any("v=dkim1" in t.lower() or "p=" in t.lower() for t in recs):
            dkim_sel = sel
            break
    if spf_ok or dkim_sel:
        parts = [x for x in (("an SPF record naming the Suite's carrier" if spf_ok else ""),
                             (f"a DKIM key at {dkim_sel}._domainkey" if dkim_sel else "")) if x]
        return {"state": "verified", "domain": domain, "spf": bool(spf_ok), "dkim": dkim_sel,
                "detail": f"{domain} carries " + " and ".join(parts) + "."}
    return {"state": "missing", "domain": domain, "spf": bool(spf_ok), "dkim": "",
            "spf_record": spf_rec,
            "detail": (f"{domain} has an SPF record, but it does not name the Suite's mail carrier, "
                       if spf_rec else f"{domain} has no SPF record, ")
                      + "and no DKIM key was found at the usual selectors. Mail will still go out from "
                        "the Suite's shared sending domain, but a dedicated domain is what keeps it "
                        "out of spam. Add it under Settings › Email Services in the client's Suite "
                        "account and publish the DNS records it shows.",
            "records_needed": [
                {"type": "TXT", "host": domain, "value": "v=spf1 include:mailgun.org ~all",
                 "note": "Add `include:mailgun.org` to the existing SPF if there is one -- a domain may have only one."},
                {"type": "TXT", "host": f"<selector>._domainkey.{domain}", "value": "(the DKIM key the Suite shows)",
                 "note": "Copy the exact host and value from Settings › Email Services after adding the domain."},
                {"type": "CNAME", "host": f"email.{domain}", "value": "mailgun.org",
                 "note": "Open/click tracking. Optional but recommended."},
            ]}


# ------------------------------------------------------------------ readiness
def readiness(client: str, url: str = "", *, from_email: str = "", from_name: str = "") -> dict:
    """Can this client's Suite account send email, in three measured parts.

    ``ready`` is True only when the account issues a token, a from-address
    exists and the email builder answers. The domain state is reported
    beside that and does not block -- LC Email sends from its shared domain
    without one -- but a ``missing`` domain is the thing to fix before a
    campaign, and the card says so.
    """
    out = {"checked_at": _now(), "ready": False, "account": {}, "from": {}, "builder": {},
           "domain": {}, "scopes": {}, "detail": ""}
    acct = account(client, url)
    out["account"] = {"state": acct.get("state"), "location_id": acct.get("location_id") or "",
                      "detail": acct.get("detail") or "", "matched_name": acct.get("matched_name") or ""}
    token = acct.get("token")
    if not token:
        # The skill's own from-address is still a fact worth showing; only
        # the Suite-side half is unknown.
        out["from"] = {"email": from_email or "", "name": from_name or "", "source": "skill" if from_email else "",
                       "state": "set" if from_email else "missing", "detail": ""}
        out["domain"] = domain_check(from_email) if from_email else {"state": "no_address", "domain": "", "detail": "No from-address yet."}
        out["detail"] = acct.get("detail") or "This client has no linked Smart 1 Suite sub-account."
        return out
    loc_id = acct["location_id"]
    try:
        loc = location(token, loc_id)
    except SuiteEmailError as exc:
        loc = {"id": loc_id, "name": "", "email": "", "website": ""}
        out["account"]["detail"] = str(exc)
    out["account"]["name"] = loc.get("name") or ""
    out["account"]["website"] = loc.get("website") or ""

    addr = (from_email or loc.get("email") or "").strip()
    name = (from_name or loc.get("name") or "").strip()
    out["from"] = {"email": addr, "name": name,
                   "source": "skill" if from_email else ("suite" if loc.get("email") else ""),
                   "state": "set" if addr else "missing",
                   "detail": "" if addr else "Neither the skill nor the Suite account has a from-address. "
                                             "Set one below, or add the business email on the Suite account."}

    try:
        data = _call(token, "GET", "/emails/builder", params={"locationId": loc_id, "limit": 1},
                     scope_hint="emails/builder.readonly")
        out["builder"] = {"state": "ok", "templates": int(data.get("total") or len(data.get("data") or []) or 0),
                          "detail": "The email builder answers for this sub-account."}
    except SuiteEmailError as exc:
        out["builder"] = {"state": "blocked", "detail": str(exc)}

    try:
        from hub import ghl_oauth, ghl_scopes
        st = ghl_oauth.status()
        cmp = st.get("scopes") or ghl_scopes.compare(st.get("scope", ""))
        granted = set(cmp.get("granted") or [])
        need = NEEDED_SCOPES
        out["scopes"] = {"known": bool(cmp.get("known")),
                         "missing": [s for s in need if s not in granted] if cmp.get("known") else list(need),
                         "detail": ("" if cmp.get("known") else "The consented scope list could not be read.")}
    except Exception as exc:                               # noqa: BLE001
        out["scopes"] = {"known": False, "missing": list(NEEDED_SCOPES),
                         "detail": f"The app's scopes could not be read ({type(exc).__name__})."}

    out["domain"] = domain_check(addr) if addr else {"state": "no_address", "domain": "", "detail": "No from-address yet."}

    out["ready"] = bool(addr) and out["builder"].get("state") == "ok"
    if out["ready"]:
        out["detail"] = ("Ready to send from " + addr
                         + ("." if out["domain"].get("state") == "verified"
                            else " -- on the Suite's shared sending domain until the client's own domain is verified."))
    elif not addr:
        out["detail"] = out["from"]["detail"]
    else:
        out["detail"] = out["builder"].get("detail") or "The email builder is not reachable."
    return out


NEEDED_SCOPES = ("emails/builder.readonly", "emails/builder.write",
                 "conversations/message.write", "contacts.write", "contacts.readonly")


def suite_settings_url(location_id: str) -> str:
    base = (os.environ.get("SUITE_APP_BASE") or "https://app.gohighlevel.com").rstrip("/")
    return f"{base}/v2/location/{location_id}/settings/email_services" if location_id else ""


# ------------------------------------------------------------------ configure
def set_business_email(client: str, email: str, url: str = "") -> dict:
    """Write the from-address onto the Suite sub-account itself.

    ``PUT /locations/{id}`` is agency-scoped (``locations.write``), which the
    marketplace app deliberately does not ask for (hub/ghl_scopes
    NOT_REQUESTED) -- so this runs on the agency Private Integration Token,
    the way sub-account creation does, and says plainly when that token is
    not there. Only ``email`` is sent: a partial PUT that also carried an
    empty ``name`` has been seen to blank a location's name.
    """
    email = str(email or "").strip()
    if not _EMAIL_RE.match(email):
        return {"ok": False, "detail": "That is not an email address."}
    acct = account(client, url)
    loc_id = acct.get("location_id") or ""
    if not loc_id:
        return {"ok": False, "detail": acct.get("detail") or "No linked sub-account."}
    pit = ""
    for name in ("GHL_PRIVATE_TOKEN", "SMART1SUITE_PRIVATE_TOKEN"):
        v = (os.environ.get(name) or "").strip().strip('"').strip("'")
        if v and v != "pit-...":
            pit = v
            break
    if not pit:
        return {"ok": False, "detail": "The agency Private Integration Token is not set, so the Suite account "
                                       "itself cannot be edited from here. The from-address is saved on the "
                                       "skill and will be used when sending."}
    try:
        _call(pit, "PUT", f"/locations/{loc_id}", json={"email": email}, scope_hint="locations.write")
    except SuiteEmailError as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True, "detail": f"The Suite account's business email is now {email}."}


# ------------------------------------------------------------------ compose
def _p(text: str) -> str:
    paras = [p.strip() for p in re.split(r"\n\s*\n", str(text or "")) if p.strip()]
    return "".join("<p style=\"margin:0 0 16px;font-size:16px;line-height:1.55;color:#1f2937\">"
                   + _html.escape(p).replace("\n", "<br>") + "</p>" for p in paras)


def render_html(*, subject: str, headline: str = "", body: str = "", cta_text: str = "",
                cta_url: str = "", hero_url: str = "", brand: dict | None = None,
                business: str = "", footer: str = "", preview: str = "") -> str:
    """A single-column, table-based email that survives every client.

    Brand comes from hub/brand_template (logo + primary colour) when the
    client has one confirmed; otherwise a neutral navy. Merge fields the
    Suite understands ({{contact.first_name}}) pass through untouched.
    """
    brand = brand or {}
    colors = brand.get("colors") or {}
    primary = colors.get("primary") or "#1e3a5f"
    logo = brand.get("logo_url") or ""
    e = _html.escape
    parts = [
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">',
        f"<title>{e(subject)}</title></head>",
        '<body style="margin:0;padding:0;background:#f3f4f6;font-family:Helvetica,Arial,sans-serif">',
    ]
    if preview:
        parts.append(f'<div style="display:none;max-height:0;overflow:hidden;opacity:0">{e(preview)}</div>')
    parts.append('<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f4f6">'
                 '<tr><td align="center" style="padding:24px 12px">'
                 '<table role="presentation" width="600" cellspacing="0" cellpadding="0" '
                 'style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;overflow:hidden">')
    if logo:
        parts.append(f'<tr><td align="center" style="padding:24px 24px 8px"><img src="{e(logo)}" alt="{e(business)}" '
                     'style="max-height:64px;max-width:240px;height:auto"></td></tr>')
    elif business:
        parts.append(f'<tr><td align="center" style="padding:24px 24px 8px;font-size:18px;font-weight:700;color:{e(primary)}">{e(business)}</td></tr>')
    if hero_url:
        parts.append(f'<tr><td><img src="{e(hero_url)}" alt="" width="600" style="display:block;width:100%;height:auto"></td></tr>')
    if headline:
        parts.append(f'<tr><td style="padding:24px 32px 8px;font-size:26px;line-height:1.2;font-weight:700;color:{e(primary)}">{e(headline)}</td></tr>')
    parts.append(f'<tr><td style="padding:8px 32px 8px">{_p(body)}</td></tr>')
    if cta_text and cta_url:
        parts.append('<tr><td align="center" style="padding:8px 32px 28px">'
                     f'<a href="{e(cta_url)}" style="display:inline-block;background:{e(primary)};color:#ffffff;'
                     'text-decoration:none;font-weight:700;font-size:16px;padding:14px 28px;border-radius:8px">'
                     f'{e(cta_text)}</a></td></tr>')
    foot = footer or (f"{business} · " if business else "") + "You are receiving this because you asked to hear from us. {{unsubscribe_link}}"
    parts.append(f'<tr><td style="padding:16px 32px 24px;font-size:12px;line-height:1.5;color:#6b7280;border-top:1px solid #e5e7eb">{foot if "{{" in foot else e(foot)}</td></tr>')
    parts.append("</table></td></tr></table></body></html>")
    return "".join(parts)


# ------------------------------------------------------------------ sends
def push_template(client: str, *, title: str, html: str, url: str = "", by: str = "") -> dict:
    acct = account(client, url)
    token, loc_id = acct.get("token"), acct.get("location_id") or ""
    if not token:
        return {"ok": False, "detail": acct.get("detail") or "No linked sub-account."}
    try:
        made = _call(token, "POST", "/emails/builder", scope_hint="emails/builder.write",
                     json={"locationId": loc_id, "type": "html", "title": title[:120],
                           "name": title[:120], "updatedBy": (by or "smart1hub")[:60],
                           "builderVersion": "2", "isPlainText": False})
        template_id = str(made.get("redirect") or made.get("id") or made.get("templateId") or "")
        if not template_id:
            return {"ok": False, "detail": "The Suite made a template but did not say which."}
        saved = _call(token, "POST", "/emails/builder/data", scope_hint="emails/builder.write",
                      json={"locationId": loc_id, "templateId": template_id,
                            "updatedBy": (by or "smart1hub")[:60], "dnd": {"elements": [], "attrs": {}, "templateSettings": {}},
                            "html": html, "editorType": "html", "isPlainText": False})
    except SuiteEmailError as exc:
        return {"ok": False, "detail": str(exc)}
    return {"ok": True, "template_id": template_id, "preview_url": saved.get("previewUrl") or "",
            "detail": f"Saved to the Suite's Email Builder as “{title[:60]}”."}


def upsert_contact(token: str, loc_id: str, email: str, *, first_name: str = "",
                   tags: tuple[str, ...] = ()) -> str:
    data = _call(token, "POST", "/contacts/upsert", scope_hint="contacts.write",
                 json={"locationId": loc_id, "email": email,
                       **({"firstName": first_name} if first_name else {}),
                       **({"tags": list(tags)} if tags else {})})
    c = data.get("contact") or data
    return str(c.get("id") or "")


def search_contacts(client: str, *, q: str = "", tag: str = "", url: str = "", limit: int = 50) -> dict:
    acct = account(client, url)
    token, loc_id = acct.get("token"), acct.get("location_id") or ""
    if not token:
        return {"ok": False, "detail": acct.get("detail") or "No linked sub-account.", "contacts": []}
    limit = max(1, min(int(limit or 50), MAX_RECIPIENTS))
    try:
        if tag:
            data = _call(token, "POST", "/contacts/search", scope_hint="contacts.readonly",
                         json={"locationId": loc_id, "pageLimit": limit,
                               **({"query": q} if q else {}),
                               "filters": [{"field": "tags", "operator": "eq", "value": tag}]})
        else:
            data = _call(token, "GET", "/contacts/", scope_hint="contacts.readonly",
                         params={"locationId": loc_id, "limit": limit, **({"query": q} if q else {})})
    except SuiteEmailError as exc:
        return {"ok": False, "detail": str(exc), "contacts": []}
    rows = []
    for c in data.get("contacts") or []:
        if not c.get("email"):
            continue
        rows.append({"id": c.get("id"), "email": c.get("email"),
                     "name": (c.get("contactName") or " ".join(x for x in (c.get("firstName"), c.get("lastName")) if x) or "").strip(),
                     "tags": c.get("tags") or []})
    return {"ok": True, "contacts": rows, "total": int(data.get("total") or len(rows))}


def send_to_contact(token: str, contact_id: str, *, subject: str, html: str,
                    from_email: str, from_name: str = "", to_email: str = "") -> dict:
    payload = {"type": "Email", "contactId": contact_id, "subject": subject[:200], "html": html,
               "emailFrom": f"{from_name} <{from_email}>" if from_name else from_email}
    if to_email:
        payload["emailTo"] = to_email
    data = _call(token, "POST", "/conversations/messages", scope_hint="conversations/message.write", json=payload)
    return {"ok": True, "message_id": data.get("messageId") or "", "email_message_id": data.get("emailMessageId") or "",
            "conversation_id": data.get("conversationId") or ""}


def send_test(client: str, *, to: str, subject: str, html: str, from_email: str,
              from_name: str = "", url: str = "") -> dict:
    """A test to one staff address. The address becomes a contact tagged
    `hub-test` on the client's sub-account -- the API has no other way to
    send -- which is why the tag is there: it is the thing to filter out."""
    to = str(to or "").strip()
    if not _EMAIL_RE.match(to):
        return {"ok": False, "detail": "That test address is not an email address."}
    acct = account(client, url)
    token, loc_id = acct.get("token"), acct.get("location_id") or ""
    if not token:
        return {"ok": False, "detail": acct.get("detail") or "No linked sub-account."}
    try:
        cid = upsert_contact(token, loc_id, to, first_name="Hub test", tags=("hub-test",))
        if not cid:
            return {"ok": False, "detail": "The Suite did not return a contact for the test address."}
        sent = send_to_contact(token, cid, subject="[TEST] " + subject, html=html,
                               from_email=from_email, from_name=from_name, to_email=to)
    except SuiteEmailError as exc:
        return {"ok": False, "detail": str(exc)}
    sent["detail"] = f"Test sent to {to} from {from_email}. Check the inbox and the spam folder both."
    return sent


def send_batch(client: str, *, contact_ids: list[str], subject: str, html: str,
               from_email: str, from_name: str = "", url: str = "") -> dict:
    ids = [str(i).strip() for i in (contact_ids or []) if str(i).strip()][:MAX_RECIPIENTS]
    if not ids:
        return {"ok": False, "detail": "No recipients chosen."}
    acct = account(client, url)
    token = acct.get("token")
    if not token:
        return {"ok": False, "detail": acct.get("detail") or "No linked sub-account."}
    results = []
    for cid in ids:
        try:
            r = send_to_contact(token, cid, subject=subject, html=html, from_email=from_email, from_name=from_name)
            results.append({"contact_id": cid, "ok": True, "message_id": r.get("message_id")})
        except SuiteEmailError as exc:
            results.append({"contact_id": cid, "ok": False, "detail": str(exc)})
            # A rejected scope fails every send the same way; stop after the first.
            if "rejected" in str(exc):
                break
    sent = sum(1 for r in results if r["ok"])
    return {"ok": sent > 0, "sent": sent, "failed": len(results) - sent,
            "skipped": len(ids) - len(results), "results": results,
            "detail": f"{sent} of {len(ids)} sent." + (f" {len(ids) - sent} did not go." if sent < len(ids) else "")}
