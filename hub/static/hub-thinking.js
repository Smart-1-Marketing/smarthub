/* Smart 1 Hub — the thing that says something is running.
 *
 * Nine copies of one border spinner
 * -----------------------------------------------------------------
 * `.spin` was defined seven times and `.spinner` twice more — hub.css,
 * sales_builder, page_image_optimizer, stock_photos, ads_base, the two scan
 * widgets, and the two Node-served ad builder pages — each a 2px border arc
 * at a slightly different size in a slightly different gray. That is the
 * drift hub/storage.py and hub/images.py exist to stop, wearing a spinner:
 * the next improvement would have had to land nine times and would have
 * landed in one.
 *
 * So there is one implementation, and it is loaded the way hub-crumbs.js is
 * — from base.html on hub pages and injected by HubBar into all twenty
 * mounted modules — which means a tool added next month gets it without
 * being edited. It **upgrades what is already there** rather than asking
 * fifty call sites to be rewritten: every `.spin` and `.spinner` on the page
 * becomes the animated glyph, including ones drawn into a panel by a fetch
 * ten seconds after load. Nothing downstream has to know this file exists.
 *
 * Three glyphs, because they answer three different questions
 * -----------------------------------------------------------------
 * A spinner says *something* is happening. It cannot say **what**, and the
 * three waits in this Hub are not alike:
 *
 *   ai    — a model is writing. Tens of seconds, billed, and the answer is
 *           prose somebody will read. ✨ is already the Hub's own mark for
 *           this ("✨ Ask AI about this data" on Client 360), so the glyph
 *           is that sparkle, thinking.
 *   scan  — we are reading somebody else's website or sweeping an account.
 *           Minutes, and the wait is somebody else's server. A radar.
 *   wait  — our own database or Knack. Seconds. The arc, which is what
 *           every one of the nine copies already drew.
 *
 * A bare `.spin` upgrades to `wait`, because that is what it meant. A screen
 * declares the other two with `data-s1-thinking="ai"` on any ancestor — one
 * attribute on a panel rather than one per call site, so a button added to
 * that panel later is right by default.
 *
 * Why a spinner alone is not enough, and where that was learnt
 * -----------------------------------------------------------------
 * modules/ads_builder/templates/ads_generator.html already carries the note:
 * "a spinner for a minute reads as a hung page, so the stages are drawn".
 * It was the only screen in the Hub that had worked that out, and it had its
 * own copy of the stage timer. Two things move here so nothing else has to
 * discover it again:
 *
 *   - **A stage line.** `handle.stage("Writing ad groups…")` replaces the
 *     label. `attach(el, {stages: [...]})` advances them on a timer, which
 *     is the ads generator's arrangement generalised.
 *   - **An elapsed line, after SLOW_AT.** Not from the first second: a
 *     stopwatch on a two-second read is noise, and a screen that counts at
 *     you teaches people to expect a wait. It appears only once the wait has
 *     gone past what anybody would call quick, and from then on it is the
 *     one thing that distinguishes a slow answer from a dead one.
 *
 * Rules it is held to
 * -----------------------------------------------------------------
 *   - **Nothing here may raise.** An indicator that breaks the page it is
 *     reporting on is worse than no indicator: every entry point is wrapped,
 *     and a failure costs the animation and nothing else. `attach()` always
 *     returns a handle with a `.done()` on it, so a caller's `finally` is
 *     safe even when the attach itself found nothing to attach to.
 *   - **It never claims to know what it does not.** `.done()` stops the
 *     animation; it does not write "Done". Whether the thing that was
 *     running succeeded is the caller's answer, and a tick drawn here over a
 *     failed call is the confident wrong answer this codebase keeps undoing.
 *   - **`currentColor`, never a palette.** Forty modules and no shared
 *     stylesheet between them. Inheriting the surrounding text color is the
 *     only way one glyph is legible on a white card, a navy button and a
 *     dark landing page without any of them being edited.
 *   - **`prefers-reduced-motion` keeps the glyph and drops the motion.** The
 *     setting asks for less animation, not less information — a wait that
 *     becomes invisible for those readers is the feature failing exactly
 *     where it was needed. The mark stays, the label stays, the elapsed line
 *     stays; only the movement goes.
 *   - **`aria-live="polite"` and `role="status"`.** A spinner is invisible to
 *     a screen reader; the label is the whole message.
 *
 * There is no Python mirror of any of this. hub/target_areas.py and the
 * creative classifier each carry one already and each needs a test proving
 * the halves still agree; the vocabulary lives here alone and
 * test_thinking.py reads this file for it.
 */
(function () {
  "use strict";

  var KINDS = ["ai", "scan", "wait"];

  /* How long before the elapsed line appears, and how often it ticks.
     Six seconds because a Knack read is under two and a model is over ten:
     the line should never appear on the first, and should always appear on
     the second. */
  var SLOW_AT = 6000;
  var TICK = 1000;

  var NS = "http://www.w3.org/2000/svg";

  function reduced() {
    try {
      return !!(window.matchMedia &&
                window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (e) { return false; }
  }

  /* ---------------------------------------------------------------- glyphs
     Each is a 24x24 viewBox drawn in currentColor. The animation is CSS, in
     hub-help.css, so a page that fails to load this script still gets a
     static mark rather than an empty box — and so reduced motion is one
     media query rather than a branch in here. */

  function el(name, attrs) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) {
        n.setAttribute(k, String(attrs[k]));
      }
    }
    return n;
  }

  /* A four-point sparkle, turning and breathing, with two smaller ones
     twinkling off-beat. The same ✨ Client 360 already puts on its own AI
     control, so the two read as the same thing happening. */
  function glyphAI(svg) {
    var star = "M12 3.2 13.6 9.1 19.4 10.7 13.6 12.3 12 18.2 10.4 12.3 4.6 10.7 10.4 9.1Z";
    svg.appendChild(el("path", { d: star, fill: "currentColor",
                                 "class": "s1-think-star" }));
    svg.appendChild(el("circle", { cx: 19.4, cy: 4.9, r: 1.7, fill: "currentColor",
                                   "class": "s1-think-tw s1-think-tw1" }));
    svg.appendChild(el("circle", { cx: 5.1, cy: 19.2, r: 1.3, fill: "currentColor",
                                   "class": "s1-think-tw s1-think-tw2" }));
  }

  /* A dish with a sweeping wedge and a contact that pings. Reads as "we are
     looking at something of theirs", which is what every scan in this Hub
     actually is. */
  function glyphScan(svg) {
    svg.appendChild(el("circle", { cx: 12, cy: 12, r: 9.2, fill: "none",
                                   stroke: "currentColor", "stroke-width": 1.6,
                                   opacity: 0.3 }));
    svg.appendChild(el("circle", { cx: 12, cy: 12, r: 4.4, fill: "none",
                                   stroke: "currentColor", "stroke-width": 1.4,
                                   opacity: 0.22 }));
    svg.appendChild(el("path", { d: "M12 12 L12 2.8 A9.2 9.2 0 0 1 20.5 8.6 Z",
                                 fill: "currentColor", opacity: 0.55,
                                 "class": "s1-think-sweep" }));
    svg.appendChild(el("circle", { cx: 16.3, cy: 7.9, r: 1.7, fill: "currentColor",
                                   "class": "s1-think-ping" }));
  }

  /* The arc. What all nine copies drew, kept because for a two-second read
     it is exactly right and anything livelier is a distraction. */
  function glyphWait(svg) {
    svg.appendChild(el("circle", { cx: 12, cy: 12, r: 8.6, fill: "none",
                                   stroke: "currentColor", "stroke-width": 2.4,
                                   opacity: 0.25 }));
    svg.appendChild(el("circle", { cx: 12, cy: 12, r: 8.6, fill: "none",
                                   stroke: "currentColor", "stroke-width": 2.4,
                                   "stroke-linecap": "round",
                                   "stroke-dasharray": "16 38",
                                   "class": "s1-think-arc" }));
  }

  var DRAW = { ai: glyphAI, scan: glyphScan, wait: glyphWait };

  function kindOf(node) {
    /* Declared on the element or on any ancestor — one attribute on a panel
       covers every control inside it, so a button added there next month is
       right without being told. */
    var n = node;
    while (n && n.getAttribute) {
      var k = n.getAttribute("data-s1-thinking");
      if (k && KINDS.indexOf(k) >= 0) return k;
      n = n.parentNode;
    }
    return "wait";
  }

  function mark(kind) {
    var svg = el("svg", { viewBox: "0 0 24 24", "aria-hidden": "true",
                          focusable: "false",
                          "class": "s1-think-svg s1-think-" + kind });
    (DRAW[kind] || glyphWait)(svg);
    return svg;
  }

  /* The class names actually in use, and only those. Five spellings of a
     spinner exist in this repo; three of them are a mark and are listed here.
     The rule is hub/config.py's ALIASES rule, and it is the same rule for the
     same reason: a speculative name costs nothing to resolve and a great deal
     to police. `.search-spinner` is Google Finder's, and is already an SVG of
     its own; `.spin-cap` is stadium's caption text, which is not a mark at
     all. Both are left alone rather than added on the chance they might one
     day mean this.

     `[data-s1-think]` is how a screen asks for a specific glyph on an element
     that carries no spinner class at all. */
  var SELECTOR = ".spin, .spinner, .cb-spinner, [data-s1-think]";

  /* ------------------------------------------------------------- upgrading
     Every `.spin` and `.spinner` already on a page becomes the glyph. The
     border those nine stylesheets draw is neutralised by `.s1-think` in
     hub-help.css rather than by clearing inline styles here: a template that
     sets `border-color:#fff` on its spinner is describing a color, and
     currentColor is now what answers that, so overriding the rule is the
     honest fix and stripping the author's attribute is not. */

  function upgrade(root) {
    var scope = root || document;
    var found;
    try {
      found = scope.querySelectorAll(SELECTOR);
    } catch (e) { return; }
    Array.prototype.forEach.call(found, function (node) {
      if (node.getAttribute("data-s1-upgraded")) return;
      /* Two of the nine are a layout box rather than a mark — stadium's
         `.spinner` is a 340px centering grid with the caption inside it. A
         node with element children of its own is a container, and replacing
         its contents would take the caption with it. */
      if (node.firstElementChild) {
        node.setAttribute("data-s1-upgraded", "skipped");
        return;
      }
      node.setAttribute("data-s1-upgraded", "1");
      node.classList.add("s1-think");
      node.appendChild(mark(node.getAttribute("data-s1-think") || kindOf(node)));
    });
  }

  /* --------------------------------------------------------------- attach
     The API a call site uses when it wants a label, stages and the elapsed
     line rather than a bare mark. */

  function fmt(ms) {
    var s = Math.round(ms / 1000);
    if (s < 60) return s + "s";
    return Math.floor(s / 60) + "m " + (s % 60) + "s";
  }

  function attach(target, opts) {
    var o = opts || {};
    var host = typeof target === "string"
      ? document.getElementById(target) || document.querySelector(target)
      : target;

    /* A handle is returned even when there is nothing to draw on, so a
       caller's finally { h.done(); } is safe and never has to null-check. */
    var handle = {
      stage: function () { return handle; },
      done: function () { return handle; },
      el: null
    };
    if (!host || !host.appendChild) return handle;

    var kind = o.kind || kindOf(host);
    var box, label, clock, timers = [];

    try {
      box = document.createElement("div");
      box.className = "s1-thinking s1-thinking-" + kind;
      box.setAttribute("role", "status");
      box.setAttribute("aria-live", "polite");
      box.appendChild(mark(kind));

      label = document.createElement("span");
      label.className = "s1-thinking-label";
      label.textContent = o.label || "Working…";
      box.appendChild(label);

      clock = document.createElement("span");
      clock.className = "s1-thinking-clock";
      box.appendChild(clock);

      if (o.replace === false) host.appendChild(box);
      else { host.innerHTML = ""; host.appendChild(box); }
      handle.el = box;

      var started = Date.now();
      timers.push(setInterval(function () {
        /* Stops itself the moment its box leaves the page.
           Half this Hub draws a panel by assigning innerHTML or textContent
           over whatever was there, which is the ordinary way one of these is
           taken down — and a caller that ends a wait that way has not done
           anything wrong. Requiring fifty call sites to remember .done() is
           how one of them forgets and leaves a timer running for the life of
           the tab; asking the timer whether it is still attached costs one
           property read a second and cannot be forgotten. */
        if (box && box.isConnected === false) { handle.done(); return; }
        var ms = Date.now() - started;
        /* Silent until SLOW_AT. A stopwatch on a quick read is noise, and
           one that starts at zero teaches people to expect a wait. */
        clock.textContent = ms < SLOW_AT ? "" : fmt(ms);
      }, TICK));

      /* Stages: [{at: ms, note: "…"}], the ads generator's arrangement, so
         a long call says what it is doing rather than counting at you. */
      (o.stages || []).forEach(function (s) {
        timers.push(setTimeout(function () {
          if (label) label.textContent = s.note;
        }, s.at || 0));
      });
    } catch (e) { return handle; }

    handle.stage = function (text) {
      try { if (label && text) label.textContent = text; } catch (e) {}
      return handle;
    };
    /* Stops and removes. Deliberately does not write "Done" or draw a tick:
       whether the call succeeded is the caller's answer, and a tick here
       over a failed one is a wrong answer that looks exactly like a right
       one. */
    handle.done = function () {
      try {
        timers.forEach(function (t) { clearTimeout(t); clearInterval(t); });
        timers = [];
        if (box && box.parentNode) box.parentNode.removeChild(box);
      } catch (e) {}
      return handle;
    };
    return handle;
  }

  /* ----------------------------------------------------------------- busy
     A button that keeps its width while it works. The ordinary shape across
     this Hub is `btn.disabled = true; btn.innerHTML = '<span class="spin">…'`
     written out longhand, which loses the original label and re-enables the
     button in whichever of the two exit paths the author remembered. */
  function busy(button, opts) {
    var o = opts || {};
    var btn = typeof button === "string"
      ? document.getElementById(button) || document.querySelector(button)
      : button;
    var handle = { stage: function () { return handle; },
                   done: function () { return handle; }, el: null };
    if (!btn) return handle;

    var was = btn.innerHTML, wasDisabled = btn.disabled;
    var label;
    try {
      var kind = o.kind || kindOf(btn);
      btn.disabled = true;
      btn.classList.add("s1-thinking-btn");
      btn.innerHTML = "";
      btn.appendChild(mark(kind));
      label = document.createElement("span");
      label.textContent = o.label || "Working…";
      btn.appendChild(label);
      handle.el = btn;
    } catch (e) { return handle; }

    handle.stage = function (text) {
      try { if (label && text) label.textContent = text; } catch (e) {}
      return handle;
    };
    handle.done = function () {
      try {
        btn.innerHTML = was;
        btn.disabled = wasDisabled;
        btn.classList.remove("s1-thinking-btn");
      } catch (e) {}
      return handle;
    };
    return handle;
  }

  /* Markup, for a template that wants the glyph in a string it is building.
     Half this Hub draws its panels with innerHTML and a template literal,
     and handing those a DOM node would mean rewriting the panel. */
  function html(kind, label) {
    var k = KINDS.indexOf(kind) >= 0 ? kind : "wait";
    var box = document.createElement("div");
    box.className = "s1-thinking s1-thinking-" + k;
    box.setAttribute("role", "status");
    box.setAttribute("aria-live", "polite");
    box.appendChild(mark(k));
    if (label) {
      var s = document.createElement("span");
      s.className = "s1-thinking-label";
      s.textContent = label;
      box.appendChild(s);
    }
    return box.outerHTML;
  }

  function watch() {
    if (!window.MutationObserver) return;
    var timer = null;
    /* Debounced, for the reason hub-help.js gives about its bubbles: Client
       360, the SEO client page and half the tools draw their panels from a
       fetch, so a single pass at load upgrades the shell and misses every
       spinner that has not been drawn yet. */
    new MutationObserver(function () {
      clearTimeout(timer);
      timer = setTimeout(function () { upgrade(document); }, 120);
    }).observe(document.body, { childList: true, subtree: true });
  }

  window.S1Think = {
    KINDS: KINDS, SLOW_AT: SLOW_AT,
    attach: attach, busy: busy, html: html, mark: mark,
    upgrade: upgrade, reduced: reduced
  };

  function init() {
    try { upgrade(document); watch(); } catch (e) {}
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
