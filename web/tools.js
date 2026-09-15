/* PrintPro — détourage, vectorisation et agrandissement (navigateur). */
"use strict";

const Tools = (() => {
  const E = Engine;

  /* ==================================================== DÉTOURAGE ====== */
  function removeBackground(image, options = {}) {
    const opt = Object.assign({
      method: "auto", color: null, tolerance: 12, softness: 6,
      edgeShift: 0, feather: 0.6, defringe: 1.5, despill: true,
      keepHoles: true, minRegion: 24, largestOnly: false, trim: false,
    }, options);

    const { width: w, height: h } = image;
    const count = w * h;
    const source = image.data;
    const lab = E.toLab(source, count);

    /* --- couleurs de fond : soit imposées, soit apprises sur la bordure */
    let references = [];      // triplets Lab
    let backgroundRgb = [];
    if (opt.method === "color" && opt.color) {
      const rgb = E.hexToRgb(opt.color);
      const value = new Float32Array(3);
      E.labFromRgb(rgb[0], rgb[1], rgb[2], value, 0);
      references.push(value);
      backgroundRgb.push(rgb);
    } else {
      const sampled = sampleBorder(source, lab, w, h);
      references = sampled.references;
      backgroundRgb = sampled.rgb;
    }

    /* --- carte de distance colorimétrique, lissée pour absorber le bruit */
    const distance = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      let best = Infinity;
      for (const reference of references) {
        const value = E.deltaE(lab, i * 3, reference, 0);
        if (value < best) best = value;
      }
      distance[i] = best;
    }
    const smoothed = E.blur(distance, w, h, 0.8);

    const low = Math.max(0.5, opt.tolerance);
    const high = low + Math.max(0.5, opt.softness);
    const alpha = new Float32Array(count);
    const sureBackground = new Uint8Array(count);
    for (let i = 0; i < count; i++) {
      const value = smoothed[i];
      alpha[i] = Math.min(1, Math.max(0, (value - low) / (high - low)));
      if (value <= low) sureBackground[i] = 1;
    }

    /* --- seul le fond relié au bord disparaît (option « trous ») */
    if (!opt.keepHoles) {
      const { labels } = E.labelComponents(sureBackground, w, h);
      const connected = new Set();
      for (let x = 0; x < w; x++) {
        if (labels[x]) connected.add(labels[x]);
        if (labels[(h - 1) * w + x]) connected.add(labels[(h - 1) * w + x]);
      }
      for (let y = 0; y < h; y++) {
        if (labels[y * w]) connected.add(labels[y * w]);
        if (labels[y * w + w - 1]) connected.add(labels[y * w + w - 1]);
      }
      for (let i = 0; i < count; i++) {
        if (sureBackground[i] && !connected.has(labels[i])) alpha[i] = 1;
      }
    }

    refine(alpha, w, h, opt);
    if (opt.defringe > 0 && backgroundRgb.length) {
      defringe(source, alpha, w, h, backgroundRgb[0], opt.defringe);
    }

    const out = E.cloneImage(image);
    const data = out.data;
    if (opt.despill && backgroundRgb.length) {
      unmix(data, alpha, count, backgroundRgb[0]);
    }
    let opaque = 0;
    for (let i = 0; i < count; i++) {
      const value = Math.round(alpha[i] * source[i * 4 + 3]);
      data[i * 4 + 3] = value;
      if (value > 8) opaque++;
    }

    let result = out;
    if (opt.trim) result = E.trim(result, 2);
    return {
      image: result,
      coverage: opaque / count,
      backgroundColors: backgroundRgb,
    };
  }

  function sampleBorder(source, lab, w, h) {
    const band = Math.max(2, Math.round(Math.min(w, h) * 0.02));
    const picked = [];
    const step = Math.max(1, Math.round(Math.sqrt((w * h) / 20000)));
    for (let y = 0; y < h; y += step) {
      for (let x = 0; x < w; x += step) {
        const inBand = x < band || x >= w - band || y < band || y >= h - band;
        if (!inBand) continue;
        const i = y * w + x;
        if (source[i * 4 + 3] > 8) picked.push(i);
      }
    }
    if (picked.length < 16) {
      for (let x = 0; x < w; x++) picked.push(x);
    }
    const data = new Float32Array(picked.length * 3);
    picked.forEach((index, slot) => {
      data[slot * 3] = lab[index * 3];
      data[slot * 3 + 1] = lab[index * 3 + 1];
      data[slot * 3 + 2] = lab[index * 3 + 2];
    });

    const k = Math.min(3, picked.length);
    const { centers, assign, counts } = E.kmeans(data, 3, k);
    const total = picked.length;
    const order = [...counts.keys()].sort((a, b) => counts[b] - counts[a]);
    const references = [], rgb = [];
    order.forEach((cluster, rank) => {
      if (rank && counts[cluster] / total < 0.10) return;    // grappe marginale
      references.push(centers.slice(cluster * 3, cluster * 3 + 3));
      let r = 0, g = 0, b = 0, members = 0;
      picked.forEach((index, slot) => {
        if (assign[slot] !== cluster) return;
        r += source[index * 4]; g += source[index * 4 + 1]; b += source[index * 4 + 2];
        members++;
      });
      rgb.push(members ? [r / members, g / members, b / members] : [255, 255, 255]);
    });
    return { references, rgb };
  }

  function refine(alpha, w, h, opt) {
    const count = w * h;
    if (opt.minRegion > 1 || opt.largestOnly) {
      const solid = new Uint8Array(count);
      for (let i = 0; i < count; i++) solid[i] = alpha[i] > 0.5 ? 1 : 0;
      const { labels, count: total, sizes } = E.labelComponents(solid, w, h);
      if (total) {
        let keep;
        if (opt.largestOnly) {
          let biggest = 1;
          for (let i = 1; i <= total; i++) if (sizes[i] > sizes[biggest]) biggest = i;
          keep = (label) => label === biggest;
        } else {
          keep = (label) => sizes[label] >= opt.minRegion;
        }
        for (let i = 0; i < count; i++) {
          if (solid[i] && !keep(labels[i])) alpha[i] = 0;
        }
      }
    }

    if (opt.edgeShift) {
      const solid = new Uint8Array(count);
      for (let i = 0; i < count; i++) solid[i] = alpha[i] > 0.5 ? 1 : 0;
      const steps = Math.max(1, Math.round(Math.abs(opt.edgeShift)));
      const moved = opt.edgeShift > 0 ? E.dilate(solid, w, h, steps) : E.erode(solid, w, h, steps);
      for (let i = 0; i < count; i++) {
        alpha[i] = opt.edgeShift > 0 ? Math.max(alpha[i], moved[i]) : Math.min(alpha[i], moved[i]);
      }
    }

    if (opt.feather > 0) {
      const blurred = E.blur(alpha, w, h, opt.feather);
      for (let i = 0; i < count; i++) alpha[i] = Math.min(1, Math.max(0, blurred[i]));
    }
  }

  /* Le bord anti-crénelé mélange sujet et fond : on recalcule son opacité
     à partir de la couleur locale du sujet, sinon il reste un halo. */
  function defringe(source, alpha, w, h, background, width) {
    const count = w * h;
    const solid = new Uint8Array(count);
    for (let i = 0; i < count; i++) solid[i] = alpha[i] > 0.5 ? 1 : 0;
    const inner = E.erode(solid, w, h, Math.max(1, Math.round(width)));

    const sigma = Math.max(1.5, width * 2);
    const weight = new Float32Array(count);
    const channels = [new Float32Array(count), new Float32Array(count), new Float32Array(count)];
    for (let i = 0; i < count; i++) {
      if (!inner[i]) continue;
      weight[i] = 1;
      channels[0][i] = source[i * 4];
      channels[1][i] = source[i * 4 + 1];
      channels[2][i] = source[i * 4 + 2];
    }
    const blurredWeight = E.blur(weight, w, h, sigma);
    const local = channels.map((channel) => E.blur(channel, w, h, sigma));

    for (let i = 0; i < count; i++) {
      if (!solid[i] || inner[i]) continue;
      const share = Math.max(1e-4, blurredWeight[i]);
      let dot = 0, norm = 0;
      for (let c = 0; c < 3; c++) {
        const subject = local[c][i] / share;
        const axis = subject - background[c];
        dot += (source[i * 4 + c] - background[c]) * axis;
        norm += axis * axis;
      }
      if (norm < 1e-4) continue;
      const estimated = Math.min(1, Math.max(0, dot / norm));
      alpha[i] = Math.min(alpha[i], estimated);
    }
  }

  /* Observé = a·Sujet + (1−a)·Fond → on résout pour le sujet. */
  function unmix(data, alpha, count, background) {
    for (let i = 0; i < count; i++) {
      const a = alpha[i];
      if (a <= 0.02 || a >= 0.98) continue;
      const safe = Math.max(a, 0.05);
      for (let c = 0; c < 3; c++) {
        const value = (data[i * 4 + c] - (1 - a) * background[c]) / safe;
        data[i * 4 + c] = Math.min(255, Math.max(0, value));
      }
    }
  }

  /* =================================================== VECTORISATION === */
  function vectorize(image, options = {}) {
    const opt = Object.assign({
      colors: 8, mode: "color", detail: 1.0, smoothing: 1.0, minArea: 12,
      blur: 0, dropBackground: true, curves: true, superSample: 0,
      mergeDelta: 6, fringeShare: 0.02, fringePx: 3,
    }, options);

    const { width: w, height: h } = image;
    const count = w * h;
    let rgb = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      rgb[i * 3] = image.data[i * 4];
      rgb[i * 3 + 1] = image.data[i * 4 + 1];
      rgb[i * 3 + 2] = image.data[i * 4 + 2];
    }
    if (opt.blur > 0) {
      for (let c = 0; c < 3; c++) {
        const channel = new Float32Array(count);
        for (let i = 0; i < count; i++) channel[i] = rgb[i * 3 + c];
        const blurred = E.blur(channel, w, h, opt.blur);
        for (let i = 0; i < count; i++) rgb[i * 3 + c] = blurred[i];
      }
    }
    const opaque = new Uint8Array(count);
    for (let i = 0; i < count; i++) opaque[i] = image.data[i * 4 + 3] > 110 ? 1 : 0;

    const { labels, palette, centers, lab } = quantize(rgb, opaque, w, h, opt);
    const backgroundIndex = findBackground(labels, w, h);
    const k = palette.length;

    /* Chaque pixel appartient à sa couleur et, pour la part de mélange
       d'anti-crénelage, à la couleur voisine la plus proche : ce dosage place
       le bord entre deux aplats au sous-pixel près, sans lisser les traits fins. */
    const blend = memberships(image, labels, centers, lab, k, count);

    /* Carte de labels sur-échantillonnée : partition exacte de l'image, donc
       ni vide ni recouvrement entre couches, sans dilatation qui empâte. */
    /* À 2×, un trait d'un pixel en diagonale se réduit à des points isolés ;
       à 3× il reste une bande continue. Le facteur suit la taille de l'image. */
    const s = opt.superSample || (count <= 0.6e6 ? 3 : count <= 2.2e6 ? 2 : 1);
    const w2 = w * s, h2 = h * s;
    /* En cas de quasi-égalité entre deux couleurs (marche diagonale d'un trait
       d'un pixel), la couleur la moins étendue l'emporte : c'est toujours le
       trait, jamais l'aplat, qui se joue à un échantillon près. */
    const extent = new Float64Array(k + 1);
    for (let i = 0; i < count; i++) {
      if (labels[i] >= 0) extent[labels[i]]++; else extent[k]++;
    }
    const priority = new Float32Array(k + 1);
    [...extent.keys()].sort((a, b) => extent[b] - extent[a]).forEach((label, rank) => { priority[label] = rank; });
    const map = superLabels(blend, w, h, s, k, priority);
    const minArea2 = Math.max(1, Math.round(opt.minArea * s * s));
    const mapRaw = opt.debug ? Uint8Array.from(map) : null;
    despeckle(map, w2, h2, minArea2);

    /* --- ordre de dessin : la plus grande surface en premier */
    const areas = new Int32Array(k);
    for (let i = 0; i < map.length; i++) if (map[i] < k) areas[map[i]]++;
    const order = [...areas.keys()].sort((a, b) => areas[b] - areas[a]);

    /* Empilement géométrique : chaque couche couvre aussi tout ce que les
       couches suivantes peindront par-dessus. Une jointure est alors toujours
       « bord anti-crénelé sur aplat plein » : aucun liseré clair, et pas de
       trait de recouvrement qui épaissirait les formes. */
    const rankOf = new Int32Array(k + 1).fill(-1);
    order.forEach((index, rank) => {
      if (!(opt.dropBackground && index === backgroundIndex)) rankOf[index] = rank;
    });

    const layers = [];
    let nodes = 0;
    const tolerance = Math.max(0.05, opt.detail) * s;
    const mask = new Uint8Array(map.length);
    order.forEach((index, rank) => {
      if (opt.dropBackground && index === backgroundIndex) return;
      if (!areas[index]) return;
      for (let i = 0; i < map.length; i++) mask[i] = rankOf[map[i]] >= rank ? 1 : 0;

      const rings = traceMask(mask, w2, h2);
      const shapes = groupRings(rings, minArea2);
      const paths = [];
      shapes.forEach((shape) => {
        const processed = [];
        shape.forEach((ring) => {
          let points = refineRing(ring, opt.smoothing, s);
          points = simplifyRing(points, tolerance);
          if (points.length < 3) return;
          if (s !== 1) points = points.map(([x, y, flag]) => [x / s, y / s, flag]);
          processed.push(points);
          nodes += points.length;
        });
        if (processed.length) paths.push(processed);
      });
      if (paths.length) {
        layers.push({ color: palette[index].map(Math.round), paths, area: areas[index] / (s * s) });
      }
    });

    const out = {
      layers, width: w, height: h, nodes,
      palette: palette.map((c) => c.map(Math.round)),
      shapes: layers.reduce((total, layer) => total + layer.paths.length, 0),
      svg: buildSvg(layers, w, h, opt),
    };
    if (opt.debug) Object.assign(out, { labels, map, mapRaw, superSample: s, blend });
    return out;
  }

  /* Pour chaque pixel : sa couleur (a), la couleur voisine la plus proche (b)
     et la part t de b qui explique sa teinte — t vaut 0 au cœur d'un aplat et
     approche 0,5 sur un bord anti-crénelé. L'alpha de la source dose de la
     même façon la « couleur » transparente. */
  function memberships(image, labels, centers, lab, k, count) {
    const first = new Uint8Array(count);
    const second = new Uint8Array(count);
    const part = new Float32Array(count);
    const alpha = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      alpha[i] = image.data[i * 4 + 3] / 255;
      if (alpha[i] <= 0) { first[i] = k; second[i] = k; continue; }
      if (labels[i] < 0) {
        // Pixel semi-transparent hors partition : on le rattache à la couleur
        // la plus proche, pour que sa part opaque compte quand même.
        E.labFromRgb(image.data[i * 4], image.data[i * 4 + 1], image.data[i * 4 + 2], lab, i * 3);
      }
      let a = labels[i], b = a, t = 0;
      if (k > 1) {
        let bestA = Infinity, bestB = Infinity;
        if (a < 0) {
          for (let c = 0; c < k; c++) {
            const d = E.deltaE(lab, i * 3, centers, c * 3);
            if (d < bestA) { bestA = d; a = c; }
          }
        }
        for (let c = 0; c < k; c++) {
          if (c === a) continue;
          const d = E.deltaE(lab, i * 3, centers, c * 3);
          if (d < bestB) { bestB = d; b = c; }
        }
        let axis = 0, along = 0;
        for (let c = 0; c < 3; c++) {
          const v = centers[b * 3 + c] - centers[a * 3 + c];
          axis += v * v;
          along += (lab[i * 3 + c] - centers[a * 3 + c]) * v;
        }
        // t peut dépasser 0,5 : un trait d'un pixel anti-crénelé est un
        // mélange où sa vraie couleur domine à peine ; c'est le vote des
        // voisins sur la carte agrandie qui tranche, pas l'étiquette k-means.
        t = axis > 1e-6 ? Math.max(0, Math.min(1, along / axis)) : 0;
      } else if (a < 0) {
        a = 0; b = 0;
      }
      first[i] = a; second[i] = b; part[i] = t;
    }
    return { first, second, part, alpha, k };
  }

  /* Carte de labels à l'échelle s : à chaque point, les dosages des quatre
     pixels sources voisins sont interpolés et la couleur majoritaire l'emporte.
     La valeur k désigne le transparent. */
  function superLabels({ first, second, part, alpha, k }, w, h, s, priority) {
    const w2 = w * s, h2 = h * s;
    const map = new Uint8Array(w2 * h2);
    const weights = new Float32Array(k + 1);
    const touched = new Int32Array(12);
    const sources = new Int32Array(4), factors = new Float32Array(4);
    for (let Y = 0; Y < h2; Y++) {
      const sy = Math.max(0, Math.min(h - 1, (Y + 0.5) / s - 0.5));
      const y0 = Math.floor(sy), y1 = Math.min(h - 1, y0 + 1), fy = sy - y0;
      for (let X = 0; X < w2; X++) {
        const sx = Math.max(0, Math.min(w - 1, (X + 0.5) / s - 0.5));
        const x0 = Math.floor(sx), x1 = Math.min(w - 1, x0 + 1), fx = sx - x0;
        sources[0] = y0 * w + x0; factors[0] = (1 - fx) * (1 - fy);
        sources[1] = y0 * w + x1; factors[1] = fx * (1 - fy);
        sources[2] = y1 * w + x0; factors[2] = (1 - fx) * fy;
        sources[3] = y1 * w + x1; factors[3] = fx * fy;
        let n = 0;
        for (let q = 0; q < 4; q++) {
          const weight = factors[q];
          if (weight <= 0) continue;
          const index = sources[q];
          const op = alpha[index];
          const clear = weight * (1 - op);
          if (clear > 0) { if (weights[k] === 0) touched[n++] = k; weights[k] += clear; }
          if (op <= 0) continue;
          const a = first[index], b = second[index], t = part[index];
          const own = weight * op * (1 - t), other = weight * op * t;
          if (weights[a] === 0) touched[n++] = a;
          weights[a] += own;
          if (other > 0) { if (weights[b] === 0) touched[n++] = b; weights[b] += other; }
        }
        let best = k, bestWeight = -1;
        for (let j = 0; j < n; j++) {
          const label = touched[j];
          const value = weights[label];
          if (value > bestWeight + 0.02 || (value > bestWeight - 0.02 && priority[label] > priority[best])) {
            bestWeight = value; best = label;
          }
          weights[label] = 0;
        }
        map[Y * w2 + X] = best;
      }
    }
    return map;
  }

  /* Les composantes plus petites que minArea (taches, trous d'épingle)
     prennent la couleur qui les entoure : la partition reste sans vide. */
  function despeckle(map, w, h, minArea) {
    if (minArea <= 1) return;
    const count = w * h;
    const region = new Int32Array(count).fill(-1);
    const sizes = [];
    const stack = new Int32Array(count);
    for (let seed = 0; seed < count; seed++) {
      if (region[seed] >= 0) continue;
      const id = sizes.length, label = map[seed];
      let size = 0, top = 0;
      stack[top++] = seed; region[seed] = id;
      while (top) {
        const i = stack[--top];
        size++;
        const x = i % w, y = (i / w) | 0;
        if (x > 0 && region[i - 1] < 0 && map[i - 1] === label) { region[i - 1] = id; stack[top++] = i - 1; }
        if (x < w - 1 && region[i + 1] < 0 && map[i + 1] === label) { region[i + 1] = id; stack[top++] = i + 1; }
        if (y > 0 && region[i - w] < 0 && map[i - w] === label) { region[i - w] = id; stack[top++] = i - w; }
        if (y < h - 1 && region[i + w] < 0 && map[i + w] === label) { region[i + w] = id; stack[top++] = i + w; }
      }
      sizes.push(size);
    }
    const UNSET = 255;
    let orphans = 0;
    for (let i = 0; i < count; i++) if (sizes[region[i]] < minArea) { map[i] = UNSET; orphans++; }
    if (!orphans) return;
    // Remplissage depuis les voisins attribués, en largeur d'abord.
    let top = 0;
    for (let i = 0; i < count; i++) {
      if (map[i] === UNSET) continue;
      const x = i % w, y = (i / w) | 0;
      if ((x > 0 && map[i - 1] === UNSET) || (x < w - 1 && map[i + 1] === UNSET) ||
          (y > 0 && map[i - w] === UNSET) || (y < h - 1 && map[i + w] === UNSET)) stack[top++] = i;
    }
    let head = 0;
    while (head < top) {
      const i = stack[head++];
      const x = i % w, y = (i / w) | 0, label = map[i];
      if (x > 0 && map[i - 1] === UNSET) { map[i - 1] = label; stack[top++] = i - 1; }
      if (x < w - 1 && map[i + 1] === UNSET) { map[i + 1] = label; stack[top++] = i + 1; }
      if (y > 0 && map[i - w] === UNSET) { map[i - w] = label; stack[top++] = i - w; }
      if (y < h - 1 && map[i + w] === UNSET) { map[i + w] = label; stack[top++] = i + w; }
    }
  }

  function quantize(rgb, opaque, w, h, opt) {
    const count = w * h;
    const labels = new Int32Array(count).fill(-1);

    const lab = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      if (opaque[i]) E.labFromRgb(rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2], lab, i * 3);
    }

    if (opt.mode === "bw") {
      const luminance = new Float32Array(count);
      let sum = 0, total = 0;
      for (let i = 0; i < count; i++) {
        luminance[i] = 0.2126 * rgb[i * 3] + 0.7152 * rgb[i * 3 + 1] + 0.0722 * rgb[i * 3 + 2];
        if (opaque[i]) { sum += luminance[i]; total++; }
      }
      const threshold = otsu(luminance, opaque, count) || (total ? sum / total : 128);
      // Centres = teinte moyenne réelle de chaque classe, pour doser les bords.
      const centers = new Float32Array(6);
      const members = [0, 0];
      for (let i = 0; i < count; i++) {
        if (!opaque[i]) continue;
        const c = luminance[i] <= threshold ? 0 : 1;
        labels[i] = c;
        members[c]++;
        for (let ch = 0; ch < 3; ch++) centers[c * 3 + ch] += lab[i * 3 + ch];
      }
      for (let c = 0; c < 2; c++) {
        if (members[c]) { for (let ch = 0; ch < 3; ch++) centers[c * 3 + ch] /= members[c]; }
        else { centers[c * 3] = c ? 100 : 0; }
      }
      return { labels, palette: [[0, 0, 0], [255, 255, 255]], centers, lab };
    }

    const indices = [];
    for (let i = 0; i < count; i++) if (opaque[i]) indices.push(i);
    if (!indices.length) return { labels, palette: [[0, 0, 0]], centers: new Float32Array(3), lab };
    const stride = Math.max(1, Math.ceil(indices.length / 40000));
    const sample = [];
    for (let s = 0; s < indices.length; s += stride) sample.push(indices[s]);
    const data = new Float32Array(sample.length * 3);
    sample.forEach((index, slot) => {
      data[slot * 3] = lab[index * 3];
      data[slot * 3 + 1] = lab[index * 3 + 1];
      data[slot * 3 + 2] = lab[index * 3 + 2];
    });

    const k = Math.max(1, Math.min(32, opt.colors));
    const { centers, assign } = E.kmeans(data, 3, k);
    const kUsed = Math.min(k, sample.length);
    const palette = [];
    for (let c = 0; c < kUsed; c++) {
      let r = 0, g = 0, b = 0, members = 0;
      sample.forEach((index, slot) => {
        if (assign[slot] !== c) return;
        r += rgb[index * 3]; g += rgb[index * 3 + 1]; b += rgb[index * 3 + 2];
        members++;
      });
      palette.push(members ? [r / members, g / members, b / members] : [128, 128, 128]);
    }
    for (const i of indices) {
      let best = 0, bestDistance = Infinity;
      for (let c = 0; c < kUsed; c++) {
        const distance = E.deltaE(lab, i * 3, centers, c * 3);
        if (distance < bestDistance) { bestDistance = distance; best = c; }
      }
      labels[i] = best;
    }

    let result = { labels, palette, centers };
    if (opt.mergeDelta > 0 && palette.length > 1) result = mergeSimilar(result, count, opt.mergeDelta);
    if (opt.fringeShare > 0 && result.palette.length > 2) {
      result = dissolveFringes(result, w, h, opt.fringeShare, opt.fringePx);
    }
    result.lab = lab;
    return result;
  }

  function otsu(values, opaque, count) {
    const histogram = new Float64Array(256);
    let total = 0;
    for (let i = 0; i < count; i++) {
      if (!opaque[i]) continue;
      histogram[Math.min(255, Math.max(0, Math.round(values[i])))]++;
      total++;
    }
    if (!total) return 128;
    let sum = 0;
    for (let i = 0; i < 256; i++) sum += i * histogram[i] / total;
    let weight = 0, mean = 0, best = 0, threshold = 128;
    for (let i = 0; i < 256; i++) {
      weight += histogram[i] / total;
      if (weight <= 0 || weight >= 1) continue;
      mean += i * histogram[i] / total;
      const between = Math.pow(sum * weight - mean, 2) / (weight * (1 - weight));
      if (between > best) { best = between; threshold = i; }
    }
    return threshold;
  }

  /* k-means sépare volontiers un fond bruité en gris quasi identiques :
     chacun deviendrait des milliers de taches une fois tracé. */
  function mergeSimilar({ labels, palette, centers }, count, minDelta) {
    const n = palette.length;
    const parent = [...Array(n).keys()];
    const find = (i) => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        if (E.deltaE(centers, i * 3, centers, j * 3) < minDelta) {
          const a = find(i), b = find(j);
          if (a !== b) parent[Math.max(a, b)] = Math.min(a, b);
        }
      }
    }
    const groups = new Map();
    for (let i = 0; i < n; i++) {
      const root = find(i);
      if (!groups.has(root)) groups.set(root, []);
      groups.get(root).push(i);
    }
    if (groups.size === n) return { labels, palette, centers };

    const sizes = new Int32Array(n);
    for (let i = 0; i < count; i++) if (labels[i] >= 0) sizes[labels[i]]++;
    const remap = new Int32Array(n).fill(-1);
    const merged = [], mergedCenters = [];
    [...groups.values()].forEach((members, slot) => {
      let total = 0;
      const colour = [0, 0, 0], center = [0, 0, 0];
      members.forEach((member) => {
        const weight = Math.max(1, sizes[member]);
        total += weight;
        for (let c = 0; c < 3; c++) {
          colour[c] += palette[member][c] * weight;
          center[c] += centers[member * 3 + c] * weight;
        }
        remap[member] = slot;
      });
      merged.push(colour.map((v) => v / total));
      mergedCenters.push(...center.map((v) => v / total));
    });
    const out = new Int32Array(labels.length);
    for (let i = 0; i < labels.length; i++) out[i] = labels[i] < 0 ? -1 : remap[labels[i]];
    return { labels: out, palette: merged, centers: new Float32Array(mergedCenters) };
  }

  /* Un halo est rare ET fin ET sa couleur est une interpolation de ses deux
     voisines — un trait fin bien réel (du texte) ne l'est pas. */
  function dissolveFringes({ labels, palette, centers }, w, h, maxShare, thickness) {
    const count = w * h;
    const sizes = new Int32Array(palette.length);
    let total = 0;
    for (let i = 0; i < count; i++) if (labels[i] >= 0) { sizes[labels[i]]++; total++; }
    if (!total) return { labels, palette, centers };

    const doomed = new Set();
    const mask = new Uint8Array(count);
    for (let index = 0; index < palette.length; index++) {
      if (!sizes[index] || sizes[index] / total > maxShare) continue;
      for (let i = 0; i < count; i++) mask[i] = labels[i] === index ? 1 : 0;
      const eroded = E.erode(mask, w, h, thickness);
      let remaining = 0;
      for (let i = 0; i < count; i++) remaining += eroded[i];
      if (remaining > 0.15 * sizes[index]) continue;          // forme épaisse

      const neighbours = neighbourCounts(labels, mask, palette.length, index, w, h);
      if (neighbours.length < 2) continue;                     // trait fin réel
      const [first, second] = neighbours;
      if (second.count < 0.15 * first.count) continue;
      if (isBetween(centers, index, first.index, second.index)) doomed.add(index);
    }
    if (!doomed.size || doomed.size >= palette.length) return { labels, palette, centers };

    /* Les pixels condamnés rejoignent la couleur valide la plus proche :
       la bande se partage entre les deux formes qu'elle séparait. */
    const out = Int32Array.from(labels);
    const queue = [];
    for (let i = 0; i < count; i++) {
      if (labels[i] >= 0 && doomed.has(labels[i])) out[i] = -2;
      else if (labels[i] >= 0) queue.push(i);
    }
    let head = 0;
    while (head < queue.length) {
      const i = queue[head++];
      const x = i % w, y = (i / w) | 0;
      const neighbours = [];
      if (x > 0) neighbours.push(i - 1);
      if (x < w - 1) neighbours.push(i + 1);
      if (y > 0) neighbours.push(i - w);
      if (y < h - 1) neighbours.push(i + w);
      for (const neighbour of neighbours) {
        if (out[neighbour] === -2) { out[neighbour] = out[i]; queue.push(neighbour); }
      }
    }

    const survivors = [];
    for (let index = 0; index < palette.length; index++) if (!doomed.has(index)) survivors.push(index);
    const remap = new Int32Array(palette.length).fill(-1);
    survivors.forEach((index, slot) => { remap[index] = slot; });
    const final = new Int32Array(count);
    for (let i = 0; i < count; i++) final[i] = out[i] < 0 ? -1 : remap[out[i]];
    const mergedCenters = new Float32Array(survivors.length * 3);
    survivors.forEach((index, slot) => {
      for (let c = 0; c < 3; c++) mergedCenters[slot * 3 + c] = centers[index * 3 + c];
    });
    return {
      labels: final,
      palette: survivors.map((index) => palette[index]),
      centers: mergedCenters,
    };
  }

  function neighbourCounts(labels, mask, colours, index, w, h) {
    const ring = E.dilate(mask, w, h, 1, true);
    const counts = new Int32Array(colours);
    for (let i = 0; i < ring.length; i++) {
      if (!ring[i] || mask[i] || labels[i] < 0 || labels[i] === index) continue;
      counts[labels[i]]++;
    }
    return [...counts.keys()]
      .filter((i) => counts[i] > 0)
      .sort((a, b) => counts[b] - counts[a])
      .map((i) => ({ index: i, count: counts[i] }));
  }

  function isBetween(centers, index, a, b, maxDistance = 14) {
    const axis = [0, 0, 0], relative = [0, 0, 0];
    for (let c = 0; c < 3; c++) {
      axis[c] = centers[b * 3 + c] - centers[a * 3 + c];
      relative[c] = centers[index * 3 + c] - centers[a * 3 + c];
    }
    const length = axis[0] ** 2 + axis[1] ** 2 + axis[2] ** 2;
    if (length < 1e-6) return false;
    const t = (relative[0] * axis[0] + relative[1] * axis[1] + relative[2] * axis[2]) / length;
    if (t <= 0.12 || t >= 0.88) return false;
    let error = 0;
    for (let c = 0; c < 3; c++) error += Math.pow(relative[c] - t * axis[c], 2);
    return Math.sqrt(error) < maxDistance;
  }

  function findBackground(labels, w, h) {
    const counts = new Map();
    const vote = (index) => {
      if (labels[index] < 0) return;
      counts.set(labels[index], (counts.get(labels[index]) || 0) + 1);
    };
    for (let x = 0; x < w; x++) { vote(x); vote((h - 1) * w + x); }
    for (let y = 0; y < h; y++) { vote(y * w); vote(y * w + w - 1); }
    let best = -1, bestCount = 0;
    counts.forEach((value, key) => { if (value > bestCount) { bestCount = value; best = key; } });
    return best;
  }

  function traceMask(mask, w, h) {
    const width = w + 2, height = h + 2;
    const padded = new Uint8Array(width * height);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) padded[(y + 1) * width + (x + 1)] = mask[y * w + x];
    }
    const stride = width + 1;
    const edges = new Map();
    const add = (from, to) => {
      const list = edges.get(from);
      if (list) list.push(to); else edges.set(from, [to]);
    };
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        if (!padded[y * width + x]) continue;
        const up = y > 0 && padded[(y - 1) * width + x];
        const down = y < height - 1 && padded[(y + 1) * width + x];
        const left = x > 0 && padded[y * width + x - 1];
        const right = x < width - 1 && padded[y * width + x + 1];
        if (!up) add(y * stride + x, y * stride + x + 1);
        if (!right) add(y * stride + x + 1, (y + 1) * stride + x + 1);
        if (!down) add((y + 1) * stride + x + 1, (y + 1) * stride + x);
        if (!left) add((y + 1) * stride + x, y * stride + x);
      }
    }

    const rings = [];
    for (const start of [...edges.keys()]) {
      while (edges.get(start) && edges.get(start).length) {
        const ring = walk(edges, start, stride);
        if (ring && ring.length >= 4) rings.push(ring);
      }
    }
    return rings;
  }

  function walk(edges, start, stride) {
    const ring = [];
    let current = start, direction = null;
    for (let guard = 0; guard < 4000000; guard++) {
      const options = edges.get(current);
      if (!options || !options.length) return null;
      let next;
      if (options.length === 1 || !direction) {
        next = options.shift();
      } else {
        next = pickTurn(options, current, direction, stride);
        options.splice(options.indexOf(next), 1);
      }
      if (!options.length) edges.delete(current);
      const cx = current % stride, cy = (current / stride) | 0;
      const nx = next % stride, ny = (next / stride) | 0;
      ring.push([cx - 1, cy - 1]);                       // retire la marge de tracé
      direction = [nx - cx, ny - cy];
      current = next;
      if (current === start) return ring;
    }
    return null;
  }

  /* À une jonction diagonale, on prend le virage le plus serré. */
  function pickTurn(options, current, [dx, dy], stride) {
    const priority = [[-dy, dx], [dx, dy], [dy, -dx], [-dx, -dy]];
    const cx = current % stride, cy = (current / stride) | 0;
    for (const [px, py] of priority) {
      const target = (cy + py) * stride + (cx + px);
      if (options.includes(target)) return target;
    }
    return options[0];
  }

  const ringArea = (points) => {
    let area = 0;
    for (let i = 0; i < points.length; i++) {
      const [x0, y0] = points[i];
      const [x1, y1] = points[(i + 1) % points.length];
      area += x0 * y1 - x1 * y0;
    }
    return area / 2;
  };

  function groupRings(rings, minArea) {
    const kept = rings.filter((ring) => Math.abs(ringArea(ring)) >= Math.max(1, minArea * 0.5));
    if (!kept.length) return [];
    let outers = kept.filter((ring) => ringArea(ring) > 0);
    const holes = kept.filter((ring) => ringArea(ring) <= 0);
    if (!outers.length) outers = kept;

    const boxes = outers.map((ring) => {
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      ring.forEach(([x, y]) => {
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      });
      return [x0, y0, x1, y1];
    });
    const shapes = outers.map((ring) => [ring]);
    holes.forEach((hole) => {
      const [hx, hy] = hole[0];
      let best = -1, bestArea = Infinity;
      boxes.forEach(([x0, y0, x1, y1], index) => {
        if (hx < x0 || hx > x1 || hy < y0 || hy > y1) return;
        const area = (x1 - x0) * (y1 - y0);
        if (area < bestArea) { bestArea = area; best = index; }
      });
      if (best >= 0) shapes[best].push(hole);
    });
    return shapes.sort((a, b) => Math.abs(ringArea(b[0])) - Math.abs(ringArea(a[0])));
  }

  /* Le contour tracé est un escalier à pas unitaires. On y repère d'abord les
     vrais coins (virage net sur une fenêtre de quelques pixels, maximum local),
     puis on lisse tout le reste en laissant ces coins strictement en place :
     une pointe d'étoile ou l'angle d'une lettre restent vifs et à leur position. */
  function refineRing(ring, strength, s) {
    const n = ring.length;
    strength = Math.max(0, Math.min(3, strength));
    const flags = new Uint8Array(n);
    const W = Math.max(2, Math.round(3 * s));
    const half = Math.max(1, W >> 1);
    if (n >= 4 * W) {
      const turnAt = (window) => {
        const out = new Float32Array(n);
        for (let i = 0; i < n; i++) {
          const before = ring[(i - window + n) % n], after = ring[(i + window) % n];
          const inX = ring[i][0] - before[0], inY = ring[i][1] - before[1];
          const outX = after[0] - ring[i][0], outY = after[1] - ring[i][1];
          const lengths = (Math.hypot(inX, inY) || 1e-9) * (Math.hypot(outX, outY) || 1e-9);
          out[i] = Math.acos(Math.max(-1, Math.min(1, (inX * outX + inY * outY) / lengths)));
        }
        return out;
      };
      const turn = turnAt(W), tight = turnAt(half);
      const threshold = 0.85;                                   // ≈ 49°
      for (let i = 0; i < n; i++) {
        if (turn[i] <= threshold) continue;
        // Un vrai coin concentre tout son virage sur le sommet : vu de plus
        // près il tourne autant. Un arrondi étale le sien, donc la mesure
        // rapprochée en retient à peine la moitié — c'est ce qui distingue
        // l'angle d'une lettre de la panse d'un « P ».
        if (tight[i] < 0.62 * turn[i]) continue;
        let peak = true;
        for (let d = -W; d <= W && peak; d++) {
          if (!d) continue;
          const other = turn[(i + d + n) % n];
          if (other > turn[i] || (other === turn[i] && d < 0)) peak = false;
        }
        if (peak) flags[i] = 1;
      }
    }

    let points = ring.map(([x, y], i) => [x, y, flags[i]]);
    const rounds = Math.round(4 * strength);
    for (let round = 0; round < rounds; round++) {
      const next = new Array(n);
      for (let i = 0; i < n; i++) {
        if (flags[i]) { next[i] = points[i]; continue; }
        const previous = points[(i - 1 + n) % n], following = points[(i + 1) % n];
        next[i] = [0.25 * previous[0] + 0.5 * points[i][0] + 0.25 * following[0],
                   0.25 * previous[1] + 0.5 * points[i][1] + 0.25 * following[1], 0];
      }
      points = next;
    }
    return points;
  }

  /* Simplification qui ne franchit jamais un coin : chaque arc entre deux
     coins est simplifié séparément, les coins eux-mêmes sont conservés. */
  function simplifyRing(points, epsilon) {
    const n = points.length;
    if (n <= 4) return points;
    /* L'écart toléré reste petit devant la forme elle-même : sans cela, la
       contre-forme d'un « P » de 15 px se réduit à un pentagone alors que le
       même écart est invisible sur une grande aplat. */
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const [x, y] of points) {
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    }
    epsilon = Math.min(epsilon, Math.max(0.2, Math.min(maxX - minX, maxY - minY) * 0.06));
    const corners = [];
    for (let i = 0; i < n; i++) if (points[i][2]) corners.push(i);
    let result;
    if (!corners.length) {
      let centroidX = 0, centroidY = 0;
      points.forEach(([x, y]) => { centroidX += x; centroidY += y; });
      centroidX /= n; centroidY /= n;
      let anchor = 0, anchorDistance = -1;
      points.forEach(([x, y], index) => {
        const distance = (x - centroidX) ** 2 + (y - centroidY) ** 2;
        if (distance > anchorDistance) { anchorDistance = distance; anchor = index; }
      });
      const rolled = points.slice(anchor).concat(points.slice(0, anchor));
      const simplified = rdp(rolled.concat([rolled[0]]), epsilon);
      result = simplified.length > 3 ? simplified.slice(0, -1) : rolled;
    } else {
      result = [];
      for (let c = 0; c < corners.length; c++) {
        const start = corners[c], end = corners[(c + 1) % corners.length];
        const arc = [];
        for (let i = start, guard = 0; guard <= n; i = (i + 1) % n, guard++) {
          arc.push(points[i]);
          if (i === end && arc.length > 1) break;
        }
        const simplified = rdp(arc, epsilon);
        simplified.pop();                                       // le coin suivant ouvre l'arc suivant
        result.push(...simplified);
      }
    }
    return dropClose(result, Math.max(0.35, epsilon * 0.6));
  }

  function rdp(points, epsilon) {
    const n = points.length;
    if (n < 3) return points;
    const keep = new Uint8Array(n);
    keep[0] = keep[n - 1] = 1;
    const stack = [[0, n - 1]];
    while (stack.length) {
      const [start, end] = stack.pop();
      if (end <= start + 1) continue;
      const [x0, y0] = points[start], [x1, y1] = points[end];
      const dx = x1 - x0, dy = y1 - y0;
      const length = Math.hypot(dx, dy);
      let best = start + 1, bestDistance = -1;
      for (let i = start + 1; i < end; i++) {
        const [x, y] = points[i];
        const distance = length < 1e-9
          ? Math.hypot(x - x0, y - y0)
          : Math.abs(dx * (y0 - y) - (x0 - x) * dy) / length;
        if (distance > bestDistance) { bestDistance = distance; best = i; }
      }
      if (bestDistance > epsilon) {
        keep[best] = 1;
        stack.push([start, best], [best, end]);
      }
    }
    return points.filter((_, index) => keep[index]);
  }

  /* Des nœuds quasi confondus font exploser les poignées de Bézier. */
  function dropClose(points, minDistance) {
    if (points.length <= 4) return points;
    const kept = [points[0]];
    for (let i = 1; i < points.length; i++) {
      const last = kept[kept.length - 1];
      const close = Math.hypot(points[i][0] - last[0], points[i][1] - last[1]) < minDistance;
      if (!close) kept.push(points[i]);
      else if (points[i][2] && !last[2]) kept[kept.length - 1] = points[i];   // le coin prime
    }
    if (kept.length >= 4) {
      const first = kept[0], last = kept[kept.length - 1];
      if (Math.hypot(last[0] - first[0], last[1] - first[1]) < minDistance) {
        if (last[2] && !first[2]) kept[0] = last;
        kept.pop();
      }
    }
    return kept.length >= 4 ? kept : points;
  }

  /* --- géométrie de sortie, partagée par le SVG et le rendu ------------ */
  function ringSegments(ring, curves, smoothing) {
    const n = ring.length;
    if (n < 3) return [];
    if (!curves) {
      return ring.map((point, i) => ({ type: "L", from: point, to: ring[(i + 1) % n] }));
    }
    const tension = 0.33 * Math.max(0.2, Math.min(1.6, smoothing > 0 ? smoothing : 1));
    const at = (i) => ring[((i % n) + n) % n];
    const corner = new Array(n);
    const tangents = new Array(n);
    for (let i = 0; i < n; i++) {
      const previous = at(i - 1), following = at(i + 1);
      tangents[i] = [(following[0] - previous[0]) * 0.5, (following[1] - previous[1]) * 0.5];
      const farPrevious = at(i - 2), farNext = at(i + 2);
      const inX = ring[i][0] - farPrevious[0], inY = ring[i][1] - farPrevious[1];
      const outX = farNext[0] - ring[i][0], outY = farNext[1] - ring[i][1];
      const inLength = Math.hypot(inX, inY) || 1e-9, outLength = Math.hypot(outX, outY) || 1e-9;
      corner[i] = ring[i][2] === 1 ||
        ((inX / inLength) * (outX / outLength) + (inY / inLength) * (outY / outLength)) < 0.2;
    }
    const clamp = (vector, chord) => {
      const length = Math.hypot(vector[0], vector[1]);
      const limit = chord * 0.5;
      return length > limit && limit > 0
        ? [vector[0] * limit / length, vector[1] * limit / length] : vector;
    };
    const segments = [];
    for (let i = 0; i < n; i++) {
      const j = (i + 1) % n;
      const from = ring[i], to = ring[j];
      const chord = Math.hypot(to[0] - from[0], to[1] - from[1]);
      const t0 = corner[i] ? [0, 0] : tangents[i];
      const t1 = corner[j] ? [0, 0] : tangents[j];
      if (!t0[0] && !t0[1] && !t1[0] && !t1[1]) {
        segments.push({ type: "L", from, to });
        continue;
      }
      const c0 = clamp([t0[0] * tension, t0[1] * tension], chord);
      const c1 = clamp([t1[0] * tension, t1[1] * tension], chord);
      segments.push({
        type: "C", from, to,
        control0: [from[0] + c0[0], from[1] + c0[1]],
        control1: [to[0] - c1[0], to[1] - c1[1]],
      });
    }
    return segments;
  }

  const format = (value) => {
    const text = value.toFixed(2).replace(/\.?0+$/, "");
    return text === "" || text === "-0" ? "0" : text;
  };

  function ringToPath(ring, curves, smoothing) {
    const segments = ringSegments(ring, curves, smoothing);
    if (!segments.length) return "";
    const parts = [`M${format(ring[0][0])} ${format(ring[0][1])}`];
    segments.forEach((segment) => {
      if (segment.type === "L") {
        parts.push(`L${format(segment.to[0])} ${format(segment.to[1])}`);
      } else {
        parts.push(`C${format(segment.control0[0])} ${format(segment.control0[1])} ` +
                   `${format(segment.control1[0])} ${format(segment.control1[1])} ` +
                   `${format(segment.to[0])} ${format(segment.to[1])}`);
      }
    });
    parts.push("Z");
    return parts.join("");
  }

  function buildSvg(layers, width, height, opt) {
    const out = [`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" ` +
                 `viewBox="0 0 ${width} ${height}" shape-rendering="geometricPrecision">`];
    layers.forEach((layer) => {
      const d = layer.paths.flat()
        .map((ring) => ringToPath(ring, opt.curves, opt.smoothing)).join("");
      if (!d) return;
      out.push(`<path fill="${E.rgbToHex(layer.color)}" fill-rule="evenodd" d="${d}"/>`);
    });
    out.push("</svg>");
    return out.join("\n");
  }

  /* --- rastérisation scanline (règle pair-impair, anti-crénelée) ------- */
  function flattenRing(ring, curves, smoothing, scale) {
    const segments = ringSegments(ring, curves, smoothing);
    const points = [];
    segments.forEach((segment) => {
      if (segment.type === "L") { points.push(segment.from); return; }
      const { from, control0, control1, to } = segment;
      const span = (Math.abs(control0[0] - from[0]) + Math.abs(control0[1] - from[1]) +
                    Math.abs(control1[0] - control0[0]) + Math.abs(control1[1] - control0[1]) +
                    Math.abs(to[0] - control1[0]) + Math.abs(to[1] - control1[1])) * scale;
      const steps = Math.min(48, Math.max(3, Math.round(span / 3)));
      for (let s = 0; s < steps; s++) {
        const t = s / steps, m = 1 - t;
        points.push([
          m * m * m * from[0] + 3 * m * m * t * control0[0] + 3 * m * t * t * control1[0] + t * t * t * to[0],
          m * m * m * from[1] + 3 * m * m * t * control0[1] + 3 * m * t * t * control1[1] + t * t * t * to[1],
        ]);
      }
    });
    return points;
  }

  const SUBSAMPLES = 4;

  function renderLayers(layers, width, height, scale, opt = {}) {
    const curves = opt.curves !== false;
    const smoothing = opt.smoothing ?? 1;
    const w = Math.max(1, Math.round(width * scale));
    const h = Math.max(1, Math.round(height * scale));
    const image = E.makeImage(w, h);
    const data = image.data;

    layers.forEach((layer) => {
      const polygons = layer.paths.flat()
        .map((ring) => flattenRing(ring, curves, smoothing, scale)
          .map(([x, y]) => [x * scale, y * scale]))
        .filter((polygon) => polygon.length >= 3);
      if (!polygons.length) return;
      // Le compositing ne parcourt que la boîte englobante du calque.
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
      polygons.forEach((polygon) => polygon.forEach(([x, y]) => {
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
      }));
      const left = Math.max(0, Math.floor(x0)), right = Math.min(w - 1, Math.ceil(x1));
      const top = Math.max(0, Math.floor(y0)), bottom = Math.min(h - 1, Math.ceil(y1));
      if (right < left || bottom < top) return;
      const coverage = rasterize(polygons, w, h);
      const [r, g, b] = layer.color;
      for (let y = top; y <= bottom; y++) {
        for (let x = left; x <= right; x++) {
          const i = y * w + x;
          const source = coverage[i];
          if (source <= 0) continue;
          const destination = data[i * 4 + 3] / 255;
          const outAlpha = source + destination * (1 - source);
          if (outAlpha <= 1e-6) continue;
          const keep = destination * (1 - source);
          data[i * 4] = (r * source + data[i * 4] * keep) / outAlpha;
          data[i * 4 + 1] = (g * source + data[i * 4 + 1] * keep) / outAlpha;
          data[i * 4 + 2] = (b * source + data[i * 4 + 2] * keep) / outAlpha;
          data[i * 4 + 3] = Math.round(outAlpha * 255);
        }
      }
    });
    return image;
  }

  /* Rendu du tracé par le moteur SVG du navigateur : strictement ce que
     l'export produira (courbes, traits de jointure, anti-crénelage). En
     l'absence de DOM, repli sur le rastériseur maison. */
  async function renderVector(result, scale, opt = {}) {
    const w = Math.max(1, Math.round(result.width * scale));
    const h = Math.max(1, Math.round(result.height * scale));
    try {
      if (typeof document === "undefined" || typeof Image === "undefined") throw new Error("no DOM");
      const blob = new Blob([result.svg], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      try {
        const img = new Image();
        await new Promise((resolve, reject) => {
          img.onload = resolve;
          img.onerror = () => reject(new Error("SVG non décodé"));
          img.src = url;
        });
        const canvas = document.createElement("canvas");
        canvas.width = w; canvas.height = h;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0, w, h);
        const out = ctx.getImageData(0, 0, w, h);
        canvas.width = canvas.height = 1;
        return out;
      } finally {
        URL.revokeObjectURL(url);
      }
    } catch (error) {
      return renderLayers(result.layers, result.width, result.height, scale, opt);
    }
  }

  function rasterize(polygons, width, height) {
    const coverage = new Float32Array(width * height);
    const x0 = [], y0 = [], x1 = [], y1 = [];
    polygons.forEach((polygon) => {
      for (let i = 0; i < polygon.length; i++) {
        const a = polygon[i], b = polygon[(i + 1) % polygon.length];
        if (a[1] === b[1]) continue;                    // arête horizontale
        x0.push(a[0]); y0.push(a[1]); x1.push(b[0]); y1.push(b[1]);
      }
    });
    if (!x0.length) return coverage;

    const buckets = Array.from({ length: height }, () => []);
    for (let i = 0; i < x0.length; i++) {
      const top = Math.max(0, Math.floor(Math.min(y0[i], y1[i])));
      const bottom = Math.min(height, Math.ceil(Math.max(y0[i], y1[i])));
      for (let row = top; row < bottom; row++) buckets[row].push(i);
    }

    const weight = 1 / SUBSAMPLES;
    const accumulator = new Float32Array(width + 2);
    for (let row = 0; row < height; row++) {
      const bucket = buckets[row];
      if (!bucket.length) continue;
      accumulator.fill(0);
      let touched = false;
      for (let sub = 0; sub < SUBSAMPLES; sub++) {
        const y = row + (sub + 0.5) * weight;
        const crossings = [];
        for (const i of bucket) {
          const top = Math.min(y0[i], y1[i]), bottom = Math.max(y0[i], y1[i]);
          if (y < top || y >= bottom) continue;
          crossings.push(x0[i] + (y - y0[i]) * (x1[i] - x0[i]) / (y1[i] - y0[i]));
        }
        crossings.sort((a, b) => a - b);
        for (let k = 0; k + 1 < crossings.length; k += 2) {
          const from = Math.max(crossings[k], 0), to = Math.min(crossings[k + 1], width);
          if (to <= from) continue;
          touched = true;
          addSpan(accumulator, from, to, weight);
        }
      }
      if (!touched) continue;
      for (let x = 0; x < width; x++) {
        coverage[row * width + x] = Math.min(1, accumulator[x]);
      }
    }
    return coverage;
  }

  function addSpan(accumulator, from, to, weight) {
    const start = Math.floor(from), end = Math.floor(to);
    if (start === end) { accumulator[start] += (to - from) * weight; return; }
    accumulator[start] += (start + 1 - from) * weight;
    for (let x = start + 1; x < end; x++) accumulator[x] += weight;
    accumulator[end] += (to - end) * weight;
  }

  /* ================================================= AGRANDISSEMENT ==== */
  function isPhotographic(image) {
    const { data } = image;
    const count = image.width * image.height;
    const step = Math.max(1, Math.floor(count / 120000));
    const seen = new Set();
    let sampled = 0;
    for (let i = 0; i < count; i += step) {
      const key = ((data[i * 4] >> 3) << 10) | ((data[i * 4 + 1] >> 3) << 5) | (data[i * 4 + 2] >> 3);
      seen.add(key);
      sampled++;
    }
    return seen.size > Math.max(600, sampled * 0.004);
  }

  async function upscale(image, options = {}) {
    const opt = Object.assign({
      scale: 2, targetWidth: 0, targetHeight: 0, method: "auto",
      denoise: 0, sharpen: 0.45, vectorColors: 12,
      maxPixels: Math.min(40e6, E.maxCanvasPixels()),
    }, options);

    const notes = [];
    let width = opt.targetWidth || Math.round(image.width * opt.scale);
    let height = opt.targetHeight || Math.round(image.height * opt.scale);
    if (!opt.targetWidth && opt.targetHeight) width = Math.round(image.width * height / image.height);
    if (opt.targetWidth && !opt.targetHeight) height = Math.round(image.height * width / image.width);
    if (width * height > opt.maxPixels) {
      const shrink = Math.sqrt(opt.maxPixels / (width * height));
      width = Math.max(1, Math.round(width * shrink));
      height = Math.max(1, Math.round(height * shrink));
      notes.push(`taille limitée à ${width}×${height} px (mémoire du navigateur)`);
    }
    const factor = (width / image.width + height / image.height) / 2;

    let method = opt.method;
    if (method === "auto") {
      method = isPhotographic(image) || factor <= 1 ? "edge" : "vector";
      notes.push(`méthode choisie : ${method === "vector" ? "vectorielle" : "photo"}`);
    }

    let source = image;
    if (opt.denoise > 0) source = denoise(source, opt.denoise);

    if (method === "vector") {
      const rendered = await vectorUpscale(source, width, height, opt);
      if (rendered) return { image: rendered, method, factor, notes, width, height };
      method = "edge";
      notes.push("vectorisation non concluante, repli sur « photo »");
    }

    let out = E.resize(source, width, height);
    if (method === "edge" && opt.sharpen > 0 && factor > 1) out = edgeSharpen(out, factor, opt.sharpen);
    return { image: out, method, factor, notes, width, height };
  }

  /* Masque flou pondéré par le gradient : renforce les contours, pas le bruit. */
  function edgeSharpen(image, factor, amount) {
    const { width: w, height: h } = image;
    const count = w * h;
    const channels = [];
    for (let c = 0; c < 3; c++) {
      const channel = new Float32Array(count);
      for (let i = 0; i < count; i++) channel[i] = image.data[i * 4 + c];
      channels.push(channel);
    }
    const sigma = Math.max(0.6, Math.min(3, 0.5 * factor));
    const blurred = channels.map((channel) => E.blur(channel, w, h, sigma));

    const grey = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      grey[i] = (channels[0][i] + channels[1][i] + channels[2][i]) / 3;
    }
    const smooth = E.blur(grey, w, h, 1);
    const gradient = new Float32Array(count);
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const i = y * w + x;
        const gx = smooth[y * w + Math.min(w - 1, x + 1)] - smooth[y * w + Math.max(0, x - 1)];
        const gy = smooth[Math.min(h - 1, y + 1) * w + x] - smooth[Math.max(0, y - 1) * w + x];
        gradient[i] = Math.hypot(gx, gy);
      }
    }
    const scale = percentile(gradient, 0.97) || 1;

    const out = E.cloneImage(image);
    for (let i = 0; i < count; i++) {
      const weight = Math.pow(Math.min(1, gradient[i] / scale), 0.7) * amount * 1.4;
      for (let c = 0; c < 3; c++) {
        const detail = channels[c][i] - blurred[c][i];
        out.data[i * 4 + c] = channels[c][i] + detail * weight;
      }
    }
    return out;
  }

  /** Percentile approché sur un échantillon : évite de trier 40 M de valeurs. */
  function percentile(values, share) {
    const step = Math.max(1, Math.floor(values.length / 200000));
    const sample = [];
    for (let i = 0; i < values.length; i += step) sample.push(values[i]);
    sample.sort((a, b) => a - b);
    return sample[Math.min(sample.length - 1, Math.floor(sample.length * share))];
  }

  /* Médiane 3×3 par tri à insertion sur 9 valeurs — sans allouer ni
     appeler Array.sort pour chaque pixel. */
  const window9 = new Float32Array(9);
  function median9() {
    for (let i = 1; i < 9; i++) {
      const value = window9[i];
      let j = i - 1;
      while (j >= 0 && window9[j] > value) { window9[j + 1] = window9[j]; j--; }
      window9[j + 1] = value;
    }
    return window9[4];
  }

  function denoise(image, strength) {
    const { width: w, height: h } = image;
    const count = w * h;
    const out = E.cloneImage(image);
    for (let c = 0; c < 3; c++) {
      const channel = new Float32Array(count);
      for (let i = 0; i < count; i++) channel[i] = image.data[i * 4 + c];
      for (let y = 0; y < h; y++) {
        const up = Math.max(0, y - 1) * w, row = y * w, down = Math.min(h - 1, y + 1) * w;
        for (let x = 0; x < w; x++) {
          const left = Math.max(0, x - 1), right = Math.min(w - 1, x + 1);
          window9[0] = channel[up + left]; window9[1] = channel[up + x]; window9[2] = channel[up + right];
          window9[3] = channel[row + left]; window9[4] = channel[row + x]; window9[5] = channel[row + right];
          window9[6] = channel[down + left]; window9[7] = channel[down + x]; window9[8] = channel[down + right];
          const i = row + x;
          out.data[i * 4 + c] = channel[i] * (1 - strength) + median9() * strength;
        }
      }
    }
    return out;
  }

  async function vectorUpscale(image, width, height, opt) {
    const pixels = image.width * image.height;
    const result = vectorize(image, {
      colors: Math.max(2, opt.vectorColors), detail: 0.9, smoothing: 1.2,
      minArea: Math.max(10, Math.round(pixels / 12000)),
      mergeDelta: 7, dropBackground: true,
    });
    if (!result.layers.length) return null;
    const scale = (width / image.width + height / image.height) / 2;
    let rendered = await renderVector(result, scale);
    if (rendered.width !== width || rendered.height !== height) {
      rendered = E.resize(rendered, width, height);
    }
    // La transparence d'origine borne le tracé (qui ne couvre que l'opaque).
    let transparent = false;
    for (let i = 3; i < image.data.length; i += 4) {
      if (image.data[i] < 250) { transparent = true; break; }
    }
    if (transparent) {
      const alpha = E.resize(image, width, height);
      for (let i = 3; i < rendered.data.length; i += 4) {
        rendered.data[i] = Math.min(rendered.data[i], alpha.data[i]);
      }
    }
    return rendered;
  }

  /* ------------------------------------------------------- contrôle --- */
  function printQuality(width, height, mmWidth, mmHeight) {
    const dpiX = mmWidth > 0 ? width / (mmWidth / 25.4) : 0;
    const dpiY = mmHeight > 0 ? height / (mmHeight / 25.4) : 0;
    const dpi = Math.min(dpiX, dpiY);
    let grade, label;
    if (dpi >= 299.5) { grade = "excellent"; label = "qualité photo (≥ 300 dpi)"; }
    else if (dpi >= 199.5) { grade = "bon"; label = "bon pour l'impression courante"; }
    else if (dpi >= 149.5) { grade = "moyen"; label = "acceptable en grand format"; }
    else { grade = "faible"; label = "insuffisant — agrandir la source"; }
    return { dpi: Math.round(dpi * 10) / 10, grade, label };
  }

  return {
    removeBackground, vectorize, renderLayers, renderVector, upscale, printQuality,
    isPhotographic, ringToPath,
  };
})();
