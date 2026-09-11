/* PrintPro — interface de l'atelier. */
"use strict";

const $ = (id) => document.getElementById(id);
const E = Engine;

const state = {
  assets: [],
  currentId: null,
  sheet: null,          // dernier montage calculé
  downloads: null,      // capacité d'enregistrement, si le visionneur l'accorde
  counter: 0,
};

/* ------------------------------------------------------------ utils -- */
function flash(message, isError = false) {
  const node = $("flash");
  node.textContent = message;
  node.classList.toggle("bad", isError);
  node.classList.add("on");
  clearTimeout(flash.timer);
  flash.timer = setTimeout(() => node.classList.remove("on"), 3600);
}

/** Affiche le voile, laisse le navigateur peindre, puis calcule. */
async function busy(label, work) {
  $("veil-text").textContent = label;
  $("veil").classList.add("on");
  await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  try {
    return await work();
  } catch (error) {
    console.error(error);
    flash(error && error.message ? error.message : String(error), true);
    return null;
  } finally {
    $("veil").classList.remove("on");
  }
}

const current = () => state.assets.find((asset) => asset.id === state.currentId) || null;
const image = (asset) => asset.versions[asset.versions.length - 1];

function previewUrl(source, maximum = 460) {
  const scale = Math.min(1, maximum / Math.max(source.width, source.height));
  const shown = scale < 1
    ? E.resize(source, Math.round(source.width * scale), Math.round(source.height * scale))
    : source;
  return E.canvasOf(shown).toDataURL("image/png");
}

const toBlob = (canvas, type = "image/png", quality) =>
  new Promise((resolve) => canvas.toBlob(resolve, type, quality));

/** Enregistre un fichier : capacité du visionneur, sinon lien classique. */
async function saveFile(filename, data) {
  if (state.downloads) {
    try {
      await state.downloads.save({ filename, data });
      flash(`${filename} enregistré`);
      return;
    } catch (error) {
      const code = error && error.code;
      if (code === "declined") return;
      if (code !== "unavailable" && code !== "not_granted") {
        flash(`Enregistrement impossible : ${(error && error.message) || code}`, true);
        return;
      }
      state.downloads = null;           // on bascule sur le lien direct
    }
  }
  const blob = data instanceof Blob ? data : new Blob([data]);
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}

/* -------------------------------------------------------- visuels --- */
function addAsset(name, source) {
  const asset = {
    id: `a${++state.counter}`,
    name: name.replace(/\.[^.]+$/, ""),
    versions: [source],
    history: [],
    svg: null,
    widthMm: Math.round(Montage.pxToMm(source.width, 300) * 10) / 10,
    quantity: 1,
    selected: true,
  };
  state.assets.push(asset);
  state.currentId = asset.id;
  return asset;
}

async function importFiles(files) {
  const list = [...files].filter((file) => file.type.startsWith("image/"));
  if (!list.length) return;
  await busy(`Import de ${list.length} visuel(s)…`, async () => {
    for (const file of list) {
      const bitmap = await createImageBitmap(file);
      const canvas = document.createElement("canvas");
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      canvas.getContext("2d").drawImage(bitmap, 0, 0);
      bitmap.close();
      addAsset(file.name, canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height));
    }
  });
  renderAll();
  flash(`${list.length} visuel(s) importé(s)`);
}

function pushVersion(asset, next, label) {
  asset.versions.push(next);
  asset.history.push(label);
  if (asset.versions.length > 6) asset.versions.shift();   // mémoire bornée
}

/* ----------------------------------------------------------- rendu -- */
function renderAll() {
  renderLibrary();
  renderStage();
  renderJob();
}

function renderLibrary() {
  const box = $("library");
  box.textContent = "";
  state.assets.forEach((asset) => {
    const source = image(asset);
    const tile = document.createElement("div");
    tile.className = "tile";
    tile.setAttribute("aria-pressed", asset.id === state.currentId ? "true" : "false");

    const pick = document.createElement("input");
    pick.type = "checkbox";
    pick.className = "pick";
    pick.checked = asset.selected;
    pick.id = `pick-${asset.id}`;
    pick.title = "Inclure dans la planche";
    pick.addEventListener("change", () => {
      asset.selected = pick.checked;
      renderJob();
      renderLibrary();
    });

    const remove = document.createElement("button");
    remove.className = "drop-one";
    remove.type = "button";
    remove.textContent = "✕";
    remove.title = "Retirer";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      state.assets = state.assets.filter((item) => item.id !== asset.id);
      if (state.currentId === asset.id) state.currentId = state.assets[0]?.id || null;
      renderAll();
    });

    const art = document.createElement("button");
    art.className = "art";
    art.type = "button";
    art.title = "Travailler ce visuel";
    const thumb = document.createElement("img");
    thumb.src = previewUrl(source, 180);
    thumb.alt = asset.name;
    art.appendChild(thumb);
    art.addEventListener("click", () => { state.currentId = asset.id; renderAll(); });

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${asset.name} · ${source.width}×${source.height}`;

    tile.append(pick, remove, art, meta);
    box.appendChild(tile);
  });

  const chosen = state.assets.filter((asset) => asset.selected).length;
  $("library-note").textContent = state.assets.length
    ? `${state.assets.length} visuel(s) · ${chosen} sur la planche`
    : "Aucun visuel pour l'instant.";
}

function renderStage() {
  const asset = current();
  const stage = $("stage");
  stage.textContent = "";
  $("save-svg").disabled = !asset || !asset.svg;
  $("undo").disabled = !asset || asset.versions.length < 2;

  if (!asset) {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = "Déposez un visuel pour commencer.";
    stage.appendChild(hint);
    $("dims").textContent = "—";
    $("trail").textContent = "";
    updateQuality();
    return;
  }

  const source = image(asset);
  const shown = document.createElement("img");
  shown.src = previewUrl(source, 1100);
  shown.alt = asset.name;
  stage.appendChild(shown);

  let transparent = false;
  for (let i = 3; i < source.data.length; i += 4) {
    if (source.data[i] < 250) { transparent = true; break; }
  }
  const mmW = Math.round(Montage.pxToMm(source.width, 300) * 10) / 10;
  const mmH = Math.round(Montage.pxToMm(source.height, 300) * 10) / 10;
  $("dims").innerHTML =
    `<b>${source.width}×${source.height}</b> px · ${mmW}×${mmH} mm à 300 dpi` +
    (transparent ? " · fond transparent" : "");

  const trail = $("trail");
  trail.textContent = "";
  asset.history.forEach((step) => {
    const tag = document.createElement("span");
    tag.textContent = step;
    trail.appendChild(tag);
  });
  updateQuality();
}

function updateQuality() {
  const asset = current();
  const chip = $("quality");
  const needed = $("needed");
  if (!asset) {
    chip.textContent = "—";
    chip.className = "chip";
    needed.textContent = "";
    return;
  }
  const source = image(asset);
  const mm = Number($("print-mm").value) || 100;
  const mmHeight = mm * source.height / source.width;
  const report = Tools.printQuality(source.width, source.height, mm, mmHeight);
  chip.textContent = `${report.dpi} dpi — ${report.label}`;
  chip.className = "chip " + (report.grade === "faible" ? "bad"
    : report.grade === "excellent" || report.grade === "bon" ? "ok" : "warn");
  const want = Math.round(mm / 25.4 * 300);
  needed.textContent = report.dpi < 299.5
    ? `il faudrait ${want} px de large pour 300 dpi`
    : "prêt pour l'impression";
}

/* ----------------------------------------------------- traitements -- */
async function runCut() {
  const asset = current();
  if (!asset) return flash("Choisissez un visuel", true);
  const method = $("cut-method").value;
  const result = await busy("Détourage…", async () => Tools.removeBackground(image(asset), {
    method,
    color: method === "color" ? $("cut-color").value : null,
    tolerance: Number($("cut-tol").value),
    softness: Number($("cut-soft").value),
    edgeShift: Number($("cut-shift").value),
    keepHoles: $("cut-holes").checked,
    largestOnly: $("cut-largest").checked,
    trim: $("cut-trim").checked,
  }));
  if (!result) return;
  pushVersion(asset, result.image, "détourage");
  asset.svg = null;
  $("cut-note").textContent =
    `Sujet : ${Math.round(result.coverage * 100)} % de l'image. ` +
    `Fond retiré : ${result.backgroundColors.map((c) => E.rgbToHex(c)).join(", ")}.`;
  renderAll();
}

async function runUpscale() {
  const asset = current();
  if (!asset) return flash("Choisissez un visuel", true);
  const source = image(asset);
  const options = {
    method: $("up-method").value === "smooth" ? "lanczos" : $("up-method").value,
    denoise: Number($("up-denoise").value),
    sharpen: Number($("up-sharpen").value),
  };
  if ($("up-target").value === "mm") {
    const dpi = Number($("up-dpi").value);
    options.targetWidth = Math.round(Number($("up-value").value) / 25.4 * dpi);
  } else {
    options.scale = Number($("up-value").value);
  }
  const result = await busy("Agrandissement…", async () => Tools.upscale(source, options));
  if (!result) return;
  pushVersion(asset, result.image, `agrandissement ×${result.factor.toFixed(2)}`);
  asset.svg = null;
  $("up-note").textContent =
    `${source.width}×${source.height} → ${result.width}×${result.height} px` +
    (result.notes.length ? ` — ${result.notes.join(" ; ")}` : "");
  renderAll();
}

const TRACE_MAX_PIXELS = 4e6;

async function runVectorize() {
  const asset = current();
  if (!asset) return flash("Choisissez un visuel", true);
  const source = image(asset);

  /* Au-delà de 4 Mpx le tracé se fait sur une version réduite : le résultat
     reste vectoriel, donc imprimable à n'importe quelle taille, et le calcul
     ne bloque pas l'onglet pendant une minute. */
  const pixels = source.width * source.height;
  const reduction = pixels > TRACE_MAX_PIXELS ? Math.sqrt(TRACE_MAX_PIXELS / pixels) : 1;
  const traced = reduction < 1
    ? E.resize(source, Math.round(source.width * reduction), Math.round(source.height * reduction))
    : source;

  const smoothing = Number($("vec-smooth").value);
  const result = await busy("Vectorisation…", async () => Tools.vectorize(traced, {
    mode: $("vec-mode").value,
    colors: Number($("vec-colors").value),
    detail: Number($("vec-detail").value),
    smoothing,
    minArea: Number($("vec-area").value),
    dropBackground: $("vec-dropbg").checked,
  }));
  if (!result) return;

  // Le viewBox garde l'échelle du tracé, width/height rendent la taille réelle.
  asset.svg = reduction < 1
    ? result.svg.replace(`width="${result.width}" height="${result.height}"`,
                         `width="${source.width}" height="${source.height}"`)
    : result.svg;

  if ($("vec-replace").checked) {
    const rendered = await busy("Rendu du tracé…", async () => Tools.renderLayers(
      result.layers, result.width, result.height, 1 / reduction, { smoothing }));
    if (rendered) pushVersion(asset, rendered, `vectorisation · ${result.shapes} formes`);
  }
  const kilobytes = (new Blob([asset.svg]).size / 1024).toFixed(1);
  $("vec-note").innerHTML =
    `${result.shapes} formes · ${result.nodes} nœuds · ${kilobytes} Ko` +
    (reduction < 1 ? ` · tracé sur ${traced.width}×${traced.height} px, SVG à l'échelle réelle` : "") +
    `<span class="swatches">${result.palette
      .map((c) => `<i style="background:${E.rgbToHex(c)}"></i>`).join("")}</span>`;
  renderAll();
  flash("SVG prêt — bouton SVG au-dessus");
}

/* --------------------------------------------------------- planche -- */
function renderJob() {
  const box = $("job");
  box.textContent = "";
  const chosen = state.assets.filter((asset) => asset.selected);
  if (!chosen.length) {
    const note = document.createElement("p");
    note.className = "note";
    note.textContent = "Cochez au moins un visuel dans la colonne Visuels.";
    box.appendChild(note);
    return;
  }
  chosen.forEach((asset) => {
    const row = document.createElement("div");
    row.className = "job-row";

    const art = document.createElement("div");
    art.className = "art";
    const thumb = document.createElement("img");
    thumb.src = previewUrl(image(asset), 60);
    thumb.alt = "";
    art.appendChild(thumb);

    const name = document.createElement("span");
    name.className = "name";
    name.textContent = asset.name;
    name.title = asset.name;

    const width = document.createElement("input");
    width.type = "number";
    width.id = `w-${asset.id}`;
    width.min = "1"; width.max = "3000"; width.step = "1";
    width.value = asset.widthMm;
    width.setAttribute("aria-label", `Largeur de ${asset.name} en mm`);
    width.addEventListener("change", () => {
      asset.widthMm = Math.max(1, Number(width.value) || 1);
    });

    const quantity = document.createElement("input");
    quantity.type = "number";
    quantity.id = `q-${asset.id}`;
    quantity.min = "1"; quantity.max = "999"; quantity.step = "1";
    quantity.value = asset.quantity;
    quantity.setAttribute("aria-label", `Quantité de ${asset.name}`);
    quantity.addEventListener("change", () => {
      asset.quantity = Math.max(1, Number(quantity.value) || 1);
    });

    row.append(art, name, width, quantity);
    box.appendChild(row);
  });
}

async function runSheet() {
  const chosen = state.assets.filter((asset) => asset.selected);
  if (!chosen.length) return flash("Cochez au moins un visuel", true);

  const result = await busy("Montage de la planche…", async () => Montage.build(
    chosen.map((asset) => ({
      image: image(asset), widthMm: asset.widthMm,
      quantity: asset.quantity, label: asset.name,
    })), {
      page: $("m-page").value,
      orientation: $("m-orient").value,
      dpi: Number($("m-dpi").value),
      layout: $("m-layout").value,
      marginMm: Number($("m-margin").value),
      spacingMm: Number($("m-space").value),
      background: $("m-transparent").checked ? null : $("m-bg").value,
      mirror: $("m-mirror").checked,
      cropMarks: $("m-marks").checked,
      registrationMarks: $("m-reg").checked,
      outline: $("m-outline").checked,
      cutContourMm: Number($("m-cut").value),
      cutStroke: Number($("m-cut").value) > 0,
    }));
  if (!result) return;
  state.sheet = result;

  const rendered = await busy("Rendu des planches…", async () =>
    result.pages.map((_, index) => Montage.renderPage(result, index)));
  if (!rendered) return;
  state.sheet.rendered = rendered;

  const [pageW, pageH] = result.pageSizeMm;
  $("sheet-stats").innerHTML = [
    `<span class="stat"><b>${result.placed}</b>/${result.requested} placés</span>`,
    `<span class="stat"><b>${result.pages.length}</b> planche(s)</span>`,
    `<span class="stat">${pageW}×${pageH} mm</span>`,
    `<span class="stat">remplissage <b>${Math.round(result.efficiency * 100)} %</b></span>`,
    `<span class="stat">${result.options.dpi} dpi</span>`,
  ].join("");

  const sheets = $("sheets");
  sheets.textContent = "";
  rendered.forEach((page, index) => {
    const figure = document.createElement("figure");
    const preview = document.createElement("img");
    preview.src = page.canvas.toDataURL("image/png");
    preview.alt = `Planche ${index + 1}`;
    const caption = document.createElement("figcaption");
    caption.textContent = `Planche ${index + 1} — ${page.canvas.width}×${page.canvas.height} px` +
      (page.reduced ? ` · aperçu à ${page.dpi} dpi, le PDF reste à ${result.options.dpi} dpi` : "");
    figure.append(preview, caption);
    sheets.appendChild(figure);
  });

  const downloads = $("sheet-downloads");
  downloads.textContent = "";
  const pdfButton = document.createElement("button");
  pdfButton.type = "button";
  pdfButton.textContent = "Télécharger le PDF";
  pdfButton.addEventListener("click", async () => {
    const blob = await busy("Écriture du PDF…", () => Montage.exportPdf(state.sheet));
    if (blob) await saveFile("planche-printpro.pdf", blob);
  });
  downloads.appendChild(pdfButton);

  rendered.forEach((page, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "second";
    button.textContent = `PNG ${index + 1}`;
    button.addEventListener("click", async () => {
      const blob = await busy("Export PNG…", () => toBlob(page.canvas));
      if (blob) await saveFile(`planche-${index + 1}.png`, blob);
    });
    downloads.appendChild(button);
  });

  result.warnings.forEach((warning) => flash(warning, true));
  flash(`${result.placed} visuels sur ${result.pages.length} planche(s)`);
}

/* ------------------------------------------------------ exemple ----- */
/* La page s'ouvre sur un visuel de démonstration : l'atelier montre
   immédiatement ce qu'il sait faire, sans attendre un import. */
function demoArtwork() {
  const size = 360;
  const canvas = document.createElement("canvas");
  canvas.width = size; canvas.height = size;
  const context = canvas.getContext("2d");
  context.fillStyle = "#1fb2aa";
  context.fillRect(0, 0, size, size);

  context.beginPath();
  const middle = size / 2;
  for (let point = 0; point < 10; point++) {
    const angle = -Math.PI / 2 + point * Math.PI / 5;
    const radius = point % 2 === 0 ? 148 : 62;
    const x = middle + radius * Math.cos(angle);
    const y = middle + radius * Math.sin(angle);
    point ? context.lineTo(x, y) : context.moveTo(x, y);
  }
  context.closePath();
  context.fillStyle = "#ffc929";
  context.fill();
  context.lineWidth = 4;
  context.strokeStyle = "#5a3e00";
  context.stroke();

  context.fillStyle = "#5a3e00";
  context.font = "600 30px Archivo, sans-serif";
  context.textAlign = "center";
  context.fillText("EXEMPLE", middle, size - 34);
  return context.getImageData(0, 0, size, size);
}

/* -------------------------------------------------------- démarrage -- */
function bindRange(rangeId, labelId, decimals = 0, suffix = "") {
  const range = $(rangeId), label = $(labelId);
  const sync = () => { label.textContent = Number(range.value).toFixed(decimals) + suffix; };
  range.addEventListener("input", sync);
  sync();
}

function bindTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((other) => {
        const on = other === tab;
        other.setAttribute("aria-selected", on ? "true" : "false");
        $(other.getAttribute("aria-controls")).classList.toggle("on", on);
      });
    });
  });
}

function start() {
  Object.entries(Montage.PAGES).forEach(([name, [w, h]]) => {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = `${name} — ${w}×${h} mm`;
    if (name === "A4") option.selected = true;
    $("m-page").appendChild(option);
  });

  bindTabs();
  bindRange("cut-tol", "cut-tol-v");
  bindRange("cut-soft", "cut-soft-v");
  bindRange("cut-shift", "cut-shift-v", 0, " px");
  bindRange("up-denoise", "up-denoise-v", 2);
  bindRange("up-sharpen", "up-sharpen-v", 2);
  bindRange("vec-colors", "vec-colors-v");
  bindRange("vec-detail", "vec-detail-v", 1, " px");
  bindRange("vec-smooth", "vec-smooth-v", 1);
  bindRange("vec-area", "vec-area-v", 0, " px²");

  $("drop").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (event) => {
    importFiles(event.target.files);
    event.target.value = "";
  });
  ["dragenter", "dragover"].forEach((type) => $("drop").addEventListener(type, (event) => {
    event.preventDefault();
    $("drop").classList.add("hot");
  }));
  ["dragleave", "drop"].forEach((type) => $("drop").addEventListener(type, (event) => {
    event.preventDefault();
    $("drop").classList.remove("hot");
  }));
  $("drop").addEventListener("drop", (event) => importFiles(event.dataTransfer.files));
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => event.preventDefault());

  $("cut-method").addEventListener("change", (event) => {
    $("cut-color-field").hidden = event.target.value !== "color";
  });
  $("up-target").addEventListener("change", (event) => {
    const physical = event.target.value === "mm";
    $("up-dpi-field").hidden = !physical;
    $("up-value").value = physical ? 200 : 2;
  });

  $("run-cut").addEventListener("click", runCut);
  $("run-up").addEventListener("click", runUpscale);
  $("run-vec").addEventListener("click", runVectorize);
  $("run-sheet").addEventListener("click", runSheet);
  $("print-mm").addEventListener("input", updateQuality);

  $("undo").addEventListener("click", () => {
    const asset = current();
    if (!asset || asset.versions.length < 2) return;
    asset.versions.pop();
    asset.history.pop();
    asset.svg = null;
    renderAll();
    flash("Étape annulée");
  });

  $("save-png").addEventListener("click", async () => {
    const asset = current();
    if (!asset) return;
    const blob = await busy("Export PNG…", () => toBlob(E.canvasOf(image(asset))));
    if (blob) await saveFile(`${asset.name}-printpro.png`, blob);
  });

  $("save-svg").addEventListener("click", async () => {
    const asset = current();
    if (!asset || !asset.svg) return;
    await saveFile(`${asset.name}.svg`, new Blob([asset.svg], { type: "image/svg+xml" }));
  });

  $("reset").addEventListener("click", () => {
    state.assets = [];
    state.currentId = null;
    state.sheet = null;
    $("sheets").innerHTML =
      '<p class="empty-sheet">La planche générée s\'affichera ici, prête à télécharger en PDF ou en PNG.</p>';
    $("sheet-stats").textContent = "";
    $("sheet-downloads").textContent = "";
    ["cut-note", "up-note", "vec-note"].forEach((id) => { $(id).textContent = ""; });
    renderAll();
  });

  const demo = addAsset("exemple-etoile", demoArtwork());
  demo.widthMm = 45;
  demo.quantity = 12;
  renderAll();

  // La capacité d'enregistrement arrive après coup ; le lien direct sert d'ici là.
  if (window.claude && typeof window.claude.use === "function") {
    window.claude.use("downloads").then((namespace) => {
      state.downloads = namespace;
    }).catch(() => {});
  }
}

start();
