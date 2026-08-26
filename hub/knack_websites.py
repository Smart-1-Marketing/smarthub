"""Knack object_153 — the website registry, read and written by field id.

This table is the missing link between a client and their website, and it
carries far more than the Hub was using: the live URL and its domain, the GA
and GTM account ids, the platform, the go-live date, the H&M fee, the client
organisation and the media partner — and, since the domain record was asked
for on Client 360, when the site went live, what status the client is in,
whether we bought the domain, and when it was bought and renews.

Reading it properly fixes several things at once:

  * **Matching.** Sites were being matched to clients by name, which fails on
    "Riverside HVAC" versus "Riverside HVAC LLC". This table has both a client
    name and a domain, so a site can be matched on either and cross-checked
    against the other. `field_3111` (URL domain) is the identifier;
    `field_2924` (Client organization) and `field_3112` (Client) are the two
    ways a record says whose it is.
  * **GA / GTM.** Those account ids were being looked up live through Google,
    which only finds accounts somebody has connected. Knack has them recorded
    regardless, so the client record can show them even when the Google
    account isn't linked.
  * **Platform.** Populates the website record's platform rather than leaving
    it to be set by hand, which is also what drives the "Log in to site" path
    (/wp-admin versus /login).
  * **Billing.** The H&M fee gives Smart 1 Sites a revenue figure to sit
    against platform cost, which is what makes the margin report meaningful.

Field ids are pinned here as constants rather than scattered through the code:
Knack field ids are opaque, and `field_3111` appearing inline three files away
is unmaintainable. If Knack renumbers, this is the one place to change.

## Writing

This module writes now, which it did not before, and the rules are the ones
`hub/knack_api.py` learned on tickets rather than a second set:

* **A connection needs a record id, never a name.** Client organization may be
  a connection on this object; writing display text into one creates nothing
  and clears the link. Every value goes through `knack_api.coerce_field()`
  against the *live* schema, so a connection is resolved to the one record it
  can only mean and anything ambiguous is **refused by name** rather than
  guessed at.
* **Nothing is dropped in silence.** Every write returns `rejected`, and every
  caller shows it. Knack refuses the whole record over one bad value, so a
  value it would refuse is refused here, and the rest of the record still goes.
* **Reads are cached for a minute.** `suggest_for()` is called once per
  unmatched project by the matcher; uncached that was a full paged pull of the
  object per project, which is why matching a thousand sites took minutes.
  A write clears the cache, so the page that just saved reads its own change.

## Registrar

`field_2926` holds the registrar where somebody filled it in. Where nobody
has, the Insites scan of that domain usually knows: WHOIS is one of the things
it reports (`domain_age.registrar`, with the registered and expiry dates
beside it). `registrar_for()` prefers Knack, falls back to the scan, and
**says which one it used** — a registrar we recorded and a registrar WHOIS
observed are different claims, and only the first is something we own.
"""
from __future__ import annotations

import os
import re
import time

OBJECT = os.environ.get("KNACK_WEBSITES_OBJECT", "object_153")

# --- object_153 field map -------------------------------------------------
F_PRODUCTION_URL = "field_2925"   # Client production URL
F_URL_DOMAIN     = "field_3111"   # URL domain
F_GA_ACCOUNT     = "field_2929"   # GA account
F_GTM_ACCOUNT    = "field_2930"   # GTM account
F_ORGANIZATION   = "field_2924"   # Client organization
F_PLATFORM       = "field_2927"   # Platform
F_GO_LIVE        = "field_3028"   # Website go-live date
F_HM_FEE         = "field_3050"   # H&M fee (hosting & maintenance)
F_CLIENT_NAME    = "field_3112"   # Client
F_MEDIA_PARTNER  = "field_3113"   # Media partner
F_REGISTRAR      = "field_2926"   # Registrar
F_LIVE_DATE      = "field_3048"   # Website Live Date
F_CLIENT_STATUS  = "field_3193"   # Client Status
F_DOMAIN_BOUGHT  = "field_2964"   # S1M Purchase Domain for Client?
F_DOMAIN_BOUGHT_ON = "field_3063"  # Domain Purchase Date
F_DOMAIN_RENEWS  = "field_3101"   # Domain Renewal Date
F_DOMAIN_FEE     = "field_3064"   # Domain Name Fee
F_RENEWAL_BILLED = "field_3298"   # Domain Renewal Billing Date

FIELDS = {
    "production_url": F_PRODUCTION_URL, "domain": F_URL_DOMAIN,
    "ga_account": F_GA_ACCOUNT, "gtm_account": F_GTM_ACCOUNT,
    "organization": F_ORGANIZATION, "platform": F_PLATFORM,
    "go_live": F_GO_LIVE, "hm_fee": F_HM_FEE,
    "client_name": F_CLIENT_NAME, "media_partner": F_MEDIA_PARTNER,
    "registrar": F_REGISTRAR, "live_date": F_LIVE_DATE,
    "client_status": F_CLIENT_STATUS, "domain_bought": F_DOMAIN_BOUGHT,
    "domain_bought_on": F_DOMAIN_BOUGHT_ON, "domain_renews": F_DOMAIN_RENEWS,
    "domain_fee": F_DOMAIN_FEE, "renewal_billing_date": F_RENEWAL_BILLED,
}

# The domain record as Client 360 draws it. `label` is what a person sees —
# "S1M Purchase Domain for Client?" is the Knack label and nobody reads it as
# a question about us, so it is asked as "Did we buy the domain?" here. The
# Knack label still travels beside it so the two can be reconciled.
DOMAIN_RECORD = (
    {"key": "live_date", "field": F_LIVE_DATE, "label": "Website live date",
     "knack_label": "Website Live Date", "kind": "date"},
    {"key": "client_status", "field": F_CLIENT_STATUS, "label": "Client status",
     "knack_label": "Client Status", "kind": "choice"},
    {"key": "domain_bought", "field": F_DOMAIN_BOUGHT,
     "label": "Did we buy the domain?",
     "knack_label": "S1M Purchase Domain for Client?", "kind": "choice"},
    {"key": "domain_bought_on", "field": F_DOMAIN_BOUGHT_ON,
     "label": "Domain purchase date", "knack_label": "Domain Purchase Date",
     "kind": "date"},
    {"key": "domain_renews", "field": F_DOMAIN_RENEWS,
     "label": "Domain renewal date", "knack_label": "Domain Renewal Date",
     "kind": "date"},
    {"key": "registrar", "field": F_REGISTRAR, "label": "Registrar",
     "knack_label": "Registrar", "kind": "text"},
)

# What a client attachment writes. Both, because a deployment fills in one or
# the other and a record carrying neither is the orphan this is fixing.
CLIENT_FIELDS = ("organization", "client_name")

EDITABLE = {row["key"]: row["field"] for row in DOMAIN_RECORD}

_CACHE: dict = {"at": 0.0, "rows": []}
_CACHE_SECONDS = 60

# Why the last read came back empty, if it did. "Knack was unreachable" and
# "Knack holds nothing for this" are different answers and every page that
# shows a count has to be able to say which one it is making.
_STATE: dict = {"error": ""}


def configured() -> bool:
    return bool((os.environ.get("KNACK_APP_ID") or "").strip()
                and (os.environ.get("KNACK_API_KEY") or "").strip())


def _text(v) -> str:
    """Knack returns strings, dicts, or lists of connection objects."""
    if v is None:
        return ""
    if isinstance(v, list):
        return ", ".join(_text(x) for x in v if x).strip(", ")
    if isinstance(v, dict):
        for k in ("identifier", "label", "name", "value", "url", "email"):
            if v.get(k):
                return str(v[k]).strip()
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    s = str(v)
    # Knack often returns HTML for link and connection fields.
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _money(v) -> float:
    s = re.sub(r"[^0-9.]", "", _text(v))
    try:
        return round(float(s), 2) if s else 0.0
    except ValueError:
        return 0.0


def _norm_name(s: str) -> str:
    """Drop the suffixes that differ between systems for the same company.

    The shared one in `hub/client_key.py`, not a second copy. The local
    version ran the words together — "ab cd" and "abcd" normalised alike, so
    two different businesses read as one — and dropped a different set of
    suffixes than the billing audit's copy did, which is how two reports came
    to disagree about whether two names were the same company.
    """
    from hub.client_key import normalise_name
    return normalise_name(s)


def _domain(v) -> str:
    from hub.client_context import canonical_domain
    return canonical_domain(_text(v))


def _headers() -> dict:
    return {"X-Knack-Application-Id": (os.environ.get("KNACK_APP_ID") or "").strip(),
            "X-Knack-REST-API-Key": (os.environ.get("KNACK_API_KEY") or "").strip(),
            "Accept": "application/json", "Content-Type": "application/json"}


def forget() -> None:
    """Drop the read cache. Called after a write, so a page reads its own change."""
    _CACHE["at"] = 0.0
    _CACHE["rows"] = []
    # /tools/domains renders a nightly snapshot of this object rather than
    # pulling it per visit, so a write here has to drop that too — otherwise
    # ticking "did we buy the domain?" on Client 360 leaves the renewal
    # calendar showing yesterday's answer until tomorrow, which reads as a
    # save that did not happen.
    try:
        from hub import domain_purchase
        domain_purchase.invalidate()
    except Exception:                                   # noqa: BLE001
        pass


def last_error() -> str:
    """Why the last read was empty, or "" if it was genuinely empty."""
    return _STATE.get("error", "")


def rows(limit: int = 2000, refresh: bool = False) -> list[dict]:
    """Every website record, normalised. Returns [] rather than raising.

    Cached for a minute: the matcher asks for a suggestion per unmatched
    project, and uncached that was one full paged pull of the object each time.
    """
    if not refresh and _CACHE["rows"] and time.time() - _CACHE["at"] < _CACHE_SECONDS:
        return _CACHE["rows"][:limit]
    if not configured():
        _STATE["error"] = ("KNACK_APP_ID / KNACK_API_KEY are not set on this "
                           "deployment, so the website registry was not read.")
        return []
    import requests
    out, page = [], 1
    headers = _headers()
    _STATE["error"] = ""
    while len(out) < limit and page <= 20:
        try:
            r = requests.get(
                f"https://api.knack.com/v1/objects/{OBJECT}/records",
                headers=headers, params={"page": page, "rows_per_page": 100},
                timeout=25)
            if not r.ok:
                _STATE["error"] = f"Knack returned HTTP {r.status_code}."
                break
            data = r.json()
        except Exception as exc:                        # noqa: BLE001
            _STATE["error"] = f"Knack was unreachable ({type(exc).__name__})."
            break
        recs = data.get("records") or []
        if not recs:
            break
        for rec in recs:
            out.append(_row(rec))
        if len(recs) < 100:
            break
        page += 1
    if out:
        _CACHE["rows"], _CACHE["at"] = out, time.time()
    return out[:limit]


def _row(rec: dict) -> dict:
    """One Knack record as the rest of the Hub reads it."""
    url = _text(rec.get(F_PRODUCTION_URL))
    dom = _domain(rec.get(F_URL_DOMAIN)) or _domain(url)
    client = _text(rec.get(F_CLIENT_NAME))
    org = _text(rec.get(F_ORGANIZATION))
    return {
        "id": rec.get("id", ""),
        "client_name": client,
        "organization": org,
        # One name to show and to match on: whichever of the two is filled in.
        # A record carrying neither is an orphan, and `has_client` is what the
        # orphan list is built from rather than a truthiness test per caller.
        "client": client or org,
        "has_client": bool(client or org),
        "production_url": url,
        "domain": dom,
        "ga_account": _text(rec.get(F_GA_ACCOUNT)),
        "gtm_account": _text(rec.get(F_GTM_ACCOUNT)),
        "platform": _text(rec.get(F_PLATFORM)),
        "go_live": _text(rec.get(F_GO_LIVE)),
        "hm_fee": _money(rec.get(F_HM_FEE)),
        "media_partner": _text(rec.get(F_MEDIA_PARTNER)),
        "registrar": _text(rec.get(F_REGISTRAR)),
        "live_date": _text(rec.get(F_LIVE_DATE)),
        "client_status": _text(rec.get(F_CLIENT_STATUS)),
        "domain_bought": _text(rec.get(F_DOMAIN_BOUGHT)),
        "domain_bought_on": _text(rec.get(F_DOMAIN_BOUGHT_ON)),
        "domain_renews": _text(rec.get(F_DOMAIN_RENEWS)),
        "domain_fee": _money(rec.get(F_DOMAIN_FEE)),
        "renewal_billing_date": _text(rec.get(F_RENEWAL_BILLED)),
        # Knack's own reading of the yes/no, kept raw as well as as text: a
        # boolean field comes back True/False and a dropdown comes back "Yes",
        # and the purchase list has to answer the same question either way.
        "domain_bought_raw": rec.get(f"{F_DOMAIN_BOUGHT}_raw",
                                     rec.get(F_DOMAIN_BOUGHT)),
    }


def index() -> dict:
    """Lookup tables keyed on the two things that identify a site."""
    by_domain, by_name = {}, {}
    for r in rows():
        if r["domain"]:
            by_domain.setdefault(r["domain"], r)
        for nm in (r["client_name"], r["organization"]):
            k = _norm_name(nm)
            if k:
                by_name.setdefault(k, r)
    return {"by_domain": by_domain, "by_name": by_name}


def record_for_domain(domain: str) -> dict:
    """The website record for one domain, or {}. Exact domain, never a guess."""
    d = _domain(domain)
    if not d:
        return {}
    return index()["by_domain"].get(d, {})


def client_for_domain(domain: str) -> dict:
    """Whose domain is this, according to object_153?

    The answer the matcher wants: field_3111 identifies the site, and
    field_2924 / field_3112 say whose it is. Returns {} when the registry has
    the domain but nobody's name on it — that is an orphan, not a match.
    """
    r = record_for_domain(domain)
    if not r or not r.get("has_client"):
        return {}
    return {"client": r["client"], "record_id": r["id"], "domain": r["domain"],
            "field": "Client organization" if r["organization"] else "Client",
            "why": "The Knack website registry records this "
                   f"domain against “{r['client']}”."}


def for_client(client: str, domain: str = "") -> dict:
    """The website record for one client, matched on domain then name."""
    idx = index()
    d = _domain(domain) if domain else ""
    if d and d in idx["by_domain"]:
        return idx["by_domain"][d]
    return idx["by_name"].get(_norm_name(client), {})


def orphan_rows() -> list[dict]:
    """Records with a real domain and nobody's name on them."""
    from hub.client_urls import looks_like_a_website
    return [r for r in rows()
            if r["domain"] and not r["has_client"]
            and looks_like_a_website(r["domain"])]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _coerce(key: str, value):
    """(what to write, why it cannot be written) for one logical key.

    Through knack_api's coercion against the live schema rather than a second
    copy of those rules here: a connection is resolved to the one record it
    can only mean, a dropdown value Knack does not publish is refused by name,
    and nothing is written as display text into a link.
    """
    fid = FIELDS.get(key)
    if not fid:
        return None, f"{key}: no field id is mapped"
    try:
        from hub import knack_api
        return knack_api.coerce_field(fid, value, obj=OBJECT, label=key)
    except Exception as exc:                            # noqa: BLE001
        # The schema could not be read. Writing an unvalidated value would
        # risk Knack refusing the whole record, so this refuses the field and
        # says why — a save that silently drops a field is the failure this
        # module is against.
        return None, f"{key}: the Knack schema could not be read ({type(exc).__name__})"


def update_record(record_id: str, values: dict, actor: str = "") -> dict:
    """Write the domain-record fields onto one object_153 record.

    Only the keys in EDITABLE (plus the two client fields, through
    `attach_client`) may be written, so a mistyped key cannot quietly land
    somewhere else on the record. Returns what went and what was refused —
    never a silent partial success.
    """
    record_id = str(record_id or "").strip()
    if not record_id:
        return {"ok": False, "error": "No website record id."}
    if not configured():
        return {"ok": False, "error": "Knack API credentials aren't set."}

    payload, rejected = {}, []
    for key, value in (values or {}).items():
        if key not in EDITABLE:
            rejected.append(f"{key}: not writable on the website record")
            continue
        out, why = _coerce(key, value)
        if why:
            rejected.append(why)
            continue
        # A blank clears the field rather than being ignored: "we recorded the
        # wrong renewal date" has to be undoable without a deploy.
        payload[FIELDS[key]] = "" if out is None else out

    if not payload:
        return {"ok": False, "error": "Nothing to update.", "rejected": rejected}
    return _put(record_id, payload, rejected, actor,
                what=[k for k in (values or {}) if k in EDITABLE])


def attach_client(record_id: str, client: str, actor: str = "") -> dict:
    """Write a client onto a website record — the Knack half of a match.

    Writes both name fields where the schema takes them. A connection that
    cannot be resolved to exactly one record is refused by name and the other
    field still goes, because half of this record naming the client is better
    than none of it and the refusal is on the screen either way.
    """
    record_id = str(record_id or "").strip()
    client = str(client or "").strip()
    if not (record_id and client):
        return {"ok": False, "error": "A record id and a client are required."}
    if not configured():
        return {"ok": False, "error": "Knack API credentials aren't set."}

    payload, rejected = {}, []
    for key in CLIENT_FIELDS:
        out, why = _coerce(key, client)
        if why:
            rejected.append(why)
            continue
        if out is not None:
            payload[FIELDS[key]] = out
    if not payload:
        return {"ok": False,
                "error": "Knack would not take that client name on either "
                         "field.", "rejected": rejected}
    return _put(record_id, payload, rejected, actor, what=list(CLIENT_FIELDS))


def set_analytics_ids(record_id: str, ga: str = "", gtm: str = "",
                      actor: str = "") -> dict:
    """Record a GA or GTM id on a website record.

    Kept apart from `update_record` rather than widening EDITABLE: that set is
    the domain-record panel's write set, and a panel's write set growing a
    field nobody put on the panel is how a form comes to write something
    nobody can see. Same coercion, same `rejected`, same silence-free result.
    """
    record_id = str(record_id or "").strip()
    if not record_id:
        return {"ok": False, "error": "No website record id."}
    if not configured():
        return {"ok": False, "error": "Knack API credentials aren't set."}
    payload, rejected, what = {}, [], []
    for key, value in (("ga_account", ga), ("gtm_account", gtm)):
        if not str(value or "").strip():
            continue
        out, why = _coerce(key, value)
        if why:
            rejected.append(why)
            continue
        payload[FIELDS[key]] = out
        what.append(key)
    if not payload:
        return {"ok": False, "error": "Nothing to update.", "rejected": rejected}
    return _put(record_id, payload, rejected, actor, what=what)


def _put(record_id: str, payload: dict, rejected: list, actor: str,
         what: list) -> dict:
    import requests
    try:
        r = requests.put(
            f"https://api.knack.com/v1/objects/{OBJECT}/records/{record_id}",
            headers=_headers(), json=payload, timeout=25)
    except Exception as exc:                            # noqa: BLE001
        return {"ok": False, "error": f"Knack was unreachable ({type(exc).__name__}).",
                "rejected": rejected}
    if not r.ok:
        return {"ok": False, "error": f"Knack returned HTTP {r.status_code}.",
                "rejected": rejected}
    forget()
    try:
        from hub import audit
        audit.log("hub", "website_record_updated", actor=actor or None,
                  detail=",".join(what)[:200], record=record_id)
    except Exception:                                   # noqa: BLE001
        pass
    out = {"ok": True, "updated": what, "rejected": rejected}
    try:
        body = r.json() or {}
        rec = body.get("record") if isinstance(body.get("record"), dict) else body
        if isinstance(rec, dict) and rec.get("id"):
            out["record"] = _row(rec)
    except Exception:                                   # noqa: BLE001
        pass                       # the write landed; the echo is a nicety
    return out


# ---------------------------------------------------------------------------
# The domain record, as a page draws it
# ---------------------------------------------------------------------------

def domain_record(client: str = "", domain: str = "") -> dict:
    """Everything the Client 360 domain panel needs for one site.

    Carries the live *choices* for the dropdown fields, for the reason the web
    ticket form does: the ids are ours but a dropdown's choices are Knack's,
    and Knack refuses the whole record over one value it does not publish.
    A schema it could not read degrades to a text box rather than a dead panel.
    """
    rec = for_client(client, domain) if (client or domain) else {}
    meta = {}
    try:
        from hub import knack_api
        meta = knack_api.object_meta(OBJECT)
    except Exception:                                   # noqa: BLE001
        meta = {}

    fields = []
    for spec in DOMAIN_RECORD:
        f = meta.get(spec["field"]) or {}
        control, choices = "text", []
        if f:
            try:
                from hub import knack_api as _ka
                control, choices = _ka._control_for(f, OBJECT)   # noqa: SLF001
            except Exception:                           # noqa: BLE001
                control, choices = "text", []
        fields.append({**spec, "value": rec.get(spec["key"], ""),
                       "control": control, "choices": choices,
                       # False means the pinned id is not on this object any
                       # more. The panel still draws the field and says so,
                       # rather than dropping it and looking complete.
                       "known": bool(f) if meta else None,
                       "live_label": f.get("label") or ""})

    reg = registrar_for(domain or rec.get("domain", ""), record=rec)
    return {
        "found": bool(rec),
        "configured": configured(),
        # Empty because Knack said nothing, or empty because we could not ask?
        # A panel that reads "no record for this domain" when the credentials
        # are missing is a confident wrong answer.
        "read_error": last_error() if not rec else "",
        "record_id": rec.get("id", ""),
        "domain": rec.get("domain", "") or _domain(domain),
        "client": rec.get("client", ""),
        "fields": fields,
        "registrar": reg,
        "writable": configured(),
        "schema_read": bool(meta),
        "note": ("Written straight to the website record."
                 if configured() else
                 "Read-only: KNACK_APP_ID / KNACK_API_KEY are not set on this "
                 "deployment, so nothing here can be saved."),
    }


def registrar_for(domain: str, record: dict | None = None) -> dict:
    """Who the domain is registered with, and how we know.

    Knack first — that is a registrar somebody recorded. Where nobody has, the
    Insites scan of the same domain reports WHOIS, which is an observation
    rather than a record of ours; it is returned labelled as such, with the
    registered and expiry dates it comes with, and never written back to Knack
    on its own. A domain with neither says *not recorded*, not "none".
    """
    d = _domain(domain)
    rec = record if record is not None else (record_for_domain(d) if d else {})
    if rec.get("registrar"):
        return {"value": rec["registrar"], "source": "knack",
                "label": "Recorded on the website record."}
    if not d:
        return {"value": "", "source": "", "label": "No domain to look up."}
    try:
        from modules.scans.app import latest_payload_for_domain
        from modules.scans.audit_fields import get_field
        report = latest_payload_for_domain(d) or {}
    except Exception:                                   # noqa: BLE001
        return {"value": "", "source": "",
                "label": "Not recorded in Knack, and the scan store could not "
                         "be read."}
    if not report:
        return {"value": "", "source": "",
                "label": "Not recorded in Knack, and this domain has no "
                         "completed site scan to read WHOIS from."}
    value = str(get_field(report, "domain_age.registrar") or "").strip()
    if not value:
        return {"value": "", "source": "",
                "label": "Not recorded in Knack, and the scan of this domain "
                         "did not report a registrar."}
    return {
        "value": value, "source": "scan",
        "registered": str(get_field(report, "domain_age.registered_date") or ""),
        "expires": str(get_field(report, "domain_age.expiry_date") or ""),
        "label": "From WHOIS on the latest Insites scan of this domain — "
                 "observed, not recorded by us.",
    }


# ---------------------------------------------------------------------------
# Fuzzy matching, for the "is this it?" prompt
# ---------------------------------------------------------------------------

def _similar(a: str, b: str) -> float:
    """0-1 similarity on normalised names, using the stdlib.

    Deliberately conservative: this only ever produces a SUGGESTION a human
    confirms. An automatic fuzzy match writes the wrong client onto a site,
    and a wrong internal_client_name is worse than a blank one because it
    attributes revenue to the wrong account.
    """
    from difflib import SequenceMatcher
    x, y = _norm_name(a), _norm_name(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    # There was a `if x in y or y in x: return 0.92` here, and it was the
    # substring rule `hub/client_key.py` exists to refuse — scored, on top of
    # that, above almost every real resemblance. It cost exactly what that
    # docstring says it costs: a Simvoly project is named "<media partner> -
    # <business>", so every one of FabLocal's thirty-seven SERVPRO franchises
    # contained the string "FabLocal" and was offered, top of the list at
    # 0.92, as the website of **FabLocal**. On this deployment's own portfolio
    # export the top suggestion was the media partner rather than the client
    # on 39 of 242 suggested rows. Accepting one files a client's website
    # under their agency.
    #
    # A genuine containment still scores on its own merits and still clears
    # the threshold — "Smitty's Fireplace" against "Smitty's Fireplace Shop"
    # is 0.88 — while "Acme" against "Acme Plumbing" is 0.47 and is now
    # refused, which is the whole point.
    return SequenceMatcher(None, x, y).ratio()


def suggest_for(name: str, domain: str = "", threshold: float = 0.72) -> list[dict]:
    """Candidate website records for something that didn't match exactly.

    Each candidate carries WHY it was suggested, so the person confirming can
    see whether it's a domain hit or a name resemblance before accepting.
    """
    d = _domain(domain)
    out = []
    for r in rows():
        reasons, score = [], 0.0
        if d and r["domain"] and d == r["domain"]:
            reasons.append("exact domain match")
            score = 1.0
        elif d and r["domain"]:
            # Same registrable name, different TLD or subdomain — common when
            # a client has both .com and .net, or a staging host.
            if d.split(".")[0] == r["domain"].split(".")[0]:
                reasons.append(f"domain stem matches ({r['domain']})")
                score = max(score, 0.85)
        for field, label in (("client_name", "client name"),
                             ("organization", "organisation")):
            s = _similar(name, r.get(field, ""))
            if s >= threshold:
                reasons.append(f"{label} looks like \"{r[field]}\" ({int(s*100)}%)")
                score = max(score, s)
        if score >= threshold:
            out.append({**r, "score": round(score, 3), "why": "; ".join(reasons)})
    out.sort(key=lambda x: -x["score"])
    return out[:8]


def enrich(client: str, domain: str = "") -> dict:
    """Everything object_153 can contribute to a client record."""
    r = for_client(client, domain)
    if not r:
        return {"found": False, "client": client}
    plat = (r.get("platform") or "").strip()
    low = plat.lower()
    login_path = ("/wp-admin" if "wordpress" in low
                  else "/login" if ("simvoly" in low or "smart 1" in low
                                    or "smart1" in low) else "")
    return {
        "found": True, "client": client, "knack_id": r["id"],
        "website": r["production_url"], "domain": r["domain"],
        "platform": plat, "login_path": login_path,
        "login_url": (r["production_url"].rstrip("/") + login_path
                      if r["production_url"] and login_path else ""),
        "ga_account": r["ga_account"], "gtm_account": r["gtm_account"],
        "go_live": r["go_live"], "hm_fee": r["hm_fee"],
        "media_partner": r["media_partner"], "organization": r["organization"],
        # The domain record: when the site went live, what status the client
        # is in, and everything about the domain itself.
        "live_date": r["live_date"], "client_status": r["client_status"],
        "domain_bought": r["domain_bought"],
        "domain_bought_on": r["domain_bought_on"],
        "domain_renews": r["domain_renews"],
        "registrar": registrar_for(r["domain"], record=r),
        "note": "From the Knack website registry. GA and GTM ids "
                "here are recorded regardless of whether anyone has connected "
                "that Google account, so they show even when the live lookup "
                "finds nothing.",
    }
