import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as vm from 'node:vm';
import { withBase } from '../src/basepath';

function editorHarness(expose = false) {
  const html = fs.readFileSync(path.join(__dirname, '../public/build.html'), 'utf8');
  let code = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join('\n');
  if (expose) { const end = code.lastIndexOf('})();'); code = code.slice(0, end) + 'window.testEditor = {state, preview, schedule};\n' + code.slice(end); }
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
  const requests: { url: string; resolve: (result: any) => void }[] = [];
  const context = { document, window: { addEventListener() {} } as any, location: { search: '', pathname: '/build' },
    fetch: (url: string) => new Promise(resolve => { requests.push({ url, resolve }); }), setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1,
    URLSearchParams, console, navigator: {} };
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
