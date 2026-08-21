/**
 * The client proof screen.
 *
 * Design brief, stated so the choices are checkable: the viewer is a business
 * owner, not a designer, deciding whether these ads represent their company.
 * The page has one job — get to a confident yes or a specific change request.
 *
 * Every decision below follows from one fact about the subject: display ads are
 * seen small, in someone else's page, for about a second. A proof screen that
 * shows them enlarged on a white void flatters the work and misleads the
 * client. So the two signature controls are the ones a designer would actually
 * use in a review:
 *
 *   Actual size    — 1:1 pixels, never scaled up. The default.
 *   Squint test    — blurs the creative until only the focal hierarchy remains.
 *                    This is the review technique behind "stick to one point of
 *                    focus" and "size variation signals importance". If the ad
 *                    still reads blurred, it will survive a glance.
 *
 * Palette is a deliberately neutral proofing grey. Brand colours have to read
 * true, so the interface refuses to compete: one ink, one signal blue, and the
 * QA traffic lights. Type pairs Poppins (the renderer's own display face, so
 * the page shares an identity with its output) against a monospace used only
 * for specifications, because dimensions and file weights are data.
 */

import type { Manifest, ManifestEntry } from './manifest';

export interface ProofOptions {
  /** How the browser should reach the creative files, e.g. '/files/'. */
  fileBase?: string;
  /** Base for the decision endpoint, e.g. '/api/proof/<projectId>'. */
  actionBase?: string;
  /** Current copy + palette per concept, to pre-fill the live editor. */
  initialCopy?: Record<string, { headline?: string; support?: string; cta?: string }>;
  initialColors?: { accent?: string; ctaText?: string; headline?: string };
  /** Campaign context for on-proof image search (business, promoting, etc.). */
  meta?: { business?: string; promoting?: string; objective?: string };
  /** Effective copy per concept/size ("C/728x90" -> {...}) for inline editing. */
  perSizeCopy?: Record<string, { headline?: string; support?: string; cta?: string }>;
  /** Latest delivered package, when the project is already complete. */
  delivered?: { at: string; zipUrl: string; fileCount?: number };
}

const esc = (v: unknown) =>
  String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);

const kb = (n: number) => `${(n / 1024).toFixed(1)} KB`;

/** Sizes in the order a reviewer should meet them: universal first. */
const SIZE_ORDER = ['300x250', '336x280', '728x90', '300x600', '160x600', '970x250', '320x50', '414x125'];

function orderEntries(entries: ManifestEntry[]): ManifestEntry[] {
  return [...entries].sort(
    (a, b) => SIZE_ORDER.indexOf(a.size) - SIZE_ORDER.indexOf(b.size),
  );
}

export function renderProof(m: Manifest, opts: ProofOptions = {}): string {
  const fileBase = opts.fileBase ?? '';

  const byConcept = new Map<string, ManifestEntry[]>();
  for (const e of m.entries) {
    if (!byConcept.has(e.conceptId)) byConcept.set(e.conceptId, []);
    byConcept.get(e.conceptId)!.push(e);
  }

  const concepts = [...byConcept.entries()].map(([id, entries]) => ({
    id,
    name: entries[0].conceptName,
    family: entries[0].layoutFamily,
    entries: orderEntries(entries),
    hero: orderEntries(entries).find((e) => e.size === '300x250') ?? orderEntries(entries)[0],
  }));

  // Cloudinary URL once uploaded; otherwise the path relative to the reports
  // directory this file is written into (<out>/reports/proof.html ->
  // <out>/<platform>/<concept>/<file>).
  const src = (e: ManifestEntry) => {
    if (e.cloudinary?.secureUrl && !e.cloudinary.simulated) return e.cloudinary.secureUrl;
    return fileBase + e.localFile.split('/').slice(-3).join('/');
  };

  const chooser = concepts
    .map(
      (c, i) => `
      <label class="choice${i === 0 ? ' on' : ''}" data-concept="${esc(c.id)}">
        <input type="radio" name="concept" value="${esc(c.id)}"${i === 0 ? ' checked' : ''}>
        <span class="choice-head">
          <span class="letter">${esc(c.id)}</span>
          <span>
            <b>${esc(c.name)}</b>
            <em>${c.entries.length} sizes</em>
          </span>
        </span>
        <span class="choice-art">
          <img src="${esc(src(c.hero))}" width="${c.hero.deliveredDimensions.split('x')[0]}"
               height="${c.hero.deliveredDimensions.split('x')[1]}" alt="Concept ${esc(c.id)} preview" loading="lazy">
        </span>
      </label>`,
    )
    .join('');

  const panels = concepts
    .map((c, i) => {
      const tiles = c.entries
        .map((e) => {
          // Display every creative at its LOGICAL size so a 2x asset sits in
          // true proportion next to its 1x sibling; the label leads with the
          // delivered dimensions so "640x100" reads as exactly that.
          const [w, h] = e.size.split('x').map(Number);
          const isScaled = e.deliveredDimensions !== e.size;
          const issues = e.qaIssues.length
            ? `<ul class="issues">${e.qaIssues.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`
            : '';
          return `
        <figure class="ad" data-size="${esc(e.size)}" data-concept="${esc(c.id)}">
          <figcaption>
            <span class="size">${esc(isScaled ? e.deliveredDimensions : e.size)}</span>
            <span class="spec">${isScaled ? `${esc(e.size)} placement @2x · ` : ''}${esc(e.format)} · ${kb(e.bytes)} · ${e.wordCount} words</span>
            <span class="dot ${esc(e.qaStatus)}" title="${esc(e.qaStatus)}"></span>
            <button type="button" class="edit-size" data-size="${esc(e.size)}" data-concept="${esc(c.id)}" title="Edit just this size">Edit this size</button>
          </figcaption>
          <div class="frame" style="width:${w}px">
            <img src="${esc(src(e))}" width="${w}" height="${h}" alt="${esc(e.size)} advertisement" loading="lazy">
          </div>
          ${issues}
        </figure>`;
        })
        .join('');

      const inline = c.entries.find((e) => e.size === '300x250');
      const board = c.entries.find((e) => e.size === '728x90');

      return `
      <section class="panel${i === 0 ? ' on' : ''}" data-panel="${esc(c.id)}">
        <div class="grid">${tiles}</div>

        <div class="context" hidden>
          <p class="context-note">Your ad as a reader meets it — inside someone else's page, at real size.</p>
          <div class="page">
            <div class="page-nav"><span class="page-brand">The Daily Register</span><span class="page-links">News · Local · Business</span></div>
            ${board ? `<div class="slot"><img src="${esc(src(board))}" width="728" height="90" alt="Leaderboard in context"></div>` : ''}
            <div class="page-body">
              <div class="page-col">
                <h4>City approves new transit corridor</h4>
                <p>Council members voted late Tuesday to advance the long-debated proposal, ending months of negotiation between neighbourhood groups and the transit authority.</p>
                <p>Construction would begin next spring under the current timetable, with the first segment opening to riders within three years.</p>
                <p>Residents along the route have asked for noise mitigation and a guarantee that existing bus service will continue during the build.</p>
                <p>The measure passed seven votes to two, with both dissenting members citing cost.</p>
              </div>
              <aside class="page-side">
                ${inline ? `<div class="slot"><img src="${esc(src(inline))}" width="300" height="250" alt="Rectangle in context"></div>` : ''}
                <h5>Most read</h5>
                <ol><li>School board sets budget</li><li>Bridge repairs delayed</li><li>Weekend market returns</li></ol>
              </aside>
            </div>
          </div>
        </div>
      </section>`;
    })
    .join('');

  const failing = m.entries.filter((e) => e.qaStatus === 'fail').length;

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(m.client)} — creative proof</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    /* A proofing grey, not a brand colour. The interface must not compete
       with whatever palette the creative is carrying. */
    --paper: #E9EAEC;
    --card: #FFFFFF;
    --ink: #14171C;
    --ink-2: #5C6470;
    --rule: #D2D5DA;
    --signal: #2C4FD6;
    --pass: #1B7F4B;
    --warn: #9A6400;
    --fail: #B3261E;
    --shadow: 0 1px 2px rgba(20,23,28,.08), 0 8px 24px rgba(20,23,28,.06);
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; }
  body {
    background: var(--paper);
    color: var(--ink);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .mono { font-family: "IBM Plex Mono", ui-monospace, Menlo, monospace; }
  h1, h2, h3, .letter, .choice b {
    font-family: Poppins, "Avenir Next", "Segoe UI", system-ui, sans-serif;
    letter-spacing: -.005em;
  }

  .wrap { max-width: 1180px; margin: 0 auto; padding: 0 24px 96px; }

  /* ---- masthead ---- */
  header.top {
    display: flex; flex-wrap: wrap; gap: 16px; align-items: baseline;
    justify-content: space-between; padding: 34px 0 20px;
    border-bottom: 1px solid var(--rule); margin-bottom: 28px;
  }
  header.top h1 { margin: 0; font-size: 25px; letter-spacing: -.01em; font-weight: 600; }
  .kicker {
    font-size: 11px; letter-spacing: .16em; text-transform: uppercase;
    color: var(--ink-2); margin-bottom: 6px;
  }
  .meta { color: var(--ink-2); font-size: 13px; }

  /* ---- step 1: choose ---- */
  .step { display: flex; align-items: baseline; gap: 10px; margin: 0 0 14px; }
  .step h2 { margin: 0; font-size: 15px; font-weight: 600; }
  .step span { color: var(--ink-2); font-size: 13.5px; }
  .step .n {
    font-family: "IBM Plex Mono", monospace; font-size: 12px; color: var(--signal);
    border: 1px solid var(--signal); border-radius: 3px; padding: 1px 6px;
  }

  .choices { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 40px; }
  .choice {
    background: var(--card); border: 1px solid var(--rule); border-radius: 10px;
    padding: 14px; cursor: pointer; box-shadow: var(--shadow);
    transition: border-color .15s, transform .15s;
    display: block; position: relative;
  }
  .choice:hover { transform: translateY(-2px); }
  .choice.on { border-color: var(--signal); box-shadow: 0 0 0 1px var(--signal), var(--shadow); }
  .choice input { position: absolute; opacity: 0; pointer-events: none; }
  .choice-head { display: flex; gap: 11px; align-items: center; margin-bottom: 12px; }
  .letter {
    width: 30px; height: 30px; border-radius: 50%; background: var(--ink); color: #fff;
    display: grid; place-items: center; font-size: 14px; font-weight: 600; flex: none;
  }
  .choice.on .letter { background: var(--signal); }
  .choice b { display: block; font-size: 14.5px; font-weight: 600; }
  .choice em { font-style: normal; font-size: 12px; color: var(--ink-2); }
  .choice-art img { display: block; max-width: 300px; height: auto; border: 1px solid var(--rule); }

  /* ---- toolbar ---- */
  .toolbar {
    position: sticky; top: 0; z-index: 5; background: rgba(233,234,236,.93);
    backdrop-filter: blur(8px); border-bottom: 1px solid var(--rule);
    display: flex; gap: 8px; flex-wrap: wrap; align-items: center;
    padding: 12px 0; margin-bottom: 22px;
  }
  .tool {
    font: inherit; font-size: 13.5px; background: var(--card); color: var(--ink);
    border: 1px solid var(--rule); border-radius: 6px; padding: 7px 13px; cursor: pointer;
  }
  .tool:hover { border-color: var(--ink-2); }
  .tool[aria-pressed="true"] { background: var(--ink); color: #fff; border-color: var(--ink); }
  .tool:focus-visible, .choice:focus-within, .act:focus-visible { outline: 2px solid var(--signal); outline-offset: 2px; }
  .toolbar .hint { color: var(--ink-2); font-size: 12.5px; margin-left: auto; }

  /* ---- the ads ---- */
  .panel { display: none; }
  .panel.on { display: block; }
  .grid { display: flex; flex-wrap: wrap; gap: 26px; align-items: flex-start; }
  .ad { margin: 0; max-width: 100%; }
  figcaption {
    display: flex; align-items: center; gap: 9px; margin-bottom: 7px;
    font-size: 12px; color: var(--ink-2);
  }
  .size { font-family: "IBM Plex Mono", monospace; font-weight: 500; color: var(--ink); font-size: 12.5px; }
  .spec { font-family: "IBM Plex Mono", monospace; font-size: 11px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; margin-left: auto; flex: none; }
  .dot.pass { background: var(--pass); }
  .dot.warn { background: var(--warn); }
  .dot.fail { background: var(--fail); }
  /* True size is the whole point, so a 970x250 is never scaled down to fit a
     phone — that would misrepresent what the client is approving. Instead the
     oversized unit scrolls inside its own frame and the page stays put. */
  .frame {
    background: var(--card); border: 1px solid var(--rule); box-shadow: var(--shadow);
    max-width: 100%; overflow-x: auto; overflow-y: hidden;
    -webkit-overflow-scrolling: touch;
  }
  .frame img { display: block; width: auto; max-width: none; height: auto; transition: filter .18s ease; }
  body.squint .frame img { filter: blur(4px) saturate(1.05); }
  body.squint .choice-art img { filter: blur(3px); }
  .issues { margin: 8px 0 0; padding-left: 16px; font-size: 12px; color: var(--warn); max-width: 320px; }

  /* ---- in-context ---- */
  .context-note { color: var(--ink-2); font-size: 13.5px; margin: 0 0 12px; }
  .page {
    background: #fff; border: 1px solid var(--rule); box-shadow: var(--shadow);
    padding: 0 0 22px; max-width: 800px; overflow-x: auto;
  }
  .page-nav {
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 14px 20px; border-bottom: 2px solid #1a1a1a;
  }
  .page-brand { font-family: Georgia, "Times New Roman", serif; font-size: 20px; letter-spacing: -.01em; }
  .page-links { font-size: 11px; color: #6b7280; letter-spacing: .04em; }
  .slot { padding: 16px 20px; display: flex; justify-content: center; }
  .slot img { display: block; max-width: 100%; height: auto; }
  .page-body { display: flex; gap: 26px; padding: 4px 20px 0; }
  .page-col { flex: 1 1 60%; }
  .page-col h4 { font-family: Georgia, serif; font-size: 21px; margin: 6px 0 10px; font-weight: 600; }
  .page-col p { font-family: Georgia, serif; font-size: 14px; line-height: 1.65; color: #374151; margin: 0 0 11px; }
  .page-side { flex: 0 0 300px; }
  .page-side .slot { padding: 0 0 14px; }
  .page-side h5 { font-size: 11px; letter-spacing: .12em; text-transform: uppercase; color: #6b7280; margin: 4px 0 8px; }
  .page-side ol { margin: 0; padding-left: 18px; font-family: Georgia, serif; font-size: 13.5px; color: #374151; }
  .page-side li { margin-bottom: 6px; }

  /* ---- decision ---- */
  .decide {
    margin-top: 46px; padding: 26px; background: var(--card);
    border: 1px solid var(--rule); border-radius: 10px; box-shadow: var(--shadow);
  }
  .decide h2 { margin: 0 0 4px; font-size: 17px; }
  .decide p { margin: 0 0 16px; color: var(--ink-2); font-size: 13.5px; }
  textarea {
    width: 100%; min-height: 84px; padding: 11px 12px; font: inherit; font-size: 14px;
    border: 1px solid var(--rule); border-radius: 7px; resize: vertical; color: var(--ink);
  }
  textarea:focus { outline: 2px solid var(--signal); outline-offset: -1px; }
  .acts { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 14px; }
  .editor { background: #F7F9FC; border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin-bottom: 14px; }
  .editor .erow { margin-bottom: 12px; }
  .editor .erow:last-child { margin-bottom: 0; }
  .editor label { display: block; font-size: 12px; font-weight: 600; color: var(--ink-2); margin-bottom: 4px; text-transform: uppercase; letter-spacing: .04em; }
  .editor input[type=text] { width: 100%; padding: 10px 12px; border: 1px solid var(--line); border-radius: 7px; font-size: 15px; color: var(--ink); box-sizing: border-box; }
  .editor .cols { display: flex; gap: 16px; }
  .editor .cols > div { flex: 0 0 auto; }
  .editor input[type=color] { width: 54px; height: 36px; border: 1px solid var(--line); border-radius: 7px; padding: 2px; cursor: pointer; background: #fff; }
  .logo-acts { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .mini { background: #fff; border: 1px solid var(--line); border-radius: 7px; padding: 8px 12px; font-size: 13px; font-weight: 600; color: var(--ink); cursor: pointer; }
  .mini:hover { border-color: var(--signal); }
  .mini.on { background: var(--signal); color: #fff; border-color: var(--signal); }
  .mini-check { font-size: 13px; color: var(--ink-2); display: flex; align-items: center; gap: 6px; }
  .edit-size { margin-left: auto; background: #fff; border: 1px solid var(--line); border-radius: 6px; padding: 4px 10px; font-size: 12px; font-weight: 600; color: var(--signal); cursor: pointer; }
  .edit-size:hover { border-color: var(--signal); }
  .size-editor { position: fixed; inset: 0; background: rgba(16,34,46,.5); display: none; align-items: center; justify-content: center; z-index: 50; padding: 20px; }
  .size-editor.on { display: flex; }
  .size-editor .box { background: #fff; border-radius: 12px; padding: 22px; width: 100%; max-width: 440px; box-shadow: 0 12px 40px rgba(16,34,46,.3); }
  .size-editor h3 { margin: 0 0 4px; font-size: 16px; }
  .size-editor p.sub { margin: 0 0 16px; color: var(--ink-2); font-size: 13px; }
  .size-editor label { display: block; font-size: 12px; font-weight: 600; color: var(--ink-2); margin: 12px 0 4px; text-transform: uppercase; letter-spacing: .04em; }
  .size-editor input { width: 100%; padding: 10px 12px; border: 1px solid var(--line); border-radius: 7px; font-size: 15px; box-sizing: border-box; }
  .size-editor .row { display: flex; gap: 10px; margin-top: 18px; }
  .size-editor .row button { flex: 1; padding: 11px; border-radius: 8px; font-weight: 600; font-size: 14px; cursor: pointer; border: 1px solid var(--line); background: #fff; }
  .size-editor .row button.primary { background: var(--signal); color: #fff; border-color: var(--signal); }
  .act {
    font: inherit; font-weight: 600; font-size: 14.5px; border-radius: 7px;
    padding: 11px 20px; cursor: pointer; border: 1px solid var(--rule); background: var(--card);
  }
  .act.primary { background: var(--signal); border-color: var(--signal); color: #fff; }
  .act.primary:hover { filter: brightness(1.08); }
  .banner {
    padding: 12px 14px; border-radius: 7px; font-size: 13.5px; margin-bottom: 16px;
  }
  .banner.fail { background: #FCE8E6; color: var(--fail); }
  footer { margin-top: 34px; color: var(--ink-2); font-size: 12.5px; }

  .context, .grid { max-width: 100%; }
  @media (max-width: 720px) {
    .choice-art img { max-width: 100%; }
    .page-body { flex-direction: column; }
    .page-side { flex: 1 1 auto; }
    .toolbar .hint { display: none; }
  }
  @media (prefers-reduced-motion: reduce) {
    * { transition: none !important; }
  }
</style>
</head>
<body>
<div class="wrap">

  <header class="top">
    <div>
      <div class="kicker">Creative proof · ${esc(m.requestId)}</div>
      <h1>${esc(m.client)}</h1>
      <div class="meta">${esc(m.campaign)} · ${m.entries.length} creatives across ${
        new Set(m.entries.map((e) => e.platform)).size
      } platform(s)</div>
    </div>
    <div class="meta mono">${esc(new Date(m.generatedAt).toISOString().slice(0, 10))}</div>
  </header>

  ${
    failing
      ? `<div class="banner fail">${failing} creative${failing === 1 ? '' : 's'} did not pass checks and ${failing === 1 ? 'is' : 'are'} shown for review only.</div>`
      : ''
  }

  <div class="step"><span class="n">1</span><h2>Choose a direction</h2><span>Both carry the same promise. Pick the one that sounds like you.</span></div>
  <div class="choices">${chooser}</div>

  <div class="step"><span class="n">2</span><h2>Check every size</h2><span>Shown at true pixel size — this is exactly how they will run.</span></div>

  <div class="toolbar">
    <button class="tool" id="squint" aria-pressed="false" title="Blur the creative to test whether one message still comes through">Squint test</button>
    <button class="tool" id="context" aria-pressed="false">See it in a page</button>
    <span class="hint">Squint until it blurs. If you can still tell what it says, it works at a glance.</span>
  </div>

  ${panels}

  <div class="decide">
    <h2>Your decision</h2>
    <p>Approve to move into production, or edit below and rebuild — your changes are applied to every size and the proof updates right here.</p>

    <div class="editor">
      <div class="erow">
        <label>Headline</label>
        <input type="text" id="ed-headline" maxlength="60" placeholder="Headline">
      </div>
      <div class="erow">
        <label>Supporting line</label>
        <input type="text" id="ed-support" maxlength="90" placeholder="Supporting line">
      </div>
      <div class="erow">
        <label>Button text</label>
        <input type="text" id="ed-cta" maxlength="24" placeholder="Button text">
      </div>
      <div class="erow cols">
        <div>
          <label>Button colour</label>
          <input type="color" id="ed-accent" value="#F2B705">
        </div>
        <div>
          <label>Button text colour</label>
          <input type="color" id="ed-ctatext" value="#12202E">
        </div>
        <div>
          <label>Headline colour</label>
          <input type="color" id="ed-headink" value="#12202E">
        </div>
      </div>
      <div class="erow">
        <label>Logo</label>
        <div class="logo-acts">
          <button type="button" class="mini" id="logo-clean" title="Remove any background box and tidy the logo, keeping your mark exactly as is">Clean up logo (remove background)</button>
          <label class="mini-check"><input type="checkbox" id="logo-reverse"> also make a white version for dark ads</label>
        </div>
        <p class="hint" style="margin:6px 0 0">Cleaning keeps your exact logo — it only removes a background box and trims dead space. Applies to every size on rebuild.</p>
      </div>
      <div class="erow">
        <label>Background (offer concept)</label>
        <div class="logo-acts">
          <button type="button" class="mini bg-mode on" data-bgmode="solid">Solid colour</button>
          <button type="button" class="mini bg-mode" data-bgmode="pixabay">Suggested photos</button>
          <button type="button" class="mini bg-mode" data-bgmode="ai">AI from your page</button>
        </div>
        <div id="ed-bgResults" style="display:none;margin-top:10px"></div>
        <p class="hint" style="margin:6px 0 0">Photos are kept readable automatically with an overlay. Applies on rebuild.
        Need to crop or edit an image first? Use the <a href="https://smart1marketing.com/image-optimizer" target="_blank" rel="noopener" style="color:var(--signal);font-weight:600">Smart 1 image optimizer</a>.</p>
      </div>
      <p class="hint" id="ed-status"></p>
    </div>

    <textarea id="changes" placeholder="Anything else? For example: use a warmer background photo, or make the offer bigger on the tall sizes."></textarea>
    <div class="acts">
      <button class="act primary" id="approve">Approve concept <span id="pickLabel">A</span></button>
      <button class="act" id="rebuild">Apply changes &amp; rebuild</button>
      <button class="act" id="request">Send notes to the team</button>
    </div>
  </div>

  <footer>Sizes and file weights meet current Google Display and Amazon DSP requirements. Colours may vary slightly between screens.</footer>
</div>

<div class="size-editor" id="sizeEditor">
  <div class="box">
    <h3>Edit <span id="se-size">300x250</span> only</h3>
    <p class="sub">Change the copy for just this one size. Every other size stays as it is.</p>
    <label>Headline</label>
    <input type="text" id="se-headline" maxlength="60">
    <label>Supporting line</label>
    <input type="text" id="se-support" maxlength="90">
    <label>Button text</label>
    <input type="text" id="se-cta" maxlength="24">
    <label style="display:flex;align-items:center;gap:8px;text-transform:none;font-size:13px;margin-top:14px">
      <input type="checkbox" id="se-reverselogo" style="width:auto"> Use the white logo on this size (for dark or photo backgrounds)
    </label>
    <label>Picture placement</label>
    <input type="text" id="se-bgpos" maxlength="120" placeholder="e.g. show more of the left side / move the photo down">
    <p class="sub" style="margin:2px 0 0;font-size:12px">Tell us in plain words how the photo should sit on this size. We reposition it when you apply.</p>
    <label>Different logo for this size</label>
    <input type="file" id="se-logofile" accept="image/png,image/jpeg,image/webp,image/svg+xml" style="font-size:13px;padding:8px 0;border:0">
    <p class="sub" style="margin:2px 0 0;font-size:12px">A rectangular logo often needs a square variant on square sizes. Upload one here — it applies to this size only.</p>
    <div class="row">
      <button id="se-cancel">Cancel</button>
      <button class="primary" id="se-apply">Apply to this size</button>
    </div>
  </div>
</div>

<script>
window.PROOF_ENDPOINT = ${opts.actionBase ? JSON.stringify(opts.actionBase) : 'null'};
window.PROOF_COPY = ${JSON.stringify(opts.initialCopy ?? {})};
window.PROOF_COLORS = ${JSON.stringify(opts.initialColors ?? {})};
window.PROOF_META = ${JSON.stringify(opts.meta ?? {})};
window.PROOF_PERSIZE = ${JSON.stringify(opts.perSizeCopy ?? {})};
window.PROOF_DELIVERED = ${JSON.stringify(opts.delivered ?? null)};
(function () {
  'use strict';
  var body = document.body;

  function setPressed(btn, on) { btn.setAttribute('aria-pressed', on ? 'true' : 'false'); }

  // Squint test. The review technique the creative guidance implies: if the
  // focal hierarchy survives a blur, it survives a one-second glance.
  var squint = document.getElementById('squint');
  squint.addEventListener('click', function () {
    var on = body.classList.toggle('squint');
    setPressed(squint, on);
  });

  var context = document.getElementById('context');
  context.addEventListener('click', function () {
    var on = context.getAttribute('aria-pressed') !== 'true';
    setPressed(context, on);
    var panels = document.querySelectorAll('.panel');
    for (var i = 0; i < panels.length; i++) {
      panels[i].querySelector('.context').hidden = !on;
      panels[i].querySelector('.grid').style.display = on ? 'none' : '';
    }
  });

  var picked = document.querySelector('input[name=concept]:checked');
  var label = document.getElementById('pickLabel');

  function choose(id) {
    var choices = document.querySelectorAll('.choice');
    for (var i = 0; i < choices.length; i++) {
      choices[i].classList.toggle('on', choices[i].dataset.concept === id);
    }
    var panels = document.querySelectorAll('.panel');
    for (var j = 0; j < panels.length; j++) {
      panels[j].classList.toggle('on', panels[j].dataset.panel === id);
    }
    label.textContent = id;
    prefillEditor(id);
  }

  // Load the current copy for the chosen concept into the editor so edits
  // start from what is actually on the ads, not a blank box.
  function prefillEditor(id) {
    var c = (window.PROOF_COPY && window.PROOF_COPY[id]) || {};
    var byId = function (x) { return document.getElementById(x); };
    if (byId('ed-headline')) byId('ed-headline').value = c.headline || '';
    if (byId('ed-support')) byId('ed-support').value = c.support || '';
    if (byId('ed-cta')) byId('ed-cta').value = c.cta || '';
    var col = window.PROOF_COLORS || {};
    if (col.accent && byId('ed-accent')) byId('ed-accent').value = col.accent;
    if (col.ctaText && byId('ed-ctatext')) byId('ed-ctatext').value = col.ctaText;
    if (col.headline && byId('ed-headink')) byId('ed-headink').value = col.headline;
  }

  var radios = document.querySelectorAll('input[name=concept]');
  for (var k = 0; k < radios.length; k++) {
    radios[k].addEventListener('change', function () { choose(this.value); });
  }

  function decision(kind) {
    var id = (document.querySelector('input[name=concept]:checked') || {}).value;
    var notes = document.getElementById('changes').value.trim();
    if (kind === 'revision' && !notes) {
      document.getElementById('changes').focus();
      return;
    }
    parent.postMessage({ type: 'smart1:decision', decision: kind, concept: id, notes: notes }, '*');

    // Record it server-side. The proof may be opened from an email, so the
    // page cannot assume a parent frame is listening.
    if (window.PROOF_ENDPOINT) {
      fetch(window.PROOF_ENDPOINT + '/' + (kind === 'approve' ? 'approve' : 'revision'), {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ concept: id, notes: notes })
      })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          if (kind === 'approve' && res && res.downloadUrl) showDownload(res.downloadUrl, res.fileCount);
        })
        .catch(function () { /* the client already saw confirmation */ });
    }
    var box = document.querySelector('.decide');
    if (kind === 'approve') {
      box.innerHTML = '<h2>Approved</h2>' +
        '<p><span class="spinner"></span> Packaging your finished files\u2026</p>';
    } else {
      box.innerHTML = '<h2>Changes requested</h2>' +
        '<p>Thanks \u2014 your notes are with the team and a revised proof is on the way.</p>';
    }
  }
  // When approval comes back with a download link, show the button right here.
  function showDownload(url, count) {
    var box = document.querySelector('.decide');
    var _pp2 = location.pathname.split('/proof/');
    var ridHere = _pp2.length > 1 ? _pp2[1].split('/')[0].split('?')[0].split('#')[0] : '';
    var b = 'display:inline-block;text-decoration:none;padding:13px 24px;border-radius:8px;font-weight:600;font-size:15px;margin:0 10px 10px 0;';
    box.innerHTML = '<h2>Approved \u2014 your ads are ready</h2>' +
      '<p>' + (count ? count + ' finished files, ' : '') + 'organised by platform and named for upload. What next?</p>' +
      '<a href="' + url + '" download style="' + b + 'background:#1F5FC0;color:#fff">Download ads</a>' +
      '<a href="/overview/' + ridHere + '" style="' + b + 'background:#fff;color:#1F5FC0;border:1px solid #C9D4E0">Review campaign overview</a>' +
      '<a href="/embed" style="' + b + 'background:#fff;color:#1F5FC0;border:1px solid #C9D4E0">Make another campaign</a>' +
      '<a href="/projects" style="' + b + 'background:#fff;color:#1F5FC0;border:1px solid #C9D4E0">Search campaigns</a>';
  }

  document.getElementById('approve').addEventListener('click', function () { decision('approve'); });
  document.getElementById('request').addEventListener('click', function () { decision('revision'); });

  // Logo clean-up toggle
  var logoClean = document.getElementById('logo-clean');
  var wantLogoClean = false;
  if (logoClean) {
    logoClean.addEventListener('click', function () {
      wantLogoClean = !wantLogoClean;
      logoClean.classList.toggle('on', wantLogoClean);
    });
  }

  // Background mode + image selection
  var bgSel = { mode: 'solid', url: null };
  function apiBase() {
    // The rebuild endpoint is /api/proof/<pid>; image endpoints are /api/images/*
    // Derive the /api base from PROOF_ENDPOINT (e.g. '/api/proof/<pid>')
    // without a regex — regex literals get mangled inside this template.
    var ep = window.PROOF_ENDPOINT || '';
    var cut = ep.indexOf('/proof/');
    return cut >= 0 ? ep.slice(0, cut) : ep;
  }
  var bgModeBtns = document.querySelectorAll('.bg-mode');
  for (var m = 0; m < bgModeBtns.length; m++) {
    bgModeBtns[m].addEventListener('click', function () {
      for (var i = 0; i < bgModeBtns.length; i++) bgModeBtns[i].classList.toggle('on', bgModeBtns[i] === this);
      var mode = this.dataset.bgmode;
      bgSel = { mode: mode, url: null };
      var box = document.getElementById('ed-bgResults');
      if (mode === 'solid') { box.style.display = 'none'; box.innerHTML = ''; return; }
      box.style.display = '';
      box.innerHTML = '<span class="hint">Finding images…</span>';
      var meta = window.PROOF_META || {};
      var endpoint = mode === 'ai' ? '/api/images/generate' : '/api/images/search';
      fetch(apiBase() + endpoint, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ business: meta.business, promoting: meta.promoting, objective: meta.objective })
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var cands = mode === 'ai' ? (data.candidate ? [data.candidate] : []) : (data.candidates || []);
          if (!cands.length) {
            box.innerHTML = '<span class="hint">' + (data.reason || 'No images available.') + '</span>';
            bgSel = { mode: 'solid', url: null };
            return;
          }
          box.innerHTML = '';
          cands.forEach(function (c) {
            var img = document.createElement('img');
            img.src = c.url; img.className = 'bg-thumb'; img.alt = 'Background option';
            img.style.cssText = 'width:120px;height:68px;border-radius:8px;border:2px solid #dfe6ee;object-fit:cover;cursor:pointer;margin:4px';
            img.addEventListener('click', function () {
              var all = box.querySelectorAll('img');
              for (var t = 0; t < all.length; t++) all[t].style.borderColor = '#dfe6ee';
              img.style.borderColor = '#1f5fc0';
              bgSel = { mode: 'image', url: c.url };
            });
            box.appendChild(img);
          });
        })
        .catch(function () { box.innerHTML = '<span class="hint">Could not load images.</span>'; });
    });
  }

  // Live rebuild: apply the editor's copy + colours to the chosen concept,
  // re-render every size on the server, and reload onto the fresh proof. This
  // is the "changes reconfigure the ads right here" behaviour.
  var rebuildBtn = document.getElementById('rebuild');
  if (rebuildBtn) {
    rebuildBtn.addEventListener('click', function () {
      if (!window.PROOF_ENDPOINT) return;
      var id = (document.querySelector('input[name=concept]:checked') || {}).value;
      var status = document.getElementById('ed-status');
      rebuildBtn.disabled = true;
      var orig = rebuildBtn.textContent;
      rebuildBtn.textContent = 'Rebuilding all sizes…';
      status.textContent = 'Applying your changes and re-rendering every size. This takes a few seconds.';

      var payload = {
        conceptId: id,
        copy: {
          headline: document.getElementById('ed-headline').value.trim(),
          support: document.getElementById('ed-support').value.trim(),
          cta: document.getElementById('ed-cta').value.trim()
        },
        colors: {
          accent: document.getElementById('ed-accent').value,
          ctaText: document.getElementById('ed-ctatext').value,
          headline: document.getElementById('ed-headink').value
        }
      };
      if (wantLogoClean) {
        payload.logo = { aiRework: true, reversed: document.getElementById('logo-reverse').checked };
      }
      if (bgSel.mode === 'solid') {
        payload.background = { mode: 'solid' };
      } else if (bgSel.url) {
        payload.background = { mode: 'image', url: bgSel.url };
      }
      fetch(window.PROOF_ENDPOINT + '/rebuild', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload)
      })
        .then(function (r) { return r.json(); })
        .then(function (res) {
          // The server returns immediately and renders in the background;
          // poll the public status endpoint and reload when it's done.
          status.textContent = 'Rebuilding in the background — this page will refresh itself when the new ads are ready. You can keep looking around.';
          var _pp = location.pathname.split('/proof/');
          var ridFromPath = _pp.length > 1 ? _pp[1].split('/')[0].split('?')[0].split('#')[0] : '';
          var target = (res.proofUrl || location.pathname);
          (function pollReady() {
            fetch('/api/requests/' + ridFromPath + '/status')
              .then(function (r2) { return r2.json(); })
              .then(function (st) {
                if (st.ready && st.state === 'ready') location.href = target + '?u=' + Date.now();
                else setTimeout(pollReady, 2500);
              })
              .catch(function () { setTimeout(pollReady, 4000); });
          })();
        })
        .catch(function () {
          rebuildBtn.disabled = false;
          rebuildBtn.textContent = orig;
          status.textContent = 'That did not go through. Please try again, or send notes to the team.';
        });
    });
  }

  prefillEditor((document.querySelector('input[name=concept]:checked') || {}).value || 'A');

  // Already approved and packaged? The download stays right here on the proof,
  // however many times the page is opened. Nobody needs an email to find it.
  if (window.PROOF_DELIVERED && window.PROOF_DELIVERED.zipUrl) {
    showDownload(window.PROOF_DELIVERED.zipUrl, window.PROOF_DELIVERED.fileCount);
  }

  /* -------------------------------------- per-banner "edit this size" */
  var sizeModal = document.getElementById('sizeEditor');
  var seCtx = { concept: null, size: null };
  function openSizeEditor(concept, size) {
    seCtx = { concept: concept, size: size };
    document.getElementById('se-size').textContent = size;
    // Prefer the size-specific copy; fall back to the concept default.
    var key = concept + '/' + size;
    var c = (window.PROOF_PERSIZE && window.PROOF_PERSIZE[key])
      || (window.PROOF_COPY && window.PROOF_COPY[concept]) || {};
    document.getElementById('se-headline').value = c.headline || '';
    document.getElementById('se-support').value = c.support || '';
    document.getElementById('se-cta').value = c.cta || '';
    sizeModal.classList.add('on');
  }
  var editButtons = document.querySelectorAll('.edit-size');
  for (var eb = 0; eb < editButtons.length; eb++) {
    editButtons[eb].addEventListener('click', function () {
      openSizeEditor(this.dataset.concept, this.dataset.size);
    });
  }
  document.getElementById('se-cancel').addEventListener('click', function () {
    sizeModal.classList.remove('on');
  });
  var seLogoInput = document.getElementById('se-logofile');
  if (seLogoInput) {
    seLogoInput.addEventListener('change', function () {
      window.__seLogoData = null;
      var f = this.files && this.files[0];
      if (!f) return;
      if (f.size > 2 * 1024 * 1024) { alert('Please keep the logo under 2 MB.'); this.value = ''; return; }
      var rd = new FileReader();
      rd.onload = function () { window.__seLogoData = rd.result; };
      rd.readAsDataURL(f);
    });
  }
  sizeModal.addEventListener('click', function (e) {
    if (e.target === sizeModal) sizeModal.classList.remove('on');
  });
  document.getElementById('se-apply').addEventListener('click', function () {
    if (!window.PROOF_ENDPOINT) return;
    var btn = this; btn.disabled = true; btn.textContent = 'Rebuilding this size…';
    fetch(window.PROOF_ENDPOINT + '/rebuild', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        conceptId: seCtx.concept,
        size: seCtx.size,
        copy: {
          headline: document.getElementById('se-headline').value.trim(),
          support: document.getElementById('se-support').value.trim(),
          cta: document.getElementById('se-cta').value.trim()
        },
        reverseLogo: document.getElementById('se-reverselogo').checked,
        sizeLogo: window.__seLogoData ? { dataUrl: window.__seLogoData } : undefined,
        picturePlacement: (document.getElementById('se-bgpos').value || '').trim() || undefined
      })
    })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        btn.textContent = 'Rebuilding in background…';
        var _pp = location.pathname.split('/proof/');
          var ridFromPath = _pp.length > 1 ? _pp[1].split('/')[0].split('?')[0].split('#')[0] : '';
        (function pollReady() {
          fetch('/api/requests/' + ridFromPath + '/status')
            .then(function (r2) { return r2.json(); })
            .then(function (st) {
              if (st.ready && st.state === 'ready') location.href = (res.proofUrl || location.pathname) + '?u=' + Date.now();
              else setTimeout(pollReady, 2500);
            })
            .catch(function () { setTimeout(pollReady, 4000); });
        })();
      })
      .catch(function () { btn.disabled = false; btn.textContent = 'Apply to this size'; });
  });
})();
</script>
</body>
</html>`;
}
