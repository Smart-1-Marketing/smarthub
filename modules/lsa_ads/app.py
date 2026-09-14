"""Independent LSA tool, sharing the team's existing Ads authorization and plans."""
from pathlib import Path
import csv
import io
import re
from decimal import Decimal, InvalidOperation

from flask import Flask, Response, jsonify, render_template, request
from modules.ads_builder import local_services as lsa
from modules.ads_builder.google_ads import GoogleAdsError

app = Flask(__name__, template_folder=str(Path(__file__).parents[1] / 'ads_builder' / 'templates'))
app.register_blueprint(lsa.bp)
from .intake import bp as intake_bp
app.register_blueprint(intake_bp)
PUBLIC_PREFIXES = ('/intake/',)


@app.context_processor
def context():
    return dict(mount=request.script_root or '/tools/lsa', lsa_workspace=True)


@app.errorhandler(GoogleAdsError)
def google_error(exc):
    return jsonify(error=exc.message), exc.status if 400 <= exc.status < 600 else 502


@app.after_request
def private(response):
    response.headers['Cache-Control'] = 'no-store'
    return response


@app.get('/')
def workspace():
    return render_template('lsa_workspace.html')


def report():
    days = request.args.get('days', '30')
    if days not in {'7', '30', '90'}:
        raise GoogleAdsError('Choose 7, 30 or 90 days.', status=400)
    return lsa.review_account(lsa.customer_id(request.args.get('customer_id')), int(days))


@app.get('/api/report.csv')
def export_report():
    data = report()
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    def row(*values):
        # Google account/campaign names are untrusted spreadsheet cells.
        writer.writerow([("'" + str(v)) if str(v).lstrip().startswith(('=', '+', '-', '@')) else
                         ('Unavailable' if v is None else v) for v in values])
    row('Local Services Ads report', data['account'].get('descriptiveName', data['customer_id']))
    row('Account ID', data['customer_id'])
    row('Currency', data['account'].get('currencyCode'))
    row('Account time zone', data['account'].get('timeZone'))
    row('Start date', data['start_date'])
    row('End date (excluded)', data['end_date_exclusive'])
    row('Checked at', data['checked_at'])
    row('LSA spend', data['spend'])
    row('Lead records', data['lead_count'])
    row('Charged lead records', data['charged_leads'])
    row('Spend / charged lead', data['cost_per_charged_lead'])
    row('Reporting note', 'Lead creation dates and spend posting dates can differ. PMax lead coverage may differ. This is not invoice reconciliation.')
    for source, error in data['errors'].items():
        row('Unavailable source: ' + source, error)
    row('Campaign', 'Status', 'Budget', 'Budget period', 'Bidding')
    for c in data['campaigns']:
        row(c['name'], c['status'], c['budget'], c['budget_period'], c['bidding'])
    for advice in data['advice']:
        row('Recommended action', advice)
    row('Recent leads (up to 100)', 'Type', 'Service', 'Status', 'Charged', 'Credit')
    for lead in data['leads']:
        row(lead.get('creationDateTime'), lead.get('leadType'), lead.get('serviceId'),
            lead.get('leadStatus'), lead.get('leadCharged'), (lead.get('creditDetails') or {}).get('creditState'))
    return Response('\ufeff' + output.getvalue(), mimetype='text/csv', headers={
        'Content-Disposition': f'attachment; filename="lsa-{data["customer_id"]}-{data["start_date"]}.csv"'})


@app.post('/api/campaign')
def update_campaign():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise GoogleAdsError('Provide campaign details.', status=400)
    cid = lsa.customer_id(body.get('customer_id'))
    campaign_id = str(body.get('campaign_id', ''))
    if not re.fullmatch(r'\d+', campaign_id):
        raise GoogleAdsError('Choose a campaign.', status=400)
    action = body.get('action')
    if action not in {'PAUSED', 'ENABLED', 'budget'}:
        raise GoogleAdsError('Choose pause, enable or budget.', status=400)
    expected = {'PAUSED': 'PAUSE', 'ENABLED': 'ENABLE', 'budget': 'UPDATE'}[action]
    if body.get('confirmation') != expected:
        raise GoogleAdsError(f'Type {expected} to confirm the displayed change.', status=400)
    rows = lsa.google_ads.search(cid, f'''SELECT campaign.id, campaign.status,
        campaign_budget.resource_name, campaign_budget.amount_micros,
        campaign_budget.period, campaign_budget.explicitly_shared
        FROM campaign WHERE campaign.id = {campaign_id}
        AND campaign.advertising_channel_type = 'LOCAL_SERVICES'
        AND campaign.status != 'REMOVED' ''', store=lsa.store)
    if len(rows) != 1:
        raise GoogleAdsError('This is not an editable Local Services campaign. Refresh the account.', status=409)
    campaign, budget = rows[0]['campaign'], rows[0].get('campaignBudget', {})
    if action == 'budget':
        try:
            amount = Decimal(str(body.get('amount', '')))
            if not amount.is_finite() or not 0 < amount <= 1000000 or amount != amount.quantize(Decimal('.01')):
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            raise GoogleAdsError('Enter a positive budget up to 1,000,000 with at most two decimals.', status=400)
        if budget.get('explicitlyShared') or not budget.get('resourceName'):
            raise GoogleAdsError('Manage shared or unavailable budgets in Google.', status=409)
        if body.get('previous_budget') != lsa.google_ads.micros(budget.get('amountMicros')) or body.get('budget_period') != budget.get('period'):
            raise GoogleAdsError('The budget changed. Refresh before updating it.', status=409)
        result = lsa.google_ads.request('post', f'/customers/{cid}/campaignBudgets:mutate', {
            'operations': [{'update': {'resourceName': budget['resourceName'], 'amountMicros': str(int(amount * 1000000))},
                            'updateMask': 'amount_micros'}]}, store=lsa.store, customer_id=cid)
    else:
        if body.get('previous_status') != campaign.get('status'):
            raise GoogleAdsError('The campaign status changed. Refresh before updating it.', status=409)
        result = lsa.google_ads.set_campaign_status(cid, campaign_id, action, store=lsa.store, confirmation=expected)
    lsa.store.log_event('LSA_CAMPAIGN_UPDATED', lsa.actor(), customer_id=cid, campaign_id=campaign_id, change=action)
    return jsonify(ok=True, result=result)
