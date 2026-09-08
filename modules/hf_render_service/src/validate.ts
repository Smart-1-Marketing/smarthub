/**
 * Defensive parameter validation.
 *
 * `hub/hyperframes.py`'s `paint_params()`/`vox_params()` already clamp
 * everything before this service ever sees it — but this file must not
 * trust that, for the same reason a route on this Hub never trusts a
 * browser: the composer that sends clean params today is not a guarantee
 * about what calls this service next year. Nothing here raises on bad
 * input; every function returns a clean value or a refusal string, because
 * a render service that crashes on a malformed body takes every job behind
 * it down with it.
 */

import {
  BEAT_TREATMENTS,
  BeatTreatment,
  DEFAULT_FORMAT,
  DEFAULT_PAINT_STYLE,
  DEFAULT_TREATMENT,
  FORMAT_DIMENSIONS,
  PAINT_MAX_SECONDS,
  PAINT_MIN_SECONDS,
  PAINT_STYLES,
  PaintStyle,
  VOX_MAX_BEATS,
  VOX_MIN_BEATS,
} from "./templates.js";

export interface PaintParams {
  text: string;
  imageUrl: string;
  style: PaintStyle;
  durationSeconds: number;
  format: string;
  brandColors: string[];
  background: string;
}

export interface Beat {
  headline: string;
  support: string;
  treatment: BeatTreatment;
  seconds: number;
  source: string;
  image_query: string;
}

export interface VoxParams {
  title: string;
  beats: Beat[];
  format: string;
  brandColors: string[];
  voiceTrackUrl: string;
}

function str(value: unknown, max: number): string {
  if (typeof value !== "string") return "";
  return value.trim().slice(0, max);
}

function isHttpUrl(value: string): boolean {
  if (!value) return true; // empty is allowed — "nothing to paint on" is a
  // refusal `paint_refusal()` already made on the Hub side, not this
  // service's job to re-litigate.
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

function cleanFormat(value: unknown): string {
  const f = typeof value === "string" ? value : "";
  return FORMAT_DIMENSIONS[f] ? f : DEFAULT_FORMAT;
}

function cleanColors(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((c): c is string => typeof c === "string" && /^#?[0-9a-fA-F]{3,8}$/.test(c.trim()))
    .slice(0, 6)
    .map((c) => c.trim());
}

export function validatePaintParams(body: unknown): { params: PaintParams } | { error: string } {
  if (typeof body !== "object" || body === null) {
    return { error: "The render request body is not an object." };
  }
  const b = body as Record<string, unknown>;

  const text = str(b.text, 240);
  const imageUrl = str(b.imageUrl, 2000);
  if (!isHttpUrl(imageUrl)) {
    return { error: "imageUrl is not a valid http(s) URL." };
  }
  if (!text && !imageUrl) {
    return { error: "A paint animation needs something to paint — text or an image." };
  }

  let style = str(b.style, 40) as PaintStyle;
  if (!(PAINT_STYLES as readonly string[]).includes(style)) style = DEFAULT_PAINT_STYLE;

  let seconds = Number(b.durationSeconds);
  if (!Number.isFinite(seconds)) seconds = 5;
  seconds = Math.round(Math.max(PAINT_MIN_SECONDS, Math.min(PAINT_MAX_SECONDS, seconds)) * 100) / 100;

  return {
    params: {
      text,
      imageUrl,
      style,
      durationSeconds: seconds,
      format: cleanFormat(b.format),
      brandColors: cleanColors(b.brandColors),
      background: str(b.background, 40),
    },
  };
}

function cleanBeat(raw: unknown, index: number): { beat: Beat } | { error: string } {
  if (typeof raw !== "object" || raw === null) {
    return { error: `beat ${index + 1} is not an object.` };
  }
  const r = raw as Record<string, unknown>;
  const headline = str(r.headline, 90);
  if (!headline) {
    return { error: `beat ${index + 1} has no headline.` };
  }
  let treatment = str(r.treatment, 20) as BeatTreatment;
  if (!(BEAT_TREATMENTS as readonly string[]).includes(treatment)) treatment = DEFAULT_TREATMENT;
  let seconds = Number(r.seconds);
  if (!Number.isFinite(seconds) || seconds <= 0) seconds = 0;
  return {
    beat: {
      headline,
      support: str(r.support, 180),
      treatment,
      seconds: Math.round(seconds * 100) / 100,
      source: str(r.source, 120),
      image_query: str(r.image_query, 80),
    },
  };
}

export function validateVoxParams(body: unknown): { params: VoxParams } | { error: string } {
  if (typeof body !== "object" || body === null) {
    return { error: "The render request body is not an object." };
  }
  const b = body as Record<string, unknown>;

  const rawBeats = Array.isArray(b.beats) ? b.beats : [];
  const beats: Beat[] = [];
  for (let i = 0; i < rawBeats.length; i++) {
    const result = cleanBeat(rawBeats[i], i);
    if ("beat" in result) beats.push(result.beat);
    // A beat this service cannot read is dropped rather than failing the
    // whole job — `vox_spec.validate()` already made this call on the Hub
    // side and named what it dropped; re-validating here is a second line
    // of defense, not a second opinion the caller has to reconcile with.
  }
  if (beats.length < VOX_MIN_BEATS) {
    return {
      error: `An explainer needs at least ${VOX_MIN_BEATS} usable beats and this has ${beats.length}.`,
    };
  }

  const voiceTrackUrl = str(b.voiceTrackUrl, 2000);
  if (!isHttpUrl(voiceTrackUrl)) {
    return { error: "voiceTrackUrl is not a valid http(s) URL." };
  }

  return {
    params: {
      title: str(b.title, 160),
      beats: beats.slice(0, VOX_MAX_BEATS),
      format: cleanFormat(b.format),
      brandColors: cleanColors(b.brandColors),
      voiceTrackUrl,
    },
  };
}
