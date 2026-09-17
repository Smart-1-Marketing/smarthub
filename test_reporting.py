"""Reporting integration tests; all storage and provider responses are disposable."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime
from decimal import Decimal
TMP = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = os.environ.get('REPORTING_TEST_DATABASE_URL') or 'sqlite:///' + TMP.name.replace('\\', '/') + '/test.db'
os.environ['HUB_DATA_DIR'] = TMP.name
os.environ['SECRET_KEY'] = 'reporting-tests-only'
from flask import Flask
from sqlalchemy import select, func
from hub.extensions import db, init_db, create_all
from hub import reporting_models as m, reporting_service as service
from hub.reporting_normalize import normalize, classify, size, derived
from hub.reporting_tradedesk import TradeDesk, ProviderError
from hub.reporting_routes import bp
ROW = dict(AdvertiserId='a', CampaignId='c', AdGroupId='g', CreativeId='r', Date='2026-09-12', Currency='USD', Impressions='1000', Clicks='20', AdvertiserCost='12.50', VideoStarts='50', VideoCompletions='25', MediaType='CTV', CreativeSize='300x250')
class Fake:
    config = SimpleNamespace(ttd_partner_id='partner')
    rows = [ROW]
    def advertisers(self): return [dict(AdvertiserId='a', AdvertiserName='Client A')]
    def campaigns(self, account): return [dict(CampaignId='c', CampaignName='Campaign')]
    def ad_groups(self, campaign): return [dict(AdGroupId='g', AdGroupName='Group', MediaType='CTV')]
    def creatives(self, account): return [dict(CreativeId='r', CreativeName='Creative', Width=0, Height=0)]
    def daily_rows(self): return self.rows
class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=Flask('test', template_folder=os.path.abspath('hub/templates'))
        cls.app.secret_key='test'
        init_db(cls.app)
        cls.app.register_blueprint(bp)
        cls.context=cls.app.app_context(); cls.context.push()
        assert not create_all(cls.app)
    @classmethod
    def tearDownClass(cls):
        db.session.remove(); db.engine.dispose(); cls.context.pop()
    def setUp(self):
        with db.engine.begin() as cx:
            for t in reversed(db.metadata.sorted_tables):
                if t.name.startswith('reporting_'): cx.execute(t.delete())
    def scalar(self, q):
        with db.engine.connect() as cx: return cx.execute(q).scalar_one()
    def test_taxonomy(self):
        for text, product in zip(['Display','Retargeting','Native','Online Video','CTV','OTT','Streaming Audio','Podcast','DOOH'],m.TAXONOMY):
            self.assertEqual(classify(text),product)
        self.assertIsNone(classify('future format'))
        self.assertEqual(classify('Display',True),('display','retargeting'))
    def test_validation_and_sizes(self):
        self.assertEqual(size(0,0),(None,None)); self.assertEqual(size(300,0),(None,None))
        self.assertEqual(size('','','300x250'),(300,250))
        for k,v in [('Impressions','NaN'),('Clicks','-1'),('Clicks','1.2'),('Currency',''),('Date','bad'),('CampaignId','')]:
            with self.assertRaises((ValueError,TypeError)): normalize({**ROW,k:v})
    def test_derived(self):
        self.assertEqual(derived(0,0,0,0,0),dict(ctr=None,cpm=None,completion_rate=None))
        self.assertEqual(derived(1000,20,Decimal('12.5'),50,25),dict(ctr=2.0,cpm=12.5,completion_rate=50.0))
    def test_rerun_replaces(self):
        fake=Fake(); self.assertEqual(service.sync(adapter=fake)[1],200)
        fake.rows=[{**ROW,'Clicks':'30'}]; self.assertEqual(service.sync(adapter=fake)[1],200)
        self.assertEqual(self.scalar(select(func.count()).select_from(m.metrics)),1)
        self.assertEqual(self.scalar(select(m.metrics.c.clicks)),30)
        self.assertEqual(self.scalar(select(func.count()).select_from(m.raw_payloads)),2)
        self.assertEqual(self.scalar(select(func.count()).select_from(m.products)),9)
        self.assertEqual(self.scalar(select(m.creatives.c.width)),300)
    def test_invalid_export_preserves_metrics(self):
        service.sync(adapter=Fake()); fake=Fake()
        for rows in [[ROW,ROW],[{**ROW,'AdvertiserId':'foreign'}],[{**ROW,'Clicks':'30'},{**ROW,'Impressions':'bad'}]]:
            fake.rows=rows; self.assertEqual(service.sync(adapter=fake)[1],502)
            self.assertEqual(self.scalar(select(m.metrics.c.clicks)),20)
    def test_mapping_and_unknown(self):
        fake=Fake(); fake.rows=[{**ROW,'MediaType':'new format'}]; service.sync(adapter=fake)
        report=service.health(); self.assertEqual(report['unmapped_metric_rows'],1)
        account=report['accounts'][0]['id']
        with self.assertRaises(ValueError): service.match(account,'invented','admin',[])
        service.match(account,'Client A','admin',[{'name':'Client A'}]); service.sync(adapter=fake)
        self.assertEqual(service.health()['unmapped_accounts'],[])
        service.match(account,None,'admin',[]); self.assertEqual(len(service.health()['unmapped_accounts']),1)
    def test_currency_and_missing_grain(self):
        fake=Fake(); fake.rows=[{**ROW,'AdGroupId':'','CreativeId':''},{**ROW,'Currency':'EUR','AdGroupId':'','CreativeId':''}]
        service.sync(adapter=fake); service.sync(adapter=fake)
        self.assertEqual(self.scalar(select(func.count()).select_from(m.metrics)),2)
        self.assertEqual(len(service.health()['totals']),2)
    def test_concurrent_run(self):
        with db.engine.begin() as cx:
            pid=service.seed(cx); cx.execute(m.runs.insert().values(provider_id=pid,status='running',started_at=datetime.utcnow(),rows_written=0))
        self.assertEqual(service.sync(adapter=Fake())[1],409)
    def test_missing_config(self):
        adapter=TradeDesk(SimpleNamespace(ttd_api_token='',ttd_partner_id='',ttd_report_url=''))
        self.assertEqual(len(adapter.health()['missing']),3)
        self.assertEqual(service.sync(adapter=adapter)[1],502)
        self.assertEqual(self.scalar(select(m.runs.c.status)),'failed')
    def test_transport_and_paging(self):
        import json
        adapter=TradeDesk(SimpleNamespace(ttd_api_token='secret',ttd_partner_id='p',ttd_report_url=''))
        for url in ['http://api.thetradedesk.com/x','https://evil.test/x','https://user@api.thetradedesk.com/x']:
            with self.assertRaises(ProviderError): adapter.request('GET',url)
        with patch.object(adapter,'request',side_effect=[json.dumps({'Result':[{'AdvertiserId':str(i)} for i in range(100)]}).encode(),b'{"Result": []}']) as call:
            self.assertEqual(len(list(adapter.advertisers())),100)
            self.assertEqual(call.call_args.kwargs['json']['PageStartIndex'],100)
    def test_existing_token_aliases(self):
        from hub.config import Settings, ALIASES
        for name in ALIASES['ttd_api_token']:
            with patch.dict(os.environ, {name: 'test-token'}, clear=True):
                self.assertEqual(Settings().ttd_api_token, 'test-token')
        with patch.dict(os.environ, {'TTD_API_TOKEN': 'preferred', 'TRADE_DESK_API': 'legacy'}, clear=True):
            self.assertEqual(Settings().ttd_api_token, 'preferred')
    def test_routes_survive_reports_mount(self):
        from werkzeug.middleware.dispatcher import DispatcherMiddleware
        from werkzeug.test import Client
        from werkzeug.wrappers import Response
        mounted = Flask('existing_reports')
        client = Client(DispatcherMiddleware(self.app, {'/reports': mounted}), Response)
        with patch('hub.users_routes._require_admin_api', return_value=(None, None)):
            result = client.get('/diagnostics/reporting?format=json')
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json['schema_ready'])
    def test_missing_required_metrics(self):
        for field in ['Impressions', 'AdvertiserCost']:
            row = dict(ROW); row.pop(field)
            with self.assertRaises(ValueError): normalize(row)
    def test_database_failure_rolls_back(self):
        service.sync(adapter=Fake())
        real = service.ingest
        def fail(cx, row, account, run):
            real(cx, row, account, run)
            raise RuntimeError('simulated database failure')
        fake=Fake(); fake.rows=[{**ROW,'Clicks':'99'}]
        with patch.object(service,'ingest',side_effect=fail):
            result,status=service.sync(adapter=fake)
        self.assertEqual(status,502)
        self.assertEqual(result['rows_written'],0)
        self.assertEqual(self.scalar(select(m.metrics.c.clicks)),20)
    def test_empty_export_preserves_metrics(self):
        service.sync(adapter=Fake()); fake=Fake(); fake.rows=[]
        self.assertEqual(service.sync(adapter=fake)[1],502)
        self.assertEqual(self.scalar(select(m.metrics.c.clicks)),20)
    def test_repeated_schema_initialization(self):
        service.sync(adapter=Fake())
        self.assertEqual(create_all(self.app),'')
        self.assertEqual(self.scalar(select(func.count()).select_from(m.metrics)),1)
    def test_discovery_metadata_is_preserved(self):
        fake=Fake(); row=dict(ROW); row.pop('MediaType'); fake.rows=[row]
        self.assertEqual(service.sync(adapter=fake)[1],200)
        self.assertEqual(self.scalar(select(m.campaigns.c.campaign_name)),'Campaign')
        self.assertEqual(self.scalar(select(m.creatives.c.creative_name)),'Creative')
        self.assertEqual(service.health()['unmapped_metric_rows'],0)
    def test_audio_uses_audio_metrics(self):
        row=normalize({**ROW,'MediaType':'Podcast','VideoStarts':'0','VideoCompletions':'0','AudioStarts':'100','AudioCompletions':'80'})
        self.assertEqual(row['starts'],100)
        self.assertEqual(row['completions'],80)
    def test_routes(self):
        client=self.app.test_client()
        with patch('hub.users_routes._require_admin_api',return_value=(None,({'error':'no'},401))):
            self.assertEqual(client.get('/diagnostics/reporting').status_code,401)
            self.assertEqual(client.post('/diagnostics/reporting/tradedesk/sync',json={}).status_code,401)
        user=SimpleNamespace(email='admin',is_admin=True)
        with patch('hub.users_routes._require_admin_api',return_value=(user,None)),patch('hub.users_routes.current_account',return_value=user),patch('hub.clients_registry.all_clients',return_value=[]):
            self.assertEqual(client.get('/diagnostics/reporting/tradedesk/sync').status_code,405)
            self.assertEqual(client.post('/diagnostics/reporting/tradedesk/sync',data='{}').status_code,415)
            self.assertEqual(client.post('/diagnostics/reporting/tradedesk/sync',json={},headers={'Origin':'https://evil.test'}).status_code,403)
            self.assertEqual(client.get('/diagnostics/reporting?format=json').status_code,200)
            self.assertEqual(client.get('/diagnostics/reporting').status_code,200)
            self.assertEqual(client.post('/diagnostics/reporting/accounts/1/match',json=[]).status_code,400)
if __name__=='__main__': unittest.main()
