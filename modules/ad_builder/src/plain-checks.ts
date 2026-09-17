/**
 * The checks, for a person.
 *
 * `qa.ts` measures. Its findings are exact and they read like this: "below
 * 4.5:1 — headline 2.1:1, cta 3.0:1", or "headline is only 1.12x the
 * supporting text; aim for 1.4x or more". Every number in those sentences is
 * right, and no operator laying out an ad knows what a contrast ratio is or
 * why 1.4x is the line. The panel of checks read as a wall of jargon, and a
 * panel people cannot read is a panel they stop reading -- which is the
 * failure `QR_CODE_RULES` names about a warning that fires on every ad.
 *
 * So every finding gets a second reading, alongside the measured one rather
 * than instead of it: what is wrong in words, and what to do about it. The
 * measured `detail` stays on the finding for the proof, the manifest and the
 * tests; `plain` is what the build screen draws.
 *
 * Two layers, and the split matters.
 *
 *   **`explainFinding()` is deterministic and always there.** It knows every
 *   check `qa.ts` emits, turns the measurement into a sentence and names the
 *   control that fixes it. Where the fix is one setting -- "make the headline
 *   48px", "use the white logo" -- it carries an `apply` the screen can do in
 *   one press and undo in another. No model, no key, no wait, and it cannot
 *   be wrong about which check it is talking about because it reads the
 *   check's own name.
 *
 *   **`adviseFindings()` asks the model to do it better for THIS ad.** It is
 *   handed the deterministic reading as a floor, plus the copy, the type sizes
 *   the composer actually fitted, the palette and the layout, and asked for
 *   the sentence a senior designer would say to a junior -- and for one
 *   concrete change, in the same `apply` shape, so "would you like to see what
 *   that looks like?" has an answer. It falls back to the floor when there is
 *   no key or the model is slow, and it NEVER invents a check: it may reword
 *   a finding and it may not add one.
 *
 * The `apply` vocabulary is deliberately small and is the build screen's own:
 * a block style (size, color, w, y, scale), a copy field, the logo tone, or a
 * layout family. Anything the model proposes outside it is dropped rather
 * than passed on, because a suggestion the screen cannot perform is a button
 * that does nothing.
 */

import type { QaFinding } from './types';
import { contrastRatio, hexLuminance } from './raster';

/** One change the build screen knows how to make -- and undo. */
export type Suggestion =
  | { kind: 'style'; block: 'headline' | 'support' | 'offer' | 'cta' | 'trust' | 'logo' | 'panel';
      prop: 'size' | 'color' | 'w' | 'y' | 'scale' | 'bg' | 'fill' | 'opacity'; value: string | number }
  | { kind: 'copy'; field: 'headline' | 'support' | 'offer' | 'cta' | 'trust'; value: string }
  | { kind: 'logo-tone'; tone: 'white' | 'black' | 'auto' }
  | { kind: 'layout'; family: string };

export interface PlainAdvice {
  /** The problem, as a short heading. "The headline is hard to read." */
  title: string;
  /** One or two sentences on what is wrong, with no ratios or pixel maths. */
  what: string;
  /** What to do about it, naming the control. */
  how: string;
  /** The one-press version of `how`, when there is one. */
  apply?: Suggestion;
  /** How `apply` reads on a button. "Make the headline 48px". */
  applyLabel?: string;
  /** 'rule' for the deterministic reading, 'ai' when the model wrote it. */
  source: 'rule' | 'ai';
}

/** What the explainer knows about the ad, beyond the finding itself. All of
 *  it optional: the proof page has none of it and still gets a sentence. */
export interface ExplainContext {
  size?: string;
  /** Px at 1x, per block, as the composer fitted them. */
  fontSizes?: Partial<Record<string, number>>;
  copy?: Partial<Record<string, string>>;
  /** Whether the ad has a photo behind it: the fallback for which ink to
   *  suggest when the finding carries no measurement. */
  backgroundImage?: boolean;
  /** Families the renderer has, so a layout suggestion names a real one. */
  families?: string[];
  /** The brand's five roles as hex, so an ink suggestion is checked against
   *  what is actually behind the block before it is made. */
  brandColors?: Record<string, string>;
}

/**
 * Which brand ink to suggest for a block that does not read.
 *
 * The contrast check records the luminance it measured behind each low
 * block. With that and the brand's light and dark, this is a subtraction:
 * whichever of the two clears 4.5:1 is the answer, the better one when
 * neither does. Without the measurement it falls back to the old guess --
 * light over a photo, dark on a flat layout -- and says so in the label.
 */
export function inkSuggestion(
  role: string, ctx: ExplainContext, low?: Array<{ role: string; behind: number }>,
): { value: 'light' | 'dark'; ratio?: number; reads: boolean; measured: boolean } {
  const guess: 'light' | 'dark' = ctx.backgroundImage ? 'light' : 'dark';
  const entry = low?.find((l) => l.role === role);
  const colors = ctx.brandColors ?? {};
  const light = colors.light, dark = colors.dark;
  if (!entry || typeof entry.behind !== 'number' || !light || !dark) {
    return { value: guess, reads: true, measured: false };
  }
  let lightRatio = 0, darkRatio = 0;
  try {
    lightRatio = contrastRatio(hexLuminance(light), entry.behind);
    darkRatio = contrastRatio(hexLuminance(dark), entry.behind);
  } catch {
    return { value: guess, reads: true, measured: false };
  }
  const value: 'light' | 'dark' = lightRatio >= darkRatio ? 'light' : 'dark';
  const ratio = Math.max(lightRatio, darkRatio);
  return { value, ratio, reads: ratio >= 4.5, measured: true };
}

const ROLE_NAMES: Record<string, string> = {
  headline: 'headline',
  support: 'supporting line',
  offer: 'offer',
  trust: 'proof point',
  cta: 'button',
  logo: 'logo',
  hero: 'picture',
  panel: 'card behind the copy',
};

const CAP = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);
const roleName = (r: string) => ROLE_NAMES[r] ?? r;
const list = (roles: string[]) => {
  const names = roles.map(roleName);
  if (names.length <= 1) return names[0] ?? '';
  return names.slice(0, -1).join(', ') + ' and ' + names[names.length - 1];
};

/** "headline 2.1:1, cta 3.0:1" -> ['headline', 'cta'] */
function rolesIn(detail: string): string[] {
  const out: string[] = [];
  for (const role of Object.keys(ROLE_NAMES)) {
    if (new RegExp(`\\b${role}\\b`, 'i').test(detail)) out.push(role);
  }
  return out;
}

/** An animated finding is "check · slide-2" with "Slide 2: " on the detail. */
function frameOf(check: string): { base: string; slide?: string } {
  const m = check.match(/^(.*?)\s+·\s+(.+)$/);
  if (!m) return { base: check };
  return { base: m[1], slide: m[2].replace(/-/g, ' ') };
}

/**
 * The deterministic reading. One entry per check `qa.ts`, `animation.ts`,
 * `asset-quality.ts` and the review screen emit; anything unknown gets the
 * measured sentence back under a generic heading rather than nothing, so a
 * check added next month is still shown.
 */
export function explainFinding(f: QaFinding, ctx: ExplainContext = {}): PlainAdvice {
  const { base, slide } = frameOf(f.check);
  const d = f.detail ?? '';
  const sizes = ctx.fontSizes ?? {};
  const prefix = slide ? `On ${slide}: ` : '';
  const done = (a: Omit<PlainAdvice, 'source'>): PlainAdvice => ({
    ...a,
    title: prefix ? prefix + a.title.charAt(0).toLowerCase() + a.title.slice(1) : a.title,
    source: 'rule',
  });

  if (base.startsWith('overflow:')) {
    const role = base.slice('overflow:'.length);
    const max = f.fix?.maxWords;
    if (role === 'cta') {
      return done({
        title: 'The button text is too long.',
        what: 'The words do not fit inside the button on this size.',
        how: 'Use two short words, like "Call now" or "Get a quote".',
      });
    }
    return done({
      title: `The ${roleName(role)} is too long to fit.`,
      what: `On this size the ${roleName(role)} runs past the room the layout gives it, so part of it would be cut off.`,
      how: max ? `Shorten it to about ${max} words, or make its type a little smaller.`
               : `Shorten it, or make its type a little smaller.`,
    });
  }

  switch (base) {
    case 'contrast': {
      const roles = rolesIn(d).filter((r) => r !== 'panel' && r !== 'hero' && r !== 'logo');
      const who = roles.length ? list(roles) : 'some of the text';
      const first = roles[0];
      const low = (f.data?.low as Array<{ role: string; behind: number }> | undefined);
      const pick = first && first !== 'cta' ? inkSuggestion(first, ctx, low) : null;
      // A suggestion that the checks would refuse a second later is worse
      // than none: when neither brand ink clears the bar, the fix is the
      // overlay, and the button says so.
      const suggestInk = !!pick && (pick.reads || !pick.measured);
      return done({
        title: `${CAP(who)} ${roles.length > 1 ? 'are' : 'is'} hard to read against what is behind ${roles.length > 1 ? 'them' : 'it'}.`,
        what: 'The text color and the background are too close in tone, so the words fade into it.',
        how: first === 'cta'
          ? 'Pick a button color that stands out from the button text, in Text Boxes > Button.'
          : pick && pick.measured && !pick.reads
            ? `Neither brand ink reads well here. Darken the overlay behind the ${who} in Background, or move it onto the card.`
            : `Pick a lighter or darker text color for the ${who} in Text Boxes, or darken the overlay behind it in Background.`,
        apply: suggestInk
          ? { kind: 'style', block: first as any, prop: 'color', value: pick!.value }
          : undefined,
        applyLabel: suggestInk
          ? `Use the ${pick!.value} brand color on the ${roleName(first)}` +
            (pick!.measured ? '' : ' (a guess — the checks will say)')
          : undefined,
      });
    }
    case 'legibility': {
      const m = d.match(/"(\w+)"/);
      const role = m?.[1] ?? 'support';
      return done({
        title: `The ${roleName(role)} is too small to read on this size.`,
        what: 'It has been shrunk to fit and is now below the size the platform will accept.',
        how: `Shorten the ${roleName(role)} so it fits at a bigger size, or make its box wider in Text Boxes.`,
      });
    }
    case 'hierarchy': {
      const sup = sizes.support;
      const target = sup ? Math.ceil((sup * 1.5) / 2) * 2 : undefined;
      return done({
        title: 'The headline is not big enough next to the supporting line.',
        what: 'The two are nearly the same size, so nothing on the ad reads first.',
        how: target
          ? `Make the headline bigger (try ${target}px) or the supporting line smaller, in Text Boxes.`
          : 'Make the headline bigger or the supporting line smaller, in Text Boxes.',
        apply: target ? { kind: 'style', block: 'headline', prop: 'size', value: target } : undefined,
        applyLabel: target ? `Make the headline ${target}px` : undefined,
      });
    }
    case 'focal-point': {
      return done({
        title: 'Too much is competing under the headline.',
        what: 'The offer, the proof point and the supporting line are all fighting for the same small space.',
        how: 'Leave one of them empty on this size (Copy, with "this size only").',
      });
    }
    case 'collision': {
      const roles = rolesIn(d);
      return done({
        title: `${CAP(list(roles)) || 'Two things'} are printed on top of each other.`,
        what: 'Something was moved onto something else, so both are unreadable where they overlap.',
        how: 'Move one of them with the arrows in Text Boxes or Logo, or press "Put it back".',
      });
    }
    case 'safe-area': {
      const roles = rolesIn(d);
      return done({
        title: `${CAP(list(roles)) || 'Something'} sits too close to the edge.`,
        what: 'Anything near the edge can be clipped or covered when the ad is shown.',
        how: 'Nudge it inward with the arrows, or press "Put it back".',
      });
    }
    case 'safe-zone': {
      const roles = rolesIn(d);
      return done({
        title: `${CAP(list(roles)) || 'Something'} sits where the app's own buttons go.`,
        what: 'On Stories and Reels the top and bottom of the screen are covered by the app itself.',
        how: 'Move it toward the middle of the ad.',
      });
    }
    case 'logo-contrast': {
      const wantWhite = /white/i.test(d);
      return done({
        title: 'The logo is hard to see against the background.',
        what: 'Part of the logo is close to the color behind it, so it disappears.',
        how: wantWhite
          ? 'Use a white version of the logo on this size (Logo > Logo color).'
          : 'Use a black version of the logo on this size (Logo > Logo color).',
        apply: { kind: 'logo-tone', tone: wantWhite ? 'white' : 'black' },
        applyLabel: wantWhite ? 'Use a white logo' : 'Use a black logo',
      });
    }
    case 'logo-plate':
      return done({
        title: 'The logo has a solid box around it.',
        what: 'The logo file is not transparent, so its background shows as a rectangle on the ad.',
        how: 'Upload a version with a transparent background in Logo, or pick one from the gallery.',
      });
    case 'logo': {
      if (/no logo/i.test(d)) {
        return done({
          title: 'There is no logo on this ad.',
          what: 'Every ad has to show who it is from.',
          how: 'Add one in Logo.',
        });
      }
      return done({
        title: 'The logo takes up too much of the ad.',
        what: 'A logo this big crowds out the message.',
        how: 'Make it smaller with the Size slider in Logo.',
        apply: { kind: 'style', block: 'logo', prop: 'scale', value: 0.7 },
        applyLabel: 'Make the logo 30% smaller',
      });
    }
    case 'cta': {
      if (/supplies its own/i.test(d)) {
        return done({
          title: 'This placement adds its own button.',
          what: 'The platform draws a button under the ad, so one baked into the picture shows twice.',
          how: 'Leave the call to action empty for this size.',
        });
      }
      return done({
        title: 'There is no call to action.',
        what: 'Nothing on the ad tells people what to do next.',
        how: 'Add a short one in Copy, like "Call now" or "Book today".',
      });
    }
    case 'source-resolution':
      return done({
        title: 'The picture will look soft.',
        what: 'The photo is being stretched bigger than it really is, so it will look blurry.',
        how: 'Choose a bigger version of the photo in Background.',
      });
    case 'text-coverage':
      return done({
        title: 'Note: there is a lot of text over the picture.',
        what: 'Meta may show text-heavy images to fewer people. It is not a rejection and does not hold this size.',
        how: 'If reach matters more than the copy, shorten it or pick a layout that gives the picture more room.',
      });
    case 'pressure-language': {
      const m = d.match(/\("([^"]+)"/);
      return done({
        title: `The wording "${m?.[1] ?? ''}" will be refused.`.replace('""', 'here'),
        what: 'Hard-sell phrases like this are turned down by the ad platforms.',
        how: 'Say it another way in Copy.',
      });
    }
    case 'dimensions':
      return done({
        title: 'The finished file is the wrong size.',
        what: 'It did not come out at the pixel size this placement needs.',
        how: 'Render again. If it happens twice, tell the team.',
      });
    case 'file-weight':
      return done({
        title: 'The finished file is too big for this placement.',
        what: 'The platform will not take a file this heavy.',
        how: 'Use a simpler photo, or a layout with less picture.',
      });
    case 'assets':
      return done({
        title: 'A picture or logo file is missing.',
        what: 'Something the ad needs could not be found on disk.',
        how: 'Choose the background and logo again.',
      });
    case 'placeholder-artwork':
      return done({
        title: 'This still uses a placeholder image.',
        what: 'The picture or logo is a stand-in, not the client’s.',
        how: 'Choose a real photo in Background, or the client’s logo in Logo.',
      });
    case 'carry':
      return done({
        title: 'This size took its adjustments from another size and they did not all fit.',
        what: d.replace(/^carried from /, 'It was carried from ') + '.',
        how: 'Look at it, then nudge anything that is off with the arrows. Changes here stay on this size.',
      });
    case 'manual-artwork-review':
      return done({
        title: 'This size ships a hand-made file.',
        what: 'A replacement file was uploaded for this size, so the settings here do not change it.',
        how: 'Check the file itself, or remove it above the preview to go back to the built version.',
      });
    case 'copy-review':
    case 'Copy needs review':
      return done({
        title: 'The copy was not written by the model.',
        what: 'The writing model was not available, so the words came straight from the form.',
        how: 'Read them over, or press "Draft the copy from their website" to try again.',
      });
    case 'render':
    case 'preview':
      return done({
        title: 'The ad could not be built.',
        what: d || 'The renderer did not answer.',
        how: 'Change something and try again. If it keeps happening, tell the team.',
      });
    case 'animation:weight':
      return done({
        title: 'The animated file is too heavy.',
        what: 'Google refuses an animated ad over its weight limit.',
        how: 'Use fewer frames, or a simpler picture.',
      });
    case 'animation:fps':
    case 'animation:loop':
    case 'animation:duration':
      return done({
        title: 'The animation runs too fast or too long.',
        what: d,
        how: 'Slow the frames down or shorten the loop in the animator.',
      });
    case 'animation:slides':
      return done({
        title: 'Two slides say the same thing.',
        what: 'A slide that repeats the one before it adds nothing.',
        how: 'Give the second slide different words, or drop it.',
      });
    default:
      return done({
        title: CAP(base.replace(/[-_:]/g, ' ')) + '.',
        what: d,
        how: '',
      });
  }
}

/** Every finding, with its plain reading attached. Passes are left alone. */
export function withPlain(findings: QaFinding[], ctx: ExplainContext = {}): QaFinding[] {
  // A note is explained like anything else; only its weight differs.
  return findings.map((f) => (f.status === 'pass' ? f : { ...f, plain: explainFinding(f, ctx) }));
}

/* ----------------------------------------------------------------- the model */

export interface AdviceInput extends ExplainContext {
  findings: QaFinding[];
  brandColors?: Record<string, string>;
  layoutFamily?: string;
  platform?: string;
}

export interface Advice {
  /** One or two sentences about the ad as a whole. */
  summary: string;
  /** In the order of `findings`, passes excluded. */
  items: Array<QaFinding & { plain: PlainAdvice }>;
  /** The first item that carries an `apply`, for the "would you like to see
   *  what this looks like?" question on the way to the next size. */
  lead?: PlainAdvice;
  source: 'ai' | 'rule';
  warnings: string[];
}

const BLOCKS = ['headline', 'support', 'offer', 'cta', 'trust', 'logo', 'panel'];
const STYLE_PROPS = ['size', 'color', 'w', 'y', 'scale', 'bg', 'fill', 'opacity'];
const COPY_FIELDS = ['headline', 'support', 'offer', 'cta', 'trust'];

/**
 * Only a suggestion the build screen can perform gets through. The model is
 * told the vocabulary and still gets it wrong now and then; a button that
 * does nothing is worse than no button.
 */
export function acceptSuggestion(raw: unknown, families: string[] = []): Suggestion | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const s = raw as Record<string, unknown>;
  if (s.kind === 'style') {
    const block = String(s.block ?? '') as Extract<Suggestion, { kind: 'style' }>['block'];
    const prop = String(s.prop ?? '') as Extract<Suggestion, { kind: 'style' }>['prop'];
    if (!BLOCKS.includes(block) || !STYLE_PROPS.includes(prop)) return undefined;
    const v = s.value;
    if (prop === 'color' || prop === 'bg' || prop === 'fill') {
      const value = String(v ?? '').trim();
      if (!/^(primary|secondary|accent|light|dark|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3})$/.test(value)) return undefined;
      return { kind: 'style', block, prop, value };
    }
    const n = Number(v);
    if (!Number.isFinite(n)) return undefined;
    if (prop === 'size' && (n < 8 || n > 200)) return undefined;
    if ((prop === 'scale' || prop === 'opacity') && (n <= 0 || n > 3)) return undefined;
    return { kind: 'style', block, prop, value: Math.round(n * 100) / 100 };
  }
  if (s.kind === 'copy') {
    const field = String(s.field ?? '');
    const value = String(s.value ?? '').trim();
    if (!COPY_FIELDS.includes(field) || !value || value.length > 120) return undefined;
    return { kind: 'copy', field: field as any, value };
  }
  if (s.kind === 'logo-tone') {
    const tone = String(s.tone ?? '');
    if (!['white', 'black', 'auto'].includes(tone)) return undefined;
    return { kind: 'logo-tone', tone: tone as any };
  }
  if (s.kind === 'layout') {
    const family = String(s.family ?? '');
    if (!families.includes(family)) return undefined;
    return { kind: 'layout', family };
  }
  return undefined;
}

function ruleAdvice(input: AdviceInput, warnings: string[]): Advice {
  const items = input.findings
    .filter((f) => f.status !== 'pass')
    .map((f) => ({ ...f, plain: f.plain ?? explainFinding(f, input) }));
  const fails = items.filter((i) => i.status === 'fail').length;
  const notes = items.filter((i) => i.status === 'info').length;
  const looks = items.length - fails - notes;
  const summary = !items.length
    ? 'Everything checks out on this size.'
    : fails
      ? `${fails} thing${fails === 1 ? '' : 's'} must be fixed before this size can ship` +
        (looks ? `, and ${looks} ${looks === 1 ? 'is' : 'are'} worth a look.` : '.')
      : looks
        ? `Nothing blocks this size. ${looks} thing${looks === 1 ? '' : 's'} worth a look.`
        : 'Nothing blocks this size.';
  return {
    summary, items,
    lead: items.find((i) => i.plain.apply && i.status !== 'info')?.plain,
    source: 'rule', warnings,
  };
}

/**
 * The model's reading of the same findings, for this ad.
 *
 * It is handed the deterministic reading and asked to improve on it, not to
 * replace it: the number of items and their order are fixed by the findings,
 * and an item the model leaves out keeps the rule's words. A suggestion is
 * accepted only in the screen's own vocabulary.
 */
export async function adviseFindings(
  input: AdviceInput,
  opts: { apiKey?: string; fetchImpl?: typeof fetch; timeoutMs?: number; model?: string } = {},
): Promise<Advice> {
  const warnings: string[] = [];
  const floor = ruleAdvice(input, warnings);
  if (!floor.items.length) return floor;
  const apiKey = opts.apiKey ?? process.env.OPENAI_API_KEY;
  if (!apiKey) {
    warnings.push('No OpenAI key is configured, so these are the built-in explanations rather than advice written for this ad.');
    return floor;
  }

  const families = input.families ?? [];
  const sys = [
    'You are a senior display-ad designer explaining automated checks to a colleague who does not know',
    'design terms. Never mention contrast ratios, luminance, px maths, WCAG or "hierarchy". Say what a',
    'viewer would notice and what one change would fix it. Short sentences. American English.',
    'You are given each finding with a plain reading already written. Improve it for THIS ad using the',
    'copy, the fitted type sizes and the colors given. Keep the same number of items, in the same order,',
    'each carrying its "check" name unchanged. Never add a finding. Never contradict a measurement.',
    'For each item give: title (one short sentence naming the problem), what (one or two sentences),',
    'how (one sentence naming the control: Copy, Layout, Background, Type/Fonts, Text Boxes or Logo),',
    'and apply: ONE concrete change in exactly one of these shapes, or null:',
    '  {"kind":"style","block":"headline|support|offer|cta|trust|logo|panel","prop":"size|color|w|y|scale|bg|fill|opacity","value":<number or brand role primary|secondary|accent|light|dark or #hex>}',
    '  {"kind":"copy","field":"headline|support|offer|cta|trust","value":"<shorter text>"}',
    '  {"kind":"logo-tone","tone":"white|black|auto"}',
    `  {"kind":"layout","family":"${families.join('|') || 'T01'}"}`,
    'Type sizes are px at 1x, 8-200. A "size" suggestion should be a round number a person would type.',
    'Also give applyLabel: how the change reads on a button, e.g. "Make the headline 48px".',
    'Respond ONLY with JSON: {"summary":"","items":[{"check":"","title":"","what":"","how":"","apply":null,"applyLabel":""}]}',
  ].join('\n');

  const user = JSON.stringify({
    size: input.size, platform: input.platform, layoutFamily: input.layoutFamily,
    photoBackground: !!input.backgroundImage,
    copy: input.copy, fittedTypeSizesPx: input.fontSizes, brandColors: input.brandColors,
    findings: floor.items.map((i) => ({
      check: i.check, status: i.status, measured: i.detail,
      plain: { title: i.plain.title, what: i.plain.what, how: i.plain.how, apply: i.plain.apply ?? null },
    })),
  });

  const doFetch = opts.fetchImpl ?? fetch;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 25_000);
  try {
    const res = await doFetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({
        model: opts.model ?? process.env.OPENAI_COPY_MODEL ?? 'gpt-4o-mini',
        temperature: 0.3,
        messages: [{ role: 'system', content: sys }, { role: 'user', content: user }],
        response_format: { type: 'json_object' },
      }),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`OpenAI ${res.status}`);
    const data: any = await res.json();
    const parsed = JSON.parse(String(data?.choices?.[0]?.message?.content ?? '{}'));
    const byCheck = new Map<string, any>();
    for (const it of Array.isArray(parsed.items) ? parsed.items : []) {
      if (it && typeof it.check === 'string' && !byCheck.has(it.check)) byCheck.set(it.check, it);
    }
    const items = floor.items.map((i) => {
      const ai = byCheck.get(i.check);
      if (!ai) return i;
      const apply = acceptSuggestion(ai.apply, families) ?? i.plain.apply;
      const plain: PlainAdvice = {
        title: String(ai.title || i.plain.title).trim(),
        what: String(ai.what || i.plain.what).trim(),
        how: String(ai.how || i.plain.how).trim(),
        apply,
        applyLabel: apply ? String(ai.applyLabel || i.plain.applyLabel || 'Try it').trim() : undefined,
        source: 'ai',
      };
      return { ...i, plain };
    });
    return {
      summary: String(parsed.summary || floor.summary).trim(),
      items,
      lead: items.find((i) => i.plain.apply && i.status !== 'info')?.plain,
      source: 'ai',
      warnings,
    };
  } catch (e: any) {
    warnings.push(`The advice model did not answer (${e?.message ?? e}), so these are the built-in explanations.`);
    return floor;
  } finally {
    clearTimeout(timer);
  }
}
