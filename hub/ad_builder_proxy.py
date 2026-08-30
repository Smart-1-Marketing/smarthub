"""Display Ad Builder, reachable through the Hub.

The ad builder is a Node service — roughly ten thousand lines of TypeScript
with a native image pipeline that a Python port would have to re-derive, and
re-deriving a layout engine changes the creative clients already receive. So it
runs as a second process in the same container and this proxies to it.

## What this buys

One login. The ad builder has its own admin token, which is the right weight
for a standalone service but wrong inside the Hub: staff would have a second
password for one tool. Everything under ``/tools/display-ads`` sits behind the
Hub session like every other module, and the token is added here, server-side,
where the browser never sees it.

## What to watch

Two processes in one container is a real cost and it was taken deliberately.
If Render builds start timing out or memory gets tight, the ad builder ships
its own ``render.yaml`` and can move to its own service without touching its
code — only ``AD_BUILDER_URL`` below changes, from loopback to that service's
address. Nothing else in the Hub knows the difference.
"""
from __future__ import annotations

import logging
import os
import re

import requests
from flask import Response, request, stream_with_context

logger = logging.getLogger(__name__)

# Loopback by default: the renderer binds to 127.0.0.1 inside the container, so
# it is reachable through this proxy and not from outside. Point this at a URL
# to split the service out later.
AD_BUILDER_URL = (os.environ.get("AD_BUILDER_URL")
                  or f"http://127.0.0.1:{os.environ.get('ADBUILDER_PORT', '8791')}").rstrip("/")

# The ad builder refuses its internal routes unless a token is set, which is
# the behaviour we want — it fails closed. Inside the Hub the token is an
# implementation detail: this proxy holds it and the browser never does.
ADMIN_TOKEN = (os.environ.get("ADBUILDER_ADMIN_TOKEN")
               or os.environ.get("ADMIN_TOKEN") or "").strip()

# Hop-by-hop headers must not be forwarded — passing Connection or
# Transfer-Encoding through a proxy produces responses the browser cannot
# parse, and passing Host makes the upstream build wrong absolute URLs.
_DROP_REQUEST = {"host", "content-length", "connection", "keep-alive",
                 "proxy-authenticate", "proxy-authorization", "te",
                 "trailer", "transfer-encoding", "upgrade", "cookie"}
_DROP_RESPONSE = {"content-length", "connection", "keep-alive",
                  "proxy-authenticate", "proxy-authorization", "te",
                  "trailer", "transfer-encoding", "upgrade",
                  "content-encoding"}

TIMEOUT = (10, 180)          # connect, read — a full ad package takes a while

# The two paths a client meets, and the only ones that answer without a Hub
# login.
#
# A proof is a page you send to somebody outside this company. Behind the Hub
# session it was staff-only, so "here is the link, tell us what you think"
# landed a client on a login form for an account they do not have — a URL that
# looks like a working link and is a dead end, which is worse than not offering
# one. The project id in the path is the capability, the same arrangement
# ``modules/scans`` and ``modules/ads_builder`` use for their client-facing
# documents, and the renderer draws the page without its editor when the
# request arrives without the admin token.
#
# Matched on the whole path segment, anchored, so ``/proofs`` and
# ``/api/proof/x/rebuild`` are not in it. Rebuild is deliberately out: it
# re-renders the creative for everyone holding the link and reaches endpoints
# that are billed per call, so it stays with the operator.
PUBLIC_PATTERNS = (
    re.compile(r"^proof/[\w.-]+$"),
    re.compile(r"^api/proof/[\w.-]+/(approve|revision)$"),
)


def is_public(path: str) -> bool:
    """Is this a path a client may reach with no Hub session?"""
    p = (path or "").strip("/")
    return any(rx.match(p) for rx in PUBLIC_PATTERNS)


def available() -> bool:
    """Is the renderer answering? Used by the tile and by diagnostics."""
    try:
        r = requests.get(f"{AD_BUILDER_URL}/healthz", timeout=3)
        return r.ok
    except requests.RequestException:
        return False


def status() -> dict:
    """Plain-language state, for /status and the proxy's own error page."""
    if not ADMIN_TOKEN:
        return {"ok": False, "detail":
                "ADBUILDER_ADMIN_TOKEN is not set, so the ad builder refuses "
                "its own internal routes. Set it (16+ characters) and redeploy."}
    if not available():
        return {"ok": False, "detail":
                f"No answer from the renderer at {AD_BUILDER_URL}. It runs as a "
                f"second process in this container; check the deploy log for "
                f"lines beginning [adbuilder]."}
    return {"ok": True, "detail": f"Renderer answering at {AD_BUILDER_URL}."}


# The renderer's write endpoints, and what each one is called in the activity
# log. The Display Ad Builder is the one module in this Hub that is not Python,
# so everything it does happens inside a TypeScript process that has never
# heard of hub/audit.py — which is why /api/integrity reported it as a module
# that never logs. The Hub-side joins (start, attach, save a logo) did log; the
# work itself — rendering a size set, delivering a pack, approving a proof —
# passed through this proxy and was recorded nowhere. Every one of those is
# creative a client receives.
#
# The proxy is the single point all of it passes through, which is the reason
# to put the log here rather than in the renderer: a route added in TypeScript
# next month cannot be silent, because anything that changes state and is not
# named below is still recorded, under its own path.
_ACTIONS = (
    (re.compile(r"^api/render/?$"), "ads_render_started", None),
    # Motion added to a set that was already built. Its own action rather than
    # folded into ads_render_started: it is a separate job on a separate day,
    # it produces files the static render did not, and a client record that
    # showed one entry for both could not say whether the animated versions on
    # the delivery were ever built here.
    (re.compile(r"^api/animate/([\w-]+)$"), "ads_animated", 1),
    (re.compile(r"^api/project/([\w.-]+)/deliver$"), "ads_delivered", 1),
    (re.compile(r"^api/project/([\w.-]+)/approve-size$"), "ads_size_approved", 1),
    (re.compile(r"^api/project/([\w.-]+)/override$"), "ads_override_saved", 1),
    (re.compile(r"^api/project/([\w.-]+)/clone$"), "ads_project_cloned", 1),
    (re.compile(r"^api/project/([\w.-]+)/note$"), "ads_note_added", 1),
    (re.compile(r"^api/proof/([\w.-]+)/rebuild$"), "ads_proof_rebuilt", 1),
    (re.compile(r"^api/proof/([\w.-]+)/(approve|revision)$"), "ads_proof_decision", 1),
    (re.compile(r"^api/requests/([\w-]+)/choose-template$"), "ads_template_chosen", 1),
)


def _record(method: str, path: str, status: int, actor: str) -> None:
    """File one proxied write in the Hub's activity log.

    Reads only the status line, never the body: the response is streamed
    straight to the browser and consuming it here to find out which client the
    project belongs to would buffer a multi-megabyte ad pack in memory. So the
    entry carries the project id and not the client name — hub/ad_builder_link
    logs the client at the two points it actually knows one, when a build is
    started for them and when the finished creative is filed onto their record.

    Never raises. A proxy that fails because logging failed would take the
    whole tool down for the sake of a line in a file.
    """
    if method in ("GET", "HEAD", "OPTIONS") or not (200 <= int(status) < 300):
        return
    clean = (path or "").split("?", 1)[0].strip("/")
    action, ref = "ads_" + (clean.replace("/", "_") or "request"), ""
    for pattern, name, group in _ACTIONS:
        m = pattern.match(clean)
        if m:
            action = name
            ref = m.group(group) if group else ""
            break
    try:
        from hub import audit
        audit.log("display_ads", action, actor=actor or None,
                  ref=ref or None, path=clean)
    except Exception:                                 # noqa: BLE001
        logger.warning("display_ads: could not record %s %s", method, clean)


def register(app, url_prefix: str = "/tools/display-ads") -> None:
    """Mount the proxy on the Hub app.

    A blueprint on the hub app rather than a DispatcherMiddleware mount,
    because there is no WSGI app to mount — the upstream is a separate process
    reached over HTTP.
    """
    from flask import Blueprint

    bp = Blueprint("display_ads", __name__, url_prefix=url_prefix)

    @bp.route("/", defaults={"path": ""},
              methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @bp.route("/<path:path>",
              methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    def proxy(path: str):
        # Hub login. Imported here rather than at module scope because
        # hub/__init__ imports this module while it is still being defined.
        from hub import current_user
        if not (current_user() or is_public(path)):
            from flask import redirect, url_for
            return redirect(f"/login?next={request.path}")

        # A public proof needs no token of ours, so it must not meet a page of
        # ours explaining that one is missing: that text is addressed to
        # whoever can set an environment variable, and a client is not them.
        if not ADMIN_TOKEN and not is_public(path):
            return _explain(
                "The Display Ad Builder isn't configured yet",
                "ADBUILDER_ADMIN_TOKEN is not set, so the renderer refuses "
                "every internal request. Set it to a random string of at least "
                "16 characters and redeploy."), 503

        # The renderer's own root is a JSON status document, which is the
        # right thing for a service being health-checked and the wrong thing
        # for someone who just clicked a tile. Send them to the builder.
        if not path:
            from flask import redirect
            return redirect(f"{url_prefix}/build")

        target = f"{AD_BUILDER_URL}/{path}"
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in _DROP_REQUEST}
        # The token is added here, not carried by the browser. It never reaches
        # the page, so it cannot leak through the address bar, history or a
        # referrer header.
        #
        # And it is added only for somebody signed in. A public proof request
        # forwarded WITH it would tell the renderer a client is staff -- which
        # is precisely the question the renderer asks to decide whether to draw
        # the live editor on that page. Attaching our own credential to an
        # anonymous request is how a public page quietly gains an operator's
        # controls, with every screen looking healthy.
        signed_in = bool(current_user())
        if signed_in:
            headers["X-Admin-Token"] = ADMIN_TOKEN
            # The intake code guards the client-facing form; staff coming
            # through the Hub have already authenticated, so supply it for them.
            intake = os.environ.get("INTAKE_CODE", "").strip()
            if intake:
                headers["X-Intake-Code"] = intake
        else:
            # Nothing of ours travels with an anonymous request -- including a
            # header of that name the caller supplied themselves. Matched
            # case-insensitively, because "x-admin-token" and "X-Admin-Token"
            # are one header to every HTTP implementation and would be two
            # keys to a dict.
            _mine = {"x-admin-token", "x-intake-code", "x-s1-user"}
            headers = {k: v for k, v in headers.items() if k.lower() not in _mine}
        # So the upstream builds links back through the Hub rather than to
        # its own loopback address.
        headers["X-Forwarded-Prefix"] = url_prefix
        # Who is signed in. The renderer has no login of its own, so without
        # this an approval is recorded against nobody -- and "who signed this
        # size off" is the only question an approval exists to answer.
        # ASCII only and bounded: a header carrying anything else raises
        # UnicodeEncodeError inside requests, and losing the whole proxied
        # request over a name with an accent in it would be absurd.
        who = (current_user() or "").encode("ascii", "ignore").decode()[:120].strip()
        if who and signed_in:
            headers["X-S1-User"] = who

        try:
            upstream = requests.request(
                request.method, target,
                params=request.args,
                data=request.get_data(),
                headers=headers,
                timeout=TIMEOUT,
                stream=True,
                allow_redirects=False,
            )
        except requests.ConnectionError:
            return _explain(
                "The Display Ad Builder isn't running",
                "It runs as a second process in this container and is not "
                "answering. Everything else in the Hub is unaffected. The "
                "deploy log records why, on lines beginning [adbuilder]."), 503
        except requests.Timeout:
            return _explain(
                "That took too long",
                "Rendering a full ad package can take a couple of minutes. If "
                "this keeps happening the renderer may be short of memory — "
                "see the note in hub/ad_builder_proxy.py about splitting it "
                "into its own service."), 504

        _record(request.method, path, upstream.status_code, who)

        out = Response(
            stream_with_context(upstream.iter_content(chunk_size=64 * 1024)),
            status=upstream.status_code,
        )
        for k, v in upstream.headers.items():
            if k.lower() in _DROP_RESPONSE:
                continue
            # Rewrite upstream redirects so they stay inside the Hub instead of
            # sending the browser to a loopback address it cannot reach.
            if k.lower() == "location" and v.startswith("/"):
                v = f"{url_prefix}{v}"
            out.headers[k] = v
        return out

    app.register_blueprint(bp)


def _explain(title: str, body: str) -> str:
    """A readable page rather than a bare 502.

    A proxy failure is the one error where the user has no way to guess the
    cause, so it says which process is missing and where to look.
    """
    return (
        "<html><body style=\"font-family:system-ui;padding:40px;max-width:760px\">"
        f"<h2 style='color:#1a2e58'>{title}</h2>"
        f"<p style='line-height:1.6;color:#41525f'>{body}</p>"
        "<p><a href='/tools'>Back to Tools</a> &middot; "
        "<a href='/status'>System Status</a></p></body></html>"
    )
