"""Offline behavior tests: no provider calls or production records.
Run with python test_industry_prospects.py.
"""
import os
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

_TEMP = tempfile.TemporaryDirectory(prefix='industry-prospects-')
os.environ['DATA_DIR'] = _TEMP.name
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(_TEMP.name, 'test.sqlite3')
os.environ['SCHEDULER_ENABLED'] = '0'
os.environ['APOLLO_API_KEY'] = 'test-key'
os.environ['GHL_PRIVATE_TOKEN'] = 'test-token'
os.environ['GHL_LEAD_LOCATION_ID'] = 'test-location'
os.environ['PROSPECT_PAID_ENABLED'] = '1'
os.environ.pop('GHL_COMPANY_ID', None)
os.environ.pop('SUITE_COMPANY_ID', None)

from flask import Flask
from hub import industry_prospects as service
from hub import industry_prospect_providers as provider
from hub import industry_prospect_store as store
from hub.industry_prospect_routes import bp
from hub.extensions import shared_engine

PERSON = {'id': 'p1', 'first_name': 'Ada', 'last_name': 'Lovelace',
          'email': 'Ada@Example.com', 'email_status': 'verified',
          'title': 'Owner', 'organization': {'name': 'Example HVAC', 'primary_domain': 'example.com'}}
VERDICT = {'address': 'ada@example.com', 'result': 'deliverable', 'risk': 'low'}

class BuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store.init_store()

    @classmethod
    def tearDownClass(cls):
        shared_engine().dispose()

    def setUp(self):
        with shared_engine().begin() as conn:
            conn.execute(store.records.delete())
        self.scope_patch = patch.object(service, 'scope', return_value='scope')
        self.scope_patch.start()
        self.network_patch = patch.object(provider.requests, 'request', side_effect=AssertionError('Unexpected live request'))
        self.network_patch.start()
        self.addCleanup(self.scope_patch.stop)
        self.addCleanup(self.network_patch.stop)
        self.fresh()
        store.put('campaign:c1', 'campaign', {'id': 'c1', 'created': time.time(), 'industry': 'hvac',
                  'name': 'Ohio HVAC', 'landing_page': 'https://example.com/hvac', 'filters': {}})
        store.put('candidate:c1:p1', 'candidate', {'campaign': 'c1', 'person': dict(PERSON), 'seen': time.time()})

    def fresh(self):
        store.put('sync', 'state', {'phase': 'complete', 'scope': 'scope', 'completed': time.time()})

    def plan(self):
        plan = service.quote('c1', ['p1'], 'tester')
        service.approve(plan['id'], 'tester', True)
        return plan['id']

    def test_preview_does_not_enable_paid_operations(self):
        with patch.dict(os.environ, {'PROSPECT_PAID_ENABLED': '0'}):
            plan = service.quote('c1', ['p1'], 'tester')
            self.assertFalse(plan['approved'])
            self.assertEqual(plan['max_verifications'], 1)
            with self.assertRaises(store.ProspectError):
                service.approve(plan['id'], 'tester', True)

    def queue(self):
        plan = self.plan()
        with patch.dict(os.environ, {'HUB_SCHEDULER': '1'}):
            return service.queue_purchase(plan, 'tester')

    def test_purchase_queue_requires_approval_and_does_not_spend(self):
        plan = service.quote('c1', ['p1'], 'tester')
        with patch.dict(os.environ, {'HUB_SCHEDULER': '1'}):
            with self.assertRaises(store.ProspectError): service.queue_purchase(plan['id'], 'tester')
            service.approve(plan['id'], 'tester', True)
            job = service.queue_purchase(plan['id'], 'tester')
            self.assertEqual(service.queue_purchase(plan['id'], 'tester'), job)
        self.assertEqual(store.rows('purchase'), [])

    def test_purchase_worker_uses_saved_approval_and_never_imports(self):
        job = self.queue()
        with patch.object(provider, 'enrich', return_value={'person': PERSON}) as reveal, \
             patch.object(provider, 'duplicate', return_value=False), \
             patch.object(provider, 'verify', return_value=VERDICT), \
             patch.object(provider, 'import_contact') as importer:
            with store.operation('scheduler', 'purchase'):
                result = service.purchase_step()
                self.assertIsNone(service.purchase_step())
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(reveal.call_count, 1)
        importer.assert_not_called()
        self.assertEqual(store.get('purchase:p1')['status'], 'ready')
        self.assertNotIn('scope', service.purchase_jobs('c1')[0])

    def test_purchase_worker_pauses_uncertain_without_retry(self):
        self.queue()
        with patch.object(provider, 'enrich', side_effect=store.ProspectError('Timeout')) as reveal:
            self.assertEqual(service.purchase_step()['status'], 'paused')
            self.assertIsNone(service.purchase_step())
            self.assertEqual(reveal.call_count, 1)
        self.assertEqual(store.get('purchase:p1')['status'], 'review_required')

    def test_purchase_worker_rechecks_paid_gate(self):
        self.queue()
        with patch.dict(os.environ, {'PROSPECT_PAID_ENABLED': '0'}):
            self.assertEqual(service.purchase_step()['status'], 'paused')
        self.assertEqual(store.rows('purchase'), [])

    def test_purchase_stop_requires_owner_and_prevents_remaining_calls(self):
        job = self.queue()
        with self.assertRaises(store.ProspectError): service.pause_purchase(job['id'], 'other')
        self.assertEqual(service.pause_purchase(job['id'], 'tester')['status'], 'stopped')
        self.assertIsNone(service.purchase_step())
        self.assertEqual(store.rows('purchase'), [])

    def test_read_checks_do_not_write_or_spend(self):
        with patch.object(provider, 'saved_page', return_value=([], 0)), \
             patch.object(provider, 'apollo', return_value={'people': [], 'total_entries': 0}) as apollo, \
             patch.object(provider, 'ghl_page', side_effect=store.ProspectError('Denied')):
            result = service.test_connections('tester')
        self.assertEqual([r['ok'] for r in result['checks']], [True, True, False])
        self.assertEqual(apollo.call_args.args[0], 'mixed_people/api_search')
        self.assertEqual(store.rows('purchase'), [])
        self.assertNotIn('scope', result)

    def test_manual_background_sync_survives_page_close(self):
        with patch.dict(os.environ, {'HUB_SCHEDULER': '1', 'PROSPECT_AUTO_SYNC': '0'}):
            job = service.queue_sync('tester')
            self.assertEqual(service.queue_sync('tester'), job)
            with patch.object(service, 'sync_step', return_value={'phase': 'complete'}):
                service.scheduled_step(Flask(__name__))
            self.assertEqual(store.get('sync-job')['status'], 'complete')

    def test_background_error_pauses_without_retry(self):
        with patch.dict(os.environ, {'HUB_SCHEDULER': '1', 'PROSPECT_AUTO_SYNC': '1'}):
            service.queue_sync('tester')
            with patch.object(service, 'sync_step', side_effect=store.ProspectError('Provider unavailable')) as step:
                service.scheduled_step(Flask(__name__))
                service.scheduled_step(Flask(__name__))
                self.assertEqual(step.call_count, 1)
            self.assertEqual(store.get('sync-job')['status'], 'paused')
            self.assertIsNone(store.get('operation-lock'))

    def test_recovery_never_offers_repurchase_of_saved_email(self):
        hint = service.recovery_hint({'id': 'p1', 'status': 'verify_pending', 'email': 'a@example.com'})
        self.assertIn('do not purchase', hint)

    def buy(self, plan=None):
        plan = plan or self.plan()
        with patch.object(provider, 'enrich', return_value={'person': PERSON}) as reveal, \
             patch.object(provider, 'duplicate', return_value=False), \
             patch.object(provider, 'verify', return_value=VERDICT):
            result = service.buy_one(plan, 'p1', 'tester')
        return result, reveal.call_count

    def test_paid_reveal_is_idempotent_and_stored_before_call(self):
        plan = self.plan()
        def reveal(pid):
            self.assertEqual(store.get('purchase:p1')['status'], 'reveal_pending')
            return {'person': PERSON}
        with patch.object(provider, 'enrich', side_effect=reveal) as enrich, \
             patch.object(provider, 'duplicate', return_value=False), \
             patch.object(provider, 'verify', return_value=VERDICT) as verify:
            first = service.buy_one(plan, 'p1', 'tester')
            second = service.buy_one(plan, 'p1', 'tester')
        self.assertEqual(first['status'], 'ready')
        self.assertEqual(second['status'], 'ready')
        self.assertEqual(enrich.call_count, 1)
        self.assertEqual(verify.call_count, 1)

    def test_timeout_is_not_retried(self):
        plan = self.plan()
        with patch.object(provider, 'enrich', side_effect=store.ProspectError('timeout')) as enrich:
            self.assertEqual(service.buy_one(plan, 'p1', 'tester')['status'], 'review_required')
            self.assertEqual(service.buy_one(plan, 'p1', 'tester')['status'], 'review_required')
        self.assertEqual(enrich.call_count, 1)

    def test_unknown_crash_attempt_cannot_be_bought_again(self):
        plan = self.plan()
        store.put('purchase:p1', 'purchase', {'id': 'p1', 'status': 'reveal_pending'})
        with patch.object(provider, 'enrich') as enrich:
            self.assertEqual(service.buy_one(plan, 'p1', 'tester')['status'], 'reveal_pending')
            enrich.assert_not_called()

    def test_saved_person_blocked_before_credit_spend(self):
        store.put('saved:a1', 'saved', {'person_id': 'p1'})
        with self.assertRaises(store.ProspectError):
            self.plan()

    def test_name_company_duplicate_and_case_insensitive_email(self):
        row = service.compact(PERSON)
        row.update(person_id='', emails=[])
        store.put('ghl:a1', 'suppression', row)
        self.assertEqual(service.suppression_reason(PERSON), 'Existing name and company')
        row.update(name='', emails=['ada@example.com'], dnd=True)
        store.put('ghl:a1', 'suppression', row)
        self.assertIn('do not contact', service.suppression_reason(PERSON))

    def test_partial_name_does_not_suppress_unrelated_person(self):
        store.put('ghl:a1', 'suppression', {'name': 'Ada Other', 'company': 'Example HVAC'})
        self.assertEqual(service.suppression_reason({**PERSON, 'last_name': ''}), '')

    def test_post_reveal_duplicate_skips_paid_verification(self):
        plan = self.plan()
        with patch.object(provider, 'enrich', return_value={'person': PERSON}), \
             patch.object(provider, 'duplicate', return_value=True), \
             patch.object(provider, 'verify') as verify:
            self.assertEqual(service.buy_one(plan, 'p1', 'tester')['status'], 'suppressed')
            verify.assert_not_called()

    def test_verification_rejects_uncertain_or_mismatched_address(self):
        for verdict in [{}, {**VERDICT, 'risk': 'unknown'}, {**VERDICT, 'result': 'catch_all'},
                        {**VERDICT, 'address': 'other@example.com'},
                        {**VERDICT, 'leadConnectorRecommendation': {'isEmailValid': False}}]:
            self.assertFalse(provider.deliverable(verdict, 'ada@example.com'))
        self.assertTrue(provider.deliverable(VERDICT, 'ada@example.com'))

    def test_low_quality_verification_holds_import(self):
        plan = self.plan()
        with patch.object(provider, 'enrich', return_value={'person': PERSON}), \
             patch.object(provider, 'duplicate', return_value=False), \
             patch.object(provider, 'verify', return_value={**VERDICT, 'result': 'unknown'}):
            self.assertEqual(service.buy_one(plan, 'p1', 'tester')['status'], 'held')
        with self.assertRaises(store.ProspectError):
            service.import_one('p1', 'tester')

    def test_import_rechecks_live_duplicate_and_is_idempotent(self):
        self.buy()
        with patch.object(provider, 'duplicate', return_value=False), \
             patch.object(provider, 'import_contact', return_value='contact1') as create:
            self.assertEqual(service.import_one('p1', 'tester')['status'], 'imported')
            self.assertEqual(service.import_one('p1', 'tester')['contact_id'], 'contact1')
        self.assertEqual(create.call_count, 1)
        self.assertTrue(store.get('imported:p1'))

    def test_import_duplicate_never_updates_existing_ghl_contact(self):
        self.buy()
        with patch.object(provider, 'duplicate', return_value=True), patch.object(provider, 'import_contact') as create:
            self.assertEqual(service.import_one('p1', 'tester')['status'], 'suppressed')
            create.assert_not_called()

    def test_import_timeout_held_instead_of_retried(self):
        self.buy()
        with patch.object(provider, 'duplicate', return_value=False), \
             patch.object(provider, 'import_contact', side_effect=store.ProspectError('timeout')) as create:
            self.assertEqual(service.import_one('p1', 'tester')['status'], 'review_required')
            with self.assertRaises(store.ProspectError):
                service.import_one('p1', 'tester')
        self.assertEqual(create.call_count, 1)

    def test_freshness_scope_and_approval_required(self):
        store.put('sync', 'state', {'phase': 'ghl', 'scope': 'scope', 'completed': time.time()})
        with self.assertRaises(store.ProspectError): self.plan()
        store.put('sync', 'state', {'phase': 'complete', 'scope': 'scope', 'completed': 0})
        with self.assertRaises(store.ProspectError): self.plan()
        self.fresh()
        plan = service.quote('c1', ['p1'], 'tester')
        with self.assertRaises(store.ProspectError): service.buy_one(plan['id'], 'p1', 'tester')
        with self.assertRaises(store.ProspectError): service.approve(plan['id'], 'other', True)
        with self.assertRaises(store.ProspectError): service.approve(plan['id'], 'tester', False)
        with patch.dict(os.environ, {'PROSPECT_PAID_ENABLED': '0'}):
            with self.assertRaises(store.ProspectError): service.approve(plan['id'], 'tester', True)

    def test_concurrent_worker_lock(self):
        with store.operation('one', 'buy'):
            with self.assertRaises(store.ProspectError):
                with store.operation('two', 'buy'): pass
        self.assertIsNone(store.get('operation-lock'))

    def test_sync_only_complete_after_both_sources(self):
        contact = {'id': 'g1', 'firstName': 'Existing', 'lastName': 'Owner',
                   'email': 'existing@example.com', 'companyName': 'Existing Co', 'dnd': True}
        with patch.object(provider, 'locations', return_value=['test-location']), \
             patch.object(provider, 'ghl_page', return_value={'contacts': [contact], 'total': 1}), \
             patch.object(provider, 'bulk_save', return_value=[{'id': 'a1', 'person_id': 'saved-person'}]), \
             patch.object(provider, 'saved_page', return_value=([{'id': 'a1', 'person_id': 'saved-person'}], 1)):
            service.start_sync('tester')
            self.assertEqual(service.sync_step()['phase'], 'apollo')
            with self.assertRaises(store.ProspectError): service.ready()
            self.assertEqual(service.sync_step()['phase'], 'complete')
        self.assertTrue(store.get('ghl:test-location:g1')['dnd'])
        self.assertEqual(service.suppression_reason({'id': 'saved-person'}), 'Existing GHL / Apollo contact')

    def test_partial_provider_page_is_not_complete(self):
        with patch.object(provider, 'locations', return_value=['test-location']), \
             patch.object(provider, 'ghl_page', return_value={'contacts': [{'id':'g1'}], 'total':101}):
            service.start_sync('tester')
            with self.assertRaises(store.ProspectError): service.sync_step()
        self.assertEqual(store.get('sync')['phase'], 'ghl')

    def test_selection_cannot_include_arbitrary_or_unhashable_ids(self):
        for ids in [['missing'], [['p1']], ['p1', 'p1']]:
            with self.assertRaises(store.ProspectError): service.quote('c1', ids, 'tester')

    def test_failed_sync_never_looks_complete(self):
        with patch.object(provider, 'locations', return_value=['test-location']), \
             patch.object(provider, 'ghl_page', side_effect=store.ProspectError('offline')):
            service.start_sync('tester')
            with self.assertRaises(store.ProspectError): service.sync_step()
        self.assertEqual(store.get('sync')['phase'], 'ghl')
        with self.assertRaises(store.ProspectError): service.ready()

    def test_http_boundary_and_billing_flags(self):
        response = Mock(ok=True)
        response.json.return_value = {'person': PERSON}
        with patch.object(provider.requests, 'request', return_value=response) as request:
            provider.enrich('p1')
            kwargs = request.call_args.kwargs
            self.assertEqual(kwargs['params']['reveal_phone_number'], 'false')
            self.assertEqual(kwargs['params']['run_waterfall_email'], 'false')
            self.assertEqual(kwargs['headers']['x-api-key'], 'test-key')
        response.ok = False
        response.status_code = 429
        with patch.object(provider.requests, 'request', return_value=response) as request:
            with self.assertRaises(store.ProspectError): provider.enrich('p1')
            self.assertEqual(request.call_count, 1)

    def test_routes_auth_csrf_and_template(self):
        app = Flask('hub', template_folder='templates')
        app.register_blueprint(bp)
        app.testing = True
        client = app.test_client()
        with patch('hub.current_user', return_value=None):
            self.assertEqual(client.get('/api/industry-prospects/status').status_code, 401)
            self.assertEqual(client.get('/sales/industry-prospects').status_code, 302)
        with patch('hub.current_user', return_value='tester'):
            self.assertEqual(client.post('/api/industry-prospects/action', json={'action': 'create'}).status_code, 403)
            self.assertEqual(client.post('/api/industry-prospects/action', json=[], headers={'X-Prospect-Request':'1'}).status_code, 400)
            self.assertEqual(client.post('/api/industry-prospects/action', json={}, headers={'X-Prospect-Request':'1', 'Sec-Fetch-Site':'cross-site'}).status_code, 403)
            self.assertEqual(client.get('/api/industry-prospects/status').status_code, 200)
            response = client.get('/sales/industry-prospects')
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'Industry Prospect Builder', response.data)
            self.assertIn(b'/assets/industry-prospects.js', response.data)

if __name__ == '__main__':
    unittest.main(verbosity=2)
