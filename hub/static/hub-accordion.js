/* Smart 1 Hub — collapsible cards.
 *
 * Client 360 is 16 cards in one column and the SEO dashboard is similar. Most
 * visits need two or three of them, so everything else is scrolling. This
 * turns every .card into a collapsible section with its open/closed state
 * remembered per page.
 *
 * Applied progressively: if the JS never loads, every card stays open and the
 * page behaves exactly as it does now. A collapse control that fails closed
 * would hide content with no way to reach it.
 *
 * Which cards start open is a judgement, not a preference: the ones you need
 * to answer "who is this client and is anything wrong" stay open, and the
 * reference material folds away.
 */
(function () {
  "use strict";

  var OPEN_BY_DEFAULT = [
    "products", "site health", "brand", "work for this client",
    "smart 1 suite account", "website record"
  ];

  function key(title) {
    return "s1acc:" + location.pathname + ":" +
           title.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40);
  }

  function remembered(title) {
    try { return localStorage.getItem(key(title)); } catch (e) { return null; }
  }
  function remember(title, open) {
    try { localStorage.setItem(key(title), open ? "1" : "0"); } catch (e) {}
  }

  function wire(card) {
    var head = card.querySelector(".card-h");
    var body = card.querySelector(".card-b");
    if (!head || !body || head.dataset.s1acc) return;
    head.dataset.s1acc = "1";

    var title = (head.querySelector("h3") || {}).textContent || "";
    var t = title.trim().toLowerCase();
    var saved = remembered(title);
    var open = saved === null
      ? OPEN_BY_DEFAULT.some(function (o) { return t.indexOf(o) === 0; })
      : saved === "1";

    var caret = document.createElement("button");
    caret.type = "button";
    caret.className = "s1-acc-caret";
    caret.setAttribute("aria-label", (open ? "Collapse " : "Expand ") + title);
    caret.setAttribute("aria-expanded", open ? "true" : "false");
    caret.innerHTML = "&#9662;";
    head.insertBefore(caret, head.firstChild);
    head.classList.add("s1-acc-head");

    function set(o) {
      card.classList.toggle("s1-acc-closed", !o);
      caret.setAttribute("aria-expanded", o ? "true" : "false");
      caret.setAttribute("aria-label", (o ? "Collapse " : "Expand ") + title);
      remember(title, o);
    }
    set(open);

    head.addEventListener("click", function (e) {
      // Don't swallow clicks on the buttons and links already in the header.
      if (e.target.closest("a, button:not(.s1-acc-caret), input, select")) return;
      set(card.classList.contains("s1-acc-closed"));
    });
    head.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        set(card.classList.contains("s1-acc-closed"));
      }
    });
    head.tabIndex = 0;
  }

  function scan() {
    document.querySelectorAll(".card").forEach(wire);
  }

  function addControls() {
    if (document.querySelector(".s1-acc-all")) return;
    var first = document.querySelector(".card");
    if (!first || !first.parentNode) return;
    var bar = document.createElement("div");
    bar.className = "s1-acc-all";
    bar.innerHTML = '<button type="button" data-all="open">Expand all</button>' +
                    '<button type="button" data-all="close">Collapse all</button>';
    first.parentNode.insertBefore(bar, first);
    bar.addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      var open = b.dataset.all === "open";
      document.querySelectorAll(".card").forEach(function (c) {
        c.classList.toggle("s1-acc-closed", !open);
        var t = ((c.querySelector(".card-h h3") || {}).textContent || "").trim();
        if (t) remember(t, open);
        var cr = c.querySelector(".s1-acc-caret");
        if (cr) cr.setAttribute("aria-expanded", open ? "true" : "false");
      });
    });
  }

  function init() {
    // Cards on these pages are rendered by JS after a fetch, so watch for them
    // rather than assuming they exist at DOMContentLoaded.
    scan(); addControls();
    var mo = new MutationObserver(function () { scan(); addControls(); });
    mo.observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.S1Accordion = { scan: scan };
})();
