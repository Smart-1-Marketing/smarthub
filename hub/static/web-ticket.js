/* New Web Ticket / Manage Ticket — the object_107 form.
 *
 * The four-box modal this replaces sent a title, a website, a description and
 * a name. The type of ticket, whether the revision is billable, the media
 * partner and their contact, and the ready-to-submit answer Knack's workflow
 * reads were left blank on every ticket Client 360 raised, and the web team
 * filled them in by hand or guessed. The ids were pinned in hub/knack_api.py;
 * nothing ever asked for their values.
 *
 * Two things this form does NOT do, both deliberate:
 *
 *   It does not carry its own copy of the field list. The ids are ours, but
 *   the *choices* on a dropdown live in Knack, and a form that guesses one
 *   writes a value Knack refuses — which costs the whole ticket, not the one
 *   field. So it draws whatever /api/client/tickets/fields returns.
 *
 *   It does not quietly drop what it could not write. Anything the API
 *   refused comes back in `rejected` and is shown, because a ticket created
 *   with half its fields missing must not read as a clean success.
 *
 * Entry points:
 *   WebTicket.open({client, site, domain, sites, user, onsaved})  — raise one
 *   WebTicket.manage({ticket, client, user, onsaved})        — edit one
 */
window.WebTicket = (function () {
  'use strict';

  var INPUT = 'padding:9px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:13px inherit;width:100%;box-sizing:border-box';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function shell(id, titleHtml, bodyHtml, width) {
    var old = document.getElementById(id);
    if (old) old.remove();
    var m = document.createElement('div');
    m.id = id;
    m.style.cssText = 'position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:99999;' +
      'display:flex;align-items:center;justify-content:center;padding:16px';
    m.innerHTML = '<div style="background:#fff;border-radius:14px;width:' + (width || 640) +
      'px;max-width:100%;max-height:92vh;display:flex;flex-direction:column">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;padding:13px 18px;' +
      'border-bottom:1px solid var(--line,#e2e8f0)">' +
      '<b style="color:var(--navy,#0f2545)">' + titleHtml + '</b>' +
      '<button data-close="1" style="border:0;background:none;font-size:22px;cursor:pointer;color:#64748b">&times;</button></div>' +
      '<div data-body="1" style="padding:16px 18px;overflow-y:auto">' + bodyHtml + '</div>' +
      '<div data-foot="1" style="display:flex;justify-content:flex-end;gap:10px;align-items:center;' +
      'padding:12px 18px;border-top:1px solid var(--line,#e2e8f0)"></div></div>';
    document.body.appendChild(m);
    m.onclick = function (e) { if (e.target === m) m.remove(); };
    m.querySelector('[data-close]').onclick = function () { m.remove(); };
    return m;
  }

  function same(a, b) {
    if (Array.isArray(a) || Array.isArray(b)) {
      return JSON.stringify(a || []) === JSON.stringify(b || []);
    }
    return String(a == null ? '' : a) === String(b == null ? '' : b);
  }

  // The yes and no a field actually holds. A boolean takes yes/no; a
  // multiple-choice takes whichever of its own choices begins with y or n,
  // because Knack refuses anything that is not one of them — the button says
  // Yes, the record gets the word this object uses for yes.
  function yesNo(f) {
    if (f.control === 'boolean') return { yes: 'yes', no: 'no' };
    var cs = f.choices || [];
    var y = cs.filter(function (c) { return /^y/i.test(String(c)); })[0];
    var n = cs.filter(function (c) { return /^n/i.test(String(c)); })[0];
    return { yes: y || cs[0] || 'yes', no: n || cs[1] || '' };
  }

  // Two answers, two radios. A dropdown for a yes/no hides one of the two
  // answers behind a click and reads as though there might be more.
  function radios(f, v) {
    var yn = yesNo(f);
    var picked = String(v == null ? '' : v).toLowerCase();
    return '<div style="display:flex;gap:16px;align-items:center;padding-top:3px">' +
      [yn.yes, yn.no].filter(Boolean).map(function (c, i) {
        var id = 'wt-' + f.key + '-' + i;
        var on = picked === String(c).toLowerCase();
        return '<label for="' + id + '" style="display:flex;gap:6px;align-items:center;font-size:13px;cursor:pointer">' +
          '<input type="radio" id="' + id + '" name="wt-' + f.key + '" value="' + esc(c) + '"' +
          (on ? ' checked' : '') + '>' + esc(c) + '</label>';
      }).join('') + '</div>';
  }

  // Ready to submit is a button, not a question: sending the form is the act
  // of submitting, so it opens on yes and one click turns it off for a ticket
  // someone is filing to finish later. Knack's workflow reads this field, and
  // a ticket that arrives with it blank sits in nobody's queue.
  function submitButton(f, v) {
    var yn = yesNo(f);
    var on = (v === '' || v == null) ? true
      : String(v).toLowerCase() === String(yn.yes).toLowerCase();
    return '<button type="button" id="wt-' + f.key + '" data-toggle="1" ' +
      'data-yes="' + esc(yn.yes) + '" data-no="' + esc(yn.no) + '" data-on="' + (on ? '1' : '0') + '" ' +
      'style="' + TOGGLE + '">' + esc(toggleText(on)) + '</button>';
  }

  function toggleText(on) {
    return on ? '\u2713  Yes — submit this ticket' : 'No — not ready to submit';
  }

  function toggleStyle(on) {
    return 'padding:10px 16px;border-radius:8px;font:600 13px inherit;cursor:pointer;' +
      'border:1px solid ' + (on ? '#15803d' : 'var(--line,#e2e8f0)') + ';' +
      'background:' + (on ? '#dcfce7' : '#fff') + ';color:' + (on ? '#15803d' : '#64748b') + ';';
  }

  var TOGGLE = toggleStyle(true);

  // -------------------------------------------------------------- controls
  // One control per Knack field type. `value` is what the record already
  // holds — a record id for a connection, because writing a connection's
  // label back is what clears the link. `ctx.sites` is the client's own
  // website list, offered on the URL field.
  function control(f, value, ctx) {
    var id = 'wt-' + f.key;
    var v = value == null ? '' : value;
    var blank = '<option value="">—</option>';

    if (f.key === 'ready_to_submit') return submitButton(f, v);
    if (f.key === 'billable') return radios(f, v);
    if (f.key === 'website') {
      // The URL of the page this was raised from, with the client's other
      // sites offered beside it — and still a text box, because the site that
      // needs the work is not always one we hold a record for.
      var sites = ((ctx && ctx.sites) || []).filter(function (x) { return x; });
      var listId = 'wt-sites';
      return '<input id="' + id + '" value="' + esc(v) + '" list="' + listId + '" ' +
        'placeholder="Which website?" style="' + INPUT + '">' +
        '<datalist id="' + listId + '">' + sites.map(function (u) {
          return '<option value="' + esc(u) + '"></option>';
        }).join('') + '</datalist>' +
        (sites.length ? '<div class="muted" style="font-size:11.5px;margin-top:3px">' +
          'From the record on screen — or type another.</div>' : '');
    }

    if (f.control === 'textarea') {
      return '<textarea id="' + id + '" rows="5" style="' + INPUT + '">' + esc(v) + '</textarea>';
    }
    if (f.control === 'boolean') {
      var yes = v === true || String(v).toLowerCase() === 'yes' || String(v) === 'true';
      var no = v === false || String(v).toLowerCase() === 'no' || String(v) === 'false';
      return '<select id="' + id + '" style="' + INPUT + '">' + blank +
        '<option value="yes"' + (yes ? ' selected' : '') + '>Yes</option>' +
        '<option value="no"' + (no ? ' selected' : '') + '>No</option></select>';
    }
    if (f.control === 'select' || f.control === 'multi') {
      if (!f.choices || !f.choices.length) {
        // Knack published no choices for it — a text box is honest, a select
        // with nothing in it is a field nobody can fill.
        return '<input id="' + id + '" value="' + esc(v) + '" style="' + INPUT + '">';
      }
      var chosen = Array.isArray(v) ? v.map(String) : (v === '' ? [] : [String(v)]);
      var opts = f.choices.map(function (c) {
        var on = chosen.indexOf(String(c)) !== -1;
        return '<option value="' + esc(c) + '"' + (on ? ' selected' : '') + '>' + esc(c) + '</option>';
      }).join('');
      if (f.control === 'multi') {
        return '<select id="' + id + '" multiple size="' + Math.min(5, f.choices.length) +
          '" style="' + INPUT + '">' + opts + '</select>';
      }
      return '<select id="' + id + '" style="' + INPUT + '">' + blank + opts + '</select>';
    }
    if (f.control === 'connection') {
      if (!f.choices || !f.choices.length) {
        return '<input id="' + id + '" value="' + esc(v) + '" placeholder="Knack record id" style="' + INPUT + '">' +
          '<div class="muted" style="font-size:11.5px;margin-top:3px">This connection’s records could not be read — ' +
          'a name will be refused, an id will be written.</div>';
      }
      var picked = Array.isArray(v) ? String(v[0] || '') : String(v);
      return '<select id="' + id + '" style="' + INPUT + '">' + blank +
        f.choices.map(function (c) {
          return '<option value="' + esc(c.id) + '"' + (c.id === picked ? ' selected' : '') +
            '>' + esc(c.label) + '</option>';
        }).join('') + '</select>';
    }
    if (f.control === 'date') {
      // Left as text on purpose: Knack shows dates as MM/DD/YYYY and a date
      // input would hand back YYYY-MM-DD, so the value that came out of the
      // record would not be the value that goes back in.
      return '<input id="' + id + '" value="' + esc(v) + '" placeholder="MM/DD/YYYY" style="' + INPUT + '">';
    }
    return '<input id="' + id + '" value="' + esc(v) + '" style="' + INPUT + '">';
  }

  function read(f) {
    var picked = document.querySelector('input[name="wt-' + f.key + '"]:checked');
    if (picked) return picked.value;
    var el = document.getElementById('wt-' + f.key);
    if (!el) return '';
    if (el.dataset && el.dataset.toggle) {
      return el.dataset.on === '1' ? el.dataset.yes : el.dataset.no;
    }
    if (f.control === 'multi' && el.tagName === 'SELECT' && el.multiple) {
      return Array.prototype.filter.call(el.options, function (o) { return o.selected; })
        .map(function (o) { return o.value; });
    }
    return String(el.value == null ? '' : el.value).trim();
  }

  function form(fields, values, skip, ctx) {
    var groups = [], byGroup = {};
    fields.forEach(function (f) {
      if (skip && skip.indexOf(f.key) !== -1) return;
      if (!byGroup[f.group]) { byGroup[f.group] = []; groups.push(f.group); }
      byGroup[f.group].push(f);
    });
    return groups.map(function (g) {
      return '<div style="margin-bottom:16px">' +
        '<div style="font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;' +
        'color:#64748b;margin-bottom:8px">' + esc(g) + '</div>' +
        byGroup[g].map(function (f) {
          return '<div style="margin-bottom:10px">' +
            '<label for="wt-' + esc(f.key) + '" style="display:block;font-size:12px;color:#475569;margin-bottom:3px">' +
            esc(f.label) + (f.required ? ' <span style="color:#b45309">*</span>' : '') +
            (f.known ? '' : ' <span style="color:#b45309;font-size:11px">(not on the object — check Knack)</span>') +
            '</label>' + control(f, (values || {})[f.key], ctx) + '</div>';
        }).join('') + '</div>';
    }).join('');
  }

  // The toggle is the one control that carries its own state, so it is wired
  // once the form is on the page.
  function wire() {
    Array.prototype.forEach.call(document.querySelectorAll('[data-toggle]'), function (b) {
      b.onclick = function () {
        var on = b.dataset.on !== '1';
        b.dataset.on = on ? '1' : '0';
        b.textContent = toggleText(on);
        b.setAttribute('style', toggleStyle(on));
      };
    });
  }

  // What came back refused. Never collapsed into "saved ✓".
  function refusedHtml(rejected) {
    if (!rejected || !rejected.length) return '';
    return '<div style="background:#fffbeb;border:1px solid #fcd34d;border-radius:8px;padding:10px 12px;' +
      'margin-bottom:12px;font-size:12px;color:#92400e">' +
      '<b>' + rejected.length + ' field' + (rejected.length === 1 ? '' : 's') + ' not written:</b><ul style="margin:6px 0 0 16px;padding:0">' +
      rejected.map(function (r) { return '<li>' + esc(r) + '</li>'; }).join('') + '</ul></div>';
  }

  function loadFields(scope) {
    var url = scope === 'manage'
      ? '/api/client/tickets/fields?scope=manage'
      : '/api/client/tickets/fields?scope=create';
    return fetch(url).then(function (r) { return r.json(); });
  }

  function people() {
    return fetch('/api/knack/people').then(function (r) { return r.json(); })
      .catch(function () { return { names: [] }; });
  }

  // ------------------------------------------------------------ raise one
  function open(opts) {
    opts = opts || {};
    var name = String(opts.client || '');
    var user = String(opts.user || '');
    var m = shell('tickModal', 'New Web Ticket — ' + esc(opts.site || name),
      '<div class="empty">Reading the ticket fields from Knack… <span class="spin"></span></div>');
    var body = m.querySelector('[data-body]');
    var foot = m.querySelector('[data-foot]');

    Promise.all([loadFields('create'), people()]).then(function (res) {
      var d = res[0], p = res[1];
      if (!d.configured) {
        body.innerHTML = '<div class="empty">Knack API not connected — set KNACK_APP_ID and KNACK_API_KEY, then redeploy.</div>';
        return;
      }
      if (d.error) { body.innerHTML = '<div class="empty">' + esc(d.error) + '</div>'; return; }

      var fields = d.fields || [];
      // The client and the website are known from the record that opened this.
      var prefill = { client: name, website: String(opts.domain || '') };
      // The attached page's URL first, then the client's other sites. Deduped
      // and blank-free, because an empty option in a picker is a trap.
      var sites = [String(opts.domain || '')].concat(opts.sites || [])
        .map(function (u) { return String(u || '').trim(); })
        .filter(function (u, i, all) { return u && all.indexOf(u) === i; });
      fields.forEach(function (f) {
        if (f.key === 'client' && f.control === 'connection') {
          var hit = (f.choices || []).filter(function (c) {
            return String(c.label).trim().toLowerCase() === name.trim().toLowerCase();
          });
          // Exactly one match or none — a near match is not a match. An
          // unmatched client is left for the rep to pick rather than guessed.
          prefill.client = hit.length === 1 ? hit[0].id : '';
        }
        if (f.key === 'ready_to_submit') {
          // Sending the form IS submitting, so this opens on yes — but it is
          // still a control, because a rep filing something to finish later
          // must be able to say no. Knack's workflow reads this field, and a
          // ticket that arrives with it blank sits in nobody's queue.
          if (f.control === 'boolean') prefill.ready_to_submit = 'yes';
          else {
            var yes = (f.choices || []).filter(function (c) { return /^y/i.test(String(c)); });
            if (yes.length) prefill.ready_to_submit = yes[0];
          }
        }
      });

      var names = (p && p.names) || [];
      var reqOpts = ['<option value="">Requested by…</option>'].concat(names.map(function (n) {
        return '<option value="' + esc(n) + '"' + (n === user ? ' selected' : '') + '>' + esc(n) + '</option>';
      }));
      if (!names.length) reqOpts.push('<option value="' + esc(user) + '" selected>' + esc(user) + '</option>');

      body.innerHTML =
        '<div style="margin-bottom:16px">' +
          '<label for="wtReq" style="display:block;font-size:12px;color:#475569;margin-bottom:3px">Requested by</label>' +
          '<select id="wtReq" style="' + INPUT + '">' + reqOpts.join('') + '</select></div>' +
        form(fields, prefill, null, { sites: sites });
      wire();

      foot.innerHTML = '<span id="wtMsg" class="muted" style="font-size:12px"></span>' +
        '<a class="btn-primary" id="wtSend" style="padding:9px 18px;font-size:13px;cursor:pointer;text-decoration:none">Send to Smart 1 Team</a>';

      document.getElementById('wtSend').onclick = function () {
        var msg = document.getElementById('wtMsg');
        var values = {};
        fields.forEach(function (f) {
          var v = read(f);
          if (v !== '' && !(Array.isArray(v) && !v.length)) values[f.key] = v;
        });
        var subject = String(values.title || '');
        if (!subject) { msg.textContent = 'Ticket Title is required.'; return; }
        // Title and description travel as the named arguments the API has
        // always taken; everything else goes through `values`.
        var description = String(values.description || '');
        delete values.title;
        delete values.description;
        msg.textContent = 'Sending…';
        fetch('/api/client/tickets', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            client: name, website: String(values.website || opts.domain || ''),
            subject: subject, description: description,
            requested_by: document.getElementById('wtReq').value,
            values: values
          })
        }).then(function (r) { return r.json(); }).then(function (r) {
          if (r.error) { msg.textContent = r.error; return; }
          if (r.rejected && r.rejected.length) {
            // Created, but not as asked. The rep sees exactly what is missing
            // instead of finding out from the web team a week later.
            body.innerHTML = refusedHtml(r.rejected) +
              '<div>Ticket created with ' + (r.written || []).length + ' fields. ' +
              'Fix the refused ones in Knack, or tell whoever renamed them.</div>';
            foot.innerHTML = '<a class="btn-primary" id="wtDone" style="padding:9px 18px;font-size:13px;cursor:pointer;text-decoration:none">Close</a>';
            document.getElementById('wtDone').onclick = function () { m.remove(); };
            if (opts.onsaved) opts.onsaved();
            return;
          }
          m.remove();
          if (opts.onsaved) opts.onsaved();
        }).catch(function () { msg.textContent = 'Could not reach the Hub. Nothing was sent.'; });
      };
    }).catch(function () {
      body.innerHTML = '<div class="empty">Could not load the ticket form. Nothing was sent.</div>';
    });
  }

  // ------------------------------------------------------------- edit one
  function manage(opts) {
    opts = opts || {};
    var t = opts.ticket || {};
    var m = shell('tickManageModal', 'Manage Ticket — ' + esc(t.title || ''),
      '<div class="empty">Reading the ticket fields from Knack… <span class="spin"></span></div>');
    var body = m.querySelector('[data-body]');
    var foot = m.querySelector('[data-foot]');

    loadFields('manage').then(function (d) {
      if (!d.configured) {
        body.innerHTML = '<div class="empty">Knack API not connected — set KNACK_APP_ID and KNACK_API_KEY, then redeploy.</div>';
        return;
      }
      if (d.error) { body.innerHTML = '<div class="empty">' + esc(d.error) + '</div>'; return; }

      var fields = d.fields || [];
      var opened = t.values || {};
      body.innerHTML =
        '<div class="muted" style="font-size:12px;margin-bottom:12px">' +
        'Ticket Title is not editable — renaming a ticket breaks the thread for whoever raised it.</div>' +
        form(fields, opened, null, { sites: opts.sites || [] });
      wire();

      foot.innerHTML = '<span id="wtMsg" class="muted" style="font-size:12px"></span>' +
        '<a class="btn-primary" id="wtSave" style="padding:9px 18px;font-size:13px;cursor:pointer;text-decoration:none">Save changes</a>';

      document.getElementById('wtSave').onclick = function () {
        var msg = document.getElementById('wtMsg');
        var values = {};
        fields.forEach(function (f) {
          var v = read(f);
          // Only what actually changed. Re-sending an untouched field is a
          // write nobody asked for, and on a connection it is a write that
          // can clear the link.
          if (!same(v, opened[f.key])) values[f.key] = v;
        });
        if (!Object.keys(values).length) { msg.textContent = 'Nothing changed.'; return; }
        msg.textContent = 'Saving…';
        fetch('/api/client/tickets/update', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: t.id, values: values })
        }).then(function (r) { return r.json(); }).then(function (r) {
          if (r.error) { msg.textContent = r.error; return; }
          if (r.rejected && r.rejected.length) {
            body.innerHTML = refusedHtml(r.rejected) +
              '<div>' + (r.updated || []).length + ' field' + ((r.updated || []).length === 1 ? '' : 's') + ' saved.</div>';
            foot.innerHTML = '<a class="btn-primary" id="wtDone" style="padding:9px 18px;font-size:13px;cursor:pointer;text-decoration:none">Close</a>';
            document.getElementById('wtDone').onclick = function () { m.remove(); };
            if (opts.onsaved) opts.onsaved();
            return;
          }
          m.remove();
          if (opts.onsaved) opts.onsaved();
        }).catch(function () { msg.textContent = 'Could not reach the Hub. Nothing was saved.'; });
      };
    }).catch(function () {
      body.innerHTML = '<div class="empty">Could not load the ticket form. Nothing was saved.</div>';
    });
  }

  return { open: open, manage: manage };
})();
