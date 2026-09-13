/* PrintPro — montage sur planche et export PDF (navigateur). */
"use strict";

const Montage = (() => {
  const E = Engine;
  const MM_PER_INCH = 25.4;
  const PT_PER_MM = 72 / MM_PER_INCH;

  /* Largeur × hauteur en mm, en portrait. */
  const PAGES = {
    "A6": [105, 148], "A5": [148, 210], "A4": [210, 297], "A3": [297, 420],
    "A2": [420, 594], "A1": [594, 841], "A0": [841, 1189],
    "Letter": [215.9, 279.4], "Legal": [215.9, 355.6], "Tabloid": [279.4, 431.8],
    "DTF-30": [300, 1000], "DTF-60": [600, 1000], "Vinyle-50": [500, 1000],
    "Mug": [200, 90], "T-shirt A3": [297, 420],
  };

  const mmToPx = (mm, dpi) => Math.max(1, Math.round(mm / MM_PER_INCH * dpi));
  const pxToMm = (px, dpi) => px * MM_PER_INCH / dpi;

  function pageSize(options) {
    let [w, h] = options.pageWidthMm && options.pageHeightMm
      ? [options.pageWidthMm, options.pageHeightMm]
      : (PAGES[options.page] || PAGES["A4"]);
    if (options.orientation === "landscape" && h > w) [w, h] = [h, w];
    else if (options.orientation === "portrait" && w > h) [w, h] = [h, w];
    return [w, h];
  }

  /* ------------------------------------------------------- placement -- */
  function build(items, options = {}) {
    const opt = Object.assign({
      page: "A4", pageWidthMm: 0, pageHeightMm: 0, orientation: "auto", dpi: 300,
      marginMm: 6, spacingMm: 3, layout: "pack", columns: 0, mirror: false,
      background: "#ffffff", cropMarks: false, registrationMarks: false,
      cutContourMm: 0, cutStroke: false, outline: false, autoTrim: true, maxPages: 30,
    }, options);

    const [pageW, pageH] = pageSize(opt);
    const usableW = pageW - 2 * opt.marginMm;
    const usableH = pageH - 2 * opt.marginMm;
    const warnings = [];
    if (usableW <= 0 || usableH <= 0) throw new Error("Marges trop grandes pour ce format.");

    /* Préparation : rognage, puis contour de découpe autour du visuel. */
    const prepared = items.map((item) => {
      let image = item.image;
      if (opt.autoTrim) image = E.trim(image, 1);
      let widthMm = item.widthMm || pxToMm(image.width, 300);
      let heightMm = item.heightMm || widthMm * image.height / image.width;
      if (opt.cutContourMm > 0) {
        const perMm = image.width / Math.max(widthMm, 1e-6);
        image = addCutContour(image, opt.cutContourMm * perMm, opt.cutStroke);
        widthMm += 2 * opt.cutContourMm;
        heightMm += 2 * opt.cutContourMm;
      }
      return { image, widthMm, heightMm, quantity: Math.max(1, item.quantity | 0),
               rotatable: item.rotatable !== false, label: item.label || "" };
    });

    const requests = [];
    prepared.forEach((item, index) => {
      const fits = item.widthMm <= usableW + 1e-6 && item.heightMm <= usableH + 1e-6;
      const fitsRotated = item.rotatable &&
        item.heightMm <= usableW + 1e-6 && item.widthMm <= usableH + 1e-6;
      if (!fits && !fitsRotated) {
        warnings.push(`« ${item.label || "visuel " + (index + 1)} » ` +
          `(${Math.round(item.widthMm)}×${Math.round(item.heightMm)} mm) dépasse la zone ` +
          `utile (${Math.round(usableW)}×${Math.round(usableH)} mm)`);
        return;
      }
      const quantity = opt.layout === "fill"
        ? fillCount(item.widthMm, item.heightMm, usableW, usableH, opt.spacingMm)
        : item.quantity;
      for (let n = 0; n < quantity; n++) {
        requests.push({ index, widthMm: item.widthMm, heightMm: item.heightMm });
      }
    });
    requests.sort((a, b) => b.widthMm * b.heightMm - a.widthMm * a.heightMm);

    const pages = opt.layout === "grid"
      ? layoutGrid(requests, usableW, usableH, opt)
      : layoutPack(requests, prepared, usableW, usableH, opt);
    if (pages.length > opt.maxPages) {
      warnings.push(`sortie limitée à ${opt.maxPages} planches`);
      pages.length = opt.maxPages;
    }
    pages.forEach((page) => page.forEach((placement) => {
      placement.xMm += opt.marginMm;
      placement.yMm += opt.marginMm;
    }));

    const placed = pages.reduce((total, page) => total + page.length, 0);
    const used = pages.flat().reduce((total, p) => total + p.wMm * p.hMm, 0);
    return {
      pages, items: prepared, options: opt, pageSizeMm: [pageW, pageH],
      placed, requested: requests.length, warnings,
      efficiency: Math.min(1, used / Math.max(1e-6, usableW * usableH * Math.max(1, pages.length))),
    };
  }

  function fillCount(w, h, usableW, usableH, gap) {
    let best = 0;
    for (const [a, b] of [[w, h], [h, w]]) {
      const columns = Math.floor((usableW + gap) / (a + gap));
      const rows = Math.floor((usableH + gap) / (b + gap));
      best = Math.max(best, columns * rows);
    }
    return Math.max(1, best);
  }

  function layoutGrid(requests, usableW, usableH, opt) {
    if (!requests.length) return [];
    const cellW = Math.max(...requests.map((r) => r.widthMm));
    const cellH = Math.max(...requests.map((r) => r.heightMm));
    const gap = opt.spacingMm;
    const columns = opt.columns || Math.max(1, Math.floor((usableW + gap) / (cellW + gap)));
    const rows = Math.max(1, Math.floor((usableH + gap) / (cellH + gap)));
    const perPage = Math.max(1, columns * rows);

    const pages = [];
    requests.forEach((request, position) => {
      const pageNumber = Math.floor(position / perPage);
      if (pageNumber >= opt.maxPages) return;
      while (pages.length <= pageNumber) pages.push([]);
      const slot = position % perPage;
      const row = Math.floor(slot / columns), column = slot % columns;
      pages[pageNumber].push({
        item: request.index,
        xMm: column * (cellW + gap) + (cellW - request.widthMm) / 2,
        yMm: row * (cellH + gap) + (cellH - request.heightMm) / 2,
        wMm: request.widthMm, hMm: request.heightMm, rotated: false,
      });
    });
    return pages;
  }

  function layoutPack(requests, items, usableW, usableH, opt) {
    const gap = opt.spacingMm;
    const pages = [], bins = [];
    for (const request of requests) {
      let done = false;
      for (let number = 0; number < bins.length; number++) {
        const spot = bins[number].insert(request.widthMm + gap, request.heightMm + gap,
                                         items[request.index].rotatable);
        if (!spot) continue;
        pages[number].push(placementOf(request, spot));
        done = true;
        break;
      }
      if (done) continue;
      if (bins.length >= opt.maxPages) break;
      const packer = new MaxRects(usableW + gap, usableH + gap);
      const spot = packer.insert(request.widthMm + gap, request.heightMm + gap,
                                 items[request.index].rotatable);
      bins.push(packer);
      pages.push([]);
      if (spot) pages[pages.length - 1].push(placementOf(request, spot));
    }
    return pages;
  }

  const placementOf = (request, spot) => ({
    item: request.index, xMm: spot.x, yMm: spot.y,
    wMm: spot.rotated ? request.heightMm : request.widthMm,
    hMm: spot.rotated ? request.widthMm : request.heightMm,
    rotated: spot.rotated,
  });

  /** Bin packing MaxRects, heuristique « best short side fit ». */
  class MaxRects {
    constructor(width, height) { this.free = [[0, 0, width, height]]; }

    insert(w, h, allowRotate) {
      let best = null, bestScore = [Infinity, Infinity];
      for (const rect of this.free) {
        for (const rotated of allowRotate ? [false, true] : [false]) {
          const rw = rotated ? h : w, rh = rotated ? w : h;
          if (rw > rect[2] + 1e-9 || rh > rect[3] + 1e-9) continue;
          const leftoverX = rect[2] - rw, leftoverY = rect[3] - rh;
          const score = [Math.min(leftoverX, leftoverY), Math.max(leftoverX, leftoverY)];
          if (score[0] < bestScore[0] || (score[0] === bestScore[0] && score[1] < bestScore[1])) {
            bestScore = score;
            best = { x: rect[0], y: rect[1], w: rw, h: rh, rotated };
          }
        }
      }
      if (!best) return null;
      this.split(best);
      this.prune();
      return best;
    }

    split({ x, y, w, h }) {
      const updated = [];
      for (const [fx, fy, fw, fh] of this.free) {
        if (x >= fx + fw || x + w <= fx || y >= fy + fh || y + h <= fy) {
          updated.push([fx, fy, fw, fh]);
          continue;
        }
        if (x > fx) updated.push([fx, fy, x - fx, fh]);
        if (x + w < fx + fw) updated.push([x + w, fy, fx + fw - (x + w), fh]);
        if (y > fy) updated.push([fx, fy, fw, y - fy]);
        if (y + h < fy + fh) updated.push([fx, y + h, fw, fy + fh - (y + h)]);
      }
      this.free = updated.filter((r) => r[2] > 1e-6 && r[3] > 1e-6);
    }

    prune() {
      this.free = this.free.filter((a, i) => !this.free.some((b, j) =>
        i !== j && a[0] >= b[0] - 1e-9 && a[1] >= b[1] - 1e-9 &&
        a[0] + a[2] <= b[0] + b[2] + 1e-9 && a[1] + a[3] <= b[1] + b[3] + 1e-9));
    }
  }

  /** Bordure opaque autour du visuel : le contour de découpe d'un sticker. */
  function addCutContour(image, offsetPx, stroke) {
    const offset = Math.max(1, Math.round(offsetPx));
    const pad = offset + (stroke ? 2 : 0) + 2;
    const w = image.width + 2 * pad, h = image.height + 2 * pad;
    const padded = E.makeImage(w, h);
    for (let y = 0; y < image.height; y++) {
      const from = y * image.width * 4;
      padded.data.set(image.data.subarray(from, from + image.width * 4),
                      ((y + pad) * w + pad) * 4);
    }
    const alpha = new Uint8Array(w * h);
    for (let i = 0; i < w * h; i++) alpha[i] = padded.data[i * 4 + 3] > 40 ? 1 : 0;
    const grown = E.dilate(alpha, w, h, offset);
    for (let i = 0; i < w * h; i++) {
      if (!grown[i] || alpha[i]) continue;
      padded.data[i * 4] = 255; padded.data[i * 4 + 1] = 255;
      padded.data[i * 4 + 2] = 255; padded.data[i * 4 + 3] = 255;
    }
    if (stroke) {
      const edge = E.dilate(grown, w, h, 2);
      for (let i = 0; i < w * h; i++) {
        if (!edge[i] || grown[i]) continue;
        padded.data[i * 4] = 60; padded.data[i * 4 + 1] = 60;
        padded.data[i * 4 + 2] = 60; padded.data[i * 4 + 3] = 255;
      }
    }
    return padded;
  }

  /* ----------------------------------------------------------- rendu -- */
  function renderPage(result, index, options = {}) {
    const opt = result.options;
    const maxPixels = options.maxPixels || Math.min(24e6, E.maxCanvasPixels());
    const [pageW, pageH] = result.pageSizeMm;
    let dpi = options.dpi || opt.dpi;
    let width = mmToPx(pageW, dpi), height = mmToPx(pageH, dpi);
    let reduced = false;
    if (width * height > maxPixels) {
      dpi = Math.floor(dpi * Math.sqrt(maxPixels / (width * height)));
      width = mmToPx(pageW, dpi); height = mmToPx(pageH, dpi);
      reduced = true;
    }

    const canvas = document.createElement("canvas");
    canvas.width = width; canvas.height = height;
    const context = canvas.getContext("2d");
    if (opt.background) {
      context.fillStyle = opt.background;
      context.fillRect(0, 0, width, height);
    }
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";

    result.pages[index].forEach((placement) => {
      const patch = patchFor(result, placement, dpi);
      context.drawImage(E.canvasOf(patch),
                        mmToPx(placement.xMm, dpi), mmToPx(placement.yMm, dpi),
                        patch.width, patch.height);
    });
    drawMarks(context, result, index, dpi, width, height);

    if (opt.mirror) {
      const flipped = document.createElement("canvas");
      flipped.width = width; flipped.height = height;
      const flipContext = flipped.getContext("2d");
      flipContext.translate(width, 0);
      flipContext.scale(-1, 1);
      flipContext.drawImage(canvas, 0, 0);
      return { canvas: flipped, dpi, reduced };
    }
    return { canvas, dpi, reduced };
  }

  function patchFor(result, placement, dpi) {
    let image = result.items[placement.item].image;
    if (placement.rotated) image = E.rotate90(image);
    return E.resize(image, mmToPx(placement.wMm, dpi), mmToPx(placement.hMm, dpi));
  }

  function drawMarks(context, result, index, dpi, width, height) {
    const opt = result.options;
    if (!opt.outline && !opt.cropMarks && !opt.registrationMarks) return;
    const thin = Math.max(1, mmToPx(0.2, dpi));
    const mark = mmToPx(4, dpi);
    context.lineWidth = thin;

    result.pages[index].forEach((placement) => {
      const x0 = mmToPx(placement.xMm, dpi), y0 = mmToPx(placement.yMm, dpi);
      const x1 = x0 + mmToPx(placement.wMm, dpi), y1 = y0 + mmToPx(placement.hMm, dpi);
      if (opt.outline) {
        context.strokeStyle = "rgba(120,130,140,0.85)";
        context.strokeRect(x0, y0, x1 - x0, y1 - y0);
      }
      if (opt.cropMarks) {
        context.strokeStyle = "#000";
        context.beginPath();
        [[x0, y0], [x1, y0], [x0, y1], [x1, y1]].forEach(([cx, cy]) => {
          context.moveTo(cx - mark, cy); context.lineTo(cx - mark / 3, cy);
          context.moveTo(cx, cy - mark); context.lineTo(cx, cy - mark / 3);
        });
        context.stroke();
      }
    });

    if (opt.registrationMarks) {
      const radius = mmToPx(3, dpi);
      const inset = mmToPx(Math.max(3, opt.marginMm / 2), dpi);
      context.strokeStyle = "#000";
      [[inset, inset], [width - inset, inset], [inset, height - inset],
       [width - inset, height - inset]].forEach(([cx, cy]) => {
        context.beginPath();
        context.arc(cx, cy, radius, 0, Math.PI * 2);
        context.moveTo(cx - radius * 1.5, cy); context.lineTo(cx + radius * 1.5, cy);
        context.moveTo(cx, cy - radius * 1.5); context.lineTo(cx, cy + radius * 1.5);
        context.stroke();
      });
    }
  }

  /* ------------------------------------------------------------- PDF -- */
  /* Écriture directe : les visuels sont placés un par un à la résolution
     demandée, donc le PDF garde ses 300 dpi même si l'aperçu est réduit. */
  async function exportPdf(result, quality = 0.95) {
    const opt = result.options;
    const [pageW, pageH] = result.pageSizeMm;
    const objects = [];
    const add = (payload) => { objects.push(payload); return objects.length; };

    objects.push(null);                                  // emplacement /Pages
    const pagesId = 1;
    const pageIds = [];
    const encoder = new TextEncoder();
    const encoded = new Map();     // un même visuel n'est encodé qu'une fois

    for (let index = 0; index < result.pages.length; index++) {
      const resources = [];
      let content = "";
      if (opt.background) {
        const [r, g, b] = E.hexToRgb(opt.background).map((v) => v / 255);
        content += `${r.toFixed(4)} ${g.toFixed(4)} ${b.toFixed(4)} rg\n`;
        content += `0 0 ${(pageW * PT_PER_MM).toFixed(3)} ${(pageH * PT_PER_MM).toFixed(3)} re f\n`;
      }

      const placements = result.pages[index];
      for (let slot = 0; slot < placements.length; slot++) {
        const placement = placements[slot];
        let xMm = placement.xMm;
        if (opt.mirror) xMm = pageW - placement.xMm - placement.wMm;
        const key = `${placement.item}|${placement.rotated ? 1 : 0}|` +
                    `${placement.wMm.toFixed(3)}x${placement.hMm.toFixed(3)}`;
        let imageId = encoded.get(key);
        if (!imageId) {
          let patch = patchFor(result, placement, opt.dpi);
          if (opt.mirror) patch = E.mirror(patch);
          imageId = await writeImage(patch, add, quality, opt.background);
          encoded.set(key, imageId);
        }
        resources.push(`/Im${slot} ${imageId} 0 R`);
        const x = xMm * PT_PER_MM;
        const w = placement.wMm * PT_PER_MM, h = placement.hMm * PT_PER_MM;
        const y = (pageH - placement.yMm - placement.hMm) * PT_PER_MM;   // origine en bas
        content += `q ${w.toFixed(3)} 0 0 ${h.toFixed(3)} ${x.toFixed(3)} ${y.toFixed(3)} cm /Im${slot} Do Q\n`;
        if (opt.cropMarks) content += cropMarkOps(xMm, placement.yMm, placement.wMm, placement.hMm, pageH);
        if (opt.outline) content += frameOps(xMm, placement.yMm, placement.wMm, placement.hMm, pageH);
      }

      const stream = encoder.encode(content);
      const contentId = add(concat(
        encoder.encode(`<< /Length ${stream.length} >>\nstream\n`), stream,
        encoder.encode("\nendstream")));
      const xobjects = resources.length ? `/XObject << ${resources.join(" ")} >> ` : "";
      pageIds.push(add(encoder.encode(
        `<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 ` +
        `${(pageW * PT_PER_MM).toFixed(3)} ${(pageH * PT_PER_MM).toFixed(3)}] ` +
        `/Resources << ${xobjects}>> /Contents ${contentId} 0 R >>`)));
    }

    objects[0] = encoder.encode(
      `<< /Type /Pages /Count ${pageIds.length} /Kids [${pageIds.map((id) => `${id} 0 R`).join(" ")}] >>`);
    const catalogId = add(encoder.encode(`<< /Type /Catalog /Pages ${pagesId} 0 R >>`));
    const infoId = add(encoder.encode("<< /Producer (PrintPro) /Title (Planche PrintPro) >>"));
    return serialize(objects, catalogId, infoId);
  }

  async function writeImage(image, add, quality, background) {
    const encoder = new TextEncoder();
    let transparent = false;
    for (let i = 3; i < image.data.length; i += 4) {
      if (image.data[i] < 250) { transparent = true; break; }
    }

    let maskId = null;
    const deflate = transparent ? await tryDeflate(alphaBytes(image)) : null;
    if (deflate) {
      maskId = add(concat(encoder.encode(
        `<< /Type /XObject /Subtype /Image /Width ${image.width} /Height ${image.height} ` +
        `/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode ` +
        `/Length ${deflate.length} >>\nstream\n`), deflate, encoder.encode("\nendstream")));
    }

    /* Le JPEG doit contenir les couleurs *droites* : un canvas transparent
       encodé tel quel livre des couleurs prémultipliées (assombries), que le
       masque du PDF assombrirait une seconde fois — d'où un liseré noir.
       On rend donc l'image opaque en gardant ses couleurs, les pixels
       entièrement transparents prenant le fond pour limiter le bruit JPEG. */
    const paper = E.hexToRgb(background || "#ffffff");
    const source = maskId ? opaqueCopy(image, paper) : E.flatten(image, paper);
    const jpeg = new Uint8Array(await (await new Promise((resolve) =>
      E.canvasOf(source).toBlob(resolve, "image/jpeg", quality))).arrayBuffer());
    const extra = maskId ? ` /SMask ${maskId} 0 R` : "";
    return add(concat(encoder.encode(
      `<< /Type /XObject /Subtype /Image /Width ${image.width} /Height ${image.height} ` +
      `/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode ` +
      `/Length ${jpeg.length}${extra} >>\nstream\n`), jpeg, encoder.encode("\nendstream")));
  }

  function opaqueCopy(image, paper) {
    const out = E.cloneImage(image);
    const d = out.data;
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] === 0) { d[i] = paper[0]; d[i + 1] = paper[1]; d[i + 2] = paper[2]; }
      d[i + 3] = 255;
    }
    return out;
  }

  function alphaBytes(image) {
    const out = new Uint8Array(image.width * image.height);
    for (let i = 0; i < out.length; i++) out[i] = image.data[i * 4 + 3];
    return out;
  }

  /** /FlateDecode via l'API native du navigateur ; null si indisponible. */
  async function tryDeflate(bytes) {
    if (typeof CompressionStream === "undefined") return null;
    try {
      const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("deflate"));
      return new Uint8Array(await new Response(stream).arrayBuffer());
    } catch (error) {
      return null;
    }
  }

  function cropMarkOps(x, y, w, h, pageH, length = 4) {
    let out = "q 0 0 0 RG 0.425 w\n";
    [[x, y], [x + w, y], [x, y + h], [x + w, y + h]].forEach(([cx, cy]) => {
      const px = cx * PT_PER_MM, py = (pageH - cy) * PT_PER_MM;
      out += `${(px - length * PT_PER_MM).toFixed(2)} ${py.toFixed(2)} m ` +
             `${(px - length / 3 * PT_PER_MM).toFixed(2)} ${py.toFixed(2)} l S\n`;
      out += `${px.toFixed(2)} ${(py + length * PT_PER_MM).toFixed(2)} m ` +
             `${px.toFixed(2)} ${(py + length / 3 * PT_PER_MM).toFixed(2)} l S\n`;
    });
    return out + "Q\n";
  }

  function frameOps(x, y, w, h, pageH) {
    const px = x * PT_PER_MM, py = (pageH - y - h) * PT_PER_MM;
    return `q 0.6 0.6 0.6 RG 0.283 w ${px.toFixed(2)} ${py.toFixed(2)} ` +
           `${(w * PT_PER_MM).toFixed(2)} ${(h * PT_PER_MM).toFixed(2)} re S Q\n`;
  }

  function concat(...chunks) {
    const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    chunks.forEach((chunk) => { out.set(chunk, offset); offset += chunk.length; });
    return out;
  }

  function serialize(objects, catalogId, infoId) {
    const encoder = new TextEncoder();
    const header = concat(encoder.encode("%PDF-1.5\n%"),
                          new Uint8Array([0xE2, 0xE3, 0xCF, 0xD3]),
                          encoder.encode("\n"));
    const parts = [header];
    let offset = header.length;
    const offsets = [];
    objects.forEach((payload, index) => {
      offsets.push(offset);
      const start = encoder.encode(`${index + 1} 0 obj\n`);
      const end = encoder.encode("\nendobj\n");
      parts.push(start, payload, end);
      offset += start.length + payload.length + end.length;
    });
    let xref = `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
    offsets.forEach((value) => {
      xref += `${String(value).padStart(10, "0")} 00000 n \n`;
    });
    xref += `trailer\n<< /Size ${objects.length + 1} /Root ${catalogId} 0 R ` +
            `/Info ${infoId} 0 R >>\nstartxref\n${offset}\n%%EOF\n`;
    parts.push(encoder.encode(xref));
    return new Blob(parts, { type: "application/pdf" });
  }

  return { PAGES, build, renderPage, exportPdf, pageSize, mmToPx, pxToMm, MM_PER_INCH };
})();
