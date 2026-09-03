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
    publisher_rules: "Publisher rules", compliance: "Advertising rules",
    archetype_ready: "What this spot needs",
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
  //
  // Once one cut IS approved that stops being true, and the rest render
  // together from the "Render the other N" button below. Whether that button
  // appears is the server's answer (`can_batch`), never worked out here: the
  // route enforces the rule, and a second reading of it in this file is the
  // copy that drifts.
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

    // The rest, together — offered only where the server says the batch is
    // open, and naming the sizes it would send so a press is never a surprise.
    const rest = document.getElementById("render-rest-btn");
    if (!rest) return;
    // `remaining_formats` is already "asked for and not approved" — a size
    // that rendered and was never approved is still outstanding, because the
    // cut exists and nobody has said it is good.
    const left = state.remaining || [];
    rest.hidden = !state.canBatch || left.length < 2;
    if (!rest.hidden) {
      rest.textContent = `Render the other ${left.length} (${left.join(", ")})`;
    }
  }

  // What has already happened, per size. Read back from the server rather than
  // held in this tab: a render takes minutes and somebody will close the page.
  const state = { jobs: [], approved: new Set(), byFormat: {},
                  canBatch: false, remaining: [] };

  async function loadJobs() {
    let data;
    try {
      data = await CB.api(`/api/projects/${projectId}/render-jobs`);
    } catch (e) { return; }
    state.jobs = data.render_jobs || [];
    state.approved = new Set((data.approved_formats || []));
    state.canBatch = !!data.can_batch;
    state.remaining = data.remaining_formats || [];
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

  document.getElementById("render-btn").addEventListener("click", () => {
    if (!selectedFormat) return CB.toast("Pick a size first.", true);
    send([selectedFormat], document.getElementById("render-btn"));
  });

  const restBtn = document.getElementById("render-rest-btn");
  if (restBtn) {
    restBtn.addEventListener("click", () => {
      const left = state.remaining || [];
      if (left.length < 2) return;
      send(left, restBtn);
    });
  }

  // One send path for both buttons. Two would be two descriptions of what
  // pressing Render does, and only one of them would gain the next fix.
  async function send(formats, btn) {
    const wrap = document.getElementById("render-jobs");
    btn.disabled = true;
    const naming = formats.join(", ");

    // Pressing Render used to show nothing at all: the route 500'd on an
    // attribute the model does not have, CB.api could not parse the HTML that
    // came back, and a three-second toast said "Bad response from server".
    // Whatever happens now, this panel says what it was.
    wrap.innerHTML = '<div class="cb-card" style="padding:14px;">'
      + CB.working("video", `Sending ${naming} to the renderer\u2026`,
                   "Building the timeline from your scenes, narration and end card.")
      + "</div>";

    let data;
    try {
      data = await CB.api(`/api/projects/${projectId}/render`, {
        method: "POST",
        // Always the plural field. The route takes one or several and decides
        // for itself whether several are allowed, so this file does not have
        // to carry a second reading of that rule.
        body: { formats: formats },
      });
    } catch (e) {
      // QC refused it, or the renderer did, or nothing has been approved yet
      // and a batch is not open. Either way it belongs on the panel, not only
      // in a toast that has already faded.
      wrap.innerHTML = `<div class="cb-note bad"><strong>${naming} ${
          formats.length > 1 ? "were" : "was"} not sent</strong>`
        + `<p>${CB.escapeHtml(e.message || "The renderer refused it.")}</p></div>`;
      runQc();
      btn.disabled = false;
      return;
    }
    if (data.note) CB.toast(data.note, true);
    await loadJobs();
    (data.render_jobs || []).forEach((j) => pollJob(j.id));
    btn.disabled = false;
  }

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

  async function approve(job, btn, override) {
    btn.disabled = true;
    btn.textContent = "Filing…";
    let data;
    try {
      data = await CB.api(`/api/projects/${projectId}/render-jobs/${job.id}/approve`,
                          { method: "POST", body: { override: !!override } });
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Approve & file";
      // The client asked for changes. That is a decision to put in front of
      // somebody, not a failure to swallow into a three-second toast — and
      // it is overridable, because a rep who has settled it on the phone
      // must not be stuck behind a rule the client has already moved past.
      if (/asked for changes/i.test(e.message || "")) {
        const box = CB.el(`<div class="cb-note bad" style="margin-top:8px;">
          <strong>Not filed</strong><p>${CB.escapeHtml(e.message)}</p></div>`);
        const go = CB.el('<button class="cb-btn cb-btn-sm">File it anyway</button>');
        go.addEventListener("click", () => { box.remove(); approve(job, btn, true); });
        box.appendChild(go);
        btn.parentNode.parentNode.appendChild(box);
      }
      return;
    }
    await loadJobs();
    loadReviews();
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
    if (left.length > 1) {
      // The first approval is what opens the batch, so this is the moment to
      // say so — the button above has just appeared and nobody was watching it.
      html += `<p>Still to render for this spot: ${left.join(", ")}. `
            + "They come off the storyboard you have just approved, so they can "
            + "go together — Render the other " + left.length + " above.</p>";
    } else if (left.length) {
      html += `<p>Still to render for this spot: ${left.join(", ")}. `
            + "Pick it above.</p>";
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

  // ---------------------------------------------------------- the client
  //
  // A rendered cut is approved by a REP pressing Approve & file, and the
  // client sees it when somebody emails an MP4. So nothing recorded which
  // cut the client approved, who at the client approved it, or what they
  // asked for on the round before — which is fine right up until a client
  // says "we never signed off on that".
  const OUTCOME_TONE = { approved: "good", approved_with_changes: "", changes_required: "bad" };

  async function loadReviews() {
    const state = document.getElementById("review-state");
    if (!state) return;
    let data;
    try {
      data = await CB.api(`/api/projects/${projectId}/reviews`);
    } catch (e) {
      state.innerHTML = '<div class="cb-empty">The review rounds could not be read.</div>';
      return;
    }
    paintReview(data);
  }

  function paintReview(data) {
    const state = document.getElementById("review-state");
    const send = document.getElementById("review-send");
    const linkBox = document.getElementById("review-link");
    const answers = document.getElementById("review-answers");
    const roundLabel = document.getElementById("review-round");
    const current = data.current;

    state.innerHTML = "";
    linkBox.innerHTML = "";
    answers.innerHTML = "";
    roundLabel.textContent = current ? current.round_state.label : "";

    // Nothing rendered means nothing to review, and saying so here beats a
    // button that fails at the moment somebody presses it.
    if (!(data.cuts || []).length) {
      state.innerHTML = '<div class="cb-empty">Render a size first — a review link '
        + "with no video on it is a page the client cannot answer.</div>";
      send.style.display = "none";
      return;
    }

    if (!current) {
      state.innerHTML = '<p class="cb-hint" style="margin:0;">Nothing has been sent to '
        + "the client for this spot yet.</p>";
      send.style.display = "";
      document.getElementById("review-send-btn").textContent = "Create a review link";
      return;
    }

    // The live round: the link, whether it has been opened, and the answer.
    const v = current.verdict;
    const opened = current.opened_count
      ? `Opened ${current.opened_count} time${current.opened_count === 1 ? "" : "s"}.`
      : "Not opened yet.";
    state.innerHTML = "";
    state.appendChild(CB.el(`<p class="cb-hint" style="margin:0 0 8px;">`
      + `${CB.escapeHtml(current.round_state.label)} · ${CB.escapeHtml(opened)}</p>`));

    linkBox.style.display = "";
    const row = CB.el('<div class="cb-flex-between" style="gap:8px;align-items:center;"></div>');
    row.appendChild(CB.el(`<input type="text" readonly value="${CB.escapeHtml(current.url)}"
      style="flex:1;font-size:12.5px;">`));
    const copy = CB.el('<button class="cb-btn cb-btn-sm">Copy</button>');
    copy.addEventListener("click", () => copyLink(current.url, copy));
    row.appendChild(copy);
    linkBox.appendChild(row);

    // The answer, or the honest absence of one. "Not sent", "sent and
    // ignored" and "they said no" are three situations and only the last is
    // a rejection — so no answer draws gray rather than as a fourth kind of
    // bad, the note modules/ads_builder/spec.py makes about its own hub.
    if (!v.outcome) {
      answers.appendChild(CB.el('<div class="cb-note"><strong>No answer yet</strong>'
        + "<p>Nothing is blocked — this is what a link that has been sent looks like.</p></div>"));
    } else {
      const tone = OUTCOME_TONE[v.outcome] === undefined ? "" : OUTCOME_TONE[v.outcome];
      answers.appendChild(CB.el(`<div class="cb-note ${tone}">`
        + `<strong>${CB.escapeHtml(v.note)}</strong>`
        + (v.by ? `<p>${CB.escapeHtml(v.by)} answered.` : "<p>")
        + (v.conflicting
            ? ` ${v.answered} people answered and they did not agree — the most `
              + "restrictive answer is the one shown, and every reply is listed below."
            : "")
        + "</p></div>"));
    }

    (current.decisions || []).forEach((d) => {
      answers.appendChild(CB.el(`<div class="cb-result">`
        + `<strong>${CB.escapeHtml(d.reviewer_name || "Someone")}</strong>`
        + `<div class="cb-result-sub">${CB.escapeHtml(d.outcome.replace(/_/g, " "))}`
        + (d.note ? " — " + CB.escapeHtml(d.note) : "") + "</div></div>"));
    });

    (current.comments || []).forEach((c) => {
      answers.appendChild(CB.el(`<div class="cb-result">`
        + (c.timecode ? `<strong>${CB.escapeHtml(c.timecode)}</strong> ` : "")
        + CB.escapeHtml(c.text)
        + `<div class="cb-result-sub">${CB.escapeHtml(c.reviewer_name || "Someone")}`
        + (c.format ? " · on the " + CB.escapeHtml(c.format) : "") + "</div></div>"));
    });

    // Another round is offered whatever they said: a client who approved may
    // still get a re-cut for a reason nothing here knows about.
    send.style.display = "";
    const next = data.next_round;
    document.getElementById("review-send-btn").textContent =
      `Send ${next.label.toLowerCase()}`;
    if (next.over) {
      // Flagged, never refused. Stopping the rep here is what pushes the
      // whole conversation back into email, where none of this is recorded.
      send.appendChild(CB.el(`<div class="cb-note" style="margin-top:8px;">`
        + "<strong>More rounds than usual</strong>"
        + `<p>${CB.escapeHtml(next.note)}</p></div>`));
    }
  }

  // navigator.clipboard is not available on http, and refusing is allowed.
  // A button that reports a copy it never made is worse than one that asks.
  function copyLink(url, btn) {
    const done = () => { btn.textContent = "Copied"; setTimeout(() => (btn.textContent = "Copy"), 1600); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(done).catch(() => fallback());
      return;
    }
    fallback();
    function fallback() {
      const input = btn.parentNode.querySelector("input");
      input.focus();
      input.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      if (ok) done();
      else btn.textContent = "Press Ctrl-C";
    }
  }

  const sendBtn = document.getElementById("review-send-btn");
  if (sendBtn) {
    sendBtn.addEventListener("click", async () => {
      sendBtn.disabled = true;
      try {
        const res = await CB.api(`/api/projects/${projectId}/reviews`, {
          method: "POST",
          body: {
            message: document.getElementById("review-message").value.trim(),
            reviewer_name: (document.getElementById("review-name") || {}).value || "",
            reviewer_email: (document.getElementById("review-email") || {}).value || "",
          },
        });
        document.getElementById("review-message").value = "";
        // Three outcomes, said apart. "Sent" is a contact Suite confirmed;
        // "held" is a link that exists and did not reach Suite -- the rep
        // sends it by hand and the panel never claims otherwise.
        const d = (res && res.review && res.review.delivery) || {};
        if (d.state === "sent") {
          CB.toast("Review link created and filed in Smart 1 Suite for the review workflow to send.");
        } else if (d.state === "held") {
          CB.toast("Review link created, but it could not be filed in Suite — copy it and send it yourself. " + (d.note || ""), true);
        } else {
          CB.toast("Review link created — copy it and send it to the client.");
        }
        await loadReviews();
      } finally {
        sendBtn.disabled = false;
      }
    });
  }

  loadReviews();

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
