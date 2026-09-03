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
    noteMock(path, data);
    return data;
  }

  /* ------------------------------------------------------- mock mode

     Every provider here degrades to mock data rather than erroring, which
     is right: a missing key must not stop a rep laying a spot out. What it
     also does is make a misnamed key invisible — concepts come back from a
     template, the casting list is eight identical rows, and the render is a
     job id with no file behind it, all of it looking exactly like work.

     The server has been saying so the whole time. Every one of these routes
     already answers `live: false` or `mock: true`, and not one line of this
     module's JavaScript read it: the mark was written, sent over the wire,
     and dropped by the last consumer. That is the shape RECORD_HOOK,
     io_creative, manifest() and thumb_url() each had, one step further out.

     It hangs off api() rather than off each caller for the reason
     hub-thinking.js upgrades a spinner rather than editing fifty call
     sites: the next route added here is covered without anybody
     remembering. Four rules.

     It never claims what it does not know — only a step the server itself
     reported as not live is named, and a route that says nothing draws
     nothing. It names the *provider and what the mock costs this spot*
     rather than saying "mock mode", because the chip on the dashboard
     already says that much and nobody reads it as applying to the thing in
     front of them. It is amber, not red: the tool is working as designed
     and a page of red is a page people scroll past. And nothing in it may
     raise — an indicator that breaks the screen it is reporting on is
     worse than no indicator — so the whole of it is guarded and a failure
     costs the note and never the answer.

     It cannot reach a client: commercial_review.html deliberately does not
     extend _layout.html, so it loads none of this. test_commercial_mock.py
     asserts that rather than trusting it. */
  /* Only routes that actually report it. Each was driven with every key
     unset and its response read: every one of these answers `live: false` or
     `mock: true`, and a table naming a route that never fires is one nobody
     can trust — hub/config.py's ALIASES rule, wearing a provider. Not
     counted in the comment, deliberately: a number in prose beside a list is
     a number that stops matching the list the first time one is added, which
     is what happened the day sound effects and music arrived.

     Three are deliberately absent and named so their absence is a decision
     rather than an oversight. `/render` and `/voiceover/full` carry no such
     key at all, so there is nothing here to read; the render is covered
     anyway, because approve_render refuses to file a mock as a delivered
     commercial, which is the gate that actually matters. `/stock/search`
     reports differently — a per-provider map rather than one flag — so it
     is read below on its own terms. */
  const MOCK_STEPS = [
    [/\/concepts$/, "Concepts", "written from a template, not by a model \u2014 set OPENAI_API_KEY"],
    [/\/script$/, "The script", "written from a template, not by a model \u2014 set OPENAI_API_KEY"],
    [/\/voices/, "Voice casting", "placeholder voices, not the ones on the account \u2014 set ELEVENLABS_API"],
    [/\/generate-ai/, "AI stills", "no image was generated \u2014 set OPENAI_API_KEY"],
    [/\/generate-video/, "AI video", "no clip was generated \u2014 set RUNWAY_API_KEY"],
    [/\/spokesperson/, "The spokesperson clip", "no clip was generated \u2014 set HEYGEN_API"],
    [/\/sound-effect$/, "Sound effects", "no sound was generated \u2014 set ELEVENLABS_API"],
    [/\/music\/compose$/, "The music bed", "no music was composed \u2014 set ELEVENLABS_API"],
  ];
  const _mocked = new Map();

  function noteMock(path, data) {
    try {
      if (!data) return;
      /* Stock answers with a map per source rather than one flag. Every
         source off means the results are placehold.co standing in for
         footage, which is the one mock a rep is most likely to drag onto a
         scene believing it is real. A source that is merely *empty* is not
         mock, so this asks whether any source was searchable at all. */
      if (data.providers && typeof data.providers === "object") {
        const vals = Object.values(data.providers);
        if (vals.length && vals.every((v) => v === false)) {
          _mocked.set("Stock footage",
            "placeholder images, not real footage \u2014 set PEXELS_API / PIXABAY_API");
          paintMockNote();
        }
        return;
      }
      if (data.live !== false && data.mock !== true) return;
      const hit = MOCK_STEPS.find(([re]) => re.test(path));
      if (!hit) return;
      if (_mocked.get(hit[1]) === hit[2]) return;   // already said; do not repaint
      _mocked.set(hit[1], hit[2]);
      paintMockNote();
    } catch (e) { /* never cost the caller its answer */ }
  }

  function paintMockNote() {
    try {
      const main = document.querySelector(".cb-main");
      if (!main || !_mocked.size) return;
      let box = document.getElementById("cb-mock-note");
      if (!box) {
        box = document.createElement("div");
        box.id = "cb-mock-note";
        box.className = "cb-note";
        main.insertBefore(box, main.firstChild);
      }
      const rows = [..._mocked].map(([k, v]) => `<div>${k} \u2014 ${v}</div>`).join("");
      box.innerHTML = `<strong>Some of this is placeholder, not real output</strong>${rows}`;
    } catch (e) { /* an indicator must not break the page it reports on */ }
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
