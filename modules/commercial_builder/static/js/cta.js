/* The CTA step — the last scene, and the only part of the spot that asks for
   anything.

   It used to be the fourth card down a page that already carried the scenes,
   the Voice Studio and Music, which meant the QR toggle — the switch that
   decides whether a CTV spot has a response mechanism at all — sat below
   everything else on the longest screen in the tool. */
(() => {
  const root = document.getElementById("cta-root");
  const projectId = root.dataset.projectId;

  let ctaStyle = document.querySelector("#cta-style-choices .selected")?.dataset.value
    || "logo_centered";

  document.getElementById("cta-style-choices").addEventListener("click", (e) => {
    const choice = e.target.closest(".cb-choice");
    if (!choice) return;
    document.querySelectorAll("#cta-style-choices .cb-choice")
      .forEach((c) => c.classList.remove("selected"));
    choice.classList.add("selected");
    ctaStyle = choice.dataset.value;
  });

  const phoneInput = document.getElementById("cta-phone"); // absent on :05 by design
  const qrEnabledInput = document.getElementById("cta-qr-enabled");
  const qrCornerInput = document.getElementById("cta-qr-corner");
  const logoPersistentInput = document.getElementById("cta-logo-persistent");
  const logoCornerInput = document.getElementById("cta-logo-corner");
  const planBox = document.getElementById("qr-plan");

  function syncCtaToggleVisibility() {
    const qrControls = document.getElementById("qr-controls");
    if (qrControls && qrEnabledInput) {
      qrControls.style.display = qrEnabledInput.checked ? "flex" : "none";
    }
    if (planBox && qrEnabledInput) {
      planBox.style.display = qrEnabledInput.checked ? "" : "none";
    }
    const logoControls = document.getElementById("logo-corner-controls");
    if (logoControls && logoPersistentInput) {
      logoControls.style.display = logoPersistentInput.checked ? "" : "none";
    }
  }
  if (qrEnabledInput) qrEnabledInput.addEventListener("change", syncCtaToggleVisibility);
  if (logoPersistentInput) logoPersistentInput.addEventListener("change", syncCtaToggleVisibility);

  // ------------------------------------------------- where the code points
  //
  // Neither half of this was ever on screen. The code was built from whatever
  // the landing page happened to be, with no way to see what it had resolved
  // to — and nothing at all about which Smart 1 Suite account would count the
  // scan, which for a client with their own sub-account is a different answer
  // from a business we are pitching.
  function renderPlan(plan) {
    if (!planBox || !plan) return;
    const attribution = plan.attribution || {};
    const bits = [];

    if (plan.missing) {
      // hub/qr_codes.py refuses to invent a destination. A code with nothing
      // behind it scans perfectly and opens nothing.
      planBox.className = "cb-note bad";
      bits.push(`<strong>This code has nowhere to send anyone</strong><p>${
        CB.escapeHtml(plan.missing)}</p>`);
    } else {
      planBox.className = "cb-note info";
      const source = {
        landing_page: "the campaign landing page on the brief",
        cta_website: "the website on this card",
        client_website: "the client's own website",
      }[plan.destination_source] || "the destination on file";
      bits.push(`<strong>Scanning this opens ${CB.escapeHtml(plan.destination_url)}</strong>`);
      bits.push(`<p>From ${CB.escapeHtml(source)}. Tracking is added to the link so scans `
        + "show up as their own source rather than as direct traffic.</p>");
    }

    // Tri-state, and the third state is the one that must not read as a tick:
    // "not measured" means nothing is counting scans anywhere.
    if (attribution.note) {
      bits.push(`<p>${CB.escapeHtml(attribution.note)}</p>`);
    }
    if (plan.provider_note) {
      bits.push(`<p class="cb-hint" style="margin:6px 0 0;">${
        CB.escapeHtml(plan.provider_note)}</p>`);
    }
    planBox.innerHTML = bits.join("");
  }

  async function loadPlan() {
    if (!planBox) return;
    try {
      const { plan } = await CB.api(`/api/projects/${projectId}/qr-plan`);
      renderPlan(plan);
    } catch (e) { /* CB.api has surfaced it */ }
  }

  // The destination is derived from the landing page and the website, so
  // typing a new website changes where the code points. Re-asking on change
  // means the panel and the code cannot disagree on screen.
  const websiteInput = document.getElementById("cta-website");
  if (websiteInput) websiteInput.addEventListener("change", loadPlan);

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
          website: websiteInput ? websiteInput.value.trim() : "",
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
      renderPlan({
        destination_url: cta.qr_destination_url,
        destination_source: cta.qr_destination_source,
        missing: cta.qr_missing,
        attribution: cta.qr_attribution,
        provider_note: cta.qr_provider_note,
      });
      CB.toast("CTA saved.");
    } finally {
      btn.disabled = false;
    }
  });

  syncCtaToggleVisibility();
  loadPlan();
})();
