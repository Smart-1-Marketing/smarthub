/* Smart 1 Proposal Builder — frontend */
"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let TOKEN = sessionStorage.getItem("s1pb_token") || "";
let CONFIG = { industries: [] };
let STATE = {
  view: "dash",
  industry: "",
  blocks: [],            // working block model
  proposalId: "",        // set when editing an existing proposal
  isExisting: false,
  customer: {},
  industryLabel: "",
};

const ICONS = { sun: "🌞", chart: "📊", boat: "⛵", scales: "⚖️", people: "👥", plate: "🍽️", campfire: "🏕️", mountain: "🏔️", football: "🏈" };
const LOADER_MSGS = ["Analyzing the market…", "Reviewing package options…", "Writing the proposal…", "Formatting the blocks…"];

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", "x-auth": TOKEN, ...(opts.headers || {}) } });
  if (res.status === 401 && path !== "/sales/proposals/api/login") { showLogin(); throw new Error("Signed out"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

/* ---------- auth ---------- */
function showLogin() { $("#viewLogin").classList.remove("hidden"); $("#app").classList.add("hidden"); }
async function login() {
  try {
    const data = await api("/sales/proposals/api/login", { method: "POST", body: JSON.stringify({ password: $("#pw").value }) });
    TOKEN = data.token; sessionStorage.setItem("s1pb_token", TOKEN);
    await boot();
  } catch (e) { $("#loginErr").textContent = e.message; }
}
$("#loginBtn").addEventListener("click", login);
$("#pw").addEventListener("keydown", (e) => { if (e.key === "Enter") login(); });

async function boot() {
  try {
    CONFIG = await api("/sales/proposals/api/config");
  } catch { return showLogin(); }
  $("#viewLogin").classList.add("hidden"); $("#app").classList.remove("hidden");
  const sel = $("#fIndustry");
  sel.innerHTML = '<option value="">All industries</option>' + CONFIG.industries.map((i) => `<option value="${i.key}">${esc(i.label)}</option>`).join("");
  $("#indGrid").innerHTML = CONFIG.industries.map((i) => `<div class="ind-card" data-key="${i.key}"><span class="ic">${ICONS[i.icon] || "📈"}</span>${esc(i.label)}</div>`).join("");
  $$("#indGrid .ind-card").forEach((el) => el.addEventListener("click", () => {
    $$("#indGrid .ind-card").forEach((x) => x.classList.remove("sel"));
    el.classList.add("sel"); STATE.industry = el.dataset.key;
  }));
  show("dash"); loadList();
}

/* ---------- views ---------- */
function show(view) {
  STATE.view = view;
  ["Dash", "New", "Edit"].forEach((v) => $("#view" + v).classList.toggle("hidden", view.toLowerCase() !== v.toLowerCase()));
  window.scrollTo(0, 0);
}
$("#navDash").addEventListener("click", () => { show("dash"); loadList(); });
$("#navNew").addEventListener("click", () => { resetForm(); show("new"); });

/* ---------- dashboard ---------- */
let listTimer;
async function loadList() {
  const q = $("#q").value.trim(), industry = $("#fIndustry").value;
  try {
    const data = await api(`/sales/proposals/api/proposals?q=${encodeURIComponent(q)}&industry=${encodeURIComponent(industry)}`);
    const rows = data.results || [];
    $("#listEmpty").classList.toggle("hidden", rows.length > 0);
    $("#list").innerHTML = rows.map((r) => `
      <div class="pcard" data-id="${r.id}">
        <div style="flex:1">
          <div class="pb">${esc(r.business || "(no name)")}</div>
          <div class="pm">${esc(r.contact || "")}${r.contact ? " · " : ""}${esc(r.salesperson ? "rep: " + r.salesperson : "")} · updated ${new Date(r.updated_at).toLocaleDateString()} · v${r.versions}</div>
        </div>
        <span class="tag">${esc(r.industry_label)}</span>
        ${r.monthly ? `<span class="val">$${Number(r.monthly).toLocaleString()}/mo</span>` : ""}
      </div>`).join("");
    $$("#list .pcard").forEach((el) => el.addEventListener("click", () => openProposal(el.dataset.id)));
  } catch (e) { toast(e.message, true); }
}
$("#q").addEventListener("input", () => { clearTimeout(listTimer); listTimer = setTimeout(loadList, 250); });
$("#fIndustry").addEventListener("change", loadList);

/* ---------- new proposal ---------- */
const C_FIELDS = ["business_name", "website", "contact_name", "contact_email", "contact_phone", "zip", "city", "state", "monthly_budget", "salesperson", "goals"];
function resetForm() {
  STATE = { ...STATE, industry: "", blocks: [], proposalId: "", isExisting: false, customer: {} };
  $$("#indGrid .ind-card").forEach((x) => x.classList.remove("sel"));
  C_FIELDS.forEach((f) => { const el = $("#c_" + f); if (el) el.value = ""; });
  const rep = localStorage.getItem("s1pb_rep"); if (rep) $("#c_salesperson").value = rep;
  $("#genErr").textContent = "";
}
function readCustomer() {
  const c = {};
  C_FIELDS.forEach((f) => { const el = $("#c_" + f); if (el) c[f] = el.value.trim(); });
  return c;
}
$("#genBtn").addEventListener("click", async () => {
  const c = readCustomer();
  $("#genErr").textContent = "";
  if (!STATE.industry) return ($("#genErr").textContent = "Pick an industry first.");
  if (!c.business_name) return ($("#genErr").textContent = "Business name is required.");
  if (c.salesperson) localStorage.setItem("s1pb_rep", c.salesperson);
  loader(true);
  try {
    const data = await api("/sales/proposals/api/generate", { method: "POST", body: JSON.stringify({ industry: STATE.industry, customer: c }) });
    STATE.blocks = data.blocks; STATE.customer = c; STATE.industryLabel = data.industry_label;
    STATE.proposalId = ""; STATE.isExisting = false;
    openEditor(`${c.business_name} — new proposal`, data.engine === "ai" ? "AI-generated draft" : "Template draft (AI unavailable)");
  } catch (e) { $("#genErr").textContent = e.message; }
  loader(false);
});

/* ---------- open existing ---------- */
async function openProposal(id) {
  loader(true, "Opening proposal…");
  try {
    const { proposal } = await api("/sales/proposals/api/proposals/" + id);
    STATE.blocks = proposal.blocks || [];
    STATE.customer = proposal.customer || {};
    STATE.industry = proposal.industry;
    STATE.industryLabel = proposal.industry_label;
    STATE.proposalId = proposal.id;
    STATE.isExisting = true;
    openEditor(`${STATE.customer.business_name || "Proposal"}`, `${proposal.industry_label} · v${(proposal.versions || []).length} · last saved ${new Date(proposal.updated_at).toLocaleString()}`);
  } catch (e) { toast(e.message, true); }
  loader(false);
}

/* ---------- editor ---------- */
function openEditor(title, meta) {
  $("#editTitle").textContent = title;
  $("#editMeta").textContent = meta || "";
  $("#saveBtn").textContent = STATE.isExisting ? "Update proposal" : "Save proposal";
  $("#saveNewBtn").classList.toggle("hidden", !STATE.isExisting);
  $("#saveNote").classList.add("hidden");
  renderCanvas();
  show("edit");
}

function renderCanvas() {
  const c = $("#canvas");
  c.innerHTML = STATE.blocks.map((b, i) => blockHtml(b, i)).join("") +
    `<div class="paper-foot">Smart 1 Marketing · (614) 536-0768 · smart1marketing.com</div>`;
  bindBlockEvents();
}

const CE = 'contenteditable="true" spellcheck="true"';
function blockHtml(b, i) {
  const d = b.data || {};
  let inner = "";
  switch (b.type) {
    case "heading":
      inner = `<div class="b-title" ${CE} data-f="title">${esc(d.title)}</div>
               <div class="b-subtitle" ${CE} data-f="subtitle">${esc(d.subtitle)}</div><div class="b-rule"></div>`;
      break;
    case "text":
      inner = `<div class="b-h" ${CE} data-f="heading">${esc(d.heading)}</div>
               <div class="b-body" ${CE} data-f="body">${esc(d.body)}</div>`;
      break;
    case "stats":
      inner = `<div class="b-h" ${CE} data-f="heading">${esc(d.heading || "")}</div>
        <div class="b-stats">${(d.items || []).map((it, j) => `
          <div class="b-stat"><div class="v" ${CE} data-f="items.${j}.value">${esc(it.value)}</div>
          <div class="l" ${CE} data-f="items.${j}.label">${esc(it.label)}</div></div>`).join("")}</div>`;
      break;
    case "list":
      inner = `<div class="b-h" ${CE} data-f="heading">${esc(d.heading || "")}</div>
        <ul class="b-list">${(d.items || []).map((it, j) => `<li><button class="li-x" data-li="${j}" title="Remove item">✕</button><span ${CE} data-f="items.${j}">${esc(typeof it === "string" ? it : (it.label + ": " + it.value))}</span></li>`).join("")}</ul>
        <button class="add-li" data-addli="1">+ Add item</button>`;
      break;
    case "tiers":
      inner = `<div class="b-h" ${CE} data-f="heading">${esc(d.heading || "Investment Options")}</div>
        <div class="b-tiers">${(d.tiers || []).map((t, j) => `
          <div class="b-tier${Number(d.recommended) === j ? " rec" : ""}">
            <span class="rec-badge" data-rec="${j}" title="Click to mark recommended">${Number(d.recommended) === j ? "RECOMMENDED" : "MARK RECOMMENDED"}</span>
            <div class="tn" ${CE} data-f="tiers.${j}.name">${esc(t.name)}</div>
            <div class="tp" ${CE} data-f="tiers.${j}.price">${esc(t.price)}</div>
            <div class="tb" ${CE} data-f="tiers.${j}.blurb">${esc(t.blurb)}</div>
            <ul>${(t.features || []).map((f, k) => `<li ${CE} data-f="tiers.${j}.features.${k}">${esc(f)}</li>`).join("")}</ul>
          </div>`).join("")}</div>`;
      break;
    case "table":
      inner = `<div class="b-h" ${CE} data-f="heading">${esc(d.heading || "")}</div>
        <div class="b-tablewrap"><table class="b-table">
        <thead><tr>${(d.columns || []).map((col, j) => `<th ${CE} data-f="columns.${j}">${esc(col)}</th>`).join("")}</tr></thead>
        <tbody>${(d.rows || []).map((r, rj) => `<tr>${r.map((cell, cj) => `<td ${CE} data-f="rows.${rj}.${cj}">${esc(cell)}</td>`).join("")}</tr>`).join("")}</tbody>
        </table></div><button class="add-li" data-addrow="1">+ Add row</button>`;
      break;
    case "cta":
      inner = `<div class="b-cta"><div style="flex:1">
          <div class="ch" ${CE} data-f="heading">${esc(d.heading)}</div>
          <div class="cb" ${CE} data-f="body">${esc(d.body)}</div>
          <div class="curl" ${CE} data-f="url">${esc(d.url || "")}</div></div>
          <div class="cbtn" ${CE} data-f="button">${esc(d.button || "Get started")}</div></div>`;
      break;
    default:
      inner = `<div class="b-body" ${CE} data-f="body">${esc(d.body || "")}</div>`;
  }
  return `<div class="blk" data-i="${i}" data-type="${b.type}">
    <div class="blk-tools">
      <button class="up" title="Move up">↑</button>
      <button class="down" title="Move down">↓</button>
      <button class="del" title="Delete block">✕</button>
    </div>${inner}</div>`;
}

function setDeep(obj, pathStr, value) {
  const parts = pathStr.split(".");
  let o = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const k = /^\d+$/.test(parts[i]) ? Number(parts[i]) : parts[i];
    if (o[k] == null) o[k] = /^\d+$/.test(parts[i + 1]) ? [] : {};
    o = o[k];
  }
  const last = parts[parts.length - 1];
  o[/^\d+$/.test(last) ? Number(last) : last] = value;
}

function bindBlockEvents() {
  $$("#canvas .blk").forEach((el) => {
    const i = Number(el.dataset.i);
    // editable fields -> write back into model on input
    el.querySelectorAll("[contenteditable]").forEach((ce) => {
      ce.addEventListener("input", () => setDeep(STATE.blocks[i].data, ce.dataset.f, ce.innerText.replace(/ /g, " ")));
    });
    el.querySelector(".del").addEventListener("click", () => {
      if (!confirm("Delete this block from the proposal?")) return;
      STATE.blocks.splice(i, 1); renderCanvas();
    });
    el.querySelector(".up").addEventListener("click", () => {
      if (i === 0) return; [STATE.blocks[i - 1], STATE.blocks[i]] = [STATE.blocks[i], STATE.blocks[i - 1]]; renderCanvas();
    });
    el.querySelector(".down").addEventListener("click", () => {
      if (i >= STATE.blocks.length - 1) return; [STATE.blocks[i + 1], STATE.blocks[i]] = [STATE.blocks[i], STATE.blocks[i + 1]]; renderCanvas();
    });
    el.querySelectorAll(".li-x").forEach((x) => x.addEventListener("click", () => {
      STATE.blocks[i].data.items.splice(Number(x.dataset.li), 1); renderCanvas();
    }));
    const addLi = el.querySelector("[data-addli]");
    if (addLi) addLi.addEventListener("click", () => { (STATE.blocks[i].data.items = STATE.blocks[i].data.items || []).push("New item"); renderCanvas(); });
    const addRow = el.querySelector("[data-addrow]");
    if (addRow) addRow.addEventListener("click", () => {
      const cols = (STATE.blocks[i].data.columns || []).length || 3;
      (STATE.blocks[i].data.rows = STATE.blocks[i].data.rows || []).push(Array(cols).fill("—")); renderCanvas();
    });
    el.querySelectorAll("[data-rec]").forEach((rb) => rb.addEventListener("click", () => {
      STATE.blocks[i].data.recommended = Number(rb.dataset.rec); renderCanvas();
    }));
  });
}

/* ---------- add block ---------- */
const NEW_BLOCKS = {
  text: { type: "text", data: { heading: "New Section", body: "Write your content here…" } },
  list: { type: "list", data: { heading: "Key Points", items: ["First point", "Second point"] } },
  stats: { type: "stats", data: { heading: "By the Numbers", items: [{ label: "Metric", value: "0" }, { label: "Metric", value: "0" }, { label: "Metric", value: "0" }] } },
  tiers: { type: "tiers", data: { heading: "Investment Options", recommended: 1, tiers: [{ name: "Good", price: "$2,500/mo", blurb: "Starter", features: ["Feature"] }, { name: "Better", price: "$5,000/mo", blurb: "Growth", features: ["Feature"] }, { name: "Best", price: "$10,000/mo", blurb: "Dominance", features: ["Feature"] }] } },
  table: { type: "table", data: { heading: "Plan", columns: ["Phase", "Timing", "Detail"], rows: [["Launch", "Weeks 1–2", "—"]] } },
  cta: { type: "cta", data: { heading: "Next Step", body: "Let's get started.", button: "Schedule a call", url: "https://smart1marketing.com/free-consultation" } },
};
$("#addBlockBtn").addEventListener("click", (e) => {
  const m = $("#blockMenu");
  m.classList.toggle("hidden");
  const r = e.target.getBoundingClientRect();
  m.style.top = r.bottom + 6 + "px"; m.style.left = Math.max(10, r.right - 180) + "px";
});
$$("#blockMenu button").forEach((b) => b.addEventListener("click", () => {
  STATE.blocks.push(JSON.parse(JSON.stringify(NEW_BLOCKS[b.dataset.add])));
  $("#blockMenu").classList.add("hidden");
  renderCanvas();
  $("#canvas").lastElementChild.previousElementSibling?.scrollIntoView({ behavior: "smooth", block: "center" });
}));
document.addEventListener("click", (e) => {
  if (!e.target.closest("#blockMenu") && !e.target.closest("#addBlockBtn")) $("#blockMenu").classList.add("hidden");
});

/* ---------- save ---------- */
async function save(mode) {
  if (!STATE.blocks.length) return toast("Proposal is empty", true);
  loader(true, "Saving proposal — building PDF…");
  try {
    const data = await api("/sales/proposals/api/proposals", {
      method: "POST",
      body: JSON.stringify({
        id: STATE.proposalId || undefined,
        mode,
        industry: STATE.industry,
        customer: STATE.customer,
        blocks: STATE.blocks,
      }),
    });
    STATE.proposalId = data.id; STATE.isExisting = true;
    $("#saveBtn").textContent = "Update proposal";
    $("#saveNewBtn").classList.remove("hidden");
    $("#editMeta").textContent = `${STATE.industryLabel} · v${data.version} · saved just now`;
    const bits = [`✅ Saved (v${data.version}).`];
    if (data.pdf_url) bits.push(`<a href="${esc(data.pdf_url)}" target="_blank" rel="noopener">View PDF</a>`);
    if (data.pdf_note) bits.push(esc(data.pdf_note));
    bits.push(data.webhook?.sent ? "Sent to Smart 1 Suite ✓" : `Suite webhook: ${esc(data.webhook?.reason || "not sent")}`);
    if (data.ghl?.created) bits.push("Opportunity created/updated in Suite ✓");
    const note = $("#saveNote"); note.innerHTML = bits.join(" · "); note.classList.remove("hidden");
    toast("Proposal saved");
  } catch (e) { toast(e.message, true); }
  loader(false);
}
$("#saveBtn").addEventListener("click", () => save(STATE.isExisting ? "update" : "new"));
$("#saveNewBtn").addEventListener("click", () => {
  if (!confirm("Save a copy as a NEW proposal (new record, version history starts fresh)?")) return;
  const src = STATE.proposalId; STATE.proposalId = ""; STATE.isExisting = false;
  save("new").then(() => { STATE.sourceId = src; });
});

/* ---------- ui helpers ---------- */
let loaderTimer, msgIdx = 0;
function loader(on, msg) {
  $("#loader").classList.toggle("hidden", !on);
  clearInterval(loaderTimer);
  if (on) {
    msgIdx = 0;
    $("#loaderMsg").textContent = msg || LOADER_MSGS[0];
    if (!msg) loaderTimer = setInterval(() => { msgIdx = (msgIdx + 1) % LOADER_MSGS.length; $("#loaderMsg").textContent = LOADER_MSGS[msgIdx]; }, 2600);
  }
}
let toastTimer;
function toast(msg, isErr) {
  const t = $("#toast");
  t.textContent = msg; t.classList.toggle("error", !!isErr); t.classList.remove("hidden");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.add("hidden"), 3500);
}

/* ---------- init ---------- */
/* Hub login already gates access — fetch the app token silently, no password screen. */
(async () => {
  if (!TOKEN) {
    try {
      const d = await api("/sales/proposals/api/login", { method: "POST", body: JSON.stringify({ password: "" }) });
      TOKEN = d.token; sessionStorage.setItem("s1pb_token", TOKEN);
    } catch { /* boot() will surface any real problem */ }
  }
  await boot();
  applyHubPrefill();
})();

/* Prefill from Client 360: /sales/proposals/?prefill=1&business_name=...&website=... */
function applyHubPrefill() {
  const p = new URLSearchParams(location.search);
  if (!p.get("prefill")) return;
  resetForm(); show("new");
  const map = { business_name: "#c_business_name", website: "#c_website", contact_name: "#c_contact_name",
                contact_email: "#c_contact_email", contact_phone: "#c_contact_phone",
                city: "#c_city", state: "#c_state", zip: "#c_zip", salesperson: "#c_salesperson" };
  for (const [k, sel] of Object.entries(map)) {
    const v = p.get(k);
    if (v && $(sel)) $(sel).value = v;
  }
  toast("Client details filled from Client 360 — pick an industry and generate.");
}
