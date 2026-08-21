/**
 * The manifest is the record of what was produced, where it lives in
 * Cloudinary, and whether it passed QA. It is the input to the image report
 * and the payload the HighLevel custom object should reference.
 */

import type { Campaign, RenderResult, SizeKey } from './types';
import type { UploadedAsset } from './cloudinary';
import { slug } from './cloudinary';

export interface ManifestEntry {
  conceptId: string;
  conceptName: string;
  layoutFamily: string;
  platform: string;
  /** All platforms this identical creative satisfies (dedup). */
  platforms?: string[];
  size: SizeKey;
  deliveredDimensions: string;
  format: string;
  bytes: number;
  wordCount: number;
  qaStatus: 'pass' | 'warn' | 'fail';
  qaIssues: string[];
  localFile: string;
  /** Absent when the creative failed QA and was withheld from upload. */
  cloudinary?: {
    publicId: string;
    assetFolder: string;
    secureUrl: string;
    version: number;
    simulated?: boolean;
  };
}

export interface Manifest {
  requestId: string;
  client: string;
  campaign: string;
  projectFolder: string;
  generatedAt: string;
  uploaded: boolean;
  dryRun: boolean;
  totals: {
    creatives: number;
    uploadedCount: number;
    pass: number;
    warn: number;
    fail: number;
    bytes: number;
  };
  entries: ManifestEntry[];
}

/** Tags let the gallery find these assets again without knowing the folder. */
export function tagsFor(
  campaign: Campaign,
  result: RenderResult,
  conceptName: string,
): string[] {
  return [
    'smart1-ads',
    `client:${slug(campaign.brand.name)}`,
    `campaign:${slug(campaign.campaignName)}`,
    `request:${slug(campaign.requestId)}`,
    `concept:${result.conceptId}`,
    `concept-name:${slug(conceptName)}`,
    `platform:${result.platform}`,
    `size:${result.size}`,
    `qa:${result.status}`,
  ];
}

/** Context metadata is searchable and shows in the Media Library. */
export function contextFor(
  campaign: Campaign,
  result: RenderResult,
  headline: string,
): Record<string, string | number> {
  return {
    alt: `${campaign.brand.name} ${result.size} display ad`,
    caption: headline,
    request_id: campaign.requestId,
    campaign: campaign.campaignName,
    concept: result.conceptId,
    platform: result.platform,
    size: result.size,
    delivered: `${result.width}x${result.height}`,
    words: result.wordCount,
    qa_status: result.status,
  };
}

export function buildManifest(args: {
  campaign: Campaign;
  projectFolder: string;
  results: RenderResult[];
  uploads: Map<string, UploadedAsset>;
  dryRun: boolean;
  uploaded: boolean;
}): Manifest {
  const { campaign, projectFolder, results, uploads, dryRun, uploaded } = args;

  const entries: ManifestEntry[] = results.map((r) => {
    const concept = campaign.concepts.find((c) => c.conceptId === r.conceptId);
    const asset = uploads.get(r.file);
    return {
      conceptId: r.conceptId,
      conceptName: concept?.name ?? r.conceptId,
      layoutFamily: concept?.layoutFamily ?? '',
      platform: r.platform,
      platforms: r.platforms ?? [r.platform],
      size: r.size,
      deliveredDimensions: `${r.width}x${r.height}`,
      format: r.format,
      bytes: r.bytes,
      wordCount: r.wordCount,
      qaStatus: r.status,
      qaIssues: r.qa
        .filter((f) => f.status !== 'pass')
        .map((f) => `${f.check}: ${f.detail}`),
      localFile: r.file,
      ...(asset
        ? {
            cloudinary: {
              publicId: asset.publicId,
              assetFolder: asset.assetFolder,
              secureUrl: asset.secureUrl,
              version: asset.version,
              ...(asset.simulated ? { simulated: true } : {}),
            },
          }
        : {}),
    };
  });

  return {
    requestId: campaign.requestId,
    client: campaign.brand.name,
    campaign: campaign.campaignName,
    projectFolder,
    generatedAt: new Date().toISOString(),
    uploaded,
    dryRun,
    totals: {
      creatives: entries.length,
      uploadedCount: entries.filter((e) => e.cloudinary).length,
      pass: entries.filter((e) => e.qaStatus === 'pass').length,
      warn: entries.filter((e) => e.qaStatus === 'warn').length,
      fail: entries.filter((e) => e.qaStatus === 'fail').length,
      bytes: entries.reduce((n, e) => n + e.bytes, 0),
    },
    entries,
  };
}
