"""Anti-aliased polygon rasteriser.

Used to preview traced artwork, to re-render a vector at any resolution
(vector upscaling) and to draw the montage sheets.  Scanline filling with the
even-odd rule, 4 sub-scanlines per pixel row and analytic horizontal coverage,
processed row by row so memory stays O(width) whatever the sheet size.
"""

from __future__ import annotations

import numpy as np

from .vectorize import Layer, ring_segments

SUBSAMPLES = 4


def flatten_ring(ring: np.ndarray, curves: bool, smoothing: float,
                 scale: float) -> np.ndarray:
    """Flatten a ring (with its Béziers) into a polyline at a given scale."""
    segments = ring_segments(ring, curves, smoothing)
    if not segments:
        return np.zeros((0, 2))
    points: list[np.ndarray] = []
    for seg in segments:
        if seg[0] == "L":
            points.append(np.asarray(seg[1], dtype=np.float64))
            continue
        _, p0, c0, c1, p1 = seg
        span = (np.abs(c0 - p0).sum() + np.abs(c1 - c0).sum() +
                np.abs(p1 - c1).sum()) * scale
        steps = int(min(48, max(3, span / 3.0)))
        t = np.linspace(0.0, 1.0, steps, endpoint=False)[:, None]
        mt = 1.0 - t
        curve = (mt ** 3 * p0 + 3 * mt ** 2 * t * c0 +
                 3 * mt * t ** 2 * c1 + t ** 3 * p1)
        points.append(curve)
    stacked = np.vstack([p.reshape(-1, 2) for p in points])
    return stacked


def rasterize_polygons(polygons: list[np.ndarray], width: int, height: int,
                       ) -> np.ndarray:
    """Coverage map (float32 0..1) of *polygons* filled with the even-odd rule."""
    cov = np.zeros((height, width), dtype=np.float32)
    xs0, ys0, xs1, ys1 = [], [], [], []
    for poly in polygons:
        if len(poly) < 3:
            continue
        a = poly
        b = np.roll(poly, -1, axis=0)
        keep = a[:, 1] != b[:, 1]                # horizontal edges never cross
        xs0.append(a[keep, 0]); ys0.append(a[keep, 1])
        xs1.append(b[keep, 0]); ys1.append(b[keep, 1])
    if not xs0:
        return cov
    x0 = np.concatenate(xs0); y0 = np.concatenate(ys0)
    x1 = np.concatenate(xs1); y1 = np.concatenate(ys1)
    if x0.size == 0:
        return cov

    ymin = np.minimum(y0, y1)
    ymax = np.maximum(y0, y1)
    slope = (x1 - x0) / (y1 - y0)

    # Bucket edges per pixel row so each scanline only tests relevant edges.
    row_start = np.clip(np.floor(ymin).astype(np.int64), 0, height - 1)
    row_end = np.clip(np.ceil(ymax).astype(np.int64), 0, height)
    buckets: list[list[int]] = [[] for _ in range(height)]
    for i in range(x0.size):
        if row_end[i] <= row_start[i]:
            if 0 <= row_start[i] < height:
                buckets[row_start[i]].append(i)
            continue
        for row in range(row_start[i], min(row_end[i], height)):
            buckets[row].append(i)

    weight = 1.0 / SUBSAMPLES
    acc = np.zeros(width + 2, dtype=np.float64)
    for row in range(height):
        idx = buckets[row]
        if not idx:
            continue
        index = np.asarray(idx, dtype=np.int64)
        e_ymin, e_ymax = ymin[index], ymax[index]
        e_x0, e_y0, e_slope = x0[index], y0[index], slope[index]
        acc[:] = 0.0
        touched = False
        for sub in range(SUBSAMPLES):
            sy = row + (sub + 0.5) * weight
            hit = (e_ymin <= sy) & (sy < e_ymax)
            if not hit.any():
                continue
            crossings = e_x0[hit] + (sy - e_y0[hit]) * e_slope[hit]
            crossings.sort()
            for k in range(0, crossings.size - 1, 2):
                a, b = crossings[k], crossings[k + 1]
                if b <= 0 or a >= width or b <= a:
                    continue
                touched = True
                _add_span(acc, max(a, 0.0), min(b, float(width)), weight)
        if touched:
            cov[row] = np.clip(acc[:width], 0.0, 1.0).astype(np.float32)
    return cov


def _add_span(acc: np.ndarray, a: float, b: float, weight: float) -> None:
    i0, i1 = int(a), int(b)
    if i0 == i1:
        acc[i0] += (b - a) * weight
        return
    acc[i0] += (i0 + 1 - a) * weight
    if i1 > i0 + 1:
        acc[i0 + 1:i1] += weight
    acc[i1] += (b - i1) * weight


def render_layers(layers: list[Layer], width: int, height: int,
                  scale: float = 1.0, curves: bool = True,
                  smoothing: float = 1.0,
                  background: tuple[int, int, int] | None = None) -> np.ndarray:
    """Render traced layers into an RGBA array scaled by *scale*."""
    out_w = max(1, int(round(width * scale)))
    out_h = max(1, int(round(height * scale)))
    canvas = np.zeros((out_h, out_w, 4), dtype=np.float32)
    if background is not None:
        canvas[:, :, :3] = np.array(background[:3], dtype=np.float32)
        canvas[:, :, 3] = 255.0

    for layer in layers:
        polygons = []
        for shape in layer.paths:
            for ring in shape:
                poly = flatten_ring(ring, curves, smoothing, scale)
                if len(poly) >= 3:
                    polygons.append(poly * scale)
        if not polygons:
            continue
        cov = rasterize_polygons(polygons, out_w, out_h)[:, :, None]
        color = np.array(layer.color[:3], dtype=np.float32).reshape(1, 1, 3)
        src_a = cov
        dst_a = canvas[:, :, 3:4] / 255.0
        out_a = src_a + dst_a * (1.0 - src_a)
        rgb = (color * src_a + canvas[:, :, :3] * dst_a * (1.0 - src_a))
        canvas[:, :, :3] = np.where(out_a > 1e-6, rgb / np.maximum(out_a, 1e-6),
                                    canvas[:, :, :3])
        canvas[:, :, 3:4] = out_a * 255.0
    return np.clip(canvas + 0.5, 0, 255).astype(np.uint8)
