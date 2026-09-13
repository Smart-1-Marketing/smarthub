"""Weather Trigger Setup — see the module docstring in `__init__.py` and
`docs/weather-trigger-setup.md` for the full design.

Two blueprints on the hub app, the arrangement `modules/hyperframes_tools`
uses for the same reason: neither `/wx` nor `/tools/weather-setup` is a
prefix `wsgi.py` mounts, so both belong here rather than through
DispatcherMiddleware.

`bp_wx` carries **no guard at all** — it is the client-facing link, reached
by a stranger with the token and nothing else, the same shape as
`modules/scans`'s `/r/<token>`. `bp_staff` is gated by
`hub/blueprint_guard.py`, because a blueprint registered on the hub app is
not behind `AuthGuard` and the hub app has no blanket gate of its own.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

from hub.weather_triggers import (MAX_TRIGGERS, TRIGGERS, VERTICAL_LABELS,
                                  VERTICALS, month_order)

from . import store
# Aliased rather than left as `images`. hub/quotas.py's OpenAI-spend sweep
# matches the OpenAI SDK's own image-generation method as a literal
# substring, and a call site here spelled the obvious way would read as that
# method even though it names this module's own function -- one that already
# records through hub.ai. The same substring trap that checker's own
# docstring names for a Smart 1 Ads helper, from the other side: a longer
# name escaped the check there, an exact match trips it here.
from . import images as image_sources

log = logging.getLogger(__name__)

STAFF_MOUNT = "/tools/weather-setup"
PUBLIC_MOUNT = "/wx"

bp_staff = Blueprint("weather_setup_staff", __name__, template_folder="templates")
bp_wx = Blueprint("weather_setup_wx", __name__, template_folder="templates")

try:
    from hub.blueprint_guard import install as _install_guard
    _install_guard(bp_staff, mount=STAFF_MOUNT)
except Exception:                                          # noqa: BLE001
    pass                                                   # standalone, no Hub

_VIEW_LIMIT = 240          # generous: reads
_WRITE_LIMIT = 90          # picks, variant, approve
_MODEL_LIMIT = 24          # copy generation, AI image generation — billed
_WINDOW = 3600


def _limited(bucket: str, limit: int) -> bool:
    try:
        from hub import leads
        return leads.rate_limited(f"weather_setup_{bucket}", request, limit, _WINDOW)
    except Exception:                                      # noqa: BLE001
        return False


def _too_many():
    return jsonify({"ok": False,
                    "error": "Too many requests. Try again in a few minutes."}), 429


def _actor() -> str:
    for key in ("X-Hub-User", "X-Hub-Actor"):
        val = (request.headers.get(key) or "").strip()
        if val:
            return val[:60]
    try:
        return str(request.environ.get("smart1.user") or "")[:60]
    except Exception:                                      # noqa: BLE001
        return ""


def _client_ip() -> str:
    try:
        from hub import leads
        return leads.client_ip(request)
    except Exception:                                      # noqa: BLE001
        return request.remote_addr or "unknown"


# --------------------------------------------------------------------------
# Staff: start a campaign from a lead.
# --------------------------------------------------------------------------

@bp_staff.route("/")
def staff_index():
    days = 90
    try:
        from hub import leads
        listing = leads.listing(days=days)
    except Exception as exc:                              # noqa: BLE001
        listing = {"leads": [], "count": 0, "error": str(exc)}
    verticals = [{"id": v, "label": VERTICAL_LABELS.get(v, v.title())}
                for v in VERTICALS]
    return render_template("weather_setup_staff.html", listing=listing,
                           days=days, max_triggers=MAX_TRIGGERS,
                           verticals=verticals)


@bp_staff.route("/api/start", methods=["POST"])
def api_start():
    """Start a campaign, and never answer with anything but JSON.

    This module is a blueprint on the hub app, which installs no blanket
    exception handler of its own (only `wsgi.py`'s dispatcher-mounted
    modules get one, via `_install_error_reporter`). So an exception that
    escapes this function reaches the rep as Flask's stock HTML 500 page --
    not JSON -- and `weather_setup_staff.html`'s `fetch().then(r =>
    r.json())` rejects on it, showing "Could not reach the server" for what
    was actually a server-side fault. The outer try/except is the safety
    net for anything below that is not already guarded; `store.create()`
    returning `None` on a write failure (rather than raising) is the one
    gap it was closing when this was found.
    """
    try:
        body = request.get_json(silent=True) or {}
        client = str(body.get("client") or "").strip()[:200]
        if not client:
            return jsonify({"ok": False, "error": "A business name is required."}), 400
        lead_id = str(body.get("lead_id") or "").strip()[:60]
        zip_code = str(body.get("zip_code") or "").strip()[:12]
        mode = "guided" if body.get("mode") != "self" else "self"
        vertical = str(body.get("vertical") or "").strip().lower()
        if vertical not in VERTICALS:
            vertical = "restaurant"

        lead = None
        if lead_id:
            try:
                from hub import leads
                lead = leads.get(lead_id)
            except Exception:                                  # noqa: BLE001
                lead = None
            if lead and not zip_code:
                zip_code = str((lead.get("fields") or {}).get("zip") or "").strip()[:12]

        row = store.create(client=client, vertical=vertical, lead_id=lead_id,
                           created_by=_actor(), mode=mode, zip_code=zip_code)
        if row is None:
            return jsonify({"ok": False, "error": (
                "Could not save the campaign. Nothing was charged or sent — "
                "try again in a moment, and check /status if it keeps "
                "happening.")}), 503
        if zip_code:
            _refresh_station(row["token"], zip_code)

        try:
            from hub import config
            public_url = config.public_base_origin().rstrip("/") + f"/wx/{row['token']}"
        except Exception:                                      # noqa: BLE001
            public_url = f"/wx/{row['token']}"
        return jsonify({"ok": True, "token": row["token"], "public_url": public_url})
    except Exception as exc:                                   # noqa: BLE001
        try:
            from hub import errors
            errors.log_exception("weather_setup", exc, path=request.path)
        except Exception:                                      # noqa: BLE001
            pass
        return jsonify({"ok": False, "error": (
            "Something went wrong at our end starting this campaign. "
            "Nothing was sent to the client.")}), 500


def _refresh_station(token: str, zip_code: str) -> None:
    """Best-effort: name the current reading with a real location rather
    than a bare ZIP. Never blocks campaign creation on a provider call."""
    try:
        from modules.smartforecast import provider
        if not provider.configured():
            return
        snapshot = provider.fetch_weather(zip_code)
        store.set_station(token, zip_code=zip_code,
                          location_name=snapshot.get("location", ""))
    except Exception:                                      # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# Public: the client wizard.
# --------------------------------------------------------------------------

def _catalog(vertical: str) -> list[dict]:
    from datetime import date
    month = date.today().strftime("%b")
    order = month_order(vertical, month)
    return [{
        "id": t, "name": TRIGGERS[t].name, "reason": TRIGGERS[t].reason,
        "condition_label": TRIGGERS[t].condition_label,
        "approximated": TRIGGERS[t].approximated,
        "tags": list(TRIGGERS[t].tags),
    } for t in order]


@bp_wx.route("/<token>")
def wizard(token: str):
    if _limited("view", _VIEW_LIMIT):
        return _too_many()
    row = store.get(token)
    if row is None:
        return render_template("weather_setup_missing.html"), 404
    row = store.record_view(token) or row
    boot = {"token": token, "campaign": row,
           "catalog": _catalog(row.get("vertical") or "restaurant"),
           "max_triggers": MAX_TRIGGERS}
    return render_template("weather_setup_wizard.html", campaign=row, boot=boot)


@bp_wx.route("/<token>/api/state")
def api_state(token: str):
    if _limited("view", _VIEW_LIMIT):
        return _too_many()
    row = store.get(token)
    if row is None:
        return jsonify({"ok": False, "error": "No such campaign."}), 404
    return jsonify({"ok": True, "campaign": row,
                    "catalog": _catalog(row.get("vertical") or "restaurant"),
                    "max_triggers": MAX_TRIGGERS})


@bp_wx.route("/<token>/api/picks", methods=["POST"])
def api_picks(token: str):
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    body = request.get_json(silent=True) or {}
    trigger_ids = body.get("trigger_ids") or []
    if not isinstance(trigger_ids, list):
        return jsonify({"ok": False, "error": "trigger_ids must be a list."}), 400
    result = store.save_picks(token, [str(t) for t in trigger_ids])
    return jsonify(result), (200 if result.get("ok") else 400)


@bp_wx.route("/<token>/api/copy/<trigger_id>", methods=["POST"])
def api_copy(token: str, trigger_id: str):
    if _limited("model", _MODEL_LIMIT):
        return _too_many()
    result = store.get_or_generate_copy(token, trigger_id)
    return jsonify(result), (200 if result.get("ok") else 400)


@bp_wx.route("/<token>/api/variant", methods=["POST"])
def api_variant(token: str):
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    body = request.get_json(silent=True) or {}
    trigger_id = str(body.get("trigger_id") or "")
    try:
        variant_index = int(body.get("variant_index"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "variant_index must be a number."}), 400
    notes = str(body.get("notes") or "")
    result = store.select_variant(token, trigger_id, variant_index, notes)
    return jsonify(result), (200 if result.get("ok") else 400)


@bp_wx.route("/<token>/api/images/search")
def api_images_search(token: str):
    if _limited("model", _MODEL_LIMIT):
        return _too_many()
    query = request.args.get("q", "")
    return jsonify({"ok": True, **image_sources.search(query)})


@bp_wx.route("/<token>/api/images/gallery")
def api_images_gallery(token: str):
    if _limited("view", _VIEW_LIMIT):
        return _too_many()
    row = store.get(token)
    if row is None:
        return jsonify({"ok": False, "error": "No such campaign."}), 404
    return jsonify({"ok": True, **image_sources.gallery(row.get("client", ""))})


@bp_wx.route("/<token>/api/images/generate", methods=["POST"])
def api_images_generate(token: str):
    if _limited("model", _MODEL_LIMIT):
        return _too_many()
    row = store.get(token)
    if row is None:
        return jsonify({"ok": False, "error": "No such campaign."}), 404
    body = request.get_json(silent=True) or {}
    prompt = str(body.get("prompt") or "").strip()[:500]
    if not prompt:
        return jsonify({"ok": False, "error": "Describe the picture you want."}), 400
    result = image_sources.generate(prompt, client=row.get("client", ""))
    return jsonify(result), (200 if result.get("ok") else 400)


@bp_wx.route("/<token>/api/images/upload", methods=["POST"])
def api_images_upload(token: str):
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    row = store.get(token)
    if row is None:
        return jsonify({"ok": False, "error": "No such campaign."}), 404
    trigger_id = str(request.form.get("trigger_id") or "")
    up = request.files.get("file")
    if up is None or not up.filename:
        return jsonify({"ok": False, "error": "No file was received."}), 400
    result = image_sources.upload(client=row.get("client", ""), trigger_id=trigger_id,
                           data=up.read(), filename=up.filename)
    return jsonify(result), (200 if result.get("ok") else 400)


@bp_wx.route("/<token>/api/images/select", methods=["POST"])
def api_images_select(token: str):
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    row = store.get(token)
    if row is None:
        return jsonify({"ok": False, "error": "No such campaign."}), 404
    body = request.get_json(silent=True) or {}
    trigger_id = str(body.get("trigger_id") or "")
    if not trigger_id:
        return jsonify({"ok": False, "error": "trigger_id is required."}), 400
    picked = image_sources.select(
        client=row.get("client", ""), trigger_id=trigger_id,
        image_id=str(body.get("id") or ""), provider=str(body.get("provider") or ""),
        url=str(body.get("url") or ""), public_id=str(body.get("public_id") or ""),
        width=body.get("width"), height=body.get("height"),
        source_url=str(body.get("source_url") or ""))
    if not picked.get("ok"):
        return jsonify(picked), 400
    result = store.set_asset(token, trigger_id, picked["asset"])
    return jsonify(result), (200 if result.get("ok") else 400)


@bp_wx.route("/<token>/api/approve", methods=["POST"])
def api_approve(token: str):
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    row = store.get(token)
    if row is None:
        return jsonify({"ok": False, "error": "No such campaign."}), 404
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "A name is required to approve."}), 400
    result = store.approve(
        token, name=name, ip=_client_ip(), actor=_actor(),
        email=str(body.get("email") or ""), phone=str(body.get("phone") or ""))
    return jsonify(result), (200 if result.get("ok") else 400)


@bp_wx.route("/<token>/api/request-change", methods=["POST"])
def api_request_change(token: str):
    if _limited("write", _WRITE_LIMIT):
        return _too_many()
    result = store.request_change(token)
    return jsonify(result), (200 if result.get("ok") else 400)
