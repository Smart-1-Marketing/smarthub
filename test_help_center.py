"""Help center contracts. No external services or paid AI calls."""
import os
import tempfile
import unittest
from unittest.mock import patch
from flask import Flask
from hub.help_center import bp
from hub import ai


class HelpCenterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = Flask(__name__)
        self.app.register_blueprint(bp)
        self.client = self.app.test_client()
        self.auth = patch('hub.current_user', return_value='Todd Smith')
        self.identity = patch('hub.identity.user_from_environ', return_value=None)
        self.root = patch('hub.jsonstore.data_root', return_value=self.tmp.name)
        self.audit = patch('hub.audit.log')
        self.qa = patch('hub.help_center.qa_notifications', return_value=([], ''))
        self.qa.start()
        self.auth.start(); self.identity.start(); self.root.start(); self.audit.start()

    def tearDown(self):
        self.qa.stop()
        self.auth.stop(); self.identity.stop(); self.root.stop(); self.audit.stop(); self.tmp.cleanup()

    def test_auth_required(self):
        with patch('hub.current_user', return_value=''):
            for path in ('/help', '/api/hub-inbox', '/api/help-center/catalog'):
                self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.post('/api/help-center/ask', json={'question':'Help me'}).status_code, 401)

    def test_personal_inbox(self):
        rows = [dict(module='fan_radio', type='spot_recorded', actor=actor, time='2026-09-08T12:00:00Z') for actor in ('Todd Smith', 'Someone Else', '')]
        with patch('hub.audit.tail', return_value=rows):
            data = self.client.get('/api/hub-inbox').get_json()
        self.assertEqual(data['user']['initials'], 'TS')
        self.assertEqual(len(data['items']), 1)
        self.assertEqual(data['items'][0]['status'], 'completed')

    def test_display_poll_is_from_owned_event(self):
        with patch('hub.audit.tail', return_value=[dict(module='display_ads', type='ads_job_tracked', actor='Todd Smith', job='abc-123')]):
            row = self.client.get('/api/hub-inbox').get_json()['items'][0]
        self.assertEqual(row['poll'], '/tools/display-ads/api/render/abc-123')

    def test_qa_notifications_use_personal_queues_and_activity_revision(self):
        self.qa.stop()
        task = dict(id=7, status_label='Open', overdue=True, target_label='Video Builder',
                    due_on='2026-09-01', last_activity_at='2026-09-08T20:00:00Z', unread=True)
        queues = dict(measured=True, to_do=[task], waiting_on_you=[], raised_by_you=[task], done=[])
        with patch('hub.qa_tasks_routes._who', return_value=('todd@example.test','Todd')), patch('hub.qa_tasks.for_person', return_value=queues) as personal, patch('hub.audit.tail', return_value=[]):
            data = self.client.get('/api/hub-inbox').get_json()
        personal.assert_called_once_with('todd@example.test', limit=50)
        self.assertEqual(len(data['items']), 1)
        item = data['items'][0]
        self.assertEqual(item['url'], '/qa-tasks/7')
        self.assertIn('overdue', item['status'])
        self.assertIn(task['last_activity_at'], item['revision'])
        self.assertTrue(item['unread'])

    def test_qa_shared_login_does_not_guess_a_person(self):
        self.qa.stop()
        with patch('hub.qa_tasks_routes._who', return_value=('', 'Todd')), patch('hub.qa_tasks.for_person') as personal, patch('hub.audit.tail', return_value=[]):
            data = self.client.get('/api/hub-inbox').get_json()
        personal.assert_not_called()
        self.assertEqual(data['items'], [])
        self.assertIn('own Hub account', data['qa_error'])

    def test_qa_read_failure_keeps_media_notifications(self):
        self.qa.stop()
        event = dict(module='fan_radio', type='spot_recorded', actor='Todd Smith', time='2026-09-08T20:00:00Z')
        with patch('hub.qa_tasks_routes._who', return_value=('todd@example.test', 'Todd')), patch('hub.qa_tasks.for_person', return_value={'measured':False}), patch('hub.audit.tail', return_value=[event]):
            data = self.client.get('/api/hub-inbox').get_json()
        self.assertEqual(data['items'][0]['title'], 'Radio')
        self.assertIn('could not be refreshed', data['qa_error'])

    def test_radio_failure_is_not_completion(self):
        with patch('hub.audit.tail', return_value=[dict(module='radio_promo', type='render_failed', actor='Todd Smith')]):
            row = self.client.get('/api/hub-inbox').get_json()['items'][0]
        self.assertEqual(row['status'], 'failed')

    def test_catalog_uses_real_content(self):
        with patch('hub.jsonstore.read_json', return_value=[]):
            data = self.client.get('/api/help-center/catalog').get_json()
        self.assertGreater(len(data['articles']), 50)
        self.assertGreater(len(data['walkthroughs']), 5)
        self.assertGreater(len(data['videos']), 10)
        self.assertTrue(all(v['url'].startswith('https://www.youtube.com/watch?v=') for v in data['videos']))

    def test_custom_tutorials_merge_without_duplicates(self):
        from hub.help_center import learning_videos
        video = learning_videos()[0]
        custom = dict(video, title='Updated lesson title')
        with patch('hub.jsonstore.read_json', return_value=[custom, {'title':'Unsafe', 'url':'javascript:alert(1)'}]):
            data = self.client.get('/api/help-center/catalog').get_json()
        self.assertEqual(len(data['videos']), len(learning_videos()))
        self.assertEqual(data['videos'][0]['title'], 'Updated lesson title')

    def test_invalid_question(self):
        for data in (None, [], {'question': ['bad']}, {'question':'x'}, {'question':'x'*1501}):
            self.assertEqual(self.client.post('/api/help-center/ask', json=data).status_code, 400)

    def test_answer_is_logged_with_sources(self):
        writes = []
        with patch('hub.jsonstore.write_json', side_effect=lambda p,d: writes.append(dict(d)) or True), patch('hub.ai.chat', return_value='Use the radio library.') as chat:
            r = self.client.post('/api/help-center/ask', json={'question':'How do I use radio?'}).get_json()
        self.assertEqual(r['status'], 'answered')
        self.assertTrue(r['sources'])
        self.assertEqual(writes[0]['status'], 'pending')
        self.assertEqual(writes[-1]['answer'], 'Use the radio library.')
        self.assertIn('Documentation:', chat.call_args.args[0][0]['content'])

    def test_generated_urls_are_removed_before_logging_and_response(self):
        writes = []
        generated = 'Open [the radio tool](https://example.com/tools/radio-promo/). More: https://invented.example/help'
        with patch('hub.jsonstore.write_json', side_effect=lambda p,d: writes.append(dict(d)) or True), patch('hub.ai.chat', return_value=generated):
            result = self.client.post('/api/help-center/ask', json={'question':'How do I use radio promo?'}).get_json()
        self.assertNotIn('https://', result['answer'])
        self.assertIn('the radio tool', result['answer'])
        self.assertEqual(writes[-1]['answer'], result['answer'])
        self.assertEqual(result['sources'][0]['link'], '/tools/radio-promo/')

    def test_ai_unavailable_is_honest(self):
        with patch('hub.jsonstore.write_json', return_value=True), patch('hub.ai.chat', side_effect=ai.AIUnavailable('secret provider error')):
            r = self.client.post('/api/help-center/ask', json={'question':'How do I use radio?'}).get_json()
        self.assertEqual(r['status'], 'unavailable')
        self.assertNotIn('secret provider error', str(r))

    def test_logging_failure_does_not_spend(self):
        with patch('hub.jsonstore.write_json', return_value=False), patch('hub.ai.chat') as chat:
            r = self.client.post('/api/help-center/ask', json={'question':'How do I use radio?'})
        self.assertEqual(r.status_code, 503)
        chat.assert_not_called()

    def test_broad_radio_question_finds_complete_walkthrough(self):
        from hub.help_center import answer_sources
        rows = answer_sources('How do I use the radio promo builder to record a spot?')
        self.assertEqual(rows[0]['key'], 'walkthrough.radio_promo.first_spot')
        self.assertIn('Pick a voice', rows[0]['body'])
        self.assertIn('Save it to the library', rows[0]['body'])

    def test_tool_name_prefers_the_requested_tool(self):
        from hub.help_center import answer_sources
        rows = answer_sources('How do I make a fan radio spot?')
        self.assertEqual(rows[0]['key'], 'walkthrough.fan_radio.spot')
        self.assertEqual(answer_sources('How do I?'), [])

    def test_inbox_does_not_depend_on_cached_help_script(self):
        from pathlib import Path
        root = Path(__file__).parent
        for file in ['hub/templates/base.html', 'hub/__init__.py', 'wsgi.py']:
            self.assertIn('data-hub-inbox src="/assets/hub-inbox.js?v=qa-inbox-1"', (root / file).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
