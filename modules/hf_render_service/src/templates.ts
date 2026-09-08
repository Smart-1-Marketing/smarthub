/**
 * The templates this service holds, and the shapes they accept.
 *
 * `hub/hyperframes.py` carries its own copy of the template names
 * (`TEMPLATES`) and refuses an unknown one *before* it ever reaches this
 * service — "a 404 from a service is indistinguishable from the service
 * being down." That means this file's list can never be read by the Hub; it
 * exists so a request that somehow arrives with a name neither side knows is
 * refused here too, rather than falling through to whichever template
 * happens to be first. `tests/templates.test.ts` pins the two spellings
 * (`paint-animation`, `vox-explainer`) against the literal strings in
 * `hub/hyperframes.py`, so the day one side is renamed the other fails
 * rather than silently refusing every job.
 */

export type TemplateName = "paint-animation" | "vox-explainer";

export const TEMPLATE_NAMES: readonly TemplateName[] = [
  "paint-animation",
  "vox-explainer",
];

export function isKnownTemplate(name: string): name is TemplateName {
  return (TEMPLATE_NAMES as readonly string[]).includes(name);
}

/** width/height for each output format id. Mirrors
 * `modules/commercial_builder/config.OUTPUT_FORMATS` — one description of
 * what "16:9" means, or the two sides drift on what a delivered frame is. */
export const FORMAT_DIMENSIONS: Record<string, { width: number; height: number }> = {
  "16:9": { width: 1920, height: 1080 },
  "9:16": { width: 1080, height: 1920 },
  "1:1": { width: 1080, height: 1080 },
};
export const DEFAULT_FORMAT = "16:9";

export function dimensionsFor(formatId: string): { width: number; height: number } {
  return FORMAT_DIMENSIONS[formatId] || FORMAT_DIMENSIONS[DEFAULT_FORMAT];
}

/** The three paint-animation styles. Closed, matching
 * `hub/hyperframes.PAINT_STYLES` — a style outside this set reaches the
 * sketch with no branch for it and silently falls back, so it is refused
 * before rendering starts instead. */
export const PAINT_STYLES = ["handwriting", "paint_on", "living_painting"] as const;
export type PaintStyle = (typeof PAINT_STYLES)[number];
export const DEFAULT_PAINT_STYLE: PaintStyle = "handwriting";

export const PAINT_MIN_SECONDS = 1.0;
export const PAINT_MAX_SECONDS = 30.0;

/** The four beat treatments. Matches
 * `modules/commercial_builder/vox_spec.TREATMENTS`. */
export const BEAT_TREATMENTS = ["statement", "collage", "data", "quote"] as const;
export type BeatTreatment = (typeof BEAT_TREATMENTS)[number];
export const DEFAULT_TREATMENT: BeatTreatment = "collage";

export const VOX_MIN_SECONDS = 60;
export const VOX_MAX_SECONDS = 90;
export const VOX_MIN_BEATS = 4;
export const VOX_MAX_BEATS = 10;

export const FPS = 24;
