// Offline browser smoke: all requests are intercepted; no Google account is used.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async () => {
  const browser = await chromium.launch({channel: 'msedge', headless: true});
  const page = await browser.newPage({viewport: {width: 1360, height: 1000}});
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  const draft = {id: 'test', state: 'VALIDATED', created_at: '2026-09-14', data: {name: 'Video launch', headline: 'New video', description: 'Explore our services', daily_budget: 25, video_id: 'dQw4w9WgXcQ', placements: ['youtubeShorts']}};
  const report = {account: {currencyCode: 'USD', timeZone: 'America/New_York'}, range: 'LAST_30_DAYS', checked_at: '2026-09-14', scope: 'Campaign totals may include other placements.', campaigns: [{id: '1', name: 'Video launch', type: 'DEMAND_GEN', status: 'PAUSED', impressions: 1000, clicks: 10, cost: 50, conversions: 2, ctr: 1, cpa: 25, roas: 3, recommendations: ['Review the opening hook.']} ]};
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/static/youtube-ads.js') return route.fulfill({contentType: 'application/javascript', body: fs.readFileSync(path.join(__dirname, 'hub/static/youtube-ads.js'), 'utf8')});
    if (url.pathname.endsWith('/api/accounts')) return route.fulfill({json: {accounts: [{id: '1234567890', name: 'Test account', formatted_id: '123-456-7890', currency: 'USD'}]}});
    if (url.pathname.endsWith('/api/drafts')) return route.fulfill({json: {drafts: [draft]}});
    if (url.pathname.endsWith('/api/report')) return route.fulfill({json: report});
    if (url.pathname.endsWith('/create')) {draft.state = 'CREATED_PAUSED'; return route.fulfill({json: {state: draft.state}});}
    if (url.pathname === '/') return route.fulfill({contentType: 'text/html; charset=utf-8', body: fs.readFileSync(path.join(__dirname, 'tmp/youtube-ads-preview.html'), 'utf8')});
    return route.abort();
  });
  try {
    await page.goto('http://youtube-ads.test/');
    await page.getByRole('button', {name: 'Load accounts', exact: true}).click();
    await page.locator('#yta-account').selectOption('1234567890');
    await page.getByRole('button', {name: 'Load drafts', exact: true}).click();
    await page.getByRole('button', {name: 'Create paused campaign', exact: true}).waitFor();
    page.on('dialog', dialog => dialog.accept());
    await page.getByRole('button', {name: 'Create paused campaign', exact: true}).click();
    await page.getByText('CREATED_PAUSED', {exact: false}).waitFor();
    await page.getByRole('button', {name: 'Monitor', exact: true}).click();
    await page.getByRole('button', {name: 'Refresh now', exact: true}).click();
    await page.locator('#yta-metrics tbody tr').waitFor();
    assert.equal(await page.locator('#yta-metrics tbody td').count(), 12);
    await page.getByRole('button', {name: 'Optimize', exact: true}).click();
    await page.getByText('Review the opening hook.').waitFor();
    await page.getByRole('button', {name: 'Report', exact: true}).click();
    assert.equal(await page.locator('#yta-report-copy tbody tr').count(), 1);
    await page.screenshot({path: path.join(__dirname, 'tmp/youtube-ads-report.png'), fullPage: true});
    await page.locator('#yta-customer').fill('9999999999');
    assert.equal(await page.locator('#yta-report-copy tbody tr').count(), 0);
    await page.setViewportSize({width: 390, height: 844});
    await page.getByRole('button', {name: 'Build', exact: true}).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    console.log('Browser checks passed: draft rendering, paused creation, monitoring, optimization, reporting, account reset, mobile layout.');
  } finally {await browser.close();}
})().catch(error => {console.error(error); process.exitCode = 1;});
