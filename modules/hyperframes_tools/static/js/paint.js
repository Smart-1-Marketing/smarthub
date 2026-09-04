/* Paint Animation, standalone. The form; everything else is HF. */
(function () {
  "use strict";
  var form = document.getElementById("paint-go");
  if (!form || !window.HF) return;

  var BASE = "/tools/paint-animation";
  var listBox = document.getElementById("paint-list");
  var waitBox = document.getElementById("paint-wait");
  var STYLES = HF.readData("hf-styles-data");

  var styleSel = document.getElementById("paint-style");
  var styleHint = document.getElementById("paint-style-hint");

  function syncStyleHint() {
    var s = STYLES.filter(function (x) { return x.id === styleSel.value; })[0];
    styleHint.textContent = s ? s.hint : "";
  }
  styleSel.addEventListener("change", syncStyleHint);
  syncStyleHint();

  function onKeep(job, row) {
    var input = document.getElementById("paint-client");
    var name = (input.value || job.client || "").trim();
    if (!name) {
      /* Said rather than the press doing nothing. The field is optional for
         making one and required for filing it, and those are different
         moments. */
      HF.fail(waitBox, "Pick a client first — that is what filing it against "
                     + "them means.");
      input.focus();
      return;
    }
    HF.api(BASE + "/api/render/" + encodeURIComponent(job.id) + "/keep",
           { method: "POST", body: { client: name } })
      .then(function (d) {
        /* Reported apart: stored, and on their record. */
        var said = d.on_record
          ? "Filed against " + name + " and on their record."
          : "Stored in " + name + "'s library — but not on their record.";
        waitBox.innerHTML = '<p class="muted">' + HF.esc(said) + "</p>";
        refresh();
      })
      .catch(function (e) { HF.fail(waitBox, e.message); });
  }

  function onForget(job) {
    HF.api(BASE + "/api/render/" + encodeURIComponent(job.id),
           { method: "DELETE" })
      .then(refresh)
      .catch(function (e) { HF.fail(waitBox, e.message); });
  }

  function refresh() {
    return HF.api(BASE + "/api/renders")
      .then(function (d) {
        var jobs = d.jobs || [];
        HF.renderList(listBox, jobs, { onKeep: onKeep, onForget: onForget });
        /* Restart the poll for anything still going. A render started an hour
           ago on another tab is picked up here rather than sitting on
           "Rendering…" for ever. */
        jobs.forEach(function (j) {
          if (j.status !== "done" && j.status !== "failed") {
            HF.watch(BASE, j.id, function () { refresh(); });
          }
        });
      })
      .catch(function () { /* the empty state already says there is nothing */ });
  }

  form.addEventListener("click", function () {
    form.disabled = true;
    HF.waiting(waitBox, "Rendering — a paint animation is drawn frame by frame, "
                      + "so it takes a few minutes. You can leave this page.");
    HF.api(BASE + "/api/render", {
      method: "POST",
      body: {
        style: styleSel.value,
        text: document.getElementById("paint-text").value,
        image_url: document.getElementById("paint-image").value,
        seconds: parseFloat(document.getElementById("paint-seconds").value) || 5,
        format: document.getElementById("paint-format").value,
        client: document.getElementById("paint-client").value,
      },
    })
      .then(function (d) {
        waitBox.innerHTML = '<p class="muted">Rendering. It will appear below '
          + "when it lands.</p>";
        refresh();
        HF.watch(BASE, (d.job || {}).id, function () { refresh(); });
      })
      .catch(function (e) { HF.fail(waitBox, e.message); })
      .then(function () { form.disabled = false; });
  });

  HF.wireClientPicker();
  refresh();
})();
