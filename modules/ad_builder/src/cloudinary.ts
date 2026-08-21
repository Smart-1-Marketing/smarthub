/**
 * Cloudinary service.
 *
 * Cloudinary is the asset library; this repo is not. Finished creatives are
 * uploaded into a per-project folder, tagged so they can be found again, and
 * the returned public_id is what gets stored downstream — never the delivery
 * URL, which is a function of the transformation and can change.
 *
 * Folder convention (one folder per project, created on first upload):
 *
 *   smart1-ads/<client>/<campaign>/
 *       source/                 customer uploads, Brandfetch assets
 *       generated/              AI-generated hero images
 *       proofs/<conceptId>/     300x250 previews for the proof screen
 *       final/<platform>/<conceptId>/   approved, QA-passed deliverables
 *
 * A note on folder modes. Cloudinary accounts are either in fixed-folder mode,
 * where the folder is a prefix of the public_id, or dynamic-folder mode, where
 * `asset_folder` is independent of the public_id. Search expressions differ:
 * `folder:` in the first, `asset_folder:` in the second. Set
 * CLOUDINARY_FOLDER_MODE to 'fixed' or 'dynamic' to match the account; the
 * search helper below builds the right expression either way.
 */

import * as fs from 'fs';
import * as path from 'path';
import { v2 as cloudinary } from 'cloudinary';

export type FolderMode = 'fixed' | 'dynamic';

export interface CloudinaryOptions {
  cloudName?: string;
  apiKey?: string;
  apiSecret?: string;
  folderMode?: FolderMode;
  /** Root folder all Smart 1 work lives under. */
  root?: string;
  /** Log intended calls instead of making them. */
  dryRun?: boolean;
}

export interface UploadedAsset {
  publicId: string;
  assetFolder: string;
  secureUrl: string;
  format: string;
  width: number;
  height: number;
  bytes: number;
  version: number;
  tags: string[];
  createdAt: string;
  /** Structured context metadata stored alongside the asset. */
  context?: Record<string, string>;
  /** True when produced by a dry run rather than a real upload. */
  simulated?: boolean;
}

export function slug(s: string): string {
  return s
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60);
}

export class CloudinaryService {
  readonly root: string;
  readonly folderMode: FolderMode;
  readonly dryRun: boolean;
  private configured = false;

  constructor(opts: CloudinaryOptions = {}) {
    this.root = opts.root ?? process.env.CLOUDINARY_ROOT_FOLDER ?? 'smart1-ads';
    this.folderMode =
      opts.folderMode ?? ((process.env.CLOUDINARY_FOLDER_MODE as FolderMode) || 'fixed');
    this.dryRun = opts.dryRun ?? false;

    const cloudName = opts.cloudName ?? process.env.CLOUDINARY_CLOUD_NAME;
    const apiKey = opts.apiKey ?? process.env.CLOUDINARY_API_KEY;
    const apiSecret = opts.apiSecret ?? process.env.CLOUDINARY_API_SECRET;

    if (cloudName && apiKey && apiSecret) {
      cloudinary.config({ cloud_name: cloudName, api_key: apiKey, api_secret: apiSecret, secure: true });
      this.configured = true;
    }
  }

  /** True when real API calls are possible. */
  get live(): boolean {
    return this.configured && !this.dryRun;
  }

  assertUsable(): void {
    if (!this.configured && !this.dryRun) {
      throw new Error(
        'Cloudinary credentials missing. Set CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY and ' +
          'CLOUDINARY_API_SECRET, or pass --dry-run to preview what would be uploaded.',
      );
    }
  }

  /* ------------------------------------------------------------- folders */

  /** `smart1-ads/icon-solar/summer-solar` — the project folder. */
  projectFolder(client: string, campaign: string): string {
    return `${this.root}/${slug(client)}/${slug(campaign)}`;
  }

  finalFolder(client: string, campaign: string, platform: string, conceptId: string): string {
    return `${this.projectFolder(client, campaign)}/final/${slug(platform)}/concept-${slug(conceptId)}`;
  }

  proofFolder(client: string, campaign: string, conceptId: string): string {
    return `${this.projectFolder(client, campaign)}/proofs/concept-${slug(conceptId)}`;
  }

  /**
   * Create the project folder skeleton. Cloudinary creates folders implicitly
   * on upload, but creating them up front means the project is visible in the
   * Media Library the moment a request is submitted, before any render runs.
   */
  async createProjectFolders(client: string, campaign: string): Promise<string[]> {
    this.assertUsable();
    const base = this.projectFolder(client, campaign);
    const folders = [
      base,
      `${base}/source`,
      `${base}/generated`,
      `${base}/proofs`,
      `${base}/final`,
    ];
    for (const folder of folders) {
      if (!this.live) continue;
      try {
        await cloudinary.api.create_folder(folder);
      } catch (e: any) {
        // Already existing is not an error worth stopping for.
        if (!/exists|409|already/i.test(String(e?.message ?? e))) throw e;
      }
    }
    return folders;
  }

  /* -------------------------------------------------------------- upload */

  async uploadCreative(args: {
    file: string;
    folder: string;
    publicId: string;
    tags: string[];
    context: Record<string, string | number>;
  }): Promise<UploadedAsset> {
    this.assertUsable();
    const { file, folder, publicId, tags, context } = args;

    if (!this.live) {
      const stat = fs.statSync(file);
      return {
        publicId: this.folderMode === 'fixed' ? `${folder}/${publicId}` : publicId,
        assetFolder: folder,
        secureUrl: `https://res.cloudinary.com/<cloud>/image/upload/${folder}/${publicId}`,
        format: path.extname(file).replace('.', ''),
        width: 0,
        height: 0,
        bytes: stat.size,
        version: 0,
        tags,
        createdAt: new Date().toISOString(),
        simulated: true,
      };
    }

    const res = await cloudinary.uploader.upload(file, {
      folder,
      public_id: publicId,
      resource_type: 'image',
      overwrite: true,
      // Deliver exactly what QA approved. Any automatic re-encoding would
      // invalidate the file-weight check we just ran.
      quality: 100,
      tags,
      context,
      use_filename: false,
      unique_filename: false,
    });

    return {
      publicId: res.public_id,
      assetFolder: (res as any).asset_folder ?? folder,
      secureUrl: res.secure_url,
      format: res.format,
      width: res.width,
      height: res.height,
      bytes: res.bytes,
      version: res.version,
      tags: res.tags ?? tags,
      createdAt: res.created_at,
    };
  }

  /* -------------------------------------------------------------- search */

  /**
   * Find every asset under a project folder. Handles both folder modes and
   * paginates, since a multi-concept campaign easily exceeds one page.
   */
  async searchFolder(
    folder: string,
    opts: { recursive?: boolean; max?: number } = {},
  ): Promise<UploadedAsset[]> {
    this.assertUsable();
    if (!this.live) return [];

    const { recursive = true, max = 500 } = opts;
    const field = this.folderMode === 'dynamic' ? 'asset_folder' : 'folder';
    const expression = recursive
      ? `${field}:"${folder}/*" OR ${field}:"${folder}"`
      : `${field}:"${folder}"`;

    const out: UploadedAsset[] = [];
    let cursor: string | undefined;

    do {
      const q = cloudinary.search
        .expression(expression)
        .with_field('context')
        .with_field('tags')
        .sort_by('public_id', 'asc')
        .max_results(Math.min(100, max - out.length));
      if (cursor) q.next_cursor(cursor);

      const res: any = await q.execute();
      for (const r of res.resources ?? []) {
        out.push({
          publicId: r.public_id,
          assetFolder: r.asset_folder ?? r.folder ?? folder,
          secureUrl: r.secure_url,
          format: r.format,
          width: r.width,
          height: r.height,
          bytes: r.bytes,
          version: r.version,
          tags: r.tags ?? [],
          createdAt: r.created_at,
          ...(r.context?.custom ? { context: r.context.custom } : {}),
        } as UploadedAsset);
      }
      cursor = res.next_cursor;
    } while (cursor && out.length < max);

    return out;
  }

  /** Find a project folder by a partial client or campaign name. */
  async findProjectFolders(query: string): Promise<string[]> {
    this.assertUsable();
    if (!this.live) return [];
    const res: any = await cloudinary.api.sub_folders(this.root);
    const clients = (res.folders ?? []).map((f: any) => f.path as string);
    const matches: string[] = [];
    for (const client of clients) {
      const sub: any = await cloudinary.api.sub_folders(client);
      for (const campaign of sub.folders ?? []) {
        if (
          !query ||
          campaign.path.toLowerCase().includes(query.toLowerCase())
        ) {
          matches.push(campaign.path);
        }
      }
    }
    return matches;
  }
}
