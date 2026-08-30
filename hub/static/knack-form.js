/* One renderer for a form built from a live Knack object.
 *
 * The New Web Ticket form (object_107) and the Campaign Support form
 * (object_121) ask the same question of two objects: draw whatever
 * /api/…/fields returns, with the richest control each field publishes —
 * a connection as a picker of the records it may point at, a multiple choice
 * as its own choices, a boolean as yes/no — and read the answers back in the
 * shape a write wants.
 *
 * The second copy is what this file exists to stop. web-ticket.js carried
 * this renderer, campaign-request.js was about to carry a near-identical one,
 * and CLAUDE.md names that failure twice already: the image-resize rule and
 * the PEXELS_API key each had to be found and fixed in several places.
 *
 * Two rules it never breaks:
 *
 *   It never invents a choice. A field with nothing published becomes a text
 *   box, because Knack refuses the WHOLE record over one bad dropdown value —
 *   an empty picker is a field nobody can fill, and a guessed one is a lost
 *   request.
 *
 *   It never draws a control for something that cannot be written. A Knack
 *   file field is written by its own upload call, so a text box here would
 *   take a filename and drop it; it draws a note instead.
 *
 * A field is {key, field, group, label, control, choices, required, known,
 * writable} — plus two optional extras a caller may add before drawing:
 * `suggest` (a list offered as a datalist on a text box) and `hint` (a line
 * of help under the control).
 *
 * `ctx.prefix` namespaces the element ids, so two of these can sit on one
 * page without colliding. `ctx.override(field, value, ctx)` returns HTML for
 * a field the caller draws itself, or null to take the standard control.
 */
window.KnackForm = (function () {
  'use strict';

  var INPUT = 'padding:9px 12px;border:1px solid var(--line,#e2e8f0);border-radius:8px;font:13px inherit;width:100%;box-sizing:border-box';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  function same(a, b) {
    if (Array.isArray(a) || Array.isArray(b)) {
      return JSON.stringify(a || []) === JSON.stringify(b || []);
    }
    return String(a == null ? '' : a) === String(b == null ? '' : b);
  }

  function pfx(ctx) { return (ctx && ctx.prefix) || 'kf-'; }

  // The dialog both forms open in. It was written out twice, identically bar
  // a footer, which is the same second copy this file exists to stop.
  // `withFoot` adds the sticky action row a long scrolling form needs; a
  // short one puts its own button in the body and passes nothing.
  function modal(id, titleHtml, bodyHtml, width, withFoot) {
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
      (withFoot === false ? '' :
        '<div data-foot="1" style="display:flex;justify-content:flex-end;gap:10px;align-items:center;' +
        'padding:12px 18px;border-top:1px solid var(--line,#e2e8f0)"></div>') +
      '</div>';
    document.body.appendChild(m);
    m.onclick = function (e) { if (e.target === m) m.remove(); };
    m.querySelector('[data-close]').onclick = function () { m.remove(); };
    return m;
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
  function radios(f, v, ctx) {
    var yn = yesNo(f);
    var picked = String(v == null ? '' : v).toLowerCase();
    return '<div style="display:flex;gap:16px;align-items:center;padding-top:3px">' +
      [yn.yes, yn.no].filter(Boolean).map(function (c, i) {
        var id = pfx(ctx) + f.key + '-' + i;
        var on = picked === String(c).toLowerCase();
        return '<label for="' + id + '" style="display:flex;gap:6px;align-items:center;font-size:13px;cursor:pointer">' +
          '<input type="radio" id="' + id + '" name="' + pfx(ctx) + f.key + '" value="' + esc(c) + '"' +
          (on ? ' checked' : '') + '>' + esc(c) + '</label>';
      }).join('') + '</div>';
  }

  function toggleStyle(on) {
    return 'padding:10px 16px;border-radius:8px;font:600 13px inherit;cursor:pointer;' +
      'border:1px solid ' + (on ? '#15803d' : 'var(--line,#e2e8f0)') + ';' +
      'background:' + (on ? '#dcfce7' : '#fff') + ';color:' + (on ? '#15803d' : '#64748b') + ';';
  }

  // A yes/no that opens on yes and is turned off in one click. Used where the
  // ordinary act of sending the form IS the yes — a question there would be a
  // question with one sensible answer.
  function toggle(f, v, ctx, on_text, off_text) {
    var yn = yesNo(f);
    var on = (v === '' || v == null) ? true
      : String(v).toLowerCase() === String(yn.yes).toLowerCase();
    return '<button type="button" id="' + pfx(ctx) + f.key + '" data-toggle="1" ' +
      'data-yes="' + esc(yn.yes) + '" data-no="' + esc(yn.no) + '" data-on="' + (on ? '1' : '0') + '" ' +
      'data-on-text="' + esc(on_text) + '" data-off-text="' + esc(off_text) + '" ' +
      'style="' + toggleStyle(on) + '">' + esc(on ? on_text : off_text) + '</button>';
  }

  // -------------------------------------------------------------- controls
  // One control per Knack field type. `value` is what the record already
  // holds — a record id for a connection, because writing a connection's
  // label back is what clears the link.
  function control(f, value, ctx) {
    ctx = ctx || {};
    if (ctx.override) {
      var own = ctx.override(f, value, ctx);
      if (own != null) return own;
    }
    var id = pfx(ctx) + f.key;
    var v = value == null ? '' : value;
    var blank = '<option value="">—</option>';
    var hint = f.hint
      ? '<div class="muted" style="font-size:11.5px;margin-top:3px">' + esc(f.hint) + '</div>'
      : '';

    if (f.control === 'file' || f.writable === false) {
      // Drawn, and not a box. A Knack file field is written by its own upload
      // call, so a text box here would take a filename and drop it.
      return '<div style="font-size:12px;color:#64748b;background:#f8fafc;border:1px dashed var(--line,#e2e8f0);' +
        'border-radius:8px;padding:9px 12px">' +
        esc(f.hint || 'Attached in Knack after the request is created — files cannot be sent with it.') +
        '</div>';
    }
    if (f.control === 'textarea') {
      return '<textarea id="' + id + '" rows="5"' +
        (f.placeholder ? ' placeholder="' + esc(f.placeholder) + '"' : '') +
        ' style="' + INPUT + '">' + esc(v) + '</textarea>' + hint;
    }
    if (f.control === 'boolean') {
      var yes = v === true || String(v).toLowerCase() === 'yes' || String(v) === 'true';
      var no = v === false || String(v).toLowerCase() === 'no' || String(v) === 'false';
      return '<select id="' + id + '" style="' + INPUT + '">' + blank +
        '<option value="yes"' + (yes ? ' selected' : '') + '>Yes</option>' +
        '<option value="no"' + (no ? ' selected' : '') + '>No</option></select>' + hint;
    }
    if (f.control === 'select' || f.control === 'multi') {
      if (!f.choices || !f.choices.length) {
        // Knack published no choices for it — a text box is honest, a select
        // with nothing in it is a field nobody can fill.
        return text(id, v, f, ctx) + hint;
      }
      var chosen = Array.isArray(v) ? v.map(String) : (v === '' ? [] : [String(v)]);
      var opts = f.choices.map(function (c) {
        var on = chosen.indexOf(String(c)) !== -1;
        return '<option value="' + esc(c) + '"' + (on ? ' selected' : '') + '>' + esc(c) + '</option>';
      }).join('');
      if (f.control === 'multi') {
        return '<select id="' + id + '" multiple size="' + Math.min(5, f.choices.length) +
          '" style="' + INPUT + '">' + opts + '</select>' +
          '<div class="muted" style="font-size:11.5px;margin-top:3px">' +
          (f.hint ? esc(f.hint) + ' ' : '') + 'Pick as many as apply.</div>';
      }
      return '<select id="' + id + '" style="' + INPUT + '">' + blank + opts + '</select>' + hint;
    }
    if (f.control === 'connection') {
      if (!f.choices || !f.choices.length) {
        // The server says which of the two empties this is — an object with
        // no records and a read that failed are different answers, and only
        // the first means the field is genuinely unanswerable. Without a
        // reason from the server this stays the older, safer wording.
        return '<input id="' + id + '" value="' + esc(v) + '" placeholder="Knack record id" style="' + INPUT + '">' +
          '<div class="muted" style="font-size:11.5px;margin-top:3px">' +
          esc(f.hint || 'This connection’s records could not be read.') + ' ' +
          'A name will be refused, an id will be written.</div>';
      }
      var picked = Array.isArray(v) ? String(v[0] || '') : String(v);
      // A truncated picker is drawn amber rather than in the muted style the
      // other hints use: it is not a note about the field, it is a warning
      // that the answer somebody wants may not be on the list.
      var short = f.hint && /Showing /.test(f.hint)
        ? '<div style="font-size:11.5px;margin-top:3px;color:#b45309">' + esc(f.hint) + '</div>'
        : hint;
      return '<select id="' + id + '" style="' + INPUT + '">' + blank +
        f.choices.map(function (c) {
          return '<option value="' + esc(c.id) + '"' + (c.id === picked ? ' selected' : '') +
            '>' + esc(c.label) + '</option>';
        }).join('') + '</select>' + short;
    }
    if (f.control === 'date') {
      // Left as text on purpose: Knack shows dates as MM/DD/YYYY and a date
      // input would hand back YYYY-MM-DD, so the value that came out of the
      // record would not be the value that goes back in.
      return '<input id="' + id + '" value="' + esc(v) + '" placeholder="MM/DD/YYYY" style="' + INPUT + '">' + hint;
    }
    return text(id, v, f, ctx) + hint;
  }

  // A text box, with the caller's suggestions offered beside it where there
  // are any. A datalist suggests and never restricts — which is the right
  // shape for a value the Hub happens to know (a client's own IO numbers) on
  // a field Knack publishes no choices for.
  function text(id, v, f, ctx) {
    var list = (f.suggest || []).filter(function (x) { return x; });
    var ph = f.placeholder ? ' placeholder="' + esc(f.placeholder) + '"' : '';
    if (!list.length) {
      return '<input id="' + id + '" value="' + esc(v) + '"' + ph + ' style="' + INPUT + '">';
    }
    var listId = id + '-list';
    return '<input id="' + id + '" value="' + esc(v) + '" list="' + listId + '"' + ph +
      ' style="' + INPUT + '">' +
      '<datalist id="' + listId + '">' + list.map(function (x) {
        return '<option value="' + esc(x) + '"></option>';
      }).join('') + '</datalist>';
  }

  function read(f, ctx) {
    if (f.control === 'file' || f.writable === false) return '';
    var picked = document.querySelector('input[name="' + pfx(ctx) + f.key + '"]:checked');
    if (picked) return picked.value;
    var el = document.getElementById(pfx(ctx) + f.key);
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


  // ------------------------------------------------------------- triage
  // Read the description somebody typed and offer the choices it already
  // answers. One implementation here rather than one per form, for the same
  // reason the controls above are: the web ticket and the campaign support
  // request are two objects asking one question, and a second copy of this is
  // the drift this file exists to stop.
  //
  // Three rules, and all three are about not being confidently wrong on a
  // form somebody sends without re-reading it:
  //
  //   - Only fields that are **empty** are asked about. A value somebody
  //     chose is the better source and is never offered over. The endpoint
  //     enforces the same thing, because a rule the form keeps while the
  //     write breaks it is not a rule.
  //   - Nothing is applied by arriving. A suggestion is drawn dotted with
  //     the reason beside it and one press keeps it; Dismiss puts the field
  //     back exactly as it was.
  //   - A suggestion is one of Knack's own published choices or it is not
  //     offered. The server drops anything else and counts it — Knack
  //     refuses the whole record over one bad choice, so a suggestion that
  //     could not be saved is worse than none.

  /* The controls a triage suggestion can be offered into. The server's
     hub/request_triage.CHOICE_CONTROLS is the same list; test_ad_copy.py
     holds the two in step, because a control added on one side and not the
     other means the button offers a field the server refuses to answer, or
     the other way round, with nothing on the screen saying so. */
  var CHOICE_CONTROLS = ['select', 'multi', 'boolean', 'radio'];

  function hasChoiceField(fields) {
    return (fields || []).some(function (f) {
      return CHOICE_CONTROLS.indexOf(f.control) !== -1;
    });
  }

  function emptyChoiceKeys(fields, ctx) {
    var out = [];
    (fields || []).forEach(function (f) {
      if (CHOICE_CONTROLS.indexOf(f.control) === -1) return;
      var v = read(f, ctx);
      if (Array.isArray(v) ? !v.length : !String(v || '').trim()) out.push(f.key);
    });
    return out;
  }

  function markSuggested(target, why, onKeep, onDrop) {
    var el = (typeof target === 'string') ? document.getElementById(target) : target;
    if (!el || el._kfSuggested) return;
    el._kfSuggested = true;
    el.style.borderStyle = 'dashed';
    el.style.background = '#f5f9ff';
    var row = document.createElement('div');
    row.style.cssText = 'margin-top:4px;font-size:11.5px;color:#475569;' +
      'display:flex;gap:8px;align-items:baseline;flex-wrap:wrap';
    row.innerHTML = '<span style="flex:1;min-width:140px">' + esc(why || 'Read from what you typed.') + '</span>';
    var keep = document.createElement('button');
    keep.type = 'button'; keep.textContent = 'Keep';
    keep.style.cssText = 'border:1px solid #1769AA;background:#1769AA;color:#fff;' +
      'border-radius:6px;padding:2px 9px;font-size:11.5px;cursor:pointer';
    var drop = document.createElement('button');
    drop.type = 'button'; drop.textContent = 'Dismiss';
    drop.style.cssText = 'border:1px solid #cbd5e1;background:#fff;color:#475569;' +
      'border-radius:6px;padding:2px 9px;font-size:11.5px;cursor:pointer';
    function clear() {
      el.style.borderStyle = ''; el.style.background = '';
      el._kfSuggested = false;
      if (row.parentNode) row.parentNode.removeChild(row);
    }
    keep.onclick = function () { clear(); if (onKeep) onKeep(); };
    drop.onclick = function () { clear(); if (onDrop) onDrop(); };
    row.appendChild(keep); row.appendChild(drop);
    el.parentNode.appendChild(row);
  }

  // Put one value into whichever control the field is drawn as, and hand back
  // the undo. Four shapes, not one: `control()` above draws a boolean as a
  // pair of radios OR as a data-toggle button, and a `multi` as a
  // <select multiple> — and on the last two, assigning `.value` does exactly
  // nothing. That is the worst way for this to fail: the field is marked as
  // suggested, the reason appears beside it, and `read()` still returns what
  // was there before, so the form reads as filled in and sends the old value.
  // Returns null when the control will not take it, which is what stops a
  // suggestion being drawn over a field it did not change.
  function setValue(f, id, value) {
    var wanted = String(value == null ? '' : value);

    // A pair of radios: addressed by name, because each input has its own id.
    var radio = document.querySelector('input[name="' + id + '"][value="' +
      wanted.replace(/["\\]/g, '\\$&') + '"]');
    if (radio) {
      var wasChecked = document.querySelector('input[name="' + id + '"]:checked');
      radio.checked = true;
      return { anchor: radio.parentNode,
               undo: function () {
                 radio.checked = false;
                 if (wasChecked) wasChecked.checked = true;
               } };
    }

    var el = document.getElementById(id);
    if (!el) return null;

    // The toggle carries its own state in data attributes and its label in
    // its text, so both have to move or `read()` answers with the old one.
    if (el.dataset && el.dataset.toggle) {
      var yes = String(el.dataset.yes || '');
      var isYes = wanted.toLowerCase() === yes.toLowerCase();
      var wasOn = el.dataset.on;
      var wasText = el.textContent;
      var wasStyle = el.getAttribute('style');
      el.dataset.on = isYes ? '1' : '0';
      el.textContent = isYes ? el.dataset.onText : el.dataset.offText;
      el.setAttribute('style', toggleStyle(isYes));
      return { anchor: el,
               undo: function () {
                 el.dataset.on = wasOn;
                 el.textContent = wasText;
                 el.setAttribute('style', wasStyle);
               } };
    }

    if (el.tagName === 'SELECT' && el.multiple) {
      var wasSelected = Array.prototype.map.call(el.options, function (o) {
        return o.selected; });
      var hit = false;
      Array.prototype.forEach.call(el.options, function (o) {
        if (o.value === wanted) { o.selected = true; hit = true; }
      });
      if (!hit) return null;
      return { anchor: id,
               undo: function () {
                 Array.prototype.forEach.call(el.options, function (o, i) {
                   o.selected = wasSelected[i]; });
               } };
    }

    var before = el.value;
    el.value = wanted;
    // A select that will not take the value is a value the schema does not
    // publish. Put back rather than left showing a choice that cannot save.
    if (el.tagName === 'SELECT' && el.value !== wanted) {
      el.value = before;
      return null;
    }
    return { anchor: id, undo: function () { el.value = before; } };
  }

  function applySuggestions(fields, suggestions, ctx) {
    var applied = 0;
    (fields || []).forEach(function (f) {
      var sug = (suggestions || {})[f.key];
      if (!sug) return;
      var set = setValue(f, pfx(ctx) + f.key, sug.value);
      if (!set) return;
      markSuggested(set.anchor, sug.why, null, set.undo);
      applied += 1;
    });
    return applied;
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
            '<label for="' + esc(pfx(ctx) + f.key) + '" style="display:block;font-size:12px;color:#475569;margin-bottom:3px">' +
            esc(f.label) + (f.required ? ' <span style="color:#b45309">*</span>' : '') +
            (f.known === false ? ' <span style="color:#b45309;font-size:11px">(not on the object — check Knack)</span>' : '') +
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

  /* The control that offers it, drawn once so all three forms get it and a
     fourth added later gets it without being edited.

     Hidden entirely where there is nothing to suggest into — a button that
     can only ever say "no" is one people learn to skip past. That sentence
     was written here from the start and the code did not keep it: it drew
     the button on every form and only said so once somebody had pressed it.
     What is knowable before the press is whether this object publishes any
     choice field **at all**, which is a fact about the form rather than
     about what has been typed into it. A form with none can never have a
     suggestion, so it gets no button.

     Deliberately not hidden when every choice field simply happens to be
     answered: those can be cleared, and a control that disappears while
     somebody is filling the form in is worse than one that says "every
     question with a set list of answers already has one".

     `textKey` may be one field key or several. An ad copy request splits
     what is being asked for across two boxes -- what is changing, and
     anything else we should know -- and reading one of them would miss the
     half the answer was actually written in. */
  function triageButton(host, fields, ctx, kindKey, textKey) {
    var el = (typeof host === 'string') ? document.getElementById(host) : host;
    if (!el) return;
    el.innerHTML = '';
    if (!hasChoiceField(fields)) return;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = 'Fill in the rest from what I typed';
    btn.style.cssText = 'border:1px solid #cbd5e1;background:#fff;color:#0d2340;' +
      'border-radius:7px;padding:6px 12px;font-size:12.5px;cursor:pointer';
    var note = document.createElement('div');
    note.style.cssText = 'font-size:11.5px;color:#64748b;margin-top:5px';
    el.appendChild(btn); el.appendChild(note);

    btn.onclick = function () {
      var empty = emptyChoiceKeys(fields, ctx);
      if (!empty.length) {
        note.textContent = 'Every question with a set list of answers already has one.';
        return;
      }
      var keys = Array.isArray(textKey) ? textKey : [textKey];
      var text = keys.map(function (k) {
        var box = document.getElementById(pfx(ctx) + k);
        return box ? String(box.value || '') : '';
      }).filter(function (t) { return t.trim(); }).join('\n\n');
      var busy = window.S1Think
        ? window.S1Think.busy(btn, {kind: 'ai', label: 'Reading what you typed…'})
        : (function () { btn.disabled = true;
                         return {done: function () { btn.disabled = false; }}; })();
      note.textContent = '';
      fetch('/api/client/requests/triage', {
        method: 'POST', credentials: 'same-origin',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({kind: kindKey, text: text, empty: empty})
      }).then(function (r) { return r.json(); }).then(function (d) {
        busy.done();
        var n = applySuggestions(fields, d.suggestions, ctx);
        // The count of what was applied, and the server's own note about what
        // it discarded. Neither is collapsed into "done": a suggestion that
        // was dropped for not being one of the field's options is the check
        // working, and worth seeing.
        note.textContent = (n ? n + ' filled in below — keep or dismiss each one. ' : '')
          + (d.note || d.error || '');
      }).catch(function (e) {
        busy.done();
        note.textContent = 'That could not be read: ' + e;
      });
    };
  }

  return {
    INPUT: INPUT, esc: esc, same: same, modal: modal, yesNo: yesNo, radios: radios,
    toggle: toggle, control: control, read: read, form: form, wire: wire,
    refusedHtml: refusedHtml,
    emptyChoiceKeys: emptyChoiceKeys, applySuggestions: applySuggestions,
    setValue: setValue, triageButton: triageButton
  };
})();
