/* Web Tickets audit — one file, three page controllers, dispatched off
   window.TKT.page. No framework: this matches the rest of the Hub. */
(function () {
  "use strict";

  var CFG = window.TKT || {};
  var BASE = CFG.base || "/tools/tickets";

  // ---- helpers ---------------------------------------------------------
  function $(sel, root) { return (root || document).querySelector(sel); }
  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function isNumeric(value) {
    // Numbers, money, counts, ages and dates line up right in mono; names and
    // free text stay left, or the table stops being readable across a row.
    return /^[#$]?[\d][\d,.]*\s*(%|h|d|days|day|\/mo|mo)?$/i.test(String(value).trim())
      || /^\d{4}-\d{2}-\d{2}$/.test(String(value).trim())
      || String(value).trim() === "\u2014";
  }

  function money(v) {
    if (!v) return "—";
    return "$" + Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 });
  }
  function dateOnly(v) { return v ? String(v).slice(0, 10) : "—"; }

  function get(path) {
    return fetch(BASE + path, { headers: { Accept: "application/json" } })
      .then(function (r) {
        return r.json().then(function (body) {
          if (!r.ok || body.ok === false) {
            var err = new Error(body.error || "Request failed (" + r.status + ")");
            err.needsSetup = !!body.needs_setup;
            throw err;
          }
          return body;
        });
      });
  }

  function showError(err) {
    var box = $("#tkt-msg");
    if (!box) return;
    box.className = "tkt-note error";
    box.hidden = false;
    box.textContent = err.needsSetup
      ? err.message + " "
      : err.message;
    if (err.needsSetup) {
      var link = el("a", { href: BASE + "/setup" }, "Open setup");
      box.appendChild(link);
    }
  }

  function stamp(at) {
    var node = $("#tkt-stamp");
    if (!node) return;
    node.textContent = at
      ? "Read from Knack " + new Date(at * 1000).toLocaleString()
      : "";
  }

  function loadBar(row) {
    var wrap = el("div", { class: "tkt-load " + (row.load_state || "unknown") });
    var track = el("div", { class: "track" });
    var allowance = row.allowance || 1;
    var ceiling = Math.max(row.per_month, allowance) * 1.35 || 1;
    var fill = el("div", { class: "fill" });
    fill.style.width = Math.min(100, (row.per_month / ceiling) * 100) + "%";
    var notch = el("div", { class: "notch" });
    notch.style.left = Math.min(100, (allowance / ceiling) * 100) + "%";
    notch.setAttribute("title", "Plan absorbs about " + allowance + " a month");
    track.appendChild(fill);
    track.appendChild(notch);
    wrap.appendChild(track);
    var caption = row.per_month + "/mo vs " + allowance;
    if (row.pct_of_revenue !== null && row.pct_of_revenue !== undefined) {
      caption += " · " + row.pct_of_revenue + "% of revenue";
    }
    wrap.appendChild(el("span", { class: "cap" }, caption));
    return wrap;
  }

  function badge(text, kind) {
    return el("span", { class: "tkt-badge " + kind }, text);
  }

  // ---- page: by customer + audits -------------------------------------
  function initIndex() {
    var state = { rows: [], reports: null, tab: "customers" };

    function renderStats(totals) {
      var host = $("#tkt-stats");
      host.innerHTML = "";
      [
        ["Clients", totals.clients, ""],
        ["Tickets", totals.tickets, ""],
        ["Open now", totals.open, ""],
        ["Past " + CFG.slaDays + " days", totals.breaches, totals.breaches ? "flag" : ""],
        ["No movement", totals.stale, totals.stale ? "watch" : ""],
        ["Over plan", totals.over_load, totals.over_load ? "flag" : ""]
      ].forEach(function (item) {
        var card = el("div", { class: "tkt-stat " + item[2] });
        card.appendChild(el("b", null, item[1]));
        card.appendChild(el("span", null, item[0]));
        host.appendChild(card);
      });
    }

    function renderCustomers() {
      var term = ($("#tkt-q").value || "").toLowerCase().trim();
      var only = $("#tkt-filter").value;
      var body = $("#tkt-rows");
      body.innerHTML = "";

      var rows = state.rows.filter(function (r) {
        if (term && r.name.toLowerCase().indexOf(term) === -1) return false;
        if (only === "open" && !r.open) return false;
        if (only === "breach" && !r.breaches) return false;
        if (only === "over" && r.load_state !== "over") return false;
        if (only === "unmatched" && r.matched) return false;
        return true;
      });

      if (!rows.length) {
        var tr = el("tr");
        tr.appendChild(el("td", { colspan: "8", class: "tkt-empty" },
          "No clients match that filter."));
        body.appendChild(tr);
        return;
      }

      rows.forEach(function (r) {
        var tr = el("tr");
        var nameCell = el("td");
        var link = el("a", { href: BASE + "/client/" + encodeURIComponent(r.key) }, r.name);
        nameCell.appendChild(link);
        if (!r.matched) {
          nameCell.appendChild(document.createTextNode(" "));
          nameCell.appendChild(badge("no client on file", "watch"));
        }
        tr.appendChild(nameCell);
        tr.appendChild(el("td", { class: "num r" }, money(r.monthly_price)));
        tr.appendChild(el("td", { class: "num r" }, r.open));
        tr.appendChild(el("td", { class: "num r" }, r.total));
        tr.appendChild(el("td", { class: "num r" },
          r.avg_resolve_days === null ? "—" : r.avg_resolve_days + "d"));

        var slaCell = el("td", { class: "r" });
        if (r.breaches) slaCell.appendChild(badge(r.breaches + " past " + CFG.slaDays + "d", "flag"));
        else if (r.approaching) slaCell.appendChild(badge(r.approaching + " due soon", "watch"));
        else slaCell.appendChild(el("span", { class: "num" }, "—"));
        tr.appendChild(slaCell);

        var loadCell = el("td");
        loadCell.appendChild(loadBar(r));
        tr.appendChild(loadCell);
        tr.appendChild(el("td", null, r.top_type || "—"));
        body.appendChild(tr);
      });
    }

    function renderReports(payload) {
      var host = $("#tkt-reports");
      host.innerHTML = "";
      payload.reports.forEach(function (report) {
        var card = el("div", { class: "tkt-card" });
        var head = el("header");
        head.appendChild(el("h2", null, report.title));
        if (!report.count) {
          head.appendChild(badge("clear", "ok"));
        } else {
          head.appendChild(badge(report.count + (report.count === 1 ? " row" : " rows"), "info"));
          if (report.flags) head.appendChild(badge(report.flags + " to act on", "flag"));
        }
        var dl = el("a", {
          class: "tkt-btn",
          href: BASE + "/api/export.csv?report=" + encodeURIComponent(report.key)
        }, "CSV");
        dl.style.marginLeft = "auto";
        head.appendChild(dl);
        head.appendChild(el("p", null, report.blurb));
        card.appendChild(head);

        if (!report.rows.length) {
          card.appendChild(el("div", { class: "tkt-empty" }, report.empty));
        } else {
          var scroll = el("div", { class: "tkt-scroll" });
          var table = el("table");
          var thead = el("thead");
          // One alignment per column, decided from the first row so the header
          // never sits over a column it disagrees with.
          var first = report.rows[0].cells;
          var align = report.columns.map(function (_c, i) {
            return i > 0 && isNumeric(first[i]);
          });
          var hr = el("tr");
          report.columns.forEach(function (c, i) {
            hr.appendChild(el("th", align[i] ? { class: "r" } : null, c));
          });
          hr.appendChild(el("th", null, ""));
          thead.appendChild(hr);
          table.appendChild(thead);
          var tbody = el("tbody");
          report.rows.forEach(function (row) {
            var tr = el("tr");
            row.cells.forEach(function (cell, i) {
              tr.appendChild(el("td", align[i] ? { class: "num r" } : null, cell));
            });
            var last = el("td", { class: "r" });
            last.appendChild(badge(row.severity, row.severity));
            tr.appendChild(last);
            tbody.appendChild(tr);
          });
          table.appendChild(tbody);
          scroll.appendChild(table);
          card.appendChild(scroll);
        }
        host.appendChild(card);
      });
    }

    function setTab(tab) {
      state.tab = tab;
      $("#tab-customers").setAttribute("aria-selected", tab === "customers");
      $("#tab-audits").setAttribute("aria-selected", tab === "audits");
      $("#panel-customers").hidden = tab !== "customers";
      $("#panel-audits").hidden = tab !== "audits";
      if (tab === "audits" && !state.reports) {
        $("#tkt-reports").innerHTML = "<div class='tkt-empty'>Running the audits…</div>";
        get("/api/reports").then(function (payload) {
          state.reports = payload;
          renderReports(payload);
        }).catch(showError);
      }
    }

    function load(force) {
      $("#tkt-rows").innerHTML =
        "<tr><td colspan='8' class='tkt-empty'>Reading tickets…</td></tr>";
      return get("/api/summary" + (force ? "?force=1" : "")).then(function (data) {
        state.rows = data.rows;
        state.reports = null;
        renderStats(data.totals);
        renderCustomers();
        stamp(data.fetched_at);
        if (state.tab === "audits") setTab("audits");
      }).catch(showError);
    }

    $("#tkt-q").addEventListener("input", renderCustomers);
    $("#tkt-filter").addEventListener("change", renderCustomers);
    $("#tab-customers").addEventListener("click", function () { setTab("customers"); });
    $("#tab-audits").addEventListener("click", function () { setTab("audits"); });
    $("#tkt-refresh").addEventListener("click", function () {
      this.disabled = true;
      var btn = this;
      load(true).then(function () { btn.disabled = false; });
    });

    load(false);
  }

  // ---- page: one customer ---------------------------------------------
  function initClient() {
    get("/api/client/" + encodeURIComponent(CFG.clientKey)).then(function (data) {
      var row = data.row;
      $("#tkt-name").textContent = row.name;
      $("#tkt-meta").textContent = [
        row.monthly_price ? money(row.monthly_price) + "/mo" : "No monthly price on file",
        (row.products || []).join(", "),
        row.matched ? "" : "No matching client record"
      ].filter(Boolean).join(" · ");

      var stats = $("#tkt-stats");
      stats.innerHTML = "";
      [
        ["Open now", row.open, row.open ? "" : ""],
        ["Past " + CFG.slaDays + " days", row.breaches, row.breaches ? "flag" : ""],
        ["No movement", row.stale, row.stale ? "watch" : ""],
        ["Avg days to close", row.avg_resolve_days === null ? "—" : row.avg_resolve_days, ""],
        ["Tickets a month", row.per_month, ""],
        ["Cost to serve", money(row.cost_to_serve), row.load_state === "over" ? "flag" : ""]
      ].forEach(function (item) {
        var card = el("div", { class: "tkt-stat " + item[2] });
        card.appendChild(el("b", null, item[1]));
        card.appendChild(el("span", null, item[0]));
        stats.appendChild(card);
      });

      $("#tkt-loadbar").appendChild(loadBar(row));
      $("#tkt-loadnote").textContent = row.pct_of_revenue === null
        ? "No monthly price on file, so cost to serve cannot be read against revenue."
        : "Support costs " + row.pct_of_revenue + "% of what this client pays each month"
          + (row.hours_estimated ? ", using estimated hours." : ".");

      var types = $("#tkt-types");
      types.innerHTML = "";
      if (!data.by_type.length) {
        types.appendChild(el("li", null, "Nothing logged yet."));
      }
      data.by_type.slice(0, 8).forEach(function (pair) {
        var li = el("li");
        li.appendChild(el("span", null, pair[0]));
        li.appendChild(el("span", { class: "num" }, pair[1]));
        types.appendChild(li);
      });

      var months = data.by_month.slice(-12);
      var peak = months.reduce(function (m, p) { return Math.max(m, p[1]); }, 1);
      var chart = $("#tkt-months");
      var labels = $("#tkt-monthlabels");
      chart.innerHTML = "";
      labels.innerHTML = "";
      months.forEach(function (pair) {
        var bar = el("i");
        bar.style.height = Math.max(2, (pair[1] / peak) * 100) + "%";
        bar.setAttribute("title", pair[0] + ": " + pair[1] + " tickets");
        chart.appendChild(bar);
        labels.appendChild(el("span", null, pair[0].slice(5)));
      });

      var body = $("#tkt-rows");
      body.innerHTML = "";
      if (!data.tickets.length) {
        body.appendChild(el("tr")).appendChild(
          el("td", { colspan: "8", class: "tkt-empty" }, "No tickets on file."));
      }
      data.tickets.forEach(function (t) {
        var tr = el("tr");
        tr.appendChild(el("td", { class: "num nw" }, "#" + t.number));
        tr.appendChild(el("td", { class: "num nw" }, dateOnly(t.opened)));
        tr.appendChild(el("td", { class: "wide" }, t.summary || t.type));
        tr.appendChild(el("td", { class: "nw" }, t.type));
        var st = el("td", { class: "nw" });
        st.appendChild(badge(t.status, t.state === "open"
          ? ((t.age_days || 0) > CFG.slaDays && !t.blocked ? "flag" : "watch")
          : "ok"));
        tr.appendChild(st);
        tr.appendChild(el("td", { class: "nw" }, t.assignee));
        tr.appendChild(el("td", { class: "num r nw" }, t.hours ? t.hours + "h" : "—"));
        tr.appendChild(el("td", { class: "num r nw" }, t.state === "open"
          ? (t.age_days || 0) + "d open"
          : (t.resolve_days === null ? "—" : t.resolve_days + "d")));
        body.appendChild(tr);
      });

      $("#tkt-csv").href = BASE + "/api/export.csv?client=" + encodeURIComponent(CFG.clientKey);
      stamp(data.fetched_at);
    }).catch(showError);
  }

  // ---- page: setup -----------------------------------------------------
  function initSetup() {
    var state = { ticketFields: [], clientFields: [], saved: {} };

    function fillSelect(select, fields, selected) {
      select.innerHTML = "";
      select.appendChild(el("option", { value: "" }, "— not mapped —"));
      fields.forEach(function (f) {
        var opt = el("option", { value: f.key }, f.name + "  (" + f.key + ")");
        if (f.key === selected) opt.setAttribute("selected", "selected");
        select.appendChild(opt);
      });
      select.value = selected || "";
    }

    function loadFields(which, objectKey, map) {
      if (!objectKey) return Promise.resolve();
      return get("/api/fields/" + encodeURIComponent(objectKey) + "?for=" + which)
        .then(function (data) {
          state[which === "clients" ? "clientFields" : "ticketFields"] = data.fields;
          var prefix = which === "clients" ? "cf-" : "tf-";
          Object.keys(data.guess).forEach(function (k) {
            if (!map[k]) map[k] = data.guess[k];
          });
          document.querySelectorAll("[data-map='" + which + "']").forEach(function (sel) {
            fillSelect(sel, data.fields, map[sel.dataset.field]);
            sel.id = prefix + sel.dataset.field;
          });
        });
    }

    function collect(which) {
      var out = {};
      document.querySelectorAll("[data-map='" + which + "']").forEach(function (sel) {
        if (sel.value) out[sel.dataset.field] = sel.value;
      });
      return out;
    }

    Promise.all([get("/api/objects"), get("/api/fieldmap")]).then(function (res) {
      var objects = res[0].objects;
      state.saved = res[1];
      [["#tkt-obj", state.saved.tickets_object],
       ["#tkt-clientobj", state.saved.clients_object]].forEach(function (pair) {
        var sel = $(pair[0]);
        sel.innerHTML = "";
        sel.appendChild(el("option", { value: "" }, "— choose an object —"));
        objects.forEach(function (o) {
          sel.appendChild(el("option", { value: o.key }, o.name + "  (" + o.key + ")"));
        });
        sel.value = pair[1] || "";
      });
      return Promise.all([
        loadFields("tickets", state.saved.tickets_object, state.saved.fieldmap || {}),
        loadFields("clients", state.saved.clients_object, state.saved.client_fieldmap || {})
      ]);
    }).catch(showError);

    $("#tkt-obj").addEventListener("change", function () {
      loadFields("tickets", this.value, {}).catch(showError);
    });
    $("#tkt-clientobj").addEventListener("change", function () {
      loadFields("clients", this.value, {}).catch(showError);
    });

    $("#tkt-save").addEventListener("click", function () {
      var btn = this;
      btn.disabled = true;
      fetch(BASE + "/api/fieldmap", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tickets_object: $("#tkt-obj").value,
          clients_object: $("#tkt-clientobj").value,
          fieldmap: collect("tickets"),
          client_fieldmap: collect("clients")
        })
      }).then(function (r) { return r.json(); }).then(function (body) {
        btn.disabled = false;
        var box = $("#tkt-msg");
        box.hidden = false;
        if (!body.ok) {
          box.className = "tkt-note error";
          box.textContent = body.error;
          return;
        }
        box.className = "tkt-note";
        box.textContent = "Mapping saved. ";
        box.appendChild(el("a", { href: BASE + "/" }, "Open the audit"));
      }).catch(function (err) { btn.disabled = false; showError(err); });
    });
  }

  // ---- dispatch --------------------------------------------------------
  document.addEventListener("DOMContentLoaded", function () {
    if (CFG.page === "index") initIndex();
    else if (CFG.page === "client") initClient();
    else if (CFG.page === "setup") initSetup();
  });
})();
