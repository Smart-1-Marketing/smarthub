/* Ask Analytics — one widget, used wherever someone has a GA4 property.
 *
 * The Google tools page had its own copy that rendered `data.answer` as a
 * paragraph and nothing else. That was all the old endpoint returned. The
 * endpoint now plans a real report, so there is a table, a comparison, and
 * sometimes a question back — and a second copy of this in Client 360 would be
 * the same divergence that put two rate cards and two proposal engines in this
 * codebase.
 *
 * Mount with:
 *   Smart1Ask.mount(hostElement, {
 *     propertyId: fn|string, login: fn|string, label: string, placeholder: string
 *   });
 *
 * propertyId and login may be functions, because on Client 360 they are not
 * known until the client's accounts have loaded.
 */
(function (global) {
  "use strict";

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function val(v) { return typeof v === "function" ? v() : v; }

  function fmtNum(n) {
    if (n === null || n === undefined) return "—";
    var x = Number(n);
    if (!isFinite(x)) return "—";
    if (Math.abs(x) >= 1000) return Math.round(x).toLocaleString();
    if (Math.abs(x) >= 10) return (Math.round(x * 10) / 10).toLocaleString();
    return (Math.round(x * 100) / 100).toLocaleString();
  }

  /* Direction is not always good news: a fall in bounce rate is an
     improvement. Metrics where lower is better are coloured accordingly
     rather than painting every minus sign red. */
  var LOWER_IS_BETTER = { bounceRate: 1, averageSessionDuration: 0 };
  function delta(cell) {
    if (cell.change_pct === undefined || cell.change_pct === null) return "";
    var p = cell.change_pct;
    var better = LOWER_IS_BETTER[cell.metric] ? p < 0 : p > 0;
    var cls = p === 0 ? "s1ask-flat" : (better ? "s1ask-up" : "s1ask-down");
    var arrow = p === 0 ? "→" : (p > 0 ? "▲" : "▼");
    return '<span class="' + cls + '">' + arrow + " " +
           Math.abs(p).toFixed(1) + "%</span>";
  }

  var CSS = [
    ".s1ask{font:13.5px system-ui,'Segoe UI',sans-serif;color:#1e293b}",
    ".s1ask-row{display:flex;gap:8px;flex-wrap:wrap}",
    ".s1ask-row input{flex:1 1 260px;padding:10px 12px;border:1px solid #dce3ea;",
    "  border-radius:9px;font:inherit}",
    ".s1ask-btn{border:0;background:#0A6E8C;color:#fff;border-radius:9px;",
    "  padding:10px 16px;font:600 13px system-ui;cursor:pointer;white-space:nowrap}",
    ".s1ask-btn[disabled]{opacity:.55;cursor:default}",
    ".s1ask-ex{margin:8px 0 0;display:flex;gap:6px;flex-wrap:wrap}",
    ".s1ask-ex button{border:1px solid #dce3ea;background:#f7fafc;border-radius:999px;",
    "  padding:4px 11px;font:12px system-ui;color:#46586E;cursor:pointer}",
    ".s1ask-out{margin-top:12px}",
    ".s1ask-card{border:1px solid #dce3ea;border-radius:10px;background:#fff;padding:12px 14px}",
    ".s1ask-title{font-weight:700;color:#0E1B2B;margin-bottom:2px}",
    ".s1ask-explain{color:#68798c;font-size:12px;margin-bottom:8px}",
    ".s1ask-answer{line-height:1.5;margin:6px 0 10px}",
    ".s1ask-clarify{border-left:3px solid #0A6E8C;background:#F1F8FB;padding:10px 12px;",
    "  border-radius:8px;margin-top:4px}",
    ".s1ask table{width:100%;border-collapse:collapse;font-size:12.5px}",
    ".s1ask th{text-align:left;color:#68798c;font-size:11px;text-transform:uppercase;",
    "  letter-spacing:.04em;padding:6px 8px;border-bottom:1px solid #eef2f6}",
    ".s1ask td{padding:6px 8px;border-bottom:1px solid #f4f7fa}",
    ".s1ask tr:last-child td{border-bottom:0}",
    ".s1ask-tot td{font-weight:700;background:#f7fafc}",
    ".s1ask-up{color:#1B6E52;font-weight:600}",
    ".s1ask-down{color:#A33327;font-weight:600}",
    ".s1ask-flat{color:#7C8B9C;font-weight:600}",
    ".s1ask-q{color:#68798c;font-size:11.5px;margin-top:8px}",
    ".s1ask-err{color:#A33327}"
  ].join("");

  function ensureCss() {
    if (document.getElementById("s1ask-css")) return;
    var st = document.createElement("style");
    st.id = "s1ask-css";
    st.textContent = CSS;
    document.head.appendChild(st);
  }

  function renderTable(res) {
    if (!res || !res.rows || !res.rows.length) return "";
    var dims = res.dimensions || [], mets = res.metrics || [];
    var head = dims.map(function (d) { return "<th>" + esc(d) + "</th>"; }).join("")
      + mets.map(function (m) {
          return "<th>" + esc(m) + (res.comparing ? " <span style='font-weight:400'>vs prev</span>" : "") + "</th>";
        }).join("");

    function cells(row) {
      return row.dims.map(function (d) { return "<td>" + esc(d || "—") + "</td>"; }).join("")
        + row.cells.map(function (c) {
            return "<td>" + fmtNum(c.value) + " " + delta(c) + "</td>";
          }).join("");
    }

    var body = res.rows.slice(0, 25).map(function (r) {
      return "<tr>" + cells(r) + "</tr>";
    }).join("");

    var totals = "";
    if (res.totals && res.totals.length) {
      totals = '<tr class="s1ask-tot">'
        + (dims.length ? '<td colspan="' + dims.length + '">Total</td>' : "<td>Total</td>")
        + res.totals.map(function (t) {
            return "<td>" + fmtNum(t.value) + " " + delta(t) + "</td>";
          }).join("") + "</tr>";
    }

    var more = res.row_count > 25
      ? '<div class="s1ask-q">Showing 25 of ' + res.row_count + " rows.</div>" : "";

    return '<div style="overflow-x:auto"><table><thead><tr>' + head
      + "</tr></thead><tbody>" + body + totals + "</tbody></table></div>" + more;
  }

  function mount(host, opts) {
    if (!host) return null;
    opts = opts || {};
    ensureCss();

    var history = [];
    var wrap = document.createElement("div");
    wrap.className = "s1ask";
    wrap.innerHTML =
      '<div class="s1ask-row">'
      + '<input type="text" placeholder="' + esc(opts.placeholder
          || "Ask anything — “how did conversions do in July versus June?”") + '">'
      + '<button type="button" class="s1ask-btn">Ask</button></div>'
      + '<div class="s1ask-ex"></div>'
      + '<div class="s1ask-out"></div>';
    host.appendChild(wrap);

    var input = wrap.querySelector("input");
    var btn = wrap.querySelector(".s1ask-btn");
    var ex = wrap.querySelector(".s1ask-ex");
    var out = wrap.querySelector(".s1ask-out");

    (opts.examples || [
      "Top landing pages last month",
      "Conversions this month vs last month",
      "Which cities drove the most sessions in the last 90 days?",
      "Mobile vs desktop engagement rate this quarter"
    ]).forEach(function (q) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = q;
      b.onclick = function () { input.value = q; ask(); };
      ex.appendChild(b);
    });

    function say(html) { out.innerHTML = html; }

    function ask() {
      var question = (input.value || "").trim();
      var propertyId = String(val(opts.propertyId) || "").trim();
      var login = String(val(opts.login) || "").trim();

      if (!question) { say('<div class="s1ask-err">Type a question first.</div>'); return; }
      if (!propertyId || !login) {
        say('<div class="s1ask-err">No Google Analytics property is connected '
          + 'for this client yet, so there is nothing to ask.</div>');
        return;
      }

      btn.disabled = true;
      say('<div class="s1ask-card">Working that out…</div>');

      fetch("/google/api/ga4/ask", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          property_id: propertyId, google_login: login, question: question,
          property_label: opts.label || "", history: history
        })
      }).then(function (r) {
        return r.json().then(function (d) { return { ok: r.ok, d: d }; });
      }).then(function (res) {
        btn.disabled = false;
        var d = res.d || {};

        if (d.needs_reauth) {
          say('<div class="s1ask-card s1ask-err">' + esc(d.error || "")
            + ' <a href="' + esc(d.reconnect_url || "/google/login")
            + '">Reconnect Google</a></div>');
          return;
        }
        if (!res.ok) {
          say('<div class="s1ask-card s1ask-err">' + esc(d.error || "That didn't work.") + "</div>");
          return;
        }

        /* A question with no defensible answer comes back as a question. The
           original travels in history so the reply is resolved against it
           rather than starting a new thread. */
        if (d.clarify) {
          history.push({ role: "user", text: question });
          history.push({ role: "assistant", text: d.clarify });
          say('<div class="s1ask-card"><div class="s1ask-clarify"><b>One thing first:</b> '
            + esc(d.clarify) + "</div></div>");
          input.value = "";
          input.placeholder = d.clarify;
          input.focus();
          return;
        }

        history = [];
        input.placeholder = opts.placeholder || "Ask another question";
        var q = d.query || {};
        var ranges = (q.dateRanges || []).map(function (r) {
          return (r.name ? r.name + ": " : "") + r.startDate + " → " + r.endDate;
        }).join("  ·  ");

        say('<div class="s1ask-card">'
          + '<div class="s1ask-title">' + esc(d.title || question) + "</div>"
          + (d.explain ? '<div class="s1ask-explain">' + esc(d.explain) + "</div>" : "")
          + (d.answer ? '<div class="s1ask-answer">' + esc(d.answer) + "</div>" : "")
          + renderTable(d.result)
          /* What was actually run. A number nobody can trace back to a query
             is a number nobody trusts, and this is the difference between an
             answer and an assertion. */
          + '<div class="s1ask-q">Ran: ' + esc((q.metrics || []).join(", "))
          + ((q.dimensions || []).length ? " by " + esc(q.dimensions.join(", ")) : "")
          + (ranges ? "  ·  " + esc(ranges) : "") + "</div>"
          + "</div>");
      }).catch(function () {
        btn.disabled = false;
        say('<div class="s1ask-card s1ask-err">Couldn\'t reach the analytics assistant.</div>');
      });
    }

    btn.addEventListener("click", ask);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") ask(); });
    return { ask: ask, el: wrap };
  }

  global.Smart1Ask = { mount: mount };
})(window);
