const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const template = fs.readFileSync(path.join(__dirname, 'hub/templates/client360.html'), 'utf8');
const start = template.indexOf('  function renderProposals(){');
const end = template.indexOf("  fetch('/sales/proposals/api/proposals?q='", start);
assert(start >= 0 && end > start);
const uploaded = [
  {id: 'link', kind: 'link', url: '/sales/builder/p/abc123token', date_sent: '2026-09-08'},
  {id: 'proposal & 1', kind: 'pdf', member: 'Member & Company', url: '/file.pdf'},
  {id: 'local', kind: 'docx', url: '/file.docx'},
  {filename: 'legacy proposal.pdf', kind: 'pdf', url: '/legacy.pdf'},
];
const rows = uploaded.map(p => ({
  dataset: {pid: p.id, pclient: p.member},
  controls: {'.up-date': {}, '.up-del': {}},
  querySelector(selector) { return this.controls[selector] || null; },
}));
const host = {innerHTML: '', querySelectorAll: () => rows};
const context = {
  $: () => host, QUOTES: [], BUILT: [], UPLOADED: uploaded,
  name: 'Parent Client', SOURCE_LABEL: {},
  propDate: p => p.date_sent || '', memberTag: () => '',
  esc: s => String(s ?? '').replaceAll('&', '&amp;').replaceAll('"', '&quot;'),
};
vm.runInNewContext(template.slice(start, end) + '\nrenderProposals();', context);
// A live Hub link precedes documents: it must not interrupt event binding.
for (const row of rows) {
  assert.equal(typeof row.controls['.up-date'].onchange, 'function');
  assert.equal(typeof row.controls['.up-del'].onclick, 'function');
}
const links = [...host.innerHTML.matchAll(/class="gbtn to-io" href="([^"]+)" target="_blank" rel="noopener"/g)];
assert.equal(links.length, 3, 'Only document proposals offer conversion');
const destinations = links.map(m => new URL(m[1].replaceAll('&amp;', '&'), 'https://example.test'));
for (const url of destinations) assert.equal(url.pathname, '/tools/io/');
assert.equal(destinations[0].searchParams.get('from_proposal'), 'proposal & 1');
assert.equal(destinations[0].searchParams.get('client'), 'Member & Company');
assert.equal(destinations[1].searchParams.get('client'), 'Parent Client');
assert.equal(destinations[2].searchParams.get('from_proposal'), 'legacy proposal.pdf');
const documents = [...host.innerHTML.matchAll(/href="(\/api\/client\/proposals\/document\/[^\"]+)"/g)]
  .map(m => new URL(m[1].replaceAll('&amp;', '&'), 'https://example.test'));
assert.equal(documents.length, 3, 'Document links use the named-file endpoint');
assert.equal(documents[0].searchParams.get('client'), 'Member & Company');
assert.equal(documents[1].searchParams.get('client'), 'Parent Client');
assert.equal(decodeURIComponent(documents[2].pathname.split('/').pop()), 'legacy proposal.pdf');
assert(host.innerHTML.includes('href="/sales/builder/p/abc123token"'), 'Live-page proposals keep their original link');
console.log('Client 360 proposal links: mixed rows, native navigation, member ownership, and filename fallback passed.');
