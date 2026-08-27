/* Commercial Builder — shared front-end helpers. Vanilla JS, no build step,
   no CDN dependencies (see commercial-builder.css header for why). */

const CB = (() => {
  const API_ROOT = "/tools/commercial-builder";

  async function api(path, options = {}) {
    const opts = { headers: { "Content-Type": "application/json" }, ...options };
    if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
    const res = await fetch(API_ROOT + path, opts);
    let data;
    try { data = await res.json(); } catch (e) { data = { ok: false, error: "Bad response from server." }; }
    if (!res.ok || data.ok === false) {
      toast(data.error || `Request failed (${res.status})`, true);
      throw new Error(data.error || `Request failed (${res.status})`);
    }
    return data;
  }

  function toast(message, isError = false) {
    let el = document.getElementById("cb-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "cb-toast";
      el.className = "cb-toast";
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.className = "cb-toast show" + (isError ? " error" : "");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 3200);
  }

  function fmtTime(seconds) {
    const s = Math.max(0, seconds || 0);
    const m = Math.floor(s / 60);
    const rem = (s % 60).toFixed(1).padStart(4, "0");
    return `${m}:${rem}`;
  }

  function el(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstElementChild;
  }

  function debounce(fn, wait = 350) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), wait); };
  }

  /* --------------------------------------------------------------- waiting

     Two of the waits in this tool are long enough that a 14-pixel spinner
     reads as a page that has stopped: writing three concepts is a model call,
     and animating a frame is minutes at Runway. These draw what is actually
     happening.

     Inline SVG, because this module ships no external assets — the deploy
     environment blocks CDNs, which is why Fabric.js is vendored elsewhere in
     this Hub. Every animation lives behind a prefers-reduced-motion guard in
     the stylesheet, so the same markup renders as a still diagram for anyone
     who has asked for that. */
  const ART = {
    /* Three storyboard cards being laid down in sequence, with sparks over
       them: the concepts step. */
    concepts:
      '<svg viewBox="0 0 132 82" role="img" aria-label="Writing concepts">' +
      '<g fill="none" stroke="currentColor" stroke-width="2" opacity=".9">' +
      '<rect class="cb-anim-card" x="4" y="22" width="36" height="30" rx="4"></rect>' +
      '<rect class="cb-anim-card" x="48" y="22" width="36" height="30" rx="4"></rect>' +
      '<rect class="cb-anim-card" x="92" y="22" width="36" height="30" rx="4"></rect>' +
      "</g>" +
      '<g fill="currentColor">' +
      '<path class="cb-anim-spark" d="M22 6l2.2 5.3L29.5 13l-5.3 2.2L22 20l-2.2-4.8L14.5 13l5.3-1.7z"></path>' +
      '<path class="cb-anim-spark" d="M66 4l2.2 5.3L73.5 11l-5.3 2.2L66 18l-2.2-4.8L58.5 11l5.3-1.7z"></path>' +
      '<path class="cb-anim-spark" d="M110 6l2.2 5.3L117.5 13l-5.3 2.2L110 20l-2.2-4.8L102.5 13l5.3-1.7z"></path>' +
      "</g>" +
      '<g fill="currentColor" opacity=".35">' +
      '<rect class="cb-anim-card" x="10" y="58" width="24" height="3" rx="1.5"></rect>' +
      '<rect class="cb-anim-card" x="54" y="58" width="24" height="3" rx="1.5"></rect>' +
      '<rect class="cb-anim-card" x="98" y="58" width="24" height="3" rx="1.5"></rect>' +
      "</g></svg>",

    /* A still frame with a play head sweeping across it and film perforations
       turning: the video step. */
    video:
      '<svg viewBox="0 0 132 82" role="img" aria-label="Animating the frame">' +
      '<rect x="18" y="14" width="96" height="54" rx="5" fill="none" ' +
      'stroke="currentColor" stroke-width="2"></rect>' +
      '<g fill="currentColor" opacity=".28">' +
      '<rect x="24" y="20" width="6" height="6" rx="1.5"></rect>' +
      '<rect x="24" y="38" width="6" height="6" rx="1.5"></rect>' +
      '<rect x="24" y="56" width="6" height="6" rx="1.5"></rect>' +
      '<rect x="102" y="20" width="6" height="6" rx="1.5"></rect>' +
      '<rect x="102" y="38" width="6" height="6" rx="1.5"></rect>' +
      '<rect x="102" y="56" width="6" height="6" rx="1.5"></rect>' +
      "</g>" +
      '<g class="cb-anim-sweep"><rect x="65" y="18" width="2" height="46" ' +
      'fill="currentColor" opacity=".55"></rect></g>' +
      '<g class="cb-anim-reel" style="transform-origin:66px 41px;">' +
      '<circle cx="66" cy="41" r="11" fill="none" stroke="currentColor" ' +
      'stroke-width="2" opacity=".7"></circle>' +
      '<circle cx="66" cy="33" r="2.4" fill="currentColor"></circle>' +
      "</g></svg>",

    /* A waveform, for the voiceover render. */
    voice:
      '<svg viewBox="0 0 132 82" role="img" aria-label="Recording the voiceover">' +
      '<g fill="currentColor">' +
      '<rect class="cb-anim-pulse" x="14" y="34" width="5" height="14" rx="2.5"></rect>' +
      '<rect class="cb-anim-pulse" x="28" y="26" width="5" height="30" rx="2.5" ' +
      'style="animation-delay:.2s"></rect>' +
      '<rect class="cb-anim-pulse" x="42" y="16" width="5" height="50" rx="2.5" ' +
      'style="animation-delay:.4s"></rect>' +
      '<rect class="cb-anim-pulse" x="56" y="28" width="5" height="26" rx="2.5" ' +
      'style="animation-delay:.6s"></rect>' +
      '<rect class="cb-anim-pulse" x="70" y="12" width="5" height="58" rx="2.5" ' +
      'style="animation-delay:.8s"></rect>' +
      '<rect class="cb-anim-pulse" x="84" y="26" width="5" height="30" rx="2.5" ' +
      'style="animation-delay:1s"></rect>' +
      '<rect class="cb-anim-pulse" x="98" y="32" width="5" height="18" rx="2.5" ' +
      'style="animation-delay:1.2s"></rect>' +
      '<rect class="cb-anim-pulse" x="112" y="36" width="5" height="10" rx="2.5" ' +
      'style="animation-delay:1.4s"></rect>' +
      "</g></svg>",
  };

  /* The panel, with the note under it. The note is not decoration: a Runway
     clip takes minutes and somebody who does not know that closes the tab. */
  function working(kind, title, note) {
    const art = ART[kind] || ART.concepts;
    return (
      '<div class="cb-working" style="color:var(--cb-primary);">' + art +
      '<div class="cb-working-title">' + escapeHtml(title || "Working\u2026") + "</div>" +
      (note ? '<p class="cb-working-note">' + escapeHtml(note) + "</p>" : "") +
      "</div>"
    );
  }

  function escapeHtml(value) {
    const d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
  }

  return { api, toast, fmtTime, el, debounce, working, escapeHtml, API_ROOT };
})();
