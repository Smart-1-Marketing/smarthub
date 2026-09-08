import type { Brand, CreativeConcept, QaFinding, SizeKey } from './types';
import { getTemplate } from './registry';

/** Intake placeholders are useful drafts but never finished artwork. Inspect
 * only images this placement uses, so switching to a text layout resolves it. */
export function placeholderFindings(brand: Brand, concept: CreativeConcept, size: SizeKey): QaFinding[] {
  const layout = getTemplate(concept.layoutFamily).sizes[size];
  const copy: any = { ...concept.copy.default, ...concept.copy[size] };
  const refs = [copy.__logoFile || brand.logos.primary, concept.backgroundImage,
    !(concept as any).hideHero && !concept.backgroundImage && layout?.hero &&
      (concept.hero?.[layout.hero.orientation] ?? concept.hero?.landscape ?? concept.hero?.square ?? concept.hero?.vertical)].filter(Boolean);
  return refs.some(ref => /(?:^|[\\/])placeholder(?:[.\-\\/]|$)/i.test(String(ref)))
    ? [{ check: 'placeholder-artwork', status: 'fail', detail: 'Replace the placeholder image before approving or delivering this size.' }]
    : [];
}
