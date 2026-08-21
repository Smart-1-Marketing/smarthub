/**
 * Campaign overview: one page that tells the whole story of a build — what
 * the customer asked for, what Brandfetch knew, and every rendered ad — plus
 * a client-ready PDF version with live links.
 *
 * The HTML page is a capability URL (knowing the request id grants access,
 * same trust model as the proof). The PDF is generated on demand from the
 * same data so the two can never drift apart.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import PDFDocument from 'pdfkit';

const esc = (v: unknown) => String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);

export interface OverviewData {
  requestId: string;
  project: any;
  campaign: any;
  submission?: Record<string, unknown>;
  brandCacheRecord?: { fetchedAt: string; result: any } | null;
  ads: { size: string; delivered: string; platform: string; file: string; url: string; bytes: number; qaStatus: string }[];
  publicUrl: string;
  proofUrl: string;
  downloadUrl?: string;
}

const LABELS: Record<string, string> = {
  business: 'Business', website: 'Website', contact: 'Contact', email: 'Email',
  campaignName: 'Campaign', projectName: 'Project', landingPage: 'Landing page',
  promoting: 'Promoting', objective: 'Objective', benefit: 'Primary benefit',
  offer: 'Offer', cta: 'Call to action', audience: 'Audience', geography: 'Geography',
};

function infoRows(sub: Record<string, unknown> | undefined, project: any, campaign: any): [string, string][] {
  const rows: [string, string][] = [];
  const src: Record<string, unknown> = {
    business: project?.client, website: project?.domain, campaignName: project?.campaignName,
    projectName: project?.projectName, landingPage: project?.landingPage,
    promoting: campaign?.promoting, objective: campaign?.objective,
    benefit: campaign?.benefit, offer: campaign?.offer, cta: campaign?.cta,
    ...(sub ?? {}),
  };
  for (const [k, label] of Object.entries(LABELS)) {
    const v = src[k];
    if (v !== undefined && v !== null && String(v).trim() !== '') rows.push([label, String(v)]);
  }
  return rows;
}

function brandRows(brand: any, cacheRec: OverviewData['brandCacheRecord']): [string, string][] {
  const rows: [string, string][] = [];
  if (!brand) return rows;
  if (brand.name) rows.push(['Brand name', brand.name]);
  if (brand.domain) rows.push(['Domain', brand.domain]);
  const c = brand.colors ?? {};
  const palette = ['primary', 'secondary', 'accent', 'light', 'dark'].map((k) => c[k]).filter(Boolean).join('  ');
  if (palette) rows.push(['Palette', palette]);
  const f = brand.fonts ?? {};
  if (f.headline || f.body) rows.push(['Fonts', [f.headline, f.body].filter(Boolean).join(' / ')]);
  if (brand.logos?.primary) rows.push(['Logo', 'On file' + (brand.logos.reverse ? ' (+ white version)' : '')]);
  if (cacheRec?.fetchedAt) rows.push(['Brand data fetched', new Date(cacheRec.fetchedAt).toLocaleDateString()]);
  return rows;
}

/* ------------------------------------------------------------- HTML page */

export function renderOverview(d: OverviewData): string {
  const info = infoRows(d.submission, d.project, d.campaign);
  const brand = brandRows(d.campaign?.brand ?? d.project?.brand, d.brandCacheRecord);
  const delivered = d.project?.delivered?.length
    ? d.project.delivered[d.project.delivered.length - 1]
    : null;

  const adTiles = d.ads.map((a) => `
      <figure>
        <figcaption>${esc(a.delivered !== a.size ? a.delivered : a.size)} · ${esc(a.platform)}</figcaption>
        <img src="${esc(a.url)}" alt="${esc(a.size)}" loading="lazy" style="max-width:min(100%,${a.size.split('x')[0]}px)">
      </figure>`).join('');

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${esc(d.project?.client)} — campaign overview</title>
<style>
  :root { --ink:#17242F; --ink2:#5B6B7A; --line:#E3E8EE; --signal:#1F5FC0; }
  * { box-sizing:border-box }
  body { margin:0; font:15px/1.55 'Segoe UI',system-ui,sans-serif; color:var(--ink); background:#F5F7FA; }
  .wrap { max-width:1080px; margin:0 auto; padding:32px 20px 60px; }
  header { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; }
  h1 { margin:0 0 2px; font-size:26px; }
  .sub { color:var(--ink2); }
  .actions a { display:inline-block; background:var(--signal); color:#fff; text-decoration:none; padding:10px 18px; border-radius:8px; font-weight:600; margin:0 8px 8px 0; font-size:14px; }
  .actions a.ghost { background:#fff; color:var(--signal); border:1px solid var(--line); }
  section { background:#fff; border:1px solid var(--line); border-radius:12px; padding:22px; margin-top:22px; }
  h2 { margin:0 0 14px; font-size:17px; }
  table { border-collapse:collapse; width:100%; }
  td { padding:6px 10px 6px 0; vertical-align:top; }
  td.k { color:var(--ink2); white-space:nowrap; width:170px; }
  .ads { display:flex; flex-wrap:wrap; gap:18px; }
  figure { margin:0; }
  figcaption { font:600 12px/1.4 ui-monospace,monospace; color:var(--ink2); margin-bottom:6px; }
  img { display:block; border:1px solid var(--line); border-radius:6px; height:auto; }
  footer { color:var(--ink2); font-size:13px; margin-top:26px; }
</style></head><body><div class="wrap">
  <header>
    <div>
      <h1>${esc(d.project?.client)} — ${esc(d.project?.campaignName)}</h1>
      <div class="sub">Campaign ${esc(d.requestId)} · ${esc(d.project?.projectName ?? '')}</div>
    </div>
    <div class="actions">
      <a href="/overview/${esc(d.requestId)}/pdf">Download PDF report</a>
      <a class="ghost" href="${esc(d.proofUrl)}">Open the proof</a>
      ${delivered ? `<a class="ghost" href="${esc(delivered.zipUrl)}" download>Download final files</a>` : ''}
    </div>
  </header>

  <section><h2>Campaign information</h2>
    <table>${info.map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')}</table>
  </section>

  <section><h2>Brand details</h2>
    ${brand.length ? `<table>${brand.map(([k, v]) => `<tr><td class="k">${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')}</table>` : '<p class="sub">No brand record on file.</p>'}
  </section>

  <section><h2>Rendered creative (${d.ads.length})</h2>
    <div class="ads">${adTiles || '<p class="sub">No renders yet.</p>'}</div>
  </section>

  <footer>Prepared by Smart 1 Marketing · This page and its PDF share live links to the proof and final files.</footer>
</div></body></html>`;
}

/* -------------------------------------------------------------- PDF report */

export async function renderOverviewPdf(d: OverviewData): Promise<Buffer> {
  const doc = new PDFDocument({ size: 'LETTER', margin: 54, info: { Title: `${d.project?.client} — ${d.project?.campaignName}` } });
  const chunks: Buffer[] = [];
  doc.on('data', (c: Buffer) => chunks.push(c));
  const done = new Promise<Buffer>((resolve) => doc.on('end', () => resolve(Buffer.concat(chunks))));

  const ink = '#17242F'; const ink2 = '#5B6B7A'; const signal = '#1F5FC0';

  doc.fillColor(ink).fontSize(22).font('Helvetica-Bold').text(`${d.project?.client ?? ''}`, { continued: false });
  doc.fontSize(13).font('Helvetica').fillColor(ink2).text(`${d.project?.campaignName ?? ''} · Campaign ${d.requestId}`);
  doc.moveDown(0.6);

  // Live links up top — the whole point of the client PDF.
  doc.fontSize(11).fillColor(signal);
  doc.text('Open the creative proof', { link: `${d.publicUrl}${d.proofUrl}`, underline: true });
  if (d.downloadUrl) doc.text('Download the final ad files', { link: `${d.publicUrl}${d.downloadUrl}`, underline: true });
  doc.text('View this overview online', { link: `${d.publicUrl}/overview/${d.requestId}`, underline: true });
  doc.moveDown(0.8);

  const section = (title: string) => {
    doc.moveDown(0.4);
    doc.fontSize(14).font('Helvetica-Bold').fillColor(ink).text(title);
    doc.moveTo(doc.x, doc.y + 2).lineTo(doc.page.width - 54, doc.y + 2).strokeColor('#E3E8EE').stroke();
    doc.moveDown(0.4);
  };
  const row = (k: string, v: string) => {
    doc.fontSize(10.5).font('Helvetica-Bold').fillColor(ink2).text(k + ':  ', { continued: true });
    doc.font('Helvetica').fillColor(ink).text(v);
  };

  section('Campaign information');
  for (const [k, v] of infoRows(d.submission, d.project, d.campaign)) row(k, v);

  section('Brand details');
  const br = brandRows(d.campaign?.brand ?? d.project?.brand, d.brandCacheRecord);
  if (br.length) for (const [k, v] of br) row(k, v);
  else doc.fontSize(10.5).font('Helvetica').fillColor(ink2).text('No brand record on file.');

  section(`Rendered creative (${d.ads.length})`);
  const usableW = doc.page.width - 108;
  for (const a of d.ads) {
    if (!fs.existsSync(a.file)) continue;
    const [lw, lh] = a.size.split('x').map(Number);
    const scale = Math.min(1, usableW / lw, 220 / lh);
    const w = lw * scale; const h = lh * scale;
    if (doc.y + h + 30 > doc.page.height - 54) doc.addPage();
    doc.fontSize(9.5).font('Helvetica-Bold').fillColor(ink2)
      .text(`${a.delivered !== a.size ? a.delivered : a.size} · ${a.platform}`);
    try {
      doc.image(a.file, doc.x, doc.y + 2, { width: w, height: h });
      doc.y += h + 12;
    } catch { doc.fontSize(9).fillColor(ink2).text('(preview unavailable)'); }
  }

  doc.fontSize(9).fillColor(ink2).text('Prepared by Smart 1 Marketing', 54, doc.page.height - 70);
  doc.end();
  return done;
}
