"""Offline proof-email tests: every GHL send is mocked."""
import copy
import threading
import unittest
from unittest.mock import patch, Mock
import requests
from hub import ad_proof_email as email

class ProofEmailTests(unittest.TestCase):
    def setUp(self):
        self.data={'drafts':[]};self.lock=threading.RLock()
        self.project={'projectId':'example','client':'Example Studio','domain':'example.test','projectName':'Example offer'}
        self.linked={'linked':True,'revision':'contact-version','contact':{'id':'contactA','email':'client@example.test'}}
        def update(path,mutate,**kw):
            with self.lock:
                result=mutate(copy.deepcopy(self.data))
                if result is not None:self.data=result
                return copy.deepcopy(self.data)
        self.patches=[patch.object(email,'context',side_effect=lambda _: (self.project,copy.deepcopy(self.linked))),
          patch.object(email.jsonstore,'read_json',side_effect=lambda *a,**k:copy.deepcopy(self.data)),patch.object(email.jsonstore,'update_json',side_effect=update),
          patch.object(email.ad_builder_link,'_api',return_value=(True,{'token':'frozen-proof','proofUrl':'/client-proof/frozen-proof'})),
          patch.object(email.ghl_contacts,'_headers',return_value={'Authorization':'Bearer synthetic-test-only'}),
          patch.object(email.requests,'post',return_value=Mock(ok=True,json=lambda:{'messageId':'messageA','conversationId':'conversationA'}))]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def draft(self):
        return email.prepare('example','reviewA','Please review','Hello <client>','team@example.test','https://hub.example.test','Tester')
    def test_preview_is_not_send_and_repeat_send_posts_once(self):
        draft=self.draft();email.requests.post.assert_not_called();self.assertEqual(self.draft()['id'],draft['id'])
        sent=email.send(draft['id']);self.assertEqual(sent['status'],'queued');self.assertEqual(sent['message_id'],'messageA')
        self.assertEqual(email.send(draft['id'])['status'],'queued');self.assertEqual(email.requests.post.call_count,1)
        payload=email.requests.post.call_args.kwargs['json'];self.assertEqual(payload['contactId'],'contactA');self.assertIn('&lt;client&gt;',payload['html'])
        self.assertEqual(self.data['drafts'][0]['client'],'Example Studio')
    def test_timeout_is_unknown_and_cannot_be_retried_as_a_new_draft(self):
        draft=self.draft();email.requests.post.side_effect=requests.Timeout()
        self.assertEqual(email.send(draft['id'])['status'],'unknown');email.send(draft['id'])
        with self.assertRaises(email.client_email.EmailError):self.draft()
        self.assertEqual(email.requests.post.call_count,1)
    def test_changed_recipient_prevents_send(self):
        draft=self.draft();self.linked['contact']['email']='other@example.test'
        with self.assertRaises(email.client_email.EmailError):email.send(draft['id'])
        email.requests.post.assert_not_called()
    def test_changed_proof_prevents_send(self):
        draft=self.draft();email.ad_builder_link._api.return_value=(False,{'error':'stale'})
        with self.assertRaises(email.client_email.EmailError):email.send(draft['id'])
        email.requests.post.assert_not_called()
    def test_concurrent_send_attempt_cannot_pass_claim(self):
        draft=self.draft()
        def sending(*a,**k):
            self.assertEqual(email.send(draft['id'])['status'],'sending')
            return Mock(ok=True,json=lambda:{'messageId':'messageA'})
        email.requests.post.side_effect=sending
        self.assertEqual(email.send(draft['id'])['status'],'queued');self.assertEqual(email.requests.post.call_count,1)
    def test_missing_receipt_is_not_claimed_as_sent(self):
        draft=self.draft();email.requests.post.return_value=Mock(ok=True,json=lambda:{})
        self.assertEqual(email.send(draft['id'])['status'],'unknown')

    def test_receipt_history_retry_never_sends_again(self):
        draft=self.draft()
        email.send(draft['id'])
        email.ad_builder_link._api.reset_mock()
        email.send(draft['id'])
        self.assertEqual(email.requests.post.call_count,1)
        email.ad_builder_link._api.assert_called_once_with('POST','/api/project/example/workflow',
            {'action':'sent','token':'frozen-proof','messageId':'messageA'})

    def test_send_routes_require_staff_and_same_origin(self):
        from flask import Flask
        from hub.client_email_routes import bp
        app=Flask(__name__);app.register_blueprint(bp)
        with patch('hub.auth.user_from_environ',return_value=None):
            self.assertEqual(app.test_client().post('/api/display-ad-send/send',json={'id':'x'}).status_code,401)
        with patch('hub.auth.user_from_environ',return_value='Tester'):
            self.assertEqual(app.test_client().post('/api/display-ad-send/send',json={'id':'x'},headers={'Origin':'https://other.test'}).status_code,403)
            self.assertEqual(app.test_client().post('/api/display-ad-send/send',data='id=x').status_code,415)
        email.requests.post.assert_not_called()

if __name__=='__main__':unittest.main()
