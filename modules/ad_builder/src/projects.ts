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

/**
 * One size, signed off.
 *
 * A set is worked size by size and there was nothing recording which ones were
 * finished, so "have I done the 300x600 yet" was answered by looking at it and
 * remembering. Worse: nothing stopped an edit to the brand colours or the
 * layout family — both of which are concept-wide — from silently changing a
 * size somebody had already passed.
 *
 * So an approval is stored per concept, platform and size, and the build
 * screen locks the controls on a size that carries one. Unapproving is one
 * click, because a lock nobody can undo is a lock people work around.
 */
export interface SizeApproval {
  conceptId: string;
  platform: string;
  size: string;
  at: string;
  by?: string;
}

/**
 * One animated size, filed against the project.
 *
 * Its own list rather than a row on RenderBatch, because an animation is not
 * produced by the same job as the static pack and must not be: the static
 * build is what gets made every time and an animation is asked for afterwards,
 * on the sizes that take one. Folding it into a batch would mean either
 * re-rendering eight static ads to add a GIF or writing a batch with no static
 * ads in it, and both lie about what happened.
 *
 * A re-render supersedes the record for its own concept, platform and size and
 * leaves every other one alone -- see recordAnimations.
 */
export interface AnimationRecord {
  conceptId: string;
  platform: string;
  size: string;
  /** Absolute path on the render disk. */
  file: string;
  /** /files/... path, which is what a browser and the proof page read. */
  url: string;
  bytes: number;
  frames: number;
  loop: number;
  totalMs: number;
  fps: number;
  kind: 'text' | 'button';
  status: 'pass' | 'warn' | 'fail';
  /** Non-passing findings, in words, so a screen does not have to re-run QA
   *  to say what is wrong with a file it is listing. */
  issues: string[];
  renderedAt: string;

  /**
   * When this one animation was approved, and by whom.
   *
   * Per animation, not per project and not per concept. A set of eight moving
   * ads is eight separate decisions: somebody watches one, likes it, and sends
   * it -- and the next one may need its second slide rewritten. A single
   * project-wide flag would deliver the seven nobody has watched on the
   * strength of the one they did.
   *
   * Approval is what makes an animation deliverable, and it is the ONLY thing
   * that does: the delivery zip does not carry these, so an animation that is
   * never approved is never sent, rather than riding out inside a package the
   * static set's approval covered.
   */
  approvedAt?: string;
  approvedBy?: string;
  /**
   * Where the approved file was stored so a client can be given it.
   *
   * Written at approval and not at render: an animation nobody approves is
   * never uploaded, so the account is not paying to store drafts. Absent on an
   * approved row means the approval landed and the upload did not, which is a
   * real state and a different one from not approved -- the two are reported
   * separately for the reason `hub/domain_links.py` gives at length.
   */
  cloudinaryPublicId?: string;
  cloudinaryUrl?: string;
  /**
   * When a previous version of THIS size was approved, if one was.
   *
   * Re-animating a size replaces its row, and the approval goes with it,
   * because a sign-off is about the file as it was -- the rule
   * `modules/commercial_builder/review_spec.py` works to. Carrying the old
   * date lets a screen say "approved on the 3rd, and rebuilt since", which is
   * a different thing to tell somebody than "nobody has approved this".
   */
  previouslyApprovedAt?: string;
}

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
  /** Sizes a person has signed off. See SizeApproval. */
  approvals?: SizeApproval[];
  /**
   * Which lettered variant of an ad set this is.
   *
   * A duplicate used to be called "<name> (copy)", and a second duplicate
   * "<name> (copy) (copy)", which stops being a name and starts being a
   * count of clicks. Concepts are lettered in this business, so a duplicate
   * is Concept B, its next duplicate is Concept C, and the letter is stored
   * rather than parsed back out of the name.
   */
  conceptLetter?: string;
  /** The project this set was first duplicated from, however many
   *  duplications ago. The letters are allocated across that whole family, so
   *  duplicating B gives C rather than a second B. */
  conceptRoot?: string;
  /** Set once a delivery zip has been produced. */
  delivered?: { at: string; zipUrl: string; fileCount: number }[];
  /** Animated versions, produced after the static build exists. */
  animations?: AnimationRecord[];
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

  /* ----------------------------------------------------------- animations */

  /**
   * File animated versions, replacing only what was re-rendered.
   *
   * Keyed on concept, platform and size. Animating three sizes must not drop
   * the five that were animated last week -- a list that quietly gets shorter
   * cannot be told from one that failed to load, and the delivery ZIP reads
   * this list.
   */
  recordAnimations(project: Project, records: AnimationRecord[]): AnimationRecord[] {
    const key = (a: { conceptId: string; platform: string; size: string }) =>
      `${a.conceptId}\u0000${a.platform}\u0000${a.size}`;
    const incoming = new Set(records.map(key));
    const previous = new Map(
      (project.animations ?? []).map((a) => [key(a), a] as const),
    );
    // A rebuilt size arrives unapproved, which is correct -- the approval was
    // about the file it replaces. What is carried across is the FACT that one
    // existed, so the panel can say it was superseded rather than reading as a
    // size nobody ever looked at.
    for (const r of records) {
      const was = previous.get(key(r));
      const stamp = was?.approvedAt ?? was?.previouslyApprovedAt;
      if (stamp) r.previouslyApprovedAt = stamp;
    }
    const kept = (project.animations ?? []).filter((a) => !incoming.has(key(a)));
    project.animations = [...kept, ...records].sort(
      (a, b) => a.conceptId.localeCompare(b.conceptId) || a.size.localeCompare(b.size),
    );
    this.save(project);
    return project.animations;
  }

  /**
   * Sign one animation off, or take the sign-off back.
   *
   * One animation, never a set. Eight moving ads are eight decisions: somebody
   * watches one, likes it and sends it, and the next may need its second slide
   * rewritten. A project-wide flag would deliver the seven nobody has watched
   * on the strength of the one they did.
   *
   * A QA-failing animation cannot be approved at all. The delivery zip used to
   * be the thing that withheld those; it no longer carries animations, so this
   * is the only gate left between a clipped second slide and a client's asset
   * library. It refuses BY NAME rather than silently doing nothing, because a
   * button that appears to work and changes nothing is how somebody concludes
   * the file was sent.
   */
  approveAnimation(
    project: Project,
    key: { conceptId: string; platform: string; size: string },
    approved: boolean,
    by?: string,
  ): { ok: boolean; error?: string; row?: AnimationRecord } {
    const row = (project.animations ?? []).find(
      (a) => a.conceptId === key.conceptId && a.platform === key.platform && a.size === key.size,
    );
    if (!row) {
      return { ok: false, error: `There is no animated ${key.size} on concept ${key.conceptId}.` };
    }
    if (approved && row.status === 'fail') {
      return {
        ok: false,
        error:
          `The animated ${key.size} did not pass its checks, so it cannot be approved: ` +
          (row.issues[0] ?? 'see the findings on the build screen') +
          '. Fix it and animate that size again.',
      };
    }
    if (approved) {
      row.approvedAt = new Date().toISOString();
      row.approvedBy = (by ?? '').trim() || undefined;
    } else {
      // The upload is left where it is. Un-approving is "do not send this
      // yet", not "delete what was stored" -- and a Cloudinary id thrown away
      // here is one the next approval pays to create again.
      delete row.approvedAt;
      delete row.approvedBy;
    }
    this.save(project);
    return { ok: true, row };
  }

  /** Record where an approved animation was stored, so the Hub can file it. */
  noteAnimationUpload(
    project: Project,
    key: { conceptId: string; platform: string; size: string },
    upload: { publicId: string; url: string },
  ): void {
    const row = (project.animations ?? []).find(
      (a) => a.conceptId === key.conceptId && a.platform === key.platform && a.size === key.size,
    );
    if (!row) return;
    row.cloudinaryPublicId = upload.publicId;
    row.cloudinaryUrl = upload.url;
    this.save(project);
  }

  /**
   * Take an animation off a concept.
   *
   * The file is left on disk: `retention.ts` sweeps the render folder, and
   * deleting it here would break a proof link somebody has already sent while
   * this screen reported a clean removal.
   */
  forgetAnimations(project: Project, key: { conceptId?: string; platform?: string; size?: string }): number {
    const before = (project.animations ?? []).length;
    project.animations = (project.animations ?? []).filter((a) =>
      (key.conceptId && a.conceptId !== key.conceptId) ||
      (key.platform && a.platform !== key.platform) ||
      (key.size && a.size !== key.size));
    if (project.animations.length !== before) this.save(project);
    return before - project.animations.length;
  }

  /* ------------------------------------------------------------ approvals */

  /** Sign one size off, or take the sign-off back. Returns the new list. */
  setApproval(
    project: Project,
    key: { conceptId: string; platform: string; size: string },
    approved: boolean,
    by?: string,
  ): SizeApproval[] {
    const same = (a: SizeApproval) =>
      a.conceptId === key.conceptId && a.platform === key.platform && a.size === key.size;
    const kept = (project.approvals ?? []).filter((a) => !same(a));
    project.approvals = approved
      ? [...kept, { ...key, at: new Date().toISOString(), by }]
      : kept;
    this.save(project);
    return project.approvals;
  }

  /* ------------------------------------------------------- concept letters
     A duplicate is Concept B, its duplicate is Concept C, and so on across
     the whole family rather than per parent -- duplicating B twice must not
     produce two Concept Cs, because the letter is what people say out loud
     when they mean a particular set. */

  /** Every project descended from the same original, the original included. */
  familyOf(project: Project): Project[] {
    const root = project.conceptRoot ?? project.projectId;
    return this.all().filter((p) => (p.conceptRoot ?? p.projectId) === root);
  }

  /**
   * The next free letter in `project`'s family. Runs A..Z and then stops
   * lettering rather than wrapping to a second A: twenty-six variants of one
   * ad set is not a naming problem any more.
   */
  nextConceptLetter(project: Project): string | null {
    const taken = new Set(
      this.familyOf(project).map((p) => (p.conceptLetter ?? 'A').toUpperCase()),
    );
    for (let i = 0; i < 26; i++) {
      const letter = String.fromCharCode(65 + i);
      if (!taken.has(letter)) return letter;
    }
    return null;
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
