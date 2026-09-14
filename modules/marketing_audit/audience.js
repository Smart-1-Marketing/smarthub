/**
 * Directional reachable-audience estimate.
 *
 * This is deliberately conservative arithmetic, not a data product. It takes a
 * service-area population (supplied by the partner, or estimated by the model
 * from the named market) and filters it by the target-market answers using
 * approximate national US shares. Every figure is returned with the assumptions
 * that produced it so the partner can argue with the math rather than trust it.
 *
 * Nothing here should be presented as a market research figure. The report
 * labels it an estimate and shows the working.
 */

/* Approximate share of total US population by age band (ACS-scale rounding). */
const AGE_SHARE = {
  '18–24': 0.09,
  '25–34': 0.14,
  '35–44': 0.13,
  '45–54': 0.12,
  '55–64': 0.13,
  '65+': 0.17,
};

/* Approximate share of US households by income band. */
const INCOME_SHARE = {
  'Under $50k': 0.36,
  '$50k–$100k': 0.28,
  '$100k–$200k': 0.26,
  'Over $200k': 0.10,
  'Mixed / not targeted': 1.0,
};

const AVG_HOUSEHOLD_SIZE = 2.5;
const HOMEOWNERSHIP = 0.65;
/* Roughly 25 employer establishments per 1,000 people nationally. */
const BUSINESSES_PER_1000 = 25;

const GENDER_SHARE = { 'Mostly women': 0.51, 'Mostly men': 0.49, 'No meaningful skew': 1.0 };

/**
 * @param {object} input
 * @param {number} input.population   service-area population
 * @param {string} input.audienceType 'Consumers (B2C)' | 'Businesses (B2B)' | 'Both'
 * @param {string[]} input.ageRanges
 * @param {string} input.incomeBand
 * @param {string} input.genderSkew
 * @param {boolean} input.homeownersOnly
 * @returns {object|null}
 */
function estimateAudience(input = {}) {
  const population = Number(input.population);
  if (!population || !isFinite(population) || population <= 0) return null;

  const steps = [{ label: 'Service-area population', value: Math.round(population) }];
  let households = population / AVG_HOUSEHOLD_SIZE;

  const b2b = /B2B/i.test(input.audienceType || '');
  const both = /both/i.test(input.audienceType || '');

  let consumerReach = null;
  let businessReach = null;

  if (!b2b || both) {
    let people = population;

    const ages = (input.ageRanges || []).filter((a) => AGE_SHARE[a]);
    if (ages.length && ages.length < Object.keys(AGE_SHARE).length) {
      const share = ages.reduce((sum, a) => sum + AGE_SHARE[a], 0);
      people *= share;
      steps.push({ label: `Adults aged ${ages.join(', ')}`, value: Math.round(people), note: `${Math.round(share * 100)}% of population` });
    }

    const gShare = GENDER_SHARE[input.genderSkew] ?? 1;
    if (gShare < 1) {
      people *= gShare;
      steps.push({ label: input.genderSkew, value: Math.round(people), note: `${Math.round(gShare * 100)}% of the above` });
    }

    const iShare = INCOME_SHARE[input.incomeBand] ?? 1;
    if (iShare < 1) {
      people *= iShare;
      steps.push({ label: `Household income ${input.incomeBand}`, value: Math.round(people), note: `${Math.round(iShare * 100)}% of households` });
    }

    if (input.homeownersOnly) {
      people *= HOMEOWNERSHIP;
      steps.push({ label: 'Homeowners only', value: Math.round(people), note: `${Math.round(HOMEOWNERSHIP * 100)}% ownership rate` });
    }

    consumerReach = Math.round(people);
    steps.push({ label: 'Estimated reachable consumers', value: consumerReach, emphasis: true });
    steps.push({ label: 'Estimated reachable households', value: Math.round(consumerReach / AVG_HOUSEHOLD_SIZE) });
  }

  if (b2b || both) {
    businessReach = Math.round((population / 1000) * BUSINESSES_PER_1000);
    steps.push({ label: 'Estimated businesses in the area', value: businessReach, emphasis: true, note: `~${BUSINESSES_PER_1000} establishments per 1,000 people` });
  }

  const primary = b2b && !both ? businessReach : consumerReach;

  return {
    population: Math.round(population),
    households: Math.round(households),
    consumerReach,
    businessReach,
    primary,
    /* ±30% band, because every input here is an approximation */
    low: primary != null ? Math.round(primary * 0.7) : null,
    high: primary != null ? Math.round(primary * 1.3) : null,
    steps,
    assumptions: [
      'National average household size of 2.5 people',
      'Age and income shares are approximate US national distributions, not local figures',
      input.homeownersOnly ? 'National homeownership rate of about 65%' : null,
      (b2b || both) ? 'About 25 employer establishments per 1,000 residents' : null,
      'Range shown is ±30% to reflect the approximations above',
    ].filter(Boolean),
    caveat: 'A directional estimate built from national averages and the answers supplied. Confirm against local census or platform reach data before setting a budget.',
  };
}

module.exports = { estimateAudience, AGE_SHARE, INCOME_SHARE };
