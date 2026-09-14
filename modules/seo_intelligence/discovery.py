"""Discover active SmartHub SEO clients in Google Search Console.

This is the rollout bridge between the existing client registry and SEO
Intelligence.  It deliberately starts from *our* active SEO clients and then
asks Google which Search Console property matches each one.  Starting from the
client book prevents an agency Google login with hundreds of unrelated or old
properties from silently turning them all into current SEO accounts.
"""
from __future__ import annotations

from . import google_search_console as gsc
from .file_store import mirror_client
from .models import SEOProperty
from .service import refresh_property, upsert_property


def _domain(value: str) -> str:
    """Return a canonical hostname from a normal URL or ``sc-domain:`` key."""
    from hub.client_context import canonical_domain

    raw = str(value or "").strip()
    if raw.lower().startswith("sc-domain:"):
        raw = raw.split(":", 1)[1]
    return canonical_domain(raw)


def preferred_site(sites: list[dict], domain: str) -> str:
    """Best Search Console property for a client's canonical domain.

    Domain properties win because they include protocol/subdomain variants.
    If the agency only has URL-prefix access, HTTPS wins over HTTP and the
    property is still usable rather than forcing a reconnect or manual setup.
    """
    domain = _domain(domain)
    if not domain:
        return ""

    matches = []
    for row in sites or []:
        site_url = str(row.get("siteUrl") or "").strip()
        if site_url and _domain(site_url) == domain:
            if site_url.lower() == f"sc-domain:{domain}".lower():
                rank = 0
            elif site_url.lower().startswith("https://"):
                rank = 1
            else:
                rank = 2
            matches.append((rank, len(site_url), site_url))
    matches.sort()
    return matches[0][2] if matches else ""


def _active_seo_clients() -> list[dict]:
    from hub import clients_registry

    rows = clients_registry.all_clients(refresh=True)
    return [
        row for row in rows
        if row.get("is_seo") and _domain(row.get("url") or row.get("domain") or "")
    ]


def discover_and_backfill_existing_clients() -> dict:
    """Register and immediately snapshot every current SEO client we can match.

    Safe to run on every scheduler pass:
    * existing property rows are upserted, not duplicated;
    * clients with a successful snapshot are not refreshed here (the normal
      weekly scheduler owns their cadence);
    * first-time clients are refreshed immediately with the token that proved
      it could see the Search Console property, avoiding a second account scan.

    Old OAuth grants cannot be expanded in code. Accounts whose token cannot
    list Search Console sites are reported as reconnect-needed, while every
    other account/client continues to backfill normally.
    """
    from modules.google_finder import app as finder

    clients = _active_seo_clients()
    accounts, account_error = finder.connected_accounts_result()
    accounts = accounts or []

    # Build one domain -> best visible property/token index across all connected
    # Google accounts. A single account failure must not stop another account
    # from supplying a client's property.
    visible: dict[str, dict] = {}
    reconnect_accounts = []
    account_errors = []
    for account in accounts:
        if str(account.get("status") or "").upper() == "REAUTH_REQUIRED":
            reconnect_accounts.append(account.get("email") or "unknown")
            continue
        email = account.get("email") or "unknown"
        try:
            token = finder.refresh_access_token(email, account["refresh_token"])
            sites = gsc.list_sites(token)
        except Exception as exc:  # one Google account must not block the book
            text = f"{type(exc).__name__}: {exc}"
            account_errors.append({"email": email, "error": text[:500]})
            # Insufficient OAuth scope is the common rollout case. Surface it
            # as reconnect-needed without pretending every Google error is auth.
            low = text.lower()
            if any(x in low for x in ("scope", "permission", "403", "unauthorized", "auth")):
                reconnect_accounts.append(email)
            continue

        # Keep all sites per domain and select the preferred one after grouping.
        by_domain: dict[str, list[dict]] = {}
        for site in sites or []:
            dom = _domain(site.get("siteUrl") or "")
            if dom:
                by_domain.setdefault(dom, []).append(site)
        for dom, domain_sites in by_domain.items():
            candidate = preferred_site(domain_sites, dom)
            if not candidate:
                continue
            current = visible.get(dom)
            # A domain property beats a URL-prefix property even when it came
            # from a later connected Google account.
            candidate_rank = 0 if candidate.lower().startswith("sc-domain:") else 1
            current_rank = 9 if not current else (0 if current["site_url"].lower().startswith("sc-domain:") else 1)
            if current is None or candidate_rank < current_rank:
                visible[dom] = {"site_url": candidate, "token": token, "email": email}

    registered = 0
    refreshed = 0
    files_written = 0
    unmatched = []
    refresh_errors = []
    matched_clients = []

    for client in clients:
        dom = _domain(client.get("url") or client.get("domain") or "")
        match = visible.get(dom)
        if not match:
            unmatched.append({"client": client.get("name") or "", "domain": dom})
            continue

        # Use SmartHub's shared derived client key so every module refers to the
        # same company. Domain-backed keys are stable and avoid name drift.
        client_id = client.get("key") or client.get("slug") or client.get("name")
        existing = SEOProperty.query.filter_by(site_url=match["site_url"]).first()
        prop = upsert_property(client_id, match["site_url"], client.get("name"))
        if existing is None:
            registered += 1
        matched_clients.append({
            "client": client.get("name") or "",
            "client_id": client_id,
            "site_url": match["site_url"],
            "google_account": match["email"],
        })

        # The user's rollout requirement: don't wait for the weekly cadence for
        # an existing client that has never received an intelligence snapshot.
        if prop.last_sync_at is None or prop.last_sync_status != "ok":
            try:
                refresh_property(prop, match["token"])
                if mirror_client(prop.client_id):
                    files_written += 1
                refreshed += 1
            except Exception as exc:
                refresh_errors.append({
                    "client": client.get("name") or "",
                    "site_url": match["site_url"],
                    "error": f"{type(exc).__name__}: {exc}"[:500],
                })

    return {
        "active_seo_clients": len(clients),
        "matched": len(matched_clients),
        "registered": registered,
        "refreshed_immediately": refreshed,
        "files_written": files_written,
        "unmatched": unmatched,
        "reconnect_accounts": sorted(set(reconnect_accounts)),
        "account_errors": account_errors,
        "refresh_errors": refresh_errors,
        "account_store_error": account_error or "",
    }
