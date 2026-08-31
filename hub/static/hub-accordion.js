/* Smart 1 Hub — collapsible, reorderable cards.
 *
 * Rewritten to be self-contained. The previous version relied on rules in
 * hub-help.css, and when that stylesheet didn't reach a page the carets
 * appeared but nothing collapsed — a control that looks live and does nothing
 * is worse than no control. Collapse is now an inline style and the styling is
 * injected by this file, so it cannot half-work.
 *
 * Only runs on the long record pages. The dashboard is four cards you read at
 * a glance; collapsing there adds a control without saving any scrolling.
 */
(function () {
  "use strict";

  var ONLY_ON = ["/client360", "/seo", "/qa"];

  /* Card order and default state, keyed on a distinctive word from the title.
   * Order is the reading order of the page: who is this client, what are we
   * doing for them, what have we made, is anything broken — then reference
   * material. Anything not listed keeps its original position, so a card
   * added later doesn't vanish or need registering here. */
  var LAYOUT = [
    { match: "products",       open: true  },
    { match: "creative infor", open: true  },
    { match: "client images",  open: true  },   // moved above analytics
    { match: "brand",          open: true,  half: true },
    { match: "tracked links",  open: true,  half: true },
    { match: "web tickets",    open: false, half: true },
    { match: "client notes",   open: true,  half: true },  // paired with brand
    { match: "work for this",  open: true  },
    { match: "site health",    open: true  },
    { match: "smart 1 suite",  open: true  },
    { match: "proposals",      open: true  },
    { match: "social media",   open: true,  full: true },
    { match: "traffic summary",open: true  },
    { match: "ga4",            open: false },   // auto-collapsed
    { match: "gtm container",  open: false },   // auto-collapsed, moved down
    { match: "invoices",       open: false },   // auto-collapsed
    { match: "website record", open: false },   // auto-collapsed

    /* SEO client page. The long working sections fold away by default —
       Schema Builder, FAQ Builder and Blogs are each a workspace you open
       deliberately, not something to scroll past every visit. */
    { match: "accounts",       open: true  },
    { match: "website",        open: true  },
    { match: "ai engine",      open: true  },
    { match: "schema check",   open: true  },
    /* These default OPEN. They were collapsed so the page wasn't a long
       scroll — but a feature nobody has seen yet, folded shut, is invisible:
       you don't expand a section you don't know exists. Collapse is still
       there per card, and the choice is remembered. Once someone has
       collapsed one deliberately, that sticks. */
    { match: "schema questions", open: true },
    { match: "schema builder", open: true },
    { match: "faq builder",    open: true },
    { match: "blogs",          open: true },
    { match: "llms.txt",       open: true }
  ];

  var CSS = [
    ".s1-acc-head{cursor:pointer;user-select:none}",
    ".s1-acc-head:focus-visible{outline:2px solid #1769AA;outline-offset:2px}",
    ".s1-acc-caret{border:0;background:none;cursor:pointer;color:#94a3b8;",
    "font-size:12px;padding:0 7px 0 0;line-height:1;transition:transform .15s}",
    ".s1-acc-closed .s1-acc-caret{transform:rotate(-90deg)}",
    /* Wraps. This bar carries Expand all, Collapse all, Group and three IO
       actions; on a narrow screen an unwrapped row is what pushes the page
       sideways even after the cards below it behave. */
    ".s1-acc-all{display:flex;flex-wrap:wrap;gap:8px;margin:0 0 14px;grid-column:1 / -1}",
    ".s1-acc-all button{border:1px solid #d7dfe8;background:#fff;border-radius:7px;",
    "padding:6px 12px;font:600 12px 'Segoe UI',system-ui,sans-serif;color:#41525f;cursor:pointer}",
    ".s1-acc-all button:hover{background:#f1f5f9}",
    ".s1-acc-actions{margin-left:auto;display:flex;flex-wrap:wrap;gap:8px}",
    "@media(max-width:700px){.s1-acc-actions{margin-left:0}}",
    ".s1-acc-actions button{border:1px solid #cfe2f2;background:#eef5fb;color:#1769AA}",
    ".s1-acc-actions button:hover{background:#e2eef8}",
    ".s1-acc-all button.s1-acc-group{border-color:#cfe2f2;background:#eef5fb;color:#1769AA}",
    ".s1-acc-all button.s1-acc-group:hover{background:#e2eef8}",
    ".s1-acc-all button.s1-acc-group.on{border-color:#b7e0c2;background:#eaf7ee;color:#1c7a3e}",
    "@media(prefers-reduced-motion:reduce){.s1-acc-caret{transition:none}}"
  ].join("");

  function injectCSS() {
    if (document.getElementById("s1-acc-css")) return;
    var st = document.createElement("style");
    st.id = "s1-acc-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function pageWants() {
    var p = location.pathname.replace(/\/+$/, "") || "/";
    return ONLY_ON.some(function (x) { return p === x || p.indexOf(x + "/") === 0; });
  }

  /* A page that manages its own sections opts the whole accordion out by
     carrying data-s1-workspace on its layout root. This is not cosmetic:
     reorder() below appends EVERY .card into the first card's parent, which
     on a page whose cards live in per-section views would tear all of them
     out of their sections and pile them into the first one — the layout
     destroyed by a script wired for the flat page it used to be. And the
     Expand/Collapse bar saves no scrolling on a page showing one section at
     a time. The SEO client record is the first such page; Client 360 joins
     it. Checked per pass rather than once, because the workspace root on
     Client 360 is rendered from a fetch after this script has already run. */
  function workspace() {
    return !!document.querySelector("[data-s1-workspace]");
  }

  function titleOf(card) {
    var h = card.querySelector(".card-h h3");
    return (h ? h.textContent : "").trim();
  }

  /* Prefer a match at the START of the title, and only fall back to a
     substring match if nothing anchors. Otherwise "brand" would claim any
     card whose title merely contains the word, and card order becomes a
     matter of which rule happens to be listed first. */
  function rule(title) {
    var t = title.toLowerCase();
    var i;
    for (i = 0; i < LAYOUT.length; i++) {
      if (t.indexOf(LAYOUT[i].match) === 0) return LAYOUT[i];
    }
    for (i = 0; i < LAYOUT.length; i++) {
      if (t.indexOf(LAYOUT[i].match) > -1) return LAYOUT[i];
    }
    return null;
  }

  /* Bumped when a default changes. Anyone who loaded the page while these
     sections defaulted to collapsed has that remembered — without a new key
     they would stay hidden forever and the fix would look like it did
     nothing. */
  var STORE_VERSION = "s1acc:v2";

  function storeKey(title) {
    return STORE_VERSION + ":" + location.pathname + ":" +
      title.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 40);
  }
  function saved(title) {
    try { return localStorage.getItem(storeKey(title)); } catch (e) { return null; }
  }
  function save(title, open) {
    try { localStorage.setItem(storeKey(title), open ? "1" : "0"); } catch (e) {}
  }

  /* Collapse via inline style, not a CSS class. No stylesheet dependency
     means this cannot silently fail to work. */
  function setOpen(card, open, title) {
    var body = card.querySelector(".card-b");
    if (body) body.style.display = open ? "" : "none";
    card.classList.toggle("s1-acc-closed", !open);
    var caret = card.querySelector(".s1-acc-caret");
    if (caret) {
      caret.setAttribute("aria-expanded", open ? "true" : "false");
      caret.style.transform = open ? "" : "rotate(-90deg)";
    }
    if (title) save(title, open);
  }

  function wire(card) {
    var head = card.querySelector(".card-h");
    var body = card.querySelector(".card-b");
    if (!head || !body || head.dataset.s1acc) return;
    head.dataset.s1acc = "1";

    var title = titleOf(card);
    var r = rule(title);
    var st = saved(title);
    var open = st === null ? (r ? r.open : true) : st === "1";

    var caret = document.createElement("button");
    caret.type = "button";
    caret.className = "s1-acc-caret";
    caret.innerHTML = "&#9662;";
    caret.setAttribute("aria-label", "Toggle " + title);
    head.insertBefore(caret, head.firstChild);
    head.classList.add("s1-acc-head");
    head.tabIndex = 0;

    setOpen(card, open, null);

    function toggle(e) {
      if (e && e.target.closest("a, input, select, textarea")) return;
      if (e && e.target.closest("button") && !e.target.closest(".s1-acc-caret")) return;
      setOpen(card, card.classList.contains("s1-acc-closed"), title);
    }
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(null); }
    });
  }

  /* Reorder to the reading order in LAYOUT. Cards with no rule keep their
     relative position at the end, so a card added later still appears. */
  function reorder() {
    var cards = Array.prototype.slice.call(document.querySelectorAll(".card"));
    if (cards.length < 3) return;
    var parent = cards[0].parentNode;
    if (!parent || parent.dataset.s1ordered === String(cards.length)) return;

    var ranked = cards.map(function (c, i) {
      var t = titleOf(c).toLowerCase();
      var idx = LAYOUT.findIndex(function (r) { return t.indexOf(r.match) === 0; });
      if (idx === -1) idx = LAYOUT.findIndex(function (r) { return t.indexOf(r.match) > -1; });
      return { card: c, rank: idx === -1 ? 900 + i : idx, i: i };
    });
    ranked.sort(function (a, b) { return a.rank - b.rank || a.i - b.i; });
    ranked.forEach(function (r) { parent.appendChild(r.card); });
    parent.dataset.s1ordered = String(cards.length);

    // Brand and Client Notes sit side by side rather than full width.
    ranked.forEach(function (r) {
      var t = titleOf(r.card).toLowerCase();
      var rule_ = rule(titleOf(r.card));
      if (rule_ && rule_.half) r.card.style.gridColumn = "span 1";
      if (rule_ && rule_.full) r.card.style.gridColumn = "1 / -1";
    });
  }

  function controls() {
    if (document.querySelector(".s1-acc-all")) return;
    var first = document.querySelector(".card");
    if (!first || !first.parentNode) return;
    var bar = document.createElement("div");
    bar.className = "s1-acc-all";
    bar.innerHTML = '<button type="button" data-all="open">Expand all</button>' +
                    '<button type="button" data-all="close">Collapse all</button>';

    /* Group sits hard against Collapse all rather than out with the IO
       actions on the right. It changes what every card on the page is reading
       from — products, creative, notes, work, proposals, invoices — so it
       belongs with the two controls that already act on the whole record.
       Client 360 fills in the label once it knows whether this client is
       grouped; the neutral wording is what shows if that never answers. */
    if (location.pathname.replace(/\/+$/, "") === "/client360" && window.CURRENT_CLIENT) {
      var grp = document.createElement("button");
      grp.type = "button";
      grp.className = "s1-acc-group";
      grp.innerHTML = "&#128279; Group";
      grp.title = "Merge another company's records into this one";
      grp.addEventListener("click", function () {
        if (window.openGroupModal) window.openGroupModal();
      });
      bar.appendChild(grp);
      if (window.S1GroupLabel) window.S1GroupLabel(grp);
    }

    /* Page actions live in the same toolbar rather than bolted into a card
       header — they act on the whole record, not on one card, and a lone
       cluster of buttons inside a card reads as belonging to that card. */
    if (location.pathname.replace(/\/+$/, "") === "/client360" && window.CURRENT_CLIENT) {
      var acts = document.createElement("span");
      acts.className = "s1-acc-actions";
      acts.innerHTML =
        '<button type="button" onclick="ioStart(\'new\')">New IO</button>' +
        '<button type="button" onclick="ioStart(\'renewal\')">Renew IO</button>' +
        '<button type="button" onclick="ioStart(\'proposal\')">IO from proposal</button>';
      bar.appendChild(acts);
    }
    first.parentNode.insertBefore(bar, first);
    bar.addEventListener("click", function (e) {
      var b = e.target.closest("button"); if (!b) return;
      var open = b.dataset.all === "open";
      document.querySelectorAll(".card").forEach(function (c) {
        setOpen(c, open, titleOf(c));
      });
    });
  }

  function pass() {
    if (workspace()) return;
    injectCSS(); reorder(); document.querySelectorAll(".card").forEach(wire); controls();
  }

  function init() {
    if (!pageWants()) return;
    pass();
    // Client 360 renders its cards from a fetch, so wait for them.
    new MutationObserver(pass).observe(document.body, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.S1Accordion = { refresh: pass };
})();
