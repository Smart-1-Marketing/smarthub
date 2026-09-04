/* Shared by both standalone HyperFrames tools.

   The poll, the render list and the client picker are identical for a paint
   animation and a Vox explainer; only the form above them differs. Written
   once for the reason `hub/storage.py` exists — the next fix to the poll
   should land in one place rather than in two files that have drifted. */
window.HF = (function () {
  "use strict";

  var POLL_MS = 5000;
  var polling = {};

  function esc(value) {
    var d = document.createElement("div");
    d.textContent = value == null ? "" : String(value);
    return d.innerHTML;
  }

  function el(html) {
    var d = document.createElement("div");
    d.innerHTML = html.trim();
    return d.firstElementChild;
  }

  function readData(id) {
    var node = document.getElementById(id);
    if (!node) return [];
    try { return JSON.parse(node.textContent) || []; } catch (e) { return []; }
  }

  /* Every call answers with a body, including the failures — the routes are
     written that way so a refusal is a sentence rather than a status code
     nobody can act on. */
  async function api(path, opts) {
    var o = opts || {};
    var init = { method: o.method || "GET", headers: {} };
    if (o.body) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(o.body);
    }
    var res = await fetch(path, init);
    var data = {};
    try { data = await res.json(); } catch (e) { data = {}; }
    if (!data.ok && data.error) throw new Error(data.error);
    if (!res.ok && !data.error) throw new Error("That did not work.");
    return data;
  }

  function fail(box, message) {
    box.innerHTML = '<p class="muted" style="color:var(--red,#c0392b)">'
      + esc(message) + "</p>";
  }

  /* The mark says a render is happening and never that it finished: whether
     it succeeded is the caller's answer, and a tick over a failed one is the
     confident wrong answer this codebase keeps undoing. */
  function waiting(box, message) {
    box.innerHTML = '<span class="spin" data-s1-think="wait"></span> '
      + '<span>' + esc(message) + "</span>";
  }

  /* One job's row, with what it actually is: a link while it is only on the
     render service, and a note saying that file does not last. */
  function jobRow(job, opts) {
    var o = opts || {};
    var filed = job.filed || {};
    var bits = [];
    if (job.status === "done" && job.url) {
      bits.push('<a class="btn-ghost" href="' + esc(job.url)
        + '" target="_blank" rel="noopener">Open</a>');
      if (!filed.url) {
        bits.push('<button class="btn-ghost hf-keep">Attach to a client</button>');
      }
    }
    bits.push('<button class="btn-ghost hf-forget">Remove</button>');

    var state = job.status === "done"
      ? (filed.url
          ? "Filed against " + esc(job.client)
          : (job.url ? "Ready" : "Finished with no file"))
      : (job.status === "failed"
          ? "Failed: " + esc(job.error || "no reason given")
          : "Rendering…");

    var note = "";
    if (job.mock) {
      note = "The render service is not configured, so no file was produced.";
    } else if (job.status === "done" && job.url && !filed.url) {
      note = "This link is on the render service and will not last. "
           + "Attach it to a client to keep it.";
    } else if (filed.url && filed.logged === false) {
      /* Reported apart on purpose: stored and on-their-record are different
         outcomes, and one tick over both is how somebody learns not to trust
         the tick. */
      note = "Stored in their library, but the activity log write failed — "
           + "so it is not on their 360 record.";
    } else if (filed.error) {
      note = filed.error;
    }

    var row = el('<div class="hf-job" style="border-bottom:1px solid var(--line);'
      + 'padding:10px 0;">'
      + '<div><strong>' + esc(job.label || job.id) + "</strong> "
      + '<span class="muted">— ' + state + "</span></div>"
      + (note ? '<p class="muted" style="margin:4px 0;font-size:12.5px;">'
                + esc(note) + "</p>" : "")
      + '<div class="hf-actions" style="display:flex;gap:8px;margin-top:6px;">'
      + bits.join("") + "</div></div>");

    var keep = row.querySelector(".hf-keep");
    if (keep) keep.addEventListener("click", function () { o.onKeep(job, row); });
    row.querySelector(".hf-forget")
      .addEventListener("click", function () { o.onForget(job); });
    return row;
  }

  function renderList(box, jobs, opts) {
    box.innerHTML = "";
    if (!jobs.length) {
      box.appendChild(el('<p class="muted">Nothing yet.</p>'));
      return;
    }
    jobs.forEach(function (j) { box.appendChild(jobRow(j, opts)); });
  }

  /* A render takes minutes, so the poll has to survive a page load rather
     than living in the press that started it. Keyed by job id, restarted
     from the list render, so re-opening the page picks up a job started an
     hour ago instead of leaving a row permanently "Rendering…". */
  function watch(base, jobId, onSettled) {
    if (polling[jobId]) return;
    polling[jobId] = true;
    (function tick() {
      api(base + "/api/render/" + encodeURIComponent(jobId))
        .then(function (d) { return d.job || {}; })
        .catch(function () { return { status: "rendering" }; })
        .then(function (job) {
          if (job.status !== "done" && job.status !== "failed") {
            setTimeout(tick, POLL_MS);
            return;
          }
          delete polling[jobId];
          onSettled(job);
        });
    })();
  }

  /* The client picker is a list of real clients and never a free-text box
     that is taken at face value: a typo'd name files a render under a client
     nothing joins to and reads as a clean success. The server refuses an
     unknown name by name as well — this only makes the right answer easy.

     A typeahead rather than a preloaded list: `/api/clients/search` answers
     a page at a time and this book is several hundred businesses, so a
     datalist filled once would be the first dozen presented as the whole of
     it — the truncation `connection_choices()` already paid for one form up. */
  function wireClientPicker() {
    var input = document.querySelector('input[list="hf-client-list"]');
    var list = document.getElementById("hf-client-list");
    if (!input || !list) return;
    var timer = null;
    input.addEventListener("input", function () {
      clearTimeout(timer);
      var q = input.value.trim();
      if (q.length < 2) { list.innerHTML = ""; return; }
      timer = setTimeout(function () {
        api("/api/clients/search?limit=25&q=" + encodeURIComponent(q))
          .then(function (d) {
            list.innerHTML = (d.clients || []).map(function (c) {
              return '<option value="' + esc(c.name || c) + '">';
            }).join("");
          })
          .catch(function () {
            /* A picker that could not be filled still takes a typed name, and
               the server is what refuses an unknown one. Silent rather than an
               error on a field that is optional anyway. */
          });
      }, 220);
    });
  }

  return { api: api, el: el, esc: esc, fail: fail, waiting: waiting,
           renderList: renderList, watch: watch, readData: readData,
           wireClientPicker: wireClientPicker, POLL_MS: POLL_MS };
})();
