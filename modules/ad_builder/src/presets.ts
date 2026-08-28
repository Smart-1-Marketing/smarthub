/**
 * A client's finished setup, kept so the next ad for them is a form fill.
 *
 * The second ad for a client is the same ad with a different offer on it.
 * Everything that took the time -- the brand colours, the logo, which layout
 * family carries the message, where the picture sits and how hard the overlay
 * is pushed -- is settled, and settling it again from the intake form is how a
 * seasonal promo for an eleven-year client costs what a new client costs.
 *
 * A preset is that settled half, saved off a build somebody has already
 * approved, plus the named slots the next ad fills in.
 *
 * ---------------------------------------------------------------------------
 * Why this is not called a template
 *
 * `src/templates/*.json` are the layout families -- T01, T02 -- and
 * `getTemplate()`, `loadTemplates()` and `TemplateSpec` all mean one of those.
 * A second thing in this service called a template would make "which template"
 * an ambiguous question in every conversation and every function signature
 * downstream, which is the failure CLAUDE.md names about two blueprints
 * offering a template of the same name. The build spec calls these brand
 * templates; the screen can say "saved setup" and the code says preset.
 * ---------------------------------------------------------------------------
 *
 * Rules, each of which is a way this goes quietly wrong:
 *
 *   **A preset carries the design, never the campaign.** The requestId, the
 *   campaign name and the approvals belong to the build it was saved from. A
 *   preset that carried them would file every ad made from it against one
 *   campaign record, and the second ad would read as a revision of the first.
 *
 *   **The client is stored as a name and a domain, never a derived key.** The
 *   Hub's own rule, from hub/client_key.py: a stored key outlives the thing it
 *   was derived from, and a client renamed upstream then has a preset filed
 *   under a name nothing joins to.
 *
 *   **A field is editable because it was saved as editable.** Not because it
 *   is non-empty today. An offer that happens to be blank on the build a
 *   preset was cut from is still the slot the next ad fills, and a preset that
 *   inferred its own fields would silently lose that slot.
 *
 *   **A slot the layout does not draw is refused at save time, by name.** The
 *   proof-point failure this module already carries: a Proof point field was
 *   offered on every size, saved, word-counted, and drawn by no template at
 *   all. Offering a preset field for a role its own family never renders is
 *   the same control that does nothing, one level further out.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import { randomUUID } from 'node:crypto';
import type { Brand, CopySet, CreativeConcept, SizeKey } from './types';
import { getTemplate } from './registry';
import { slug } from './cloudinary';

/** The copy roles a preset may expose. Order is the order a form draws them. */
export const FIELD_ROLES = ['headline', 'support', 'offer', 'trust', 'cta'] as const;
export type FieldRole = (typeof FIELD_ROLES)[number];

export interface PresetField {
  role: FieldRole;
  /** What the form calls it, and the CSV column header. Defaults to the role. */
  label: string;
  /** Carried into a new ad when the field is left blank. */
  fallback?: string;
}

export interface Preset {
  id: string;
  name: string;
  /** Who it belongs to, by name and domain — never a derived key. */
  client: string;
  domain?: string;

  /** The settled half. */
  brand: Brand;
  layoutFamily: string;
  /** Everything about the concept except the copy: background, hero, style
   *  overrides, reverse logo. Copied verbatim so a preset renders identically
   *  to the build it was cut from. */
  design: Omit<CreativeConcept, 'conceptId' | 'name' | 'copy' | 'layoutFamily'>;
  /** Copy that is NOT a filled-in slot travels as-is — a standing disclaimer,
   *  a tagline the client never changes. Per-size overrides come too, or the
   *  320x50's short headline is lost on every ad made from this. */
  copy: CreativeConcept['copy'];

  /** The slots the next ad fills in. */
  fields: PresetField[];
  /** Platforms the build this came from was bought on, as the default. */
  platforms: string[];

  createdAt: string;
  createdBy?: string;
  /** The build this was cut from, for "where did this come from". */
  sourceRequestId?: string;
  sourceConceptId?: string;
}

export class PresetStore {
  readonly dir: string;

  constructor(baseDir: string) {
    this.dir = path.join(baseDir, 'presets');
    fs.mkdirSync(this.dir, { recursive: true });
  }

  private file(id: string): string {
    return path.join(this.dir, `${id}.json`);
  }

  get(id: string): Preset | null {
    const f = this.file(id);
    if (!fs.existsSync(f)) return null;
    try {
      return JSON.parse(fs.readFileSync(f, 'utf8')) as Preset;
    } catch {
      return null;
    }
  }

  /**
   * Presets for a client, newest first.
   *
   * Matched on the exact normalised name or the domain, never a substring:
   * "Riverside HVAC" must not collect "Riverside HVAC Supply", because a
   * preset offered under the wrong client renders another company's brand
   * colours and logo onto this one's ad. The Hub's client_key rule.
   */
  list(client?: string): Preset[] {
    const wanted = client ? norm(client) : null;
    const out: Preset[] = [];
    for (const f of fs.readdirSync(this.dir).filter((f) => f.endsWith('.json'))) {
      try {
        const p = JSON.parse(fs.readFileSync(path.join(this.dir, f), 'utf8')) as Preset;
        if (!wanted || norm(p.client) === wanted || (p.domain && norm(p.domain) === wanted)) {
          out.push(p);
        }
      } catch {
        /* a preset that will not parse is skipped rather than failing the list */
      }
    }
    return out.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  save(preset: Preset): Preset {
    fs.writeFileSync(this.file(preset.id), JSON.stringify(preset, null, 2));
    return preset;
  }

  remove(id: string): boolean {
    const f = this.file(id);
    if (!fs.existsSync(f)) return false;
    fs.unlinkSync(f);
    return true;
  }
}

function norm(s: string): string {
  return String(s ?? '').trim().toLowerCase().replace(/\s+/g, ' ');
}

/**
 * Cut a preset from a concept somebody has built.
 *
 * Returns `refused` rather than throwing for a field the family cannot draw,
 * so the caller can save the rest and name what it dropped -- the rule
 * knack_websites.py works to for a value Knack would refuse. Dropping it
 * silently would put a slot on the form that renders nowhere.
 */
export function presetFromConcept(input: {
  name: string;
  client: string;
  domain?: string;
  brand: Brand;
  concept: CreativeConcept;
  fields: { role: string; label?: string; fallback?: string }[];
  platforms: string[];
  createdBy?: string;
  sourceRequestId?: string;
}): { preset: Preset; refused: { role: string; reason: string }[] } {
  const { conceptId, name: _conceptName, copy, layoutFamily, ...design } = input.concept;

  // Which roles this family draws on at least one size. A role drawn by no
  // size in the family is a slot that renders nowhere.
  const drawn = new Set<string>();
  let familyKnown = true;
  try {
    const family = getTemplate(layoutFamily);
    for (const layout of Object.values(family.sizes)) {
      if (!layout) continue;
      for (const role of FIELD_ROLES) if ((layout as unknown as Record<string, unknown>)[role]) drawn.add(role);
    }
  } catch {
    // An unknown family is not a reason to refuse every field: the family is
    // named on the preset and the render will say so far more clearly than a
    // list of five identical refusals here would.
    familyKnown = false;
  }

  const fields: PresetField[] = [];
  const refused: { role: string; reason: string }[] = [];
  const seen = new Set<string>();
  for (const f of input.fields) {
    const role = String(f.role ?? '').trim() as FieldRole;
    if (!FIELD_ROLES.includes(role)) {
      refused.push({ role: String(f.role), reason: 'not a copy role this renderer draws' });
      continue;
    }
    if (seen.has(role)) {
      refused.push({ role, reason: 'listed twice; the first one was kept' });
      continue;
    }
    if (familyKnown && !drawn.has(role)) {
      refused.push({
        role,
        reason: `layout family ${layoutFamily} draws no ${role} on any size, so the slot would render nowhere`,
      });
      continue;
    }
    seen.add(role);
    fields.push({
      role,
      label: String(f.label ?? role).trim() || role,
      ...(f.fallback ? { fallback: String(f.fallback) } : {}),
    });
  }

  const preset: Preset = {
    id: `${slug(input.client)}-${slug(input.name)}-${randomUUID().slice(0, 6)}`,
    name: input.name,
    client: input.client,
    ...(input.domain ? { domain: input.domain } : {}),
    brand: input.brand,
    layoutFamily,
    design: design as Preset['design'],
    copy,
    fields,
    platforms: input.platforms,
    createdAt: new Date().toISOString(),
    ...(input.createdBy ? { createdBy: input.createdBy } : {}),
    ...(input.sourceRequestId ? { sourceRequestId: input.sourceRequestId } : {}),
    sourceConceptId: conceptId,
  };
  return { preset, refused };
}

/**
 * One ad from a preset: the settled design, with the slots filled in.
 *
 * `values` is keyed on the field ROLE, not the label -- a label is what a
 * screen and a CSV header call the slot and is meant to be edited, and keying
 * on it would mean renaming "Offer" to "Deal" silently stopped filling the
 * offer.
 *
 * Only named slots are written. Copy the preset carries that is not a slot --
 * a standing disclaimer, a tagline -- travels untouched, and a slot left blank
 * falls back to what the preset saved rather than rendering an empty box: an
 * ad with a missing line is worse than an ad with last month's line, and the
 * blank is visible on the proof either way.
 */
export function conceptFromPreset(
  preset: Preset,
  values: Partial<Record<FieldRole, string>>,
  opts: { conceptId: string; name: string } ,
): CreativeConcept {
  const base: CopySet = { headline: '', ...(preset.copy.default ?? {}) } as CopySet;
  for (const field of preset.fields) {
    const supplied = String(values[field.role] ?? '').trim();
    const value = supplied || field.fallback || base[field.role] || '';
    if (value) (base as unknown as Record<string, string>)[field.role] = value;
  }

  // Per-size overrides come across as they were saved, EXCEPT for a role this
  // ad has just been given a new value for: a 320x50 carrying last month's
  // short headline would quietly outrank the one somebody just typed, on the
  // one size where nobody looks first.
  const filled = new Set(preset.fields.map((f) => f.role));
  const copy: CreativeConcept['copy'] = { default: base };
  for (const [key, override] of Object.entries(preset.copy)) {
    if (key === 'default' || !override) continue;
    const kept: Record<string, string> = {};
    for (const [role, text] of Object.entries(override as Record<string, string>)) {
      if (filled.has(role as FieldRole)) continue;
      kept[role] = text;
    }
    if (Object.keys(kept).length) {
      (copy as Record<string, unknown>)[key as SizeKey] = kept;
    }
  }

  return {
    ...preset.design,
    conceptId: opts.conceptId,
    name: opts.name,
    layoutFamily: preset.layoutFamily,
    copy,
  } as CreativeConcept;
}
