(() => {
  "use strict";
  const base = window.SF_BASE || "";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  let model = null;
  let activeVariant = null;
  let installMode = "sites";
  let toastTimer = null;

  const titleCase = value => String(value || "").replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
  const phaseLabel = value => ({pre_event:"Pre-event", active_event:"Active event", post_event:"Post-event", default:"Default", manual:"Manual"}[value] || titleCase(value));
  const number = (value, digits = 0) => Number(value || 0).toFixed(digits).replace(/\.0$/, "");
  const escapeHtml = value => String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
  const safeUrl = value => {
    const url = String(value || "");
    if (url.startsWith("/tools/smartforecast/") && base !== "/tools/smartforecast") {
      return base + url.slice("/tools/smartforecast".length);
    }
    return /^(https?:|\/)/i.test(url) ? url : "";
  };
  const formObject = form => Object.fromEntries(new FormData(form).entries());

  async function api(path, options = {}) {
    const response = await fetch(base + path, {
      headers: {"Content-Type":"application/json", ...(options.headers || {})},
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) throw new Error(payload.error || `Request failed (${response.status})`);
    return payload;
  }

  function toast(message, error = false) {
    const node = $("#toast");
    node.textContent = message;
    node.classList.toggle("is-error", error);
    node.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => node.classList.remove("is-visible"), 3000);
  }

  function go(view) {
    $$(".sf-tab").forEach(tab => tab.classList.toggle("is-active", tab.dataset.view === view));
    $$(".sf-view").forEach(panel => {
      const active = panel.dataset.panel === view;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
    history.replaceState(null, "", `#${view}`);
    window.scrollTo({top:0, behavior:"smooth"});
  }

  function formatDate(value, includeTime = true) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.valueOf())) return value;
    return new Intl.DateTimeFormat("en-US", includeTime ? {
      month:"short", day:"numeric", year:"numeric", hour:"numeric", minute:"2-digit"
    } : {month:"short", day:"numeric"}).format(date);
  }

  function currentRule() {
    const key = model?.state?.current_trigger;
    return model?.rules?.find(rule => rule.id === key) || null;
  }

  function renderAll(data) {
    model = data;
    activeVariant = data.current_variant || data.variants[0] || null;
    renderDashboard();
    renderSetup();
    renderRules();
    renderVariants();
    renderPreview(activeVariant);
    renderReport();
    populateOverride();
    $("#loadingState").hidden = true;
    const target = location.hash.slice(1);
    go(["dashboard","setup","triggers","content","preview","report"].includes(target) ? target : "dashboard");
  }

  function renderDashboard() {
    const site = model.site, weather = model.weather || {}, state = model.state || {};
    const rule = currentRule();
    $("#dashClient").textContent = site.client_name;
    $("#dashZip").textContent = site.postal_code;
    $("#dashIndustry").textContent = site.industry;
    $("#weatherTemp").textContent = number(weather.temperature);
    $("#weatherFeels").textContent = number(weather.feels_like);
    $("#weatherHumidity").textContent = `${number(weather.humidity)}%`;
    $("#weatherHigh").textContent = number(weather.forecast_high);
    $("#weatherLow").textContent = number(weather.forecast_low);
    $("#weatherWind").textContent = number(weather.wind_mph);
    $("#weatherUpdated").textContent = weather.observed_at ? formatDate(weather.observed_at) : "Cached";
    const alerts = weather.official_alerts || [];
    $("#weatherCondition").textContent = alerts[0] || (Number(weather.rain_probability) >= 70 ? "Rain likely" : "Forecast ready");
    const running = Boolean(site.enabled);
    $("#pauseToggle").checked = !running;
    $("#pauseLabel").textContent = running ? "Running" : "Paused";
    $("#statusText").textContent = running ? titleCase(state.status || "active") : "Paused";
    $("#statusPill").classList.toggle("is-paused", !running);
    $("#stateTrigger").textContent = rule?.name || "Default website";
    $("#statePhase").textContent = phaseLabel(state.phase);
    $("#stateActivated").textContent = formatDate(state.activated_at);
    $("#stateNext").textContent = `${site.check_interval_minutes} minutes`;
    $("#stateReason").textContent = state.reason || "No trigger currently qualifies";
    $("#stateChecks").textContent = state.qualify_count ? `Confirmed across ${state.qualify_count} consecutive check${state.qualify_count === 1 ? "" : "s"}` : "Waiting for qualifying weather";
    $("#stateExpectation").textContent = state.phase === "pre_event" ? "Pre-event messaging is live now. Active-event content takes over when current conditions meet the rule." : state.phase === "post_event" ? "Post-event content remains relevant until the configured window expires." : "The highest-priority matching trigger controls this content slot.";
    renderHero(model.current_variant, "dashboard");
  }

  function renderHero(content, target = "dashboard") {
    if (!content) return;
    if (target === "dashboard") {
      $("#currentContentName").textContent = content.name;
      $("#dashboardEyebrow").textContent = content.eyebrow;
      $("#dashboardHeadline").textContent = content.headline;
      $("#dashboardBody").textContent = content.body;
      $("#dashboardCta").textContent = content.cta_label;
      const img = $("#dashboardImage");
      img.src = safeUrl(content.desktop_image_url);
      img.alt = content.alt_text || "";
      img.style.objectPosition = content.desktop_focal || "50% 50%";
    }
  }

  function renderSetup() {
    const site = model.site, form = $("#setupForm");
    ["name","domain","platform","industry","location_label","postal_code","timezone","check_interval_minutes"].forEach(name => {
      if (form.elements[name]) form.elements[name].value = site[name] ?? "";
    });
    $$("#businessGoals input").forEach(input => input.checked = (site.business_goals || []).includes(input.value));
    const brand = site.branding || {};
    $("#brandFont").value = brand.font || "inherit";
    $("#brandHeadline").value = brand.headline_color || "#ffffff";
    $("#brandButton").value = brand.button_color || "#f6b544";
    $("#brandRadius").value = brand.border_radius ?? 18;
    $("#brandMobileSize").value = brand.mobile_headline_size ?? 38;
    renderEmbedCode();
  }

  function renderEmbedCode() {
    const token = model?.site?.embed_token;
    if (!token) return;
    const src = `${location.origin}${base}/embed/${token}`;
    const codes = {
      sites: `<iframe src="${src}" title="Dynamic website message" loading="lazy" style="width:100%;min-height:590px;border:0" allow="none"></iframe>`,
      wordpress: `<!-- SmartForecast: paste into a Custom HTML block -->\n<iframe src="${src}" title="Dynamic website message" loading="lazy" class="smartforecast-frame"></iframe>\n<style>.smartforecast-frame{width:100%;min-height:590px;border:0}@media(max-width:640px){.smartforecast-frame{min-height:620px}}</style>`,
      html: `<div style="width:100%"><iframe src="${src}" title="Dynamic website message" loading="lazy" style="display:block;width:100%;min-height:590px;border:0"></iframe></div>`,
    };
    $("#embedCode code").textContent = codes[installMode];
  }

  function conditionText(rule) {
    const list = rule.forecast_conditions || [];
    const joiner = rule.condition_mode === "any" ? " OR " : " AND ";
    return list.map(c => `${titleCase(c.metric)} ${c.operator} ${c.value}${c.metric.includes("temperature") || c.metric.includes("high") || c.metric.includes("low") || c.metric === "feels_like" ? "°F" : c.metric.includes("probability") || c.metric === "humidity" ? "%" : c.metric.includes("wind") ? " mph" : ""}`).join(joiner) || "Official alert only";
  }

  function renderRules() {
    $("#ruleEnabledCount").textContent = model.rules.filter(rule => rule.enabled).length;
    $("#ruleList").innerHTML = model.rules.map(rule => `
      <article class="sf-rule-card" data-rule="${escapeHtml(rule.id)}">
        <div class="sf-rule-row">
          <div class="sf-rule-name"><span class="sf-rule-badge">${rule.official_alerts?.length ? "⚡" : rule.name.toLowerCase().includes("freeze") ? "❄" : rule.name.toLowerCase().includes("rain") ? "◒" : "☀"}</span><div><strong>${escapeHtml(rule.name)}</strong><small>${escapeHtml(rule.industry)} · ${escapeHtml(rule.description)}</small></div></div>
          <div class="sf-rule-metric"><small>Priority</small><b>${rule.priority}</b></div>
          <div class="sf-rule-metric"><small>Lead</small><b>${number(rule.lead_hours)} hr</b></div>
          <div class="sf-rule-metric"><small>Post</small><b>${number(rule.post_hours)} hr</b></div>
          <div class="sf-rule-metric"><small>Activate</small><b>${rule.activation_checks} checks</b></div>
          <div class="sf-rule-metric"><small>Clear</small><b>${rule.clear_checks} checks</b></div>
          <label class="sf-switch"><input class="rule-enabled" type="checkbox" ${rule.enabled ? "checked" : ""}><span></span></label>
        </div>
        <div class="sf-rule-details">
          <div><div class="sf-condition-code">IF ${escapeHtml(conditionText(rule))}<br>THEN pre → active → ${rule.post_hours ? `${number(rule.post_hours)}-hour post` : "default"}${rule.official_alerts?.length ? `<br>ALERTS immediate: ${escapeHtml(rule.official_alerts.join(", "))}` : ""}</div></div>
          <div class="sf-rule-controls">
            <label>Priority<input name="priority" type="number" min="0" max="1000" value="${rule.priority}"></label>
            <label>Forecast value<input name="forecast_value" type="number" step="0.1" value="${rule.forecast_conditions?.[0]?.value ?? 0}"></label>
            <label>Active value<input name="active_value" type="number" step="0.1" value="${rule.active_conditions?.[0]?.value ?? 0}"></label>
            <label>Lead hrs<input name="lead_hours" type="number" min="0" max="240" value="${number(rule.lead_hours)}"></label>
            <label>Minimum hrs<input name="min_duration_hours" type="number" min="0" max="240" value="${number(rule.min_duration_hours)}"></label>
            <label>Post hrs<input name="post_hours" type="number" min="0" max="336" value="${number(rule.post_hours)}"></label>
            <label>Cooldown hrs<input name="cooldown_hours" type="number" min="0" max="336" value="${number(rule.cooldown_hours)}"></label>
            <label>Activate checks<input name="activation_checks" type="number" min="1" max="10" value="${rule.activation_checks}"></label>
            <label>Clear checks<input name="clear_checks" type="number" min="1" max="10" value="${rule.clear_checks}"></label>
          </div>
        </div>
      </article>`).join("");
    $$(".sf-rule-card").forEach(card => {
      const save = () => saveRule(card);
      $(".rule-enabled", card).addEventListener("change", save);
      $$("input[type=number]", card).forEach(input => input.addEventListener("change", save));
    });
  }

  async function saveRule(card) {
    const id = card.dataset.rule;
    const rule = model.rules.find(item => item.id === id);
    const payload = {
      ...rule,
      enabled: $(".rule-enabled", card).checked,
      priority: Number($("[name=priority]", card).value),
      lead_hours: Number($("[name=lead_hours]", card).value),
      min_duration_hours: Number($("[name=min_duration_hours]", card).value),
      post_hours: Number($("[name=post_hours]", card).value),
      cooldown_hours: Number($("[name=cooldown_hours]", card).value),
      activation_checks: Number($("[name=activation_checks]", card).value),
      clear_checks: Number($("[name=clear_checks]", card).value),
      forecast_conditions: (rule.forecast_conditions || []).map((item, index) => index ? item : ({...item, value:Number($("[name=forecast_value]", card).value)})),
      active_conditions: (rule.active_conditions || []).map((item, index) => index ? item : ({...item, value:Number($("[name=active_value]", card).value)})),
    };
    try {
      const data = await api(`/api/rules/${encodeURIComponent(id)}`, {method:"POST", body:JSON.stringify(payload)});
      Object.assign(rule, data.rule);
      renderRules();
      toast(`${rule.name} updated`);
    } catch (error) { toast(error.message, true); }
  }

  function renderVariants() {
    if (!activeVariant) return;
    const order = {default:0, pre_event:1, active_event:2, post_event:3};
    const sorted = [...model.variants].sort((a,b) => a.trigger_key.localeCompare(b.trigger_key) || (order[a.phase] - order[b.phase]));
    $("#variantList").innerHTML = sorted.map(variant => `<button class="sf-variant-item ${variant.id === activeVariant.id ? "is-active" : ""}" data-variant="${variant.id}" type="button"><strong>${escapeHtml(variant.name)}</strong><span>${escapeHtml(titleCase(variant.trigger_key))} · ${escapeHtml(phaseLabel(variant.phase))}</span></button>`).join("");
    $$(".sf-variant-item").forEach(button => button.addEventListener("click", () => {
      activeVariant = model.variants.find(item => item.id === Number(button.dataset.variant));
      renderVariants();
      fillContentForm();
    }));
    fillContentForm();
  }

  function fillContentForm() {
    const form = $("#contentForm"), content = activeVariant;
    if (!content) return;
    ["name","eyebrow","headline","body","cta_label","cta_url","desktop_image_url","mobile_image_url","alt_text","desktop_focal","mobile_focal","overlay_opacity"].forEach(name => {
      if (form.elements[name]) form.elements[name].value = content[name] ?? "";
    });
    $("#editorName").textContent = content.name;
    $("#editorPhase").textContent = phaseLabel(content.phase);
    $("#opacityOutput").textContent = Number(content.overlay_opacity || 0).toFixed(2);
    updateCrops();
  }

  function updateCrops() {
    const form = $("#contentForm");
    const desktop = safeUrl(form.elements.desktop_image_url.value);
    const mobile = safeUrl(form.elements.mobile_image_url.value) || desktop;
    $("#cropDesktop").src = desktop;
    $("#cropMobile").src = mobile;
    $("#cropDesktop").alt = $("#cropMobile").alt = form.elements.alt_text.value;
    $("#cropDesktop").style.objectPosition = form.elements.desktop_focal.value || "50% 50%";
    $("#cropMobile").style.objectPosition = form.elements.mobile_focal.value || "50% 50%";
    $("#opacityOutput").textContent = Number(form.elements.overlay_opacity.value || 0).toFixed(2);
  }

  function renderPreview(content) {
    if (!content) return;
    $("#previewDomain").textContent = model?.site?.domain || "client website";
    $("#previewEyebrow").textContent = content.eyebrow;
    $("#previewHeadline").textContent = content.headline;
    $("#previewBody").textContent = content.body;
    $("#previewCta").textContent = content.cta_label;
    $("#previewCta").href = safeUrl(content.cta_url) || "#";
    $("#previewImage").src = safeUrl(content.desktop_image_url);
    $("#previewImage").alt = content.alt_text || "";
    $("#previewImage").style.objectPosition = content.desktop_focal || "50% 50%";
    $("#previewMobileSource").srcset = safeUrl(content.mobile_image_url) || safeUrl(content.desktop_image_url);
    const opacity = Math.max(0, Math.min(.9, Number(content.overlay_opacity || 0)));
    $(".sf-live-overlay").style.background = `linear-gradient(90deg,rgba(5,21,33,${Math.min(1, .82 + opacity)}),rgba(5,21,33,${Math.min(.94, .58 + opacity)}) 47%,rgba(5,21,33,${opacity}) 82%)`;
  }

  function renderReport() {
    const report = model.report || {};
    $("#reportEvents").textContent = report.weather_events || 0;
    $("#reportActivations").textContent = report.activations || 0;
    $("#reportHours").textContent = number(report.hours_personalized, 1);
    $("#reportTransitions").textContent = report.transitions || 0;
    renderTimeline();
    renderEventRows();
    $("#exportCsv").href = `${base}/api/report.csv`;
  }

  function renderTimeline() {
    const rows = (model.history || []).slice(0, 8);
    $("#timeline").innerHTML = rows.length ? rows.map(row => `<div class="sf-timeline-item"><time>${escapeHtml(formatDate(row.recorded_at, false))}</time><span class="sf-timeline-marker"></span><div class="sf-timeline-copy"><strong>${escapeHtml(titleCase(row.trigger_key || "SmartForecast"))}</strong><span>${escapeHtml(titleCase(row.event_type))} · ${escapeHtml(phaseLabel(row.phase))}</span><p>${escapeHtml(row.reason || row.source)}</p></div></div>`).join("") : `<p class="sf-empty">No transitions recorded yet.</p>`;
  }

  function renderEventRows(query = "") {
    query = query.trim().toLowerCase();
    const rows = (model.history || []).filter(row => !query || JSON.stringify(row).toLowerCase().includes(query));
    $("#eventRows").innerHTML = rows.length ? rows.map(row => `<tr><td>${escapeHtml(formatDate(row.recorded_at))}</td><td>${escapeHtml(titleCase(row.trigger_key || "SmartForecast"))}</td><td><span class="sf-phase">${escapeHtml(phaseLabel(row.phase))}</span></td><td>${escapeHtml(titleCase(row.event_type))}${row.manual_override ? " · Manual" : ""}</td><td>${escapeHtml(row.source)}</td></tr>`).join("") : `<tr><td class="sf-empty" colspan="5">No matching history.</td></tr>`;
  }

  function populateOverride() {
    $("#overrideVariant").innerHTML = model.variants.map(variant => `<option value="${variant.id}">${escapeHtml(variant.name)} · ${escapeHtml(phaseLabel(variant.phase))}</option>`).join("");
    if (activeVariant) $("#overrideVariant").value = activeVariant.id;
  }

  async function load() {
    try {
      renderAll(await api("/api/bootstrap"));
    } catch (error) {
      $("#loadingState").innerHTML = `<p>SmartForecast could not load: ${escapeHtml(error.message)}</p>`;
      toast(error.message, true);
    }
  }

  $$(".sf-tab").forEach(tab => tab.addEventListener("click", () => go(tab.dataset.view)));
  $$("[data-go]").forEach(button => button.addEventListener("click", () => go(button.dataset.go)));
  $$("[data-install]").forEach(button => button.addEventListener("click", () => {
    installMode = button.dataset.install;
    $$("[data-install]").forEach(item => item.classList.toggle("is-active", item === button));
    renderEmbedCode();
  }));
  $$("[data-device]").forEach(button => button.addEventListener("click", () => {
    const mobile = button.dataset.device === "mobile";
    $("#previewBrowser").classList.toggle("is-mobile", mobile);
    $$("[data-device]").forEach(item => item.classList.toggle("is-active", item === button));
  }));

  $("#saveSetup").addEventListener("click", async () => {
    const body = formObject($("#setupForm"));
    body.check_interval_minutes = Number(body.check_interval_minutes);
    body.enabled = model.site.enabled;
    body.business_goals = $$("#businessGoals input:checked").map(input => input.value);
    body.branding = {
      ...model.site.branding,
      font: $("#brandFont").value,
      headline_color: $("#brandHeadline").value,
      button_color: $("#brandButton").value,
      border_radius: Number($("#brandRadius").value),
      mobile_headline_size: Number($("#brandMobileSize").value),
    };
    try { renderAll(await api("/api/setup", {method:"POST", body:JSON.stringify(body)})); toast("Website setup saved"); }
    catch (error) { toast(error.message, true); }
  });

  $("#copyEmbed").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText($("#embedCode code").textContent); toast("Embed code copied"); }
    catch { toast("Select the embed code and copy it manually", true); }
  });

  $("#pauseToggle").addEventListener("change", async event => {
    try { renderAll(await api("/api/pause", {method:"POST", body:JSON.stringify({paused:event.target.checked})})); toast(event.target.checked ? "SmartForecast paused" : "SmartForecast resumed"); }
    catch (error) { event.target.checked = !event.target.checked; toast(error.message, true); }
  });

  $("#refreshWeather").addEventListener("click", async () => {
    const button = $("#refreshWeather");
    button.disabled = true; button.textContent = "…";
    try { await api("/api/weather/refresh", {method:"POST", body:"{}"}); renderAll(await api("/api/bootstrap")); toast("Weather refreshed and evaluated"); }
    catch (error) { toast(error.message, true); }
    finally { button.disabled = false; button.textContent = "↻"; }
  });

  $("#contentForm").addEventListener("input", updateCrops);
  $("#saveContent").addEventListener("click", async () => {
    if (!activeVariant) return;
    const body = formObject($("#contentForm"));
    body.overlay_opacity = Number(body.overlay_opacity);
    try {
      const data = await api(`/api/content/${activeVariant.id}`, {method:"POST", body:JSON.stringify(body)});
      const index = model.variants.findIndex(item => item.id === activeVariant.id);
      model.variants[index] = data.content; activeVariant = data.content;
      renderVariants(); renderPreview(activeVariant); toast("Content and responsive crops saved");
    } catch (error) { toast(error.message, true); }
  });

  $("#simulatorForm").addEventListener("submit", async event => {
    event.preventDefault();
    const body = formObject(event.currentTarget);
    ["temperature","feels_like","forecast_high","forecast_low","rain_probability","snow_inches","wind_mph","humidity","hours_until_event"].forEach(key => body[key] = Number(body[key]));
    body.persist = $("#persistSimulation").checked;
    try {
      const result = await api("/api/simulate", {method:"POST", body:JSON.stringify(body)});
      $("#simWinner").textContent = result.winner?.name || "Default Website";
      $("#simPriority").textContent = result.winner ? `Priority ${result.winner.priority}` : "Priority 0";
      $("#simReason").textContent = result.reason;
      $("#simPhase").textContent = phaseLabel(result.phase);
      renderPreview(result.content);
      if (body.persist) { model = await api("/api/bootstrap"); renderDashboard(); renderReport(); }
      toast(body.persist ? `Simulation applied: ${titleCase(result.transition || "no transition")}` : "Simulation complete");
    } catch (error) { toast(error.message, true); }
  });

  $("#eventSearch").addEventListener("input", event => renderEventRows(event.target.value));
  $("#forceMessage").addEventListener("click", () => $("#overrideDialog").showModal());
  $("#overrideForm").addEventListener("submit", async event => {
    if (event.submitter?.value === "cancel") return;
    event.preventDefault();
    const body = formObject(event.currentTarget);
    body.content_variant_id = Number(body.content_variant_id);
    body.hours = Number(body.hours);
    const variant = model.variants.find(item => item.id === body.content_variant_id);
    body.trigger_key = variant?.trigger_key || "manual";
    body.phase = variant?.phase || "active_event";
    try { renderAll(await api("/api/override", {method:"POST", body:JSON.stringify(body)})); $("#overrideDialog").close(); toast("Manual message is now live and recorded"); }
    catch (error) { toast(error.message, true); }
  });

  load();
})();
