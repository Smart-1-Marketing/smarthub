"""Reviewed GHL email drafts with durable, at-most-once send attempts."""
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from html import escape
import requests
from hub import client_email, jsonstore, ad_builder_link, ghl_contacts

def _path():
    return os.path.join(jsonstore.data_dir('suite'), 'display_ad_email_drafts.json')

def _now():
    return datetime.now(timezone.utc).isoformat()

def context(project_id):
    if not re.fullmatch(r'[\w.-]{1,240}', str(project_id or '')):
        raise client_email.EmailError('Open Send from the campaign review.')
    project = ad_builder_link._project(project_id)
    if not project:
        raise client_email.EmailError('The campaign could not be loaded.')
    linked = client_email.summary(project['client'], project.get('domain', ''))
    return project, linked

def prepare(project_id, review_id, subject, message, sender, origin, actor):
    project, linked = context(project_id)
    if not linked.get('linked'):
        raise client_email.EmailError('Link and verify the client’s GHL contact before sending.')
    if not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+', str(sender or '')):
        raise client_email.EmailError('Enter the sender email configured in Smart 1’s GHL account.')
    subject, message = str(subject or '').strip(), str(message or '').strip()
    if not subject or len(subject)>200 or '\n' in subject or '\r' in subject or not message or len(message)>10000:
        raise client_email.EmailError('Add a subject (up to 200 characters) and message (up to 10,000 characters).')
    ok, proof = ad_builder_link._api('POST', f'/api/project/{project_id}/workflow', {'reviewId': review_id})
    if not ok:
        raise client_email.EmailError(proof.get('error') or 'Finish reviewing and approving the sizes first.')
    proof_url = origin.rstrip('/') + '/tools/display-ads' + proof['proofUrl']
    payload = {'type':'Email', 'contactId':linked['contact']['id'], 'emailTo':linked['contact']['email'], 'emailFrom':sender,
               'subject':subject, 'html':'<p>'+escape(message).replace('\n','<br>')+'</p><p><a href="'+escape(proof_url, quote=True)+'">Review and approve your ads</a></p>', 'status':'pending'}
    fingerprint=hashlib.sha256(json.dumps([project_id,proof['token'],linked['revision'],payload],sort_keys=True).encode()).hexdigest()
    draft={'id':str(uuid.uuid4()), 'fingerprint':fingerprint, 'project_id':project_id, 'client':project['client'],
           'domain':project.get('domain',''), 'review_id':review_id, 'proof_token':proof['token'], 'proof_url':proof_url,
           'revision':linked['revision'], 'recipient':linked['contact']['email'], 'sender':sender, 'subject':subject,
           'message':message, 'payload':payload, 'status':'draft', 'created_at':_now(), 'actor':actor}
    def mutate(data):
        for old in data['drafts']:
            if old['project_id']==project_id and old['proof_token']==draft['proof_token'] and old['status'] in ('sending','unknown','queued'):
                raise client_email.EmailError('This proof already has a send attempt. Check its GHL conversation before sending again.')
            if old['fingerprint']==fingerprint and old['status']=='draft':
                draft.update(old);return None
        data['drafts'].append(draft);return data
    jsonstore.update_json(_path(),mutate,default={'drafts':[]},durable=True)
    return public(draft)

def public(row):
    return {k:row.get(k) for k in ('id','review_id','client','recipient','sender','subject','message','proof_url','status','message_id','error')}

def attempts(project_id):
    rows=[r for r in jsonstore.read_json(_path(),default={'drafts':[]})['drafts'] if r['project_id']==project_id and r['status']!='draft']
    for row in rows:
        _record_receipt(row)
    return [public(row) for row in reversed(rows)]


def _record_receipt(draft):
    if draft.get('status') != 'queued' or not draft.get('message_id'):
        return
    # Reconcile a durable GHL receipt without ever posting the email again.
    ok, _ = ad_builder_link._api('POST', f"/api/project/{draft['project_id']}/workflow",
        {'action':'sent', 'token':draft['proof_token'], 'messageId':draft['message_id']})
    if not ok:
        draft['error'] = 'GHL accepted the email. Campaign history is still syncing; reopen this result to retry the history update.'

def send(draft_id):
    draft=next((r for r in jsonstore.read_json(_path(),default={'drafts':[]})['drafts'] if r['id']==draft_id),None)
    if not draft:
        raise client_email.EmailError('Preview the message before sending.')
    if draft['status']!='draft':
        _record_receipt(draft)
        return public(draft)
    project, linked=context(draft['project_id'])
    if not linked.get('linked') or linked.get('revision')!=draft['revision'] or linked['contact']['email']!=draft['recipient']:
        raise client_email.EmailError('The recipient changed. Preview a new message before sending.')
    ok, result=ad_builder_link._api('POST',f"/api/project/{draft['project_id']}/workflow",{'reviewId':draft['review_id']})
    if not ok or result.get('token')!=draft['proof_token']:
        raise client_email.EmailError('The proof changed. Review it and preview a new message.')
    claimed=False
    def claim(data):
        nonlocal claimed
        row=next(r for r in data['drafts'] if r['id']==draft_id)
        if row['status']!='draft':draft.update(row);return None
        if any(r['id']!=draft_id and r['project_id']==draft['project_id'] and r['proof_token']==draft['proof_token'] and r['status'] in ('sending','unknown','queued') for r in data['drafts']):
            raise client_email.EmailError('This proof already has a send attempt. Check GHL before retrying.')
        row.update(status='sending',attempted_at=_now());draft.update(row);claimed=True;return data
    jsonstore.update_json(_path(),claim,default={'drafts':[]},durable=True)
    if not claimed:return public(draft)
    status,error,receipt='unknown','GHL’s response is uncertain. Check the contact’s conversation before sending again.',{}
    try:
        response=requests.post('https://services.leadconnectorhq.com/conversations/messages',
            headers={**ghl_contacts._headers(),'Version':'v3'},json=draft['payload'],timeout=30)
        if response.ok:
            receipt=response.json()
            if isinstance(receipt,dict) and receipt.get('messageId'):
                status,error='queued',''
        elif 400<=response.status_code<500 and response.status_code not in (408,429):
            status,error='failed',f'GHL declined the email (HTTP {response.status_code}). Check sender and message-send permissions, then preview again.'
    except (requests.RequestException,ValueError):
        pass
    def finish(data):
        row=next(r for r in data['drafts'] if r['id']==draft_id)
        row.update(status=status,error=error,message_id=receipt.get('messageId') if isinstance(receipt,dict) else None,
                   conversation_id=receipt.get('conversationId') if isinstance(receipt,dict) else None,updated_at=_now())
        draft.update(row);return data
    jsonstore.update_json(_path(),finish,default={'drafts':[]},durable=True)
    _record_receipt(draft)
    return public(draft)
