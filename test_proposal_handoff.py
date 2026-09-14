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
    def test_invalid_alternative_blocks_both_exports(self):
        self.state['packages']=[{'name':'Recommended','lines':[{'name':'Programmatic Campaign with Retargeting','cat':'DATA TARGETED DISPLAY','amt':300}]}]
        q=self.quote()
        for extension in ['pdf','docx']:
            r=self.client.get(f"/api/quotes/{q['id']}/{extension}")
            self.assertEqual(r.status_code,422,r.get_data(as_text=True)[:500])
            self.assertIn('Rebuild',r.json['error'])
    def test_legacy_package_prices_remain_exportable(self):
        self.state['packages']=[{'name':'Accelerated','lines':[{'product':'Connected TV - Targeted','dollars':9000}]}]
        q=self.quote()
        with patch.object(builder,'build_proposal_pdf',return_value=(b'%PDF-QA','QA')):
            self.assertEqual(self.client.get(f"/api/quotes/{q['id']}/pdf").status_code,200)
        self.state['packages'][0]['lines'][0]['dollars']=100
        q=self.quote()
        self.assertEqual(self.client.get(f"/api/quotes/{q['id']}/pdf").status_code,422)
    def test_pdf_archive_has_direct_download(self):
        q=self.quote()
        with patch.object(builder,'build_proposal_pdf',return_value=(b'%PDF-QA','QA')):
            self.assertEqual(self.client.get(f"/api/quotes/{q['id']}/pdf").status_code,200)
        r=self.client.get(f"/api/quotes/{q['id']}/pdf/archived?download=1")
        self.assertEqual(r.status_code,200)
        self.assertTrue(r.headers['Content-Disposition'].startswith('attachment'))
        self.assertEqual(r.data,b'%PDF-QA')
    def test_conversion_rejects_stale_version(self):
        q=self.quote()
        r=self.client.post(f"/api/quotes/{q['id']}/conversion-check",json={'revision':q['revision']-1})
        self.assertEqual(r.status_code,409)
    def test_valid_conversion_preflight(self):
        q=self.quote()
        r=self.client.post(f"/api/quotes/{q['id']}/conversion-check",json={'revision':q['revision'],'updated_at':q['updated_at']})
        self.assertEqual(r.status_code,200,r.json)
    def test_converted_proposal_cannot_issue_another_io(self):
        q=self.quote()
        path=f"/api/quotes/{q['id']}"
        q=self.client.put(path,json={'status':'Converted'}).json['quote']
        response=self.client.post(path+'/conversion-check',json={'revision':q['revision'],'updated_at':q['updated_at']})
        self.assertEqual(response.status_code,409)
        self.assertIn('already has an IO',response.json['error'])
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

    def test_approval_baseline_is_preserved_after_edits(self):
        q=self.quote()
        path=f"/api/quotes/{q['id']}"
        approved=self.client.put(path,json={'status':'Approved'}).json['quote']
        self.assertEqual(approved['approval_changes'],[])
        state=approved['data']
        state['items'][0]['dollars']=4000
        state['_approvedScope']={}
        edited=self.client.put(path,json={'data':state}).json['quote']
        self.assertIn('Products and prices',edited['approval_changes'])
        self.assertEqual(edited['data']['_approvedScope']['items'][0]['dollars'],3000)
        self.assertIn('checklist',edited)
        duplicate=self.client.post(path+'/duplicate').json['quote']
        self.assertEqual(duplicate['status'],'Draft')
        self.assertIsNone(duplicate['approval_changes'])
        self.assertNotIn('_approvedScope',duplicate['data'])

    def test_list_and_package_investment_match_saved_scope(self):
        self.state['suiteTier']={'name':'Smart 1','monthly':199,'include':True}
        item=self.state['items'][0]
        self.state['packages']=[{'name':'Essential','lines':[{'name':item['product'],'cat':item['category'],'amt':2000}]}]
        self.state['items'].append({'product':'Video shoot','category':'VIDEO PRODUCTION','basis':'one_time','dollars':100})
        q=self.quote()
        self.assertEqual(q['investment']['campaign_total'],9697)
        self.assertEqual(q['package_investments'][0]['campaign_total'],6697)
        with builder.SessionLocal() as db:
            row=builder.quote_json(db.get(builder.Quote,q['id']))
        self.assertEqual(row['investment'],q['investment'])

def tearDownModule():
    builder.engine.dispose()
    _network.stop()
    _tmp.cleanup()

if __name__=='__main__':unittest.main()
