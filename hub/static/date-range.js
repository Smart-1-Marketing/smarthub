/* Smart1Range — the date-range control every analytics card shares.
 *
 * ## Why this exists
 *
 * The SEO client page offered two buttons: "Month to date" and "Custom
 * compare". Anything other than the current month meant typing four dates by
 * hand, every time, on every visit — and Client 360 offered nothing at all,
 * it simply reported last month. Google Analytics and Google Ads both answer
 * this with a list of the periods people actually ask for, and a custom
 * option underneath for the ones they don't. So does this.
 *
 * One implementation, mounted by both pages, because the next period somebody
 * wants added should be added once.
 *
 * ## Conventions
 *
 * "Last N days" ends yesterday, not today — the same as GA4 and Google Ads.
 * A part-day at the end of the window drags every average down and makes the
 * comparison a lie about a fall that is only the clock.
 *
 * Month to date is the exception the pages already relied on: it runs to
 * today, and its default comparison is the *same days* of last month rather
 * than the whole of it. Comparing nine days to thirty-one reports a collapse
 * that has not happened. That comparison is worked out by the server, so
 * month-to-date with the default comparison sends no dates at all and lets it.
 *
 * ## Mount
 *
 *   var range = Smart1Range.mount(hostEl, {
 *     preset: 'mtd',                  // starting period
 *     compare: 'previous',            // starting comparison
 *     onChange: function (v) { ... }  // fired on every applied change
 *   });
 *
 * The value handed to onChange (and returned by range.value()):
 *
 *   { preset, compare, start, end, compare_start, compare_end,
 *     label, compare_label, server_default }
 *
 * `server_default` true means: send no dates, the server's own default is the
 * right one. Callers post start/end/compare_start/compare_end only when it is
 * false, which keeps the month-to-date behavior byte-for-byte what it was.
 */
(function (global) {
  "use strict";

  // ---------------------------------------------------------------- dates
  // Local time throughout. toISOString() is UTC, and for anyone west of
  // Greenwich in the evening that silently reports yesterday as today.
  function iso(d) {
    var m = d.getMonth() + 1, day = d.getDate();
    return d.getFullYear() + "-" + (m < 10 ? "0" : "") + m + "-" + (day < 10 ? "0" : "") + day;
  }
  function parse(s) {
    var p = String(s || "").split("-");
    return new Date(Number(p[0]), Number(p[1]) - 1, Number(p[2]));
  }
  function addDays(d, n) { var x = new Date(d.getTime()); x.setDate(x.getDate() + n); return x; }
  function startOfMonth(d) { return new Date(d.getFullYear(), d.getMonth(), 1); }
  function endOfMonth(d) { return new Date(d.getFullYear(), d.getMonth() + 1, 0); }
  function startOfWeek(d) { return addDays(d, -d.getDay()); }           // Sunday
  function startOfQuarter(d) { return new Date(d.getFullYear(), Math.floor(d.getMonth() / 3) * 3, 1); }
  function daysBetween(a, b) { return Math.round((b - a) / 86400000); }

  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function pretty(s) {
    if (!s) return "";
    var d = parse(s);
    return MONTHS[d.getMonth()] + " " + d.getDate() + ", " + d.getFullYear();
  }
  function span(a, b) { return pretty(a) + " – " + pretty(b); }

  // -------------------------------------------------------------- presets
  // Order is the order they are read in: the short recent windows, then the
  // week, the month, the quarter, the year. Custom last.
  var PRESETS = [
    { key: "today",         label: "Today",           range: function (t) { return [t, t]; } },
    { key: "yesterday",     label: "Yesterday",       range: function (t) { var y = addDays(t, -1); return [y, y]; } },
    { key: "last7",         label: "Last 7 days",     range: function (t) { return lastN(t, 7); } },
    { key: "last14",        label: "Last 14 days",    range: function (t) { return lastN(t, 14); } },
    { key: "last28",        label: "Last 28 days",    range: function (t) { return lastN(t, 28); } },
    { key: "last30",        label: "Last 30 days",    range: function (t) { return lastN(t, 30); } },
    { key: "last90",        label: "Last 90 days",    range: function (t) { return lastN(t, 90); } },
    { key: "wtd",           label: "Week to date",    range: function (t) { return [startOfWeek(t), t]; } },
    { key: "last_week",     label: "Last week (Sun–Sat)",
      range: function (t) { var s = addDays(startOfWeek(t), -7); return [s, addDays(s, 6)]; } },
    { key: "mtd",           label: "Month to date",   range: function (t) { return [startOfMonth(t), t]; } },
    { key: "last_month",    label: "Last month",
      range: function (t) { var e = addDays(startOfMonth(t), -1); return [startOfMonth(e), e]; } },
    { key: "qtd",           label: "Quarter to date", range: function (t) { return [startOfQuarter(t), t]; } },
    { key: "last_quarter",  label: "Last quarter",
      range: function (t) { var e = addDays(startOfQuarter(t), -1); return [startOfQuarter(e), e]; } },
    { key: "ytd",           label: "Year to date",
      range: function (t) { return [new Date(t.getFullYear(), 0, 1), t]; } },
    { key: "last_year",     label: "Last year",
      range: function (t) { return [new Date(t.getFullYear() - 1, 0, 1), new Date(t.getFullYear() - 1, 11, 31)]; } },
    { key: "custom",        label: "Custom…",         range: null }
  ];

  function lastN(today, n) { var e = addDays(today, -1); return [addDays(e, -(n - 1)), e]; }

  function presetByKey(k) {
    for (var i = 0; i < PRESETS.length; i++) if (PRESETS[i].key === k) return PRESETS[i];
    return null;
  }

  // ------------------------------------------------------------ comparisons
  var COMPARES = [
    { key: "previous",  label: "Preceding period" },
    { key: "year",      label: "Same period last year" },
    { key: "custom",    label: "Custom…" }
  ];

  function comparisonFor(kind, start, end) {
    var s = parse(start), e = parse(end);
    if (kind === "year") {
      return [iso(new Date(s.getFullYear() - 1, s.getMonth(), s.getDate())),
              iso(new Date(e.getFullYear() - 1, e.getMonth(), e.getDate()))];
    }
    // Preceding period of exactly the same length, ending the day before.
    var len = daysBetween(s, e);              // inclusive span - 1
    var ce = addDays(s, -1);
    return [iso(addDays(ce, -len)), iso(ce)];
  }

  // ------------------------------------------------------------------- css
  var CSS = [
    ".s1rng{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end;font:13px system-ui,'Segoe UI',sans-serif;color:#1e293b}",
    ".s1rng-f{display:flex;flex-direction:column;gap:3px}",
    ".s1rng-f label{font-size:10.5px;font-weight:700;text-transform:uppercase;",
    "  letter-spacing:.5px;color:#68798c}",
    ".s1rng select,.s1rng input[type=date]{padding:7px 9px;border:1px solid #dce3ea;",
    "  border-radius:8px;font:inherit;background:#fff;color:inherit}",
    ".s1rng-apply{border:0;background:#0A6E8C;color:#fff;border-radius:8px;padding:8px 15px;",
    "  font:600 12.5px system-ui;cursor:pointer}",
    ".s1rng-custom{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end}",
    ".s1rng-sum{flex-basis:100%;color:#68798c;font-size:12px;margin-top:2px}"
  ].join("");

  function ensureCss() {
    if (document.getElementById("s1rng-css")) return;
    var st = document.createElement("style");
    st.id = "s1rng-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  // ----------------------------------------------------------------- mount
  function mount(host, opts) {
    if (!host) return null;
    opts = opts || {};
    ensureCss();

    var today = new Date();
    var state = {
      preset: presetByKey(opts.preset || "mtd") ? (opts.preset || "mtd") : "mtd",
      compare: opts.compare || "previous",
      start: "", end: "", compare_start: "", compare_end: ""
    };

    var wrap = document.createElement("div");
    wrap.className = "s1rng";
    wrap.innerHTML =
      '<div class="s1rng-f"><label>Date range</label><select data-r="preset">'
      + PRESETS.map(function (p) {
          return '<option value="' + p.key + '">' + p.label + "</option>";
        }).join("")
      + "</select></div>"
      + '<div class="s1rng-f"><label>Compare to</label><select data-r="compare">'
      + COMPARES.map(function (c) {
          return '<option value="' + c.key + '">' + c.label + "</option>";
        }).join("")
      + "</select></div>"
      + '<div class="s1rng-custom" data-r="customwrap" style="display:none">'
      + '<div class="s1rng-f"><label>From</label><input type="date" data-r="start"></div>'
      + '<div class="s1rng-f"><label>To</label><input type="date" data-r="end"></div>'
      + "</div>"
      + '<div class="s1rng-custom" data-r="ccustomwrap" style="display:none">'
      + '<div class="s1rng-f"><label>Compare from</label><input type="date" data-r="cstart"></div>'
      + '<div class="s1rng-f"><label>Compare to</label><input type="date" data-r="cend"></div>'
      + "</div>"
      + '<button type="button" class="s1rng-apply" data-r="apply" style="display:none">Apply</button>'
      + '<div class="s1rng-sum" data-r="sum"></div>';
    host.appendChild(wrap);

    function el(name) { return wrap.querySelector('[data-r="' + name + '"]'); }

    el("preset").value = state.preset;
    el("compare").value = state.compare;

    /* Custom dates start filled in with whatever period is on screen, so
       "custom" opens on something real to adjust rather than two empty boxes
       and a date picker defaulting to today. */
    function seedCustom() {
      var v = compute();
      if (!el("start").value) el("start").value = v.start;
      if (!el("end").value) el("end").value = v.end;
      if (!el("cstart").value) el("cstart").value = v.compare_start;
      if (!el("cend").value) el("cend").value = v.compare_end;
    }

    function compute() {
      var pkey = el("preset").value, ckey = el("compare").value;
      var start, end;
      if (pkey === "custom") {
        start = el("start").value; end = el("end").value;
        if (!start || !end) return null;
        if (end < start) { var t = start; start = end; end = t; }
      } else {
        var r = presetByKey(pkey).range(today);
        start = iso(r[0]); end = iso(r[1]);
      }

      var cs, ce;
      if (ckey === "custom") {
        cs = el("cstart").value; ce = el("cend").value;
        if (!cs || !ce) return null;
        if (ce < cs) { var t2 = cs; cs = ce; ce = t2; }
      } else {
        var c = comparisonFor(ckey, start, end);
        cs = c[0]; ce = c[1];
      }

      /* The one case the server does better than we can: month to date
         against the same days of last month. Sending nothing keeps it. */
      var serverDefault = (pkey === "mtd" && ckey === "previous");

      return {
        preset: pkey, compare: ckey,
        start: start, end: end, compare_start: cs, compare_end: ce,
        label: pkey === "custom" ? span(start, end) : presetByKey(pkey).label,
        compare_label: serverDefault ? "same days last month" : span(cs, ce),
        server_default: serverDefault
      };
    }

    function paint(v) {
      el("customwrap").style.display = el("preset").value === "custom" ? "flex" : "none";
      el("ccustomwrap").style.display = el("compare").value === "custom" ? "flex" : "none";
      var anyCustom = el("preset").value === "custom" || el("compare").value === "custom";
      el("apply").style.display = anyCustom ? "" : "none";
      el("sum").textContent = v
        ? span(v.start, v.end) + "  vs  " + (v.server_default ? "same days last month"
                                                              : span(v.compare_start, v.compare_end))
        : "Pick both dates, then Apply.";
    }

    function fire() {
      var v = compute();
      paint(v);
      if (v && typeof opts.onChange === "function") opts.onChange(v);
    }

    el("preset").onchange = function () {
      if (el("preset").value === "custom") { seedCustom(); paint(compute()); el("start").focus(); return; }
      fire();
    };
    el("compare").onchange = function () {
      if (el("compare").value === "custom") { seedCustom(); paint(compute()); el("cstart").focus(); return; }
      fire();
    };
    el("apply").onclick = fire;
    ["start", "end", "cstart", "cend"].forEach(function (n) {
      el(n).addEventListener("keydown", function (e) { if (e.key === "Enter") fire(); });
    });

    paint(compute());

    return {
      el: wrap,
      value: compute,
      /* Post body for either analytics endpoint. Empty where the server's
         own default is the one we want. */
      params: function () {
        var v = compute();
        if (!v || v.server_default) return {};
        return { start: v.start, end: v.end,
                 compare_start: v.compare_start, compare_end: v.compare_end };
      }
    };
  }

  global.Smart1Range = { mount: mount, presets: PRESETS, compares: COMPARES };
})(window);
