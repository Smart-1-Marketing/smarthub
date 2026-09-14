/* Smart 1 Hub — one loading/thinking system for current and future tools.
 *
 * Existing tool-specific progress stays authoritative. This file upgrades the
 * Hub's historic spinner classes, exposes S1Think for explicit waits, and now
 * also watches fetch/XHR requests so slow work gets a shared Smart 1 status
 * card automatically. Fast requests never draw the card. New tools inherit the
 * behavior because this script is injected by the Hub shell, HubBar, and the
 * blueprint injector rather than wired into individual tools.
 *
 * The personality layer is intentionally progressive: informative first,
 * playful next, sarcastic only when the wait has earned it. It never claims a
 * request succeeded; the caller still owns the answer.
 */
(function () {
  "use strict";

  var KINDS = ["ai", "scan", "wait"];
  var CONTEXTS = ["generic", "ai", "api", "database", "analytics", "creative",
                  "weather", "search", "report", "deployment"];

  var SLOW_AT = 6000;
  var TICK = 1000;
  var NETWORK_DELAY = 700;
  var MESSAGE_MIN = 3200;
  var MESSAGE_JITTER = 1600;
  var NS = "http://www.w3.org/2000/svg";

  function reduced() {
    try {
      return !!(window.matchMedia &&
                window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch (e) { return false; }
  }

  function el(name, attrs) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) {
        n.setAttribute(k, String(attrs[k]));
      }
    }
    return n;
  }

  function glyphAI(svg) {
    var star = "M12 3.2 13.6 9.1 19.4 10.7 13.6 12.3 12 18.2 10.4 12.3 4.6 10.7 10.4 9.1Z";
    svg.appendChild(el("path", { d: star, fill: "currentColor",
                                 "class": "s1-think-star" }));
    svg.appendChild(el("circle", { cx: 19.4, cy: 4.9, r: 1.7, fill: "currentColor",
                                   "class": "s1-think-tw s1-think-tw1" }));
    svg.appendChild(el("circle", { cx: 5.1, cy: 19.2, r: 1.3, fill: "currentColor",
                                   "class": "s1-think-tw s1-think-tw2" }));
  }

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
    var n = node;
    while (n && n.getAttribute) {
      var k = n.getAttribute("data-s1-thinking");
      if (k && KINDS.indexOf(k) >= 0) return k;
      n = n.parentNode;
    }
    return "wait";
  }

  function mark(kind) {
    var k = KINDS.indexOf(kind) >= 0 ? kind : "wait";
    var svg = el("svg", { viewBox: "0 0 24 24", "aria-hidden": "true",
                          focusable: "false",
                          "class": "s1-think-svg s1-think-" + k });
    (DRAW[k] || glyphWait)(svg);
    return svg;
  }

  var SELECTOR = ".spin, .spinner, .cb-spinner, [data-s1-think]";

  function upgrade(root) {
    var scope = root || document;
    var found;
    try {
      found = scope.querySelectorAll(SELECTOR);
    } catch (e) { return; }
    Array.prototype.forEach.call(found, function (node) {
      if (node.getAttribute("data-s1-upgraded")) return;
      if (node.firstElementChild) {
        node.setAttribute("data-s1-upgraded", "skipped");
        return;
      }
      node.setAttribute("data-s1-upgraded", "1");
      node.classList.add("s1-think");
      node.appendChild(mark(node.getAttribute("data-s1-think") || kindOf(node)));
    });
  }

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
        if (box && box.isConnected === false) { handle.done(); return; }
        var ms = Date.now() - started;
        clock.textContent = ms < SLOW_AT ? "" : fmt(ms);
      }, TICK));

      (o.stages || []).forEach(function (s) {
        timers.push(setTimeout(function () {
          if (label) label.textContent = s.note;
        }, s.at || 0));
      });
    } catch (e) { return handle; }

    handle.stage = function (text, nextKind) {
      try {
        if (label && text) label.textContent = text;
        if (box && nextKind && KINDS.indexOf(nextKind) >= 0
            && nextKind !== kind) {
          var was = box.querySelector(".s1-think-svg");
          if (was) box.replaceChild(mark(nextKind), was);
          box.className = "s1-thinking s1-thinking-" + nextKind;
          kind = nextKind;
        }
      } catch (e) {}
      return handle;
    };
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
    if (!window.MutationObserver || !document.body) return;
    var timer = null;
    new MutationObserver(function () {
      clearTimeout(timer);
      timer = setTimeout(function () { upgrade(document); }, 120);
    }).observe(document.body, { childList: true, subtree: true });
  }

  /* ---------------------------------------------------- personality layer */

  var COMMON = [
    [
      "Connecting the dots…",
      "Checking the data before we pretend to know the answer.",
      "Smart 1 is finding the useful part.",
      "Stand by. Smart things are happening."
    ],
    [
      "Marketing magic takes at least a few milliseconds.",
      "We asked the machines. They have follow-up questions.",
      "Smart 1 is looking under every digital rock.",
      "Turning caffeine into marketing intelligence."
    ],
    [
      "Artificial intelligence. Natural impatience.",
      "Somewhere, a server is working very hard to impress you.",
      "Your competitors probably still use ‘Loading…’",
      "We could guess. We’d rather actually find it."
    ],
    [
      "The algorithm is being dramatic. We’re supervising.",
      "Smart 1 Marketing AI: mildly terrifying, extremely useful.",
      "Please enjoy this brief moment of artificial suspense.",
      "The robots assure us this is worth the wait.",
      "Patient, young marketer. Dramatic, the computers have become."
    ]
  ];

  var MESSAGES = {
    generic: [
      ["Smart 1 is working on it.", "Finding the answer that actually helps."],
      ["The Smart 1 machine is warming up.", "Connecting systems that were clearly never introduced properly."],
      ["The mainframe has opinions. This should be interesting.", "Making marketing smarter one unnecessarily complicated request at a time."],
      ["The machine considered world domination. We redirected it to conversions.", "No dramatic movie computer sequence required. Probably."]
    ],
    ai: [
      ["Asking the model to think about this…", "Giving the AI enough context to be useful."],
      ["The AI is overthinking it. We encouraged that.", "Asking billions of parameters to agree on something."],
      ["Our AI just said ‘interesting.’ That’s usually a good sign.", "Teaching a computer about marketing. Again."],
      ["The machines are holding a strategy meeting. Humans were not invited.", "Global domination postponed. Conversion optimization has priority."]
    ],
    api: [
      ["Calling the other computer…", "Request sent. Waiting for the other system."],
      ["The API says ‘one moment.’ Classic.", "Translating computer into other computer."],
      ["Waiting for the internet equivalent of ‘I’ll check in the back.’", "Request sent. Now staring professionally at the API."],
      ["Their server. Our patience. Your answer.", "Carrier pigeon remains available as a fallback architecture."]
    ],
    database: [
      ["Querying Smart 1’s data…", "Checking the records."],
      ["Opening a suspicious number of digital filing cabinets.", "The database knows we saved it somewhere."],
      ["Asking SQL nicely.", "Following the data trail without stepping on the joins."],
      ["Somewhere in here is exactly what you asked for.", "The database would like everyone to know this is technically very impressive."]
    ],
    analytics: [
      ["Crunching the numbers…", "Checking what actually happened."],
      ["Making the numbers tell the truth.", "Turning rows into decisions."],
      ["Separating impressions from actual results.", "Looking for the part where marketing made money."],
      ["Preparing charts executives will insist they already understood.", "Data in. Excuses out."]
    ],
    creative: [
      ["Building the creative…", "Giving the pixels something useful to do."],
      ["Moving pixels until marketing gets happier.", "Making it look expensive."],
      ["Teaching AI the difference between clean and boring.", "Creative department currently arguing with mathematics."],
      ["Making the logo bigger in spirit, if not literally.", "Rendering pixels at an irresponsible rate."]
    ],
    weather: [
      ["Checking the forecast…", "Reading the conditions before the campaign reacts."],
      ["Consulting the atmosphere.", "Asking the clouds whether the budget should move."],
      ["Mother Nature just entered the media plan.", "Forecast: a strong chance of smarter targeting."],
      ["Somewhere, a cold front just triggered an ad.", "The temperature changed. So did the opportunity."]
    ],
    search: [
      ["Searching Smart 1’s sources…", "Looking for the useful result."],
      ["Looking under every digital rock.", "We found the haystack. Working on the needle."],
      ["Finding the needle. Ignoring several million pieces of hay.", "Searching faster than a human with 37 browser tabs."],
      ["Enhancing… except we’re actually querying the data.", "Satellite-level paranoia not required. The search data is enough."]
    ],
    report: [
      ["Building the report…", "Turning data into something readable."],
      ["Turning rows into something a client can actually read.", "Making analytics slightly less boring."],
      ["Data in. Excuses out.", "Calculating. Recalculating. Blaming rounding."],
      ["The numbers are ready. Now making them look less like homework.", "Converting a small mountain of data into one useful answer."]
    ],
    deployment: [
      ["Putting the pieces together…", "Checking the build before it goes anywhere."],
      ["Convincing the computers this was always the plan.", "Packaging optimism with version control."],
      ["Deploying optimism with a rollback plan.", "The servers are discussing who gets to do the work."],
      ["If this works, it was automation. If not, it was character building.", "Mainframe says hello. Deployment says keep going."]
    ]
  };

  function tierFor(elapsed) {
    if (elapsed < 3500) return 0;
    if (elapsed < 8000) return 1;
    if (elapsed < 15000) return 2;
    return 3;
  }

  function normalContext(context) {
    return CONTEXTS.indexOf(context) >= 0 ? context : "generic";
  }

  function messageFor(context, elapsed, previous) {
    var c = normalContext(context);
    var tier = tierFor(elapsed || 0);
    var specific = (MESSAGES[c] && MESSAGES[c][tier]) || [];
    var pool = specific.concat(COMMON[tier] || []);
    if (!pool.length) return "Smart 1 is working on it.";
    var start = Math.floor(Math.random() * pool.length);
    for (var i = 0; i < pool.length; i += 1) {
      var candidate = pool[(start + i) % pool.length];
      if (pool.length === 1 || candidate !== previous) return candidate;
    }
    return pool[0];
  }

  function inferContext(value, explicit) {
    if (explicit && CONTEXTS.indexOf(String(explicit).toLowerCase()) >= 0) {
      return String(explicit).toLowerCase();
    }
    var text = "";
    try {
      if (typeof value === "string") text = value;
      else if (value && value.url) text = value.url;
      else text = String(value || "");
    } catch (e) { text = ""; }
    text = text.toLowerCase();

    if (/weather|forecast|temperature|precip|humidity|smartforecast/.test(text)) return "weather";
    if (/image|creative|video|commercial|audio|voice|radio|artwork|banner/.test(text)) return "creative";
    if (/analytics|ga4|looker|metric|kpi|conversion|attribution/.test(text)) return "analytics";
    if (/report|reporting|audit|summary|insight|pdf/.test(text)) return "report";
    if (/deploy|publish|release|render|build/.test(text)) return "deployment";
    if (/search|lookup|find|scan|scrape|crawl|prospect/.test(text)) return "search";
    if (/database|\bdb\b|knack|sql|record|ledger/.test(text)) return "database";
    if (/openai|\bai\b|gpt|model|assistant|generate|draft|rewrite|chat/.test(text)) return "ai";
    if (/\/api\//.test(text) || /^api[:/]/.test(text)) return "api";
    return "generic";
  }

  function kindForContext(context) {
    if (context === "ai" || context === "creative") return "ai";
    if (context === "search" || context === "weather" || context === "api") return "scan";
    return "wait";
  }

  function injectPersonalityStyle() {
    if (document.getElementById("s1-thinking-personality-style")) return;
    try {
      var style = document.createElement("style");
      style.id = "s1-thinking-personality-style";
      style.textContent =
        ".s1-global-thinking{position:fixed;right:18px;bottom:18px;z-index:2147483000;" +
        "width:min(410px,calc(100vw - 36px));box-sizing:border-box;display:flex;align-items:flex-start;" +
        "gap:12px;padding:13px 15px;background:rgba(255,255,255,.97);color:#1b2733;" +
        "border:1px solid #dfe6ee;border-left:4px solid #1769AA;border-radius:12px;" +
        "box-shadow:0 14px 38px rgba(6,18,32,.22);font:400 13px/1.45 system-ui,-apple-system,sans-serif}" +
        ".s1-global-thinking[hidden]{display:none!important}" +
        ".s1-global-thinking-mark{display:flex;align-items:center;justify-content:center;width:24px;height:24px;" +
        "flex:0 0 24px;color:#1769AA;margin-top:1px}" +
        ".s1-global-thinking-mark .s1-think-svg{width:22px;height:22px}" +
        ".s1-global-thinking-copy{min-width:0;display:flex;flex-direction:column;gap:2px}" +
        ".s1-global-thinking-title{font-weight:750;color:#0d2340;font-size:13px;letter-spacing:.01em}" +
        ".s1-global-thinking-message{color:#41525f;overflow-wrap:anywhere}" +
        "@media(max-width:640px){.s1-global-thinking{right:10px;left:10px;bottom:10px;width:auto}}" +
        "@media(prefers-reduced-motion:reduce){.s1-global-thinking{transition:none!important}}";
      (document.head || document.documentElement).appendChild(style);
    } catch (e) {}
  }

  var globalCard = null;
  var globalMessageTimer = null;
  var lastGlobalMessage = "";
  var activeWaits = {};
  var waitSeq = 0;

  function ensureGlobalCard() {
    if (globalCard && globalCard.box && globalCard.box.isConnected) return globalCard;
    try {
      injectPersonalityStyle();
      var box = document.createElement("div");
      box.className = "s1-global-thinking";
      box.hidden = true;
      box.setAttribute("role", "status");
      box.setAttribute("aria-live", "polite");
      box.setAttribute("aria-atomic", "true");

      var icon = document.createElement("span");
      icon.className = "s1-global-thinking-mark";
      box.appendChild(icon);

      var copy = document.createElement("span");
      copy.className = "s1-global-thinking-copy";
      var title = document.createElement("strong");
      title.className = "s1-global-thinking-title";
      title.textContent = "Smart 1 Thinking…";
      var message = document.createElement("span");
      message.className = "s1-global-thinking-message";
      copy.appendChild(title);
      copy.appendChild(message);
      box.appendChild(copy);

      (document.body || document.documentElement).appendChild(box);
      globalCard = { box: box, icon: icon, message: message, token: null, kind: null };
    } catch (e) { return null; }
    return globalCard;
  }

  function visibleWait() {
    var choice = null;
    Object.keys(activeWaits).forEach(function (token) {
      var w = activeWaits[token];
      if (!w.visible) return;
      if (!choice || w.started < choice.started) choice = w;
    });
    return choice;
  }

  function stopMessageTimer() {
    try { if (globalMessageTimer) clearTimeout(globalMessageTimer); } catch (e) {}
    globalMessageTimer = null;
  }

  function paintGlobal(wait) {
    var card = ensureGlobalCard();
    if (!card || !wait) return;
    try {
      var nextKind = kindForContext(wait.context);
      if (card.kind !== nextKind) {
        card.icon.innerHTML = "";
        card.icon.appendChild(mark(nextKind));
        card.kind = nextKind;
      }
      var msg = messageFor(wait.context, Date.now() - wait.started, lastGlobalMessage);
      card.message.textContent = msg;
      lastGlobalMessage = msg;
      card.token = wait.token;
      card.box.setAttribute("data-s1-context", wait.context);
      card.box.hidden = false;
    } catch (e) {}
  }

  function scheduleGlobalMessage(wait) {
    stopMessageTimer();
    if (!wait) return;
    var delay = MESSAGE_MIN + Math.floor(Math.random() * MESSAGE_JITTER);
    globalMessageTimer = setTimeout(function () {
      var current = activeWaits[wait.token];
      if (!current || !current.visible) return;
      var shown = visibleWait();
      if (!shown || shown.token !== wait.token) return;
      paintGlobal(shown);
      scheduleGlobalMessage(shown);
    }, delay);
  }

  function refreshGlobal() {
    var wait = visibleWait();
    if (!wait) {
      stopMessageTimer();
      try { if (globalCard && globalCard.box) globalCard.box.hidden = true; } catch (e) {}
      return;
    }
    paintGlobal(wait);
    scheduleGlobalMessage(wait);
  }

  function startGlobal(opts) {
    var o = opts || {};
    var context = inferContext(o.url || "", o.context);
    var token = "s1w-" + (++waitSeq);
    var wait = {
      token: token,
      context: context,
      started: Date.now(),
      visible: false,
      timer: null
    };
    activeWaits[token] = wait;

    var delay = typeof o.delay === "number" ? Math.max(0, o.delay) : NETWORK_DELAY;
    wait.timer = setTimeout(function () {
      if (!activeWaits[token]) return;
      wait.visible = true;
      refreshGlobal();
    }, delay);

    var ended = false;
    return {
      token: token,
      context: context,
      done: function () {
        if (ended) return;
        ended = true;
        try { clearTimeout(wait.timer); } catch (e) {}
        delete activeWaits[token];
        refreshGlobal();
      }
    };
  }

  function trackPromise(promise, opts) {
    var h = startGlobal(opts || {});
    if (!promise || typeof promise.then !== "function") { h.done(); return promise; }
    return promise.then(function (value) {
      h.done();
      return value;
    }, function (err) {
      h.done();
      throw err;
    });
  }

  function requestURL(input) {
    try {
      if (typeof input === "string") return input;
      if (input && input.url) return input.url;
    } catch (e) {}
    return "";
  }

  function headerOptOut(headers) {
    try {
      if (!headers) return false;
      if (window.Headers && headers instanceof window.Headers) {
        return String(headers.get("X-S1-Thinking") || "").toLowerCase() === "off";
      }
      if (Array.isArray(headers)) {
        for (var i = 0; i < headers.length; i += 1) {
          if (String(headers[i][0]).toLowerCase() === "x-s1-thinking" &&
              String(headers[i][1]).toLowerCase() === "off") return true;
        }
      } else {
        for (var key in headers) {
          if (Object.prototype.hasOwnProperty.call(headers, key) &&
              String(key).toLowerCase() === "x-s1-thinking" &&
              String(headers[key]).toLowerCase() === "off") return true;
        }
      }
    } catch (e) {}
    return false;
  }

  function shouldTrack(input, init) {
    try {
      var o = init || {};
      if (o.s1Thinking === false || headerOptOut(o.headers)) return false;
      var method = String(o.method || (input && input.method) || "GET").toUpperCase();
      if (method === "HEAD" || method === "OPTIONS") return false;
      var url = requestURL(input);
      if (!url) return true;
      if (/\.(?:js|css|map|png|jpe?g|gif|webp|svg|ico|woff2?|ttf)(?:\?|$)/i.test(url)) return false;
      if (/\/hub-thinking\.js(?:\?|$)|\/hub-help\.css(?:\?|$)/i.test(url)) return false;
      return true;
    } catch (e) { return false; }
  }

  function installNetworkHooks() {
    try {
      if (window.fetch && !window.fetch.__s1ThinkingWrapped) {
        var nativeFetch = window.fetch;
        var wrappedFetch = function (input, init) {
          if (!shouldTrack(input, init)) return nativeFetch.apply(this, arguments);
          var o = init || {};
          var h = startGlobal({
            url: requestURL(input),
            context: o.s1Context,
            delay: typeof o.s1ThinkingDelay === "number" ? o.s1ThinkingDelay : NETWORK_DELAY
          });
          var result;
          try { result = nativeFetch.apply(this, arguments); }
          catch (e) { h.done(); throw e; }
          if (!result || typeof result.then !== "function") { h.done(); return result; }
          return result.then(function (response) {
            h.done();
            return response;
          }, function (err) {
            h.done();
            throw err;
          });
        };
        wrappedFetch.__s1ThinkingWrapped = true;
        wrappedFetch.__s1NativeFetch = nativeFetch;
        window.fetch = wrappedFetch;
      }
    } catch (e) {}

    try {
      if (window.XMLHttpRequest && !window.XMLHttpRequest.prototype.__s1ThinkingWrapped) {
        var proto = window.XMLHttpRequest.prototype;
        var nativeOpen = proto.open;
        var nativeSend = proto.send;
        proto.open = function (method, url) {
          this.__s1Method = method;
          this.__s1Url = url;
          return nativeOpen.apply(this, arguments);
        };
        proto.send = function () {
          var xhr = this;
          var h = null;
          if (xhr.s1Thinking !== false && String(xhr.__s1Method || "GET").toUpperCase() !== "HEAD") {
            h = startGlobal({
              url: xhr.__s1Url || "",
              context: xhr.s1Context,
              delay: typeof xhr.s1ThinkingDelay === "number" ? xhr.s1ThinkingDelay : NETWORK_DELAY
            });
            try {
              xhr.addEventListener("loadend", function () { if (h) h.done(); }, { once: true });
            } catch (e) {}
          }
          try { return nativeSend.apply(xhr, arguments); }
          catch (e) { if (h) h.done(); throw e; }
        };
        proto.__s1ThinkingWrapped = true;
      }
    } catch (e) {}
  }

  window.S1Think = {
    KINDS: KINDS,
    CONTEXTS: CONTEXTS,
    SLOW_AT: SLOW_AT,
    attach: attach,
    busy: busy,
    html: html,
    mark: mark,
    upgrade: upgrade,
    reduced: reduced,
    inferContext: inferContext,
    messageFor: messageFor,
    start: startGlobal,
    track: trackPromise
  };

  function init() {
    try {
      injectPersonalityStyle();
      upgrade(document);
      watch();
      installNetworkHooks();
    } catch (e) {}
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();