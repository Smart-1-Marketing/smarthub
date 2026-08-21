/**
 * Smart 1 Ad Builder — core types
 *
 * Three JSON inputs combine to produce every ad:
 *   Brand JSON     (who the advertiser is)      — from Brandfetch + customer confirmation
 * + Creative JSON  (what the ad says)           — from the OpenAI creative director
 * + Template JSON  (where everything sits)      — hand-authored layout families
 * = a deterministic, pixel-exact banner.
 */

export type SizeKey =
  | '300x250'
  | '250x250'
  | '1080x1080'
  | '1200x628'
  | '1080x1350'
  | '1080x1920'
  | '336x280'
  | '728x90'
  | '160x600'
  | '300x600'
  | '320x50'
  | '970x250'
  | '414x125';

export const ALL_SIZES: SizeKey[] = [
  '300x250',
  '336x280',
  '728x90',
  '160x600',
  '300x600',
  '320x50',
  '970x250',
  '414x125',
];

/* ------------------------------------------------------------------ brand */

export type Weight = 'regular' | 'medium' | 'bold';

export interface Brand {
  name: string;
  domain: string;
  colors: {
    primary: string;
    secondary: string;
    accent: string;
    light: string;
    dark: string;
  };
  fonts: {
    /** Family names must resolve in src/fonts.ts */
    headline: string;
    body: string;
  };
  logos: {
    /** Absolute or project-relative path to a PNG/SVG with transparency. */
    primary: string;
    reverse?: string;
  };
}

/* --------------------------------------------------------------- creative */

/** Copy written specifically for one canvas, not shrunk from another. */
export interface CopySet {
  headline: string;
  support?: string;
  cta?: string;
  offer?: string;
  trust?: string;
}

export interface HeroSet {
  landscape?: string;
  square?: string;
  vertical?: string;
}

export interface CreativeConcept {
  /** Optional full-bleed background photo (path). When set, the composer
   *  paints it across the whole canvas under a legibility overlay instead of
   *  using the flat brand background. Used by the image-background option on
   *  Concept C. */
  backgroundImage?: string;
  /** Strength of the dark overlay over a background image, 0..1. Auto-set from
   *  the image's brightness when not specified. */
  backgroundOverlay?: number;
  conceptId: string;
  name: string;
  /** Which template family renders this concept, e.g. 'T01'. */
  layoutFamily: string;
  /**
   * `default` carries the full copy set. A size key overrides it field by
   * field, so a 320x50 entry can supply only a shorter headline and inherit
   * the rest. Overrides are partial by design.
   */
  copy: { default?: CopySet } & Partial<Record<SizeKey, Partial<CopySet>>>;
  hero: HeroSet;
  /** Use the reverse (white) logo — set when the panel behind it is dark. */
  useReverseLogo?: boolean;
}

export interface Campaign {
  requestId: string;
  campaignName: string;
  brand: Brand;
  concepts: CreativeConcept[];
}

/* --------------------------------------------------------------- template */

export type BoxRole =
  | 'logo'
  | 'headline'
  | 'support'
  | 'cta'
  | 'hero'
  | 'offer'
  | 'trust';

export type HAlign = 'left' | 'center' | 'right';
export type VAlign = 'top' | 'middle' | 'bottom';

/** Named colour slot resolved against Brand.colors at render time. */
export type ColorRef =
  | 'primary'
  | 'secondary'
  | 'accent'
  | 'light'
  | 'dark'
  | string; // literal hex also allowed

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface TextBox extends Box {
  align?: HAlign;
  valign?: VAlign;
  /** [min, max] px at 1x. Autofit steps down from max. */
  size: [number, number];
  maxLines: number;
  lineHeight?: number; // ratio, default 1.2
  weight?: Weight;
  color?: ColorRef;
  /** Uppercase the copy before measuring (common for CTAs / offers). */
  uppercase?: boolean;
  letterSpacing?: number;
}

export interface CtaBox extends TextBox {
  bg?: ColorRef;
  radius?: number;
}

export interface HeroBox extends Box {
  /** Which hero orientation to pull from CreativeConcept.hero. */
  orientation: 'landscape' | 'square' | 'vertical';
  /** Crop anchor when the source aspect differs from the box. */
  focal?:
    | 'center'
    | 'left'
    | 'right'
    | 'top'
    | 'bottom'
    | 'top-left'
    | 'top-right'
    | 'bottom-left'
    | 'bottom-right';
  /** Dark scrim under text that overlaps the image. */
  scrim?: { from: number; to: number; direction: 'left' | 'right' | 'up' | 'down' };
}

export interface PanelSpec extends Box {
  fill: ColorRef;
  radius?: number;
}

export interface SizeLayout {
  canvas: { w: number; h: number };
  /** Uniform safe margin in px. */
  safe: number;
  /**
   * Explicit safe region, overriding `safe`. Needed for formats where the
   * platform defines an asymmetric safe zone — e.g. Amazon 414x125, which is
   * supplied at 828x250 with a 640x250 safe area.
   */
  safeBox?: Box;
  /** Platform UI exclusion zones (e.g. Meta 9:16 top 14% / bottom 35%).
   *  Content must stay clear of these; QA flags violations. */
  safeZone?: { top?: number; bottom?: number; left?: number; right?: number; note?: string };
  background: ColorRef;
  panels?: PanelSpec[];
  logo?: Box & { align?: HAlign; valign?: VAlign };
  hero?: HeroBox;
  headline?: TextBox;
  support?: TextBox;
  offer?: TextBox;
  trust?: TextBox;
  cta?: CtaBox;
}

export interface TemplateSpec {
  id: string;
  name: string;
  description: string;
  sizes: Partial<Record<SizeKey, SizeLayout>>;
}

/* --------------------------------------------------------------- platform */

export interface PlatformSizeRule {
  w: number;
  h: number;
  /** Max bytes for the delivered file. */
  maxFileBytes: number;
  formats: ('jpg' | 'png' | 'gif')[];
  /** Deliver the artwork at this multiple of the nominal size (Amazon 2x). */
  deliverScale: number;
  /** Copy budget from the creative research, [min, max] words. */
  words: [number, number];
  /** Minimum rendered text size in px at delivery scale. */
  minFontPx?: number;
  /** Platform inserts its own CTA — do not bake one in. */
  noBakedCta?: boolean;
  notes?: string;
  /** 'doc' = specified in the background research; 'verify' = needs confirming. */
  source: 'doc' | 'verify';
}

export interface PlatformConfig {
  platform: string;
  label: string;
  sizes: Partial<Record<SizeKey, PlatformSizeRule>>;
}

/* ----------------------------------------------------------------- output */

export interface QaFinding {
  check: string;
  status: 'pass' | 'warn' | 'fail';
  detail: string;
  /** Machine-readable hint the AI copy-shortener can act on. */
  fix?: { action: 'shorten'; role: BoxRole; maxWords?: number };
}

export interface RenderResult {
  platform: string;
  /** Every platform this identical creative satisfies. A 300x250 is byte-for-
   *  byte the same for Google and Amazon, so it is rendered once and tagged
   *  for both rather than duplicated. */
  platforms?: string[];
  size: SizeKey;
  conceptId: string;
  file: string;
  format: 'png' | 'jpg';
  width: number;
  height: number;
  bytes: number;
  wordCount: number;
  qa: QaFinding[];
  status: 'pass' | 'warn' | 'fail';
}
