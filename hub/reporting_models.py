"""Canonical reporting tables on the Hub's shared database.

Additive migration v1: imported before extensions.create_all(), using its
Postgres DDL lock and retry policy. No parallel database or app.
"""
from sqlalchemy import Column as C, Integer, String, Date, DateTime, Numeric, JSON, ForeignKey, UniqueConstraint, CheckConstraint
from hub.extensions import db


def table(name, *columns):
    return db.Table('reporting_' + name, C('id', Integer, primary_key=True), *columns)


providers = table(
    'providers',
    C('code', String(40), unique=True, nullable=False),
    C('name', String(160), nullable=False),
)
products = table(
    'products',
    C('product_family', String(40), nullable=False),
    C('product_subtype', String(40), nullable=False),
    UniqueConstraint('product_family', 'product_subtype'),
)
accounts = table(
    'provider_accounts',
    C('provider_id', ForeignKey(providers.c.id), nullable=False),
    C('partner_id', String(100), nullable=False),
    C('external_account_id', String(100), nullable=False),
    C('account_name', String(300)),
    C('client_id', String(300)),
    C('mapped_by', String(300)),
    C('mapped_at', DateTime),
    C('raw_payload', JSON),
    UniqueConstraint('provider_id', 'partner_id', 'external_account_id'),
)
campaigns = table(
    'campaigns',
    C('provider_account_id', ForeignKey(accounts.c.id), nullable=False),
    C('external_campaign_id', String(100), nullable=False),
    C('campaign_name', String(300)),
    C('raw_payload', JSON),
    UniqueConstraint('provider_account_id', 'external_campaign_id'),
)
line_items = table(
    'line_items',
    C('campaign_id', ForeignKey(campaigns.c.id), nullable=False),
    C('external_line_item_id', String(100), nullable=False),
    C('line_item_name', String(300)),
    C('product_id', ForeignKey(products.c.id)),
    C('classification', String(300)),
    C('raw_payload', JSON),
    UniqueConstraint('campaign_id', 'external_line_item_id'),
)
creatives = table(
    'creatives',
    C('provider_account_id', ForeignKey(accounts.c.id), nullable=False),
    C('external_creative_id', String(100), nullable=False),
    C('creative_name', String(300)),
    C('width', Integer),
    C('height', Integer),
    C('raw_payload', JSON),
    UniqueConstraint('provider_account_id', 'external_creative_id'),
    CheckConstraint('(width IS NULL AND height IS NULL) OR (width > 0 AND height > 0)'),
)
runs = table(
    'sync_runs',
    C('provider_id', ForeignKey(providers.c.id), nullable=False),
    C('started_at', DateTime, nullable=False),
    C('finished_at', DateTime),
    C('status', String(30), nullable=False),
    C('actor', String(300)),
    C('rows_written', Integer, nullable=False, default=0),
    C('error', String(500)),
)
errors = table(
    'sync_errors',
    C('sync_run_id', ForeignKey(runs.c.id), nullable=False),
    C('row_number', Integer),
    C('message', String(500), nullable=False),
)
raw_payloads = table(
    'raw_payloads',
    C('sync_run_id', ForeignKey(runs.c.id), nullable=False),
    C('row_number', Integer, nullable=False),
    C('payload', JSON, nullable=False),
)
metrics = table(
    'daily_metrics',
    C('provider_account_id', ForeignKey(accounts.c.id), nullable=False),
    C('campaign_id', ForeignKey(campaigns.c.id), nullable=False),
    C('line_item_id', ForeignKey(line_items.c.id)),
    C('creative_id', ForeignKey(creatives.c.id)),
    C('external_line_item_id', String(100), nullable=False),
    C('external_creative_id', String(100), nullable=False),
    C('metric_date', Date, nullable=False),
    C('currency', String(3), nullable=False),
    C('product_id', ForeignKey(products.c.id)),
    C('impressions', Numeric(20, 0), nullable=False),
    C('clicks', Numeric(20, 0), nullable=False),
    C('spend', Numeric(20, 6), nullable=False),
    C('starts', Numeric(20, 0), nullable=False),
    C('completions', Numeric(20, 0), nullable=False),
    C('sync_run_id', ForeignKey(runs.c.id), nullable=False),
    UniqueConstraint('provider_account_id', 'campaign_id', 'external_line_item_id', 'external_creative_id', 'metric_date', 'currency'),
)
TAXONOMY = [('display', s) for s in ('display', 'retargeting', 'native')] + [('video', s) for s in ('online_video', 'ctv', 'ott')] + [('audio', s) for s in ('streaming_audio', 'podcast')] + [('dooh', 'dooh')]
