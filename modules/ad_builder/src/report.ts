/**
 * Two separate documents, deliberately.
 *
 *   Image report — the internal record. Every creative that was produced, its
 *   delivered dimensions, file weight, word count, QA result and Cloudinary
 *   public ID. Includes failures, because a report that hides what did not
 *   ship is not a report.
 *
 *   Gallery — the client-facing view, built by searching a Cloudinary folder
 *   rather than from local state, so it reflects what is actually in the
 *   library at the moment it runs.
 */

import * as fs from 'fs';
import * as path from 'path';
import type { Manifest, ManifestEntry } from './manifest';
import type { UploadedAsset } from './cloudinary';
import { renderProof } from './proof';

const kb = (n: number) => `${(n / 1024).toFixed(1)} KB`;

function esc(s: string): string {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const SHELL_CSS = `
  :root {
    --ink: #10233A; --muted: #5b6b7f; --line: #e2e8f0;
    --bg: #f6f8fb; --card: #ffffff;
    --pass: #1a7f4b; --warn: #a86a00; --fail: #b3261e;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 40px 28px 72px;
    font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    color: var(--ink); background: var(--bg);
  }
  .wrap { max-width: 1180px; margin: 0 auto; }
  header { border-bottom: 2px solid var(--ink); padding-bottom: 18px; margin-bottom: 28px; }
  h1 { margin: 0 0 6px; font-size: 26px; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: 14px; }
  .folder {
    display: inline-block; margin-top: 10px; padding: 5px 10px;
    background: #fff; border: 1px solid var(--line); border-radius: 5px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px;
  }
  .stats { display: flex; flex-wrap: wrap; gap: 10px; margin: 22px 0 30px; }
  .stat {
    background: var(--card); border: 1px solid var(--line); border-radius: 8px;
    padding: 12px 16px; min-width: 108px;
  }
  .stat b { display: block; font-size: 22px; line-height: 1.2; }
  .stat span { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }
  h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .06em;
       color: var(--muted); margin: 34px 0 12px; }
  table { width: 100%; border-collapse: collapse; background: var(--card);
          border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--line); font-size: 13.5px; }
  th { background: #eef2f7; font-weight: 600; font-size: 12px;
       text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
  tr:last-child td { border-bottom: none; }
  td.num { font-variant-numeric: tabular-nums; white-space: nowrap; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px;
         background: #eef2f7; padding: 1px 5px; border-radius: 3px; word-break: break-all; }
  .pill { display: inline-block; padding: 2px 9px; border-radius: 99px;
          font-size: 11.5px; font-weight: 600; letter-spacing: .03em; }
  .pill.pass { background: #e4f4ea; color: var(--pass); }
  .pill.warn { background: #fdf1dc; color: var(--warn); }
  .pill.fail { background: #fce8e6; color: var(--fail); }
  .issues { color: var(--fail); font-size: 12.5px; margin-top: 4px; }
  .issues.warn { color: var(--warn); }
  a { color: #1c5fa8; }
  footer { margin-top: 44px; padding-top: 16px; border-top: 1px solid var(--line);
           color: var(--muted); font-size: 12.5px; }
`;

function shell(title: string, body: string, extraCss = ''): string {
  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(title)}</title>
<style>${SHELL_CSS}${extraCss}</style>
</head><body><div class="wrap">
${body}
</div></body></html>`;
}

/* ------------------------------------------------------------ image report */

function entryRow(e: ManifestEntry): string {
  const cid = e.cloudinary
    ? `<code>${esc(e.cloudinary.publicId)}</code>${e.cloudinary.simulated ? ' <span class="pill warn">dry run</span>' : ''}`
    : '<span class="pill fail">not uploaded</span>';
  const issues = e.qaIssues.length
    ? `<div class="issues ${e.qaStatus === 'warn' ? 'warn' : ''}">${e.qaIssues.map(esc).join('<br>')}</div>`
    : '';
  return `<tr>
    <td><strong>${esc(e.size)}</strong>${issues}</td>
    <td class="num">${esc(e.deliveredDimensions)}</td>
    <td class="num">${esc(e.format)}</td>
    <td class="num">${kb(e.bytes)}</td>
    <td class="num">${e.wordCount}</td>
    <td><span class="pill ${e.qaStatus}">${e.qaStatus}</span></td>
    <td>${cid}</td>
  </tr>`;
}

export function renderImageReport(m: Manifest): string {
  const groups = new Map<string, ManifestEntry[]>();
  for (const e of m.entries) {
    const key = `${e.platform}|${e.conceptId}|${e.conceptName}|${e.layoutFamily}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(e);
  }

  const sections = [...groups.entries()]
    .map(([key, entries]) => {
      const [platform, conceptId, conceptName, family] = key.split('|');
      return `<h2>${esc(platform)} &middot; Concept ${esc(conceptId)} &ldquo;${esc(conceptName)}&rdquo; &middot; ${esc(family)}</h2>
      <table>
        <thead><tr>
          <th>Size</th><th>Delivered</th><th>Format</th><th>Weight</th>
          <th>Words</th><th>QA</th><th>Cloudinary public ID</th>
        </tr></thead>
        <tbody>${entries.map(entryRow).join('')}</tbody>
      </table>`;
    })
    .join('\n');

  const t = m.totals;
  const body = `
<header>
  <h1>Image report &middot; ${esc(m.client)}</h1>
  <div class="sub">${esc(m.campaign)} &middot; Request ${esc(m.requestId)} &middot; generated ${esc(
    new Date(m.generatedAt).toUTCString(),
  )}</div>
  <div class="folder">${esc(m.projectFolder)}</div>
</header>

<div class="stats">
  <div class="stat"><b>${t.creatives}</b><span>Creatives</span></div>
  <div class="stat"><b>${t.uploadedCount}</b><span>In Cloudinary</span></div>
  <div class="stat"><b>${t.pass}</b><span>Passed</span></div>
  <div class="stat"><b>${t.warn}</b><span>Warnings</span></div>
  <div class="stat"><b>${t.fail}</b><span>Failed</span></div>
  <div class="stat"><b>${kb(t.bytes)}</b><span>Total weight</span></div>
</div>

${sections}

<footer>
  ${
    m.dryRun
      ? 'Dry run &mdash; nothing was uploaded. Public IDs shown are the paths that would be used.'
      : m.uploaded
        ? 'Creatives that failed QA are listed but were withheld from upload.'
        : 'Upload was not requested; this report covers locally rendered files only.'
  }
</footer>`;

  return shell(`Image report — ${m.client}`, body);
}

/** Flat CSV of every image, for spreadsheets and the account-manager handoff. */
export function renderCsv(m: Manifest): string {
  const head = [
    'request_id', 'client', 'campaign', 'platform', 'concept_id', 'concept_name',
    'layout_family', 'size', 'delivered', 'format', 'bytes', 'kb', 'words',
    'qa_status', 'qa_issues', 'cloudinary_public_id', 'secure_url', 'local_file',
  ];
  const cell = (v: unknown) => {
    const s = String(v ?? '');
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const rows = m.entries.map((e) =>
    [
      m.requestId, m.client, m.campaign, e.platform, e.conceptId, e.conceptName,
      e.layoutFamily, e.size, e.deliveredDimensions, e.format, e.bytes,
      (e.bytes / 1024).toFixed(1), e.wordCount, e.qaStatus,
      e.qaIssues.join(' | '), e.cloudinary?.publicId ?? '',
      e.cloudinary?.secureUrl ?? '', e.localFile,
    ].map(cell).join(','),
  );
  return [head.join(','), ...rows].join('\n') + '\n';
}

/* ----------------------------------------------------------------- gallery */

const GALLERY_CSS = `
  .grid { display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start; margin-bottom: 8px; }
  .tile { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
          padding: 12px; max-width: 100%; }
  .tile img { display: block; max-width: 100%; height: auto;
              background: repeating-conic-gradient(#eceff3 0% 25%, #fff 0% 50%) 50%/16px 16px; }
  .tile .meta { margin-top: 9px; font-size: 12px; color: var(--muted); display: flex;
                justify-content: space-between; gap: 12px; align-items: center; }
  .tile .meta strong { color: var(--ink); font-size: 13px; }
  .empty { background: var(--card); border: 1px dashed var(--line); border-radius: 8px;
           padding: 28px; text-align: center; color: var(--muted); }
`;

export interface GalleryOptions {
  title: string;
  folder: string;
  subtitle?: string;
  /** Cap the on-screen width so a 970x250 does not blow out the layout. */
  maxTileWidth?: number;
}

function tagValue(tags: string[], prefix: string): string | undefined {
  const hit = tags.find((t) => t.startsWith(`${prefix}:`));
  return hit?.slice(prefix.length + 1);
}

export function renderGallery(assets: UploadedAsset[], opts: GalleryOptions): string {
  const max = opts.maxTileWidth ?? 500;

  const groups = new Map<string, UploadedAsset[]>();
  for (const a of assets) {
    const platform = tagValue(a.tags, 'platform') ?? 'assets';
    const concept = tagValue(a.tags, 'concept') ?? '';
    const key = `${platform}|${concept}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(a);
  }

  const sections =
    assets.length === 0
      ? `<div class="empty">No assets found in <code>${esc(opts.folder)}</code>.</div>`
      : [...groups.entries()]
          .sort()
          .map(([key, list]) => {
            const [platform, concept] = key.split('|');
            const tiles = list
              .sort((a, b) => (b.width || 0) - (a.width || 0))
              .map((a) => {
                const size = tagValue(a.tags, 'size') ?? `${a.width}x${a.height}`;
                const w = Math.min(a.width || max, max);
                return `<figure class="tile" style="width:${w + 26}px">
        <img src="${esc(a.secureUrl)}" width="${a.width}" height="${a.height}" alt="${esc(size)} display ad" loading="lazy">
        <figcaption class="meta"><strong>${esc(size)}</strong><span>${kb(a.bytes)} &middot; ${esc(a.format)}</span></figcaption>
      </figure>`;
              })
              .join('\n');
            return `<h2>${esc(platform)}${concept ? ` &middot; Concept ${esc(concept)}` : ''}</h2>
    <div class="grid">${tiles}</div>`;
          })
          .join('\n');

  const body = `
<header>
  <h1>${esc(opts.title)}</h1>
  ${opts.subtitle ? `<div class="sub">${esc(opts.subtitle)}</div>` : ''}
  <div class="folder">${esc(opts.folder)}</div>
</header>
${sections}
<footer>${assets.length} asset${assets.length === 1 ? '' : 's'} found by searching the Cloudinary folder above. Creatives are shown at actual pixel size, capped at ${max}px wide.</footer>`;

  return shell(opts.title, body, GALLERY_CSS);
}

/* -------------------------------------------------------------- file write */

export function writeReports(m: Manifest, outDir: string): string[] {
  fs.mkdirSync(outDir, { recursive: true });
  const base = `image-report_${m.requestId}`;
  const files = [
    // Proof first: it is the thing a person actually opens.
    [path.join(outDir, `proof_${m.requestId}.html`), renderProof(m, { fileBase: '../' })],
    [path.join(outDir, `${base}.html`), renderImageReport(m)],
    [path.join(outDir, `${base}.csv`), renderCsv(m)],
    [path.join(outDir, `manifest_${m.requestId}.json`), JSON.stringify(m, null, 2)],
  ] as const;
  for (const [file, content] of files) fs.writeFileSync(file, content);
  return files.map(([f]) => f);
}
