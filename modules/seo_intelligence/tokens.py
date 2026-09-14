"""Resolve Search Console access through the existing encrypted Google Finder store."""
from __future__ import annotations

from . import google_search_console as gsc


def token_for_property(prop):
    """Return an access token for an account that can see prop.site_url.

    Google Finder owns refresh tokens; SEO Intelligence never stores or copies
    them. This deliberately tests property visibility so an agency account with
    many connected logins does not accidentally query the wrong one.
    """
    from modules.google_finder import app as finder

    accounts, error = finder.connected_accounts_result()
    if error and not accounts:
        raise RuntimeError(error)
    failures = []
    target = (prop.site_url or "").rstrip("/")
    for account in accounts:
        if str(account.get("status") or "").upper() == "REAUTH_REQUIRED":
            continue
        email = account.get("email") or ""
        try:
            token = finder.refresh_access_token(email, account["refresh_token"])
            sites = gsc.list_sites(token)
            visible = {(s.get("siteUrl") or "").rstrip("/") for s in sites}
            if target in visible:
                return token
        except Exception as exc:
            failures.append(f"{email}: {type(exc).__name__}")
    detail = "; ".join(failures[:5])
    raise RuntimeError(
        f"No connected Google account can access Search Console property {prop.site_url}."
        + (f" Checked accounts: {detail}" if detail else "")
    )
