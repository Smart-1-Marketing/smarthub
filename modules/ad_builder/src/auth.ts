/**
 * Access control.
 *
 * Two different problems, deliberately solved differently:
 *
 *   Internal routes (build screen, campaign read/write, project search,
 *   diagnostics) must not be readable by anyone who guesses the URL. They
 *   take a token.
 *
 *   Public routes (intake, brand discovery, landing analysis, upload signing)
 *   have to stay open — an embedded form on a customer's site calls them — so
 *   they get rate limiting instead. Upload signing is the one that actually
 *   costs money if abused: a signature is permission to write into your
 *   Cloudinary account, so it gets the tightest budget.
 *
 * The token is a shared secret, which is the right weight for a handful of
 * staff. It is not a user system: no accounts, no roles, no audit trail. If
 * more than a few people need access, or you need to know who changed what,
 * replace this rather than extending it.
 */

import * as crypto from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';

export interface AuthResult {
  ok: boolean;
  reason?: string;
}

/** Timing-safe compare so a wrong token cannot be guessed byte by byte. */
function safeEqual(a: string, b: string): boolean {
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ab.length !== bb.length) {
    crypto.timingSafeEqual(ab, ab); // burn the comparison; do not leak length
    return false;
  }
  return crypto.timingSafeEqual(ab, bb);
}

export function configuredToken(): string | undefined {
  const t = process.env.ADMIN_TOKEN?.trim();
  return t && t.length >= 16 ? t : undefined;
}

/** True when a token is set but too short to be worth having. */
export function tokenIsWeak(): boolean {
  const raw = process.env.ADMIN_TOKEN?.trim();
  return Boolean(raw && raw.length < 16);
}

/**
 * Accepts the token from an Authorization header, an X-Admin-Token header, a
 * `token` query parameter, or the s1_admin cookie. The query parameter exists
 * so a bookmarked link works; the handler then sets a cookie so the token
 * stops appearing in the address bar and referrer headers.
 */
/**
 * The team access code that gates the intake surface when the tool is
 * internal-only. Deliberately separate from ADMIN_TOKEN: the code is typed by
 * salespeople and shared around the office, so it must never unlock the build
 * screen, project data, or delivered files — compromise of the code costs
 * only the ability to submit requests.
 *
 * Unset means the intake is open (the original public-form behavior).
 */
export function intakeCodeOk(req: IncomingMessage): boolean {
  const wanted = process.env.INTAKE_CODE ?? '';
  // Unset used to mean "open", which makes a missing environment variable
  // silently leave a public write surface — the same failure the Hub's own
  // notes call out, where something looks configured and is not. Inside the
  // Hub the intake is reached through the Hub login anyway, so failing closed
  // costs nothing here and removes the accident.
  //
  // INTAKE_OPEN=true is the deliberate way back to a public form, so the
  // choice has to be made rather than defaulted into.
  if (!wanted) {
    return String(process.env.INTAKE_OPEN ?? '').toLowerCase() === 'true';
  }
  const got = String(req.headers['x-intake-code'] ?? '');
  return safeEqual(got, wanted);
}

/**
 * The intake gate, as the rest of the server should ask it.
 *
 * The admin token is the stronger credential of the two: it opens every
 * internal route, and the intake code only opens the public form. Requiring
 * the weaker one from a caller that already presented the stronger one is how
 * the Hub ends up with a tool that loads and cannot create anything — staff
 * are signed in, the proxy sends the admin token, and the build screen answers
 * "a valid team access code is required" with no way to supply one.
 */
export function intakeAllowed(req: IncomingMessage, url: URL): boolean {
  return intakeCodeOk(req) || checkAuth(req, url).ok;
}

export function checkAuth(req: IncomingMessage, url: URL): AuthResult {
  const expected = configuredToken();
  if (!expected) {
    return {
      ok: false,
      reason:
        'ADMIN_TOKEN is not set, or is shorter than 16 characters, so internal routes are closed.',
    };
  }

  const header = req.headers.authorization ?? '';
  const bearer = header.toLowerCase().startsWith('bearer ') ? header.slice(7).trim() : '';
  const custom = String(req.headers['x-admin-token'] ?? '').trim();
  const query = url.searchParams.get('token') ?? '';
  const cookie = /(?:^|;\s*)s1_admin=([^;]+)/.exec(String(req.headers.cookie ?? ''))?.[1] ?? '';

  for (const candidate of [bearer, custom, query, cookie ? decodeURIComponent(cookie) : '']) {
    if (candidate && safeEqual(candidate, expected)) return { ok: true };
  }
  return { ok: false, reason: 'Missing or invalid token.' };
}

export function sessionCookie(token: string): string {
  return [
    `s1_admin=${encodeURIComponent(token)}`,
    'Path=/',
    'HttpOnly',
    'SameSite=Lax',
    'Max-Age=604800',
    process.env.NODE_ENV === 'production' ? 'Secure' : '',
  ].filter(Boolean).join('; ');
}

/* ---------------------------------------------------------- rate limiting */

interface Bucket { count: number; resetAt: number }
const buckets = new Map<string, Bucket>();

export interface Budget { limit: number; windowMs: number }

/**
 * Per-route budgets. Signing an upload is the expensive one, so it is
 * tightest. Landing analysis costs an OpenAI call plus a third-party fetch.
 * Preview is generous because the build screen fires one per edit.
 */
export const BUDGETS: Record<string, Budget> = {
  'POST /api/requests': { limit: 12, windowMs: 3_600_000 },
  'POST /api/intake/verify': { limit: 30, windowMs: 3_600_000 },
  'POST /api/copy/suggest': { limit: 40, windowMs: 3_600_000 },
  'POST /api/images/search': { limit: 40, windowMs: 3_600_000 },
  'POST /api/images/generate': { limit: 20, windowMs: 3_600_000 },
  'POST /api/assets/upload-signature': { limit: 40, windowMs: 3_600_000 },
  'POST /api/landing/analyze': { limit: 20, windowMs: 3_600_000 },
  'POST /api/brand/discover': { limit: 40, windowMs: 3_600_000 },
  'POST /api/preview': { limit: 600, windowMs: 3_600_000 },
};

/**
 * Behind Render or nginx the socket address is the proxy, so the forwarded
 * header is used when present. It is spoofable, which is why these are
 * budgets rather than a security control.
 */
export function clientKey(req: IncomingMessage): string {
  const fwd = String(req.headers['x-forwarded-for'] ?? '').split(',')[0].trim();
  return fwd || req.socket.remoteAddress || 'unknown';
}

export interface LimitResult {
  allowed: boolean;
  remaining: number;
  retryAfterSec: number;
}

export function rateLimit(route: string, req: IncomingMessage): LimitResult {
  const budget = BUDGETS[route];
  if (!budget) return { allowed: true, remaining: Infinity, retryAfterSec: 0 };

  const key = `${route}|${clientKey(req)}`;
  const now = Date.now();
  const bucket = buckets.get(key);

  if (!bucket || now >= bucket.resetAt) {
    buckets.set(key, { count: 1, resetAt: now + budget.windowMs });
    return { allowed: true, remaining: budget.limit - 1, retryAfterSec: 0 };
  }

  bucket.count++;
  if (bucket.count > budget.limit) {
    return { allowed: false, remaining: 0, retryAfterSec: Math.ceil((bucket.resetAt - now) / 1000) };
  }
  return { allowed: true, remaining: budget.limit - bucket.count, retryAfterSec: 0 };
}

/** Drop expired buckets so the map cannot grow without bound. */
export function sweepBuckets(): number {
  const now = Date.now();
  let dropped = 0;
  for (const [k, b] of buckets) if (now >= b.resetAt) { buckets.delete(k); dropped++; }
  return dropped;
}

export function bucketCount(): number { return buckets.size; }
export function resetBuckets(): void { buckets.clear(); }

/** Writes the 401/429 and returns true, so handlers can `return denied(...)`. */
export function denied(
  res: ServerResponse,
  code: 401 | 429,
  message: string,
  extra: Record<string, string> = {},
): true {
  const body = JSON.stringify({ error: message }, null, 2);
  res.writeHead(code, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    ...extra,
  });
  res.end(body);
  return true;
}
