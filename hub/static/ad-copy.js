/* Ad Copy Request — the form, wherever it is opened from.
 *
 * It used to be a Campaign Change Request with its subject pre-written: four
 * boxes, and a rep retyping the client, the campaign, the order number and the
 * media partner out of the record on the screen behind them. Every one of
 * those is on the client's own insertion orders and the seller and the
 * confirmation address are on the signed-in account, so the form now opens on
 * what the Hub already knows and asks only for what nobody else can answer.
 *
 * The controls are /knack-form.js, shared with the web ticket and campaign
 * support forms — three objects asking the same question, and a second copy of
 * that renderer is the failure CLAUDE.md names twice already.
 *
 * Everything the form opens on is decided server-side, in hub/ad_copy.py, and
 * this draws what comes back: the prefilled values, the datalists on the
 * fields that can offer one, and the note saying what could not be filled in.
 * There is deliberately no JavaScript mirror of those rules — target areas and
 * the creative classifier each carry one and each needs a test proving the
 * halves still agree, and that cost is paid twice already.
 *
 * Two entry points, matching /campaign-request.js:
 *
 *   AdCopyRequest.open({client, user, onsaved})   — a caller with the client
 *   AdCopyRequest.pick({user, title, onsaved})    — look the client up first
 */
window.AdCopyRequest = (function () {
  'use strict';

  // Read at call time, not at load: the drawer is a separate script, and a
  // page that loaded them in the other order would otherwise capture
  // undefined here and fail with no clue why.
  var KF = function () { return window.KnackForm; };
  var CTX = { prefix: 'ac-' };

  function esc(s) { return KF().esc(s); }

  // What the form could not fill in, and why. Its own color and not an
  // error: "we could not look" and "there is nothing to look at" are both
  // worth saying, and neither one stops the form being sent.
  function notesHtml(notes) {
    if (!notes || !notes.length) return '';
    return '<div style="background:#f8fafc;border:1px solid var(--line,#e2e8f0);border-radius:8px;' +
      'padding:10px 12px;margin-bottom:14px;font-size:12px;color:#475569">' +
      '<b style="color:#334155">What we could not fill in</b><ul style="margin:6px 0 0 16px;padding:0">' +
      notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul></div>';
  }

  // Picking a campaign fills the order number and the media partner from the
  // insertion order that campaign is on — and only where that IO says which,
  // never a guess between two. It writes into the boxes rather than redrawing
  // them: a container that re-renders while somebody is typing into it eats
  // what they typed, the trap the Smart 1 Ads target-area rows had.
  function link(options, say) {
    var camp = document.getElementById(CTX.prefix + 'campaign');
    if (!camp) return;
    camp.addEventListener('change', function () {
      var want = String(camp.value || '').trim().toLowerCase();
      if (!want) { say(''); return; }
      var hits = (options.orders || []).filter(function (o) {
        return String(o.campaign || '').trim().toLowerCase() === want;
      });
      var ambiguous = [];
      [['order_number', 'io'], ['media_partner', 'partner']].forEach(function (pair) {
        var el = document.getElementById(CTX.prefix + pair[0]);
        if (!el || el.disabled) return;
        var vals = hits.map(function (h) { return String(h[pair[1]] || '').trim(); })
          .filter(function (x, i, all) { return x && all.indexOf(x) === i; });
        if (vals.length === 1) el.value = vals[0];
        else if (vals.length > 1) ambiguous.push(pair[0] === 'order_number'
          ? vals.length + ' insertion orders' : vals.length + ' media partners');
      });
      // Two orders on one campaign is a real answer and not one this can pick
      // between — say so rather than filling in the first.
      say(ambiguous.length
        ? 'That campaign is on ' + ambiguous.join(' and ') + ' — pick one below.'
        : '');
    });
  }

  // ---------------------------------------------------------------- the form
  function open(opts) {
    opts = opts || {};
    if (!KF()) {
      alert('The request form failed to load. Reload the page and try again.');
      return;
    }
    var name = String(opts.client || '');
    var m = KF().modal('adCopyModal', 'Ad Copy Request — ' + esc(name),
      '<div class="empty">Reading the ad copy fields from Knack… <span class="spin"></span></div>',
      660, true);
    var body = m.querySelector('[data-body]');
    var foot = m.querySelector('[data-foot]');

    fetch('/api/client/ad-copy/fields?client=' + encodeURIComponent(name))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.configured) {
          body.innerHTML = '<div class="empty">Knack API not connected — set KNACK_APP_ID and KNACK_API_KEY, then redeploy.</div>';
          return;
        }
        if (d.error) { body.innerHTML = '<div class="empty">' + esc(d.error) + '</div>'; return; }

        var fields = d.fields || [];
        var options = d.options || {};

        body.innerHTML = notesHtml(d.notes) +
          '<div id="acLink" style="font-size:12px;color:#b45309;margin-bottom:10px"></div>' +
          KF().form(fields, d.values || {}, null, CTX) +
          '<div id="acTriage" style="margin-top:4px"></div>';
        KF().wire();
        var linkNote = document.getElementById('acLink');
        link(options, function (msg) { if (linkNote) linkNote.textContent = msg; });
        // The third form knack-form.js draws, and the one that went without
        // this control. Same renderer, same rules: into the empty choice
        // fields only, nothing written until it is kept, and drawn only
        // where this object publishes a choice field to offer into.
        //
        // Two text keys rather than one. What is being asked for is split
        // across "Change for What?" and "Is there Something Else we need to
        // know?", and the deadline or the URL change is as likely to be
        // written in the second as the first -- reading one of them would
        // miss the half the answer was in.
        KF().triageButton('acTriage', fields, CTX, 'adcopy',
                          ['change_for', 'anything_else']);

        foot.innerHTML = '<span id="acMsg" class="muted" style="font-size:12px"></span>' +
          '<a class="btn-primary" id="acSend" style="padding:9px 18px;font-size:13px;cursor:pointer;text-decoration:none">Send to Smart 1 Team</a>';

        document.getElementById('acSend').onclick = function () {
          var msg = document.getElementById('acMsg');
          var values = {};
          fields.forEach(function (f) {
            if (f.writable === false) return;
            var v = KF().read(f, CTX);
            if (v !== '' && !(Array.isArray(v) && !v.length)) values[f.key] = v;
          });
          if (!values.client) { msg.textContent = 'Client Name is required.'; return; }
          if (!values.change_for) { msg.textContent = 'Change for What? is required.'; return; }
          msg.textContent = 'Sending…';
          fetch('/api/client/ad-copy', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ client: d.client || name, values: values })
          }).then(function (r) { return r.json(); }).then(function (r) {
            if (r.error) { msg.textContent = r.error; return; }
            if (r.rejected && r.rejected.length) {
              // Created, but not as asked. The rep sees exactly what is
              // missing instead of hearing it from the campaign team later.
              body.innerHTML = KF().refusedHtml(r.rejected) +
                '<div>Ad copy request created with ' + (r.written || []).length + ' fields. ' +
                'Fix the refused ones in Knack, or tell whoever renamed them.</div>';
              foot.innerHTML = '<a class="btn-primary" id="acDone" style="padding:9px 18px;font-size:13px;cursor:pointer;text-decoration:none">Close</a>';
              document.getElementById('acDone').onclick = function () { m.remove(); };
              if (opts.onsaved) opts.onsaved();
              return;
            }
            m.remove();
            if (opts.onsaved) opts.onsaved();
          }).catch(function () { msg.textContent = 'Could not reach the Hub. Nothing was sent.'; });
        };
      }).catch(function () {
        body.innerHTML = '<div class="empty">Could not load the ad copy form. Nothing was sent.</div>';
      });
  }

  // ------------------------------------------------------- client lookup first
  // The same lookup /campaign-request.js offers, for a page with no client on
  // screen. A search of real clients and never a text box, for the reason
  // hub/client_key.py gives at length: a typed name that matches nothing files
  // the request under a client nothing joins to and still reads as sent.
  function pick(opts) {
    opts = opts || {};
    if (!KF()) {
      alert('The request form failed to load. Reload the page and try again.');
      return;
    }
    var m = KF().modal('adCopyPickModal', esc(opts.title || 'Ad Copy Request'),
      '<input id="acPickQ" placeholder="Client name or domain…" autocomplete="off" ' +
      'style="width:100%;padding:10px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:14px inherit">' +
      '<div id="acPickList" style="margin-top:10px;max-height:46vh;overflow-y:auto"></div>', 520, false);

    var q = document.getElementById('acPickQ');
    var list = document.getElementById('acPickList');
    var timer = null, seq = 0;

    function choose(clientName) {
      m.remove();
      open({ client: clientName, user: opts.user, onsaved: opts.onsaved });
    }

    function draw(rows, note) {
      if (note) { list.innerHTML = '<div class="empty" style="padding:14px">' + esc(note) + '</div>'; return; }
      if (!rows.length) {
        // Not a dead end: a client not yet in the registry still needs ad copy.
        list.innerHTML = '<div class="empty" style="padding:12px 14px">No match in the client list.</div>' +
          '<a class="open-link" id="acPickAny" style="display:block;padding:10px 14px;cursor:pointer">' +
          'Use “' + esc(q.value.trim()) + '” anyway →</a>';
        var any = document.getElementById('acPickAny');
        if (any) any.onclick = function () { choose(q.value.trim()); };
        return;
      }
      list.innerHTML = rows.map(function (r, i) {
        return '<a class="ac-pick-row" data-i="' + i + '" style="display:flex;gap:10px;align-items:baseline;' +
          'padding:9px 12px;border-radius:8px;cursor:pointer;text-decoration:none;color:inherit">' +
          '<b style="font-size:13.5px">' + esc(r.name) + '</b>' +
          '<span class="muted" style="font-size:12px">' + esc(r.domain || '') + '</span></a>';
      }).join('');
      Array.prototype.forEach.call(list.querySelectorAll('.ac-pick-row'), function (el) {
        el.onmouseenter = function () { el.style.background = '#f1f5f9'; };
        el.onmouseleave = function () { el.style.background = ''; };
        el.onclick = function () { choose(rows[Number(el.dataset.i)].name); };
      });
    }

    function search() {
      var term = q.value.trim();
      var mine = ++seq;
      fetch('/api/clients/search?q=' + encodeURIComponent(term) + '&limit=12')
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (mine !== seq) return;                      // a later keystroke won
          draw((d && d.clients) || []);
        })
        .catch(function () { if (mine === seq) draw([], 'Client list unavailable.'); });
    }

    q.oninput = function () { clearTimeout(timer); timer = setTimeout(search, 180); };
    q.onkeydown = function (e) {
      if (e.key !== 'Enter') return;
      e.preventDefault();
      var first = list.querySelector('.ac-pick-row');
      if (first) first.click(); else if (q.value.trim()) choose(q.value.trim());
    };
    q.focus();
    search();
  }

  return { open: open, pick: pick };
})();
