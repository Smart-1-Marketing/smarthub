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
    "google-access": "Google Access", "image-picker": "Image Picker",
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
      ".s1-crumb-sep{color:#cbd5e1}";
    document.head.appendChild(st);
  }

  function init() { remember(); css(); render(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
