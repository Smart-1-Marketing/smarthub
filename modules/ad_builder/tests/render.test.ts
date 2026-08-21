/**
 * Regression tests for the rendering pipeline.
 *
 * These exist because of specific bugs that shipped: widening the CTA buttons
 * silently pushed the 320x50 headline underneath one, and repositioning the
 * 970x250 trust line broke a campaign that had been passing. Both were
 * invisible to QA, which checks each box against its own rectangle and cannot
 * see two elements sharing space.
 *
 * Run with: npm test
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { getPlatform, loadPlatforms, loadTemplates } from '../src/registry';
import { fitText, wrap, countWords } from '../src/typeset';
import { resolveFont, measure, listFamilies, fontIsAvailable } from '../src/fonts';
import { renderPreview } from '../src/render';
import { contrastRatio, hexLuminance } from '../src/raster';
import { offerToken, ensureContrast, copyFromSubmission } from '../src/intake';
import { mapFont, reconcileColors, normalizeDomain, mapPayload } from '../src/brandfetch';
import { extractContent, heuristicAnalysis } from '../src/landing';
import { signUpload } from '../src/assets';
import { CTA_PADDING } from '../src/svg';
import { rateLimit, resetBuckets, checkAuth } from '../src/auth';
import type { Box, Campaign, SizeKey, SizeLayout } from '../src/types';

const ROOT = path.resolve(__dirname, '..');

/* ------------------------------------------------------------- templates */

test('every layout matches the canvas its size key promises', () => {
  for (const t of loadTemplates().values()) {
    for (const [size, layout] of Object.entries(t.sizes)) {
      if (!layout) continue;
      const [w, h] = size.split('x').map(Number);
      assert.equal(layout.canvas.w, w, `${t.id}/${size} width`);
      assert.equal(layout.canvas.h, h, `${t.id}/${size} height`);
    }
  }
});

test('no two elements overlap in any layout', () => {
  const roles = ['logo', 'headline', 'support', 'offer', 'trust', 'cta'] as const;
  const failures: string[] = [];

  for (const t of loadTemplates().values()) {
    for (const [size, layout] of Object.entries(t.sizes)) {
      if (!layout) continue;
      const boxes = roles
        .map((r) => [r, (layout as any)[r] as Box | undefined] as const)
        .filter((e): e is readonly [(typeof roles)[number], Box] => Boolean(e[1]));

      for (let i = 0; i < boxes.length; i++) {
        for (let j = i + 1; j < boxes.length; j++) {
          const [an, a] = boxes[i];
          const [bn, b] = boxes[j];
          const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
          const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
          if (ox > 1 && oy > 1) failures.push(`${t.id}/${size}: ${an} overlaps ${bn}`);
        }
      }
    }
  }
  assert.deepEqual(failures, []);
});

test('every element sits inside its safe area', () => {
  const roles = ['logo', 'headline', 'support', 'offer', 'trust', 'cta'] as const;
  const failures: string[] = [];

  for (const t of loadTemplates().values()) {
    for (const [size, layout] of Object.entries(t.sizes) as [string, SizeLayout][]) {
      if (!layout) continue;
      const region = layout.safeBox ?? {
        x: layout.safe, y: layout.safe,
        w: layout.canvas.w - layout.safe * 2,
        h: layout.canvas.h - layout.safe * 2,
      };
      for (const r of roles) {
        const b = (layout as any)[r] as Box | undefined;
        if (!b) continue;
        if (b.x < region.x - 0.5 || b.y < region.y - 0.5 ||
            b.x + b.w > region.x + region.w + 0.5 || b.y + b.h > region.y + region.h + 0.5) {
          failures.push(`${t.id}/${size}: ${r} breaches the safe area`);
        }
      }
    }
  }
  assert.deepEqual(failures, []);
});

test('the CTA vocabulary offered by the form fits every button', () => {
  // The form lets a customer pick "Check Availability". If a template cannot
  // hold the longest option at its minimum legible size, that is a bug in the
  // template, not in the customer's choice.
  const font = resolveFont('Open Sans', 'bold');
  const longest = ['Check Availability', 'Request Pricing', 'View Inventory', 'Schedule Now'];
  const failures: string[] = [];

  for (const t of loadTemplates().values()) {
    for (const [size, layout] of Object.entries(t.sizes)) {
      const cta = layout?.cta;
      if (!cta) continue;
      // Tight canvases legitimately use short forms; check those separately.
      if (size === '320x50' || size === '414x125') continue;
      for (const label of longest) {
        const w = measure(font, label.toUpperCase(), cta.size[0], cta.letterSpacing ?? 0);
        if (w > cta.w - CTA_PADDING) failures.push(`${t.id}/${size}: "${label}" needs ${w.toFixed(0)}px, button allows ${cta.w - CTA_PADDING}`);
      }
    }
  }
  assert.deepEqual(failures, []);
});

/* ---------------------------------------------------------- platform rules */

test('platform rules cover every size their templates render', () => {
  for (const p of loadPlatforms().values()) {
    for (const [size, rule] of Object.entries(p.sizes)) {
      assert.ok(rule!.maxFileBytes > 0, `${p.platform}/${size} needs a file limit`);
      assert.ok(rule!.formats.length > 0, `${p.platform}/${size} needs a format`);
      assert.ok(rule!.deliverScale >= 1, `${p.platform}/${size} scale`);
      const [w, h] = size.split('x').map(Number);
      assert.equal(rule!.w, w);
      assert.equal(rule!.h, h);
    }
  }
});

test('Amazon 2x sizes are declared at the scale Amazon requires', () => {
  const amazon = getPlatform('amazon');
  assert.equal(amazon.sizes['320x50']!.deliverScale, 2, '320x50 ships at 640x100');
  assert.equal(amazon.sizes['970x250']!.deliverScale, 2, '970x250 ships at 1940x500');
  assert.equal(amazon.sizes['414x125']!.deliverScale, 2, '414x125 ships at 828x250');
});

/* ----------------------------------------------------------------- typeset */

test('wrapping never breaks a word and respects the width', () => {
  const font = resolveFont('Montserrat', 'bold');
  const lines = wrap(font, 'Take Control of Your Electric Bill Today', 20, 140);
  assert.ok(lines.length > 1);
  for (const l of lines) assert.ok(measure(font, l, 20) <= 140 || !l.includes(' '));
  assert.equal(lines.join(' '), 'Take Control of Your Electric Bill Today');
});

test('autofit reports overflow rather than silently clipping', () => {
  const font = resolveFont('Montserrat', 'bold');
  const fit = fitText({
    font, text: 'A headline far too long to ever fit inside this tiny box',
    maxWidth: 60, maxHeight: 20, maxLines: 1, sizeRange: [10, 14],
  });
  assert.equal(fit.overflow, true);
  assert.ok(fit.lines.length >= 1, 'still returns something renderable');
});

test('word counting ignores empty fields', () => {
  assert.equal(countWords('two words', undefined, '', 'and more'), 4);
});

/* ------------------------------------------------------------------ fonts */

test('every registered font renders glyphs without throwing', () => {
  // DejaVu parsed fine and threw on getPath, which broke every compiled
  // render until it was caught. Any face in the registry must survive this.
  for (const family of listFamilies()) {
    const font = resolveFont(family, 'bold');
    assert.doesNotThrow(() => font.getPath('Handgloves 123 $%&', 0, 0, 24, { kerning: true }), family);
  }
});

test('an unknown family falls back to a face that works', () => {
  const font = resolveFont('No Such Font Anywhere');
  assert.doesNotThrow(() => font.getPath('fallback', 0, 0, 20));
  assert.equal(fontIsAvailable('No Such Font Anywhere'), false);
});

/* --------------------------------------------------------------- contrast */

test('contrast maths matches known WCAG pairs', () => {
  assert.equal(Math.round(contrastRatio(hexLuminance('#000000'), hexLuminance('#FFFFFF'))), 21);
  assert.equal(Math.round(contrastRatio(hexLuminance('#FFFFFF'), hexLuminance('#FFFFFF'))), 1);
});

test('accent correction fixes unreadable pairs and leaves good ones alone', () => {
  const bad = ensureContrast('#4FA3D1', '#1F5C8B');
  assert.equal(bad.adjusted, true);
  assert.ok(contrastRatio(hexLuminance(bad.hex), hexLuminance('#1F5C8B')) >= 4.5);

  const good = ensureContrast('#FFC400', '#123456');
  assert.equal(good.adjusted, false);
  assert.equal(good.hex, '#FFC400');
});

/* ------------------------------------------------------------------ intake */

test('offer tokens extract the figure an offer-led layout needs', () => {
  assert.equal(offerToken('$59 New Patient Exam').token, '$59');
  assert.equal(offerToken('Save 20% on all services').token, '20% Off');
  assert.equal(offerToken('$0 down financing').token, '$0 Down');
  assert.equal(offerToken('Two for one Tuesdays').extracted, false);
});

test('copy generation respects the tight canvases', () => {
  const copy = copyFromSubmission({
    business: 'Test Co', website: 'test.com', campaignName: 'C',
    promoting: 'A long description of the service being promoted here',
    benefit: 'Save Money On Your Monthly Energy Bill Today',
    cta: 'Check Availability', offer: '20% off',
  } as any);
  assert.ok((copy['320x50']!.headline ?? '').split(/\s+/).length <= 5, 'mobile headline stays short');
  assert.equal(copy['320x50']!.support, '', 'mobile banner drops the support line');
  assert.ok((copy['320x50']!.cta ?? '').length < 'Check Availability'.length, 'long CTA gets a short form');
});

/* -------------------------------------------------------------- brandfetch */

test('domains are normalised before lookup', () => {
  assert.equal(normalizeDomain('https://www.Example.com/path?x=1'), 'example.com');
  assert.equal(normalizeDomain('EXAMPLE.CO.UK'), 'example.co.uk');
});

test('unregistered brand fonts map to something renderable', () => {
  assert.equal(mapFont('Montserrat').exact, true);
  const gilroy = mapFont('Gilroy');
  assert.equal(gilroy.exact, false, 'not an exact match, so the caller must warn');
  if (gilroy.family) assert.ok(fontIsAvailable(gilroy.family));
});

test('a light/dark pair that cannot carry text is replaced', () => {
  const { colors, warnings } = reconcileColors([
    { hex: '#1F5C8B', type: 'brand' }, { hex: '#F7FAFC', type: 'light' }, { hex: '#EFEFEF', type: 'dark' },
  ]);
  assert.ok(contrastRatio(hexLuminance(colors.light), hexLuminance(colors.dark)) >= 7);
  assert.ok(warnings.length > 0, 'the substitution must be reported');
});

test('brandfetch mapping prefers vector and transparent logos', () => {
  const c = mapPayload('x.com', {
    name: 'X', logos: [
      { type: 'logo', formats: [{ src: 'a.jpeg', format: 'jpeg' }] },
      { type: 'logo', formats: [{ src: 'b.svg', format: 'svg' }] },
    ],
    colors: [{ hex: '#112233', type: 'brand' }], fonts: [],
  });
  assert.equal(c.logoOptions[0].format, 'svg');
});

/* ----------------------------------------------------------------- landing */

test('page extraction drops nav and footer noise', () => {
  const html = `<html><head><title>T</title></head><body>
    <nav><a>Home</a></nav><h1>Real Heading</h1>
    <p>${'A substantial paragraph of body copy that carries the offer. '.repeat(2)}</p>
    <footer><a>Westerville</a></footer></body></html>`;
  const page = extractContent(html);
  assert.equal(page.title, 'T');
  assert.ok(page.headings.includes('Real Heading'));
  assert.ok(!page.text.includes('Westerville'), 'footer links must not skew the analysis');
});

test('the heuristic reader labels itself as weaker', () => {
  const a = heuristicAnalysis('https://x.com', extractContent('<h1>Save 20% Today</h1>'));
  assert.equal(a.source, 'heuristic');
  assert.ok(a.warnings.length > 0);
});

/* ------------------------------------------------------------------- auth */

test('upload signatures match Cloudinary\'s documented algorithm', () => {
  const s = signUpload({
    folder: 'f', tags: ['a', 'b'], apiKey: 'k', apiSecret: 'SECRET', cloudName: 'c', timestamp: 1700000000,
  });
  const crypto = require('node:crypto');
  const expected = crypto.createHash('sha1')
    .update('folder=f&tags=a,b&timestamp=1700000000SECRET').digest('hex');
  assert.equal(s.signature, expected);
});

test('rate limiting refuses once the budget is spent', () => {
  resetBuckets();
  const req: any = { headers: {}, socket: { remoteAddress: '10.0.0.1' } };
  const route = 'POST /api/assets/upload-signature';
  let lastAllowed = true;
  for (let i = 0; i < 41; i++) lastAllowed = rateLimit(route, req).allowed;
  assert.equal(lastAllowed, false, 'the 41st request in the window is refused');

  // A different client keeps its own budget.
  const other: any = { headers: {}, socket: { remoteAddress: '10.0.0.2' } };
  assert.equal(rateLimit(route, other).allowed, true);
});

test('auth refuses when no token is configured', () => {
  const saved = process.env.ADMIN_TOKEN;
  delete process.env.ADMIN_TOKEN;
  const res = checkAuth({ headers: {} } as any, new URL('http://x/build'));
  assert.equal(res.ok, false);
  process.env.ADMIN_TOKEN = saved;
});

test('auth accepts a correct token and refuses a wrong one of equal length', () => {
  const saved = process.env.ADMIN_TOKEN;
  process.env.ADMIN_TOKEN = 'a-sufficiently-long-token-value';
  const good = checkAuth(
    { headers: { 'x-admin-token': 'a-sufficiently-long-token-value' } } as any,
    new URL('http://x/build'));
  const bad = checkAuth(
    { headers: { 'x-admin-token': 'b-sufficiently-long-token-value' } } as any,
    new URL('http://x/build'));
  assert.equal(good.ok, true);
  assert.equal(bad.ok, false);
  process.env.ADMIN_TOKEN = saved;
});

/* ---------------------------------------------------- end-to-end campaigns */

const CAMPAIGNS = [
  'src/examples/icon-solar.json',
  'campaigns/bella-vista-catering.json',
].filter((f) => fs.existsSync(path.join(ROOT, f)));

for (const file of CAMPAIGNS) {
  for (const platform of ['google', 'amazon']) {
    test(`${path.basename(file)} renders clean on ${platform}`, async () => {
      const campaign: Campaign = JSON.parse(fs.readFileSync(path.join(ROOT, file), 'utf8'));
      const cfg = getPlatform(platform);

      for (const concept of campaign.concepts) {
        const template = loadTemplates().get(concept.layoutFamily)!;
        const sizes = (Object.keys(template.sizes) as SizeKey[]).filter((s) => cfg.sizes[s]);
        assert.ok(sizes.length > 0, `${concept.conceptId} renders no sizes on ${platform}`);

        for (const size of sizes) {
          const out = await renderPreview({
            brand: campaign.brand, concept, platform, size, assetRoot: ROOT,
          });
          const failures = out.qa.filter((f) => f.status === 'fail');
          assert.deepEqual(
            failures.map((f) => `${f.check}: ${f.detail}`), [],
            `${concept.conceptId}/${size} on ${platform}`,
          );
          assert.ok(out.png.length > 500, 'produced a real image');
        }
      }
    });
  }
}
