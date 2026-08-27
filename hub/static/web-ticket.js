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

  // The controls, the reading back and the refusal notice all live in
  // /knack-form.js, shared with the Campaign Support form: the two ask the
  // same question of two Knack objects, and a second copy of this renderer is
  // the failure CLAUDE.md names twice over (the image-resize rule and the
  // PEXELS_API key each had to be fixed in several places).
  //
  // What stays here is the three fields this form draws its own way, and the
  // wording on the submit toggle.
  var KF = function () { return window.KnackForm; };
  var INPUT = 'padding:9px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:13px inherit;width:100%;box-sizing:border-box';

  function esc(s) { return KF().esc(s); }
  function same(a, b) { return KF().same(a, b); }
  function read(f) { return KF().read(f, CTX); }
  function wire() { KF().wire(); }
  function refusedHtml(r) { return KF().refusedHtml(r); }

  function toggleText(on) {
    return on ? '\u2713  Yes — submit this ticket' : 'No — not ready to submit';
  }

  // Ready to submit is a button, not a question: sending the form is the act
  // of submitting, so it opens on yes and one click turns it off for a ticket
  // someone is filing to finish later. Knack's workflow reads this field, and
  // a ticket that arrives with it blank sits in nobody's queue.
  //
  // Revision Requires Billing is two radios, because a field with two answers
  // should not hide one of them behind a click.
  function override(f, v) {
    if (f.key === 'ready_to_submit') {
      return KF().toggle(f, v, CTX, toggleText(true), toggleText(false));
    }
    if (f.key === 'billable') return KF().radios(f, v, CTX);
    return null;
  }

  var CTX = { prefix: 'wt-', override: override };

  // The website field is a text box with the client's own sites offered
  // beside it — the site that needs the work is not always one we hold a
  // record for, so the list suggests and never restricts.
  function withSites(fields, sites) {
    (fields || []).forEach(function (f) {
      if (f.key !== 'website') return;
      f.suggest = (sites || []).filter(function (x) { return x; });
      f.placeholder = 'Which website?';
      if (f.suggest.length) f.hint = 'From the record on screen — or type another.';
    });
    return fields;
  }

  function form(fields, values, skip) {
    return KF().form(fields, values, skip, CTX);
  }

  function shell(id, titleHtml, bodyHtml, width) {
    return KF().modal(id, titleHtml, bodyHtml, width || 640, true);
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
        form(withSites(fields, sites), prefill);
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
        form(withSites(fields, opts.sites || []), opened);
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
