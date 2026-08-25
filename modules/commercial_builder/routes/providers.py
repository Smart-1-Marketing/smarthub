"""Provider readiness — is the key set, and does the provider accept it.

Two endpoints for two different questions, deliberately not merged:

  * `GET /api/providers` is free and answers "is a key configured", which is
    what the dashboard renders on load.
  * `POST /api/providers/verify` spends eight outbound API calls to answer
    "does the provider accept it", which is the question somebody has right
    after pasting keys into Render — and is not something a page load should
    do on its own, on every visit, for everybody.
"""

from flask import Blueprint, jsonify, request

from ..services import provider_check

bp = Blueprint("cb_providers", __name__, url_prefix="/api")


@bp.get("/providers")
def list_providers():
    return jsonify({"ok": True, "providers": provider_check.PROVIDERS,
                    "status": provider_check.status()})


@bp.post("/providers/verify")
def verify_providers():
    """Ask each provider whether it accepts the key we hold.

    `names` narrows it to one provider, for the per-row retry button. The
    results never carry a key value — see provider_check's docstring.
    """
    body = request.get_json(silent=True) or {}
    names = body.get("names")
    if isinstance(names, str):
        names = [names]
    results = provider_check.check_all(names if isinstance(names, list) else None)
    return jsonify({"ok": True, "results": results})
