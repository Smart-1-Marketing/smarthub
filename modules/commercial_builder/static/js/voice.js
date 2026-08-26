/* The Voice & music step.

   This was a `<select>` of every voice on the ElevenLabs account, in whatever
   order the API returned it, with nothing to listen to — so in practice the
   answer was always whichever name came out first. The Radio Promo builder,
   against the same account, asks what the read should sound like and offers
   three ranked voices with a sample on each. There is one casting question
   now (hub/voice_casting.py) and both tools ask it. */
(() => {
  const root = document.getElementById("voice-root");
  const projectId = root.dataset.projectId;
  const clientId = root.dataset.clientId;

  let selectedVoiceId = null;
  let clientPronunciation = {};
  const want = {};

  // ------------------------------------------------------- the question
  async function loadCharacteristics() {
    const box = document.getElementById("voice-wants");
    let data;
    try {
      data = await CB.api("/api/voice-characteristics");
    } catch (e) { return; }
    if (!data.characteristics.length) {
      box.innerHTML = `<p class="cb-hint">${CB.escapeHtml(data.note || "")}</p>`;
      return;
    }
    Object.assign(want, data.default || {});
    box.innerHTML = "";
    data.characteristics.forEach((c) => {
      const options = c.options.map((o) =>
        `<option value="${o.id}"${want[c.id] === o.id ? " selected" : ""}>${o.label}</option>`
      ).join("");
      const field = CB.el(`<div class="cb-field">
        <label class="cb-label">${c.label}</label>
        <select data-want="${c.id}">${options}</select>
        ${c.help ? `<p class="cb-hint">${c.help}</p>` : ""}
      </div>`);
      field.querySelector("select").addEventListener("change", (e) => {
        want[c.id] = e.target.value;
      });
      box.appendChild(field);
    });
  }

  // --------------------------------------------------------- the answer
  async function cast() {
    const btn = document.getElementById("cast-btn");
    const out = document.getElementById("cast-results");
    btn.disabled = true;
    out.innerHTML = CB.working("voice", "Listening to the account…",
                               "Ranking every voice against what you asked for.");
    let data;
    try {
      data = await CB.api("/api/voices/cast", {
        method: "POST",
        body: { want, count: 3, project_id: parseInt(projectId, 10) },
      });
    } catch (e) {
      out.innerHTML = "";
      btn.disabled = false;
      return;
    }
    btn.disabled = false;

    // The note is never decoration. An account of cloned voices carries no
    // labels at all, and three names in the account's own order presented as
    // a ranking is the confident wrong answer this codebase keeps undoing.
    document.getElementById("cast-note").textContent = data.note || "";

    out.innerHTML = "";
    if (!data.voices.length) {
      out.appendChild(CB.el('<div class="cb-empty">No voices came back.</div>'));
      return;
    }
    data.voices.forEach((v) => out.appendChild(voiceRow(v, data.live)));
  }

  function voiceRow(voice, live) {
    const why = (voice.match_reasons || []).join(", ");
    const traits = [voice.gender, voice.age, voice.accent, voice.descriptor]
      .filter(Boolean).join(" · ");
    const row = CB.el(`<div class="cb-voice-row" data-voice-id="${voice.voice_id}">
      <div style="flex:1;">
        <div class="cb-voice-name">${CB.escapeHtml(voice.name || "Unnamed voice")}</div>
        <div class="cb-voice-why">${CB.escapeHtml(traits || "no labels on this voice")}</div>
        <div class="cb-voice-why">${why ? "Matched on " + CB.escapeHtml(why)
          : "Matched nothing you asked for — listen before casting."}</div>
      </div>
    </div>`);

    // The sample is the point. A voice picked without hearing it is a voice
    // picked by its name.
    if (voice.preview_url) {
      const audio = CB.el(`<audio controls preload="none" src="${voice.preview_url}"></audio>`);
      // Playing the sample must not also cast the voice — the row is
      // clickable, and a click on the play button bubbles up to it.
      audio.addEventListener("click", (e) => e.stopPropagation());
      row.appendChild(audio);
    } else {
      row.appendChild(CB.el(`<span class="cb-voice-why">${live
        ? "No sample published for this voice."
        : "No sample — mock mode, no ElevenLabs key set."}</span>`));
    }

    row.addEventListener("click", () => selectVoice(voice.voice_id, voice.name));
    if (voice.voice_id === selectedVoiceId) row.classList.add("selected");
    return row;
  }

  async function selectVoice(voiceId, name) {
    selectedVoiceId = voiceId;
    document.querySelectorAll(".cb-voice-row").forEach((r) =>
      r.classList.toggle("selected", r.dataset.voiceId === voiceId));
    const select = document.getElementById("voice-select");
    if (select) select.value = voiceId;

    // Saved onto the client rather than held in this tab. A voice cast and
    // then lost to a page reload is a decision taken twice, and the second
    // time it will be a different voice.
    try {
      await CB.api(`/api/clients/${clientId}`, {
        method: "PUT", body: { preferred_voiceover_id: voiceId },
      });
      CB.toast(`Cast ${name || "this voice"}.`);
    } catch (e) { /* CB.api has surfaced it */ }
  }

  document.getElementById("cast-btn").addEventListener("click", cast);

  // ------------------------------------------------ the whole account list
  async function loadVoices() {
    const select = document.getElementById("voice-select");
    let data;
    try {
      data = await CB.api("/api/voices");
    } catch (e) { return; }
    select.innerHTML = '<option value="">— Choose a voice —</option>'
      + data.voices.map((v) =>
          `<option value="${v.voice_id}">${v.name}${v.style ? " — " + v.style : ""}</option>`
        ).join("");
    select.addEventListener("change", () => {
      if (select.value) {
        selectVoice(select.value, select.options[select.selectedIndex].text);
      }
    });
    if (!data.live) {
      document.getElementById("voice-status").textContent =
        "Mock mode — no ELEVENLABS_API key set, so nothing will actually be rendered.";
    }
  }

  // ------------------------------------------------------------- settings
  ["voice-speed", "voice-stability", "voice-style"].forEach((id) => {
    const input = document.getElementById(id);
    const out = document.getElementById(id.replace("voice-", "") + "-val");
    input.addEventListener("input", () => {
      out.textContent = id === "voice-speed"
        ? `${input.value}×` : `${Math.round(input.value * 100)}%`;
    });
  });

  async function loadClient() {
    const { client } = await CB.api(`/api/clients/${clientId}`);
    clientPronunciation = client.pronunciation_dict || {};
    if (client.preferred_voiceover_id) selectedVoiceId = client.preferred_voiceover_id;
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
      <input type="text" class="pron-word" placeholder="Gahanna" value="${CB.escapeHtml(word)}" style="flex:1;">
      <input type="text" class="pron-phonetic" placeholder="guh-HAN-uh" value="${CB.escapeHtml(phonetic)}" style="flex:1;">
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
    await CB.api(`/api/clients/${clientId}/pronunciation`,
                 { method: "PUT", body: { pronunciation_dict: dict } });
  }

  document.getElementById("pron-add").addEventListener("click", () => addPronRow());

  document.getElementById("voice-preview-btn").addEventListener("click", async () => {
    if (!selectedVoiceId) return CB.toast("Cast a voice first.", true);
    const status = document.getElementById("voice-status");
    status.textContent = "Generating…";
    try {
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
        : `Mock mode — estimated ${voiceover.duration_estimate}s of narration `
          + "(no audio file, no ELEVENLABS_API key set).";
    } catch (e) {
      status.textContent = "";
    }
  });

  // ---------------------------------------------------------------- music
  document.getElementById("save-music-btn").addEventListener("click", async () => {
    await CB.api(`/api/projects/${projectId}/music`, {
      method: "PUT",
      body: {
        mood: document.getElementById("music-mood").value,
        level: document.getElementById("music-level").value,
      },
    });
    CB.toast("Music selection saved.");
  });

  loadCharacteristics();
  loadVoices();
  loadClient();
})();
