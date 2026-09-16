"""Admin reporting routes registered on the existing Hub application."""
from flask import Blueprint, jsonify, render_template, request
from sqlalchemy.exc import SQLAlchemyError
from hub import reporting_service as service

bp = Blueprint('reporting', __name__)


@bp.before_request
def guard():
    from hub.users_routes import _require_admin_api
    _, error = _require_admin_api()
    if error:
        return error
    if request.method == 'POST':
        if not request.is_json:
            return jsonify(error='Send application/json.'), 415
        if request.headers.get('Origin') and request.headers['Origin'].rstrip('/') != request.host_url.rstrip('/'):
            return jsonify(error='Cross-origin changes are not allowed.'), 403
    return None


@bp.errorhandler(SQLAlchemyError)
def database_error(exc):
    return jsonify(error='Reporting database is unavailable or its tables have not been initialized. Check Hub database health.', schema_ready=False), 503


@bp.get('/diagnostics/reporting')
def provider_check():
    health = service.health()
    if request.args.get('format') == 'json':
        return jsonify(health)
    from hub.clients_registry import all_clients
    try:
        clients = all_clients()
        registry_error = None
    except Exception:
        clients = []
        registry_error = 'Client registry is unavailable. Retry before assigning accounts.'
    return render_template('reporting_provider_check.html', health=health, clients=clients,
                           registry_error=registry_error, active='diagnostics')


@bp.post('/diagnostics/reporting/tradedesk/sync')
def sync_now():
    from hub.users_routes import current_account
    result, status = service.sync(current_account().email)
    return jsonify(result), status


@bp.post('/diagnostics/reporting/accounts/<int:account_id>/match')
def match_account(account_id):
    from hub.users_routes import current_account
    from hub.clients_registry import all_clients
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or 'client_id' not in body or (body['client_id'] is not None and not isinstance(body['client_id'], str)):
        return jsonify(error='Choose a client or null to remove a mapping.'), 400
    try:
        service.match(account_id, body['client_id'], current_account().email, all_clients())
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    return jsonify(saved=True)


def register_reporting(app):
    app.register_blueprint(bp)
