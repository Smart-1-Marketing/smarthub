"""Offline route and OAuth regression tests; never contacts Google."""
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

_tmp = tempfile.TemporaryDirectory(prefix='google-access-flow-')
os.environ.update(HUB_DATA_DIR=_tmp.name, SECRET_KEY='test-only',
                  PUBLIC_BASE_URL='https://hub.example',
                  GOOGLE_ACCESS_CLIENT_ID='test.apps.googleusercontent.com',
                  GOOGLE_ACCESS_CLIENT_SECRET='test-secret',
                  GOOGLE_ACCESS_AGENCY_EMAIL='agency@example.com',
                  GOOGLE_ACCESS_GBP_ENABLED='true')

from flask import Flask
from modules.google_access import register_google_access, config
from modules.google_access import app as routes, google_client as gc
from modules.google_access.models import db, AccessRequest, AccessGrant, OAuthState, new_token
from modules.google_access.grants import expiry_from_now
from hub import auth

class GoogleAccessFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.network = patch('requests.sessions.Session.request', side_effect=AssertionError('Unexpected network call'))
        cls.network.start()
        cls.app = Flask(__name__)
        cls.app.config.update(TESTING=True, SECRET_KEY='test', SQLALCHEMY_DATABASE_URI='sqlite://')
        db.init_app(cls.app)
        register_google_access(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.network.stop()
        _tmp.cleanup()

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        routes._hits.clear()
        self.client = self.app.test_client()
        self.client.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value('Tester'))
        self.audit = patch('modules.google_access.grants.audit')
        self.audit.start()

    def tearDown(self):
        self.audit.stop()
        db.session.remove()
        self.ctx.pop()

    def invite(self, services):
        req = AccessRequest(token=new_token(), client_name='Test Co', expires_at=expiry_from_now())
        req.services = services
        db.session.add(req)
        db.session.commit()
        return req

    def test_pause_form_api_health_and_legacy(self):
        self.assertFalse(config.GBP_ENABLED)  # even with the old env flag true
        self.assertEqual(config.scopes_for(['gbp']), [])
        page = self.client.get('/tools/google-access/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Google Business Profile is paused', page.text)
        self.assertNotIn('value="gbp"', page.text)
        bad = self.client.post('/tools/google-access/api/requests', json={
            'client_type':'existing', 'client_name':'Test Co', 'services':['gbp']})
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(self.client.get('/tools/google-access/api/health').json['gbp_enabled'])
        req = self.invite(['ga4', 'gbp'])
        db.session.add(AccessGrant(request_id=req.id, service='gbp', status='waiting'))
        db.session.commit()
        detail = self.client.get(f'/tools/google-access/r/{req.id}')
        self.assertEqual(detail.status_code, 200)
        self.assertIn('Google Business Profile (paused)', detail.text)
        for suffix in ('', '/done'):
            page = self.client.get(f'/connect/{req.token}{suffix}')
            self.assertEqual(page.status_code, 200)
            self.assertNotIn('Business Profile', page.text)
        response = self.client.post(f'/connect/{req.token}/start', data={'service':'gbp'})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('accounts.google.com', response.location)
        self.assertEqual(OAuthState.query.count(), 0)
        marked = self.client.post(f'/tools/google-access/api/requests/{req.id}/mark',
                                  json={'service':'gbp', 'status':'skipped'})
        self.assertEqual(marked.status_code, 200)

    def test_oauth_success_and_old_state_cannot_grant_gbp(self):
        req = self.invite(['ga4', 'gtm', 'gbp', 'search_console'])
        from werkzeug.datastructures import MultiDict
        started = self.client.post(f'/connect/{req.token}/start', data=MultiDict(
            [('service', s) for s in req.services]))
        query = parse_qs(urlsplit(started.location).query)
        self.assertEqual(query['redirect_uri'], ['https://hub.example/connect/callback'])
        self.assertNotIn('business.manage', query['scope'][0])
        state = OAuthState.query.one()
        import json
        state.services = json.dumps(req.services)  # consent begun before the pause
        db.session.commit()
        with patch.object(gc, 'exchange_code', return_value={'access_token':'fake'}), \
             patch.object(gc, 'userinfo_email', return_value='owner@example.com'), \
             patch.object(gc, 'granted_scopes', return_value=config.scopes_for(['ga4','gtm'])), \
             patch.object(gc, 'ga4_list_properties', return_value=[{'resource':'properties/1','name':'Web'}]), \
             patch.object(gc, 'gtm_list_accounts', return_value=[{'account_id':'2','name':'Tags'}]), \
             patch.object(gc, 'ga4_add_user') as ga4, patch.object(gc, 'gtm_add_user') as gtm, \
             patch.object(gc, 'gbp_list_accounts') as gbp, patch.object(gc, 'revoke') as revoke:
            response = self.client.get('/connect/callback', query_string={'state':state.state,'code':'fake-code'})
            self.assertEqual(response.status_code, 302)
            ga4.assert_called_once()
            gtm.assert_called_once()
            gbp.assert_not_called()
            revoke.assert_called_once_with('fake')
        self.assertEqual(req.grant_for('ga4').status, 'granted')
        self.assertEqual(req.grant_for('gtm').status, 'granted')
        self.assertEqual(req.grant_for('search_console').status, 'waiting')
        self.assertEqual(req.grant_for('gbp').status, 'skipped')
        receipt = self.client.get(response.location)
        self.assertEqual(receipt.status_code, 200)
        self.assertIn('Finish Search Console', receipt.text)
        replay = self.client.get('/connect/callback', query_string={'state':state.state,'code':'again'})
        self.assertEqual(replay.status_code, 400)

    def test_create_active_invite_and_client_status(self):
        with patch.object(routes, '_registry_match', return_value=({'name':'Test Co', 'url':'https://test.example'}, '')):
            result = self.client.post('/tools/google-access/api/requests', json={
                'client_type':'existing', 'client_name':'Test Co', 'services':['ga4','gtm','search_console','gbp']})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json['link'].startswith('https://hub.example/connect/'))
        req = db.session.get(AccessRequest, result.json['id'])
        self.assertEqual(req.services, ['ga4','gtm','search_console'])
        page = self.client.get(urlsplit(result.json['link']).path)
        self.assertEqual(page.status_code, 200)
        self.assertIn('Google Analytics', page.text)
        self.assertIn('Google Tag Manager', page.text)
        self.assertIn('Google Search Console', page.text)
        self.assertNotIn('Business Profile', page.text)

    def test_partial_failure_preserves_success_and_revokes(self):
        req = self.invite(['ga4','gtm'])
        from werkzeug.datastructures import MultiDict
        self.client.post(f'/connect/{req.token}/start', data=MultiDict([('service','ga4'),('service','gtm')]))
        state = OAuthState.query.one()
        with patch.object(gc, 'exchange_code', return_value={'access_token':'fake'}), \
             patch.object(gc, 'userinfo_email', return_value='owner@example.com'), \
             patch.object(gc, 'granted_scopes', return_value=config.scopes_for(['ga4','gtm'])), \
             patch.object(gc, 'ga4_list_properties', return_value=[{'resource':'properties/1','name':'Web'}]), \
             patch.object(gc, 'ga4_add_user'), \
             patch.object(gc, 'gtm_list_accounts', side_effect=gc.GoogleError('permission denied')), \
             patch.object(gc, 'revoke') as revoke:
            response = self.client.get('/connect/callback', query_string={'state':state.state,'code':'fake-code'})
            self.assertEqual(response.status_code, 302)
            revoke.assert_called_once_with('fake')
        self.assertEqual(req.grant_for('ga4').status, 'granted')
        self.assertEqual(req.grant_for('gtm').status, 'failed')
        receipt = self.client.get(response.location)
        self.assertEqual(receipt.status_code, 200)
        self.assertIn('Needs us', receipt.text)

    def test_manual_only_and_invalid_state(self):
        req = self.invite(['search_console'])
        result = self.client.post(f'/connect/{req.token}/start', data={'service':'search_console'})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(OAuthState.query.count(), 0)
        self.assertEqual(req.grant_for('search_console').status, 'waiting')
        self.assertEqual(self.client.get('/connect/callback?state=invalid&code=fake').status_code, 400)

    def test_failed_exchange_revokes_token_and_shows_error(self):
        req = self.invite(['ga4'])
        self.client.post(f'/connect/{req.token}/start', data={'service':'ga4'})
        state = OAuthState.query.one()
        with patch.object(gc, 'exchange_code', side_effect=gc.GoogleError('bad code')), \
             patch.object(gc, 'revoke') as revoke:
            response = self.client.get('/connect/callback', query_string={'state':state.state,'code':'bad'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('We could not finish', response.text)
            revoke.assert_called_once_with(None)

if __name__ == '__main__':
    unittest.main(verbosity=2)
