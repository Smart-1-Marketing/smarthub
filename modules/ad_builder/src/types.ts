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
  | '1200x1200'
  | '1200x1500'
  | '1080x1350'
  | '1080x1920'
  | '336x280'
  | '728x90'
  | '160x600'
  | '300x600'
  | '320x50'
  | '970x250'
  | '414x125';
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
  /** Per-block type and CTA-fill overrides on top of the template's boxes.
   *  Applied in render.ts right after the layout loads, clamped to the canvas
   *  -- see block-style.ts for why only these properties and not a free
   *  partial box. */
  styleOverrides?: import('./block-style').StyleOverrides;
  /** Optional full-bleed background photo (path). When set, the composer
   *  paints it across the whole canvas under a legibility overlay instead of
   *  using the flat brand background. Used by the image-background option on
   *  Concept C. */
  backgroundImage?: string;
  /** Strength of the overlay over a background image, 0..1. Auto-set from
   *  the image's brightness when not specified. */
  backgroundOverlay?: number;
  /**
   * The overlay's color. Absent means the graded dark scrim the composer has
   * always drawn -- heavier where the copy sits, lighter on the other side.
   * Set to a hex and the overlay becomes a FLAT wash of that color at
   * `backgroundOverlay` opacity, because "a color and a level of transparency"
   * is the thing a person is choosing, and a graded version of it would not
   * be the color they picked anywhere on the canvas.
   */
  backgroundOverlayColor?: string;
  /**
   * Which part of the background photo survives the crop, as one of the nine
   * SVG preserveAspectRatio alignments.
   *
   * Superseded by `backgroundOffset`, and kept because concepts saved before
   * that existed carry it. A nine-way grid answers "top or bottom" and cannot
   * answer "a bit further down", which is the note an operator actually
   * writes — so the grid became a pair of nudge arrows and this converts to
   * the offset they move. Read only when `backgroundOffset` is absent.
   */
  backgroundPosition?: string;
  /**
   * Where the background photo sits, as a fraction of its own overflow.
   *
   * 0,0 is centred. -1 shows the left/top edge of the picture, +1 the
   * right/bottom — exactly what the nine-way grid used to express, and every
   * position in between, which is the point. Stored as a fraction rather than
   * pixels so it means the same thing on a 300x250 and a 970x250: the same
   * part of the picture is showing on both, which is what "the same ad in
   * eight sizes" has to mean.
   */
  backgroundOffset?: { x: number; y: number };
  /**
   * How far in the background photo is zoomed, 1 = just covering the canvas.
   *
   * Below 1 the picture would not cover and the ad would show through to the
   * brand colour at the edges, so 1 is the floor rather than a choice.
   */
  backgroundZoom?: number;
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
  /**
   * Family for this block only. Unset means the brand's font for this role --
   * the headline face for a headline, the body face for everything else --
   * which is what nearly every ad wants. Set when one block needs to differ,
   * e.g. an offer flash in the headline face on a body-face layout.
   * Must be a family fonts.ts actually has; block-style.ts checks that.
   */
  font?: string;
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
  /**
   * Warn when text covers more than this share of the canvas.
   *
   * Set only where a platform publishes such a guideline, which today is Meta
   * and only Meta. Absent means the question is not asked for this size at
   * all, rather than asked with a lenient number: a display banner is mostly
   * type by design, and a threshold generous enough never to fire on one is a
   * threshold that would not catch anything on a Meta feed image either.
   */
  textCoverageWarnPct?: number;
  notes?: string;
  /** 'doc' = specified in the background research; 'verify' = needs confirming. */
  source: 'doc' | 'verify';
  /**
   * What confirmed this rule's numbers, and when.
   *
   * The claim, not a decoration: `source: 'doc'` says a limit came from the
   * platform's documentation, and diagnostics.ceilingDoubt() reads *this*
   * rather than that claim, because for a long time thirteen rules declared
   * `doc` with nothing behind them and the panel reported a clean sweep. A
   * rule with neither this nor `source: 'verify'` is reported as unconfirmed
   * whatever it declares.
   */
  _verifiedAgainst?: string;
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
