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

  function briefPayload() {
    return {
      what_advertising: document.getElementById("f-what").value.trim(),
      primary_cta: document.getElementById("f-cta").value.trim(),
      landing_page: document.getElementById("f-landing").value.trim(),
      phone: document.getElementById("f-phone").value.trim(),
      target_audience: document.getElementById("f-audience").value.trim(),
      tone: selectedTone,
    };
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
