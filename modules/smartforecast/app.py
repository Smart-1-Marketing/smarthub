"""SmartForecast Dynamic Website — Smart 1 Hub Client Tool."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from hub.webargs import clamp_int

from . import provider
from .store import SmartForecastStore, default_path


BASE_DIR = Path(__file__).resolve().parent
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))

# These exact relative prefixes are passed to the Hub guard in wsgi.py.  Staff
# screens and mutation APIs remain behind the one Hub login; only the iframe
# and its read-only JSON payload are public.
PUBLIC_PREFIXES = ("/embed/", "/api/public/")

# The "Font" setting on a site's branding is one of these two named choices,
# or "inherit" -- resolved here into a real CSS font stack rather than
# templated as a raw string. Two reasons. A cross-origin iframe cannot read
# the embedding page's computed font: `embed.html` is its own standalone
# document served with `frame-ancestors *`, and CSS `inherit` on its own
# <body> inherits from nothing -- it silently falls back to the browser
# default, which is Times New Roman on every browser this was checked
# against. "Inherit website" was therefore never deliverable as written, on
# every site it has ever been embedded on, and DEFAULT_FONT_STACK is the
# honest answer: a modern system stack that will not match the host site
# exactly but stops the widget reading as broken. And a raw value from the
# stored branding JSON is not otherwise validated on write
# (`branding.update(body.get("branding") or {})` in store.py takes whatever
# a request sends), so templating it straight into a <style> block would let
# an arbitrary string reach un-escaped CSS -- Jinja's autoescape is
# HTML-aware and does not block `{`, `}`, `:` or `;`. A value outside this
# table is refused the same way an unrecognised one is: it falls through to
# the default rather than being trusted.
DEFAULT_FONT_STACK = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
                      "Helvetica,Arial,sans-serif")
FONT_STACKS = {
    "DM Sans": ("'DM Sans'," + DEFAULT_FONT_STACK, "DM+Sans:wght@400;500;700;800"),
    "Manrope": ("'Manrope'," + DEFAULT_FONT_STACK, "Manrope:wght@400;500;700;800"),
}


def _resolve_font(branding: dict) -> dict:
    """(font_stack, font_google) for the <style> block and the Google Fonts
    <link>, never the raw stored value."""
    stack, google_family = FONT_STACKS.get(
        (branding or {}).get("font", ""), (DEFAULT_FONT_STACK, None))
    branding = {**(branding or {}), "font_stack": stack, "font_google": google_family}
    # The headline tag reaches the template as an actual HTML tag name, not
    # text content, so it has to come from a closed set resolved here --
    # never the stored string rendered directly, which for a tag-name
    # position is an HTML-injection hole rather than a styling one. Same
    # `branding.update(body.get("branding") or {})` write path as `font`
    # above, so the same rule applies: an unrecognised value falls through to
    # the existing default (h1) rather than being trusted.
    branding["heading_tag"] = branding.get("heading_tag") if branding.get(
        "heading_tag") in ("h1", "h2") else "h1"
    return branding


@lru_cache(maxsize=4)
def _store_for_path(path: str) -> SmartForecastStore:
    return SmartForecastStore(path)


def store() -> SmartForecastStore:
    return _store_for_path(str(default_path()))


def _error(exc: Exception, status: int = 400):
    return jsonify({"ok": False, "error": str(exc)}), status


def _site_id() -> int:
    return clamp_int(request.args.get("site_id"), 1, 1, 2_147_483_647)


def _user() -> str:
    return str(request.environ.get("smart1.user") or
               request.headers.get("X-Smart1-User") or "SmartHub user")[:120]


def _activity(type_: str, **extra) -> None:
    """Mirror material client changes into the Hub-wide activity ledger."""
    try:
        from hub import audit
        # Client 360 files rows by client name. Routes already know the site,
        # so enrich their activity here once instead of letting otherwise
        # valid SmartForecast work disappear from the client's record.
        if not extra.get("client") and extra.get("site_id"):
            data = store().bootstrap(int(extra["site_id"]))
            extra["client"] = data["site"]["client_name"]
        audit.log("smartforecast", type_, actor=_user(), **extra)
    except Exception:  # noqa: BLE001 — activity logging must never break the action
        pass


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
        data = {"tool": "smartforecast", **store().operational_health(provider.configured())}
        return jsonify(data), 200 if data["ok"] else 503
    except Exception as exc:  # noqa: BLE001
        return _error(exc, 503)


@app.route("/api/operations")
def api_operations():
    try:
        data = store().operational_health(provider.configured())
        return jsonify(data), 200 if data["ok"] else 503
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
        site_id = _site_id()
        result = _staff_payload(store().save_setup(
            request.get_json(silent=True) or {}, site_id))
        _activity("setup_updated", site_id=site_id,
                  client=result.get("client", {}).get("name"))
        return jsonify(result)
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/rules/<rule_id>", methods=["POST"])
def api_rule(rule_id: str):
    try:
        site_id = _site_id()
        rule = store().save_rule(
            rule_id, request.get_json(silent=True) or {}, site_id)
        _activity("rule_updated", site_id=site_id, rule_id=rule_id)
        return jsonify({"ok": True, "rule": rule})
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/content/<int:variant_id>", methods=["POST"])
def api_content(variant_id: int):
    try:
        site_id = _site_id()
        content = store().save_variant(
            variant_id, request.get_json(silent=True) or {}, site_id)
        _activity("content_draft_saved", site_id=site_id, variant_id=variant_id)
        return jsonify({"ok": True, "content": content})
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/content/<int:variant_id>/publish", methods=["POST"])
def api_content_publish(variant_id: int):
    try:
        site_id = _site_id()
        content = store().publish_variant(variant_id, site_id, _user())
        _activity("content_published", site_id=site_id, variant_id=variant_id)
        return jsonify({"ok": True, "content": content})
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/simulate", methods=["POST"])
def api_simulate():
    body = request.get_json(silent=True) or {}
    try:
        site_id = _site_id()
        persist = bool(body.get("persist"))
        result = store().run_simulation(
            body, site_id=site_id, persist=persist)
        if persist:
            _activity("simulation_persisted", site_id=site_id,
                      trigger_id=(result.get("winner") or {}).get("id"))
        return jsonify({"ok": True, **result})
    except (TypeError, ValueError) as exc:
        return _error(exc)


@app.route("/api/pause", methods=["POST"])
def api_pause():
    body = request.get_json(silent=True) or {}
    site_id = _site_id()
    paused = bool(body.get("paused"))
    result = _staff_payload(store().set_paused(paused, site_id))
    _activity("site_paused" if paused else "site_resumed", site_id=site_id)
    return jsonify(result)


@app.route("/api/override", methods=["POST"])
def api_override():
    body = request.get_json(silent=True) or {}
    try:
        site_id = _site_id()
        result = _staff_payload(store().force_override(
            body, site_id=site_id, user=_user()))
        _activity("override_updated", site_id=site_id,
                  active=bool(body.get("active")))
        return jsonify(result)
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
        _activity("weather_refreshed", site_id=site_id,
                  snapshot_id=evaluated.get("snapshot_id"))
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


@app.route("/api/engagement.csv")
def api_engagement_csv():
    csv_text = store().engagement_csv(_site_id())
    date = datetime.now(timezone.utc).date().isoformat()
    return Response(csv_text, mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="smartforecast-engagement-{date}.csv"'})


@app.route("/api/sites", methods=["GET", "POST"])
def api_sites():
    try:
        if request.method == "GET":
            return jsonify({"ok": True, "sites": store().list_sites()})
        result = _staff_payload(store().create_site(
            request.get_json(silent=True) or {}, user=_user()))
        _activity("site_created", site_id=result["site"]["id"],
                  client=result.get("client", {}).get("name"))
        return jsonify(result), 201
    except (TypeError, ValueError) as exc:
        return _error(exc)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/embed-token/rotate", methods=["POST"])
def api_embed_token_rotate():
    try:
        site_id = _site_id()
        result = store().rotate_embed_token(site_id, _user())
        _activity("embed_token_rotated", site_id=site_id)
        return jsonify(result)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/api/packs/<pack_id>/apply", methods=["POST"])
def api_pack_apply(pack_id: str):
    try:
        site_id = _site_id()
        result = _staff_payload(store().apply_pack(pack_id, site_id, _user()))
        _activity("industry_pack_applied", site_id=site_id, pack_id=pack_id,
                  client=result.get("client", {}).get("name"))
        return jsonify(result)
    except LookupError as exc:
        return _error(exc, 404)


@app.route("/embed/<token>")
def embed(token: str):
    payload = store().embed_payload(token)
    if not payload:
        return render_template("embed_missing.html"), 404
    payload = {**payload, "embed_token": token,
              "branding": _resolve_font(payload.get("branding"))}
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


@app.route("/api/public/embed/<token>/event", methods=["POST", "OPTIONS"])
def api_public_event(token: str):
    if request.method == "OPTIONS":
        response = Response(status=204)
    else:
        try:
            result = store().record_engagement(token, request.get_json(silent=True) or {})
            response = jsonify(result)
            response.status_code = 202
        except (TypeError, ValueError) as exc:
            response = jsonify({"ok": False, "error": str(exc)})
            response.status_code = 400
        except LookupError as exc:
            response = jsonify({"ok": False, "error": str(exc)})
            response.status_code = 404
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Cache-Control"] = "no-store"
    return response


if __name__ == "__main__":
    app.run("127.0.0.1", int(os.environ.get("PORT", "8015")), debug=True)
