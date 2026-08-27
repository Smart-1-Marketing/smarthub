/* The Blueprint step: the scenes, their footage and their narration.
   Voice, music and the CTA were on this same page and are their own steps now
   -- see routes/pages.py for why. What is left here is one job. */
(() => {
  const wrap = document.getElementById("scenes-wrap");
  const projectId = wrap.dataset.projectId;
  const clientId = wrap.dataset.clientId;
  const clientSlug = wrap.dataset.clientSlug;
  const lengthSeconds = parseInt(wrap.dataset.length, 10) || 0;
  const tpl = document.getElementById("scene-card-tpl");
  let dragSourceId = null;

  /* The voice is cast on the NEXT step, so this page has no picker of its own
     -- and the spokesperson button still needs a voice id, because HeyGen
     speaks the narration into the clip. Left null it comes back in HeyGen's
     default: not the voice the spot was cast in, baked into a clip that has
     already been paid for, and nobody notices until the render.

     So it reads the voice cast against the client. Where none has been cast
     yet, the picker says so rather than generating a presenter in a voice
     nobody chose. */
  let selectedVoiceId = null;

  async function loadCastVoice() {
    try {
      const { client } = await CB.api(`/api/clients/${clientId}`);
      selectedVoiceId = client.preferred_voiceover_id || null;
    } catch (e) { /* the picker says so below */ }
  }

  const ASSET_TYPE_LABEL = {
    stock: "Stock", ai_generated: "AI generated", spokesperson: "Spokesperson",
    upload: "Uploaded", client_asset: "Client asset", cta: "CTA card",
  };

  // A HeyGen clip takes minutes, so a scene can sit "generating" across a page
  // load. Polling is keyed by scene id and restarted from renderScenes(), so
  // re-opening the storyboard picks up a job that was started hours ago
  // instead of leaving a presenter scene permanently empty.
  const POLL_MS = 5000;
  const polling = new Set();

  function heygenJob(scene) {
    return (scene.asset_meta || {}).heygen_job || null;
  }

  function runwayPending(scene) {
    const job = (scene.asset_meta || {}).runway_job;
    if (!job) return false;
    if ((scene.asset_meta || {}).runway_url) return false;
    return job.status === "processing" || job.status === "pending";
  }

  async function pollVideoStatus(sceneId) {
    const res = await fetch(
      `${CB.API_ROOT}/api/projects/${projectId}/scenes/${sceneId}/generate-video/status`);
    try { return await res.json(); } catch (e) { return { status: "processing" }; }
  }

  // Same shape as watchSpokesperson: a Runway clip takes minutes, so the job
  // has to survive a page load rather than living in this tab.
  function watchVideo(sceneId) {
    const key = "v" + sceneId;
    if (polling.has(key)) return;
    polling.add(key);
    (async function tick() {
      const data = await pollVideoStatus(sceneId);
      if (data.status === "processing" || data.status === "pending") {
        setTimeout(tick, POLL_MS);
        return;
      }
      polling.delete(key);
      if (data.status === "failed") {
        CB.toast(data.error || "The AI video failed to generate.", true);
      } else if (data.mock) {
        CB.toast("Mock mode — no video was produced (no Runway key set).", true);
      } else if (data.attached) {
        CB.toast("AI video attached.");
      }
      loadScenes();
    })();
  }

  // Runway animates a starting frame, so the scene needs an image first. The
  // button says which step is missing rather than sending a request that the
  // server will only refuse.
  async function generateVideo(card, scene) {
    if (!scene.asset_url) {
      /* The button is disabled in this state, so this is the belt to that
         brace -- a scene whose frame was cleared between render and click. */
      return CB.toast("This scene needs a frame first. Press “Make a frame”, or "
                      + "pick footage, then animate it.", true);
    }
    const picker = card.querySelector(".asset-picker");
    picker.innerHTML = '<div class="cb-card" style="margin-top:10px;padding:12px;">'
      + CB.working("video", "Sending this frame to Runway…",
                   "Runway animates the still you made — it does not invent one. "
                   + "A clip takes a few minutes; you can leave this page and come back.")
      + "</div>";
    try {
      await CB.api(`/api/projects/${projectId}/scenes/${scene.id}/generate-video`,
                   { method: "POST", body: {} });
    } catch (e) {
      picker.innerHTML = "";
      return;                            // CB.api has already surfaced the reason
    }
    picker.innerHTML = "";
    CB.toast("AI video generating — this takes a few minutes.");
    await loadScenes();                  // re-render starts the poll
  }

  function spokespersonPending(scene) {
    const job = heygenJob(scene);
    if (!job) return false;
    if ((scene.asset_meta || {}).spokesperson_url) return false;
    return job.status === "processing" || job.status === "pending";
  }

  // Deliberately not CB.api: that toasts and throws on ok:false, which would
  // fire an error toast on every tick of a job that is simply still running.
  async function pollStatus(sceneId) {
    const res = await fetch(
      `${CB.API_ROOT}/api/projects/${projectId}/scenes/${sceneId}/spokesperson/status`);
    try { return await res.json(); } catch (e) { return { status: "processing" }; }
  }

  function watchSpokesperson(sceneId) {
    if (polling.has(sceneId)) return;
    polling.add(sceneId);
    (async function tick() {
      const data = await pollStatus(sceneId);
      if (data.status === "processing" || data.status === "pending") {
        setTimeout(tick, POLL_MS);
        return;
      }
      polling.delete(sceneId);
      if (data.status === "failed") {
        CB.toast(data.error || "The presenter clip failed to generate.", true);
      } else if (data.mock) {
        CB.toast("Mock mode — no presenter video was produced (no HeyGen key set).", true);
      } else if (data.attached) {
        CB.toast("Presenter clip attached.");
      }
      loadScenes();
    })();
  }

  // ---------------------------------------------------------------- scenes
  async function loadScenes() {
    const { project } = await CB.api(`/api/projects/${projectId}`);
    renderWordCount(project.script);
    renderScenes(project.scenes);
    loadBudget();
  }

  function renderWordCount(script) {
    const el = document.getElementById("wc-summary");
    /* `!script.word_count === undefined` parses as `(!script.word_count) === undefined`,
       which is always false -- so a project with no script fell straight through
       to reading `script.target_range` off null. This is the guard it was meant
       to be. */
    if (!script || script.word_count === undefined) { el.textContent = "No script yet."; return; }
    const [lo, hi] = script.target_range || [0, 0];
    const ok = script.within_target;
    el.innerHTML = `Narration: <strong class="${ok ? "" : "cb-word-count warn"}">${script.word_count} words</strong> (target ${lo}-${hi})`;
  }

  function renderScenes(scenes) {
    wrap.innerHTML = "";
    scenes.forEach((scene, idx) => wrap.appendChild(buildSceneCard(scene, idx, scenes.length)));
  }

  function buildSceneCard(scene, idx, total) {
    const node = tpl.content.firstElementChild.cloneNode(true);
    node.dataset.sceneId = scene.id;
    node.draggable = true;
    if (scene.is_cta) node.classList.add("cta-scene");

    node.querySelector(".scene-num").textContent = `${idx + 1} of ${total}${scene.is_cta ? " — CTA" : ""}`;
    node.querySelector(".cb-scene-time").textContent = `${CB.fmtTime(scene.start)} – ${CB.fmtTime(scene.end)}`;
    const job = heygenJob(scene);
    const pending = spokespersonPending(scene);
    const presenterUrl = (scene.asset_meta || {}).spokesperson_url;
    const badge = node.querySelector(".asset-type-badge");
    if (pending) {
      // Not "No asset yet" — a job IS running, and a scene that reads as empty
      // while HeyGen is working invites a rep to generate a second clip.
      badge.textContent = "Presenter generating…";
    } else if (job && job.status === "failed") {
      badge.textContent = "Presenter failed";
    } else {
      badge.textContent = scene.asset_url
        ? (ASSET_TYPE_LABEL[scene.asset_type] || "Asset set") : "No asset yet";
    }
    const thumb = node.querySelector(".cb-scene-thumb");
    if (scene.asset_thumb_url || scene.asset_url) {
      thumb.style.backgroundImage = `url('${scene.asset_thumb_url || scene.asset_url}')`;
      thumb.textContent = "";
    } else if (presenterUrl) {
      // A presenter clip has no poster frame, so the card says what is there
      // rather than falling through to "No footage selected" on a scene that
      // does in fact have video on it.
      thumb.textContent = "Presenter clip";
    } else {
      thumb.textContent = scene.is_cta ? "CTA end card" : "No footage selected";
    }

    const note = node.querySelector(".spokesperson-note");
    if (pending) {
      note.innerHTML = '<span class="cb-spinner"></span> Presenter clip is rendering at HeyGen. '
        + 'This takes a few minutes — you can leave this page and come back.';
    } else if (job && job.status === "failed") {
      note.textContent = `Presenter clip failed: ${job.error || "HeyGen reported no reason."}`;
      note.classList.add("cb-word-count", "warn");
    } else if (presenterUrl && (scene.asset_meta || {}).spokesperson_over_footage) {
      note.textContent = "Presenter is keyed over this scene's footage.";
    } else if (presenterUrl && (scene.asset_meta || {}).spokesperson_mirrored === false) {
      // A HeyGen URL is signed and expires. Saying so beats a scene that
      // plays today and 404s next week with nothing to explain it.
      note.textContent = "Presenter clip is linked from HeyGen, not copied into the "
        + "client library — it will stop working when the link expires.";
      note.classList.add("cb-word-count", "warn");
    } else {
      note.textContent = "";
    }
    if (pending) watchSpokesperson(scene.id);

    const vjob = (scene.asset_meta || {}).runway_job;
    if (runwayPending(scene)) {
      badge.textContent = "Video generating…";
      note.innerHTML = '<span class="cb-spinner"></span> Runway is animating this frame. '
        + 'A few minutes — you can leave this page and come back.';
      watchVideo(scene.id);
    } else if (vjob && vjob.status === "failed" && !(scene.asset_meta || {}).runway_url) {
      badge.textContent = "Video failed";
      note.textContent = `AI video failed: ${vjob.error || "Runway reported no reason."}`;
      note.classList.add("cb-word-count", "warn");
    } else if ((scene.asset_meta || {}).runway_mirrored === false) {
      note.textContent = "AI video is linked from Runway, not copied into the client "
        + "library — it will stop working when the link expires.";
      note.classList.add("cb-word-count", "warn");
    }

    node.querySelector(".visual-input").value = scene.visual_description || "";
    node.querySelector(".narration-input").value = scene.narration || "";
    node.querySelector(".duration-input").value = (scene.end - scene.start).toFixed(1);

    node.querySelector(".visual-input").addEventListener("change", (e) =>
      updateScene(scene.id, { visual_description: e.target.value }));
    node.querySelector(".narration-input").addEventListener("change", (e) =>
      updateScene(scene.id, { narration: e.target.value }).then(loadScenes));
    node.querySelector(".duration-input").addEventListener("change", (e) =>
      updateScene(scene.id, { duration: parseFloat(e.target.value) }).then(loadScenes));

    /* "Generate AI" and "Generate Video" sat side by side as peers and read
       as two ways of doing the same thing. They are two halves of one: Runway
       animates a starting frame and has no usable text-only path, so the
       second cannot run before the first. Numbered, paired, and the second is
       disabled until the scene has a frame -- a button that explains itself
       only after being pressed has already wasted the press. */
    const videoBtn = node.querySelector(".generate-video-btn");
    const pairNote = node.querySelector(".ai-pair-note");
    if (!scene.asset_url) {
      videoBtn.disabled = true;
      pairNote.textContent = "Make or pick a frame first \u2014 step 2 animates what step 1 makes.";
    } else if ((scene.asset_meta || {}).media === "video") {
      pairNote.textContent = "This scene is already video.";
    } else {
      pairNote.textContent = "Ready to animate this scene's frame.";
    }

    node.querySelector(".find-stock-btn").addEventListener("click", () => openStockPicker(node, scene));
    node.querySelector(".generate-ai-btn").addEventListener("click", () => openAiPicker(node, scene));
    videoBtn.addEventListener("click", () => generateVideo(node, scene));
    node.querySelector(".more-narration-btn").addEventListener("click", (e) =>
      expandNarration(scene.order_index, e.target));
    node.querySelector(".spokesperson-btn").addEventListener("click", () => openSpokespersonPicker(node, scene));
    node.querySelector(".upload-btn").addEventListener("click", () => openUploadPicker(node, scene));
    node.querySelector(".client-asset-btn").addEventListener("click", () => openClientAssetPicker(node, scene));

    node.querySelector(".regen-btn").addEventListener("click", async (e) => {
      e.target.disabled = true;
      await CB.api(`/api/projects/${projectId}/scenes/${scene.id}/regenerate`, { method: "POST" });
      loadScenes();
    });
    node.querySelector(".duplicate-btn").addEventListener("click", async () => {
      await CB.api(`/api/projects/${projectId}/scenes/${scene.id}/duplicate`, { method: "POST" });
      loadScenes();
    });
    node.querySelector(".delete-btn").addEventListener("click", async () => {
      if (scene.is_cta) return CB.toast("The CTA scene can't be deleted.", true);
      if (!confirm("Delete this scene?")) return;
      await CB.api(`/api/projects/${projectId}/scenes/${scene.id}`, { method: "DELETE" });
      loadScenes();
    });

    // drag reorder
    node.addEventListener("dragstart", () => { dragSourceId = scene.id; node.style.opacity = ".4"; });
    node.addEventListener("dragend", () => { node.style.opacity = "1"; });
    node.addEventListener("dragover", (e) => e.preventDefault());
    node.addEventListener("drop", async (e) => {
      e.preventDefault();
      if (dragSourceId === null || dragSourceId === scene.id) return;
      const ids = [...wrap.children].map((c) => parseInt(c.dataset.sceneId, 10));
      const from = ids.indexOf(dragSourceId), to = ids.indexOf(scene.id);
      ids.splice(to, 0, ids.splice(from, 1)[0]);
      await CB.api(`/api/projects/${projectId}/scenes/reorder`, { method: "POST", body: { order: ids } });
      loadScenes();
    });

    return node;
  }

  function updateScene(sceneId, body) {
    return CB.api(`/api/projects/${projectId}/scenes/${sceneId}`, { method: "PUT", body });
  }

  function closeExistingPickers() {
    document.querySelectorAll(".asset-picker").forEach((p) => (p.innerHTML = ""));
  }

  // ------------------------------------------------------------ Find Stock
  function openStockPicker(card, scene) {
    closeExistingPickers();
    const picker = card.querySelector(".asset-picker");
    picker.innerHTML = "";
    const box = CB.el(`
      <div class="cb-card" style="margin-top:10px;padding:12px;">
        <div style="display:flex;gap:8px;margin-bottom:8px;">
          <input type="search" placeholder="Search stock video…" class="stock-q" style="flex:1;">
          <button class="cb-btn cb-btn-sm stock-go">Search</button>
        </div>
        <div class="stock-results cb-choice-grid"></div>
      </div>`);
    picker.appendChild(box);
    const input = box.querySelector(".stock-q");
    input.value = scene.visual_description || "";
    const results = box.querySelector(".stock-results");

    async function runSearch() {
      const q = input.value.trim();
      if (!q) return;
      results.innerHTML = '<span class="cb-spinner"></span>';
      const data = await CB.api(`/api/stock/search?q=${encodeURIComponent(q)}&expand=true`);
      const items = data.results || [];
      results.innerHTML = "";
      renderOwnedNote(box, data);
      items.forEach((item) => {
        /* OWNED footage was drawn with the FREE badge, so a clip we already
           hold looked exactly like a Pexels result. routes/stock.py ranks it
           first precisely because it costs nothing and needs no license check,
           and a badge that does not say so throws that away. */
        const tier = item.tier === "PREMIUM" ? "cb-badge-premium"
                   : (item.tier === "OWNED" ? "cb-badge-owned" : "cb-badge-free");
        const cell = CB.el(`
          <div class="cb-choice" style="padding:6px;">
            <div class="cb-scene-thumb" style="aspect-ratio:16/9;background-image:url('${item.thumbnail}')"></div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px;">
              <span class="cb-badge ${tier}">${item.tier}</span>
              <span class="cb-muted" style="font-size:10.5px;">${item.author || ""}</span>
            </div>
          </div>`);
        cell.addEventListener("click", async () => {
          await updateScene(scene.id, {
            asset_type: "stock", asset_source: item.provider,
            asset_url: item.preview_url || item.full_url || item.thumbnail,
            asset_thumb_url: item.thumbnail,
            asset_meta: { width: item.width, height: item.height, author: item.author, source_url: item.source_url },
          });
          picker.innerHTML = "";
          loadScenes();
        });
        results.appendChild(cell);
      });
      if (!items.length) results.innerHTML = '<div class="cb-empty">No results.</div>';
    }
    box.querySelector(".stock-go").addEventListener("click", runSearch);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") runSearch(); });
    if (input.value) runSearch();
  }

  // ---------------------------------------------------- 1 · Make a frame
  //
  // This is the button that "finally gave me two options but it failed".
  // gpt-image-1 returns b64_json and never a url, and the service read
  // `resp.data[0].url` unconditionally -- so both options came back with no
  // image on them, the picker drew Option A and Option B exactly as it would
  // for a success, and clicking either said "This option failed to generate"
  // with nothing anywhere saying why. The service is fixed; this half stops a
  // dead option from looking like a live one.
  async function openAiPicker(card, scene) {
    closeExistingPickers();
    const picker = card.querySelector(".asset-picker");
    picker.innerHTML = '<div class="cb-card" style="margin-top:10px;padding:12px;">'
      + CB.working("concepts", "Drawing two options\u2026",
                   "A still frame for this scene. Pick one, then press "
                   + "\u201cAnimate it\u201d to turn it into video.")
      + "</div>";

    let payload;
    try {
      payload = await CB.api(`/api/projects/${projectId}/scenes/${scene.id}/generate-ai`,
                             { method: "POST" });
    } catch (e) {
      picker.innerHTML = "";
      return;                              // CB.api has already surfaced the reason
    }
    const options = payload.options || [];
    const live = payload.live;

    const box = CB.el('<div class="cb-card" style="margin-top:10px;padding:12px;"></div>');
    if (!live) {
      box.appendChild(CB.el('<p class="cb-hint">Mock mode \u2014 no OPENAI_API_KEY is set, '
        + "so these are placeholders rather than generated frames.</p>"));
    }

    const usable = options.filter((o) => o && o.url);
    if (!usable.length) {
      // Every option failed. The reason is the provider's own and it is the
      // only thing worth showing -- an empty grid of two dead tiles is what
      // this looked like before.
      const why = options.map((o) => o && o.error).filter(Boolean);
      box.appendChild(CB.el('<div class="cb-note bad"><strong>No frames came back</strong><p>'
        + CB.escapeHtml(why.length ? why.join(" \u00b7 ")
                                   : "OpenAI returned no image and gave no reason.")
        + "</p></div>"));
      picker.innerHTML = "";
      picker.appendChild(box);
      return;
    }

    const grid = CB.el('<div class="cb-choice-grid"></div>');
    usable.forEach((opt, i) => {
      const cell = CB.el(`<div class="cb-choice" style="padding:6px;">
        <div class="cb-scene-thumb" style="aspect-ratio:16/9;background-image:url('${opt.url}')"></div>
        <div class="cb-choice-title" style="margin-top:4px;">Option ${String.fromCharCode(65 + i)}</div></div>`);
      cell.addEventListener("click", async () => {
        await CB.api(`/api/projects/${projectId}/scenes/${scene.id}/choose-ai-option`,
                     { method: "POST", body: { url: opt.url } });
        picker.innerHTML = "";
        loadScenes();
      });
      grid.appendChild(cell);
    });
    box.appendChild(grid);

    // An option that failed while its partner worked is named rather than
    // silently absent: asking for two and being shown one, with nothing
    // saying so, reads as the tool having decided one was better.
    const failed = options.filter((o) => o && !o.url && o.error);
    if (failed.length) {
      box.appendChild(CB.el('<p class="cb-hint">' + failed.length
        + (failed.length === 1 ? " option" : " options") + " failed: "
        + CB.escapeHtml(failed.map((o) => o.error).join(" \u00b7 ")) + "</p>"));
    }

    picker.innerHTML = "";
    picker.appendChild(box);
  }

  // -------------------------------------------------------- Use Spokesperson
  async function openSpokespersonPicker(card, scene) {
    closeExistingPickers();
    const picker = card.querySelector(".asset-picker");
    picker.innerHTML = '<div class="cb-card" style="margin-top:10px;padding:12px;"><span class="cb-spinner"></span> Loading presenters…</div>';
    const { presenters, live } = await CB.api(`/api/presenters?client_id=${clientId}`);
    const box = CB.el(`<div class="cb-card" style="margin-top:10px;padding:12px;"></div>`);
    if (!live) {
      box.appendChild(CB.el(`<p class="cb-hint">Mock mode — no HeyGen key set, so no video
        will be produced. Set HEYGEN_API to generate real presenter clips.</p>`));
    }
    if (!selectedVoiceId) {
      // The clip is generated once and the voice is baked into it, so this is
      // said before the money is spent rather than after.
      box.appendChild(CB.el('<div class="cb-note"><strong>No voice cast yet</strong>'
        + "<p>The presenter speaks this scene's narration, so the clip is rendered in "
        + "whatever voice is chosen — and it cannot be changed afterwards without "
        + "paying for a second clip. Cast the voice on the next step first.</p></div>"));
    }

    // A scene that already has footage can either keep it, with the presenter
    // keyed on top, or be replaced by a full-frame presenter. The choice has
    // to be made BEFORE generating: it decides what background HeyGen is asked
    // for, and re-deciding later means paying for a second clip.
    const hasFootage = Boolean(scene.asset_url) && scene.asset_type !== "spokesperson";
    let overFootage = hasFootage;
    if (hasFootage) {
      const choice = CB.el(`<div class="cb-field">
        <label class="cb-label">This scene already has footage</label>
        <select class="over-footage-select">
          <option value="over">Key the presenter over the footage</option>
          <option value="replace">Replace the footage with a full-frame presenter</option>
        </select></div>`);
      choice.querySelector("select").addEventListener("change", (e) => {
        overFootage = e.target.value === "over";
      });
      box.appendChild(choice);
    }

    function section(title, list) {
      if (!list || !list.length) return;
      box.appendChild(CB.el(`<h4>${title}</h4>`));
      const grid = CB.el('<div class="cb-choice-grid"></div>');
      list.forEach((p) => {
        const avatarId = p.heygen_avatar_id || p.avatar_id || p.id;
        // The talent roster ships with no HeyGen ids against it. Showing those
        // five as pickable and failing on click is the placeholder trap: say
        // up front which of them can actually be used.
        const usable = p.available !== false && Boolean(avatarId);
        const cell = CB.el(`<div class="cb-choice" style="padding:10px;">
          <div class="cb-choice-title">${p.name}</div>
          <div class="cb-choice-sub">${usable ? (p.specialty || "")
            : (p.unavailable_reason || "Not linked to a HeyGen avatar yet.")}</div></div>`);
        if (!usable) {
          cell.style.opacity = ".55";
          cell.style.cursor = "not-allowed";
          cell.title = p.unavailable_reason || "Not linked to a HeyGen avatar yet.";
          grid.appendChild(cell);
          return;
        }
        cell.addEventListener("click", async () => {
          picker.innerHTML = '<div class="cb-card" style="margin-top:10px;padding:12px;">'
            + '<span class="cb-spinner"></span> Sending the narration to HeyGen…</div>';
          try {
            await CB.api(`/api/projects/${projectId}/scenes/${scene.id}/spokesperson`, {
              method: "POST",
              body: { avatar_id: avatarId, voice_id: selectedVoiceId, over_footage: overFootage },
            });
          } catch (e) {
            picker.innerHTML = "";
            return;                       // CB.api has already surfaced the reason
          }
          picker.innerHTML = "";
          CB.toast(live ? "Presenter clip generating — this takes a few minutes."
                        : "Mock mode — no presenter video was produced.", !live);
          await loadScenes();             // re-render starts the poll for this scene
        });
        grid.appendChild(cell);
      });
      box.appendChild(grid);
    }
    section("Client Avatar", presenters.client_avatar ? [presenters.client_avatar] : []);
    section("Saved Smart 1 Talent", presenters.smart1_talent);
    section("HeyGen Stock Presenter", presenters.stock);
    picker.innerHTML = "";
    picker.appendChild(box);
  }

  // ------------------------------------------------------------------ Upload
  function openUploadPicker(card, scene) {
    closeExistingPickers();
    const picker = card.querySelector(".asset-picker");
    picker.innerHTML = "";
    const box = CB.el(`<div class="cb-card" style="margin-top:10px;padding:12px;">
      <input type="file" class="upload-input" accept="video/*,image/*">
      <p class="cb-hint">Uploads go into this client's Cloudinary Creative Library.</p></div>`);
    picker.appendChild(box);
    box.querySelector(".upload-input").addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${CB.API_ROOT}/api/projects/${projectId}/scenes/${scene.id}/upload`, { method: "POST", body: fd });
      const data = await res.json();
      if (!data.ok) return CB.toast(data.error || "Upload failed.", true);
      picker.innerHTML = "";
      loadScenes();
    });
  }

  // ------------------------------------------------------------ Client Asset
  async function openClientAssetPicker(card, scene) {
    closeExistingPickers();
    const picker = card.querySelector(".asset-picker");
    picker.innerHTML = '<div class="cb-card" style="margin-top:10px;padding:12px;"><span class="cb-spinner"></span> Loading client library…</div>';
    const { assets, live } = await CB.api(`/api/clients/${clientId}/assets?category=video`);
    const box = CB.el(`<div class="cb-card" style="margin-top:10px;padding:12px;"></div>`);
    if (!live) box.appendChild(CB.el(`<p class="cb-hint">Mock mode — no Cloudinary credentials set, or nothing uploaded yet for ${clientSlug}.</p>`));
    const grid = CB.el('<div class="cb-choice-grid"></div>');
    (assets || []).forEach((a) => {
      const cell = CB.el(`<div class="cb-choice" style="padding:6px;">
        <div class="cb-scene-thumb" style="aspect-ratio:16/9;background-image:url('${a.secure_url}')"></div></div>`);
      cell.addEventListener("click", async () => {
        await CB.api(`/api/projects/${projectId}/scenes/${scene.id}/use-client-asset`, {
          method: "POST", body: { url: a.secure_url, thumbnail: a.secure_url },
        });
        picker.innerHTML = "";
        loadScenes();
      });
      grid.appendChild(cell);
    });
    box.appendChild(grid);
    if (!assets || !assets.length) box.appendChild(CB.el('<div class="cb-empty">No assets in this client\'s library yet.</div>'));
    picker.innerHTML = "";
    picker.appendChild(box);
  }

  // --------------------------------------------------- the owned library
  //
  // routes/stock.py has searched our own Cloudinary footage alongside Pexels
  // and Pixabay all along, and ranked it first. None of that was visible: the
  // OWNED tier was painted with the FREE badge and `owned_note` -- which says
  // WHY the owned library returned nothing, and has three different answers
  // for it -- was never printed at all. So "we own nothing relevant", "there
  // is no Cloudinary key" and "indexing has not started" all rendered as the
  // same silence, and a producer reading it goes and licenses a clip we may
  // already have.
  function renderOwnedNote(box, data) {
    let note = box.querySelector(".owned-note");
    if (!note) {
      note = CB.el('<p class="cb-hint owned-note"></p>');
      box.appendChild(note);
    }
    const providers = data.providers || {};
    const owned = (data.results || []).filter((r) => r.tier === "OWNED").length;
    if (data.owned_note) {
      note.textContent = "Our own footage library: " + data.owned_note;
      return;
    }
    if (!providers.owned) {
      note.textContent = "Our own footage library was not searched.";
      return;
    }
    note.textContent = owned
      ? `${owned} clip${owned === 1 ? "" : "s"} from our own library are listed first — `
        + "they cost nothing and need no license check."
      : "Our own library was searched and had nothing matching this. "
        + "Everything below is stock.";
  }

  // ------------------------------------------------------------- narration
  //
  // A :60 has room for about 150 words and the script writer sizes the read
  // once and stops, so a long spot came back reading like a :30 with pauses
  // in it. Typing more by hand then turned the word count red, because
  // nothing re-measured. This writes more inside the budget the length
  // actually has, and re-measures.
  async function loadBudget() {
    const el = document.getElementById("narration-budget");
    if (!el) return;
    let data;
    try {
      data = await CB.api(`/api/projects/${projectId}/narration/budget`);
    } catch (e) { return; }
    const b = data.budget;
    let verdict = "on target";
    if (b.under) verdict = "under — there is room for more";
    if (b.over) verdict = "over — the read will feel rushed";
    el.innerHTML = `<strong>${b.used} words</strong> against a target of `
      + `${b.target_low}–${b.target_high} for a :${String(lengthSeconds).padStart(2, "0")} `
      + `<span class="${b.over ? "cb-word-count warn" : ""}">(${verdict})</span>`;
    const btn = document.getElementById("expand-narration-btn");
    if (btn) btn.disabled = b.room < 5;
  }

  async function expandNarration(sceneIndex, btn) {
    const label = btn ? btn.textContent : "";
    if (btn) { btn.disabled = true; btn.textContent = "Writing…"; }
    const noteBox = document.getElementById("narration-note");
    try {
      const body = {};
      if (sceneIndex !== undefined && sceneIndex !== null) body.scene_index = sceneIndex;
      const data = await CB.api(`/api/projects/${projectId}/narration/expand`,
                                { method: "POST", body });
      if (noteBox) {
        noteBox.innerHTML = "";
        if (data.note) {
          // A refusal with its reason, not a button that appears to work and
          // changes nothing.
          noteBox.appendChild(CB.el('<div class="cb-note"><p>'
            + CB.escapeHtml(data.note) + "</p></div>"));
        }
      }
      if (data.written) {
        CB.toast(`Rewrote narration on ${data.written} scene${data.written === 1 ? "" : "s"}.`);
        await loadScenes();
      }
    } catch (e) {
      /* CB.api has already surfaced the reason */
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = label; }
    }
  }

  const expandBtn = document.getElementById("expand-narration-btn");
  if (expandBtn) expandBtn.addEventListener("click", () => expandNarration(null, expandBtn));

  // ---------------------------------------------------------------- checks
  //
  // The same checks Render runs, on the screen everything they are about
  // lives on. They were only on Preview, so the first sight of "scene 3 has
  // no footage" was two steps after the screen with scene 3 on it -- and
  // pressing Render then re-ran the identical set, which is the tool
  // answering a question it had just been asked.
  const QC_LABELS = {
    timing: "Timing", scene_assets: "Footage", voice_fits: "Narration length",
    cta: "CTA", brand: "Brand", resolution: "Resolution", aspect_ratio: "Aspect ratio",
    text_safe_area: "Text safe area", spelling: "Spelling", qr_code: "QR code",
    logo_persistence: "Persistent logo", youtube_hook: "YouTube hook",
    creative_spec: "Published spec", social_hook: "Feed hook", sound_off: "Sound off",
  };

  // Which findings are worth stopping for and which are advice. Both used to
  // paint red, and a page of red teaches people to scroll past it.
  const ADVISORY = new Set(["logo_persistence", "brand", "aspect_ratio", "text_safe_area"]);

  async function runChecks() {
    const list = document.getElementById("qc-list");
    if (!list) return;
    list.innerHTML = '<span class="cb-spinner"></span>';
    let data;
    try {
      data = await CB.api(`/api/projects/${projectId}/qc`, { method: "POST" });
    } catch (e) {
      list.innerHTML = '<div class="cb-empty">The checks could not run.</div>';
      return;
    }
    const qc = data.qc_results || {};
    list.innerHTML = "";
    let blocking = 0;
    Object.entries(qc).forEach(([key, result]) => {
      if (key === "_all_passed" || !QC_LABELS[key]) return;
      let tone = "pass", mark = "✓";
      if (!result.passed) {
        const advisory = ADVISORY.has(key);
        tone = advisory ? "warn" : "fail";
        mark = advisory ? "!" : "✕";
        if (!advisory) blocking += 1;
      }
      list.appendChild(CB.el(`<div class="cb-qc-item">
        <div class="cb-qc-icon ${tone}">${mark}</div>
        <div class="cb-qc-text"><strong>${QC_LABELS[key]}</strong>
        <span>${CB.escapeHtml(result.message)}</span></div>
      </div>`));
    });
    if (qc._all_passed) CB.toast("Everything checks out.");
    else if (!blocking) CB.toast("Nothing blocking — the rest are recommendations.");
  }

  const checksBtn = document.getElementById("run-checks-btn");
  if (checksBtn) checksBtn.addEventListener("click", runChecks);

  loadCastVoice().then(loadScenes);
})();
