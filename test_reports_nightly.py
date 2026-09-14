"""Nightly time boundaries, persistent retry state, parser and bulk upserts."""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

TMP = tempfile.mkdtemp(prefix='reports-nightly-')
os.environ['HUB_DATA_DIR'] = TMP
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(TMP, 'hub.db')
os.environ['SECRET_KEY'] = 'test'
os.environ['PANEL_PASSWORD'] = 'test'
os.environ['HUB_SCHEDULER'] = 'false'
import _reports_testdb
_reports_testdb.bind(TMP)
from hub import report_schedule as schedule
from modules.reports import store
from modules.reports.parsers import audiogo_csv


def clock(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


class NightlyTests(unittest.TestCase):
    def test_eastern_dst_and_standard_time(self):
        self.assertFalse(schedule.ready({}, clock('2026-09-15T06:59:00')))
        self.assertTrue(schedule.ready({}, clock('2026-09-15T07:00:00')))
        self.assertFalse(schedule.ready({}, clock('2026-01-15T07:59:00')))
        self.assertTrue(schedule.ready({}, clock('2026-01-15T08:00:00')))
        self.assertFalse(schedule.ready({}, clock('2026-03-08T06:59:00')))
        self.assertTrue(schedule.ready({}, clock('2026-03-08T07:00:00')))
        self.assertFalse(schedule.ready({}, clock('2026-11-01T07:59:00')))
        self.assertTrue(schedule.ready({}, clock('2026-11-01T08:00:00')))

    def test_retry_catchup_and_no_repeat(self):
        self.assertFalse(schedule.ready({'completed_day':'2026-09-15'},clock('2026-09-15T18:00:00')))
        self.assertTrue(schedule.ready({'completed_day':'2026-09-13'},clock('2026-09-15T01:00:00')))
        state={'attempted_at':'2026-09-15T07:00:00+00:00'}
        self.assertFalse(schedule.ready(state,clock('2026-09-15T07:29:00')))
        self.assertTrue(schedule.ready(state,clock('2026-09-15T07:30:00')))

    def test_durable_completion_and_pending(self):
        from hub import jsonstore
        ledger={}
        def save(path,state):
            ledger.clear();ledger.update(state);return True
        good={'ok':True,'result':{'platforms':{'stackadapt':{'ok':True}}}}
        pending={'ok':True,'result':{'platforms':{},'pending':['stackadapt']}}
        with patch.object(jsonstore,'read_json',side_effect=lambda *a:dict(ledger)),patch.object(jsonstore,'write_json',side_effect=save):
            callback=MagicMock(return_value=pending)
            self.assertTrue(schedule.run_due(callback,clock('2026-09-15T07:00:00')))
            self.assertNotIn('completed_day',ledger)
            callback.return_value=good
            self.assertTrue(schedule.run_due(callback,clock('2026-09-15T07:30:00')))
            self.assertEqual(ledger['completed_day'],'2026-09-15')
            self.assertFalse(schedule.run_due(callback,clock('2026-09-15T09:00:00')))
            self.assertEqual(callback.call_count,2)
        self.assertFalse(schedule.successful({'ok':True,'result':{'skipped':['google']}}))
        self.assertFalse(schedule.successful({'ok':True,'result':{'errors':{'google':'failed'},'platforms':{'stackadapt':{'ok':True}}}}))

    def test_stackadapt_convs(self):
        parsed=audiogo_csv.parse('Date,Advertiser ID,Campaign ID,Media Cost,Impressions,Clicks,Convs\n2026-09-14,1,2,$10.00,100,5,3\n',platform='stackadapt')
        self.assertEqual(parsed['error'],'')
        self.assertEqual(parsed['rows'][0]['conversions'],3)

    def test_google_current_video_metric(self):
        from datetime import date
        from modules.reports import google_ads_perf as google
        self.assertIn('metrics.video_trueview_views',google.gaql(date(2026,9,1),date(2026,9,14)))
        self.assertNotIn('metrics.video_views',google.gaql(date(2026,9,1),date(2026,9,14)))
        rows=google._facts('1',[{'campaign':{'id':'2'},'segments':{'date':'2026-09-14'},
                         'metrics':{'videoTrueviewViews':'123'}}],lambda x:0)
        self.assertEqual(rows[0]['video_views'],123)

    def test_postgres_bulk_duplicate_keys(self):
        rows=[store._fact_values({'platform':'stackadapt','account_id':'1','campaign_id':str(i),
               'date':'2026-09-14','spend':1,'impressions':10,'clicks':1}) for i in range(1000)]
        rows.append({**rows[0],'spend':2})
        session=MagicMock()
        with patch.object(store,'is_postgres',return_value=True),patch.object(store,'SessionLocal',return_value=session):
            self.assertEqual(store._write_values(rows),1001)
        self.assertEqual(session.execute.call_count,2)
        from sqlalchemy.dialects import postgresql
        params=session.execute.call_args_list[0].args[0].compile(dialect=postgresql.dialect()).params
        self.assertEqual(params['spend_m0'],2)
        self.assertEqual(len([k for k in params if k.startswith('campaign_id_m')]),500)
        session.commit.assert_called_once()
        session.close.assert_called_once()


if __name__=='__main__':
    unittest.main()
