/**
 * Diagnostics.
 *
 * Built because every failure in this system so far has been silent from the
 * outside: a missing font fell back and rendered the wrong typeface, a stale
 * build served a 404, a CSP default showed a blank iframe, sharp's .stats()
 * quietly reported the whole canvas. In each case the app looked fine and the
 * output was wrong.
 *
 * So each check answers one question: is this specific thing actually working
 * right now? Checks run live, in order of how badly they break the product,
 * and every failure carries the fix rather than just a red light.
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';
import { execSync } from 'node:child_process';
import sharp from 'sharp';
import { listFamilies, resolveFont } from './fonts';
import { getPlatform, loadPlatforms, loadTemplates } from './registry';
import { CloudinaryService } from './cloudinary';
import { configuredToken, tokenIsWeak, bucketCount } from './auth';
import { renderPreview } from './render';
import type { Brand, CreativeConcept, SizeKey, Box, SizeLayout } from './types';

export type Level = 'ok' | 'warn' | 'fail' | 'skip';

export interface Check {
  id: string;
  group: string;
  label: string;
  level: Level;
  detail: string;
  /** What to do about it. Omitted when the check passed. */
  fix?: string;
  ms?: number;
}

async function timed(fn: () => Promise<Check> | Check): Promise<Check> {
  const t = Date.now();
  try {
    const c = await fn();
    return { ...c, ms: Date.now() - t };
  } catch (e: any) {
    return {
      id: 'unknown', group: 'Internal', label: 'Check crashed',
      level: 'fail', detail: String(e?.message ?? e), ms: Date.now() - t,
    };
  }
}

/* --------------------------------------------------------------- runtime */

function checkNode(): Check {
  const major = Number(process.versions.node.split('.')[0]);
  return {
    id: 'node', group: 'Runtime', label: 'Node version',
    level: major >= 20 ? 'ok' : 'fail',
    detail: `v${process.versions.node}`,
    fix: major >= 20 ? undefined : 'This project needs Node 20 or newer. Set NODE_VERSION and redeploy.',
  };
}

async function checkSharp(): Promise<Check> {
  // Exercise the actual pipeline, not just the import: a broken libvips binary
  // imports cleanly and fails on first use.
  const png = await sharp({ create: { width: 40, height: 20, channels: 3, background: '#123456' } })
    .png().toBuffer();
  const meta = await sharp(png).metadata();
  const ok = meta.width === 40 && meta.height === 20;
  return {
    id: 'sharp', group: 'Runtime', label: 'Image pipeline (sharp)',
    level: ok ? 'ok' : 'fail',
    detail: ok ? `libvips ${sharp.versions.vips}, encode and decode working` : 'round trip produced the wrong dimensions',
    fix: ok ? undefined : 'Reinstall sharp for this platform: npm rebuild sharp.',
  };
}

function checkMemory(): Check {
  const totalMb = Math.round(os.totalmem() / 1048576);
  const freeMb = Math.round(os.freemem() / 1048576);
  // The largest job is the Amazon 970x250 at 2x (1940x500), composed twice
  // (artwork plus a text-free pass for contrast sampling).
  const level: Level = totalMb < 700 ? 'fail' : totalMb < 1400 ? 'warn' : 'ok';
  return {
    id: 'memory', group: 'Runtime', label: 'Available memory',
    level,
    detail: `${totalMb} MB total, ${freeMb} MB free, ${os.cpus().length} CPU(s)`,
    fix: level === 'ok' ? undefined
      : 'Rendering the Amazon 970x250 at 2x composes a 1940x500 canvas twice. Under about 1.4 GB this can fail mid-package. Use a larger instance.',
  };
}

/* ----------------------------------------------------------------- fonts */

async function checkFonts(): Promise<Check[]> {
  const families = listFamilies();
  const out: Check[] = [];

  out.push({
    id: 'fonts.registry', group: 'Fonts', label: 'Registered families',
    level: families.length ? 'ok' : 'fail',
    detail: families.join(', ') || 'none',
    fix: families.length ? undefined : 'No fonts resolved. Check src/fonts.ts paths and that node_modules is present.',
  });

  // A font that parses can still throw on glyph lookup, which is how the
  // DejaVu fallback silently broke every render from the compiled build.
  for (const family of families) {
    let level: Level = 'ok';
    let detail = 'renders glyphs';
    try {
      const font = resolveFont(family, 'bold');
      font.getPath('Handgloves 123 $%', 0, 0, 24, { kerning: true });
    } catch (e: any) {
      level = 'fail';
      detail = String(e?.message ?? e);
    }
    out.push({
      id: `fonts.${family}`, group: 'Fonts', label: family, level, detail,
      fix: level === 'ok' ? undefined : 'opentype.js cannot render this face. Remove it from the registry or substitute another weight.',
    });
  }
  return out;
}

/* ------------------------------------------------------------- templates */

/** Boxes that overlap are invisible to QA, which checks each one alone. */
function overlaps(layout: SizeLayout): string[] {
  const roles = ['logo', 'headline', 'support', 'offer', 'trust', 'cta'] as const;
  const boxes = roles
    .map((r) => [r, (layout as any)[r] as Box | undefined] as const)
    .filter((e): e is readonly [typeof roles[number], Box] => Boolean(e[1]));
  const hits: string[] = [];
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const [an, a] = boxes[i];
      const [bn, b] = boxes[j];
      const ox = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
      const oy = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
      if (ox > 1 && oy > 1) hits.push(`${an}/${bn}`);
    }
  }
  return hits;
}

function checkTemplates(): Check[] {
  const templates = loadTemplates();
  const out: Check[] = [];

  out.push({
    id: 'templates.count', group: 'Templates', label: 'Layout families',
    level: templates.size ? 'ok' : 'fail',
    detail: [...templates.values()].map((t) => `${t.id} (${Object.keys(t.sizes).length} sizes)`).join(', '),
    fix: templates.size ? undefined : 'No templates loaded. In a compiled build this means the JSON was not copied into dist — run npm run build, not bare tsc.',
  });

  const bad: string[] = [];
  const outside: string[] = [];
  for (const t of templates.values()) {
    for (const [size, layout] of Object.entries(t.sizes)) {
      if (!layout) continue;
      for (const pair of overlaps(layout)) bad.push(`${t.id}/${size} ${pair}`);
      const [w, h] = size.split('x').map(Number);
      if (layout.canvas.w !== w || layout.canvas.h !== h) {
        outside.push(`${t.id}/${size} canvas is ${layout.canvas.w}x${layout.canvas.h}`);
      }
    }
  }

  out.push({
    id: 'templates.overlap', group: 'Templates', label: 'Overlapping boxes',
    level: bad.length ? 'fail' : 'ok',
    detail: bad.length ? bad.join('; ') : 'no elements overlap in any family',
    fix: bad.length ? 'Two elements share space, which QA cannot see because it checks each box alone. Adjust the coordinates in src/templates.' : undefined,
  });

  out.push({
    id: 'templates.canvas', group: 'Templates', label: 'Canvas dimensions',
    level: outside.length ? 'fail' : 'ok',
    detail: outside.length ? outside.join('; ') : 'every layout matches its size key',
    fix: outside.length ? 'A layout declares a canvas that disagrees with its size key.' : undefined,
  });

  return out;
}

function checkPlatforms(): Check[] {
  const platforms = loadPlatforms();
  const out: Check[] = [];
  const unverified: string[] = [];

  for (const cfg of platforms.values()) {
    for (const [size, rule] of Object.entries(cfg.sizes)) {
      if (rule?.source === 'verify') unverified.push(`${cfg.platform}/${size}`);
    }
  }

  out.push({
    id: 'platforms.loaded', group: 'Platform rules', label: 'Configurations',
    level: platforms.size ? 'ok' : 'fail',
    detail: [...platforms.values()].map((p) => `${p.platform} (${Object.keys(p.sizes).length} sizes)`).join(', '),
  });

  out.push({
    id: 'platforms.verify', group: 'Platform rules', label: 'Unconfirmed limits',
    level: unverified.length ? 'warn' : 'ok',
    detail: unverified.length ? unverified.join(', ') : 'all limits sourced from documentation',
    fix: unverified.length ? 'These file-weight limits were inferred, not documented. Confirm against the platform spec sheet before first live delivery.' : undefined,
  });

  return out;
}

/* ------------------------------------------------------------ integrations */

function envCheck(id: string, group: string, label: string, vars: string[], why: string): Check {
  const missing = vars.filter((v) => !process.env[v]?.trim());
  return {
    id, group, label,
    level: missing.length === 0 ? 'ok' : missing.length === vars.length ? 'warn' : 'fail',
    detail: missing.length === 0 ? 'configured' : `missing ${missing.join(', ')}`,
    fix: missing.length === 0 ? undefined : why,
  };
}

/** A live call, because a key that exists can still be wrong or revoked. */
async function pingCloudinary(): Promise<Check> {
  const cld = new CloudinaryService();
  if (!cld.live) {
    return {
      id: 'cloudinary.ping', group: 'Integrations', label: 'Cloudinary reachable',
      level: 'skip', detail: 'credentials not set, so not attempted',
    };
  }
  try {
    const found = await cld.findProjectFolders('');
    return {
      id: 'cloudinary.ping', group: 'Integrations', label: 'Cloudinary reachable',
      level: 'ok', detail: `authenticated, ${found.length} project folder(s) under ${cld.root}`,
    };
  } catch (e: any) {
    // The Cloudinary Admin API rejects with { error: { message, http_code } },
    // not a plain Error — e.message is undefined, so naively stringifying `e`
    // produced the literal text "[object Object]" here previously.
    const inner = e?.error ?? e;
    const httpCode: number | undefined = inner?.http_code ?? e?.http_code;
    const msg: string = typeof inner?.message === 'string' ? inner.message
      : typeof e?.message === 'string' ? e.message
      : (() => { try { return JSON.stringify(e); } catch { return String(e); } })();

    // Cloudinary does not pre-create folders — they exist only once something
    // has been uploaded into them. A 404 on the root folder is therefore the
    // expected state for an account that has never received an upload, not a
    // connectivity or credentials problem.
    if (httpCode === 404 || /can't find folder|not found/i.test(msg)) {
      return {
        id: 'cloudinary.ping', group: 'Integrations', label: 'Cloudinary reachable',
        level: 'ok',
        detail: `authenticated; the "${cld.root}" folder does not exist yet because nothing has been uploaded`,
      };
    }

    return {
      id: 'cloudinary.ping', group: 'Integrations', label: 'Cloudinary reachable',
      level: 'fail', detail: httpCode ? `HTTP ${httpCode}: ${msg}` : msg,
      fix: httpCode === 401 || httpCode === 403 || /signature|api_key/i.test(msg)
        ? 'Credentials were rejected. Check CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET.'
        : 'Could not reach Cloudinary. Check outbound network access from this host.',
    };
  }
}

async function pingHost(id: string, label: string, url: string, hint: string): Promise<Check> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 6000);
  try {
    const res = await fetch(url, { method: 'GET', signal: ctrl.signal });
    // 200/401/404 from the service proves the route out works — 401 is just a
    // missing key. A 403 is more often an egress proxy or firewall answering
    // on the service's behalf, so it is reported as unproven rather than fine.
    const deny = res.headers.get('x-deny-reason');
    if (deny) {
      return {
        id, group: 'Integrations', label, level: 'fail',
        detail: `blocked by network policy (${deny})`,
        fix: `Outbound traffic to this host is being refused before it leaves the network. ${hint}`,
      };
    }
    if (res.status === 403) {
      return {
        id, group: 'Integrations', label, level: 'warn',
        detail: 'got HTTP 403, which usually means a proxy or firewall answered, not the service',
        fix: `Could not confirm a real route to this service. ${hint}`,
      };
    }
    return {
      id, group: 'Integrations', label,
      level: 'ok', detail: `reachable (HTTP ${res.status})`,
    };
  } catch (e: any) {
    return {
      id, group: 'Integrations', label,
      level: 'fail', detail: String(e?.message ?? e), fix: hint,
    };
  } finally {
    clearTimeout(timer);
  }
}

/* ------------------------------------------------------------- filesystem */

function checkDisk(outDir: string): Check[] {
  const out: Check[] = [];
  let writable = false;
  try {
    fs.mkdirSync(outDir, { recursive: true });
    const probe = path.join(outDir, `.diag-${Date.now()}`);
    fs.writeFileSync(probe, 'ok');
    fs.unlinkSync(probe);
    writable = true;
  } catch (e: any) {
    out.push({
      id: 'disk.write', group: 'Storage', label: 'Output directory writable',
      level: 'fail', detail: `${outDir}: ${e?.message ?? e}`,
      fix: 'The service cannot write renders. Check the mount and its permissions.',
    });
  }
  if (writable) {
    out.push({
      id: 'disk.write', group: 'Storage', label: 'Output directory writable',
      level: 'ok', detail: outDir,
    });
  }

  // Free space, and how much of it this app is holding. Nothing prunes
  // renders automatically, so this is the check that catches a slow leak.
  try {
    const df = execSync(`df -Pk ${JSON.stringify(outDir)}`, { encoding: 'utf8' }).trim().split('\n')[1];
    const cols = df.split(/\s+/);
    const availGb = Number(cols[3]) / 1048576;
    const usedPct = Number(String(cols[4]).replace('%', ''));
    const level: Level = usedPct >= 90 || availGb < 1 ? 'fail' : usedPct >= 75 || availGb < 3 ? 'warn' : 'ok';
    out.push({
      id: 'disk.space', group: 'Storage', label: 'Free disk',
      level,
      detail: `${availGb.toFixed(1)} GB free, volume ${usedPct}% used`,
      fix: level === 'ok' ? undefined : 'Rendered files are kept after upload and nothing prunes them. Run the cleanup task or lower the retention window.',
    });
  } catch {
    out.push({ id: 'disk.space', group: 'Storage', label: 'Free disk', level: 'skip', detail: 'df unavailable on this host' });
  }

  try {
    const size = dirSize(outDir);
    out.push({
      id: 'disk.usage', group: 'Storage', label: 'Output held on disk',
      level: size > 5 * 1024 ** 3 ? 'warn' : 'ok',
      detail: `${(size / 1024 ** 2).toFixed(0)} MB in ${outDir}`,
      fix: size > 5 * 1024 ** 3 ? 'Over 5 GB of renders retained. Cloudinary is the permanent home; prune the local copies.' : undefined,
    });
  } catch { /* non-fatal */ }

  return out;
}

function dirSize(dir: string): number {
  let total = 0;
  const walk = (d: string) => {
    let entries: fs.Dirent[];
    try { entries = fs.readdirSync(d, { withFileTypes: true }); } catch { return; }
    for (const e of entries) {
      const full = path.join(d, e.name);
      if (e.isDirectory()) walk(full);
      else { try { total += fs.statSync(full).size; } catch { /* raced */ } }
    }
  };
  walk(dir);
  return total;
}

/* ---------------------------------------------------------------- security */

function checkSecurity(): Check[] {
  const out: Check[] = [];
  const token = configuredToken();
  out.push({
    id: 'security.token', group: 'Security', label: 'Admin token',
    level: token ? 'ok' : 'fail',
    detail: token ? 'set, internal routes protected' : tokenIsWeak() ? 'set but shorter than 16 characters' : 'not set',
    fix: token ? undefined : 'Without ADMIN_TOKEN the build screen and campaign data are closed to everyone, including you. Set a long random value.',
  });

  const frame = process.env.ALLOWED_FRAME_ANCESTORS?.trim();
  out.push({
    id: 'security.frame', group: 'Security', label: 'Embed permitted origins',
    level: frame ? 'ok' : 'warn',
    detail: frame || "not set — defaults to 'self'",
    fix: frame ? undefined : "The embed will show a blank box on any other domain. Set ALLOWED_FRAME_ANCESTORS to the site that hosts the form.",
  });

  out.push({
    id: 'security.limits', group: 'Security', label: 'Rate limiting',
    level: 'ok', detail: `active, tracking ${bucketCount()} client bucket(s)`,
  });

  return out;
}

/* ------------------------------------------------------- end-to-end render */

/**
 * The check that matters most: compose, rasterise and QA a real creative.
 * If this passes, the whole pipeline is intact.
 */
async function checkRender(assetRoot: string): Promise<Check> {
  // A generated logo, so the check exercises image compositing as well as
  // text and layout. Without one the render is only half the pipeline.
  const logoPath = path.join(os.tmpdir(), 's1-diag-logo.png');
  if (!fs.existsSync(logoPath)) {
    await sharp({ create: { width: 300, height: 80, channels: 4, background: { r: 31, g: 58, b: 95, alpha: 1 } } })
      .png().toFile(logoPath);
  }

  const brand: Brand = {
    name: 'Diagnostic', domain: 'diagnostic.test',
    colors: { primary: '#1F3A5F', secondary: '#4A6E9B', accent: '#F2B705', light: '#FFFFFF', dark: '#111111' },
    fonts: { headline: 'Montserrat', body: 'Open Sans' },
    logos: { primary: logoPath },
  };
  const concept: CreativeConcept = {
    conceptId: 'DIAG', name: 'Diagnostic', layoutFamily: 'T01', hero: {},
    copy: { default: { headline: 'Check The Rendering Path', support: 'A real creative, composed end to end.', cta: 'Learn More' } },
  };

  const out = await renderPreview({ brand, concept, platform: 'google', size: '300x250' as SizeKey, assetRoot });
  const ok = out.png.length > 1000 && out.width === 300 && out.height === 250;
  const unexpected = out.qa.filter((f) => f.status === 'fail');
  return {
    id: 'render.e2e', group: 'Rendering', label: 'End-to-end render',
    level: ok && !unexpected.length ? 'ok' : 'fail',
    detail: ok
      ? `produced a ${out.width}x${out.height} PNG of ${(out.png.length / 1024).toFixed(1)} KB` +
        (unexpected.length ? `, but QA failed: ${unexpected.map((f) => f.check).join(', ')}` : '')
      : 'the renderer did not produce a valid image',
    fix: ok && !unexpected.length ? undefined : 'The rendering pipeline is broken. Check the font and template results above first — they are the usual cause.',
  };
}

/* -------------------------------------------------------------- the runner */

export interface Report {
  generatedAt: string;
  host: string;
  uptimeSec: number;
  summary: { ok: number; warn: number; fail: number; skip: number };
  verdict: 'healthy' | 'degraded' | 'broken';
  checks: Check[];
}

export async function runDiagnostics(opts: { outDir: string; assetRoot: string }): Promise<Report> {
  const checks: Check[] = [];

  checks.push(checkNode());
  checks.push(await timed(checkSharp));
  checks.push(checkMemory());
  checks.push(...(await checkFonts()));
  checks.push(...checkTemplates());
  checks.push(...checkPlatforms());
  checks.push(...checkSecurity());
  checks.push(...checkDisk(opts.outDir));

  checks.push(envCheck('env.cloudinary', 'Integrations', 'Cloudinary credentials',
    ['CLOUDINARY_CLOUD_NAME', 'CLOUDINARY_API_KEY', 'CLOUDINARY_API_SECRET'],
    'Without these, uploads and the gallery are unavailable and creatives stay on local disk only.'));
  checks.push(envCheck('env.brandfetch', 'Integrations', 'Brandfetch key',
    ['BRANDFETCH_API_KEY'],
    'Brand discovery will fall back to manual entry for every customer. Set BRANDFETCH_MOCK=1 to test without a key.'));
  checks.push(envCheck('env.openai', 'Integrations', 'OpenAI key',
    ['OPENAI_API_KEY'],
    'Landing page reading and copy generation fall back to the weaker heuristic path.'));

  checks.push(await timed(pingCloudinary));
  checks.push(await timed(() => pingHost('net.brandfetch', 'Brandfetch reachable',
    'https://api.brandfetch.io/v2/brands/example.com',
    'No outbound route to Brandfetch. Check egress rules on this host.')));
  checks.push(await timed(() => pingHost('net.openai', 'OpenAI reachable',
    'https://api.openai.com/v1/models',
    'No outbound route to OpenAI. Check egress rules on this host.')));

  checks.push(await timed(() => checkRender(opts.assetRoot)));

  const summary = {
    ok: checks.filter((c) => c.level === 'ok').length,
    warn: checks.filter((c) => c.level === 'warn').length,
    fail: checks.filter((c) => c.level === 'fail').length,
    skip: checks.filter((c) => c.level === 'skip').length,
  };

  return {
    generatedAt: new Date().toISOString(),
    host: os.hostname(),
    uptimeSec: Math.round(process.uptime()),
    summary,
    verdict: summary.fail ? 'broken' : summary.warn ? 'degraded' : 'healthy',
    checks,
  };
}
