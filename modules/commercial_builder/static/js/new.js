(() => {
  const modeChoices = document.getElementById("client-mode");
  const clientSelect = document.getElementById("client-select");
  const chosenBox = document.getElementById("chosen-client");

  let mode = "hub";
  // The client this build is for, once one is settled on. `id` is a
  // cb_clients row; `name`/`url` are what a Hub client or a new business was
  // picked as, and are resolved into a row at submit time.
  let chosen = null;

  const selectedLengths = new Set();
  const selectedFormats = new Set();
  const selectedPublishers = new Set();
  let selectedType = null;
  let selectedPlatform =
    document.querySelector("#platform-choices .selected")?.dataset.value || "both";

  // ------------------------------------------------------------ mode switch
  function setMode(next) {
    mode = next;
    [...modeChoices.children].forEach((c) =>
      c.classList.toggle("selected", c.dataset.value === next));
    document.getElementById("mode-hub").style.display = next === "hub" ? "" : "none";
    document.getElementById("mode-profile").style.display = next === "profile" ? "" : "none";
    document.getElementById("mode-new").style.display = next === "new" ? "" : "none";
    clearChosen();
  }

  modeChoices.addEventListener("click", (e) => {
    const choice = e.target.closest(".cb-choice");
    if (choice) setMode(choice.dataset.value);
  });

  function clearChosen() {
    chosen = null;
    chosenBox.style.display = "none";
  }

  function showChosen(label, sub) {
    document.getElementById("chosen-name").textContent = label;
    document.getElementById("chosen-sub").textContent = sub || "";
    chosenBox.style.display = "flex";
  }

  document.getElementById("chosen-clear").addEventListener("click", () => {
    clearChosen();
    const q = document.getElementById("hub-q");
    q.value = "";
    document.getElementById("hub-results").innerHTML = "";
    q.focus();
  });

  // ------------------------------------------- the agency's own client list
  //
  // A searchable list of real clients, never a text box. hub/client_key.py
  // says at length why: a typed name that matches nothing files the work
  // under a client nothing joins to, and still reads as a clean success.
  const results = document.getElementById("hub-results");
  const hubNote = document.getElementById("hub-note");

  const runHubSearch = CB.debounce(async () => {
    const q = document.getElementById("hub-q").value.trim();
    if (q.length < 2) { results.innerHTML = ""; return; }
    let data;
    try {
      data = await CB.api(`/api/clients/hub-search?q=${encodeURIComponent(q)}`);
    } catch (e) { return; }
    if (!data.available) {
      // "No such client" and "we could not read the client list" send someone
      // to two different places, and only the first means "create as new".
      results.innerHTML = "";
      hubNote.textContent = data.note;
      return;
    }
    hubNote.textContent = "";
    results.innerHTML = "";
    if (!data.clients.length) {
      results.appendChild(CB.el(
        `<div class="cb-result"><em>No client matches "${CB.escapeHtml(q)}".</em>` +
        '<div class="cb-result-sub">If this is somebody we are pitching, choose ' +
        "<strong>New business</strong> above — a prospect has no client record yet, " +
        "and that is the normal case.</div></div>"));
      return;
    }
    data.clients.forEach((c) => {
      const bits = [c.domain, c.running ? `${c.running} running` : "", c.source]
        .filter(Boolean).join(" · ");
      const row = CB.el(
        `<div class="cb-result"><strong>${CB.escapeHtml(c.name)}</strong>` +
        `<div class="cb-result-sub">${CB.escapeHtml(bits || "on the client list")}</div></div>`);
      row.addEventListener("click", () => pickHubClient(c));
      results.appendChild(row);
    });
  }, 260);

  document.getElementById("hub-q").addEventListener("input", runHubSearch);

  async function pickHubClient(client) {
    results.innerHTML = "";
    document.getElementById("hub-q").value = client.name;
    let data;
    try {
      data = await CB.api("/api/clients/adopt", { method: "POST", body: { name: client.name } });
    } catch (e) { return; }
    chosen = { id: data.client.id, name: data.client.name };
    showChosen(data.client.name,
      (data.created ? "Brand profile created from the client record. "
                    : "Existing brand profile. ") + (data.note || ""));
  }

  // ---------------------------------------------------------- a new business
  document.getElementById("analyze-btn").addEventListener("click", async () => {
    const website = document.getElementById("nc-website").value.trim();
    if (!website) return CB.toast("Enter a website first.", true);
    const status = document.getElementById("analyze-status");
    status.textContent = "Analyzing…";
    try {
      const { profile, live } = await CB.api("/api/clients/analyze-website",
                                             { method: "POST", body: { website } });
      if (!document.getElementById("nc-name").value) {
        document.getElementById("nc-name").value = profile.business_name || "";
      }
      status.textContent = live
        ? "Pre-filled from the website. Review the rest of the brand profile after this step."
        : "AI analysis is running in mock mode (no OPENAI_API_KEY set) — filled a placeholder name only.";
    } catch (e) {
      status.textContent = "Couldn't analyze that site — you can still continue manually.";
    }
  });

  // ------------------------------------------------------------- the choices
  function wireChoices(containerId, onPick) {
    const container = document.getElementById(containerId);
    container.addEventListener("click", (e) => {
      const choice = e.target.closest(".cb-choice");
      if (!choice) return;
      onPick(choice, container);
    });
  }

  function toggle(set, choice) {
    const v = choice.dataset.value;
    if (set.has(v)) { set.delete(v); choice.classList.remove("selected"); }
    else { set.add(v); choice.classList.add("selected"); }
  }

  wireChoices("length-choices", (choice) => {
    toggle(selectedLengths, choice);
    refreshLengthNotes();
    refreshCost();
  });

  wireChoices("platform-choices", (choice, container) => {
    [...container.children].forEach((c) => c.classList.remove("selected"));
    choice.classList.add("selected");
    selectedPlatform = choice.dataset.value;
    syncPublisherField();
    refreshLengthNotes();
  });

  // The publisher question only means anything on a CTV buy — asking a
  // YouTube-only spot which streaming platform it runs on is a field nobody
  // can answer, and a form full of those is a form people stop reading.
  function syncPublisherField() {
    const field = document.getElementById("publisher-field");
    const relevant = selectedPlatform === "ctv" || selectedPlatform === "both";
    field.style.display = relevant ? "" : "none";
    if (!relevant) {
      selectedPublishers.clear();
      document.querySelectorAll("#publisher-choices .cb-choice")
        .forEach((c) => c.classList.remove("selected"));
      document.getElementById("publisher-notes").innerHTML = "";
    }
  }

  wireChoices("publisher-choices", (choice) => {
    toggle(selectedPublishers, choice);
    paintPublisherNotes();
  });

  // Said the moment it is picked, not at the render. A rep who switches a QR
  // code on for an Amazon buy has built something Amazon will reject.
  const PUBLISHER_NOTES = {
    amazon: "Amazon Streaming TV doesn't support QR codes, and its specs say ads "
          + "shouldn't include call-to-action elements that encourage clicking. "
          + "The CTA step will warn you if a code is switched on.",
  };

  function paintPublisherNotes() {
    const box = document.getElementById("publisher-notes");
    box.innerHTML = "";
    [...selectedPublishers].forEach((id) => {
      if (!PUBLISHER_NOTES[id]) return;
      box.appendChild(CB.el('<div class="cb-note"><strong>Worth knowing</strong><p>'
        + CB.escapeHtml(PUBLISHER_NOTES[id]) + "</p></div>"));
    });
  }

  wireChoices("format-choices", (choice) => {
    toggle(selectedFormats, choice);
    refreshLengthNotes();
    refreshCost();
  });

  wireChoices("type-choices", (choice, container) => {
    [...container.children].forEach((c) => c.classList.remove("selected"));
    choice.classList.add("selected");
    selectedType = choice.dataset.value;
    refreshCost();
  });

  // ------------------------------------------------- what it will consume
  //
  // Said while the choice is being made. The usage page answers "what did we
  // spend last month", which is the right question for a bill and the wrong
  // one for somebody about to tick three lengths — by the time a number
  // shows there the money is gone.
  const costBox = document.getElementById("cost-preview");

  const refreshCost = CB.debounce(async () => {
    if (!costBox) return;
    if (!selectedLengths.size) { costBox.innerHTML = ""; return; }
    let data;
    try {
      data = await CB.api(
        `/api/projects/cost-preview?lengths=${[...selectedLengths].join(",")}`
        + `&formats=${encodeURIComponent([...selectedFormats].join(","))}`
        + `&method=${encodeURIComponent(selectedType || "stock_vo")}`);
    } catch (e) { return; }
    const est = data.estimate;
    if (!est.measured || !est.rows.length) { costBox.innerHTML = ""; return; }

    const rows = est.rows.map((r) => {
      // "Not priced" said in words rather than left blank: a blank is a gap a
      // reader fills in with a guess, and this is a number people repeat.
      const cost = r.usd === null
        ? '<span class="cb-muted">not priced</span>'
        : `$${r.usd.toFixed(2)}`;
      return `<tr><td>${CB.escapeHtml(r.label)}</td>`
        + `<td style="text-align:right;">${r.units.toLocaleString()}</td>`
        + `<td class="cb-muted">${CB.escapeHtml(r.unit)}</td>`
        + `<td style="text-align:right;">${cost}</td></tr>`;
    }).join("");

    costBox.innerHTML = `<div class="cb-note" style="margin-top:10px;">
      <strong>What this will use</strong>
      <table class="cb-table" style="margin:8px 0;">
        <thead><tr><th>Provider</th><th style="text-align:right;">Count</th>
          <th>Unit</th><th style="text-align:right;">Cost</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p style="margin:0 0 4px;">${CB.escapeHtml(est.note)}</p>
      <p class="cb-hint" style="margin:0;">${CB.escapeHtml(est.caveat)}</p>
    </div>`;
  }, 300);

  // ------------------------------------------- what each length is going to
  // cost, and what the published spec says about running it on this buy.
  //
  // Both asked here, while the choice is being made. The spec check in
  // particular: the kit sells Connected TV at 15-30 seconds, so two of the
  // four lengths on this page are outside the buy for a CTV spot — which was
  // discoverable only by building one and having the platform refuse it.
  const notesBox = document.getElementById("length-notes");

  const refreshLengthNotes = CB.debounce(async () => {
    if (!selectedLengths.size) { notesBox.innerHTML = ""; return; }
    const lengths = [...selectedLengths].sort((a, b) => a - b).join(",");
    const formats = ([...selectedFormats].join(",")) || "16:9";
    let data;
    try {
      data = await CB.api(
        `/api/projects/spec-preview?platform=${encodeURIComponent(selectedPlatform)}` +
        `&lengths=${encodeURIComponent(lengths)}&formats=${encodeURIComponent(formats)}`);
    } catch (e) { return; }

    notesBox.innerHTML = "";
    data.lengths.forEach((row) => {
      if (row.cost_warning) {
        notesBox.appendChild(CB.el(
          `<div class="cb-note"><strong>:${String(row.length).padStart(2, "0")} — worth knowing</strong>` +
          `<p>${CB.escapeHtml(row.cost_warning)}</p></div>`));
      }
      if (!row.passed) {
        notesBox.appendChild(CB.el(
          `<div class="cb-note bad"><strong>:${String(row.length).padStart(2, "0")} — outside the published spec</strong>` +
          `<p>${CB.escapeHtml(row.message)}</p>` +
          "<p>You can still build it — a long cut is a real thing to want for a website " +
          "or a lobby screen. It just is not what this buy is sold in.</p></div>"));
      }
    });
  }, 300);

  // ------------------------------------------------------------------ submit
  document.getElementById("start-btn").addEventListener("click", async () => {
    if (!selectedLengths.size) return CB.toast("Choose at least one commercial length.", true);
    if (!selectedFormats.size) return CB.toast("Choose at least one output format.", true);
    if (!selectedType) return CB.toast("Choose a commercial type.", true);

    const btn = document.getElementById("start-btn");
    btn.disabled = true;
    try {
      const clientId = await resolveClientId();
      if (!clientId) return;

      const { projects, notes } = await CB.api("/api/projects", {
        method: "POST",
        body: {
          client_id: parseInt(clientId, 10),
          lengths: [...selectedLengths].map((l) => parseInt(l, 10)),
          formats: [...selectedFormats],
          commercial_type: selectedType,
          platform: selectedPlatform,
          publishers: [...selectedPublishers],
        },
      });
      if (projects.length > 1) {
        CB.toast(`${projects.length} commercials started — they share one concept.`);
      }
      // Straight into the first one's brief. The brief is shared across the
      // set, so it is written once.
      location.href = `${CB.API_ROOT}/project/${projects[0].id}/brief`;
    } finally {
      btn.disabled = false;
    }
  });

  async function resolveClientId() {
    if (mode === "profile") {
      const id = clientSelect.value;
      if (!id) { CB.toast("Choose a brand profile.", true); return null; }
      return id;
    }
    if (mode === "hub") {
      if (!chosen) {
        CB.toast("Search for the client and pick them from the list.", true);
        return null;
      }
      return chosen.id;
    }
    const name = document.getElementById("nc-name").value.trim();
    if (!name) { CB.toast("Enter the new business's name.", true); return null; }
    const website = document.getElementById("nc-website").value.trim();
    const { client } = await CB.api("/api/clients",
                                    { method: "POST", body: { name, website } });
    return client.id;
  }

  // Arriving from the dashboard's "New commercial" link on a client row: that
  // client already has a brand profile, so open on the profile list with it
  // chosen rather than making somebody search for it again.
  syncPublisherField();

  const params = new URLSearchParams(location.search);
  if (params.get("client_id")) {
    setMode("profile");
    clientSelect.value = params.get("client_id");
  }
})();
