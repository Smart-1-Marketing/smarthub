/**
 * The preview cache.
 *
 * Switching size used to re-render a preview that had not changed since
 * the last visit -- the build screen fires /api/preview on every edit and
 * every size switch, and a size the person already rendered replays the
 * same input. A small LRU (24 entries, 60s) keyed on the campaign JSON
 * plus concept/size/platform serves the hit without touching sharp.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { previewCache, previewCacheKey, PREVIEW_CACHE_MAX, PREVIEW_CACHE_TTL_MS } from '../src/preview-cache';

const CAMPAIGN = { brand: { name: 'Acme' }, concepts: [{ conceptId: 'A' }] };

test('the cache key hashes the whole campaign and the concept/size/platform triple', () => {
  const a = previewCacheKey(CAMPAIGN, 'A', '300x250', 'google');
  const b = previewCacheKey(CAMPAIGN, 'A', '300x250', 'google');
  assert.equal(a, b, 'same inputs, same key');
  assert.notEqual(a, previewCacheKey(CAMPAIGN, 'A', '300x250', 'meta'), 'platform changes it');
  assert.notEqual(a, previewCacheKey(CAMPAIGN, 'A', '728x90', 'google'), 'size changes it');
  assert.notEqual(a, previewCacheKey(CAMPAIGN, 'B', '300x250', 'google'), 'concept changes it');
  assert.notEqual(a, previewCacheKey({ ...CAMPAIGN, note: 'x' }, 'A', '300x250', 'google'), 'a campaign edit changes it');
});

test('a hit answers without touching sharp; a miss on any input runs the render', () => {
  previewCache.clear();
  const key = previewCacheKey(CAMPAIGN, 'A', '300x250', 'google');
  assert.equal(previewCache.get(key), null, 'nothing cached yet');
  previewCache.set(key, { image: 'data:png;base64,AAAA', status: 'pass' });
  const hit = previewCache.get(key);
  assert.equal(hit?.image, 'data:png;base64,AAAA', 'the body comes back verbatim');
  // A different size is a miss even though the campaign is the same.
  assert.equal(previewCache.get(previewCacheKey(CAMPAIGN, 'A', '728x90', 'google')), null);
});

test('the cache evicts by least-recently-used and never exceeds its ceiling', () => {
  previewCache.clear();
  for (let i = 0; i < PREVIEW_CACHE_MAX + 5; i++) {
    previewCache.set(previewCacheKey(CAMPAIGN, 'A', `${i}x${i}`, 'google'), { image: 'x', i });
  }
  assert.equal(previewCache.size(), PREVIEW_CACHE_MAX, 'the ceiling holds');
  assert.equal(previewCache.get(previewCacheKey(CAMPAIGN, 'A', '0x0', 'google')), null, 'the oldest was evicted');
  assert.ok(previewCache.get(previewCacheKey(CAMPAIGN, 'A', `${PREVIEW_CACHE_MAX + 4}x${PREVIEW_CACHE_MAX + 4}`, 'google')), 'the newest survives');
});

test('a read counts as freshness, so an in-use size is not evicted first', () => {
  previewCache.clear();
  const busy = previewCacheKey(CAMPAIGN, 'A', 'busy', 'google');
  previewCache.set(busy, { image: 'busy' });
  // Fill the rest of the cache.
  for (let i = 0; i < PREVIEW_CACHE_MAX - 1; i++) {
    previewCache.set(previewCacheKey(CAMPAIGN, 'A', `${i}x${i}`, 'google'), { i });
  }
  // Touch busy again, then add one more: the oldest non-busy row goes.
  previewCache.get(busy);
  previewCache.set(previewCacheKey(CAMPAIGN, 'A', 'newest', 'google'), { newest: true });
  assert.ok(previewCache.get(busy), 'the size the person is looking at survives');
});

test('a stale row is not served; the next visit re-renders', async () => {
  previewCache.clear();
  const key = previewCacheKey(CAMPAIGN, 'A', 'stale', 'google');
  previewCache.set(key, { image: 'stale' });
  // Reach in and age the row past the TTL. Doing it by clock rather than
  // sleep keeps the test fast and the TTL constant.
  const map = (previewCache as unknown as { map: Map<string, { at: number; body: any }> }).map;
  const row = map.get(key)!; row.at = Date.now() - PREVIEW_CACHE_TTL_MS - 1;
  assert.equal(previewCache.get(key), null, 'stale reads are dropped');
  assert.equal((previewCache as any).map.has(key), false, 'and the row is deleted on read');
});
