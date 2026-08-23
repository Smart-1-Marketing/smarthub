/**
 * Landing-page image extraction.
 *
 * The parsing rules here are the ones that decide whether a background
 * chooser offers the client's hero photo or a row of social icons, so each
 * one has a case: srcset picks the largest rather than the first, a width
 * attribute never stands in for the real file, SVG is skipped because on a
 * marketing page it is the logo, and a page that cannot be read says so
 * instead of returning an empty list that reads as "nothing good here".
 *
 * Run with: npm test
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import sharp from 'sharp';
import { extractImageUrls, landingImages } from '../src/landing-images';

/* A real encoded image, so the measuring path is exercised rather than mocked. */
async function png(width: number, height: number): Promise<Buffer> {
  return sharp({
    create: { width, height, channels: 3, background: { r: 120, g: 140, b: 160 } },
  }).png().toBuffer();
}

/** A fetch that serves one page and a set of images, and counts the calls. */
function fakeFetch(html: string, images: Record<string, Buffer>) {
  const calls: string[] = [];
  const impl = async (input: any, _init?: any) => {
    const url = String(input);
    calls.push(url);
    if (images[url]) {
      return new Response(images[url], {
        status: 200,
        headers: { 'content-type': 'image/png' },
      }) as any;
    }
    if (url === 'https://example.test/lp') {
      return new Response(html, { status: 200 }) as any;
    }
    return new Response('not found', { status: 404 }) as any;
  };
  return { impl: impl as unknown as typeof fetch, calls };
}

test('srcset picks the largest candidate, not the first', () => {
  const html = `<img srcset="/s.jpg 320w, /m.jpg 800w, /l.jpg 1600w" src="/s.jpg" alt="Crew">`;
  const found = extractImageUrls(html, 'https://example.test/lp');
  const urls = found.map((f) => f.url);
  assert.ok(urls.includes('https://example.test/l.jpg'), 'largest srcset entry taken');
});

test('density descriptors are handled too', () => {
  const html = `<img srcset="/a.jpg, /b.jpg 2x" alt="">`;
  const urls = extractImageUrls(html, 'https://example.test/lp').map((f) => f.url);
  assert.ok(urls.includes('https://example.test/b.jpg'));
});

test('relative, protocol and data URLs are resolved or dropped', () => {
  const html = `
    <img src="photos/van.jpg">
    <img src="data:image/gif;base64,R0lGOD">
    <img src="https://cdn.example.net/hero.jpg">
    <img src="/logo.svg">`;
  const urls = extractImageUrls(html, 'https://example.test/a/lp').map((f) => f.url);
  assert.ok(urls.includes('https://example.test/a/photos/van.jpg'), 'relative resolved');
  assert.ok(urls.includes('https://cdn.example.net/hero.jpg'), 'absolute kept');
  assert.ok(!urls.some((u) => u.startsWith('data:')), 'data URI dropped');
  assert.ok(!urls.some((u) => u.endsWith('.svg')), 'svg dropped — it is the logo');
});

test('og:image and CSS backgrounds are found', () => {
  const html = `
    <meta property="og:image" content="https://example.test/og.jpg">
    <div style="background-image:url('/hero.jpg')"></div>`;
  const found = extractImageUrls(html, 'https://example.test/lp');
  assert.equal(found.find((f) => f.url.endsWith('og.jpg'))?.origin, 'og');
  assert.equal(found.find((f) => f.url.endsWith('hero.jpg'))?.origin, 'css');
});

test('the same file listed twice is offered once', () => {
  const html = `<img src="/x.jpg"><img src="/x.jpg#frag"><div style="background:url(/x.jpg)"></div>`;
  const urls = extractImageUrls(html, 'https://example.test/lp').map((f) => f.url);
  assert.equal(urls.filter((u) => u.includes('x.jpg')).length, 1);
});

test('the file is measured, not the width attribute', async () => {
  // The markup lies in both directions: a big photo displayed small, and an
  // icon stretched wide. Trusting the attribute would invert both.
  const html = `
    <img src="https://example.test/big.png" width="120" alt="Premises">
    <img src="https://example.test/icon.png" width="900" alt="Badge">`;
  const { impl } = fakeFetch(html, {
    'https://example.test/big.png': await png(1400, 900),
    'https://example.test/icon.png': await png(64, 64),
  });
  const res = await landingImages('https://example.test/lp', { fetchImpl: impl });
  const urls = res.images.map((i) => i.url);
  assert.ok(urls.includes('https://example.test/big.png'), 'large file kept despite width=120');
  assert.ok(!urls.includes('https://example.test/icon.png'), 'icon rejected despite width=900');
  assert.equal(res.images[0].width, 1400);
});

test('anything under the threshold is left out, and the reason is said', async () => {
  const html = `<img src="https://example.test/small.png">`;
  const { impl } = fakeFetch(html, { 'https://example.test/small.png': await png(200, 150) });
  const res = await landingImages('https://example.test/lp', { fetchImpl: impl });
  assert.equal(res.images.length, 0);
  assert.equal(res.examined, 1);
  assert.match(res.warnings.join(' '), /under 300px wide/);
});

test('og:image outranks a larger ordinary image', async () => {
  const html = `
    <meta property="og:image" content="https://example.test/og.png">
    <img src="https://example.test/other.png">`;
  const { impl } = fakeFetch(html, {
    'https://example.test/og.png': await png(800, 600),
    'https://example.test/other.png': await png(2000, 1500),
  });
  const res = await landingImages('https://example.test/lp', { fetchImpl: impl });
  assert.equal(res.images[0].url, 'https://example.test/og.png',
    'the page\'s own chosen picture leads');
  assert.equal(res.images[1].url, 'https://example.test/other.png');
});

test('one unreadable image does not lose the others', async () => {
  const html = `
    <img src="https://example.test/gone.png">
    <img src="https://example.test/good.png">`;
  const { impl } = fakeFetch(html, { 'https://example.test/good.png': await png(900, 600) });
  const res = await landingImages('https://example.test/lp', { fetchImpl: impl });
  assert.equal(res.images.length, 1);
  assert.equal(res.images[0].url, 'https://example.test/good.png');
});

test('a page that cannot be read explains itself rather than returning nothing', async () => {
  const impl = (async () => new Response('nope', { status: 503 })) as unknown as typeof fetch;
  const res = await landingImages('https://example.test/lp', { fetchImpl: impl });
  assert.equal(res.images.length, 0);
  assert.match(res.warnings.join(' '), /Could not read/);
});

test('a page with no images says that, rather than failing', async () => {
  const { impl } = fakeFetch('<p>All text, no pictures.</p>', {});
  const res = await landingImages('https://example.test/lp', { fetchImpl: impl });
  assert.equal(res.images.length, 0);
  assert.match(res.warnings.join(' '), /no images/);
});

test('the candidate list is capped, and the cap is stated', async () => {
  const many = Array.from({ length: 30 }, (_, i) => `<img src="/p${i}.png">`).join('');
  const { impl, calls } = fakeFetch(many, {});
  const res = await landingImages('https://example.test/lp', { fetchImpl: impl, maxCandidates: 5 });
  // One page fetch plus five image fetches — the other 25 are never requested.
  assert.equal(calls.length, 6);
  assert.match(res.warnings.join(' '), /first 5 of 30/);
});

test('a srcset and its src fallback are one picture, not two', () => {
  // Taking both offered the phone-sized and the desktop-sized copy of the same
  // photo as separate choices, which is clutter in a chooser.
  const html = `<img srcset="/crew-small.png 320w, /crew-large.png 1600w"
                     src="/crew-small.png" alt="Our crew">`;
  const urls = extractImageUrls(html, 'https://example.test/lp').map((f) => f.url);
  assert.ok(urls.includes('https://example.test/crew-large.png'), 'largest kept');
  assert.ok(!urls.includes('https://example.test/crew-small.png'), 'fallback not offered too');
});

test('a lazy-loaded image still resolves through data-src', () => {
  // Here `src` really is a placeholder and data-src holds the file, so the
  // srcset rule must not suppress it.
  const html = `<img src="/placeholder.png" data-src="/real-photo.png" alt="Van">`;
  const urls = extractImageUrls(html, 'https://example.test/lp').map((f) => f.url);
  assert.ok(urls.includes('https://example.test/real-photo.png'));
});

test('a picture the asset guard would refuse is never offered', () => {
  // Applying fetches through assetUrlIsSafe (https only, no internal hosts).
  // Listing something it will refuse gives a thumbnail that fails on click,
  // for a reason the person cannot see.
  const html = `
    <img src="http://plain.example.com/insecure.jpg">
    <img src="https://cdn.example.com/fine.jpg">
    <img src="https://localhost/internal.jpg">`;
  const urls = extractImageUrls(html, 'https://example.test/lp').map((f) => f.url);
  assert.deepEqual(urls, ['https://cdn.example.com/fine.jpg']);
});
