/* hub-video-alerts.js — "your video edit is ready".
 *
 * Video Tools submit an edit to Cloudinary and do not wait for it. That is
 * deliberate: the Hub has no ffmpeg and a video encode inside a web request
 * takes the whole Hub down rather than one page. The consequence is that
 * somebody starts an edit and leaves, which is exactly what they should be
 * able to do — so something has to tell them when it lands.
 *
 * Two surfaces, and this is one of them. The dashboard card is what is still
 * waiting whenever they next look; this is the interruption, and it follows
 * the rules hub-cheers.js worked out for the birthday popup because they are
 * the rules that keep a popup worth reading:
 *
 *   * ONLY WHAT IS THEIRS AND ONLY WHAT IS NEW. The server decides both —
 *     /video-tools/api/ready returns this person's finished edits that have
 *     not been shown yet, and returns nothing at all otherwise.
 *   * MARKED WHEN SHOWN, NOT WHEN DISMISSED. A reload must not bring the
 *     same notice back. The marker is on the row rather than in localStorage,
 *     unlike the birthday popup: a birthday is everybody's and re-showing it
 *     is harmless, and this one has to survive them opening the Hub on a
 *     different machine.
 *   * NEVER IN SOMEBODY ELSE'S IFRAME. Hub pages are framed inside Smart 1
 *     Suite. Framed means silent.
 *   * IT IS NOT A TOAST. It carries the link to the edit, because a notice
 *     that only says "it is done" leaves the person to go and find it, and
 *     the finding is most of the work.
 *
 * Polls while the tab is open so an edit that lands under somebody's nose is
 * announced without a reload, and asks once immediately so one that landed
 * while they were away is announced on the page they come back to.
 */
(function () {
  'use strict';

  var API = '/video-tools/api/ready';
  var EVERY_MS = 20000;
  var timer = null;

  function framed() {
    try { return window.top !== window.self; } catch (e) { return true; }
  }

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/[&<>"]/g, function (c) {
        return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c];
      });
  }

  function styles() {
    if (document.getElementById('vta-style')) return;
    var css = [
      '.vta-wrap{position:fixed;right:18px;bottom:18px;z-index:100000;',
      'display:flex;flex-direction:column;gap:10px;max-width:340px}',
      '.vta{border-radius:10px;background:#fff;border:1px solid #e2e8f0;',
      'box-shadow:0 10px 30px rgba(15,23,42,.18);overflow:hidden;',
      'animation:vta-in .22s ease}',
      '@keyframes vta-in{from{opacity:0;transform:translateY(10px)}',
      'to{opacity:1;transform:none}}',
      '.vta-h{display:flex;align-items:center;gap:8px;padding:10px 12px;',
      'background:#1a2e58;color:#fff;font-size:12px;font-weight:700;',
      'letter-spacing:.3px}',
      '.vta.bad .vta-h{background:#991b1b}',
      '.vta-h .x{margin-left:auto;cursor:pointer;opacity:.75;font-size:15px;',
      'line-height:1;background:none;border:0;color:#fff;padding:0 2px}',
      '.vta-h .x:hover{opacity:1}',
      '.vta-b{padding:11px 12px;font-size:13px;color:#1e293b;line-height:1.45}',
      '.vta-b .why{display:block;margin-top:5px;font-size:12px;color:#991b1b}',
      '.vta-b a{display:inline-block;margin-top:9px;padding:7px 12px;',
      'border-radius:7px;background:#1a2e58;color:#fff;text-decoration:none;',
      'font-size:12.5px;font-weight:600}',
      '@media (max-width:520px){.vta-wrap{left:14px;right:14px;max-width:none}}'
    ].join('');
    var tag = document.createElement('style');
    tag.id = 'vta-style';
    tag.textContent = css;
    document.head.appendChild(tag);
  }

  function wrap() {
    var el = document.getElementById('vta-wrap');
    if (!el) {
      el = document.createElement('div');
      el.id = 'vta-wrap';
      el.className = 'vta-wrap';
      document.body.appendChild(el);
    }
    return el;
  }

  function card(item) {
    var bad = item.status === 'failed';
    var el = document.createElement('div');
    el.className = 'vta' + (bad ? ' bad' : '');
    el.innerHTML =
      '<div class="vta-h"><span>' + (bad ? '&#9888;' : '&#127916;') + '</span>'
      + '<span>' + esc(item.tool_label) + '</span>'
      + '<button class="x" type="button" aria-label="Dismiss">&times;</button></div>'
      + '<div class="vta-b">' + esc(item.headline)
      + (bad && item.error ? '<span class="why">' + esc(item.error) + '</span>' : '')
      + '<a href="' + esc(item.url) + '">'
      + (bad ? 'See what happened' : 'Watch it and save it') + '</a></div>';
    el.querySelector('.x').addEventListener('click', function () { el.remove(); });
    return el;
  }

  /* Marked as shown before anything is drawn, so a browser that dies between
   * the two loses a notice rather than repeating one for ever. Losing it is
   * recoverable — the edit is still on the tool page under "Recent edits" and
   * on the dashboard card until it is opened; repeating it is not, because
   * people stop reading a popup that keeps coming back. */
  function announce(items) {
    if (!items.length) return;
    styles();
    var box = wrap();
    items.forEach(function (item) { box.appendChild(card(item)); });
    try {
      fetch('/video-tools/api/ready/seen', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids: items.map(function (i) { return i.id; }) })
      });
    } catch (e) { /* the sweep will offer them again */ }
  }

  function poll() {
    fetch(API, { headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data && data.items) announce(data.items); })
      .catch(function () { /* offline, or signed out — try again later */ });
  }

  function start() {
    if (framed()) return;
    poll();
    timer = setInterval(function () {
      // Nothing to announce to a tab nobody is looking at, and a background
      // tab polling every twenty seconds for hours is a request the server
      // answers for no one.
      if (!document.hidden) poll();
    }, EVERY_MS);
    // Coming back to a tab that was hidden while the edit landed is the
    // single most likely way this gets seen, so it asks immediately rather
    // than waiting out the rest of the interval.
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) poll();
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }

  window.HubVideoAlerts = { poll: poll, stop: function () { clearInterval(timer); } };
})();
