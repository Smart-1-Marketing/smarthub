/* Drawing a Knack object as a form — one copy, for every object that needs one.
 *
 * The web ticket form (object_107) and the Ad Copy Request form each draw a
 * list of {key, label, control, choices, required, known} into controls, read
 * them back, and show what Knack refused. That is the same job twice, and this
 * codebase has already paid twice for a rule that had to be found and fixed in
 * several places at once — the image-resize and PEXELS_API notes in CLAUDE.md.
 * So the type-to-control reading lives here, exactly as `coerce_field` in
 * hub/knack_api.py is object-agnostic rather than restated per object.
 *
 * What is NOT here is anything a particular object means. A field that needs
 * its own control — the ticket's ready-to-submit toggle, the ad copy form's
 * campaign picker — comes through `ctx.custom(f, value, ctx)`, which returns
 * HTML or null. A branch per object in here would be this file learning what a
 * web ticket is.
 *
 * Ids are prefixed per form so two forms can be open without colliding, and so
 * the ids a form has always used do not change under it.
 */
window.KnackForm = (function () {
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
  function radios(prefix, f, v) {
    var yn = yesNo(f);
    var picked = String(v == null ? '' : v).toLowerCase();
    return '<div style="display:flex;gap:16px;align-items:center;padding-top:3px">' +
      [yn.yes, yn.no].filter(Boolean).map(function (c, i) {
        var id = prefix + f.key + '-' + i;
        var on = picked === String(c).toLowerCase();
        return '<label for="' + id + '" style="display:flex;gap:6px;align-items:center;font-size:13px;cursor:pointer">' +
          '<input type="radio" id="' + id + '" name="' + prefix + f.key + '" value="' + esc(c) + '"' +
          (on ? ' checked' : '') + '>' + esc(c) + '</label>';
      }).join('') + '</div>';
  }

  function toggleStyle(on) {
    return 'padding:10px 16px;border-radius:8px;font:600 13px inherit;cursor:pointer;' +
      'border:1px solid ' + (on ? '#15803d' : 'var(--line,#e2e8f0)') + ';' +
      'background:' + (on ? '#dcfce7' : '#fff') + ';color:' + (on ? '#15803d' : '#64748b') + ';';
  }

  // A yes/no that opens on yes and is turned off by one click, for the answer
  // that IS the act of sending the form. The two captions ride on the element
  // so wire() can flip it without knowing what the field means.
  function toggle(prefix, f, v, onText, offText) {
    var yn = yesNo(f);
    var on = (v === '' || v == null) ? true
      : String(v).toLowerCase() === String(yn.yes).toLowerCase();
    return '<button type="button" id="' + prefix + f.key + '" data-toggle="1" ' +
      'data-yes="' + esc(yn.yes) + '" data-no="' + esc(yn.no) + '" data-on="' + (on ? '1' : '0') + '" ' +
      'data-on-text="' + esc(onText) + '" data-off-text="' + esc(offText) + '" ' +
      'style="' + toggleStyle(on) + '">' + esc(on ? onText : offText) + '</button>';
  }

  // A datalist-backed text box: the values we know offered, and still typable,
  // for a field whose right answer is usually — but not always — one of ours.
  function suggest(prefix, f, v, list, note, placeholder) {
    var id = prefix + f.key;
    var listId = id + '-list';
    var items = (list || []).filter(function (x) { return x; });
    return '<input id="' + id + '" value="' + esc(v) + '"' +
      (items.length ? ' list="' + listId + '"' : '') +
      ' placeholder="' + esc(placeholder || '') + '" style="' + INPUT + '">' +
      (items.length ? '<datalist id="' + listId + '">' + items.map(function (u) {
        return '<option value="' + esc(u) + '"></option>';
      }).join('') + '</datalist>' : '') +
      (items.length && note ? '<div class="muted" style="font-size:11.5px;margin-top:3px">' +
        esc(note) + '</div>' : '');
  }

  // -------------------------------------------------------------- controls
  // One control per Knack field type. `value` is what the record already
  // holds — a record id for a connection, because writing a connection's
  // label back is what clears the link.
  function control(prefix, f, value, ctx) {
    var id = prefix + f.key;
    var v = value == null ? '' : value;
    var blank = '<option value="">—</option>';

    if (ctx && typeof ctx.custom === 'function') {
      var own = ctx.custom(f, v, ctx);
      if (own != null) return own;
    }

    if (f.control === 'textarea') {
      return '<textarea id="' + id + '" rows="5" style="' + INPUT + '">' + esc(v) + '</textarea>';
    }
    if (f.control === 'file') {
      // Not a text box, and not silently absent either. A Knack file field
      // takes an upload to Knack's own asset endpoint, so the Hub cannot
      // write it — and a request sent believing the artwork went with it is
      // worse than one that says where to put it.
      return '<input id="' + id + '" disabled value="" placeholder="Attach on the Knack record" ' +
        'style="' + INPUT + ';background:#f8fafc;color:#94a3b8">' +
        '<div class="muted" style="font-size:11.5px;margin-top:3px">' +
        'Files are attached on the record in Knack — the Hub cannot upload them, ' +
        'so nothing is sent from here.</div>';
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

  function read(prefix, f) {
    var picked = document.querySelector('input[name="' + prefix + f.key + '"]:checked');
    if (picked) return picked.value;
    var el = document.getElementById(prefix + f.key);
    if (!el) return '';
    if (el.disabled) return '';
    if (el.dataset && el.dataset.toggle) {
      return el.dataset.on === '1' ? el.dataset.yes : el.dataset.no;
    }
    if (f.control === 'multi' && el.tagName === 'SELECT' && el.multiple) {
      return Array.prototype.filter.call(el.options, function (o) { return o.selected; })
        .map(function (o) { return o.value; });
    }
    return String(el.value == null ? '' : el.value).trim();
  }

  function form(prefix, fields, values, skip, ctx) {
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
            '<label for="' + esc(prefix + f.key) + '" style="display:block;font-size:12px;color:#475569;margin-bottom:3px">' +
            esc(f.label) + (f.required ? ' <span style="color:#b45309">*</span>' : '') +
            (f.known ? '' : ' <span style="color:#b45309;font-size:11px">(not on the object — check Knack)</span>') +
            '</label>' + control(prefix, f, (values || {})[f.key], ctx) +
            '<div data-meta="' + esc(prefix + f.key) + '"></div></div>';
        }).join('') + '</div>';
    }).join('');
  }

  // The toggle is the one control that carries its own state, so it is wired
  // once the form is on the page.
  function wire(root) {
    var scope = root || document;
    Array.prototype.forEach.call(scope.querySelectorAll('[data-toggle]'), function (b) {
      b.onclick = function () {
        var on = b.dataset.on !== '1';
        b.dataset.on = on ? '1' : '0';
        b.textContent = on ? b.dataset.onText : b.dataset.offText;
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

  // What the form could not fill in, and why. Deliberately its own colour and
  // not an error: "we could not look" and "there is nothing to look at" are
  // both worth saying, and neither one stops the form being sent.
  function notesHtml(notes) {
    if (!notes || !notes.length) return '';
    return '<div style="background:#f8fafc;border:1px solid var(--line,#e2e8f0);border-radius:8px;' +
      'padding:10px 12px;margin-bottom:14px;font-size:12px;color:#475569">' +
      '<b style="color:#334155">What we could not fill in</b><ul style="margin:6px 0 0 16px;padding:0">' +
      notes.map(function (n) { return '<li>' + esc(n) + '</li>'; }).join('') + '</ul></div>';
  }

  return {
    INPUT: INPUT, esc: esc, shell: shell, same: same,
    yesNo: yesNo, radios: radios, toggle: toggle, suggest: suggest,
    control: control, read: read, form: form, wire: wire,
    refusedHtml: refusedHtml, notesHtml: notesHtml
  };
})();
