/* Smart 1 Hub — breadcrumbs.
 *
 * The Hub is 20-odd tools reached from several index pages, and once you're
 * inside one there's no way back except the sidebar or the browser button.
 * That's fine until a tool opens from Client 360 or a QA report, where "back"
 * means a specific client record you'd otherwise have to search for again.
 *
 * Two parts:
 *   1. A trail derived from the URL, so every page has one without being
 *      edited. /tools/seo-images/ -> Hub / Tools / SEO Image Pipeline.
 *   2. A remembered origin. Arriving from another Hub page records it, so the
 *      first crumb is where you actually came from rather than a guess.
 *
 * Deliberately not rendered on pages that already have their own crumb —
 * duplicating it is worse than not having one.
 *
 * Part 3 is the one that had to be built: **back to the client record**.
 * A generic "where you came from" crumb says "Client 360", which is the page
 * and not the record — and it is lost the moment you click once more inside
 * the tool. So Client 360 stamps the client's name onto every link that
 * leaves it, this script remembers it for the tab, and every page downstream
 * carries "Back to <client>" until you actually go back. One script, loaded
 * on hub pages by base.html and injected into all 20 mounted modules by
 * HubBar, so a tool linked from Client 360 next month gets it without being
 * edited — the alternative was a back link written into twenty templates,
 * nineteen of which would have been forgotten.
 */
(function () {
  "use strict";

  var LABELS = {
    "tools": "Tools", "qa": "QA Reports", "scans": "Site Scans",
    "client360": "Client 360", "seo": "SEO Clients", "diagnostics": "Diagnostics",
    "activity": "Activity Log", "status": "System Status", "sites": "Sites",
    "suite": "Suite", "google": "Google", "clients": "Clients",
    "seo-images": "SEO Image Pipeline", "image-creator": "Image Creator",
    "bg-remover": "Background Remover", "utm": "UTM Builder",
    "image": "Image Optimizer", "pdf": "PDF Optimizer",
    "radio-promo": "Radio Promo", "landing-ads": "Landing Page Ads",
    "fan-radio": "Fan Radio", "tickets": "Web Tickets",
    "calculators": "Calculators", "page-images": "Page Images",
    "google-access": "Google Access",
    "image-picker": "Client Image Uploads",
    "sites-match": "Match Sites", "commercial-builder": "Commercial Builder",
    "stale-creative": "Stale Creative", "bulk": "Scan All Clients",
    "users": "Users", "builder": "Sales Builder", "proposals": "Proposal Builder"
  };

  // Which index page each tool belongs under, so the trail is meaningful
  // rather than just a copy of the URL.
  var PARENT = {
    "tools": null, "qa": null, "scans": null, "client360": null, "seo": null,
    "diagnostics": null, "activity": null, "status": null,
    "stale-creative": ["/qa", "QA Reports"], "tickets": ["/tools", "Tools"],
    "calculators": ["/tools", "Tools"], "google-access": ["/tools", "Tools"],
    "sites-match": ["/tools", "Tools"], "bulk": ["/tools", "Tools"]
  };

  // The creative tools moved off the Tools index onto /creative, but their
  // URLs stayed under /tools/, so the trail kept sending people back to a page
  // their tool is no longer listed on.
  var CREATIVE = ["seo-images", "image-creator", "bg-remover", "image",
                  "image-picker", "radio-promo", "fan-radio", "landing-ads",
                  "page-images", "commercial-builder"];
  CREATIVE.forEach(function (k) { PARENT[k] = ["/creative", "Creative"]; });

  function pretty(seg) {
    return LABELS[seg] ||
      seg.replace(/-/g, " ").replace(/\b\w/g, function (m) { return m.toUpperCase(); });
  }

  function remember() {
    // Record where we were, so the next page can offer a real "back".
    try {
      var here = location.pathname + location.search;
      var prev = sessionStorage.getItem("s1crumb:here");
      if (prev && prev !== here) sessionStorage.setItem("s1crumb:from", prev);
      sessionStorage.setItem("s1crumb:here", here);
    } catch (e) {}
  }

  function origin() {
    try {
      var f = sessionStorage.getItem("s1crumb:from");
      if (!f || f === location.pathname + location.search) return null;
      // Only offer it when it's a different page, and give it a real name.
      var seg = f.replace(/^\/+/, "").split(/[/?]/)[0];
      if (!seg) return { href: f, label: "Dashboard" };
      return { href: f, label: pretty(seg) };
    } catch (e) { return null; }
  }

  function build() {
    var path = location.pathname.replace(/\/+$/, "");
    if (!path || path === "") return null;          // dashboard needs none
    var segs = path.replace(/^\/+/, "").split("/").filter(Boolean);
    if (!segs.length) return null;

    var crumbs = [{ href: "/", label: "Dashboard" }];

    var back = origin();
    var last = segs[segs.length - 1];
    var first = segs[0];
    var key = (first === "tools" && segs[1]) ? segs[1] : first;
    var parent = PARENT[key];

    if (parent) {
      crumbs.push({ href: parent[0], label: parent[1] });
    } else if (first === "tools" && segs.length > 1) {
      crumbs.push({ href: "/tools", label: "Tools" });
    } else if (first === "qa" && segs.length > 1) {
      crumbs.push({ href: "/qa", label: "QA Reports" });
    } else if (first === "scans" && segs.length > 1) {
      crumbs.push({ href: "/scans/", label: "Site Scans" });
    }

    crumbs.push({ href: null, label: pretty(key === first ? first : key) });

    // If we came from somewhere that isn't already in the trail, surface it.
    if (back && !crumbs.some(function (c) { return c.href === back.href; })) {
      crumbs.splice(1, 0, { href: back.href, label: back.label, from: true });
    }
    return crumbs;
  }

  function render() {
    if (document.querySelector(".s1-crumbs")) return;
    // Respect a crumb the page already provides.
    if (document.querySelector(".c360-head, .sm-crumb, .diag-crumb, .qa-crumb")) return;
    var crumbs = build();
    if (!crumbs || crumbs.length < 2) return;

    var nav = document.createElement("nav");
    nav.className = "s1-crumbs";
    nav.setAttribute("aria-label", "Breadcrumb");
    nav.innerHTML = crumbs.map(function (c, i) {
      var sep = i ? '<span class="s1-crumb-sep">/</span>' : "";
      if (!c.href) return sep + '<span aria-current="page">' + c.label + "</span>";
      return sep + '<a href="' + c.href + '"' +
        (c.from ? ' class="s1-crumb-back" title="Where you came from"' : "") +
        ">" + (c.from ? "&#8592; " : "") + c.label + "</a>";
    }).join("");

    var host = document.querySelector(".page, main, .main") || document.body;
    host.insertBefore(nav, host.firstChild);
  }

  function css() {
    if (document.getElementById("s1-crumbs-css")) return;
    var st = document.createElement("style");
    st.id = "s1-crumbs-css";
    st.textContent =
      ".s1-crumbs{font:12.5px/1.5 'Segoe UI',system-ui,sans-serif;color:#94a3b8;" +
      "margin:0 0 14px;display:flex;flex-wrap:wrap;align-items:center;gap:6px}" +
      ".s1-crumbs a{color:#1769AA;text-decoration:none}" +
      ".s1-crumbs a:hover{text-decoration:underline}" +
      ".s1-crumbs .s1-crumb-back{font-weight:600}" +
      ".s1-crumb-sep{color:#cbd5e1}" +
      ".s1-c360-back{display:inline-flex;align-items:center;gap:8px;margin:0 0 14px;" +
      "padding:6px 6px 6px 12px;border:1px solid #bfdbfe;background:#eff6ff;" +
      "border-radius:999px;font:600 12.5px 'Segoe UI',system-ui,sans-serif}" +
      ".s1-c360-back a{color:#1769AA;text-decoration:none}" +
      ".s1-c360-back a:hover{text-decoration:underline}" +
      ".s1-c360-x{border:0;background:transparent;color:#64748b;cursor:pointer;" +
      "font-size:16px;line-height:1;padding:0 6px}" +
      ".s1-c360-x:hover{color:#1e293b}";
    document.head.appendChild(st);
  }


  // ------------------------------------------------------------------
  // Back to the client record you came from
  // ------------------------------------------------------------------
  var C360_KEY = "s1c360:client";
  var C360_PARAM = "c360";
  var C360_PATH = "/client360";

  function onClient360() {
    return location.pathname.replace(/\/+$/, "") === C360_PATH;
  }

  function c360Client() {
    // The URL wins over the tab's memory: a link stamped with one client must
    // never be answered with a different one left over from an earlier visit.
    var q;
    try { q = new URLSearchParams(location.search).get(C360_PARAM); } catch (e) { q = null; }
    if (q) {
      try { sessionStorage.setItem(C360_KEY, q); } catch (e) {}
      return q;
    }
    try { return sessionStorage.getItem(C360_KEY) || null; } catch (e) { return null; }
  }

  function forget() {
    try { sessionStorage.removeItem(C360_KEY); } catch (e) {}
  }

  // Which links carry the client onwards. Skipped, each for its own reason:
  // the chrome (following the sidebar to the Dashboard is not "still working
  // on this client"), anything cross-origin (QuickBooks and Cloudinary are
  // not ours to add parameters to), an API path (the parameter would reach a
  // handler that never asked for one), and a download.
  function stampable(a) {
    var href = a.getAttribute("href") || "";
    if (!href || href.charAt(0) === "#") return false;
    if (/^[a-z][a-z0-9+.-]*:/i.test(href) && !/^https?:/i.test(href)) return false;
    if (/^https?:/i.test(href) && href.indexOf(location.origin) !== 0) return false;
    if (href.indexOf(C360_PARAM + "=") >= 0) return false;
    if (/(^|\/)api\//.test(href)) return false;
    if (a.hasAttribute("download")) return false;
    if (a.closest(".s1hub-sb, .s1-crumbs, .s1-c360-back, .topbar")) return false;
    return true;
  }

  function stamp(name) {
    var enc = encodeURIComponent(name);
    var links = document.querySelectorAll("a[href]");
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      if (!stampable(a)) continue;
      var href = a.getAttribute("href");
      a.setAttribute("href", href + (href.indexOf("?") >= 0 ? "&" : "?")
                     + C360_PARAM + "=" + enc);
    }
  }

  function backBar(name) {
    var existing = document.querySelector(".s1-c360-back");
    if (existing) {
      if (existing.getAttribute("data-for") === name) return;
      existing.parentNode.removeChild(existing);
    }
    var bar = document.createElement("div");
    bar.className = "s1-c360-back";
    bar.setAttribute("data-for", name);
    var a = document.createElement("a");
    a.href = C360_PATH + "?q=" + encodeURIComponent(name);
    a.textContent = "\u2190 Back to " + name;
    var x = document.createElement("button");
    x.type = "button";
    x.className = "s1-c360-x";
    x.title = "Stop offering this";
    x.setAttribute("aria-label", "Stop offering the way back to " + name);
    x.innerHTML = "&times;";
    x.onclick = function () { forget(); bar.parentNode.removeChild(bar); };
    bar.appendChild(a);
    bar.appendChild(x);
    var host = document.querySelector(".page, main, .main") || document.body;
    host.insertBefore(bar, host.firstChild);
  }

  function c360() {
    if (onClient360()) {
      // Arriving back at a record clears the trail — a bar still offering
      // the way back to the page you are standing on is noise, and one left
      // pointing at yesterday's client is worse than noise.
      var here;
      try { here = new URLSearchParams(location.search).get("q") || ""; } catch (e) { here = ""; }
      if (here) { try { sessionStorage.setItem(C360_KEY, here); } catch (e) {} }
      else forget();
      var stale = document.querySelector(".s1-c360-back");
      if (stale) stale.parentNode.removeChild(stale);
      if (here) stamp(here);
      return;
    }
    var name = c360Client();
    if (!name) return;
    backBar(name);
    stamp(name);          // the trail survives a second hop, not just the first
  }

  // Client 360 draws most of itself from fetches, and so do several of the
  // tools it opens, so a single pass at load would stamp the shell and miss
  // every link the page has not drawn yet. Same debounced observer
  // hub-help.js uses for bubbles, for the same reason.
  function watch() {
    if (!window.MutationObserver) return;
    var timer = null;
    new MutationObserver(function () {
      clearTimeout(timer);
      timer = setTimeout(c360, 180);
    }).observe(document.body, { childList: true, subtree: true });
  }

  function init() { remember(); css(); render(); c360(); watch(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
