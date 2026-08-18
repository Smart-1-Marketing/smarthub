"""Match Smart 1 Sites projects to Hub clients, by domain.

`project_meta.internal_client_name` is what links a Simvoly project to a client
in Knack, and it is filled in by hand. With over a thousand projects that means
it is mostly empty, so the Sites margin report has no revenue to compare cost
against, and no cross-tool report can see a client's website.

Nothing was doing the obvious thing: `websites.domain` is already on every
project, and the client registry already has a URL. A domain is an exact
identifier where a name is not — "Riverside HVAC" and "Riverside HVAC LLC" are
different strings for one company, but riverside-hvac.com is one company.

This proposes matches; it does not apply them silently. A wrong
`internal_client_name` is worse than a blank one — it attributes revenue to the
wrong client and the billing audits then disagree with each other for reasons
nobody can trace. So `suggest()` is read-only and `apply()` takes an explicit
list of the matches a human accepted.
"""
from __future__ import annotations

import re

from hub.client_context import canonical_domain

# Simvoly's own hosting domains. A project still parked on one of these has no
# real domain yet, so matching on it would join every unlaunched site together.
PLATFORM_DOMAINS = {
    "simvoly.com", "simvolysite.com", "smart1sites.com", "mysimvoly.com",
}


def _is_platform(domain: str) -> bool:
    d = (domain or "").lower()
    return any(d == p or d.endswith("." + p) for p in PLATFORM_DOMAINS)


def _norm_name(name: str) -> str:
    """Normalise a business name for a fallback comparison.

    Drops the suffixes that differ between systems for the same company —
    "LLC", "Inc", "Ltd" — and any punctuation. Only used when there is no
    domain to match on, and only reported as a lower-confidence suggestion.
    """
    n = (name or "").lower()
    n = re.sub(r"\b(llc|l\.l\.c\.|inc|inc\.|ltd|ltd\.|co|co\.|corp|corporation|"
               r"company|the)\b", " ", n)
    return re.sub(r"[^a-z0-9]+", "", n)


def _hub_clients() -> list[dict]:
    try:
        from hub import clients_registry
        return clients_registry.all_clients()
    except Exception:                                   # noqa: BLE001
        return []


def _site_rows() -> list[dict]:
    """Every Simvoly project with its domain and current client link."""
    try:
        from modules.sites_admin import db as sdb
    except Exception:                                   # noqa: BLE001
        return []
    # query_projects is paginated and returns (rows, total). Page through it
    # rather than asking for one huge page — the count is over a thousand.
    out, page, per_page = [], 1, 200
    try:
        while True:
            rows, total = sdb.query_projects(page=page, per_page=per_page)
            out.extend(r for r in rows if isinstance(r, dict))
            if len(out) >= total or not rows or page > 30:
                break
            page += 1
    except Exception:                                   # noqa: BLE001
        return out
    return out


def suggest(limit: int = 2000) -> dict:
    """Propose a client for every unmatched site. Changes nothing."""
    clients = _hub_clients()
    by_domain, by_name = {}, {}
    for c in clients:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        d = canonical_domain(c.get("url") or c.get("domain") or "")
        if d and not _is_platform(d):
            by_domain.setdefault(d, name)
        by_name.setdefault(_norm_name(name), name)

    rows = _site_rows()[:limit]
    matched, already, no_domain, unmatched = [], [], [], []

    for r in rows:
        pid = str(r.get("project_id") or r.get("id") or "")
        site = (r.get("name") or r.get("site") or "").strip()
        current = (r.get("internal_client_name") or "").strip()
        raw = r.get("domain") or r.get("url") or r.get("subdomain") or ""
        dom = canonical_domain(raw)

        if current:
            already.append({"project_id": pid, "site": site,
                            "client": current, "domain": dom})
            continue
        if not dom or _is_platform(dom):
            no_domain.append({"project_id": pid, "site": site,
                              "domain": raw or "",
                              "why": "Still on a platform domain — no real "
                                     "website to match on yet."
                                     if _is_platform(dom) else
                                     "No domain recorded."})
            continue

        hit = by_domain.get(dom)
        if hit:
            matched.append({"project_id": pid, "site": site, "domain": dom,
                            "client": hit, "confidence": "domain",
                            "why": "Exact domain match against the client "
                                   "registry."})
            continue
        # Fall back to a normalised name, reported separately because it is a
        # guess and should be eyeballed before being applied.
        nm = by_name.get(_norm_name(site))
        if nm:
            matched.append({"project_id": pid, "site": site, "domain": dom,
                            "client": nm, "confidence": "name",
                            "why": "Business names match once LLC/Inc and "
                                   "punctuation are ignored. Check before "
                                   "applying."})
            continue
        unmatched.append({"project_id": pid, "site": site, "domain": dom})

    by_conf = {"domain": 0, "name": 0}
    for m in matched:
        by_conf[m["confidence"]] += 1

    return {
        "checked": len(rows),
        "already_linked": len(already),
        "suggested": matched,
        "suggested_count": len(matched),
        "by_confidence": by_conf,
        "no_domain": no_domain,
        "no_domain_count": len(no_domain),
        "unmatched": unmatched,
        "unmatched_count": len(unmatched),
        "clients_with_domain": len(by_domain),
        "note": "Nothing has been changed. A wrong internal_client_name is "
                "worse than a blank one — it attributes revenue to the wrong "
                "client and makes the billing audits disagree. Review these, "
                "then apply the ones you accept.",
    }


def apply(matches: list[dict], actor: str = "") -> dict:
    """Write accepted matches. Takes an explicit list, never the whole set."""
    try:
        from modules.sites_admin import db as sdb
    except Exception as exc:                            # noqa: BLE001
        return {"error": f"Sites module unavailable ({type(exc).__name__})."}

    saved, failed = [], []
    for m in matches or []:
        pid = str(m.get("project_id") or "").strip()
        client = str(m.get("client") or "").strip()
        if not pid or not client:
            failed.append({"project_id": pid, "error": "missing id or client"})
            continue
        try:
            sdb.save_meta(pid, internal_client_name=client)
            saved.append({"project_id": pid, "client": client})
        except Exception as exc:                        # noqa: BLE001
            failed.append({"project_id": pid, "error": type(exc).__name__})

    try:
        from hub import audit
        audit.log("sites_admin", "clients_matched", actor=actor or None,
                  saved=len(saved), failed=len(failed))
    except Exception:                                   # noqa: BLE001
        pass
    return {"saved": len(saved), "failed": failed, "items": saved}
