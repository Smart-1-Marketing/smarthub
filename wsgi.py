"""Smart 1 Hub — application composition.

One process, one login, six tools:

    /                → Hub shell (dashboard, Client 360, activity, status)
    /clients         → Knack Creative/Client Lookup (prebuilt React app)
    /static, /data   → served for the Clients app (its bundle expects root paths)
    /google/…        → Google Finder (GA4 / GTM / GSC / GMB)
    /sites/…         → Smart 1 Sites (Simvoly) admin
    /suite/…         → Smart 1 Suite (GHL) control panel  [Python port]
    /tools/image/…   → Image optimizer
    /tools/pdf/…     → PDF optimizer                       [Python port]
    /tools/smartforecast/… → Weather-triggered website personalization

Every mounted module sits behind the Hub auth guard: no valid hub cookie,
no access — pages redirect to /login, API paths get a 401 JSON.
"""
import importlib.util
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)                                        # `hub` package
sys.path.insert(0, os.path.join(ROOT, "modules", "sites_admin"))     # its flat imports (config, db, …)
sys.path.insert(0, os.path.join(ROOT, "modules", "image_optimizer")) # `optimizer`

from werkzeug.middleware.dispatcher import DispatcherMiddleware
from werkzeug.middleware.proxy_fix import ProxyFix

from hub import auth, create_hub_app


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- middleware
from hub.sidebar import render_sidebar, collapses_by_default


def _session(environ) -> dict:
    """The signed account session, or {} for a shared-password session."""
    try:
        from hub.users_routes import session_from_environ
        return session_from_environ(environ)
    except Exception:                         # noqa: BLE001
        return {}


def _viewer_is_admin(environ) -> bool:
    """Admin or General, from the signed account cookie alone.

    Used only to decide whether the injected sidebar shows the Utilities
    section. Three ways to answer no role at all, and each is deliberately an
    Admin nav rather than a General one: a shared-password session (which
    hub/access.py treats as Admin anyway), a cookie this process cannot
    verify, and any error. A nav that hides Diagnostics from an admin whenever
    a lookup hiccups is a bug nobody reports as one; the reverse shows a
    General user a link they will be refused, which is visible and harmless.
    """
    data = _session(environ)
    if not data:
        return True                           # shared password, or no account
    return data.get("r") in ("admin", "super_admin")


def _owes_password_change(environ) -> bool:
    """Is this session still on the password somebody else chose for it?

    The hub app has its own `before_request` for this, and that covered the
    hub's routes and nothing else — so a person who owed a password change
    could sidestep the whole thing by opening /tools/social/ instead of the
    dashboard. Twenty mounted modules, all of them reachable, and the Users
    panel still showing the "must change password" pill against their name.

    Read from the signed cookie rather than the database because this runs in
    front of every module request. It cannot go stale: setting a starting
    password and changing one both bump the session epoch, which invalidates
    the cookie carrying the old answer.
    """
    return bool(_session(environ).get("c"))


_MOUNT_ACTIVE = {
    "/google": "google", "/sites": "sites", "/suite": "suite",
    "/scans": "scans",
    # Both point at the one sidebar entry: /sales/proposals is the retired
    # standalone builder and redirects to /sales/builder.
    "/sales/builder": "salesb", "/sales/proposals": "salesb",
    "/tools/image": "tools", "/tools/pdf": "tools", "/tools/seo-images": "tools",
    "/tools/image-creator": "tools", "/tools/bg-remover": "tools",
    "/tools/utm": "tools",
    # Creative rather than Tools: it sources imagery for client work and its
    # tile sits on /creative beside Image Creator.
    "/tools/stock-photos": "creative",
    "/tools/site-blocks": "tools",
    "/tools/smartforecast": "tools",
    # Creative, not Tools: it produces client-facing copy and pulls from the
    # image gallery, so it sits with Image Creator rather than with the
    # housekeeping utilities.
    "/tools/social": "creative",
    # Creative for the same reason: it produces the ad copy and the square that
    # a client's campaign runs on, and it reads their brand kit and gallery.
    "/tools/gpt-ads": "creative",
    "/tools/io": "io_builder",
    "/tools/radio-promo": "radio_promo",
    "/tools/landing-ads": "landing_ads",
    "/tools/calculators": "calculators",
    "/tools/fan-radio": "fan_radio",
    "/tools/google-access": "google_access",
    "/tools/image-picker": "image_picker",
    "/tools/page-images": "page_image_optimizer",
    # Its own sidebar entry rather than "tools": this one operates a
    # client's live Google Ads account, and a page that can enable
    # spend should say where it is in the nav.
    "/tools/ads": "ads",
}


class AuthGuard:
    """Blocks every request to a mounted module unless the hub cookie is valid.

    ``public_prefixes`` lists mount-relative path prefixes that skip the Hub
    cookie check — used for server-to-server callbacks (e.g. Insites POSTing a
    finished audit) and public embed endpoints, which authenticate with their
    own shared-secret token inside the module instead.
    """

    def __init__(self, app, mount: str, public_prefixes=None):
        self.app = app
        self.mount = mount
        self.public_prefixes = tuple(public_prefixes or ())

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "") or "/"
        if self.public_prefixes and path.startswith(self.public_prefixes):
            return self.app(environ, start_response)
        user = auth.user_from_environ(environ)
        if user is None:
            path = environ.get("PATH_INFO", "") or "/"
            wants_json = "/api/" in path or path.startswith("/api") or \
                "application/json" in environ.get("HTTP_ACCEPT", "")
            if wants_json or environ.get("REQUEST_METHOD") not in ("GET", "HEAD"):
                body = b'{"error": "Not authenticated. Please log in to the Hub."}'
                start_response("401 Unauthorized", [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                ])
                return [body]
            target = "/login?next=" + (self.mount + path)
            start_response("302 Found", [("Location", target), ("Content-Length", "0")])
            return [b""]
        if _owes_password_change(environ):
            # /account is a hub route, so this is a redirect out of the mount
            # rather than something the module can serve. A JSON caller gets
            # 403 with the destination named: a fetch that followed a redirect
            # into an HTML page would report malformed data, not a gate.
            path = environ.get("PATH_INFO", "") or "/"
            if "/api/" in path or path.startswith("/api") or \
                    "application/json" in environ.get("HTTP_ACCEPT", ""):
                body = (b'{"error": "Set a new password before using the Hub.",'
                        b' "redirect": "/account"}')
                start_response("403 Forbidden", [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                ])
                return [body]
            start_response("302 Found", [("Location", "/account?first=1"),
                                         ("Content-Length", "0")])
            return [b""]
        environ["s1hub.user"] = user
        # An hour spent in Smart 1 Ads is an hour signed in. Without this the
        # headcount on the dashboard would only ever see hub pages, and
        # somebody working in a mounted module all morning would drop out of
        # it fifteen minutes in — a wrong number that looks exactly like a
        # right one. Throttled to a dict lookup on almost every request, and
        # it pushes the hub app's context itself because middleware has none.
        try:
            from hub import presence as _presence
            _presence.touch_from_environ(environ, hub_app)
        except Exception:  # noqa: BLE001 — never cost a module request
            pass
        return self.app(environ, start_response)


class HubBar:
    """Injects the shared Hub sidebar + theme into module HTML pages."""

    def __init__(self, app, active="", bare_prefixes=(), mount=""):
        self.app = app
        self.active = active
        # The mount prefix, so the nav can be asked whether this is a
        # creative tool. DispatcherMiddleware strips the prefix off PATH_INFO
        # before the module ever sees it, so a module-relative path alone
        # cannot answer that -- "/" is the front page of twenty different
        # tools. Carried explicitly rather than read off SCRIPT_NAME because
        # _mount() already knows it and a middleware that depends on the
        # composition above it is one that breaks quietly when it moves.
        self.mount = mount or ""
        # Routes that must never receive Hub chrome. Anything served outside
        # the Hub login is by definition being looked at by someone who is not
        # staff — a prospect on a client's website. Injecting the staff sidebar
        # and the feedback tab there leaks internal navigation onto a third
        # party's domain and makes the embed look broken. Sec-Fetch-Dest below
        # catches the iframe case but not the hosted landing-page case.
        self.bare_prefixes = tuple(bare_prefixes or ())

    def __call__(self, environ, start_response):
        if self.bare_prefixes:
            path = environ.get("PATH_INFO", "") or "/"
            if path.startswith(self.bare_prefixes):
                return self.app(environ, start_response)
        captured = {}

        def _start(status, headers, exc_info=None):
            captured["status"] = status
            captured["headers"] = headers
            captured["exc_info"] = exc_info
            return lambda *_: None  # write() unused

        result = self.app(environ, _start)
        status = captured.get("status", "500 Internal Server Error")
        headers = captured.get("headers", [])
        ctype = next((v for k, v in headers if k.lower() == "content-type"), "")
        is_html = "text/html" in ctype
        if not is_html:
            start_response(status, headers, captured.get("exc_info"))
            return result

        try:
            body = b"".join(result)
        finally:
            if hasattr(result, "close"):
                result.close()

        # A page loaded inside an iframe must not get the sidebar: Sales
        # Builder frames /tools/io/, so injecting chrome there puts a full
        # navigation column inside a frame on a page that already has one.
        # ?embed=1 is the opt-out, and Sec-Fetch-Dest catches browsers that
        # send it without needing the caller to remember.
        qs = environ.get("QUERY_STRING", "")
        if ("embed=1" in qs
                or environ.get("HTTP_SEC_FETCH_DEST") in ("iframe", "frame")):
            start_response(status, headers, captured.get("exc_info"))
            return [body]
        # The help/demo layer has to be injected here, not put in base.html:
        # every module is a standalone Flask app mounted through
        # DispatcherMiddleware with its own <html>, so none of them inherit the
        # hub's base template. Injecting alongside the sidebar means all 20
        # tools get bubbles and walkthroughs without touching 20 templates.
        # hub-detail.css beside theme.css: theme.css is typography and
        # colour, hub-detail.css is the shape of a record page. A module
        # that adopts the s1d- class names therefore needs no stylesheet
        # of its own, and cannot drift from the page the look came from.
        _THEME = (b'<link rel="stylesheet" href="/assets/theme.css">'
                  b'<link rel="stylesheet" href="/assets/hub-detail.css">'
                  b'<link rel="stylesheet" href="/hub-help.css">')
        if b"</head>" in body:
            body = body.replace(b"</head>", _THEME + b"</head>", 1)
        elif b"<body" in body:
            body = _THEME + body
        # The nav a module page gets is the nav for whoever is looking at it.
        # The role is read from the signed session cookie rather than from the
        # database, because this is middleware with no app context and no
        # request: a DB read here would need one per module page. That is fine
        # for *chrome* -- the cookie is signed, so the role in it cannot be
        # edited -- and it is deliberately not the gate. The gate re-reads the
        # row (hub/access.py, hub/__init__.py), so a role changed a minute ago
        # takes effect on the click even if a stale nav is still on screen.
        # A creative tool opens with the nav as an icon rail: it is a
        # workbench -- a canvas, a storyboard, a gallery of squares -- and the
        # nav is 224px of a laptop the work needs. One list, in hub/sidebar.py,
        # read here and by the hub app's own injector, because two prefix
        # lists is how one tool comes to behave differently from the tool
        # beside it. A stored preference still wins in both directions.
        _full = self.mount + (environ.get("PATH_INFO", "") or "")
        _bar = render_sidebar(self.active, is_admin=_viewer_is_admin(environ),
                              collapsed_default=collapses_by_default(_full))
        _scripts = (b'<script defer src="/hub-help.js"></script>'
                    b'<script defer src="/hub-demo.js"></script>'
                    b'<script defer src="/hub-crumbs.js"></script>'
                    b'<script defer src="/hub-thinking.js"></script>'
                    b'<script defer src="/hub-autofill.js"></script>'
                    b'<script defer src="/hub-accordion.js"></script>')
        # The LAST </body>, not the first. A module page that builds a printable
        # document in JavaScript carries a whole `<html>...</body></html>`
        # string inside its own script -- the IO Builder builds two of them --
        # and injecting at the first match dropped the sidebar markup, a
        # stylesheet and five <script> tags into the middle of a template
        # literal. The injected <script> closed the page's own script block
        # early, so everything after it parsed as HTML: the IO Builder's entire
        # interview died with "Unexpected identifier" before drawing a single
        # question. The document's real closing tag is the last one.
        cut = body.rfind(b"</body>")
        if cut >= 0:
            body = body[:cut] + _bar + _scripts + body[cut:]
        else:
            body += _bar + _scripts
        headers = [(k, v) for k, v in headers if k.lower() != "content-length"]
        headers.append(("Content-Length", str(len(body))))
        start_response(status, headers, captured.get("exc_info"))
        return [body]


# ---------------------------------------------------------------- load modules
hub_app = create_hub_app()


def _fallback_app(label: str, reason: str):
    """Tiny stand-in served when a module fails to load — the rest of the
    Hub keeps working and the problem is stated plainly on the page."""
    def wsgi_app(environ, start_response):
        body = (
            f"<html><body style=\"font-family:system-ui;padding:40px;color:#1e293b\">"
            f"<h2 style='color:#1a2e58'>{label} is unavailable</h2>"
            f"<p>This module failed to start. Reason:</p>"
            f"<pre style='background:#f4f6fa;padding:14px;border-radius:8px;white-space:pre-wrap'>{reason}</pre>"
            f"<p>Fix the configuration (see <a href='/status'>System Status</a>) and redeploy.</p>"
            f"</body></html>"
        ).encode()
        start_response("503 Service Unavailable", [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ])
        return [body]
    return wsgi_app


def _try_load(name, relpath, label):
    try:
        return _load(name, os.path.join(ROOT, *relpath)), None
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return None, _fallback_app(label, str(exc))


gf, gf_fb = _try_load("gf_app", ("modules", "google_finder", "app.py"), "Google Finder")
if gf:
    gf.app.config.update(SESSION_COOKIE_NAME="gf_session", SESSION_COOKIE_PATH="/google")

sites, sites_fb = _try_load("sites_app", ("modules", "sites_admin", "app.py"), "Smart 1 Sites Admin")
if sites:
    sites.app.config.update(SESSION_COOKIE_NAME="sites_session", SESSION_COOKIE_PATH="/sites")

    @sites.app.before_request
    def _sites_hub_autologin():
        """Hub auth replaces the Sites admin's own login screen; also surface
        a friendly page (not a traceback) while its database is unreachable."""
        from flask import request as _rq
        from flask import session as _s
        if getattr(sites, "DB_BOOT_ERROR", None) and _rq.path not in ("/health", "/healthz"):
            return (
                "<html><body style='font-family:system-ui;padding:40px'>"
                "<h2 style='color:#1a2e58'>Sites database not ready</h2>"
                f"<p>{sites.DB_BOOT_ERROR}</p>"
                "<p>Set <code>DATABASE_URL</code> and redeploy. "
                "<a href='/status'>System Status</a></p></body></html>", 503,
            )
        user = _rq.environ.get("s1hub.user")
        if user and not _s.get("authenticated"):
            _s["authenticated"] = True
            _s.setdefault("csrf", secrets.token_urlsafe(24))

    @sites.app.errorhandler(Exception)
    def _sites_show_error(exc):
        """Internal tool behind Hub auth — show the real error instead of a
        blank 500 so problems (DB, API, config) are self-diagnosing."""
        import traceback
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return exc
        tb = traceback.format_exc()
        return (
            "<html><body style='font-family:system-ui;padding:40px;max-width:900px'>"
            "<h2 style='color:#1a2e58'>Sites module error</h2>"
            f"<p style='color:#dc2626;font-weight:600'>{type(exc).__name__}: {exc}</p>"
            f"<pre style='background:#f4f6fa;padding:14px;border-radius:8px;"
            f"white-space:pre-wrap;font-size:12px'>{tb}</pre>"
            "<p><a href='/sites/'>Back to Sites</a> · <a href='/status'>System Status</a></p>"
            "</body></html>", 500,
        )

img, img_fb = _try_load("img_app", ("modules", "image_optimizer", "app.py"), "Image Optimizer")
pdf, pdf_fb = _try_load("pdf_app", ("modules", "pdf_optimizer", "app.py"), "PDF Optimizer")
suite, suite_fb = _try_load("suite_app", ("modules", "suite_panel", "app.py"), "Suite Control Panel")
salesb, salesb_fb = _try_load("salesb_app", ("modules", "sales_builder", "app.py"), "Proposal Builder")

try:
    import importlib as _il
    propb = _il.import_module("modules.proposal_builder.app")
    propb_fb = None
except Exception as _pb_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    propb, propb_fb = None, _fallback_app("Proposal Builder archive", str(_pb_exc))

try:
    import importlib as _il_msa
    msa_app = _il_msa.import_module("modules.msa.app")
    msa_fb = None
except Exception as _exc_msa:  # noqa: BLE001
    msa_app, msa_fb = None, _fallback_app("MSA", str(_exc_msa))

try:
    import importlib as _il2
    scans = _il2.import_module("modules.scans.app")
    scans_fb = None
except Exception as _sc_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    scans, scans_fb = None, _fallback_app("Scans", str(_sc_exc))

try:
    import importlib as _il3
    seoimg = _il3.import_module("modules.seo_images.app")
    seoimg_fb = None
except Exception as _si_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    seoimg, seoimg_fb = None, _fallback_app("SEO Image Pipeline", str(_si_exc))

try:
    import importlib as _il4
    imgcreator = _il4.import_module("modules.image_creator.app")
    imgcreator_fb = None
except Exception as _ic_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    imgcreator, imgcreator_fb = None, _fallback_app("Image Creator", str(_ic_exc))

try:
    import importlib as _il5
    bgrem = _il5.import_module("modules.bg_remover.app")
    bgrem_fb = None
except Exception as _bg_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    bgrem, bgrem_fb = None, _fallback_app("Background Remover", str(_bg_exc))

try:
    import importlib as _il6
    utm = _il6.import_module("modules.utm_builder.app")
    utm_fb = None
except Exception as _utm_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    utm, utm_fb = None, _fallback_app("UTM Builder", str(_utm_exc))

try:
    import importlib as _il_stock
    stockp = _il_stock.import_module("modules.stock_photos.app")
    stockp_fb = None
except Exception as _stock_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    stockp, stockp_fb = None, _fallback_app("Stock Photo Search", str(_stock_exc))

try:
    import importlib as _il_siteblk
    siteblk = _il_siteblk.import_module("modules.site_blocks.app")
    siteblk_fb = None
except Exception as _siteblk_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    siteblk, siteblk_fb = None, _fallback_app("Website Blocks", str(_siteblk_exc))

try:
    import importlib as _il_smartforecast
    smartforecast = _il_smartforecast.import_module("modules.smartforecast.app")
    smartforecast_fb = None
except Exception as _smartforecast_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    smartforecast, smartforecast_fb = None, _fallback_app(
        "SmartForecast Dynamic Website", str(_smartforecast_exc))

try:
    import importlib as _il_social
    social = _il_social.import_module("modules.social_planner.app")
    social_fb = None
except Exception as _social_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    social, social_fb = None, _fallback_app("Social Content Planner", str(_social_exc))

try:
    import importlib as _il_gptads
    gptads = _il_gptads.import_module("modules.gpt_ads.app")
    gptads_fb = None
except Exception as _gptads_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    gptads, gptads_fb = None, _fallback_app("GPT Ads Builder", str(_gptads_exc))

try:
    import importlib as _il_adsb
    adsb = _il_adsb.import_module("modules.ads_builder.app")
    adsb_fb = None
except Exception as _adsb_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    adsb, adsb_fb = None, _fallback_app("Smart 1 Ads", str(_adsb_exc))

try:
    import importlib as _il_adsg
    adsg = _il_adsg.import_module("modules.ads_grader.app")
    adsg_fb = None
except Exception as _adsg_exc:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    adsg, adsg_fb = None, _fallback_app("Google Ads Grader", str(_adsg_exc))


def _install_error_reporter(flask_app, label):
    """Make every module's 500 name its own cause.

    Flask's stock 500 page says nothing, and the Hub error log only mirrors the
    response body — so when Site Scans and the SEO Image Pipeline broke on
    18 Aug the log recorded 600 characters of the injected stylesheet and not
    one word about the actual exception. Recording the traceback here, once for
    every mount, means the next failure is diagnosable from /status alone.

    Skipped for a module that already registers its own Exception handler
    (Sites does), so this never quietly replaces a module's own error page.
    """
    try:
        existing = flask_app.error_handler_spec.get(None, {}).get(None, {})
        if Exception in existing:
            return
    except Exception:  # noqa: BLE001 — a Flask version that shapes this
        pass           # differently just gets the handler installed

    @flask_app.errorhandler(Exception)
    def _report(exc):  # noqa: ANN001
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):     # 404/403/… are not faults
            return exc
        import traceback
        from flask import request as _rq
        actor = _rq.environ.get("s1hub.user") or ""
        try:
            from hub import errors as _err
            _err.log_exception(label, exc, path=_rq.path, actor=actor)
        except Exception:  # noqa: BLE001 — logging must never mask the error
            pass
        if not actor:
            # Unauthenticated callers (the Insites callback) get nothing to
            # read; the traceback is already in the log for us.
            return f"Module error — {label}", 500
        tb = traceback.format_exc()
        return (
            "<html><body style='font-family:system-ui;padding:40px;max-width:900px'>"
            f"<h2 style='color:#1a2e58'>Module error — {label}</h2>"
            f"<p style='color:#dc2626;font-weight:600'>{type(exc).__name__}: {exc}</p>"
            f"<pre style='background:#f4f6fa;padding:14px;border-radius:8px;"
            f"white-space:pre-wrap;font-size:12px'>{tb}</pre>"
            "<p><a href='/status'>System Status</a></p>"
            "</body></html>", 500,
        )


def _mount(flask_app, prefix, public_prefixes=None):
    # A module's Jinja environment is its own, so hub-level globals aren't
    # visible to it. Register the shared template helpers here rather than in
    # each module: without this, {{ help_dot(...) }} in a module template
    # raises UndefinedError and the page 500s.
    try:
        from hub.help_routes import install_template_helpers
        install_template_helpers(flask_app)
    except Exception:  # noqa: BLE001 — helpers are never load-bearing
        pass
    _install_error_reporter(flask_app, prefix)
    return AuthGuard(HubBar(flask_app, _MOUNT_ACTIVE.get(prefix, ""),
                            bare_prefixes=public_prefixes, mount=prefix),
                     prefix, public_prefixes=public_prefixes)



try:
    import importlib as _il_fanrad
    fanrad = _il_fanrad.import_module("modules.fan_radio.app")
    fanrad_fb = None
except Exception as _exc_fanrad:  # noqa: BLE001
    fanrad, fanrad_fb = None, _fallback_app("Fan Radio", str(_exc_fanrad))


try:
    import importlib as _il_radiop
    radiop = _il_radiop.import_module("modules.radio_promo.app")
    radiop_fb = None
except Exception as _exc_radiop:  # noqa: BLE001
    radiop, radiop_fb = None, _fallback_app("Radio Promo", str(_exc_radiop))

try:
    import importlib as _il_landads
    landads = _il_landads.import_module("modules.landing_ads.app")
    landads_fb = None
except Exception as _exc_landads:  # noqa: BLE001
    landads, landads_fb = None, _fallback_app("Landing Page Ads", str(_exc_landads))

try:
    import importlib as _il_iob
    iob = _il_iob.import_module("modules.io_builder.app")
    iob_fb = None
except Exception as _exc_iob:  # noqa: BLE001
    iob, iob_fb = None, _fallback_app("IO Builder", str(_exc_iob))

try:
    import importlib as _il_boat
    boat = _il_boat.import_module("modules.boat.app")
    boat_fb = None
except Exception as _exc_boat:  # noqa: BLE001
    boat, boat_fb = None, _fallback_app("Boat Landing", str(_exc_boat))

try:
    import importlib as _il_legal
    legal_app = _il_legal.import_module("modules.legal.app")
    legal_fb = None
except Exception as _exc_legal:  # noqa: BLE001
    legal_app, legal_fb = None, _fallback_app("Legal Landing", str(_exc_legal))

try:
    import importlib as _il_hvac
    hvac_app = _il_hvac.import_module("modules.hvac.app")
    hvac_fb = None
except Exception as _exc_hvac:  # noqa: BLE001
    hvac_app, hvac_fb = None, _fallback_app("Hvac Landing", str(_exc_hvac))

try:
    import importlib as _il_ski
    ski_app = _il_ski.import_module("modules.ski.app")
    ski_fb = None
except Exception as _exc_ski:  # noqa: BLE001
    ski_app, ski_fb = None, _fallback_app("Ski Landing", str(_exc_ski))

try:
    import importlib as _il_restaurant
    restaurant_app = _il_restaurant.import_module("modules.restaurant.app")
    restaurant_fb = None
except Exception as _exc_restaurant:  # noqa: BLE001
    restaurant_app, restaurant_fb = None, _fallback_app("Restaurant Landing", str(_exc_restaurant))

try:
    import importlib as _il_recruit
    recruit_app = _il_recruit.import_module("modules.recruit.app")
    recruit_fb = None
except Exception as _exc_recruit:  # noqa: BLE001
    recruit_app, recruit_fb = None, _fallback_app("Recruit Landing", str(_exc_recruit))

try:
    import importlib as _il_tour
    tourism_app = _il_tour.import_module("modules.tourism.app")
    tourism_fb = None
except Exception as _exc_tour:  # noqa: BLE001
    tourism_app, tourism_fb = None, _fallback_app("Tourism Landing", str(_exc_tour))

try:
    import importlib as _il_rv
    rv_app = _il_rv.import_module("modules.rv.app")
    rv_fb = None
except Exception as _exc_rv:  # noqa: BLE001
    rv_app, rv_fb = None, _fallback_app("RV Landing", str(_exc_rv))

try:
    import importlib as _il_std
    stadium_app = _il_std.import_module("modules.stadium.app")
    stadium_fb = None
except Exception as _exc_std:  # noqa: BLE001
    stadium_app, stadium_fb = None, _fallback_app("Stadium Landing", str(_exc_std))

# Public (login-exempt) routes under /scans, read from the module itself so the
# mount and the module can never disagree about what is public.
_SCANS_PUBLIC = tuple(getattr(scans, "PUBLIC_PREFIXES", ("/api/callback",))) \
    if scans else ("/api/callback",)

# Same arrangement for Smart 1 Ads: /tools/ads/estimate/<token> is the campaign
# estimate a CLIENT opens, and a client has no Hub login. Read from the module
# so the two halves cannot drift, and passed to _mount, which hands it to both
# AuthGuard (reachable) and HubBar (no sidebar, help layer or feedback tab in a
# document a prospect reads).
_ADS_PUBLIC = tuple(getattr(adsb, "PUBLIC_PREFIXES", ("/estimate/",))) \
    if adsb else ("/estimate/",)

# The grader is a lead magnet: every page of it is served to a stranger, and
# there is no staff screen in it at all. Read from the module rather than
# restated here, so the mount and the module cannot disagree.
_ADS_GRADER_PUBLIC = tuple(getattr(adsg, "PUBLIC_PREFIXES", ("/",))) if adsg else ("/",)

# And for the Proposal Builder: /sales/builder/p/<token> is the proposal a
# CLIENT opens and accepts, and a client has no Hub login. Read from the module
# so the mount and the module cannot drift, and handed to both AuthGuard
# (reachable) and HubBar (no sidebar, help layer or feedback tab on a document
# a client reads).
_SALESB_PUBLIC = tuple(getattr(salesb, "PUBLIC_PREFIXES", ("/p/", "/api/p/"))) \
    if salesb else ("/p/", "/api/p/")

# And for the Social Content Planner: /tools/social/c/<token>/… is the four
# pages a CLIENT opens — send us something to post, swipe on ideas, approve a
# post, say what to write about — and a client has no Hub login. Read from the
# module so the mount and the module cannot drift, and handed to both
# AuthGuard (reachable) and HubBar (no sidebar, help layer or feedback tab on
# a page a client reads).
_SOCIAL_PUBLIC = tuple(getattr(social, "PUBLIC_PREFIXES", ("/c/",))) \
    if social else ("/c/",)

# And for Fan Radio: /tools/fan-radio/r/<token> is the page a CLIENT opens to
# listen to their spots and approve them, /api/public/<token> is what that
# page fetches and posts its approval to, and /audio/<name> is the render it
# plays when Cloudinary is not configured. A client has no Hub login, so
# without this the approval link mails a customer a staff sign-in form for an
# account they will never have -- and the module's own docstring has said
# these three are the customer's since the day it was written. Read from the
# module so the mount and the module cannot drift, and handed to both
# AuthGuard (reachable) and HubBar (no sidebar, help layer or feedback tab on
# a page a client reads).
_FANRAD_PUBLIC = tuple(getattr(fanrad, "PUBLIC_PREFIXES",
                               ("/r/", "/api/public/", "/audio/"))) \
    if fanrad else ("/r/", "/api/public/", "/audio/")
_SMARTFORECAST_PUBLIC = tuple(getattr(
    smartforecast, "PUBLIC_PREFIXES", ("/embed/", "/api/public/"))) \
    if smartforecast else ("/embed/", "/api/public/")

application = DispatcherMiddleware(hub_app, {
    "/google": _mount(gf.app, "/google") if gf else gf_fb,
    "/sites": _mount(sites.app, "/sites") if sites else sites_fb,
    "/suite": _mount(suite.app, "/suite") if suite else suite_fb,
    # The widget is embedded on other people's websites, so its pages and its
    # public API routes sit outside the Hub login — as does /r/<token>, the
    # unguessable link a converted lead opens their own report on.
    "/scans": _mount(scans.app, "/scans",
                     public_prefixes=_SCANS_PUBLIC) if scans else scans_fb,
    "/sales/builder": _mount(salesb.app, "/sales/builder",
                             public_prefixes=_SALESB_PUBLIC) if salesb else salesb_fb,
    "/sales/proposals": _mount(propb.app, "/sales/proposals") if propb else propb_fb,
    "/tools/seo-images": _mount(seoimg.app, "/tools/seo-images") if seoimg else seoimg_fb,
    "/tools/image-creator": _mount(imgcreator.app, "/tools/image-creator") if imgcreator else imgcreator_fb,
    "/tools/bg-remover": _mount(bgrem.app, "/tools/bg-remover") if bgrem else bgrem_fb,
    "/tools/utm": _mount(utm.app, "/tools/utm") if utm else utm_fb,
    # Searches the three free stock libraries and our own Cloudinary folders in
    # one pass. Every source degrades to "not configured" by name rather than
    # to an empty grid, so a missing key reads as a setting rather than a fault.
    "/tools/stock-photos": _mount(stockp.app, "/tools/stock-photos")
                           if stockp else stockp_fb,
    "/tools/site-blocks": _mount(siteblk.app, "/tools/site-blocks")
                          if siteblk else siteblk_fb,
    # The editor, simulator and history stay behind Hub login. The iframe and
    # its read-only JSON payload are public because they render on a client's
    # own website; neither public route can change configuration or state.
    "/tools/smartforecast": _mount(
        smartforecast.app, "/tools/smartforecast",
        public_prefixes=_SMARTFORECAST_PUBLIC) if smartforecast else smartforecast_fb,
    "/tools/social": _mount(social.app, "/tools/social",
                            public_prefixes=_SOCIAL_PUBLIC) if social else social_fb,
    "/tools/gpt-ads": _mount(gptads.app, "/tools/gpt-ads") if gptads else gptads_fb,
    # Google Ads campaign operations. With no GOOGLE_ADS_* credentials it
    # still serves its own settings page naming each variable it is
    # missing, so an unconnected account reads as "not connected"
    # rather than as a broken tool.
    "/tools/ads": _mount(adsb.app, "/tools/ads",
                         public_prefixes=_ADS_PUBLIC) if adsb else adsb_fb,
    # A prospect connects their OWN Google Ads account read-only and gets a
    # score. Entirely public: the whole module is a lead magnet, and the only
    # credential it ever sees is an online-only access token that lives for
    # one request and is written nowhere.
    "/tools/ads-grader": _mount(adsg.app, "/tools/ads-grader",
                                public_prefixes=_ADS_GRADER_PUBLIC)
                         if adsg else adsg_fb,
    "/tools/image": _mount(img.app, "/tools/image") if img else img_fb,
    "/tools/pdf": _mount(pdf.app, "/tools/pdf") if pdf else pdf_fb,
    "/tools/io": _mount(iob.app, "/tools/io") if iob else iob_fb,
    # The client signing an agreement has no Hub login, so this is public in
    # the same way a landing page is. Writes on it are rate-limited in the
    # module itself, because "public" and "free to hammer" are not the same
    # thing when a signature costs a PDF render and a Cloudinary upload.
    "/msa": (AuthGuard(msa_app.app, "/msa", public_prefixes=("/",))
             if msa_app else msa_fb),
    # A landing page must not sit behind the Hub login — the whole page and
    # its lead endpoints are public, which is what public_prefixes=("/",) says.
    "/land/boat": (AuthGuard(boat.app, "/land/boat", public_prefixes=("/",))
                   if boat else boat_fb),
    "/land/stadium": (AuthGuard(stadium_app.app, "/land/stadium", public_prefixes=("/",))
                      if stadium_app else stadium_fb),
    "/land/rv": (AuthGuard(rv_app.app, "/land/rv", public_prefixes=("/",))
                 if rv_app else rv_fb),
    "/land/tourism": (AuthGuard(tourism_app.app, "/land/tourism", public_prefixes=("/",))
                      if tourism_app else tourism_fb),
    "/land/recruit": (AuthGuard(recruit_app.app, "/land/recruit", public_prefixes=("/",))
                       if recruit_app else recruit_fb),
    "/land/restaurant": (AuthGuard(restaurant_app.app, "/land/restaurant", public_prefixes=("/",))
                       if restaurant_app else restaurant_fb),
    "/land/ski": (AuthGuard(ski_app.app, "/land/ski", public_prefixes=("/",))
                  if ski_app else ski_fb),
    "/land/hvac": (AuthGuard(hvac_app.app, "/land/hvac", public_prefixes=("/",))
                    if hvac_app else hvac_fb),
    "/land/legal": (AuthGuard(legal_app.app, "/land/legal", public_prefixes=("/",))
                    if legal_app else legal_fb),
    "/tools/radio-promo": _mount(radiop.app, "/tools/radio-promo") if radiop else radiop_fb,
    "/tools/landing-ads": _mount(landads.app, "/tools/landing-ads") if landads else landads_fb,
    "/tools/fan-radio": _mount(fanrad.app, "/tools/fan-radio",
                               public_prefixes=_FANRAD_PUBLIC) if fanrad else fanrad_fb,
})
from hub import errors as _errors
application = _errors.ErrorMirror(application)
# X-Robots-Tag on every response in the composed app, mounted modules
# included. As a Flask after_request on the hub app it would have covered the
# hub's own pages and left twenty modules without it -- among them every
# public landing page, which is the only part of this Hub a crawler can
# actually reach. hub/no_crawl.py says what the header is and why robots.txt
# alone is not enough.
from hub.no_crawl import NoIndex as _NoIndex
application = _NoIndex(application)
application = ProxyFix(application, x_for=1, x_proto=1, x_host=1)


if __name__ == "__main__":
    from werkzeug.serving import run_simple
    run_simple("0.0.0.0", int(os.environ.get("PORT", "8000")), application,
               use_reloader=True, use_debugger=False)
