/**
 * CLI: render a campaign package and print the QA report.
 *
 *   npx tsx src/cli.ts --campaign src/examples/icon-solar.json --platform google
 *   npx tsx src/cli.ts --platform amazon --concept A --svg
 */

import * as fs from 'fs';
import * as path from 'path';
import type { Campaign, RenderResult, SizeKey } from './types';
import { renderPackage } from './render';
import { missingSizes } from './registry';
import { CloudinaryService, slug, type UploadedAsset } from './cloudinary';
import { buildManifest, contextFor, tagsFor } from './manifest';
import { writeReports } from './report';
import { copyForSize } from './render';
import { printFindings, validateCampaign } from './validate';

const ROOT = path.resolve(__dirname, '..');

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--')
    ? process.argv[i + 1]
    : fallback;
}
const flag = (name: string) => process.argv.includes(`--${name}`);

const ICON = { pass: '  ok  ', warn: ' warn ', fail: ' FAIL ' } as const;

function report(results: RenderResult[]) {
  const pad = (s: string, n: number) => s.padEnd(n).slice(0, n);
  console.log('');
  console.log(
    `${pad('SIZE', 10)}${pad('DELIVERED', 12)}${pad('FMT', 5)}${pad('WEIGHT', 11)}${pad('WORDS', 7)}STATUS`,
  );
  console.log('-'.repeat(60));
  for (const r of results) {
    console.log(
      pad(r.size, 10) +
        pad(`${r.width}x${r.height}`, 12) +
        pad(r.format, 5) +
        pad(`${(r.bytes / 1024).toFixed(1)} KB`, 11) +
        pad(String(r.wordCount), 7) +
        `[${ICON[r.status]}]`,
    );
    for (const f of r.qa) {
      if (f.status === 'pass') continue;
      console.log(`          ${f.status === 'fail' ? 'x' : '!'} ${f.check}: ${f.detail}`);
      if (f.fix) {
        console.log(
          `            -> auto-fix: shorten ${f.fix.role}` +
            (f.fix.maxWords ? ` to <= ${f.fix.maxWords} words` : ''),
        );
      }
    }
  }
  const fails = results.filter((r) => r.status === 'fail').length;
  const warns = results.filter((r) => r.status === 'warn').length;
  console.log('-'.repeat(60));
  console.log(
    `${results.length} rendered, ${results.length - fails - warns} clean, ${warns} warning, ${fails} failing`,
  );
}

async function main() {
  const campaignFile = arg('campaign', path.join(ROOT, 'src/examples/icon-solar.json'))!;
  const isExample = campaignFile.includes('src/examples/');
  const platform = arg('platform', 'google')!;
  const only = arg('concept');
  const sizeArg = arg('size');
  const outDir = arg('out', path.join(ROOT, 'out'))!;
  const wantUpload = flag('upload') || flag('dry-run');
  const dryRun = flag('dry-run');
  const uploadAll = flag('upload-all');

  const campaign: Campaign = JSON.parse(fs.readFileSync(campaignFile, 'utf8'));

  if (isExample && !arg('campaign')) {
    console.log('No --campaign given; using the bundled Icon Solar example.');
    console.log('Scaffold a real client with: npx tsx scripts/scaffold.ts --client ... --domain ... --campaign ...');
  }

  // Validate before rendering. A brand font that is not in the registry would
  // otherwise render in the fallback face and look finished.
  const findings = validateCampaign(campaign, { assetRoot: ROOT, platforms: [platform] });
  const { errors, warnings } = (() => {
    const e = findings.filter((x) => x.level === 'error');
    const w = findings.filter((x) => x.level === 'warning');
    if (e.length || w.length) {
      console.log(`\nValidation (${campaign.brand.name}):`);
      return printFindings(findings, flag('verbose'));
    }
    return { errors: 0, warnings: 0 };
  })();
  if (errors && !flag('skip-validation')) {
    console.error(`\n${errors} validation error(s). Fix them, or pass --skip-validation to render anyway.`);
    process.exit(1);
  }
  if (warnings) console.log('');

  const concepts = only
    ? campaign.concepts.filter((c) => c.conceptId === only)
    : campaign.concepts;

  if (!concepts.length) throw new Error(`No concept matched "${only}"`);

  const all: RenderResult[] = [];
  for (const concept of concepts) {
    console.log(
      `\n=== ${campaign.brand.name} / ${campaign.campaignName} / Concept ${concept.conceptId} "${concept.name}" (${concept.layoutFamily}) on ${platform} ===`,
    );
    const gaps = missingSizes(concept.layoutFamily, platform);
    if (gaps.length) {
      console.log(
        `    note: ${concept.layoutFamily} has no layout for ${gaps.join(', ')} — skipped`,
      );
    }
    const results = await renderPackage({
      brand: campaign.brand,
      concept,
      platform,
      outDir,
      assetRoot: ROOT,
      sizes: sizeArg ? [sizeArg as SizeKey] : undefined,
      emitSvg: flag('svg'),
    });
    report(results);
    all.push(...results);
  }

  /* ------------------------------------------------------------- upload */

  const cld = new CloudinaryService({ dryRun });
  const projectFolder = cld.projectFolder(campaign.brand.name, campaign.campaignName);
  const uploads = new Map<string, UploadedAsset>();

  if (wantUpload) {
    cld.assertUsable();
    console.log(
      `\nCloudinary: ${dryRun ? 'DRY RUN — no API calls' : cld.live ? 'live' : 'not configured'}`,
    );
    const folders = await cld.createProjectFolders(campaign.brand.name, campaign.campaignName);
    console.log(`Project folder: ${projectFolder}`);
    if (!dryRun) console.log(`  created/verified ${folders.length} folders`);

    // A creative that failed QA does not belong in the client's asset library.
    const shippable = all.filter((r) => uploadAll || r.status !== 'fail');
    const withheld = all.length - shippable.length;

    for (const r of shippable) {
      const concept = campaign.concepts.find((c) => c.conceptId === r.conceptId)!;
      const folder = cld.finalFolder(
        campaign.brand.name,
        campaign.campaignName,
        r.platform,
        r.conceptId,
      );
      const publicId = `${slug(campaign.brand.domain)}_${r.conceptId}_${r.size}`;
      const copy = copyForSize(concept, r.size);
      const asset = await cld.uploadCreative({
        file: r.file,
        folder,
        publicId,
        tags: tagsFor(campaign, r, concept.name),
        context: contextFor(campaign, r, copy.headline ?? ''),
      });
      uploads.set(r.file, asset);
      console.log(`  ${dryRun ? 'would upload' : 'uploaded'}  ${asset.publicId}`);
    }
    if (withheld) {
      console.log(`  withheld ${withheld} failing creative(s) — pass --upload-all to override`);
    }
  }

  /* ------------------------------------------------------------ reports */

  const manifest = buildManifest({
    campaign,
    projectFolder,
    results: all,
    uploads,
    dryRun,
    uploaded: wantUpload,
  });
  const reportDir = path.join(outDir, 'reports');
  const written = writeReports(manifest, reportDir);

  fs.writeFileSync(path.join(outDir, `qa-${platform}.json`), JSON.stringify(all, null, 2));
  console.log('\nReports:');
  for (const f of written) console.log(`  ${f}`);

  if (all.some((r) => r.status === 'fail')) process.exitCode = 1;
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
