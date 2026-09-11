/* PrintPro — interface d'atelier. Vanilla JS, aucune dépendance. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = { assets: [], current: null, selected: new Set(), caps: null };

/* ---------------------------------------------------------------- utils */
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = response.statusText;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message, isError = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("err", isError);
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 3200);
}

async function busy(fn) {
  $("busy").classList.add("on");
  try { return await fn(); }
  catch (error) { toast(error.message || String(error), true); }
  finally { $("busy").classList.remove("on"); }
}

function bindRange(rangeId, labelId, decimals = 0) {
  const range = $(rangeId), label = $(labelId);
  const sync = () => { label.textContent = Number(range.value).toFixed(decimals); };
  range.addEventListener("input", sync);
  sync();
}

/* ------------------------------------------------------------- library */
function renderLibrary() {
  const box = $("library");
  box.innerHTML = "";
  state.assets.forEach((asset) => {
    const card = document.createElement("div");
    card.className = "thumb" + (state.current === asset.id ? " active" : "");
    card.innerHTML = `
      <input class="pick" type="checkbox" ${state.selected.has(asset.id) ? "checked" : ""}>
      <button class="kill" title="Retirer">✕</button>
      <img src="${asset.preview}" alt="">
      <div class="meta">${asset.name} · ${asset.width}×${asset.height}</div>`;
    card.querySelector("img").addEventListener("click", () => select(asset.id));
    card.querySelector(".meta").addEventListener("click", () => select(asset.id));
    card.querySelector(".pick").addEventListener("change", (event) => {
      event.target.checked ? state.selected.add(asset.id) : state.selected.delete(asset.id);
      renderMontageItems();
    });
    card.querySelector(".kill").addEventListener("click", async (event) => {
      event.stopPropagation();
      await busy(() => api(`/api/assets/${asset.id}`, { method: "DELETE" }));
      state.assets = state.assets.filter((a) => a.id !== asset.id);
      state.selected.delete(asset.id);
      if (state.current === asset.id) state.current = state.assets[0]?.id || null;
      refreshAll();
    });
    box.appendChild(card);
  });
  $("lib-note").textContent = state.assets.length
    ? `${state.assets.length} visuel(s) · ${state.selected.size} coché(s) pour la planche`
    : "Aucun visuel importé pour l'instant.";
}

function asset(id = state.current) { return state.assets.find((a) => a.id === id); }

function select(id) { state.current = id; refreshAll(); }

function refreshAll() {
  renderLibrary();
  renderStage();
  renderMontageItems();
}

function renderStage() {
  const current = asset();
  const stage = $("stage");
  if (!current) {
    stage.innerHTML = '<div class="empty">Importez un visuel pour commencer.</div>';
    $("dims").textContent = "—";
    $("quality").textContent = "—";
    return;
  }
  stage.innerHTML = `<img src="${current.preview}" alt="${current.name}">`;
  const steps = current.history.slice(1).join(" → ");
  $("dims").textContent = `${current.width}×${current.height} px` +
    ` · ${current.native_mm[0]}×${current.native_mm[1]} mm à 300 dpi` +
    (current.transparent ? " · fond transparent" : "") +
    (steps ? ` · ${steps}` : "");
  $("dl-png").href = `/api/assets/${current.id}/download`;
  $("dl-svg").href = `/api/assets/${current.id}/svg`;
  $("dl-svg").style.opacity = current.has_svg ? 1 : .4;
  updateQuality();
}

async function updateQuality() {
  const current = asset();
  if (!current) return;
  const mm = Number($("print-mm").value) || 100;
  try {
    const report = await api(`/api/assets/${current.id}/quality?mm=${mm}&dpi=300`);
    const node = $("quality");
    node.textContent = `${report.dpi} dpi — ${report.label}`;
    node.className = report.grade === "faible" ? "note warn"
      : report.grade === "excellent" ? "note ok" : "muted";
  } catch (_) { /* l'aperçu n'est pas critique */ }
}

/* -------------------------------------------------------------- upload */
async function upload(files) {
  if (!files.length) return;
  const form = new FormData();
  [...files].forEach((file) => form.append("files", file));
  const created = await busy(() => api("/api/assets", { method: "POST", body: form }));
  if (!created) return;
  state.assets.push(...created);
  created.forEach((item) => state.selected.add(item.id));
  state.current = created[0].id;
  refreshAll();
  toast(`${created.length} visuel(s) importé(s)`);
}

/* --------------------------------------------------------- traitements */
function updateAsset(updated) {
  const index = state.assets.findIndex((a) => a.id === updated.id);
  if (index >= 0) state.assets[index] = updated;
  refreshAll();
}

async function runBackground() {
  const current = asset();
  if (!current) return toast("Sélectionnez un visuel", true);
  const body = {
    method: $("bg-method").value,
    color: $("bg-method").value === "color" ? $("bg-color").value : null,
    tolerance: Number($("bg-tol").value),
    softness: Number($("bg-soft").value),
    edge_shift: Number($("bg-shift").value),
    keep_holes: $("bg-holes").checked,
    largest_only: $("bg-largest").checked,
    trim: $("bg-trim").checked,
  };
  const result = await busy(() => api(`/api/assets/${current.id}/background`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
  if (!result) return;
  updateAsset(result);
  $("bg-note").textContent =
    `Moteur ${result.engine} · sujet ${result.coverage}% de l'image · ${result.seconds}s`;
}

async function runUpscale() {
  const current = asset();
  if (!current) return toast("Sélectionnez un visuel", true);
  const mode = $("up-mode").value;
  const body = {
    method: $("up-method").value,
    denoise: Number($("up-dn").value),
    sharpen: Number($("up-sh").value),
  };
  if (mode === "mm") {
    body.target_mm = Number($("up-value").value);
    body.dpi = Number($("up-dpi").value);
  } else {
    body.scale = Number($("up-value").value);
  }
  const result = await busy(() => api(`/api/assets/${current.id}/upscale`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
  if (!result) return;
  updateAsset(result);
  $("up-note").textContent =
    `${result.method} ×${result.factor} → ${result.width}×${result.height} px · ${result.seconds}s`
    + (result.notes?.length ? ` — ${result.notes.join(" ; ")}` : "");
}

async function runVectorize() {
  const current = asset();
  if (!current) return toast("Sélectionnez un visuel", true);
  const body = {
    mode: $("vec-mode").value,
    colors: Number($("vec-colors").value),
    detail: Number($("vec-detail").value),
    smoothing: Number($("vec-smooth").value),
    min_area: Number($("vec-area").value),
    drop_background: $("vec-dropbg").checked,
    replace: $("vec-replace").checked,
  };
  const result = await busy(() => api(`/api/assets/${current.id}/vectorize`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
  if (!result) return;
  updateAsset(result);
  $("vec-note").innerHTML =
    `${result.shapes} formes · ${result.nodes} nœuds · ${(result.svg_bytes / 1024).toFixed(1)} Ko · ${result.seconds}s`
    + `<br>Palette : ` + result.palette.map((c) =>
      `<span style="display:inline-block;width:12px;height:12px;border-radius:3px;border:1px solid #ccc;background:rgb(${c.join(",")})"></span>`
    ).join(" ");
  toast("SVG prêt — bouton SVG dans la barre d'aperçu");
}

/* ------------------------------------------------------------- montage */
function renderMontageItems() {
  const box = $("montage-items");
  box.innerHTML = "";
  const chosen = state.assets.filter((a) => state.selected.has(a.id));
  if (!chosen.length) {
    box.innerHTML = '<p class="note">Cochez au moins un visuel dans la bibliothèque.</p>';
    return;
  }
  chosen.forEach((item) => {
    const row = document.createElement("div");
    row.className = "mitem";
    row.innerHTML = `
      <img src="${item.preview}" alt="">
      <span class="name" title="${item.name}">${item.name}</span>
      <input type="number" value="${item.width_mm}" min="1" max="2000" step="1" title="largeur en mm">
      <input type="number" value="${item.quantity}" min="1" max="999" step="1" title="quantité">`;
    const [width, quantity] = row.querySelectorAll("input");
    const save = async () => {
      const updated = await api(`/api/assets/${item.id}/settings`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ width_mm: Number(width.value), quantity: Number(quantity.value) }),
      });
      const index = state.assets.findIndex((a) => a.id === updated.id);
      if (index >= 0) state.assets[index] = updated;
    };
    width.addEventListener("change", save);
    quantity.addEventListener("change", save);
    box.appendChild(row);
  });
}

async function runMontage() {
  const chosen = state.assets.filter((a) => state.selected.has(a.id));
  if (!chosen.length) return toast("Cochez au moins un visuel", true);
  const body = {
    items: chosen.map((item) => ({
      id: item.id, width_mm: item.width_mm, quantity: item.quantity,
    })),
    page: $("m-page").value,
    orientation: $("m-orient").value,
    dpi: Number($("m-dpi").value),
    layout: $("m-layout").value,
    margin_mm: Number($("m-margin").value),
    spacing_mm: Number($("m-space").value),
    background: $("m-transparent").checked ? null : $("m-bg").value,
    mirror: $("m-mirror").checked,
    crop_marks: $("m-marks").checked,
    registration_marks: $("m-reg").checked,
    outline: $("m-outline").checked,
    cut_contour_mm: Number($("m-cut").value),
    cut_stroke: Number($("m-cut").value) > 0,
  };
  const result = await busy(() => api("/api/montage", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }));
  if (!result) return;

  $("m-stats").innerHTML = [
    `<span class="stat"><b>${result.placed}</b>/${result.requested} placés</span>`,
    `<span class="stat"><b>${result.pages}</b> planche(s)</span>`,
    `<span class="stat">${result.page_size_mm[0]}×${result.page_size_mm[1]} mm</span>`,
    `<span class="stat">remplissage <b>${result.efficiency}%</b></span>`,
    `<span class="stat">${result.dpi} dpi · ${result.seconds}s</span>`,
  ].join("");

  $("m-links").innerHTML =
    `<a href="${result.pdf}" download>Télécharger le PDF</a>` +
    result.sheets.map((url, index) =>
      `<a class="alt" href="${url}" download>PNG ${index + 1}</a>`).join("");

  $("sheets").innerHTML = result.previews
    .map((url) => `<img src="${url}" alt="planche">`).join("");

  (result.warnings || []).forEach((warning) => toast(warning, true));
  toast(`${result.placed} visuels sur ${result.pages} planche(s)`);
}

/* ---------------------------------------------------------------- init */
async function init() {
  state.caps = await api("/api/capabilities");
  const select = $("m-page");
  Object.entries(state.caps.pages).forEach(([name, size]) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = `${name} — ${size[0]}×${size[1]} mm`;
    if (name === "A4") option.selected = true;
    select.appendChild(option);
  });
  if (!state.caps.ai_background) {
    $("bg-method").querySelector('option[value="ai"]').textContent =
      "IA (rembg non installé)";
  }
  if (!state.caps.ai_upscale) {
    $("up-method").querySelector('option[value="ai"]').textContent =
      "Real-ESRGAN (non installé)";
  }

  const existing = await api("/api/assets");
  state.assets = existing;
  existing.forEach((item) => state.selected.add(item.id));
  state.current = existing[0]?.id || null;
  refreshAll();
}

/* événements */
$("drop").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", (event) => upload(event.target.files));
["dragenter", "dragover"].forEach((type) =>
  $("drop").addEventListener(type, (event) => {
    event.preventDefault(); $("drop").classList.add("hot");
  }));
["dragleave", "drop"].forEach((type) =>
  $("drop").addEventListener(type, (event) => {
    event.preventDefault(); $("drop").classList.remove("hot");
  }));
$("drop").addEventListener("drop", (event) => upload(event.dataTransfer.files));
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => event.preventDefault());

document.querySelectorAll(".tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((b) => b.classList.remove("on"));
    document.querySelectorAll(".tool").forEach((t) => t.classList.remove("on"));
    button.classList.add("on");
    $("tab-" + button.dataset.tab).classList.add("on");
  });
});

$("bg-method").addEventListener("change", (event) => {
  $("bg-color-field").style.display = event.target.value === "color" ? "block" : "none";
});
$("up-mode").addEventListener("change", (event) => {
  const physical = event.target.value === "mm";
  $("up-dpi-field").style.display = physical ? "block" : "none";
  $("up-value").value = physical ? 200 : 2;
});

$("run-bg").addEventListener("click", runBackground);
$("run-up").addEventListener("click", runUpscale);
$("run-vec").addEventListener("click", runVectorize);
$("run-montage").addEventListener("click", runMontage);
$("print-mm").addEventListener("change", updateQuality);

$("undo").addEventListener("click", async () => {
  const current = asset();
  if (!current) return;
  const result = await busy(() => api(`/api/assets/${current.id}/undo`, { method: "POST" }));
  if (result) { updateAsset(result); toast("Étape annulée"); }
});

$("clear").addEventListener("click", async () => {
  if (!confirm("Supprimer tous les visuels et les planches générées ?")) return;
  await busy(() => api("/api/workspace/clear", { method: "POST" }));
  state.assets = []; state.selected.clear(); state.current = null;
  $("sheets").innerHTML = ""; $("m-links").innerHTML = ""; $("m-stats").innerHTML = "";
  refreshAll();
});

bindRange("bg-tol", "bg-tol-v");
bindRange("bg-soft", "bg-soft-v");
bindRange("bg-shift", "bg-shift-v");
bindRange("up-dn", "up-dn-v", 2);
bindRange("up-sh", "up-sh-v", 2);
bindRange("vec-colors", "vec-col-v");
bindRange("vec-detail", "vec-det-v", 1);
bindRange("vec-smooth", "vec-sm-v", 1);
bindRange("vec-area", "vec-area-v");

init().catch((error) => toast(error.message, true));
