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
 *
 * What the support form draws is the whole of object_121 — the insertion
 * order, the due date, the kind of support, the pixel URL, the timeline, the
 * rush and its reason, who to notify, the notes — because for as long as it
 * sent four boxes the rest of that record arrived blank and the campaign team
 * filled it in by asking. Every option on it comes off the LIVE object
 * (/api/knack/campaign-fields): the ids are ours, the choices are Knack's, and
 * a form that guesses a choice writes a value Knack refuses — which loses the
 * whole request, not the one field. The controls themselves are shared with
 * the web ticket form, in /knack-form.js.
 */
window.CampaignRequest = (function () {
  'use strict';

  var KF = function () { return window.KnackForm; };
  var CTX = { prefix: 'cs-' };
  var INPUT = 'padding:9px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:13px inherit;width:100%;box-sizing:border-box';

  function esc(s) { return KF().esc(s); }

  var LABEL = { change: 'Campaign Change Request', support: 'Campaign Support Request' };

  // The fields the header of the form already asks for, so they are not asked
  // twice. Subject is a box of its own on both kinds; on a support request it
  // leads the issue, because object_121 publishes no subject field.
  var SKIP = { change: ['title'], support: [] };

  function shell(id, titleHtml, bodyHtml, width) {
    return KF().modal(id, titleHtml, bodyHtml, width || 640, true);
  }

  function today() {
    var d = new Date();
    return ('0' + (d.getMonth() + 1)).slice(-2) + '/' +
           ('0' + d.getDate()).slice(-2) + '/' + d.getFullYear();
  }

  // Exactly one match or none. A near match is not a match — attributing one
  // company's request to another is the worst outcome available here, the
  // rule hub/client_key.py gives at length.
  function connectionId(field, name) {
    var want = String(name || '').trim().toLowerCase();
    if (!want) return '';
    var hit = (field.choices || []).filter(function (c) {
      return String(c.label).trim().toLowerCase() === want;
    });
    return hit.length === 1 ? hit[0].id : '';
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
    var foot = m.querySelector('[data-foot]');

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

      var fields = (d.fields || []).slice();

      // The caller's search results, so a name that matched several clients
      // stays switchable inside the form.
      var allGroups = (opts.groups && opts.groups.length)
        ? opts.groups
        : [{ client: name, products: opts.products || [] }];

      var clientSel = allGroups.length > 1
        ? '<select id="campClient" style="' + INPUT + '">' +
          allGroups.map(function (g) {
            return '<option value="' + esc(g.client) + '"' + (g.client === name ? ' selected' : '') + '>' + esc(g.client) + '</option>';
          }).join('') + '</select>'
        : '<input id="campClient" value="' + esc(name) + '" readonly style="' + INPUT +
          ';background:#f8fafc;color:#475569">';

      function productsOf(clientName) {
        var grp = allGroups.filter(function (g) { return g.client === clientName; })[0] || allGroups[0] || {};
        return (grp.products || []).slice().sort(function (a, b) {
          return (parseInt(b.io, 10) || 0) - (parseInt(a.io, 10) || 0);   // newest IO first
        });
      }

      function prodOptions(clientName) {
        return '<option value="">Which campaign / IO?</option>' + productsOf(clientName).map(function (p) {
          var lbl = esc(clientName) + (p.io ? ' — IO ' + esc(p.io) : '') + ': ' + esc(p.product || '') +
            (String(p.status || '').toLowerCase() === 'live' ? ' (live)' : '');
          return '<option value="' + esc(p.product || '') + '" data-io="' + esc(p.io || '') + '">' + lbl + '</option>';
        }).join('');
      }

      // What the Hub already knows about this client, offered on the fields
      // Knack publishes no choices for. A datalist suggests and never
      // restricts — the IO that needs help is not always one we hold a row
      // for, and a picker that refuses an unknown number is a form somebody
      // gives up on.
      function suggest(clientName) {
        var prods = productsOf(clientName);
        var byKey = { campaign: [], io_number: [], product: [], io_product: [] };
        prods.forEach(function (p) {
          if (p.product) {
            byKey.campaign.push(p.product);
            byKey.product.push(p.product);
            byKey.io_product.push(p.product);
          }
          if (p.io) byKey.io_number.push(String(p.io));
        });
        fields.forEach(function (f) {
          var list = byKey[f.key];
          if (!list || !list.length) return;
          f.suggest = list.filter(function (x, i) { return list.indexOf(x) === i; });
        });
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

      function prefillFor(clientName) {
        var pre = {};
        fields.forEach(function (f) {
          if (f.key === 'client') {
            pre.client = f.control === 'connection'
              ? connectionId(f, clientName) : clientName;
          }
          if (f.key === 'submitted_date' && f.control === 'date') pre.submitted_date = today();
        });
        return pre;
      }

      suggest(name);
      var prefill = prefillFor(name);
      // The description is the one field the placeholder belongs on: it is
      // what a rep is actually being asked for.
      fields.forEach(function (f) {
        if (f.key === 'description') f.placeholder = placeholder;
      });

      function draw() {
        body.innerHTML =
          '<div style="margin-bottom:16px">' +
            '<div style="font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;' +
            'color:#64748b;margin-bottom:8px">This request</div>' +
            '<div style="margin-bottom:10px">' +
              '<label for="campClient" style="display:block;font-size:12px;color:#475569;margin-bottom:3px">Client on the Hub</label>' +
              clientSel + '</div>' +
            '<div style="margin-bottom:10px">' +
              '<label for="campProd" style="display:block;font-size:12px;color:#475569;margin-bottom:3px">Campaign / IO on file</label>' +
              '<select id="campProd" style="' + INPUT + '">' + prodOptions(name) + '</select>' +
              '<div class="muted" style="font-size:11.5px;margin-top:3px">Picking one fills the campaign, IO and product below. ' +
              'They stay editable — the Hub’s list is not always the whole of it.</div></div>' +
            '<div style="margin-bottom:10px">' +
              '<label for="campReq" style="display:block;font-size:12px;color:#475569;margin-bottom:3px">Requested by</label>' +
              '<select id="campReq" style="' + INPUT + '">' + reqOpts.join('') + '</select></div>' +
            '<div style="margin-bottom:10px">' +
              '<label for="campSubject" style="display:block;font-size:12px;color:#475569;margin-bottom:3px">Subject' +
              ' <span style="color:#b45309">*</span></label>' +
              '<input id="campSubject" value="' + esc(opts.subject || '') + '" placeholder="Subject — what do you need?" style="' + INPUT + '">' +
              (kind === 'support'
                ? '<div class="muted" style="font-size:11.5px;margin-top:3px">This object has no subject field of its own, ' +
                  'so the subject leads the issue below.</div>' : '') +
            '</div></div>' +
          KF().form(fields, prefill, SKIP[kind], CTX);
        KF().wire();
        wireHeader();
      }

      function wireHeader() {
        var clientEl = document.getElementById('campClient');
        var prodEl = document.getElementById('campProd');
        if (clientEl && clientEl.tagName === 'SELECT') {
          clientEl.onchange = function () {
            // Switching client redraws the fields — their choices and the
            // suggestions behind them are that client's. What somebody has
            // already typed is carried across: a form that empties itself
            // when the name at the top changes is one they start again.
            var typed = {};
            fields.forEach(function (f) {
              var v = KF().read(f, CTX);
              if (v !== '' && !(Array.isArray(v) && !v.length)) typed[f.key] = v;
            });
            var subject = (document.getElementById('campSubject') || {}).value;
            suggest(clientEl.value);
            prefill = prefillFor(clientEl.value);
            Object.keys(typed).forEach(function (k) {
              // The client field is the one thing that must NOT survive: it
              // is the record the request is filed against.
              if (k !== 'client') prefill[k] = typed[k];
            });
            if (subject != null) opts.subject = subject;
            draw();
          };
        }
        if (prodEl) prodEl.onchange = function () {
          var opt = prodEl.options[prodEl.selectedIndex] || {};
          var ioNo = (opt.dataset && opt.dataset.io) || '';
          fill('campaign', prodEl.value);
          fill('io', ioNo);
          fill('io_number', ioNo);
          fill('product', prodEl.value);
          fill('io_product', prodEl.value);
        };
      }

      // Only into a box somebody types into. A connection is a record id and
      // a dropdown is Knack's own list; writing a product name into either
      // would be a value Knack refuses, presented as a helpful prefill.
      function fill(key, value) {
        var f = fields.filter(function (x) { return x.key === key; })[0];
        if (!f || !value) return;
        if (f.control !== 'text' && !(f.control === 'select' && !(f.choices || []).length)) return;
        var el = document.getElementById(CTX.prefix + key);
        if (el && el.tagName === 'INPUT') el.value = value;
      }

      draw();

      foot.innerHTML = '<span id="campMsg" class="muted" style="font-size:12px"></span>' +
        '<a class="btn-primary" id="campSend" style="padding:9px 18px;font-size:13px;cursor:pointer;text-decoration:none">Send to Smart 1 Team</a>';

      document.getElementById('campSend').onclick = function () {
        var msg = document.getElementById('campMsg');
        var subject = document.getElementById('campSubject').value.trim();
        if (!subject) { msg.textContent = 'Subject is required.'; return; }
        var clientEl = document.getElementById('campClient');
        var prodEl = document.getElementById('campProd');
        var opt = prodEl.options[prodEl.selectedIndex] || {};

        var values = {};
        fields.forEach(function (f) {
          if ((SKIP[kind] || []).indexOf(f.key) !== -1) return;
          var v = KF().read(f, CTX);
          if (v !== '' && !(Array.isArray(v) && !v.length)) values[f.key] = v;
        });
        // The description travels as the named argument the API has always
        // taken — it is what the subject is folded into — and everything else
        // goes through `values`.
        var description = String(values.description || '');
        delete values.description;

        msg.textContent = 'Sending…';
        fetch('/api/client/campaign-request', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            kind: kind, client: clientEl.value, campaign: prodEl.value,
            io: (opt.dataset && opt.dataset.io) || '', subject: subject,
            requested_by: document.getElementById('campReq').value,
            description: description, values: values
          })
        }).then(function (r) { return r.json(); }).then(function (r) {
          if (r.error) { msg.textContent = r.error; return; }
          if (r.rejected && r.rejected.length) {
            // Created, but not as asked. The rep sees exactly what is missing
            // instead of finding out from the campaign team a week later.
            body.innerHTML = KF().refusedHtml(r.rejected) +
              '<div>Request created with ' + (r.written || []).length + ' fields. ' +
              'Fix the refused ones in Knack, or tell whoever renamed them.</div>';
            foot.innerHTML = '<a class="btn-primary" id="campDone" style="padding:9px 18px;font-size:13px;cursor:pointer;text-decoration:none">Close</a>';
            document.getElementById('campDone').onclick = function () { m.remove(); };
            return;
          }
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

    var m = KF().modal('campPickModal', esc(title),
      '<input id="campPickQ" placeholder="Client name or domain…" autocomplete="off" ' +
      'style="width:100%;padding:10px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:14px inherit">' +
      '<div id="campPickList" style="margin-top:10px;max-height:46vh;overflow-y:auto"></div>', 520, false);

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
