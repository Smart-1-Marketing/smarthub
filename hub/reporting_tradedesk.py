"""Bounded Trade Desk discovery and configured scheduled CSV report download.

Only the official HTTPS API receives the API token. Downloads are bounded,
redirects refused, and provider error bodies/URLs never enter logs.
"""
import csv
import io
import time
from urllib.parse import urlsplit
import requests
from hub.config import settings


class ProviderError(Exception):
    pass


class TradeDesk:
    base = 'https://api.thetradedesk.com/v3/'

    def __init__(self, config=None, session=None):
        self.config = config or settings
        self.session = session or requests.Session()
        self.deadline = time.monotonic() + 100

    def health(self):
        missing = [name for name, value in (
            ('TTD_API_TOKEN', self.config.ttd_api_token),
            ('TTD_PARTNER_ID', self.config.ttd_partner_id),
            ('TTD_REPORT_URL', self.config.ttd_report_url)) if not value]
        return {'configured': not missing, 'missing': missing,
                'live_verified': False, 'report_source': 'scheduled CSV export'}

    def request(self, method, url, **kwargs):
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.hostname != 'api.thetradedesk.com' or parts.port not in (None, 443) or parts.username or parts.password:
            raise ProviderError('Use an HTTPS report URL on api.thetradedesk.com.')
        if time.monotonic() >= self.deadline:
            raise ProviderError('Sync time limit reached; retry with a smaller report.')
        try:
            response = self.session.request(method, url, headers={'TTD-Auth': self.config.ttd_api_token},
                                           timeout=(5, 20), allow_redirects=False, stream=True, **kwargs)
            with response:
                if response.status_code != 200:
                    raise ProviderError('Trade Desk returned HTTP %s. Check API access and report availability.' % response.status_code)
                data = bytearray()
                for chunk in response.iter_content(65536):
                    data.extend(chunk)
                    if len(data) > 25 * 1024 * 1024 or time.monotonic() >= self.deadline:
                        raise ProviderError('Report exceeds the 25 MB or 100 second sync limit.')
                return bytes(data)
        except requests.RequestException:
            raise ProviderError('Trade Desk could not be reached. Retry or check API access.') from None

    def query(self, path, body):
        import json
        offset = 0
        for _ in range(100):
            raw = self.request('POST', self.base + path, json={**body, 'PageStartIndex': offset, 'PageSize': 100})
            try:
                data = json.loads(raw)
                rows = data['Result']
                if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                    raise ValueError()
            except (ValueError, KeyError, TypeError):
                raise ProviderError('Trade Desk returned an unexpected discovery response.') from None
            yield from rows
            offset += len(rows)
            if len(rows) < 100:
                return
        raise ProviderError('Discovery exceeded 10,000 records; narrow the configured account.')

    def advertisers(self):
        if not self.config.ttd_api_token or not self.config.ttd_partner_id:
            raise ProviderError('Set TTD_API_TOKEN and TTD_PARTNER_ID to discover advertisers.')
        return self.query('advertiser/query/partner', {'PartnerId': self.config.ttd_partner_id})

    def campaigns(self, advertiser):
        return self.query('campaign/query/advertiser', {'AdvertiserId': advertiser})

    def ad_groups(self, campaign):
        return self.query('adgroup/query/campaign', {'CampaignId': campaign})

    def creatives(self, advertiser):
        return self.query('creative/query/advertiser', {'AdvertiserId': advertiser})

    def daily_rows(self):
        if not self.config.ttd_report_url:
            raise ProviderError('Set TTD_REPORT_URL to a completed daily, creative-level CSV export from Trade Desk.')
        raw = self.request('GET', self.config.ttd_report_url)
        try:
            reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
            if not reader.fieldnames or len(reader.fieldnames) < 5:
                raise ValueError()
            rows = list(reader)
            if len(rows) > 100000:
                raise ProviderError('Report exceeds 100,000 rows; use a smaller export.')
            return rows
        except (UnicodeError, csv.Error, ValueError):
            raise ProviderError('Expected a UTF-8 CSV daily report. Check the report delivery format.') from None
