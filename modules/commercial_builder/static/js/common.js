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

  return { api, toast, fmtTime, el, debounce, API_ROOT };
})();
