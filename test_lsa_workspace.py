import re
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from test_ads_local_services import LocalServicesTests, lsa
from modules.lsa_ads.app import app

class WorkspaceTests(LocalServicesTests):
    def setUp(self):
        self.client = app.test_client()

    def test_workspace_and_isolation(self):
        response = self.client.get('/')
        html = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Product Success', html)
        self.assertIn('setInterval(', html)
        self.assertNotIn('Campaign generator', html)
        self.assertEqual(self.client.get('/settings').status_code, 404)
        self.assertIn('/tools/lsa/api/local-services', self.client.get('/local-services').get_data(as_text=True))

    def test_rendered_scripts_parse(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is not installed')
        html = self.client.get('/').get_data(as_text=True)
        scripts = '\n'.join(re.findall(r'<script>(.*?)</script>', html, re.S))
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'workspace.js'
            script.write_text(scripts, encoding='utf-8')
            result = subprocess.run([node, '--check', str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_csv_partial_failure_and_formula(self):
        def search(cid, query, **kw):
            if 'FROM customer' in query:
                return [{'customer': {'descriptiveName': '=HYPERLINK("bad")', 'currencyCode': 'USD', 'timeZone': 'UTC'}}]
            if 'metrics.cost_micros' in query:
                raise lsa.GoogleAdsError('Unavailable spend')
            return self.fake_search(cid, query, **kw)
        with patch.object(lsa.google_ads, 'search', side_effect=search):
            response = self.client.get('/api/report.csv?customer_id=1234567890')
        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        self.assertIn("'=HYPERLINK", text)
        self.assertIn('LSA spend,Unavailable', text)
        self.assertIn('Unavailable source: spend', text)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def campaign(self):
        return [{'campaign': {'id': '7', 'status': 'PAUSED'}, 'campaignBudget': {
            'resourceName': 'customers/1234567890/campaignBudgets/8', 'amountMicros': '10000000', 'period': 'DAILY', 'explicitlyShared': False}}]

    def payload(self, **values):
        return dict(customer_id='1234567890', campaign_id='7', **values)

    def test_status_confirmation_scope_and_stale(self):
        body = self.payload(action='ENABLED', confirmation='ENABLE', previous_status='PAUSED')
        with patch.object(lsa.google_ads, 'search', return_value=self.campaign()) as read, patch.object(lsa.google_ads, 'set_campaign_status', return_value={}) as mutate:
            self.assertEqual(self.client.post('/api/campaign', json={**body, 'confirmation': ''}).status_code, 400)
            mutate.assert_not_called()
            self.assertEqual(self.client.post('/api/campaign', json={**body, 'previous_status': 'ENABLED'}).status_code, 409)
            mutate.assert_not_called()
            self.assertEqual(self.client.post('/api/campaign', json=body).status_code, 200)
            self.assertIn("advertising_channel_type = 'LOCAL_SERVICES'", read.call_args.args[1])
            mutate.assert_called_once()
        with patch.object(lsa.google_ads, 'search', return_value=[]), patch.object(lsa.google_ads, 'set_campaign_status') as mutate:
            self.assertEqual(self.client.post('/api/campaign', json=body).status_code, 409)
            mutate.assert_not_called()

    def test_budget_validation_and_micros(self):
        body = self.payload(action='budget', confirmation='UPDATE', amount='12.34', previous_budget=10.0, budget_period='DAILY')
        with patch.object(lsa.google_ads, 'search', return_value=self.campaign()), patch.object(lsa.google_ads, 'request', return_value={}) as mutate:
            for amount in ['NaN', '-1', '0', '12.345', 'Infinity']:
                self.assertEqual(self.client.post('/api/campaign', json={**body, 'amount': amount}).status_code, 400)
            self.assertEqual(self.client.post('/api/campaign', json={**body, 'previous_budget': 9}).status_code, 409)
            mutate.assert_not_called()
            self.assertEqual(self.client.post('/api/campaign', json=body).status_code, 200)
            self.assertEqual(mutate.call_args.args[2]['operations'][0]['update']['amountMicros'], '12340000')
        shared = self.campaign()
        shared[0]['campaignBudget']['explicitlyShared'] = True
        with patch.object(lsa.google_ads, 'search', return_value=shared), patch.object(lsa.google_ads, 'request') as mutate:
            self.assertEqual(self.client.post('/api/campaign', json=body).status_code, 409)
            mutate.assert_not_called()

if __name__ == '__main__':
    unittest.main()
