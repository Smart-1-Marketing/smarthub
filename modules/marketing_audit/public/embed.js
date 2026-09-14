/**
 * Smart 1 Marketing — Marketing Efficiency Audit™ embed loader
 *
 * Drop this on any page:
 *   <div id="smart1-audit"></div>
 *   <script src="https://YOUR-APP.onrender.com/embed.js" data-target="#smart1-audit"></script>
 *
 * Optional attributes on the script tag:
 *   data-target   CSS selector for the container. Defaults to the script's parent element.
 *   data-title    iframe title for screen readers.
 *   data-scroll   "off" to stop the page from scrolling when the visitor changes step.
 */
(function () {
  var script = document.currentScript;
  if (!script) return;

  var scriptUrl = new URL(script.src);
  var origin = scriptUrl.origin;
  // The tool is mounted under a path prefix inside Smart 1 Hub
  // (/tools/marketing-audit/), not always served from a bare origin, so the
  // iframe's source is built from this script's own directory rather than
  // just its origin -- origin() alone would point the frame at the Hub's
  // own root and lose the tool entirely, silently, in exactly the shape
  // this repo's own embed helper (hub/embed.py) was written to stop.
  var base = scriptUrl.href.replace(/[^/]*$/, '');
  var target = script.getAttribute('data-target');
  var mount = target ? document.querySelector(target) : script.parentElement;
  if (!mount) {
    console.error('[smart1-audit] Container not found:', target);
    return;
  }

  var reduceMotion = false;
  try { reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { /* ignore */ }

  // Positioned wrapper so the loading overlay can sit on top of the iframe
  var wrapper = document.createElement('div');
  wrapper.style.cssText = 'position:relative;width:100%;min-height:640px';

  var iframe = document.createElement('iframe');
  iframe.src = base + '?embed=1';
  iframe.title = script.getAttribute('data-title') || 'Marketing Efficiency Audit';
  iframe.loading = 'lazy';
  iframe.setAttribute('scrolling', 'no');
  iframe.style.cssText = 'width:100%;border:0;display:block;min-height:640px;transition:height .2s ease';

  // Loading overlay: navy panel, Smart 1 wordmark, animated ring + bar chart
  var anim = function (attr, values, dur, begin) {
    if (reduceMotion) return '';
    return '<animate attributeName="' + attr + '" values="' + values + '" dur="' + dur + '"' +
      (begin ? ' begin="' + begin + '"' : '') + ' repeatCount="indefinite"/>';
  };
  var overlay = document.createElement('div');
  overlay.setAttribute('role', 'status');
  overlay.setAttribute('aria-live', 'polite');
  overlay.style.cssText = 'position:absolute;inset:0;z-index:2;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:14px;background:#0A2240;color:#fff;text-align:center;padding:24px;font-family:Montserrat,\'Segoe UI\',-apple-system,Helvetica,Arial,sans-serif';
  overlay.innerHTML =
    '<div style="font-weight:800;letter-spacing:.14em;font-size:15px">SMART <span style="color:#009ED2">1</span> MARKETING</div>' +
    '<svg viewBox="0 0 200 200" width="120" height="120" aria-hidden="true">' +
      '<circle cx="100" cy="100" r="76" fill="none" stroke="rgba(255,255,255,.14)" stroke-width="6"/>' +
      '<circle cx="100" cy="100" r="76" fill="none" stroke="#009ED2" stroke-width="6" stroke-linecap="round" stroke-dasharray="120 358" transform="rotate(-90 100 100)">' +
        anim('stroke-dashoffset', '0;-478', '1.6s') +
      '</circle>' +
      '<circle cx="100" cy="100" r="58" fill="none" stroke="rgba(255,255,255,.10)" stroke-width="2"/>' +
      '<g fill="#2DBB72">' +
        '<rect x="72" y="118" width="12" height="20" rx="3">' + anim('height', '10;44;22;38;10', '2.8s') + anim('y', '128;94;116;100;128', '2.8s') + '</rect>' +
        '<rect x="90" y="106" width="12" height="32" rx="3">' + anim('height', '30;14;46;20;30', '2.8s', '-0.4s') + anim('y', '108;124;92;118;108', '2.8s', '-0.4s') + '</rect>' +
        '<rect x="108" y="96" width="12" height="42" rx="3">' + anim('height', '42;24;12;40;42', '2.8s', '-0.9s') + anim('y', '96;114;126;98;96', '2.8s', '-0.9s') + '</rect>' +
      '</g>' +
    '</svg>' +
    '<div style="font-size:13px;color:#DBE5ED">Loading your audit tool…</div>';

  var removeOverlay = function () {
    if (overlay && overlay.parentNode) overlay.parentNode.removeChild(overlay);
    overlay = null;
  };
  iframe.addEventListener('load', removeOverlay);
  setTimeout(removeOverlay, 20000); // safety net: never leave the overlay stuck

  wrapper.appendChild(iframe);
  wrapper.appendChild(overlay);
  mount.appendChild(wrapper);

  var allowScroll = script.getAttribute('data-scroll') !== 'off';

  window.addEventListener('message', function (e) {
    if (e.origin !== origin || !e.data || typeof e.data !== 'object') return;

    if (e.data.type === 's1-audit-height' && typeof e.data.height === 'number') {
      var h = Math.max(e.data.height, 400);
      iframe.style.height = h + 'px';
      wrapper.style.minHeight = '0';
    }

    if (e.data.type === 's1-audit-scroll' && allowScroll) {
      var top = iframe.getBoundingClientRect().top + window.pageYOffset - 24;
      if (window.pageYOffset > top) window.scrollTo({ top: top, behavior: 'smooth' });
    }
  });
})();
