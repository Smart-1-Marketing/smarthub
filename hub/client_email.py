"""Client 360 links to Smart 1's GHL contacts and reads their email history.

Email composition and sending remain in GHL. This module never sends a message
or uses the client's own subaccount mapping. Links are explicit Hub overlays.
"""
from __future__ import annotations
import os
import re
import uuid
from html.parser import HTMLParser
from datetime import datetime, timezone
from urllib.parse import quote
import requests
from . import ghl_contacts, jsonstore
from .client_key import domain_key

class EmailError(ValueError):
    pass

def _plain(value):
    class Reader(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True); self.parts=[]; self.hidden=0
        def handle_starttag(self, tag, attrs):
            if tag in ('script', 'style'): self.hidden += 1
            if tag in ('br', 'p', 'div'): self.parts.append('\n')
        def handle_endtag(self, tag):
            if tag in ('script', 'style') and self.hidden: self.hidden -= 1
        def handle_data(self, data):
            if not self.hidden: self.parts.append(data)
    reader=Reader(); reader.feed(str(value or '')); reader.close()
    return ''.join(reader.parts).strip()[:1000]

def _path():
    return os.path.join(jsonstore.data_dir('suite'), 'client_email_links.json')

def _name(value):
    return re.sub(r'\s+', ' ', str(value or '').strip()).casefold()

def _domain(value):
    value = str(value or '').strip()
    if value and (len(value) > 300 or not domain_key(value)):
        raise EmailError('Open this client from Client 360 with a valid website.')
    return value

def _matches(row, client, domain):
    left, right = domain_key(row.get('domain') or ''), domain_key(domain)
    if left and right:
        return left == right
    return _name(row.get('client')) == _name(client)

def _client(value):
    value = str(value or '').strip()
    if not value or len(value) > 200:
        raise EmailError('Open email from the client record in Client 360.')
    return value

def _id(value):
    value = str(value or '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise EmailError('Choose a valid Smart 1 contact.')
    return value

def _store():
    data = jsonstore.read_json(_path(), default={'links': []})
    if not isinstance(data, dict) or not isinstance(data.get('links'), list):
        raise EmailError('The saved contact links could not be read. No link was changed.')
    return data

def _get(route, params=None):
    if not ghl_contacts.token():
        raise EmailError('Smart 1 GHL access is not configured.')
    try:
        response = requests.get('https://services.leadconnectorhq.com' + route,
            headers=ghl_contacts._headers(), params=params, timeout=20)
    except requests.RequestException as exc:
        raise EmailError('GHL could not be reached. Refresh to try again.') from exc
    if not response.ok:
        raise EmailError(f'GHL refused this read (HTTP {response.status_code}). Check Smart 1 contact, conversation and message read access.')
    try:
        data = response.json()
    except ValueError as exc:
        raise EmailError('GHL returned an unreadable response.') from exc
    if not isinstance(data, dict):
        raise EmailError('GHL returned an unexpected response.')
    return data

def _location():
    try:
        return ghl_contacts.location_id()
    except Exception as exc:
        raise EmailError('The Smart 1 Marketing GHL location is not configured. The agency company ID cannot be used here.') from exc

def _contact(contact_id):
    contact_id = _id(contact_id)
    raw = _get('/contacts/' + contact_id).get('contact')
    if not isinstance(raw, dict) or raw.get('id') != contact_id or raw.get('locationId') != _location():
        raise EmailError('This contact is not verified in Smart 1 Marketing’s GHL account.')
    email = str(raw.get('email') or '').strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        raise EmailError('This contact has no usable email address. Add it in GHL first.')
    return {'id': contact_id, 'location_id': raw['locationId'], 'email': email,
            'name': str(raw.get('name') or raw.get('contactName') or ' '.join(filter(None, [raw.get('firstName'), raw.get('lastName')]))),
            'company': str(raw.get('companyName') or '')}

def lookup(client, domain=""):
    client = _client(client)
    domain = _domain(domain)
    rows = [r for r in _store()['links'] if _matches(r, client, domain)]
    if len(rows) > 1:
        raise EmailError('This name matches several client contact links. Open the client with its website from Client 360.')
    return rows[0] if rows else None

def search(term):
    term = str(term or '').strip()
    if not 2 <= len(term) <= 200:
        raise EmailError('Search by the contact’s name, email or company (at least two characters).')
    location = _location()
    data = _get('/contacts/', {'locationId': location, 'query': term, 'limit': 20})
    if not isinstance(data.get('contacts'), list):
        raise EmailError('GHL did not return a contact list.')
    return [{'id': r.get('id'), 'name': r.get('contactName') or r.get('name') or ' '.join(filter(None, [r.get('firstName'), r.get('lastName')])),
             'email': r.get('email') or '', 'company': r.get('companyName') or ''}
            for r in data['contacts'] if r.get('id') and r.get('locationId', location) == location]

def link(client, contact_id, revision=None, actor='', domain=''):
    client = _client(client)
    domain = _domain(domain)
    contact = _contact(contact_id)
    row = dict(contact, client=client, domain=domain, revision=str(uuid.uuid4()), linked_by=actor,
               linked_at=datetime.now(timezone.utc).isoformat())
    def mutate(data):
        if not isinstance(data, dict) or not isinstance(data.get('links'), list):
            raise EmailError('Contact links are unreadable. Nothing changed.')
        existing = [r for r in data['links'] if _matches(r, client, domain)]
        if len(existing) > 1 or (existing[0]['revision'] if existing else None) != revision:
            raise EmailError('The client contact link changed in another session. Refresh before choosing again.')
        if any(r.get('id') == contact['id'] and r.get('location_id') == contact['location_id'] and not _matches(r, client, domain) for r in data['links']):
            raise EmailError('That contact is already linked to another client. Choose the correct contact before continuing.')
        data['links'] = [r for r in data['links'] if not _matches(r, client, domain)] + [row]
        return data
    jsonstore.update_json(_path(), mutate, default={'links': []}, durable=True)
    return row

def unlink(client, revision, domain=""):
    client = _client(client)
    domain = _domain(domain)
    def mutate(data):
        if not isinstance(data, dict) or not isinstance(data.get('links'), list):
            raise EmailError('Contact links are unreadable. Nothing changed.')
        rows = [r for r in data['links'] if _matches(r, client, domain)]
        if len(rows) != 1 or rows[0]['revision'] != revision:
            raise EmailError('The contact link changed. Refresh before unlinking.')
        data['links'] = [r for r in data['links'] if not _matches(r, client, domain)]
        return data
    jsonstore.update_json(_path(), mutate, default={'links': []}, durable=True)


def summary(client, domain=""):
    saved = lookup(client, domain)
    if not saved:
        return {'linked': False, 'messages': []}
    if domain_key(domain) and not domain_key(saved.get('domain') or ''):
        raise EmailError('Verify this contact for the client’s website by linking it again.')
    if saved['location_id'] != _location():
        raise EmailError('Smart 1’s configured account changed. Verify this client’s contact link again.')
    contact = _contact(saved['id'])
    if contact['email'].casefold() != saved['email'].casefold():
        raise EmailError('The contact’s email changed in GHL. Review and link the contact again.')
    base = (os.environ.get('SUITE_APP_BASE') or 'https://app.gohighlevel.com').rstrip('/')
    if not base.startswith('https://'):
        base = 'https://app.gohighlevel.com'
    result = {'linked': True, 'contact': contact, 'revision': saved['revision'], 'messages': [],
              'ghl_url': f"{base}/v2/location/{quote(contact['location_id'], safe='')}/contacts/detail/{quote(contact['id'], safe='')}",
              'measured': False}
    try:
        data = _get('/conversations/search', {'locationId': contact['location_id'], 'contactId': contact['id'], 'limit': 20})
        if not isinstance(data.get('conversations'), list):
            raise EmailError('GHL did not return a conversation list.')
        for conversation in data['conversations']:
            if conversation.get('contactId') != contact['id'] or conversation.get('locationId') != contact['location_id']:
                raise EmailError('GHL returned a conversation for a different contact. It was not displayed.')
            cid = _id(conversation.get('id'))
            response = _get(f'/conversations/{cid}/messages', {'type': 'TYPE_EMAIL', 'limit': 20})
            envelope = response.get('messages')
            messages = envelope.get('messages') if isinstance(envelope, dict) else envelope
            if not isinstance(messages, list):
                raise EmailError('GHL did not return an email history.')
            for msg in messages:
                if msg.get('contactId') not in (None, contact['id']) or msg.get('locationId') not in (None, contact['location_id']):
                    raise EmailError('GHL returned a message for a different contact. It was not displayed.')
                if str(msg.get('messageType') or msg.get('type') or '').upper() not in ('TYPE_EMAIL', 'EMAIL', '3'):
                    continue
                meta = msg.get('meta') if isinstance(msg.get('meta'), dict) else {}
                email_meta = meta.get('email') if isinstance(meta.get('email'), dict) else {}
                result['messages'].append({'id': msg.get('id'), 'conversation_id': cid,
                    'subject': str(msg.get('subject') or email_meta.get('subject') or 'Email'),
                    'body': _plain(msg.get('body')), 'at': str(msg.get('dateAdded') or ''),
                    'direction': str(msg.get('direction') or ''), 'status': str(msg.get('status') or 'unknown')})
        result['messages'].sort(key=lambda m: m['at'], reverse=True)
        result['messages'] = result['messages'][:20]
        result['measured'] = True
    except EmailError as exc:
        result['messages'] = []
        result['error'] = str(exc)
    return result
