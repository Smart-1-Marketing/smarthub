import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { campaignRevision, artworkFingerprint, saveCampaignDocument, type CampaignDocument } from '../src/campaign-state';
import { placeholderFindings } from '../src/asset-quality';
import type { Project } from '../src/projects';

function fixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ad-state-'));
  fs.writeFileSync(path.join(root, 'logo.png'), 'original-logo');
  const doc: CampaignDocument = { platforms: ['google'], campaign: {
    requestId: 'QA-STATE', campaignName: 'Test',
    brand: { name: 'Test', domain: 'example.test', colors: { primary: '#123456', secondary: '#654321', accent: '#FFFFFF', light: '#FFFFFF', dark: '#000000' },
      fonts: { headline: 'Montserrat', body: 'Open Sans' }, logos: { primary: 'logo.png' } },
    concepts: [{ conceptId: 'A', name: 'Test', layoutFamily: 'T07', hero: {}, copy: { default: { headline: 'Visit us today', cta: 'Learn more' } } }],
  } };
  const file = path.join(root, 'campaign.json');
  fs.writeFileSync(file, JSON.stringify(doc));
  const key = { conceptId: 'A', platform: 'google', size: '300x250' };
  const project = { approvals: [{ ...key, at: 'now', inputHash: artworkFingerprint(doc, key, root) }] } as Project;
  return { root, doc, file, key, project, clean: () => fs.rmSync(root, { recursive: true, force: true }) };
}

test('stale save is rejected and the winning document is preserved', () => {
  const f = fixture();
  try {
    const version = campaignRevision(f.doc);
    const next = structuredClone(f.doc); next.campaign.campaignName = 'First editor';
    const revision = saveCampaignDocument(f.file, next, version, undefined, f.root);
    assert.notEqual(revision, version);
    assert.throws(() => saveCampaignDocument(f.file, f.doc, version, undefined, f.root), /another session/);
    assert.equal(JSON.parse(fs.readFileSync(f.file, 'utf8')).campaign.campaignName, 'First editor');
  } finally { f.clean(); }
});

test('approved artwork blocks shared changes but permits another size copy edit', () => {
  const f = fixture();
  try {
    const next = structuredClone(f.doc);
    next.campaign.concepts[0].copy['728x90'] = { headline: 'A different headline' };
    const revision = saveCampaignDocument(f.file, next, campaignRevision(f.doc), f.project, f.root);
    next.campaign.brand.colors.primary = '#654321';
    assert.throws(() => saveCampaignDocument(f.file, next, revision, f.project, f.root), /Unapprove/);
  } finally { f.clean(); }
});

test('legacy approvals remain locked, and removing a signed placement is refused', () => {
  const f = fixture();
  try {
    delete f.project.approvals![0].inputHash;
    const next = structuredClone(f.doc); next.campaign.concepts = [];
    assert.throws(() => saveCampaignDocument(f.file, next, campaignRevision(f.doc), f.project, f.root), /placement/);
  } finally { f.clean(); }
});

test('asset bytes changing in place invalidate a stored artwork fingerprint', () => {
  const f = fixture();
  try {
    fs.writeFileSync(path.join(f.root, 'logo.png'), 'different-logo');
    assert.throws(() => saveCampaignDocument(f.file, f.doc, campaignRevision(f.doc), f.project, f.root), /Unapprove/);
  } finally { f.clean(); }
});

test('only a placeholder drawn by the chosen layout blocks the creative', () => {
  const f = fixture();
  try {
    const c = f.doc.campaign.concepts[0];
    c.hero.landscape = '/cache/placeholder/placeholder-landscape.jpg';
    assert.deepEqual(placeholderFindings(f.doc.campaign.brand, c, '300x250'), []);
    c.backgroundImage = c.hero.landscape;
    assert.equal(placeholderFindings(f.doc.campaign.brand, c, '300x250')[0].status, 'fail');
  } finally { f.clean(); }
});
