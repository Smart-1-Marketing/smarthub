(() => {
  const form = document.getElementById("brief-form");
  const projectId = form.dataset.projectId;
  let selectedTone = document.querySelector("#tone-choices .selected")?.dataset.value || "";

  document.getElementById("tone-choices").addEventListener("click", (e) => {
    const choice = e.target.closest(".cb-choice");
    if (!choice) return;
    document.querySelectorAll("#tone-choices .cb-choice").forEach((c) => c.classList.remove("selected"));
    choice.classList.add("selected");
    selectedTone = choice.dataset.value;
  });

  // ------------------------------------------------------------- archetype
  //
  // What the spot IS. Picking one draws what it needs from the client, on the
  // same screen — a testimonial needs a customer who has agreed, and a
  // before-and-after needs the before, which nobody photographs because at the
  // time it was just a Tuesday. Discovering that at the shoot is a launch date.
  const ARCHETYPE_NEEDS = JSON.parse(
    document.getElementById("archetype-needs").textContent || "{}");
  const SUGGESTED = JSON.parse(
    document.getElementById("archetype-suggested-data").textContent || "{}");

  let selectedArchetype =
    document.querySelector("#archetype-choices .selected")?.dataset.value || "";

  function drawNeeds() {
    const box = document.getElementById("archetype-detail");
    const needs = ARCHETYPE_NEEDS[selectedArchetype] || [];
    // Redrawn only when the ARCHETYPE changes, never on a keystroke: a
    // container that re-renders while somebody is typing into it eats what
    // they typed, which is the trap the Smart 1 Ads target-area rows had.
    box.innerHTML = "";
    if (!needs.length) return;
    needs.forEach((need) => {
      const id = "need-" + need.key;
      const prior = (PRIOR[need.key] || "");
      box.appendChild(CB.el(`<div class="cb-field">
        <label class="cb-label">${CB.escapeHtml(need.question)}</label>
        <input type="text" id="${id}" value="${CB.escapeHtml(prior)}">
        <p class="cb-hint" style="margin:4px 0 0;">${CB.escapeHtml(need.why)}</p>
      </div>`));
    });
  }

  const PRIOR = JSON.parse(
    document.getElementById("archetype-answers").textContent || "{}");

  function paintSuggested() {
    const note = document.getElementById("archetype-suggested");
    if (!note) return;
    // A suggestion and never a filter: an unusual spot for a category is often
    // the reason it works, and a picker that hides nine of twelve makes that
    // impossible.
    if (SUGGESTED.state !== "matched" || !(SUGGESTED.labels || []).length) {
      note.textContent = SUGGESTED.state === "not_recorded"
        ? "This client has no industry on file, so nothing is suggested — all "
          + "twelve are offered."
        : "";
      return;
    }
    note.textContent = "Common in this category: " + SUGGESTED.labels.join(", ")
      + ". All twelve are offered — an unusual choice is often the reason a "
      + "spot works.";
  }

  const grid = document.getElementById("archetype-choices");
  if (grid) {
    grid.addEventListener("click", (e) => {
      const choice = e.target.closest(".cb-choice");
      if (!choice) return;
      grid.querySelectorAll(".cb-choice").forEach((c) => c.classList.remove("selected"));
      choice.classList.add("selected");
      selectedArchetype = choice.dataset.value;
      drawNeeds();
    });
    paintSuggested();
    drawNeeds();
  }

  function briefPayload() {
    const payload = {
      what_advertising: document.getElementById("f-what").value.trim(),
      primary_cta: document.getElementById("f-cta").value.trim(),
      landing_page: document.getElementById("f-landing").value.trim(),
      phone: document.getElementById("f-phone").value.trim(),
      target_audience: document.getElementById("f-audience").value.trim(),
      tone: selectedTone,
      archetype: selectedArchetype,
    };
    // Only what the CHOSEN archetype asks for. Sending every key would write
    // an empty string over an answer given under a different archetype, and a
    // rep who switches back would find their own words gone.
    (ARCHETYPE_NEEDS[selectedArchetype] || []).forEach((need) => {
      const el = document.getElementById("need-" + need.key);
      if (el) payload[need.key] = el.value.trim();
    });
    return payload;
  }

  async function saveBrief() {
    if (!document.getElementById("f-what").value.trim()) {
      CB.toast("Describe what you're advertising first.", true);
      return false;
    }
    await CB.api(`/api/projects/${projectId}/brief`, { method: "PUT", body: briefPayload() });
    return true;
  }

  document.getElementById("save-btn").addEventListener("click", async () => {
    if (await saveBrief()) CB.toast("Brief saved.");
  });

  document.getElementById("concepts-btn").addEventListener("click", async () => {
    if (!(await saveBrief())) return;
    location.href = `${CB.API_ROOT}/project/${projectId}/concepts`;
  });
})();
