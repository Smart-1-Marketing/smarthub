import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(tempfile.mkdtemp()) / 'lsa.db')
os.environ['HUB_DATA_DIR'] = tempfile.mkdtemp()
from modules.ads_builder import local_services as lsa
from modules.ads_builder.app import app
from modules.ads_builder.google_ads import GoogleAdsError


class LocalServicesTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_draft_roundtrip_and_conflict(self):
        response = self.client.post('/api/local-services/setups', json={
            'business_name': 'Example HVAC', 'mode': 'new', 'step': 2, 'weekly_budget': '300'})
        self.assertEqual(response.status_code, 200)
        saved = response.get_json()
        self.assertEqual(saved['revision'], 1)
        self.assertIn(saved, self.client.get('/api/local-services/setups').get_json()['setups'])
        changed = self.client.post('/api/local-services/setups', json={**saved, 'step': 3}).get_json()
        self.assertEqual(changed['revision'], 2)
        self.assertEqual(self.client.post('/api/local-services/setups', json=saved).status_code, 409)

    def test_validation(self):
        for body in [{'customer_id': 'abc1234567890'}, {'weekly_budget': 'NaN'},
                     {'weekly_budget': '-1'}, {'step': 6}, {'website': 'javascript:alert(1)'}, []]:
            self.assertEqual(self.client.post('/api/local-services/setups', json=body).status_code, 400, body)
        self.assertEqual(self.client.get('/api/local-services/review?customer_id=1234567890&days=abc').status_code, 400)

    def test_connection_verifies_access(self):
        accounts = [{'id': '1234567890', 'name': 'Client'},
                    {'id': '1111111111', 'is_manager': True, 'error': 'Access denied'}]
        with patch.object(lsa.google_ads, 'list_client_accounts', return_value=accounts) as read:
            result = self.client.get('/api/local-services/connection').get_json()
        self.assertFalse(result['verified'])
        self.assertEqual(len(result['accounts']), 1)
        self.assertTrue(read.call_args.kwargs['force'])

    def fake_search(self, cid, query, **kwargs):
        if 'FROM customer' in query:
            return [{'customer': {'id': cid, 'currencyCode': 'USD', 'timeZone': 'America/New_York'}}]
        if 'local_services_enabled' in query:
            return []
        if 'FROM campaign' in query and 'metrics.cost_micros' not in query:
            return [{'campaign': {'id': '7', 'status': 'PAUSED', 'advertisingChannelType': 'LOCAL_SERVICES'},
                     'campaignBudget': {'amountMicros': '10000000', 'period': 'DAILY'}}]
        if 'metrics.cost_micros' in query:
            return [{'campaign': {'id': '7'}, 'metrics': {'costMicros': '90000000'}},
                    {'campaign': {'id': '9'}, 'metrics': {'costMicros': '800000000'}}]
        if 'FROM local_services_lead' in query:
            return [{'localServicesLead': {'id': '1', 'leadCharged': True, 'leadStatus': 'NEW'}}]
        return []

    def test_only_lsa_spend_and_preserved_metadata(self):
        with patch.object(lsa.google_ads, 'search', side_effect=self.fake_search):
            result = lsa.review_account('123-456-7890')
        self.assertEqual(result['spend'], 90)
        self.assertEqual(result['charged_leads'], 1)
        self.assertEqual(result['campaigns'][0]['budget_period'], 'DAILY')
        self.assertTrue(any('paused' in a for a in result['advice']))

    def test_failed_read_is_unknown(self):
        def search(cid, query, **kw):
            if 'FROM local_services_lead' in query or 'local_services_enabled' in query:
                raise GoogleAdsError('Read failed')
            return self.fake_search(cid, query, **kw)
        with patch.object(lsa.google_ads, 'search', side_effect=search):
            result = lsa.review_account('1234567890')
        self.assertIsNone(result['lead_count'])
        self.assertIsNone(result['spend'])
        self.assertFalse(result['discovery_complete'])
        self.assertEqual(len(result['campaigns']), 1)

    def test_pmax_does_not_mix_legacy_lead_cost(self):
        def search(cid, query, **kw):
            if 'local_services_enabled' in query:
                return [{'campaign': {'id': '9', 'advertisingChannelType': 'PERFORMANCE_MAX'}}]
            return self.fake_search(cid, query, **kw)
        with patch.object(lsa.google_ads, 'search', side_effect=search):
            result = lsa.review_account('1234567890')
        self.assertIsNone(result['cost_per_charged_lead'])
        self.assertEqual(len(result['campaigns']), 2)

    def test_template_and_private_response(self):
        response = self.client.get('/local-services')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        text = response.get_data(as_text=True)
        self.assertIn('Save and continue', text)
        self.assertIn('Finish the required steps in Google', text)
        self.assertNotIn('GOOGLE_ADS_REFRESH_TOKEN', text)


if __name__ == '__main__':
    unittest.main()
