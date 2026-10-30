/* PrintPro — parcours « prêt à imprimer ». Vanilla JS, aucune dépendance. */
"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  family: "papier",
  products: [],
  product: { papier: "flyer-a5", dtf: "dtf-a4" },
  asset: null,
  seg: {
    fit: "cover", bleed_mode: "mirror", orientation: "auto", edges: "threshold",
    bg_method: "auto", vec_colors: "8", vec_engine: "local", up_scale: "2", up_method: "auto",
  },
  tool: "bg",
  caps: {},
};

/* ---------------------------------------------------------------- utils */
async function api(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let message = response.statusText;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}

const post = (url, body) => api(url, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

function toast(message, isError = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("err", isError);
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 3600);
}

async function busy(text, fn) {
  $("busy-text").textContent = text;
  $("busy").classList.add("on");
  try { return await fn(); }
  catch (error) { toast(error.message || String(error), true); }
  finally { $("busy").classList.remove("on"); }
}

const esc = (text) => String(text).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n, d = 0) => Number(n).toLocaleString("fr-FR", { maximumFractionDigits: d });
const bytes = (n) => n > 1048576 ? `${fmt(n / 1048576, 1)} Mo` : `${fmt(n / 1024)} Ko`;
const ICON = { ok: "✓", warn: "!", fail: "✕" };

function checkItem(check) {
  return `<li><i class="ico ${check.status}">${ICON[check.status]}</i>
    <div><b>${esc(check.label)}</b> <span>${esc(check.detail)}</span></div></li>`;
}

/* --------------------------------------------------------------- family */
function setFamily(family) {
  state.family = family;
  document.body.dataset.family = family;
  document.querySelectorAll("[data-family]").forEach((button) => {
    if (button.tagName !== "BUTTON") return;
    button.classList.toggle("on", button.dataset.family === family);
    button.setAttribute("aria-selected", String(button.dataset.family === family));
  });
  fillProducts();
  $("result").hidden = true;
  if (state.asset) inspect();
}

function fillProducts() {
  const select = $("product");
  select.innerHTML = state.products
    .filter((p) => p.family === state.family)
    .map((p) => `<option value="${esc(p.key)}">${esc(p.label)} — ${fmt(p.width_mm)} × ${fmt(p.height_mm)} mm</option>`)
    .join("");
  select.value = state.product[state.family];
}

/* --------------------------------------------------------------- upload */
async function upload(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) { toast("Ce fichier n'est pas une image.", true); return; }
  await busy("Import du visuel…", async () => {
    const form = new FormData();
    form.append("files", file, file.name);
    const [asset] = await api("/api/assets", { method: "POST", body: form });
    showAsset(asset);
    $("tool-note").textContent = "";
    $("job").hidden = false;
    $("result").hidden = true;
    await inspect();
    $("job").scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function showAsset(asset) {
  state.asset = asset;
  $("art-img").src = `${asset.preview}&t=${Date.now()}`;  // ?v= repeats after undo
  $("art-name").textContent = asset.name;
  $("art-px").textContent = `${fmt(asset.width)} × ${fmt(asset.height)} px`
    + (asset.transparent ? " · fond transparent" : "");
  $("tool-undo").disabled = asset.versions <= 1;
  $("dl-png").href = `/api/assets/${asset.id}/download`;
  $("dl-svg").href = `/api/assets/${asset.id}/svg`;
  $("dl-svg").hidden = !asset.has_svg;
  $("tool-history").innerHTML = (asset.history || []).slice(1)
    .map((step) => `<li>${esc(step)}</li>`).join("");
}

/* -------------------------------------------------------- advanced tools */
const TOOLS = {
  bg: {
    label: () => "Supprimer le fond",
    busy: "Suppression du fond…",
    url: "background",
    body: () => ({ method: state.seg.bg_method }),
    done: (r) => `Fond supprimé (${r.engine}) — sujet sur ${fmt(r.coverage)} % de l'image.`,
  },
  vec: {
    label: () => "Vectoriser en SVG",
    busy: "Vectorisation…",
    url: "vectorize",
    body: () => ({ colors: Number(state.seg.vec_colors), engine: state.seg.vec_engine }),
    done: (r) => `Vectorisé : ${fmt(r.shapes)} formes, SVG de ${bytes(r.svg_bytes)}.`,
  },
  up: {
    label: () => `Agrandir ×${state.seg.up_scale}`,
    busy: "Agrandissement…",
    url: "upscale",
    body: () => ({ scale: Number(state.seg.up_scale), method: state.seg.up_method }),
    done: (r) => `Agrandi ×${fmt(r.factor, 2)} (${r.method}).`,
  },
};

function setTool(tool) {
  state.tool = tool;
  document.querySelectorAll(".tool-tabs button").forEach((button) => {
    const on = button.dataset.tool === tool;
    button.classList.toggle("on", on);
    button.setAttribute("aria-selected", String(on));
  });
  document.querySelectorAll(".tool-pane").forEach((pane) => {
    pane.hidden = pane.dataset.pane !== tool;
  });
  $("tool-run").textContent = TOOLS[tool].label();
}

async function applyTool() {
  if (!state.asset) return;
  const tool = TOOLS[state.tool];
  const result = await busy(tool.busy, () =>
    post(`/api/assets/${state.asset.id}/${tool.url}`, tool.body()));
  if (!result) return;
  showAsset(result);
  $("tool-note").textContent = `${tool.done(result)} (${fmt(result.seconds, 1)} s)`;
  $("result").hidden = true;
  await inspect();
}

async function undoTool() {
  if (!state.asset) return;
  const asset = await busy("Annulation…", () => post(`/api/assets/${state.asset.id}/undo`, {}));
  if (!asset) return;
  showAsset(asset);
  $("tool-note").textContent = "Dernière étape annulée.";
  $("result").hidden = true;
  await inspect();
}

function applyCaps(caps) {
  state.caps = caps || {};
  document.querySelectorAll(".needs-ai").forEach((button) => {
    const available = Boolean(state.caps[button.dataset.cap]);
    button.hidden = !available;
    button.closest(".opt").hidden = !available;   // a one-choice row says nothing
    if (!available && button.classList.contains("on")) {
      button.parentElement.querySelector("button").click();
    }
  });
}

async function inspect() {
  if (!state.asset) return;
  try {
    const report = await post(`/api/assets/${state.asset.id}/inspect`, {
      product: state.product[state.family], orientation: state.seg.orientation,
    });
    $("checks").innerHTML = report.checks.map(checkItem).join("");
    const score = $("check-score");
    score.textContent = report.issues
      ? `${report.issues} point${report.issues > 1 ? "s" : ""} à corriger`
      : "Prêt";
    score.className = "tag " + (report.issues ? "warn" : "ok");
    $("check-intro").textContent =
      `Ce que dirait l'imprimeur pour « ${report.product.label} », tel quel :`;
    $("run-note").textContent = report.issues
      ? "Tout ce qui est signalé est corrigé automatiquement."
      : "";
  } catch (error) { toast(error.message, true); }
}

/* --------------------------------------------------------------- run */
async function run() {
  if (!state.asset) return;
  const product = state.product[state.family];
  const isDtf = state.family === "dtf";
  const body = isDtf ? {
    product,
    garment: $("knockout").checked ? $("garment").value : null,
    preview_garment: $("garment").value,
    edges: state.seg.edges,
    choke_mm: Number($("choke").value) || 0,
    mirror: $("dtf-mirror").checked,
    remove_background: $("dtf-cut").checked ? "auto" : "no",
  } : {
    product,
    fit: state.seg.fit,
    bleed_mode: state.seg.bleed_mode,
    orientation: state.seg.orientation,
    crop_marks: $("crop-marks").checked,
  };
  const label = isDtf ? "Préparation du film DTF…" : "Agrandissement, séparation CMJN, contrôle…";
  const report = await busy(label, () =>
    post(`/api/assets/${state.asset.id}/${isDtf ? "dtf" : "print"}`, body));
  if (report) showResult(report, isDtf);
}

function showResult(report, isDtf) {
  const s = report.specs;
  $("res-name").textContent = report.file;
  $("res-proof").src = `${report.proof}?t=${Date.now()}`;
  const pf = report.preflight;
  const status = $("res-status");
  status.textContent = pf.passed ? `Vérifié · ${pf.ok}/${pf.count}` : "À revoir";
  status.classList.toggle("fail", !pf.passed);
  $("pf-title").textContent = isDtf
    ? `Contrôle PrintPro — ${pf.count} contrôles du film`
    : `Contrôle PrintPro — ${pf.count} contrôles structurels`;
  $("pf-list").innerHTML = pf.checks.map(checkItem).join("");

  let rows;
  if (isDtf) {
    $("res-size").textContent = `PNG ${s.dpi} dpi · couche blanche · PDF`;
    rows = [
      ["Film", `${fmt(s.size_mm[0])} × ${fmt(s.size_mm[1])} mm`, report.product.label],
      ["Résolution", `${s.dpi} dpi`, `${fmt(s.pixels[0])} × ${fmt(s.pixels[1])} px`],
      ["Motif", `${fmt(s.design_mm[0], 1)} × ${fmt(s.design_mm[1], 1)} mm`, `marge ${fmt(s.margin_mm, 1)} mm`],
      ["Bords", s.edges === "halftone" ? "Tramés" : "Nets", "alpha 0 ou 100 %"],
      ["Sous-couche", "Blanc rétréci", `${fmt(s.choke_mm, 2)} mm · ${fmt(s.white_coverage, 1)} % du film`],
      ["Source", `${fmt(s.native_dpi)} dpi`, "résolution d'origine à cette taille"],
    ];
  } else {
    $("res-size").textContent = `${bytes(s.bytes)} · ${s.format}`;
    rows = [
      ["Format", s.format, s.standard],
      ["Résolution", `${s.dpi} dpi`, `${fmt(s.pixels[0])} × ${fmt(s.pixels[1])} px`],
      ["Format fini", `${fmt(s.trim_mm[0])} × ${fmt(s.trim_mm[1])} mm`, report.product.label],
      ["Fond perdu", `+ ${fmt(s.bleed_mm, 1)} mm`, "TrimBox + BleedBox"],
      ["Couleur", "CMJN", s.profile],
      ["Traits de coupe", s.crop_marks === "aucun" ? "Aucun" : "Couleur de repérage", s.crop_marks],
      ["Encrage", `max ${fmt(s.tac_max)} %`, `limite ${fmt(s.tac_limit)} %`],
      ["Source", `${fmt(s.native_dpi)} dpi`, s.upscale_factor > 1 ? `agrandie ×${fmt(s.upscale_factor, 2)}` : "sans agrandissement"],
    ];
  }
  $("res-spec").innerHTML = rows.map(([k, v, small]) =>
    `<dt>${esc(k)}</dt><dd>${esc(v)}<small>${esc(small)}</small></dd>`).join("");

  const links = isDtf
    ? [["Film PNG", report.png, true], ["Couche blanche", report.white], ["PDF d'épreuve", report.pdf]]
    : [["Télécharger le PDF/X-1a", report.pdf, true], ["Épreuve PNG", report.proof]];
  $("downloads").innerHTML = links.map(([text, href, main]) =>
    `<a class="btn ${main ? "btn-grad" : "btn-ghost"}" href="${esc(href)}" download>${esc(text)}</a>`).join("");
  $("res-steps").innerHTML = report.steps.map((step) => `<li>${esc(step)}</li>`).join("");
  $("result").hidden = false;
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* --------------------------------------------------------------- wiring */
function wire() {
  document.querySelectorAll("button[data-family]").forEach((button) =>
    button.addEventListener("click", () => setFamily(button.dataset.family)));
  document.querySelectorAll("[data-pick]").forEach((link) =>
    link.addEventListener("click", () => setFamily(link.dataset.pick)));

  $("product").addEventListener("change", (event) => {
    state.product[state.family] = event.target.value;
    $("result").hidden = true;
    inspect();
  });

  document.querySelectorAll(".seg").forEach((group) => {
    group.addEventListener("click", (event) => {
      const button = event.target.closest("button");
      if (!button) return;
      group.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === button));
      state.seg[group.dataset.name] = button.dataset.value;
      if (group.dataset.name === "orientation") inspect();
      if (group.dataset.name === "up_scale") $("tool-run").textContent = TOOLS.up.label();
    });
  });

  const drop = $("drop"), file = $("file");
  drop.addEventListener("click", () => file.click());
  drop.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); file.click(); }
  });
  file.addEventListener("change", () => { upload(file.files[0]); file.value = ""; });
  ["dragenter", "dragover"].forEach((name) => drop.addEventListener(name, (event) => {
    event.preventDefault(); drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((name) => drop.addEventListener(name, (event) => {
    event.preventDefault(); drop.classList.remove("over");
  }));
  drop.addEventListener("drop", (event) => upload(event.dataTransfer.files[0]));
  // Dropping anywhere on the page works too.
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => {
    if (event.target.closest && event.target.closest("#drop")) return;
    event.preventDefault();
    upload(event.dataTransfer.files[0]);
  });

  $("replace").addEventListener("click", () => file.click());
  document.querySelectorAll(".tool-tabs button").forEach((button) =>
    button.addEventListener("click", () => setTool(button.dataset.tool)));
  $("tool-run").addEventListener("click", applyTool);
  $("tool-undo").addEventListener("click", undoTool);
  $("run").addEventListener("click", run);
  $("clear").addEventListener("click", async () => {
    await busy("Nettoyage…", () => post("/api/workspace/clear", {}));
    state.asset = null;
    $("job").hidden = true;
    toast("Atelier vidé : plus aucune image sur le disque.");
  });
}

(async function init() {
  wire();
  try {
    state.products = await api("/api/products");
    fillProducts();
  } catch (error) { toast(error.message, true); }
  try { applyCaps(await api("/api/capabilities")); } catch (_) { applyCaps({}); }
})();
