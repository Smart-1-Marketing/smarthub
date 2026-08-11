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
_HUB_BAR = (
    '<a href="/" style="position:fixed;bottom:14px;left:14px;z-index:99999;'
    "background:#1a2e58;color:#fff;padding:8px 14px;border-radius:20px;"
    "font:600 12.5px 'Segoe UI',system-ui,sans-serif;text-decoration:none;"
    'box-shadow:0 6px 18px rgba(0,0,0,.3)">&#8962; Smart 1 Hub</a>'
).encode()


class AuthGuard:
    """Blocks every request to a mounted module unless the hub cookie is valid."""

    def __init__(self, app, mount: str):
        self.app = app
        self.mount = mount

    def __call__(self, environ, start_response):
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
    """Injects a small floating 'back to Hub' chip into module HTML pages."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
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
        is_html = "text/html" in ctype and status.startswith("200")
        if not is_html:
            start_response(status, headers, captured.get("exc_info"))
            return result

        try:
            body = b"".join(result)
        finally:
            if hasattr(result, "close"):
                result.close()
        if b"</body>" in body:
            body = body.replace(b"</body>", _HUB_BAR + b"</body>", 1)
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

img, img_fb = _try_load("img_app", ("modules", "image_optimizer", "app.py"), "Image Optimizer")
pdf, pdf_fb = _try_load("pdf_app", ("modules", "pdf_optimizer", "app.py"), "PDF Optimizer")
suite, suite_fb = _try_load("suite_app", ("modules", "suite_panel", "app.py"), "Suite Control Panel")


def _mount(flask_app, prefix):
    return AuthGuard(HubBar(flask_app), prefix)


application = DispatcherMiddleware(hub_app, {
    "/google": _mount(gf.app, "/google") if gf else gf_fb,
    "/sites": _mount(sites.app, "/sites") if sites else sites_fb,
    "/suite": _mount(suite.app, "/suite") if suite else suite_fb,
    "/tools/image": _mount(img.app, "/tools/image") if img else img_fb,
    "/tools/pdf": _mount(pdf.app, "/tools/pdf") if pdf else pdf_fb,
})
application = ProxyFix(application, x_for=1, x_proto=1, x_host=1)


if __name__ == "__main__":
    from werkzeug.serving import run_simple
    run_simple("0.0.0.0", int(os.environ.get("PORT", "8000")), application,
               use_reloader=True, use_debugger=False)
