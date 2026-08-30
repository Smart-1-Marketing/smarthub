(() => {
  const wrap = document.getElementById("concepts-wrap");
  const projectId = wrap.dataset.projectId;
  const scriptBtn = document.getElementById("script-btn");
  let selectedConceptId = null;

  function render(concepts) {
    wrap.innerHTML = "";
    concepts.forEach((c) => {
      const card = CB.el(`
        <div class="cb-card cb-choice" data-id="${c.id}" style="cursor:pointer;">
          <h3>${c.title}</h3>
          <div class="cb-badge cb-badge-mock" style="margin-bottom:8px;">${c.angle || ""}</div>
          <p style="margin:0;">${c.summary || ""}</p>
        </div>`);
      card.addEventListener("click", () => selectConcept(c.id, card));
      wrap.appendChild(card);
    });
  }

  async function selectConcept(id, card) {
    document.querySelectorAll("#concepts-wrap .cb-choice").forEach((c) => c.classList.remove("selected"));
    card.classList.add("selected");
    selectedConceptId = id;
    await CB.api(`/api/projects/${projectId}/select-concept`, { method: "POST", body: { concept_id: id } });
    scriptBtn.disabled = false;
  }

  async function loadConcepts(force) {
    if (!force) {
      const { project } = await CB.api(`/api/projects/${projectId}`);
      if (project.concepts && project.concepts.length) {
        render(project.concepts);
        if (project.selected_concept_id) {
          const card = wrap.querySelector(`[data-id="${project.selected_concept_id}"]`);
          if (card) { card.classList.add("selected"); selectedConceptId = project.selected_concept_id; scriptBtn.disabled = false; }
        }
        return;
      }
    }
    const { concepts, live } = await CB.api(`/api/projects/${projectId}/concepts`, { method: "POST" });
    render(concepts);
    if (!live) CB.toast("Concepts generated in mock mode (no OPENAI_API_KEY set).");
  }

  /* A model call long enough that a 14-pixel spinner reads as a page that has
     stopped. The panel draws what is happening; the animation itself sits
     behind a prefers-reduced-motion guard in the stylesheet. */
  function showWorking(title) {
    wrap.innerHTML = '<div style="grid-column:1/-1;">' + CB.working(
      "concepts", title,
      "Three materially different angles on the brief \u2014 not the same idea "
      + "reworded. This takes a few seconds.") + "</div>";
  }

  document.getElementById("regen-btn").addEventListener("click", () => {
    scriptBtn.disabled = true;
    showWorking("Rewriting the concepts\u2026");
    loadConcepts(true);
  });

  scriptBtn.addEventListener("click", async () => {
    scriptBtn.disabled = true;
    scriptBtn.innerHTML = '<span class="cb-spinner"></span> Writing script\u2026';
    showWorking("Writing the timed script\u2026");
    try {
      await CB.api(`/api/projects/${projectId}/script`, { method: "POST" });
      location.href = `${CB.API_ROOT}/project/${projectId}/storyboard`;
    } catch (e) {
      scriptBtn.disabled = false;
      scriptBtn.textContent = "Generate Script & Storyboard →";
    }
  });

  showWorking("Writing three concepts\u2026");
  loadConcepts(false);
})();
