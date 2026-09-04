/* hub-qa-nudge.js — the QA task reminder, on the first page you open.
 *
 * There is no mailer in this Hub. That sentence appears in half a dozen
 * modules here, and the answer every one of them arrives at is the same: put
 * the number where people already look. The dashboard card is that answer for
 * somebody who opens the dashboard; this is the answer for somebody who signs
 * in and goes straight to a tool, which is most people most mornings.
 *
 * It is modeled on hub-cheers.js deliberately — same storage shape, same
 * once-a-day marker, same iframe rule — because two interruption layers that
 * behave differently is two things for a reader to learn. What differs is what
 * it is allowed to say, and the four rules below are all about that:
 *
 *   * IT IS A REMINDER, NOT A DIALOG. A modal in front of somebody who has
 *     just signed in to do a job is the thing hub-help.js was changed to stop
 *     doing. This is a corner card with the page fully usable behind it.
 *   * ONCE A DAY, PER PERSON. The marker is written when the card is SHOWN,
 *     not when it is dismissed — a reload must not bring it back, and somebody
 *     who closed it has still seen it.
 *   * ONLY WHEN SOMETHING IS WAITING ON THEM. A card that appears every
 *     morning to say there is nothing to do is a card people close without
 *     reading, and then they close the one that mattered.
 *   * NEVER IN SOMEBODY ELSE'S IFRAME. Hub pages are framed inside Smart 1
 *     Suite, and a staff to-do list inside a client-facing panel is an
 *     internal note in front of a client.
 *
 * Preview it without waiting for a task:
 *     ?qatasks=preview   — today's real answer, whatever it is
 *     ?qatasks=demo      — a made-up one, clearly marked as a sample
 */
(function () {
  'use strict';

  var KEY_PREFIX = 's1hub-qa-tasks:';
  var API = '/api/qa-tasks/summary';

  function framed() {
    try { return window.top !== window.self; } catch (e) { return true; }
  }

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/[&<>"]/g, function (c) {
        return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c];
      });
  }

  /* localStorage is not available in every context — a private window, a
   * browser set to block site data. A reminder that throws on read would take
   * the page's other scripts with it, so both halves are guarded and the
   * failure mode is "shown again tomorrow", never "page broken today". */
  function seen(key) {
    try { return window.localStorage.getItem(key) === '1'; } catch (e) { return false; }
  }
  function mark(key) {
    try { window.localStorage.setItem(key, '1'); } catch (e) { /* nothing to do */ }
  }

  function styles() {
    if (document.getElementById('qanudge-style')) { return; }
    var css = [
      '.qanudge{position:fixed;right:18px;bottom:58px;z-index:99992;',
      'width:min(360px,calc(100vw - 36px));background:#fff;border:1px solid #e2e8f0;',
      'border-left:4px solid #1769AA;border-radius:14px;',
      'box-shadow:0 14px 40px rgba(15,23,42,.18);',
      'font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#0f172a;',
      'animation:qanudge-in .22s cubic-bezier(.2,.9,.3,1.2)}',
      '@keyframes qanudge-in{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}',
      '.qanudge-h{display:flex;align-items:center;gap:9px;padding:13px 14px 8px}',
      '.qanudge-h b{font-size:14.5px;font-weight:800}',
      '.qanudge-x{margin-left:auto;border:0;background:none;font-size:20px;line-height:1;',
      'color:#94a3b8;cursor:pointer;padding:0 2px}',
      '.qanudge-x:hover{color:#334155}',
      '.qanudge-b{padding:0 14px 4px;font-size:13.5px;color:#334155}',
      '.qanudge-list{list-style:none;margin:9px 0 0;padding:0;font-size:13px}',
      '.qanudge-list li{padding:4px 0;border-top:1px solid #f1f5f9;line-height:1.45}',
      '.qanudge-list li b{font-weight:700}',
      '.qanudge-late{color:#dc2626;font-weight:700}',
      '.qanudge-f{display:flex;gap:9px;align-items:center;padding:11px 14px 14px}',
      '.qanudge-go{background:#1769AA;color:#fff;border:0;border-radius:10px;',
      'padding:9px 15px;font:700 13px system-ui,-apple-system,"Segoe UI",sans-serif;',
      'text-decoration:none;display:inline-block}',
      '.qanudge-go:hover{background:#12558a;color:#fff}',
      '.qanudge-later{border:0;background:none;color:#64748b;font:600 13px system-ui,',
      '-apple-system,"Segoe UI",sans-serif;cursor:pointer;margin-left:auto}',
      '.qanudge-demo{margin:0 14px 6px;padding:7px 11px;border-radius:9px;',
      'background:#e8f0fe;color:#1e3a8a;font-size:12px;font-weight:700}',
      '@media (prefers-reduced-motion:reduce){.qanudge{animation:none}}'
    ].join('');
    var tag = document.createElement('style');
    tag.id = 'qanudge-style';
    tag.textContent = css;
    document.head.appendChild(tag);
  }

  function close(card) {
    if (card) { card.remove(); }
  }

  function show(payload, opts) {
    opts = opts || {};
    var counts = (payload && payload.counts) || {};
    var todo = counts.to_do || 0;
    var back = counts.waiting_on_you || 0;
    if (!todo && !back) { return false; }
    styles();

    var rows = (payload.rows || []).slice(0, 3).map(function (t) {
      var who = t.mine_to_answer
        ? ('from ' + esc(t.created_by_name))
        : (esc(t.assigned_to_name) + ' answered');
      return '<li><b>' + esc(t.target_label) + '</b> — ' + who
        + (t.overdue ? ' <span class="qanudge-late">past the need-by date</span>' : '')
        + '</li>';
    }).join('');

    var card = document.createElement('div');
    card.className = 'qanudge';
    card.setAttribute('role', 'status');
    card.innerHTML =
      '<div class="qanudge-h"><span aria-hidden="true">&#9989;</span>'
      + '<b>QA tasks</b>'
      + '<button class="qanudge-x" type="button" aria-label="Close">&times;</button></div>'
      + (opts.demo ? '<div class="qanudge-demo">Sample &mdash; this is what the '
          + 'reminder looks like.</div>' : '')
      + '<div class="qanudge-b">' + esc(payload.line || '')
      + (rows ? '<ul class="qanudge-list">' + rows + '</ul>' : '')
      + '</div>'
      + '<div class="qanudge-f">'
      + '<a class="qanudge-go" href="' + esc(payload.url || '/qa-tasks') + '">Open QA tasks</a>'
      + '<button class="qanudge-later" type="button">Not now</button>'
      + '</div>';

    document.body.appendChild(card);
    card.querySelector('.qanudge-x').onclick = function () { close(card); };
    card.querySelector('.qanudge-later').onclick = function () { close(card); };
    return true;
  }

  var DEMO = {
    line: '2 reviews to do (1 past the need-by date) · 1 answer waiting on your reply.',
    url: '/qa-tasks',
    counts: { to_do: 2, overdue: 1, waiting_on_you: 1 },
    rows: [
      { target_label: 'Proposal Builder', created_by_name: 'Sample Person',
        mine_to_answer: true, overdue: true },
      { target_label: 'Client 360', created_by_name: 'Sample Person',
        mine_to_answer: true, overdue: false }
    ]
  };

  function start() {
    var params = '';
    try { params = window.location.search || ''; } catch (e) { params = ''; }
    var demo = params.indexOf('qatasks=demo') !== -1;
    var preview = params.indexOf('qatasks=preview') !== -1;

    if (demo) { show(DEMO, { demo: true }); return; }
    // Framed, and not asked for: silent. A staff to-do list inside a
    // client-facing panel is an internal note in front of a client.
    if (framed() && !preview) { return; }

    fetch(API, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        // "We could not read the table" is not "you have nothing to do", and
        // it is not an interruption either: the page says so where somebody
        // went looking, rather than in a corner card on an unrelated screen.
        if (!d || d.measured === false) { return; }
        var counts = d.counts || {};
        if (!counts.to_do && !counts.waiting_on_you) { return; }
        var key = KEY_PREFIX + (d.email || 'hub') + ':'
          + new Date().toISOString().slice(0, 10);
        if (!preview && seen(key)) { return; }
        // Marked when SHOWN, not when dismissed: a reload must not bring it
        // back, and somebody who closed it has still seen it.
        if (!preview) { mark(key); }
        show(d, {});
      })
      .catch(function () { /* a reminder is never worth an error on the page */ });
  }

  window.HubQaNudge = { show: show, demo: function () { return show(DEMO, { demo: true }); } };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
