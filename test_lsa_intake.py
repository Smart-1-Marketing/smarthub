import ast
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from datetime import timedelta
import test_ads_local_services as fixture
from modules.lsa_ads.app import app, PUBLIC_PREFIXES
from modules.lsa_ads import intake
from werkzeug.test import Client
from werkzeug.wrappers import Response

class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        self.plan = self.client.post('/api/local-services/setups', json={'business_name':'Team draft','step':2}).get_json()
        self.endpoint = '/api/setups/'+self.plan['id']+'/intake'
        self.link = self.client.post(self.endpoint,json={'action':'create'}).get_json()['path'].removeprefix('/tools/lsa')
        self.answers = dict(business_name='Client business', phone='555 555 1234',country='US',postal_code='10001',
            services='AC repair',areas='New York',schedule='Weekdays 9-5 Eastern',weekly_budget='300',
            contact_name='Client Name',contact_email='client@example.com',business_address='1 Sample St',budget_currency='USD')

    def submit(self, **changes):
        revision=self.client.get(self.endpoint).get_json()['revision']
        return self.client.post(self.link,data={**self.answers,'revision':str(revision),**changes})

    def test_submit_persist_review_import(self):
        page=self.client.get(self.link)
        self.assertEqual(page.status_code,200)
        self.assertNotIn('Team draft',page.get_data(as_text=True))
        self.assertEqual(self.submit().status_code,303)
        response=self.client.get(self.endpoint).get_json()
        self.assertEqual(response['answers']['contact_name'],'Client Name')
        self.assertNotIn('token_hash',response)
        setups=self.client.get('/api/local-services/setups').get_json()['setups']
        self.assertEqual(next(s for s in setups if s['id']==self.plan['id'])['business_name'],'Team draft')
        applied=self.client.post(self.endpoint,json={'action':'apply','revision':self.plan['revision'],'response_revision':response['revision']})
        self.assertEqual(applied.status_code,200)
        self.assertEqual(applied.get_json()['setup']['business_name'],'Client business')
        self.assertEqual(applied.get_json()['setup']['step'],2)
        self.assertIn('client@example.com',self.client.get(self.link).get_data(as_text=True))

    def test_intake_activity_names_the_business(self):
        with patch.object(intake.audit, 'log') as logged:
            self.client.post(self.endpoint, json={'action':'create'})
            self.assertEqual(logged.call_args.kwargs['client'], 'Team draft')
            self.client.post(self.endpoint, json={'action':'revoke'})
            self.assertEqual(logged.call_args.kwargs['client'], 'Team draft')

    def test_revocation_rotation_and_expiry(self):
        self.client.post(self.endpoint,json={'action':'revoke'})
        self.assertEqual(self.client.get(self.link).status_code,404)
        self.assertEqual(self.submit().status_code,404)
        new=self.client.post(self.endpoint,json={'action':'create'}).get_json()['path'].removeprefix('/tools/lsa')
        self.assertEqual(self.client.get(self.link).status_code,404)
        self.assertEqual(self.client.get(new).status_code,200)
        with patch.object(intake,'now',return_value=intake.now()+timedelta(days=31)):
            self.assertEqual(self.client.get(new).status_code,404)

    def test_validation_xss_and_limits(self):
        self.assertEqual(self.submit(contact_email='bad').status_code,400)
        self.assertEqual(self.submit(services='').status_code,400)
        self.assertEqual(self.submit(weekly_budget='NaN').status_code,400)
        self.assertEqual(self.submit(profile_url='javascript:alert(1)').status_code,400)
        self.assertEqual(self.submit(contact_name='x'*201).status_code,400)
        self.assertEqual(self.client.post(self.link,data={'services':'x'*40000}).status_code,413)
        self.assertNotIn('submitted_at',self.client.get(self.endpoint).get_json())
        self.assertEqual(self.submit(business_name='<script>alert(1)</script>').status_code,303)
        self.assertIn('&lt;script&gt;',self.client.get(self.link).get_data(as_text=True))

    def test_conflicts_preserve_team_and_client_edits(self):
        revision=self.client.get(self.endpoint).get_json()['revision']
        self.submit()
        self.assertEqual(self.submit(revision=str(revision)).status_code,409)
        response=self.client.get(self.endpoint).get_json()
        self.client.post('/api/local-services/setups',json={**self.plan,'business_name':'Team changed'})
        self.assertEqual(self.client.post(self.endpoint,json={'action':'apply','revision':self.plan['revision'],'response_revision':response['revision']}).status_code,409)
        self.assertEqual(self.client.post(self.endpoint,json={'action':'apply','revision':2,'response_revision':revision}).status_code,409)

    def test_link_scoping_and_headers(self):
        other=self.client.post('/api/local-services/setups',json={'step':1}).get_json()
        wrong=self.link.replace(self.plan['id'],other['id'])
        self.assertEqual(self.client.get(wrong).status_code,404)
        response=self.client.get(self.link)
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.assertEqual(response.headers['Referrer-Policy'],'no-referrer')
        self.assertEqual(response.headers['X-Robots-Tag'],'noindex, nofollow')

    def test_real_auth_guard_keeps_staff_routes_private(self):
        source=Path('wsgi.py').read_text(encoding='utf-8')
        tree=ast.parse(source)
        guard=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='AuthGuard')
        scope={'auth':SimpleNamespace(user_from_environ=lambda e:None)}
        exec(compile(ast.Module(body=[guard],type_ignores=[]),'wsgi.py','exec'),scope)
        client=Client(scope['AuthGuard'](app,'/tools/lsa',public_prefixes=PUBLIC_PREFIXES),Response)
        self.assertEqual(client.get(self.link).status_code,200)
        self.assertEqual(client.get(self.endpoint).status_code,401)
        self.assertEqual(client.post(self.endpoint,json={'action':'create'}).status_code,401)
        self.assertEqual(client.post('/api/campaign',json={}).status_code,401)
        self.assertEqual(client.get('/local-services').status_code,302)
        self.assertIn('"/tools/lsa": _mount(lsa_ads_app, "/tools/lsa", public_prefixes=("/intake/",))', source)

    def test_setup_scripts_parse_and_intake_controls(self):
        html=self.client.get('/local-services').get_data(as_text=True)
        self.assertIn('Save setup &amp; create client link',html)
        self.assertIn('New campaign setup:',html)
        node=shutil.which('node')
        if not node:self.skipTest('Node unavailable')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'setup.js'
            path.write_text('\n'.join(re.findall(r'<script>(.*?)</script>',html,re.S)),encoding='utf-8')
            result=subprocess.run([node,'--check',str(path)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

if __name__=='__main__':unittest.main()
