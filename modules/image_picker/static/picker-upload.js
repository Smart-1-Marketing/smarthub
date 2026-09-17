/* picker-upload.js — open the Cloudinary upload widget into one client's
 * gallery, and record what lands.
 *
 * One script for the three places a person can add files: the staff
 * gallery, the client's own share link (both through _upload_panel.html),
 * and the "Add more images" panel on Client 360, which this module does not
 * render. The widget is loaded from Cloudinary's CDN because it is the
 * widget — there is no self-hosted build — and only when somebody presses
 * the button, so a page that never uploads never loads it.
 *
 * Everything the widget needs comes back from /api/upload-signature: the
 * cloud name, the folder, which source tabs to draw, the accepted formats
 * and the per-file ceiling. Decided server-side, in one place, so the
 * three pages cannot drift apart and a template needs no Jinja for it.
 * Uploads are signed per file by the same endpoint, so this script cannot
 * place a file anywhere except the gallery's own folder whatever is typed
 * into the console.
 *
 * No build step and no module syntax: the share link is opened on whatever
 * phone the client has, and this is loaded with a plain script tag.
 */
(function () {
  'use strict';

  var WIDGET_SRC = 'https://upload-widget.cloudinary.com/global/all.js';

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function scriptLoaded() {
    if (window.cloudinary && window.cloudinary.createUploadWidget) {
      return Promise.resolve();
    }
    return new Promise(function (resolve, reject) {
      var existing = document.querySelector('script[data-cloudinary-widget]');
      if (existing) {
        existing.addEventListener('load', function () { resolve(); });
        existing.addEventListener('error', function () { reject(new Error('widget')); });
        return;
      }
      var tag = document.createElement('script');
      tag.src = WIDGET_SRC;
      tag.defer = true;
      tag.setAttribute('data-cloudinary-widget', '1');
      tag.onload = function () { resolve(); };
      tag.onerror = function () { reject(new Error('widget')); };
      document.head.appendChild(tag);
    });
  }

  function post(url, data) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    }).then(function (r) { return r.json(); });
  }

  /* The body every call carries: the share token for a client, the gallery
   * id for staff, and the folder the person named, if any. */
  function body(opts, extra) {
    var b = extra || {};
    if (opts.token) { b.token = opts.token; } else { b.client_id = opts.clientId; }
    var folder = typeof opts.folder === 'function' ? opts.folder() : (opts.folder || '');
    if (folder) { b.folder = String(folder).trim(); }
    if (opts.internal) { b.internal = true; }
    return b;
  }

  function scope(opts) {
    return opts.token ? 't=' + encodeURIComponent(opts.token)
                      : 'client_id=' + encodeURIComponent(opts.clientId);
  }

  /* The folders this gallery already has, defaults first, so a chooser can
   * offer "Logos" before somebody types "logo". */
  function folders(opts) {
    return fetch(opts.base + '/api/folders?' + scope(opts), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) { return (d && d.ok) ? (d.folders || []) : []; })
      .catch(function () { return []; });
  }

  /* Draw a folder chooser into `select`: the five sections, then every
   * named folder with its count, then "New folder…" which reveals `input`. */
  function fillChooser(select, input, list) {
    var opts = ['<option value="">Choose a folder (optional)</option>'];
    var defaults = [], named = [];
    (list || []).forEach(function (f) { (f['default'] ? defaults : named).push(f); });
    defaults.forEach(function (f) {
      opts.push('<option value="' + esc(f.label) + '">' + esc(f.label)
        + (f.count ? ' (' + f.count + ')' : '') + '</option>');
    });
    if (named.length) {
      opts.push('<optgroup label="Folders already in use">');
      named.forEach(function (f) {
        opts.push('<option value="' + esc(f.label) + '">' + esc(f.label)
          + (f.count ? ' (' + f.count + ')' : '') + '</option>');
      });
      opts.push('</optgroup>');
    }
    opts.push('<option value="__new__">New folder…</option>');
    select.innerHTML = opts.join('');
    select.onchange = function () {
      var fresh = select.value === '__new__';
      if (input) {
        input.hidden = !fresh;
        if (fresh) { input.focus(); }
      }
    };
  }

  /* What the chooser currently says: the typed name when "New folder…" is
   * picked, the picked folder otherwise. */
  function chosenFolder(select, input) {
    if (!select) { return ''; }
    if (select.value === '__new__') { return input ? String(input.value || '').trim() : ''; }
    return String(select.value || '').trim();
  }

  function record(opts, info, extra) {
    return post(opts.base + '/api/uploads', body(opts, Object.assign({
      public_id: info.public_id, secure_url: info.secure_url,
      resource_type: info.resource_type,
      source: info.source || opts.lastSource || 'local',
      original_filename: info.original_filename, width: info.width,
      height: info.height, bytes: info.bytes
    }, extra || {})));
  }

  function open(opts) {
    var say = opts.onState || function () {};
    opts.lastSource = '';
    say('Opening…');
    return Promise.all([
      scriptLoaded(),
      post(opts.base + '/api/upload-signature', body(opts, {}))
    ]).then(function (both) {
      var d = both[1];
      if (!d || !d.ok) { say((d && d.error) || 'Uploads are unavailable.'); return null; }
      var config = {
        cloudName: d.cloud_name,
        apiKey: d.api_key,
        uploadSignatureTimestamp: d.timestamp,
        uploadSignature: function (cb, paramsToSign) {
          post(opts.base + '/api/upload-signature', body(opts, { params: paramsToSign }))
            .then(function (s) {
              if (!s.ok) { say(s.error || 'Uploads are unavailable.'); return; }
              cb(s.signature);
            })
            .catch(function () { say('Could not start the upload.'); });
        },
        folder: d.folder,
        tags: (d.tags || '').split(','),
        sources: d.sources || ['local'],
        multiple: true,
        maxFiles: 40,
        maxFileSize: d.max_bytes,
        clientAllowedFormats: d.formats,
        resourceType: 'auto',
        showAdvancedOptions: false,
        showPoweredBy: false,
        singleUploadAutoClose: false,
        styles: {
          palette: {
            window: '#FFFFFF', windowBorder: '#DCE3EA', tabIcon: '#0A6E8C',
            menuIcons: '#46586E', textDark: '#0E1B2B', textLight: '#FFFFFF',
            link: '#0A6E8C', action: '#0A6E8C', inactiveTabIcon: '#7C8B9C',
            error: '#A33327', inProgress: '#0A6E8C', complete: '#1B6E52',
            sourceBg: '#F4F6F8'
          }
        }
      };
      /* Per-source credentials, only the ones actually set: an empty
       * dropboxAppKey is worse than none. */
      var keys = d.source_options || {};
      for (var k in keys) { if (Object.prototype.hasOwnProperty.call(keys, k)) { config[k] = keys[k]; } }

      var pending = 0;
      var widget = window.cloudinary.createUploadWidget(config, function (error, result) {
        if (error) { say('Upload failed. Please try again.'); return; }
        if (!result) { return; }
        /* Which tab the file came from. The success payload does not
         * reliably carry it, so the last source-changed event is what we
         * have — and worth having: without it every Instagram and Dropbox
         * file is filed as "local". */
        if (result.event === 'source-changed' && result.info && result.info.source) {
          opts.lastSource = String(result.info.source);
        }
        if (result.event === 'success') {
          pending += 1;
          record(opts, result.info).then(function (res) {
            pending -= 1;
            if (!res || !res.ok) { say((res && res.error) || 'That file did not save.'); return; }
            if (res.duplicate && res.choices && res.choices.length && opts.onDuplicate) {
              opts.onDuplicate(result.info, res);
              say('One file is already in this gallery — see below.');
              return;
            }
            if (opts.onAdded) { opts.onAdded(result.info, res); }
            if (!pending && opts.onSettled) { opts.onSettled(); }
          }).catch(function () { pending -= 1; say('That file did not save.'); });
        }
        if (result.event === 'queues-end') {
          if (opts.onQueueEnd) { opts.onQueueEnd(); } else { say('Done.'); }
        }
      });
      say('');
      widget.open();
      return widget;
    }).catch(function () {
      say('Could not start the upload.');
      return null;
    });
  }

  window.S1PickerUpload = {
    open: open, record: record, folders: folders,
    fillChooser: fillChooser, chosenFolder: chosenFolder, esc: esc
  };
})();
