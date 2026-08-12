"""Smart 1 Hub — the shell application.

Owns: login/logout, dashboard, Client 360, Tools landing, Activity, Status,
plus serving the prebuilt Knack "Clients" app (which expects /static and
/data at the site root, so the hub serves those paths for it).
"""
import os
import shutil
import subprocess

import requests as _rq
from flask import (
    Flask, jsonify, make_response, redirect, render_template, request,
    send_from_directory,
)

from . import audit, auth, knack_data

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENTS_APP = os.path.join(ROOT, "clients_app")

MODULES = [
    {"key": "clients", "label": "Clients", "href": "/clients", "tag": "Knack"},
    {"key": "google", "label": "Google", "href": "/google/", "tag": "GA4 · GTM"},
    {"key": "sites", "label": "Sites", "href": "/sites/", "tag": "Simvoly"},
    {"key": "suite", "label": "Suite", "href": "/suite/", "tag": "GHL"},
    {"key": "tools", "label": "Tools", "href": "/tools", "tag": ""},
]


def current_user():
    return auth.verify_cookie_value(request.cookies.get(auth.COOKIE_NAME))


def create_hub_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/assets",
    )
    app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024

    # ---------------- auth ----------------
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if current_user():
                return redirect(request.args.get("next") or "/")
            return render_template("login.html", next=request.args.get("next", "/"))

        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
        wait = auth.throttle_check(ip)
        if wait:
            return render_template(
                "login.html", next=request.form.get("next", "/"),
                error=f"Too many attempts. Try again in {max(1, wait // 60)} minute(s).",
            ), 429

        name = (request.form.get("name") or "").strip()
        password = request.form.get("password") or ""
        if not auth.panel_password():
            return render_template("login.html", next=request.form.get("next", "/"),
                                   error="PANEL_PASSWORD is not configured on the server."), 500
        if not auth.check_password(password):
            auth.throttle_fail(ip)
            audit.log("hub", "login_failed", ip=ip)
            return render_template("login.html", next=request.form.get("next", "/"),
                                   error="Incorrect password."), 401

        auth.throttle_reset(ip)
        actor = name or "Unknown"
        audit.log("hub", "login_success", actor=actor, ip=ip)
        nxt = request.form.get("next") or "/"
        if not nxt.startswith("/"):
            nxt = "/"
        resp = make_response(redirect(nxt))
        resp.set_cookie(
            auth.COOKIE_NAME, auth.issue_cookie_value(actor),
            max_age=auth.SESSION_TTL_SECONDS, httponly=True, samesite="Lax",
            secure=os.environ.get("NODE_ENV") == "production" or os.environ.get("FLASK_ENV") == "production",
        )
        return resp

    @app.route("/logout")
    def logout():
        resp = make_response(redirect("/login"))
        resp.delete_cookie(auth.COOKIE_NAME)
        return resp

    def _require_page():
        """Redirect helper for HTML pages."""
        if not current_user():
            return redirect("/login?next=" + request.path)
        return None

    # ---------------- shell pages ----------------
    @app.route("/")
    def dashboard():
        gate = _require_page()
        if gate:
            return gate
        return render_template("dashboard.html", user=current_user(), modules=MODULES, active="dashboard")

    @app.route("/client360")
    def client360():
        gate = _require_page()
        if gate:
            return gate
        return render_template("client360.html", user=current_user(), modules=MODULES,
                               active="c360", q=request.args.get("q", ""))

    @app.route("/tools")
    def tools():
        gate = _require_page()
        if gate:
            return gate
        return render_template("tools.html", user=current_user(), modules=MODULES, active="tools")

    @app.route("/activity")
    def activity():
        gate = _require_page()
        if gate:
            return gate
        return render_template("activity.html", user=current_user(), modules=MODULES, active="activity")

    @app.route("/status")
    def status():
        gate = _require_page()
        if gate:
            return gate
        return render_template("status.html", user=current_user(), modules=MODULES, active="status")

    # ---------------- Clients app (prebuilt Knack lookup) ----------------
    # The React bundle was built with absolute /static/... and /data/... URLs,
    # so the hub serves those two prefixes straight from clients_app/.
    @app.route("/clients")
    @app.route("/clients/")
    def clients_index():
        gate = _require_page()
        if gate:
            return gate
        from .sidebar import render_sidebar
        with open(os.path.join(CLIENTS_APP, "index.html"), "rb") as fh:
            body = fh.read()
        snippet = b'<link rel="stylesheet" href="/assets/theme.css">'
        if b"</head>" in body:
            body = body.replace(b"</head>", snippet + b"</head>", 1)
        bar = render_sidebar("clients")
        body = body.replace(b"</body>", bar + b"</body>", 1) if b"</body>" in body else body + bar
        return app.response_class(body, mimetype="text/html")

    @app.route("/static/<path:filename>")
    def clients_static(filename):
        return send_from_directory(os.path.join(CLIENTS_APP, "static"), filename)

    @app.route("/data/<path:filename>")
    def clients_data(filename):
        if not current_user():
            return jsonify({"error": "Not authenticated."}), 401
        return send_from_directory(os.path.join(CLIENTS_APP, "data"), filename)

    # ---------------- QuickBooks connect / lookup ----------------
    @app.route("/qb/connect")
    def qb_connect():
        gate = _require_page()
        if gate:
            return gate
        from . import quickbooks as qb
        if not qb.configured():
            return ("QuickBooks is not configured — set QB_CLIENT_ID and QB_CLIENT_SECRET "
                    "(from developer.intuit.com) and redeploy. <a href='/status'>Status</a>", 400)
        return redirect(qb.authorize_url(request))

    @app.route("/qb/callback")
    def qb_callback():
        gate = _require_page()
        if gate:
            return gate
        from . import quickbooks as qb
        ok, msg = qb.handle_callback(request)
        audit.log("hub", "quickbooks_connected" if ok else "quickbooks_connect_failed",
                  actor=current_user(), detail=msg)
        return redirect("/status?qb=" + ("connected" if ok else "error"))

    @app.route("/qb/disconnect", methods=["POST", "GET"])
    def qb_disconnect():
        gate = _require_page()
        if gate:
            return gate
        from . import quickbooks as qb
        qb.disconnect()
        audit.log("hub", "quickbooks_disconnected", actor=current_user())
        return redirect("/status")

    @app.route("/api/qb/invoices")
    def api_qb_invoices():
        gate = _require_api()
        if gate:
            return gate
        from . import quickbooks as qb
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"configured": qb.configured(), "connected": qb.connected(), "customers": []})
        try:
            return jsonify(qb.lookup(q))
        except Exception as exc:  # noqa: BLE001 — Client 360 must degrade gracefully
            return jsonify({"configured": qb.configured(), "connected": qb.connected(),
                            "customers": [], "error": str(exc)})

    @app.route("/favicon.ico")
    def favicon():
        return ("", 204)

    @app.route("/health")
    @app.route("/healthz")
    def health():
        return jsonify({"status": "ok"})

    # ---------------- JSON APIs (hub-side) ----------------
    def _require_api():
        if not current_user():
            return jsonify({"error": "Not authenticated."}), 401
        return None

    @app.route("/api/summary")
    def api_summary():
        gate = _require_api()
        if gate:
            return gate
        try:
            data = knack_data.summary()
        except Exception as exc:  # noqa: BLE001 — dashboard must never 500
            data = {"error": str(exc)}
        return jsonify(data)

    @app.route("/api/c360")
    def api_c360():
        gate = _require_api()
        if gate:
            return gate
        q = request.args.get("q", "")
        try:
            groups = knack_data.search_client(q)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"groups": [], "error": str(exc)})
        return jsonify({"groups": groups})

    @app.route("/api/c360/sites")
    def api_c360_sites():
        """Best-effort search of the Simvoly admin's Postgres inventory."""
        gate = _require_api()
        if gate:
            return gate
        q = (request.args.get("q") or "").strip()
        dsn = os.environ.get("DATABASE_URL", "")
        if not q or not dsn:
            return jsonify({"results": [], "configured": bool(dsn)})
        try:
            import psycopg2
            import psycopg2.extras
            conn = psycopg2.connect(dsn)
            try:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        """
                        SELECT p.project_id, p.name, p.status,
                               (SELECT w.domain FROM websites w
                                 WHERE w.project_id = p.project_id AND w.domain IS NOT NULL
                                 LIMIT 1) AS domain
                          FROM projects p
                         WHERE p.name ILIKE %s
                            OR p.project_id IN (
                                 SELECT w2.project_id FROM websites w2
                                  WHERE w2.name ILIKE %s OR w2.domain ILIKE %s)
                         ORDER BY p.name LIMIT 6
                        """,
                        (f"%{q}%", f"%{q}%", f"%{q}%"),
                    )
                    rows = [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()
            return jsonify({"results": rows, "configured": True})
        except Exception as exc:  # noqa: BLE001
            return jsonify({"results": [], "configured": True, "error": str(exc)})

    @app.route("/api/activity")
    def api_activity():
        gate = _require_api()
        if gate:
            return gate
        limit = min(int(request.args.get("limit", 300) or 300), 1000)
        module = request.args.get("module") or None
        return jsonify({"entries": audit.read(limit=limit, module=module)})

    @app.route("/api/status")
    def api_status():
        gate = _require_api()
        if gate:
            return gate
        checks = []

        def add(name, status_, message):
            checks.append({"name": name, "status": status_, "message": message})

        # --- core config ---
        pw = auth.panel_password()
        if not pw:
            add("Panel password", "error", "PANEL_PASSWORD is not set — nobody can log in.")
        elif pw in ("change-me", "change-me-to-something-strong"):
            add("Panel password", "warn", "Still set to a placeholder — change it.")
        else:
            add("Panel password", "ok", "Configured.")
        add("Session secret", "ok" if os.environ.get("SECRET_KEY") or os.environ.get("SESSION_SECRET") else "warn",
            "Configured — logins survive restarts." if os.environ.get("SECRET_KEY") or os.environ.get("SESSION_SECRET")
            else "Not set — everyone is logged out on every restart/redeploy.")

        # --- Knack data ---
        age = knack_data.data_age_hours()
        if age is None:
            add("Smart 1 Team data", "error", "clients_app/data/products.json not found.")
        elif age > 48:
            add("Smart 1 Team data", "warn", f"Last refreshed {age / 24:.1f} days ago — run the refresh workflow.")
        else:
            add("Smart 1 Team data", "ok", f"Refreshed {age:.0f}h ago · {len(knack_data.products())} product rows · {len(knack_data.websites())} sites.")

        # --- GHL ---
        token, company = os.environ.get("GHL_PRIVATE_TOKEN"), os.environ.get("GHL_COMPANY_ID")
        if not token or not company:
            add("GoHighLevel API", "error", "GHL_PRIVATE_TOKEN and/or GHL_COMPANY_ID is not set.")
        else:
            try:
                r = _rq.get(
                    "https://services.leadconnectorhq.com/locations/search",
                    params={"companyId": company, "limit": "1"},
                    headers={"Authorization": f"Bearer {token}",
                             "Version": os.environ.get("GHL_API_VERSION", "2021-07-28"),
                             "Accept": "application/json"},
                    timeout=12,
                )
                add("GoHighLevel API", "ok" if r.ok else "error",
                    "Token is valid and can read sub-accounts." if r.ok else f"Token check failed (HTTP {r.status_code}).")
            except Exception as exc:  # noqa: BLE001
                add("GoHighLevel API", "error", f"Could not reach GHL: {exc}")

        # --- Simvoly ---
        skey = os.environ.get("SIMVOLY_API_KEY")
        if not skey:
            add("Smart 1 Sites Platform API", "warn", "SIMVOLY_API_KEY is not set — Smart 1 Sites module runs limited/mock.")
        else:
            base = os.environ.get("SIMVOLY_API_BASE_URL", "https://api.smart1sites.com").rstrip("/")
            try:
                r = _rq.get(f"{base}/api/v1/plans", headers={"X-CLIENT-KEY": skey, "Accept": "application/json"}, timeout=12)
                add("Smart 1 Sites Platform API", "ok" if r.ok else "error",
                    "Key is valid — plan catalog reachable." if r.ok else f"Key check failed (HTTP {r.status_code}).")
            except Exception as exc:  # noqa: BLE001
                add("Smart 1 Sites Platform API", "error", f"Could not reach Smart 1 Sites API: {exc}")
        add("Sites database", "ok" if os.environ.get("DATABASE_URL") else "warn",
            "DATABASE_URL configured." if os.environ.get("DATABASE_URL")
            else "DATABASE_URL not set — Sites inventory won't persist.")

        # --- Brandfetch ---
        bkey = os.environ.get("BRANDFETCH_API_KEY")
        if not bkey:
            add("Brandfetch API", "skipped", "Not configured — auto-fill from website is disabled (optional).")
        else:
            try:
                r = _rq.get("https://api.brandfetch.io/v2/brands/brandfetch.com",
                            headers={"Authorization": f"Bearer {bkey}"}, timeout=12)
                if r.ok:
                    add("Brandfetch API", "ok", "Key is valid.")
                elif r.status_code == 429:
                    add("Brandfetch API", "warn", "Key valid, but quota exhausted right now.")
                else:
                    add("Brandfetch API", "error", f"Key check failed (HTTP {r.status_code}).")
            except Exception as exc:  # noqa: BLE001
                add("Brandfetch API", "error", f"Could not reach Brandfetch: {exc}")

        # --- Google OAuth ---
        gid, gsec = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
        add("Google OAuth app", "ok" if gid and gsec else "warn",
            "Client ID + secret configured. Manage connected accounts in the Google module."
            if gid and gsec else "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set — Google module disabled.")

        # --- QuickBooks ---
        from . import quickbooks as qb
        if not qb.configured():
            add("QuickBooks", "skipped",
                "QB_CLIENT_ID / QB_CLIENT_SECRET not set — invoice lookup disabled (optional).")
        elif not qb.connected():
            add("QuickBooks", "warn",
                "App configured but no company connected yet — use the Connect QuickBooks button below.")
        else:
            add("QuickBooks", "ok", "Connected — client invoice lookup active on Client 360.")

        # --- Sales section (Proposal + Sales Builder) ---
        add("OpenAI API", "ok" if os.environ.get("OPENAI_API_KEY") else "skipped",
            "Configured — AI proposal generation enabled." if os.environ.get("OPENAI_API_KEY")
            else "OPENAI_API_KEY not set — proposal generation falls back to templates (optional).")
        add("Cloudinary", "ok" if (os.environ.get("CLOUDINARY_URL") or "").startswith("cloudinary://") else "warn",
            "Configured — proposal PDFs and logs persist to Cloudinary."
            if (os.environ.get("CLOUDINARY_URL") or "").startswith("cloudinary://")
            else "CLOUDINARY_URL not set — proposals persist to the local disk only.")

        # --- binaries for the PDF optimizer ---
        gs, qpdf = shutil.which("gs"), shutil.which("qpdf")
        if gs and qpdf:
            try:
                v = subprocess.run([gs, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
            except Exception:  # noqa: BLE001
                v = "?"
            add("Ghostscript / qPDF", "ok", f"gs {v} · qpdf present — PDF optimizer ready.")
        else:
            add("Ghostscript / qPDF", "error", "Missing gs/qpdf — PDF optimizer will fail (Docker image installs these).")

        # --- persistent disk ---
        add("Persistent disk", "ok" if os.path.isdir("/var/data") else "warn",
            "/var/data mounted — audit log & tokens survive deploys." if os.path.isdir("/var/data")
            else "/var/data not mounted — audit log and Google tokens are ephemeral.")

        return jsonify({"checks": checks})

    return app
