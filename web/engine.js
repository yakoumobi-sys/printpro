/* PrintPro — moteur de traitement d'image, 100 % navigateur.
   Portage JavaScript des algorithmes Python : détourage, vectorisation,
   agrandissement, rastérisation. Aucune dépendance externe. */
"use strict";

const Engine = (() => {

  /* ------------------------------------------------------------ couleur */
  const SRGB_TO_LINEAR = new Float32Array(256);
  for (let i = 0; i < 256; i++) {
    const c = i / 255;
    SRGB_TO_LINEAR[i] = c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  }

  function labFromRgb(r, g, b, out, offset) {
    const lr = SRGB_TO_LINEAR[r], lg = SRGB_TO_LINEAR[g], lb = SRGB_TO_LINEAR[b];
    let x = (0.4124564 * lr + 0.3575761 * lg + 0.1804375 * lb) / 0.95047;
    let y = (0.2126729 * lr + 0.7151522 * lg + 0.0721750 * lb);
    let z = (0.0193339 * lr + 0.1191920 * lg + 0.9503041 * lb) / 1.08883;
    const f = (t) => t > 0.008856451679 ? Math.cbrt(t) : (903.2962962 * t + 16) / 116;
    x = f(x); y = f(y); z = f(z);
    out[offset] = 116 * y - 16;
    out[offset + 1] = 500 * (x - y);
    out[offset + 2] = 200 * (y - z);
  }

  /** Image RGBA -> tableau L*a*b* (3 flottants par pixel). */
  function toLab(data, count) {
    const lab = new Float32Array(count * 3);
    for (let i = 0, p = 0; i < count; i++, p += 4) {
      labFromRgb(data[p], data[p + 1], data[p + 2], lab, i * 3);
    }
    return lab;
  }

  const deltaE = (a, ai, b, bi) => {
    const d0 = a[ai] - b[bi], d1 = a[ai + 1] - b[bi + 1], d2 = a[ai + 2] - b[bi + 2];
    return Math.sqrt(d0 * d0 + d1 * d1 + d2 * d2);
  };

  function hexToRgb(hex) {
    let v = String(hex).replace("#", "");
    if (v.length === 3) v = v.split("").map((c) => c + c).join("");
    return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)];
  }
  const rgbToHex = (c) => "#" + c.slice(0, 3)
    .map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");

  /* --------------------------------------------------------- images ---- */
  function makeImage(width, height) {
    return new ImageData(width, height);
  }

  function cloneImage(image) {
    return new ImageData(new Uint8ClampedArray(image.data), image.width, image.height);
  }

  function canvasOf(image) {
    const canvas = document.createElement("canvas");
    canvas.width = image.width;
    canvas.height = image.height;
    canvas.getContext("2d").putImageData(image, 0, 0);
    return canvas;
  }

  /** Redimensionnement alpha-correct : prémultiplie, échantillonne, démultiplie. */
  function resize(image, width, height, smooth = true) {
    width = Math.max(1, Math.round(width));
    height = Math.max(1, Math.round(height));
    if (width === image.width && height === image.height) return cloneImage(image);
    const source = cloneImage(image);
    const d = source.data;
    for (let i = 0; i < d.length; i += 4) {      // prémultiplication
      const a = d[i + 3] / 255;
      d[i] *= a; d[i + 1] *= a; d[i + 2] *= a;
    }
    const canvas = document.createElement("canvas");
    canvas.width = width; canvas.height = height;
    const context = canvas.getContext("2d");
    context.imageSmoothingEnabled = smooth;
    context.imageSmoothingQuality = "high";
    context.drawImage(canvasOf(source), 0, 0, width, height);
    const out = context.getImageData(0, 0, width, height);
    const o = out.data;
    for (let i = 0; i < o.length; i += 4) {      // démultiplication
      const a = o[i + 3];
      if (a === 0) { o[i] = o[i + 1] = o[i + 2] = 0; continue; }
      const f = 255 / a;
      o[i] = Math.min(255, o[i] * f);
      o[i + 1] = Math.min(255, o[i + 1] * f);
      o[i + 2] = Math.min(255, o[i + 2] * f);
    }
    return out;
  }

  function rotate90(image) {
    const { width: w, height: h, data } = image;
    const out = makeImage(h, w);
    const o = out.data;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const src = (y * w + x) * 4;
        const dst = ((w - 1 - x) * h + y) * 4;
        o[dst] = data[src]; o[dst + 1] = data[src + 1];
        o[dst + 2] = data[src + 2]; o[dst + 3] = data[src + 3];
      }
    }
    return out;
  }

  function mirror(image) {
    const { width: w, height: h, data } = image;
    const out = makeImage(w, h);
    const o = out.data;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const src = (y * w + x) * 4;
        const dst = (y * w + (w - 1 - x)) * 4;
        o[dst] = data[src]; o[dst + 1] = data[src + 1];
        o[dst + 2] = data[src + 2]; o[dst + 3] = data[src + 3];
      }
    }
    return out;
  }

  function alphaBounds(image, threshold = 8) {
    const { width: w, height: h, data } = image;
    let x0 = w, y0 = h, x1 = -1, y1 = -1;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (data[(y * w + x) * 4 + 3] > threshold) {
          if (x < x0) x0 = x;
          if (x > x1) x1 = x;
          if (y < y0) y0 = y;
          if (y > y1) y1 = y;
        }
      }
    }
    return x1 < 0 ? null : { x0, y0, x1: x1 + 1, y1: y1 + 1 };
  }

  function trim(image, padding = 0) {
    const box = alphaBounds(image);
    if (!box) return cloneImage(image);
    const x0 = Math.max(0, box.x0 - padding), y0 = Math.max(0, box.y0 - padding);
    const x1 = Math.min(image.width, box.x1 + padding);
    const y1 = Math.min(image.height, box.y1 + padding);
    return crop(image, x0, y0, x1 - x0, y1 - y0);
  }

  function crop(image, x, y, width, height) {
    const out = makeImage(width, height);
    for (let row = 0; row < height; row++) {
      const from = ((y + row) * image.width + x) * 4;
      out.data.set(image.data.subarray(from, from + width * 4), row * width * 4);
    }
    return out;
  }

  /** Aplatit sur une couleur opaque (pour le JPEG et les fonds de planche). */
  function flatten(image, background) {
    const out = cloneImage(image);
    const d = out.data;
    const [br, bg, bb] = background;
    for (let i = 0; i < d.length; i += 4) {
      const a = d[i + 3] / 255;
      d[i] = d[i] * a + br * (1 - a);
      d[i + 1] = d[i + 1] * a + bg * (1 - a);
      d[i + 2] = d[i + 2] * a + bb * (1 - a);
      d[i + 3] = 255;
    }
    return out;
  }

  /* ------------------------------------------------------ traitements -- */
  /** Flou approché par trois passes de boîte : O(n), indépendant de sigma. */
  function blur(src, w, h, sigma) {
    if (sigma <= 0) return src;
    const radius = Math.max(1, Math.round(sigma * 1.6));
    let current = src;
    for (let pass = 0; pass < 3; pass++) {
      current = boxBlurPass(current, w, h, radius);
    }
    return current;
  }

  function boxBlurPass(src, w, h, radius) {
    const temporary = new Float32Array(w * h);
    const out = new Float32Array(w * h);
    const window = radius * 2 + 1;
    for (let y = 0; y < h; y++) {                       // horizontal
      const row = y * w;
      let sum = 0;
      for (let x = -radius; x <= radius; x++) sum += src[row + Math.min(w - 1, Math.max(0, x))];
      for (let x = 0; x < w; x++) {
        temporary[row + x] = sum / window;
        sum -= src[row + Math.min(w - 1, Math.max(0, x - radius))];
        sum += src[row + Math.min(w - 1, Math.max(0, x + radius + 1))];
      }
    }
    for (let x = 0; x < w; x++) {                       // vertical
      let sum = 0;
      for (let y = -radius; y <= radius; y++) sum += temporary[Math.min(h - 1, Math.max(0, y)) * w + x];
      for (let y = 0; y < h; y++) {
        out[y * w + x] = sum / window;
        sum -= temporary[Math.min(h - 1, Math.max(0, y - radius)) * w + x];
        sum += temporary[Math.min(h - 1, Math.max(0, y + radius + 1)) * w + x];
      }
    }
    return out;
  }

  /** Étiquetage des composantes connexes (4-connexité) sur un masque. */
  function labelComponents(mask, w, h) {
    const labels = new Int32Array(w * h).fill(0);
    const stack = new Int32Array(w * h);
    const sizes = [0];
    let next = 0;
    for (let start = 0; start < mask.length; start++) {
      if (!mask[start] || labels[start]) continue;
      next++;
      let top = 0, size = 0;
      stack[top++] = start;
      labels[start] = next;
      while (top > 0) {
        const index = stack[--top];
        size++;
        const x = index % w, y = (index / w) | 0;
        if (x > 0 && mask[index - 1] && !labels[index - 1]) { labels[index - 1] = next; stack[top++] = index - 1; }
        if (x < w - 1 && mask[index + 1] && !labels[index + 1]) { labels[index + 1] = next; stack[top++] = index + 1; }
        if (y > 0 && mask[index - w] && !labels[index - w]) { labels[index - w] = next; stack[top++] = index - w; }
        if (y < h - 1 && mask[index + w] && !labels[index + w]) { labels[index + w] = next; stack[top++] = index + w; }
      }
      sizes.push(size);
    }
    return { labels, count: next, sizes };
  }

  function dilate(mask, w, h, iterations = 1, diagonal = false) {
    let current = mask;
    for (let step = 0; step < iterations; step++) {
      const out = new Uint8Array(w * h);
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const i = y * w + x;
          if (current[i]) { out[i] = 1; continue; }
          if ((x > 0 && current[i - 1]) || (x < w - 1 && current[i + 1]) ||
              (y > 0 && current[i - w]) || (y < h - 1 && current[i + w])) { out[i] = 1; continue; }
          if (diagonal &&
              ((x > 0 && y > 0 && current[i - w - 1]) || (x < w - 1 && y > 0 && current[i - w + 1]) ||
               (x > 0 && y < h - 1 && current[i + w - 1]) || (x < w - 1 && y < h - 1 && current[i + w + 1]))) out[i] = 1;
        }
      }
      current = out;
    }
    return current;
  }

  function erode(mask, w, h, iterations = 1) {
    let current = mask;
    for (let step = 0; step < iterations; step++) {
      const out = new Uint8Array(w * h);
      for (let y = 0; y < h; y++) {
        for (let x = 0; x < w; x++) {
          const i = y * w + x;
          if (!current[i]) continue;
          const inside = x > 0 && x < w - 1 && y > 0 && y < h - 1;
          out[i] = inside && current[i - 1] && current[i + 1] &&
                   current[i - w] && current[i + w] ? 1 : 0;
        }
      }
      current = out;
    }
    return current;
  }

  /** k-means++ compact, utilisé pour le fond et pour la palette. */
  function kmeans(data, dim, k, iterations = 12, seed = 7) {
    const n = data.length / dim;
    k = Math.max(1, Math.min(k, n));
    let state = seed >>> 0;
    const random = () => {
      state = (state * 1664525 + 1013904223) >>> 0;
      return state / 4294967296;
    };
    const centers = new Float32Array(k * dim);
    const closest = new Float32Array(n).fill(Infinity);
    let pick = Math.floor(random() * n);
    for (let j = 0; j < dim; j++) centers[j] = data[pick * dim + j];
    for (let c = 1; c < k; c++) {
      let total = 0;
      for (let i = 0; i < n; i++) {
        let distance = 0;
        for (let j = 0; j < dim; j++) {
          const diff = data[i * dim + j] - centers[(c - 1) * dim + j];
          distance += diff * diff;
        }
        if (distance < closest[i]) closest[i] = distance;
        total += closest[i];
      }
      let target = random() * total, accumulated = 0;
      pick = n - 1;
      for (let i = 0; i < n; i++) {
        accumulated += closest[i];
        if (accumulated >= target) { pick = i; break; }
      }
      for (let j = 0; j < dim; j++) centers[c * dim + j] = data[pick * dim + j];
    }

    const assign = new Int32Array(n);
    const sums = new Float64Array(k * dim);
    const counts = new Int32Array(k);
    for (let round = 0; round < iterations; round++) {
      let moved = 0;
      sums.fill(0); counts.fill(0);
      for (let i = 0; i < n; i++) {
        let best = 0, bestDistance = Infinity;
        for (let c = 0; c < k; c++) {
          let distance = 0;
          for (let j = 0; j < dim; j++) {
            const diff = data[i * dim + j] - centers[c * dim + j];
            distance += diff * diff;
            if (distance >= bestDistance) break;
          }
          if (distance < bestDistance) { bestDistance = distance; best = c; }
        }
        if (assign[i] !== best) moved++;
        assign[i] = best;
        counts[best]++;
        for (let j = 0; j < dim; j++) sums[best * dim + j] += data[i * dim + j];
      }
      for (let c = 0; c < k; c++) {
        if (!counts[c]) continue;
        for (let j = 0; j < dim; j++) centers[c * dim + j] = sums[c * dim + j] / counts[c];
      }
      if (round && !moved) break;
    }
    return { centers, assign, counts, k };
  }

  return {
    labFromRgb, toLab, deltaE, hexToRgb, rgbToHex,
    makeImage, cloneImage, canvasOf, resize, rotate90, mirror, crop, trim,
    alphaBounds, flatten, blur, labelComponents, dilate, erode, kmeans,
  };
})();
