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
  //
  // Tiles, not dropdowns: every option visible at once, and each carrying the
  // words it will actually match on. That sub-line is the honest part — the
  // ranking searches ElevenLabs' own description text for them, so somebody
  // whose client is nothing like "announcer, commercial, broadcast" can see
  // that before casting rather than after listening to three wrong voices.
  //
  // An energy option also draws its amplitude, because energy is the one
  // characteristic that does more than rank: STYLE_BY_ENERGY becomes the
  // `style` value on the render, so the choice changes the read itself.
  // Nothing is drawn for gender, age or accent — a glyph there would assert
  // something the tool does not know.
  function energyArt(style) {
    const level = typeof style === "number" ? style : 0.3;
    const bars = [0.35, 0.62, 1, 0.72, 0.45, 0.8];
    const rects = bars.map((b, i) => {
      const h = Math.max(3, Math.round(4 + b * level * 30));
      const y = Math.round((22 - h) / 2);
      return `<rect x="${i * 5}" y="${y}" width="3" height="${h}" rx="1.5" fill="currentColor"></rect>`;
    }).join("");
    return `<svg width="28" height="22" viewBox="0 0 28 22" aria-hidden="true">${rects}</svg>`;
  }

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
      const row = CB.el(`<div class="cb-char">
        <span class="cb-charname">${CB.escapeHtml(c.label)}</span>
        <div class="cb-optrow"></div>
      </div>`);
      const optrow = row.querySelector(".cb-optrow");

      c.options.forEach((o) => {
        const art = (c.id === "energy") ? energyArt(o.style) : "";
        // Only the first few words: the whole list is six items on some rows
        // and turns a tile into a paragraph.
        const words = (o.matches || []).slice(0, 3).join(", ");
        const cell = CB.el(`<button type="button" class="cb-opt${
          want[c.id] === o.id ? " selected" : ""}" data-value="${o.id}">
          ${art}
          <span><span class="cb-opt-label">${CB.escapeHtml(o.label)}</span>${
            words ? `<span class="cb-opt-sub">${CB.escapeHtml(words)}</span>` : ""}</span>
        </button>`);
        cell.addEventListener("click", () => {
          want[c.id] = o.id;
          [...optrow.children].forEach((x) => x.classList.remove("selected"));
          cell.classList.add("selected");
        });
        optrow.appendChild(cell);
      });

      if (c.scored_on) {
        row.appendChild(CB.el(`<p class="cb-hint" style="margin:5px 0 0;">Matched against ${
          CB.escapeHtml(c.scored_on)}.</p>`));
      }
      box.appendChild(row);
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
    data.voices.forEach((v) => out.appendChild(voiceCard(v, data.live, data.asked)));
  }

  // One <audio> for the page, not one per card: several samples playing over
  // each other is the fastest way to make a casting screen useless.
  const player = new Audio();
  let playingId = null;

  player.addEventListener("ended", () => setPlaying(null));
  player.addEventListener("error", () => {
    if (playingId) CB.toast("That sample would not play.", true);
    setPlaying(null);
  });

  function setPlaying(voiceId) {
    playingId = voiceId;
    document.querySelectorAll(".cb-voice-card").forEach((c) => {
      const on = c.dataset.voiceId === voiceId;
      c.classList.toggle("playing", on);
      const btn = c.querySelector(".cb-play");
      if (btn) btn.innerHTML = on ? ICON_PAUSE : ICON_PLAY;
    });
  }

  const ICON_PLAY = '<svg width="14" height="16" viewBox="0 0 14 16" aria-hidden="true">'
    + '<path d="M2 1.5v13l11-6.5z" fill="currentColor"></path></svg>';
  const ICON_PAUSE = '<svg width="14" height="16" viewBox="0 0 14 16" aria-hidden="true">'
    + '<rect x="2" y="1.5" width="3.6" height="13" rx="1.2" fill="currentColor"></rect>'
    + '<rect x="8.4" y="1.5" width="3.6" height="13" rx="1.2" fill="currentColor"></rect></svg>';

  const WAVE = '<svg class="cb-voice-wave" viewBox="0 0 74 26" aria-hidden="true">'
    + [6, 15, 24, 33, 42, 51].map((x, i) => {
        const h = [10, 20, 14, 24, 12, 18][i];
        return `<rect x="${x}" y="${(26 - h) / 2}" width="5" height="${h}" rx="2.5" `
             + 'fill="currentColor"></rect>';
      }).join("") + "</svg>";

  function voiceCard(voice, live, asked) {
    const traits = [voice.gender, voice.age, voice.accent, voice.descriptor]
      .filter(Boolean).join(" · ");
    const reasons = voice.match_reasons || [];
    const card = CB.el(`<div class="cb-voice-card" data-voice-id="${voice.voice_id}"></div>`);

    const play = CB.el(`<button type="button" class="cb-play"
      aria-label="Play a sample of ${CB.escapeHtml(voice.name || "this voice")}">${ICON_PLAY}</button>`);
    if (!voice.preview_url) {
      play.disabled = true;
      play.title = live ? "No sample published for this voice."
                        : "No sample — mock mode, no ElevenLabs key set.";
    }
    play.addEventListener("click", (e) => {
      e.stopPropagation();                     // playing is not casting
      if (playingId === voice.voice_id) { player.pause(); setPlaying(null); return; }
      player.src = voice.preview_url;
      player.play().then(() => setPlaying(voice.voice_id)).catch(() => setPlaying(null));
    });
    card.appendChild(play);
    card.appendChild(CB.el(WAVE));

    const body = CB.el('<div style="flex:1;min-width:0;"></div>');
    body.appendChild(CB.el(`<div class="cb-voice-name">${
      CB.escapeHtml(voice.name || "Unnamed voice")}</div>`));
    body.appendChild(CB.el(`<div class="cb-voice-why">${
      CB.escapeHtml(traits || "no labels published on this voice")}</div>`));
    const chips = CB.el('<div class="cb-match"></div>');
    if (reasons.length) {
      reasons.forEach((r) => chips.appendChild(CB.el(`<span>${CB.escapeHtml(r)}</span>`)));
    } else {
      // Top of a list having matched nothing is a real outcome and must say so
      // rather than showing an empty row that reads like a clean match.
      chips.appendChild(CB.el('<span class="none">matched nothing you asked for</span>'));
    }
    body.appendChild(chips);
    card.appendChild(body);

    // "3 of 4", where 4 is what was actually asked — "any" is not a question,
    // so counting it would make a perfect match read as a poor one.
    if (asked) {
      card.appendChild(CB.el(`<div class="cb-voice-strength">${reasons.length} of ${asked}<br>
        <span style="font-size:10px;">matched</span></div>`));
    }

    card.addEventListener("click", () => selectVoice(voice.voice_id, voice.name));
    if (voice.voice_id === selectedVoiceId) card.classList.add("selected");
    return card;
  }

  async function selectVoice(voiceId, name) {
    selectedVoiceId = voiceId;
    document.querySelectorAll(".cb-voice-card").forEach((r) =>
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
      const { voiceover } = await CB.api(`/api/projects/${projectId}/voiceover/full`, {
        method: "POST",
        body: {
          voice_id: selectedVoiceId,
          speed: parseFloat(document.getElementById("voice-speed").value),
          stability: parseFloat(document.getElementById("voice-stability").value),
          style: parseFloat(document.getElementById("voice-style").value),
        },
      });
      // Whether it was STORED is the part that matters: the render reads the
      // stored URL, and a voiceover generated and not stored is a commercial
      // that comes back silent.
      status.textContent = `Estimated ${voiceover.duration_estimate}s of narration. `
        + (voiceover.store_note || "");
    } catch (e) {
      status.textContent = "";
    }
  });

  // ---------------------------------------------------------------- music
  //
  // Two dropdowns became two pictures, on the same rule as the casting tiles:
  // draw a graphic where it encodes something real.
  //
  // The mood waveforms are the honest exception, and they are labelled as
  // one. No track has been chosen at this point — nothing in this Hub picks
  // music yet — so a waveform here is an illustration of the mood, not a
  // reading of a file. The note under the grid says exactly that, because a
  // picture that looks like a waveform reading and is not is precisely the
  // kind of confident wrong answer this codebase keeps having to undo.
  //
  // The level tiles are not illustrative at all: they draw the two dB figures
  // in config.MUSIC_LEVELS, which are the same numbers creatomate_service
  // turns into the ducking automation. What is on screen is what renders.
  const MOOD_SHAPES = {
    Energetic:     [3, 8, 5, 10, 4, 9, 6, 10, 5, 8],
    Corporate:     [5, 6, 5, 6, 5, 6, 5, 6, 5, 6],
    Inspirational: [2, 3, 4, 5, 6, 7, 8, 9, 10, 10],
    Fun:           [4, 9, 3, 8, 4, 9, 3, 8, 4, 9],
    Dramatic:      [1, 2, 3, 4, 6, 8, 10, 10, 7, 3],
    Luxury:        [4, 5, 6, 6, 7, 7, 6, 6, 5, 4],
    Country:       [5, 7, 6, 8, 6, 7, 5, 7, 6, 8],
    Rock:          [9, 3, 10, 2, 9, 4, 10, 3, 9, 2],
    Electronic:    [10, 10, 2, 2, 10, 10, 2, 2, 10, 10],
    Relaxed:       [3, 4, 3, 4, 3, 4, 3, 4, 3, 4],
  };

  function moodArt(mood) {
    const shape = MOOD_SHAPES[mood] || MOOD_SHAPES.Corporate;
    const bars = shape.map((h, i) => {
      const height = Math.max(2, h * 3);
      return `<rect x="${i * 11 + 2}" y="${(34 - height) / 2}" width="7" `
           + `height="${height}" rx="3" fill="currentColor"></rect>`;
    }).join("");
    return `<svg viewBox="0 0 120 34" preserveAspectRatio="none" aria-hidden="true">${bars}</svg>`;
  }

  // The bed, and the same bed under narration. Both bars are drawn from the
  // real dB values, mapped onto a -30..0 scale so the three tiles are
  // comparable with each other rather than each filling its own box.
  function levelArt(bedDb, duckedDb) {
    const pct = (db) => Math.max(4, Math.min(100, ((db + 30) / 30) * 100));
    return `<svg viewBox="0 0 120 52" preserveAspectRatio="none" aria-hidden="true">
      <rect x="0" y="6" width="${pct(bedDb)}" height="14" rx="7" fill="currentColor"
        opacity=".85"></rect>
      <rect x="0" y="30" width="${pct(duckedDb)}" height="14" rx="7" fill="currentColor"
        opacity=".38"></rect>
    </svg>`;
  }

  let musicMood = document.querySelector("#music-mood-choices .selected")?.dataset.value || "";
  let musicLevel = document.querySelector("#music-level-choices .selected")?.dataset.value
    || "Medium";

  document.querySelectorAll("#music-mood-choices .cb-mood").forEach((cell) => {
    cell.querySelector(".mood-art").innerHTML = moodArt(cell.dataset.value);
    cell.addEventListener("click", () => {
      document.querySelectorAll("#music-mood-choices .cb-mood")
        .forEach((c) => c.classList.remove("selected"));
      cell.classList.add("selected");
      musicMood = cell.dataset.value;
    });
  });

  document.querySelectorAll("#music-level-choices .cb-level").forEach((cell) => {
    cell.querySelector(".level-art").innerHTML =
      levelArt(parseFloat(cell.dataset.bed), parseFloat(cell.dataset.ducked));
    // The two bars need naming or they are just two bars.
    cell.querySelector(".level-art").insertAdjacentHTML("afterend",
      '<div class="cb-level-db" style="margin-top:2px;">top: on its own · '
      + "bottom: under the read</div>");
    cell.addEventListener("click", () => {
      document.querySelectorAll("#music-level-choices .cb-level")
        .forEach((c) => c.classList.remove("selected"));
      cell.classList.add("selected");
      musicLevel = cell.dataset.value;
    });
  });

  // Said out loud rather than left for somebody to discover at the render.
  // The mood is captured and no track is attached by anything in this Hub, so
  // claiming it will appear in the finished cut would be the Proposal
  // Builder's four unread discovery questions all over again.
  const moodNote = document.getElementById("music-mood-note");
  if (moodNote) {
    moodNote.textContent = "The waveforms illustrate the mood — they are not a track. "
      + "No music library is connected yet, so this is a brief for whoever picks the "
      + "bed, and the render carries no music until one is attached.";
  }

  document.getElementById("save-music-btn").addEventListener("click", async () => {
    const status = document.getElementById("music-status");
    try {
      await CB.api(`/api/projects/${projectId}/music`, {
        method: "PUT", body: { mood: musicMood, level: musicLevel },
      });
      status.textContent = musicMood
        ? `Saved — ${musicMood}, ${musicLevel}.`
        : `Saved — ${musicLevel}, no mood picked.`;
      CB.toast("Music selection saved.");
    } catch (e) { status.textContent = ""; }
  });

  loadCharacteristics();
  loadVoices();
  loadClient();
})();
