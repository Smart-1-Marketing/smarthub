"""SmartForecast Dynamic Website — Smart 1 Hub Client Tool."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from . import provider
from .store import SmartForecastStore, default_path


BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))

# These exact relative prefixes are passed to the Hub guard in wsgi.py.  Staff
# screens and mutation APIs remain behind the one Hub login; only the iframe
# and its read-only JSON payload are public.
PUBLIC_PREFIXES = ("/embed/", "/api/public/")


@lru_cache(maxsize=4)
def _store_for_path(path: str) -> SmartForecastStore:
    return SmartForecastStore(path)


def store() -> SmartForecastStore:
    return _store_for_path(str(default_path()))


def _error(exc: Exception, status: int = 400):
    return jsonify({"ok": False, "error": str(exc)}), status


def _site_id() -> int:
    return max(1, int(request.args.get("site_id") or 1))


def _user() -> str:
    return str(request.environ.get("smart1.user") or
               request.headers.get("X-Smart1-User") or "SmartHub user")[:120]


def _staff_payload(data: dict) -> dict:
    site_id = int(data["site"]["id"])
    data["weather_provider_configured"] = provider.configured()
    data["preflight"] = store().preflight(
        site_id, provider_configured=provider.configured())
    return data


@app.route("/")
def index():
    return render_template("index.html", provider_configured=provider.configured())


@app.route("/health")
def health():
    try:
        data = store().bootstrap()
        return jsonify({"ok": True, "tool": "smartforecast", "site": data["site"]["id"],
                        "weather_provider_configured": provider.configured()})
    except Exception as exc:  # noqa: BLE001
        return _error(exc, 503)


@app.route("/api/bootstrap")
def api_bootstrap():
    try:
        site_id = _site_id()
        return jsonify(_staff_payload(store().bootstrap(site_id)))
    except LookupError as exc:
        return _error(exc, 404)
    except Exception as exc:  # noqa: BLE001
        return _error(exc, 500)


@app.route("/api/preflight")
def api_preflight():
    try:
        site_id = _site_id()
        return jsonify(store().preflight(site_id, provider_configured=provider.configured()))
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/qa/run", methods=["POST"])
def api_qa_run():
    try:
        site_id = _site_id()
        return jsonify(store().qa_suite(site_id))
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/setup", methods=["POST"])
def api_setup():
    try:
        return jsonify(_staff_payload(store().save_setup(
            request.get_json(silent=True) or {}, _site_id())))
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/rules/<rule_id>", methods=["POST"])
def api_rule(rule_id: str):
    try:
        return jsonify({"ok": True, "rule": store().save_rule(
            rule_id, request.get_json(silent=True) or {}, _site_id())})
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/content/<int:variant_id>", methods=["POST"])
def api_content(variant_id: int):
    try:
        return jsonify({"ok": True, "content": store().save_variant(
            variant_id, request.get_json(silent=True) or {}, _site_id())})
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/content/<int:variant_id>/publish", methods=["POST"])
def api_content_publish(variant_id: int):
    try:
        return jsonify({"ok": True, "content": store().publish_variant(
            variant_id, _site_id(), _user())})
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    body = request.get_json(silent=True) or {}
    try:
        result = store().run_simulation(
            body, site_id=_site_id(), persist=bool(body.get("persist")))
        return jsonify({"ok": True, **result})
    except (TypeError, ValueError) as exc:
        return _error(exc)


@app.route("/api/pause", methods=["POST"])
def api_pause():
    body = request.get_json(silent=True) or {}
    return jsonify(_staff_payload(store().set_paused(bool(body.get("paused")), _site_id())))


@app.route("/api/override", methods=["POST"])
def api_override():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(_staff_payload(store().force_override(
            body, site_id=_site_id(), user=_user())))
    except (TypeError, ValueError) as exc:
        return _error(exc)


@app.route("/api/weather/refresh", methods=["POST"])
def api_weather_refresh():
    site_id = _site_id()
    data = store().bootstrap(site_id)
    try:
        snapshot = provider.fetch_weather(data["site"]["postal_code"])
        evaluated = store().run_simulation(
            snapshot, site_id=site_id, persist=True, source="WeatherAPI")
        return jsonify({"ok": True, "snapshot_id": evaluated.get("snapshot_id"), "weather": snapshot,
                        "evaluation": evaluated})
    except provider.WeatherProviderError as exc:
        return _error(exc, 503)


@app.route("/api/report.csv")
def api_report_csv():
    csv_text = store().report_csv(_site_id())
    date = datetime.now(timezone.utc).date().isoformat()
    return Response(csv_text, mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="smartforecast-{date}.csv"'})


@app.route("/api/sites", methods=["GET", "POST"])
def api_sites():
    try:
        if request.method == "GET":
            return jsonify({"ok": True, "sites": store().list_sites()})
        return jsonify(_staff_payload(store().create_site(
            request.get_json(silent=True) or {}, user=_user()))), 201
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/embed-token/rotate", methods=["POST"])
def api_embed_token_rotate():
    try:
        return jsonify(store().rotate_embed_token(_site_id(), _user()))
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/packs/<pack_id>/apply", methods=["POST"])
def api_pack_apply(pack_id: str):
    try:
        return jsonify(_staff_payload(store().apply_pack(pack_id, _site_id(), _user())))
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/embed/<token>")
def embed(token: str):
    payload = store().embed_payload(token)
    if not payload:
        return render_template("embed_missing.html"), 404
    # Stored local assets use their production mount. Keep the module runnable
    # by itself for development without creating a second copy of content data.
    image_mount = "/tools/smartforecast"
    if request.script_root != image_mount:
        payload = {**payload, "content": {**payload["content"]}}
        for key in ("desktop_image_url", "mobile_image_url"):
            value = payload["content"].get(key)
            if value and value.startswith(image_mount + "/"):
                payload["content"][key] = request.script_root + value[len(image_mount):]
    response = render_template("embed.html", payload=payload)
    return Response(response, headers={
        "Content-Security-Policy": "frame-ancestors *",
        "Cache-Control": "public, max-age=60, stale-while-revalidate=300",
    })


@app.route("/api/public/embed/<token>")
def api_public_embed(token: str):
    payload = store().embed_payload(token)
    if not payload:
        return _error(LookupError("Embed not found"), 404)
    response = jsonify(payload)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=300"
    return response


if __name__ == "__main__":
    app.run("127.0.0.1", int(os.environ.get("PORT", "8015")), debug=True)
