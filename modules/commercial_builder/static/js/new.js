(() => {
  const clientSelect = document.getElementById("client-select");
  const newClientFields = document.getElementById("new-client-fields");
  let selectedLength = null, selectedFormats = new Set(), selectedType = null;
  let selectedPlatform = document.querySelector("#platform-choices .selected")?.dataset.value || "both";

  const LENGTH_HINTS = {
    5: "Bumper: pure brand recall. No story, no phone number, no QR — just the hero shot, logo, and one value prop.",
    15: "One distinct message, one CTA. Hook (0-3s) → product/benefit (3-10s) → end card with logo/URL/phone/QR (10-15s). Script under 35 words.",
    30: "The industry standard. Hook (0-5s) → value (5-20s) → close with a persistent end card + spoken \"scan the code or call us today\" (20-30s).",
    60: "Deep dive — reserve for YouTube or high-engagement CTV/OTT. Establish (0-10s) → narrative/build (10-40s) → close/end-slate (40-60s).",
  };

  // pre-select client if arriving from dashboard "New commercial" link
  const params = new URLSearchParams(location.search);
  if (params.get("client_id")) clientSelect.value = params.get("client_id");

  clientSelect.addEventListener("change", () => {
    newClientFields.style.display = clientSelect.value === "__new__" ? "block" : "none";
  });

  document.getElementById("analyze-btn").addEventListener("click", async () => {
    const website = document.getElementById("nc-website").value.trim();
    if (!website) return CB.toast("Enter a website first.", true);
    const status = document.getElementById("analyze-status");
    status.textContent = "Analyzing…";
    try {
      const { profile, live } = await CB.api("/api/clients/analyze-website", { method: "POST", body: { website } });
      if (!document.getElementById("nc-name").value) document.getElementById("nc-name").value = profile.business_name || "";
      status.textContent = live ? "Pre-filled from the website. Review the rest of the brand profile after this step." :
        "AI analysis is running in mock mode (no OPENAI_API_KEY set) — filled a placeholder name only.";
    } catch (e) { status.textContent = "Couldn't analyze that site — you can still continue manually."; }
  });

  function wireChoices(containerId, onPick) {
    const container = document.getElementById(containerId);
    container.addEventListener("click", (e) => {
      const choice = e.target.closest(".cb-choice");
      if (!choice) return;
      onPick(choice, container);
    });
  }

  wireChoices("length-choices", (choice, container) => {
    [...container.children].forEach((c) => c.classList.remove("selected"));
    choice.classList.add("selected");
    selectedLength = parseInt(choice.dataset.value, 10);
    document.getElementById("length-hint").textContent = LENGTH_HINTS[selectedLength] || "";
  });

  wireChoices("platform-choices", (choice, container) => {
    [...container.children].forEach((c) => c.classList.remove("selected"));
    choice.classList.add("selected");
    selectedPlatform = choice.dataset.value;
  });

  wireChoices("format-choices", (choice) => {
    const v = choice.dataset.value;
    if (selectedFormats.has(v)) { selectedFormats.delete(v); choice.classList.remove("selected"); }
    else { selectedFormats.add(v); choice.classList.add("selected"); }
  });

  wireChoices("type-choices", (choice, container) => {
    [...container.children].forEach((c) => c.classList.remove("selected"));
    choice.classList.add("selected");
    selectedType = choice.dataset.value;
  });

  document.getElementById("start-btn").addEventListener("click", async () => {
    if (!selectedLength) return CB.toast("Choose a commercial length.", true);
    if (selectedFormats.size === 0) return CB.toast("Choose at least one output format.", true);
    if (!selectedType) return CB.toast("Choose a commercial type.", true);

    let clientId = clientSelect.value;
    if (!clientId) return CB.toast("Choose or create a client.", true);

    if (clientId === "__new__") {
      const name = document.getElementById("nc-name").value.trim();
      if (!name) return CB.toast("Enter the new client's business name.", true);
      const website = document.getElementById("nc-website").value.trim();
      const { client } = await CB.api("/api/clients", { method: "POST", body: { name, website } });
      clientId = client.id;
    }

    const { project } = await CB.api("/api/projects", {
      method: "POST",
      body: {
        client_id: parseInt(clientId, 10), length_seconds: selectedLength,
        formats: [...selectedFormats], commercial_type: selectedType, platform: selectedPlatform,
      },
    });
    location.href = `${CB.API_ROOT}/project/${project.id}/brief`;
  });
})();
