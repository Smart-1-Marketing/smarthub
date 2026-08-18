/* Smart 1 Hub — client autofill.
 *
 * Any form can prefill itself from what the Hub already knows:
 *
 *     <input name="company" data-fill="client">
 *     <input name="website" data-fill="website">
 *     <input name="phone"   data-fill="phone">
 *
 * Call S1Fill.load("Riverside HVAC") once a client is chosen. Fields the
 * person has already typed into are never overwritten -- prefill should save
 * typing, not fight the user.
 *
 * Every filled field is marked so it's obvious the value came from the Hub
 * rather than being something they entered and forgot.
 */
(function () {
  "use strict";
  var LAST = null;

  function setValue(node, value) {
    var proto = node instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
              : (node instanceof HTMLSelectElement ? HTMLSelectElement.prototype
                                                   : HTMLInputElement.prototype);
    var d = Object.getOwnPropertyDescriptor(proto, "value");
    if (d && d.set) d.set.call(node, value); else node.value = value;
    node.dispatchEvent(new Event("input", { bubbles: true }));
    node.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function apply(data) {
    if (!data || !data.fields) return 0;
    var n = 0;
    document.querySelectorAll("[data-fill]").forEach(function (el) {
      var key = el.getAttribute("data-fill");
      var val = data.fields[key];
      if (!val) return;
      // Don't clobber anything already entered.
      if (el.value && el.value.trim() && !el.dataset.s1filled) return;
      setValue(el, val);
      el.dataset.s1filled = "1";
      el.title = "Filled from " + (data.sources[key] || "the Hub");
      el.classList.add("s1-filled");
      n++;
    });
    return n;
  }

  function toast(n, data) {
    if (!n) return;
    var t = document.createElement("div");
    t.className = "s1-fill-toast";
    var miss = (data.missing || []).length;
    t.textContent = n + " field" + (n === 1 ? "" : "s") + " filled from "
      + data.client + (miss ? " · " + miss + " still needed" : "");
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 4200);
  }

  function load(client, domain) {
    if (!client || client === LAST) return Promise.resolve(0);
    LAST = client;
    return fetch("/api/client/context?name=" + encodeURIComponent(client)
                 + (domain ? "&domain=" + encodeURIComponent(domain) : ""),
                 { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { var n = apply(d); toast(n, d); return n; })
      .catch(function () { return 0; });   // prefill is never load-bearing
  }

  // If the page already knows its client, fill without being asked.
  function init() {
    var el = document.querySelector("[data-client-name]");
    var name = el ? el.getAttribute("data-client-name")
                  : (window.CURRENT_CLIENT || null);
    if (name) load(name);
    // A client picker that announces its choice gets autofill for free.
    document.addEventListener("s1:client-selected", function (e) {
      if (e.detail && e.detail.client) load(e.detail.client, e.detail.domain);
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();

  window.S1Fill = { load: load, apply: apply };
})();
