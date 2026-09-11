"""Raster -> vector conversion (colour tracing).

The pipeline mirrors what potrace/vtracer do, implemented with numpy so that
PrintPro has no external binary dependency:

1. optional pre-blur + colour quantisation in CIE L*a*b* (k-means),
2. per-colour binary masks, cleaned from specks and pinholes,
3. exact contour extraction by walking the edges between pixels,
4. Chaikin smoothing then Ramer-Douglas-Peucker simplification,
5. corner detection and conversion to cubic Bézier curves,
6. SVG assembly (one path per colour, ``fill-rule="evenodd"``).

The traced geometry is kept in the result so it can be rasterised again at any
resolution — this is what powers the "vector" upscaling mode.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from .bgremove import _kmeans
from .imaging import gaussian_blur, rgb_to_hex, rgb_to_lab


@dataclass
class VectorOptions:
    colors: int = 8               # palette size (1..64); 2 = black & white
    mode: str = "color"           # color | bw | posterize
    detail: float = 1.0           # RDP tolerance in px (lower = more nodes)
    smoothing: float = 1.0        # 0 = polygons, 1 = default, 2 = very round
    min_area: int = 12            # discard shapes/holes smaller than this (px²)
    blur: float = 0.0             # pre-blur, helps on noisy scans
    stack: bool = True            # overlap shapes by 1px to avoid seams
    drop_background: bool = True  # do not emit a path for the background colour
    background: str | None = None # force the background colour (#rrggbb)
    curves: bool = True           # emit Bézier curves instead of polylines
    merge_delta: float = 6.0      # merge palette entries closer than this ΔE
    edge_cleanup: float = 0.6     # absorb 1px anti-aliasing fringes (0 = off)
    fringe_share: float = 0.02    # colours thinner than `fringe_px` and rarer
    fringe_px: int = 3            # than this share are dissolved into neighbours


@dataclass
class Layer:
    color: tuple[int, int, int]
    paths: list[list[np.ndarray]]      # each shape = list of closed rings
    area: int


@dataclass
class VectorResult:
    svg: str
    width: int
    height: int
    layers: list[Layer] = field(default_factory=list)
    palette: list[tuple[int, int, int]] = field(default_factory=list)
    nodes: int = 0

    @property
    def shapes(self) -> int:
        return sum(len(layer.paths) for layer in self.layers)


# --------------------------------------------------------------------------- #
def vectorize(rgba: np.ndarray, options: VectorOptions | None = None) -> VectorResult:
    opt = options or VectorOptions()
    rgba = np.asarray(rgba, dtype=np.uint8)
    h, w = rgba.shape[:2]
    rgb = rgba[:, :, :3].astype(np.float32)
    opaque = rgba[:, :, 3] > 110 if rgba.shape[2] == 4 else np.ones((h, w), bool)

    if opt.blur > 0:
        rgb = gaussian_blur(rgb, opt.blur)

    labels, palette = _quantize(rgb, opaque, opt)

    # Background = the most frequent colour of the border, so that a logo on
    # white does not produce a giant white rectangle behind everything.
    bg_index = _background_index(labels, palette, opt)

    order = sorted(range(len(palette)),
                   key=lambda i: -int((labels == i).sum()))
    layers: list[Layer] = []
    nodes = 0
    tol = max(0.05, float(opt.detail))
    struct = ndimage.generate_binary_structure(2, 2)

    for rank, index in enumerate(order):
        if opt.drop_background and index == bg_index:
            continue
        mask = (labels == index)
        if not mask.any():
            continue
        mask = _clean(mask, opt.min_area)
        if not mask.any():
            continue
        if opt.stack and rank > 0:
            mask = ndimage.binary_dilation(mask, struct, 1)
        rings = _trace(mask)
        shapes = _group_rings(rings, opt.min_area)
        if not shapes:
            continue
        processed: list[list[np.ndarray]] = []
        for shape in shapes:
            out_rings = []
            for ring in shape:
                pts = _smooth(ring, opt.smoothing)
                pts = _rdp_closed(pts, tol)
                if len(pts) >= 3:
                    out_rings.append(pts)
                    nodes += len(pts)
            if out_rings:
                processed.append(out_rings)
        if processed:
            layers.append(Layer(color=tuple(int(c) for c in palette[index]),
                                paths=processed, area=int(mask.sum())))

    svg = _build_svg(layers, w, h, opt)
    return VectorResult(svg=svg, width=w, height=h, layers=layers,
                        palette=[tuple(int(c) for c in c3) for c3 in palette],
                        nodes=nodes)


# --------------------------------------------------------------------------- #
# Quantisation
# --------------------------------------------------------------------------- #
def _quantize(rgb: np.ndarray, opaque: np.ndarray, opt: VectorOptions):
    h, w = rgb.shape[:2]
    labels = np.full((h, w), -1, dtype=np.int32)

    if opt.mode == "bw":
        from .imaging import luminance
        lum = luminance(rgb)
        thr = _otsu(lum[opaque]) if opaque.any() else 128.0
        dark = (lum <= thr) & opaque
        labels[dark] = 0
        labels[~dark & opaque] = 1
        return labels, np.array([[0, 0, 0], [255, 255, 255]], dtype=np.float32)

    k = max(1, min(64, int(opt.colors)))
    lab = rgb_to_lab(rgb.astype(np.uint8))
    data = lab[opaque]
    rgb_data = rgb[opaque]
    if data.shape[0] == 0:
        return labels, np.zeros((1, 3), dtype=np.float32)

    sample = data
    sample_rgb = rgb_data
    if sample.shape[0] > 60000:                      # k-means on a subsample
        idx = np.linspace(0, sample.shape[0] - 1, 60000).astype(np.int64)
        sample, sample_rgb = sample[idx], rgb_data[idx]
    centers, assign = _kmeans(sample, k=k)

    palette = np.zeros((centers.shape[0], 3), dtype=np.float32)
    for j in range(centers.shape[0]):
        member = sample_rgb[assign == j]
        palette[j] = member.mean(axis=0) if member.size else sample_rgb.mean(axis=0)

    full = _assign_nearest(data, centers)
    labels[opaque] = full.astype(np.int32)
    if opt.merge_delta > 0 and centers.shape[0] > 1:
        labels, palette = _merge_similar(labels, centers, palette,
                                         float(opt.merge_delta))
    if opt.edge_cleanup > 0 and palette.shape[0] > 1:
        labels = _majority_filter(labels, palette.shape[0],
                                  float(opt.edge_cleanup))
    if opt.fringe_share > 0 and palette.shape[0] > 2:
        labels, palette = _dissolve_fringes(labels, palette,
                                            float(opt.fringe_share),
                                            int(opt.fringe_px))
    return labels, palette


def _dissolve_fringes(labels: np.ndarray, palette: np.ndarray,
                      max_share: float, thickness: int):
    """Remove the intermediate colours anti-aliasing creates along edges.

    A halo colour is rare *and* thin: eroding it by a couple of pixels makes
    it vanish.  Such pixels are handed over to the nearest surviving colour,
    which splits the band between the two real shapes it separates.
    """
    valid = labels >= 0
    total = max(1, int(valid.sum()))
    counts = np.bincount(labels[valid], minlength=palette.shape[0])
    lab = rgb_to_lab(np.clip(palette, 0, 255).astype(np.uint8).reshape(-1, 1, 3))[:, 0]
    cross = ndimage.generate_binary_structure(2, 1)
    doomed = []
    for index, count in enumerate(counts):
        if count == 0 or count / total > max_share:
            continue
        mask = labels == index
        eroded = ndimage.binary_erosion(mask, cross, iterations=thickness)
        if eroded.sum() > 0.15 * mask.sum():        # a real thick shape
            continue
        neighbours = _neighbour_counts(labels, mask, palette.shape[0], index)
        if len(neighbours) < 2:
            continue                                # a thin *shape*, e.g. text
        (a, count_a), (b, count_b) = neighbours[0], neighbours[1]
        if count_b < 0.15 * count_a:
            continue                                # only borders one colour
        if _is_between(lab[index], lab[a], lab[b]):
            doomed.append(index)
    if not doomed or len(doomed) >= palette.shape[0]:
        return labels, palette

    drop = np.isin(labels, doomed)
    keep = valid & ~drop
    if not keep.any():
        return labels, palette
    _, indices = ndimage.distance_transform_edt(~keep, return_indices=True)
    out = labels.copy()
    out[drop] = labels[indices[0][drop], indices[1][drop]]

    survivors = [i for i in range(palette.shape[0]) if i not in doomed]
    remap = np.full(palette.shape[0], -1, dtype=np.int32)
    for new_index, old_index in enumerate(survivors):
        remap[old_index] = new_index
    final = out.copy()
    good = out >= 0
    final[good] = remap[out[good]]
    return final, palette[survivors]


def _neighbour_counts(labels: np.ndarray, mask: np.ndarray, count: int,
                      index: int):
    """Which colours does this region touch, and how much of each."""
    ring = ndimage.binary_dilation(
        mask, ndimage.generate_binary_structure(2, 2), 1) & ~mask
    touching = labels[ring]
    touching = touching[touching >= 0]
    if touching.size == 0:
        return []
    counts = np.bincount(touching, minlength=count)
    counts[index] = 0
    order = np.argsort(-counts)
    return [(int(i), int(counts[i])) for i in order if counts[i] > 0]


def _is_between(colour, first, second, max_distance: float = 14.0) -> bool:
    """True when *colour* is an interpolation of the two others (a halo)."""
    axis = second - first
    length = float(np.dot(axis, axis))
    if length < 1e-6:
        return False
    t = float(np.dot(colour - first, axis) / length)
    if not 0.12 < t < 0.88:
        return False
    projected = first + t * axis
    return float(np.sqrt(((colour - projected) ** 2).sum())) < max_distance


def _majority_filter(labels: np.ndarray, count: int, sigma: float) -> np.ndarray:
    """Locally vote for the dominant colour.

    Anti-aliased edges produce one-pixel-wide bands of an intermediate colour.
    Traced as-is they become visible fringes around every shape, so each pixel
    is reassigned to the colour that dominates its neighbourhood.
    """
    from scipy.ndimage import gaussian_filter
    best_score = np.full(labels.shape, -1.0, dtype=np.float32)
    best_label = np.zeros(labels.shape, dtype=np.int32)
    for index in range(count):
        score = gaussian_filter((labels == index).astype(np.float32), sigma,
                                mode="nearest")
        better = score > best_score
        best_score[better] = score[better]
        best_label[better] = index
    return np.where(labels < 0, -1, best_label).astype(np.int32)


def _merge_similar(labels: np.ndarray, centers: np.ndarray, palette: np.ndarray,
                   min_delta: float):
    """Collapse palette entries that no human eye would tell apart.

    k-means happily splits a noisy background into three near-identical
    greys; each of them would then be traced as thousands of specks.
    """
    n = centers.shape[0]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if float(np.sqrt(((centers[i] - centers[j]) ** 2).sum())) < min_delta:
                a, b = find(i), find(j)
                if a != b:
                    parent[max(a, b)] = min(a, b)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    if len(groups) == n:
        return labels, palette

    remap = np.full(n, -1, dtype=np.int32)
    new_palette = []
    for new_index, (_, members) in enumerate(sorted(groups.items())):
        weights = np.array([max(1, int((labels == m).sum())) for m in members],
                           dtype=np.float32)
        colour = (palette[members] * weights[:, None]).sum(axis=0) / weights.sum()
        new_palette.append(colour)
        for member in members:
            remap[member] = new_index
    out = labels.copy()
    valid = labels >= 0
    out[valid] = remap[labels[valid]]
    return out, np.array(new_palette, dtype=np.float32)


def _assign_nearest(data: np.ndarray, centers: np.ndarray, chunk: int = 200_000):
    out = np.empty(data.shape[0], dtype=np.int64)
    for start in range(0, data.shape[0], chunk):
        block = data[start:start + chunk]
        d = ((block[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        out[start:start + chunk] = np.argmin(d, axis=1)
    return out


def _otsu(values: np.ndarray) -> float:
    hist, edges = np.histogram(values, bins=256, range=(0, 255))
    total = hist.sum()
    if total == 0:
        return 128.0
    p = hist.astype(np.float64) / total
    omega = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    mu_t = mu[-1]
    denom = omega * (1.0 - omega)
    denom[denom == 0] = 1e-9
    sigma = (mu_t * omega - mu) ** 2 / denom
    return float(np.argmax(sigma))


def _background_index(labels: np.ndarray, palette: np.ndarray,
                      opt: VectorOptions) -> int:
    if opt.background:
        from .imaging import hex_to_rgb
        want = np.array(hex_to_rgb(opt.background), dtype=np.float32)
        return int(np.argmin(((palette - want) ** 2).sum(axis=1)))
    border = np.concatenate([labels[0, :], labels[-1, :],
                             labels[:, 0], labels[:, -1]])
    border = border[border >= 0]
    if border.size == 0:
        return -1
    return int(np.bincount(border).argmax())


def _clean(mask: np.ndarray, min_area: int) -> np.ndarray:
    """Remove specks and pinholes smaller than *min_area* pixels."""
    if min_area <= 1:
        return mask
    labels, count = ndimage.label(mask)
    if count:
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        small = np.isin(labels, np.where(sizes < min_area)[0])
        mask = mask & ~small
    holes, hcount = ndimage.label(~mask)
    if hcount:
        sizes = np.bincount(holes.ravel())
        border_ids = set(np.unique(np.concatenate(
            [holes[0, :], holes[-1, :], holes[:, 0], holes[:, -1]])).tolist())
        fill_ids = [i for i in range(1, hcount + 1)
                    if i not in border_ids and sizes[i] < min_area]
        if fill_ids:
            mask = mask | np.isin(holes, fill_ids)
    return mask


# --------------------------------------------------------------------------- #
# Contour extraction
# --------------------------------------------------------------------------- #
_DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))       # right, down, left, up


def _trace(mask: np.ndarray) -> list[np.ndarray]:
    """Extract closed rings running along the pixel borders of *mask*.

    Coordinates are grid coordinates: pixel (y, x) occupies the unit square
    from (x, y) to (x+1, y+1), so the returned rings are exact outlines.
    """
    padded = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), dtype=bool)
    padded[1:-1, 1:-1] = mask
    h, w = padded.shape
    stride = w + 1

    up = padded & ~np.roll(padded, 1, axis=0)
    down = padded & ~np.roll(padded, -1, axis=0)
    left = padded & ~np.roll(padded, 1, axis=1)
    right = padded & ~np.roll(padded, -1, axis=1)

    edges: dict[int, list[int]] = {}

    def add(ys, xs, dx0, dy0, dx1, dy1):
        starts = (ys + dy0) * stride + (xs + dx0)
        ends = (ys + dy1) * stride + (xs + dx1)
        for s, e in zip(starts.tolist(), ends.tolist()):
            edges.setdefault(s, []).append(e)

    ys, xs = np.where(up)
    add(ys, xs, 0, 0, 1, 0)          # top edge,走 right
    ys, xs = np.where(right)
    add(ys, xs, 1, 0, 1, 1)          # right edge, down
    ys, xs = np.where(down)
    add(ys, xs, 1, 1, 0, 1)          # bottom edge, left
    ys, xs = np.where(left)
    add(ys, xs, 0, 1, 0, 0)          # left edge, up

    rings: list[np.ndarray] = []
    for start in list(edges.keys()):
        while edges.get(start):
            ring = _walk(edges, start, stride)
            if ring is not None and len(ring) >= 4:
                rings.append(ring - 1.0)      # undo the padding offset
    return rings


def _walk(edges: dict[int, list[int]], start: int, stride: int):
    """Follow one closed loop, turning as tightly as possible at junctions."""
    ring: list[tuple[float, float]] = []
    current = start
    prev_dir: tuple[int, int] | None = None
    while True:
        options = edges.get(current)
        if not options:
            return None
        if len(options) == 1 or prev_dir is None:
            nxt = options.pop(0)
        else:
            nxt = _pick(options, current, prev_dir, stride)
            options.remove(nxt)
        if not options:
            edges.pop(current, None)
        cx, cy = current % stride, current // stride
        nx, ny = nxt % stride, nxt // stride
        ring.append((float(cx), float(cy)))
        prev_dir = (nx - cx, ny - cy)
        current = nxt
        if current == start:
            break
        if len(ring) > 8_000_000:                 # safety valve
            return None
    return np.array(ring, dtype=np.float64)


def _pick(options: list[int], current: int, prev_dir, stride: int) -> int:
    """At a diagonal junction pick the sharpest right turn (8-connectivity)."""
    dx, dy = prev_dir
    priority = [(-dy, dx), (dx, dy), (dy, -dx), (-dx, -dy)]  # right, straight, left, back
    cx, cy = current % stride, current // stride
    for pdx, pdy in priority:
        target = (cy + pdy) * stride + (cx + pdx)
        if target in options:
            return target
    return options[0]


def _ring_area(points: np.ndarray) -> float:
    x, y = points[:, 0], points[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _group_rings(rings: list[np.ndarray], min_area: int):
    """Group outer rings with the holes they contain (even-odd friendly)."""
    kept = [r for r in rings if abs(_ring_area(r)) >= max(1.0, min_area * 0.5)]
    if not kept:
        return []
    outers, holes = [], []
    for ring in kept:
        (outers if _ring_area(ring) > 0 else holes).append(ring)
    if not outers:
        outers, holes = kept, []

    boxes = [(r[:, 0].min(), r[:, 1].min(), r[:, 0].max(), r[:, 1].max())
             for r in outers]
    shapes: list[list[np.ndarray]] = [[o] for o in outers]
    for hole in holes:
        hx, hy = hole[0, 0], hole[0, 1]
        best, best_area = -1, math.inf
        for i, (x0, y0, x1, y1) in enumerate(boxes):
            if x0 <= hx <= x1 and y0 <= hy <= y1:
                area = (x1 - x0) * (y1 - y0)
                if area < best_area:
                    best, best_area = i, area
        if best >= 0:
            shapes[best].append(hole)
    shapes.sort(key=lambda s: -abs(_ring_area(s[0])))
    return shapes


# --------------------------------------------------------------------------- #
# Smoothing / simplification
# --------------------------------------------------------------------------- #
def _smooth(ring: np.ndarray, strength: float) -> np.ndarray:
    """Kill the pixel staircase: Chaikin cutting + corner-aware averaging."""
    strength = max(0.0, min(3.0, strength))
    if strength <= 0:
        return ring
    pts = ring
    for _ in range(2):                        # Chaikin corner cutting
        if len(pts) < 4 or len(pts) > 40000:
            break
        nxt = np.roll(pts, -1, axis=0)
        q, r = 0.75 * pts + 0.25 * nxt, 0.25 * pts + 0.75 * nxt
        pts = np.empty((len(pts) * 2, 2), dtype=np.float64)
        pts[0::2], pts[1::2] = q, r

    if len(pts) < 12:
        return pts
    window = 4                                # ≈1 source pixel after Chaikin
    v_in = pts - np.roll(pts, window, axis=0)
    v_out = np.roll(pts, -window, axis=0) - pts
    n_in = np.linalg.norm(v_in, axis=1, keepdims=True)
    n_out = np.linalg.norm(v_out, axis=1, keepdims=True)
    cos = np.sum((v_in / np.maximum(n_in, 1e-9)) * (v_out / np.maximum(n_out, 1e-9)),
                 axis=1)
    # Real corners (>60° turn) are frozen; staircase noise gets averaged out.
    weight = np.clip((cos - 0.5) / 0.4, 0.0, 1.0)[:, None]

    for _ in range(int(round(3 * strength))):
        blurred = 0.25 * np.roll(pts, 1, axis=0) + 0.5 * pts + \
                  0.25 * np.roll(pts, -1, axis=0)
        pts = pts * (1.0 - weight) + blurred * weight
    return pts


def _rdp_closed(points: np.ndarray, epsilon: float) -> np.ndarray:
    if len(points) <= 4:
        return points
    # Anchor on the point furthest from the centroid so the split is stable.
    centroid = points.mean(axis=0)
    start = int(np.argmax(((points - centroid) ** 2).sum(axis=1)))
    rolled = np.roll(points, -start, axis=0)
    closed = np.vstack([rolled, rolled[:1]])
    simplified = _rdp(closed, epsilon)
    result = simplified[:-1] if len(simplified) > 3 else rolled
    return _drop_close(result, max(0.35, epsilon * 0.6))


def _drop_close(points: np.ndarray, min_dist: float) -> np.ndarray:
    """Remove quasi-duplicated nodes: they make Bézier handles explode."""
    if len(points) <= 4:
        return points
    keep = [0]
    for i in range(1, len(points)):
        if np.hypot(*(points[i] - points[keep[-1]])) >= min_dist:
            keep.append(i)
    if len(keep) >= 4 and np.hypot(*(points[keep[-1]] - points[keep[0]])) < min_dist:
        keep.pop()
    return points[keep] if len(keep) >= 4 else points


def _rdp(points: np.ndarray, epsilon: float) -> np.ndarray:
    n = len(points)
    if n < 3:
        return points
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        p0, p1 = points[i0], points[i1]
        seg = p1 - p0
        length = math.hypot(seg[0], seg[1])
        block = points[i0 + 1:i1]
        if length < 1e-9:
            dist = np.hypot(block[:, 0] - p0[0], block[:, 1] - p0[1])
        else:
            dist = np.abs(seg[0] * (p0[1] - block[:, 1]) -
                          (p0[0] - block[:, 0]) * seg[1]) / length
        idx = int(np.argmax(dist))
        if dist[idx] > epsilon:
            split = i0 + 1 + idx
            keep[split] = True
            stack.append((i0, split))
            stack.append((split, i1))
    return points[keep]


# --------------------------------------------------------------------------- #
# SVG output
# --------------------------------------------------------------------------- #
def _fmt(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def ring_segments(ring: np.ndarray, curves: bool, smoothing: float):
    """Convert a closed ring into drawing segments.

    Yields ``("L", p0, p1)`` for straight edges and ``("C", p0, c0, c1, p1)``
    for cubic Béziers.  Both the SVG writer and the rasteriser consume this,
    which guarantees the preview matches the exported vector file.
    """
    n = len(ring)
    if n < 3:
        return []
    if not curves:
        return [("L", ring[i], ring[(i + 1) % n]) for i in range(n)]

    tension = 0.33 * max(0.2, min(1.6, smoothing if smoothing > 0 else 1.0))
    prev, nxt = np.roll(ring, 1, axis=0), np.roll(ring, -1, axis=0)
    tangents = (nxt - prev) * 0.5

    # Corner detection uses a 2-node window: a single noisy node must not be
    # mistaken for a corner, otherwise smooth outlines grow spikes.
    far_prev, far_next = np.roll(ring, 2, axis=0), np.roll(ring, -2, axis=0)
    v_in, v_out = ring - far_prev, far_next - ring
    n_in = np.linalg.norm(v_in, axis=1, keepdims=True)
    n_out = np.linalg.norm(v_out, axis=1, keepdims=True)
    cos = np.sum((v_in / np.maximum(n_in, 1e-9)) * (v_out / np.maximum(n_out, 1e-9)),
                 axis=1)
    corner = cos < 0.2                       # > ~78° direction change

    segments = []
    for i in range(n):
        j = (i + 1) % n
        p0, p1 = ring[i], ring[j]
        chord = float(np.hypot(*(p1 - p0)))
        t0 = np.zeros(2) if corner[i] else tangents[i]
        t1 = np.zeros(2) if corner[j] else tangents[j]
        if not t0.any() and not t1.any():
            segments.append(("L", p0, p1))
            continue
        c0 = p0 + _clamp_handle(t0 * tension, chord)
        c1 = p1 - _clamp_handle(t1 * tension, chord)
        segments.append(("C", p0, c0, c1, p1))
    return segments


def _clamp_handle(vec: np.ndarray, chord: float) -> np.ndarray:
    """Keep a control handle below half the chord to avoid overshoot loops."""
    length = float(np.hypot(vec[0], vec[1]))
    limit = chord * 0.5
    if length > limit > 0:
        return vec * (limit / length)
    return vec


def _ring_to_path(ring: np.ndarray, curves: bool, smoothing: float) -> str:
    segments = ring_segments(ring, curves, smoothing)
    if not segments:
        return ""
    parts = [f"M{_fmt(ring[0][0])} {_fmt(ring[0][1])}"]
    for seg in segments:
        if seg[0] == "L":
            parts.append(f"L{_fmt(seg[2][0])} {_fmt(seg[2][1])}")
        else:
            _, _, c0, c1, p1 = seg
            parts.append(f"C{_fmt(c0[0])} {_fmt(c0[1])} {_fmt(c1[0])} {_fmt(c1[1])} "
                         f"{_fmt(p1[0])} {_fmt(p1[1])}")
    parts.append("Z")
    return "".join(parts)


def _build_svg(layers: list[Layer], width: int, height: int,
               opt: VectorOptions) -> str:
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
           f'height="{height}" viewBox="0 0 {width} {height}" '
           f'shape-rendering="geometricPrecision">']
    for layer in layers:
        d_parts = []
        for shape in layer.paths:
            for ring in shape:
                d_parts.append(_ring_to_path(ring, opt.curves, opt.smoothing))
        d = "".join(p for p in d_parts if p)
        if not d:
            continue
        out.append(f'<path fill="{rgb_to_hex(layer.color)}" '
                   f'fill-rule="evenodd" d="{d}"/>')
    out.append("</svg>")
    return "\n".join(out)
