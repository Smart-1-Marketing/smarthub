/**
 * The project record.
 *
 * Everything produced for a client hangs off one of these: the brand, the
 * assets, the landing page, and every render that has ever been made. It is
 * the thing you search six months later when someone asks "what did we run for
 * them last spring?".
 *
 * Storage is a JSON file per project plus an index, which is honest about what
 * it is — correct for one instance, wrong the moment there are two. The read
 * and write surface here is deliberately narrow so it can move to Postgres
 * without touching anything that calls it.
 *
 * Two things are load-bearing:
 *
 *   Dates. Every project carries createdAt, updatedAt, and a dated entry per
 *   render batch. Search defaults to newest first because that is what people
 *   actually want.
 *
 *   Links, not copies. The record stores Cloudinary public IDs and URLs. It
 *   never becomes a second copy of the asset library.
 */

import * as fs from 'node:fs';
import * as path from 'node:path';
import type { Brand, RenderResult } from './types';

export interface CreativeOverride {
  conceptId: string;
  platform: string;
  size: string;
  /** Absolute path of the uploaded replacement on disk. */
  file: string;
  originalName: string;
  bytes: number;
  uploadedAt: string;
}
import { slug } from './cloudinary';

export interface AssetLink {
  kind: 'logo' | 'logo-reverse' | 'hero' | 'product' | 'background' | 'brand-guide';
  publicId?: string;
  url?: string;
  /** Where it came from, so nobody has to guess later. */
  source: 'upload' | 'brandfetch' | 'generated' | 'placeholder';
  name?: string;
  addedAt: string;
}

export interface RenderBatch {
  batchId: string;
  renderedAt: string;
  platform: string;
  conceptId: string;
  /** Cloudinary public IDs of the finished creatives, by size. */
  ads: { size: string; publicId?: string; url?: string; status: string; bytes: number }[];
  proofUrl?: string;
  reportUrl?: string;
}

export interface Project {
  /** Contact person and email from the intake, so campaigns are searchable
   *  by who asked for them. */
  contact?: string;
  email?: string;
  /** Human-chosen name. This is what people search for. */
  projectName: string;
  /** Slug used for the Cloudinary folder and the record filename. */
  projectId: string;
  requestId: string;
  client: string;
  domain: string;
  campaignName: string;

  createdAt: string;
  updatedAt: string;
  status: 'draft' | 'in-build' | 'proof-sent' | 'approved' | 'complete' | 'archived';

  landingPage?: string;
  /** Cached result of reading the landing page, so it is not re-fetched. */
  landingAnalysis?: LandingAnalysis;

  brand?: Brand;
  /** True when the customer typed the brand in rather than Brandfetch finding it. */
  brandEnteredManually?: boolean;

  /** Which concept the client approved, recorded at approval time so delivery
   *  does not have to guess from the notes. */
  approvedConcept?: string;
  /** Hand-edited files that replace a rendered creative for one size. */
  overrides?: CreativeOverride[];
  /** Set once a delivery zip has been produced. */
  delivered?: { at: string; zipUrl: string; fileCount: number }[];
  /** The job id of the automatic render started at intake, so the public
   *  status endpoint can report real progress instead of guessing. */
  autoJobId?: string;

  cloudinaryFolder?: string;
  assets: AssetLink[];
  batches: RenderBatch[];
  notes: string[];
  /** Free-text terms folded into search: product, audience, offer, geography. */
  keywords: string[];
}

export interface LandingAnalysis {
  fetchedAt: string;
  url: string;
  title?: string;
  summary: string;
  detectedOffer?: string;
  detectedCta?: string;
  audience?: string;
  suggestedHeadlines: string[];
  suggestedSupport: string[];
  suggestedCtas: string[];
  warnings: string[];
  source: 'openai' | 'heuristic';
}

const INDEX = 'index.json';

export class ProjectStore {
  readonly dir: string;

  constructor(baseDir: string) {
    this.dir = path.join(baseDir, 'projects');
    fs.mkdirSync(this.dir, { recursive: true });
  }

  private file(projectId: string): string {
    return path.join(this.dir, `${projectId}.json`);
  }

  /**
   * Project names repeat — a client runs "Spring Promotion" every year — so the
   * id carries the client and a date. Colliding ids get a numeric suffix rather
   * than silently overwriting last year's work.
   */
  makeId(client: string, projectName: string, when = new Date()): string {
    const stamp = when.toISOString().slice(0, 10);
    const base = `${slug(client)}_${slug(projectName)}_${stamp}`;
    let id = base;
    let n = 2;
    while (fs.existsSync(this.file(id))) id = `${base}-${n++}`;
    return id;
  }

  create(input: {
    projectName: string;
    client: string;
    domain: string;
    campaignName: string;
    requestId: string;
    landingPage?: string;
    contact?: string;
    email?: string;
    brand?: Brand;
    brandEnteredManually?: boolean;
    cloudinaryFolder?: string;
    keywords?: string[];
    notes?: string[];
  }): Project {
    const now = new Date().toISOString();
    const project: Project = {
      projectName: input.projectName,
      projectId: this.makeId(input.client, input.projectName),
      requestId: input.requestId,
      client: input.client,
      domain: input.domain,
      campaignName: input.campaignName,
      createdAt: now,
      updatedAt: now,
      status: 'draft',
      landingPage: input.landingPage,
      contact: input.contact,
      email: input.email,
      brand: input.brand,
      brandEnteredManually: input.brandEnteredManually,
      cloudinaryFolder: input.cloudinaryFolder,
      assets: [],
      batches: [],
      notes: input.notes ?? [],
      keywords: (input.keywords ?? []).filter(Boolean),
    };
    this.save(project);
    return project;
  }

  save(project: Project): Project {
    project.updatedAt = new Date().toISOString();
    fs.writeFileSync(this.file(project.projectId), JSON.stringify(project, null, 2));
    this.reindex();
    return project;
  }

  get(projectId: string): Project | null {
    const f = this.file(projectId);
    return fs.existsSync(f) ? (JSON.parse(fs.readFileSync(f, 'utf8')) as Project) : null;
  }

  /** Find by the request id the intake form issued. */
  byRequest(requestId: string): Project | null {
    return this.all().find((p) => p.requestId === requestId) ?? null;
  }

  all(): Project[] {
    return fs
      .readdirSync(this.dir)
      .filter((f) => f.endsWith('.json') && f !== INDEX)
      .map((f) => JSON.parse(fs.readFileSync(path.join(this.dir, f), 'utf8')) as Project);
  }

  addAsset(projectId: string, asset: Omit<AssetLink, 'addedAt'>): Project | null {
    const p = this.get(projectId);
    if (!p) return null;
    // Replace rather than append when the same slot is re-uploaded, so the
    // record shows what is in use, not a pile of superseded logos.
    p.assets = p.assets.filter((a) => !(a.kind === asset.kind && a.publicId === asset.publicId));
    p.assets.push({ ...asset, addedAt: new Date().toISOString() });
    return this.save(p);
  }

  addBatch(projectId: string, results: RenderResult[], extra: Partial<RenderBatch> = {}): Project | null {
    const p = this.get(projectId);
    if (!p || !results.length) return null;
    const batch: RenderBatch = {
      batchId: `${results[0].platform}-${results[0].conceptId}-${Date.now().toString(36)}`,
      renderedAt: new Date().toISOString(),
      platform: results[0].platform,
      conceptId: results[0].conceptId,
      ads: results.map((r) => ({
        size: r.size,
        status: r.status,
        bytes: r.bytes,
      })),
      ...extra,
    };
    p.batches.push(batch);
    if (p.status === 'draft') p.status = 'in-build';
    return this.save(p);
  }

  /**
   * Search across the fields people actually remember: client, project name,
   * campaign, domain, landing page, and the keywords lifted from the brief.
   * Optional date window, newest first.
   */
  search(opts: {
    q?: string;
    client?: string;
    status?: Project['status'];
    from?: string;
    to?: string;
    limit?: number;
  } = {}): Project[] {
    const q = (opts.q ?? '').trim().toLowerCase();
    const terms = q ? q.split(/\s+/) : [];

    return this.all()
      .filter((p) => {
        if (opts.status && p.status !== opts.status) return false;
        if (opts.client && slug(p.client) !== slug(opts.client)) return false;
        if (opts.from && p.createdAt < opts.from) return false;
        if (opts.to && p.createdAt > opts.to) return false;
        if (!terms.length) return true;
        const hay = [
          p.projectName, p.client, p.campaignName, p.domain,
          p.landingPage ?? '', p.requestId, p.status,
          p.contact ?? '',
          ...(p.keywords ?? []),
          p.landingAnalysis?.summary ?? '',
        ].join(' ').toLowerCase();
        // Every term must appear: narrowing a search should narrow results.
        return terms.every((t) => hay.includes(t));
      })
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
      .slice(0, opts.limit ?? 100);
  }

  /** A compact index so a list view does not read every project file. */
  private reindex(): void {
    const rows = this.all()
      .map((p) => ({
        projectId: p.projectId,
        projectName: p.projectName,
        client: p.client,
        campaignName: p.campaignName,
        requestId: p.requestId,
        status: p.status,
        createdAt: p.createdAt,
        updatedAt: p.updatedAt,
        adCount: p.batches.reduce((n, b) => n + b.ads.length, 0),
        landingPage: p.landingPage,
      }))
      .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    fs.writeFileSync(path.join(this.dir, INDEX), JSON.stringify({ generatedAt: new Date().toISOString(), rows }, null, 2));
  }
}
