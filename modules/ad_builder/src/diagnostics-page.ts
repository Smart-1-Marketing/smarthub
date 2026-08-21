/**
 * The diagnostics page.
 *
 * Read under pressure, usually when something is already broken, so it is
 * ordered by consequence: the verdict first, then failures, then everything
 * else. Each failure states the fix inline — a red light you have to go and
 * interpret is only half a diagnosis.
 */

import type { Check, Report } from './diagnostics';

const esc = (v: unknown) =>
  String(v ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);

const LEVEL_LABEL: Record<string, string> = { ok: 'PASS', warn: 'WARN', fail: 'FAIL', skip: 'SKIP' };

function row(c: Check): string {
  return `<tr class="${esc(c.level)}">
    <td class="lvl"><span class="tag ${esc(c.level)}">${LEVEL_LABEL[c.level]}</span></td>
    <td>
      <div class="label">${esc(c.label)}</div>
      <div class="detail">${esc(c.detail)}</div>
      ${c.fix ? `<div class="fix"><b>Fix</b> ${esc(c.fix)}</div>` : ''}
    </td>
    <td class="ms">${c.ms !== undefined ? c.ms + 'ms' : ''}</td>
  </tr>`;
}

export interface JobRow {
  id: string; status: string; progress: { done: number; total: number };
  startedAt?: string; finishedAt?: string; error?: string;
}

function jobsSection(jobs: JobRow[]): string {
  if (!jobs.length) return '';
  const age = (iso?: string) => {
    if (!iso) return '—';
    const m = Math.round((Date.now() - Date.parse(iso)) / 60000);
    return m < 1 ? 'just now' : m < 60 ? `${m}m ago` : `${Math.round(m / 60)}h ago`;
  };
  const rows = jobs.slice(-12).reverse().map((j) => `
    <tr>
      <td class="mono">${j.id}</td>
      <td><span class="lvl ${j.status === 'complete' ? 'ok' : j.status === 'failed' ? 'fail' : 'warn'}">${j.status.toUpperCase()}</span></td>
      <td class="mono">${j.progress.done}/${j.progress.total}</td>
      <td>${age(j.startedAt)}</td>
      <td>${j.error ? `<span class="detail">${j.error.slice(0, 140)}</span>` : ''}</td>
    </tr>`).join('');
  return `
  <h2>Render jobs (this instance)</h2>
  <table style="width:100%;border-collapse:collapse">
    <thead><tr>
      <th style="text-align:left;padding:8px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-2)">Job</th>
      <th style="text-align:left;padding:8px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-2)">Status</th>
      <th style="text-align:left;padding:8px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-2)">Sizes</th>
      <th style="text-align:left;padding:8px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-2)">Started</th>
      <th style="text-align:left;padding:8px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--ink-2)">Error</th>
    </tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

export function renderDiagnostics(r: Report, jobs: JobRow[] = []): string {
  const groups = new Map<string, Check[]>();
  for (const c of r.checks) {
    if (!groups.has(c.group)) groups.set(c.group, []);
    groups.get(c.group)!.push(c);
  }

  const problems = r.checks.filter((c) => c.level === 'fail' || c.level === 'warn');

  const verdictCopy = {
    healthy: 'Everything checked is working.',
    degraded: 'Working, but something is not configured or is close to a limit.',
    broken: 'Something is broken. Start with the failures below.',
  }[r.verdict];

  return `<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Diagnostics — Smart 1 Ad Builder</title>
<style>
  :root {
    --bg:#14171C; --panel:#1D2027; --line:#2E333D; --ink:#EDEFF3; --ink-2:#98A1B0;
    --ok:#35C089; --warn:#E0A72B; --fail:#EF6B62; --skip:#6C7484;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;-webkit-font-smoothing:antialiased}
  .wrap{max-width:920px;margin:0 auto;padding:32px 20px 80px}
  h1{font-size:22px;margin:0 0 4px;letter-spacing:-.01em}
  .meta{color:var(--ink-2);font-size:12.5px;font-family:ui-monospace,Menlo,monospace}
  .verdict{display:flex;gap:14px;align-items:center;margin:22px 0;padding:16px 18px;
    border-radius:10px;background:var(--panel);border:1px solid var(--line);border-left-width:4px}
  .verdict.healthy{border-left-color:var(--ok)} .verdict.degraded{border-left-color:var(--warn)}
  .verdict.broken{border-left-color:var(--fail)}
  .verdict b{display:block;font-size:16px;margin-bottom:2px}
  .verdict p{margin:0;color:var(--ink-2);font-size:13px}
  .tallies{margin-left:auto;display:flex;gap:8px;flex-wrap:wrap}
  .tally{font-family:ui-monospace,Menlo,monospace;font-size:12px;padding:4px 9px;border-radius:5px;background:#0F1216;border:1px solid var(--line)}
  .tally.ok{color:var(--ok)} .tally.warn{color:var(--warn)} .tally.fail{color:var(--fail)} .tally.skip{color:var(--skip)}
  h2{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--ink-2);margin:30px 0 8px}
  table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:9px;overflow:hidden}
  td{padding:11px 12px;border-bottom:1px solid var(--line);vertical-align:top}
  tr:last-child td{border-bottom:none}
  .lvl{width:64px}
  .tag{display:inline-block;font-family:ui-monospace,Menlo,monospace;font-size:10.5px;font-weight:600;
    padding:2px 6px;border-radius:4px;letter-spacing:.06em}
  .tag.ok{background:rgba(53,192,137,.14);color:var(--ok)}
  .tag.warn{background:rgba(224,167,43,.14);color:var(--warn)}
  .tag.fail{background:rgba(239,107,98,.15);color:var(--fail)}
  .tag.skip{background:rgba(108,116,132,.16);color:var(--skip)}
  .label{font-weight:600;font-size:13.5px}
  .detail{color:var(--ink-2);font-size:12.5px;font-family:ui-monospace,Menlo,monospace;
    word-break:break-word;margin-top:2px}
  .fix{margin-top:7px;font-size:12.5px;color:#E8D6AE;background:rgba(224,167,43,.08);
    border-left:2px solid var(--warn);padding:7px 10px;border-radius:0 5px 5px 0}
  .fix b{color:var(--warn);margin-right:5px}
  .ms{width:58px;text-align:right;color:var(--skip);font-family:ui-monospace,Menlo,monospace;font-size:11.5px}
  .bar{display:flex;gap:8px;margin:18px 0 0}
  button,a.btn{font:inherit;font-size:13px;background:var(--panel);color:var(--ink);
    border:1px solid var(--line);border-radius:6px;padding:8px 14px;cursor:pointer;text-decoration:none}
  button:hover,a.btn:hover{border-color:var(--ink-2)}
  footer{margin-top:34px;color:var(--skip);font-size:12px}
</style>
</head><body><div class="wrap">

  <nav style="display:flex;gap:2px;margin:0 0 18px">
    <a href="/build" style="color:var(--ink-2);text-decoration:none;font-size:13px;font-weight:600;padding:6px 11px;border-radius:6px">Build</a>
    <a href="/projects" style="color:var(--ink-2);text-decoration:none;font-size:13px;font-weight:600;padding:6px 11px;border-radius:6px">Projects</a>
    <a href="/diagnostics" style="color:var(--ink);background:var(--panel);text-decoration:none;font-size:13px;font-weight:600;padding:6px 11px;border-radius:6px">Diagnostics</a>
  </nav>
  <h1>Diagnostics</h1>
  <div class="meta">${esc(r.host)} · ${esc(r.generatedAt)} · up ${Math.floor(r.uptimeSec / 60)}m</div>

  <div class="verdict ${esc(r.verdict)}">
    <div>
      <b>${r.verdict === 'healthy' ? 'Healthy' : r.verdict === 'degraded' ? 'Degraded' : 'Broken'}</b>
      <p>${esc(verdictCopy)}</p>
    </div>
    <div class="tallies">
      <span class="tally ok">${r.summary.ok} pass</span>
      <span class="tally warn">${r.summary.warn} warn</span>
      <span class="tally fail">${r.summary.fail} fail</span>
      <span class="tally skip">${r.summary.skip} skip</span>
    </div>
  </div>

  ${problems.length ? `<h2>Needs attention</h2><table>${problems.map(row).join('')}</table>` : ''}

  ${[...groups.entries()].map(([g, list]) =>
    `<h2>${esc(g)}</h2><table>${list.map(row).join('')}</table>`).join('')}

  ${jobsSection(jobs)}

  <div class="bar">
    <button onclick="location.reload()">Run again</button>
    <a class="btn" href="?format=json">View as JSON</a>
  </div>

  <footer>Checks run live against this process. Integration checks make a real
  outbound call, so a failure here means this host genuinely cannot reach that
  service.</footer>
</div></body></html>`;
}
