"""Knack object_153 — the website registry, read by field id.

This table is the missing link between a client and their website, and it
carries far more than the Hub was using: the live URL and its domain, the GA
and GTM account ids, the platform, the go-live date, the H&M fee, the client
organisation and the media partner.

Reading it properly fixes several things at once:

  * **Matching.** Sites were being matched to clients by name, which fails on
    "Riverside HVAC" versus "Riverside HVAC LLC". This table has both a client
    name and a domain, so a site can be matched on either and cross-checked
    against the other.
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
"""
from __future__ import annotations

import os
import re

OBJECT = "object_153"

# --- object_153 field map -------------------------------------------------
F_PRODUCTION_URL = "field_2925"   # Client production URL
F_URL_DOMAIN     = "field_3111"   # URL domain
F_GA_ACCOUNT     = "field_2929"   # GA account
F_GTM_ACCOUNT    = "field_2930"   # GTM account
F_ORGANIZATION   = "field_2924"   # Client organization
F_PLATFORM       = "field_2927"   # Platform
F_GO_LIVE        = "field_3028"   # Website go-live date
F_HM_FEE         = "field_3050"   # H&M fee (hosting & maintenance)
F_CLIENT_NAME    = "field_3112"   # Client name
F_MEDIA_PARTNER  = "field_3113"   # Media partner

FIELDS = {
    "production_url": F_PRODUCTION_URL, "domain": F_URL_DOMAIN,
    "ga_account": F_GA_ACCOUNT, "gtm_account": F_GTM_ACCOUNT,
    "organization": F_ORGANIZATION, "platform": F_PLATFORM,
    "go_live": F_GO_LIVE, "hm_fee": F_HM_FEE,
    "client_name": F_CLIENT_NAME, "media_partner": F_MEDIA_PARTNER,
}


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
    """Drop the suffixes that differ between systems for the same company."""
    n = str(s or "").lower()
    n = re.sub(r"\b(llc|l\.l\.c\.|inc|inc\.|ltd|ltd\.|co|co\.|corp|"
               r"corporation|company|the|dba)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)


def _domain(v) -> str:
    from hub.client_context import canonical_domain
    return canonical_domain(_text(v))


def rows(limit: int = 2000) -> list[dict]:
    """Every website record, normalised. Returns [] rather than raising."""
    app_id = (os.environ.get("KNACK_APP_ID") or "").strip()
    api_key = (os.environ.get("KNACK_API_KEY") or "").strip()
    if not (app_id and api_key):
        return []
    import requests
    out, page = [], 1
    headers = {"X-Knack-Application-Id": app_id, "X-Knack-REST-API-Key": api_key,
               "Accept": "application/json"}
    while len(out) < limit and page <= 20:
        try:
            r = requests.get(
                f"https://api.knack.com/v1/objects/{OBJECT}/records",
                headers=headers, params={"page": page, "rows_per_page": 100},
                timeout=25)
            if not r.ok:
                break
            data = r.json()
        except Exception:                               # noqa: BLE001
            break
        recs = data.get("records") or []
        if not recs:
            break
        for rec in recs:
            url = _text(rec.get(F_PRODUCTION_URL))
            dom = _domain(rec.get(F_URL_DOMAIN)) or _domain(url)
            out.append({
                "id": rec.get("id", ""),
                "client_name": _text(rec.get(F_CLIENT_NAME)),
                "organization": _text(rec.get(F_ORGANIZATION)),
                "production_url": url,
                "domain": dom,
                "ga_account": _text(rec.get(F_GA_ACCOUNT)),
                "gtm_account": _text(rec.get(F_GTM_ACCOUNT)),
                "platform": _text(rec.get(F_PLATFORM)),
                "go_live": _text(rec.get(F_GO_LIVE)),
                "hm_fee": _money(rec.get(F_HM_FEE)),
                "media_partner": _text(rec.get(F_MEDIA_PARTNER)),
            })
        if len(recs) < 100:
            break
        page += 1
    return out[:limit]


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


def for_client(client: str, domain: str = "") -> dict:
    """The website record for one client, matched on domain then name."""
    idx = index()
    d = _domain(domain) if domain else ""
    if d and d in idx["by_domain"]:
        return idx["by_domain"][d]
    return idx["by_name"].get(_norm_name(client), {})


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
    if x in y or y in x:
        return 0.92
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
        "note": "From the Knack website registry (object_153). GA and GTM ids "
                "here are recorded regardless of whether anyone has connected "
                "that Google account, so they show even when the live lookup "
                "finds nothing.",
    }
