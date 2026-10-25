/* PrintPro — interface d'atelier. Vanilla JS, aucune dépendance. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  assets: [],
  current: null,
  selected: new Set(),
  caps: null,
  zoom: 1,
  original: false,
  view: "prepare",
  pending: new Set(),
  working: false,
};
const escapeHTML = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const emptyStage =
  '<div class="empty"><span class="empty-symbol">⌑</span><strong>Un beau fichier.<br>Une impression impeccable.</strong><p>Importez votre premier visuel pour le détourer, l’agrandir ou le préparer pour votre planche.</p></div>';

/* ---------------------------------------------------------------- utils */
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = response.statusText;
    try {
      const detail = (await response.json()).detail;
      message = Array.isArray(detail)
        ? detail
            .map((e) => `${e.loc.slice(1).join(" · ")} : ${e.msg}`)
            .join(" ; ")
        : detail || message;
    } catch (_) {}
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
  if (state.working) return;
  state.working = true;
  $("busy").classList.add("on");
  document.body.setAttribute("aria-busy", "true");
  try {
    return await fn();
  } catch (error) {
    toast(error.message || String(error), true);
  } finally {
    state.working = false;
    $("busy").classList.remove("on");
    document.body.removeAttribute("aria-busy");
  }
}

function bindRange(rangeId, labelId, decimals = 0) {
  const range = $(rangeId),
    label = $(labelId);
  const sync = () => {
    label.textContent = Number(range.value).toFixed(decimals);
  };
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
      <div class="meta">${escapeHTML(asset.name)} · ${asset.width}×${asset.height}</div>`;
    card
      .querySelector(".pick")
      .setAttribute("aria-label", `Inclure ${asset.name} sur la planche`);
    card.querySelector(".meta").tabIndex = 0;
    card.querySelector(".meta").setAttribute("role", "button");
    card.querySelector(".meta").addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        select(asset.id);
      }
    });
    card.querySelector("img").addEventListener("click", () => select(asset.id));
    card
      .querySelector(".meta")
      .addEventListener("click", () => select(asset.id));
    card.querySelector(".pick").addEventListener("change", (event) => {
      event.target.checked
        ? state.selected.add(asset.id)
        : state.selected.delete(asset.id);
      invalidateMontage();
      renderMontageItems();
      renderLibrary();
    });
    card.querySelector(".kill").addEventListener("click", async (event) => {
      event.stopPropagation();
      if (!confirm(`Retirer « ${asset.name} » de l’atelier ?`)) return;
      const removed = await busy(() =>
        api(`/api/assets/${asset.id}`, { method: "DELETE" }),
      );
      if (!removed) return;
      invalidateMontage();
      state.assets = state.assets.filter((a) => a.id !== asset.id);
      state.selected.delete(asset.id);
      if (state.current === asset.id)
        state.current = state.assets[0]?.id || null;
      refreshAll();
    });
    box.appendChild(card);
  });
  $("asset-count").textContent = state.assets.length;
  $("lib-note").textContent = state.assets.length
    ? `${state.assets.length} visuel(s) · ${state.selected.size} coché(s) pour la planche`
    : "Aucun visuel importé pour l'instant.";
}

function asset(id = state.current) {
  return state.assets.find((a) => a.id === id);
}

function select(id) {
  state.current = id;
  state.original = false;
  state.zoom = 1;
  refreshAll();
}

function refreshAll() {
  renderLibrary();
  renderStage();
  renderMontageItems();
}

function renderStage() {
  const current = asset();
  const stage = $("stage");
  [
    "run-bg",
    "run-up",
    "run-vec",
    "compare",
    "zoom-in",
    "zoom-out",
    "zoom-reset",
    "print-mm",
  ].forEach((id) => ($(id).disabled = !current));
  $("undo").disabled = !current || current.versions < 2;
  $("dl-png").setAttribute("aria-disabled", String(!current));
  $("dl-svg").setAttribute("aria-disabled", String(!current?.has_svg));
  $("compare").setAttribute("aria-pressed", String(state.original));
  $("compare").textContent = state.original
    ? "Revenir au résultat"
    : "Voir l’original";
  $("preview-name").textContent = current?.name || "Aucun visuel";
  if (!current) {
    stage.innerHTML = emptyStage;
    $("dl-png").removeAttribute("href");
    $("dl-svg").removeAttribute("href");
    $("dims").textContent = "—";
    $("quality").textContent = "—";
    return;
  }
  const url = `/api/assets/${current.id}/preview?full=1&original=${state.original}&v=${current.versions}`;
  stage.innerHTML = `<img src="${url}" alt="${escapeHTML(current.name)}">`;
  applyZoom();
  $("print-mm").value = current.width_mm;
  const steps = current.history.slice(1).join(" → ");
  $("dims").textContent =
    `${current.width}×${current.height} px` +
    ` · ${current.native_mm[0]}×${current.native_mm[1]} mm à 300 dpi` +
    (current.transparent ? " · fond transparent" : "") +
    (steps ? ` · ${steps}` : "");
  $("dl-png").href = `/api/assets/${current.id}/download`;
  $("dl-svg").href = `/api/assets/${current.id}/svg`;
  $("dl-svg").style.opacity = current.has_svg ? 1 : 0.4;
  updateQuality();
}

async function updateQuality() {
  const current = asset();
  if (!current) return;
  const mm = Number($("print-mm").value) || 100;
  try {
    const report = await api(
      `/api/assets/${current.id}/quality?mm=${mm}&dpi=300`,
    );
    const node = $("quality");
    if (state.current !== current.id || Number($("print-mm").value) !== mm)
      return;
    node.textContent =
      `${report.dpi} dpi — ${report.label}` +
      (report.resampled
        ? " · image rééchantillonnée : vérifier les détails"
        : "");
    node.className =
      report.grade === "faible"
        ? "note warn"
        : report.grade === "excellent"
          ? "note ok"
          : "muted";
  } catch (_) {
    /* l'aperçu n'est pas critique */
  }
}

/* -------------------------------------------------------------- upload */
async function upload(files) {
  if (!files.length) return;
  const accepted = [...files].filter(
    (file) =>
      file.size <= 25 * 1024 * 1024 &&
      /\.(png|jpe?g|webp|tiff?|bmp)$/i.test(file.name),
  );
  if (accepted.length !== files.length)
    toast(
      "Certains fichiers ont été ignorés : PNG, JPG, WEBP, TIFF ou BMP, 25 Mo maximum.",
      true,
    );
  if (!accepted.length) return;
  const form = new FormData();
  accepted.forEach((file) => form.append("files", file));
  const created = await busy(() =>
    api("/api/assets", { method: "POST", body: form }),
  );
  if (!created) {
    // A mixed batch may have imported valid files before reporting an error.
    try {
      state.assets = await api("/api/assets");
      state.current = state.assets.at(-1)?.id || null;
      refreshAll();
    } catch (_) {}
    return;
  }
  invalidateMontage();
  state.assets.push(...created);
  state.original = false;
  state.zoom = 1;
  created.forEach((item) => state.selected.add(item.id));
  state.current = created[0].id;
  refreshAll();
  toast(`${created.length} visuel(s) importé(s)`);
}

/* --------------------------------------------------------- traitements */
function updateAsset(updated) {
  invalidateMontage();
  state.original = false;
  state.zoom = 1;
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
  const result = await busy(() =>
    api(`/api/assets/${current.id}/background`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  if (!result) return;
  updateAsset(result);
  $("bg-note").textContent =
    `Moteur ${result.engine} · sujet ${result.coverage}% de l'image · ${result.seconds}s`;
}

async function runUpscale() {
  const current = asset();
  if (!current) return toast("Sélectionnez un visuel", true);
  if (!$("up-value").reportValidity()) return;
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
  const result = await busy(() =>
    api(`/api/assets/${current.id}/upscale`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  if (!result) return;
  updateAsset(result);
  $("up-note").textContent =
    `${result.method} ×${result.factor} → ${result.width}×${result.height} px · ${result.seconds}s` +
    (result.notes?.length ? ` — ${result.notes.join(" ; ")}` : "");
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
  const result = await busy(() =>
    api(`/api/assets/${current.id}/vectorize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  if (!result) return;
  updateAsset(result);
  $("vec-note").innerHTML =
    `${result.shapes} formes · ${result.nodes} nœuds · ${(result.svg_bytes / 1024).toFixed(1)} Ko · ${result.seconds}s` +
    `<br>Palette : ` +
    result.palette
      .map(
        (c) =>
          `<span style="display:inline-block;width:12px;height:12px;border-radius:3px;border:1px solid #ccc;background:rgb(${c.join(",")})"></span>`,
      )
      .join(" ");
  toast("SVG prêt — bouton SVG dans la barre d'aperçu");
}

/* ------------------------------------------------------------- montage */
function renderMontageItems() {
  const box = $("montage-items");
  box.innerHTML = "";
  const chosen = state.assets.filter((a) => state.selected.has(a.id));
  if (!chosen.length) {
    box.innerHTML =
      '<p class="note">Cochez au moins un visuel dans la bibliothèque.</p>';
    return;
  }
  chosen.forEach((item) => {
    const row = document.createElement("div");
    row.className = "mitem";
    row.innerHTML = `
      <img src="${item.preview}" alt="">
      <span class="name" title="${escapeHTML(item.name)}">${escapeHTML(item.name)}</span>
      <span class="item-size">Largeur mm / Qté<br>${((item.width_mm * item.height) / item.width).toFixed(1)} mm de haut*</span>
      <input aria-label="Largeur en mm de ${escapeHTML(item.name)}" type="number" value="${item.width_mm}" min="0.1" max="2000" step="0.1" title="largeur en mm">
      <input aria-label="Quantité de ${escapeHTML(item.name)}" type="number" value="${item.quantity}" min="1" max="999" step="1" title="quantité">`;
    const [width, quantity] = row.querySelectorAll("input");
    const save = () => {
      invalidateMontage();
      if (!width.reportValidity() || !quantity.reportValidity()) return;
      const task = api(`/api/assets/${item.id}/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          width_mm: Number(width.value),
          quantity: Number(quantity.value),
        }),
      })
        .then((updated) => {
          const index = state.assets.findIndex((a) => a.id === updated.id);
          if (index >= 0) state.assets[index] = updated;
          row.querySelector(".item-size").innerHTML =
            `Largeur mm / Qté<br>${((updated.width_mm * updated.height) / updated.width).toFixed(1)} mm de haut*`;
          if (state.current === updated.id) {
            $("print-mm").value = updated.width_mm;
            updateQuality();
          }
        })
        .catch((error) => {
          toast(error.message, true);
          width.value = item.width_mm;
          quantity.value = item.quantity;
        })
        .finally(() => state.pending.delete(task));
      state.pending.add(task);
    };
    width.addEventListener("change", save);
    quantity.addEventListener("change", save);
    box.appendChild(row);
  });
}

async function runMontage(previewOnly = false) {
  if (
    [...document.querySelectorAll(".montage-panel input[type=number]")].some(
      (input) => !input.reportValidity(),
    )
  )
    return;
  await Promise.all([...state.pending]);
  const chosen = state.assets.filter((a) => state.selected.has(a.id));
  if (!chosen.length) return toast("Cochez au moins un visuel", true);
  const body = {
    items: chosen.map((item) => ({
      id: item.id,
      width_mm: item.width_mm,
      quantity: item.quantity,
    })),
    page: $("m-page").value,
    page_width_mm:
      $("m-page").value === "custom" ? Number($("m-width").value) : 0,
    page_height_mm:
      $("m-page").value === "custom" ? Number($("m-height").value) : 0,
    preview_only: previewOnly,
    auto_trim: $("m-trim").checked,
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
  const result = await busy(() =>
    api("/api/montage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
  if (!result) return;

  $("m-stats").innerHTML = [
    `<span class="stat"><b>${result.placed}</b>/${result.requested} placés</span>`,
    `<span class="stat"><b>${result.pages}</b> planche(s)</span>`,
    `<span class="stat">${result.page_size_mm[0]}×${result.page_size_mm[1]} mm</span>`,
    `<span class="stat">remplissage <b>${result.efficiency}%</b></span>`,
    `<span class="stat">${result.dpi} dpi · ${result.seconds}s</span>`,
  ].join("");

  $("m-links").innerHTML = previewOnly
    ? '<p class="note">Aperçu de placement uniquement. Exportez pour obtenir les fichiers à pleine résolution.</p>'
    : `<a href="${result.pdf}" download>Télécharger le PDF</a>` +
      result.sheets
        .map(
          (url, index) =>
            `<a class="alt" href="${url}" download>PNG ${index + 1}</a>`,
        )
        .join("");

  $("sheets").innerHTML = result.previews
    .map((url) => `<img src="${url}" alt="planche">`)
    .join("");

  $("m-warnings").replaceChildren();
  (result.warnings || []).forEach((warning) => {
    const p = document.createElement("p");
    p.textContent = warning;
    $("m-warnings").appendChild(p);
  });
  toast(
    `${previewOnly ? "Aperçu calculé" : "Export prêt"} · ${result.placed}/${result.requested} visuels sur ${result.pages} planche(s)`,
    result.placed < result.requested,
  );
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
  select.add(new Option("Dimensions personnalisées…", "custom"));
  if (!state.caps.ai_background) {
    $("bg-method").querySelector('option[value="ai"]').textContent =
      "IA (rembg non installé)";
    $("bg-method").querySelector('option[value="ai"]').disabled = true;
  }
  if (!state.caps.ai_upscale) {
    $("up-method").querySelector('option[value="ai"]').textContent =
      "Real-ESRGAN (non installé)";
    $("up-method").querySelector('option[value="ai"]').disabled = true;
  }

  const existing = await api("/api/assets");
  state.assets = existing;
  existing.forEach((item) => state.selected.add(item.id));
  state.current = existing[0]?.id || null;
  try {
    const saved = JSON.parse(localStorage.getItem("printpro-montage") || "{}");
    Object.entries(saved).forEach(([id, value]) => {
      const input = $(id);
      if (!input || !input.closest(".montage-panel")) return;
      if (input.type === "checkbox") input.checked = Boolean(value);
      else input.value = value;
    });
    $("custom-size").hidden = $("m-page").value !== "custom";
  } catch (_) {
    /* private browsing may disable storage */
  }
  refreshAll();
}

/* événements */
$("drop").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", async (event) => {
  await upload(event.target.files);
  event.target.value = "";
});
["dragenter", "dragover"].forEach((type) =>
  $("drop").addEventListener(type, (event) => {
    event.preventDefault();
    $("drop").classList.add("hot");
  }),
);
["dragleave", "drop"].forEach((type) =>
  $("drop").addEventListener(type, (event) => {
    event.preventDefault();
    $("drop").classList.remove("hot");
  }),
);
$("drop").addEventListener("drop", (event) => upload(event.dataTransfer.files));
window.addEventListener("dragover", (event) => event.preventDefault());
window.addEventListener("drop", (event) => event.preventDefault());

document.querySelectorAll(".tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    document
      .querySelectorAll(".tabs button")
      .forEach((b) => b.classList.remove("on"));
    document.querySelectorAll(".tool").forEach((t) => t.classList.remove("on"));
    button.classList.add("on");
    $("tab-" + button.dataset.tab).classList.add("on");
  });
});

$("bg-method").addEventListener("change", (event) => {
  $("bg-color-field").style.display =
    event.target.value === "color" ? "block" : "none";
});
$("up-mode").addEventListener("change", (event) => {
  const physical = event.target.value === "mm";
  $("up-dpi-field").style.display = physical ? "block" : "none";
  $("up-value").value = physical ? asset()?.width_mm || 200 : 2;
  $("up-value").max = physical ? 2000 : 20;
  $("up-value").step = physical ? 0.1 : 0.5;
});

$("run-bg").addEventListener("click", runBackground);
$("run-up").addEventListener("click", runUpscale);
$("run-vec").addEventListener("click", runVectorize);
$("run-montage").addEventListener("click", () => runMontage(false));
$("preview-montage").addEventListener("click", () => runMontage(true));
$("print-mm").addEventListener("change", async () => {
  const current = asset();
  if (!current || !$("print-mm").reportValidity()) return;
  const result = await busy(() =>
    api(`/api/assets/${current.id}/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ width_mm: Number($("print-mm").value) }),
    }),
  );
  if (result) updateAsset(result);
});

$("undo").addEventListener("click", async () => {
  const current = asset();
  if (!current) return;
  const result = await busy(() =>
    api(`/api/assets/${current.id}/undo`, { method: "POST" }),
  );
  if (result) {
    updateAsset(result);
    toast("Étape annulée");
  }
});

$("clear").addEventListener("click", async () => {
  if (!confirm("Supprimer tous les visuels et les planches générées ?")) return;
  const cleared = await busy(() =>
    api("/api/workspace/clear", { method: "POST" }),
  );
  if (!cleared) return;
  state.assets = [];
  state.selected.clear();
  state.current = null;
  invalidateMontage();
  $("sheets").innerHTML = "";
  $("m-links").innerHTML = "";
  $("m-stats").innerHTML = "";
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

function invalidateMontage() {
  if ($("m-stats").textContent)
    $("sheets").innerHTML =
      '<div class="export-empty"><strong>Réglages modifiés</strong><p>Recalculez l’aperçu pour vérifier votre nouvelle planche.</p></div>';
  $("m-stats").replaceChildren();
  $("m-links").replaceChildren();
  $("m-warnings").replaceChildren();
}
function applyZoom() {
  const image = $("stage").querySelector("img");
  $("stage").classList.toggle("zoomed", state.zoom > 1);
  if (image) {
    image.style.width = state.zoom > 1 ? `${state.zoom * 90}%` : "";
    image.style.height = "auto";
  }
  $("zoom-reset").textContent =
    state.zoom === 1 ? "Ajuster" : `${Math.round(state.zoom * 100)} %`;
}
$("zoom-in").onclick = () => {
  state.zoom = Math.min(4, state.zoom + 0.5);
  applyZoom();
};
$("zoom-out").onclick = () => {
  state.zoom = Math.max(1, state.zoom - 0.5);
  applyZoom();
};
$("zoom-reset").onclick = () => {
  state.zoom = 1;
  applyZoom();
};
$("canvas-bg").onchange = (e) => {
  $("stage").dataset.background = e.target.value;
};
$("compare").onclick = () => {
  state.original = !state.original;
  renderStage();
};
$("import-main").onclick = () => $("file").click();
$("drop").onkeydown = (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    $("file").click();
  }
};
$("select-all").onclick = () => {
  state.assets.forEach((a) => state.selected.add(a.id));
  invalidateMontage();
  refreshAll();
};
$("select-none").onclick = () => {
  state.selected.clear();
  invalidateMontage();
  refreshAll();
};
$("m-page").onchange = () => {
  $("custom-size").hidden = $("m-page").value !== "custom";
};
document
  .querySelectorAll(".montage-panel input, .montage-panel select")
  .forEach((el) =>
    el.addEventListener("change", () => {
      invalidateMontage();
      const saved = {};
      document
        .querySelectorAll(".montage-panel input[id], .montage-panel select[id]")
        .forEach((input) => {
          saved[input.id] =
            input.type === "checkbox" ? input.checked : input.value;
        });
      try {
        localStorage.setItem("printpro-montage", JSON.stringify(saved));
      } catch (_) {}
    }),
  );
document.querySelectorAll(".nav-btn").forEach(
  (button) =>
    (button.onclick = () => {
      state.view = button.dataset.view;
      document.querySelectorAll(".nav-btn").forEach((b) => {
        b.classList.toggle("on", b === button);
        b.setAttribute("aria-pressed", String(b === button));
      });
      const montage = state.view === "montage";
      document.querySelector(".preview-panel").hidden = montage;
      document.querySelector(".tools-panel").hidden = montage;
      document.querySelector(".montage-panel").hidden = !montage;
      $("view-title").textContent = montage
        ? "Chaque millimètre compte."
        : "De l’image au fichier prêt à imprimer.";
    }),
);
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input,select,textarea")) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
    e.preventDefault();
    if (!state.working) $("undo").click();
  }
});
// Associate all static field captions with their controls.
document.querySelectorAll(".field").forEach((field) => {
  const label = field.querySelector("label"),
    input = field.querySelector("input, select");
  if (label && input && input.id) label.htmlFor = input.id;
});
init().catch((error) => {
  $("save-state").textContent = "Connexion indisponible";
  toast(
    `Impossible d’ouvrir l’atelier : ${error.message}. Rechargez la page.`,
    true,
  );
});
