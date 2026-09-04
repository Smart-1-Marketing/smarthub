/* Vox Explainer, standalone. Draft the beats, read them, then render.

   Two presses on purpose: a render is minutes of headless Chrome and the beat
   list is the only thing anybody can correct before it. The same "approve
   before spend" shape every expensive step in this Hub uses. */
(function () {
  "use strict";
  var draftBtn = document.getElementById("vox-draft");
  if (!draftBtn || !window.HF) return;

  var BASE = "/tools/vox-explainer";
  var TREATMENTS = HF.readData("hf-treatments-data");
  var KINDS = HF.readData("hf-kinds-data");

  var kindSel = document.getElementById("vox-kind");
  var kindHint = document.getElementById("vox-kind-hint");
  var textField = document.getElementById("vox-text-field");
  var linkField = document.getElementById("vox-link-field");
  var beatBox = document.getElementById("vox-beats");
  var durationNote = document.getElementById("vox-duration");
  var draftNote = document.getElementById("vox-draft-note");
  var waitBox = document.getElementById("vox-wait");
  var listBox = document.getElementById("vox-list");

  var beats = [];

  function syncKind() {
    var k = KINDS.filter(function (x) { return x.id === kindSel.value; })[0];
    kindHint.textContent = k ? k.hint : "";
    var isLink = kindSel.value === "link";
    linkField.style.display = isLink ? "" : "none";
    textField.style.display = isLink ? "none" : "";
  }
  kindSel.addEventListener("change", syncKind);
  syncKind();

  /* Read the open editors back into the model before any redraw. A container
     of live inputs rebuilt with innerHTML loses whatever was half-typed in
     it, which is the trap the SEO client page's two editors each had. */
  function harvest() {
    beatBox.querySelectorAll("[data-beat-index]").forEach(function (row) {
      var i = parseInt(row.dataset.beatIndex, 10);
      if (!beats[i]) return;
      beats[i].headline = row.querySelector(".b-head").value;
      beats[i].support = row.querySelector(".b-sup").value;
      beats[i].treatment = row.querySelector(".b-tre").value;
      beats[i].seconds = parseFloat(row.querySelector(".b-sec").value) || 0;
      beats[i].source = row.querySelector(".b-src").value;
    });
  }

  function total() {
    return beats.reduce(function (n, b) { return n + (parseFloat(b.seconds) || 0); }, 0);
  }

  function draw() {
    beatBox.innerHTML = "";
    if (!beats.length) {
      beatBox.appendChild(HF.el('<p class="muted">No beats yet.</p>'));
      durationNote.textContent = "";
      return;
    }
    beats.forEach(function (b, i) {
      var opts = TREATMENTS.map(function (t) {
        return '<option value="' + HF.esc(t.id) + '">' + HF.esc(t.label) + "</option>";
      }).join("");
      var row = HF.el('<div data-beat-index="' + i + '" style="border-bottom:'
        + '1px solid var(--line);padding:10px 0;">'
        + '<div style="display:flex;justify-content:space-between;">'
        + "<strong>Beat " + (i + 1) + "</strong>"
        + '<button class="btn-ghost b-del">Remove</button></div>'
        + '<div class="field"><label>Headline</label>'
        + '<input type="text" class="b-head"></div>'
        + '<div class="field"><label>Support</label>'
        + '<input type="text" class="b-sup"></div>'
        + '<div style="display:flex;gap:8px;flex-wrap:wrap;">'
        + '<div class="field"><label>Treatment</label>'
        + '<select class="b-tre">' + opts + "</select></div>"
        + '<div class="field"><label>Seconds</label>'
        + '<input type="number" step="0.5" min="0" class="b-sec" style="width:90px;"></div>'
        + '<div class="field" style="flex:1;"><label>Source</label>'
        + '<input type="text" class="b-src" placeholder="Needed for a quote"></div>'
        + "</div></div>");
      row.querySelector(".b-head").value = b.headline || "";
      row.querySelector(".b-sup").value = b.support || "";
      row.querySelector(".b-sec").value = b.seconds || 0;
      row.querySelector(".b-src").value = b.source || "";
      row.querySelector(".b-tre").value = b.treatment || (TREATMENTS[0] || {}).id || "";
      row.querySelector(".b-del").addEventListener("click", function () {
        harvest();
        beats.splice(i, 1);
        draw();
      });
      beatBox.appendChild(row);
    });
    /* Said as the honest arithmetic of what is on screen. The server
       rebalances into the window at render time and its answer is the one
       that counts — this is only so somebody editing can see where they are. */
    durationNote.textContent = total().toFixed(0) + "s of beats";
  }

  function dropped(box, rows, note) {
    box.innerHTML = "";
    if (note) box.appendChild(HF.el('<p class="muted">' + HF.esc(note) + "</p>"));
    (rows || []).forEach(function (d) {
      box.appendChild(HF.el('<p class="muted" style="font-size:12.5px;">Dropped: '
        + HF.esc(d.reason || "") + "</p>"));
    });
  }

  draftBtn.addEventListener("click", function () {
    draftBtn.disabled = true;
    HF.waiting(draftNote, "Reading the material and writing the beats…");
    HF.api(BASE + "/api/beats", {
      method: "POST",
      body: {
        title: document.getElementById("vox-title").value,
        source_kind: kindSel.value,
        source_text: document.getElementById("vox-text").value,
        link: document.getElementById("vox-link").value,
      },
    })
      .then(function (d) {
        beats = d.beats || [];
        draw();
        dropped(draftNote, d.dropped,
                d.source === "house"
                  ? "Built from your own text rather than written — " + (d.error || "")
                  : "");
      })
      .catch(function (e) { HF.fail(draftNote, e.message); })
      .then(function () { draftBtn.disabled = false; });
  });

  document.getElementById("vox-add").addEventListener("click", function () {
    harvest();
    beats.push({ headline: "", support: "",
                 treatment: (TREATMENTS[0] || {}).id || "",
                 seconds: 0, source: "", image_query: "" });
    draw();
  });

  function onKeep(job) {
    var name = (document.getElementById("vox-client").value || job.client || "").trim();
    if (!name) {
      HF.fail(waitBox, "Pick a client first — that is what filing it against "
                     + "them means.");
      return;
    }
    HF.api(BASE + "/api/render/" + encodeURIComponent(job.id) + "/keep",
           { method: "POST", body: { client: name } })
      .then(function (d) {
        waitBox.innerHTML = '<p class="muted">' + HF.esc(
          d.on_record
            ? "Filed against " + name + " and on their record."
            : "Stored in " + name + "'s library — but not on their record."
        ) + "</p>";
        refresh();
      })
      .catch(function (e) { HF.fail(waitBox, e.message); });
  }

  function onForget(job) {
    HF.api(BASE + "/api/render/" + encodeURIComponent(job.id), { method: "DELETE" })
      .then(refresh)
      .catch(function (e) { HF.fail(waitBox, e.message); });
  }

  function refresh() {
    return HF.api(BASE + "/api/renders")
      .then(function (d) {
        var jobs = d.jobs || [];
        HF.renderList(listBox, jobs, { onKeep: onKeep, onForget: onForget });
        jobs.forEach(function (j) {
          if (j.status !== "done" && j.status !== "failed") {
            HF.watch(BASE, j.id, function () { refresh(); });
          }
        });
      })
      .catch(function () { /* the empty state already says there is nothing */ });
  }

  document.getElementById("vox-go").addEventListener("click", function (e) {
    harvest();
    var btn = e.target;
    btn.disabled = true;
    HF.waiting(waitBox, "Rendering — a collage explainer is drawn frame by "
                      + "frame, so it takes a few minutes. You can leave this page.");
    HF.api(BASE + "/api/render", {
      method: "POST",
      body: {
        title: document.getElementById("vox-title").value,
        beats: beats,
        format: document.getElementById("vox-format").value,
        client: document.getElementById("vox-client").value,
      },
    })
      .then(function (d) {
        waitBox.innerHTML = '<p class="muted">Rendering. It will appear below '
          + "when it lands.</p>";
        refresh();
        HF.watch(BASE, (d.job || {}).id, function () { refresh(); });
      })
      .catch(function (err) { HF.fail(waitBox, err.message); })
      .then(function () { btn.disabled = false; });
  });

  HF.wireClientPicker();
  refresh();
})();
