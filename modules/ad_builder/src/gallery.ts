/**
 * Gallery CLI — search Cloudinary for a project folder and build a gallery.
 *
 *   npx tsx src/gallery.ts --find icon                  list matching projects
 *   npx tsx src/gallery.ts --folder smart1-ads/icon-solar/summer-solar
 *   npx tsx src/gallery.ts --client "Icon Solar" --campaign "Summer Solar"
 *   npx tsx src/gallery.ts --folder ... --manifest out/reports/manifest_AD-2026-002138.json
 *
 * The gallery is built from a live folder search, not from local state, so it
 * shows what is actually in the library. Passing --manifest lets it fall back
 * to the last render when Cloudinary credentials are not configured, which is
 * what makes the command usable before the account is wired up.
 */

import * as fs from 'fs';
import * as path from 'path';
import { CloudinaryService } from './cloudinary';
import { renderGallery } from './report';
import type { Manifest } from './manifest';
import type { UploadedAsset } from './cloudinary';

const ROOT = path.resolve(__dirname, '..');

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] && !process.argv[i + 1].startsWith('--')
    ? process.argv[i + 1]
    : fallback;
}

/** Reconstruct gallery assets from a manifest when Cloudinary is unavailable. */
function assetsFromManifest(m: Manifest): UploadedAsset[] {
  return m.entries
    .filter((e) => e.cloudinary)
    .map((e) => {
      const [w, h] = e.deliveredDimensions.split('x').map(Number);
      return {
        publicId: e.cloudinary!.publicId,
        assetFolder: e.cloudinary!.assetFolder,
        // Local path so the page renders before anything is uploaded.
        secureUrl: e.cloudinary!.simulated
          ? path.relative(path.join(ROOT, 'out', 'reports'), e.localFile)
          : e.cloudinary!.secureUrl,
        format: e.format,
        width: w,
        height: h,
        bytes: e.bytes,
        version: e.cloudinary!.version,
        tags: [
          `platform:${e.platform}`,
          `concept:${e.conceptId}`,
          `size:${e.size}`,
          `qa:${e.qaStatus}`,
        ],
        createdAt: m.generatedAt,
        simulated: e.cloudinary!.simulated,
      };
    });
}

async function main() {
  const cld = new CloudinaryService();
  const find = arg('find');
  const manifestFile = arg('manifest');
  const outFile = arg('out');

  if (find !== undefined) {
    cld.assertUsable();
    const folders = await cld.findProjectFolders(find);
    if (!folders.length) {
      console.log(`No project folders under ${cld.root} matching "${find}"`);
      return;
    }
    console.log(`Project folders matching "${find}":`);
    for (const f of folders) console.log(`  ${f}`);
    return;
  }

  let folder = arg('folder');
  const client = arg('client');
  const campaign = arg('campaign');
  if (!folder && client && campaign) folder = cld.projectFolder(client, campaign);

  let manifest: Manifest | undefined;
  if (manifestFile) {
    manifest = JSON.parse(fs.readFileSync(manifestFile, 'utf8')) as Manifest;
    folder = folder ?? manifest.projectFolder;
  }
  if (!folder) {
    throw new Error(
      'Specify --folder, or --client and --campaign, or --manifest. Use --find <text> to list projects.',
    );
  }

  let assets: UploadedAsset[] = [];
  let sourceNote = '';

  if (cld.live) {
    assets = await cld.searchFolder(folder);
    sourceNote = `${assets.length} asset(s) from Cloudinary search`;
  }

  if (assets.length === 0 && manifest) {
    assets = assetsFromManifest(manifest);
    sourceNote = cld.live
      ? `Cloudinary search returned nothing; showing ${assets.length} from the manifest`
      : `Cloudinary not configured; showing ${assets.length} from the manifest`;
  }

  if (!cld.live && !manifest) {
    throw new Error(
      'Cloudinary credentials are not set and no --manifest was given, so there is nothing to build a gallery from.',
    );
  }

  const title = manifest
    ? `${manifest.client} — ${manifest.campaign}`
    : folder.split('/').slice(-2).join(' — ');
  const html = renderGallery(assets, {
    title,
    folder,
    subtitle: manifest ? `Request ${manifest.requestId}` : undefined,
  });

  const file =
    outFile ?? path.join(ROOT, 'out', 'reports', `gallery_${folder.split('/').pop()}.html`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, html);

  console.log(sourceNote);
  console.log(`Gallery: ${file}`);
}

main().catch((e) => {
  console.error(e.message ?? e);
  process.exit(1);
});
