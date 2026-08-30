(() => {
  const rowsBox = document.getElementById("lib-rows");
  const countBox = document.getElementById("lib-count");
  let filtersDrawn = false;

  // Filtering happens on the server so the counts are of the whole filtered
  // list rather than of whatever this page happened to have loaded — a page
  // reporting its own length as the total is how somebody concludes there are
  // twenty-five spots.
  const load = CB.debounce(async () => {
    const params = new URLSearchParams({
      q: document.getElementById("lib-q").value.trim(),
      length: document.getElementById("lib-length").value,
      format: document.getElementById("lib-format").value,
      archetype: document.getElementById("lib-archetype").value,
    });
    let data;
    try {
      data = await CB.api(`/api/projects/library?${params}`);
    } catch (e) {
      rowsBox.innerHTML = '<div class="cb-empty">The library could not be read.</div>';
      return;
    }
    drawFilters(data);
    paint(data);
  }, 220);

  function drawFilters(data) {
    if (filtersDrawn) return;
    filtersDrawn = true;
    const fill = (id, values, fmt) => {
      const el = document.getElementById(id);
      values.forEach((v) => {
        const opt = document.createElement("option");
        opt.value = v;
        opt.textContent = fmt ? fmt(v) : v;
        el.appendChild(opt);
      });
    };
    fill("lib-length", data.lengths, (v) => `:${String(v).padStart(2, "0")}`);
    fill("lib-format", data.formats);
  }

  function paint(data) {
    // "Showing N of M" where N is what was asked for and M is the whole
    // library. Both named, because a filtered list reporting an unfiltered
    // total is a wrong answer with two right ones either side of it.
    countBox.textContent = data.delivered_total
      ? `Showing ${data.total} of ${data.delivered_total} delivered spot(s).`
      : "";
    rowsBox.innerHTML = "";
    if (!data.spots.length) {
      rowsBox.innerHTML = `<div class="cb-empty">${
        CB.escapeHtml(data.note || "Nothing matches those filters.")}</div>`;
      return;
    }
    data.spots.forEach((s) => {
      const when = s.approved_at ? s.approved_at.slice(0, 10) : "";
      const card = CB.el(`<div class="cb-card" style="margin-bottom:10px;">
        <div class="cb-flex-between">
          <div>
            <strong>${CB.escapeHtml(s.client)}</strong>
            <span class="cb-muted"> · :${String(s.length_seconds).padStart(2, "0")}
              · ${CB.escapeHtml(s.format)}${
                s.archetype_label ? " · " + CB.escapeHtml(s.archetype_label) : ""}</span>
            <div class="cb-result-sub">${CB.escapeHtml(s.title || "Untitled")}${
              when ? " · approved " + when : ""}${
              s.approved_by ? " by " + CB.escapeHtml(s.approved_by) : ""}</div>
          </div>
          <div class="cb-actions"></div>
        </div>
      </div>`);
      const actions = card.querySelector(".cb-actions");
      // A missing stored copy is said rather than drawn as a dead link: the
      // only other copy is a provider URL that expires.
      if (s.url) {
        actions.appendChild(CB.el(`<a class="cb-btn cb-btn-sm" href="${s.url}"
          target="_blank" rel="noopener">Watch</a>`));
      } else {
        actions.appendChild(CB.el(`<span class="cb-badge cb-badge-premium"
          title="${CB.escapeHtml(s.url_note)}">no stored copy</span>`));
      }
      actions.appendChild(CB.el(`<a class="cb-btn cb-btn-sm"
        href="${CB.API_ROOT}/project/${s.project_id}/preview">Open</a>`));
      rowsBox.appendChild(card);
    });
  }

  ["lib-q", "lib-length", "lib-format", "lib-archetype"].forEach((id) => {
    const el = document.getElementById(id);
    el.addEventListener(id === "lib-q" ? "input" : "change", load);
  });

  load();
})();
