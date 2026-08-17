(() => {
  const root = document.getElementById("preview-root");
  const projectId = root.dataset.projectId;
  let selectedFormats = new Set([...document.querySelectorAll("#render-format-choices .selected")].map((c) => c.dataset.value));

  const QC_LABELS = {
    timing: "Timing", voice_fits: "Voice", cta: "CTA", brand: "Brand",
    resolution: "Resolution", aspect_ratio: "Aspect ratio",
    text_safe_area: "Text safe area", spelling: "Spelling",
    qr_code: "QR code", logo_persistence: "Persistent logo", youtube_hook: "YouTube hook",
  };

  document.getElementById("run-qc-btn").addEventListener("click", runQc);

  async function runQc() {
    const list = document.getElementById("qc-list");
    list.innerHTML = '<span class="cb-spinner"></span>';
    const { qc_results } = await CB.api(`/api/projects/${projectId}/qc`, { method: "POST" });
    list.innerHTML = "";
    Object.entries(qc_results).forEach(([key, result]) => {
      if (key === "_all_passed" || !QC_LABELS[key]) return;
      const item = CB.el(`<div class="cb-qc-item">
        <div class="cb-qc-icon ${result.passed ? "pass" : "fail"}">${result.passed ? "✓" : "!"}</div>
        <div class="cb-qc-text"><strong>${QC_LABELS[key]}</strong><span>${result.message}</span></div>
      </div>`);
      list.appendChild(item);
    });
    if (qc_results._all_passed) CB.toast("All checks passed — ready to render.");
  }

  document.getElementById("render-format-choices").addEventListener("click", (e) => {
    const choice = e.target.closest(".cb-choice");
    if (!choice) return;
    const v = choice.dataset.value;
    if (selectedFormats.has(v)) { selectedFormats.delete(v); choice.classList.remove("selected"); }
    else { selectedFormats.add(v); choice.classList.add("selected"); }
  });

  document.getElementById("render-btn").addEventListener("click", async () => {
    if (!selectedFormats.size) return CB.toast("Choose at least one format.", true);
    const btn = document.getElementById("render-btn");
    btn.disabled = true;
    try {
      const { render_jobs, live } = await CB.api(`/api/projects/${projectId}/render`, {
        method: "POST", body: { formats: [...selectedFormats] },
      });
      renderJobs(render_jobs);
      if (!live) CB.toast("Rendering in mock mode — no CREATOMATE_API_KEY set.");
      render_jobs.forEach((j) => pollJob(j.id));
    } catch (e) {
      // QC failure or other error already toasted by CB.api; refresh QC panel
      runQc();
    } finally {
      btn.disabled = false;
    }
  });

  function renderJobs(jobs) {
    const wrap = document.getElementById("render-jobs");
    wrap.innerHTML = "";
    jobs.forEach((j) => {
      const row = CB.el(`<div class="cb-flex-between" data-job-id="${j.id}" style="padding:8px 0;border-bottom:1px solid var(--cb-border);">
        <span><strong>${j.format}</strong></span>
        <span class="cb-status-pill job-status">${j.status}</span>
      </div>`);
      wrap.appendChild(row);
    });
  }

  async function pollJob(jobId) {
    const { render_job } = await CB.api(`/api/projects/${projectId}/render-jobs/${jobId}/status`);
    const row = document.querySelector(`#render-jobs [data-job-id="${jobId}"] .job-status`);
    if (row) row.textContent = render_job.status;
    if (render_job.status === "queued" || render_job.status === "rendering") {
      setTimeout(() => pollJob(jobId), 4000);
    } else if (render_job.status === "succeeded") {
      const rowEl = document.querySelector(`#render-jobs [data-job-id="${jobId}"]`);
      if (rowEl && render_job.output_url) {
        rowEl.appendChild(CB.el(`<a class="cb-btn cb-btn-sm" href="${render_job.output_url}" target="_blank" rel="noopener">Download</a>`));
      }
    }
  }

  const varTypeSelect = document.getElementById("var-type");
  const varLabel = document.getElementById("var-input-label");
  const VAR_LABELS = {
    offer: "New offer text", location: "New target audience / location",
    weather: "New weather-triggered copy", cta: "New CTA line",
    voice: "New ElevenLabs voice ID", footage: "(no input needed — unlocks all footage)",
    duration: "New length in seconds (5/15/30/60)",
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
