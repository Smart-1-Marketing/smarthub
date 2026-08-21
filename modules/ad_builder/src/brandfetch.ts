/**
 * Brandfetch discovery.
 *
 * Brandfetch is a shortcut, not the final authority. Everything it returns is
 * shown to the customer for confirmation before it reaches a creative, and the
 * mapping below is deliberately conservative: where the API is ambiguous or the
 * renderer cannot honour a value, we degrade to something safe and say so in
 * `warnings` rather than silently substituting.
 *
 * Two mismatches need real handling:
 *
 *   Fonts. Brandfetch returns font *names*. The renderer converts glyphs to
 *   paths from files it actually has, so an unregistered family cannot be used
 *   at all. We map to the nearest registered family and flag it — never render
 *   in a substitute face without saying so.
 *
 *   Colours. The API returns a loose palette with type hints. Templates need
 *   five specific roles, two of which (light/dark) must contrast for QA to
 *   pass. We fill gaps deterministically instead of leaving holes.
 */

import * as fs from 'fs';
import * as path from 'path';
import type { Brand } from './types';
import { fontIsAvailable, listFamilies } from './fonts';
import { contrastRatio, hexLuminance } from './raster';

const API_BASE = 'https://api.brandfetch.io/v2/brands';

export interface BrandCandidate {
  brand: Brand;
  /** Brandfetch's own quality score, 0-1, when supplied. */
  quality?: number;
  /** Logo choices offered to the customer, best first. */
  logoOptions: { url: string; kind: string; format: string; background?: string }[];
  /** Imagery Brandfetch found, usable as hero candidates. */
  imageOptions: { url: string; kind: string }[];
  description?: string;
  warnings: string[];
  /** True when confidence is low enough that a human should look. */
  needsReview: boolean;
  source: 'brandfetch' | 'mock' | 'fallback';
}

export function normalizeDomain(input: string): string {
  return String(input || '')
    .trim()
    .replace(/^https?:\/\//i, '')
    .replace(/^www\./i, '')
    .replace(/\/.*$/, '')
    .toLowerCase();
}

const HEX = /^#?[0-9a-fA-F]{6}$/;
const hex = (v: unknown): string | undefined => {
  const s = String(v ?? '').trim();
  if (!HEX.test(s)) return undefined;
  return s.startsWith('#') ? s.toUpperCase() : `#${s.toUpperCase()}`;
};

/**
 * Pick a registered family for a name Brandfetch reported. Returns null when
 * there is no defensible match, so the caller can warn instead of guessing.
 */
export function mapFont(name: string | undefined): { family: string | null; exact: boolean } {
  if (!name) return { family: null, exact: false };
  const want = name.trim();
  if (fontIsAvailable(want)) return { family: want, exact: true };

  // Nearest registered family by broad classification. This is a starting
  // point for the proof, not a licensing decision — a client whose brand font
  // matters should have that font vendored into the registry.
  const lower = want.toLowerCase();
  const geometric = /poppins|futura|century gothic|avenir|nunito|circular|gilroy|montserrat/;
  const grotesque = /helvetica|arial|inter|roboto|open sans|source sans|lato|work sans|neue/;
  const available = listFamilies();

  if (geometric.test(lower) && available.includes('Poppins')) return { family: 'Poppins', exact: false };
  if (grotesque.test(lower) && available.includes('Open Sans')) return { family: 'Open Sans', exact: false };
  return { family: null, exact: false };
}

/** Ensure the five template colour roles exist and that light/dark contrast. */
export function reconcileColors(
  found: { hex: string; type?: string; brightness?: number }[],
): { colors: Brand['colors']; warnings: string[] } {
  const warnings: string[] = [];
  const byType = (t: string) => found.find((c) => c.type === t)?.hex;
  const clean = found.map((c) => c.hex).filter(Boolean);

  const primary = byType('brand') ?? byType('primary') ?? clean[0] ?? '#123456';
  const accent = byType('accent') ?? clean.find((c) => c !== primary) ?? '#FFC400';
  const secondary = clean.find((c) => c !== primary && c !== accent) ?? primary;

  let light = byType('light') ?? '#FFFFFF';
  let dark = byType('dark') ?? '#111111';

  // Brandfetch sometimes reports a near-white as "dark" or vice versa. If they
  // do not contrast, the templates cannot produce readable text at all.
  if (contrastRatio(hexLuminance(light), hexLuminance(dark)) < 7) {
    warnings.push(
      'Reported light and dark colours did not contrast enough for readable text; using white and near-black instead.',
    );
    light = '#FFFFFF';
    dark = '#111111';
  }

  if (!byType('brand') && !byType('primary')) {
    warnings.push('No primary brand colour was identified; the first palette colour was used.');
  }

  return { colors: { primary, secondary, accent, light, dark }, warnings };
}

interface FetchLike {
  (url: string, init?: any): Promise<{ ok: boolean; status: number; json(): Promise<any> }>;
}

export interface DiscoverOptions {
  apiKey?: string;
  /** Return a fixture instead of calling the API. */
  mock?: boolean;
  mockFile?: string;
  fetchImpl?: FetchLike;
  timeoutMs?: number;
}

export async function discoverBrand(
  domainInput: string,
  opts: DiscoverOptions = {},
): Promise<BrandCandidate> {
  const domain = normalizeDomain(domainInput);
  if (!domain || !domain.includes('.')) {
    throw new Error(`"${domainInput}" is not a usable domain`);
  }

  const apiKey = opts.apiKey ?? process.env.BRANDFETCH_API_KEY;
  const useMock = opts.mock ?? (!apiKey && process.env.BRANDFETCH_MOCK === '1');

  let payload: any;
  let source: BrandCandidate['source'] = 'brandfetch';

  if (useMock) {
    const file =
      opts.mockFile ?? path.resolve(__dirname, 'examples', 'brandfetch-sample.json');
    payload = JSON.parse(fs.readFileSync(file, 'utf8'));
    // Sandbox test hook: the fixture's logo URLs point at real CDNs that a
    // sealed test environment cannot reach, which silently forces every mock
    // submission down the "no logo" path. This rewrites them to a local
    // stand-in so the render path itself is testable end to end.
    const localLogo = process.env.BRANDFETCH_MOCK_LOGO;
    if (localLogo) {
      for (const l of payload.logos ?? []) {
        for (const f of l.formats ?? []) f.src = localLogo;
      }
    }
    source = 'mock';
  } else {
    if (!apiKey) {
      throw new Error(
        'BRANDFETCH_API_KEY is not set. Set it, or set BRANDFETCH_MOCK=1 to work from the fixture.',
      );
    }
    const doFetch = (opts.fetchImpl ?? (globalThis.fetch as unknown as FetchLike));
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 8000);
    try {
      const res = await doFetch(`${API_BASE}/${encodeURIComponent(domain)}`, {
        headers: { Authorization: `Bearer ${apiKey}` },
        signal: ctrl.signal,
      });
      if (res.status === 404) throw new Error(`Brandfetch has no record for ${domain}`);
      if (!res.ok) throw new Error(`Brandfetch returned ${res.status} for ${domain}`);
      payload = await res.json();
    } finally {
      clearTimeout(timer);
    }
  }

  return mapPayload(domain, payload, source);
}

/** Exported so the mapping can be tested without any network. */
export function mapPayload(
  domain: string,
  payload: any,
  source: BrandCandidate['source'] = 'brandfetch',
): BrandCandidate {
  const warnings: string[] = [];

  /* ------------------------------------------------------------- logos */
  const logos: BrandCandidate['logoOptions'] = [];
  for (const entry of payload?.logos ?? []) {
    for (const f of entry.formats ?? []) {
      if (!f.src) continue;
      logos.push({
        url: f.src,
        kind: entry.type ?? 'logo',
        format: f.format ?? path.extname(String(f.src)).replace('.', ''),
        background: entry.theme,
      });
    }
  }
  // Vector and transparent PNG first — the renderer composites these over
  // brand colour panels, so a JPEG with a baked white box is close to useless.
  const rank = (l: (typeof logos)[number]) =>
    (l.format === 'svg' ? 0 : l.format === 'png' ? 1 : 3) + (l.kind === 'logo' ? 0 : 0.5);
  logos.sort((a, b) => rank(a) - rank(b));

  if (!logos.length) {
    warnings.push('No logo was found. The customer will need to upload one.');
  } else if (!logos.some((l) => l.format === 'svg' || l.format === 'png')) {
    warnings.push(
      'Only raster logos without transparency were found. Ask the customer for an SVG or transparent PNG.',
    );
  }

  /* ------------------------------------------------------------ colors */
  const { colors, warnings: colorWarnings } = reconcileColors(
    (payload?.colors ?? [])
      .map((c: any) => ({ hex: hex(c.hex), type: c.type, brightness: c.brightness }))
      .filter((c: any) => c.hex),
  );
  warnings.push(...colorWarnings);

  /* ------------------------------------------------------------- fonts */
  const fontsFound = (payload?.fonts ?? []) as { name?: string; type?: string }[];
  const titleName = fontsFound.find((f) => f.type === 'title')?.name ?? fontsFound[0]?.name;
  const bodyName = fontsFound.find((f) => f.type === 'body')?.name ?? titleName;

  const head = mapFont(titleName);
  const body = mapFont(bodyName);

  if (titleName && !head.exact) {
    warnings.push(
      head.family
        ? `Brand headline font "${titleName}" is not installed; using ${head.family} as the closest available match.`
        : `Brand headline font "${titleName}" is not installed and has no close match. Ads will use Montserrat until the font files are added.`,
    );
  }
  if (bodyName && !body.exact && bodyName !== titleName) {
    warnings.push(
      body.family
        ? `Brand body font "${bodyName}" is not installed; using ${body.family}.`
        : `Brand body font "${bodyName}" is not installed and has no close match.`,
    );
  }
  if (!fontsFound.length) warnings.push('No brand fonts were identified; defaults will be used.');

  /* ------------------------------------------------------------ images */
  const images: BrandCandidate['imageOptions'] = [];
  for (const entry of payload?.images ?? []) {
    for (const f of entry.formats ?? []) {
      if (f.src) images.push({ url: f.src, kind: entry.type ?? 'image' });
    }
  }

  const quality = typeof payload?.quality === 'number' ? payload.quality : undefined;
  const needsReview =
    source === 'fallback' ||
    !logos.length ||
    (quality !== undefined && quality < 0.6) ||
    warnings.length > 2;

  const brand: Brand = {
    name: payload?.name ?? domain.split('.')[0],
    domain,
    colors,
    fonts: {
      headline: head.family ?? 'Montserrat',
      body: body.family ?? 'Open Sans',
    },
    // Left empty on purpose: these are URLs, not local files. The asset step
    // downloads the chosen logo into Cloudinary and fills these in.
    logos: { primary: '' },
  };

  return {
    brand,
    quality,
    logoOptions: logos.slice(0, 8),
    imageOptions: images.slice(0, 12),
    description: payload?.description ?? payload?.longDescription,
    warnings,
    needsReview,
    source,
  };
}
