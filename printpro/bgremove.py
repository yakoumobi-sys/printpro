"""Background removal.

Two engines are available:

``auto`` / ``color``
    A classical, dependency-free pipeline built for print artwork: sample the
    border to learn the background colour(s), build a colour-distance map in
    CIE L*a*b*, keep only the background regions that are connected to the
    border, then refine the alpha on the transition band and unmix the
    background tint out of the semi-transparent pixels (despill).

``ai``
    Used only when the optional ``rembg`` package is installed (U²-Net).  It
    handles photographic subjects with hair/fur that colour segmentation
    cannot.  We still run our edge refinement on top of its mask.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from .imaging import delta_e, gaussian_blur, hex_to_rgb, rgb_to_lab


@dataclass
class BgOptions:
    method: str = "auto"          # auto | color | ai
    color: str | None = None      # "#ffffff" for method="color"
    tolerance: float = 12.0       # Delta-E below which a pixel is background
    softness: float = 6.0         # width (Delta-E) of the semi-transparent band
    edge_shift: float = 0.0       # <0 shrinks the subject, >0 grows it (px)
    feather: float = 0.6          # alpha blur in px, kills jaggies
    despill: bool = True          # remove background tint from edge pixels
    defringe: float = 1.5         # px of edge decontamination (0 = off)
    keep_holes: bool = True       # keep enclosed background (e.g. inside an "O")
    min_region: int = 24          # drop foreground specks smaller than this (px)
    largest_only: bool = False    # keep only the biggest subject
    post_trim: bool = False       # crop the transparent border afterwards


@dataclass
class BgResult:
    image: np.ndarray
    alpha: np.ndarray
    background_colors: list[tuple[int, int, int]] = field(default_factory=list)
    engine: str = "auto"
    coverage: float = 0.0          # share of opaque pixels, 0..1


# --------------------------------------------------------------------------- #
def remove_background(rgba: np.ndarray, options: BgOptions | None = None) -> BgResult:
    opt = options or BgOptions()
    rgba = np.asarray(rgba, dtype=np.uint8)
    if rgba.shape[2] == 3:
        rgba = np.dstack([rgba, np.full(rgba.shape[:2], 255, np.uint8)])

    engine = opt.method
    if engine == "ai":
        mask = _ai_mask(rgba)
        if mask is None:
            engine = "auto"       # graceful fallback, never fail the request
    if engine == "ai":
        alpha = mask.astype(np.float32) / 255.0
        bg_colors: list[tuple[int, int, int]] = []
    else:
        alpha, bg_colors = _color_alpha(rgba, opt)

    alpha = _refine(alpha, opt)
    out = rgba.copy()
    if opt.defringe > 0 and bg_colors:
        alpha = _defringe(rgba[:, :, :3], alpha, bg_colors[0], opt.defringe)
    if opt.despill and bg_colors:
        out[:, :, :3] = _unmix(rgba[:, :, :3], alpha, bg_colors[0])
    prev = rgba[:, :, 3].astype(np.float32) / 255.0
    out[:, :, 3] = np.clip(alpha * prev * 255.0 + 0.5, 0, 255).astype(np.uint8)

    if opt.post_trim:
        from .imaging import trim
        out = trim(out, padding=2)

    return BgResult(image=out, alpha=out[:, :, 3].copy(),
                    background_colors=bg_colors, engine=engine,
                    coverage=float((out[:, :, 3] > 8).mean()))


# --------------------------------------------------------------------------- #
# Colour based segmentation
# --------------------------------------------------------------------------- #
def _color_alpha(rgba: np.ndarray, opt: BgOptions):
    lab = rgb_to_lab(rgba[:, :, :3])
    h, w = lab.shape[:2]

    if opt.method == "color" and opt.color:
        refs = [np.array(hex_to_rgb(opt.color), dtype=np.float32)]
        lab_refs = [rgb_to_lab(r.reshape(1, 1, 3))[0, 0] for r in refs]
        bg_rgb = [tuple(int(c) for c in refs[0])]
    else:
        lab_refs, bg_rgb = _sample_border(rgba, lab)

    dist = np.full((h, w), np.inf, dtype=np.float32)
    for ref in lab_refs:
        dist = np.minimum(dist, delta_e(lab, ref.reshape(1, 1, 3)))

    # Smoothing the distance map before thresholding avoids salt and pepper
    # holes on noisy JPEG backgrounds.
    dist = gaussian_blur(dist, 0.8)

    low = max(0.5, float(opt.tolerance))
    high = low + max(0.5, float(opt.softness))
    alpha = np.clip((dist - low) / (high - low), 0.0, 1.0).astype(np.float32)

    sure_bg = dist <= low
    if not opt.keep_holes:
        # Only background touching the border is erased; enclosed areas (the
        # hole of an "O", the sky seen through a handle) stay opaque.
        labels, count = ndimage.label(sure_bg)
        if count:
            border = np.concatenate([labels[0, :], labels[-1, :],
                                     labels[:, 0], labels[:, -1]])
            keep = np.unique(border[border > 0])
            connected = np.isin(labels, keep)
            alpha = np.where(sure_bg & ~connected, 1.0, alpha)
    return alpha, bg_rgb


def _sample_border(rgba: np.ndarray, lab: np.ndarray, k: int = 3):
    """Learn background colours from a border ring, with a tiny k-means."""
    h, w = lab.shape[:2]
    band = max(2, int(round(min(h, w) * 0.02)))
    ring = np.zeros((h, w), dtype=bool)
    ring[:band, :] = ring[-band:, :] = True
    ring[:, :band] = ring[:, -band:] = True
    opaque = rgba[:, :, 3] > 8
    sel = ring & opaque
    if sel.sum() < 16:
        sel = ring
    samples = lab[sel]
    rgb_samples = rgba[:, :, :3][sel].astype(np.float32)
    if samples.shape[0] > 20000:
        idx = np.linspace(0, samples.shape[0] - 1, 20000).astype(np.int64)
        samples, rgb_samples = samples[idx], rgb_samples[idx]

    centers, assign = _kmeans(samples, k=min(k, max(1, samples.shape[0])))
    order = np.argsort(-np.bincount(assign, minlength=centers.shape[0]))
    centers = centers[order]
    counts = np.bincount(assign, minlength=centers.shape[0])[order]
    total = max(1, counts.sum())

    lab_refs, rgb_refs = [], []
    for i, center in enumerate(centers):
        if i and counts[i] / total < 0.10:      # ignore marginal clusters
            continue
        lab_refs.append(center)
        member = rgb_samples[assign == order[i]]
        rgb_refs.append(tuple(int(round(v)) for v in member.mean(axis=0))
                        if member.size else (255, 255, 255))
    return lab_refs, rgb_refs


def _kmeans(data: np.ndarray, k: int, iters: int = 12, seed: int = 7):
    """Compact k-means++ (used for background sampling and quantisation)."""
    data = np.asarray(data, dtype=np.float32)
    n = data.shape[0]
    k = max(1, min(int(k), n))
    rng = np.random.default_rng(seed)
    centers = np.empty((k, data.shape[1]), dtype=np.float32)
    centers[0] = data[rng.integers(n)]
    closest = np.sum((data - centers[0]) ** 2, axis=1)
    for i in range(1, k):
        total = float(closest.sum())
        if total <= 0:
            centers[i] = data[rng.integers(n)]
        else:
            centers[i] = data[np.searchsorted(np.cumsum(closest / total),
                                              rng.random())]
        closest = np.minimum(closest, np.sum((data - centers[i]) ** 2, axis=1))

    assign = np.zeros(n, dtype=np.int64)
    for _ in range(iters):
        d = ((data[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2) \
            if n * k <= 4_000_000 else _chunked_dist(data, centers)
        new_assign = np.argmin(d, axis=1)
        if _ and np.array_equal(new_assign, assign):
            break
        assign = new_assign
        for j in range(k):
            member = data[assign == j]
            if member.size:
                centers[j] = member.mean(axis=0)
    return centers, assign


def _chunked_dist(data: np.ndarray, centers: np.ndarray, chunk: int = 100_000):
    out = np.empty((data.shape[0], centers.shape[0]), dtype=np.float32)
    for start in range(0, data.shape[0], chunk):
        block = data[start:start + chunk]
        out[start:start + chunk] = ((block[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
    return out


# --------------------------------------------------------------------------- #
# Mask refinement
# --------------------------------------------------------------------------- #
def _refine(alpha: np.ndarray, opt: BgOptions) -> np.ndarray:
    a = np.clip(alpha, 0.0, 1.0).astype(np.float32)

    if opt.min_region > 1 or opt.largest_only:
        solid = a > 0.5
        labels, count = ndimage.label(solid)
        if count:
            sizes = np.bincount(labels.ravel())
            sizes[0] = 0
            if opt.largest_only:
                keep_ids = {int(np.argmax(sizes))}
            else:
                keep_ids = {i for i in range(1, count + 1)
                            if sizes[i] >= opt.min_region}
            keep = np.isin(labels, list(keep_ids) or [0])
            a = np.where(solid & ~keep, 0.0, a)

    if opt.edge_shift:
        shift = float(opt.edge_shift)
        iters = max(1, int(round(abs(shift))))
        struct = ndimage.generate_binary_structure(2, 1)
        solid = a > 0.5
        moved = (ndimage.binary_dilation(solid, struct, iters) if shift > 0
                 else ndimage.binary_erosion(solid, struct, iters)).astype(np.float32)
        # grow: the mask can only gain opacity; shrink: it can only lose it.
        a = np.maximum(a, moved) if shift > 0 else np.minimum(a, moved)

    if opt.feather > 0:
        a = gaussian_blur(a, float(opt.feather))
    return np.clip(a, 0.0, 1.0)


def _defringe(rgb: np.ndarray, alpha: np.ndarray, bg_color,
              width: float) -> np.ndarray:
    """Lower the alpha of edge pixels still carrying background colour.

    Anti-aliased edges are a mix of subject and background, yet their colour
    can stay far enough from the background to be scored fully opaque — that
    is the halo everybody complains about after a cut-out.  Here the local
    subject colour is estimated from the pixels just inside the edge, and each
    edge pixel's opacity is re-derived from where its colour sits between that
    subject colour and the background.
    """
    solid = alpha > 0.5
    if not solid.any():
        return alpha
    inner = ndimage.binary_erosion(
        solid, ndimage.generate_binary_structure(2, 1),
        iterations=max(1, int(round(width))))
    band = solid & ~inner
    if not band.any() or not inner.any():
        return alpha

    # Local subject colour: blur the interior only, then renormalise.
    sigma = max(1.5, width * 2.0)
    mask = inner.astype(np.float32)
    weight = gaussian_blur(mask, sigma)
    local = gaussian_blur(rgb.astype(np.float32) * mask[:, :, None], sigma)
    local = local / np.maximum(weight, 1e-4)[:, :, None]

    bg = np.array(bg_color[:3], dtype=np.float32).reshape(1, 1, 3)
    axis = local - bg
    denom = np.sum(axis * axis, axis=2)
    ratio = np.sum((rgb.astype(np.float32) - bg) * axis, axis=2) / np.maximum(denom, 1e-4)
    estimated = np.clip(ratio, 0.0, 1.0)
    out = alpha.copy()
    out[band] = np.minimum(alpha[band], estimated[band])
    return out


def _unmix(rgb: np.ndarray, alpha: np.ndarray, bg_color) -> np.ndarray:
    """Recover the true subject colour on partially transparent pixels.

    Observed = alpha*Foreground + (1-alpha)*Background, so we solve for the
    foreground instead of leaving a white/green halo around the cut-out.
    """
    a = np.clip(alpha, 0.0, 1.0)[:, :, None]
    bg = np.array(bg_color[:3], dtype=np.float32).reshape(1, 1, 3)
    band = (a > 0.02) & (a < 0.98)
    fg = (rgb.astype(np.float32) - (1.0 - a) * bg) / np.maximum(a, 0.05)
    out = np.where(band, np.clip(fg, 0, 255), rgb.astype(np.float32))
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def _ai_mask(rgba: np.ndarray):
    """Optional U²-Net matting through ``rembg`` — ``None`` when unavailable."""
    try:  # pragma: no cover - optional heavy dependency
        from rembg import remove as rembg_remove
        from .imaging import encode, load_rgba
        cut = load_rgba(rembg_remove(encode(rgba, "PNG")))
        return cut[:, :, 3]
    except Exception:
        return None


def ai_available() -> bool:
    """Is the optional ``rembg`` engine installed?"""
    from importlib.util import find_spec
    try:
        return find_spec("rembg") is not None
    except Exception:  # pragma: no cover - broken installs
        return False
