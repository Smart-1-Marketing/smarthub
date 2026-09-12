/**
 * Marketing expense file → text → AI category breakdown.
 *
 * Accepts a P&L export, ledger, invoice, or statement as PDF, XLSX/XLS, CSV,
 * TXT, or an image, pulls the text out, and asks the model to sort the line
 * items into the audit's ten spend categories. Anything it can't place with
 * confidence goes to "Other" rather than being guessed into a bucket.
 */

const XLSX = require('xlsx');

const SPEND_CATEGORIES = [
  'Google Advertising', 'Social Media Advertising', 'SEO', 'Streaming TV',
  'Display / Programmatic Ads', 'Retargeting', 'Linear TV Advertising',
  'Radio Advertising', 'Billboard Advertising', 'Direct Mail',
  'Website Expenses', 'Marketing Software', 'Agency Fees', 'Email Marketing',
  'In-house Marketing Staff', 'Live Events / Sponsorships',
];

const MAX_CHARS = 24000;

const EXT = (name = '') => (name.toLowerCase().match(/\.([a-z0-9]+)$/) || [, ''])[1];

const IMAGE_EXT = ['png', 'jpg', 'jpeg', 'webp', 'gif'];
const ACCEPTED = ['pdf', 'xlsx', 'xls', 'csv', 'txt', ...IMAGE_EXT];

const isAccepted = (name) => ACCEPTED.includes(EXT(name));

/* ---------------------------------------------------------------
   Text extraction
---------------------------------------------------------------- */
async function extractText(buffer, filename) {
  const ext = EXT(filename);

  if (ext === 'pdf') {
    const { PDFParse } = require('pdf-parse');
    const parser = new PDFParse({ data: new Uint8Array(buffer) });
    try {
      const { text } = await parser.getText();
      return (text || '').slice(0, MAX_CHARS);
    } finally {
      await parser.destroy().catch(() => {});
    }
  }

  if (ext === 'xlsx' || ext === 'xls' || ext === 'csv') {
    const wb = XLSX.read(buffer, { type: 'buffer' });
    const sheets = wb.SheetNames.map((n) => `--- sheet: ${n} ---\n${XLSX.utils.sheet_to_csv(wb.Sheets[n])}`);
    return sheets.join('\n\n').slice(0, MAX_CHARS);
  }

  if (ext === 'txt') return buffer.toString('utf8').slice(0, MAX_CHARS);

  if (IMAGE_EXT.includes(ext)) return null; // handled as vision input

  throw new Error(`Unsupported file type: .${ext}`);
}

/* ---------------------------------------------------------------
   Prompt
---------------------------------------------------------------- */
const SYSTEM = `You are a marketing finance analyst at Smart 1 Marketing reading a client's expense records.

Your job is to sort marketing line items into a fixed set of monthly spend categories so an accounting partner can drop the numbers straight into an audit form.

The categories, exactly:
${SPEND_CATEGORIES.map((c) => `- ${c}`).join('\n')}
- Other

Rules:
- Report MONTHLY figures. If the document covers a year, quarter, or several months, divide to get a monthly average and say so in "period".
- Only include marketing costs. Ignore rent, payroll unrelated to marketing, COGS, insurance, utilities, and other non-marketing lines.
- If a line item does not clearly belong to a named category, put it in "Other". Never force a guess into a specific category. It is correct and expected for "Other" to be large.
- Every number you report must trace to a line in the document. Do not estimate, extrapolate, or invent amounts.
- If the document has no usable marketing expense data, return empty categories and explain why in "notes".
- Round to whole dollars.

Return ONLY valid JSON:
{
  "period": "what the document covers and how you converted it to monthly",
  "currency": "USD or what you found",
  "categories": [{"name": "exact category name from the list above", "monthlyAmount": 0, "confidence": "high|medium|low", "sourceLines": "the line items that make up this figure"}],
  "totalMonthly": 0,
  "unclassified": [{"label": "line item text", "monthlyAmount": 0, "why": "why it could not be placed"}],
  "notes": "anything the partner should check or that you could not read",
  "questions": ["a question the partner should ask the client about these numbers"]
}
Include only categories with an amount above zero. 0-4 questions.`;

function buildUserPrompt(text, context = {}) {
  return `CLIENT CONTEXT
Business: ${context.clientName || 'not provided'}
Industry: ${context.industry || 'not provided'}
Annual revenue: ${context.annualRevenue ? '$' + Number(context.annualRevenue).toLocaleString('en-US') : 'not provided'}
Known vendors: ${context.digitalVendors || 'not provided'}
Buys through: ${context.buyingModel || 'not provided'}

EXPENSE DOCUMENT
${text}

Sort these into monthly marketing spend categories.`;
}

/* ---------------------------------------------------------------
   Analysis
---------------------------------------------------------------- */
async function analyzeExpenses({ buffer, filename, context = {}, apiKey, model = 'gpt-4o-mini' }) {
  if (!apiKey) throw new Error('OpenAI is not configured');
  if (!isAccepted(filename)) throw new Error(`Unsupported file type: .${EXT(filename)}`);

  const text = await extractText(buffer, filename);

  let messages;
  if (text === null) {
    // Image: hand the picture to the model directly
    messages = [
      { role: 'system', content: SYSTEM },
      {
        role: 'user',
        content: [
          { type: 'text', text: buildUserPrompt('(see attached image)', context) },
          {
            type: 'image_url',
            image_url: { url: `data:image/${EXT(filename) === 'jpg' ? 'jpeg' : EXT(filename)};base64,${buffer.toString('base64')}` },
          },
        ],
      },
    ];
  } else {
    if (!text.trim()) {
      throw new Error('No readable text found in that file. If it is a scan, upload it as an image instead.');
    }
    messages = [
      { role: 'system', content: SYSTEM },
      { role: 'user', content: buildUserPrompt(text, context) },
    ];
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 90000);
  try {
    const r = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
      body: JSON.stringify({
        model,
        temperature: 0.1,
        max_tokens: 2000,
        response_format: { type: 'json_object' },
        messages,
      }),
    });
    if (!r.ok) {
      const body = await r.text();
      throw new Error(`OpenAI ${r.status}: ${body.slice(0, 200)}`);
    }
    const data = await r.json();
    const parsed = JSON.parse(data.choices?.[0]?.message?.content || '{}');
    return normalize(parsed);
  } finally {
    clearTimeout(timer);
  }
}

/** Force the model's output onto the exact category names the form uses. */
function normalize(result = {}) {
  const valid = new Set([...SPEND_CATEGORIES, 'Other']);
  const categories = [];
  let other = 0;

  for (const c of result.categories || []) {
    const amount = Math.round(Number(c.monthlyAmount) || 0);
    if (amount <= 0) continue;
    if (valid.has(c.name)) {
      if (c.name === 'Other') { other += amount; continue; }
      categories.push({
        name: c.name,
        monthlyAmount: amount,
        confidence: ['high', 'medium', 'low'].includes(c.confidence) ? c.confidence : 'medium',
        sourceLines: c.sourceLines || '',
      });
    } else {
      // Unrecognized label: never guess a bucket, roll it into Other
      other += amount;
    }
  }

  for (const u of result.unclassified || []) {
    other += Math.round(Number(u.monthlyAmount) || 0);
  }

  if (other > 0) {
    categories.push({ name: 'Other', monthlyAmount: other, confidence: 'low', sourceLines: 'Items that could not be placed in a named category' });
  }

  const totalMonthly = categories.reduce((sum, c) => sum + c.monthlyAmount, 0);

  return {
    period: result.period || 'not stated',
    currency: result.currency || 'USD',
    categories,
    totalMonthly,
    unclassified: result.unclassified || [],
    notes: result.notes || '',
    questions: (result.questions || []).slice(0, 4),
  };
}

module.exports = { analyzeExpenses, extractText, normalize, isAccepted, ACCEPTED, SPEND_CATEGORIES };
