"""Trade Desk export normalization. Unknown media stays explicitly unmapped."""
import re
from datetime import date
from decimal import Decimal, InvalidOperation


def key(value):
    return re.sub(r'[^a-z0-9]', '', str(value or '').lower())


def normalize(row):
    fields = {key(k): v for k, v in row.items()}
    def get(*names, default=''):
        return next((fields[key(n)] for n in names if fields.get(key(n)) not in (None, '')), default)
    def number(*names):
        if names[0] in ('impressions', 'spend') and get(*names) == '':
            raise ValueError('Required metric column is missing: ' + names[0])
        value = str(get(*names, default='0')).replace(',', '').strip()
        try:
            result = Decimal(value)
        except InvalidOperation:
            raise ValueError('Invalid metric: ' + names[0]) from None
        if not result.is_finite() or result < 0:
            raise ValueError('Invalid metric: ' + names[0])
        if names[0] != 'spend' and result != result.to_integral_value():
            raise ValueError('Count must be an integer: ' + names[0])
        if result >= Decimal('100000000000000'):
            raise ValueError('Metric exceeds supported range: ' + names[0])
        return result
    result = {name: str(get(name, alias)).strip() for name, alias in (
        ('advertiser_id', 'AdvertiserId'), ('campaign_id', 'CampaignId'),
        ('ad_group_id', 'AdGroupId'), ('creative_id', 'CreativeId'))}
    if not result['advertiser_id'] or not result['campaign_id']:
        raise ValueError('Advertiser ID and Campaign ID are required')
    if any(len(v) > 100 for v in result.values()):
        raise ValueError('Provider ID exceeds 100 characters')
    result['date'] = date.fromisoformat(str(get('date', 'ReportDate'))[:10])
    result['currency'] = str(get('currency', 'CurrencyCode')).upper().strip()
    if not re.fullmatch('[A-Z]{3}', result['currency']):
        raise ValueError('Explicit three-letter currency is required')
    media = classify(get('product_subtype', 'MediaType', 'Channel', 'AdFormat'))
    starts = ('AudioStarts', 'VideoStarts') if media and media[0] == 'audio' else ('VideoStarts', 'AudioStarts')
    completions = ('AudioCompletions', 'VideoCompletions') if media and media[0] == 'audio' else ('VideoCompletions', 'AudioCompletions')
    result.update(impressions=number('impressions'), clicks=number('clicks'),
                  spend=number('spend', 'AdvertiserCost'), starts=number('starts', *starts),
                  completions=number('completions', *completions))
    for name, alias in [('campaign_name', 'Campaign'), ('ad_group_name', 'AdGroup'), ('creative_name', 'Creative')]:
        result[name] = str(get(name, alias))[:300]
    result['classification'] = str(get('product_subtype', 'MediaType', 'Channel', 'AdFormat'))[:300]
    result['product'] = classify(result['classification'], get('IsRetargeting'))
    result['size'] = size(get('width', 'CreativeWidth'), get('height', 'CreativeHeight'), get('CreativeSize'))
    return result


def classify(media, retargeting=False):
    value = key(media)
    aliases = {'display': ('display', 'display'), 'banner': ('display', 'display'),
               'retargeting': ('display', 'retargeting'), 'native': ('display', 'native'),
               'video': ('video', 'online_video'), 'onlinevideo': ('video', 'online_video'),
               'ctv': ('video', 'ctv'), 'connectedtv': ('video', 'ctv'), 'ott': ('video', 'ott'),
               'audio': ('audio', 'streaming_audio'), 'streamingaudio': ('audio', 'streaming_audio'),
               'podcast': ('audio', 'podcast'), 'dooh': ('dooh', 'dooh'), 'digitaloutofhome': ('dooh', 'dooh')}
    product = aliases.get(value)
    if product == ('display', 'display') and str(retargeting).lower() in ('true', '1', 'yes'):
        return ('display', 'retargeting')
    return product


def size(width, height, label=''):
    if (not width or not height) and label:
        match = re.fullmatch(r'\s*(\d+)\s*[xX×]\s*(\d+)\s*', str(label))
        if match:
            width, height = match.groups()
    try:
        w, h = int(width), int(height)
        return (w, h) if 0 < w <= 100000 and 0 < h <= 100000 else (None, None)
    except (ValueError, TypeError):
        return None, None


def derived(impressions, clicks, spend, starts, completions):
    return {'ctr': float(clicks / impressions * 100) if impressions else None,
            'cpm': float(spend / impressions * 1000) if impressions else None,
            'completion_rate': float(completions / starts * 100) if starts else None}
