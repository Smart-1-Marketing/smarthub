/**
 * One ad structure, many offers: the CSV data merge.
 *
 * An HVAC client runs the same spot in nine cities, a restaurant runs a
 * different special every week, a dealer runs one banner per unit. The design
 * is identical and only the words move — and the list already exists, in the
 * email or the spreadsheet it arrived in. Retyping it into the build screen
 * nine times is where the fourth city gets missed.
 *
 * A row becomes a CONCEPT on one campaign, which is the whole reason this is
 * small: jobs.ts already renders concepts x sizes, counts progress over
 * exactly that set, persists across a deploy and recovers on boot. A separate
 * batch queue would be a second description of all of that, and the two would
 * disagree about what "done" means.
 *
 * Every rule here is about failing a row rather than a batch:
 *
 *   **A bad row is named and the rest still build.** Twelve rows producing
 *   nine ads with no word about the other three is the failure target_areas
 *   describes about a pasted location list, and it is worse here because the
 *   output is a folder of images nobody counts.
 *
 *   **A missing column fails the whole file, before anything renders.** That
 *   is not the same case: a header that does not match the preset's slots
 *   means every row is wrong in the same way, and rendering fifty ads with a
 *   blank offer to discover it is expensive in a way one bad row is not.
 *
 *   **A row cap that is stated.** N rows x M sizes is a lot of rendering, and
 *   an unbounded CSV is the one request that holds the queue for an hour.
 *   BATCH_MAX_ROWS, reported in the refusal rather than silently truncating —
 *   a batch that quietly built the first fifty of eighty rows would read as a
 *   complete delivery.
 *
 *   **Nothing is invented.** A blank cell falls back to what the preset saved,
 *   which is visible on the proof; it never asks a model to fill the gap.
 */

import type { Campaign, CreativeConcept } from './types';
import { conceptFromPreset, FIELD_ROLES, type FieldRole, type Preset } from './presets';

/** Rows past this are refused rather than truncated. */
export const BATCH_MAX_ROWS = Number(process.env.BATCH_MAX_ROWS ?? 50);

export interface BatchRow {
  /** 1-based row number as a person reading the spreadsheet counts it, so the
   *  header is row 1 and the first record is row 2. An error naming "row 3"
   *  has to mean the row they can see. */
  line: number;
  values: Partial<Record<FieldRole, string>>;
  /** The row's own name for the ad, from a `name` column if one was supplied. */
  name?: string;
}

export interface BatchParse {
  rows: BatchRow[];
  /** Rows read but not usable, each saying which row and why. */
  rejected: { line: number; reason: string }[];
  /** Header columns that match no slot on the preset. Carried rather than
   *  refused: a spreadsheet routinely has a notes or a city column that is
   *  not an ad field, and refusing the file over one would send somebody back
   *  to edit a CSV that was fine. */
  ignoredColumns: string[];
  /** Populated only when the file cannot be used at all. */
  error?: string;
}

/**
 * A CSV reader that handles quoted fields, embedded commas and newlines, and
 * doubled quotes.
 *
 * Written out rather than depended on: this module takes no new dependencies
 * (the Hub's own rule), the shape is one function, and the input is a file a
 * person exported from a spreadsheet — which means quoted commas in every
 * offer line ("Save $500, this month only") and CRLF endings from Excel.
 * Splitting on commas would have cut that offer in half and shifted every
 * column after it, which reads on the proof as the wrong copy in the right
 * place.
 */
export function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;
  // A BOM from Excel becomes part of the first header otherwise, so `headline`
  // silently stops matching.
  const s = text.replace(/^﻿/, '');

  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (quoted) {
      if (c === '"') {
        if (s[i + 1] === '"') { field += '"'; i++; }
        else quoted = false;
      } else field += c;
      continue;
    }
    if (c === '"') { quoted = true; continue; }
    if (c === ',') { row.push(field); field = ''; continue; }
    if (c === '\r') continue;
    if (c === '\n') { row.push(field); rows.push(row); row = []; field = ''; continue; }
    field += c;
  }
  // A file with no trailing newline still has a last row.
  if (field.length || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((cell) => cell.trim() !== ''));
}

/** Header text to the slot it fills: case, spaces and underscores ignored. */
function headerKey(h: string): string {
  return h.trim().toLowerCase().replace(/[\s_-]+/g, '');
}

/**
 * Read a CSV against one preset's slots.
 *
 * Columns are matched on the preset's field LABEL or on the role itself, so a
 * preset whose offer slot is labelled "Deal" accepts either header. `name` is
 * reserved for what to call the ad.
 */
export function readBatch(preset: Preset, text: string): BatchParse {
  const table = parseCsv(text);
  if (!table.length) {
    return { rows: [], rejected: [], ignoredColumns: [], error: 'The file is empty.' };
  }

  const header = table[0];
  const byColumn: (FieldRole | 'name' | null)[] = [];
  const ignoredColumns: string[] = [];
  const matched = new Set<FieldRole>();

  for (const raw of header) {
    const key = headerKey(raw);
    if (key === 'name') { byColumn.push('name'); continue; }
    const field = preset.fields.find(
      (f) => headerKey(f.label) === key || headerKey(f.role) === key,
    );
    if (field) {
      byColumn.push(field.role);
      matched.add(field.role);
      continue;
    }
    byColumn.push(null);
    if (raw.trim()) ignoredColumns.push(raw.trim());
  }

  if (!matched.size) {
    // Every row is wrong in the same way, so this is a file-level failure and
    // is reported before anything renders. Naming what was expected and what
    // arrived is the difference between a fixable message and "invalid CSV".
    const wanted = preset.fields.map((f) => f.label).join(', ');
    return {
      rows: [], rejected: [], ignoredColumns,
      error:
        `No column matches a slot on "${preset.name}". Expected one of: ${wanted}. ` +
        `Found: ${header.map((h) => h.trim()).filter(Boolean).join(', ') || '(no headers)'}.`,
    };
  }

  const rows: BatchRow[] = [];
  const rejected: { line: number; reason: string }[] = [];

  for (let r = 1; r < table.length; r++) {
    const line = r + 1; // header is row 1
    const cells = table[r];
    if (cells.length > header.length) {
      // Almost always an unquoted comma, which has shifted every column after
      // it. Building the row would put the right copy in the wrong slot, which
      // is harder to spot on a proof than a missing row.
      rejected.push({
        line,
        reason: `${cells.length} values for ${header.length} columns — an unquoted comma shifts every column after it`,
      });
      continue;
    }

    const values: Partial<Record<FieldRole, string>> = {};
    let name: string | undefined;
    byColumn.forEach((role, i) => {
      const cell = String(cells[i] ?? '').trim();
      if (!cell || !role) return;
      if (role === 'name') { name = cell; return; }
      values[role] = cell;
    });

    if (!Object.keys(values).length) {
      rejected.push({ line, reason: 'no value in any slot column' });
      continue;
    }
    rows.push({ line, values, ...(name ? { name } : {}) });
  }

  if (rows.length > BATCH_MAX_ROWS) {
    return {
      rows: [], rejected, ignoredColumns,
      error:
        `${rows.length} rows is over the ${BATCH_MAX_ROWS}-row limit for one batch. ` +
        `Split the file — a batch is not truncated, because ${BATCH_MAX_ROWS} ads out of ` +
        `${rows.length} would look exactly like a finished job.`,
    };
  }

  return { rows, rejected, ignoredColumns };
}

/**
 * The rows, as one campaign of lettered concepts.
 *
 * Concepts are lettered in this business and jobs.ts renders one campaign's
 * concepts across the platforms asked for, so a batch is a campaign. Past Z
 * they become AA, AB -- a batch of thirty is allowed and two concepts sharing
 * an id would have the second overwrite the first's renders.
 */
export function campaignFromBatch(input: {
  preset: Preset;
  rows: BatchRow[];
  requestId: string;
  campaignName: string;
}): Campaign {
  const concepts: CreativeConcept[] = input.rows.map((row, i) => {
    const id = conceptLetter(i);
    return conceptFromPreset(input.preset, row.values, {
      conceptId: id,
      // The row's own name if it gave one, else the first slot it filled --
      // which is nearly always the headline, and is what somebody scanning a
      // folder of thirty proofs is looking for.
      name: row.name || firstValue(row) || `Row ${row.line}`,
    });
  });
  return {
    requestId: input.requestId,
    campaignName: input.campaignName,
    brand: input.preset.brand,
    concepts,
  };
}

function firstValue(row: BatchRow): string {
  for (const role of FIELD_ROLES) {
    const v = row.values[role];
    if (v) return v;
  }
  return '';
}

/** 0 -> A, 25 -> Z, 26 -> AA. */
export function conceptLetter(index: number): string {
  let n = index;
  let out = '';
  do {
    out = String.fromCharCode(65 + (n % 26)) + out;
    n = Math.floor(n / 26) - 1;
  } while (n >= 0);
  return out;
}
