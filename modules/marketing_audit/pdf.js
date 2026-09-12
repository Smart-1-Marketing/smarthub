/**
 * Smart 1 Marketing — Marketing Efficiency Audit™
 * Branded PDF report generator (PDFKit, no headless browser required).
 *
 * buildAuditPdf(payload) -> Promise<Buffer>
 */

const PDFDocument = require('pdfkit');
const path = require('path');
const fs = require('fs');

const C = {
  navy: '#0A2240', navy2: '#16395F', sky: '#009ED2', green: '#01cdb0', green2: '#01b8a0',
  gold: '#fbbc00', ink: '#1A2233', muted: '#5A6577', line: '#E4E8EF', bg: '#F6F8FB',
  red: '#e0574d', amber: '#f0a02a', white: '#ffffff', lightText: '#D6E0EE',
};

const PAGE = { w: 612, h: 792, m: 46 };
const CONTENT_W = PAGE.w - PAGE.m * 2;
const FOOT_Y = PAGE.h - 58;
const LOGO = path.join(__dirname, 'public', 'img', 'logo-white.png');

const money = (n, dp = 0) =>
  (n === null || n === undefined || !isFinite(n)) ? '—'
    : '$' + Number(n).toLocaleString('en-US', { minimumFractionDigits: dp, maximumFractionDigits: dp });

const pct = (n, dp = 1) => (n === null || n === undefined || !isFinite(n)) ? '—' : n.toFixed(dp) + '%';

function buildAuditPdf(payload = {}) {
  return new Promise((resolve, reject) => {
    try {
      const s = payload.snapshot || {};
      const m = payload.metrics || {};
      const b = payload.benchmark || {};
      const a = payload.analysis || {};
      const pr = payload.profile || {};
      const mk = payload.market || {};
      const cp = payload.competition || {};
      const wb = payload.website || null;
      const aud = payload.audience || null;
      const lead = payload.lead || {};

      const doc = new PDFDocument({ size: 'LETTER', margin: PAGE.m, bufferPages: true, autoFirstPage: false });
      const chunks = [];
      doc.on('data', (c) => chunks.push(c));
      doc.on('end', () => resolve(Buffer.concat(chunks)));
      doc.on('error', reject);

      doc.info.Title = `Marketing Efficiency Audit — ${s.clientName || 'Client'}`;
      doc.info.Author = 'Smart 1 Marketing';
      doc.info.Subject = 'Marketing Efficiency Audit findings';

      let y = 0;
      let firstPage = true;

      /* ---------- page chrome ---------- */
      function newPage() {
        doc.addPage();
        const bandH = firstPage ? 108 : 62;
        doc.rect(0, 0, PAGE.w, bandH).fill(C.navy);
        doc.rect(0, bandH - 4, PAGE.w, 4).fill(C.green);

        if (fs.existsSync(LOGO)) {
          try { doc.image(LOGO, PAGE.m, firstPage ? 26 : 20, { height: firstPage ? 26 : 20 }); } catch (e) { /* ignore */ }
        }

        if (firstPage) {
          doc.font('Helvetica-Bold').fontSize(20).fillColor(C.white)
            .text('Marketing Efficiency Audit\u2122', PAGE.m, 62, { width: CONTENT_W });
          doc.font('Helvetica').fontSize(9).fillColor(C.lightText)
            .text('Directional assessment prepared for internal discussion', PAGE.m, 86, { width: CONTENT_W });
          y = bandH + 26;
        } else {
          const LOGO_W = 20 * (1094 / 102); // keep the running head clear of the logo
          doc.font('Helvetica').fontSize(8.5).fillColor(C.lightText)
            .text(s.clientName || 'Client report', PAGE.m + LOGO_W + 16, 27,
              { width: PAGE.w - PAGE.m * 2 - LOGO_W - 16, align: 'right' });
          y = bandH + 22;
        }
        firstPage = false;
      }

      function ensure(h) {
        if (y + h > FOOT_Y - 12) newPage();
      }

      /* ---------- primitives ---------- */
      function sectionTitle(text) {
        ensure(78); // keep the heading with at least the first rows of its section
        doc.font('Helvetica-Bold').fontSize(8.5).fillColor(C.muted)
          .text(text.toUpperCase(), PAGE.m, y, { width: CONTENT_W, characterSpacing: 0.9, wordSpacing: 3 });
        y += 15;
        doc.moveTo(PAGE.m, y).lineTo(PAGE.m + CONTENT_W, y).lineWidth(1).strokeColor(C.line).stroke();
        y += 14;
      }

      function body(text, opts = {}) {
        const width = opts.width || CONTENT_W;
        const size = opts.size || 10;
        const font = opts.font || 'Helvetica';
        const color = opts.color || C.ink;
        doc.font(font).fontSize(size).fillColor(color);
        const h = doc.heightOfString(text, { width, lineGap: opts.lineGap ?? 2 });
        ensure(h);
        doc.font(font).fontSize(size).fillColor(color)
          .text(text, opts.x ?? PAGE.m, y, { width, lineGap: opts.lineGap ?? 2 });
        y += h + (opts.after ?? 8);
      }

      function panel(h, fill = C.white, stroke = C.line) {
        ensure(h);
        doc.roundedRect(PAGE.m, y, CONTENT_W, h, 8).fillAndStroke(fill, stroke);
      }

      /* ---------- 1. client snapshot ---------- */
      newPage();

      const preparedBits = [
        s.preparedBy || lead.name ? `Prepared by ${s.preparedBy || lead.name}` : null,
        s.partnerFirm || lead.firm || null,
        new Date().toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' }),
      ].filter(Boolean).join('  ·  ');

      doc.font('Helvetica-Bold').fontSize(22).fillColor(C.navy)
        .text(s.clientName || 'Client business', PAGE.m, y, { width: CONTENT_W });
      y += 30;
      doc.font('Helvetica').fontSize(9.5).fillColor(C.muted)
        .text(preparedBits, PAGE.m, y, { width: CONTENT_W });
      y += 22;

      const facts = [
        ['Industry', s.industry || '—'],
        ['Annual revenue', money(s.annualRevenue)],
        ['Market', [s.cityMarket, s.zipCode].filter(Boolean).join(' ') || '—'],
        ['Locations', s.locations ? String(s.locations) : '—'],
        ['Monthly investment', money(m.spend)],
        ['Annualized investment', money((m.spend || 0) * 12)],
      ];
      panel(64);
      const colW = CONTENT_W / 3;
      facts.forEach((f, i) => {
        const col = i % 3, row = Math.floor(i / 3);
        const fx = PAGE.m + col * colW + 14;
        const fy = y + 12 + row * 26;
        doc.font('Helvetica').fontSize(7.5).fillColor(C.muted)
          .text(f[0].toUpperCase(), fx, fy, { width: colW - 20, characterSpacing: 0.6 });
        doc.font('Helvetica-Bold').fontSize(10.5).fillColor(C.navy)
          .text(f[1], fx, fy + 9, { width: colW - 20 });
      });
      y += 64 + 20;

      /* ---------- 2. score ---------- */
      sectionTitle('Marketing Efficiency Score\u2122');

      const score = Math.max(0, Math.min(100, Number(payload.score) || 0));
      const gaugeColor = score >= 65 ? C.green : score >= 50 ? C.gold : C.red;
      const panelH = 132;
      panel(panelH);

      const cx = PAGE.m + 92, cy = y + 96, r = 62;
      doc.path(`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`)
        .lineWidth(13).strokeColor('#EDF1F6').lineCap('round').stroke();
      const ang = Math.PI * (1 - score / 100);
      const ex = cx + r * Math.cos(ang), ey = cy - r * Math.sin(ang);
      if (score > 0.5) {
        doc.path(`M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${ex} ${ey}`)
          .lineWidth(13).strokeColor(gaugeColor).lineCap('round').stroke();
      }
      doc.font('Helvetica-Bold').fontSize(30).fillColor(C.navy)
        .text(String(score), cx - 50, cy - 34, { width: 100, align: 'center' });
      doc.font('Helvetica').fontSize(9).fillColor(C.muted)
        .text('out of 100', cx - 50, cy - 4, { width: 100, align: 'center' });

      const tx = PAGE.m + 190, tw = CONTENT_W - 210;
      doc.font('Helvetica-Bold').fontSize(14).fillColor(C.navy)
        .text(payload.scoreTier || '', tx, y + 24, { width: tw });
      doc.font('Helvetica').fontSize(9.5).fillColor(C.muted)
        .text(payload.scoreNote || '', tx, y + 44, { width: tw, lineGap: 2 });
      const flaggedCount = (payload.flags || []).filter((f) => f.answer).length;
      const totalFlags = (payload.flags || []).length || 10;
      doc.font('Helvetica-Bold').fontSize(9).fillColor(C.ink)
        .text(`Warning signs: ${flaggedCount} of ${totalFlags} present  ·  ${payload.leakTier || ''}`,
          tx, y + panelH - 34, { width: tw });
      y += panelH + 20;

      /* ---------- 3. benchmarks ---------- */
      sectionTitle('Against industry benchmarks');

      function rangeBar(label, lo, hi, value, fmt, verdict, verdictClass, fmtValue) {
        const fmtV = fmtValue || fmt;
        const barH = 16, blockH = 58;
        ensure(blockH);
        doc.font('Helvetica-Bold').fontSize(9.5).fillColor(C.navy).text(label, PAGE.m, y, { width: CONTENT_W });
        const by = y + 15;

        if (lo === null || lo === undefined || value === null || value === undefined || !isFinite(value)) {
          doc.font('Helvetica').fontSize(9).fillColor(C.muted)
            .text(verdict || 'Not enough information to compare.', PAGE.m, by + 2, { width: CONTENT_W });
          y += 38;
          return;
        }

        const max = Math.max(hi * 1.6, value * 1.15);
        const px = (v) => PAGE.m + Math.min(CONTENT_W, Math.max(0, (v / max) * CONTENT_W));
        doc.roundedRect(PAGE.m, by, CONTENT_W, barH, 4).fillAndStroke(C.bg, C.line);
        doc.rect(px(lo), by, px(hi) - px(lo), barH).fill('#CFF3EC');
        doc.rect(px(lo), by, 1.6, barH).fill(C.green);
        doc.rect(px(hi) - 1.6, by, 1.6, barH).fill(C.green);
        doc.rect(px(value) - 1.2, by - 3, 2.4, barH + 6).fill(C.navy);

        doc.font('Helvetica').fontSize(7.5).fillColor(C.muted)
          .text(`benchmark ${fmt(lo)} – ${fmt(hi)}`, PAGE.m, by + barH + 4, { width: CONTENT_W / 2 });
        const vColor = verdictClass === 'bad' ? C.red : verdictClass === 'warn' ? C.amber : C.green2;
        doc.font('Helvetica-Bold').fontSize(8.5).fillColor(vColor)
          .text(`This client: ${fmtV(value)} — ${verdict}`, PAGE.m + CONTENT_W / 2, by + barH + 3,
            { width: CONTENT_W / 2, align: 'right' });
        y += blockH;
      }

      rangeBar('Media spend as a share of revenue (staff and events excluded)', b.budgetLo, b.budgetHi, m.spendPct,
        (v) => v.toFixed(0) + '%', b.spendVerdict, b.spendClass, (v) => v.toFixed(1) + '%');

      rangeBar('Cost per lead', b.cplLo, b.cplHi, m.cpl, (v) => money(v),
        b.cplLo == null ? `No published range for ${s.industry || 'this industry'}. This client: ${money(m.cpl)} per lead.` : b.cplVerdict,
        b.cplClass);
      y += 6;

      /* ---------- 4. metrics ---------- */
      sectionTitle('Calculated performance');

      const cards = [
        ['Cost per lead', money(m.cpl), m.leads ? `${m.leads} leads per month` : 'Leads not provided'],
        ['Customer acquisition cost', money(m.cac), m.customers ? `${m.customers} new customers / month` : 'Customers not provided'],
        ['Close rate', pct(m.closeRate), 'Customers / leads'],
        ['Customer lifetime value', money(m.clv), `${money(m.avgSale)} × ${m.purchasesPerYear || 1}/yr × ${m.customerYears || 1} yrs`],
        ['Revenue from marketing', money(m.revenue), 'New customers × average sale, monthly'],
        ['Marketing ROI', m.roi != null ? Math.round(m.roi) + '%' : '—', '(Revenue - cost) / cost'],
      ];
      const cardW = (CONTENT_W - 20) / 3, cardH = 70;
      ensure(cardH * 2 + 10);
      cards.forEach((c, i) => {
        const col = i % 3, row = Math.floor(i / 3);
        const x = PAGE.m + col * (cardW + 10);
        const cy2 = y + row * (cardH + 10);
        doc.roundedRect(x, cy2, cardW, cardH, 7).fillAndStroke(C.white, C.line);
        // Labels are clipped to a single line so a long one can never run into the value
        doc.font('Helvetica').fontSize(7).fillColor(C.muted)
          .text(c[0].toUpperCase(), x + 11, cy2 + 12, {
            width: cardW - 22, characterSpacing: 0.4, height: 10, ellipsis: true, lineBreak: false,
          });
        doc.font('Helvetica-Bold').fontSize(15).fillColor(C.navy)
          .text(c[1], x + 11, cy2 + 27, { width: cardW - 22, height: 19, lineBreak: false });
        doc.font('Helvetica').fontSize(7).fillColor(C.muted)
          .text(c[2], x + 11, cy2 + 50, { width: cardW - 22, height: 18, ellipsis: true });
      });
      y += cardH * 2 + 10 + 22;

      /* ---------- 5. opportunity ---------- */
      const scen = (m.scenarios && m.scenarios.length) ? m.scenarios
        : [{ liftPct: m.liftPct || 15, monthly: m.opportunityMonthly, annual: m.opportunityAnnual, addCustomers: 0 }];
      const oppH = 132;
      ensure(oppH);
      doc.roundedRect(PAGE.m, y, CONTENT_W, oppH, 8).fill(C.navy);
      doc.font('Helvetica').fontSize(7.5).fillColor('#9FB4D0')
        .text('GROWTH OPPORTUNITY', PAGE.m + 20, y + 15, { width: CONTENT_W - 40, characterSpacing: 0.8, wordSpacing: 2.5 });

      const oppCardW = (CONTENT_W - 40 - 16) / 2;
      scen.slice(0, 2).forEach((x, i) => {
        const cx2 = PAGE.m + 20 + i * (oppCardW + 16);
        const cy3 = y + 32;
        doc.roundedRect(cx2, cy3, oppCardW, 60, 7).fillAndStroke('#243B66', '#33507F');
        doc.font('Helvetica-Bold').fontSize(7.5).fillColor('#9FB4D0')
          .text(`+${x.liftPct}% LEAD VOLUME`, cx2 + 13, cy3 + 10, { width: oppCardW - 26, characterSpacing: 0.5 });
        doc.font('Helvetica-Bold').fontSize(17).fillColor(C.gold)
          .text(money(x.annual) + ' / yr', cx2 + 13, cy3 + 22, { width: oppCardW - 26 });
        doc.font('Helvetica').fontSize(7.5).fillColor(C.lightText)
          .text(`${money(x.monthly)} per month${x.addCustomers ? ` · ${x.addCustomers.toFixed(1)} more customers` : ''}`,
            cx2 + 13, cy3 + 45, { width: oppCardW - 26 });
      });

      doc.font('Helvetica').fontSize(8).fillColor(C.lightText)
        .text(`At the current close rate of ${pct(m.closeRate)} and average sale of ${money(m.avgSale)}, with no change to average sale value. Both figures assume the close rate holds as volume rises.`,
          PAGE.m + 20, y + oppH - 32, { width: CONTENT_W - 40, lineGap: 2 });
      y += oppH + 24;

      function bullets(items, color) {
        (items || []).forEach((t) => {
          const tw = CONTENT_W - 16;
          doc.font('Helvetica').fontSize(9);
          const h = doc.heightOfString(t, { width: tw, lineGap: 2 });
          ensure(h + 9);
          doc.circle(PAGE.m + 3, y + 5, 2.2).fill(color || C.sky);
          doc.font('Helvetica').fontSize(9).fillColor(C.muted)
            .text(t, PAGE.m + 16, y, { width: tw, lineGap: 2 });
          y += h + 8;
        });
      }

      /* ---------- 5d. target market ---------- */
      const hasMarket = mk.audienceType || mk.serviceRadius || (mk.ageRanges || []).length || mk.incomeBand || mk.genderSkew || mk.audienceNotes || mk.contextNotes;
      if (hasMarket) {
        sectionTitle('Target market and business context');
        const mRows = [
          ['Sells to', mk.audienceType],
          ['Service area', mk.serviceRadius],
          ['Primary age ranges', (mk.ageRanges || []).join(', ')],
          ['Household income', mk.incomeBand],
          ['Gender skew', mk.genderSkew],
        ].filter((r2) => r2[1]);
        mRows.forEach((row) => {
          ensure(17);
          doc.font('Helvetica').fontSize(9).fillColor(C.muted).text(row[0], PAGE.m, y, { width: CONTENT_W * 0.5 });
          doc.font('Helvetica-Bold').fontSize(9).fillColor(C.navy)
            .text(row[1], PAGE.m + CONTENT_W * 0.5, y, { width: CONTENT_W * 0.5, align: 'right' });
          y += 16;
        });
        y += 6;
        if (mk.audienceNotes) body(`Audience notes: ${mk.audienceNotes}`, { size: 9, color: C.ink, after: 6 });
        if (mk.contextNotes) {
          body('Business context supplied by the partner', { font: 'Helvetica-Bold', size: 9.5, color: C.navy, after: 4 });
          body(mk.contextNotes, { size: 9, color: C.muted, after: 16 });
        }
      }

      /* ---------- 5b. industry spending ---------- */
      sectionTitle(`What ${s.industry || 'this industry'} typically spends`);

      const midSpend = s.annualRevenue && b.budgetMid ? (s.annualRevenue * b.budgetMid) / 100 / 12 : null;
      const indFacts = [
        ['Typical budget range', `${b.budgetLo ?? '?'}%–${b.budgetHi ?? '?'}% of revenue`],
        ['Industry midpoint', b.budgetMid != null ? b.budgetMid.toFixed(1) + '% of revenue' : '—'],
        ['Midpoint at this revenue', midSpend != null ? money(midSpend) + ' / mo' : '—'],
        ['This client (media)', m.spendPct != null ? pct(m.spendPct) + ' of revenue' : '—'],
      ];
      const indH = 58;
      panel(indH);
      const iw = CONTENT_W / 4;
      indFacts.forEach((f, i) => {
        const fx = PAGE.m + i * iw + 12;
        doc.font('Helvetica').fontSize(7).fillColor(C.muted)
          .text(f[0].toUpperCase(), fx, y + 13, { width: iw - 18, characterSpacing: 0.4 });
        doc.font('Helvetica-Bold').fontSize(10).fillColor(C.navy)
          .text(f[1], fx, y + 30, { width: iw - 18 });
      });
      y += indH + 12;

      if (b.mixDigital != null) {
        ensure(46);
        const mixH = 20;
        const dw = (CONTENT_W * b.mixDigital) / 100;
        doc.rect(PAGE.m, y, dw, mixH).fill(C.sky);
        doc.rect(PAGE.m + dw, y, CONTENT_W - dw, mixH).fill(C.navy);
        doc.font('Helvetica-Bold').fontSize(8).fillColor(C.white)
          .text(`${b.mixDigital}% digital`, PAGE.m, y + 6, { width: dw, align: 'center' });
        doc.font('Helvetica-Bold').fontSize(8).fillColor(C.white)
          .text(`${b.mixTraditional}% traditional`, PAGE.m + dw, y + 6, { width: CONTENT_W - dw, align: 'center' });
        y += mixH + 8;
        doc.font('Helvetica').fontSize(7.5).fillColor(C.muted)
          .text('Typical channel mix for this industry', PAGE.m, y, { width: CONTENT_W });
        y += 14;
      }

      if (midSpend != null && (m.mediaSpend || m.spend)) {
        const gap = (m.mediaSpend ?? m.spend) - midSpend;
        body(`${gap >= 0 ? 'Above' : 'Below'} the industry midpoint by ${money(Math.abs(gap))} per month, or ${money(Math.abs(gap) * 12)} a year.`,
          { font: 'Helvetica-Bold', size: 9.5, color: C.navy, after: 4 });
      }
      if (b.industryNote) body(b.industryNote, { size: 9, color: C.muted, after: 16 });

      /* ---------- 5b2. industry facts ---------- */
      if ((b.industryFacts || []).length) {
        sectionTitle(`${s.industry || 'Industry'} benchmarks and facts`);
        const fr = [
          ['Budget range', `${b.budgetLo}%-${b.budgetHi}% of revenue`],
          ['Cost per lead range', b.cplLo != null ? `${money(b.cplLo)}-${money(b.cplHi)}` : 'No published range'],
          ['Channel mix', `${b.mixDigital}% digital / ${b.mixTraditional}% traditional`],
        ];
        ensure(40);
        const fw = CONTENT_W / 3;
        fr.forEach((f, i) => {
          const fx = PAGE.m + i * fw;
          doc.font('Helvetica').fontSize(7).fillColor(C.muted)
            .text(f[0].toUpperCase(), fx, y, { width: fw - 12, characterSpacing: 0.4 });
          doc.font('Helvetica-Bold').fontSize(9.5).fillColor(C.navy)
            .text(f[1], fx, y + 11, { width: fw - 12 });
        });
        y += 34;
        bullets(b.industryFacts, C.green);
        body('Directional industry patterns, not guarantees. Any individual business can sit outside them for good reasons.',
          { size: 8, color: C.muted, after: 16 });
      }

      /* ---------- 5b3. audience estimate ---------- */
      if (aud && aud.steps) {
        const md = payload.marketData || {};
        sectionTitle(`Estimated reachable audience${md.areaName ? ` — ${md.areaName}` : (s.cityMarket ? ` — ${s.cityMarket}` : '')}`);
        if (md.basis) {
          body(`${md.basis} (${md.confidence || 'estimated'} confidence)`, { size: 8.5, color: C.muted, after: 8 });
        }
        aud.steps.forEach((st) => {
          ensure(17);
          const em = !!st.emphasis;
          doc.font(em ? 'Helvetica-Bold' : 'Helvetica').fontSize(9).fillColor(em ? C.navy : C.muted)
            .text(st.label + (st.note ? `  (${st.note})` : ''), PAGE.m, y, { width: CONTENT_W * 0.68 });
          doc.font('Helvetica-Bold').fontSize(em ? 10 : 9).fillColor(em ? C.green2 : C.navy)
            .text(Number(st.value).toLocaleString('en-US'), PAGE.m + CONTENT_W * 0.68, y, { width: CONTENT_W * 0.32, align: 'right' });
          y += 16;
        });
        y += 6;
        if (aud.low != null) {
          body(`Working range: ${aud.low.toLocaleString('en-US')} - ${aud.high.toLocaleString('en-US')}.`,
            { font: 'Helvetica-Bold', size: 9.5, color: C.navy, after: 5 });
        }
        if (md.demographicNote) body(`About this area: ${md.demographicNote}`, { size: 9, color: C.ink, after: 6 });
        if (md.medianHouseholdIncome) {
          body(`Median household income ${money(md.medianHouseholdIncome)}${md.medianAge ? ` · median age ${md.medianAge}` : ''}`,
            { size: 9, color: C.muted, after: 6 });
        }
        body('A directional estimate: an approximate service-area population filtered by national age, income, household, and business-density averages. Not local census data. Confirm against census or ad-platform reach figures before setting a budget.',
          { size: 8, color: C.muted, after: 16 });
      }

      /* ---------- 5b4. website ---------- */
      if (wb) {
        sectionTitle('Website conversion review');
        body(wb.finalUrl, { font: 'Helvetica-Bold', size: 10, color: C.navy, after: 8 });
        const wr = [
          ['Ways to convert', `${wb.conversionPoints} found`, wb.conversionPoints < 2],
          ['Forms', String(wb.counts?.forms ?? 0), false],
          ['Click-to-call links', String(wb.counts?.telLinks ?? 0), !wb.counts?.telLinks],
          ['Tracking tags', (wb.trackers || []).length ? `${wb.trackers.length} detected` : 'None detected', !(wb.trackers || []).length],
        ];
        ensure(40);
        const ww = CONTENT_W / 4;
        wr.forEach((f, i) => {
          const fx = PAGE.m + i * ww;
          doc.font('Helvetica').fontSize(7).fillColor(C.muted)
            .text(f[0].toUpperCase(), fx, y, { width: ww - 10, characterSpacing: 0.4 });
          doc.font('Helvetica-Bold').fontSize(10).fillColor(f[2] ? C.red : C.navy)
            .text(f[1], fx, y + 11, { width: ww - 10 });
        });
        y += 36;
        if (wb.analysis?.summary) body(wb.analysis.summary, { size: 9.5, color: C.ink, after: 8 });
        if (wb.analysis?.measurementVerdict) body(`Measurement: ${wb.analysis.measurementVerdict}`, { size: 9.5, color: C.ink, after: 10 });

        if (wb.analysis?.gaps?.length) {
          body('What to fix', { font: 'Helvetica-Bold', size: 9.5, color: C.navy, after: 6 });
          wb.analysis.gaps.forEach((g) => {
            const barColor = g.impact === 'high' ? C.red : g.impact === 'medium' ? C.amber : C.green;
            const tw = CONTENT_W - 16;
            doc.font('Helvetica-Bold').fontSize(9.5);
            const th = doc.heightOfString(g.issue || '', { width: tw });
            doc.font('Helvetica').fontSize(9);
            const dh = doc.heightOfString(g.fix || '', { width: tw, lineGap: 2 });
            ensure(th + dh + 14);
            doc.rect(PAGE.m, y, 3, th + dh + 5).fill(barColor);
            doc.font('Helvetica-Bold').fontSize(9.5).fillColor(C.navy).text(g.issue || '', PAGE.m + 14, y, { width: tw });
            doc.font('Helvetica').fontSize(9).fillColor(C.muted).text(g.fix || '', PAGE.m + 14, y + th + 2, { width: tw, lineGap: 2 });
            y += th + dh + 14;
          });
        } else if ((wb.missing || []).length) {
          body('What the scan did not find', { font: 'Helvetica-Bold', size: 9.5, color: C.navy, after: 6 });
          bullets(wb.missing, C.red);
        }
        body('Based on the served home page only. Design, speed, and copy quality were not assessed.',
          { size: 8, color: C.muted, after: 16 });
      }

      /* ---------- 5b5. competition ---------- */
      if ((cp.competitors || []).length || cp.differentiation || cp.losingTo) {
        sectionTitle('Competitive position');
        if ((cp.competitors || []).length) {
          cp.competitors.forEach((x) => {
            ensure(16);
            doc.font('Helvetica-Bold').fontSize(9).fillColor(C.navy)
              .text(x.name || x.website || '', PAGE.m, y, { width: CONTENT_W * 0.5 });
            doc.font('Helvetica').fontSize(9).fillColor(C.muted)
              .text(x.website || 'no website supplied', PAGE.m + CONTENT_W * 0.5, y, { width: CONTENT_W * 0.5, align: 'right' });
            y += 15;
          });
          y += 6;
        } else {
          body('No competitors were named. A business that cannot name who it loses work to is usually bidding against them blind in search auctions.',
            { size: 9.5, color: C.muted, after: 8 });
        }
        if (cp.differentiation) body(`What sets them apart: ${cp.differentiation}`, { size: 9.5, color: C.ink, after: 6 });
        else body('No differentiation was stated. Where a business cannot say why a customer should choose them, advertising competes on price by default, which raises cost per lead.',
          { size: 9.5, color: C.muted, after: 6 });
        if (cp.losingTo) body(`Losing work to: ${cp.losingTo}`, { size: 9.5, color: C.ink, after: 10 });

        // Head-to-head site comparisons, where the background scan succeeded
        const compared = (cp.competitors || []).filter((x) => x.comparison && x.comparison.comparison);
        compared.forEach((x) => {
          const cc = x.comparison.comparison;
          body(`${x.name} — site comparison`, { font: 'Helvetica-Bold', size: 9.5, color: C.navy, after: 4 });
          if (cc.verdict) body(cc.verdict, { size: 9, color: C.ink, after: 5 });
          if ((cc.competitorAdvantages || []).length) {
            body('What they do that this client does not:', { size: 8.5, color: C.muted, after: 3 });
            bullets(cc.competitorAdvantages, C.red);
          }
          if ((cc.clientAdvantages || []).length) {
            body('What this client does that they do not:', { size: 8.5, color: C.muted, after: 3 });
            bullets(cc.clientAdvantages, C.green);
          }
          if (cc.takeaway) body(cc.takeaway, { font: 'Helvetica-Bold', size: 9, color: C.navy, after: 12 });
        });
        if (!compared.length) y += 10;
      }

      /* ---------- 5c. how they buy ---------- */
      sectionTitle('How this client buys marketing');

      const YN = { yes: 'Yes', no: 'No', unsure: 'Not sure', consistent: 'Consistent', semi: 'Semi-consistent', sporadic: 'Starts and stops' };
      const lab = (v) => YN[v] || (v && v !== 'not answered' && v !== 'not asked' ? v : 'Not answered');

      const buyRows = [
        ['Digital spend over $2,500/mo', lab(pr.digitalOver2500)],
        ['Traditional spend over $2,500/mo', lab(pr.traditionalOver2500)],
        ['Buys lead services', lab(pr.buysLeadServices)],
        ['Buying model', lab(pr.buyingModel)],
      ];
      if (pr.providesTraining && pr.providesTraining !== 'not asked') buyRows.push(['Trains in-house staff', lab(pr.providesTraining)]);
      buyRows.push(['Seasonal pushes', lab(pr.seasonalMarketing) + (pr.seasonDetail ? ` — ${pr.seasonDetail}` : '')]);
      buyRows.push(['Monthly consistency', lab(pr.monthlyConsistency)]);
      buyRows.push(['Live events', lab(pr.liveEvents) + (pr.eventsDetail ? ` — ${pr.eventsDetail}` : '')]);
      if (pr.marketingHeadcount) {
        buyRows.push(['Marketing employees', `${pr.marketingHeadcount}${pr.marketingPayroll ? ` at ${money(pr.marketingPayroll)}/mo` : ''}`]);
      }
      if (pr.assetOwnership) buyRows.push(['Owns website, domain, ad accounts', pr.assetOwnership]);
      if (pr.leadResponseTime) buyRows.push(['Lead response time', pr.leadResponseTime]);
      if (pr.crmTracking) buyRows.push(['Tracks leads to closed sale', pr.crmTracking]);

      buyRows.forEach((row) => {
        ensure(17);
        doc.font('Helvetica').fontSize(9).fillColor(C.muted).text(row[0], PAGE.m, y, { width: CONTENT_W * 0.55 });
        const flagged =
          (row[0] === 'Owns website, domain, ad accounts' && /agency|nobody/i.test(row[1])) ||
          (row[0] === 'Lead response time' && /next business day|not tracked/i.test(row[1])) ||
          (row[0] === 'Tracks leads to closed sale' && /^(No|Not sure)/i.test(row[1])) ||
          (row[0] === 'Trains in-house staff' && row[1] === 'No') ||
          (row[0] === 'Buys lead services' && row[1] === 'Yes') ||
          (row[0] === 'Monthly consistency' && row[1] === 'Starts and stops');
        doc.font('Helvetica-Bold').fontSize(9).fillColor(flagged ? C.red : C.navy)
          .text(row[1], PAGE.m + CONTENT_W * 0.55, y, { width: CONTENT_W * 0.45, align: 'right' });
        y += 16;
      });
      y += 6;

      const mediaList = (pr.traditionalMedia || []).join(', ') + (pr.traditionalOther ? `${(pr.traditionalMedia || []).length ? ', ' : ''}${pr.traditionalOther}` : '');
      body(`Traditional media: ${mediaList || 'none reported'}`, { size: 9, color: C.ink, after: 5 });
      if (pr.digitalVendors) body(`Digital vendors: ${pr.digitalVendors.replace(/\s*\n\s*/g, ', ')}`, { size: 9, color: C.ink, after: 5 });
      if (payload.expenses) {
        body(`Expense document: ${payload.expenses.filename} — ${payload.expenses.mode === 'ai' ? 'read automatically' : 'saved for analyst review'}${payload.expenses.period ? `, covering ${payload.expenses.period}` : ''}.`,
          { size: 9, color: C.muted, after: 16 });
      } else { y += 10; }

      /* ---------- 6b. vendor consolidation ---------- */
      const vendorCount = pr.digitalVendors
        ? pr.digitalVendors.split(/[,\n;]+/).map((v) => v.trim()).filter(Boolean).length
        : (s.vendors || 0);
      if (vendorCount >= 2) {
        y += 6;
        sectionTitle('Why consolidating digital vendors is worth considering');
        body('The problem is not the number of vendors. It is that no one sees the whole picture.',
          { font: 'Helvetica-Bold', size: 10, color: C.navy, after: 6 });
        body(`With ${vendorCount} vendors each running part of the marketing, each reports on its own slice and each slice looks acceptable in isolation. Nobody can answer the only question that matters: which dollar produced which customer.`,
          { size: 9.5, color: C.ink, after: 10 });
        const overlap = m.spend ? m.spend * 0.1 : null;
        bullets([
          'Attribution breaks at the seams. A visitor who sees a social ad, searches the brand later, and calls from the map listing gets counted by whichever vendor claims the last click. Two vendors bill for the same customer.',
          'Budget moves in the wrong direction. Spend shifts toward whichever vendor reports most confidently rather than whichever produces revenue.',
          `Duplicate spend goes unnoticed. Overlapping retargeting, brand-term bidding against the client's own organic listing, and two vendors buying the same audience are only visible when someone sees every account at once.${overlap ? ` At ${money(m.spend)} a month, ten percent overlap is ${money(overlap)} a month, or ${money(overlap * 12)} a year.` : ''}`,
          'Testing becomes impossible. Optimization needs one variable changed at a time against one measure of success. Separate vendors optimizing separate metrics cancel each other out.',
          'Nobody owns the outcome. When leads fall, each vendor points to its own numbers and is right. Consolidation creates one accountable party for cost per acquired customer.',
        ], C.gold);
        body('Consolidation does not have to mean one vendor for everything. It means one place where all spend, leads, and closed sales are visible together, and one party accountable for that view.',
          { size: 9.5, color: C.ink, after: 16 });
      }

      /* ---------- 6a. savings ---------- */
      const sv = payload.savings;
      if (sv && sv.monthly > 0) {
        y += 4;
        sectionTitle('Possible savings from tightening the program');

        const svH = 62;
        ensure(svH);
        doc.roundedRect(PAGE.m, y, CONTENT_W, svH, 8).fillAndStroke('#EAFBF7', '#B7EDE2');
        doc.font('Helvetica-Bold').fontSize(22).fillColor(C.green2)
          .text(money(sv.annual) + ' a year', PAGE.m + 20, y + 14, { width: CONTENT_W - 40 });
        doc.font('Helvetica').fontSize(9).fillColor(C.muted)
          .text(`${money(sv.monthly)} per month · ${((sv.monthly / (sv.total || 1)) * 100).toFixed(1)}% of current spend`,
            PAGE.m + 20, y + 41, { width: CONTENT_W - 40 });
        y += svH + 16;

        if (sv.consolidate > 0) {
          body('Consolidating digital vendors', { font: 'Helvetica-Bold', size: 10, color: C.navy, after: 4 });
          body(`${sv.vendorCount} vendors across ${money(sv.digital)} a month of digital spend. Where several vendors work from one plan and one measurement standard rather than their own, roughly 20% of digital spend is typically recoverable: duplicate tools, overlapping audiences, brand terms bid against organic listings, and management fees paid twice on the same work. That is ${money(sv.consolidate)} a month here.`,
            { size: 9.5, color: C.ink, after: 12 });
        }
        if (sv.overlap > 0) {
          body('Removing traditional and digital overlap', { font: 'Helvetica-Bold', size: 10, color: C.navy, after: 4 });
          body(`${money(sv.traditional)} a month in traditional media running alongside ${money(sv.digital)} in digital. Bought separately, the two usually reach the same people at the same time without either side knowing. Aligning the calendar and the audience typically frees about 25% of traditional spend without reducing reach — ${money(sv.overlap)} a month here.`,
            { size: 9.5, color: C.ink, after: 12 });
        }

        // Before/after on the metrics that move. Both columns are derived from the
        // same spend figure so the comparison can never be internally inconsistent.
        const oldSpend = m.spend || 0;
        const newSpend = Math.max(0, oldSpend - sv.monthly);
        const per = (total, divisor) => (divisor > 0 && total > 0 ? total / divisor : null);
        const roiOf = (cost) => (m.revenue != null && cost > 0 ? ((m.revenue - cost) / cost) * 100 : null);
        const oldCpl = per(oldSpend, m.leads), newCpl = per(newSpend, m.leads);
        const oldCac = per(oldSpend, m.customers), newCac = per(newSpend, m.customers);
        const oldRoi = roiOf(oldSpend), newRoi = roiOf(newSpend);
        const rows2 = [
          ['Monthly spend', money(oldSpend), money(newSpend)],
          newCpl != null ? ['Cost per lead', money(oldCpl), money(newCpl)] : null,
          newCac != null ? ['Customer acquisition cost', money(oldCac), money(newCac)] : null,
          newRoi != null ? ['Marketing ROI', Math.round(oldRoi) + '%', Math.round(newRoi) + '%'] : null,
        ].filter(Boolean);

        ensure(rows2.length * 17 + 24);
        doc.font('Helvetica-Bold').fontSize(7.5).fillColor(C.muted)
          .text('TODAY', PAGE.m + CONTENT_W * 0.55, y, { width: CONTENT_W * 0.22, align: 'right' })
          .text('AFTER SAVINGS', PAGE.m + CONTENT_W * 0.77, y, { width: CONTENT_W * 0.23, align: 'right' });
        y += 14;
        rows2.forEach((r3) => {
          ensure(17);
          doc.font('Helvetica').fontSize(9).fillColor(C.navy).text(r3[0], PAGE.m, y, { width: CONTENT_W * 0.55 });
          doc.font('Helvetica').fontSize(9).fillColor(C.muted).text(r3[1], PAGE.m + CONTENT_W * 0.55, y, { width: CONTENT_W * 0.22, align: 'right' });
          doc.font('Helvetica-Bold').fontSize(9).fillColor(C.green2).text(r3[2], PAGE.m + CONTENT_W * 0.77, y, { width: CONTENT_W * 0.23, align: 'right' });
          y += 16;
        });
        y += 8;
        body('The 20% and 25% figures are typical recovery rates for programs with these characteristics, not a quote or a guarantee. The actual figure depends on what the vendor invoices contain, which is what the expense review exists to establish.',
          { size: 8, color: C.muted, after: 16 });
      }

      /* ---------- 6a2. vendor questions ---------- */
      {
        y += 4;
        sectionTitle('Five questions to ask any marketing vendor');
        body('These apply to the current vendors and to anyone the client might hire. The answers separate vendors who manage outcomes from vendors who bill for activity.',
          { size: 9, color: C.muted, after: 10 });
        const VQ = [
          ['Can you show me cost per acquired customer by channel?', 'Not cost per click or per lead — per customer who paid. A vendor who cannot produce this is reporting activity, not results.'],
          ['Who owns the ad accounts, the website, and the tracking?', 'The client should own all three. A vendor who owns them has leverage a supplier should not have.'],
          ['What is the notice period, and what transfers when we leave?', 'Month-to-month with full asset transfer is the professional standard. Long lock-ins usually protect the vendor, not the work.'],
          ['What did you change last month, and what happened?', 'Ongoing management should produce a running log of tests and results. No log usually means the account is on autopilot while fees continue.'],
          ['Where does my budget overlap with anything else we run?', 'A vendor who only sees their own slice cannot answer. That inability is the cost of fragmentation, stated out loud.'],
        ];
        VQ.forEach(([q, why], i) => {
          const tw = CONTENT_W - 30;
          doc.font('Helvetica-Bold').fontSize(9.5);
          const qh = doc.heightOfString(q, { width: tw });
          doc.font('Helvetica').fontSize(8.5);
          const wh = doc.heightOfString(why, { width: tw, lineGap: 1.5 });
          ensure(qh + wh + 15);
          doc.circle(PAGE.m + 8, y + 7, 8).fill(C.navy);
          doc.font('Helvetica-Bold').fontSize(8).fillColor(C.white)
            .text(String(i + 1), PAGE.m + 3, y + 4, { width: 10, align: 'center' });
          doc.font('Helvetica-Bold').fontSize(9.5).fillColor(C.navy)
            .text(q, PAGE.m + 24, y, { width: tw });
          doc.font('Helvetica').fontSize(8.5).fillColor(C.muted)
            .text(why, PAGE.m + 24, y + qh + 2, { width: tw, lineGap: 1.5 });
          y += qh + wh + 13;
        });
        y += 6;
      }

      /* ---------- 6. written findings ---------- */
      sectionTitle('Findings');

      if (a.headline) body(a.headline, { font: 'Helvetica-Bold', size: 13, color: C.navy, after: 8 });
      if (a.executiveSummary) body(a.executiveSummary, { size: 10, color: C.ink, after: 14 });

      (a.findings || []).forEach((f) => {
        const barColor = f.severity === 'high' ? C.red : f.severity === 'medium' ? C.amber : C.green;
        const tW = CONTENT_W - 16;
        doc.font('Helvetica-Bold').fontSize(10);
        const th = doc.heightOfString(f.title || '', { width: tW });
        doc.font('Helvetica').fontSize(9.5);
        const dh = doc.heightOfString(f.detail || '', { width: tW, lineGap: 2 });
        ensure(th + dh + 16);
        doc.rect(PAGE.m, y, 3, th + dh + 6).fill(barColor);
        doc.font('Helvetica-Bold').fontSize(10).fillColor(C.navy)
          .text(f.title || '', PAGE.m + 14, y, { width: tW });
        doc.font('Helvetica').fontSize(9.5).fillColor(C.muted)
          .text(f.detail || '', PAGE.m + 14, y + th + 2, { width: tW, lineGap: 2 });
        y += th + dh + 16;
      });

      if ((a.leaks || []).length) {
        y += 6;
        sectionTitle('Where money may be leaking');
        a.leaks.forEach((l) => {
          const lw = CONTENT_W - 145;
          doc.font('Helvetica-Bold').fontSize(9.5);
          const ah = doc.heightOfString(l.area || '', { width: lw });
          doc.font('Helvetica').fontSize(8.5);
          const wh = doc.heightOfString(l.why || '', { width: lw, lineGap: 1.5 });
          doc.font('Helvetica-Bold').fontSize(9.5);
          const amtH = doc.heightOfString(l.estimatedMonthlyImpact || '—', { width: 130 });
          const rowH = Math.max(ah + wh + 2, amtH);
          ensure(rowH + 14);
          doc.font('Helvetica-Bold').fontSize(9.5).fillColor(C.navy)
            .text(l.area || '', PAGE.m, y, { width: lw });
          doc.font('Helvetica').fontSize(8.5).fillColor(C.muted)
            .text(l.why || '', PAGE.m, y + ah + 1, { width: lw, lineGap: 1.5 });
          doc.font('Helvetica-Bold').fontSize(9.5).fillColor(C.red)
            .text(l.estimatedMonthlyImpact || '—', PAGE.m + CONTENT_W - 130, y + 1, { width: 130, align: 'right' });
          y += rowH + 8;
          doc.moveTo(PAGE.m, y).lineTo(PAGE.m + CONTENT_W, y).lineWidth(0.5).strokeColor(C.line).dash(2, { space: 2 }).stroke().undash();
          y += 8;
        });
      }

      /* ---------- 7. questions + next steps ---------- */
      function numberedList(title, items, accent) {
        if (!items || !items.length) return;
        y += 6;
        sectionTitle(title);
        items.forEach((item, i) => {
          const tw = CONTENT_W - 26;
          doc.font('Helvetica').fontSize(9.5);
          const h = doc.heightOfString(item, { width: tw, lineGap: 2 });
          ensure(h + 12);
          doc.circle(PAGE.m + 7, y + 6, 7).fill(accent);
          doc.font('Helvetica-Bold').fontSize(7.5).fillColor(C.white)
            .text(String(i + 1), PAGE.m + 2, y + 3.5, { width: 10, align: 'center' });
          doc.font('Helvetica').fontSize(9.5).fillColor(C.ink)
            .text(item, PAGE.m + 26, y, { width: tw, lineGap: 2 });
          y += h + 10;
        });
      }

      numberedList('Questions to ask in your next client meeting', a.questionsToAsk, C.sky);
      numberedList('Recommended next steps', a.nextSteps, C.green2);

      if (a.partnerTalkingPoint) {
        y += 4;
        const qw = CONTENT_W - 26;
        doc.font('Helvetica-Bold').fontSize(10.5);
        const qh = doc.heightOfString(`“${a.partnerTalkingPoint}”`, { width: qw, lineGap: 2 });
        ensure(qh + 26);
        doc.roundedRect(PAGE.m, y, CONTENT_W, qh + 22, 6).fillAndStroke(C.bg, C.line);
        doc.rect(PAGE.m, y, 3, qh + 22).fill(C.sky);
        doc.font('Helvetica-Bold').fontSize(10.5).fillColor(C.navy)
          .text(`“${a.partnerTalkingPoint}”`, PAGE.m + 16, y + 11, { width: qw, lineGap: 2 });
        y += qh + 32;
      }

      /* ---------- 8. warning sign detail ---------- */
      const flags = payload.flags || [];
      if (flags.length) {
        y += 4;
        sectionTitle('Warning sign detail');
        const RESP = {
          yes:   { dot: C.red,   text: 'Yes',          color: C.red,   ink: C.ink },
          unsure:{ dot: C.amber, text: 'Unsure',       color: C.amber, ink: C.ink },
          no:    { dot: C.green, text: 'No',           color: C.green2,ink: C.muted },
        };
        const order2 = { yes: 0, unsure: 1, 'not answered': 2, no: 3 };
        [...flags]
          .sort((a, b) => (order2[a.response] ?? 4) - (order2[b.response] ?? 4))
          .forEach((f) => {
            ensure(19);
            const r2 = RESP[f.response] || { dot: C.line, text: 'Not answered', color: C.muted, ink: C.muted };
            doc.circle(PAGE.m + 5, y + 5.5, 4).fill(r2.dot);
            doc.font('Helvetica').fontSize(9).fillColor(r2.ink)
              .text(f.label, PAGE.m + 17, y, { width: CONTENT_W - 105 });
            doc.font('Helvetica-Bold').fontSize(8.5).fillColor(r2.color)
              .text(r2.text, PAGE.m + CONTENT_W - 84, y, { width: 84, align: 'right' });
            y += 18;
          });
        y += 10;
      }

      /* ---------- 9. CTA ---------- */
      const bookingUrl = payload.bookingUrl || 'https://smart1marketing.com/contact';
      const ctaH = 132;
      ensure(ctaH);
      // If the call to action opened a page of its own, sit it at the foot rather
      // than floating at the top above a half page of white space.
      if (y < 120) y = FOOT_Y - ctaH - 18;
      doc.roundedRect(PAGE.m, y, CONTENT_W, ctaH, 8).fill(C.navy);

      doc.font('Helvetica-Bold').fontSize(15).fillColor(C.white)
        .text('Have questions about these results?', PAGE.m + 22, y + 18, { width: CONTENT_W - 44 });
      doc.font('Helvetica').fontSize(9.5).fillColor(C.lightText)
        .text('A 15\u201330 minute review with us can help explain what the numbers mean for this business and where the opportunities are. Advertising spend analysis \u00b7 conversion review \u00b7 website evaluation \u00b7 marketing technology review \u00b7 competitive insights \u00b7 recommendations report.',
          PAGE.m + 22, y + 41, { width: CONTENT_W - 44, lineGap: 2.5 });

      /* Button: drawn, then made clickable across the same rectangle. */
      const btnW = 168, btnH = 34;
      const btnX = PAGE.m + 22, btnY = y + ctaH - btnH - 18;
      doc.roundedRect(btnX, btnY, btnW, btnH, 8).fill(C.gold);
      doc.font('Helvetica-Bold').fontSize(11).fillColor('#3a2b00')
        .text('Schedule a review', btnX, btnY + 11.5, { width: btnW, align: 'center' });
      doc.link(btnX, btnY, btnW, btnH, bookingUrl);
      y += ctaH + 10;

      /* ---------- footers ---------- */
      const range = doc.bufferedPageRange();
      for (let i = range.start; i < range.start + range.count; i++) {
        doc.switchToPage(i);
        doc.page.margins.bottom = 0; // footers sit below the text margin; without this PDFKit adds blank pages
        doc.moveTo(PAGE.m, FOOT_Y).lineTo(PAGE.m + CONTENT_W, FOOT_Y).lineWidth(0.5).strokeColor(C.line).stroke();
        doc.font('Helvetica').fontSize(7).fillColor(C.muted)
          .text('Smart 1 Marketing · (614) 536-0768 · smart1marketing.com · Directional assessment based on information supplied by the preparer. Benchmarks vary by market, competition, maturity, and geography.',
            PAGE.m, FOOT_Y + 8, { width: CONTENT_W - 60, lineGap: 1 });
        doc.font('Helvetica-Bold').fontSize(7.5).fillColor(C.muted)
          .text(`${i + 1} / ${range.count}`, PAGE.m + CONTENT_W - 60, FOOT_Y + 8, { width: 60, align: 'right' });
      }

      doc.end();
    } catch (err) {
      reject(err);
    }
  });
}

module.exports = { buildAuditPdf };
