"""Hub pages rendered inside Smart 1 Suite's own UI.

A HighLevel custom menu link is an iframe pointing at a URL you give it. So
"an app inside Suite" is, mechanically, a Hub page that survives being framed
by somebody else's site. Three things in this Hub stop that happening, and each
one fails in a way that looks like something else.

## 1. The login cookie is not sent inside the frame

`s1hub_auth` is `SameSite=Lax`, so a browser will not attach it to a request
made from a page on gohighlevel.com. The Hub then sees an anonymous request,
`AuthGuard` redirects to `/login`, and the rep watches a login form appear
inside Suite for an account they are already signed in to. Nothing errors.

The fix is a companion cookie, not a change to the existing one. Relaxing
`s1hub_auth` to `SameSite=None` would attach it to *every* cross-site request
to the Hub — including a form POST from a page an attacker controls — and this
Hub has destructive buttons behind that cookie (delete a sub-account, delete an
image, dissolve a client group). So `s1hub_embed` carries the same signed value
and is accepted under two conditions that together make it useless for that
attack:

  * **safe methods only** — GET and HEAD. A cross-site POST carrying it
    authenticates as nobody, exactly as it does today.
  * **allowlisted paths only** — see `EMBEDDABLE`. Not every Hub page is
    reachable this way just because one is.

The consequence has to be said out loud rather than discovered: **an embedded
page is read-only.** Buttons that write still need the Lax cookie, which the
frame does not have. That is the honest first increment, and it is why the
client-facing version needs HighLevel's SSO handshake rather than more cookie
work — see `SSO_NOT_BUILT` at the bottom of this file.

## 2. The chrome is injected into anything that looks like a page

`HubBar` already skips the sidebar when `Sec-Fetch-Dest` says `iframe`, which
covers every dispatcher-mounted module. The hub app's own `after_request` does
not, and Client 360 is a hub route — so the one page most worth putting inside
Suite is the one that would arrive with a full second navigation column inside
a frame that already has one. `is_embedded()` is the shared test so both halves
answer the same way.

## 3. Nothing pins who may frame us

No `X-Frame-Options` and no CSP is set on hub pages today, so the Hub can be
framed by anyone — a clickjacking surface on a staff tool. Adding the embed
path without also adding the allowlist would widen that from an oversight into
a feature. `framable()` is the same shape `modules/msa/app.py` already uses for
the signing page, for the same reason stated there: one rule in charge of the
answer rather than two that can disagree.
"""
from __future__ import annotations

import os

from . import auth

COOKIE_NAME = "s1hub_embed"

# Who may frame a Hub page. CSP syntax, space-separated. HighLevel serves the
# agency UI from app.gohighlevel.com and whitelabel domains from elsewhere, so
# this is overridable — but it is an allowlist and never a wildcard. The scan
# widget at modules/scans is the deliberate exception in this codebase (it is
# pasted onto clients' own domains and has to accept any of them); a staff tool
# is framed on exactly one site and should say so.
FRAME_ANCESTORS = os.environ.get(
    "HUB_EMBED_FRAME_ANCESTORS",
    "'self' https://app.gohighlevel.com https://*.gohighlevel.com "
    "https://*.leadconnectorhq.com https://*.smart1marketing.com"
).strip()

# Paths that may be served inside the frame. Prefixes, matched against the
# composed app's path.
#
# Deliberately short. Every entry is a read surface a rep actually wants while
# looking at a client in Suite, plus the assets and GET APIs those pages need
# to render. A write-heavy tool is *not* listed: with the embed cookie limited
# to safe methods it would load, look complete, and fail on save — which is a
# worse offer than not being there. Widen this when the SSO path lands and
# writes work, not before.
EMBEDDABLE: tuple[str, ...] = (
    "/client360",          # who is this client — the reason to do this at all
    "/api/c360",           # and the fetches it renders from
    "/api/client/",
    "/api/clients/",
    "/assets/",            # theme.css
    "/hub-",               # hub-help.js and friends
    "/static/",
)

SAFE_METHODS = ("GET", "HEAD")


def embeddable(path: str) -> bool:
    """Is this path allowed to render inside somebody else's page?"""
    return (path or "/").startswith(EMBEDDABLE)


def is_embedded(environ: dict) -> bool:
    """Is this request being made for a frame?

    Same test `HubBar` applies, kept here so the hub app and the module wrapper
    cannot drift into disagreeing about what "embedded" means. `?embed=1` is
    the explicit opt-out for callers that want it; `Sec-Fetch-Dest` is what
    browsers send without anyone having to remember.
    """
    if "embed=1" in (environ.get("QUERY_STRING") or ""):
        return True
    return environ.get("HTTP_SEC_FETCH_DEST") in ("iframe", "frame")


# ------------------------------------------------------------------- cookie
def issue_cookie(resp, name: str, secure: bool) -> None:
    """Set the embed companion beside the ordinary login cookies.

    `secure` is passed in rather than read here so it matches whatever the
    caller decided for the other two — three cookies disagreeing about Secure
    on one response is a debugging session nobody needs. Note that a
    `SameSite=None` cookie without `Secure` is rejected outright by every
    current browser, so off a production deploy this simply does not stick;
    that is correct, and it is why the embed is not silently available on a
    local HTTP run.
    """
    resp.set_cookie(
        COOKIE_NAME, auth.issue_cookie_value(name),
        max_age=auth.SESSION_TTL_SECONDS, httponly=True,
        samesite="None", secure=secure,
    )


def clear_cookie(resp) -> None:
    resp.delete_cookie(COOKIE_NAME, samesite="None", secure=True)


def user_from_environ(environ: dict) -> str | None:
    """The signed-in name carried by the embed cookie, if it may be used here.

    Returns None — not an error — when the request is anything other than a
    safe method on an allowlisted path. A caller that gets None falls through
    to the ordinary cookie check and then to the usual 401 or redirect, so a
    write attempted from inside the frame is refused by the same code that
    refuses an anonymous one.
    """
    if environ.get("REQUEST_METHOD") not in SAFE_METHODS:
        return None
    if not embeddable(environ.get("PATH_INFO") or "/"):
        return None
    for part in (environ.get("HTTP_COOKIE") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE_NAME:
            return auth.verify_cookie_value(v)
    return None


# ------------------------------------------------------------------ response
def framable(resp):
    """Let the allowlisted hosts frame this response, and nobody else."""
    resp.headers["Content-Security-Policy"] = "frame-ancestors " + FRAME_ANCESTORS
    # X-Frame-Options has no allowlist form: any value it could carry would
    # either forbid the embed outright or be honoured inconsistently, and some
    # browsers let it override CSP. Dropping it leaves one rule in charge.
    resp.headers.pop("X-Frame-Options", None)
    return resp


def refuse(path: str) -> str:
    """What a page that is not embeddable says when somebody frames it.

    A blank frame or a redirect loop reads as a broken integration and gets
    reported as one. Naming the path and the reason turns it into a
    one-line fix by whoever configured the menu link.
    """
    return (
        "This Hub page is not available inside Smart 1 Suite.\n\n"
        f"Path: {path}\n\n"
        "Only a short allowlist of read-only pages can be embedded — see "
        "EMBEDDABLE in hub/embed.py. Open the Hub directly for anything else."
    )


# --------------------------------------------------------------------------
# The half that is designed and not built
# --------------------------------------------------------------------------
SSO_NOT_BUILT = """\
A client-facing page inside their own sub-account cannot use any of the above.

The rep case works because the rep already has a Hub session in that browser;
a client has no Hub account at all and must never be given one. HighLevel's
answer is an SSO handshake: the framed page posts `REQUEST_USER_DATA` to its
parent, HighLevel replies with a payload encrypted under the app's SSO key,
and the app decrypts it server-side to learn the HighLevel user and the
location they are in.

Two things about that are worth writing down before anyone starts:

  * The SSO key is a *third* credential, separate from GHL_CLIENT_ID and
    GHL_CLIENT_SECRET, issued on the app's own settings page.
  * The location id in that payload is the authorisation, and it is the whole
    security model. Every read behind a client-facing page has to be filtered
    by it — resolved to a client the way hub/client_key.py resolves one, never
    by a name in a query string. Getting that wrong shows one client another
    client's record, which is the worst outcome any tool in this Hub can
    produce.
"""
