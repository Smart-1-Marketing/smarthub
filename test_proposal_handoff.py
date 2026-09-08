import os,tempfile,unittest
from unittest.mock import patch
_tmp=tempfile.TemporaryDirectory(prefix='proposal-handoff-')
os.environ['DATABASE_URL']='sqlite:///'+_tmp.name+'/test.db'
os.environ['HUB_DATA_DIR']=_tmp.name
os.environ['SECRET_KEY']='local-regression-only'
os.environ['PANEL_PASSWORD']='test'
# The test must never contact providers or send anything.
_network=patch('requests.sessions.Session.request',side_effect=AssertionError('Network disabled for handoff tests'))
_network.start()
from modules.sales_builder import app as builder

class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.client=builder.app.test_client()
        self.state={'client':'QA ONLY','months':3,'objectives':['Lead Generation'],'items':[{'product':'Pay Per Click','category':'SEARCH ENGINE MARKETING / PAY PER CLICK','rate':'Management Fee','dollars':3000}], 'audiences':['Homeowners'],'industry':'Home Services'}
    def quote(self,state=None):
        response=self.client.post('/api/quotes',json={'data':state or self.state})
        self.assertEqual(response.status_code,200,response.get_data(as_text=True)[:500])
        return response.json['quote']
    def test_failed_draft_blocks_share_delivery_and_conversion(self):
        self.state['draftFailures']=[{'id':'areas','title':'Audience'}]
        q=self.quote()
        for path in ['share','deliver','conversion-check']:
            r=self.client.post(f"/api/quotes/{q['id']}/{path}",json={'revision':q['revision'],'updated_at':q['updated_at']})
            self.assertEqual(r.status_code,422,(path,r.get_data(as_text=True)[:500]))
    def test_conversion_rejects_stale_version(self):
        q=self.quote()
        r=self.client.post(f"/api/quotes/{q['id']}/conversion-check",json={'revision':q['revision']-1})
        self.assertEqual(r.status_code,409)
    def test_valid_conversion_preflight(self):
        q=self.quote()
        r=self.client.post(f"/api/quotes/{q['id']}/conversion-check",json={'revision':q['revision'],'updated_at':q['updated_at']})
        self.assertEqual(r.status_code,200,r.json)
    def test_unverified_zip_blocks_conversion(self):
        self.state['targetAreas']=[{'type':'City/ZIP + Radius','origin':'Columbus, OH','radius':15,'zips':'43215','zipSource':'AI-assisted — unverified','zipVerified':False}]
        q=self.quote()
        r=self.client.post(f"/api/quotes/{q['id']}/conversion-check",json={'revision':q['revision'],'updated_at':q['updated_at']})
        self.assertEqual(r.status_code,422,r.json)
    def test_seeded_copy_does_not_invent_audiences_or_gaps(self):
        sections=builder._seeded_sections(self.state)
        text='\n'.join(s.get('body','') for s in sections)
        self.assertNotIn('RV & Camping',text)
        self.assertNotIn('No central CRM',text)
        self.assertIn('Homeowners',text)
    def test_plan_setup_has_source_for_io_deduplication(self):
        self.state['items'].append({'product':'Video shoot','category':'VIDEO PRODUCTION','basis':'one_time','dollars':100})
        q=self.quote()
        setup=[l for l in q['investment']['lines'] if l.get('source')=='plan']
        self.assertEqual(len(setup),1)
        self.assertEqual(setup[0]['amount'],100)

def tearDownModule():
    builder.engine.dispose()
    _network.stop()
    _tmp.cleanup()

if __name__=='__main__':unittest.main()
