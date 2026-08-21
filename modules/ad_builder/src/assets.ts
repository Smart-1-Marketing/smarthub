/**
 * Asset intake and resolution.
 *
 * Two directions:
 *
 *   In  — the browser uploads directly to Cloudinary using a signature this
 *         server generates. Files never pass through Render, which keeps the
 *         web service responsive and avoids paying for the bandwidth twice.
 *         Signed rather than unsigned so the folder, tags and allowed formats
 *         are fixed server-side and cannot be rewritten by the page.
 *
 *   Out — the renderer needs local files. Anything referenced as a URL or a
 *         Cloudinary public ID gets downloaded into a cache directory first.
 *         This is what lets a Brandfetch logo or a customer upload be used by
 *         a pipeline that otherwise reads from disk.
 */

import * as crypto from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';
import sharp from 'sharp';
import { CloudinaryService, slug } from './cloudinary';

export type AssetKind = 'logo' | 'logo-reverse' | 'product' | 'background' | 'lifestyle' | 'brand-guide';

/** Formats we are willing to accept and can actually composite. */
export const ALLOWED_FORMATS = ['png', 'jpg', 'jpeg', 'webp', 'svg'] as const;
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

export interface SignedUpload {
  timestamp: number;
  signature: string;
  apiKey: string;
  cloudName: string;
  folder: string;
  tags: string;
  uploadUrl: string;
  /** Echoed back so the browser sends exactly what was signed. */
  params: Record<string, string | number>;
}

/**
 * Cloudinary signs the alphabetically sorted, ampersand-joined parameter list
 * with the api_secret appended. Any parameter the browser sends that was not
 * signed will be rejected, which is the point.
 */
export function signUpload(args: {
  folder: string;
  tags: string[];
  publicId?: string;
  apiKey: string;
  apiSecret: string;
  cloudName: string;
  timestamp?: number;
}): SignedUpload {
  const timestamp = args.timestamp ?? Math.floor(Date.now() / 1000);
  const tags = args.tags.join(',');

  const params: Record<string, string | number> = {
    folder: args.folder,
    tags,
    timestamp,
  };
  if (args.publicId) params.public_id = args.publicId;

  const toSign = Object.keys(params)
    .sort()
    .map((k) => `${k}=${params[k]}`)
    .join('&');

  const signature = crypto
    .createHash('sha1')
    .update(toSign + args.apiSecret)
    .digest('hex');

  return {
    timestamp,
    signature,
    apiKey: args.apiKey,
    cloudName: args.cloudName,
    folder: args.folder,
    tags,
    uploadUrl: `https://api.cloudinary.com/v1_1/${args.cloudName}/image/upload`,
    params,
  };
}

/** Where a given kind of asset belongs inside the project folder. */
export function folderFor(
  cld: CloudinaryService,
  client: string,
  campaign: string,
  kind: AssetKind,
): string {
  const base = cld.projectFolder(client, campaign);
  return kind === 'logo' || kind === 'logo-reverse' || kind === 'brand-guide'
    ? `${base}/source/brand`
    : `${base}/source/${slug(kind)}`;
}

/**
 * Is this URL safe for the server to fetch on a caller's behalf?
 *
 * resolveAsset takes a URL from the request body (the logo and background
 * editors both pass one straight through), so without a check this function is
 * a request-forgery hole: the caller chooses an address and the server fetches
 * it from inside the network. On Render the first thing anyone tries is the
 * instance metadata endpoint on 169.254.169.254.
 *
 * The routes that reach here are behind the admin token, so this is not
 * open to the world — but staff credentials leak, and the cost of the guard is
 * nothing. The Hub's own modules (image_picker, tourism) already refuse the
 * same shapes; this brings the ad builder in line.
 *
 * Allowed: https to a public host. Refused: any private, loopback,
 * link-local or unspecified address, and plain http, which on this network
 * only ever points somewhere internal.
 */
const PRIVATE_V4 = [
  /^10\./, /^127\./, /^169\.254\./, /^0\./,
  /^172\.(1[6-9]|2\d|3[01])\./, /^192\.168\./,
  /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./,   // CGNAT
];

export function assetUrlIsSafe(raw: string): { ok: boolean; reason?: string } {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return { ok: false, reason: 'not a valid URL' };
  }
  if (u.protocol !== 'https:') {
    return { ok: false, reason: 'only https is fetched' };
  }
  const host = u.hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.internal')) {
    return { ok: false, reason: 'internal host' };
  }
  // Literal IPv4.
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) {
    if (PRIVATE_V4.some((re) => re.test(host))) {
      return { ok: false, reason: 'private address' };
    }
  }
  // IPv6 loopback / link-local / unique-local.
  if (host === '::1' || host.startsWith('fe80:') || host.startsWith('fc') || host.startsWith('fd')) {
    return { ok: false, reason: 'private address' };
  }
  return { ok: true };
}

/* ------------------------------------------------------------- resolution */

export interface ResolveOptions {
  cacheDir: string;
  /** Passed through for logging only. */
  label?: string;
  timeoutMs?: number;
}

function cacheName(ref: string, ext: string): string {
  return crypto.createHash('sha1').update(ref).digest('hex').slice(0, 16) + ext;
}

/**
 * Turn any asset reference into a local file path the renderer can read.
 * Accepts a local path, an https URL, or `cloudinary:<publicId>`.
 */
export async function resolveAsset(ref: string, opts: ResolveOptions): Promise<string> {
  if (!ref) throw new Error('Empty asset reference');

  // Already local.
  if (!/^(https?:|cloudinary:)/i.test(ref)) {
    if (!fs.existsSync(ref)) throw new Error(`Asset not found on disk: ${ref}`);
    return ref;
  }

  let url = ref;
  if (ref.toLowerCase().startsWith('cloudinary:')) {
    const publicId = ref.slice('cloudinary:'.length);
    const cloud = process.env.CLOUDINARY_CLOUD_NAME;
    if (!cloud) throw new Error('CLOUDINARY_CLOUD_NAME is not set, cannot resolve a Cloudinary asset');
    // f_auto would hand back a format sharp may not expect; ask for the
    // original bytes and let the renderer decide.
    url = `https://res.cloudinary.com/${cloud}/image/upload/${publicId}`;
  }

  fs.mkdirSync(opts.cacheDir, { recursive: true });
  const guessedExt = path.extname(new URL(url).pathname) || '.bin';
  const cached = path.join(opts.cacheDir, cacheName(ref, guessedExt));
  if (fs.existsSync(cached) && fs.statSync(cached).size > 0) return cached;

  // Checked here rather than at the call sites: every path into this function
  // ends at the same fetch, and a guard the caller has to remember is a guard
  // that gets forgotten by the next caller.
  const safe = assetUrlIsSafe(url);
  if (!safe.ok) {
    throw new Error(`Refusing to fetch ${opts.label ?? 'asset'}: ${safe.reason}`);
  }

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 15_000);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`Fetching ${opts.label ?? 'asset'} failed: ${res.status} ${url}`);
    const buf = Buffer.from(await res.arrayBuffer());
    if (buf.length === 0) throw new Error(`Empty response for ${url}`);
    if (buf.length > MAX_UPLOAD_BYTES) throw new Error(`Asset exceeds ${MAX_UPLOAD_BYTES} bytes: ${url}`);
    fs.writeFileSync(cached, buf);
    return cached;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Logos arrive as SVG surprisingly often, and sharp cannot composite an SVG
 * into a raster pipeline without rasterising it first. Do that once, at a size
 * generous enough for the largest placement (970x250 at 2x).
 */
export async function prepareLogo(file: string, cacheDir: string): Promise<string> {
  if (path.extname(file).toLowerCase() !== '.svg') return file;
  fs.mkdirSync(cacheDir, { recursive: true });
  const out = path.join(cacheDir, path.basename(file, '.svg') + '-raster.png');
  if (fs.existsSync(out)) return out;
  await sharp(file, { density: 300 })
    .resize({ width: 1200, withoutEnlargement: false, fit: 'inside' })
    .png()
    .toFile(out);
  return out;
}

/** Reject anything that is not a usable image before it reaches the renderer. */
export async function validateAsset(
  file: string,
): Promise<{ ok: boolean; width?: number; height?: number; format?: string; reason?: string }> {
  try {
    const meta = await sharp(file).metadata();
    if (!meta.width || !meta.height) return { ok: false, reason: 'Could not read image dimensions' };
    if (meta.width < 200 || meta.height < 200) {
      return {
        ok: false,
        width: meta.width,
        height: meta.height,
        format: meta.format,
        reason: `Too small at ${meta.width}x${meta.height}. Hero images should be at least 1200px on the long edge.`,
      };
    }
    return { ok: true, width: meta.width, height: meta.height, format: meta.format };
  } catch (e: any) {
    return { ok: false, reason: `Not a readable image: ${e?.message ?? e}` };
  }
}

/**
 * Resolve every asset reference on a brand and its concepts to local files.
 * Called before rendering so the pipeline downstream stays filesystem-only.
 */
export async function materializeAssets(
  refs: Record<string, string | undefined>,
  cacheDir: string,
): Promise<{ resolved: Record<string, string>; errors: string[] }> {
  const resolved: Record<string, string> = {};
  const errors: string[] = [];
  for (const [key, ref] of Object.entries(refs)) {
    if (!ref) continue;
    try {
      let file = await resolveAsset(ref, { cacheDir, label: key });
      if (key.startsWith('logo')) file = await prepareLogo(file, cacheDir);
      const check = await validateAsset(file);
      if (!check.ok && !key.startsWith('logo')) {
        errors.push(`${key}: ${check.reason}`);
        continue;
      }
      resolved[key] = file;
    } catch (e: any) {
      errors.push(`${key}: ${e?.message ?? e}`);
    }
  }
  return { resolved, errors };
}
