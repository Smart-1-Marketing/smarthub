/* Smart 1 Hub — help bubbles + guided tours.
 *
 * Vendored deliberately, no CDN. The Image Creator already lost an afternoon
 * to Fabric.js being loaded from a CDN that is blocked in the deploy
 * environment, where it failed silently. Nothing here loads anything external.
 *
 * Markup contract:
 *     <span data-help="utm.form.medium"></span>      -> renders a ? bubble
 *     <div  data-tour="utm-medium">                  -> a tour highlight target
 *
 * Content comes from GET /api/help (hub/help.py). One source of truth.
 */
(function () {
  "use strict";
  var HELP = {}, TOURS = {}, ready = false;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  /* ---------------- bubbles ---------------- */

  function bubble(el, item) {
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "s1-help-dot";
    btn.setAttribute("aria-label", "Help: " + item.title);
    btn.setAttribute("aria-expanded", "false");
    btn.textContent = "?";

    var pop = document.createElement("span");
    pop.className = "s1-help-pop";
    pop.setAttribute("role", "tooltip");
    pop.hidden = true;
    pop.innerHTML =
      '<strong>' + esc(item.title) + "</strong><span>" + esc(item.body) + "</span>" +
      (item.link ? '<a href="' + esc(item.link) + '">' + esc(item.linkText || "Learn more") + "</a>" : "");

    function close() { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); }
    function open() {
      document.querySelectorAll(".s1-help-pop").forEach(function (p) { p.hidden = true; });
      pop.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      // Flip to the left if it would run off the right edge.
      var r = pop.getBoundingClientRect();
      pop.classList.toggle("s1-flip", r.right > window.innerWidth - 8);
    }

    btn.addEventListener("click", function (e) {
      e.preventDefault(); e.stopPropagation();
      pop.hidden ? open() : close();
    });
    btn.addEventListener("keydown", function (e) { if (e.key === "Escape") close(); });

    var wrap = document.createElement("span");
    wrap.className = "s1-help";
    wrap.appendChild(btn);
    wrap.appendChild(pop);
    el.replaceWith(wrap);
  }

  document.addEventListener("click", function () {
    document.querySelectorAll(".s1-help-pop").forEach(function (p) { p.hidden = true; });
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") document.querySelectorAll(".s1-help-pop").forEach(function (p) { p.hidden = true; });
  });

  function mountBubbles(root) {
    (root || document).querySelectorAll("[data-help]").forEach(function (el) {
      var item = HELP[el.getAttribute("data-help")];
      if (item) bubble(el, item);
      else el.remove();     // never leave a dead ? on the page
    });
  }

  /* ---------------- tour ---------------- */

  var tourState = null;

  function seen(screen) {
    try { return localStorage.getItem("s1tour:" + screen) === "done"; }
    catch (e) { return false; }
  }
  function markSeen(screen) {
    try { localStorage.setItem("s1tour:" + screen, "done"); } catch (e) {}
  }

  function report(screen, step, action) {
    // Where people quit a tour is a UX bug report you'd never otherwise get.
    try {
      navigator.sendBeacon("/api/help/tour-event", new Blob(
        [JSON.stringify({ screen: screen, step: step, action: action })],
        { type: "application/json" }));
    } catch (e) {}
  }

  function teardown() {
    document.querySelectorAll(".s1-tour-layer,.s1-tour-ring").forEach(function (n) { n.remove(); });
    document.body.classList.remove("s1-tour-open");
    tourState = null;
  }

  function paint() {
    var st = tourState;
    if (!st) return;
    var step = st.steps[st.i];
    var target = step.selector ? document.querySelector(step.selector) : null;

    var ring = document.querySelector(".s1-tour-ring");
    if (target) {
      var r = target.getBoundingClientRect();
      ring.style.display = "block";
      ring.style.top = (r.top + window.scrollY - 6) + "px";
      ring.style.left = (r.left + window.scrollX - 6) + "px";
      ring.style.width = (r.width + 12) + "px";
      ring.style.height = (r.height + 12) + "px";
      if (r.top < 80 || r.bottom > window.innerHeight - 80) {
        target.scrollIntoView({ behavior: "smooth", block: "center" });
      }
    } else {
      ring.style.display = "none";   // step survives a missing element
    }

    var card = document.querySelector(".s1-tour-card");
    card.querySelector(".s1-tour-count").textContent = (st.i + 1) + " of " + st.steps.length;
    card.querySelector(".s1-tour-title").textContent = step.title;
    card.querySelector(".s1-tour-body").textContent = step.body;
    card.querySelector(".s1-tour-back").disabled = st.i === 0;
    card.querySelector(".s1-tour-next").textContent =
      st.i === st.steps.length - 1 ? "Done" : "Next";
  }

  function start(screen, force) {
    var steps = TOURS[screen] || [];
    if (!steps.length) return false;
    if (!force && seen(screen)) return false;

    var layer = document.createElement("div");
    layer.className = "s1-tour-layer";
    layer.innerHTML =
      '<div class="s1-tour-card" role="dialog" aria-modal="true" aria-labelledby="s1tt">' +
        '<div class="s1-tour-count"></div>' +
        '<h3 class="s1-tour-title" id="s1tt"></h3>' +
        '<p class="s1-tour-body"></p>' +
        '<div class="s1-tour-actions">' +
          '<button type="button" class="s1-tour-skip">Skip</button>' +
          '<span class="s1-tour-spacer"></span>' +
          '<button type="button" class="s1-tour-back">Back</button>' +
          '<button type="button" class="s1-tour-next">Next</button>' +
        "</div></div>";
    var ring = document.createElement("div");
    ring.className = "s1-tour-ring";
    document.body.appendChild(layer);
    document.body.appendChild(ring);
    document.body.classList.add("s1-tour-open");

    tourState = { screen: screen, steps: steps, i: 0 };

    layer.querySelector(".s1-tour-next").addEventListener("click", function () {
      if (tourState.i === tourState.steps.length - 1) {
        report(screen, tourState.i + 1, "finished"); markSeen(screen); teardown();
      } else { tourState.i++; report(screen, tourState.i, "next"); paint(); }
    });
    layer.querySelector(".s1-tour-back").addEventListener("click", function () {
      if (tourState.i > 0) { tourState.i--; paint(); }
    });
    layer.querySelector(".s1-tour-skip").addEventListener("click", function () {
      report(screen, tourState.i + 1, "skipped"); markSeen(screen); teardown();
    });
    document.addEventListener("keydown", function esc(e) {
      if (!tourState) { document.removeEventListener("keydown", esc); return; }
      if (e.key === "Escape") { report(screen, tourState.i + 1, "escaped"); teardown(); }
    });
    window.addEventListener("resize", paint);
    window.addEventListener("scroll", paint, { passive: true });

    paint();
    return true;
  }

  /* ---------------- boot ---------------- */

  function init() {
    fetch("/api/help", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        HELP = d.help || {};
        (d.screens || []).forEach(function (s) { TOURS[s] = d.tours ? d.tours[s] : null; });
        if (d.tours) TOURS = d.tours;
        ready = true;
        mountBubbles(document);
        var screen = document.body.getAttribute("data-screen");
        if (screen) start(screen, false);
        document.querySelectorAll("[data-tour-start]").forEach(function (b) {
          b.addEventListener("click", function () {
            start(b.getAttribute("data-tour-start") || screen, true);
          });
        });
      })
      .catch(function () { /* help is never load-bearing */ });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }

  window.S1Help = {
    start: function (s) { return start(s, true); },
    mount: mountBubbles,
    ready: function () { return ready; }
  };
})();
