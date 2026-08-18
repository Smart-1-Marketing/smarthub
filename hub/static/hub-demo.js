/* Smart 1 Hub — guided demos that drive the real tool.
 *
 * This does NOT open a demo copy of a screen. It fills the actual fields on
 * the actual page, so what someone learns here is what they'll do on Monday.
 *
 * Wiring, per tool page:
 *     <body data-module="seo_images">
 *     <button data-demo-start>Walk me through it</button>
 *   and mark the things a step points at:
 *     <button data-demo="analyse">Analyse</button>
 *
 * Steps that would spend a credit are flagged `simulated` server-side; those
 * are narrated and skipped rather than clicked.
 */
(function () {
  "use strict";

  var S = null;   // {scenario, i}

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function el(sel) { try { return sel ? document.querySelector(sel) : null; } catch (e) { return null; } }

  function beacon(payload) {
    try {
      navigator.sendBeacon("/api/demos/event",
        new Blob([JSON.stringify(payload)], { type: "application/json" }));
    } catch (e) {}
  }

  /* ---- setting values so the page's own JS notices ---- */

  function setValue(node, value) {
    if (!node) return;
    var proto = node instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : (node instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype);
    var setter = Object.getOwnPropertyDescriptor(proto, "value");
    if (setter && setter.set) setter.set.call(node, value); else node.value = value;
    // Frameworks and hand-rolled listeners both watch these.
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function typeInto(node, value, done) {
    // Typing character by character so any live preview updates visibly —
    // the UTM builder's normalisation is only convincing if you watch it happen.
    if (!node) return done && done();
    node.focus();
    setValue(node, "");
    var i = 0;
    (function tick() {
      if (i >= value.length) { return done && done(); }
      setValue(node, value.slice(0, ++i));
      setTimeout(tick, 22);
    })();
  }

  /* ---- overlay ---- */

  function mount() {
    if (document.querySelector(".s1-demo-panel")) return;
    var p = document.createElement("div");
    p.className = "s1-demo-panel";
    p.setAttribute("role", "dialog");
    p.setAttribute("aria-live", "polite");
    p.innerHTML =
      '<div class="s1-demo-head">' +
        '<span class="s1-demo-scenario"></span>' +
        '<span class="s1-demo-count"></span>' +
        '<button type="button" class="s1-demo-close" aria-label="End walkthrough">&times;</button>' +
      "</div>" +
      '<div class="s1-demo-bar"><i></i></div>' +
      '<h4 class="s1-demo-title"></h4>' +
      '<p class="s1-demo-body"></p>' +
      '<p class="s1-demo-notice"></p>' +
      '<div class="s1-demo-sim" hidden>Simulated here so it doesn\'t spend a credit.</div>' +
      '<div class="s1-demo-actions">' +
        '<button type="button" class="s1-demo-back">Back</button>' +
        '<button type="button" class="s1-demo-do">Do it for me</button>' +
        '<button type="button" class="s1-demo-next">Next</button>' +
      "</div>";
    document.body.appendChild(p);

    var ring = document.createElement("div");
    ring.className = "s1-demo-ring";
    document.body.appendChild(ring);

    p.querySelector(".s1-demo-close").onclick = function () { stop("closed"); };
    p.querySelector(".s1-demo-next").onclick = function () { next(); };
    p.querySelector(".s1-demo-back").onclick = function () { back(); };
    p.querySelector(".s1-demo-do").onclick = function () { perform(); };
  }

  function highlight(sel) {
    var ring = document.querySelector(".s1-demo-ring");
    var t = el(sel);
    if (!t) { ring.style.display = "none"; return; }
    var r = t.getBoundingClientRect();
    ring.style.display = "block";
    ring.style.top = (r.top + window.scrollY - 5) + "px";
    ring.style.left = (r.left + window.scrollX - 5) + "px";
    ring.style.width = (r.width + 10) + "px";
    ring.style.height = (r.height + 10) + "px";
    if (r.top < 90 || r.bottom > window.innerHeight - 200) {
      t.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function paint() {
    if (!S) return;
    var sc = S.scenario, st = sc.steps[S.i], p = document.querySelector(".s1-demo-panel");
    p.querySelector(".s1-demo-scenario").textContent = sc.title;
    p.querySelector(".s1-demo-count").textContent = (S.i + 1) + "/" + sc.steps.length;
    p.querySelector(".s1-demo-bar i").style.width =
      Math.round(((S.i + 1) / sc.steps.length) * 100) + "%";
    p.querySelector(".s1-demo-title").textContent = st.title;
    p.querySelector(".s1-demo-body").textContent = st.body;

    var n = p.querySelector(".s1-demo-notice");
    n.textContent = st.notice || "";
    n.hidden = !st.notice;

    p.querySelector(".s1-demo-sim").hidden = !st.simulated;
    p.querySelector(".s1-demo-back").disabled = S.i === 0;

    var doBtn = p.querySelector(".s1-demo-do");
    var actionable = ["fill", "choose", "click", "upload"].indexOf(st.action) > -1;
    doBtn.hidden = !actionable || st.autofill === false || st.simulated;
    doBtn.textContent = st.action === "fill" ? "Fill it in"
                      : st.action === "click" ? "Click it"
                      : st.action === "upload" ? "Load the sample"
                      : "Choose it";

    p.querySelector(".s1-demo-next").textContent =
      S.i === sc.steps.length - 1 ? "Finish" : "Next";

    highlight(st.selector);
  }

  function perform() {
    if (!S) return;
    var st = S.scenario.steps[S.i], node = el(st.selector);
    if (!node) return;
    if (st.action === "fill") { typeInto(node, st.value); }
    else if (st.action === "choose") { setValue(node, st.value); }
    else if (st.action === "click") { node.click(); }
    else if (st.action === "upload" && window.S1Demo.loadSample) {
      window.S1Demo.loadSample(node, st.sample);
    }
    beacon({ scenario: S.scenario.key, step: S.i + 1, action: "performed" });
  }

  function next() {
    if (!S) return;
    if (S.i === S.scenario.steps.length - 1) return stop("finished");
    S.i++;
    beacon({ scenario: S.scenario.key, step: S.i + 1, action: "next" });
    paint();
  }

  function back() { if (S && S.i > 0) { S.i--; paint(); } }

  function stop(why) {
    if (S) beacon({ scenario: S.scenario.key, step: S.i + 1, action: why || "stopped" });
    document.querySelectorAll(".s1-demo-panel,.s1-demo-ring").forEach(function (n) { n.remove(); });
    S = null;
  }

  function start(key) {
    return fetch("/api/demos/" + encodeURIComponent(key), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (sc) {
        if (!sc || !sc.steps || !sc.steps.length) return false;
        S = { scenario: sc, i: 0 };
        mount(); paint();
        beacon({ scenario: sc.key, step: 1, action: "started" });
        return true;
      })
      .catch(function () { return false; });
  }

  window.addEventListener("resize", function () { if (S) paint(); });
  window.addEventListener("scroll", function () { if (S) highlight(S.scenario.steps[S.i].selector); },
                          { passive: true });
  document.addEventListener("keydown", function (e) {
    if (!S) return;
    if (e.key === "Escape") stop("escaped");
    if (e.key === "ArrowRight" && e.altKey) next();
    if (e.key === "ArrowLeft" && e.altKey) back();
  });

  function autoLauncher(mod) {
    // Only render the button if this tool actually has a walkthrough, so a
    // module without one shows nothing rather than a button that fails.
    fetch("/api/demos?module=" + encodeURIComponent(mod), { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.scenarios || !d.scenarios.length) return;
        if (document.querySelector("[data-demo-start]")) return;  // page has its own
        var sc = d.scenarios[0];
        var b = document.createElement("button");
        b.type = "button";
        b.className = "s1-demo-fab";
        b.title = sc.title + " — about " + sc.minutes + " min";
        b.innerHTML = '<span aria-hidden="true">\u25b6</span> Walk me through this';
        b.addEventListener("click", function () { start(sc.key, true); });
        document.body.appendChild(b);
      })
      .catch(function () { /* the launcher is never load-bearing */ });
  }

  function init() {
    var mod = document.body.getAttribute("data-module");
    if (mod) autoLauncher(mod);
    document.querySelectorAll("[data-demo-start]").forEach(function (b) {
      b.addEventListener("click", function () {
        var key = b.getAttribute("data-demo-start");
        if (key) return start(key);
        if (!mod) return;
        fetch("/api/demos?module=" + encodeURIComponent(mod), { credentials: "same-origin" })
          .then(function (r) { return r.json(); })
          .then(function (d) { if (d.scenarios && d.scenarios[0]) start(d.scenarios[0].key); });
      });
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.S1Demo = { start: start, stop: stop, loadSample: null };
})();
