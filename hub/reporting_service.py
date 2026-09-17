"""Reporting persistence: stable provider identities, replacement upserts and audit."""
from datetime import datetime, timedelta
import time
from sqlalchemy import select, func, Index
from sqlalchemy.exc import IntegrityError
from hub.extensions import db
from hub import reporting_models as m
from hub.reporting_normalize import normalize, classify, size, derived
from hub.reporting_tradedesk import TradeDesk, ProviderError

# The database, not a worker-local flag, excludes simultaneous syncs.
Index('reporting_one_active_sync', m.runs.c.provider_id, unique=True,
      sqlite_where=m.runs.c.status == 'running', postgresql_where=m.runs.c.status == 'running')


def upsert(cx, table, identity, values):
    if cx.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    elif cx.dialect.name == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError('Reporting supports PostgreSQL and SQLite.')
    stmt = insert(table).values(**identity, **values)
    stmt = stmt.on_conflict_do_update(index_elements=list(identity), set_=values) if values else stmt.on_conflict_do_nothing(index_elements=list(identity))
    cx.execute(stmt)
    return cx.execute(select(table.c.id).filter_by(**identity)).scalar_one()


def seed(cx):
    provider = upsert(cx, m.providers, {'code': 'tradedesk'}, {'name': 'The Trade Desk'})
    for family, subtype in m.TAXONOMY:
        upsert(cx, m.products, {'product_family': family, 'product_subtype': subtype}, {})
    return provider


def product_id(cx, product):
    if product is None:
        return None
    return cx.execute(select(m.products.c.id).where(m.products.c.product_family == product[0], m.products.c.product_subtype == product[1])).scalar_one()


def provider_id(row, field):
    value = str(row.get(field) or '').strip()
    if not value or len(value) > 100:
        raise ProviderError('Discovery response is missing a valid ' + field + '.')
    return value


def discover(adapter, provider):
    known = {}
    for row in adapter.advertisers():
        external = provider_id(row, 'AdvertiserId')
        with db.engine.begin() as cx:
            account = upsert(cx, m.accounts, {'provider_id': provider, 'partner_id': adapter.config.ttd_partner_id, 'external_account_id': external}, {'account_name': str(row.get('AdvertiserName') or external)[:300], 'raw_payload': row})
        known[external] = account
        for campaign in adapter.campaigns(external):
            cid = provider_id(campaign, 'CampaignId')
            with db.engine.begin() as cx:
                internal = upsert(cx, m.campaigns, {'provider_account_id': account, 'external_campaign_id': cid}, {'campaign_name': str(campaign.get('CampaignName') or cid)[:300], 'raw_payload': campaign})
            for group in adapter.ad_groups(cid):
                gid = provider_id(group, 'AdGroupId')
                media = str(group.get('MediaType') or group.get('AdGroupType') or '')[:300]
                with db.engine.begin() as cx:
                    upsert(cx, m.line_items, {'campaign_id': internal, 'external_line_item_id': gid}, {'line_item_name': str(group.get('AdGroupName') or gid)[:300], 'product_id': product_id(cx, classify(media)), 'classification': media, 'raw_payload': group})
        for creative in adapter.creatives(external):
            cid = provider_id(creative, 'CreativeId')
            w, h = size(creative.get('Width'), creative.get('Height'), creative.get('CreativeSize'))
            values = {'creative_name': str(creative.get('CreativeName') or cid)[:300], 'raw_payload': creative}
            if w is not None:
                values.update(width=w, height=h)
            with db.engine.begin() as cx:
                upsert(cx, m.creatives, {'provider_account_id': account, 'external_creative_id': cid}, values)
    return known


def ingest(cx, row, account, run):
    campaign = upsert(cx, m.campaigns, {'provider_account_id': account, 'external_campaign_id': row['campaign_id']}, ({'campaign_name': row['campaign_name']} if row['campaign_name'] else {}))
    product = product_id(cx, row['product'])
    line = None
    if row['ad_group_id'] and not row['classification']:
        product = cx.execute(select(m.line_items.c.product_id).where(m.line_items.c.campaign_id == campaign, m.line_items.c.external_line_item_id == row['ad_group_id'])).scalar_one_or_none()
    if row['ad_group_id']:
        values = {'product_id': product}
        if row['ad_group_name']:
            values['line_item_name'] = row['ad_group_name']
        if row['classification']:
            values['classification'] = row['classification']
        line = upsert(cx, m.line_items, {'campaign_id': campaign, 'external_line_item_id': row['ad_group_id']}, values)
    creative = None
    if row['creative_id']:
        values = {'creative_name': row['creative_name']} if row['creative_name'] else {}
        if row['size'][0] is not None:
            values.update(width=row['size'][0], height=row['size'][1])
        creative = upsert(cx, m.creatives, {'provider_account_id': account, 'external_creative_id': row['creative_id']}, values)
    return upsert(cx, m.metrics, {'provider_account_id': account, 'campaign_id': campaign, 'external_line_item_id': row['ad_group_id'], 'external_creative_id': row['creative_id'], 'metric_date': row['date'], 'currency': row['currency']}, dict(line_item_id=line, creative_id=creative, product_id=product, sync_run_id=run, **{k: row[k] for k in ('impressions', 'clicks', 'spend', 'starts', 'completions')}))


def sync(actor='', adapter=None):
    adapter = adapter or TradeDesk()
    deadline = time.monotonic() + 140
    with db.engine.begin() as cx:
        provider = seed(cx)
    try:
        with db.engine.begin() as cx:
            run = cx.execute(m.runs.insert().values(provider_id=provider, started_at=datetime.utcnow(), status='running', actor=actor, rows_written=0).returning(m.runs.c.id)).scalar_one()
    except IntegrityError:
        return {'status': 'busy', 'error': 'A Trade Desk sync is already running. If a worker stopped, an administrator must mark that run failed before retrying.'}, 409
    written, rejected = 0, 0
    failure = None
    try:
        known = discover(adapter, provider)
        rows = adapter.daily_rows()
        # Retain rejected and accepted source rows before validation/transaction.
        with db.engine.begin() as cx:
            if rows:
                cx.execute(m.raw_payloads.insert(), [{'sync_run_id': run, 'row_number': i, 'payload': raw} for i, raw in enumerate(rows, 1)])
        normalized, seen = [], set()
        for i, raw in enumerate(rows, 1):
            try:
                row = normalize(raw)
                if row['advertiser_id'] not in known:
                    raise ValueError('Advertiser is not in the configured partner; row rejected')
                grain = tuple(row[k] for k in ('advertiser_id', 'campaign_id', 'ad_group_id', 'creative_id', 'date', 'currency'))
                if grain in seen:
                    raise ValueError('Duplicate daily grain in export; export only one row per day/campaign/ad group/creative/currency')
                seen.add(grain)
                normalized.append((row, known[row['advertiser_id']]))
            except (ValueError, TypeError):
                rejected += 1
                with db.engine.begin() as cx:
                    cx.execute(m.errors.insert().values(sync_run_id=run, row_number=i, message='Invalid or duplicate daily row. Check IDs, date, currency, nonnegative metrics and report grain.'))
        # A malformed export never partially replaces a previously good report.
        if rejected:
            raise ProviderError(f'{rejected} invalid or duplicate report rows; metrics were not changed.')
        if not normalized:
            raise ProviderError('The report contained no daily rows; existing metrics were preserved.')
        with db.engine.begin() as cx:
            for row, account in normalized:
                if time.monotonic() >= deadline:
                    raise ProviderError('Sync exceeded its time budget; use a smaller report. Metrics were preserved.')
                ingest(cx, row, account, run)
                written += 1
    except Exception as exc:
        written = 0
        failure = str(exc) if isinstance(exc, ProviderError) else 'Reporting sync failed. Check database availability and provider report format.'
    status = 'failed' if failure else 'succeeded'
    with db.engine.begin() as cx:
        cx.execute(m.runs.update().where(m.runs.c.id == run).values(status=status, error=failure, rows_written=written, finished_at=datetime.utcnow()))
        if failure:
            cx.execute(m.errors.insert().values(sync_run_id=run, message=failure[:500]))
    return {'run_id': run, 'status': status, 'rows_written': written, 'error': failure}, 502 if failure else 200


def health():
    result = {'provider': 'tradedesk', **TradeDesk().health(), 'schema_ready': False}
    with db.engine.connect() as cx:
        counts = {name: cx.execute(select(func.count()).select_from(table)).scalar_one() for name, table in [('accounts', m.accounts), ('campaigns', m.campaigns), ('line_items', m.line_items), ('creatives', m.creatives), ('daily_rows', m.metrics)]}
        result.update(schema_ready=True, counts=counts)
        result['accounts'] = [dict(r) for r in cx.execute(select(m.accounts.c.id, m.accounts.c.account_name, m.accounts.c.external_account_id, m.accounts.c.client_id)).mappings()]
        result['unmapped_accounts'] = [r for r in result['accounts'] if not r['client_id']]
        result['unmapped_products'] = [dict(r) for r in cx.execute(select(m.line_items.c.id, m.line_items.c.line_item_name, m.line_items.c.classification).where(m.line_items.c.product_id.is_(None))).mappings()]
        result['unmapped_metric_rows'] = cx.execute(select(func.count()).select_from(m.metrics).where(m.metrics.c.product_id.is_(None))).scalar_one()
        result['runs'] = [dict(r) for r in cx.execute(select(m.runs).order_by(m.runs.c.id.desc()).limit(20)).mappings()]
        result['errors'] = [dict(r) for r in cx.execute(select(m.errors).order_by(m.errors.c.id.desc()).limit(30)).mappings()]
        latest = cx.execute(select(func.max(m.metrics.c.metric_date))).scalar_one()
        result['latest_metric_date'] = latest.isoformat() if latest else None
        result['stale'] = latest is None or latest < (datetime.utcnow() - timedelta(days=2)).date()
        result['totals'] = []
        cols = [func.sum(m.metrics.c[k]).label(k) for k in ('impressions', 'clicks', 'spend', 'starts', 'completions')]
        for record in cx.execute(select(m.metrics.c.currency, *cols).group_by(m.metrics.c.currency)).mappings():
            item = dict(record)
            item.update(derived(*(item[k] for k in ('impressions', 'clicks', 'spend', 'starts', 'completions'))))
            result['totals'].append(item)
    return result


def match(account_id, client_id, actor, clients):
    # Registry keys are names throughout the Hub; validate against that registry.
    if client_id is not None and (len(client_id) > 300 or client_id not in {c['name'] for c in clients}):
        raise ValueError('Choose a client from the SmartHub registry.')
    with db.engine.begin() as cx:
        changed = cx.execute(m.accounts.update().where(m.accounts.c.id == account_id).values(client_id=client_id, mapped_by=actor, mapped_at=datetime.utcnow()))
        if not changed.rowcount:
            raise ValueError('Provider account not found.')
