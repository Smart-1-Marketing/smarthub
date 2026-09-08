import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import * as net from 'node:net';
import { spawn } from 'node:child_process';
import sharp from 'sharp';
import { ProjectStore } from '../src/projects';

test('HTTP workflow: save conflicts, approval locks, render, download and placeholder rejection', { timeout: 240_000 }, async t => {
  const root = path.resolve(__dirname, '..');
  const out = fs.mkdtempSync(path.join(os.tmpdir(), 'ad-http-'));
  const logo = path.join(out, 'logo.png');
  const reverse = path.join(out, 'reverse.png');
  for (const [file, fill] of [[logo, '#000000'], [reverse, '#FFFFFF']]) {
    await sharp(Buffer.from(`<svg width="200" height="60"><rect x="5" y="5" width="190" height="50" rx="8" fill="${fill}"/></svg>`)).png().toFile(file);
  }
  const campaign = { requestId: 'QA-HTTP', campaignName: 'Disposable test campaign',
    brand: { name: 'QA Example', domain: 'example.test',
      colors: { primary: '#123456', secondary: '#654321', accent: '#FFFFFF', light: '#FFFFFF', dark: '#000000' },
      fonts: { headline: 'Montserrat', body: 'Open Sans' }, logos: { primary: logo, reverse } },
    concepts: [{ conceptId: 'A', name: 'Test', layoutFamily: 'T07', hero: {}, copy: { default: { headline: 'Visit us today', cta: 'Learn more' } } }],
  };
  fs.mkdirSync(path.join(out, 'campaigns'));
  fs.writeFileSync(path.join(out, 'campaigns', 'QA-HTTP.json'), JSON.stringify({ campaign, platforms: ['google'] }));
  const project = new ProjectStore(out).create({ projectName: 'QA HTTP', client: 'QA Example', domain: 'example.test', campaignName: campaign.campaignName, requestId: campaign.requestId });
  const portProbe = net.createServer();
  await new Promise<void>(resolve => portProbe.listen(0, '127.0.0.1', resolve));
  const port = (portProbe.address() as net.AddressInfo).port;
  await new Promise<void>(resolve => portProbe.close(() => resolve()));
  const token = 'disposable-local-test-token-only';
  const child = spawn(process.execPath, ['--import', 'tsx', 'src/server.ts'], { cwd: root, windowsHide: true,
    env: { PATH: process.env.PATH, SystemRoot: process.env.SystemRoot, TEMP: process.env.TEMP, TMP: process.env.TMP,
      HOST: '127.0.0.1', PORT: String(port), ADMIN_TOKEN: token, OUTPUT_DIR: out, HEALTH_CHECK_HOURS: '0' },
    stdio: ['ignore', 'pipe', 'pipe'] });
  let logs = ''; child.stdout.on('data', d => logs += d); child.stderr.on('data', d => logs += d);
  t.after(async () => { child.kill(); await new Promise<void>(resolve => { if (child.exitCode !== null) resolve(); else child.once('exit', () => resolve()); }); fs.rmSync(out, { recursive: true, force: true }); });
  const base = `http://127.0.0.1:${port}`;
  let ready = false;
  for (let i = 0; i < 900; i++) {
    try { ready = (await fetch(base + '/healthz')).ok; if (ready) break; } catch { /* starting */ }
    if (child.exitCode !== null) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(ready, logs);
  async function api(route: string, method = 'GET', body?: any) {
    const response = await fetch(base + route, { method, headers: { 'x-admin-token': token, 'content-type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, body: await response.json() };
  }
  const mounted = await fetch(base + '/build', { headers: { 'x-admin-token': token, 'x-forwarded-prefix': '/tools/display-ads' } });
  assert.match(await mounted.text(), /href="\/tools\/display-ads\/diagnostics"/);
  let doc = (await api('/api/campaign/QA-HTTP')).body;
  const stale = structuredClone(doc);
  doc.campaign.campaignName = 'Updated locally';
  assert.equal((await api('/api/campaign/QA-HTTP', 'PUT', doc)).status, 200);
  assert.equal((await api('/api/campaign/QA-HTTP', 'PUT', stale)).status, 409);
  doc = (await api('/api/campaign/QA-HTTP')).body;
  const approve = { conceptId: 'A', platform: 'google', size: '300x250', revision: doc.revision, acceptWarnings: true };
  assert.equal((await api(`/api/project/${project.projectId}/approve-size`, 'POST', { ...approve, revision: undefined })).status, 409);
  assert.equal((await api(`/api/project/${project.projectId}/approve-size`, 'POST', { ...approve, size: '999x999' })).status, 422);
  let result = await api(`/api/project/${project.projectId}/approve-size`, 'POST', approve);
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.ok(result.body.approvals[0].fileHash);
  assert.ok(fs.existsSync(result.body.approvals[0].artifact));
  const changed = structuredClone(doc); changed.campaign.brand.colors.primary = '#654321';
  assert.equal((await api('/api/campaign/QA-HTTP', 'PUT', changed)).status, 409);
  assert.equal((await api(`/api/project/${project.projectId}/override`, 'POST', { ...approve, remove: true })).status, 409);
  result = await api('/api/render', 'POST', { campaign: doc.campaign, platforms: doc.platforms, sizes: ['300x250'], upload: false });
  assert.equal(result.status, 202, JSON.stringify(result.body));
  const jobId = result.body.jobId;
  for (let i = 0; i < 150; i++) {
    result = await api(`/api/render/${jobId}`);
    if (['complete', 'failed'].includes(result.body.status)) break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.equal(result.body.status, 'complete', JSON.stringify(result.body));
  const manifestFile = path.join(out, 'reports', 'manifest_QA-HTTP.json');
  const manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8'));
  const renderedFile = manifest.entries[0].localFile;
  const originalBytes = fs.readFileSync(renderedFile);
  fs.appendFileSync(renderedFile, 'changed-after-approval');
  result = await api(`/api/project/${project.projectId}/deliver`, 'POST', { concept: 'A', record: false });
  assert.notEqual(result.status, 200, 'altered artwork must not be delivered on an old approval');
  fs.writeFileSync(renderedFile, originalBytes);
  result = await api(`/api/project/${project.projectId}/deliver`, 'POST', { concept: 'A', record: false });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(result.body.fileCount, 1);
  const zip = await fetch(base + result.body.zipUrl, { headers: { 'x-admin-token': token } });
  assert.equal(zip.status, 200);
  assert.equal(Buffer.from(await zip.arrayBuffer()).subarray(0, 2).toString(), 'PK');
  await api(`/api/project/${project.projectId}/approve-size`, 'POST', { ...approve, approved: false });
  doc = (await api('/api/campaign/QA-HTTP')).body;
  const placeholder = path.join(out, 'placeholder-landscape.png'); fs.copyFileSync(logo, placeholder);
  doc.campaign.concepts[0].backgroundImage = placeholder;
  assert.equal((await api('/api/campaign/QA-HTTP', 'PUT', doc)).status, 200);
  doc = (await api('/api/campaign/QA-HTTP')).body;
  result = await api(`/api/project/${project.projectId}/approve-size`, 'POST', { ...approve, revision: doc.revision });
  assert.equal(result.status, 422);
  assert.ok(result.body.findings.some((f: any) => f.check === 'placeholder-artwork'));
  manifest.entries[0].qaStatus = 'pass';
  fs.writeFileSync(manifestFile, JSON.stringify(manifest));
  result = await api(`/api/project/${project.projectId}/deliver`, 'POST', { concept: 'A', record: false });
  assert.notEqual(result.status, 200, 'a legacy green manifest does not make placeholders deliverable');
  assert.match(result.body.error, /Nothing deliverable/);
});
