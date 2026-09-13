# Marketing Efficiency Audit™ — accounting-partner lead form, inside the Hub

An interactive marketing audit questionnaire and calculator for accounting and
bookkeeping partners, built for the Accounting Partner Program described at
`smart1marketing.com/accounting-partner-program`. It scores a client's
marketing efficiency, benchmarks spend and cost per lead against industry
ranges, calculates CPL / CAC / close rate / CLV / ROI / growth opportunity,
and uses the OpenAI API to write plain-English findings the partner can bring
to a client meeting.

Results are gated behind a name, firm, email, and phone capture, so the tool
doubles as a lead engine for the Strategic Referral Partnership program.

**This module lives inside Smart 1 Hub now, not as its own Render service.**
It started life as a standalone Express app (still reachable, for the moment,
at the Render URL it was deployed under) and is ported in wholesale — the
questionnaire, the scoring model, the PDF, the scans — because none of that
is a thing the Hub should re-derive. What changed is everything this file's
own README used to describe as "GHL delivery": that is gone, and every lead
this tool captures goes through `hub/leads.py`'s one public route instead.
See `hubLeads.js` and the "Where leads go" section below.

It runs as a second process in the Hub's own container, the same shape
`modules/ad_builder` and `modules/hf_render_service` already use for a
runtime the Python side of this Hub cannot host — proxied at
`/tools/marketing-audit` by `hub/marketing_audit_proxy.py`, which the whole
of this tool is deliberately public through: an accountant or bookkeeper
reading this has no Hub login and never should need one.

## What's inside

| File | Purpose |
|---|---|
| `server.js` | Express server: OpenAI proxy, PDF endpoint, lead capture, rate limiting |
| `hubLeads.js` | Posts a captured lead to the Hub's `/api/leads/capture` — the only place a lead goes |
| `pdf.js` | Branded PDF report generator (PDFKit — no headless browser, small memory footprint) |
| `cloudinary.js` | Signed Cloudinary upload for generated reports (no SDK, no dependencies) |
| `expenses.js` | Expense file text extraction (PDF, Excel, CSV, image) and AI category breakdown |
| `website.js` | Website conversion scan: signal detection plus AI commentary, with SSRF protection |
| `audience.js` | Directional reachable-audience estimate from service-area population |
| `market.js` | AI service-area sizing, competitor discovery, and head-to-head site comparison |
| `public/index.html` | Landing-page frame (nav, hero, footer) plus the six-section questionnaire and report |
| `public/app.js` | Benchmark data, scoring model, all calculations, rendering |
| `public/styles.css` | Smart 1 design system matched to the partner landing page, plus a print stylesheet |
| `public/img/` | Smart 1 logo (nav and footer versions) |
| `public/embed.js` | Loader script for embedding the audit in another site |
| `public/embed-demo.html` | Local test harness for the embed |

The OpenAI key lives only on the server (it is the Hub's own `OPENAI_API_KEY`,
inherited from the container environment — nothing is configured twice). The
browser never sees it.

## The questionnaire

Seven sections, roughly ten minutes:

1. **Client snapshot** — industry, revenue, website URL, ZIP code, primary market, and the partner's name and firm, plus sliders for number of locations (1–20+) and current marketing vendors (0–20+), each with an info circle. The service-area population is estimated from the ZIP rather than asked.
2. **How they buy marketing** — whether digital and traditional spend each clear $2,500 a month, lead services, agency vs. in house (and whether in-house staff get training), traditional channels, digital vendors, in-house marketing headcount and payroll, live events, who owns the website and ad accounts, lead response time, CRM tracking, seasonality, and month-to-month consistency.
3. **Competition and website** — optional Google rating and review count, top services by revenue, then competitors looked up automatically from the website, ZIP, and industry; the partner confirms or dismisses each one and can add their own. Every confirmed competitor with a website is scanned and compared against the client's site in the background. The client's own conversion scan also starts in the background as soon as a URL is entered.
4. **Profit leak warning signs** — eight questions, each answered Yes / No / Unsure. Two of the original ten (monthly spend above $2,500, multiple vendors) were removed because Section 2 already captures them directly. Point values are never shown to the partner; an "unsure" carries the same weight as a "yes", because an unknown is itself a warning sign, and the report distinguishes the two.
5. **Monthly investment** — seventeen spend categories as sliders, each with a category-appropriate ceiling and an info circle explaining what belongs there, plus the optional expense-document upload.
6. **Performance indicators** — marketing-generated leads (referrals and repeat customers explicitly excluded, so cost per lead reflects the marketing), customers from those leads, average sale, purchase frequency, relationship length, plus optional questions on repeat-revenue mix and whether the business could handle more leads. The report models +15% and +25% growth scenarios automatically — unless capacity is "already full", in which case the findings pivot to pricing and efficiency instead of volume.
7. **Target market and context** — B2C or B2B, service radius, age ranges, household income, gender skew, homeowner focus, and free text for leadership changes, lost accounts, or new locations.

Only Section 4 affects the numeric score. Everything else feeds the written findings, the report sections, and the questions the partner brings to the client meeting.

## Scanning the client's website

Section 3 fetches the client's home page and detects conversion and measurement signals directly from the served HTML: forms, click-to-call links, mailto links, booking and scheduling tools, chat widgets, call-to-action language, review mentions, and tracking tags (GA4, GTM, Google Ads conversion, Meta pixel, LinkedIn, Microsoft UET, TikTok, Hotjar/Clarity, call tracking). It also checks HTTPS, mobile viewport, meta description, H1, and schema markup. The model then comments on what it found, ranked by revenue impact.

Two limits are stated in the output rather than hidden: the scan reads only the **initial HTML response**, so a site that renders in the browser will under-report and the result says so; and it never claims to assess design, speed, or copy, because it hasn't seen the rendered page.

**Safety.** Submitted URLs are resolved and checked before fetching. Private ranges, loopback, and link-local addresses are refused, which blocks the standard SSRF path to cloud metadata endpoints. Set `ALLOW_LOCAL_FETCH=1` only for local development.

## Estimating audience size

The partner is never asked for a population figure. The audit sends the ZIP code, city, industry, and service radius to the model, which estimates the reachable service area and returns it with a confidence rating, the basis for the figure, and local demographics where it knows them (median household income, median age, homeownership). If the model is unavailable or returns something implausible, a radius-based fallback applies, so **there is always an audience number**. The report always states which of the two produced it.

That population is then filtered by the target-market answers, with the arithmetic shown line by line:

```
Service-area population                             905,000
Adults aged 35–44, 45–54, 55–64 (38% of population) 323,000
Household income $100k–$200k (26% of households)     83,980
Homeowners only (65% ownership rate)                 54,587
Estimated reachable consumers                        54,587
Working range: 38,211 – 70,963
```

This is deliberately transparent arithmetic, not a data product. It uses approximate national US shares for age, income, household size, homeownership, and business density, and reports a ±30% band. Every figure carries the caveat that it is directional and should be confirmed against census or ad-platform reach data. `audience.js` holds the share tables if you want to substitute local figures.

For B2B clients it estimates establishments instead, at roughly 25 per 1,000 residents. Without a population figure, the report says what it could not assess rather than guessing.



## Partner experience

Three things keep a busy accountant from abandoning a ten-minute form:

- **Save and resume.** Every answer persists in the browser (localStorage) as it's typed. Returning to the page offers "Pick up where you left off" with the client's name; progress clears when the audit completes or on "Start over", and goes stale after 14 days. Contact details never leave the machine until the results gate is submitted; once the client snapshot is complete (or the tab is closed mid-audit), the business fields already entered are sent to the webhook as a `"stage": "partial"` lead so an abandoned audit can still be followed up.
- **A completeness check before the report.** "Calculate results" first lists what's blank and what each blank costs — "Without leads and customers, the report can't compute cost per lead" — with the choice to go back or generate anyway. This is why dashes in the report are always a decision, never a surprise.
- **A sample report on the intro page** (`/sample-report.pdf`, served from `public/`), so the partner sees the payoff before investing the time. Regenerate it whenever the format changes by saving any audit PDF over `public/sample-report.pdf`.

## Calculation policy

Decisions that shape the numbers, so nobody has to reverse-engineer them:

- **ROI is deliberately conservative**: first-month math only — (new customers × average sale − spend) ÷ spend. Repeat purchases are excluded from ROI; the findings may note that true return including lifetime value runs higher, but the printed figure never inflates.
- **Benchmark comparisons use media spend only.** In-house staff and live events count toward total spend and ROI, but are excluded from the % -of-revenue benchmark, because published industry ranges are media-only and including payroll would make every client with a team look like an overspender. The report labels this wherever the comparison appears.
- **Leads means marketing-generated leads.** The form says so, and explains why: counting referrals flatters cost per lead and hides the real number.
- **Savings rates are 20% (digital consolidation) and 25% (traditional/digital overlap)**, always labelled as typical recovery rates rather than quotes.

## The vendor questions section

Every report closes with "Five questions to ask any marketing vendor" — cost per acquired customer by channel, asset ownership, notice period, change log, and budget overlap. These apply to the client's current vendors and to anyone they might hire, which is exactly the point: the questions do the differentiating, and the report never has to.

The webhook payload also carries `lastScreen`, so a GHL workflow can tell where a partner abandoned when a `started` lead never converts to `completed`.

## Modelling savings

Where the numbers support it, the report shows what tightening the program could return:

- **Consolidating digital vendors** — 20% of digital spend, applied when two or more vendors are in play. Covers duplicate tools, overlapping audiences, brand terms bid against the client's own organic listing, and management fees paid twice on the same work.
- **Removing traditional and digital overlap** — 25% of traditional spend, applied when both are running. Media bought separately usually reaches the same people at the same time without either side knowing.

Both are followed by a before-and-after table on monthly spend, cost per lead, acquisition cost, and ROI. **Both columns are computed from the same spend figure** rather than reusing stored metrics, so the comparison can never show savings making a metric worse.

The percentages are labelled as typical recovery rates, never a quote. Edit `savingsModel()` in `public/app.js` to change them, along with `DIGITAL_CATS` and `TRADITIONAL_CATS` which decide what counts as each.

## Uploading marketing expenses

Section 4 accepts a P&L export, ledger, vendor statement, or invoice list — PDF, XLSX, XLS, CSV, TXT, or a photo, up to 15 MB. The partner picks one of two modes:

- **AI evaluation** — the server extracts the text (PDFKit's parser for PDFs, SheetJS for spreadsheets, the model's vision for images), asks the model to sort line items into the ten spend categories, and writes the resulting monthly figures straight into the form fields. The partner reviews and corrects before continuing; the audit always uses what is in the fields, not what was read.
- **Human review** — the file is stored and flagged for a Smart 1 analyst, with no automated interpretation.

Either way the file goes to Cloudinary under `smart1-audits/expense-uploads/`, so there is always a copy to go back to.

Two behaviors worth knowing. **Anything the model cannot place with confidence goes to "Other"** rather than being guessed into a named category — a large Other is expected and correct, and unrecognized category labels are folded in too rather than trusted. And **annual or quarterly documents are divided down to a monthly average**, with the conversion stated in the results panel so the partner can check it. Each line carries a high/medium/low confidence badge and the source line items it came from.

If the file has no readable text — a scan saved as a PDF, say — the partner is told to re-upload it as an image, which routes it through vision instead.

## Scoring model

**Marketing Efficiency Score™ (0–100)**

Warning-sign weights are normalised against `FLAG_MAX`, so adding or removing a question rescales the score automatically rather than silently shifting every tier.

| Component | Points | Basis |
|---|---|---|
| Measurement and visibility | 45 | The 10 warning signs (30 possible points), inverted |
| Spend alignment | 20 | Annualized spend as % of revenue vs. the industry budget range |
| Acquisition efficiency | 15 | Cost per lead vs. the industry CPL range |
| Return | 20 | ROI: ≥300% = 20, ≥150% = 15, ≥50% = 10, ≥0% = 5 |

Tiers: 80+ Strong · 65–79 Monitor · 50–64 Opportunity exists · 35–49 Significant opportunity · under 35 Immediate review recommended.

The raw warning-sign tiers from the Client Profit Leak Assessment™ (0–5 Healthy through 21+ Immediate review) are preserved separately and shown in the findings.

**Formulas** — CPL = spend ÷ leads · CAC = spend ÷ new customers · Close rate = customers ÷ leads × 100 · CLV = average sale × purchases per year × customer years · ROI = ((revenue − cost) ÷ cost) × 100 · Growth opportunity = leads × lift % × close rate × average sale × 12.

**Benchmarks** — 20 industries, each with three or four industry facts shown in a dedicated benchmarks section, a budget-as-%-of-revenue range, a cost-per-lead range where one is published, a typical digital/traditional channel split, and a one-line note on what drives spend in that industry. The report shows the industry midpoint converted to dollars at the client's own revenue, so the gap reads as "$7,179 per month below the midpoint" rather than a percentage. Edit `INDUSTRIES` at the top of `public/app.js` to adjust.

## Run it locally, standalone

The module still boots on its own — useful for iterating on the questionnaire
or the PDF without the rest of the Hub running:

```bash
cd modules/marketing_audit
npm install
OPENAI_API_KEY=sk-... npm start     # http://localhost:3000
```

Without a key the app still runs end to end and produces rules-based findings
instead of AI-written ones. Without `HUB_BASE_URL` set, leads are logged to
the console and not delivered anywhere (see "Where leads go" below) — that is
the correct behaviour for local iteration, not a bug to chase.

## Running inside the Hub

This module does not deploy on its own any more, and does not need its own
`render.yaml`, GitHub repo, or Render service — it ships inside `smarthub` and
starts as a second process in the same container `docker-start.sh` already
manages for `modules/ad_builder` and `modules/hf_render_service`. Nothing
here needs configuring beyond the Hub's own environment:

- `OPENAI_API_KEY` / `OPENAI_MODEL` — inherited from the Hub's own settings;
  nothing is configured twice.
- `CLOUDINARY_CLOUD_NAME` / `CLOUDINARY_API_KEY` / `CLOUDINARY_API_SECRET` —
  derived from the Hub's `CLOUDINARY_URL` by `docker-start.sh`, the same
  bridge the Display Ad Builder already relies on. Reports go to Cloudinary
  under `smart1-audits/` and fall back to local disk when it isn't set.
- `HUB_BASE_URL` / `HUB_LEADS_SOURCE_TOKEN` — set by `docker-start.sh` so
  this process can reach the Hub's own `/api/leads/capture` over loopback.
  See "Where leads go" below.
- `MARKETING_AUDIT_PORT` — which loopback port this process binds; the Hub's
  proxy (`hub/marketing_audit_proxy.py`) reads the same variable. Defaults to
  `8793`.
- `BOOKING_URL` — where the "Schedule a review" button goes; defaults to the
  Smart 1 contact page. Set it in Render if the partner program has its own
  scheduling link.
- `ALLOW_LOCAL_FETCH` — development only; lets the website scanner reach
  private addresses.

Health at `/tools/marketing-audit/api/health` reports whether Cloudinary and
the Hub delivery path are configured.

## Embedding on smart1marketing.com

The accounting-partner-program page frames the real tool rather than a copy
of the form — the same rule `hub/embed.py` and `docs/smart1marketing-embeds.md`
already state for the nine industry gameplans: a pasted copy needs a host
spelled correctly and goes stale the day a field changes here, and a frame
needs neither.

```html
<iframe src="https://smart1.agency/tools/marketing-audit/?embed=1"
        title="Marketing Efficiency Audit"
        style="display:block;width:100%;height:1400px;border:0"
        loading="lazy"></iframe>
```

Or, for the auto-resizing loader (no inner scrollbar, no fixed height to
maintain):

```html
<div id="smart1-audit"></div>
<script src="https://smart1.agency/tools/marketing-audit/embed.js" data-target="#smart1-audit"></script>
```

In embed mode the audit hides its own nav, credibility strip, and footer,
since the host page supplies those. Optional attributes on the script tag:

| Attribute | Effect |
|---|---|
| `data-target` | CSS selector for the container. Defaults to the script tag's parent element. |
| `data-title` | iframe title announced by screen readers. |
| `data-scroll="off"` | Stops the host page from scrolling to the audit when the step changes. |

**`EMBED_ALLOWED_ORIGINS`** restricts who may iframe the tool at all — set on
this module's process (not the Hub's own) to
`https://smart1marketing.com https://www.smart1marketing.com`. Include both
the apex and `www` versions, or the embed breaks on whichever one is left
out.

**Testing locally**: run `npm start` inside `modules/marketing_audit` and open
`http://localhost:3000/embed-demo.html`, which loads the audit inside a
stand-in host page.

## The PDF report

When the visitor unlocks their results, the browser posts the full audit to `/api/report`. The server renders a four-page branded PDF, stores it (Cloudinary, or `PDF_DIR` as a fallback), returns a download link, and delivers the lead to the Hub with that link attached — see "Where leads go" below. Download buttons appear at the top of the report and in the closing call-to-action.

What's in it:

1. **Page 1** — client snapshot, partner attribution line, Marketing Efficiency Score™ gauge, and both benchmark bars.
2. **Page 2** — the six calculated metrics, the growth-opportunity figure, and what the industry typically spends including the channel-mix bar.
3. **Page 3 onward** — industry benchmarks and facts, the audience estimate with its arithmetic, the website conversion review, competitive position, how the client buys marketing, target market and business context, the written findings, the vendor-consolidation case, where money may be leaking, questions to ask, next steps, warning-sign detail, and the call-to-action.

Reports run four to seven pages depending on how much the partner supplied. Sections with no data are omitted rather than printed empty.

Rendering is vector PDFKit rather than a headless browser, so it runs in well under 100 MB of memory and adds roughly 200 ms per report. No Chromium, no Puppeteer buildpack.

### Partner attribution

Section 1 asks for **Prepared by (partner name)** and **Partner firm**. Both print on the PDF header line, ride along in the lead payload the Hub receives as `partner_name` and `partner_firm`, and prefill the gate form so the partner never types their name twice.

Give each firm its own link and the fields fill themselves:

```
https://smart1.agency/tools/marketing-audit/?partner=Jane%20Doe%2C%20CPA&firm=Doe%20CPA%20Group
```

### Where PDFs are stored

Reports go to **Cloudinary** when it's configured, and fall back to local disk when it isn't — or when an upload fails, which is logged and never blocks the visitor's download.

**Cloudinary.** `docker-start.sh` derives `CLOUDINARY_CLOUD_NAME` / `CLOUDINARY_API_KEY` / `CLOUDINARY_API_SECRET` from the Hub's own `CLOUDINARY_URL` before either Node process starts, so nothing here needs configuring separately. Set `CLOUDINARY_FOLDER` if `smart1-audits` should be something else.

Reports upload as `raw` resources to `smart1-audits/`, and the returned `secure_url` is what the visitor downloads and what the Hub receives as `pdf_url`. Two reasons for `raw` rather than `image`: PDFs uploaded as images are blocked from delivery on many Cloudinary accounts until "Allow delivery of PDF and ZIP files" is enabled in Settings → Security, and raw storage never re-encodes the file.

This makes report links permanent and survives restarts and redeploys. Every report is roughly 45 KB, so a thousand audits is about 45 MB against the shared Cloudinary quota.

The upload is signed server-side with the API secret using Cloudinary's standard SHA-1 scheme. The secret never reaches the browser, and no Cloudinary SDK is installed.

**Local disk (fallback).** Without Cloudinary, reports are written to `PDF_DIR` (default: the system temp directory) and swept after `PDF_TTL_HOURS`, default 30 days — and the container's own disk is wiped on every redeploy, so a link stored only there does not survive one. Set `PUBLIC_BASE_URL` to the Hub's real domain (already set for the rest of the Hub) so a locally served link doesn't expose an internal host.

Check which mode is live at `/tools/marketing-audit/api/health` — look for `"pdfStorage": "cloudinary"` or `"local-disk"`. The `/api/report` response also reports `"storage"` per request, which tells you whether a specific upload fell back.

## Where leads go

Every captured lead goes to exactly one place: `hub/leads.py`'s
`POST /api/leads/capture`, over loopback, through `hubLeads.js`. That module
holds Smart 1 Hub's whole policy on this — one store, one delivery path, a
lead that survives a Suite outage — and this tool does not get a second
opinion on any of it. There is no GoHighLevel client here any more, no
`LEAD_WEBHOOK_URL`, and no separate "partial lead" signal: the Hub's route
refuses a submission with neither an email nor a phone, on purpose (a
contactless row would read as a live prospect on every report that counts
leads), so the one delivery this tool makes is a single call from
`/api/report`, once the visitor has unlocked their results and the PDF
exists. See the block comment above `deliverLead()` in `server.js`.

```js
// what /api/report sends, via hubLeads.captureLead()
{
  source: "marketing_audit",
  page: "accounting-partner-audit",
  fields: {
    name, email, phone, company,
    partner_name, partner_firm,
    client_business, client_industry, client_annual_revenue,
    website, zip_code, city_market, locations,
    monthly_marketing_spend, efficiency_score, score_tier,
    leak_points, leak_tier,
  },
  pdf_url: "https://res.cloudinary.com/.../marketing-efficiency-audit-acme-....pdf",
  client: "Acme Plumbing & Drain",   // the business the audit is about
  meta: { lead_id, last_screen },
}
```

`client` is deliberately the **audited business**, not the partner submitting
the form — the same distinction `hub/leads.py` draws everywhere else, because
a lead filed under the wrong name attributes one company's enquiry to
another.

Two environment variables control delivery, both set by `docker-start.sh`
when this runs inside the Hub's container:

- `HUB_BASE_URL` — where the Hub itself answers, e.g. `http://127.0.0.1:8000`
  in-container. Unset, `hubLeads.captureLead()` returns an error rather than
  silently dropping the lead, and the failure is logged.
- `HUB_LEADS_SOURCE_TOKEN` — the same shared secret the five standalone
  landing apps already send as `X-S1-Lead-Token` (`hub/leads.py`'s
  `LEADS_SOURCE_TOKEN`). This tool posts from its own server process, so
  every visitor's lead would otherwise share one address and trip the Hub's
  per-visitor rate limit within the hour; a trusted caller skips the limit
  and nothing else — the lead is still stored, tagged, and delivered down the
  identical path, with the row recording that it arrived from a trusted
  source.

What reaches the visitor is unchanged: the Cloudinary link, returned in the
`/api/report` response, regardless of whether Hub delivery succeeds. A Hub
outage costs the lead delivery, never the partner's download.

## Cost

At `gpt-4o-mini`, each completed audit is roughly 2,600 input and 900 output tokens, and an expense-document evaluation adds roughly 6,000 input and 800 output tokens — well under a cent per audit. PDF generation is free; each report is about 45 KB, stored on Cloudinary or disk. Rate limiting is set to 15 analyses per IP per hour in `server.js`.

## Customizing

- **The "Schedule a review" button** — set `BOOKING_URL` in Render rather than editing code. It drives both the on-screen button and the clickable button in the PDF. The wording lives in the `.cta-band` block of `index.html` and section 9 of `pdf.js`.
- **Tone and structure of the findings** — `SYSTEM_PROMPT` in `server.js`.
- **Questions and weights** — `FLAGS` in `public/app.js`. Weights are internal only and never displayed; keep the total at 30 or adjust the 45-point divisor in `calculate()`.
- **Spend categories, slider ceilings, and the info-circle text** — `SPEND_ITEMS` at the top of `public/app.js`. If you change a category name, change the matching entry in `SPEND_CATEGORIES` in `expenses.js` too, or the AI expense reader will drop that category into Other.
- **Growth scenarios** — the `LIFTS` array in `calculate()`, currently `[15, 25]`.
- **Traditional media and age-range options** — `TRADITIONAL_MEDIA` and `AGE_RANGES` in `public/app.js`.
- **Expense categorization rules** — the `SYSTEM` prompt in `expenses.js`. The "unknown goes to Other" rule lives there and in `normalize()`.
- **The thinking spinner's stages** — `THINK_STEPS` in `public/app.js`. It holds for a minimum of 2.4 seconds so it never flashes.
- **Partner attribution** — read a `?partner=firm-name` query parameter in `app.js` and include it in the `/api/lead` payload to track which firm sent each audit.
