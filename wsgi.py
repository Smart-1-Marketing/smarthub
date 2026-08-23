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
from hub.sidebar import render_sidebar

_MOUNT_ACTIVE = {
    "/google": "google", "/sites": "sites", "/suite": "suite",
    "/scans": "scans",
    # Both point at the one sidebar entry: /sales/proposals is the retired
    # standalone builder and redirects to /sales/builder.
    "/sales/builder": "salesb", "/sales/proposals": "salesb",
    "/tools/image": "tools", "/tools/pdf": "tools", "/tools/seo-images": "tools",
    "/tools/image-creator": "tools", "/tools/bg-remover": "tools",
    "/tools/utm": "tools",
    "/tools/io": "io_builder",
    "/tools/radio-promo": "radio_promo",
    "/tools/landing-ads": "landing_ads",
    "/tools/calculators": "calculators",
    "/tools/fan-radio": "fan_radio",
    "/tools/google-access": "google_access",
    "/tools/image-picker": "image_picker",
    "/tools/page-images": "page_image_optimizer",
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
        environ["s1hub.user"] = user
        return self.app(environ, start_response)


class HubBar:
    """Injects the shared Hub sidebar + theme into module HTML pages."""

    def __init__(self, app, active="", bare_prefixes=()):
        self.app = app
        self.active = active
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
        _THEME = (b'<link rel="stylesheet" href="/assets/theme.css">'
                  b'<link rel="stylesheet" href="/hub-help.css">')
        if b"</head>" in body:
            body = body.replace(b"</head>", _THEME + b"</head>", 1)
        elif b"<body" in body:
            body = _THEME + body
        _bar = render_sidebar(self.active)
        _scripts = (b'<script defer src="/hub-help.js"></script>'
                    b'<script defer src="/hub-demo.js"></script>'
                    b'<script defer src="/hub-crumbs.js"></script>'
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
                            bare_prefixes=public_prefixes),
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

application = DispatcherMiddleware(hub_app, {
    "/google": _mount(gf.app, "/google") if gf else gf_fb,
    "/sites": _mount(sites.app, "/sites") if sites else sites_fb,
    "/suite": _mount(suite.app, "/suite") if suite else suite_fb,
    # The widget is embedded on other people's websites, so its pages and its
    # public API routes sit outside the Hub login — as does /r/<token>, the
    # unguessable link a converted lead opens their own report on.
    "/scans": _mount(scans.app, "/scans",
                     public_prefixes=_SCANS_PUBLIC) if scans else scans_fb,
    "/sales/builder": _mount(salesb.app, "/sales/builder") if salesb else salesb_fb,
    "/sales/proposals": _mount(propb.app, "/sales/proposals") if propb else propb_fb,
    "/tools/seo-images": _mount(seoimg.app, "/tools/seo-images") if seoimg else seoimg_fb,
    "/tools/image-creator": _mount(imgcreator.app, "/tools/image-creator") if imgcreator else imgcreator_fb,
    "/tools/bg-remover": _mount(bgrem.app, "/tools/bg-remover") if bgrem else bgrem_fb,
    "/tools/utm": _mount(utm.app, "/tools/utm") if utm else utm_fb,
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
    "/tools/fan-radio": _mount(fanrad.app, "/tools/fan-radio") if fanrad else fanrad_fb,
})
from hub import errors as _errors
application = _errors.ErrorMirror(application)
application = ProxyFix(application, x_for=1, x_proto=1, x_host=1)


if __name__ == "__main__":
    from werkzeug.serving import run_simple
    run_simple("0.0.0.0", int(os.environ.get("PORT", "8000")), application,
               use_reloader=True, use_debugger=False)
