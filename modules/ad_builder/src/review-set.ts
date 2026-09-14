import * as fs from 'node:fs';
import * as path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import sharp from 'sharp';
import { renderOne } from './render';
import { getPlatform, getTemplate } from './registry';
import { artworkFingerprint, campaignRevision, CampaignConflict, readCampaign } from './campaign-state';
import { captureVersion } from './history';
import type { ProjectStore, Project, SizeApproval } from './projects';
import type { SizeKey, QaFinding } from './types';

const active = new Map<string, string>();
let reviewQueue: Promise<void> = Promise.resolve();
const qaVersion = () => process.env.RENDER_GIT_COMMIT || process.env.BUILD_SHA || 'development';
export const fileHash = (file: string) => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
export const fileUrl = (out: string, file: string) => '/files/' + path.relative(out, file).split(path.sep).map(encodeURIComponent).join('/');
type Cell = { conceptId: string; platform: string; size: string; inputHash: string; fileHash?: string;
  file?: string; image?: string; qa: QaFinding[]; status: 'pass'|'warn'|'fail'; overrideFile?: string };
type Review = { qaVersion?: string; id: string; projectId: string; revision: string; createdAt: string;
  status: 'building'|'ready'|'failed'; error?: string; cells: Cell[] };
const reviewFile = (out: string, id: string) => {
  if (!/^[a-f0-9-]{36}$/.test(id)) throw new Error('Invalid review identifier');
  return path.join(out, 'reviews', id, 'review.json');
};
function writeReview(out: string, r: Review) {
  const file = reviewFile(out, r.id); fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = file + '.tmp'; fs.writeFileSync(tmp, JSON.stringify(r)); fs.renameSync(tmp, file);
}
export function readReview(out: string, id: string, projectId: string): Review {
  const r: Review = JSON.parse(fs.readFileSync(reviewFile(out, id), 'utf8'));
  if (r.projectId !== projectId) throw new Error('This review belongs to another project');
  if (r.status === 'building' && active.get(projectId) !== id) return { ...r, status: 'failed', error: 'Review interrupted. Build a new contact sheet.' };
  return r;
}
export function beginReview(out: string, root: string, project: Project, resumeId?: string): string {
  const running = active.get(project.projectId); if (running) return running;
  const campaignFile = path.join(out, 'campaigns', project.requestId + '.json');
  const doc = readCampaign(campaignFile);
  const r: Review = { qaVersion: qaVersion(), id: resumeId || randomUUID(), projectId: project.projectId, revision: campaignRevision(doc),
    createdAt: new Date().toISOString(), status: 'building', cells: [] };
  captureVersion(campaignFile, doc, r.revision);
  writeReview(out, r); active.set(project.projectId, r.id);
  const run = async () => {
    try {
      const notes = (doc.notes ?? []).filter((n: string) => /copy|model|openai/i.test(n) && /fell back|form answers|no .*key|fail|not configured|error/i.test(n));
      for (const concept of doc.campaign.concepts) for (const platform of doc.platforms ?? ['google']) {
        for (const size of Object.keys(getPlatform(platform).sizes) as SizeKey[]) {
          if (!getTemplate(concept.layoutFamily).sizes[size] || getPlatform(platform).sizes[size]!.enabled === false) continue;
          const key = { conceptId: concept.conceptId, platform, size };
          const cell: Cell = { ...key, inputHash: artworkFingerprint(doc, key, root), status: 'fail', qa: [] };
          try {
            const override = project.overrides?.find(o => o.conceptId === key.conceptId && o.platform === platform && o.size === size);
            if (override) {
              const bytes = fs.readFileSync(override.file); const meta = await sharp(bytes).metadata();
              const rule = getPlatform(platform).sizes[size]!; const [w, h] = size.split('x').map(Number);
              if (!['png','jpeg'].includes(meta.format ?? '') || meta.width !== w * rule.deliverScale || meta.height !== h * rule.deliverScale || bytes.length > rule.maxFileBytes) throw new Error('Replacement file fails format, dimensions or file-weight checks.');
              cell.file = path.join(out, 'reviews', r.id, `${key.conceptId}-${platform}-${size}.${meta.format === 'jpeg' ? 'jpg' : 'png'}`);
              fs.writeFileSync(cell.file, bytes); cell.overrideFile = override.file;
              cell.qa = [{ check: 'manual-artwork-review', status: 'warn', detail: 'Manually replaced artwork requires individual visual approval.' }];
            } else {
              const rendered = await renderOne({ brand: doc.campaign.brand, concept, platform, size, assetRoot: root, outDir: path.join(out, 'reviews', r.id) });
              cell.file = rendered.file; cell.qa = rendered.qa;
            }
            for (const note of notes) cell.qa.push({ check: 'copy-review', status: 'warn', detail: note });
            cell.fileHash = fileHash(cell.file!); cell.image = fileUrl(out, cell.file!);
            cell.status = cell.qa.some(q => q.status === 'fail') ? 'fail' : cell.qa.some(q => q.status === 'warn') ? 'warn' : 'pass';
          } catch (e: any) { cell.qa.push({ check: 'render', status: 'fail', detail: e.message }); }
          r.cells.push(cell); writeReview(out, r);
        }
      }
      r.status = 'ready';
    } catch (e: any) { r.status = 'failed'; r.error = e.message; }
    finally {
      try { writeReview(out, r); } catch { console.error('Could not persist contact-sheet completion', r.id); }
      active.delete(project.projectId);
    }
  };
  reviewQueue = reviewQueue.then(run, run);
  return r.id;
}
export function latestReview(out: string, projectId: string): Review | null {
  const dir=path.join(out,'reviews');if(!fs.existsSync(dir))return null;
  const rows=fs.readdirSync(dir).flatMap(id=>{try{return [readReview(out,id,projectId)];}catch{return [];}});
  return rows.sort((a,b)=>b.createdAt.localeCompare(a.createdAt))[0]||null;
}
export function recoverReviews(out:string,root:string,store:ProjectStore) {
  const dir=path.join(out,'reviews');if(!fs.existsSync(dir))return;
  for(const id of fs.readdirSync(dir))try{
    const r:Review=JSON.parse(fs.readFileSync(reviewFile(out,id),'utf8'));
    if(r.status!=='building')continue;
    const project=store.get(r.projectId);
    if(!project)continue;
    const doc=readCampaign(path.join(out,'campaigns',project.requestId+'.json'));
    if(campaignRevision(doc)!==r.revision){r.status='failed';r.error='The draft changed during the restart. Build a new contact sheet.';writeReview(out,r);continue;}
    beginReview(out,root,project,id);
  }catch(e){console.error('Could not recover contact sheet',id,String(e));}
}
export function approveReview(out: string, root: string, store: ProjectStore, project: Project, id: string, revision: string, by?: string) {
  const r = readReview(out, id, project.projectId);
  if ((r.qaVersion || 'development') !== qaVersion()) throw new CampaignConflict('The review checks changed after this sheet was captured. Build a new contact sheet.');
  if (r.status !== 'ready') throw new CampaignConflict('The contact sheet must finish before approval.');
  const doc = readCampaign(path.join(out, 'campaigns', project.requestId + '.json'));
  if (r.revision !== revision || campaignRevision(doc) !== revision) throw new CampaignConflict('The saved version changed. Build and review a new contact sheet.');
  if ((doc.notes ?? []).some((n: string) => /copy|model|openai/i.test(n) && /fell back|form answers|no .*key|fail|not configured|error/i.test(n))) throw new CampaignConflict('Copy warnings require individual review. Build a new contact sheet.');
  const fresh = store.get(project.projectId)!;
  const groups = new Map<string, Cell[]>();
  for (const c of r.cells) { const k = c.conceptId + '/' + c.size; groups.set(k, [...(groups.get(k) ?? []), c]); }
  const approved: string[] = [], skipped: string[] = [];
  const additions: SizeApproval[] = [];
  for (const [key, cells] of groups) {
    if (cells.some(c => c.status !== 'pass')) { skipped.push(key); continue; }
    // A size is approved only if every bought platform has passed.
    const expected = (doc.platforms ?? ['google']).filter(p => getPlatform(p).sizes[cells[0].size as SizeKey]?.enabled !== false && getPlatform(p).sizes[cells[0].size as SizeKey]);
    if (expected.some(p => !cells.some(c => c.platform === p))) throw new CampaignConflict('Incomplete review; build a new contact sheet.');
    for (const c of cells) {
      if (artworkFingerprint(doc, c, root) !== c.inputHash || !c.file || fileHash(c.file) !== c.fileHash || fresh.overrides?.some(o => o.conceptId === c.conceptId && o.platform === c.platform && o.size === c.size)) throw new CampaignConflict('Artwork changed after review. Build a new contact sheet.');
      if (!fresh.approvals?.some(a => a.conceptId === c.conceptId && a.platform === c.platform && a.size === c.size)) additions.push({ conceptId: c.conceptId, platform: c.platform, size: c.size,
        inputHash: c.inputHash, fileHash: c.fileHash, artifact: c.file, campaignRevision: revision, at: new Date().toISOString(), by, acceptedWarnings: [] });
    }
    approved.push(key);
  }
  fresh.approvals = [...(fresh.approvals ?? []), ...additions]; store.save(fresh);
  return { approved, skipped, approvals: fresh.approvals };
}
