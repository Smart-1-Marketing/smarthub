"""Fast factory tests without booting unrelated vertical applications."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask

TMP = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
os.environ["HUB_DATA_DIR"] = TMP.name
os.environ["DATABASE_URL"] = "sqlite:///" + TMP.name.replace("\\", "/") + "/db.sqlite3"
os.environ["SECRET_KEY"] = "industry-core-test"
from hub.extensions import db
from hub.industry_factory import bp, IndustryPage, QA, resolve_capture
from hub.industry_config import load_pack, selection, triggers
from hub import creative_jobs, auth


class CoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__, template_folder=str(Path(__file__).parent / 'hub/templates'))
        cls.app.config.update(SECRET_KEY='industry-core-test',SQLALCHEMY_DATABASE_URI=os.environ['DATABASE_URL'], TESTING=True)
        db.init_app(cls.app)
        cls.app.register_blueprint(bp)
        with cls.app.app_context(): db.create_all()
        cls.client=cls.app.test_client()

    def setUp(self):
        self.guard=patch('hub.auth.user_from_environ',return_value='Test')
        self.guard.start()

    def tearDown(self): self.guard.stop()

    def draft(self):
        r=self.client.post('/api/industry-factory/pages',json={'industry_id':'roofing','service':'roof_repair','market':'Columbus, OH','radius':25})
        self.assertEqual(r.status_code,201)
        return r.json

    def action(self,p,action,**body):
        return self.client.post('/api/industry-factory/pages/'+p['id']+'/'+action,json=body)

    def test_catalog(self):
        pack=load_pack('roofing')
        rules=triggers(pack)
        self.assertEqual(len(rules),3)
        from hub.weather_triggers import TRIGGERS
        from modules.smartforecast.packs import get_pack
        self.assertEqual(rules[0]['active_conditions'],get_pack('home_services')['rules'][0]['active_conditions'])
        self.assertEqual(rules[1]['rule'],TRIGGERS['high-wind-shingle-risk'].rule)
        with self.assertRaises(ValueError): load_pack('../roofing')
        for bad in ({'service':'nope'},{'radius':0},{'radius':201},{'radius':True},{'conversion_goal':'invalid'},{'market':''},{'trigger_ids':[]},{'trigger_ids':[{}]}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                selection({'industry_id':'roofing','service':'roof_repair','market':'Columbus'}|bad)

    def test_publication_and_clone(self):
        p=self.draft()
        self.assertEqual(self.client.get('/industry/p/'+p['id']).status_code,404)
        self.assertEqual(self.client.get('/industry/widget/'+p['id']+'/embed.js').status_code,404)
        self.assertEqual(self.action(p,'publish',qa=list(QA)).status_code,400)
        for action in ('generate-page','generate-report'):
            self.assertEqual(self.action(p,action).status_code,200)
        self.assertEqual(self.action(p,'publish',qa=[{}]).status_code,400)
        preview=self.client.get('/sales/industry-factory/preview/'+p['id'])
        self.assertEqual(preview.status_code,200)
        self.assertIn('submission is disabled',preview.text)
        self.assertEqual(self.action(p,'publish',qa=list(QA)).status_code,200)
        loader=self.client.get('/industry/widget/'+p['id']+'/embed.js')
        self.assertEqual(loader.status_code,200)
        self.assertEqual(loader.mimetype,'application/javascript')
        self.assertIn('parent_referrer',loader.text)
        self.assertIn('s1embed:height',loader.text)
        self.assertEqual(self.client.get('/industry/widget/'+p['id']+'/embed').status_code,200)
        for path in ('/industry/p/','/industry/widget/'):
            response=self.client.get(path+p['id'])
            self.assertEqual(response.status_code,200)
            self.assertIn('frame-ancestors',response.headers['Content-Security-Policy'])
        report=self.client.get('/industry/p/'+p['id']+'/report')
        self.assertEqual(report.status_code,200)
        self.assertIn('High Wind Shingle Risk',report.text)
        self.assertEqual(self.action(p,'generate-page').status_code,409)
        clone=self.client.post('/api/industry-factory/pages',json=p['config']|{'parent_id':p['id'],'market':'Cincinnati, OH'}).json
        self.assertEqual(clone['version'],2)
        self.assertEqual(clone['states']['report'],'not_started')
        self.assertEqual(clone['qa'],[])

    def test_lead_and_queue(self):
        p=self.draft()
        for action in ('generate-page','generate-report'): self.action(p,action)
        self.action(p,'publish',qa=list(QA))
        with self.app.test_request_context('/api/leads/capture'):
            body=resolve_capture({'meta':{'page_id':p['id'],'industry_id':'spoof','market':'wrong','utm_source':'newsletter'},'fields':{'name':'Alex','company':'Roofer','email':'alex@example.test'}})
            self.assertEqual(body['source'],'landing')
            meta=body['meta'];self.assertEqual(meta['industry_id'],'roofing');self.assertEqual(meta['market'],'Columbus, OH');self.assertEqual(meta['utm_source'],'newsletter')
            for key in ('industry_family','page_version','service','trigger_profile','trigger_ids','creative_profile','conversion_goal','referrer','report_url'): self.assertIn(key,meta)
            from hub.lead_tags import tags_for
            self.assertIn('industry-roofing',tags_for(body))
            from hub.ghl_contacts import payload_for
            with patch('hub.ghl_contacts.location_id',return_value='location'),patch.dict(os.environ,{'GHL_INDUSTRY_MARKET_FIELD_ID':'market-field'}):
                payload=payload_for(body)
            self.assertIn({'id':'market-field','field_value':'Columbus, OH'},payload['customFields'])
            job=creative_jobs.enqueue_for_lead(body|{'id':'factory-test'})
            self.assertIsNotNone(job)
            self.assertEqual(job.kind,'industry_concepts')
            output=creative_jobs.run_one()
            self.assertTrue(output['ok'])
            self.assertEqual(len(job.result()['concepts']),3)
            self.assertIsNone(creative_jobs.enqueue_for_lead({'source':'landing','id':'generic'}))

    def test_auth(self):
        self.guard.stop()
        for path in ('/api/industry-factory/pages',):
            self.assertEqual(self.client.get(path).status_code,401)
        self.assertEqual(self.client.get('/sales/industry-factory').status_code,302)


if __name__ == '__main__': unittest.main(verbosity=2)
