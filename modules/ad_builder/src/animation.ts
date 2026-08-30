/**
 * Animated banners.
 *
 * A GIF here is the static ad that was already built, played two or three
 * ways. Nothing about the layout, the brand or the picture changes: a frame is
 * one more pass through `compose()` with a different `CopySet` or a different
 * CTA fill, which is why this file is arithmetic and a weight ladder rather
 * than a second renderer. It is deliberately downstream of a saved static
 * build -- there is nothing to animate until the ad exists, and adding a
 * question about motion to the first build would slow down the eight sizes
 * that get made every time to serve the one that occasionally moves.
 *
 * Two motions are offered because two are what people ask for:
 *
 *   `text`   -- up to three slides. The headline (and whatever else is typed
 *               against that slide) changes and everything else holds. This is
 *               the one that carries a second and third message.
 *   `button`  -- one message, and the call to action pulses. Nothing is said
 *               that the static ad does not already say; it is there to be
 *               noticed.
 *
 * Both are capped at five frames, which is the operator's cap and not a
 * platform's -- see ANIMATION_RULES for which numbers are whose.
 *
 * WHAT IS AND IS NOT A PUBLISHED RULE
 *
 * Google publishes four numbers for an animated image ad and every one of them
 * is a way to have a perfectly good ad disapproved:
 *
 *   150 KB           the same ceiling a static image ad has
 *   30 seconds       total animation, INCLUDING the loops
 *   5 FPS or slower  so 200ms is the floor for a frame, not a preference
 *   it must stop     a GIF that loops for ever is outside the rule even
 *                    though it plays perfectly in every browser
 *
 * That last one is the one worth being careful about, because it is invisible:
 * `loop: 0` is endless, renders correctly everywhere, passes every eye on
 * every screen here, and is a policy breach. So the loop count is COMPUTED
 * from the cycle length rather than chosen, and `loop: 0` is unreachable from
 * this file.
 *
 * `maxSlides` and `maxFrames` are ours. They are marked as ours in
 * ANIMATION_RULES.source, the rule `services/abcd_service.py` works to: a
 * threshold with no name on it is an opinion, and "Google requires this" about
 * a number Google has never published is the kind of claim a client can talk
 * us out of once they find out.
 *
 * WHICH SIZES CAN CARRY ONE
 *
 * Not a judgement made here. `src/config/platforms/*.json` already says which
 * formats each placement accepts, and only Google's eight banner sizes list
 * `gif`. Amazon's specs in this repo are static at 40-50 KB, Meta converts an
 * uploaded GIF into a video, and Google's three responsive-display IMAGE
 * assets are image assets -- Google composes its own headline around those.
 * So `animationSupport()` reads the config and refuses BY NAME rather than
 * quietly handing back a static file, because a set that came back with seven
 * moving ads and one still one, with nothing saying which or why, is the
 * confident wrong answer this codebase keeps having to undo.
 */

import sharp from 'sharp';
import type { CopySet, PlatformSizeRule, QaFinding, SizeKey } from './types';
import type { StyleOverrides } from './block-style';
import { getPlatform } from './registry';

/* --------------------------------------------------------------- the rules */

export const ANIMATION_RULES = {
  /** Ours. Three messages is what a banner can carry before it is a slideshow. */
  maxSlides: 3,
  /** Ours. */
  maxFrames: 5,
  /** Google: "5 FPS or slower", which is a 200ms floor on a frame. */
  minFrameMs: 200,
  /** Ours: past this a slide is a pause, and the 30s ceiling arrives fast. */
  maxFrameMs: 4000,
  /** Google: animation stops at 30 seconds, loops included. */
  maxTotalMs: 30_000,
  /** Google: the same 150 KB an image ad has. Read per size from the platform
   *  config rather than hard-coded here, because Amazon's is 40 KB and the
   *  number that matters is the one for the placement in hand. */
  source: {
    platform: 'https://support.google.com/adspolicy/answer/176108',
    house: 'Smart 1 Marketing — maxSlides and maxFrames are ours, not Google’s.',
  },
} as const;

/** The first question the operator is asked, as data, so the build screen and
 *  the server read one list rather than two that drift. */
export const MOTIONS = [
  {
    kind: 'text' as const,
    label: 'Animate the text',
    detail:
      'Up to three slides. The words change and the layout, the picture and the ' +
      'button hold still. Use it to say a second thing — the offer after the ' +
      'promise, or three services in turn.',
  },
  {
    kind: 'button' as const,
    label: 'Animate the button',
    detail:
      'One message, and the call to action pulses. It says nothing the static ad ' +
      'does not already say; it is there to be noticed.',
  },
];

/** Defaults chosen so an operator who changes nothing gets a compliant ad. */
export const DEFAULTS = {
  /** A slide has to be read, so it holds well past the 200ms floor. */
  textFrameMs: 1800,
  /** A pulse is motion, not a message: fast, and still legal. */
  buttonFrameMs: 320,
  buttonFrames: 4,
  /** How much lighter the highlight is than the button's own fill. */
  buttonLift: 0.22,
} as const;

/* ---------------------------------------------------------------- the spec */

/**
 * What an operator asked for, stored on the concept and saved with the build.
 *
 * Slide 1 is deliberately NOT in here: it is the concept's own copy, so an
 * edit to the static ad is an edit to the first slide and the two cannot come
 * apart. `slides` holds slide 2 and slide 3 as partial overrides, resolved the
 * way per-size copy already is — field by field over what is underneath.
 */
export interface AnimationSpec {
  kind: 'text' | 'button';
  /** Slides 2 and 3, as partial copy over the concept's own. `text` only. */
  slides?: Array<Partial<CopySet>>;
  /**
   * Slide copy written for one canvas.
   *
   * The same reason `CreativeConcept.copy` carries per-size entries: a
   * headline that fits the 300x600 is two lines on the 320x50, and a mobile
   * banner has room for five words. Without this a set animates perfectly at
   * seven sizes and fails QA at the eighth on copy nobody can shorten -- which
   * is not hypothetical, it is what the first run of this against the sample
   * campaign did.
   *
   * Resolved slide by slide and field by field over `slides`, so a size that
   * needs a shorter slide 2 overrides slide 2 and inherits slide 3.
   */
  sizeSlides?: Partial<Record<SizeKey, Array<Partial<CopySet>>>>;
  /** How long each frame is held, ms. Clamped to the rules. */
  frameMs?: number;
  /** `button` only: how many frames the pulse takes. */
  frames?: number;
  /** `button` only: the color the fill lifts to. Absent means computed. */
  highlight?: string;
  /** Off without deleting what was set up, so an operator can put it back. */
  enabled?: boolean;
}

export interface AnimationFrame {
  /** Patched over the concept's copy for this frame only. */
  copy?: Partial<CopySet>;
  /** Patched over the concept's style overrides for this frame only. */
  style?: StyleOverrides;
  /** How long this frame is held, ms. */
  ms: number;
  /** 1-based, and what QA names when this frame is the one that is wrong. */
  slide: number;
  /**
   * How QA refers to this frame.
   *
   * A text animation has slides and a button pulse does not -- every frame of
   * a pulse is slide 1 with a different button on it, so labelling its third
   * frame "slide 3" describes an ad that does not exist and sends somebody
   * looking for copy nobody typed.
   */
  tag: string;
  label: string;
}

export interface AnimationPlan {
  kind: 'text' | 'button';
  frames: AnimationFrame[];
  /** How many times the sequence plays. Never 0 — 0 is endless. */
  loop: number;
  cycleMs: number;
  /** cycleMs * loop. What the 30-second rule is measured against. */
  totalMs: number;
  fps: number;
  /**
   * What was clamped or dropped, in words.
   *
   * A spec that asked for four slides comes back with three and says so. A
   * plan that silently did what it could is one where the operator believes
   * the fourth slide is in the ad.
   */
  adjustments: string[];
  /** Set when there is nothing to render, with the reason. */
  refused?: string;
}

export interface PlanContext {
  /** The button's own fill, already resolved to a hex. `button` needs it to
   *  compute a highlight; absent means the layout draws no button. */
  baseCtaFill?: string;
  /** False when this size's layout has no CTA box at all. */
  hasCta?: boolean;
  /** Which canvas this plan is for, so per-size slide copy is picked up. */
  size?: SizeKey;
}

/**
 * The slides this size actually runs, `copyForSize` for animation.
 *
 * Exported because the build screen asks the server what a size will say
 * rather than resolving it in the browser -- a second copy of this rule is a
 * second answer to "what does slide 2 say here", and the two disagree the day
 * either is edited.
 */
export function slidesFor(spec: AnimationSpec, size?: SizeKey): Array<Partial<CopySet>> {
  const base = Array.isArray(spec.slides) ? spec.slides : [];
  const forSize = size ? spec.sizeSlides?.[size] : undefined;
  if (!forSize?.length) return base;
  const n = Math.max(base.length, forSize.length);
  const out: Array<Partial<CopySet>> = [];
  for (let i = 0; i < n; i++) {
    out.push({ ...(base[i] ?? {}), ...(forSize[i] ?? {}) });
  }
  return out;
}

/* ------------------------------------------------------------ the planning */

const clampNum = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

const HEX = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

function expand(hex: string): [number, number, number] {
  const m = hex.replace('#', '');
  const full = m.length === 3 ? m.split('').map((c) => c + c).join('') : m;
  return [
    parseInt(full.slice(0, 2), 16),
    parseInt(full.slice(2, 4), 16),
    parseInt(full.slice(4, 6), 16),
  ];
}

function toHex(r: number, g: number, b: number): string {
  const h = (n: number) => clampNum(Math.round(n), 0, 255).toString(16).padStart(2, '0');
  return `#${h(r)}${h(g)}${h(b)}`;
}

/**
 * A visibly different version of the button's own color.
 *
 * Lightening is the obvious move and it is wrong on a fill that is already
 * near-white: #fefefe lifted 22% is #ffffff, which is a pulse nobody can see
 * and an animation that technically ran. So a light fill darkens instead. The
 * direction is decided from the fill rather than fixed, for the same reason
 * the composer picks the reverse logo from what is behind it.
 */
export function pulseColor(hex: string, amount = DEFAULTS.buttonLift): string {
  const [r, g, b] = expand(hex);
  const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  const up = lum < 0.6;
  return up
    ? toHex(r + (255 - r) * amount, g + (255 - g) * amount, b + (255 - b) * amount)
    : toHex(r * (1 - amount), g * (1 - amount), b * (1 - amount));
}

/**
 * How many times a sequence may play before it has to stop.
 *
 * Never 0. `loop: 0` in a GIF means "for ever", which is the one value the
 * 30-second rule forbids and the one value every encoder defaults to.
 */
export function loopsWithin(cycleMs: number, maxTotalMs = ANIMATION_RULES.maxTotalMs): number {
  if (!(cycleMs > 0)) return 1;
  return Math.max(1, Math.floor(maxTotalMs / cycleMs));
}

function copyIsEmpty(patch: Partial<CopySet> | undefined): boolean {
  if (!patch) return true;
  return !Object.values(patch).some((v) => String(v ?? '').trim().length > 0);
}

/**
 * Turn what was asked for into frames that comply, and say what it cost.
 *
 * Every clamp is recorded rather than applied quietly: an operator who typed a
 * fourth slide, or 80ms a frame, has to be told which of those the ad does not
 * have. Refusals are separate from adjustments — a refusal means there is no
 * animation at all, and it is the answer the screen prints instead of a
 * preview.
 */
export function planAnimation(spec: AnimationSpec, ctx: PlanContext = {}): AnimationPlan {
  const adjustments: string[] = [];
  const kind: AnimationPlan['kind'] = spec.kind === 'button' ? 'button' : 'text';

  const askedMs = Number(
    spec.frameMs ?? (kind === 'button' ? DEFAULTS.buttonFrameMs : DEFAULTS.textFrameMs),
  );
  let ms = Number.isFinite(askedMs) ? askedMs : DEFAULTS.textFrameMs;
  if (ms < ANIMATION_RULES.minFrameMs) {
    adjustments.push(
      `A frame was asked to hold ${Math.round(ms)}ms and is held for ${ANIMATION_RULES.minFrameMs}ms: ` +
      'Google requires animated ads to run at 5 frames a second or slower.',
    );
    ms = ANIMATION_RULES.minFrameMs;
  }
  if (ms > ANIMATION_RULES.maxFrameMs) {
    adjustments.push(
      `A frame was asked to hold ${Math.round(ms)}ms and is held for ${ANIMATION_RULES.maxFrameMs}ms.`,
    );
    ms = ANIMATION_RULES.maxFrameMs;
  }
  ms = Math.round(ms);

  const frames: AnimationFrame[] = [];

  if (kind === 'text') {
    // Slide 1 is the concept's own copy and carries no patch, so editing the
    // static ad edits the first slide and the two can never disagree.
    frames.push({ ms, slide: 1, tag: 'slide 1', label: 'Slide 1 — the ad as built' });

    const asked = slidesFor(spec, ctx.size);
    const usable = asked.filter((s) => !copyIsEmpty(s));
    if (usable.length < asked.length) {
      adjustments.push(
        `${asked.length - usable.length} slide(s) had nothing typed on them and were left out.`,
      );
    }
    const extra = ANIMATION_RULES.maxSlides - 1;
    const kept = usable.slice(0, extra);
    if (usable.length > extra) {
      adjustments.push(
        `${usable.length + 1} slides were asked for; ${ANIMATION_RULES.maxSlides} is the most a banner carries here, ` +
        `so slide${usable.length - extra > 1 ? 's' : ''} ${kept.length + 2}` +
        `${usable.length - extra > 1 ? ` to ${usable.length + 1}` : ''} ${usable.length - extra > 1 ? 'were' : 'was'} left out.`,
      );
    }
    kept.forEach((patch, i) => {
      frames.push({
        copy: patch,
        ms,
        slide: i + 2,
        tag: `slide ${i + 2}`,
        label: `Slide ${i + 2}`,
      });
    });

    if (frames.length < 2) {
      return {
        kind, frames: [], loop: 1, cycleMs: 0, totalMs: 0, fps: 0, adjustments,
        refused:
          'A text animation needs a second slide. Slide 1 is the ad as it already ' +
          'is, so with nothing typed on slide 2 there is nothing to animate.',
      };
    }
  } else {
    if (ctx.hasCta === false) {
      return {
        kind, frames: [], loop: 1, cycleMs: 0, totalMs: 0, fps: 0, adjustments,
        refused:
          'This layout draws no button on this size, so there is no button to ' +
          'animate. Animate the text instead, or pick a size that carries one.',
      };
    }
    const base = HEX.test(String(ctx.baseCtaFill ?? '')) ? String(ctx.baseCtaFill) : null;
    const asked = String(spec.highlight ?? '').trim();
    const highlight = HEX.test(asked) ? asked : base ? pulseColor(base) : null;
    if (asked && !HEX.test(asked)) {
      adjustments.push(
        `"${asked}" is not a color this renderer reads, so the pulse was computed ` +
        'from the button’s own fill instead.',
      );
    }
    if (!highlight) {
      return {
        kind, frames: [], loop: 1, cycleMs: 0, totalMs: 0, fps: 0, adjustments,
        refused:
          'There is no button color to work from on this size, so a pulse would ' +
          'have nothing to pulse between.',
      };
    }

    const wantFrames = clampNum(
      Math.round(Number(spec.frames ?? DEFAULTS.buttonFrames)) || DEFAULTS.buttonFrames,
      3,
      ANIMATION_RULES.maxFrames,
    );
    if (Number(spec.frames) > ANIMATION_RULES.maxFrames) {
      adjustments.push(
        `${spec.frames} frames were asked for; ${ANIMATION_RULES.maxFrames} is the most used here.`,
      );
    }

    // A triangle: the fill rises to the highlight and comes back, so the loop
    // joins to itself without a jump. Interpolating rather than switching
    // between two flat colors is what makes it read as a pulse and not a
    // blink, which on a 200ms floor is the difference between "notice this"
    // and "something is broken".
    const peak = Math.floor(wantFrames / 2);
    for (let i = 0; i < wantFrames; i++) {
      const t = peak === 0 ? 0 : i <= peak ? i / peak : (wantFrames - i) / (wantFrames - peak);
      const fill = base ? mix(base, highlight, t) : highlight;
      frames.push({
        style: { cta: { bg: fill } },
        ms,
        slide: 1,
        tag: `frame ${i + 1}`,
        label: i === peak ? 'Button, brightest' : `Button, frame ${i + 1}`,
      });
    }
  }

  const cycleMs = frames.reduce((n, f) => n + f.ms, 0);
  const loop = loopsWithin(cycleMs);
  return {
    kind,
    frames,
    loop,
    cycleMs,
    totalMs: cycleMs * loop,
    fps: Number((1000 / ms).toFixed(2)),
    adjustments,
  };
}

function mix(a: string, b: string, t: number): string {
  const [r1, g1, b1] = expand(a);
  const [r2, g2, b2] = expand(b);
  const k = clampNum(t, 0, 1);
  return toHex(r1 + (r2 - r1) * k, g1 + (g2 - g1) * k, b1 + (b2 - b1) * k);
}

/* ------------------------------------------------------ which sizes can run */

export interface AnimationSupport {
  supported: boolean;
  /** Present when it is not, and written for a person rather than a log. */
  reason?: string;
  maxFileBytes?: number;
}

/**
 * Does this placement take an animated file?
 *
 * Read out of the platform config, never decided here. The config is where the
 * format list already lives and `src/render.ts` already renders against it, so
 * a placement that gains or loses GIF is one edit rather than two that drift.
 */
export function animationSupport(platform: string, size: SizeKey): AnimationSupport {
  let cfg;
  try {
    cfg = getPlatform(platform);
  } catch {
    return { supported: false, reason: `There is no platform called "${platform}" here.` };
  }
  const rule = cfg.sizes[size];
  if (!rule) {
    return { supported: false, reason: `${cfg.label} does not run a ${size}.` };
  }
  if (!rule.formats.includes('gif')) {
    return {
      supported: false,
      reason:
        `${cfg.label} takes ${rule.formats.join(' and ')} at ${size} and not GIF, ` +
        'so this size ships as the static ad.',
      maxFileBytes: rule.maxFileBytes,
    };
  }
  return { supported: true, maxFileBytes: rule.maxFileBytes };
}

/** Every size of a platform that can carry one, in the config's own order. */
export function animatableSizes(platform: string): SizeKey[] {
  try {
    const cfg = getPlatform(platform);
    return (Object.keys(cfg.sizes) as SizeKey[]).filter(
      (s) => cfg.sizes[s]?.formats.includes('gif'),
    );
  } catch {
    return [];
  }
}

/* ------------------------------------------------------------ the encoding */

export interface GifResult {
  buffer: Buffer;
  bytes: number;
  /** True when even the last step of the ladder exceeded the limit. */
  overweight: boolean;
  attempts: number;
  /** What the winning step did, for the panel to print. */
  settings: string;
}

/**
 * The weight ladder, `src/raster.ts`'s in GIF terms.
 *
 * A GIF's levers are not a JPEG's. There is no quality: there is a palette (at
 * most 256 colors), how hard it dithers, and how different two frames have to
 * be before the second one re-encodes a pixel rather than reusing it. That
 * last lever is why this is cheap here and would not be for video: the two
 * motions offered leave the background byte-identical between frames, so the
 * encoder only ever pays for the words or the button.
 */
const GIF_LADDER: Array<{ colours?: number; dither?: number; interFrameMaxError?: number }> = [
  {},
  { colours: 128, dither: 0.8 },
  { colours: 96, dither: 0.7, interFrameMaxError: 4 },
  { colours: 64, dither: 0.6, interFrameMaxError: 8 },
  { colours: 32, dither: 0.5, interFrameMaxError: 16 },
];

function describe(step: (typeof GIF_LADDER)[number]): string {
  if (!Object.keys(step).length) return 'full palette';
  const bits = [`${step.colours} colors`];
  if (step.dither !== undefined) bits.push(`dither ${step.dither}`);
  if (step.interFrameMaxError !== undefined) bits.push(`frame reuse ${step.interFrameMaxError}`);
  return bits.join(', ');
}

/**
 * Frames in, one animated GIF out.
 *
 * Frames must be encoded images of identical dimensions — PNG is what the
 * render path already produces. `sharp`'s join is what assembles them; the
 * delays and the loop count come from the plan, which is the only place the
 * 30-second rule is applied, so nothing here can hand back an endless file.
 */
export async function encodeAnimation(input: {
  frames: Buffer[];
  plan: AnimationPlan;
  maxFileBytes: number;
}): Promise<GifResult> {
  const { frames, plan, maxFileBytes } = input;
  if (!frames.length) throw new Error('An animation needs at least one frame.');
  const delay = plan.frames.map((f) => f.ms);

  // `plan.loop` is HOW MANY TIMES THE SEQUENCE PLAYS, and sharp takes it that
  // way -- it writes `loop - 1` into the file's Netscape block, which is the
  // GIF format's count of iterations AFTER the first. Worth stating because
  // readers disagree about that block: one that treats it as "N more times"
  // plays `plan.loop` times, one that treats it as "N times" plays one fewer.
  // The arithmetic in planAnimation is done against the LARGER of those, so
  // `totalMs` can never understate what a browser will actually play -- which
  // is the direction that matters, since overstating costs nothing and
  // understating is how a file goes over 30 seconds while the panel says it
  // does not.


  let attempts = 0;
  let best: { buffer: Buffer; settings: string } | null = null;

  for (const step of GIF_LADDER) {
    attempts++;
    const buffer = await sharp(frames, { join: { across: 1, animated: true } })
      .gif({ loop: plan.loop, delay, ...step })
      .toBuffer();
    if (!best || buffer.length < best.buffer.length) {
      best = { buffer, settings: describe(step) };
    }
    if (buffer.length <= maxFileBytes) {
      return {
        buffer, bytes: buffer.length, overweight: false, attempts,
        settings: describe(step),
      };
    }
  }

  return {
    buffer: best!.buffer,
    bytes: best!.buffer.length,
    overweight: true,
    attempts,
    settings: best!.settings,
  };
}

/* ----------------------------------------------------------------- the QA */

/**
 * The checks that are about the motion rather than about a frame.
 *
 * Everything a static ad is checked for is still checked, once per frame, in
 * `src/render.ts` — a second slide is different copy in the same box and can
 * overflow it, collide with the button or lose its contrast where slide 1 was
 * fine. Running QA on frame one only is how a broken ad passes.
 *
 * These four are what a still frame cannot answer.
 */
export function animationFindings(
  plan: AnimationPlan,
  gif: Pick<GifResult, 'bytes' | 'overweight' | 'settings'>,
  rule: Pick<PlatformSizeRule, 'maxFileBytes'>,
): QaFinding[] {
  const out: QaFinding[] = [];
  const add = (check: string, status: QaFinding['status'], detail: string) =>
    out.push({ check, status, detail });

  const slowest = Math.min(...plan.frames.map((f) => f.ms));
  if (slowest < ANIMATION_RULES.minFrameMs) {
    add('animation:fps', 'fail',
      `a frame is held for ${slowest}ms, which is ${(1000 / slowest).toFixed(1)} frames a second — ` +
      'Google requires 5 or slower');
  } else {
    add('animation:fps', 'pass', `${plan.fps} frames a second, at or under Google’s 5`);
  }

  if (plan.loop < 1) {
    add('animation:loop', 'fail',
      'this file would loop for ever; Google requires the animation to stop');
  } else {
    add('animation:loop', 'pass',
      `plays ${plan.loop} time${plan.loop === 1 ? '' : 's'} and stops`);
  }

  if (plan.totalMs > ANIMATION_RULES.maxTotalMs) {
    add('animation:duration', 'fail',
      `${(plan.totalMs / 1000).toFixed(1)}s of animation, over Google’s 30-second limit`);
  } else {
    add('animation:duration', 'pass',
      `${(plan.totalMs / 1000).toFixed(1)}s in total (${(plan.cycleMs / 1000).toFixed(1)}s × ${plan.loop})`);
  }

  if (gif.overweight) {
    add('animation:weight', 'fail',
      `${(gif.bytes / 1024).toFixed(0)} KB at ${gif.settings}, over the ` +
      `${(rule.maxFileBytes / 1024).toFixed(0)} KB this placement allows — ` +
      'shorten the animation or drop a slide');
  } else {
    add('animation:weight', 'pass',
      `${(gif.bytes / 1024).toFixed(0)} KB of ${(rule.maxFileBytes / 1024).toFixed(0)} KB (${gif.settings})`);
  }

  return out;
}

/**
 * A slide that says what the slide before it said.
 *
 * Not a rule anybody publishes — it is an animation that ran and changed
 * nothing, which reads on the proof as a GIF that failed to build. Warned
 * rather than failed: repeating a line deliberately to hold it on screen for
 * longer is a real thing to want.
 */
export function repeatedSlides(resolved: CopySet[], kind: AnimationPlan['kind'] = 'text'): QaFinding[] {
  const out: QaFinding[] = [];
  // A button pulse is one slide four times over. Every frame says the same
  // thing BY DESIGN, so asking this question of it produces three warnings
  // about the feature working correctly -- which is how a panel of warnings
  // stops being read.
  if (kind !== 'text') return out;
  for (let i = 1; i < resolved.length; i++) {
    const a = JSON.stringify(resolved[i - 1]);
    const b = JSON.stringify(resolved[i]);
    if (a === b) {
      out.push({
        check: 'animation:slides',
        status: 'warn',
        detail: `slide ${i + 1} says exactly what slide ${i} says, so nothing changes on screen`,
      });
    }
  }
  return out;
}
