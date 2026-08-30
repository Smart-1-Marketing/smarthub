import csv
import io
import json
import math
import os
import re
import secrets
from decimal import Decimal, InvalidOperation
from functools import wraps

from dotenv import load_dotenv
load_dotenv()

import requests

from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from config import SETTINGS
from db import (
    alert_rows,
    db_path,
    get_project,
    get_website,
    get_websites,
    infer_partner,
    init_db,
    list_plans_for_ui,
    list_templates_for_ui,
    metrics,
    query_projects,
    save_meta,
    set_lifecycle,
    upsert_project,
)
from simvoly_client import SimvolyClient, SimvolyError, unwrap_data
from sync_service import (
    discover_customer,
    import_inventory,
    refresh_known_projects,
    sync_catalog,
    sync_project,
)
from hub.webargs import clamp_int

# Activity logging. Guarded so this module still runs standalone, but
# inside the Hub every action is attributable — this module changes live client sites, and
# an unattributable change to a client's account is one nobody can
# explain later.
try:
    from hub import audit as _hub_audit
    _audit = _hub_audit.for_module("sites_admin")
except Exception:  # noqa: BLE001
    def _audit(*a, **k):  # no-op outside the Hub
        return None


app = Flask(__name__)


@app.context_processor
def _site_helpers():
    """Template helpers for the Sites views.

    project_detail.html calls login_url(host) and nothing defined it, so every
    site detail page raised UndefinedError and returned a traceback. Defined
    here rather than in the template so the URL shape lives in one place.
    """
    def login_url(host: str) -> str:
        host = str(host or "").strip().lower()
        host = re.sub(r"^https?://", "", host).split("/")[0]
        if not host:
            return ""
        # Simvoly's per-site admin entry point.
        return f"https://{host}/admin"

    return {"login_url": login_url}

app.secret_key = SETTINGS.secret_key
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("FLASK_ENV") == "production",
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,
)

# Hub integration: don't crash the whole Hub at boot if the Sites database
# isn't configured/reachable yet — surface the problem on the page instead.
try:
    init_db()
    DB_BOOT_ERROR = None
except Exception as _db_exc:  # noqa: BLE001
    DB_BOOT_ERROR = str(_db_exc)
    import logging as _logging
    _logging.getLogger(__name__).warning("Sites DB unavailable at boot: %s", _db_exc)
client = SimvolyClient()


def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login", next=request.path))
        return fn(*args, **kwargs)
    return wrapped


def require_csrf():
    supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
    expected = session.get("csrf")
    if not supplied or not expected or not secrets.compare_digest(supplied, expected):
        abort(400, "Invalid CSRF token")


def money(value):
    if value is None or value == "":
        return "—"
    try:
        return f"${Decimal(str(value)):,.2f}"
    except Exception:
        return "—"


def status_class(status):
    """The pill modifier for a project status, in the Hub's own vocabulary.

    `ok` / `warn` / `bad`, and "" for a status we do not recognise -- which is
    a bare `.s1d-pill`, and grey. That last one matters: a status this app has
    never seen is not a *bad* status, and colouring it red is the confident
    wrong answer this codebase keeps having to undo. These are the modifiers
    hub/static/hub-detail.css defines, so this module's pills are the same
    pills as Client 360's rather than a second set that looks nearly like them.
    """
    return {
        "ACTIVE": "ok",
        "TRIAL": "warn",
        "EXPIRED": "bad",
    }.get((status or "").upper(), "")


@app.context_processor
def inject_context():
    return {
        "money": money,
        "status_class": status_class,
        "settings": SETTINGS,
    }


@app.get("/health")
def health():
    return {
        "ok": True,
        "mock_mode": SETTINGS.mock_mode,
        "write_actions": SETTINGS.enable_write_actions,
        "api_base_url": SETTINGS.api_base_url,
        "api_key_configured": bool(SETTINGS.api_key),
        "database_path": db_path(),
    }


@app.get("/diag")
@login_required
def diag():
    """Read-only connectivity probe. Tries GET /api/v1/plans against several
    candidate API hosts using the configured X-CLIENT-KEY and shows which one
    returns real JSON. No secrets are printed. Redirects are not followed so a
    login bounce is visible as a 3xx + Location instead of an HTML login page.
    """
    candidates = [
        SETTINGS.api_base_url,
        "https://api.app-sources.com",
        "https://websitebuilder.app-sources.com",
        "https://api.smart1sites.com",
    ]
    # Show, safely, exactly what key the app is transmitting so it can be
    # compared against the real Platform API Key in Simvoly's settings.
    _k = SETTINGS.api_key or ""
    if _k:
        key_preview = f"{_k[:4]}…{_k[-4:]}"
        key_info = f"sending <code>{key_preview}</code>, length <b>{len(_k)}</b>"
        warnings = []
        if _k != _k.strip():
            warnings.append("has leading/trailing whitespace")
        if _k[:1] in "\"'" or _k[-1:] in "\"'":
            warnings.append("appears wrapped in quotes")
        if len(_k) != 32:
            warnings.append("length is not 32 characters")
        if warnings:
            key_info += " <span class='bad'>⚠ " + "; ".join(warnings) + "</span>"
    else:
        key_info = "<span class='bad'>NO KEY SET</span>"

    seen = []
    rows = []
    for base in candidates:
        base = (base or "").rstrip("/")
        if not base or base in seen:
            continue
        seen.append(base)
        url = f"{base}/api/v1/plans"
        row = {"base": base, "status": "", "content_type": "", "result": ""}
        try:
            r = requests.get(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Smart1SitesAdmin/3.0",
                    "X-CLIENT-KEY": SETTINGS.api_key,
                },
                timeout=SETTINGS.timeout_seconds,
                verify=SETTINGS.verify_ssl,
                allow_redirects=False,
            )
            row["status"] = str(r.status_code)
            row["content_type"] = r.headers.get("Content-Type", "")
            ct = row["content_type"].lower()
            if r.is_redirect or r.is_permanent_redirect:
                row["result"] = f"REDIRECT → {r.headers.get('Location', '(no location)')}"
            elif "json" in ct:
                try:
                    data = r.json()
                    d = data.get("data") if isinstance(data, dict) else None
                    if isinstance(d, list):
                        row["result"] = f"✓ JSON OK — {len(d)} plans returned"
                    else:
                        row["result"] = "JSON, but no 'data' list (check key/permissions)"
                except ValueError:
                    row["result"] = "declared JSON but body did not parse"
            else:
                snippet = (r.text or "")[:80].replace("\n", " ")
                row["result"] = f"non-JSON ({ct or 'unknown'}) — {snippet!r}"
        except requests.RequestException as exc:
            row["status"] = "ERR"
            row["result"] = str(exc)[:200]
        rows.append(row)

    parts = [
        "<!doctype html><meta charset='utf-8'>",
        "<title>API connectivity diagnostic</title>",
        "<style>body{font:14px/1.5 system-ui,sans-serif;margin:2rem;color:#111}"
        "table{border-collapse:collapse;width:100%;max-width:1100px}"
        "th,td{border:1px solid #ddd;padding:8px 10px;text-align:left;vertical-align:top}"
        "th{background:#f4f4f5}code{background:#f4f4f5;padding:1px 4px;border-radius:4px}"
        ".ok{color:#166534;font-weight:600}.bad{color:#991b1b}</style>",
        "<h1>Simvoly API connectivity diagnostic</h1>",
        f"<p>Configured base URL: <code>{SETTINGS.api_base_url}</code> &nbsp;•&nbsp; "
        f"mock mode: <b>{SETTINGS.mock_mode}</b></p>",
        f"<p>API key: {key_info}</p>",
        "<p>Compare the key above against the Platform API Key shown in your Simvoly "
        "settings. It should match the first four and last four characters exactly and be "
        "32 characters long. If it does not, correct <code>SIMVOLY_API_KEY</code> in Render.</p>",
        "<p>The host whose result says <span class='ok'>JSON OK</span> is your correct "
        "<code>SIMVOLY_API_BASE_URL</code>. Set it in Render and redeploy.</p>",
        "<table><tr><th>Candidate host</th><th>HTTP</th><th>Content-Type</th><th>Result</th></tr>",
    ]
    for row in rows:
        cls = "ok" if "JSON OK" in row["result"] else ("bad" if row["status"] in ("ERR", "") or "REDIRECT" in row["result"] or "non-JSON" in row["result"] else "")
        parts.append(
            f"<tr><td><code>{row['base']}</code></td><td>{row['status']}</td>"
            f"<td>{row['content_type']}</td><td class='{cls}'>{row['result']}</td></tr>"
        )
    parts.append("</table>")
    return "\n".join(parts)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        good_user = secrets.compare_digest(
            request.form.get("username", ""), SETTINGS.admin_username
        )
        good_pass = secrets.compare_digest(
            request.form.get("password", ""), SETTINGS.admin_password
        )
        if good_user and good_pass:
            session.clear()
            session["authenticated"] = True
            session["csrf"] = secrets.token_urlsafe(24)
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Incorrect username or password.", "danger")
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    require_csrf()
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def dashboard():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    plan = request.args.get("plan", "").strip()
    partner = request.args.get("partner", "").strip()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1

    # By default (no search/filter) show only the 10 most recent active sites
    # instead of pre-loading the whole portfolio.
    preview = not (q or status or plan or partner)
    if preview:
        rows, count = query_projects("", "ACTIVE", "", "", 1, 10)
        pages = 1
    else:
        rows, count = query_projects(q, status, plan, partner, page, 50)
        pages = max(1, math.ceil(count / 50))

    total, statuses, plans, allmeta, last, template_count = metrics()

    revenue = 0.0
    cost = 0.0
    partner_stats = {}
    for row in allmeta:
        client_price = (
            row["client_price"]
            if row["client_price"] is not None
            else (row["monthly_price"] or 0)
        )
        # Platform cost defaults to the plan's BG (wholesale) monthly price when
        # no manual per-account cost has been entered.
        platform_cost = (
            row["platform_cost"]
            if row["platform_cost"] is not None
            else (row["bg_monthly_price"] or 0)
        )
        if row["status"] == "ACTIVE":
            revenue += client_price or 0
            cost += platform_cost or 0
        p = row["partner"] or infer_partner(row["name"])
        st = partner_stats.setdefault(p, {"active": 0, "total": 0})
        st["total"] += 1
        if row["status"] == "ACTIVE":
            st["active"] += 1

    # Partners with at least one active client, sorted by active count (desc).
    active_partners = sorted(
        (
            {"name": k, "active": v["active"], "total": v["total"]}
            for k, v in partner_stats.items()
            if v["active"] > 0
        ),
        key=lambda x: (-x["active"], -x["total"], x["name"].lower()),
    )
    partners_main = [p for p in active_partners if p["total"] >= 2]
    partners_more = [p for p in active_partners if p["total"] < 2]
    # Everything with zero active clients rolls up into one "Non-Active" group.
    non_active_total = sum(v["total"] for v in partner_stats.values() if v["active"] == 0)

    return render_template(
        "dashboard.html",
        rows=rows,
        count=count,
        page=page,
        pages=pages,
        preview=preview,
        q=q,
        status=status,
        plan=plan,
        partner=partner,
        total=total,
        statuses=statuses,
        plans=plans,
        last=last,
        template_count=template_count,
        revenue=revenue,
        cost=cost,
        margin=revenue - cost,
        alerts=alert_rows(8),
        partners_main=partners_main,
        partners_more=partners_more,
        non_active_total=non_active_total,
    )


def _effective_platform_cost(row):
    """Manual per-account cost if set, otherwise the plan's BG (wholesale) price."""
    if row.get("platform_cost") is not None:
        return row["platform_cost"] or 0
    return row.get("bg_monthly_price") or 0


def _effective_client_price(row):
    """Manual client price if set, otherwise the plan's catalog monthly price."""
    if row.get("client_price") is not None:
        return row["client_price"] or 0
    return row.get("monthly_price") or 0


@app.get("/export.csv")
@login_required
def export_csv():
    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()
    plan = request.args.get("plan", "").strip()
    partner = request.args.get("partner", "").strip()
    # Pull every matching row (no pagination) for the export.
    rows, _ = query_projects(q, status, plan, partner, page=1, per_page=1_000_000)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "Project ID",
            "Account",
            "Internal Client Name",
            "Partner",
            "Status",
            "Lifecycle",
            "Plan",
            "Plan ID",
            "Domain",
            "Created",
            "Billing Period",
            "Next Billing",
            "Client Price",
            "Platform Cost",
            "Margin",
            "Notes",
        ]
    )
    for r in rows:
        client_price = _effective_client_price(r)
        platform_cost = _effective_platform_cost(r)
        writer.writerow(
            [
                r.get("project_id", ""),
                r.get("name", ""),
                r.get("internal_client_name", "") or "",
                r.get("partner_display") or r.get("partner") or "",
                r.get("status", "") or "",
                r.get("lifecycle_state", "") or "",
                r.get("plan_name", "") or "",
                r.get("plan_id", "") or "",
                r.get("domain", "") or "",
                r.get("created", "") or "",
                r.get("billing_period", "") or "",
                r.get("next_billing_date", "") or "",
                f"{client_price:.2f}",
                f"{platform_cost:.2f}",
                f"{client_price - platform_cost:.2f}",
                (r.get("notes", "") or "").replace("\n", " "),
            ]
        )

    safe_partner = "".join(ch for ch in partner if ch.isalnum() or ch in "-_") or "all"
    filename = f"smart1-clients-{safe_partner}.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/sync")
@login_required
def sync():
    require_csrf()
    try:
        out = sync_catalog()
        if SETTINGS.mock_mode:
            flash(
                f"Demo sync complete: {out['projects']} projects, {out['plans']} plans, and {out['templates']} templates.",
                "success",
            )
        else:
            flash(
                f"Platform catalog synced: {out['plans']} plans and {out['templates']} templates. Existing project inventory is managed separately because Simvoly's documented project-list endpoint is customer-scoped.",
                "success",
            )
    except Exception as exc:
        flash(f"Sync failed: {exc}", "danger")
    return redirect(url_for("dashboard"))


@app.route("/inventory", methods=["GET"])
@login_required
def inventory():
    total, _, _, _, last, _ = metrics()
    return render_template("inventory.html", total=total, last=last)


@app.post("/inventory/import")
@login_required
def inventory_import():
    require_csrf()
    raw = request.form.get("payload", "").strip()
    upload = request.files.get("file")
    if upload and upload.filename:
        raw = upload.read().decode("utf-8", errors="replace")
    if not raw:
        flash("Paste JSON or choose a JSON file to import.", "danger")
        return redirect(url_for("inventory"))
    try:
        payload = json.loads(raw)
        count = import_inventory(payload)
        flash(f"Imported {count} unique project records into the local registry.", "success")
    except Exception as exc:
        flash(f"Inventory import failed: {exc}", "danger")
    return redirect(url_for("inventory"))


@app.post("/inventory/discover")
@login_required
def inventory_discover():
    require_csrf()
    identifier_type = request.form.get("identifier_type", "customerEmail")
    value = request.form.get("identifier_value", "").strip()
    try:
        out = discover_customer(identifier_type, value)
        flash(
            f"Customer discovery found {out['imported']} project(s); {out['refreshed']} were refreshed with official project/site details.",
            "success",
        )
    except Exception as exc:
        flash(f"Customer discovery failed: {exc}", "danger")
    return redirect(url_for("inventory"))


@app.post("/inventory/refresh-known")
@login_required
def inventory_refresh_known():
    require_csrf()
    try:
        limit = clamp_int(request.form.get("limit"), 25, 1, 100)
    except ValueError:
        limit = 25
    try:
        out = refresh_known_projects(limit=limit)
        msg = f"Refreshed {out['ok']} project(s); {out['failed']} failed."
        if out["errors"]:
            msg += " First errors: " + " | ".join(out["errors"][:3])
        flash(msg, "success" if out["failed"] == 0 else "danger")
    except Exception as exc:
        flash(f"Project refresh failed: {exc}", "danger")
    return redirect(url_for("dashboard"))


@app.get("/projects/<pid>")
@login_required
def project_detail(pid):
    p = get_project(pid)
    if not p:
        abort(404)
    return render_template(
        "project_detail.html",
        p=p,
        websites=get_websites(pid),
        plans=list_plans_for_ui(),
    )


@app.post("/projects/<pid>/refresh")
@login_required
def refresh_project(pid):
    require_csrf()
    try:
        out = sync_project(pid)
        flash(
            f"Project refreshed from the official Platform API ({out['websites']} website/funnel record(s)).",
            "success",
        )
    except Exception as exc:
        flash(f"Detail refresh failed: {exc}", "danger")
    return redirect(url_for("project_detail", pid=pid))


@app.route("/packages", methods=["GET", "POST"])
@login_required
def packages():
    """Package (plan) wholesale costs — restores the endpoint the nav and
    packages.html expect; this app.py revision had lost it."""
    from db import set_plan_cost
    if request.method == "POST":
        require_csrf()
        updated = 0
        for key, value in request.form.items():
            if not key.startswith("cost_"):
                continue
            plan_id = key[len("cost_"):]
            value = value.strip()
            if not value:
                continue
            try:
                cost = float(Decimal(value))
            except InvalidOperation:
                flash(f"Ignored invalid cost for plan {plan_id}: {value!r}", "danger")
                continue
            set_plan_cost(plan_id, cost)
            updated += 1
        flash(f"Saved platform cost for {updated} package(s).", "success")
        return redirect(url_for("packages"))
    return render_template("packages.html", plans=list_plans_for_ui())


@app.post("/costs/import")
@login_required
def costs_import():
    """Per-site actual cost import from pasted invoice text / JSON / file."""
    import json as _json

    from costs import parse_charges
    from db import known_project_ids, set_platform_cost
    require_csrf()
    payload = (request.form.get("payload") or "").strip()
    upload = request.files.get("file")
    if upload and upload.filename:
        try:
            payload = upload.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            flash("Could not read the uploaded file.", "danger")
            return redirect(url_for("packages"))
    if not payload:
        flash("Paste invoice text or choose a file first.", "danger")
        return redirect(url_for("packages"))

    costs_map = {}
    try:  # JSON map {"project_id": cost} first
        data = _json.loads(payload)
        if isinstance(data, dict):
            costs_map = {str(k): float(v) for k, v in data.items()}
    except (ValueError, TypeError):
        pass
    matched = len(costs_map)
    if not costs_map:  # fall back to invoice-text parsing
        costs_map, matched = parse_charges(payload)
    if not costs_map:
        flash("No site charges recognized in that text.", "danger")
        return redirect(url_for("packages"))

    known = set(known_project_ids())
    applied = skipped = 0
    for pid, cost in costs_map.items():
        if known and str(pid) not in known:
            skipped += 1
            continue
        set_platform_cost(pid, cost)
        applied += 1
    msg = f"Imported per-site costs for {applied} site(s) from {matched} charge line(s)."
    if skipped:
        msg += f" Skipped {skipped} unknown project id(s)."
    flash(msg, "success")
    return redirect(url_for("packages"))


@app.post("/projects/<pid>/pricing")
@login_required
def pricing(pid):
    require_csrf()
    if not get_project(pid):
        abort(404)

    def number(name):
        value = request.form.get(name, "").strip()
        if not value:
            return None
        try:
            return float(Decimal(value))
        except InvalidOperation as exc:
            raise ValueError from exc

    try:
        save_meta(
            pid,
            number("client_price"),
            number("platform_cost"),
            request.form.get("partner", "").strip(),
            request.form.get("notes", "").strip(),
            request.form.get("internal_client_name", "").strip(),
        )
        flash("Pricing and account notes saved.", "success")
    except ValueError:
        flash("Pricing fields must be valid numbers.", "danger")
    return redirect(url_for("project_detail", pid=pid))


@app.route("/sites/add", methods=["GET", "POST"])
@login_required
def add_site():
    plans = list_plans_for_ui()
    templates = list_templates_for_ui()
    if request.method == "POST":
        require_csrf()
        values = {
            "site_name": request.form.get("site_name", "").strip(),
            "email": request.form.get("email", "").strip(),
            "first_name": request.form.get("first_name", "").strip(),
            "last_name": request.form.get("last_name", "").strip(),
            "external_customer_id": request.form.get("external_customer_id", "").strip(),
            "user_id": request.form.get("user_id", "").strip(),
            "template_id": request.form.get("template_id", "").strip(),
            "subdomain": request.form.get("subdomain", "").strip(),
            "brand_color": request.form.get("brand_color", "").strip(),
            "personalization_tags": request.form.get("personalization_tags", "").strip(),
        }
        activate_plan_id = request.form.get("plan_id", "").strip()
        period = request.form.get("period", "MONTHLY").strip()
        try:
            if values["personalization_tags"]:
                parsed = json.loads(values["personalization_tags"])
                if not isinstance(parsed, list):
                    raise ValueError("Personalization tags must be a JSON array.")
                values["personalization_tags"] = json.dumps(parsed)
            result = client.create_project_website(values)
            data = unwrap_data(result) if isinstance(result, dict) else {}
            project_id = data.get("projectId") if isinstance(data, dict) else None
            if project_id and activate_plan_id:
                client.set_project_status(project_id, "ACTIVE", activate_plan_id, period)
            if project_id:
                try:
                    sync_project(project_id)
                except Exception:
                    pass
            flash(
                f"Site created{f' as project {project_id}' if project_id else ''}."
                + (" Plan activation requested." if activate_plan_id else ""),
                "success",
            )
            if project_id:
                return redirect(url_for("project_detail", pid=project_id))
        except Exception as exc:
            flash(f"Create site failed: {exc}", "danger")
    return render_template("add_site.html", plans=plans, templates=templates)


@app.post("/projects/<pid>/action/<action>")
@login_required
def project_action(pid, action):
    require_csrf()
    p = get_project(pid)
    if not p:
        abort(404)
    try:
        if action == "suspend":
            client.set_project_status(pid, "EXPIRED")
            set_lifecycle(pid, "SUSPENDED")
        elif action == "cancel":
            if request.form.get("confirm") != "CANCEL":
                flash("Type CANCEL to confirm cancellation.", "danger")
                return redirect(url_for("project_detail", pid=pid))
            client.set_project_status(pid, "EXPIRED")
            set_lifecycle(pid, "CANCELLED")
        elif action == "reactivate":
            plan_id = request.form.get("plan_id") or p.get("plan_id")
            period = request.form.get("period") or p.get("billing_period") or "MONTHLY"
            client.set_project_status(pid, "ACTIVE", plan_id, period)
            set_lifecycle(pid, "")
        else:
            abort(404)
        try:
            sync_project(pid)
        except Exception:
            pass
        flash(f"{action.title()} request completed.", "success")
    except Exception as exc:
        flash(f"{action.title()} failed: {exc}", "danger")
    return redirect(url_for("project_detail", pid=pid))


@app.post("/websites/<website_id>/domain/connect")
@login_required
def connect_domain(website_id):
    require_csrf()
    w = get_website(website_id)
    if not w:
        abort(404)
    domain = request.form.get("domain", "").strip()
    try:
        client.connect_domain(website_id, domain)
        sync_project(w["project_id"])
        flash(f"Domain connection requested for {domain}.", "success")
    except Exception as exc:
        flash(f"Connect domain failed: {exc}", "danger")
    return redirect(url_for("project_detail", pid=w["project_id"]))


@app.post("/websites/<website_id>/domain/disconnect")
@login_required
def disconnect_domain(website_id):
    require_csrf()
    w = get_website(website_id)
    if not w:
        abort(404)
    domain = request.form.get("domain", "").strip() or w.get("domain", "")
    try:
        client.disconnect_domain(website_id, domain)
        sync_project(w["project_id"])
        flash(f"Domain disconnect requested for {domain}.", "success")
    except Exception as exc:
        flash(f"Disconnect domain failed: {exc}", "danger")
    return redirect(url_for("project_detail", pid=w["project_id"]))


@app.post("/websites/<website_id>/personalization")
@login_required
def personalization(website_id):
    require_csrf()
    w = get_website(website_id)
    if not w:
        abort(404)
    raw = request.form.get("tags", "[]").strip() or "[]"
    try:
        tags = json.loads(raw)
        if not isinstance(tags, list):
            raise ValueError("Tags must be a JSON array.")
        colors = {
            "brand_color": request.form.get("brand_color", "").strip(),
            "secondary_color_1": request.form.get("secondary_color_1", "").strip(),
            "secondary_color_2": request.form.get("secondary_color_2", "").strip(),
            "secondary_color_3": request.form.get("secondary_color_3", "").strip(),
            "secondary_color_4": request.form.get("secondary_color_4", "").strip(),
        }
        client.set_personalization_tags(website_id, tags, colors)
        flash("Personalization tags updated.", "success")
    except Exception as exc:
        flash(f"Personalization update failed: {exc}", "danger")
    return redirect(url_for("project_detail", pid=w["project_id"]))


@app.post("/websites/<website_id>/delete")
@login_required
def delete_website(website_id):
    require_csrf()
    w = get_website(website_id)
    if not w:
        abort(404)
    if request.form.get("confirm") != "DELETE":
        flash("Type DELETE to permanently delete the website/funnel.", "danger")
        return redirect(url_for("project_detail", pid=w["project_id"]))
    try:
        client.set_website_status(website_id, "DELETED")
        sync_project(w["project_id"])
        flash("Website/funnel delete request completed.", "success")
    except Exception as exc:
        flash(f"Delete failed: {exc}", "danger")
    return redirect(url_for("project_detail", pid=w["project_id"]))


@app.post("/websites/<website_id>/check-limits")
@login_required
def website_check_limits(website_id):
    """Ask Simvoly whether this site fits a plan's limits.

    The form for this shipped on project_detail.html and the route did not, so
    every visit to a project page died on `url_for` before a single row was
    drawn — a BuildError is raised at render time, so the 500 was the whole
    page rather than the one control. `simvoly_client.check_limits()` had been
    written and had no caller at all; this is the caller.

    Read-only on purpose, and deliberately NOT behind the write-actions gate:
    it asks Simvoly a question and changes nothing, which is what makes it
    useful before a plan change rather than after one.
    """
    require_csrf()
    w = get_website(website_id)
    if not w:
        abort(404)
    plan_id = request.form.get("plan_id", "").strip()
    if not plan_id:
        flash("Pick a plan to check against.", "danger")
        return redirect(url_for("project_detail", pid=w["project_id"]))
    try:
        out = client.check_limits(website_id, plan_id)
        # Simvoly answers in more than one shape, and an unrecognised one must
        # not read as a pass: an empty message with a green tick is the
        # confident blank this codebase keeps having to undo.
        if isinstance(out, dict):
            passed = out.get("success", out.get("result", None))
            msg = str(out.get("message") or out.get("error") or "").strip()
        else:
            passed, msg = None, str(out or "").strip()
        if passed is True:
            flash(msg or "This site fits the selected plan's limits.", "success")
        elif passed is False:
            flash(msg or "This site does NOT fit the selected plan's limits.",
                  "danger")
        else:
            flash("Simvoly answered, but not in a shape this page can read as "
                  "pass or fail: " + (msg or repr(out)[:200]), "warning")
    except Exception as exc:
        flash(f"Limits check failed: {exc}", "danger")
    return redirect(url_for("project_detail", pid=w["project_id"]))


@app.post("/projects/<pid>/sso")
@login_required
def project_sso(pid):
    require_csrf()
    if not get_project(pid):
        abort(404)
    user_email = request.form.get("user_email", "").strip()
    user_id = request.form.get("user_id", "").strip()
    external_customer_id = request.form.get("external_customer_id", "").strip()
    website_id = request.form.get("website_id", "").strip() or None
    path = request.form.get("path", "").strip() or None
    try:
        result = client.start_building_session(
            project_id=pid,
            website_id=website_id,
            user_email=user_email or None,
            user_id=user_id or None,
            external_customer_id=external_customer_id or None,
            path=path,
        )
        access_url = result.get("accessUrl")
        if not access_url:
            raise SimvolyError("SSO response did not contain accessUrl.")
        return redirect(access_url)
    except Exception as exc:
        flash(f"Builder SSO failed: {exc}", "danger")
        return redirect(url_for("project_detail", pid=pid))


@app.errorhandler(404)
def not_found(_):
    return render_template("error.html", code=404, message="Not found."), 404


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=error.description), 400
