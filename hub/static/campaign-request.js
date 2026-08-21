/* Campaign Change / Campaign Support / Ad Copy request form.
 *
 * This modal lived inside client360.html, where it could only be opened from a
 * client record that was already on screen. The dashboard needs the same three
 * forms without that context, and the wrong way to get there is a second copy:
 * this codebase has already paid twice for a fix that had to be found and made
 * in several places (see the image-resize and PEXELS_API notes in CLAUDE.md).
 * So the form moved here, unchanged, and Client 360 now calls into it.
 *
 * Two entry points:
 *
 *   CampaignRequest.open({kind, client, groups, user, subject})
 *       The form itself, for a caller that already knows the client — that is
 *       Client 360, which passes the groups it searched.
 *
 *   CampaignRequest.pick({kind, user, subject, title})
 *       Client lookup first, then the form. That is the dashboard, which has
 *       no client on screen. The lookup loads the same /api/c360 record Client
 *       360 does, so the campaign/IO dropdown is populated from the client's
 *       real insertion orders rather than left as a free-text box.
 *
 * `kind` is what Knack sees: 'change' writes to the Campaign Change object,
 * 'support' to the Campaign Support object. Ad Copy is a change request with a
 * pre-filled subject, because that is the form the team asked for — not a
 * third object, which would be a Knack schema change nobody requested.
 */
window.CampaignRequest = (function () {
  'use strict';

  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  };

  var LABEL = { change: 'Campaign Change Request', support: 'Campaign Support Request' };

  function shell(id, titleHtml, bodyHtml, width) {
    var old = document.getElementById(id);
    if (old) old.remove();
    var m = document.createElement('div');
    m.id = id;
    m.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:99999;' +
      'display:flex;align-items:center;justify-content:center;padding:16px';
    m.innerHTML = '<div style="background:#fff;border-radius:14px;width:' + (width || 620) +
      'px;max-width:100%;max-height:92vh;display:flex;flex-direction:column">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;padding:13px 18px;' +
      'border-bottom:1px solid var(--line,#e2e8f0)">' +
      '<b style="color:var(--navy,#0f2545)">' + titleHtml + '</b>' +
      '<button data-close="1" style="border:0;background:none;font-size:22px;cursor:pointer;color:#64748b">&times;</button></div>' +
      '<div data-body="1" style="padding:16px 18px;overflow-y:auto">' + bodyHtml + '</div></div>';
    document.body.appendChild(m);
    m.onclick = function (e) { if (e.target === m) m.remove(); };
    m.querySelector('[data-close]').onclick = function () { m.remove(); };
    return m;
  }

  // ---------------------------------------------------------------- the form
  function open(opts) {
    opts = opts || {};
    var kind = opts.kind === 'support' ? 'support' : 'change';
    var name = String(opts.client || '');
    var user_name = String(opts.user || '');
    var label = opts.label || LABEL[kind];

    var m = shell('campModal', esc(label) + ' — ' + esc(name),
      '<div class="empty">Reading Knack fields… <span class="spin"></span></div>');
    var body = m.querySelector('[data-body]');

    Promise.all([
      fetch('/api/knack/campaign-fields?kind=' + kind).then(function (r) { return r.json(); }),
      fetch('/api/knack/people').then(function (r) { return r.json(); }).catch(function () { return { names: [] }; })
    ]).then(function (res) {
      var d = res[0], people = res[1];
      if (!d.configured) {
        body.innerHTML = '<div class="empty">Knack API not connected — set KNACK_APP_ID and KNACK_API_KEY, then redeploy.</div>';
        return;
      }
      if (d.error) { body.innerHTML = '<div class="empty">' + esc(d.error) + '</div>'; return; }

      var roleNames = { title: 'Subject', description: 'Details', client: 'Client name',
                        campaign: 'Campaign / product', io: 'IO number', requested_by: 'Requested by' };
      var mapRows = Object.keys(roleNames).map(function (role) {
        var slot = (d.map || {})[role];
        return '<tr><td style="padding:4px 10px 4px 0;color:#64748b;font-size:12px">' + roleNames[role] + '</td>' +
          '<td style="padding:4px 0;font-size:12px">' + (slot
            ? '<b>' + esc(slot.label) + '</b> <span class="muted">(' + esc(slot.field) + ')</span>'
            : '<span style="color:#b45309">no matching field — will be skipped</span>') + '</td></tr>';
      }).join('');

      // The caller's search results, so a name that matched several clients
      // stays switchable inside the form.
      var allGroups = (opts.groups && opts.groups.length)
        ? opts.groups
        : [{ client: name, products: opts.products || [] }];

      var clientSel = allGroups.length > 1
        ? '<select id="campClient" style="padding:9px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:13px inherit">' +
          allGroups.map(function (g) {
            return '<option value="' + esc(g.client) + '"' + (g.client === name ? ' selected' : '') + '>' + esc(g.client) + '</option>';
          }).join('') + '</select>'
        : '<input id="campClient" value="' + esc(name) + '" readonly style="padding:9px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:13px inherit;background:#f8fafc;color:#475569">';

      function prodOptions(clientName) {
        var grp = allGroups.filter(function (g) { return g.client === clientName; })[0] || allGroups[0];
        var prods = (grp.products || []).slice().sort(function (a, b) {
          return (parseInt(b.io, 10) || 0) - (parseInt(a.io, 10) || 0);   // newest IO first
        });
        return '<option value="">Which campaign / IO?</option>' + prods.map(function (p) {
          var lbl = esc(clientName) + (p.io ? ' — IO ' + esc(p.io) : '') + ': ' + esc(p.product || '') +
            (String(p.status || '').toLowerCase() === 'live' ? ' (live)' : '');
          return '<option value="' + esc(p.product || '') + '" data-io="' + esc(p.io || '') + '">' + lbl + '</option>';
        }).join('');
      }

      var names = (people && people.names) || [];
      var meFirst = names.indexOf(user_name) !== -1 ? user_name : '';
      var reqOpts = ['<option value="">Requested by…</option>'].concat(names.map(function (n) {
        return '<option value="' + esc(n) + '"' + (n === meFirst ? ' selected' : '') + '>' + esc(n) + '</option>';
      }));
      if (!names.length) reqOpts.push('<option value="' + esc(user_name) + '" selected>' + esc(user_name) + '</option>');

      var placeholder = opts.placeholder || (kind === 'change'
        ? 'Describe the change: budget, targeting, dates, creative…'
        : 'Describe the issue or question for the campaign team…');

      body.innerHTML =
        '<div style="background:#f8fafc;border:1px solid var(--line,#e2e8f0);border-radius:10px;padding:12px 14px;margin-bottom:14px">' +
          '<div style="font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#64748b;margin-bottom:6px">' +
          'Writing to Knack ' + esc(d.object) + ' — these fields will be used</div>' +
          '<table>' + mapRows + '</table></div>' +
        '<div style="display:flex;flex-direction:column;gap:10px">' +
          clientSel +
          '<select id="campProd" style="padding:9px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:13px inherit">' + prodOptions(name) + '</select>' +
          '<select id="campReq" style="padding:9px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:13px inherit">' + reqOpts.join('') + '</select>' +
          '<input id="campSubject" value="' + esc(opts.subject || '') + '" placeholder="Subject — what do you need?" style="padding:9px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:13px inherit">' +
          '<textarea id="campDesc" rows="5" placeholder="' + esc(placeholder) + '" style="padding:9px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:13px inherit"></textarea>' +
          '<div style="display:flex;justify-content:flex-end;gap:10px;align-items:center">' +
            '<span id="campMsg" class="muted" style="font-size:12px"></span>' +
            '<a class="btn-primary" id="campSend" style="padding:9px 18px;font-size:13px;cursor:pointer;text-decoration:none">Send to Smart 1 Team</a>' +
          '</div></div>';

      var clientEl = document.getElementById('campClient');
      if (clientEl.tagName === 'SELECT') clientEl.onchange = function () {
        document.getElementById('campProd').innerHTML = prodOptions(clientEl.value);
      };
      document.getElementById('campSend').onclick = function () {
        var subject = document.getElementById('campSubject').value.trim();
        var msg = document.getElementById('campMsg');
        if (!subject) { msg.textContent = 'Subject is required.'; return; }
        var sel = document.getElementById('campProd');
        var opt = sel.options[sel.selectedIndex] || {};
        msg.textContent = 'Sending…';
        fetch('/api/client/campaign-request', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            kind: kind, client: clientEl.value, campaign: sel.value,
            io: (opt.dataset && opt.dataset.io) || '', subject: subject,
            requested_by: document.getElementById('campReq').value,
            description: document.getElementById('campDesc').value.trim()
          })
        }).then(function (r) { return r.json(); }).then(function (r) {
          if (r.error) { msg.textContent = r.error; return; }
          msg.textContent = 'Sent ✓';
          setTimeout(function () { m.remove(); }, 900);
        }).catch(function () { msg.textContent = 'Could not reach the Hub. Nothing was sent.'; });
      };
    }).catch(function () {
      body.innerHTML = '<div class="empty">Could not load the request form. Nothing was sent.</div>';
    });
  }

  // ------------------------------------------------------- client lookup first
  function pick(opts) {
    opts = opts || {};
    var title = opts.title || LABEL[opts.kind === 'support' ? 'support' : 'change'];

    var m = shell('campPickModal', esc(title),
      '<input id="campPickQ" placeholder="Client name or domain…" autocomplete="off" ' +
      'style="width:100%;padding:10px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:14px inherit">' +
      '<div id="campPickList" style="margin-top:10px;max-height:46vh;overflow-y:auto"></div>', 520);

    var q = document.getElementById('campPickQ');
    var list = document.getElementById('campPickList');
    var timer = null, seq = 0;

    function choose(clientName) {
      list.innerHTML = '<div class="empty" style="padding:14px">Loading ' + esc(clientName) + '… <span class="spin"></span></div>';
      // Same record Client 360 draws, so the campaign/IO list is the client's
      // real insertion orders. If it cannot be read the form still opens — with
      // an empty product list and a note, rather than not at all.
      fetch('/api/c360?q=' + encodeURIComponent(clientName))
        .then(function (r) { return r.json(); })
        .catch(function () { return { groups: [] }; })
        .then(function (d) {
          var groups = (d && d.groups) || [];
          var exact = groups.filter(function (g) { return g.client === clientName; });
          m.remove();
          open({
            kind: opts.kind, client: (exact[0] || groups[0] || {}).client || clientName,
            groups: exact.length ? exact : groups, user: opts.user,
            subject: opts.subject, label: opts.label, placeholder: opts.placeholder
          });
        });
    }

    function draw(rows, note) {
      if (note) { list.innerHTML = '<div class="empty" style="padding:14px">' + esc(note) + '</div>'; return; }
      if (!rows.length) {
        // Not a dead end: a prospect or a client not yet in the registry still
        // needs to be able to raise a request.
        list.innerHTML = '<div class="empty" style="padding:12px 14px">No match in the client list.</div>' +
          '<a class="open-link" id="campPickAny" style="display:block;padding:10px 14px;cursor:pointer">' +
          'Use “' + esc(q.value.trim()) + '” anyway →</a>';
        var any = document.getElementById('campPickAny');
        if (any) any.onclick = function () { choose(q.value.trim()); };
        return;
      }
      list.innerHTML = rows.map(function (r, i) {
        return '<a class="camp-pick-row" data-i="' + i + '" style="display:flex;gap:10px;align-items:baseline;' +
          'padding:9px 12px;border-radius:8px;cursor:pointer;text-decoration:none;color:inherit">' +
          '<b style="font-size:13.5px">' + esc(r.name) + '</b>' +
          '<span class="muted" style="font-size:12px">' + esc(r.domain || '') + '</span></a>';
      }).join('');
      Array.prototype.forEach.call(list.querySelectorAll('.camp-pick-row'), function (el) {
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
      var first = list.querySelector('.camp-pick-row');
      if (first) first.click(); else if (q.value.trim()) choose(q.value.trim());
    };
    q.focus();
    search();                                            // house clients up front
  }

  return { open: open, pick: pick };
})();
