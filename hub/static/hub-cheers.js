/* hub-cheers.js — the birthday and work-anniversary popup.
 *
 * Fires once per person per day, on the first Hub page they open. The
 * dashboard block says what is happening this month; this says what is
 * happening today, and it is the only thing here allowed to interrupt
 * anybody. Four rules follow from that, and each of them is a way this
 * becomes the popup people close without reading:
 *
 *   * TODAY ONLY. A popup about a birthday four days away teaches people to
 *     dismiss it unread, and then they dismiss the one that mattered.
 *   * ONCE A DAY. The marker is written when the popup is SHOWN, not when it
 *     is dismissed — a reload must not bring it back. It is per person and
 *     per date, so signing in as somebody else does not inherit the answer,
 *     and tomorrow is a new day rather than a new deploy.
 *   * NEVER IN SOMEBODY ELSE'S IFRAME. Hub pages are framed inside Smart 1
 *     Suite, and a confetti cannon going off in a client-facing panel is not
 *     a feature. Framed means silent.
 *   * NEVER OVER A PAGE THAT IS ASKING FOR A PASSWORD. base.html is the
 *     signed-in shell, so this is already true; it is written down because
 *     the day somebody adds the script to the sign-in layout, nothing will
 *     error.
 *
 * Preview it without waiting for somebody's birthday:
 *     /?cheers=preview        — today's real answer, whatever it is
 *     /?cheers=demo           — a made-up one, clearly marked as a sample
 *     HubCheers.show(payload) — from the console
 */
(function () {
  'use strict';

  var KEY_PREFIX = 's1hub-cheers:';
  var api = (window.HUB_CHEERS_API || '/api/celebrations');

  function framed() {
    try { return window.top !== window.self; } catch (e) { return true; }
  }

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/[&<>"]/g, function (c) {
        return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c];
      });
  }

  function initials(name) {
    var parts = String(name || '').trim().split(/\s+/);
    if (!parts[0]) return '?';
    return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
  }

  /* localStorage is not available in every context — a private window, a
   * browser set to block site data. A popup that throws on read would take
   * the page's other scripts with it, so both halves are guarded and the
   * failure mode is "shown again tomorrow", never "page broken today". */
  function seen(key) {
    try { return window.localStorage.getItem(key) === '1'; } catch (e) { return false; }
  }
  function mark(key) {
    try { window.localStorage.setItem(key, '1'); } catch (e) { /* nothing to do */ }
  }

  function styles() {
    if (document.getElementById('cheers-style')) return;
    var css = [
      '.cheers-back{position:fixed;inset:0;z-index:100000;display:flex;',
      'align-items:center;justify-content:center;padding:20px;',
      'background:rgba(9,17,34,.55);backdrop-filter:blur(2px);',
      'animation:cheers-fade .18s ease}',
      '@keyframes cheers-fade{from{opacity:0}to{opacity:1}}',
      '@keyframes cheers-pop{from{opacity:0;transform:translateY(14px) scale(.96)}',
      'to{opacity:1;transform:none}}',
      '@keyframes cheers-wave{0%,60%,100%{transform:rotate(0)}',
      '20%{transform:rotate(16deg)}40%{transform:rotate(-10deg)}}',
      '.cheers-card{position:relative;width:min(560px,100%);max-height:88vh;overflow:auto;',
      'background:#fff;border-radius:20px;box-shadow:0 30px 80px rgba(6,12,28,.45);',
      'font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;color:#0f172a;',
      'animation:cheers-pop .26s cubic-bezier(.2,.9,.3,1.2)}',
      '.cheers-top{padding:26px 28px 20px;color:#fff;border-radius:20px 20px 0 0;',
      'background:linear-gradient(135deg,#7b2ff7,#f107a3 55%,#f9a826)}',
      '.cheers-top.anni{background:linear-gradient(135deg,#0b3d91,#1f9169 60%,#f0b429)}',
      '.cheers-kicker{font-size:11.5px;font-weight:800;letter-spacing:.14em;',
      'text-transform:uppercase;opacity:.9}',
      '.cheers-title{margin:8px 0 0;font-size:26px;line-height:1.2;font-weight:850}',
      '.cheers-sub{margin:8px 0 0;font-size:13.5px;opacity:.95}',
      '.cheers-hand{display:inline-block;transform-origin:70% 70%;',
      'animation:cheers-wave 2.4s ease-in-out infinite}',
      '.cheers-body{padding:18px 28px 8px}',
      '.cheers-person{display:flex;gap:13px;align-items:center;padding:11px 12px;',
      'border:1px solid #e8ecf3;border-radius:14px;margin-bottom:10px;background:#fcfdff}',
      '.cheers-av{flex:none;width:44px;height:44px;border-radius:50%;display:flex;',
      'align-items:center;justify-content:center;font-weight:800;font-size:15px;color:#fff;',
      'background:linear-gradient(135deg,#7b2ff7,#f107a3)}',
      '.cheers-person.anni .cheers-av{background:linear-gradient(135deg,#0b3d91,#1f9169)}',
      '.cheers-name{font-weight:800;font-size:15px}',
      '.cheers-meta{color:#64748b;font-size:12.5px}',
      '.cheers-badge{margin-left:auto;flex:none;font-size:22px}',
      '.cheers-me{margin:0 28px 12px;padding:12px 14px;border-radius:14px;',
      'background:#fff7e0;border:1px solid #f6dfa0;font-size:13.5px;color:#7a4d05}',
      '.cheers-foot{display:flex;flex-wrap:wrap;gap:10px;align-items:center;',
      'padding:6px 28px 24px}',
      '.cheers-btn{border:0;border-radius:11px;padding:11px 18px;font-weight:800;',
      'font-size:13.5px;cursor:pointer;text-decoration:none;display:inline-block}',
      '.cheers-go{background:#0f172a;color:#fff}',
      '.cheers-go:hover{background:#1e293b}',
      '.cheers-close{background:#eef2f7;color:#334155;margin-left:auto}',
      '.cheers-close:hover{background:#e2e8f0}',
      '.cheers-x{position:absolute;top:12px;right:14px;border:0;background:rgba(255,255,255,.22);',
      'color:#fff;width:30px;height:30px;border-radius:50%;font-size:17px;cursor:pointer;',
      'line-height:1}',
      '.cheers-x:hover{background:rgba(255,255,255,.36)}',
      '.cheers-demo{margin:0 28px 10px;padding:8px 12px;border-radius:10px;',
      'background:#e8f0fe;color:#1e3a8a;font-size:12px;font-weight:700}',
      '.cheers-canvas{position:fixed;inset:0;pointer-events:none;z-index:100001}',
      '@media (prefers-reduced-motion:reduce){.cheers-card,.cheers-back{animation:none}',
      '.cheers-hand{animation:none}}'
    ].join('');
    var tag = document.createElement('style');
    tag.id = 'cheers-style';
    tag.textContent = css;
    document.head.appendChild(tag);
  }

  /* Confetti, in about forty lines and no dependency. Skipped outright when
   * the browser asks for reduced motion — a full-screen particle system is
   * exactly what that setting is for. */
  function confetti() {
    try {
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion:reduce)').matches) return;
    } catch (e) { /* older browser: carry on */ }
    var cv = document.createElement('canvas');
    cv.className = 'cheers-canvas';
    document.body.appendChild(cv);
    var ctx = cv.getContext('2d');
    if (!ctx) { cv.remove(); return; }
    var w = cv.width = window.innerWidth;
    var h = cv.height = window.innerHeight;
    var colors = ['#f107a3', '#7b2ff7', '#f9a826', '#1f9169', '#1d5b96', '#f0b429'];
    var bits = [];
    for (var i = 0; i < 130; i++) {
      bits.push({
        x: Math.random() * w,
        y: -20 - Math.random() * h * 0.6,
        r: 4 + Math.random() * 6,
        c: colors[i % colors.length],
        vy: 1.6 + Math.random() * 2.6,
        vx: -1.1 + Math.random() * 2.2,
        rot: Math.random() * Math.PI,
        vr: -0.12 + Math.random() * 0.24
      });
    }
    var start = Date.now();
    (function frame() {
      var age = Date.now() - start;
      ctx.clearRect(0, 0, w, h);
      for (var i = 0; i < bits.length; i++) {
        var b = bits[i];
        b.x += b.vx; b.y += b.vy; b.rot += b.vr;
        ctx.save();
        ctx.translate(b.x, b.y);
        ctx.rotate(b.rot);
        ctx.globalAlpha = age > 3200 ? Math.max(0, 1 - (age - 3200) / 900) : 1;
        ctx.fillStyle = b.c;
        ctx.fillRect(-b.r / 2, -b.r / 2, b.r, b.r * 1.6);
        ctx.restore();
      }
      if (age < 4100) { window.requestAnimationFrame(frame); } else { cv.remove(); }
    })();
  }

  function personRow(e) {
    var anni = e.kind === 'anniversary';
    var meta = anni
      ? (e.years_label || '') + (e.title ? ' &middot; ' + esc(e.title) : '')
      : esc(e.title || '');
    return '<div class="cheers-person' + (anni ? ' anni' : '') + '">'
      + '<div class="cheers-av">' + esc(initials(e.name)) + '</div>'
      + '<div><div class="cheers-name">' + esc(e.name) + '</div>'
      + '<div class="cheers-meta">' + meta + '</div></div>'
      + '<div class="cheers-badge">' + (anni ? '&#127881;' : '&#127874;') + '</div>'
      + '</div>';
  }

  function headline(bdays, annis, mine) {
    var myBirthday = null, myAnni = null;
    mine.forEach(function (e) {
      if (e.kind === 'birthday' && !myBirthday) myBirthday = e;
      if (e.kind === 'anniversary' && !myAnni) myAnni = e;
    });
    if (myBirthday) {
      return { kicker: 'Happy birthday', anni: false,
               title: 'Happy birthday, ' + esc(myBirthday.first_name || myBirthday.name) + '! <span class="cheers-hand">&#127881;</span>',
               sub: 'The whole Hub says so. Have a good one.' };
    }
    if (myAnni && !bdays.length) {
      var years = myAnni.years || 0;
      return { kicker: 'Work anniversary', anni: true,
               title: years > 0 ? years + ' years at Smart 1 today!' : 'Welcome to Smart 1!',
               sub: 'Thanks for everything you have built here.' };
    }
    var total = bdays.length + annis.length;
    if (bdays.length && annis.length) {
      return { kicker: 'Today at Smart 1', anni: false,
               title: 'Two reasons to celebrate today <span class="cheers-hand">&#128075;</span>',
               sub: 'A birthday and a work anniversary. Drop them a line.' };
    }
    if (bdays.length) {
      return { kicker: 'Today at Smart 1', anni: false,
               title: bdays.length > 1
                 ? (bdays.length + ' birthdays today!')
                 : ('It is ' + esc(bdays[0].name) + '&rsquo;s birthday!'),
               sub: 'Say happy birthday before the day runs out.' };
    }
    return { kicker: 'Today at Smart 1', anni: true,
             title: annis.length > 1
               ? (annis.length + ' work anniversaries today!')
               : (esc(annis[0].name) + ' &mdash; ' + esc(annis[0].years_label || 'work anniversary')),
             sub: total ? 'Worth a note.' : '' };
  }

  function close(back, opener) {
    if (!back) return;
    back.remove();
    document.removeEventListener('keydown', back._esc);
    if (opener && opener.focus) { try { opener.focus(); } catch (e) { /* gone */ } }
  }

  function show(payload, opts) {
    opts = opts || {};
    var bdays = (payload && payload.birthdays) || [];
    var annis = (payload && payload.anniversaries) || [];
    var mine = (payload && payload.mine) || [];
    if (!bdays.length && !annis.length) return false;
    styles();

    var head = headline(bdays, annis, mine);
    var everyone = bdays.concat(annis);
    // Never address the note to the person reading it: "email yourself happy
    // birthday" is the button nobody presses twice.
    var own = mine.map(function (e) { return (e.email || '').toLowerCase(); });
    var emails = everyone.map(function (e) { return (e.email || '').toLowerCase(); })
      .filter(function (m) { return m && own.indexOf(m) === -1; });
    var mailto = emails.length
      ? ('mailto:' + emails.join(',') + '?subject='
         + encodeURIComponent(bdays.length ? 'Happy birthday!' : 'Happy work anniversary!'))
      : '';

    var back = document.createElement('div');
    back.className = 'cheers-back';
    back.setAttribute('role', 'dialog');
    back.setAttribute('aria-modal', 'true');
    back.setAttribute('aria-label', 'Celebrations today');
    back.innerHTML =
      '<div class="cheers-card">'
      + '<div class="cheers-top' + (head.anni ? ' anni' : '') + '">'
      + '<button class="cheers-x" type="button" aria-label="Close">&times;</button>'
      + '<div class="cheers-kicker">' + esc(head.kicker) + '</div>'
      + '<h2 class="cheers-title">' + head.title + '</h2>'
      + (head.sub ? '<p class="cheers-sub">' + esc(head.sub) + '</p>' : '')
      + '</div>'
      + (opts.demo ? '<div class="cheers-demo">Sample &mdash; these are not real dates. '
          + 'This is what the popup looks like.</div>' : '')
      + '<div class="cheers-body">' + everyone.map(personRow).join('') + '</div>'
      + (mine.length && bdays.length > mine.length
          ? '<div class="cheers-me">It is your day too &mdash; enjoy it.</div>' : '')
      + '<div class="cheers-foot">'
      + (mailto ? '<a class="cheers-btn cheers-go" href="' + esc(mailto) + '">'
          + (bdays.length ? 'Send them a note' : 'Send congratulations') + '</a>' : '')
      + '<button class="cheers-btn cheers-close" type="button">Close</button>'
      + '</div></div>';

    var opener = document.activeElement;
    document.body.appendChild(back);
    back._esc = function (ev) { if (ev.key === 'Escape') close(back, opener); };
    document.addEventListener('keydown', back._esc);
    back.addEventListener('click', function (ev) {
      if (ev.target === back) close(back, opener);
    });
    back.querySelector('.cheers-x').onclick = function () { close(back, opener); };
    back.querySelector('.cheers-close').onclick = function () { close(back, opener); };
    var focusable = back.querySelector('.cheers-btn');
    if (focusable && focusable.focus) { try { focusable.focus(); } catch (e) { /* ignore */ } }
    confetti();
    return true;
  }

  var DEMO = {
    birthdays: [{ kind: 'birthday', name: 'Sample Person', first_name: 'Sample',
                  title: 'Senior Campaign Strategist', email: '' }],
    anniversaries: [{ kind: 'anniversary', name: 'Another Person',
                      title: 'Data Specialist', years: 7,
                      years_label: '7th anniversary', email: '' }],
    mine: []
  };

  function start() {
    var params = '';
    try { params = window.location.search || ''; } catch (e) { params = ''; }
    var demo = params.indexOf('cheers=demo') !== -1;
    var preview = params.indexOf('cheers=preview') !== -1;

    if (demo) { show(DEMO, { demo: true }); return; }
    // Framed, and not asked for: silent. A confetti cannon inside somebody
    // else's panel is not a feature.
    if (framed() && !preview) return;

    fetch(api, { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var today = (d && d.today_list) || {};
        var payload = {
          birthdays: today.birthdays || [],
          anniversaries: today.anniversaries || [],
          mine: (d && d.me) || []
        };
        if (!payload.birthdays.length && !payload.anniversaries.length) return;
        var key = KEY_PREFIX + ((d && d.me_name) || 'hub') + ':' + (today.date || '');
        if (!preview && seen(key)) return;
        // Marked when SHOWN, not when dismissed: a reload must not bring it
        // back, and a person who closed it with Escape has still seen it.
        if (!preview) mark(key);
        show(payload, {});
      })
      .catch(function () { /* the popup is never worth an error on the page */ });
  }

  window.HubCheers = { show: show, demo: function () { return show(DEMO, { demo: true }); } };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();
