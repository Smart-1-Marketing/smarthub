from flask import Blueprint, jsonify, render_template, request
from hub.blueprint_guard import install
from hub import client_email as email

bp = Blueprint('client_email', __name__)
install(bp)

@bp.get('/client-email')
def page():
    from hub import current_user
    return render_template('client_email.html', client=request.args.get('client', '')[:200], domain=request.args.get('domain', '')[:300], user=current_user())

@bp.get('/api/client-email/state')
def state():
    try:
        saved = email.lookup(request.args.get('client'), request.args.get('domain', ''))
        try:
            result = email.summary(request.args.get('client'), request.args.get('domain', ''))
        except email.EmailError as exc:
            result = {'linked': False, 'error': str(exc), 'messages': []}
        result['revision'] = saved.get('revision') if saved else None
        return jsonify(result)
    except email.EmailError as exc:
        return jsonify(error=str(exc)), 400

@bp.get('/api/client-email/search')
def search():
    try:
        return jsonify(contacts=email.search(request.args.get('q')))
    except email.EmailError as exc:
        return jsonify(error=str(exc)), 400

@bp.post('/api/client-email/link')
def link():
    origin = request.headers.get('Origin')
    if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
        return jsonify(error='Open this action from Smart Hub.'), 403
    if not request.is_json:
        return jsonify(error='Expected a JSON request.'), 415
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify(error='Expected an object.'), 400
    try:
        from hub import current_user
        if body.get('unlink') is True:
            email.unlink(body.get('client'), body.get('revision'), body.get('domain', ''))
            return jsonify(ok=True)
        row = email.link(body.get('client'), body.get('contact_id'), body.get('revision'), str(current_user() or ''), domain=body.get('domain', ''))
        return jsonify(ok=True, revision=row['revision'])
    except email.EmailError as exc:
        return jsonify(error=str(exc)), 409

def register(app):
    app.register_blueprint(bp)
