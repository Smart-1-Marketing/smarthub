"""Client-scoped LSA intake links and reviewed import into setup drafts."""
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, render_template, request, redirect, url_for, abort
from sqlalchemy import update
from hub import audit
from modules.ads_builder import local_services as lsa
from modules.ads_builder.google_ads import GoogleAdsError

bp = Blueprint('lsa_intake', __name__)
PREFIX = 'lsa_intake:'
EXTRA = {'contact_name': 200, 'contact_email': 254, 'business_address': 500,
         'profile_url': 500, 'budget_currency': 3, 'verification_notes': 2000}
LABELS = {'business_name': 'Business name', 'phone': 'Phone number for leads',
          'website': 'Website (optional)', 'country': 'Country code (US, CA, etc.)',
          'postal_code': 'Business postal code', 'services': 'Services and job types',
          'areas': 'Cities or postal codes you serve', 'schedule': 'Hours you can answer leads (include time zone)',
          'weekly_budget': 'Proposed weekly advertising budget',
          'contact_name': 'Your name', 'contact_email': 'Your email',
          'business_address': 'Business address', 'profile_url': 'Google Business Profile link (optional)',
          'budget_currency': 'Budget currency (USD, CAD, etc.)',
          'verification_notes': 'License / insurance readiness or other notes (optional)'}
REQUIRED = {'business_name', 'phone', 'country', 'postal_code', 'services', 'areas',
            'schedule', 'weekly_budget', 'contact_name', 'contact_email', 'business_address', 'budget_currency'}


def now():
    return datetime.now(timezone.utc)


def key(pid):
    if not re.fullmatch(r'[a-f0-9]{24}', pid):
        abort(404)
    return PREFIX + pid


def change(session, row, data):
    encoded = json.dumps(data)
    changed = session.execute(update(lsa.store.Setting).where(
        lsa.store.Setting.key == row.key, lsa.store.Setting.value == row.value).values(value=encoded))
    if changed.rowcount != 1:
        raise GoogleAdsError('This information changed. Refresh before saving.', status=409)


def public_record(session, pid, token):
    row = session.get(lsa.store.Setting, key(pid))
    data = json.loads(row.value) if row else {}
    digest = hashlib.sha256(token.encode()).hexdigest()
    if (not re.fullmatch(r'[A-Za-z0-9_-]{43}', token) or not data.get('token_hash') or
            not secrets.compare_digest(data['token_hash'], digest) or data.get('revoked') or
            datetime.fromisoformat(data['expires_at']) <= now()):
        abort(404)
    return row, data


@bp.route('/api/setups/<pid>/intake', methods=['GET', 'POST'])
def manage(pid):
    with lsa.store.SessionLocal() as session:
        setup = session.get(lsa.store.Setting, lsa.PREFIX + pid)
        if not setup:
            abort(404)
        plan = json.loads(setup.value)
        row = session.get(lsa.store.Setting, key(pid))
        data = json.loads(row.value) if row else {}
        if request.method == 'GET':
            return jsonify({k: v for k, v in data.items() if k != 'token_hash'})
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            raise GoogleAdsError('Choose an intake action.', status=400)
        action = body.get('action')
        if action == 'create':
            token = secrets.token_urlsafe(32)
            data.update(token_hash=hashlib.sha256(token.encode()).hexdigest(), revoked=False,
                        expires_at=(now() + timedelta(days=30)).isoformat(),
                        revision=data.get('revision', 0) + 1)
            if row:
                change(session, row, data)
            else:
                session.add(lsa.store.Setting(key=key(pid), value=json.dumps(data)))
            session.commit()
            audit.log('ads_builder', 'LSA_INTAKE_LINK_CREATED', actor=lsa.actor(), setup_id=pid, client=plan.get('business_name'))
            return jsonify(path=(request.script_root or '/tools/lsa') + '/intake/' + pid + '/' + token,
                           expires_at=data['expires_at'])
        if not row:
            abort(404)
        if action == 'revoke':
            data.update(revoked=True, revision=data.get('revision', 0) + 1)
            change(session, row, data)
        elif action == 'apply':
            if not data.get('submitted_at'):
                raise GoogleAdsError('No client response has been submitted.', status=409)
            if body.get('revision') != plan.get('revision') or body.get('response_revision') != data.get('revision'):
                raise GoogleAdsError('The setup or response changed. Reopen the saved setup and review the latest response.', status=409)
            incoming = {k: v for k, v in data['answers'].items() if k in lsa.FIELDS}
            normalized = lsa.normalize_plan({**plan, **incoming})
            updated = {**plan, **normalized, 'revision': plan['revision'] + 1,
                       'updated_at': now().isoformat(), 'updated_by': lsa.actor()}
            change(session, setup, updated)
            data.update(imported_at=now().isoformat(), revision=data['revision'] + 1)
            change(session, row, data)
            session.commit()
            audit.log('ads_builder', 'LSA_INTAKE_IMPORTED', actor=lsa.actor(), setup_id=pid, client=updated.get('business_name'))
            return jsonify(setup=updated)
        else:
            raise GoogleAdsError('Choose create, revoke or apply.', status=400)
        session.commit()
    audit.log('ads_builder', 'LSA_INTAKE_LINK_REVOKED', actor=lsa.actor(), setup_id=pid, client=plan.get('business_name'))
    return jsonify(ok=True)


@bp.route('/intake/<pid>/<token>', methods=['GET', 'POST'])
def client_form(pid, token):
    request.max_content_length = 32768
    error = None
    status = 200
    with lsa.store.SessionLocal() as session:
        row, data = public_record(session, pid, token)
        answers = data.get('answers', {})
        if request.method == 'POST':
            answers = {name: request.form.get(name, '').strip() for name in {**lsa.FIELDS, **EXTRA}}
            try:
                if request.form.get('revision') != str(data['revision']):
                    raise GoogleAdsError('This form has changed. Reload the page before submitting again.', status=409)
                for name, limit in {**lsa.FIELDS, **EXTRA}.items():
                    if len(answers[name]) > limit or (name in REQUIRED and not answers[name]):
                        raise GoogleAdsError('Please complete or shorten: ' + LABELS[name], status=400)
                if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', answers['contact_email']):
                    raise GoogleAdsError('Enter a valid contact email.', status=400)
                answers['budget_currency'] = answers['budget_currency'].upper()
                if not re.fullmatch(r'[A-Z]{3}', answers['budget_currency']):
                    raise GoogleAdsError('Enter a three-letter currency such as USD or CAD.', status=400)
                if answers['profile_url'] and not re.match(r'^https?://[^\s/]+', answers['profile_url']):
                    raise GoogleAdsError('Use an https:// or http:// Business Profile link.', status=400)
                normalized = lsa.normalize_plan(answers)
                answers.update({name: normalized[name] for name in lsa.FIELDS})
                data.update(answers=answers, submitted_at=now().isoformat(), revision=data['revision'] + 1)
                data.pop('imported_at', None)
                change(session, row, data)
                session.commit()
                return redirect(url_for('lsa_intake.client_form', pid=pid, token=token, saved='1'), code=303)
            except GoogleAdsError as exc:
                error, status = exc.message, exc.status
        return render_template('lsa_client_intake.html', answers=answers, labels=LABELS,
                               limits={**lsa.FIELDS, **EXTRA}, required=REQUIRED,
                               revision=data['revision'], error=error,
                               saved=request.args.get('saved') == '1' and bool(data.get('submitted_at'))), status


@bp.after_request
def protect(response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Robots-Tag'] = 'noindex, nofollow'
    response.headers['X-Frame-Options'] = 'DENY'
    return response
