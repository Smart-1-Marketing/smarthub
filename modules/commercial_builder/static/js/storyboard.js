(() => {
  const wrap = document.getElementById("scenes-wrap");
  const projectId = wrap.dataset.projectId;
  const clientId = wrap.dataset.clientId;
  const clientSlug = wrap.dataset.clientSlug;
  const tpl = document.getElementById("scene-card-tpl");
  let dragSourceId = null;
  let selectedVoiceId = null;
  let clientPronunciation = {};

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
  }

  function renderWordCount(script) {
    const el = document.getElementById("wc-summary");
    if (!script || !script.word_count === undefined) { el.textContent = "No script yet."; return; }
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

    node.querySelector(".visual-input").value = scene.visual_description || "";
    node.querySelector(".narration-input").value = scene.narration || "";
    node.querySelector(".duration-input").value = (scene.end - scene.start).toFixed(1);

    node.querySelector(".visual-input").addEventListener("change", (e) =>
      updateScene(scene.id, { visual_description: e.target.value }));
    node.querySelector(".narration-input").addEventListener("change", (e) =>
      updateScene(scene.id, { narration: e.target.value }).then(loadScenes));
    node.querySelector(".duration-input").addEventListener("change", (e) =>
      updateScene(scene.id, { duration: parseFloat(e.target.value) }).then(loadScenes));

    node.querySelector(".find-stock-btn").addEventListener("click", () => openStockPicker(node, scene));
    node.querySelector(".generate-ai-btn").addEventListener("click", () => openAiPicker(node, scene));
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
      const { results: items } = await CB.api(`/api/stock/search?q=${encodeURIComponent(q)}&expand=true`);
      results.innerHTML = "";
      items.forEach((item) => {
        const tier = item.tier === "PREMIUM" ? "cb-badge-premium" : "cb-badge-free";
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

  // ------------------------------------------------------------ Generate AI
  async function openAiPicker(card, scene) {
    closeExistingPickers();
    const picker = card.querySelector(".asset-picker");
    picker.innerHTML = '<div class="cb-card" style="margin-top:10px;padding:12px;"><span class="cb-spinner"></span> Generating two options…</div>';
    const { options, live } = await CB.api(`/api/projects/${projectId}/scenes/${scene.id}/generate-ai`, { method: "POST" });
    const box = CB.el(`<div class="cb-card" style="margin-top:10px;padding:12px;">
      <p style="margin:0 0 8px;">${live ? "" : "Mock mode — no OPENAI_API_KEY set. "}Runway (V1.5) will add true AI video generation; this is a still-frame option in the meantime.</p>
      <div class="cb-choice-grid"></div></div>`);
    const grid = box.querySelector(".cb-choice-grid");
    ["A", "B"].forEach((label, i) => {
      const opt = options[i];
      if (!opt) return;
      const cell = CB.el(`<div class="cb-choice" style="padding:6px;">
        <div class="cb-scene-thumb" style="aspect-ratio:16/9;background-image:url('${opt.url || ""}')"></div>
        <div class="cb-choice-title" style="margin-top:4px;">Option ${label}</div></div>`);
      cell.addEventListener("click", async () => {
        if (!opt.url) return CB.toast("This option failed to generate.", true);
        await CB.api(`/api/projects/${projectId}/scenes/${scene.id}/choose-ai-option`, { method: "POST", body: { url: opt.url } });
        picker.innerHTML = "";
        loadScenes();
      });
      grid.appendChild(cell);
    });
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

  // ------------------------------------------------------------- Voice Studio
  async function loadVoices() {
    const select = document.getElementById("voice-select");
    const { voices, live } = await CB.api("/api/voices");
    select.innerHTML = voices.map((v) => `<option value="${v.voice_id}">${v.name}${v.style ? " — " + v.style : ""}</option>`).join("");
    if (voices.length) selectedVoiceId = voices[0].voice_id;
    select.addEventListener("change", () => (selectedVoiceId = select.value));
    if (!live) document.getElementById("voice-status").textContent = "Mock mode — no ELEVENLABS_API_KEY set.";
  }

  ["voice-speed", "voice-stability", "voice-style"].forEach((id) => {
    const input = document.getElementById(id);
    const out = document.getElementById(id.replace("voice-", "") + "-val");
    input.addEventListener("input", () => {
      out.textContent = id === "voice-speed" ? `${input.value}×` : `${Math.round(input.value * 100)}%`;
    });
  });

  async function loadPronunciation() {
    const { client } = await CB.api(`/api/clients/${clientId}`);
    clientPronunciation = client.pronunciation_dict || {};
    renderPronRows();
  }
  function renderPronRows() {
    const wrapEl = document.getElementById("pron-rows");
    wrapEl.innerHTML = "";
    Object.entries(clientPronunciation).forEach(([word, phonetic]) => addPronRow(word, phonetic));
  }
  function addPronRow(word = "", phonetic = "") {
    const wrapEl = document.getElementById("pron-rows");
    const row = CB.el(`<div style="display:flex;gap:8px;margin-bottom:6px;">
      <input type="text" class="pron-word" placeholder="Gahanna" value="${word}" style="flex:1;">
      <input type="text" class="pron-phonetic" placeholder="guh-HAN-uh" value="${phonetic}" style="flex:1;">
      <button class="cb-btn cb-btn-sm cb-btn-danger pron-remove">✕</button></div>`);
    row.querySelector(".pron-remove").addEventListener("click", async () => {
      row.remove();
      await savePronunciation();
    });
    row.querySelectorAll("input").forEach((i) => i.addEventListener("change", savePronunciation));
    wrapEl.appendChild(row);
  }
  async function savePronunciation() {
    const dict = {};
    document.querySelectorAll("#pron-rows > div").forEach((row) => {
      const word = row.querySelector(".pron-word").value.trim();
      const phonetic = row.querySelector(".pron-phonetic").value.trim();
      if (word) dict[word] = phonetic;
    });
    clientPronunciation = dict;
    await CB.api(`/api/clients/${clientId}/pronunciation`, { method: "PUT", body: { pronunciation_dict: dict } });
  }
  document.getElementById("pron-add").addEventListener("click", () => addPronRow());

  document.getElementById("voice-preview-btn").addEventListener("click", async () => {
    if (!selectedVoiceId) return CB.toast("Choose a voice first.", true);
    const status = document.getElementById("voice-status");
    status.textContent = "Generating…";
    const { voiceover, live } = await CB.api(`/api/projects/${projectId}/voiceover/full`, {
      method: "POST",
      body: {
        voice_id: selectedVoiceId,
        speed: parseFloat(document.getElementById("voice-speed").value),
        stability: parseFloat(document.getElementById("voice-stability").value),
        style: parseFloat(document.getElementById("voice-style").value),
      },
    });
    status.textContent = live
      ? `Generated — estimated ${voiceover.duration_estimate}s of narration.`
      : `Mock mode — estimated ${voiceover.duration_estimate}s of narration (no audio file, no ELEVENLABS_API_KEY set).`;
  });

  // ------------------------------------------------------------------- Music
  document.getElementById("save-music-btn").addEventListener("click", async () => {
    await CB.api(`/api/projects/${projectId}/music`, {
      method: "PUT",
      body: { mood: document.getElementById("music-mood").value, level: document.getElementById("music-level").value },
    });
    CB.toast("Music selection saved.");
  });

  // --------------------------------------------------------------- CTA Builder
  let ctaStyle = document.querySelector("#cta-style-choices .selected")?.dataset.value || "logo_centered";
  document.getElementById("cta-style-choices").addEventListener("click", (e) => {
    const choice = e.target.closest(".cb-choice");
    if (!choice) return;
    document.querySelectorAll("#cta-style-choices .cb-choice").forEach((c) => c.classList.remove("selected"));
    choice.classList.add("selected");
    ctaStyle = choice.dataset.value;
  });
  const phoneInput = document.getElementById("cta-phone"); // absent on :05 bumpers by design
  const qrEnabledInput = document.getElementById("cta-qr-enabled");
  const qrCornerInput = document.getElementById("cta-qr-corner");
  const logoPersistentInput = document.getElementById("cta-logo-persistent");
  const logoCornerInput = document.getElementById("cta-logo-corner");

  function syncCtaToggleVisibility() {
    const qrControls = document.getElementById("qr-controls");
    if (qrControls && qrEnabledInput) qrControls.style.display = qrEnabledInput.checked ? "flex" : "none";
    const logoControls = document.getElementById("logo-corner-controls");
    if (logoControls && logoPersistentInput) logoControls.style.display = logoPersistentInput.checked ? "" : "none";
  }
  if (qrEnabledInput) qrEnabledInput.addEventListener("change", syncCtaToggleVisibility);
  if (logoPersistentInput) logoPersistentInput.addEventListener("change", syncCtaToggleVisibility);
  syncCtaToggleVisibility();

  document.getElementById("save-cta-btn").addEventListener("click", async () => {
    const btn = document.getElementById("save-cta-btn");
    btn.disabled = true;
    try {
      const { cta } = await CB.api(`/api/projects/${projectId}/cta`, {
        method: "PUT",
        body: {
          style: ctaStyle,
          offer: document.getElementById("cta-offer").value.trim(),
          headline: document.getElementById("cta-headline").value.trim(),
          website: document.getElementById("cta-website").value.trim(),
          phone: phoneInput ? phoneInput.value.trim() : "",
          qr_enabled: qrEnabledInput ? qrEnabledInput.checked : false,
          qr_corner: qrCornerInput ? qrCornerInput.value : undefined,
          logo_persistent: logoPersistentInput ? logoPersistentInput.checked : false,
          logo_corner: logoCornerInput ? logoCornerInput.value : undefined,
        },
      });
      const qrPreview = document.getElementById("qr-preview");
      if (qrPreview) {
        if (cta.qr_enabled && (cta.qr_data_url || cta.qr_image_url)) {
          qrPreview.src = cta.qr_data_url || cta.qr_image_url;
          qrPreview.style.display = "";
        } else {
          qrPreview.style.display = "none";
        }
      }
      CB.toast("CTA saved.");
    } finally {
      btn.disabled = false;
    }
  });

  loadScenes();
  loadVoices();
  loadPronunciation();
})();
