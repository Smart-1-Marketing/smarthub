(() => {
  const root = document.getElementById("preview-root");
  const projectId = root.dataset.projectId;

  /* Every key run_qc returns. `scene_assets` was missing from this map and a
     check absent from it is skipped silently by the loop below -- so the one
     check that catches an unfinished scene, the whole reason it was written,
     never appeared on the panel it was written for. The Blueprint step draws
     the same list; both are complete, and test_commercial_qc.py asserts it. */
  const QC_LABELS = {
    timing: "Timing", scene_assets: "Footage", voice_fits: "Narration length",
    cta: "CTA", brand: "Brand", resolution: "Resolution", aspect_ratio: "Aspect ratio",
    text_safe_area: "Text safe area", spelling: "Spelling", qr_code: "QR code",
    logo_persistence: "Persistent logo", youtube_hook: "YouTube hook",
    creative_spec: "Published spec", social_hook: "Feed hook", sound_off: "Sound off",
    abcd_pacing: "Pacing", abcd_brand_window: "Brand window",
    publisher_rules: "Publisher rules",
  };

  /* A recommendation and a refusal are not the same finding, and painting
     both red is how a panel of red teaches people to scroll past it. The
     severity comes off the server: this file and blueprint.js each kept an
     ADVISORY set by hand, which is two copies of a decision qc_service
     already had every fact to make, and the fastest way to have one panel
     draw a finding red while the other drew the same finding amber. */

  document.getElementById("run-qc-btn").addEventListener("click", runQc);

  async function runQc() {
    const list = document.getElementById("qc-list");
    list.innerHTML = '<span class="cb-spinner"></span>';
    const { qc_results } = await CB.api(`/api/projects/${projectId}/qc`, { method: "POST" });
    list.innerHTML = "";
    let blocking = 0;
    Object.entries(qc_results).forEach(([key, result]) => {
      if (key === "_all_passed" || !QC_LABELS[key]) return;
      const level = result.level || (result.passed ? "pass" : "fail");
      const tone = level === "pass" ? "pass" : level;
      const mark = level === "pass" ? "✓" : (level === "warn" ? "!" : "✕");
      if (level === "fail") blocking += 1;
      const item = CB.el(`<div class="cb-qc-item">
        <div class="cb-qc-icon ${tone}">${mark}</div>
        <div class="cb-qc-text"><strong>${QC_LABELS[key]}</strong>
        <span>${CB.escapeHtml(result.message)}</span></div>
      </div>`);
      list.appendChild(item);
    });
    if (qc_results._all_passed && !(qc_results._warnings || []).length) {
      CB.toast("All checks passed — ready to render.");
    } else if (!blocking) {
      CB.toast("Nothing blocking — the rest are recommendations.");
    }
  }

  // ------------------------------------------------------------ one size
  //
  // Single-select, not a set of tickboxes. Rendering three sizes at once
  // means the second and third come off a storyboard nobody has watched: a
  // note on the first applies to two cuts already paid for.
  let selectedFormat = null;

  document.getElementById("render-format-choices").addEventListener("click", (e) => {
    const choice = e.target.closest(".cb-choice");
    if (!choice || choice.classList.contains("is-approved")) return;
    document.querySelectorAll("#render-format-choices .cb-choice")
      .forEach((c) => c.classList.remove("selected"));
    choice.classList.add("selected");
    selectedFormat = choice.dataset.value;
    syncRenderButton();
  });

  function syncRenderButton() {
    const btn = document.getElementById("render-btn");
    const done = state.approved.has(selectedFormat);
    btn.disabled = !selectedFormat || done;
    btn.textContent = !selectedFormat ? "Pick a size"
      : (done ? `${selectedFormat} is approved` : `Render ${selectedFormat}`);
  }

  // What has already happened, per size. Read back from the server rather than
  // held in this tab: a render takes minutes and somebody will close the page.
  const state = { jobs: [], approved: new Set(), byFormat: {} };

  async function loadJobs() {
    let data;
    try {
      data = await CB.api(`/api/projects/${projectId}/render-jobs`);
    } catch (e) { return; }
    state.jobs = data.render_jobs || [];
    state.approved = new Set((data.approved_formats || []));
    state.byFormat = {};
    state.jobs.forEach((j) => { state.byFormat[j.format] = j; });
    paintFormatStates();
    renderJobList();
    // Keep watching anything the server still calls unfinished.
    state.jobs.filter((j) => j.status === "queued" || j.status === "rendering")
      .forEach((j) => pollJob(j.id));
  }

  function paintFormatStates() {
    document.querySelectorAll("#render-format-choices .cb-choice").forEach((cell) => {
      const fmt = cell.dataset.value;
      const job = state.byFormat[fmt];
      const slot = cell.querySelector(".cb-format-state");
      cell.classList.remove("is-approved", "is-rendered");
      if (state.approved.has(fmt)) {
        cell.classList.add("is-approved");
        slot.innerHTML = '<span class="cb-badge cb-badge-free">✓ approved</span>';
      } else if (job && job.status === "succeeded") {
        cell.classList.add("is-rendered");
        slot.innerHTML = '<span class="cb-badge cb-badge-owned">rendered</span>';
      } else if (job && (job.status === "queued" || job.status === "rendering")) {
        slot.innerHTML = '<span class="cb-badge cb-badge-mock">rendering…</span>';
      } else if (job && job.status === "failed") {
        slot.innerHTML = '<span class="cb-badge cb-badge-premium">failed</span>';
      } else {
        slot.innerHTML = "";
      }
    });
    syncRenderButton();
  }

  document.getElementById("render-btn").addEventListener("click", async () => {
    if (!selectedFormat) return CB.toast("Pick a size first.", true);
    const btn = document.getElementById("render-btn");
    const wrap = document.getElementById("render-jobs");
    btn.disabled = true;

    // Pressing Render used to show nothing at all: the route 500'd on an
    // attribute the model does not have, CB.api could not parse the HTML that
    // came back, and a three-second toast said "Bad response from server".
    // Whatever happens now, this panel says what it was.
    wrap.innerHTML = '<div class="cb-card" style="padding:14px;">'
      + CB.working("video", `Sending ${selectedFormat} to the renderer\u2026`,
                   "Building the timeline from your scenes, narration and end card.")
      + "</div>";

    let data;
    try {
      data = await CB.api(`/api/projects/${projectId}/render`, {
        method: "POST", body: { format: selectedFormat },
      });
    } catch (e) {
      // QC refused it, or the renderer did. Either way it belongs on the
      // panel, not only in a toast that has already faded.
      wrap.innerHTML = `<div class="cb-note bad"><strong>${selectedFormat} was not sent</strong>`
        + `<p>${CB.escapeHtml(e.message || "The renderer refused it.")}</p></div>`;
      runQc();
      btn.disabled = false;
      return;
    }
    if (data.note) CB.toast(data.note, true);
    await loadJobs();
    (data.render_jobs || []).forEach((j) => pollJob(j.id));
    btn.disabled = false;
  });

  function renderJobList() {
    const wrap = document.getElementById("render-jobs");
    wrap.innerHTML = "";
    if (!state.jobs.length) {
      wrap.innerHTML = '<div class="cb-empty">Nothing rendered yet.</div>';
      return;
    }
    state.jobs.slice().reverse().forEach((j) => wrap.appendChild(jobCard(j)));
  }

  function jobCard(job) {
    const approved = job.approval;
    const working = job.status === "queued" || job.status === "rendering";
    const card = CB.el(`<div class="cb-render-job" data-job-id="${job.id}"></div>`);

    card.appendChild(CB.el(`<div class="cb-flex-between">
      <span><strong>${job.format}</strong>
        <span class="cb-muted" style="font-size:12px;"> · ${CB.escapeHtml(job.status)}</span></span>
      <span class="cb-muted job-elapsed" style="font-size:12px;"></span>
    </div>`));

    if (working) {
      // A status word that never changes reads as a page that has stopped.
      card.appendChild(CB.el('<div class="cb-render-bar"><span></span></div>'));
      card.appendChild(CB.el('<p class="cb-hint" style="margin:6px 0 0;">'
        + "Rendering takes a few minutes. You can leave this page — it picks "
        + "back up where it left off.</p>"));
      startElapsed(card, job);
    } else if (job.status === "failed") {
      card.appendChild(CB.el(`<div class="cb-note bad" style="margin:8px 0 0;"><p>${
        CB.escapeHtml(job.error || "The renderer gave no reason.")}</p></div>`));
    } else if (job.status === "succeeded" && !job.output_url) {
      // Mock mode reports success and produces nothing. Drawing that as a
      // finished render with a missing download is the confident wrong answer.
      card.appendChild(CB.el('<div class="cb-note" style="margin:8px 0 0;">'
        + "<strong>No file was produced</strong><p>The job reported success but there "
        + "is no video behind it — that is what happens with no CREATOMATE_API_KEY "
        + "set. Nothing can be approved or filed from a mock render.</p></div>"));
    } else if (job.output_url) {
      card.appendChild(CB.el(`<video class="cb-render-video" controls preload="metadata"
        src="${job.output_url}"></video>`));
      const row = CB.el('<div class="cb-flex-between" style="margin-top:8px;"></div>');
      row.appendChild(CB.el(`<a class="cb-btn cb-btn-sm" href="${job.output_url}"
        target="_blank" rel="noopener">Download</a>`));
      if (approved) {
        row.appendChild(CB.el('<span class="cb-badge cb-badge-free">✓ approved</span>'));
      } else {
        const btn = CB.el('<button class="cb-btn cb-btn-primary cb-btn-sm">Approve &amp; file</button>');
        btn.addEventListener("click", () => approve(job, btn));
        row.appendChild(btn);
      }
      card.appendChild(row);
      if (approved) card.appendChild(filingReport(approved));
    }
    return card;
  }

  // "Filed" and "filed in one of two places" are different outcomes, and one
  // tick over both is how somebody learns not to trust the tick.
  function filingReport(approval) {
    const bits = [];
    bits.push(approval.stored_url
      ? "Copied into the client's library."
      : "Not copied into the client's library.");
    bits.push(approval.filed_to_client
      ? "Recorded on the client's record."
      : "Not recorded on the client's record.");
    const bad = !approval.stored_url || !approval.filed_to_client;
    return CB.el(`<div class="cb-note ${bad ? "" : "good"}" style="margin:8px 0 0;">`
      + `<strong>Approved${approval.approved_by ? " by " + CB.escapeHtml(approval.approved_by) : ""}</strong>`
      + `<p>${bits.join(" ")}${approval.filing_error
          ? " " + CB.escapeHtml(approval.filing_error) : ""}</p></div>`);
  }

  async function approve(job, btn) {
    btn.disabled = true;
    btn.textContent = "Filing…";
    let data;
    try {
      data = await CB.api(`/api/projects/${projectId}/render-jobs/${job.id}/approve`,
                          { method: "POST" });
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Approve & file";
      return;
    }
    await loadJobs();
    handOff(data);
  }

  // Approving the :30 is not the end of the job when three lengths were
  // started together — the next one is waiting at its Blueprint, and leaving
  // somebody on a finished Preview screen is how it goes unbuilt.
  function handOff(data) {
    const wrap = document.getElementById("render-jobs");
    const next = data.next;
    const left = data.remaining_formats || [];
    const box = CB.el('<div class="cb-note good" style="margin-top:12px;"></div>');
    let html = "<strong>Approved and filed.</strong>";
    if (left.length) {
      html += `<p>Still to render for this spot: ${left.join(", ")}. `
            + "Pick one above.</p>";
    }
    if (next) {
      html += `<p>Next spot in this campaign is the :${
        String(next.length_seconds).padStart(2, "0")}.</p>`;
    }
    box.innerHTML = html;
    if (next) {
      const go = CB.el(`<a class="cb-btn cb-btn-primary cb-btn-sm"
        href="${next.url}">Start the :${String(next.length_seconds).padStart(2, "0")} →</a>`);
      box.appendChild(go);
    }
    wrap.prepend(box);
  }

  function startElapsed(card, job) {
    const el = card.querySelector(".job-elapsed");
    const started = job.created_at ? Date.parse(job.created_at) : Date.now();
    function tick() {
      if (!el.isConnected) return;
      const secs = Math.max(0, Math.round((Date.now() - started) / 1000));
      el.textContent = `${Math.floor(secs / 60)}m ${String(secs % 60).padStart(2, "0")}s`;
      setTimeout(tick, 1000);
    }
    tick();
  }

  const polling = new Set();

  async function pollJob(jobId) {
    if (polling.has(jobId)) return;
    polling.add(jobId);
    (async function tick() {
      let data;
      try {
        data = await CB.api(`/api/projects/${projectId}/render-jobs/${jobId}/status`);
      } catch (e) { polling.delete(jobId); return; }
      const job = data.render_job;
      if (job.status === "queued" || job.status === "rendering") {
        setTimeout(tick, 4000);
        return;
      }
      polling.delete(jobId);
      await loadJobs();
      if (job.status === "failed") CB.toast(`${job.format} failed to render.`, true);
      else if (job.output_url) CB.toast(`${job.format} is ready to watch.`);
    })();
  }

  loadJobs();

  const varTypeSelect = document.getElementById("var-type");
  const varLabel = document.getElementById("var-input-label");
  const VAR_LABELS = {
    offer: "New offer text", location: "New target audience / location",
    weather: "New weather-triggered copy", cta: "New CTA line",
    voice: "New ElevenLabs voice ID", footage: "(no input needed — unlocks all footage)",
    duration: "New length in seconds (5/6/15/30/60)",
  };
  varTypeSelect.addEventListener("change", () => (varLabel.textContent = VAR_LABELS[varTypeSelect.value]));
  varLabel.textContent = VAR_LABELS[varTypeSelect.value];

  document.getElementById("create-var-btn").addEventListener("click", async () => {
    const type = varTypeSelect.value;
    const value = document.getElementById("var-input").value.trim();
    const changes = {};
    if (type === "offer") changes.what_advertising = value;
    else if (type === "location") changes.target_audience = value;
    else if (type === "weather") changes.what_advertising = value;
    else if (type === "cta") changes.headline = value;
    else if (type === "voice") changes.voice_id = value;
    else if (type === "duration") changes.length_seconds = parseInt(value, 10);

    const { project } = await CB.api(`/api/projects/${projectId}/variation`, {
      method: "POST", body: { variation_type: type, changes },
    });
    CB.toast("Variation created.");
    location.href = `${CB.API_ROOT}/project/${project.id}/storyboard`;
  });
})();
