/**
 * Delivers a captured lead to the Hub, over hub/leads.py's one route.
 *
 * The standalone version of this tool wrote a GoHighLevel contact itself
 * (upsert, attach the PDF to a custom field, add a note) and also fired a
 * generic webhook. Both are gone. Every lead in Smart 1 Hub goes through
 * `POST /api/leads/capture` now -- that is the route hub/leads.py exists to
 * be the only one of, and it is what puts a lead on the Leads panel and (once
 * hub/ghl_contacts.py can reach it) creates the Smart 1 Suite contact.
 * Posting to GoHighLevel a second time from here would be the exact failure
 * that route was written to end: two places writing the same contact, with
 * no way to tell later which one a given lead actually came from.
 *
 * Env:
 *   HUB_BASE_URL          e.g. http://127.0.0.1:8000 in-container, or the
 *                          Hub's real origin if this ever runs as its own
 *                          Render service. Required -- with nothing set, a
 *                          captured lead has nowhere to go and this module
 *                          says so rather than pretending to have delivered.
 *   HUB_LEADS_SOURCE_TOKEN  shared secret, echoed to hub/leads.py's
 *                          X-S1-Lead-Token header. This tool posts leads from
 *                          its own server, not a browser, so every submission
 *                          would otherwise share one IP and trip the Hub's
 *                          per-visitor rate limit within the hour -- the same
 *                          shape as the five standalone landing apps that
 *                          already carry LEADS_SOURCE_TOKEN.
 */

const HUB_BASE_URL = (process.env.HUB_BASE_URL || '').replace(/\/$/, '');
const SOURCE_TOKEN = (process.env.HUB_LEADS_SOURCE_TOKEN || process.env.LEADS_SOURCE_TOKEN || '').trim();
const SOURCE_TOKEN_HEADER = 'X-S1-Lead-Token';

const isConfigured = () => Boolean(HUB_BASE_URL);

/**
 * @param {object} args
 * @param {string} args.page      which screen/step this came from, for the panel
 * @param {object} args.fields    must carry an email or a phone -- hub/leads.py
 *                                refuses a lead with neither, on purpose: a
 *                                contactless row reads as a live prospect on
 *                                every report that counts leads.
 * @param {string} [args.pdfUrl]
 * @param {string} [args.client]  the business the audit is about, not the
 *                                partner submitting it
 * @param {object} [args.meta]
 */
async function captureLead({ page, fields, pdfUrl, client, meta } = {}) {
  if (!isConfigured()) {
    return { ok: false, error: 'HUB_BASE_URL is not set; the lead was not delivered.' };
  }
  const headers = { 'Content-Type': 'application/json' };
  if (SOURCE_TOKEN) headers[SOURCE_TOKEN_HEADER] = SOURCE_TOKEN;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const r = await fetch(`${HUB_BASE_URL}/api/leads/capture`, {
      method: 'POST',
      signal: controller.signal,
      headers,
      body: JSON.stringify({
        source: 'marketing_audit',
        page: page || 'accounting-partner-audit',
        fields,
        pdf_url: pdfUrl || '',
        client: client || '',
        meta: meta || {},
      }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok || data.ok === false) {
      throw new Error(data.error || `Hub returned ${r.status}`);
    }
    return data;
  } finally {
    clearTimeout(timer);
  }
}

module.exports = { captureLead, isConfigured };
