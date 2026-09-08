// Exercise the real page script with controlled out-of-order HTTP responses.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('modules/video_backgrounds/templates/video_backgrounds.html', 'utf8');
const script = source.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
class Element {
  constructor() { this.textContent = ''; this.value = ''; this.style = {}; this.listeners = {}; this.children = []; }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  appendChild(child) { this.children.push(child); }
  get innerHTML() { return this.html ?? this.textContent.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  set innerHTML(value) { this.html = value; }
  querySelector() { return {play: () => Promise.resolve(), pause() {}, addEventListener() {}}; }
  querySelectorAll() { return []; }
}
const nodes = new Map();
const document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element()); return nodes.get(id); },
  createElement() { return new Element(); },
  querySelectorAll() { return []; },
};
document.getElementById('vb-boot').textContent = '{}';
const pending = [];
vm.runInNewContext(script, {document, URLSearchParams, navigator: {}, setTimeout,
  fetch: () => new Promise(resolve => pending.push(resolve))});
const settle = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  document.getElementById('vb-go').listeners.click();
  pending[1]({ok: true, json: async () => ({ok:true, results:[], note:'Latest search'})});
  await settle();
  pending[0]({ok: true, json: async () => ({ok:true, results:[], note:'Old search'})});
  await settle();
  assert.equal(document.getElementById('vb-msg').textContent, 'Latest search');
  for (const id of ['vb-orientation','vb-duration','vb-bgready']) {
    assert.equal(typeof document.getElementById(id).listeners.change, 'function');
  }
  document.getElementById('vb-go').listeners.click();
  pending[2]({ok:true, json:async () => ({ok:true,total:1,results:[{
    public_id:'portrait', thumbnail:'https://example.test/" onerror="bad', preview_url:'https://example.test/video.mp4'
  }]})});
  await settle();
  const card = document.getElementById('vb-results').children[0];
  assert.match(card.innerHTML, /<video controls/);
  assert.match(card.innerHTML, /&quot; onerror=&quot;bad/);
  document.getElementById('vb-go').listeners.click();
  pending[3]({ok:false,status:503});
  await settle();
  assert.match(document.getElementById('vb-msg').textContent, /503/);
  document.getElementById('vb-pending').listeners.click();
  pending[4]({json: async () => ({cutoff:'',pending:[{}]})});
  await settle();
  assert.match(document.getElementById('vb-index-out').textContent, /1 clip\(s\) waiting/);
  console.log('Video Search: stale responses, filter changes, playback controls, attribute escaping, HTTP errors and backlog message passed.');
})().catch(error => {console.error(error); process.exitCode=1;});
