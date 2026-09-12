import * as fs from 'node:fs';
import * as path from 'node:path';
import { randomUUID } from 'node:crypto';

export function historyDir(campaignFile: string): string {
  return path.join(path.dirname(path.dirname(campaignFile)), 'history', path.basename(campaignFile, '.json'));
}
export function captureVersion(campaignFile: string, doc: any, revision: string): void {
  const dir = historyDir(campaignFile);
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${revision}.json`);
  if (fs.existsSync(file)) return;
  const tmp = `${file}.${randomUUID()}.tmp`;
  try {
    fs.writeFileSync(tmp, JSON.stringify({ revision, savedAt: new Date().toISOString(),
      doc: { campaign: doc.campaign, platforms: doc.platforms, notes: doc.notes } }));
    fs.renameSync(tmp, file);
  } finally { if (fs.existsSync(tmp)) fs.unlinkSync(tmp); }
}
export function versions(campaignFile: string): any[] {
  const dir = historyDir(campaignFile);
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter(f => /^[a-f0-9]{64}\.json$/.test(f))
    .map(f => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8')))
    .sort((a, b) => b.savedAt.localeCompare(a.savedAt));
}
export function changes(before: any, after: any, at = ''): { path: string; before: any; after: any }[] {
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  if (before && after && typeof before === 'object' && typeof after === 'object') {
    return [...new Set([...Object.keys(before), ...Object.keys(after)])].sort()
      .flatMap(k => changes(before[k], after[k], at ? `${at}.${k}` : k));
  }
  return [{ path: at, before: before ?? null, after: after ?? null }];
}
