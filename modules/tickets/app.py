"""Web Tickets audit — Flask blueprint mounted at /tools/tickets."""
import csv
import io
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, Response

from . import config, knack_client, normalize, reports, service

bp = Blueprint(
    "tickets", __name__,
    url_prefix="/tools/tickets",
    template_folder="templates",
    static_folder="static",
    static_url_path="/static",
)

# Staff only. This is a blueprint on the hub app, so wsgi.py's AuthGuard --
# which wraps dispatcher-mounted modules -- never sees it, and the hub app has
# no blanket gate of its own. Without this every page and API route here
# answered 200 to anyone with the URL. One gate on the blueprint, so the next
# route added does not have to remember; hub/blueprint_guard.py says why it is
# shared rather than written out here for the third time.
try:
    from hub.blueprint_guard import install as _install_guard
    _install_guard(bp, mount="/tools/tickets")
except Exception:                                       # noqa: BLE001
    pass                                                # standalone, no Hub


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _ticket_json(t):
    out = dict(t)
    for key in ("opened", "closed", "last_update"):
        out[key] = _iso(out.get(key))
    return out


def _fail(exc, code=400):
    return jsonify({"ok": False, "error": str(exc)}), code


def _wants_force():
    return request.args.get("force") in ("1", "true", "yes")


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
@bp.route("/")
def index():
    return render_template("tickets_index.html",
                           page="index",
                           demo=knack_client.demo_mode(),
                           window_days=config.LOAD_WINDOW_DAYS,
                           sla_days=config.SLA_DAYS)


@bp.route("/client/<path:key>")
def client_page(key):
    return render_template("tickets_client.html",
                           page="client",
                           client_key=key,
                           demo=knack_client.demo_mode(),
                           sla_days=config.SLA_DAYS)


@bp.route("/setup")
def setup_page():
    return render_template("tickets_setup.html",
                           page="setup",
                           demo=knack_client.demo_mode(),
                           ticket_fields=config.FIELDS,
                           client_fields=config.CLIENT_FIELDS)


# --------------------------------------------------------------------------
# Data API
# --------------------------------------------------------------------------
@bp.route("/api/summary")
def api_summary():
    try:
        data = service.build(force=_wants_force())
    except service.NotConfigured as exc:
        return jsonify({"ok": False, "needs_setup": True, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001 - surfaced to the page, not swallowed
        return _fail(exc, 502)
    return jsonify({
        "ok": True,
        "rows": data["rows"],
        "totals": data["totals"],
        "window_days": data["window_days"],
        "fetched_at": data["fetched_at"],
        "demo": data["demo"],
    })


@bp.route("/api/client/<path:key>")
def api_client(key):
    try:
        detail = service.client_detail(key, force=_wants_force())
    except service.NotConfigured as exc:
        return jsonify({"ok": False, "needs_setup": True, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, 502)
    if detail is None:
        return jsonify({"ok": False, "error": "No tickets on file for that client."}), 404
    return jsonify({
        "ok": True,
        "row": detail["row"],
        "tickets": [_ticket_json(t) for t in detail["tickets"]],
        "by_type": detail["by_type"],
        "by_month": detail["by_month"],
        "fetched_at": detail["fetched_at"],
        "demo": detail["demo"],
    })


@bp.route("/api/card")
def api_card():
    """Compact payload for the Client 360 card. Takes ?client=<name>."""
    name = request.args.get("client", "")
    if not name:
        return jsonify({"ok": False, "error": "Pass ?client=<name>."}), 400
    key = normalize.name_key(name)
    try:
        detail = service.client_detail(key)
    except service.NotConfigured as exc:
        return jsonify({"ok": False, "needs_setup": True, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, 502)
    if detail is None:
        return jsonify({"ok": True, "found": False, "key": key,
                        "url": f"/tools/tickets/client/{key}"})
    row = detail["row"]
    return jsonify({
        "ok": True,
        "found": True,
        "key": key,
        "url": f"/tools/tickets/client/{key}",
        "open": row["open"],
        "total": row["total"],
        "breaches": row["breaches"],
        "stale": row["stale"],
        "avg_resolve_days": row["avg_resolve_days"],
        "per_month": row["per_month"],
        "allowance": row["allowance"],
        "load_state": row["load_state"],
        "pct_of_revenue": row["pct_of_revenue"],
        "cost_to_serve": row["cost_to_serve"],
        "top_type": row["top_type"],
        "recent": [_ticket_json(t) for t in detail["tickets"][:10]],
    })


@bp.route("/api/reports")
def api_reports():
    try:
        payload = reports.run_all(force=_wants_force())
    except service.NotConfigured as exc:
        return jsonify({"ok": False, "needs_setup": True, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, 502)
    payload["ok"] = True
    return jsonify(payload)


@bp.route("/api/reports/<key>")
def api_report(key):
    try:
        report = reports.run_one(key, force=_wants_force())
    except service.NotConfigured as exc:
        return jsonify({"ok": False, "needs_setup": True, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, 502)
    if report is None:
        return jsonify({"ok": False, "error": "Unknown report."}), 404
    return jsonify({"ok": True, "report": report})


@bp.route("/api/refresh", methods=["POST"])
def api_refresh():
    try:
        data = service.build(force=True)
    except service.NotConfigured as exc:
        return jsonify({"ok": False, "needs_setup": True, "error": str(exc)}), 409
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, 502)
    return jsonify({"ok": True, "fetched_at": data["fetched_at"],
                    "tickets": len(data["tickets"])})


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------
@bp.route("/api/export.csv")
def api_export():
    report_key = request.args.get("report")
    client_key = request.args.get("client")
    buf = io.StringIO()
    writer = csv.writer(buf)

    try:
        if report_key:
            report = reports.run_one(report_key)
            if report is None:
                return jsonify({"ok": False, "error": "Unknown report."}), 404
            writer.writerow(report["columns"] + ["Severity"])
            for row in report["rows"]:
                writer.writerow(list(row["cells"]) + [row.get("severity", "")])
            name = report_key
        elif client_key:
            detail = service.client_detail(client_key)
            if detail is None:
                return jsonify({"ok": False, "error": "No tickets on file."}), 404
            writer.writerow(["Ticket", "Opened", "Closed", "Status", "Type",
                             "Priority", "Assigned", "Hours", "Age", "Summary"])
            for t in detail["tickets"]:
                writer.writerow([
                    t["number"], _iso(t["opened"]) or "", _iso(t["closed"]) or "",
                    t["status"], t["type"], t["priority"], t["assignee"],
                    t["hours"], t["age_days"] if t["age_days"] is not None else "",
                    t["summary"],
                ])
            name = f"client-{client_key.replace(' ', '-')}"
        else:
            data = service.build()
            writer.writerow(["Client", "Monthly", "Tickets", "Open", "Closed",
                             f'Last {data["window_days"]}d', "Per month",
                             "Plan absorbs", "Hours", "Cost to serve",
                             "% of revenue", "Avg days to close", "Past SLA",
                             "Stale", "Matched"])
            for r in data["rows"]:
                writer.writerow([
                    r["name"], r["monthly_price"], r["total"], r["open"], r["closed"],
                    r["window_total"], r["per_month"], r["allowance"],
                    r["hours_window"], r["cost_to_serve"],
                    r["pct_of_revenue"] if r["pct_of_revenue"] is not None else "",
                    r["avg_resolve_days"] if r["avg_resolve_days"] is not None else "",
                    r["breaches"], r["stale"], "yes" if r["matched"] else "no",
                ])
            name = "tickets-by-customer"
    except service.NotConfigured as exc:
        return _fail(exc, 409)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, 502)

    stamp = datetime.now().strftime("%Y-%m-%d")
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}-{stamp}.csv"'},
    )


# --------------------------------------------------------------------------
# Setup / field mapping
# --------------------------------------------------------------------------
@bp.route("/api/objects")
def api_objects():
    try:
        return jsonify({"ok": True, "objects": knack_client.list_objects(),
                        "demo": knack_client.demo_mode()})
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, 502)


@bp.route("/api/fields/<object_key>")
def api_fields(object_key):
    which = request.args.get("for", "tickets")
    spec = config.CLIENT_FIELDS if which == "clients" else config.FIELDS
    try:
        fields = knack_client.list_fields(object_key)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, 502)
    return jsonify({"ok": True, "fields": fields,
                    "guess": normalize.guess_fieldmap(fields, spec)})


@bp.route("/api/fieldmap", methods=["GET", "POST"])
def api_fieldmap():
    if request.method == "GET":
        eff = config.effective()
        if knack_client.demo_mode() and not eff["tickets_object"]:
            # Nothing to map against a real app yet, so show the sample schema
            # rather than three empty dropdowns.
            from .demo import demo_client_fieldmap, demo_fieldmap
            eff.update({
                "tickets_object": "object_demo_tickets",
                "clients_object": "object_demo_clients",
                "fieldmap": demo_fieldmap(),
                "client_fieldmap": demo_client_fieldmap(),
            })
        # What the module is actually using. With nothing saved that is the
        # auto map — the pinned ids plus a label match for the rest — so the
        # page shows the mapping in force rather than empty dropdowns beside a
        # report that is already working.
        auto = {}
        if not eff["fieldmap"] and not knack_client.demo_mode():
            auto = service.auto_fieldmap(eff["tickets_object"])
            if auto:
                eff["fieldmap"] = auto
        eff["ok"] = True
        eff["auto"] = bool(auto)
        eff["confirmed"] = sorted(config.CONFIRMED_FIELDS)
        eff["required"] = config.REQUIRED_FIELDS
        eff["demo"] = knack_client.demo_mode()
        return jsonify(eff)

    body = request.get_json(silent=True) or {}
    fieldmap = body.get("fieldmap") or {}
    missing = [k for k in config.REQUIRED_FIELDS if not fieldmap.get(k)]
    if missing:
        labels = {f["key"]: f["label"] for f in config.FIELDS}
        return jsonify({
            "ok": False,
            "error": "Map these before saving: "
                     + ", ".join(labels.get(m, m) for m in missing),
        }), 400
    if not body.get("tickets_object"):
        return jsonify({"ok": False, "error": "Choose the ticket object first."}), 400

    saved = config.save_config({
        "tickets_object": body.get("tickets_object", ""),
        "clients_object": body.get("clients_object", ""),
        "fieldmap": fieldmap,
        "client_fieldmap": body.get("client_fieldmap") or {},
        "saved_at": datetime.now().isoformat(),
    })
    # The one write this module has: the field map decides what every column
    # in every ticket report means, so changing it silently re-points the
    # whole report at different Knack fields. _audit was bound at the top of
    # this module and called nowhere, so that change was unattributable --
    # which on a config nobody re-reads is how a report comes to be wrong
    # with no way back to who changed it. Plain reads, never an expression.
    _audit("fieldmap_saved", tickets_object=body.get("tickets_object") or "",
           clients_object=body.get("clients_object") or "",
           fields_mapped=len(fieldmap))
    saved["ok"] = True
    return jsonify(saved)


def register_tickets(app):
    """Call from the Hub's app factory."""
    app.register_blueprint(bp)
    return bp
