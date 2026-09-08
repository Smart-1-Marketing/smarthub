// Execute the real form handler against download/gallery responses.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('modules/image_optimizer/static/app.js', 'utf8');

async function run({targetMet, gallery = false, filed = true, uploadOk = true}) {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {
      value: '', files: [{}], checked: false, style: {}, listeners: {},
      addEventListener(type, handler) { this.listeners[type] = handler; },
      getContext() { return {}; },
    });
    return nodes.get(id);
  };
  node('format').value = 'PNG';
  node('optClient').value = 'Test client';
  const downloads = [];
  const headers = new Map([['X-Target-Bytes', '10240'], ['X-Target-Met', targetMet]]);
  const responses = [{ok: true, headers: {get: key => headers.get(key) ?? null},
    blob: async () => ({size: 41639})},
    {ok: uploadOk, json: async () => ({gallery_filed: filed})}];
  const context = {
    document: {
      getElementById: node,
      querySelectorAll: () => [],
      querySelector: () => ({value: gallery ? 'gallery' : 'download'}),
      createElement: () => ({click() { downloads.push(this.download); }, remove() {}}),
      body: {appendChild() {}},
    },
    window: {addEventListener() {}},
    Image: class {}, FormData: class {append() {}},
    URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
    fetch: async () => responses.shift(),
  };
  vm.runInNewContext(source, context);
  await node('optimizer-form').listeners.submit({preventDefault() {}});
  assert.equal(node('submit').disabled, false);
  return {message: node('status').textContent, downloads};
}

(async () => {
  for (const gallery of [false, true]) {
    const missed = await run({targetMet: 'false', gallery});
    assert.match(missed.message, /10 KB target was not met/);
    assert.doesNotMatch(missed.message, /^Complete/);
    assert.equal(missed.downloads.length, gallery ? 0 : 1);
    for (const targetMet of ['true', null]) {
      const met = await run({targetMet, gallery});
      assert.match(met.message, /^Complete/);
      assert.doesNotMatch(met.message, /target was not met/);
    }
  }
  const unfiled = await run({targetMet: 'false', gallery: true, filed: false});
  assert.match(unfiled.message, /could not be filed/);
  assert.match(unfiled.message, /target was not met/);
  const fallback = await run({targetMet: 'false', gallery: true, uploadOk: false});
  assert.equal(fallback.downloads.length, 1);
  assert.match(fallback.message, /downloaded it instead/);
  assert.match(fallback.message, /target was not met/);
  console.log('8 image optimizer UI scenarios passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
