/* hub-job-notify.js — "it's done, come back," wherever you are.
 *
 * A paint animation, a Vox explainer, or (once a tool registers into
 * hub/job_notify.py) a Commercial Builder render is minutes of headless
 * work, exactly like HeyGen and Runway. Nobody sits on the page that
 * started it waiting; they go back to whatever else they were doing, and
 * until now the only way to learn a render had finished was to remember to
 * go back and look. This is the reminder, wherever "back" turns out to be.
 *
 * Modeled on hub-qa-nudge.js deliberately — same corner-card shape, same
 * "shown once" marker, same iframe rule — because two interruption layers
 * behaving differently is two things for a reader to learn. What differs:
 *
 *   * IT KEEPS ASKING WHILE SOMETHING IS RUNNING. hub-qa-nudge.js asks once
 *     per page load; a render can still be going when somebody navigates
 *     away, so this polls every JOB_POLL_MS for as long as the answer says
 *     something of theirs is still queued or rendering, and stops asking
 *     the moment nothing is.
 *   * ONE CARD PER FINISHED JOB, NOT ONE SUMMARY. Two different renders
 *     finishing five minutes apart are two things to come back to, and
 *     folding them into one card is how the second gets missed.
 *   * MARKED PER JOB, NOT PER DAY. The key is the job's own id — once shown,
 *     never again, on any page, on this browser.
 *   * NEVER IN SOMEBODY ELSE'S IFRAME. Hub pages are framed inside Smart 1
 *     Suite; a staff render notice inside a client-facing panel is an
 *     internal note in front of a client.
 *
 * Preview it without waiting for a real job:
 *     ?jobnotify=demo — a made-up finished job, clearly marked as a sample
 */
(function () {
  'use strict';

  var KEY_PREFIX = 's1hub-job-seen:';
  var API = '/api/background-jobs/mine';
  var JOB_POLL_MS = 20000;
  var RUNNING = { queued: 1, rendering: 1, pending: 1, processing: 1 };

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
   * browser set to block site data. A notifier that throws on read would
   * take the page's other scripts with it, so both halves are guarded and
   * the failure mode is "shown again next time", never "page broken now". */
  function seen(key) {
    try { return window.localStorage.getItem(key) === '1'; } catch (e) { return false; }
  }
  function mark(key) {
    try { window.localStorage.setItem(key, '1'); } catch (e) { /* nothing to do */ }
  }

  function styles() {
    if (document.getElementById('jobnotify-style')) { return; }
    var css = [
      '.jobnotify-stack{position:fixed;right:18px;bottom:18px;z-index:99991;',
      'display:flex;flex-direction:column-reverse;gap:10px;',
      'width:min(360px,calc(100vw - 36px))}',
      '.jobnotify{background:#fff;border:1px solid #e2e8f0;',
      'border-left:4px solid #1769AA;border-radius:14px;',
      'box-shadow:0 14px 40px rgba(15,23,42,.18);',
      'font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#0f172a;',
      'animation:jobnotify-in .22s cubic-bezier(.2,.9,.3,1.2)}',
      '.jobnotify.jobnotify-failed{border-left-color:#b91c1c}',
      '@keyframes jobnotify-in{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}',
      '.jobnotify-h{display:flex;align-items:center;gap:9px;padding:13px 14px 6px}',
      '.jobnotify-h b{font-size:14.5px;font-weight:800}',
      '.jobnotify-x{margin-left:auto;border:0;background:none;font-size:20px;line-height:1;',
      'color:#94a3b8;cursor:pointer;padding:0 2px}',
      '.jobnotify-x:hover{color:#334155}',
      '.jobnotify-b{padding:0 14px 4px;font-size:13.5px;color:#334155}',
      '.jobnotify-f{display:flex;gap:9px;align-items:center;padding:11px 14px 14px}',
      '.jobnotify-go{background:#1769AA;color:#fff;border:0;border-radius:10px;',
      'padding:9px 15px;font:700 13px system-ui,-apple-system,"Segoe UI",sans-serif;',
      'text-decoration:none;display:inline-block}',
      '.jobnotify-go:hover{background:#12558a;color:#fff}',
      '.jobnotify.jobnotify-failed .jobnotify-go{background:#b91c1c}',
      '.jobnotify.jobnotify-failed .jobnotify-go:hover{background:#991b1b}',
      '.jobnotify-later{border:0;background:none;color:#64748b;font:600 13px system-ui,',
      '-apple-system,"Segoe UI",sans-serif;cursor:pointer;margin-left:auto}',
      '.jobnotify-demo{margin:0 14px 6px;padding:7px 11px;border-radius:9px;',
      'background:#e8f0fe;color:#1e3a8a;font-size:12px;font-weight:700}',
      '@media (prefers-reduced-motion:reduce){.jobnotify{animation:none}}'
    ].join('');
    var tag = document.createElement('style');
    tag.id = 'jobnotify-style';
    tag.textContent = css;
    document.head.appendChild(tag);
  }

  function stack() {
    var el = document.getElementById('jobnotify-stack');
    if (!el) {
      el = document.createElement('div');
      el.id = 'jobnotify-stack';
      el.className = 'jobnotify-stack';
      document.body.appendChild(el);
    }
    return el;
  }

  function close(card) {
    if (card) { card.remove(); }
  }

  function show(job, opts) {
    opts = opts || {};
    styles();
    var failed = job.status === 'failed';
    var card = document.createElement('div');
    card.className = 'jobnotify' + (failed ? ' jobnotify-failed' : '');
    card.setAttribute('role', 'status');
    var label = job.label || job.tool || 'Your render';
    var body = failed
      ? 'This one failed. Open it to see what happened.'
      : "It's ready.";
    card.innerHTML =
      '<div class="jobnotify-h"><span aria-hidden="true">'
      + (failed ? '&#9888;&#65039;' : '&#127916;') + '</span>'
      + '<b>' + esc(label) + '</b>'
      + '<button class="jobnotify-x" type="button" aria-label="Close">&times;</button></div>'
      + (opts.demo ? '<div class="jobnotify-demo">Sample &mdash; this is what the '
          + 'notice looks like.</div>' : '')
      + '<div class="jobnotify-b">' + esc(body) + '</div>'
      + '<div class="jobnotify-f">'
      + '<a class="jobnotify-go" href="' + esc(job.return_url || '#') + '">'
      + (failed ? 'Open it' : 'View') + '</a>'
      + '<button class="jobnotify-later" type="button">Dismiss</button>'
      + '</div>';

    stack().appendChild(card);
    card.querySelector('.jobnotify-x').onclick = function () { close(card); };
    card.querySelector('.jobnotify-later').onclick = function () { close(card); };
    return true;
  }

  var DEMO = {
    id: 'demo',
    tool: 'paint-animation',
    label: 'Paint animation — "Smart 1 Marketing gets you found"',
    status: 'done',
    return_url: '/tools/paint-animation/'
  };

  var timer = null;

  function scheduleNext(active) {
    if (timer) { clearTimeout(timer); timer = null; }
    if (active) { timer = setTimeout(poll, JOB_POLL_MS); }
  }

  // Root-relative and same-origin only ("/x", never "//x" or "https://x").
  // hub/job_notify.py's poll_url is built from constants the registering
  // tool controls, not from anything a browser sent, so this is defense in
  // depth rather than a response to a real attacker-controlled value.
  function isSafePollUrl(u) {
    return typeof u === 'string' && u.charAt(0) === '/' && u.charAt(1) !== '/';
  }

  // A job the summary just reported as still running may have finished in
  // the seconds since — advancing it here, by fetching the *tool's own*
  // status route, is what lets this follow somebody who has navigated away
  // from the tool that started it. That route already writes the finished
  // URL onto its own row on any request ("closing the tab does not lose a
  // render"); fetching it from here gets that write-through for free,
  // without hub-job-notify.js needing to know anything HyperFrames-specific.
  function advance(job) {
    if (!RUNNING[job.status] || !isSafePollUrl(job.poll_url)) {
      return Promise.resolve(job);
    }
    return fetch(job.poll_url, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (pd) {
        var status = pd && pd.job && pd.job.status;
        if (status) { job.status = status; }
        return job;
      })
      .catch(function () { return job; }); // a poll that failed leaves the
      // job exactly as the summary reported it — tried again next round.
  }

  function poll() {
    fetch(API, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        // "We could not read the store" must not read as "nothing is
        // running" — but it is also not worth interrupting anybody about,
        // so it is silent here and simply tried again on the next poll.
        if (!d || d.measured === false) { scheduleNext(false); return; }
        return Promise.all((d.jobs || []).map(advance));
      })
      .then(function (jobs) {
        if (!jobs) { return; }
        var stillRunning = false;
        jobs.forEach(function (job) {
          if (RUNNING[job.status]) { stillRunning = true; return; }
          var key = KEY_PREFIX + job.id;
          if (seen(key)) { return; }
          // Marked when SHOWN, not when dismissed: a reload must not bring
          // it back, and somebody who closed it has still seen it.
          mark(key);
          show(job, {});
        });
        scheduleNext(stillRunning);
      })
      .catch(function () { scheduleNext(false); });
  }

  function start() {
    var params = '';
    try { params = window.location.search || ''; } catch (e) { params = ''; }
    if (params.indexOf('jobnotify=demo') !== -1) { show(DEMO, { demo: true }); return; }

    // Framed, and not asked for: silent. A staff render notice inside a
    // client-facing panel is an internal note in front of a client.
    if (framed()) { return; }

    poll();
  }

  window.HubJobNotify = { show: show, demo: function () { return show(DEMO, { demo: true }); } };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
