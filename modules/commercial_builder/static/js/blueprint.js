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
    loadAbcd();
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

    // Numbered in tens the way a storyboard is, so a shot inserted between 20
    // and 30 does not renumber the board.
    const shotNo = (scene.asset_meta || {}).shot_no || (idx + 1) * 10;
    node.querySelector(".scene-num").textContent =
      `${shotNo} · ${idx + 1} of ${total}${scene.is_cta ? " — CTA" : ""}`;
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
      note.innerHTML = '<span class="cb-spinner" data-s1-think="ai"></span> Presenter clip is rendering at HeyGen. '
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
      note.innerHTML = '<span class="cb-spinner" data-s1-think="ai"></span> Runway is animating this frame. '
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

    // Which beat this shot belongs to. A Scene row is a shot now, and without
    // the badge a board of fifteen rows loses the argument the beats carry.
    const meta = scene.asset_meta || {};
    const beatBadge = node.querySelector(".beat-badge");
    if (beatBadge) {
      beatBadge.textContent = meta.beat || "";
      beatBadge.style.display = meta.beat ? "" : "none";
    }

    const grammar = meta.grammar || {};
    [["size", ".grammar-size"], ["angle", ".grammar-angle"], ["move", ".grammar-move"]]
      .forEach(([field, sel]) => {
        const el = node.querySelector(sel);
        if (!el) return;
        if (grammar[field]) el.value = grammar[field];
        el.addEventListener("change", () => {
          const next = {
            size: node.querySelector(".grammar-size").value,
            angle: node.querySelector(".grammar-angle").value,
            move: node.querySelector(".grammar-move").value,
          };
          updateScene(scene.id, { grammar: next }).then(loadAbcd);
        });
      });

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
    node.querySelector(".sfx-btn").addEventListener("click", () => openSfxPicker(node, scene));
    paintSfxNote(node, scene);

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
        <div class="cb-flex-between" style="margin-bottom:4px;">
          <strong>Our video library + stock</strong>
          <a href="/tools/video-backgrounds/" target="_blank" rel="noopener">Open full library</a>
        </div>
        <p class="cb-hint" style="margin:0 0 8px;">We search our owned footage first, then Pexels and Pixabay.</p>
        <div style="display:flex;gap:8px;margin-bottom:8px;">
          <input type="search" placeholder="Search our video library and stock…" class="stock-q" style="flex:1;">
          <button class="cb-btn cb-btn-sm stock-go">Search</button>
        </div>
        <p class="cb-hint owned-note" style="margin:0 0 8px;"></p>
        <div class="stock-results"></div>
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
      // These named groups come from the same Video Search service used by
      // /tools/video-backgrounds. Fall back to the tier split so an older API
      // response still renders correctly during a rolling deploy.
      const videoSearchItems = data.video_search_results
        || items.filter((item) => item.tier === "OWNED");
      const stockItems = data.stock_results
        || items.filter((item) => item.tier !== "OWNED");
      results.innerHTML = "";
      renderOwnedNote(box, data);

      function renderShelf(title, shelfItems, videoSearch) {
        if (!shelfItems.length) return;
        results.appendChild(CB.el(`<h4 style="margin:12px 0 6px;">${title}</h4>`));
        const grid = CB.el('<div class="cb-choice-grid"></div>');
        shelfItems.forEach((item) => {
        /* OWNED footage was drawn with the FREE badge, so a clip we already
           hold looked exactly like a Pexels result. routes/stock.py ranks it
           first precisely because it costs nothing and needs no license check,
           and a badge that does not say so throws that away. */
        const tier = item.tier === "PREMIUM" ? "cb-badge-premium"
                   : (item.tier === "OWNED" ? "cb-badge-owned" : "cb-badge-free");
        const badge = videoSearch ? "VIDEO SEARCH" : item.tier;
        const cell = CB.el(`
          <div class="cb-choice" style="padding:6px;">
            <div class="cb-scene-thumb" style="aspect-ratio:16/9;background-image:url('${item.thumbnail}')"></div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:4px;">
              <span class="cb-badge ${tier}">${badge}</span>
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
          grid.appendChild(cell);
        });
        results.appendChild(grid);
      }

      // The first shelf is explicit, not just a sort order hidden in one grid:
      // it is the indexed Smart 1 Video Search library and should be reviewed
      // before outside stock whenever it has a relevant match.
      renderShelf("Suggested from Video Search", videoSearchItems, true);
      renderShelf("More stock options", stockItems, false);
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

  // ------------------------------------------------------ Add Sound Effect
  //
  // The fifth source on a scene card, and the first one that is audio. Two
  // drafts per press, nothing generated on a page load, and the attached one
  // is named on the card rather than left as a state you can only see by
  // opening the picker again -- an attached asset a rep cannot see is one
  // they attach twice.

  function paintSfxNote(card, scene) {
    const note = card.querySelector(".sfx-note");
    if (!note) return;
    const sfx = (scene.asset_meta || {}).sfx || {};
    if (!sfx.url) { note.textContent = ""; return; }
    const len = sfx.seconds ? `${sfx.seconds}s` : "length not measured";
    note.innerHTML = `\u{1F50A} <strong>${CB.escapeHtml(sfx.prompt || "Sound effect")}</strong>`
      + ` \u00b7 ${len} \u00b7 <a href="#" class="sfx-clear">remove</a>`;
    note.querySelector(".sfx-clear").addEventListener("click", async (e) => {
      e.preventDefault();
      await CB.api(`/api/projects/${projectId}/scenes/${scene.id}/sound-effect`,
                   { method: "DELETE" });
      loadScenes();
    });
  }

  async function openSfxPicker(card, scene) {
    closeExistingPickers();
    const picker = card.querySelector(".asset-picker");
    const sceneSeconds = Math.round((scene.end - scene.start) * 10) / 10;
    let limits = { min: 0.5, max: 30 };
    try {
      const opts = await CB.api(`/api/projects/${projectId}/audio/options`);
      if (opts && opts.sfx_duration) limits = opts.sfx_duration;
    } catch (e) { /* the defaults are the published ones; a failed read costs nothing */ }

    /* A shot longer than ElevenLabs will generate is not offered as a length.
       The server refuses it by name either way, which is right — but a picker
       that offers a value its own server rejects has wasted the press, and on
       a :60 with four long shots it wastes it every time. */
    const matchable = sceneSeconds >= limits.min && sceneSeconds <= limits.max;

    /* Blank is offered FIRST and is the default, because it is very often the
       right answer: a thump and a thirty-second ambience are not the same
       length, and the model reads that from the description better than a
       slider does. The scene's own length is offered beside it because an
       effect longer than its shot is trimmed at the render -- said here,
       where it can still be changed, rather than discovered in the file. */
    const box = CB.el(`
      <div class="cb-card" style="margin-top:10px;padding:12px;">
        <div class="cb-flex-between" style="margin-bottom:4px;">
          <strong>Sound effect for this shot</strong>
          <button class="cb-btn cb-btn-sm sfx-close">Close</button>
        </div>
        <div class="cb-field">
          <input type="text" class="sfx-prompt" placeholder="cinematic whoosh \u00b7 car door slam \u00b7 cash register">
        </div>
        <div class="cb-field">
          <label class="cb-label">Length</label>
          <select class="sfx-duration">
            <option value="">Let the model decide from the description</option>
            ${matchable
              ? `<option value="${sceneSeconds}">Match this shot \u2014 ${sceneSeconds}s</option>`
              : ""}
            <option value="1">1s \u2014 a hit or a stinger</option>
            <option value="3">3s \u2014 a transition</option>
            <option value="8">8s \u2014 a bed of ambience</option>
          </select>
          <p class="cb-hint">ElevenLabs generates between ${limits.min}s and ${limits.max}s.
            Anything longer than this shot is trimmed to it at the render.</p>
        </div>
        <div class="cb-flex-between">
          <button class="cb-btn cb-btn-primary sfx-go">Generate 2 options</button>
          <span class="cb-hint sfx-status"></span>
        </div>
        <div class="sfx-options" style="margin-top:10px;"></div>
      </div>`);
    picker.innerHTML = "";
    picker.appendChild(box);

    const promptInput = box.querySelector(".sfx-prompt");
    promptInput.value = ((scene.asset_meta || {}).sfx || {}).prompt || "";
    box.querySelector(".sfx-close").addEventListener("click", () => { picker.innerHTML = ""; });
    box.querySelector(".sfx-go").addEventListener("click", () =>
      generateSfx(box, scene, promptInput.value,
                  box.querySelector(".sfx-duration").value));
  }

  async function generateSfx(box, scene, prompt, duration) {
    const status = box.querySelector(".sfx-status");
    const list = box.querySelector(".sfx-options");
    const btn = box.querySelector(".sfx-go");
    if (!(prompt || "").trim()) {
      status.textContent = "Say what the sound is first.";
      return;
    }
    /* A billed wait behind a button, marked the way every other billed wait
       in this Hub is. Guarded on window.S1Think so a page that failed to load
       the script loses the mark rather than the press. */
    const busy = (btn && window.S1Think)
      ? window.S1Think.busy(btn, { kind: "ai", label: "Generating\u2026" })
      : null;
    if (btn && !window.S1Think) { btn.disabled = true; btn.textContent = "Generating\u2026"; }
    status.textContent = "";
    list.innerHTML = "";
    let data;
    try {
      data = await CB.api(
        `/api/projects/${projectId}/scenes/${scene.id}/sound-effect`,
        { method: "POST", body: { prompt: prompt.trim(), duration_seconds: duration || null } });
    } catch (e) {
      return;                              // CB.api has already surfaced the reason
    } finally {
      if (busy) busy.done();
      if (btn) { btn.disabled = false; btn.textContent = "Generate 2 options"; }
    }
    if (data.note) status.textContent = data.note;
    drawSfxOptions(list, scene, data.options || []);
  }

  function drawSfxOptions(list, scene, options) {
    list.innerHTML = "";
    if (!options.length) {
      list.appendChild(CB.el('<p class="cb-hint">Nothing came back.</p>'));
      return;
    }
    options.forEach((opt) => {
      /* A failed option carries its own reason rather than the batch
         collapsing into one -- asking for two and getting one is ordinary,
         and reporting the whole press as failed throws away the one that
         worked. */
      if (!opt.url) {
        list.appendChild(CB.el(
          `<p class="cb-word-count warn">Option ${opt.index + 1}: ${CB.escapeHtml(opt.error || "no audio")}</p>`));
        return;
      }
      const len = opt.seconds ? `${opt.seconds}s` : "length not measured";
      const row = CB.el(`<div class="cb-audio-row">
        <audio controls preload="none" src="${opt.url}"></audio>
        <span class="cb-hint">${len}${opt.cached ? " \u00b7 reused" : ""}</span>
        <button class="cb-btn cb-btn-sm sfx-use">Use this</button>
      </div>`);
      row.querySelector(".sfx-use").addEventListener("click", async () => {
        await CB.api(`/api/projects/${projectId}/scenes/${scene.id}/sound-effect/choose`, {
          method: "POST",
          body: { url: opt.url, public_id: opt.public_id, prompt: opt.prompt,
                  seconds: opt.seconds, requested_seconds: opt.requested_seconds },
        });
        CB.toast("Sound effect attached to this shot.");
        loadScenes();
      });
      list.appendChild(row);
    });
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
            + '<span class="cb-spinner" data-s1-think="ai"></span> Sending the narration to HeyGen…</div>';
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
    const owned = (data.video_search_results
      || (data.results || []).filter((r) => r.tier === "OWNED")).length;
    const videoSearchNote = data.video_search_note || data.owned_note;
    if (videoSearchNote) {
      note.textContent = "Video Search: " + videoSearchNote;
      return;
    }
    if (!(providers.video_search || providers.owned)) {
      note.textContent = "Video Search was not available for this search.";
      return;
    }
    note.textContent = owned
      ? `${owned} relevant clip${owned === 1 ? "" : "s"} from Video Search are suggested first — `
        + "they cost nothing and need no license check."
      : "Video Search was checked and had nothing relevant for this scene. "
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
    var wBusy = (btn && window.S1Think)
      ? window.S1Think.busy(btn, {kind: "ai", label: "Writing…"})
      : {done: function () { if (btn) { btn.disabled = false; btn.textContent = label; } }};
    if (btn && !window.S1Think) { btn.disabled = true; btn.textContent = "Writing…"; }
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
      wBusy.done();
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
    abcd_pacing: "Pacing", abcd_brand_window: "Brand window",
    publisher_rules: "Publisher rules", compliance: "Advertising rules",
    archetype_ready: "What this spot needs",
    sfx_gain_conflict: "Sound effect level", music_length_mismatch: "Music length",
  };

  // Severity comes off the server now. It used to be an ADVISORY set kept by
  // hand in THIS file and again in preview.js — two copies of a decision
  // qc_service already had all the information to make, and the fastest way
  // to have one panel draw a finding red while the other drew it amber.

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
      const level = result.level || (result.passed ? "pass" : "fail");
      const tone = level === "pass" ? "pass" : level;
      const mark = level === "pass" ? "✓" : (level === "warn" ? "!" : "✕");
      if (level === "fail") blocking += 1;
      list.appendChild(CB.el(`<div class="cb-qc-item">
        <div class="cb-qc-icon ${tone}">${mark}</div>
        <div class="cb-qc-text"><strong>${QC_LABELS[key]}</strong>
        <span>${CB.escapeHtml(result.message)}</span></div>
      </div>`));
    });
    renderAbcd(qc._abcd);
    if (qc._all_passed && !(qc._warnings || []).length) CB.toast("Everything checks out.");
    else if (!blocking) CB.toast("Nothing blocking — the rest are recommendations.");
  }

  const checksBtn = document.getElementById("run-checks-btn");
  if (checksBtn) checksBtn.addEventListener("click", runChecks);

  // ---------------------------------------------------------------- scoring
  //
  // The published thresholds, drawn from the plan. Every row names whose
  // number it is, because "your average shot is 10 seconds and Google's own
  // detector wants 2" is an argument a client cannot talk us out of, where
  // "our tool thinks this is slow" is an opinion.
  function renderAbcd(abcd) {
    const box = document.getElementById("abcd-rows");
    const headline = document.getElementById("abcd-headline");
    if (!box) return;
    if (!abcd || !abcd.rows.length) {
      box.innerHTML = '<div class="cb-empty">Nothing to score yet.</div>';
      if (headline) headline.textContent = "";
      return;
    }
    if (headline) headline.textContent = abcd.headline;
    box.innerHTML = "";
    abcd.rows.forEach((row) => {
      // Not measured is its own state and never a tick. A green mark over a
      // rule nothing could check is the confident wrong answer.
      const tone = !row.measured ? "info" : (row.passed ? "pass" : "warn");
      const mark = !row.measured ? "–" : (row.passed ? "✓" : "!");
      box.appendChild(CB.el(`<div class="cb-qc-item">
        <div class="cb-qc-icon ${tone}">${mark}</div>
        <div class="cb-qc-text"><strong>${CB.escapeHtml(row.label)}</strong>
        <span>${CB.escapeHtml(row.message)}</span>
        <span class="cb-source">${CB.escapeHtml(row.source || "")}</span></div>
      </div>`));
    });
  }

  async function loadAbcd() {
    try {
      const data = await CB.api(`/api/projects/${projectId}/abcd`);
      renderAbcd(data.abcd);
    } catch (e) { /* the panel simply stays empty */ }
  }

  // ------------------------------------------------------ what rules require
  //
  // Never a verdict. This tool renders finished, deliverable video and two of
  // the commercial types it offers walk straight into published rules — but
  // whether a spot COMPLIES is a legal judgment about a specific ad in a
  // specific state, and a green tick over that question is the thing somebody
  // relies on. So every row says which rule is engaged and what it requires,
  // with the citation, and the panel asks for an acknowledgment rather than
  // claiming an answer.
  async function loadCompliance() {
    const box = document.getElementById("compliance-rows");
    if (!box) return;
    let data;
    try {
      data = await CB.api(`/api/projects/${projectId}/compliance`);
    } catch (e) {
      box.innerHTML = '<div class="cb-empty">The rule check could not run.</div>';
      return;
    }
    const headline = document.getElementById("compliance-headline");
    const ackBox = document.getElementById("compliance-ack");
    const disclaimer = document.getElementById("compliance-disclaimer");
    if (headline) headline.textContent = data.summary || "";
    if (disclaimer) disclaimer.textContent = data.compliance.disclaimer || "";
    box.innerHTML = "";
    ackBox.innerHTML = "";

    const findings = (data.compliance && data.compliance.findings) || [];
    if (!findings.length) {
      // Not "this is fine to run". A statement about what was scanned, and
      // the unknown-industry case says so rather than reading as a clearance.
      box.appendChild(CB.el(`<div class="cb-note"><p>${
        CB.escapeHtml(data.compliance.note || data.summary || "")}</p></div>`));
      return;
    }

    findings.forEach((f) => {
      // "addressed" is tri-state and the middle one matters: we can sometimes
      // see the script already carries what a rule asks for. We can never see
      // that the spot complies, so nothing here draws a tick.
      const state = f.addressed === true
        ? '<span class="cb-badge cb-badge-free">the script mentions this</span>'
        : (f.addressed === false
            ? '<span class="cb-badge cb-badge-premium">not in the script</span>'
            : '<span class="cb-badge cb-badge-mock">check this</span>');
      box.appendChild(CB.el(`<div class="cb-note" style="margin-bottom:10px;">
        <strong>${CB.escapeHtml(f.headline)}</strong> ${state}
        <p style="margin:6px 0;">${CB.escapeHtml(f.requires)}</p>
        <p class="cb-hint" style="margin:0;">
          Engaged by ${CB.escapeHtml(f.evidence || "this spot")} ·
          <strong>${CB.escapeHtml(f.citation)}</strong> ·
          ${CB.escapeHtml(f.authority)}</p>
      </div>`));
    });

    if (data.acknowledged) {
      const a = data.acknowledgment || {};
      ackBox.appendChild(CB.el(`<div class="cb-note good">
        <strong>Acknowledged by ${CB.escapeHtml(a.acknowledged_by || "somebody")}</strong>
        <p style="margin:6px 0 0;">That is a record of who read these, not a
        judgment that the spot complies.${a.note ? " " + CB.escapeHtml(a.note) : ""}</p>
      </div>`));
      return;
    }

    // An edit retires a sign-off. "Nobody has looked" and "somebody looked at
    // a different script" are different situations, and only the second has a
    // name to go back to.
    if (data.superseded) {
      ackBox.appendChild(CB.el(`<div class="cb-note bad">
        <strong>The copy has changed since this was acknowledged</strong>
        <p style="margin:6px 0 0;">${CB.escapeHtml(
          (data.acknowledgment || {}).acknowledged_by || "Somebody")} signed off an
        earlier version, so that no longer covers this cut.</p></div>`));
    }

    const wrap = CB.el('<div style="margin-top:10px;"></div>');
    wrap.appendChild(CB.el('<div class="cb-field"><label class="cb-label">'
      + 'Anything worth recording <span class="cb-muted" style="font-weight:400;">'
      + '(optional)</span></label><input type="text" id="compliance-note" '
      + 'placeholder="Cleared with the firm\u2019s general counsel on the 14th."></div>'));
    const btn = CB.el('<button class="cb-btn cb-btn-primary cb-btn-sm">'
      + "I have read what these require</button>");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await CB.api(`/api/projects/${projectId}/compliance/acknowledge`, {
          method: "POST",
          body: { note: (document.getElementById("compliance-note") || {}).value || "" },
        });
        CB.toast("Recorded against your name.");
        await loadCompliance();
      } finally {
        btn.disabled = false;
      }
    });
    wrap.appendChild(btn);
    wrap.appendChild(CB.el('<p class="cb-hint" style="margin:8px 0 0;">'
      + "Filing a rendered cut waits on this. It is not a compliance sign-off "
      + "\u2014 it records that these were put in front of somebody before the "
      + "spot went out.</p>"));
    ackBox.appendChild(wrap);
  }

  loadCastVoice().then(loadScenes).then(loadAbcd).then(loadCompliance);
})();
