/* The Vox explainer's beat list.

   Two rules shape this file. Nothing renders from here — writing the beats
   and rendering them are separate presses, because a render is minutes of
   headless Chrome and this list is the only thing anybody can correct before
   it. And the server is what decides whether a list is usable: the duration
   verdict, the treatments and the beat bounds all come back on every
   response rather than being recomputed here, or the page and the route come
   to disagree about whether a spot can be rendered. */
(() => {
  const root = document.getElementById("vox-root");
  if (!root) return;
  const projectId = root.dataset.projectId;

  function data(id) {
    const node = document.getElementById(id);
    if (!node) return [];
    try { return JSON.parse(node.textContent) || []; } catch (e) { return []; }
  }
  const TREATMENTS = data("vox-treatments-data");
  const KINDS = data("vox-kinds-data");

  const kindSel = document.getElementById("vox-kind");
  const kindHint = document.getElementById("vox-kind-hint");
  const textField = document.getElementById("vox-text-field");
  const linkField = document.getElementById("vox-link-field");
  const list = document.getElementById("vox-beats");
  const durationNote = document.getElementById("vox-duration");

  /* The model's own beats, held here between edits. Read back out of the
     inputs before any redraw -- a container of live inputs rebuilt with
     innerHTML loses whatever was half-typed in it, which is the trap the SEO
     client page's alt-text and FAQ editors each had. */
  let beats = [];

  function syncKind() {
    const kind = kindSel.value;
    const match = KINDS.find((k) => k.id === kind);
    kindHint.textContent = match ? match.hint : "";
    linkField.style.display = kind === "link" ? "" : "none";
    textField.style.display = kind === "link" ? "none" : "";
  }
  kindSel.addEventListener("change", syncKind);
  syncKind();

  function harvest() {
    /* Read the open editors back into the model before anything redraws.
       Only fields somebody actually typed in, which is why each carries the
       value it was drawn with: comparing against the model would treat a
       whole freshly-written list as edited. */
    list.querySelectorAll("[data-beat-index]").forEach((row) => {
      const i = parseInt(row.dataset.beatIndex, 10);
      if (!beats[i]) return;
      const head = row.querySelector(".beat-headline");
      const sup = row.querySelector(".beat-support");
      const tre = row.querySelector(".beat-treatment");
      const sec = row.querySelector(".beat-seconds");
      const src = row.querySelector(".beat-source");
      if (head) beats[i].headline = head.value;
      if (sup) beats[i].support = sup.value;
      if (tre) beats[i].treatment = tre.value;
      if (sec) beats[i].seconds = parseFloat(sec.value) || 0;
      if (src) beats[i].source = src.value;
    });
  }

  function render() {
    list.innerHTML = "";
    if (!beats.length) {
      list.appendChild(CB.el('<div class="cb-empty">No beats yet.</div>'));
      return;
    }
    beats.forEach((b, i) => {
      const row = CB.el(`<div class="cb-card" data-beat-index="${i}"
        style="margin-bottom:8px;padding:10px;">
        <div class="cb-flex-between" style="margin-bottom:6px;">
          <strong>Beat ${i + 1}</strong>
          <button class="cb-btn cb-btn-sm cb-btn-danger beat-delete">Remove</button>
        </div>
        <div class="cb-field"><label class="cb-label">Headline</label>
          <input type="text" class="beat-headline"></div>
        <div class="cb-field"><label class="cb-label">Support</label>
          <input type="text" class="beat-support"></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <div class="cb-field"><label class="cb-label">Treatment</label>
            <select class="beat-treatment"></select></div>
          <div class="cb-field"><label class="cb-label">Seconds</label>
            <input type="number" step="0.5" min="0" class="beat-seconds" style="width:90px;"></div>
          <div class="cb-field" style="flex:1;"><label class="cb-label">Source</label>
            <input type="text" class="beat-source" placeholder="Required for a quote"></div>
        </div>
      </div>`);
      row.querySelector(".beat-headline").value = b.headline || "";
      row.querySelector(".beat-support").value = b.support || "";
      row.querySelector(".beat-seconds").value = b.seconds || 0;
      row.querySelector(".beat-source").value = b.source || "";
      const sel = row.querySelector(".beat-treatment");
      TREATMENTS.forEach((t) => {
        const opt = document.createElement("option");
        opt.value = t.id;
        opt.textContent = t.label;
        opt.title = t.hint;
        sel.appendChild(opt);
      });
      sel.value = b.treatment || (TREATMENTS[0] || {}).id || "";
      row.querySelector(".beat-delete").addEventListener("click", () => {
        harvest();
        beats.splice(i, 1);
        render();
      });
      list.appendChild(row);
    });
  }

  /* The verdict is the server's. Recomputing the window here would be a
     second answer to "can this be rendered", and the two disagree the day
     either is edited. */
  function paintState(state) {
    if (!state) return;
    beats = state.beats || [];
    const d = state.duration || {};
    durationNote.textContent = d.message || "";
    render();
  }

  function dropNote(target, dropped, note) {
    const box = document.getElementById(target);
    box.innerHTML = "";
    if (note) box.appendChild(CB.el(`<p class="cb-hint">${CB.escapeHtml(note)}</p>`));
    /* What was thrown away and why. A list that quietly gets shorter is an
       explainer missing exactly the beat somebody wanted made. */
    (dropped || []).forEach((d) => {
      box.appendChild(CB.el(
        `<p class="cb-hint warn">Dropped: ${CB.escapeHtml(d.reason || "")}</p>`));
    });
  }

  document.getElementById("vox-generate").addEventListener("click", async (e) => {
    /* CB.working() into the note box rather than S1Think.attach() onto the
       button: attach() APPENDS its status box into whatever it is given, so
       on a <button> the mark lands inside the label. The module's own panel
       is the shape every other wait in this tool draws. */
    const btn = e.target;
    const note = document.getElementById("vox-generate-note");
    note.innerHTML = CB.working("concepts", "Writing the beats\u2026",
      "The material is read and turned into an outline. Nothing renders yet \u2014 "
      + "you read the beats and press Render on the next step.");
    btn.disabled = true;
    try {
      const body = {
        source_kind: kindSel.value,
        source_text: document.getElementById("vox-text").value,
        link: document.getElementById("vox-link").value,
      };
      const state = await CB.api(`/api/projects/${projectId}/vox/beats`,
                                 { method: "POST", body });
      paintState(state);
      dropNote("vox-generate-note", state.dropped, state.note);
    } catch (err) {
      note.innerHTML = "";      // CB.api has already surfaced the reason
    } finally {
      btn.disabled = false;
    }
  });

  document.getElementById("vox-add").addEventListener("click", () => {
    harvest();
    beats.push({ headline: "", support: "", treatment: (TREATMENTS[0] || {}).id || "",
                 seconds: 0, source: "", image_query: "" });
    render();
  });

  document.getElementById("vox-save").addEventListener("click", async (e) => {
    harvest();
    const btn = e.target;
    btn.disabled = true;
    try {
      const state = await CB.api(`/api/projects/${projectId}/vox/beats`,
                                 { method: "PUT", body: { beats } });
      paintState(state);
      dropNote("vox-save-note", state.dropped, "");
      CB.toast("Beats saved.");
    } catch (err) {
      /* CB.api has already surfaced the reason. */
    } finally {
      btn.disabled = false;
    }
  });

  (async function load() {
    try {
      paintState(await CB.api(`/api/projects/${projectId}/vox`));
    } catch (e) { /* the empty state already says there is nothing */ }
  })();
})();
