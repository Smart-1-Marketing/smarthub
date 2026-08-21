/* Renders a Smart 1 ski plan into a container. Loaded by both the embedded
   app and the standalone report window, so there is exactly one copy of the
   markup and it can never drift between them. */

(function (root) {
  "use strict";

  var OBJECTIVES = {
    lift_tickets: "Lift tickets",
    season_passes: "Season passes",
    lodging: "Lodging",
    local_visits: "Local visits and tubing"
  };
  var TYPES = {
    day_drive: "Day and drive market",
    regional_overnight: "Regional with lodging",
    destination: "Destination resort",
    tubing_focus: "Tubing and beginner focus"
  };
  var TIER = {
    "Aggressive activation":          { cls: "tag-go",   color: "#E8933A" },
    "Selective activation":           { cls: "tag-sel",  color: "#3E7FA6" },
    "Hold or use future-date offers": { cls: "tag-hold", color: "#C4607A" }
  };

  function money(n) { return "$" + Math.round(Number(n) || 0).toLocaleString("en-US"); }
  function int(n)   { return Math.round(Number(n) || 0).toLocaleString("en-US"); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function boardCell(k, v) {
    return '<div class="board-cell"><span class="k">' + esc(k) + '</span><div class="v">' + esc(v) + "</div></div>";
  }
  function stat(k, v, cls) {
    return '<div class="stat ' + (cls || "") + '"><span class="k">' + esc(k) + '</span><div class="v">' + esc(v) + "</div></div>";
  }
  function chips(items) {
    return '<ul class="chips">' + (items || []).map(function (i) { return "<li>" + esc(i) + "</li>"; }).join("") + "</ul>";
  }

  /* -------------------------------------- signature: the season board SVG */

  function seasonBoard(weeks) {
    if (!weeks || !weeks.length) return "";

    var W = 26, GAP = 6, PAD = 30, AXIS = 118, BELOW = 62;
    var width = PAD + weeks.length * (W + GAP) + PAD;
    var height = AXIS + BELOW + 40;
    var unitUp = AXIS / 7, unitDown = BELOW / 7;

    var grid = [0, 2, 4, 6].map(function (d) {
      var y = AXIS - d * unitUp;
      return '<line x1="' + (PAD - 8) + '" y1="' + y + '" x2="' + (width - PAD + 8) + '" y2="' + y +
             '" stroke="' + (d === 0 ? "#16283F" : "#D6E2EC") + '" stroke-width="' + (d === 0 ? 1.5 : 1) + '"/>' +
             '<text x="' + (PAD - 12) + '" y="' + (y + 3) + '" text-anchor="end" font-size="9" fill="#8CA0B4">' + d + "</text>";
    }).join("");

    var bars = weeks.map(function (w, i) {
      var x = PAD + i * (W + GAP);
      var up = Math.min(w.avg_activation_days, 7) * unitUp;
      var qual = Math.min(w.avg_qualified_ad_days, 7) * unitUp;
      var down = Math.min(w.avg_suppressed_days, 7) * unitDown;
      var color = (TIER[w.recommendation] || {}).color || "#3E7FA6";

      return (
        // Pale bar: days media should be in market (lead-shifted).
        '<rect x="' + x + '" y="' + (AXIS - up) + '" width="' + W + '" height="' + Math.max(up, 1.5) +
          '" rx="2" fill="' + color + '" opacity="0.35"/>' +
        // Solid bar: days that actually qualified.
        '<rect x="' + x + '" y="' + (AXIS - qual) + '" width="' + W + '" height="' + Math.max(qual, 1.5) +
          '" rx="2" fill="' + color + '">' +
          "<title>Week " + w.week_number + " — " + w.avg_qualified_ad_days + " qualified days (" +
          w.avg_qualified_weekend_days + " on weekends), " + w.avg_activation_days + " activation days, " +
          w.avg_suppressed_days + " suppressed. " + w.recommendation + ".</title>" +
        "</rect>" +
        (down > 0.5
          ? '<rect x="' + x + '" y="' + AXIS + '" width="' + W + '" height="' + down + '" rx="2" fill="#C4607A" opacity="0.45"/>'
          : "") +
        '<text x="' + (x + W / 2) + '" y="' + (AXIS + BELOW + 18) + '" text-anchor="middle" font-size="9" fill="#8CA0B4">' +
          w.week_number + "</text>"
      );
    }).join("");

    return (
      '<div class="seasonboard">' +
        '<svg viewBox="0 0 ' + width + " " + height + '" preserveAspectRatio="xMinYMid meet" role="img" ' +
          'aria-label="Qualified and activation advertising days by week of the ski season">' +
          grid + bars +
          '<text x="' + PAD + '" y="' + (AXIS + BELOW + 34) + '" font-size="9" fill="#8CA0B4" letter-spacing="0.1em">WEEK OF SEASON</text>' +
        "</svg>" +
      "</div>" +
      '<div class="legend">' +
        '<span><i class="swatch" style="background:#E8933A"></i> Aggressive activation</span>' +
        '<span><i class="swatch" style="background:#3E7FA6"></i> Selective activation</span>' +
        '<span><i class="swatch" style="background:#C4607A"></i> Hold or future-date offers</span>' +
        '<span><i class="swatch" style="background:#3E7FA6;opacity:.35"></i> Media in market (lead-shifted)</span>' +
        '<span><i class="swatch" style="background:#C4607A;opacity:.45"></i> Suppressed days, below the line</span>' +
      "</div>"
    );
  }

  /* ---------------------------------------------------------- the outlook */

  function outlookBlock(o) {
    if (!o || o.unavailable) {
      return (
        '<section class="block">' +
          '<div class="block-eyebrow">The next 16 days</div>' +
          "<h3>Live outlook unavailable</h3>" +
          '<p class="lede">' + esc((o && o.note) || "The forecast could not be retrieved. The historical plan is unaffected.") + "</p>" +
        "</section>"
      );
    }

    return (
      '<section class="block">' +
        '<div class="block-eyebrow">The next 16 days</div>' +
        "<h3>What the rules would do right now</h3>" +
        '<p class="lede">The same triggers, run against the live forecast for this mountain. This is the plan working, not describing itself. ' +
          "Of the next " + o.days_ahead + " days, <strong>" + o.qualified_days + "</strong> qualify, <strong>" +
          o.activation_days + "</strong> call for media in market, and <strong>" + o.suppressed_days + "</strong> call for suppression.</p>" +
        "<table><thead><tr>" +
          "<th>Day</th><th class='num'>Hi / Lo</th><th class='num'>Snow</th><th class='num'>Wind</th>" +
          "<th>Signal</th><th>Call</th>" +
        "</tr></thead><tbody>" +
        o.days.map(function (d) {
          var tag = d.suppressed ? "tag-hold" : d.qualified ? "tag-go" : d.activation ? "tag-sel" : "tag-quiet";
          var label = d.suppressed ? "Suppress" : d.qualified ? "Qualified" : d.activation ? "In market" : "Hold";
          return "<tr" + (d.is_weekend ? ' class="is-weekend"' : "") + ">" +
            "<td><strong>" + esc(d.weekday) + "</strong> <span style='color:var(--ink-soft)'>" + esc(d.date.slice(5)) + "</span></td>" +
            '<td class="num">' + d.high_f + "° / " + d.low_f + "°</td>" +
            '<td class="num">' + (d.snowfall_inches ? d.snowfall_inches + '"' : "—") + "</td>" +
            '<td class="num">' + d.wind_mph + "</td>" +
            '<td><span class="tag ' + tag + '">' + label + "</span></td>" +
            "<td>" + esc(d.triggers.length ? d.triggers.join(", ") : d.call) + "</td>" +
          "</tr>";
        }).join("") +
        "</tbody></table>" +
        '<p class="methodology">' + esc(o.note) + "</p>" +
      "</section>"
    );
  }

  /* ----------------------------------------------------------- the report */

  function markup(r, opts) {
    var weeks = r.historical_weekly_plan || [];
    var c = r.climate || {};
    var a = r.audience || {};
    var s = r.savings_model || {};
    var b = r.budget_plan || {};
    var t = r.recommended_targets || {};
    var sc = r.weather_marketing_readiness || {};
    var ea = c.elevation_adjustment || {};
    var standalone = opts && opts.standalone;

    var html = "";

    if (!standalone) {
      html +=
        '<div class="toolbar no-print">' +
          '<button type="button" class="btn btn-primary" data-act="open">Open full report</button>' +
          '<button type="button" class="btn btn-ghost" data-act="print">Save as PDF</button>' +
          '<button type="button" class="btn btn-ghost" data-act="json">Download data</button>' +
          '<button type="button" class="btn btn-ghost" data-act="reset">Start over</button>' +
        "</div>";
      if (opts && opts.notice) {
        html += '<div class="banner banner-' + opts.notice.level + ' no-print" style="margin:14px 20px 0">' +
                esc(opts.notice.text) + "</div>";
      }
    }

    /* ---------------------------------------------------------- the board */
    html +=
      '<div class="board">' +
        '<div class="board-top">' +
          "<div><h2>" + esc(r.resort.name) + "</h2>" +
            '<div class="board-sub">' +
              esc(r.resort.location || r.resort.zip_code) +
              (r.resort.site_elevation_ft ? " · " + int(r.resort.site_elevation_ft) + " ft" : "") +
              " · " + esc(r.resort.operating_months) + "<br>" +
              esc(TYPES[r.resort.resort_type] || "") + " · " +
              esc(OBJECTIVES[r.resort.objective] || r.resort.objective) + " · " +
              "books " + esc(r.resort.booking_window_label) +
            "</div>" +
          "</div>" +
          '<div class="score">' +
            '<div class="score-num">' + sc.score + "</div>" +
            '<div class="score-band">' + esc(sc.band) + "</div>" +
            '<div class="score-label">Weather marketing readiness · out of 100</div>' +
          "</div>" +
        "</div>" +
        '<div class="board-grid">' +
          boardCell("Seasons analyzed", c.seasons_analyzed) +
          boardCell("Avg snowfall", c.avg_natural_snowfall_inches + '"') +
          boardCell("Snowmaking days", c.avg_snowmaking_days) +
          boardCell("Powder days", c.avg_powder_days) +
          boardCell("Bluebird days", c.avg_bluebird_days) +
          boardCell("Rain-risk days", c.avg_rain_risk_days) +
          boardCell("High-wind days", c.avg_high_wind_days) +
          boardCell("Media lead", c.media_lead_days + (c.media_lead_days === 1 ? " day" : " days")) +
        "</div>" +
      "</div>";

    /* --------------------------------------------------- data provenance */
    html +=
      '<section class="block">' +
        '<div class="block-eyebrow">Where these numbers come from</div>' +
        "<h3>The mountain, not the town</h3>" +
        '<p class="lede">Weather was read at <strong>' + esc(r.resort.coordinates_source.toLowerCase()) + "</strong> " +
          "(" + r.resort.latitude.toFixed(3) + ", " + r.resort.longitude.toFixed(3) + "), " +
          "across " + c.seasons_analyzed + " completed seasons (" + esc((c.season_years || []).join(", ")) + "). " +
          "Snowfall arrived from the source in <strong>" + esc(c.units_reported_by_source.snowfall) +
          "</strong> and temperature in <strong>" + esc(c.units_reported_by_source.temperature) +
          "</strong>; both were converted from the units the source declared rather than assumed.</p>" +
        '<div class="callout">' +
          (ea.applied
            ? "<strong>Elevation corrected.</strong> " + esc(ea.note)
            : "<strong>No elevation correction.</strong> " + esc(ea.note)) +
        "</div>" +
      "</section>";

    /* ---------------------------------------------------- the season board */
    if (weeks.length) {
      html +=
        '<section class="block">' +
          '<div class="block-eyebrow">Section 1 — The season, week by week</div>' +
          "<h3>Not every week is worth the same money</h3>" +
          '<p class="lede">Each bar is one week of the season, averaged across ' + c.seasons_analyzed +
            " seasons. The solid bar is days that qualified. The pale bar behind it is days media should already have been " +
            "running, shifted forward by this resort's " + c.media_lead_days + "-day booking lead. Below the line: days when " +
            "immediate-visit advertising was likely wasted.</p>" +
          seasonBoard(weeks) +
        "</section>" +

        '<section class="block">' +
          '<div class="block-eyebrow">The weekly table</div>' +
          "<h3>What each week looks like historically</h3>" +
          "<table><thead><tr>" +
            "<th>Week</th><th class='num'>Qualified</th><th class='num'>Of those, weekend</th>" +
            "<th class='num'>In market</th><th class='num'>Snowfall</th><th class='num'>Suppressed</th><th>Recommendation</th>" +
          "</tr></thead><tbody>" +
          weeks.map(function (w) {
            var tier = TIER[w.recommendation] || { cls: "tag-sel" };
            return "<tr>" +
              "<td><strong>Week " + w.week_number + "</strong></td>" +
              '<td class="num">' + w.avg_qualified_ad_days + "</td>" +
              '<td class="num">' + w.avg_qualified_weekend_days + "</td>" +
              '<td class="num">' + w.avg_activation_days + "</td>" +
              '<td class="num">' + w.avg_snowfall_inches + '"</td>' +
              '<td class="num">' + w.avg_suppressed_days + "</td>" +
              '<td><span class="tag ' + tier.cls + '">' + esc(w.recommendation) + "</span></td>" +
            "</tr>";
          }).join("") +
          "</tbody></table>" +
          '<p class="methodology">' + esc(c.qualified_day_definition) + "</p>" +
          '<p class="methodology">' + esc(c.activation_definition) + "</p>" +
        "</section>";
    }

    /* ------------------------------------------------------- the outlook */
    html += outlookBlock(r.outlook);

    /* ------------------------------------------------------- the audience */
    html +=
      '<section class="block">' +
        '<div class="block-eyebrow">Section 2 — Who is reachable</div>' +
        "<h3>Estimated targeted skiing households</h3>" +
        '<p class="lede">' + esc(a.basis) + "</p>";

    if (a.markets && a.markets.length) {
      html +=
        "<table><thead><tr>" +
          "<th>Feeder market</th><th class='num'>Drive</th><th class='num'>Households</th>" +
          "<th class='num'>Relevance</th><th class='num'>Targeted skiing HH</th>" +
        "</tr></thead><tbody>" +
        a.markets.map(function (m) {
          return "<tr>" +
            "<td><strong>" + esc(m.name) + "</strong>" + (m.state ? ", " + esc(m.state) : "") + "</td>" +
            '<td class="num">' + m.drive_miles + " mi<br><span style='color:var(--ink-soft)'>~" + m.drive_hours + " hr</span></td>" +
            '<td class="num">' + int(m.households) + "</td>" +
            '<td class="num">' + m.drive_relevance_percent + "%</td>" +
            '<td class="num"><strong>' + int(m.targeted_skiing_households) + "</strong></td>" +
          "</tr>";
        }).join("") +
        "</tbody></table>" +
        '<p class="methodology">Relevance falls off with distance against this resort\'s own drive radius of ' +
          a.core_radius_miles + " miles, taken from the typical guest drive time. Markets inside half that radius carry full weight.</p>";

      if (a.markets_not_located && a.markets_not_located.length) {
        html += '<div class="callout"><strong>Not located:</strong> ' + esc(a.markets_not_located.join(", ")) +
                ". These markets were left out of the estimate. Re-enter them with a state, or add households directly as \"Market: 640000\".</div>";
      }
    }

    html +=
        '<h4 class="sub">The funnel</h4>' +
        '<div class="funnel">' +
          (a.funnel || []).map(function (f) {
            return '<div class="funnel-row"><span class="step-name">' + esc(f.step) + "</span>" +
                   '<span class="step-val">' + int(f.value) + "</span></div>";
          }).join("") +
        "</div>" +
        '<div class="stat-row">' +
          stat("Broad skiing households", int(a.broad_skiing_households)) +
          stat("Family ski households", int(a.estimated_family_ski_households)) +
          stat("Past-visitor style households", int(a.estimated_past_resort_visitor_households)) +
          stat("Skiers and riders represented", int(a.estimated_targeted_skiers_and_riders)) +
        "</div>" +
        '<p class="methodology">' + esc(a.methodology_note) + "</p>" +
      "</section>";

    /* --------------------------------------------------- budget protection */
    html +=
      '<section class="block">' +
        '<div class="block-eyebrow">Section 3 — Budget protection</div>' +
        "<h3>Always-on versus weather-triggered</h3>" +
        '<p class="lede">An always-on plan spends the same money in a rainy 45-degree week as it does the day before a storm. ' +
          "The trigger-controlled model concentrates the same budget where it historically had a chance to work.</p>" +
        '<div class="stat-row">' +
          stat("Always-on season spend", money(s.always_on_season_spend)) +
          stat("Trigger-controlled spend", money(s.modeled_trigger_controlled_spend)) +
          stat("Estimated budget protected", money(s.estimated_budget_protected), "is-protected") +
          stat("Percent protected", s.estimated_budget_protected_percent + "%", "is-protected") +
        "</div>" +
        '<div class="stat-row">' +
          stat("Season weeks", s.season_weeks) +
          stat("Modeled weekly budget", money(s.modeled_weekly_budget)) +
          stat("Historically strong weeks", s.historically_strong_activation_weeks) +
          stat("Historically weak weeks", s.historically_weak_or_suppressed_weeks) +
        "</div>" +
        '<div class="callout" style="border-left-color:' + (s.estimated_budget_protected_percent >= 12 ? "#E8933A" : "#C4607A") + '">' +
          "<strong>Read this before you quote the number.</strong> " + esc(s.verdict) + "</div>" +
        '<div class="callout"><strong>The floor is set by the booking window.</strong> Activation never drops below ' +
          s.minimum_activation_share_percent + "% of the always-on level. " + esc(s.minimum_activation_share_source) +
          ". A resort whose guests book weeks ahead cannot run a pure trigger campaign, so its floor is higher and its protected budget smaller.</div>" +
        '<p class="lede" style="margin-top:18px;margin-bottom:8px"><strong>The protected budget is not a refund.</strong> It is money that could be:</p>' +
        chips(s.protected_budget_uses) +
        '<p class="methodology">' + esc(s.methodology_note) + "</p>" +
      "</section>";

    /* ------------------------------------------------------- the media plan */
    html +=
      '<section class="block">' +
        '<div class="block-eyebrow">Section 4 — The media plan</div>' +
        "<h3>Three channels, " + money(b.budget) + " a month</h3>" +
        '<p class="lede">' + esc(b.objective_rationale) + " " + esc(b.resort_type_rationale) + "</p>" +
        "<table><thead><tr>" +
          "<th>Channel</th><th class='num'>Share</th><th class='num'>Monthly</th>" +
          "<th class='num'>Planning CPM</th><th class='num'>Est. impressions</th>" +
        "</tr></thead><tbody>" +
        (b.channels || []).map(function (ch) {
          return "<tr>" +
            "<td><strong>" + esc(ch.channel) + "</strong><br><span style='color:var(--ink-soft);font-size:12.5px'>" + esc(ch.role) + "</span></td>" +
            '<td class="num">' + ch.share_percent + "%</td>" +
            '<td class="num">' + money(ch.budget) + "</td>" +
            '<td class="num">$' + ch.cpm_assumption + "</td>" +
            '<td class="num">' + int(ch.impressions) + "</td>" +
          "</tr>";
        }).join("") +
        "</tbody></table>";

    if (b.current_media_gaps && b.current_media_gaps.length) {
      html +=
        '<h4 class="sub">Against what is running today</h4>' +
        '<p class="lede">Current paid media: ' + esc(b.current_media) + "</p>" +
        '<ul class="plain">' + b.current_media_gaps.map(function (g) { return "<li>" + esc(g) + "</li>"; }).join("") + "</ul>";
    }

    html +=
        '<p class="methodology">Paid search and paid social are intentionally excluded. Actual delivery varies by ' +
          esc((b.delivery_variance_factors || []).join(", ").toLowerCase()) + ".</p>" +
      "</section>";

    /* -------------------------------------------------------- score rubric */
    html +=
      '<section class="block">' +
        '<div class="block-eyebrow">Section 5 — The readiness score</div>' +
        "<h3>" + sc.score + " out of 100 — " + esc(sc.band) + "</h3>" +
        '<p class="lede">' + esc(sc.band_meaning) + "</p>" +
        '<div class="scorebars">' +
          (sc.components || []).map(function (comp) {
            var pct = comp.out_of ? Math.round((comp.earned / comp.out_of) * 100) : 0;
            return '<div class="scorebar">' +
              '<div class="scorebar-top"><span class="scorebar-name">' + esc(comp.name) + "</span>" +
                '<span class="scorebar-val">' + comp.earned + " / " + comp.out_of + "</span></div>" +
              '<div class="scorebar-track"><div class="scorebar-fill" style="width:' + pct + '%"></div></div>' +
              '<div class="scorebar-detail">' + esc(comp.detail) + "</div>" +
            "</div>";
          }).join("") +
        "</div>" +
        '<p class="methodology">Bands: ' +
          (sc.scale || []).slice().reverse().map(function (band) { return band.from + "+ " + band.label.toLowerCase(); }).join("; ") + ".</p>" +
      "</section>";

    /* ------------------------------------------------------------ geofence */
    html +=
      '<section class="block">' +
        '<div class="block-eyebrow">Section 6 — Where to target</div>' +
        "<h3>Geofence and visitation categories</h3>" +
        '<p class="lede">' + esc(t.why_it_matters) + "</p>" +
        '<div class="callout">' + esc(t.radius_recommendation) + "</div>" +
        (t.named_competitors && t.named_competitors.length
          ? '<p class="lede" style="margin-top:16px"><strong>Named competitors to target:</strong> ' + esc(t.named_competitors.join(", ")) + "</p>"
          : '<div style="height:16px"></div>') +
        chips(t.geofence_categories) +
        '<p class="methodology">' + esc(t.lookback_recommendation) + "</p>" +
      "</section>";

    /* ---------------------------------------------------- trigger playbook */
    html +=
      '<section class="block">' +
        '<div class="block-eyebrow">Section 7 — The trigger playbook</div>' +
        "<h3>What activates the campaign, and what pulls it back</h3>" +
        (r.trigger_plan || []).map(function (tr) {
          return '<div class="trigger ' + (tr.priority === "Protective" ? "is-protective" : "is-active") + '">' +
            '<div class="trigger-head"><h4>' + esc(tr.name) + "</h4>" +
              '<span class="tag ' + (tr.priority === "Protective" ? "tag-hold" : "tag-go") + '">' + esc(tr.priority) + "</span></div>" +
            "<dl>" +
              "<dt>Trigger</dt><dd>" + esc(tr.condition) + "</dd>" +
              "<dt>Timing</dt><dd>" + esc(tr.lead) + "</dd>" +
              "<dt>Action</dt><dd>" + esc(tr.action) + "</dd>" +
              "<dt>Products</dt><dd>" + esc((tr.products || []).join(" · ")) + "</dd>" +
            "</dl>" +
            '<div class="quote">' + esc(tr.message) + "</div>" +
          "</div>";
        }).join("") +
      "</section>";

    /* -------------------------------------------------------------- phases */
    html +=
      '<section class="block">' +
        '<div class="block-eyebrow">Section 8 — The full season</div>' +
        "<h3>Beyond the winter triggers</h3>" +
        (r.campaign_phases || []).map(function (p) {
          return '<div class="phase"><div><h4>' + esc(p.phase) + "</h4>" +
            '<div class="timing">' + esc(p.timing) + "</div></div>" + chips(p.focus) + "</div>";
        }).join("") +
      "</section>";

    /* --------------------------------------------------------- disclosures */
    html +=
      '<section class="block fineprint">' +
        "<h3>What this plan does not promise</h3>" +
        "<ul>" + (r.disclosures || []).map(function (d) { return "<li>" + esc(d) + "</li>"; }).join("") + "</ul>" +
        '<p class="methodology" style="margin-top:14px">' + esc(r.summary) + "</p>" +
        '<p class="methodology">Prepared for ' + esc(r.contact.name) + " · " +
          new Date(r.generated_at).toLocaleDateString("en-US", { dateStyle: "long" }) + " · Smart 1 Marketing</p>" +
      "</section>";

    return html;
  }

  function render(container, report, opts) {
    opts = opts || {};
    container.innerHTML = markup(report, opts);

    container.querySelectorAll("[data-act]").forEach(function (btn) {
      var act = btn.getAttribute("data-act");
      btn.addEventListener("click", function () {
        if (act === "print") window.print();
        if (act === "reset") window.location.reload();
        if (act === "open") root.Smart1Report.openStandalone(report);
        if (act === "json") download(report);
      });
    });
  }

  function download(report) {
    var blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = (report.resort.name || "ski-resort").replace(/[^a-z0-9]+/gi, "-").toLowerCase() + "-plan.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  /* Printing from inside an iframe is unreliable across browsers, and this
     tool is designed to be embedded. Open the report in its own tab and let
     the browser print a normal page. */

  function openStandalone(report) {
    var base = window.location.origin;
    var win = window.open("", "_blank");
    if (!win) {
      alert("Allow pop-ups for this site to open the full report.");
      return;
    }

    win.document.write(
      "<!doctype html><html lang='en'><head><meta charset='utf-8'>" +
      "<meta name='viewport' content='width=device-width, initial-scale=1'>" +
      "<title>" + esc(report.resort.name) + " — Ski Resort Growth and Weather Trigger Plan</title>" +
      "<link rel='preconnect' href='https://fonts.googleapis.com'>" +
      "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>" +
      "<link href='https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=Public+Sans:ital,wght@0,400;0,500;0,600;1,400&display=swap' rel='stylesheet'>" +
      "<link rel='stylesheet' href='" + base + "/styles.css'>" +
      "<style>body{padding:24px}</style></head><body>" +
      "<div class='no-print' style='max-width:900px;margin:0 auto 14px'>" +
        "<button class='btn btn-primary' onclick='window.print()'>Save as PDF</button></div>" +
      "<div class='card report-standalone' id='report'></div>" +
      "<script src='" + base + "/report.js'><\/script>" +
      "</body></html>"
    );
    win.document.close();

    var tries = 0;
    var tick = win.setInterval(function () {
      if (win.Smart1Report || tries++ > 200) {
        win.clearInterval(tick);
        if (win.Smart1Report) {
          win.Smart1Report.render(win.document.getElementById("report"), report, { standalone: true });
        }
      }
    }, 25);
  }

  root.Smart1Report = { render: render, markup: markup, openStandalone: openStandalone };
})(window);
