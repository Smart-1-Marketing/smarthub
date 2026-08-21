/* One date format for the whole Hub, front end: mm-dd-yy.
 *
 * hub/dates.py does this on the server. The browser had three separate copies
 * of the same idea and they did not agree: Client 360 rendered mm-dd-yy, the
 * SEO client page rendered mm/dd/yyyy, the uploads gallery rendered mm-dd-yy,
 * and several tables printed raw ISO out of a .slice(0,10). A column that
 * changes format between two pages reads as a data problem rather than a
 * formatting one, and ISO is the worst of them for a list that is scanned
 * rather than read.
 *
 *   S1Date.fmt(v)      -> "08-31-26", or "—" when it cannot be read
 *   S1Date.fmt(v, "")  -> same, with your own placeholder
 *   S1Date.sortKey(v)  -> a number for sorting, unknown dates last
 *
 * Deliberately does NOT use `new Date(v)` for date-only strings. "2026-08-31"
 * parses as midnight UTC, so west of Greenwich it renders as the 30th — which
 * is how an end-of-month renewal ends up reported a day early.
 */
(function (global) {
  "use strict";

  var BLANK = "—";

  function pad(n) { return (n < 10 ? "0" : "") + n; }

  /* Returns {y, m, d} or null. Order matters: ISO first because it is
     unambiguous. Day-first forms are absent on purpose — nothing in this
     system produces them, and guessing between 03-04-26 and 04-03-26 would
     silently mis-date a renewal. */
  function parse(v) {
    if (v === null || v === undefined || v === "") return null;

    if (v instanceof Date) {
      return isNaN(v) ? null
        : { y: v.getFullYear(), m: v.getMonth() + 1, d: v.getDate() };
    }

    var s = String(v).trim();
    if (!s) return null;

    var m;
    // 2026-08-31, or an ISO timestamp — take the date half, ignore the zone
    m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
    if (m) return { y: +m[1], m: +m[2], d: +m[3] };

    // 08/31/2026, 8/3/26, 08-31-2026, 08-31-26
    m = /^(\d{1,2})[\/-](\d{1,2})[\/-](\d{2,4})$/.exec(s);
    if (m) {
      var y = +m[3];
      if (y < 100) y += 2000;
      return { y: y, m: +m[1], d: +m[2] };
    }

    // 20260831 — Knack stores some dates this way
    m = /^(\d{4})(\d{2})(\d{2})$/.exec(s);
    if (m) return { y: +m[1], m: +m[2], d: +m[3] };

    // Anything else: let the engine try, but only for strings that carry a
    // time — those are real timestamps and the zone shift is correct for them.
    if (/[T ]\d{2}:\d{2}/.test(s)) {
      var d = new Date(s);
      if (!isNaN(d)) return { y: d.getFullYear(), m: d.getMonth() + 1, d: d.getDate() };
    }
    return null;
  }

  function fmt(v, blank) {
    var p = parse(v);
    if (!p) return blank === undefined ? BLANK : blank;
    return pad(p.m) + "-" + pad(p.d) + "-" + String(p.y).slice(2);
  }

  /* Sort on the real date, not the formatted string — 01-05-27 sorts above
     12-31-26 as text, which is how a "next to expire" list shows next year
     first. Unknown dates go last rather than first. */
  function sortKey(v) {
    var p = parse(v);
    if (!p) return Infinity;
    return p.y * 10000 + p.m * 100 + p.d;
  }

  global.S1Date = { fmt: fmt, sortKey: sortKey, parse: parse, BLANK: BLANK };
})(window);
