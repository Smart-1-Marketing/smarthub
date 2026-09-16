"""One reading of "may this Hub fetch that URL", and one way to fetch it.

Every module here that reaches a **client's own website** is fetching an
address somebody typed into a form or a crawler found in a sitemap -- not a
provider endpoint we chose. From inside our own network that is the ordinary
SSRF shape: `http://169.254.169.254/latest/meta-data/` reads the cloud
metadata endpoint, `http://127.0.0.1:5432` is our own Postgres, and a
perfectly public hostname can resolve to either.

The rule already existed twice and in neither place the next person would look
for it. `modules/ad_builder/src/assets.ts` has `assetUrlIsSafe()`, whose own
docstring says the Hub's modules "already refuse the same shapes"; and
`modules/restaurant/app.py` has `_ssrf_safe()`, which is the better of the two
because it checks the **resolved addresses** rather than the hostname. What
neither of them is, is reachable from `hub/`, so `hub/wordpress.py` shipped
six outbound fetches with no guard at all -- one of which carries the client's
application password in an Authorization header.

This is that rule, in the place the shared services live, so the next module
to fetch a client's page does not have to rediscover it.

## What it refuses, and why each one

**Anything that resolves inside our own network.** Checked on the resolved
IPs, never the hostname: a public name pointing at a private address is the
whole trick, and a hostname denylist catches none of it.

**A name that will not resolve.** Refused rather than allowed -- "we could not
look" is not permission, which is the rule this codebase applies to every
other unmeasurable thing.

**Credentials in the URL.** `https://user:pass@host/` is never ours, and
`requests` would send them.

**A scheme that is not http or https.** `file://` reads the disk.

## How it fetches

**Redirects are followed by hand, and every hop is re-checked.** A guard that
validates the first URL and then lets `requests` follow a 302 to
`http://169.254.169.254/` has checked nothing -- which is why
`allow_redirects` is False here and the loop is written out.

**The body is capped.** `r.text` on an arbitrary page is however many
megabytes somebody else's server decides to send, into a request thread. The
cap is on the bytes actually read, not on a Content-Length a server is free to
lie about.

**Nothing in here raises for a refusal.** `fetch()` answers `(response,
reason)` so a caller can say what happened in its own words; the modules that
reach clients' sites already report per item, and an exception would cost them
that.
"""
from __future__ import annotations

import ipaddress
import socket
import urllib.parse

import requests

# 10 MB. A page that does not fit in this is not a page anybody is reading a
# meta tag out of, and the number is ours rather than anybody's published one.
MAX_BYTES = 10 * 1024 * 1024
MAX_HOPS = 4
TIMEOUT = 20

UA = {"User-Agent": "Smart1Hub/1.0 (+https://smart1.agency)"}


def safe_url(url: str) -> tuple[bool, str]:
    """May we fetch this? Answers (ok, why-not) and never raises.

    The reason is a sentence rather than a code because every caller prints it
    at somebody: "that address is inside our own network" is actionable and
    "refused" is not.
    """
    raw = str(url or "").strip()
    if not raw:
        return False, "No address was given."
    try:
        u = urllib.parse.urlparse(raw)
    except ValueError:
        return False, "That is not a URL we can read."
    if u.scheme not in ("http", "https"):
        return False, ("Only http and https addresses are fetched — "
                       f"'{u.scheme or 'no scheme'}' is not one.")
    if u.username or u.password:
        return False, ("That address carries a username and password in it, "
                       "which this never sends.")
    host = u.hostname or ""
    if not host:
        return False, "That address names no host."
    try:
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return False, "That address has a port this cannot read."
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except Exception:                                       # noqa: BLE001
        # Unresolvable is refused, not allowed: "we could not look" has never
        # been permission anywhere else in this Hub either.
        return False, f"'{host}' does not resolve to an address we can reach."
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False, f"'{host}' resolved to something that is not an address."
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False, (f"'{host}' resolves to {ip}, which is inside our own "
                           "network rather than on the public internet.")
    return True, ""


def _read_capped(r, limit: int) -> tuple[bytes, bool]:
    """The body, up to `limit`. Second value says it was cut short.

    Read off the stream rather than trusted from Content-Length, which a
    server is free to understate.
    """
    out, total, capped = [], 0, False
    for chunk in r.iter_content(64 * 1024):
        if not chunk:
            continue
        out.append(chunk)
        total += len(chunk)
        if total >= limit:
            capped = True
            break
    return b"".join(out)[:limit], capped


def fetch(url: str, *, headers: dict | None = None, timeout: int = TIMEOUT,
          max_bytes: int = MAX_BYTES,
          allow_redirects: bool = True) -> tuple[object | None, str]:
    """GET a URL every hop of which has been checked. Answers (response, why-not).

    The response carries `.text`, `.status_code`, `.headers` and `.url` like a
    `requests` one, and `.truncated` where the body hit the cap -- so a caller
    that reads a page can say it only saw part of it rather than concluding
    something is absent from it.
    """
    ok, why = safe_url(url)
    if not ok:
        return None, why
    hops, current = 0, url
    while True:
        try:
            r = requests.get(current, headers=dict(UA, **(headers or {})),
                             timeout=timeout, allow_redirects=False,
                             stream=True)
        except requests.RequestException as exc:            # noqa: BLE001
            return None, f"Could not reach that address ({type(exc).__name__})."
        if allow_redirects and r.is_redirect and hops < MAX_HOPS:
            nxt = r.headers.get("Location") or ""
            r.close()
            if not nxt:
                return None, "That address redirected to nowhere."
            nxt = urllib.parse.urljoin(current, nxt)
            ok, why = safe_url(nxt)
            if not ok:
                # The hop is where a guard that checked only the first URL has
                # already lost: this is the redirect doing the work.
                return None, f"That address redirected somewhere we do not fetch: {why}"
            current, hops = nxt, hops + 1
            continue
        if allow_redirects and r.is_redirect:
            r.close()
            return None, "That address redirects more times than this follows."
        body, capped = _read_capped(r, max_bytes)
        r.close()
        return _Answer(r, body, capped), ""


class _Answer:
    """What a caller gets back: a requests response with a bounded body."""

    def __init__(self, r, body: bytes, truncated: bool):
        self._r = r
        self.content = body
        self.truncated = truncated
        self.status_code = r.status_code
        self.headers = r.headers
        self.url = r.url

    @property
    def text(self) -> str:
        enc = self._r.encoding or self._r.apparent_encoding or "utf-8"
        try:
            return self.content.decode(enc, "replace")
        except LookupError:
            return self.content.decode("utf-8", "replace")
