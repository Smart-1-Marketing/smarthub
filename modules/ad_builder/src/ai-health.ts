import { generateCopy } from './copywriter';
import type { Check } from './diagnostics';

export async function authenticatedAiCheck(fetchImpl: typeof fetch = fetch): Promise<Check> {
  const base = { id: 'auth.openai', group: 'Integrations', label: 'OpenAI authentication' };
  const key = process.env.OPENAI_API_KEY;
  if (!key) return { ...base, level: 'skip', detail: 'No key configured; authenticated access was not tested.' };
  try {
    const r = await fetchImpl('https://api.openai.com/v1/models', { headers: { authorization: `Bearer ${key}` }, signal: AbortSignal.timeout(10_000) });
    return r.ok ? { ...base, level: 'ok', detail: 'Authenticated models request succeeded. Use the copy test below to verify generation separately.' }
      : { ...base, level: 'fail', detail: `Authenticated request returned HTTP ${r.status}.`, fix: 'Check the OpenAI key and project permissions. Network reachability alone does not validate credentials.' };
  } catch { return { ...base, level: 'fail', detail: 'Authenticated request timed out or could not connect.' }; }
}
export async function copySmokeTest() {
  const brief = { business: 'Example Studio', promoting: 'Custom design consultations', benefit: 'Discuss your next project', cta: 'Learn more' };
  const result = await generateCopy(brief, { ...brief, website: 'https://example.test', campaignName: 'Diagnostic only' }, { sizes: ['300x250','320x50'], timeoutMs: 45_000 });
  return { ok: result.source === 'openai', source: result.source, warnings: result.warnings,
    concepts: result.concepts, testedAt: new Date().toISOString(), note: 'Uses synthetic data. No campaign was changed. Generation does not establish visual QA or claim accuracy.' };
}
