/**
 * Render Web Service entrypoint.
 *
 * Deliberately thin. Rendering a full package takes seconds and hits Cloudinary
 * and (later) OpenAI, which is exactly what Render tells you not to do inside a
 * request handler. The HTTP layer accepts a job, validates it synchronously so
 * the caller gets a real error immediately, and returns 202. A worker does the
 * slow part.
 *
 * Node's built-in http is used rather than Express because this only needs four
 * routes. Swap it out when the form and proof screens land.
 */

import * as http from 'node:http';
import { withBase } from './basepath.js';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as crypto from 'node:crypto';
import type { Campaign } from './types';
import { validateCampaign } from './validate';
import { enqueue, getJob, listJobs, startWorkerLoop, recoverJobs, startWatchdog } from './jobs';
import { renderPreview } from './render';
import { buildCampaign, type Submission } from './intake';
import { loadTemplates } from './registry';
import { ProjectStore } from './projects';
import { analyzeLandingPage } from './landing';
import { landingImages } from './landing-images';
import { checkAuth, denied, rateLimit, sessionCookie, configuredToken, sweepBuckets, intakeCodeOk, intakeAllowed } from './auth';
import { runDiagnostics } from './diagnostics';
import { renderDiagnostics } from './diagnostics-page';
import { scheduleSweep, sweep } from './retention';
import { deliverProject, latestManifest } from './deliver';
import { renderProof } from './proof';
import { suggestCopy, critiqueCopy } from './copy-approval';
import { searchPixabay, generateHero, imageKeywords } from './imagery';
import { reworkLogo } from './logo-tools';
import { resolveAsset } from './assets';
import { fitImageToBudget } from './image-budget';
import { getPlatform } from './registry';
import { listFamilies } from './fonts';
import sharp from 'sharp';
import { notify } from './notify';
import { discoverBrand, normalizeDomain } from './brandfetch';
import { BrandCache } from './brand-cache';
import { renderOverview, renderOverviewPdf } from './overview';
import { ALLOWED_FORMATS, assetUrlIsSafe, folderFor, signUpload, type AssetKind } from './assets';
import { CloudinaryService, slug } from './cloudinary';

const ROOT = path.resolve(__dirname, '..', '..');
const OUT = process.env.OUTPUT_DIR ?? path.join(ROOT, 'out');
const PUBLIC = path.join(ROOT, 'public');
const PORT = Number(process.env.PORT ?? 3000);

/** Build stamp written by the build step; tells us what is actually deployed. */
const BUILD_STAMP: { builtAt?: string; node?: string } = (() => {
  try { return JSON.parse(fs.readFileSync(path.join(__dirname, 'build-stamp.json'), 'utf8')); }
  catch { return { builtAt: 'unknown (dev / unbuilt)' }; }
})();
/** Base URL for links inside notifications. Set on Render to the public host. */
const PUBLIC_URL = (process.env.PUBLIC_URL ?? `http://localhost:${PORT}`).replace(/\/$/, '');
const projects = new ProjectStore(OUT);
function newRequestIdFor(): string {
  // Same alphabet and entropy as intake ids: unguessable, no confusable chars.
  return `AD-${new Date().getFullYear()}-` +
    Array.from(crypto.randomBytes(8), (b) => 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'[b % 31]).join('').slice(0, 8);
}

const brandCache = new BrandCache(path.join(OUT, 'brand-cache'));

/**
 * Sites allowed to iframe the embed. Browsers block framing unless the framed
 * page permits it, so the Simvoly domain must be listed here or the embed
 * renders as a blank box with a console error and no other clue.
 */
const FRAME_ANCESTORS = (process.env.ALLOWED_FRAME_ANCESTORS ?? "'self'")
  .split(/[\s,]+/)
  .filter(Boolean)
  .join(' ');

/** Origins allowed to POST to the API. The embed fetches same-origin, but a
 *  customer pasting the form markup directly into their page would not. */
const CORS_ORIGINS = (process.env.ALLOWED_ORIGINS ?? '')
  .split(/[\s,]+/)
  .filter(Boolean);

function corsHeaders(origin?: string): Record<string, string> {
  if (!origin || !CORS_ORIGINS.includes(origin)) return {};
  return {
    'access-control-allow-origin': origin,
    'access-control-allow-methods': 'POST, GET, OPTIONS',
    'access-control-allow-headers': 'content-type',
    'access-control-max-age': '86400',
  };
}

/**
 * Render sets PORT and expects a service of its own to bind 0.0.0.0.
 *
 * Inside the Hub container this runs as a second process behind a Flask
 * proxy, and binding every interface there would publish an admin-token
 * service on the same host as the Hub for anything that can reach the
 * container. HOST=127.0.0.1 keeps it on loopback, which is all the proxy
 * needs; the default is unchanged so a standalone deployment still works.
 */
const HOST = process.env.HOST ?? '0.0.0.0';

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.csv': 'text/csv; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
};

function json(
  res: http.ServerResponse,
  code: number,
  body: unknown,
  extra: Record<string, string> = {},
): void {
  const payload = JSON.stringify(body, null, 2);
  res.writeHead(code, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(payload),
    ...extra,
  });
  res.end(payload);
}

async function readBody(req: http.IncomingMessage, limit = 2_000_000): Promise<string> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw new Error('Request body too large');
    chunks.push(chunk as Buffer);
  }
  return Buffer.concat(chunks).toString('utf8');
}

/** Serve report and creative files without escaping the output directory. */
function serveStatic(res: http.ServerResponse, urlPath: string): boolean {
  const rel = decodeURIComponent(urlPath.replace(/^\/files\/?/, ''));
  const target = path.resolve(OUT, rel);
  if (!target.startsWith(path.resolve(OUT))) {
    json(res, 403, { error: 'Path outside the output directory' });
    return true;
  }
  if (!fs.existsSync(target) || fs.statSync(target).isDirectory()) return false;
  res.writeHead(200, { 'content-type': MIME[path.extname(target)] ?? 'application/octet-stream' });
  fs.createReadStream(target).pipe(res);
  return true;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url ?? '/', `http://${req.headers.host}`);
  const route = `${req.method} ${url.pathname}`;

  /**
   * Routes that expose or modify client work. Everything not listed is public
   * because an embedded form on a customer's site has to reach it. New routes
   * default to public, so anything sensitive must be added here deliberately.
   */
  const isInternal =
    url.pathname === '/build' ||
    url.pathname === '/build.html' ||
    url.pathname === '/projects' ||
    url.pathname === '/projects.html' ||
    url.pathname === '/diagnostics' ||
    (url.pathname.startsWith('/api/campaign') && url.pathname !== '/api/campaign-search') ||
    url.pathname.startsWith('/api/campaigns') ||
    url.pathname.startsWith('/api/project') ||
    url.pathname.startsWith('/api/build/') ||
    // Both are editor-only: they fetch a picture on the server's behalf and
    // write it into a campaign's cache directory. The SSRF guard and the rate
    // limiter already stood behind them, but the only caller is build.html,
    // which is internal and reaches them through the Hub proxy with the admin
    // token attached -- so there is no reason to leave the work open.
    route === 'POST /api/background/apply' ||
    route === 'POST /api/logo/apply' ||
    url.pathname.startsWith('/api/diagnostics') ||
    (url.pathname.startsWith('/files/') && !url.pathname.startsWith('/files/imagery/') && !url.pathname.startsWith('/files/gallery/') && !url.pathname.startsWith('/files/deliveries/') && !url.pathname.startsWith('/files/renders/')) ||
    route === 'POST /api/render' ||
    url.pathname.startsWith('/api/render/');

  try {
    if (isInternal) {
      const auth = checkAuth(req, url);
      if (!auth.ok) {
        console.warn(`[auth] refused ${route} from ${req.headers['x-forwarded-for'] ?? req.socket.remoteAddress}`);
        return denied(res, 401, auth.reason ?? 'Unauthorised');
      }
      // Move a token supplied in the query string into a cookie, so it stops
      // appearing in the address bar, browser history and referrer headers.
      const q = url.searchParams.get('token');
      if (q && configuredToken() === q) res.setHeader('set-cookie', sessionCookie(q));
    }

    const limit = rateLimit(route, req);
    if (!limit.allowed) {
      return denied(res, 429, 'Too many requests. Please try again shortly.', {
        'retry-after': String(limit.retryAfterSec),
      });
    }

    // Render's health check. Must stay cheap and dependency-free — if this
    // touches Cloudinary, an outage there takes the whole service down.
    if (route === 'GET /healthz' || route === 'GET /') {
      return json(res, 200, {
        status: 'ok',
        service: 'smart1-ad-builder',
        builtAt: BUILD_STAMP.builtAt,
        uptime: Math.round(process.uptime()),
        queued: listJobs().filter((j) => j.status === 'queued').length,
        embed: {
          path: '/embed',
          available: fs.existsSync(path.join(PUBLIC, 'embed.html')),
          // The commonest embed failure is this being left at the default,
          // which permits only same-origin framing. Show it plainly.
          frameAncestors: FRAME_ANCESTORS,
          configured: Boolean(process.env.ALLOWED_FRAME_ANCESTORS),
        },
      });
    }

    if (route === 'GET /version') {
      return json(res, 200, BUILD_STAMP);
    }

    // The embeddable intake form. Framed by the customer's marketing site, so
    // it sets frame-ancestors rather than relying on the default.
    if (route === 'GET /embed' || route === 'GET /embed.html') {
      const file = path.join(PUBLIC, 'embed.html');
      if (!fs.existsSync(file)) return json(res, 404, { error: 'Embed page not built' });
      const html = fs.readFileSync(file, 'utf8')
        .replaceAll('__NEEDS_CODE__', process.env.INTAKE_CODE ? 'true' : 'false');
      res.writeHead(200, {
        'content-type': 'text/html; charset=utf-8',
        'content-security-policy': `frame-ancestors ${FRAME_ANCESTORS}`,
        // X-Frame-Options has no allow-list beyond a single origin, and CSP
        // supersedes it in every browser we care about. Setting it here would
        // only break multi-domain embedding.
        'cache-control': 'public, max-age=300',
      });
      return res.end(withBase(req, html));
    }

    if (req.method === 'OPTIONS') {
      res.writeHead(204, corsHeaders(req.headers.origin));
      return res.end();
    }

    // Intake submissions from the embedded form.
    // The intake surface honors the team access code when one is set. The
    // check sits before rate limiting so a wrong code cannot burn the budget.
    const intakeSurface =
      route === 'POST /api/requests' ||
      route === 'POST /api/brand/discover' ||
      route === 'POST /api/landing/analyze' ||
      route === 'POST /api/copy/suggest' ||
      route === 'POST /api/images/search' ||
      route === 'POST /api/images/generate' ||
      route === 'POST /api/assets/upload-signature';
    if (intakeSurface && !intakeAllowed(req, url)) {
      return json(res, 401, { error: 'A valid team access code is required.' }, corsHeaders(req.headers.origin));
    }

    if (route === 'POST /api/intake/verify') {
      const cors = corsHeaders(req.headers.origin);
      const limit = rateLimit(route, req);
      if (!limit.allowed) return json(res, 429, { error: 'Too many attempts. Wait a while.' }, cors);
      return json(res, intakeCodeOk(req) ? 200 : 401, { ok: intakeCodeOk(req) }, cors);
    }

    if (route === 'POST /api/requests') {
      const cors = corsHeaders(req.headers.origin);
      const body = JSON.parse(await readBody(req, 200_000)) as Record<string, any>;

      // Bot trap. Two signals must AGREE before we treat a submission as a
      // bot, because either one alone has a real-world false positive:
      //   - the hidden text field, which browser autofill / password managers
      //     will fill for a genuine human despite autocomplete=off;
      //   - submission speed: a human spends at least a couple of seconds, a
      //     script posts instantly.
      // A filled honeypot with human-plausible timing is almost always
      // autofill, so we let it through (and clear the field). Only a filled
      // honeypot AND an instant submit is rejected — and even then we LOG it,
      // so it shows up in the Render logs rather than vanishing as a silent
      // fake success id.
      const trap = String(body.honeypot ?? '').trim();
      const elapsed = typeof body.elapsedMs === 'number' ? body.elapsedMs : null;
      const instant = elapsed !== null && elapsed < 1200;
      if (trap && (instant || elapsed === null)) {
        console.warn(`[intake] REJECTED as bot: honeypot="${trap.slice(0, 40)}" elapsedMs=${elapsed}`);
        return json(res, 200, { requestId: `AD-${new Date().getFullYear()}-000000`, rejected: true }, cors);
      }
      if (trap) {
        console.warn(`[intake] honeypot was filled (elapsedMs=${elapsed}) but timing looks human — treating as autofill, allowing through`);
      }

      const missing = ['business', 'website', 'contact', 'email', 'campaignName', 'promoting']
        .filter((k) => !String(body[k] ?? '').trim());
      if (missing.length) {
        return json(res, 400, { error: `Missing required fields: ${missing.join(', ')}` }, cors);
      }

      // Random, not timestamp-derived: the requestId doubles as the proof
      // link capability, so it must not be enumerable. 8 chars of A-Z0-9 is
      // ~40 bits — unguessable at any polite request rate.
      const requestId = `AD-${new Date().getFullYear()}-` +
        Array.from(crypto.randomBytes(8), (b) => 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'[b % 31]).join('').slice(0, 8);
      console.log(`[intake] ${requestId} accepted from ${body.email ?? '?'} — building campaign…`);
      const record = { requestId, receivedAt: new Date().toISOString(), ...body };
      const dir = path.join(OUT, 'requests');
      fs.mkdirSync(dir, { recursive: true });
      fs.writeFileSync(path.join(dir, `${requestId}.json`), JSON.stringify(record, null, 2));
      console.log(`[intake] ${requestId} ${body.business} <${body.email}>`);

      // Build the campaign immediately so the request lands in the build queue
      // ready to open, rather than as an inert JSON file someone has to notice.
      // Failure here must not lose the submission — the request is already saved.
      let build: { renderable: boolean; notes: string[] } | undefined;
      try {
        const result = await buildCampaign({ requestId, ...body } as Submission, {
          assetRoot: ROOT,
          cacheDir: path.join(OUT, 'cache', requestId),
          outputDir: OUT,
        });
        fs.mkdirSync(path.join(OUT, 'campaigns'), { recursive: true });
        fs.writeFileSync(
          path.join(OUT, 'campaigns', `${requestId}.json`),
          JSON.stringify({
            campaign: result.campaign,
            platforms: (body.platforms ?? ['google']).filter((p: string) => p === 'google' || p === 'amazon'),
            notes: result.notes,
            assetSources: result.assetSources,
          }, null, 2),
        );
        build = { renderable: result.renderable, notes: result.notes };

        // Every submission becomes a dated, searchable project record.
        const cld = new CloudinaryService();
        const project = projects.create({
          projectName: String(body.projectName || `${body.campaignName} — ${body.business}`).trim(),
          client: body.business,
          domain: result.campaign.brand.domain,
          campaignName: body.campaignName,
          requestId,
          landingPage: body.landingPage,
          contact: body.contact,
          email: body.email,
          brand: result.campaign.brand,
          brandEnteredManually: Boolean(body.brandManual),
          cloudinaryFolder: cld.projectFolder(body.business, body.campaignName),
          keywords: [body.promoting, body.benefit, body.offer, body.audience, body.geography, body.objective]
            .filter(Boolean).map(String),
          notes: result.notes,
        });
        const now = new Date().toISOString();
        for (const [kind, source] of Object.entries(result.assetSources)) {
          if (source === 'none') continue;
          project.assets.push({ kind: kind as any, source: source as any, addedAt: now });
        }
        for (const up of (body.uploadedLogos ?? []) as any[]) {
          if (up?.url) project.assets.push({ kind: 'logo', source: 'upload', url: up.url, publicId: up.publicId, name: up.name, addedAt: now });
        }
        for (const up of (body.uploadedImages ?? []) as any[]) {
          if (up?.url) {
            const isBg = body.backgroundChoice?.mode === 'upload' && body.backgroundChoice?.url === up.url;
            project.assets.push({ kind: isBg ? 'background' : 'product', source: 'upload', url: up.url, publicId: up.publicId, name: up.name, addedAt: now });
          }
        }
        // Log the website + brand details we pulled, with the fetch time, so a
        // refresh decision (>3 months) has something to check against later.
        if (result.campaign.brand && body.landingPage) {
          project.notes.push(`[${now}] Brand details on file for ${body.website ?? body.landingPage} (source: ${body.brandSource ?? 'brandfetch'}).`);
        }
        projects.save(project);
        (build as any).projectId = project.projectId;
        console.log(`[intake] ${requestId} -> project ${project.projectId}, renderable=${result.renderable}` +
          (result.renderable ? '' : ` (not renderable: ${result.notes.slice(0, 2).join('; ')})`));

        // Read the landing page in the background: the customer should not wait
        // on a third-party fetch to see their confirmation screen.
        if (body.landingPage) {
          analyzeLandingPage(String(body.landingPage))
            .then((analysis) => {
              const p = projects.get(project.projectId);
              if (p) { p.landingAnalysis = analysis; projects.save(p); }
              console.log(`[landing] ${project.projectId} analysed via ${analysis.source}`);
            })
            .catch((e) => console.warn(`[landing] ${project.projectId} failed: ${e?.message ?? e}`));
        }
        // Renderable submissions go straight into the queue. A customer who
        // uploaded a logo and a photo should not need staff to notice their
        // request before anything happens.
        if (result.renderable) {
          const platforms = (body.platforms ?? ['google']).filter(
            (p: string) => p === 'google' || p === 'amazon',
          );
          // First look BEFORE the batch job. Order matters on a small
          // instance: one 300x250 rendered alone lands in seconds, while the
          // same render competing with a 20-size batch for one starved CPU is
          // exactly how a customer watches a skeleton for 17 minutes.
          // STAGED FLOW: render the 250x250 in three template families and
          // stop. The person picks a template (with edits) before the rest
          // builds — no point rendering 20 sizes of a look they may reject.
          const pRef = project;
          (async () => {
            const t0 = Date.now();
            const FAMILIES = ['T01', 'T02', 'T03'];
            const dir = path.join(OUT, 'tplprev', requestId);
            fs.mkdirSync(dir, { recursive: true });
            for (const fam of FAMILIES) {
              try {
                const prev = await renderPreview({
                  brand: result.campaign.brand,
                  concept: { ...result.campaign.concepts[0], layoutFamily: fam },
                  platform: platforms[0] ?? 'google',
                  size: '250x250',
                  assetRoot: ROOT,
                });
                fs.writeFileSync(path.join(dir, `${fam}.png`), prev.png);
                console.log(`[tplprev] ${requestId} ${fam} done at ${Date.now() - t0}ms`);
              } catch (e: any) {
                console.warn(`[tplprev] ${requestId} ${fam}: ${e?.message ?? e}`);
              }
            }
            projects.save(pRef);

          // Staff heads-up: previews are ready and the customer is choosing a
          // template. The full-build notification fires from the choose route.
          await notify(
            {
              subject: `New request — ${project.client} / ${project.projectName}`,
              body: `"${project.campaignName}" was submitted. Template previews are rendered and the customer is choosing a direction; remaining sizes build when they pick.`,
              url: `${PUBLIC_URL}/build?request=${requestId}`,
            },
            OUT,
          );
          })().catch((e: any) => console.error(`[intake] ${requestId} background render failed: ${e?.message ?? e}`));
        } else {
          // Not renderable — usually missing a logo. Still worth a heads-up so
          // staff know a customer is waiting on an asset request, not nothing.
          await notify(
            {
              subject: `New request needs attention — ${project.client} / ${project.projectName}`,
              body: `"${project.campaignName}" was submitted but is not ready to render yet:\n${result.notes.map((n) => `- ${n}`).join('\n')}`,
              url: `${PUBLIC_URL}/build?request=${requestId}`,
            },
            OUT,
          );
        }
      } catch (e: any) {
        console.error(`[intake] ${requestId} campaign build failed: ${e?.message ?? e}`);
      }

      // TODO: push to HighLevel as a Creative Request custom object once the
      // custom object exists there.
      return json(res, 201, { requestId, status: 'received', build }, cors);
    }

    /* --------------------------------------------------- brand discovery */
    if (route === 'POST /api/brand/discover') {
      const cors = corsHeaders(req.headers.origin);
      const body = JSON.parse(await readBody(req, 20_000)) as { domain?: string };
      const domain = normalizeDomain(body.domain ?? '');
      if (!domain || !domain.includes('.')) {
        return json(res, 400, { error: 'Provide a website domain' }, cors);
      }
      try {
        // Cache Brandfetch results for ~3 months. A domain looked up recently
        // is served from disk; only a stale (or forced) lookup hits the API.
        // `refresh=1` on the query forces a re-fetch.
        const forceRefresh = url.searchParams.get('refresh') === '1';
        const { result: found, cached, ageDays } = await brandCache.getOrFetch(
          domain,
          () => discoverBrand(domain),
          { refresh: forceRefresh },
        );
        console.log(`[brand] ${domain}: ${cached ? `cache hit (${ageDays}d old)` : 'fetched fresh from Brandfetch'}`);
        return json(res, 200, { ...(found as object), cached, ageDays }, cors);
      } catch (e: any) {
        // Discovery failing is normal for small businesses with no public
        // brand record. It must not block the request — the customer just
        // fills the details in and uploads a logo.
        console.log(`[brand] lookup failed for ${domain}: ${e?.message ?? e}`);
        return json(
          res,
          200,
          {
            brand: null,
            found: false,
            reason: e?.message ?? 'Lookup failed',
            warnings: ['We could not find your brand automatically. Please enter your details and upload your logo.'],
            needsReview: true,
            source: 'fallback',
          },
          cors,
        );
      }
    }

    /* ------------------------------------------------------ upload signing */
    if (route === 'POST /api/assets/upload-signature') {
      const cors = corsHeaders(req.headers.origin);
      const body = JSON.parse(await readBody(req, 20_000)) as {
        client?: string;
        campaign?: string;
        kind?: AssetKind;
        filename?: string;
      };
      const { client, campaign, kind } = body;
      if (!client || !campaign || !kind) {
        return json(res, 400, { error: 'client, campaign and kind are required' }, cors);
      }
      const ext = path.extname(body.filename ?? '').replace('.', '').toLowerCase();
      if (ext && !(ALLOWED_FORMATS as readonly string[]).includes(ext)) {
        return json(
          res,
          400,
          { error: `Unsupported file type ".${ext}". Accepted: ${ALLOWED_FORMATS.join(', ')}` },
          cors,
        );
      }

      const cloudName = process.env.CLOUDINARY_CLOUD_NAME;
      const apiKey = process.env.CLOUDINARY_API_KEY;
      const apiSecret = process.env.CLOUDINARY_API_SECRET;
      if (!cloudName || !apiKey || !apiSecret) {
        return json(res, 503, { error: 'Asset storage is not configured on the server.' }, cors);
      }

      const cld = new CloudinaryService();
      const signed = signUpload({
        folder: folderFor(cld, client, campaign, kind),
        // Slugged, because Cloudinary tags are comma-delimited and a client
        // name containing a comma would split into bogus tags.
        tags: [
          `client:${slug(client)}`,
          `campaign:${slug(campaign)}`,
          `kind:${slug(kind)}`,
          'source:customer-upload',
        ],
        apiKey,
        apiSecret,
        cloudName,
      });
      return json(res, 200, signed, cors);
    }

    /* -------------------------------------------- public proof + status */
    // The customer-facing side of the pipeline. Both are public by capability:
    // the requestId in the URL is the ticket. Neither route touches /files/,
    // which stays admin-only — the proof page inlines its images as data URIs
    // so a client needs exactly one URL and no token.

    // Polled by the confirmation screen after submission.
    const statusMatch = url.pathname.match(/^\/api\/requests\/([\w-]+)\/status$/);
    if (statusMatch && req.method === 'GET') {
      const cors = corsHeaders(req.headers.origin);
      const requestId = statusMatch[1];
      const requestFile = path.join(OUT, 'requests', `${requestId}.json`);
      if (!fs.existsSync(requestFile)) return json(res, 404, { error: 'Unknown request' }, cors);
      const manifest = latestManifest(OUT, requestId);
      const ready = Boolean(manifest && manifest.entries.length);
      const project = projects.byRequest(requestId);
      const job = project?.autoJobId ? getJob(project.autoJobId) : null;
      // 'rendering' while a job is queued or running; 'failed' if it died;
      // 'waiting-assets' when nothing was renderable (usually: no logo yet).
      const prevDir = path.join(OUT, 'tplprev', requestId);
      const previews = fs.existsSync(prevDir)
        ? fs.readdirSync(prevDir).filter((f) => f.endsWith('.png'))
            .map((f) => ({ template: f.replace('.png', ''), url: `/tplprev/${requestId}/${f}` }))
        : [];
      const state = ready ? 'ready'
        : job ? (job.status === 'failed' ? 'failed' : 'rendering')
        : previews.length ? 'choose-template'
        : 'waiting-assets';
      let firstLookFile = path.join(OUT, 'firstlook', `${requestId}.png`);
      if (!fs.existsSync(firstLookFile) && manifest) {
        // The quick render can lose a race with a restart; the batch output
        // contains the same creative, so serve that instead of nothing.
        const entry = manifest.entries.find((e) => e.size === '300x250' && fs.existsSync(e.localFile));
        if (entry) {
          fs.mkdirSync(path.dirname(firstLookFile), { recursive: true });
          fs.copyFileSync(entry.localFile, firstLookFile);
        }
      }
      return json(res, 200, {
        received: true,
        ready,
        state,
        progress: job ? job.progress : null,
        firstLook: fs.existsSync(firstLookFile) ? `/firstlook/${requestId}.png` : null,
        templatePreviews: previews,
        proofUrl: ready ? `/proof/${requestId}` : null,
      }, cors);
    }

    const tpMatch = url.pathname.match(/^\/tplprev\/([\w-]+)\/(T\d+)\.png$/);
    if (tpMatch && req.method === 'GET') {
      const f = path.join(OUT, 'tplprev', tpMatch[1], `${tpMatch[2]}.png`);
      if (!fs.existsSync(f)) return json(res, 404, { error: 'Not ready' });
      res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'no-store' });
      return res.end(fs.readFileSync(f));
    }

    const flMatch = url.pathname.match(/^\/firstlook\/([\w-]+)\.png$/);
    if (flMatch && req.method === 'GET') {
      const f = path.join(OUT, 'firstlook', `${flMatch[1]}.png`);
      if (!fs.existsSync(f)) return json(res, 404, { error: 'Not ready' });
      res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'no-store' });
      return res.end(fs.readFileSync(f));
    }

    const ovMatch = url.pathname.match(/^\/overview\/([\w-]+)(\/pdf)?$/);
    if (ovMatch && req.method === 'GET') {
      const requestId = ovMatch[1];
      const manifest = latestManifest(OUT, requestId);
      const project = projects.byRequest(requestId);
      if (!project) return json(res, 404, { error: 'Unknown campaign' });
      let campaign: any = null; let submission: any = undefined;
      try {
        const doc = JSON.parse(fs.readFileSync(path.join(OUT, 'campaigns', `${requestId}.json`), 'utf8'));
        campaign = doc.campaign ?? null;
      } catch { /* overview still renders from the project record */ }
      try {
        // The intake record is the submission of record — every field the
        // customer typed, exactly as received.
        submission = JSON.parse(fs.readFileSync(path.join(OUT, 'requests', `${requestId}.json`), 'utf8'));
      } catch { /* fall back to project/campaign fields */ }
      const brandRec = project.domain ? brandCache.read(project.domain) : null;
      const delivered = project.delivered?.length ? project.delivered[project.delivered.length - 1] : null;
      const ads = (manifest?.entries ?? [])
        .filter((e: any) => fs.existsSync(e.localFile))
        .map((e: any) => ({
          size: e.size, delivered: e.deliveredDimensions, platform: e.platform,
          file: e.localFile, bytes: e.bytes, qaStatus: e.qaStatus,
          url: `/files/${path.relative(OUT, e.localFile).split(path.sep).join('/')}`,
        }));
      const data = {
        requestId, project, campaign, submission,
        brandCacheRecord: brandRec, ads,
        publicUrl: PUBLIC_URL, proofUrl: `/proof/${requestId}`,
        downloadUrl: delivered?.zipUrl,
      };
      if (ovMatch[2]) {
        const pdf = await renderOverviewPdf(data as any);
        res.writeHead(200, { 'content-type': 'application/pdf',
          'content-disposition': `inline; filename="${slug(project.client)}_${slug(project.campaignName)}_overview.pdf"` });
        return res.end(pdf);
      }
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      return res.end(withBase(req, renderOverview(data as any)));
    }

    // Overview ad images must be publicly viewable via their /files paths —
    // covered below by the renders exemption added to the admin gate.

    const proofMatch = url.pathname.match(/^\/proof\/([\w-]+)$/);
    if (proofMatch && req.method === 'GET') {
      const requestId = proofMatch[1];
      const manifest = latestManifest(OUT, requestId);
      if (!manifest || !manifest.entries.length) {
        res.writeHead(404, { 'content-type': 'text/html; charset=utf-8' });
        return res.end('<!doctype html><meta charset="utf-8"><title>Not ready</title>' +
          '<body style="font:16px/1.5 system-ui;color:#16222E;padding:40px;text-align:center">' +
          '<h2>Your previews are still being prepared</h2>' +
          '<p>This page will work as soon as rendering finishes — usually under a minute. Refresh to check.</p>');
      }
      const project = projects.byRequest(requestId);
      // Pull current copy + palette from the stored campaign so the proof's
      // live editor starts from what is actually on the ads.
      const initialCopy: Record<string, { headline?: string; support?: string; cta?: string }> = {};
      const perSizeCopy: Record<string, { headline?: string; support?: string; cta?: string }> = {};
      let initialColors: { accent?: string; ctaText?: string; headline?: string } = {};
      let proofMeta: { business?: string; promoting?: string; objective?: string } = {};
      try {
        const campFile = path.join(OUT, 'campaigns', `${requestId}.json`);
        if (fs.existsSync(campFile)) {
          const doc = JSON.parse(fs.readFileSync(campFile, 'utf8'));
          for (const c of doc.campaign?.concepts ?? []) {
            const d = c.copy?.default ?? {};
            initialCopy[c.conceptId] = { headline: d.headline, support: d.support, cta: d.cta };
            // Per-size copy for the inline "edit this size" editor: the
            // effective copy for a size is default overlaid with any per-size
            // override, matching how the renderer resolves it.
            for (const key of Object.keys(c.copy ?? {})) {
              if (key === 'default') continue;
              perSizeCopy[`${c.conceptId}/${key}`] = { ...d, ...c.copy[key] };
            }
          }
          const col = doc.campaign?.brand?.colors ?? {};
          initialColors = { accent: col.accent, ctaText: col.ctaText ?? col.dark, headline: col.headlineInk ?? col.dark };
          proofMeta = {
            business: doc.campaign?.brand?.name,
            promoting: doc.campaign?.promoting ?? doc.campaign?.landing?.summary,
            objective: doc.campaign?.objective,
          };
        }
      } catch { /* editor simply starts blank if the campaign can't be read */ }

      let html = renderProof(manifest, {
        fileBase: '',
        actionBase: project ? `/api/proof/${project.projectId}` : undefined,
        initialCopy,
        initialColors,
        perSizeCopy,
        meta: proofMeta,
        delivered: (project?.delivered && project.delivered.length)
          ? project.delivered[project.delivered.length - 1]
          : undefined,
      });
      // Inline every creative as a data URI so the page depends on nothing else.
      for (const e of manifest.entries) {
        const srcAttr = e.localFile.split('/').slice(-3).join('/');
        if (!fs.existsSync(e.localFile)) continue;
        const mime = e.format === 'png' ? 'image/png' : 'image/jpeg';
        const b64 = fs.readFileSync(e.localFile).toString('base64');
        html = html.split(`src="${srcAttr}"`).join(`src="data:${mime};base64,${b64}"`);
      }
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      return res.end(withBase(req, html));
    }

    /* ------------------------------------------------------------- delivery */
    const deliverMatch = url.pathname.match(/^\/api\/project\/([\w.-]+)\/deliver$/);
    if (deliverMatch && req.method === 'POST') {
      const project = projects.get(deliverMatch[1]);
      if (!project) return json(res, 404, { error: 'No such project' });
      const body = JSON.parse((await readBody(req, 20_000)) || '{}') as { concept?: string };
      try {
        const out = await deliverProject(project, { outDir: OUT, conceptId: body.concept });
        project.status = 'complete';
        project.delivered = project.delivered ?? [];
        project.delivered.push({ at: new Date().toISOString(), zipUrl: out.zipUrl, fileCount: out.fileCount });
        project.notes.push(
          `[${new Date().toISOString()}] Delivered ${out.fileCount} file(s)` +
          (out.overrideCount ? ` (${out.overrideCount} manually edited)` : '') +
          (out.skipped.length ? `; skipped ${out.skipped.map((s) => s.size).join(', ')}` : ''),
        );
        projects.save(project);
        await notify(
          {
            subject: `Delivered — ${project.client} / ${project.projectName}`,
            body: `${out.fileCount} finished file(s) packaged for ${project.client}. Download from the build screen.` +
              (out.skipped.length ? `\nNot included: ${out.skipped.map((s) => `${s.size} (${s.reason})`).join('; ')}` : ''),
            url: `${PUBLIC_URL}/build?request=${project.requestId}`,
          },
          OUT,
        );
        return json(res, 200, out);
      } catch (e: any) {
        return json(res, 422, { error: e?.message ?? 'Delivery failed' });
      }
    }

    /* ------------------------------------------------- manual overrides */
    // The last 5% of real agency work: "the 300x600 needs the photo nudged —
    // I'll fix it in Photoshop." That file needs somewhere to go. The upload
    // is validated against the same platform rules as rendered output, so an
    // override cannot smuggle in a wrong-sized or overweight creative.
    const overrideMatch = url.pathname.match(/^\/api\/project\/([\w.-]+)\/override$/);
    if (overrideMatch && req.method === 'POST') {
      const project = projects.get(overrideMatch[1]);
      if (!project) return json(res, 404, { error: 'No such project' });

      const body = JSON.parse(await readBody(req, 5_000_000)) as {
        conceptId?: string; platform?: string; size?: string;
        filename?: string; dataBase64?: string; remove?: boolean;
      };
      const { conceptId, platform, size } = body;
      if (!conceptId || !platform || !size) {
        return json(res, 400, { error: 'conceptId, platform and size are required' });
      }

      project.overrides = project.overrides ?? [];

      if (body.remove) {
        const before = project.overrides.length;
        project.overrides = project.overrides.filter(
          (o) => !(o.conceptId === conceptId && o.platform === platform && o.size === size),
        );
        projects.save(project);
        return json(res, 200, { removed: before !== project.overrides.length });
      }

      if (!body.dataBase64) return json(res, 400, { error: 'No file supplied' });
      const data = Buffer.from(body.dataBase64, 'base64');

      const rule = getPlatform(platform)?.sizes[size as keyof ReturnType<typeof getPlatform>['sizes']];
      if (!rule) return json(res, 400, { error: `${platform} does not define ${size}` });

      // Validate exactly what the renderer's own output must satisfy.
      if (data.length > rule.maxFileBytes) {
        return json(res, 422, {
          error: `File is ${(data.length / 1024).toFixed(1)} KB; ${platform} allows ${(rule.maxFileBytes / 1024).toFixed(0)} KB for ${size}.`,
        });
      }
      let meta: { width?: number; height?: number; format?: string };
      try {
        meta = await sharp(data).metadata();
      } catch {
        return json(res, 422, { error: 'That file is not a readable image.' });
      }
      const wantW = rule.w * rule.deliverScale;
      const wantH = rule.h * rule.deliverScale;
      if (meta.width !== wantW || meta.height !== wantH) {
        return json(res, 422, {
          error: `Image is ${meta.width}x${meta.height}; ${platform}/${size} must be exactly ${wantW}x${wantH}` +
            (rule.deliverScale > 1 ? ` (${size} at ${rule.deliverScale}x)` : '') + '.',
        });
      }
      const fmt = meta.format === 'jpeg' ? 'jpg' : meta.format;
      if (!rule.formats.includes(fmt as any)) {
        return json(res, 422, { error: `${platform}/${size} accepts ${rule.formats.join(', ')}, not ${fmt}.` });
      }

      const dir = path.join(OUT, 'overrides', project.requestId);
      fs.mkdirSync(dir, { recursive: true });
      const file = path.join(dir, `${conceptId}_${platform}_${size}.${fmt}`);
      fs.writeFileSync(file, data);

      // Replace any prior override for the same slot.
      project.overrides = project.overrides.filter(
        (o) => !(o.conceptId === conceptId && o.platform === platform && o.size === size),
      );
      project.overrides.push({
        conceptId, platform, size, file,
        originalName: body.filename ?? 'upload',
        bytes: data.length,
        uploadedAt: new Date().toISOString(),
      });
      project.notes.push(
        `[${new Date().toISOString()}] ${platform}/${size} (concept ${conceptId}) replaced with a manually edited file: ${body.filename ?? 'upload'}`,
      );
      projects.save(project);
      return json(res, 200, { saved: true, file: path.basename(file), bytes: data.length });
    }

    /* ------------------------------------------------------------ approvals */
    // Posted by the proof screen. Public on purpose: the customer opening a
    // In-place rebuild. The heart of "revisions happen in the same window":
    // apply edits (per-concept or per-size copy, CTA, colours) to the stored
    // campaign, re-render, and refresh the proof. No new request, no "a revised
    // proof is on the way" — the same proof URL now shows the updated ads.
    const rebuildMatch = url.pathname.match(/^\/api\/proof\/([\w.-]+)\/rebuild$/);
    if (rebuildMatch && req.method === 'POST') {
      const project = projects.get(rebuildMatch[1]);
      if (!project) return json(res, 404, { error: 'No such project' });
      const campFile = path.join(OUT, 'campaigns', `${project.requestId}.json`);
      if (!fs.existsSync(campFile)) return json(res, 404, { error: 'No campaign to rebuild' });

      const body = JSON.parse(await readBody(req, 3_000_000)) as {
        conceptId?: string;
        copy?: { headline?: string; support?: string; cta?: string; offer?: string };
        size?: string;
        /** Flip to the white (reversed) logo for one size — dark backgrounds. */
        reverseLogo?: boolean;
        /** Replace the logo on ONE size (with body.size) or brand-wide
         *  (without). dataUrl carries a direct upload from the size editor —
         *  e.g. a square logo variant for square placements. */
        sizeLogo?: { dataUrl?: string; url?: string };
        /** Plain-English photo placement for this size, e.g. "show more of
         *  the left side" or "move the picture down". */
        picturePlacement?: string;
        colors?: { accent?: string; ctaText?: string; headline?: string };
        /** Logo change: a new uploaded logo (url/publicId) or an AI-rework of
         *  the current one. Applied to the whole brand (all sizes). */
        logo?: { url?: string; publicId?: string; aiRework?: boolean; reversed?: boolean };
        /** Background change for a concept. `mode:'solid'` clears any image;
         *  a `url` applies a chosen Pixabay/AI/uploaded image; `overlay` tunes
         *  the legibility scrim (0..1). Defaults to the offer concept. */
        background?: { conceptId?: string; mode?: 'solid' | 'image'; url?: string; overlay?: number };
      };

      const doc = JSON.parse(fs.readFileSync(campFile, 'utf8'));
      const campaign = doc.campaign;

      // --- apply logo edit ---
      if (body.logo) {
        const cacheDir = path.join(OUT, 'cache', project.requestId, 'relogo-' + Date.now().toString(36));
        try {
          let src: string | undefined;
          if (body.logo.url || body.logo.publicId) {
            const ref = body.logo.publicId ? `cloudinary:${body.logo.publicId}` : body.logo.url!;
            src = await resolveAsset(ref, { cacheDir, label: 'logo' });
          } else if (body.logo.aiRework && campaign.brand.logos?.primary) {
            src = campaign.brand.logos.primary;
          }
          if (src) {
            // Always enforce transparency; optionally make a reversed version.
            const reworked = await reworkLogo(src, cacheDir, { reversed: body.logo.reversed });
            const fitted = await fitImageToBudget(reworked.primary, path.join(cacheDir, 'logo-final.png'), { keepAlpha: true });
            campaign.brand.logos.primary = fitted.file;
            if (reworked.reverse) {
              const rev = await fitImageToBudget(reworked.reverse, path.join(cacheDir, 'logo-rev-final.png'), { keepAlpha: true });
              campaign.brand.logos.reverse = rev.file;
            }
          }
        } catch (e: any) {
          return json(res, 400, { error: `Logo change failed: ${e?.message ?? e}` });
        }
      }

      // --- apply background change ---
      // Change or remove a concept's full-bleed background photo after the
      // fact. Defaults to the offer concept, the one designed to carry a photo.
      if (body.background) {
        const targetId = body.background.conceptId
          ?? (campaign.concepts.find((c: any) => c.conceptId === 'C') ? 'C' : campaign.concepts[0]?.conceptId);
        const concept = campaign.concepts.find((c: any) => c.conceptId === targetId);
        if (concept) {
          if (body.background.mode === 'solid' || (!body.background.url && body.background.mode !== 'image')) {
            // Clear back to the flat brand background.
            delete concept.backgroundImage;
            delete concept.backgroundOverlay;
          } else if (body.background.url) {
            try {
              const cacheDir = path.join(OUT, 'cache', project.requestId, 'rebg-' + Date.now().toString(36));
              const rel = body.background.url.replace(/^\/files\//, '');
              const candidate = path.join(OUT, rel);
              const src = fs.existsSync(candidate)
                ? candidate
                : await resolveAsset(body.background.url, { cacheDir, label: 'background' });
              const fitted = await fitImageToBudget(src, path.join(cacheDir, 'bg.jpg'));
              concept.backgroundImage = fitted.file;
              try {
                const gal = path.join(OUT, 'gallery', slug(campaign.campaignName ?? project.projectId));
                fs.mkdirSync(gal, { recursive: true });
                fs.copyFileSync(fitted.file, path.join(gal, `bg-${Date.now().toString(36)}.jpg`));
              } catch { /* gallery is best-effort */ }
              if (typeof body.background.overlay === 'number') {
                concept.backgroundOverlay = body.background.overlay;
              }
            } catch (e: any) {
              return json(res, 400, { error: `Background change failed: ${e?.message ?? e}` });
            }
          }
        }
      }

      // --- apply colour edits to the brand palette ---
      if (body.colors) {
        if (body.colors.accent) campaign.brand.colors.accent = body.colors.accent;
        // ctaText / headline are carried as brand extensions the composer reads.
        if (body.colors.ctaText) campaign.brand.colors.ctaText = body.colors.ctaText;
        if (body.colors.headline) campaign.brand.colors.headlineInk = body.colors.headline;
      }

      // --- apply copy edits ---
      if ((body.copy || body.picturePlacement || body.sizeLogo || typeof body.reverseLogo === 'boolean') && body.conceptId) {
        const concept = campaign.concepts.find((c: any) => c.conceptId === body.conceptId);
        if (concept) {
          if (body.size) {
            // Targeted edit: change just this one size.
            const target = (concept.copy[body.size] = concept.copy[body.size] || {});
            for (const k of ['headline', 'support', 'cta', 'offer'] as const) {
              if (body.copy && body.copy[k] !== undefined) target[k] = body.copy[k];
            }
            if (typeof body.reverseLogo === 'boolean') {
              // The composer reads this flag from the size's copy set.
              (target as any).__useReverseLogo = body.reverseLogo;
            }
            if (typeof body.picturePlacement === 'string' && body.picturePlacement.trim()) {
              // Conversational repositioning: read direction words and map
              // them to the photo's focal point for this one size. "Show more
              // of the left" means anchor the LEFT edge of the photo.
              const t = body.picturePlacement.toLowerCase();
              const has = (...words: string[]) => words.some((w) => t.includes(w));
              let fx = 'xMid'; let fy = 'YMid';
              if (has('left')) fx = 'xMin';
              if (has('right')) fx = 'xMax';
              if (has('top', 'up', 'higher', 'head', 'face', 'sky')) fy = 'YMin';
              if (has('bottom', 'down', 'lower', 'ground', 'floor')) fy = 'YMax';
              if (has('center', 'centre', 'middle')) { if (!has('left','right')) fx = 'xMid'; if (!has('top','bottom','up','down')) fy = 'YMid'; }
              (target as any).__bgPos = `${fx}${fy}`;
              (target as any).__bgPosNote = body.picturePlacement.trim();
            }
            if (body.sizeLogo?.dataUrl || body.sizeLogo?.url) {
              try {
                const cacheDir = path.join(OUT, 'cache', project.requestId, 'szlogo-' + Date.now().toString(36));
                fs.mkdirSync(cacheDir, { recursive: true });
                let raw: Buffer;
                if (body.sizeLogo.dataUrl) {
                  const b64 = body.sizeLogo.dataUrl.split(',')[1] ?? '';
                  raw = Buffer.from(b64, 'base64');
                } else {
                  const r = await fetch(body.sizeLogo.url!);
                  if (!r.ok) throw new Error(`fetch ${r.status}`);
                  raw = Buffer.from(await r.arrayBuffer());
                }
                const src = path.join(cacheDir, 'raw');
                fs.writeFileSync(src, raw);
                // Same treatment as every logo: transparency enforced, budget-fit.
                const reworked = await reworkLogo(src, cacheDir, {});
                const fitted = await fitImageToBudget(reworked.primary, path.join(cacheDir, 'size-logo.png'), { keepAlpha: true });
                (target as any).__logoFile = fitted.file;
              } catch (e: any) {
                return json(res, 400, { error: `Size logo failed: ${e?.message ?? e}` });
              }
            }
          } else {
            // Default edit: change the concept default AND clear any stale
            // per-size overrides for the edited fields. Without this, a size
            // that got its own shortened headline at build time would keep it
            // and ignore the edit — the bug where 728x90 and 970x250 stayed on
            // the old copy after a rebuild.
            const def = (concept.copy.default = concept.copy.default || {});
            const editedFields: string[] = [];
            for (const k of ['headline', 'support', 'cta', 'offer'] as const) {
              if (body.copy && body.copy[k] !== undefined) { def[k] = body.copy[k]; editedFields.push(k); }
            }
            for (const key of Object.keys(concept.copy)) {
              if (key === 'default') continue;
              for (const f of editedFields) delete concept.copy[key][f];
            }
          }
        }
      }

      fs.writeFileSync(campFile, JSON.stringify(doc, null, 2));

      const platforms: string[] = (doc.platforms as string[])
        ?? (campaign.platforms as string[])
        ?? ['google', 'amazon'];
      const job = enqueue({ campaign, platforms, upload: false, outDir: OUT, assetRoot: ROOT });
      project.status = 'in-build';
      project.notes.push(`[${new Date().toISOString()}] Rebuild requested (job ${job.id})` +
        (body.conceptId ? ` for concept ${body.conceptId}${body.size ? '/' + body.size : ''}` : ''));
      projects.save(project);

      // Return immediately; render in the background. The proof polls the
      // status endpoint and reloads when ready — holding this HTTP request
      // open through a multi-minute render blocks the UI and trips proxies.
      project.autoJobId = job.id;
      projects.save(project);
      void (async () => {
        const started = Date.now();
        const t = setInterval(() => {
          const j = getJob(job.id);
          if (!j || j.status === 'complete' || j.status === 'failed' || Date.now() - started > 300_000) {
            clearInterval(t);
            if (j?.status === 'complete' && j.results) {
              const proofUrl = j.reports?.find((r) => r.includes('/proof_'));
              projects.addBatch(project.projectId, j.results, { reportUrl: proofUrl });
            }
          }
        }, 1500);
      })();
      return json(res, 200, { rebuilt: true, building: true, requestId: project.requestId, proofUrl: `/proof/${project.requestId}` });
    }

    // proof link is not an authenticated user. The project id in the URL is
    // the capability, so treat it like a signed link, not a secret.
    const decision = url.pathname.match(/^\/api\/proof\/([\w.-]+)\/(approve|revision)$/);
    if (decision && req.method === 'POST') {
      const [, projectId, kind] = decision;
      const project = projects.get(projectId);
      if (!project) return json(res, 404, { error: 'No such project' });

      const body = JSON.parse(await readBody(req, 50_000)) as { concept?: string; notes?: string };
      const at = new Date().toISOString();

      if (kind === 'approve') {
        project.status = 'approved';
        if (body.concept) project.approvedConcept = body.concept;
        project.notes.push(`[${at}] Concept ${body.concept ?? '?'} approved by the client.`);
        projects.save(project);
        console.log(`[proof] ${projectId} -> approved`);

        // Approval IS the trigger for delivery — no human in the loop just to
        // press "deliver". Package the approved concept now: platform folders,
        // final names, QA failures withheld. The client gets the download link
        // in this response; staff get it in the notification.
        try {
          const out = await deliverProject(project, { outDir: OUT, conceptId: body.concept });
          project.status = 'complete';
          project.delivered = project.delivered ?? [];
          project.delivered.push({ at, zipUrl: out.zipUrl, fileCount: out.fileCount });
          project.notes.push(
            `[${at}] Auto-delivered ${out.fileCount} file(s) on approval` +
            (out.skipped.length ? `; withheld ${out.skipped.map((s) => s.size).join(', ')}` : ''),
          );
          projects.save(project);
          await notify(
            {
              subject: `Approved & delivered — ${project.client} / ${project.projectName}`,
              body: `Concept ${body.concept ?? '?'} was approved. ${out.fileCount} finished file(s) are packaged — download from the build screen or the projects dashboard.` +
                (out.skipped.length ? `\nNot included: ${out.skipped.map((s) => `${s.size} (${s.reason})`).join('; ')}` : ''),
              url: `${PUBLIC_URL}/build?request=${project.requestId}`,
            },
            OUT,
          );
          return json(res, 200, { status: 'complete', recordedAt: at, downloadUrl: out.zipUrl, fileCount: out.fileCount });
        } catch (e: any) {
          // Delivery failing must not lose the approval. Record it, tell
          // staff, and let the client know files are on the way manually.
          project.notes.push(`[${at}] Auto-delivery failed: ${e?.message ?? e}. Deliver manually from the build screen.`);
          projects.save(project);
          await notify(
            {
              subject: `Approved (delivery needs attention) — ${project.client} / ${project.projectName}`,
              body: `Concept ${body.concept ?? '?'} was approved but packaging failed: ${e?.message ?? e}`,
              url: `${PUBLIC_URL}/build?request=${project.requestId}`,
            },
            OUT,
          );
          return json(res, 200, { status: 'approved', recordedAt: at });
        }
      } else {
        project.status = 'proof-sent';
        project.notes.push(`[${at}] Revision requested on concept ${body.concept ?? '?'}: ${body.notes ?? '(no detail)'}`);
      }
      projects.save(project);
      console.log(`[proof] ${projectId} -> ${project.status}`);

      await notify(
        {
          subject: `Revision requested — ${project.client} / ${project.projectName}`,
          body: `Concept ${body.concept ?? '?'} needs changes:\n\n"${body.notes ?? '(no detail given)'}"`,
          url: `${PUBLIC_URL}/build?request=${project.requestId}`,
        },
        OUT,
      );

      // TODO: push the status to HighLevel once the custom object exists there.
      return json(res, 200, { status: project.status, recordedAt: at });
    }

    /* ------------------------------------------------------------ retention */
    if (route === 'POST /api/maintenance/sweep') {
      const dry = url.searchParams.get('dry') !== '0';
      return json(res, 200, sweep({ outDir: OUT, dryRun: dry }));
    }

    /* ---------------------------------------------------------- diagnostics */
    if (route === 'GET /diagnostics' || route === 'GET /api/diagnostics') {
      const report = await runDiagnostics({ outDir: OUT, assetRoot: ROOT });
      if (url.searchParams.get('format') === 'json' || url.pathname.startsWith('/api/')) {
        // Non-200 when broken, so an uptime monitor can watch this directly.
        return json(res, report.verdict === 'broken' ? 503 : 200, report);
      }
      res.writeHead(report.verdict === 'broken' ? 503 : 200, {
        'content-type': 'text/html; charset=utf-8',
        'cache-control': 'no-store',
      });
      return res.end(withBase(req, renderDiagnostics(report, listJobs())));
    }

    /* ------------------------------------------------- landing page reader */
    // Pixabay background options for the campaign. Returns candidates served
    // from a public cache dir; nothing is applied until the person picks one.
    const galMatch = url.pathname.match(/^\/api\/gallery\/([\w-]+)$/);
    if (galMatch && req.method === 'GET') {
      const cors = corsHeaders(req.headers.origin);
      const dir = path.join(OUT, 'gallery', galMatch[1]);
      const items = fs.existsSync(dir)
        ? fs.readdirSync(dir).filter((f) => /\.(jpe?g|png)$/.test(f)).map((f) => ({ url: `/files/gallery/${galMatch[1]}/${f}` }))
        : [];
      return json(res, 200, { items }, cors);
    }

    if (route === 'POST /api/images/search') {
      const cors = corsHeaders(req.headers.origin);
      const limit = rateLimit(route, req);
      if (!limit.allowed) return json(res, 429, { error: 'Slow down a moment.' }, cors);
      if (!process.env.PIXABAY_API) {
        return json(res, 200, { candidates: [], reason: 'Image search is not configured (PIXABAY_API missing).' }, cors);
      }
      const body = JSON.parse(await readBody(req, 50_000)) as any;
      try {
        const cacheDir = path.join(OUT, 'imagery', slug(body.requestId ?? "adhoc"));
        const cands = await searchPixabay(
          { business: body.business ?? '', promoting: body.promoting ?? '', objective: body.objective, landing: body.landingAnalysis, keywords: body.keywords },
          { cacheDir, count: 2 },
        );
        // expose each under /files with a public url the browser can load
        const withUrls = cands.map((c) => ({ ...c, url: `/files/${path.relative(OUT, c.file)}` , file: undefined }));
        return json(res, 200, { candidates: withUrls, query: imageKeywords({ business: body.business ?? '', promoting: body.promoting ?? '', landing: body.landingAnalysis, keywords: body.keywords }) }, cors);
      } catch (e: any) {
        return json(res, 200, { candidates: [], reason: e?.message ?? 'Image search failed' }, cors);
      }
    }

    // AI-generated hero from the campaign / landing page. One candidate.
    if (route === 'POST /api/images/generate') {
      const cors = corsHeaders(req.headers.origin);
      const limit = rateLimit(route, req);
      if (!limit.allowed) return json(res, 429, { error: 'Slow down a moment.' }, cors);
      if (!process.env.OPENAI_API_KEY) {
        return json(res, 200, { candidate: null, reason: 'Image generation is not configured (OPENAI_API_KEY missing).' }, cors);
      }
      const body = JSON.parse(await readBody(req, 50_000)) as any;
      try {
        const cacheDir = path.join(OUT, 'imagery', slug(body.requestId ?? "adhoc"));
        // If the caller passed uploaded photo URLs (Cloudinary), fetch them as
        // local references so the generated hero echoes the real product.
        const refs: string[] = [];
        for (const u of (body.referenceImages ?? []) as string[]) {
          try {
            if (!/^https?:\/\//.test(u)) continue;
            const r = await fetch(u);
            if (!r.ok) continue;
            fs.mkdirSync(cacheDir, { recursive: true });
            const f = path.join(cacheDir, `ref-${refs.length}.png`);
            fs.writeFileSync(f, Buffer.from(await r.arrayBuffer()));
            refs.push(f);
          } catch { /* skip a bad reference */ }
        }
        // On a revision, the previous image becomes the primary reference so
        // the edit builds on the last result rather than starting fresh. This
        // is what makes "make the sky darker" iterate instead of re-roll.
        if (body.reviseInstruction && body.previousUrl) {
          try {
            const rel = String(body.previousUrl).replace(/^\/files\//, '');
            const prev = path.join(OUT, rel);
            if (fs.existsSync(prev)) {
              fs.mkdirSync(cacheDir, { recursive: true });
              const f = path.join(cacheDir, `prev-${Date.now().toString(36)}.png`);
              fs.copyFileSync(prev, f);
              refs.unshift(f);
            }
          } catch { /* fall back to a fresh generation with the instruction */ }
        }
        const cand = await generateHero(
          {
            business: body.business ?? '', promoting: body.promoting ?? '', objective: body.objective,
            landing: body.landingAnalysis, benefit: body.benefit, offer: body.offer,
            palette: body.palette, referenceImages: refs,
            reviseInstruction: body.reviseInstruction,
          },
          { cacheDir },
        );
        return json(res, 200, { candidate: { ...cand, url: `/files/${path.relative(OUT, cand.file)}`, file: undefined } }, cors);
      } catch (e: any) {
        return json(res, 200, { candidate: null, reason: e?.message ?? 'Image generation failed' }, cors);
      }
    }

    // Template chosen on the confirmation screen (optionally with edits).
    // Apply to every concept, THEN build the remaining sizes in the background.
    const chooseMatch = url.pathname.match(/^\/api\/requests\/([\w-]+)\/choose-template$/);
    if (chooseMatch && req.method === 'POST') {
      const cors = corsHeaders(req.headers.origin);
      if (!intakeAllowed(req, url)) return json(res, 401, { error: 'A valid team access code is required.' }, cors);
      const requestId = chooseMatch[1];
      const campFile = path.join(OUT, 'campaigns', `${requestId}.json`);
      if (!fs.existsSync(campFile)) return json(res, 404, { error: 'Unknown request' }, cors);
      const body = JSON.parse(await readBody(req, 100_000)) as {
        layoutFamily?: string;
        copy?: { headline?: string; support?: string; cta?: string };
        colors?: { accent?: string; ctaText?: string; headline?: string };
      };
      const fam = ['T01', 'T02', 'T03'].includes(body.layoutFamily ?? '') ? body.layoutFamily! : 'T01';
      const doc = JSON.parse(fs.readFileSync(campFile, 'utf8'));
      for (const c of doc.campaign.concepts) c.layoutFamily = fam;
      if (body.colors) {
        if (body.colors.accent) doc.campaign.brand.colors.accent = body.colors.accent;
        if (body.colors.ctaText) doc.campaign.brand.colors.ctaText = body.colors.ctaText;
        if (body.colors.headline) doc.campaign.brand.colors.headlineInk = body.colors.headline;
      }
      if (body.copy) {
        for (const c of doc.campaign.concepts) {
          c.copy.default = { ...c.copy.default };
          for (const k of ['headline', 'support', 'cta'] as const) {
            if (body.copy[k]) {
              c.copy.default[k] = body.copy[k];
              for (const key of Object.keys(c.copy)) if (key !== 'default') delete c.copy[key][k];
            }
          }
        }
      }
      fs.writeFileSync(campFile, JSON.stringify(doc, null, 2));
      const project = projects.byRequest(requestId);
      const platforms: string[] = doc.platforms ?? ['google'];
      const job = enqueue({ campaign: doc.campaign, platforms, upload: false, outDir: OUT, assetRoot: ROOT });
      if (project) { project.autoJobId = job.id; project.status = 'in-build'; projects.save(project); }
      console.log(`[choose] ${requestId} template ${fam}, building remaining sizes as job ${job.id}`);
      return json(res, 200, { building: true, template: fam }, cors);
    }

    if (route === 'POST /api/copy/suggest') {
      const cors = corsHeaders(req.headers.origin);
      const limit = rateLimit(route, req);
      if (!limit.allowed) return json(res, 429, { error: 'Slow down a moment and try again.' }, cors);
      const body = JSON.parse(await readBody(req, 50_000)) as any;
      if (!body.business || !body.promoting) {
        return json(res, 400, { error: 'business and promoting are required' }, cors);
      }
      const suggestion = await suggestCopy({
        business: body.business, promoting: body.promoting, benefit: body.benefit,
        offer: body.offer, audience: body.audience, geography: body.geography,
        objective: body.objective, cta: body.cta, landing: body.landingAnalysis,
      });
      return json(res, 200, suggestion, cors);
    }

    if (route === 'POST /api/landing/analyze') {
      const cors = corsHeaders(req.headers.origin);
      const body = JSON.parse(await readBody(req, 20_000)) as { url?: string; projectId?: string };
      let target = String(body.url ?? '').trim();
      if (!target) return json(res, 400, { error: 'Provide a landing page URL' }, cors);
      if (!/^https?:\/\//i.test(target)) target = 'https://' + target;

      const analysis = await analyzeLandingPage(target);
      // Cache on the project so the page is read once, not on every revision.
      if (body.projectId) {
        const p = projects.get(body.projectId);
        if (p) { p.landingAnalysis = analysis; projects.save(p); }
      }
      return json(res, 200, analysis, cors);
    }

    // Turn a chosen picture into a background this concept can render.
    //
    // The chooser hands back a URL -- from the landing page, from stock, from
    // an AI generation -- but CreativeConcept.backgroundImage is a local path,
    // because the composer reads pixels rather than fetching. So the choosing
    // and the applying are two steps, and this is the second: fetch it once,
    // fit it to the image budget, and hand back the path the campaign should
    // carry. Doing it here rather than in the browser also keeps the fetch
    // behind the same SSRF guard every other asset goes through.
    if (route === 'POST /api/background/apply') {
      const cors = corsHeaders(req.headers.origin);
      const body = JSON.parse(await readBody(req, 20_000)) as
        { requestId?: string; url?: string };
      const requestId = String(body.requestId ?? '').trim();
      const url = String(body.url ?? '').trim();
      if (!requestId) return json(res, 400, { error: 'Which campaign?' }, cors);
      if (!url) return json(res, 400, { error: 'Pick a picture first.' }, cors);

      // A relative /files/... URL is something this renderer already produced
      // (a stock pick or an AI generation), so it is read off disk rather than
      // fetched back out of ourselves over the network.
      let source = url;
      if (url.startsWith('/files/')) {
        const local = path.join(OUT, url.replace(/^\/files\//, ''));
        if (!fs.existsSync(local)) {
          return json(res, 404, { error: 'That image is no longer on disk. Choose another.' }, cors);
        }
        source = local;
      } else {
        const safe = assetUrlIsSafe(url);
        if (!safe.ok) {
          return json(res, 400, { error: `That image cannot be fetched (${safe.reason}).` }, cors);
        }
      }

      try {
        const cacheDir = path.join(OUT, 'cache', requestId);
        fs.mkdirSync(cacheDir, { recursive: true });
        const local = source.startsWith('/') && fs.existsSync(source)
          ? source
          : await resolveAsset(source, { cacheDir });
        // Same budget pass the intake path applies, so a 4 MB hero does not
        // ride into every size at full weight.
        const fitted = await fitImageToBudget(
          local, path.join(cacheDir, `bg-${Date.now().toString(36)}.jpg`));
        return json(res, 200, { ok: true, path: fitted.file }, cors);
      } catch (e: any) {
        return json(res, 502, {
          error: `That image could not be prepared (${e?.message ?? e}).`,
        }, cors);
      }
    }

    /* Replace the client's logo from inside the editor.
     *
     * The same shape as the background apply above, and for the same reason:
     * the chooser holds a URL and the renderer needs a prepared local file.
     * The difference is what preparing means. A logo goes through reworkLogo
     * first, which flattens a near-uniform border to transparency -- a white
     * box around a logo sitting on a coloured ad is the single most common
     * note on a proof -- and then through the weight budget with the alpha
     * channel kept, because a logo that loses transparency to a JPEG pass has
     * had the box put back.
     *
     * reworkLogo never redraws the mark. It trims and cleans what is there.
     * A logo an AI invented is not the client's logo.
     *
     * This returns the path and changes nothing: the editor holds the campaign
     * document and saves it, so the logo lands through the same write as every
     * other edit rather than through a second one that could disagree with it.
     */
    if (route === 'POST /api/logo/apply') {
      const cors = corsHeaders(req.headers.origin);
      const body = JSON.parse(await readBody(req, 20_000)) as
        { requestId?: string; url?: string; publicId?: string; reversed?: boolean };
      const requestId = String(body.requestId ?? '').trim();
      const url = String(body.url ?? '').trim();
      const publicId = String(body.publicId ?? '').trim();
      if (!requestId) return json(res, 400, { error: 'Which campaign?' }, cors);
      if (!url && !publicId) return json(res, 400, { error: 'Pick a logo first.' }, cors);

      let source = publicId ? `cloudinary:${publicId}` : url;
      if (!publicId && url.startsWith('/files/')) {
        const local = path.join(OUT, url.replace(/^\/files\//, ''));
        if (!fs.existsSync(local)) {
          return json(res, 404, { error: 'That logo is no longer on disk. Choose another.' }, cors);
        }
        source = local;
      } else if (!publicId) {
        const safe = assetUrlIsSafe(url);
        if (!safe.ok) {
          return json(res, 400, { error: `That logo cannot be fetched (${safe.reason}).` }, cors);
        }
      }

      try {
        const cacheDir = path.join(OUT, 'cache', requestId,
          'relogo-' + Date.now().toString(36));
        fs.mkdirSync(cacheDir, { recursive: true });
        const local = source.startsWith('/') && fs.existsSync(source)
          ? source
          : await resolveAsset(source, { cacheDir, label: 'logo' });
        const reworked = await reworkLogo(local, cacheDir, { reversed: body.reversed });
        const fitted = await fitImageToBudget(
          reworked.primary, path.join(cacheDir, 'logo-final.png'), { keepAlpha: true });
        const out: { ok: true; path: string; reverse?: string } =
          { ok: true, path: fitted.file };
        if (reworked.reverse) {
          const rev = await fitImageToBudget(
            reworked.reverse, path.join(cacheDir, 'logo-rev-final.png'), { keepAlpha: true });
          out.reverse = rev.file;
        }
        return json(res, 200, out, cors);
      } catch (e: any) {
        return json(res, 502, {
          error: `That logo could not be prepared (${e?.message ?? e}).`,
        }, cors);
      }
    }

    // Pictures already on the landing page, offered as ad backgrounds. One of
    // the five ways to choose one; the others are the client's gallery, an
    // upload, stock search and AI generation.
    if (route === 'POST /api/landing/images') {
      const cors = corsHeaders(req.headers.origin);
      const body = JSON.parse(await readBody(req, 20_000)) as
        { url?: string; projectId?: string; minWidth?: number };
      let target = String(body.url ?? '').trim();
      // Falling back to the project's own landing page means the build screen
      // does not have to re-send something it already stored.
      if (!target && body.projectId) {
        target = String(projects.get(body.projectId)?.landingPage ?? '').trim();
      }
      if (!target) return json(res, 400, { error: 'Provide a landing page URL' }, cors);
      if (!/^https?:\/\//i.test(target)) target = 'https://' + target;

      const minWidth = Number.isFinite(body.minWidth as number)
        ? Math.max(1, Math.min(4000, Number(body.minWidth)))
        : undefined;
      // landingImages never throws: an unreadable page returns an empty list
      // and a warning, which is what the chooser should show.
      const found = await landingImages(target, { minWidth });
      return json(res, 200, found, cors);
    }

    /* -------------------------------------------------------------- projects */
    // Clone a campaign: everything copies to a fresh AD number so a returning
    // customer's edits live on a NEW campaign, leaving the original intact.
    const cloneMatch = url.pathname.match(/^\/api\/project\/([\w-]+)\/clone$/);
    if (cloneMatch && req.method === 'POST') {
      const src = projects.get(cloneMatch[1]);
      if (!src) return json(res, 404, { error: 'Unknown project' });
      const newRequestId = newRequestIdFor();
      const srcCamp = path.join(OUT, 'campaigns', `${src.requestId}.json`);
      if (!fs.existsSync(srcCamp)) return json(res, 409, { error: 'Original campaign file is missing.' });
      const doc = JSON.parse(fs.readFileSync(srcCamp, 'utf8'));
      doc.requestId = newRequestId;
      if (doc.campaign) doc.campaign.requestId = newRequestId;
      fs.writeFileSync(path.join(OUT, 'campaigns', `${newRequestId}.json`), JSON.stringify(doc, null, 2));
      // The intake record travels too, so the clone's overview shows the
      // same submission details as the original.
      try {
        const srcReq = path.join(OUT, 'requests', `${src.requestId}.json`);
        if (fs.existsSync(srcReq)) {
          const rec = JSON.parse(fs.readFileSync(srcReq, 'utf8'));
          rec.requestId = newRequestId;
          rec.clonedFrom = src.requestId;
          fs.writeFileSync(path.join(OUT, 'requests', `${newRequestId}.json`), JSON.stringify(rec, null, 2));
        }
      } catch { /* overview simply shows less without it */ }
      const clone = projects.create({
        projectName: `${src.projectName} (copy)`,
        client: src.client, domain: src.domain, campaignName: src.campaignName,
        requestId: newRequestId, landingPage: src.landingPage, brand: src.brand,
        brandEnteredManually: src.brandEnteredManually,
        cloudinaryFolder: src.cloudinaryFolder, keywords: src.keywords,
        notes: [`Cloned from ${src.requestId} (${src.projectId}).`],
      } as any);
      clone.status = 'in-build';
      projects.save(clone);
      // Render the clone so its proof is immediately reviewable/editable.
      const platforms: string[] = doc.platforms ?? ['google'];
      const job = enqueue({ campaign: doc.campaign, platforms, upload: false, outDir: OUT, assetRoot: ROOT });
      clone.autoJobId = job.id;
      projects.save(clone);
      console.log(`[clone] ${src.requestId} -> ${newRequestId}`);
      return json(res, 200, {
        requestId: newRequestId, projectId: clone.projectId,
        proofUrl: `/proof/${newRequestId}`, overviewUrl: `/overview/${newRequestId}`,
        building: true,
      });
    }

    if (route === 'GET /api/projects') {
      const q = url.searchParams;
      return json(res, 200, {
        projects: projects.search({
          q: q.get('q') ?? undefined,
          client: q.get('client') ?? undefined,
          status: (q.get('status') as any) ?? undefined,
          from: q.get('from') ?? undefined,
          to: q.get('to') ?? undefined,
          limit: Number(q.get('limit') ?? 100),
        }),
      });
    }

    // Team-facing search from the intake page. Gated by the intake code (not
    // the admin token), so the embedded form can offer "search past campaigns"
    // without exposing the admin API. Returns a trimmed, link-ready shape.
    if (route === 'GET /api/campaign-search') {
      const cors = corsHeaders(req.headers.origin);
      if (!intakeAllowed(req, url)) return json(res, 401, { error: 'A valid team access code is required.' }, cors);
      const q = url.searchParams;
      const found = projects.search({ q: q.get('q') ?? undefined, limit: Number(q.get('limit') ?? 25) });
      return json(res, 200, {
        results: found.map((p: any) => ({
          requestId: p.requestId,
          projectName: p.projectName,
          client: p.client,
          campaignName: p.campaignName,
          domain: p.domain,
          contact: p.contact,
          email: p.email,
          status: p.status,
          updatedAt: p.updatedAt,
          proofUrl: `/proof/${p.requestId}`,
          overviewUrl: `/overview/${p.requestId}`,
          delivered: p.delivered?.length ? p.delivered[p.delivered.length - 1] : null,
        })),
      }, cors);
    }

    /* ------------------------------------------------- note from the Hub */
    // The Hub appends a line here when finished ads are attached to a client,
    // so the build screen records where the creative went. A PUT on the
    // project would replace the whole notes array; this only appends, which is
    // what a second writer has to do to be safe.
    const noteMatch = url.pathname.match(/^\/api\/project\/([\w.-]+)\/note$/);
    if (noteMatch && req.method === 'POST') {
      const project = projects.get(noteMatch[1]);
      if (!project) return json(res, 404, { error: 'No such project' });
      const body = JSON.parse((await readBody(req, 4_000)) || '{}') as { note?: string };
      const note = String(body.note ?? '').trim().slice(0, 500);
      if (!note) return json(res, 400, { error: 'A note is required.' });
      project.notes.push(`[${new Date().toISOString()}] ${note}`);
      projects.save(project);
      return json(res, 200, { ok: true, notes: project.notes.length });
    }

    const projMatch = url.pathname.match(/^\/api\/project\/([\w.-]+)$/);
    if (projMatch) {
      if (req.method === 'GET') {
        const p = projects.get(projMatch[1]);
        return p ? json(res, 200, p) : json(res, 404, { error: 'No such project' });
      }
      if (req.method === 'PUT') {
        const body = JSON.parse(await readBody(req, 500_000));
        const existing = projects.get(projMatch[1]);
        if (!existing) return json(res, 404, { error: 'No such project' });
        return json(res, 200, projects.save({ ...existing, ...body, projectId: existing.projectId }));
      }
    }

    if (route === 'GET /projects' || route === 'GET /projects.html') {
      const file = path.join(PUBLIC, 'projects.html');
      if (!fs.existsSync(file)) return json(res, 404, { error: 'Projects screen not built' });
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      return res.end(withBase(req, fs.readFileSync(file, 'utf8')));
    }

    /* --------------------------------------------------- campaign search page */
    if (route === 'GET /search' || route === 'GET /search.html') {
      const file = path.join(PUBLIC, 'search.html');
      if (!fs.existsSync(file)) return json(res, 404, { error: 'Search screen not built' });
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      return res.end(withBase(req, fs.readFileSync(file, 'utf8')));
    }

    /* -------------------------------------------------------- build screen */
    if (route === 'GET /build' || route === 'GET /build.html') {
      const file = path.join(PUBLIC, 'build.html');
      if (!fs.existsSync(file)) return json(res, 404, { error: 'Build screen not built' });
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      return res.end(withBase(req, fs.readFileSync(file, 'utf8')));
    }

    // Everything the build screen needs to populate its controls.
    if (route === 'GET /api/build/options') {
      // `wire` is the real block geometry for one representative size, so the
      // build screen can draw each family as a small diagram rather than
      // listing names in a dropdown. Nobody picks a layout from the words
      // "Split image" -- they pick it from seeing where the picture goes.
      const templates = [...loadTemplates().values()].map((t) => {
        const sizeKeys = Object.keys(t.sizes);
        // 300x250 is the one everybody recognises; fall back to whatever the
        // family does have, because not every family carries every size.
        const key = sizeKeys.includes('300x250') ? '300x250' : sizeKeys[0];
        const layout = key ? (t.sizes as Record<string, any>)[key] : undefined;
        const canvas = layout?.canvas;
        const blocks: Array<{ k: string; x: number; y: number; w: number; h: number }> = [];
        if (layout && canvas?.w && canvas?.h) {
          for (const [k, v] of Object.entries(layout as Record<string, any>)) {
            if (k === 'canvas' || k === 'safe' || k === 'background') continue;
            if (!v) continue;
            // `panels` is an array of rects, not a single box. Skipping it drew
            // "Image top, copy below" as text floating on white -- the panel is
            // most of what that family looks like.
            const rects = Array.isArray(v) ? v : [v];
            for (const r of rects) {
              if (!r || typeof r !== 'object') continue;
              const { x, y, w, h } = r as Record<string, number>;
              if ([x, y, w, h].some((n) => typeof n !== 'number')) continue;
              blocks.push({ k: Array.isArray(v) ? 'panel' : k, x, y, w, h });
            }
          }
        }
        return {
          id: t.id,
          name: t.name,
          description: t.description,
          sizes: sizeKeys,
          // The field colour matters as much as the boxes: two families differ
          // mainly in whether the ad is a flat brand field or a white card.
          wire: canvas?.w && canvas?.h
            ? { size: key, w: canvas.w, h: canvas.h, background: layout?.background ?? null, blocks }
            : null,
        };
      });
      // The families the renderer actually has. Sent so the editor can offer a
      // list rather than a text box: an unknown name falls back to Montserrat
      // predictably, which means a free-text control can show one family while
      // the ad renders another.
      return json(res, 200, { templates, fonts: listFamilies() });
    }

    if (route === 'GET /api/campaigns') {
      const dir = path.join(OUT, 'campaigns');
      if (!fs.existsSync(dir)) return json(res, 200, { campaigns: [] });
      const campaigns = fs
        .readdirSync(dir)
        .filter((f) => f.endsWith('.json'))
        .map((f) => {
          const d = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
          const requestId = d.campaign?.requestId ?? f.replace('.json', '');
          // Status lives on the project record, not the campaign file, so the
          // build screen can show "needs revision" without a second fetch.
          const proj = projects.byRequest(requestId);
          return {
            requestId,
            client: d.campaign?.brand?.name ?? 'Unknown',
            campaignName: d.campaign?.campaignName ?? '',
            concepts: (d.campaign?.concepts ?? []).length,
            notes: (d.notes ?? []).length,
            status: proj?.status ?? 'draft',
            projectId: proj?.projectId,
            updated: fs.statSync(path.join(dir, f)).mtime.toISOString(),
          };
        })
        .sort((a, b) => {
          // Needs-revision surfaces first — that is the queue staff work from.
          const weight: Record<string, number> = { 'proof-sent': 0, draft: 1, 'in-build': 2, approved: 3, complete: 4, archived: 5 };
          const wa = weight[a.status] ?? 1, wb = weight[b.status] ?? 1;
          return wa !== wb ? wa - wb : b.updated.localeCompare(a.updated);
        });
      return json(res, 200, { campaigns });
    }

    const campMatch = url.pathname.match(/^\/api\/campaign\/([\w-]+)$/);
    if (campMatch) {
      const file = path.join(OUT, 'campaigns', `${campMatch[1]}.json`);
      if (req.method === 'GET') {
        if (!fs.existsSync(file)) return json(res, 404, { error: 'No such campaign' });
        const doc = JSON.parse(fs.readFileSync(file, 'utf8'));
        // The build screen needs status and the latest proof link, both of
        // which live on the project record rather than the campaign file.
        const proj = projects.byRequest(campMatch[1]);
        const lastBatch = proj?.batches[proj.batches.length - 1];
        return json(res, 200, {
          ...doc,
          status: proj?.status,
          reportUrl: lastBatch?.reportUrl,
        });
      }
      if (req.method === 'PUT') {
        const body = JSON.parse(await readBody(req, 500_000));
        if (!body?.campaign?.brand) return json(res, 400, { error: 'Body must include a campaign' });
        fs.mkdirSync(path.dirname(file), { recursive: true });
        fs.writeFileSync(file, JSON.stringify(body, null, 2));
        return json(res, 200, { saved: true, requestId: campMatch[1] });
      }
    }

    // Live preview for the editor. Returns a PNG so the browser can show it
    // immediately, plus the QA findings for that one creative.
    if (route === 'POST /api/preview') {
      const body = JSON.parse(await readBody(req, 500_000)) as {
        campaign?: any;
        conceptId?: string;
        size?: string;
        platform?: string;
      };
      const campaign = body.campaign;
      const concept = campaign?.concepts?.find((c: any) => c.conceptId === body.conceptId)
        ?? campaign?.concepts?.[0];
      if (!campaign?.brand || !concept) {
        return json(res, 400, { error: 'Provide a campaign and a concept' });
      }
      try {
        const out = await renderPreview({
          brand: campaign.brand,
          concept,
          platform: body.platform ?? 'google',
          size: (body.size ?? '300x250') as any,
          assetRoot: ROOT,
        });
        return json(res, 200, {
          image: `data:image/png;base64,${out.png.toString('base64')}`,
          width: out.width,
          height: out.height,
          status: out.status,
          wordCount: out.wordCount,
          qa: out.qa.filter((f) => f.status !== 'pass'),
        });
      } catch (e: any) {
        return json(res, 422, { error: e?.message ?? 'Preview failed' });
      }
    }

    if (route === 'POST /api/render') {
      const body = JSON.parse(await readBody(req)) as {
        campaign: Campaign;
        platforms?: string[];
        upload?: boolean;
      };
      if (!body?.campaign) return json(res, 400, { error: 'Body must include a `campaign` object' });

      const platforms = body.platforms ?? ['google'];
      // Validate in-request so a bad brand font is a 400, not a job that fails
      // silently in a worker ten seconds later.
      const findings = validateCampaign(body.campaign, { assetRoot: ROOT, platforms });
      const errors = findings.filter((f) => f.level === 'error');
      if (errors.length) {
        return json(res, 422, { error: 'Campaign failed validation', findings: errors });
      }

      const job = enqueue({
        campaign: body.campaign,
        platforms,
        upload: body.upload ?? false,
        outDir: OUT,
        assetRoot: ROOT,
      });
      return json(res, 202, {
        jobId: job.id,
        status: job.status,
        warnings: findings.filter((f) => f.level === 'warning'),
        poll: `/api/render/${job.id}`,
      });
    }

    const jobMatch = url.pathname.match(/^\/api\/render\/([\w-]+)$/);
    if (req.method === 'GET' && jobMatch) {
      const job = getJob(jobMatch[1]);
      if (!job) return json(res, 404, { error: 'No such job' });
      return json(res, 200, job);
    }

    if (req.method === 'GET' && url.pathname.startsWith('/files/')) {
      if (serveStatic(res, url.pathname)) return;
      return json(res, 404, { error: 'Not found' });
    }

    return json(res, 404, { error: `No route for ${route}` });
  } catch (err: any) {
    console.error(`[error] ${route}`, err);
    return json(res, 500, { error: err?.message ?? 'Internal error' });
  }
});

// Single-process mode runs the queue in-band. Set WORKER_MODE=external once a
// separate Render Background Worker is deployed against the same queue.
// Rendering runs next to the web server on whatever instance Render gives us.
// libvips defaults its thread pool to the HOST's core count (8 on Render's
// machines) regardless of the container's actual CPU allowance, which on a
// small instance means thrashing instead of rendering. Two threads is the
// honest ceiling; SHARP_CONCURRENCY overrides for bigger instances.
sharp.concurrency(Number(process.env.SHARP_CONCURRENCY ?? 2));
sharp.cache(false); // rendered buffers are never re-read; caching them only holds memory

// Recover anything left mid-render by a prior restart before the loop starts
// draining, so a Render deploy cannot silently drop a customer's job.
const recovery = recoverJobs(OUT);
if (recovery.recovered) console.log(`[boot] requeued ${recovery.recovered} job(s) interrupted by the last restart`);

if (process.env.WORKER_MODE !== 'external') startWorkerLoop();
startWatchdog((job) => {
  void notify(
    {
      subject: `Render watchdog killed job ${job.id}`,
      body: job.error ?? 'A render ran too long and was stopped.',
      url: `${PUBLIC_URL}/diagnostics`,
    },
    OUT,
  );
});

// Expired rate-limit buckets would otherwise accumulate for every client seen.
setInterval(() => sweepBuckets(), 10 * 60 * 1000).unref();

// Watch our own health. The diagnostics page is thorough but passive — someone
// has to remember to look. This runs the same checks on a timer and pushes ONE
// notification when the verdict transitions to broken (and one when it
// recovers), so a font failure at 2am pages someone instead of waiting for a
// customer to find it. Transition-only, because a broken check repeating every
// three hours trains people to ignore the channel.
let lastVerdict: string | null = null;
const HEALTH_EVERY_MS = Number(process.env.HEALTH_CHECK_HOURS ?? 3) * 3_600_000;
if (HEALTH_EVERY_MS > 0) {
  setInterval(async () => {
    try {
      const report = await runDiagnostics({ outDir: OUT, assetRoot: ROOT });
      if (report.verdict === 'broken' && lastVerdict !== 'broken') {
        const fails = report.checks.filter((c) => c.level === 'fail');
        await notify(
          {
            subject: `Ad Builder is broken — ${fails.length} failing check(s)`,
            body: fails.map((f) => `${f.label}: ${f.detail}`).join('\n'),
            url: `${PUBLIC_URL}/diagnostics`,
          },
          OUT,
        );
      } else if (report.verdict !== 'broken' && lastVerdict === 'broken') {
        await notify(
          { subject: 'Ad Builder recovered', body: 'All previously failing checks now pass.', url: `${PUBLIC_URL}/diagnostics` },
          OUT,
        );
      }
      lastVerdict = report.verdict;
    } catch (e: any) {
      console.warn(`[health] self-check crashed: ${e?.message ?? e}`);
    }
  }, HEALTH_EVERY_MS).unref();
}

// Rendered files are kept after upload; without this they accumulate until the
// volume fills. Cloudinary holds the permanent copies.
scheduleSweep({
  outDir: OUT,
  renderDays: Number(process.env.RENDER_RETENTION_DAYS ?? 30),
  cacheDays: Number(process.env.CACHE_RETENTION_DAYS ?? 7),
}).unref();

server.listen(PORT, HOST, () => {
  console.log(`smart1-ad-builder listening on ${HOST}:${PORT}`);
  console.log(`  output dir: ${OUT}`);
  console.log(`  worker:     ${process.env.WORKER_MODE === 'external' ? 'external' : 'in-process'}`);
  console.log(`  embed page: ${fs.existsSync(path.join(PUBLIC, 'embed.html')) ? 'present' : 'MISSING'}`);
  console.log(`  build:      ${BUILD_STAMP.builtAt}`);
  console.log(`  frame-ancestors: ${FRAME_ANCESTORS}`);
  if (!process.env.ALLOWED_FRAME_ANCESTORS) {
    console.warn(
      "  WARNING: ALLOWED_FRAME_ANCESTORS is not set, so frame-ancestors is 'self'. " +
        'Any other site embedding /embed will get a blank box and a CSP error in the ' +
        "browser console. Set it to the embedding site's origin, e.g. " +
        "\"'self' https://smart1marketing.com\".",
    );
  }
});

// Render sends SIGTERM on deploy and scale-down; finish cleanly.
for (const signal of ['SIGTERM', 'SIGINT'] as const) {
  process.on(signal, () => {
    console.log(`${signal} received, closing`);
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 10_000).unref();
  });
}
