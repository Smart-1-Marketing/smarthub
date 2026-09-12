"""Offline Client 360/GHL linkage tests; no messages are sent."""
import copy
import unittest
from unittest.mock import patch
from flask import Flask
from hub import client_email as email
from hub.client_email_routes import bp

class ClientEmailTest(unittest.TestCase):
    def setUp(self):
        self.data={'links': []}
        self.contact={'id':'contactA','locationId':'smart1','email':'client@example.test','firstName':'Client','lastName':'Owner','companyName':'Example'}
        def update(path, mutate, **kwargs):
            self.data=mutate(copy.deepcopy(self.data));return self.data
        self.patches=[patch.object(email,'_path',return_value='memory.json'),patch.object(email.jsonstore,'read_json',side_effect=lambda *a,**k:copy.deepcopy(self.data)),patch.object(email.jsonstore,'update_json',side_effect=update),patch.object(email,'_location',return_value='smart1'),patch.object(email,'_get',side_effect=self.get)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
    def get(self,route,params=None):
        if route.startswith('/contacts/'):return {'contact':self.contact}
        if route=='/conversations/search':return {'conversations':[{'id':'threadA','contactId':'contactA','locationId':'smart1'}]}
        return {'messages':{'messages':[{'id':'messageA','messageType':'TYPE_EMAIL','contactId':'contactA','locationId':'smart1','subject':'Your ad proof','body':'Review the attached proof','direction':'outbound','status':'pending','dateAdded':'2026-09-12'}]}}
    def test_unlink_requires_current_revision_and_keeps_other_links(self):
        row=email.link('Example','contactA')
        with self.assertRaises(email.EmailError):email.unlink('Example','stale')
        email.unlink('Example',row['revision'])
        self.assertFalse(email.summary('Example')['linked'])
        email.link('Renamed Example','contactA')
    def test_html_email_is_plain_text_and_scripts_are_omitted(self):
        self.assertEqual(email._plain('<p>Hello &amp; welcome</p><script>bad()</script>'), 'Hello & welcome')
    def test_no_link_means_no_guessed_recipient(self):
        self.assertFalse(email.summary('Example')['linked']);email._get.assert_not_called()
    def test_explicit_link_and_history_keep_contact_and_message_ids(self):
        row=email.link('Example','contactA',actor='Tester')
        result=email.summary('Example')
        self.assertTrue(result['measured']);self.assertEqual(result['contact']['email'],'client@example.test')
        self.assertEqual(result['messages'][0]['id'],'messageA');self.assertEqual(result['messages'][0]['status'],'pending')
        self.assertIn('/smart1/contacts/detail/contactA',result['ghl_url'])
        self.assertEqual(self.data['links'][0]['linked_by'],'Tester')
        email._get.assert_any_call('/conversations/search',{'locationId':'smart1','contactId':'contactA','limit':20})
    def test_contact_in_client_subaccount_cannot_be_linked(self):
        self.contact['locationId']='client-subaccount'
        with self.assertRaises(email.EmailError):email.link('Example','contactA')
        self.assertEqual(self.data,{'links':[]})
    def test_no_email_and_path_injection_are_refused(self):
        self.contact['email']=''
        with self.assertRaises(email.EmailError):email.link('Example','contactA')
        with self.assertRaises(email.EmailError):email.link('Example','../other')
    def test_stale_link_update_and_two_clients_sharing_contact_are_refused(self):
        row=email.link('Example','contactA')
        with self.assertRaises(email.EmailError):email.link('Other Client','contactA')
        with self.assertRaises(email.EmailError):email.link('Example','contactA')
        self.assertEqual(len(self.data['links']),1)
        email.link('Example','contactA',row['revision'])
    def test_email_change_requires_relink(self):
        email.link('Example','contactA');self.contact['email']='changed@example.test'
        with self.assertRaises(email.EmailError):email.summary('Example')
    def test_wrong_contact_history_is_not_displayed(self):
        email.link('Example','contactA')
        original=self.get
        def mismatch(route,params=None):
            if route=='/conversations/search':return {'conversations':[{'id':'threadB','contactId':'other','locationId':'smart1'}]}
            return original(route,params)
        email._get.side_effect=mismatch
        result=email.summary('Example');self.assertFalse(result['measured']);self.assertEqual(result['messages'],[])
    def test_route_keeps_revision_available_when_contact_needs_relink(self):
        row=email.link('Example','contactA');self.contact['email']='changed@example.test'
        app=Flask(__name__);app.register_blueprint(bp)
        with patch('hub.auth.user_from_environ',return_value='Tester'):
            response=app.test_client().get('/api/client-email/state?client=Example')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['revision'],row['revision']);self.assertIn('changed',response.json['error'])
    def test_api_requires_staff_and_rejects_cross_origin_links(self):
        app=Flask(__name__);app.register_blueprint(bp)
        with patch('hub.auth.user_from_environ',return_value=None):
            self.assertEqual(app.test_client().get('/api/client-email/state?client=Example').status_code,401)
        with patch('hub.auth.user_from_environ',return_value='Tester'):
            self.assertEqual(app.test_client().post('/api/client-email/link',json={'client':'Example','contact_id':'contactA'},headers={'Origin':'https://other.test'}).status_code,403)

if __name__=='__main__':unittest.main()
