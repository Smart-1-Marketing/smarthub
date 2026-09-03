/**
 * Gallery CLI — search Cloudinary for a project folder and build a gallery.
 *
 *   npx tsx src/gallery.ts --find icon                  list matching projects
 *   npx tsx src/gallery.ts --folder smart1-ads/icon-solar/summer-solar
 *   npx tsx src/gallery.ts --client "Icon Solar" --campaign "Summer Solar"
 *   npx tsx src/gallery.ts --folder ... --manifest out/reports/manifest_AD-2026-002138.json
 *   npx tsx src/gallery.ts --folder ... --out /somewhere/else/gallery.html
 *
 * The gallery is built from a live folder search, not from local state, so it
 * shows what is actually in the library. Passing --manifest lets it fall back
 * to the last render when Cloudinary credentials are not configured, which is
 * what makes the command usable before the account is wired up.
 *
 * Two things this file has to get right, and it got neither.
 *
 * **The folder is one string that three readers derive from.** The search
 * normalises it and the other two took it raw, so a trailing slash -- which
 * is exactly how the README's own folder tree prints it -- found the right
 * assets and then wrote them to `gallery_.html`, silently overwriting every
 * other project's gallery, under a heading that had lost the client's name.
 * `normalizeFolder()` is the one reading now.
 *
 * **A relative path is relative to where the file lands.** A manifest from a
 * dry run carries no hosted URL, so a simulated asset is drawn from a path on
 * this disk -- computed against `out/reports` regardless of where `--out`
 * actually put the page. Every image on the page was broken and the command
 * still printed the file it had written. The output path is decided first now
 * and the assets are built against its directory.
 */

import * as fs from 'fs';
import * as path from 'path';
import { CloudinaryService, normalizeFolder } from './cloudinary';
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

/**
 * Reconstruct gallery assets from a manifest when Cloudinary is unavailable.
 *
 * `pageDir` is the directory the gallery HTML will be written to, and it is a
 * parameter rather than a constant because a simulated entry has no hosted
 * URL: the tile is drawn from a file on this disk, and a relative path is
 * relative to the page that carries it. Computed against a fixed directory it
 * is right only when the page happens to land there.
 */
export function assetsFromManifest(m: Manifest, pageDir: string): UploadedAsset[] {
  return m.entries
    .filter((e) => e.cloudinary)
    .map((e) => {
      const [w, h] = e.deliveredDimensions.split('x').map(Number);
      return {
        publicId: e.cloudinary!.publicId,
        assetFolder: e.cloudinary!.assetFolder,
        // Local path so the page renders before anything is uploaded.
        secureUrl: e.cloudinary!.simulated
          ? path.relative(pageDir, e.localFile)
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

/**
 * The heading on the page: the client and campaign a manifest names, or the
 * last two segments of the folder they were slugged into.
 *
 * Taken off the raw folder, a trailing slash made the last segment empty --
 * so the heading dropped the client and ended in a dash.
 */
export function galleryTitle(folder: string, m?: Manifest): string {
  if (m) return `${m.client} — ${m.campaign}`;
  return normalizeFolder(folder).split('/').slice(-2).join(' — ');
}

/**
 * Where the page is written. An explicit `--out` wins; otherwise it is named
 * for the project, under `out/reports`.
 *
 * Named off the raw folder, a trailing slash left the last segment empty and
 * every project collapsed onto one `gallery_.html`, each run overwriting the
 * last with nothing saying so.
 */
export function galleryFile(folder: string, outFile: string | undefined, root = ROOT): string {
  if (outFile) return path.resolve(outFile);
  const name = normalizeFolder(folder).split('/').pop();
  if (!name) throw new Error(`Cannot name a gallery file for folder "${folder}".`);
  return path.join(root, 'out', 'reports', `gallery_${name}.html`);
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
  if (!folder || !normalizeFolder(folder)) {
    throw new Error(
      'Specify --folder, or --client and --campaign, or --manifest. Use --find <text> to list projects.',
    );
  }
  folder = normalizeFolder(folder);

  // Decided before the assets are built, because a simulated asset's tile is a
  // path relative to this file and cannot be computed without knowing it.
  const file = galleryFile(folder, outFile);

  let assets: UploadedAsset[] = [];
  let sourceNote = '';

  if (cld.live) {
    assets = await cld.searchFolder(folder);
    sourceNote = `${assets.length} asset(s) from Cloudinary search`;
  }

  if (assets.length === 0 && manifest) {
    assets = assetsFromManifest(manifest, path.dirname(file));
    sourceNote = cld.live
      ? `Cloudinary search returned nothing; showing ${assets.length} from the manifest`
      : `Cloudinary not configured; showing ${assets.length} from the manifest`;
  }

  if (!cld.live && !manifest) {
    throw new Error(
      'Cloudinary credentials are not set and no --manifest was given, so there is nothing to build a gallery from.',
    );
  }

  const html = renderGallery(assets, {
    title: galleryTitle(folder, manifest),
    folder,
    subtitle: manifest ? `Request ${manifest.requestId}` : undefined,
  });

  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, html);

  console.log(sourceNote);
  console.log(`Gallery: ${file}`);
}

// Guarded so importing this file for its helpers does not run the command --
// `main()` throws on an empty argv and takes the process down with it.
if (require.main === module) {
  main().catch((e) => {
    console.error(e.message ?? e);
    process.exit(1);
  });
}
