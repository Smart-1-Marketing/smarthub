"""Marketing Efficiency Audit — the accounting-partner lead form, in the Hub.

## Why this is a proxy, not a Flask module

`modules/marketing_audit` is a plain Express app — the questionnaire, the
scoring model, the branded PDF (PDFKit), the website scan, the AI service-area
sizing. None of that is a thing worth re-deriving in Flask: it already works,
it was built and tested against real questions an accounting or bookkeeping
partner asks, and porting it would risk changing the numbers a partner brings
to a client meeting. So it runs as a second process in the Hub's own
container, the same shape `hub/ad_builder_proxy.py` and
`modules/hf_render_service` already use, and this proxies to it.

## The whole prefix is public, and that is the point

`hub/ad_builder_proxy.py` gates almost everything behind the Hub login and
carves out two client-facing paths. This tool has no such split: an
accounting or bookkeeping partner running an audit has no Hub account and
never should need one — the whole reason this exists is to be the lead form
for the Accounting Partner Program (see
``smart1marketing.com/accounting-partner-program``), reached directly or
framed from that page. So nothing here checks ``current_user()`` at all.
``wsgi.py``'s own trap applies one door over: this is a blueprint on the hub
app, not a dispatcher mount, so there is no ``PUBLIC_PREFIXES`` list for
``AuthGuard`` to read — the absence of a login check here *is* the exemption,
and it has to be total or a partner meets a sign-in form mid-audit.

## What to watch

A third Node process in one container is a real cost, same as the ad
builder's own note says. If it ever needs to feel less like tenant of the
Hub's CPU and more like its own thing, it ships from a checkout that already
knows how to run standalone (``npm start`` in ``modules/marketing_audit``) —
only ``MARKETING_AUDIT_URL`` below has to change, from loopback to wherever
it moves.
"""
from __future__ import annotations

import logging
import os

import requests
from flask import Response, request, stream_with_context

logger = logging.getLogger(__name__)

# Loopback by default: the renderer binds to 127.0.0.1 inside the container,
# so it is reachable through this proxy and not from outside.
MARKETING_AUDIT_URL = (
    os.environ.get("MARKETING_AUDIT_URL")
    or f"http://127.0.0.1:{os.environ.get('MARKETING_AUDIT_PORT', '8793')}"
).rstrip("/")

# Hop-by-hop headers must not be forwarded — the same list ad_builder_proxy
# carries, for the same reason: passing Connection or Transfer-Encoding
# through a proxy produces responses the browser cannot parse, and passing
# Host makes the upstream build wrong absolute URLs.
_DROP_REQUEST = {"host", "content-length", "connection", "keep-alive",
                 "proxy-authenticate", "proxy-authorization", "te",
                 "trailer", "transfer-encoding", "upgrade", "cookie"}
_DROP_RESPONSE = {"content-length", "connection", "keep-alive",
                  "proxy-authenticate", "proxy-authorization", "te",
                  "trailer", "transfer-encoding", "upgrade",
                  "content-encoding"}

TIMEOUT = (10, 90)          # connect, read — PDF generation and AI findings take a moment


def available() -> bool:
    """Is the audit tool answering? Used by /status and diagnostics."""
    try:
        r = requests.get(f"{MARKETING_AUDIT_URL}/api/health", timeout=3)
        return r.ok
    except requests.RequestException:
        return False


def status() -> dict:
    """Plain-language state, for /status and the proxy's own error page."""
    if not available():
        return {"ok": False, "detail":
                f"No answer from the Marketing Efficiency Audit at "
                f"{MARKETING_AUDIT_URL}. It runs as a second process in this "
                f"container; check the deploy log for lines beginning "
                f"[marketing-audit]."}
    return {"ok": True, "detail": f"Answering at {MARKETING_AUDIT_URL}."}


def register(app, url_prefix: str = "/tools/marketing-audit") -> None:
    """Mount the proxy on the Hub app.

    A blueprint rather than a DispatcherMiddleware mount, because there is no
    WSGI app to mount — the upstream is a separate process reached over HTTP.
    """
    from flask import Blueprint, redirect

    bp = Blueprint("marketing_audit", __name__, url_prefix=url_prefix)

    # Every asset and API call in this tool is written as a RELATIVE path
    # (`api/analyze`, `app.js`, `img/logo.png` -- see public/app.js and
    # public/index.html), which resolves against the *directory* of the
    # current URL. That is what makes it work identically standalone and
    # mounted here with no template layer to inject a base path into -- and
    # it is also the trap `hub/embed.py`'s own docstring names: reached
    # without the trailing slash, the browser resolves those paths against
    # `/tools/` instead of `/tools/marketing-audit/`, and every fetch and
    # every asset 404s. So the bare prefix redirects to the one canonical
    # form rather than being served directly.
    @bp.route("")
    def index_no_slash():
        return redirect(f"{url_prefix}/")

    @bp.route("/", defaults={"path": ""},
              methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @bp.route("/<path:path>",
              methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def proxy(path: str):
        upstream_url = f"{MARKETING_AUDIT_URL}/{path}"
        if request.query_string:
            upstream_url += "?" + request.query_string.decode("utf-8", "ignore")

        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in _DROP_REQUEST}
        # The visitor's real address, for the Node process's own per-IP
        # rate limits (the website scan and the analysis endpoint) — without
        # this every request would arrive from the loopback hop and share
        # one limit across every partner using the tool at once.
        headers["X-Forwarded-For"] = request.headers.get(
            "X-Forwarded-For", request.remote_addr or "")

        try:
            upstream = requests.request(
                request.method, upstream_url,
                headers=headers, params=None,
                data=request.get_data(),
                stream=True, timeout=TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.warning("marketing_audit: upstream unreachable: %s", exc)
            return Response(
                "The Marketing Efficiency Audit is temporarily unavailable. "
                "Please try again shortly.",
                status=503, mimetype="text/plain")

        resp_headers = [(k, v) for k, v in upstream.headers.items()
                        if k.lower() not in _DROP_RESPONSE]
        return Response(
            stream_with_context(upstream.iter_content(chunk_size=8192)),
            status=upstream.status_code, headers=resp_headers)

    app.register_blueprint(bp)
