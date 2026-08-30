"""Every OAuth redirect URI this Hub will actually send, and where to register it.

Six OAuth flows run here and each hands a provider a callback URL that has to
have been registered, verbatim, in that provider's console first. Nothing in
the Hub listed them: `/diagnostics` said which *variables* resolved, the
Google Access admin page printed its own one, and the other five were
knowable only by reading the source. So the day a second hostname started
answering for this service, the first anybody knew was a customer meeting
`redirect_uri_mismatch` on a Google consent screen.

## The failure this exists to stop

**A redirect URI is matched as an exact string, hostname included.** Adding a
custom domain does not change any registration, and Render keeps the
`onrender.com` subdomain live alongside it unless it is switched off — so the
service answers on two hostnames and the callback that is sent depends
entirely on which one the person happened to browse. Half the flows here work
that way.

## The two families, which is the whole point of the panel

**Host-derived** — built from the hostname of the request, so they follow the
browser and there is **one string to register per hostname**:

* Google Finder, `url_for(_external=True)` in `modules/google_finder/app.py`
* Hub Google sign-in, `request.url_root` in `hub/__init__.py`
* QuickBooks, `request.url_root` in `hub/quickbooks.py`, unless pinned

**Environment-derived** — built from a variable, so they are the same string
whatever anybody browses, and moving the Hub to a new domain changes them
*only* when the variable is edited:

* Google Access and Smart 1 Suite, `PUBLIC_BASE_URL`
* Smart 1 Ads, `GOOGLE_ADS_REDIRECT_URI`, which is a whole URL rather than a
  path and so does not follow `PUBLIC_BASE_URL` either

Reporting these as one list would be the confidently wrong answer: somebody
registers two new URIs at Google, watches staff sign-in start working, and
does not learn until a client is on the phone that the client-facing consent
link is still being built from the old hostname.

## Rules

* **A provider with no credentials has nothing to register.** Google Ads is
  parked on this deployment, so its row reads *not in use* rather than
  standing on the panel for ever as a missing redirect URI — the
  `provider_check.py` rule that no key is *not measured* and never a cross.
* **No value is carried.** The OAuth client is named by the variable that
  supplied it, never printed. This is rendered into a page and pasted into
  chats.
* **Only hostnames we have actually observed are listed**, and each says how
  it was observed. Enumerating a service's custom domains is not something an
  app can do from inside itself, so a panel that implied it had would be
  understating the work every time somebody adds a third.
* **Nothing here raises.** A diagnostics panel that 500s costs the eleven
  other panels on the page.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

# Where each provider's registration screen is, in one string so the panel and
# any future caller cannot describe it two ways.
GOOGLE_CONSOLE = "Google Cloud Console → APIs & Services → Credentials"
GHL_CONSOLE = "HighLevel Marketplace → your app → Redirect URLs"
INTUIT_CONSOLE = "developer.intuit.com → your app → Keys & OAuth"

HOST = "host"  # built from the hostname of the request


def _env(name: str) -> str:
    # Read at call time and stripped of the quotes Render stores literally —
    # the hub/config.py rule, because a value read at import is a value from
    # whatever the environment looked like at boot.
    return (os.environ.get(name) or "").strip().strip('"').strip("'")


def _origin(url: str) -> str:
    """The scheme and host of a URL, with any path discarded."""
    parts = urlsplit(url if "://" in url else "https://" + url)
    return f"{parts.scheme}://{parts.netloc}" if parts.netloc else ""


def _host_of(url: str) -> str:
    return urlsplit(url if "://" in url else "https://" + url).netloc.lower()


# Every OAuth flow in the Hub. `source` is the variable the URI is built from,
# or HOST where it follows the browser.
FLOWS = (
    {
        "key": "google_finder",
        "label": "Google Finder — staff connect a Google account",
        "path": "/google/oauth2callback",
        "source": HOST,
        "console": GOOGLE_CONSOLE,
        "client": ("GOOGLE_CLIENT_ID",),
        "where": "modules/google_finder/app.py",
    },
    {
        "key": "hub_login",
        "label": "Hub sign-in with Google",
        "path": "/auth/google/callback",
        "source": HOST,
        "console": GOOGLE_CONSOLE,
        "client": ("GOOGLE_CLIENT_ID",),
        "where": "hub/identity.py",
        # The button is behind HUB_GOOGLE_LOGIN, but the route answers either
        # way and the registration is what has lead time on it, so this is
        # listed whether or not the button is showing.
        "flag": "HUB_GOOGLE_LOGIN",
    },
    {
        "key": "google_access",
        "label": "Google Access — a client grants us access",
        "path": "/connect/callback",
        "source": "PUBLIC_BASE_URL",
        "console": GOOGLE_CONSOLE,
        "client": ("GOOGLE_ACCESS_CLIENT_ID", "GOOGLE_CLIENT_ID"),
        "where": "modules/google_access/config.py",
        # This is the one a customer meets. A mismatch here fails in front of
        # them, for a reason that is nothing to do with them.
        "client_facing": True,
    },
    {
        "key": "google_ads",
        "label": "Smart 1 Ads — Google Ads API",
        "path": "/tools/ads/oauth/callback",
        "source": "GOOGLE_ADS_REDIRECT_URI",
        "console": GOOGLE_CONSOLE,
        "client": ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_CLIENT_ID"),
        "where": "modules/ads_builder/google_ads.py",
        # Parked until Google approves a developer token.
        "requires": ("GOOGLE_ADS_DEVELOPER_TOKEN",),
    },
    {
        "key": "suite",
        "label": "Smart 1 Suite — HighLevel Marketplace app",
        "path": "/suite/oauth/callback",
        "source": "PUBLIC_BASE_URL",
        "console": GHL_CONSOLE,
        "client": ("GHL_CLIENT_ID",),
        "where": "hub/ghl_oauth.py",
    },
    {
        "key": "quickbooks",
        "label": "QuickBooks",
        "path": "/qb/callback",
        "source": HOST,
        "console": INTUIT_CONSOLE,
        "client": ("QB_CLIENT_ID",),
        "where": "hub/quickbooks.py",
        # QB_REDIRECT_URI pins it; unset, it follows the browser like the
        # other two host-derived ones.
        "pin": "QB_REDIRECT_URI",
    },
)


def known_hosts(request_origin: str = "") -> list[dict]:
    """The origins this Hub is known to answer on, and how each is known.

    Two at most, and both are observations rather than a survey: the origin
    the current request arrived on, and whatever `PUBLIC_BASE_URL` names. A
    service can carry more custom domains than either — Render also keeps its
    `onrender.com` subdomain live unless it is switched off — and an app
    cannot enumerate its own DNS, so the count is never presented as complete.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def add(origin: str, how: str):
        origin = _origin(origin)
        if not origin or origin.lower() in seen:
            return
        seen.add(origin.lower())
        out.append({"origin": origin, "how": how})

    add(request_origin, "the address this page was opened on")
    add(_env("PUBLIC_BASE_URL"), "PUBLIC_BASE_URL")
    return out


def rows(request_origin: str = "") -> list[dict]:
    """One row per OAuth flow: what it sends, from where, and where it goes."""
    hosts = known_hosts(request_origin)
    base = _env("PUBLIC_BASE_URL")
    out = []
    for flow in FLOWS:
        client_var = next((n for n in flow["client"] if _env(n)), "")
        configured = bool(client_var)
        missing = [n for n in flow.get("requires", ()) if not _env(n)]

        source = flow["source"]
        pin = _env(flow["pin"]) if flow.get("pin") else ""
        if pin:
            source = flow["pin"]

        uris: list[str] = []
        problem = ""
        if source == HOST:
            # One string per hostname. This is the whole reason the panel
            # exists: a second domain answering for the service silently
            # doubles what has to be registered, and nothing said so.
            uris = [h["origin"] + flow["path"] for h in hosts]
            if not uris:
                problem = ("No hostname is known, so nothing can be listed. "
                           "Open this page over https, or set PUBLIC_BASE_URL.")
        elif source == "PUBLIC_BASE_URL":
            if base:
                origin = _origin(base)
                uris = [origin + flow["path"]]
                # PUBLIC_BASE_URL is an origin. A path in it lands inside every
                # URL the Hub builds, this callback included — hub/config.py
                # reports the variable, this reports what it does here.
                if base.rstrip("/") != origin:
                    problem = (f"PUBLIC_BASE_URL is {base}, which has a path in "
                               f"it. It is an origin and nothing else.")
            else:
                problem = ("PUBLIC_BASE_URL is not set, so this callback is "
                           "built with no hostname at all and cannot work.")
        else:
            pinned = _env(source)
            if pinned:
                uris = [pinned]
                host = _host_of(pinned)
                if hosts and host not in {_host_of(h["origin"]) for h in hosts}:
                    problem = (f"{source} points at {host}, which is not a "
                               f"hostname this Hub is known to answer on.")
            elif configured:
                problem = f"{source} is not set, so this flow has nowhere to return to."

        # Order matters. A flow nobody can run has nothing anybody has to act
        # on, so "not in use" is decided *before* a missing or mismatched URI
        # is reported as a finding — otherwise Smart 1 Ads, parked until
        # Google approves a developer token, stands on this panel in amber
        # for ever and teaches people to skim past the rows that are real.
        if not configured:
            state, note = "off", (
                "No OAuth client is set (" + " / ".join(flow["client"]) +
                "), so there is nothing to register yet.")
        elif missing:
            state, note = "off", (
                "Not in use on this deployment: " + ", ".join(missing) +
                " is not set. The redirect URI still has to be registered "
                "before it can be." + (" " + problem if problem else ""))
        elif problem:
            state, note = "warn", problem
        elif source == HOST and len(uris) > 1:
            # Not a fault — it is the thing somebody has to act on.
            state, note = "warn", (
                f"This Hub answers on {len(uris)} hostnames and this callback "
                f"follows whichever one the browser used, so every one of "
                f"these must be registered or that hostname fails.")
        else:
            state, note = "ok", "Registered as one string, at the console named below."

        out.append({
            "key": flow["key"],
            "label": flow["label"],
            "path": flow["path"],
            "console": flow["console"],
            "where": flow["where"],
            "follows_browser": source == HOST,
            "source": "the hostname in the browser" if source == HOST else source,
            # The variable, never the value: this is read on a screen and
            # pasted into chats.
            "client_var": client_var,
            "client_names": list(flow["client"]),
            "configured": configured,
            "client_facing": bool(flow.get("client_facing")),
            "uris": uris,
            "state": state,
            "note": note,
        })
    return out


def problems(request_origin: str = "") -> list[dict]:
    """Only what somebody has to act on, for the top of the panel."""
    out = []
    hosts = known_hosts(request_origin)
    if len(hosts) > 1:
        origins = ", ".join(h["origin"] for h in hosts)
        out.append({
            "name": "Two hostnames",
            "detail": (f"This Hub is answering on {origins}. Three of the six "
                       f"callbacks below are built from whichever one the "
                       f"browser used, so each needs registering under both "
                       f"spellings — a hostname that is missing one fails with "
                       f"redirect_uri_mismatch and nothing else."),
        })
    for r in rows(request_origin):
        if r["state"] == "warn":
            out.append({"name": r["label"], "detail": r["note"]})
    return out


def report(request_origin: str = "") -> dict:
    try:
        return {
            "hosts": known_hosts(request_origin),
            "redirects": rows(request_origin),
            "problems": problems(request_origin),
            "measured": True,
        }
    except Exception as exc:  # noqa: BLE001 — a panel never costs the page
        # "We could not look" is not "there are no redirect URIs", the rule
        # connected_accounts_result() gives in Google Finder.
        return {"hosts": [], "redirects": [], "problems": [],
                "measured": False, "error": str(exc)}
