/**
 * The campaign the browser test opens: a copy of the sample, under a fixed
 * request id, in a renderer's output directory.
 *
 * Shared by the test (which starts its own renderer) and the nightly
 * workflow (which starts the renderer and the Hub itself, then runs the test
 * against the Hub). One copy of the seeding, so the two cannot open different
 * campaigns and pass for different reasons.
 *
 *   npx tsx tests-browser/seed.ts <outputDir> [requestId]
 */
import * as fs from 'node:fs';
import * as path from 'node:path';

export const E2E_REQUEST = 'AD-E2E-000001';

export function seedCampaign(outDir: string, requestId = E2E_REQUEST): string {
  const root = path.resolve(__dirname, '..');
  fs.mkdirSync(path.join(outDir, 'campaigns'), { recursive: true });
  const sample = JSON.parse(fs.readFileSync(path.join(root, 'campaigns', 'bella-vista-catering.json'), 'utf8'));
  sample.requestId = requestId;
  const file = path.join(outDir, 'campaigns', `${requestId}.json`);
  fs.writeFileSync(file, JSON.stringify({ campaign: sample, platforms: ['google', 'meta'], notes: [] }, null, 2));
  return file;
}

if (require.main === module) {
  const [outDir, requestId] = process.argv.slice(2);
  if (!outDir) { console.error('usage: seed.ts <outputDir> [requestId]'); process.exit(2); }
  console.log(seedCampaign(outDir, requestId || E2E_REQUEST));
}
