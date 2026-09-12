"""Factory configuration, real HTTP routing, lead delivery and queue integration."""
import os
import tempfile
import unittest
from unittest.mock import patch

TMP = tempfile.TemporaryDirectory(prefix="industry-factory-tests-", ignore_cleanup_errors=True)
os.environ.update(HUB_DATA_DIR=TMP.name, DATABASE_URL="sqlite:///" + TMP.name.replace("\\", "/") + "/test.db",
                  HUB_LEADS_FILE=TMP.name + "/leads.jsonl", SECRET_KEY="industry-test", PANEL_PASSWORD="test", LEADS_RATE_LIMIT="0")
for key in ("OPENAI_API_KEY", "GHL_PRIVATE_TOKEN", "SMART1SUITE_PRIVATE_TOKEN"):
    os.environ.pop(key, None)

from hub import auth, industry_config
from hub.industry_factory import QA
from hub.extensions import db
from hub.creative_jobs import CreativeJob, enqueue_for_lead
from wsgi import application
from werkzeug.test import Client


class FactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.anon = Client(application)
        cls.staff = Client(application)
        cls.staff.set_cookie(auth.COOKIE_NAME, auth.issue_cookie_value("Test"), domain="localhost")

    def draft(self, **overrides):
        response = self.staff.post("/api/industry-factory/pages", json=dict(industry_id="roofing", service="residential_roofing", market="Columbus, OH", radius=25, **overrides))
        self.assertEqual(response.status_code, 201, response.text[:500])
        return response.json

    def action(self, page, action, **body):
        return self.staff.post(f"/api/industry-factory/pages/{page['id']}/{action}", json=body)

    def publish(self, page):
        for action in ("generate-page", "generate-report"):
            self.assertEqual(self.action(page, action).status_code, 200)
        result = self.action(page, "publish", qa=list(QA))
        self.assertEqual(result.status_code, 200, result.text[:500])
        return result.json

    def test_pack_and_weather_adapter(self):
        pack = industry_config.load_pack("roofing")
        from modules.smartforecast.packs import get_pack
        source = get_pack("home_services")["rules"][0]
        rule = industry_config.triggers(pack)[0]
        self.assertEqual(rule["active_conditions"], source["active_conditions"])
        self.assertEqual(rule["post_hours"], source["post_hours"])
        self.assertIn("roof", pack["messaging"]["headline"])
        pack["messaging"]["headline"] = "changed"
        self.assertNotEqual(industry_config.load_pack("roofing")["messaging"]["headline"], "changed")
        for bad in ("../roofing", "unknown"):
            with self.assertRaises(ValueError): industry_config.load_pack(bad)

    def test_validation(self):
        valid = dict(industry_id="roofing", service="roof_repair", market="43215")
        for bad in ({"radius":0}, {"radius":201}, {"radius":True}, {"market":""}, {"service":"hvac"}, {"conversion_goal":"invented"}, {"trigger_ids":[]}, {"trigger_ids":["hail"]}, {"trigger_ids":[{}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError): industry_config.selection(valid | bad)
        pack=industry_config.load_pack("roofing"); pack["trigger_refs"]=[{"catalog":"hub","id":"missing"}]
        with self.assertRaises(ValueError): industry_config.triggers(pack)

    def test_auth_and_ui(self):
        self.assertIn(self.anon.get("/sales/industry-factory").status_code, (302,401))
        self.assertEqual(self.anon.get("/api/industry-factory/pages").status_code,401)
        response=self.staff.get("/sales/industry-factory")
        self.assertEqual(response.status_code,200,response.text[:500])
        self.assertIn('id="setup"',response.text)
        self.assertIn("Industry Factory",response.text)

    def test_assets(self):
        for name in ('industry-factory.css','industry-factory.js','industry-widget.js'):
            response=self.anon.get('/assets/'+name)
            self.assertEqual(response.status_code,200)
            self.assertNotIn('<!doctype',response.text.lower())
            response.close()

    def test_publish_preview_clone(self):
        p=self.draft()
        self.assertEqual(self.anon.get('/industry/p/'+p['id']).status_code,404)
        self.assertEqual(self.action(p,'publish',qa=list(QA)).status_code,400)
        self.action(p,'generate-page')
        preview=self.staff.get('/sales/industry-factory/preview/'+p['id'])
        self.assertEqual(preview.status_code,200)
        self.assertIn('submission is disabled',preview.text)
        self.assertNotIn('s1hub-sidebar',preview.text)
        self.assertIn(self.anon.get('/sales/industry-factory/preview/'+p['id']).status_code,(302,401))
        p=self.publish(p)
        public=self.anon.get('/industry/p/'+p['id'])
        self.assertEqual(public.status_code,200)
        self.assertNotIn('s1hub-sidebar',public.text)
        widget=self.anon.get('/industry/widget/'+p['id'])
        self.assertEqual(widget.status_code,200)
        self.assertIn('frame-ancestors',widget.headers.get('Content-Security-Policy',''))
        self.assertEqual(self.anon.get('/industry/p/'+p['id']+'/report').status_code,200)
        self.assertEqual(self.action(p,'generate-report').status_code,409)
        clone=self.draft(parent_id=p['id'])
        self.assertEqual(clone['version'],2)
        self.assertEqual(clone['status'],'draft')
        self.assertEqual(clone['states']['page'],'not_started')

    def test_lead_metadata_is_canonical(self):
        p=self.publish(self.draft())
        body=dict(source='landing',page='industry-'+p['id'],fields={'company':'Roof Co','name':'Alex','email':'alex@example.test'},meta={'page_id':p['id'],'industry_id':'fake','market':'Wrong','utm_source':'newsletter','referrer':'https://example.test/'})
        with patch('hub.leads.capture_and_deliver',return_value={'ok':True,'lead_id':'test'}) as capture:
            r=self.anon.post('/api/leads/capture',json=body)
            self.assertEqual(r.status_code,200,r.text[:500])
            args=capture.call_args.args
            self.assertEqual(args[0],'landing')
            meta=args[5]
            self.assertEqual(meta['industry_id'],'roofing')
            self.assertEqual(meta['market'],'Columbus, OH')
            self.assertEqual(meta['utm_source'],'newsletter')
            for key in ('industry_family','page_id','page_version','service','trigger_profile','trigger_ids','creative_profile','conversion_goal','referrer'):
                self.assertIn(key,meta)
            from hub.lead_tags import tags_for
            self.assertIn('industry-roofing',tags_for({'source':'landing','meta':meta}))
            from hub.ghl_contacts import payload_for
            with patch('hub.ghl_contacts.location_id',return_value='test-location'), patch.dict(os.environ,{'GHL_INDUSTRY_MARKET_FIELD_ID':'market-field'}):
                payload=payload_for({'source':'landing','meta':meta,'email':'alex@example.test'})
            self.assertIn({'id':'market-field','field_value':'Columbus, OH'},payload['customFields'])
        body['meta']['page_id']=self.draft()['id']
        self.assertEqual(self.anon.post('/api/leads/capture',json=body).status_code,400)

    def test_creative_queue(self):
        p=self.draft(); self.action(p,'generate-page')
        result=self.action(p,'generate-creative')
        self.assertEqual(result.status_code,200,result.text[:500])
        self.assertEqual(result.json['states']['creative'],'queued')
        # Existing scheduler runner processes industry jobs without external AI.
        from wsgi import hub_app
        with hub_app.app_context():
            job=CreativeJob.query.filter_by(lead_id='industry-page-'+p['id']).first()
            from hub.industry_creative import run
            output=run(job)
            self.assertEqual(len(output['concepts']),3)
            self.assertIn('300x250',output['concepts'][0]['sizes'])
            self.assertIsNone(enqueue_for_lead({'source':'landing','id':'plain','meta':{}}))
            from hub.creative_jobs import _RUNNERS
            self.assertIn('radio',_RUNNERS)
            self.assertIn('industry_concepts',_RUNNERS)


if __name__ == '__main__': unittest.main()
