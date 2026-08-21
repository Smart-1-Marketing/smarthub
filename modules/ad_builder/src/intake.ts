/**
 * Intake to campaign.
 *
 * This is the bridge between a form submission and the renderer. It takes the
 * loose shape a customer produced and turns it into the strict Campaign the
 * renderer needs, resolving every asset to a local file on the way.
 *
 * Asset priority, highest first. The order matters: a customer who took the
 * trouble to upload a logo has told us something Brandfetch cannot know.
 *
 *   1. Customer upload            explicit intent
 *   2. Brandfetch discovery       convenience, confirmed on screen
 *   3. Generated placeholder      so a proof exists at all, clearly marked
 *
 * Colours follow the same rule: Brandfetch supplies the default, a customer
 * override replaces it. Nothing here invents a brand colour silently — where a
 * value had to be derived or substituted it lands in `notes`, which the account
 * manager sees on the request.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import sharp from 'sharp';
import type { Brand, Campaign, CreativeConcept, CopySet, SizeKey } from './types';
import { discoverBrand, mapPayload, normalizeDomain } from './brandfetch';
import { materializeAssets, resolveAsset, prepareLogo, validateAsset } from './assets';
import { contrastRatio, hexLuminance } from './raster';
import { fontIsAvailable } from './fonts';
import { validateCampaign, type Finding } from './validate';
import { makeWordmark } from './wordmark';
import { fitImageToBudget } from './image-budget';
import { getTemplate } from './registry';
import { generateCopy, type CopyBrief } from './copywriter';
import type { LandingAnalysis } from './projects';

export interface Submission {
  requestId?: string;
  business: string;
  website: string;
  contact?: string;
  email?: string;
  campaignName: string;
  promoting: string;
  objective?: string;
  cta?: string;
  benefit?: string;
  offer?: string;
  audience?: string;
  geography?: string;
  landingPage?: string;
  notes?: string;
  /** Cached landing-page read, when the form already ran it. */
  landingAnalysis?: LandingAnalysis;
  /** Copy the customer approved on the intake form's review step. When
   *  present it is authoritative — the build uses it rather than re-writing. */
  approvedCopy?: { headline?: string; support?: string; cta?: string };
  /** Background direction chosen for the offer concept: a Pixabay or AI image
   *  the person selected on the review step. */
  backgroundChoice?: { mode: 'pixabay' | 'ai' | 'upload'; url: string };
  /** Cloudinary links for everything the customer uploaded. Files live in
   *  Cloudinary; we keep only the URL. Logged with the campaign. */
  uploadedLogos?: { publicId?: string; url: string; name?: string }[];
  uploadedImages?: { publicId?: string; url: string; name?: string }[];
  platforms?: string[];
  stockOk?: boolean;
  /** Present when discovery ran in the browser and the customer confirmed it. */
  brand?: Partial<Brand> | null;
  brandSource?: string;
  pickedLogoUrl?: string;
  /** Customer overrides, when they edited the discovered values. */
  colorOverrides?: Partial<Brand['colors']>;
  uploads?: {
    logo?: { publicId?: string; url?: string; name?: string }[];
    image?: { publicId?: string; url?: string; name?: string }[];
  };
}

export interface BuildOptions {
  assetRoot: string;
  cacheDir: string;
  /** Run discovery server-side when the submission carries no brand. */
  discover?: boolean;
  /** Generate a placeholder hero when the customer supplied no imagery. */
  allowPlaceholders?: boolean;
  /** Write copy with the model rather than the deterministic fallback. Default true. */
  aiCopy?: boolean;
  /** Root of the output tree, so chosen background images (served from
   *  /files/imagery/...) can be resolved to disk. */
  outputDir?: string;
}

export interface BuildResult {
  campaign: Campaign;
  notes: string[];
  findings: Finding[];
  assetSources: Record<string, 'upload' | 'brandfetch' | 'wordmark' | 'placeholder' | 'none'>;
  renderable: boolean;
}

const DEFAULTS: Brand['colors'] = {
  primary: '#1F3A5F',
  secondary: '#4A6E9B',
  accent: '#F2B705',
  light: '#FFFFFF',
  dark: '#111111',
};

/** Pull a usable palette out of a logo when no brand colours are known. */
export async function colorsFromImage(file: string): Promise<string[]> {
  const { dominant } = await sharp(file).stats();
  const hex = `#${[dominant.r, dominant.g, dominant.b]
    .map((n) => n.toString(16).padStart(2, '0'))
    .join('')
    .toUpperCase()}`;
  return [hex];
}

/* Colour maths for keeping a brand hue while forcing it to be readable. */

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  const f = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  return [parseInt(f.slice(0, 2), 16), parseInt(f.slice(2, 4), 16), parseInt(f.slice(4, 6), 16)];
}
const rgbToHex = (r: number, g: number, b: number) =>
  '#' + [r, g, b].map((n) => Math.round(Math.max(0, Math.min(255, n))).toString(16).padStart(2, '0')).join('').toUpperCase();

/**
 * Lighten or darken a colour, keeping its hue, until it contrasts with `against`.
 * Used where a template puts brand colour on brand colour — the offer text on
 * the primary panel in T04, for instance. Brandfetch palettes routinely have an
 * accent that sits only 2-3:1 from the primary, which is unreadable; nudging
 * lightness preserves the brand far better than swapping in an unrelated colour.
 */
export function ensureContrast(color: string, against: string, target = 4.5): { hex: string; adjusted: boolean } {
  if (contrastRatio(hexLuminance(color), hexLuminance(against)) >= target) {
    return { hex: color, adjusted: false };
  }
  const [r, g, b] = hexToRgb(color);
  const bgLum = hexLuminance(against);
  // Move away from the background: lighten on dark, darken on light.
  const towardWhite = bgLum < 0.5;
  for (let step = 1; step <= 20; step++) {
    const t = step / 20;
    const candidate = towardWhite
      ? rgbToHex(r + (255 - r) * t, g + (255 - g) * t, b + (255 - b) * t)
      : rgbToHex(r * (1 - t), g * (1 - t), b * (1 - t));
    if (contrastRatio(hexLuminance(candidate), hexLuminance(against)) >= target) {
      return { hex: candidate, adjusted: true };
    }
  }
  return { hex: towardWhite ? '#FFFFFF' : '#111111', adjusted: true };
}

/** Guarantee the five roles exist and that text will be readable on them. */
function finalizeColors(
  found: Partial<Brand['colors']> | undefined,
  overrides: Partial<Brand['colors']> | undefined,
  notes: string[],
): Brand['colors'] {
  const c: Brand['colors'] = { ...DEFAULTS, ...(found ?? {}), ...(overrides ?? {}) };

  if (contrastRatio(hexLuminance(c.light), hexLuminance(c.dark)) < 7) {
    notes.push('Light and dark brand colours did not contrast; substituted white and near-black.');
    c.light = '#FFFFFF';
    c.dark = '#111111';
  }
  // The CTA is dark text on the accent by default. If that combination is
  // unreadable the button fails QA on every size, so catch it here.
  if (contrastRatio(hexLuminance(c.accent), hexLuminance(c.dark)) < 4.5) {
    const onLight = contrastRatio(hexLuminance(c.accent), hexLuminance(c.light));
    notes.push(
      onLight >= 4.5
        ? 'Accent colour is dark; CTA text will be light rather than dark.'
        : 'Accent colour has poor contrast with both light and dark text — review the CTA before sending the proof.',
    );
  }

  // The offer-led layout sets brand-coloured type on the primary panel. Many
  // real palettes fail that pairing, so nudge the accent's lightness rather
  // than letting every offer creative fail QA.
  const onPrimary = ensureContrast(c.accent, c.primary, 4.5);
  if (onPrimary.adjusted) {
    notes.push(
      `Accent ${c.accent} was unreadable on the primary colour (${contrastRatio(hexLuminance(c.accent), hexLuminance(c.primary)).toFixed(1)}:1); ` +
        `adjusted to ${onPrimary.hex} for offer-led layouts.`,
    );
    c.accent = onPrimary.hex;
  }
  return c;
}

function pickFont(name: string | undefined, fallback: string, slot: string, notes: string[]): string {
  if (!name) return fallback;
  if (fontIsAvailable(name)) return name;
  notes.push(
    `Brand font "${name}" (${slot}) is not installed; ads will use ${fallback} until the font files are added to the registry.`,
  );
  return fallback;
}

/* ------------------------------------------------------------------ copy */

const CTA_FALLBACK = 'Learn More';

/**
 * Short forms for canvases that cannot hold the full CTA. A 320x50 button has
 * roughly 72px of usable width; "CHECK AVAILABILITY" needs 109px at the
 * smallest legible size. Truncating mid-phrase would read as a bug, so each
 * long CTA gets a deliberate short equivalent that keeps the same promise.
 */
const CTA_SHORT: Record<string, string> = {
  'Check Availability': 'Check Times',
  'Request Pricing': 'Get Pricing',
  'View Inventory': 'Inventory',
  'Schedule Now': 'Schedule',
  'Register Now': 'Register',
  'Get Estimate': 'Estimate',
  'See the Menu': 'See Menu',
  'Book Today': 'Book Now',
  'Learn More': 'Learn More',
  'Shop Now': 'Shop Now',
  'Call Now': 'Call Now',
  'Get Offer': 'Get Offer',
  'Visit Us': 'Visit Us',
};

/**
 * The headline number in an offer. T04 sets this at 40-88px, so it has to be a
 * token, not a phrase: "$59 New Patient Exam" becomes "$59", "Save 20% on all
 * services" becomes "20% Off". Falls back to the first two words when there is
 * no recognisable figure, and the caller warns when that happens.
 */
export function offerToken(offer: string): { token: string; extracted: boolean } {
  const text = (offer ?? '').trim();
  if (!text) return { token: '', extracted: false };

  const pct = text.match(/(\d{1,3})\s*%/);
  if (pct) return { token: `${pct[1]}% Off`, extracted: true };

  const money = text.match(/\$\s?([\d,]+(?:\.\d{2})?)/);
  if (money) {
    const zeroDown = /down/i.test(text) && /^0+$/.test(money[1].replace(/\D/g, ''));
    return { token: zeroDown ? '$0 Down' : `$${money[1]}`, extracted: true };
  }

  if (/\bfree\b/i.test(text)) {
    const after = text.match(/free\s+(\w+)/i);
    return { token: after ? `Free ${after[1]}` : 'Free', extracted: true };
  }

  return { token: text.split(/\s+/).slice(0, 2).join(' '), extracted: false };
}

/** Short CTA for tight canvases; falls back to the first word. */
function shortCta(cta: string): string {
  return CTA_SHORT[cta] ?? cta.split(/\s+/)[0];
}

/**
 * Copy written from the submission, per canvas. This is the deterministic
 * stand-in for the OpenAI copywriter: it respects the per-size word budgets
 * from the creative research rather than repeating one headline everywhere.
 * When the AI step lands it replaces this function and nothing else.
 */
export function copyFromSubmission(sub: Submission): CreativeConcept['copy'] {
  const benefit = (sub.benefit ?? '').trim();
  const promoting = (sub.promoting ?? '').trim();
  const offer = (sub.offer ?? '').trim();

  const words = (s: string, n: number) => s.split(/\s+/).filter(Boolean).slice(0, n).join(' ');

  // Approved copy from the review step wins over everything. This is the whole
  // point of the gate: what the human signed off on is what renders.
  const ap = sub.approvedCopy;
  const cta = (ap?.cta && ap.cta.trim()) || (sub.cta && !/recommend/i.test(sub.cta) ? sub.cta : CTA_FALLBACK);
  const headline = (ap?.headline && ap.headline.trim()) || benefit || words(promoting, 6) || sub.business;
  const support = (ap?.support !== undefined)
    ? ap.support.trim()
    : (promoting && promoting !== headline ? words(promoting, 12) : '');

  const shortOffer = words(offer, 3);

  return {
    default: {
      headline: words(headline, 8),
      support,
      cta,
      ...(offer ? { offer: shortOffer } : {}),
    },
    // A leaderboard reads as one horizontal sentence; a mobile banner gets
    // roughly five words and no support line at all.
    '728x90': { headline: words(headline, 6), support: words(support, 5), cta: shortCta(cta) },
    '320x50': { headline: words(headline, 5), support: '', cta: shortCta(cta) },
    '414x125': { headline: words(headline, 5), support: offer ? shortOffer : '' },
    // The offer strip on these is a single narrow line, so it takes the short
    // form of the offer rather than the full phrase.
    '300x250': { offer: words(offer, 2) },
    '336x280': { offer: words(offer, 2) },
    '160x600': { headline: words(headline, 6), support: words(support, 6), offer: words(offer, 2), cta: shortCta(cta) },
    '970x250': {
      headline: words(headline, 7),
      support: words(support, 9),
      ...(sub.geography ? { trust: words(sub.geography, 4) } : {}),
    },
  };
}

/* ------------------------------------------------------------- placeholder */

async function placeholderHero(
  dir: string,
  colors: Brand['colors'],
  orientation: 'landscape' | 'square' | 'vertical',
): Promise<string> {
  const dims = { landscape: [1600, 900], square: [1200, 1200], vertical: [900, 1500] }[orientation];
  const [w, h] = dims;
  fs.mkdirSync(dir, { recursive: true });
  const out = path.join(dir, `placeholder-${orientation}.jpg`);
  if (fs.existsSync(out)) return out;

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">
    <defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="${colors.primary}"/><stop offset="1" stop-color="${colors.secondary}"/>
    </linearGradient></defs>
    <rect width="${w}" height="${h}" fill="url(#g)"/>
    <circle cx="${w * 0.68}" cy="${h * 0.42}" r="${Math.min(w, h) * 0.3}" fill="${colors.accent}" opacity="0.35"/>
    <text x="${w / 2}" y="${h / 2}" text-anchor="middle" font-family="monospace"
      font-size="${Math.max(14, Math.min(w, h) * 0.05)}" fill="#fff" opacity="0.9">PLACEHOLDER IMAGE</text>
    <text x="${w / 2}" y="${h / 2 + Math.min(w, h) * 0.07}" text-anchor="middle" font-family="monospace"
      font-size="${Math.max(11, Math.min(w, h) * 0.032)}" fill="#fff" opacity="0.75">AWAITING CUSTOMER PHOTOGRAPHY</text>
  </svg>`;
  await sharp(Buffer.from(svg)).jpeg({ quality: 86 }).toFile(out);
  return out;
}

/** Derive the three hero orientations from one customer photo. */
async function deriveOrientations(
  source: string,
  dir: string,
): Promise<{ landscape: string; square: string; vertical: string }> {
  fs.mkdirSync(dir, { recursive: true });
  const base = path.basename(source, path.extname(source));
  const out: Record<string, string> = {};
  const targets: [string, number, number][] = [
    ['landscape', 1600, 900],
    ['square', 1200, 1200],
    ['vertical', 900, 1500],
  ];
  for (const [name, w, h] of targets) {
    const file = path.join(dir, `${base}-${name}.jpg`);
    if (!fs.existsSync(file)) {
      // attention:'entropy' keeps the busy part of the frame when cropping to
      // an extreme aspect, which is what a 160x600 needs from a wide photo.
      await sharp(source)
        .resize({ width: w, height: h, fit: 'cover', position: sharp.strategy.attention })
        .jpeg({ quality: 88 })
        .toFile(file);
    }
    out[name] = file;
  }
  return out as { landscape: string; square: string; vertical: string };
}

/* -------------------------------------------------------------- the bridge */

export async function buildCampaign(
  sub: Submission,
  opts: BuildOptions,
): Promise<BuildResult> {
  const notes: string[] = [];
  const assetSources: BuildResult['assetSources'] = {};
  const cacheDir = opts.cacheDir;
  const domain = normalizeDomain(sub.website || '');

  /* ------------------------------------------------------------- brand */
  let discovered: Partial<Brand> | null = sub.brand ?? null;
  let logoCandidates: string[] = [];

  if (sub.pickedLogoUrl) logoCandidates.push(sub.pickedLogoUrl);

  if (!discovered && opts.discover !== false && domain) {
    try {
      const found = await discoverBrand(domain);
      discovered = found.brand;
      notes.push(...found.warnings);
      logoCandidates.push(...found.logoOptions.map((l) => l.url));
      if (found.needsReview) notes.push('Brand discovery confidence was low — please review before sending.');
    } catch (e: any) {
      notes.push(`Brand lookup did not return a result (${e?.message ?? e}). Working from uploads only.`);
    }
  }

  const colors = finalizeColors(discovered?.colors, sub.colorOverrides, notes);
  const brand: Brand = {
    name: sub.business,
    domain: domain || sub.business.toLowerCase().replace(/\W+/g, '-'),
    colors,
    fonts: {
      headline: pickFont(discovered?.fonts?.headline, 'Montserrat', 'headline', notes),
      body: pickFont(discovered?.fonts?.body, 'Open Sans', 'body', notes),
    },
    logos: { primary: '' },
  };

  /* -------------------------------------------------------------- logo */
  const uploadedLogos = sub.uploads?.logo ?? [];
  let logoFile = '';

  for (const up of uploadedLogos) {
    const ref = up.publicId ? `cloudinary:${up.publicId}` : up.url;
    if (!ref) continue;
    try {
      logoFile = await prepareLogo(await resolveAsset(ref, { cacheDir, label: 'logo' }), cacheDir);
      assetSources.logo = 'upload';
      break;
    } catch (e: any) {
      notes.push(`Uploaded logo could not be used (${e?.message ?? e}).`);
    }
  }

  if (!logoFile) {
    for (const url of logoCandidates) {
      try {
        logoFile = await prepareLogo(await resolveAsset(url, { cacheDir, label: 'logo' }), cacheDir);
        assetSources.logo = 'brandfetch';
        notes.push('Logo taken from brand discovery. Ask the customer to confirm it is current.');
        break;
      } catch {
        /* try the next candidate */
      }
    }
  }

  if (logoFile) {
    brand.logos.primary = logoFile;
    // A dark logo on a dark panel disappears. Detect it rather than shipping it.
    try {
      const stats = await sharp(logoFile).stats();
      const lum = hexLuminance(
        `#${[stats.dominant.r, stats.dominant.g, stats.dominant.b]
          .map((n) => n.toString(16).padStart(2, '0'))
          .join('')}`,
      );
      if (lum < 0.25) {
        notes.push(
          'The logo is dark, so it will not read on dark brand panels. A white or reversed version would improve the offer-led layouts.',
        );
      }
    } catch {
      /* stats are advisory only */
    }
  } else {
    // No image logo resolved — from upload or discovery. Rather than dead-end
    // the whole request, set the business name as a typographic wordmark and
    // keep building. It is a real, presentable lockup, and it means a customer
    // without a logo file still gets ads instead of a "we'll be in touch".
    try {
      logoFile = await makeWordmark(brand.name, brand, { cacheDir });
      brand.logos.primary = logoFile;
      assetSources.logo = 'wordmark';
      notes.push(
        'No logo image was available, so the business name is set as a text wordmark. ' +
        'Uploading a logo later will sharpen the brand presence.',
      );
    } catch (e: any) {
      assetSources.logo = 'none';
      notes.push(`No usable logo and the text wordmark could not be generated (${e?.message ?? e}).`);
    }
  }

  /* -------------------------------------------------------------- hero */
  const uploadedImages = sub.uploads?.image ?? [];
  let hero: { landscape?: string; square?: string; vertical?: string } = {};

  for (const up of uploadedImages) {
    const ref = up.publicId ? `cloudinary:${up.publicId}` : up.url;
    if (!ref) continue;
    try {
      const file = await resolveAsset(ref, { cacheDir, label: 'hero' });
      const check = await validateAsset(file);
      if (!check.ok) {
        notes.push(`"${up.name ?? ref}" was not usable: ${check.reason}`);
        continue;
      }
      // Guarantee the source hero is under the 150 KB ceiling before it ever
      // enters the render pipeline. A customer's 5 MB phone photo is squeezed,
      // not rejected.
      const fitted = await fitImageToBudget(file, path.join(cacheDir, 'hero-source.jpg'));
      if (fitted.note) notes.push(`Uploaded photo: ${fitted.note}`);
      hero = await deriveOrientations(fitted.file, path.join(cacheDir, 'hero'));
      assetSources.hero = 'upload';
      break;
    } catch (e: any) {
      notes.push(`Uploaded image could not be used (${e?.message ?? e}).`);
    }
  }

  if (!hero.landscape && opts.allowPlaceholders !== false) {
    const dir = path.join(cacheDir, 'placeholder');
    hero = {
      landscape: await placeholderHero(dir, colors, 'landscape'),
      square: await placeholderHero(dir, colors, 'square'),
      vertical: await placeholderHero(dir, colors, 'vertical'),
    };
    assetSources.hero = 'placeholder';
    notes.push(
      sub.stockOk
        ? 'No photography supplied. Placeholder imagery is in place — replace with stock or generated imagery before the client proof.'
        : 'No photography supplied and stock was not authorised. These proofs use placeholders and must not be sent as-is.',
    );
  }

  /* ---------------------------------------------------------- concepts */
  // Deterministic copy is the floor: always computed, always renderable,
  // used outright when there is no key and used to fill any size the model
  // skipped.
  const fallbackCopy = copyFromSubmission(sub);
  const offerText = (sub.offer ?? '').trim();
  let copy = fallbackCopy;
  let offerConceptCopy: CreativeConcept['copy'] | null = null;
  let copySource: 'openai' | 'fallback' = 'fallback';

  // If the customer approved copy on the review step, that is final — do not
  // let the AI writer second-guess it. It still runs when there is no approved
  // copy (e.g. an API caller that skipped the form).
  if (opts.aiCopy !== false && !sub.approvedCopy) {
    const brief: CopyBrief = {
      business: sub.business,
      promoting: sub.promoting,
      benefit: sub.benefit,
      offer: offerText || undefined,
      audience: sub.audience,
      geography: sub.geography,
      objective: sub.objective,
      cta: sub.cta,
      landing: sub.landingAnalysis,
    };
    // T01 carries the benefit concept, T04 the offer concept when there is
    // one; request every size either layout actually uses.
    const sizes = [...new Set([
      ...Object.keys(getTemplate('T01').sizes),
      ...(offerText ? Object.keys(getTemplate('T04').sizes) : []),
    ])] as SizeKey[];

    const written = await generateCopy(brief, sub, { sizes });
    copySource = written.source;
    notes.push(...written.warnings);

    if (written.source === 'openai') {
      const benefitConcept = written.concepts[0];
      if (benefitConcept) {
        copy = { default: fallbackCopy.default, ...benefitConcept.copy } as CreativeConcept['copy'];
        notes.push(`Copy for the benefit concept was written by the model (angle: ${benefitConcept.angle}).`);
      }
      if (offerText) {
        // Prefer a concept whose angle reads as offer/price-led; otherwise
        // take the second concept if one exists.
        const offerConcept =
          written.concepts.find((c) => /offer|price|\$|percent|discount/i.test(c.angle)) ??
          written.concepts[1];
        if (offerConcept) {
          offerConceptCopy = { default: offerConcept.copy['300x250'] ?? {}, ...offerConcept.copy } as CreativeConcept['copy'];
          notes.push(`Copy for the offer concept was written by the model (angle: ${offerConcept.angle}).`);
        }
      }
    }
  }

  let offerBg: string | undefined;
  const concepts: CreativeConcept[] = [
    {
      conceptId: 'A',
      name: 'Benefit',
      layoutFamily: 'T01',
      hero,
      copy,
    },
  ];

  // An offer-led concept only makes sense when there is an offer to lead with.
  if (offerText) {
    const { token, extracted } = offerToken(offerText);
    if (!extracted && copySource === 'fallback') {
      notes.push(
        `The offer "${offerText}" has no clear figure, so the offer-led concept leads with "${token}". A price or percentage would work harder here.`,
      );
    }
    const trim = (s: string, n: number) => s.split(/\s+/).filter(Boolean).slice(0, n).join(' ');
    const baseHeadline = copy.default?.headline ?? sub.business;
    const baseCta = copy.default?.cta ?? CTA_FALLBACK;

    // AI copy is trimmed to a word budget, not a pixel width, so any CTA still
    // gets clamped through the vocabulary map for the two tightest canvases —
    // "Check Availability" fits the word count and overflows the button.
    const clampCta = (c: Partial<CopySet> | undefined) => {
      if (!c?.cta) return c;
      return { ...c, cta: shortCta(c.cta) };
    };

    const aiCopy = offerConceptCopy;

    // If the customer picked a photo/AI background on the review step, resolve
    // it to a local file (already under 150 KB from the imagery endpoints) and
    // attach it. The composer paints it full-bleed with a legibility overlay.
    if (sub.backgroundChoice?.url) {
      try {
        const chosen = sub.backgroundChoice.url;
        let candidate: string | null = null;
        if (/^https?:\/\//.test(chosen)) {
          // An uploaded photo lives in Cloudinary — fetch it once to raster it
          // under budget. We still keep the Cloudinary link as the source of
          // record; this local copy is only the composited input.
          const resp = await fetch(chosen);
          if (resp.ok) {
            const buf = Buffer.from(await resp.arrayBuffer());
            const tmp = path.join(cacheDir, 'uploaded-bg-src');
            fs.mkdirSync(cacheDir, { recursive: true });
            fs.writeFileSync(tmp, buf);
            candidate = tmp;
          }
        } else {
          // A Pixabay/AI pick already served locally under /files/...
          const rel = chosen.replace(/^\/files\//, '');
          const local = path.join(opts.outputDir ?? 'out', rel);
          if (fs.existsSync(local)) candidate = local;
        }
        if (candidate) {
          const fitted = await fitImageToBudget(candidate, path.join(cacheDir, 'offer-bg.jpg'));
          offerBg = fitted.file;
          notes.push(`Your chosen ${sub.backgroundChoice.mode} background is applied to every concept.`);
          try {
            const gal = path.join(opts.outputDir ?? 'out', 'gallery',
              (sub.campaignName ?? 'campaign').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''));
            fs.mkdirSync(gal, { recursive: true });
            fs.copyFileSync(fitted.file, path.join(gal, `bg-${Date.now().toString(36)}.jpg`));
          } catch { /* best-effort */ }
        }
      } catch (e: any) {
        notes.push(`Chosen background could not be applied (${e?.message ?? e}); using a solid colour.`);
      }
    }

    concepts.push({
      conceptId: 'C',
      name: 'Offer',
      layoutFamily: 'T04',
      useReverseLogo: true,
      hero: {},
      backgroundImage: offerBg,
      copy: aiCopy ? {
        ...aiCopy,
        default: {
          headline: aiCopy.default?.headline ?? baseHeadline,
          support: aiCopy.default?.support ?? '',
          cta: aiCopy.default?.cta ?? baseCta,
          offer: offerToken(aiCopy.default?.offer ?? offerText).token || token,
        },
        '320x50': clampCta({ ...aiCopy['320x50'], offer: offerToken(aiCopy['320x50']?.offer ?? offerText).token || token }),
        '728x90': clampCta(aiCopy['728x90']),
      } : {
        default: {
          offer: token,
          headline: trim(baseHeadline, 6),
          support: trim(copy.default?.support ?? '', 8),
          cta: baseCta,
        },
        // The leaderboard carries the offer, a short headline and the CTA in
        // one horizontal line; anything more breaks the word budget.
        '728x90': {
          headline: trim(baseHeadline, 5),
          support: trim(copy.default?.support ?? '', 4),
          cta: shortCta(baseCta),
        },
        '320x50': { headline: trim(baseHeadline, 2), support: '', cta: shortCta(baseCta) },
      },
    });
  } else {
    notes.push('No offer was supplied, so only the benefit-led concept was produced.');
  }

  // A chosen background belongs to the whole look, not one concept: apply it
  // to every concept so no template option shows a grey placeholder instead.
  if (typeof offerBg !== 'undefined' && offerBg) {
    for (const c of concepts) c.backgroundImage = offerBg;
  }

  const campaign: Campaign = {
    requestId: sub.requestId ?? `AD-${new Date().getFullYear()}-${String(Date.now()).slice(-6)}`,
    campaignName: sub.campaignName,
    brand,
    concepts,
  };

  const findings = validateCampaign(campaign, {
    assetRoot: opts.assetRoot,
    platforms: (sub.platforms ?? ['google']).filter((p) => p === 'google' || p === 'amazon'),
  });

  return {
    campaign,
    notes,
    findings,
    assetSources,
    renderable: !findings.some((f) => f.level === 'error'),
  };
}
