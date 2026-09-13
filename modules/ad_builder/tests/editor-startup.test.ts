import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vm from 'node:vm';
import { withBase } from '../src/basepath';

function editorHarness(expose = false) {
  const html = fs.readFileSync(path.join(__dirname, '../public/build.html'), 'utf8');
  let code = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
  if (expose) { const end = code.lastIndexOf('})();'); code = code.slice(0, end) + 'window.testEditor = {state, preview, schedule, saveCampaign};\n' + code.slice(end); }
  const events = new Map<string, Set<string>>();
  const nodes = new Map<string, any>();
  function node(id: string): any {
    if (!nodes.has(id)) nodes.set(id, { style: {}, dataset: {}, classList: { add() {}, remove() {}, toggle() {} },
      querySelectorAll: () => [], querySelector: () => node('child'),
      addEventListener: (event: string) => { if (!events.has(id)) events.set(id, new Set()); events.get(id)!.add(event); },
      setAttribute() {}, removeAttribute(name: string) { delete nodes.get(id)[name]; }, getAttribute() { return ''; }, appendChild() {}, remove() {} });
    return nodes.get(id);
  }
  const document = { getElementById: node, querySelectorAll: () => [], addEventListener() {}, body: node('body') };
  const requests: { url: string; options?: any; resolve: (result: any) => void }[] = [];
  const context = { document, window: { addEventListener() {} } as any, location: { search: '', pathname: '/build' },
    fetch: (url: string, options?: any) => new Promise(resolve => { requests.push({ url, options, resolve }); }), setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1,
    URLSearchParams, console, navigator: {}, MutationObserver: class { observe() {} } };
  vm.runInNewContext(code, context, { timeout: 2000 });
  return { events, nodes, requests, editor: context.window.testEditor };
}

test('the complete editor script starts and registers animation and workflow handlers', () => {
  const { events } = editorHarness();
  for (const id of ['animate', 'renderAll', 'save', 'deliverStage']) assert.ok(events.get(id)?.has('click'), `${id} is wired`);
});

test('an old preview cannot restore artwork or approval readiness during debounce', async () => {
  const { editor, nodes, requests } = editorHarness(true);
  editor.state.doc = { campaign: { concepts: [{ conceptId: 'A', copy: {} }] }, notes: [] };
  editor.state.conceptId = 'A';
  editor.state.saved = true;
  editor.preview();
  const pending = requests.find(r => r.url === '/api/preview')!;
  assert.ok(pending);
  editor.schedule();
  pending.resolve({ ok: true, json: async () => ({ image: 'stale-image', width: 300, height: 250, status: 'pass', qa: [] }) });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(editor.state.previewReady, false);
  assert.notEqual(nodes.get('preview').src, 'stale-image');
});

test('mounted core navigation is correct before browser JavaScript runs', () => {
  const req = { headers: { 'x-forwarded-prefix': '/tools/display-ads' } } as any;
  const html = withBase(req, '<html><head></head><body><a href="/diagnostics">Diagnostics</a><a href="/projects?q=test">Projects</a></body></html>');
  assert.match(html, /href="\/tools\/display-ads\/diagnostics"/);
  assert.match(html, /href="\/tools\/display-ads\/projects\?q=test"/);
  assert.doesNotMatch(html, /href="\/diagnostics"/);
});

test('overlapping saves serialize and preserve edits made while the first request is pending', async () => {
  const {editor,requests,nodes}=editorHarness(true);
  editor.state.requestId='save-test';editor.state.doc={revision:'old',campaign:{concepts:[]},platforms:['google']};editor.state.dirty=true;
  const first=editor.saveCampaign();
  editor.state.doc.campaign.campaignName='Newer edit';
  const second=editor.saveCampaign();
  const writes=()=>requests.filter(r=>r.url==='/api/campaign/save-test');
  assert.equal(writes().length,1);
  writes()[0].resolve({ok:true,json:async()=>({revision:'one'})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(writes().length,2);
  const newer=JSON.parse(writes()[1].options.body);
  assert.equal(newer.revision,'one');assert.equal(newer.campaign.campaignName,'Newer edit');
  writes()[1].resolve({ok:true,json:async()=>({revision:'two'})});
  await Promise.all([first,second]);
  assert.equal(editor.state.dirty,false);assert.equal(editor.state.doc.revision,'two');
  assert.equal(nodes.get('saveHint').textContent,'Saved');
});

test('a failed autosave keeps edits and a manual retry can finish saving', async () => {
  const {editor,requests,nodes}=editorHarness(true);
  editor.state.requestId='retry-test';editor.state.doc={revision:'old',campaign:{concepts:[]},platforms:['google']};editor.state.dirty=true;
  const first=editor.saveCampaign();
  requests.find(r=>r.url==='/api/campaign/retry-test')!.resolve({ok:false,status:503,json:async()=>({error:'Temporarily unavailable'})});
  await assert.rejects(first,/Temporarily unavailable/);
  assert.equal(editor.state.dirty,true);assert.match(nodes.get('saveHint').textContent,/Save now to retry/);
  const retry=editor.saveCampaign();
  requests.filter(r=>r.url==='/api/campaign/retry-test')[1].resolve({ok:true,json:async()=>({revision:'saved'})});
  await retry;assert.equal(editor.state.dirty,false);assert.equal(nodes.get('saveHint').textContent,'Saved');
});
