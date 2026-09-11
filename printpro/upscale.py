"""Image enlargement for print.

Three strategies, pickable or automatic:

``lanczos``  plain high quality resampling — the safe baseline.
``edge``     Lanczos + edge-guided unsharp masking and optional denoising, so
             photos stay crisp instead of turning into a blurry mush.
``vector``   trace the artwork then re-render it at the target size: a logo
             enlarged 10x stays perfectly sharp, which is what a print shop
             actually needs.

If a Real-ESRGAN ncnn binary is available in ``PATH`` (or ``PRINTPRO_ESRGAN``)
the ``ai`` method uses it; otherwise it degrades to ``edge``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .imaging import (MM_PER_INCH, gaussian_blur, is_photographic, resize,
                      save_image, load_rgba)


@dataclass
class UpscaleOptions:
    scale: float = 2.0
    target_width: int | None = None      # px, wins over scale
    target_height: int | None = None
    target_mm: tuple[float, float] | None = None   # physical size...
    target_dpi: int | None = None                  # ...at this resolution
    method: str = "auto"                 # auto | lanczos | edge | vector | ai
    denoise: float = 0.0                 # 0..1, for noisy/JPEG sources
    sharpen: float = 0.45                # 0..1.5 edge-guided unsharp amount
    vector_colors: int = 12              # palette used by the vector method
    max_pixels: int = 120_000_000        # guard rail (~120 Mpx)


@dataclass
class UpscaleResult:
    image: np.ndarray
    method: str
    factor: float
    source_size: tuple[int, int]
    target_size: tuple[int, int]
    notes: list[str]


def target_size(rgba: np.ndarray, opt: UpscaleOptions) -> tuple[int, int]:
    h, w = rgba.shape[:2]
    if opt.target_width or opt.target_height:
        if opt.target_width and opt.target_height:
            return int(opt.target_width), int(opt.target_height)
        if opt.target_width:
            return int(opt.target_width), max(1, round(h * opt.target_width / w))
        return max(1, round(w * opt.target_height / h)), int(opt.target_height)
    if opt.target_mm and opt.target_dpi:
        mm_w, mm_h = opt.target_mm
        return (max(1, round(mm_w / MM_PER_INCH * opt.target_dpi)),
                max(1, round(mm_h / MM_PER_INCH * opt.target_dpi)))
    factor = max(0.05, float(opt.scale))
    return max(1, round(w * factor)), max(1, round(h * factor))


def upscale(rgba: np.ndarray, options: UpscaleOptions | None = None) -> UpscaleResult:
    opt = options or UpscaleOptions()
    rgba = np.asarray(rgba, dtype=np.uint8)
    h, w = rgba.shape[:2]
    tw, th = target_size(rgba, opt)
    notes: list[str] = []

    if tw * th > opt.max_pixels:
        shrink = (opt.max_pixels / float(tw * th)) ** 0.5
        tw, th = max(1, int(tw * shrink)), max(1, int(th * shrink))
        notes.append(f"taille limitée à {tw}×{th} px ({opt.max_pixels/1e6:.0f} Mpx max)")

    factor = (tw / w + th / h) / 2.0
    method = opt.method
    if method == "auto":
        method = "edge" if is_photographic(rgba) or factor <= 1.0 else "vector"
        notes.append(f"méthode choisie automatiquement : {method}")

    source = rgba
    if opt.denoise > 0:
        source = _denoise(source, opt.denoise)
        notes.append(f"débruitage {opt.denoise:.2f}")

    if method == "ai":
        out = _esrgan(source, tw, th)
        if out is None:
            method, notes_add = "edge", "Real-ESRGAN indisponible, repli sur 'edge'"
            notes.append(notes_add)
        else:
            return UpscaleResult(out, "ai", factor, (w, h), (tw, th), notes)

    if method == "vector":
        out = _vector_upscale(source, tw, th, opt)
        if out is None:
            method = "edge"
            notes.append("vectorisation non concluante, repli sur 'edge'")
        else:
            return UpscaleResult(out, "vector", factor, (w, h), (tw, th), notes)

    out = resize(source, tw, th, Image.LANCZOS)
    if method == "edge" and opt.sharpen > 0 and factor > 1.0:
        out = _edge_sharpen(out, factor, opt.sharpen)
    return UpscaleResult(out, method, factor, (w, h), (tw, th), notes)


# --------------------------------------------------------------------------- #
def _edge_sharpen(rgba: np.ndarray, factor: float, amount: float) -> np.ndarray:
    """Unsharp mask weighted by local gradient: sharpens edges, not noise."""
    rgb = rgba[:, :, :3].astype(np.float32)
    sigma = max(0.6, min(3.0, 0.5 * factor))
    blurred = gaussian_blur(rgb, sigma)
    detail = rgb - blurred

    gray = rgb.mean(axis=2)
    gy, gx = np.gradient(gaussian_blur(gray, 1.0))
    grad = np.hypot(gx, gy)
    scale = np.percentile(grad, 97) or 1.0
    weight = np.clip(grad / scale, 0.0, 1.0)[:, :, None] ** 0.7

    out = rgb + detail * (float(amount) * 1.4) * weight
    result = rgba.copy()
    result[:, :, :3] = np.clip(out + 0.5, 0, 255).astype(np.uint8)
    return result


def _denoise(rgba: np.ndarray, strength: float) -> np.ndarray:
    """Edge preserving smoothing (bilateral-ish) on flat areas only."""
    from scipy.ndimage import median_filter
    strength = float(np.clip(strength, 0.0, 1.0))
    rgb = rgba[:, :, :3].astype(np.float32)
    size = 3 if strength < 0.6 else 5
    med = median_filter(rgb, size=(size, size, 1), mode="nearest")

    gray = rgb.mean(axis=2)
    gy, gx = np.gradient(gaussian_blur(gray, 1.0))
    grad = np.hypot(gx, gy)
    scale = np.percentile(grad, 90) or 1.0
    flat = np.clip(1.0 - grad / max(scale, 1e-6), 0.0, 1.0)[:, :, None]

    mixed = rgb * (1.0 - flat * strength) + med * (flat * strength)
    out = rgba.copy()
    out[:, :, :3] = np.clip(mixed + 0.5, 0, 255).astype(np.uint8)
    return out


def _vector_upscale(rgba: np.ndarray, tw: int, th: int, opt: UpscaleOptions):
    """Trace then re-render — infinite sharpness on logos and line art."""
    from .raster import render_layers
    from .vectorize import VectorOptions, vectorize

    h, w = rgba.shape[:2]
    # Scale the cleaning thresholds with the source size: on a small noisy
    # scan, 4-pixel specks must not become 4-millimetre blobs once enlarged.
    pixels = w * h
    min_area = int(max(10, round(pixels / 12000)))
    result = vectorize(rgba, VectorOptions(colors=max(2, opt.vector_colors),
                                           detail=0.9, smoothing=1.2,
                                           blur=0.7, min_area=min_area,
                                           merge_delta=7.0, edge_cleanup=0.8,
                                           drop_background=True))
    if not result.layers:
        return None
    scale = (tw / w + th / h) / 2.0
    rendered = render_layers(result.layers, w, h, scale=scale)
    if rendered.shape[1] != tw or rendered.shape[0] != th:
        rendered = resize(rendered, tw, th, Image.LANCZOS)

    # Keep the original transparency (the trace only covers opaque pixels).
    if rgba.shape[2] == 4 and (rgba[:, :, 3] < 250).any():
        alpha = resize(np.dstack([rgba[:, :, 3]] * 3 +
                                 [np.full(rgba.shape[:2], 255, np.uint8)]),
                       tw, th, Image.LANCZOS)[:, :, 0]
        rendered[:, :, 3] = np.minimum(rendered[:, :, 3], alpha)
    return rendered


def _esrgan(rgba: np.ndarray, tw: int, th: int):  # pragma: no cover - optional
    """Use an external Real-ESRGAN ncnn binary when the operator installed one."""
    binary = os.environ.get("PRINTPRO_ESRGAN") or shutil.which("realesrgan-ncnn-vulkan")
    if not binary:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.png"
        dst = Path(tmp) / "out.png"
        save_image(rgba, src)
        factor = max(2, min(4, int(round(max(tw / rgba.shape[1],
                                             th / rgba.shape[0])))))
        try:
            subprocess.run([binary, "-i", str(src), "-o", str(dst),
                            "-s", str(factor)], check=True, timeout=600,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            out = load_rgba(dst)
        except Exception:
            return None
    return resize(out, tw, th, Image.LANCZOS)


def esrgan_available() -> bool:
    return bool(os.environ.get("PRINTPRO_ESRGAN") or
                shutil.which("realesrgan-ncnn-vulkan"))


def print_quality(width: int, height: int, mm_w: float, mm_h: float) -> dict:
    """Report the effective DPI when printing *width×height* px at that size."""
    dpi_x = width / (mm_w / MM_PER_INCH) if mm_w else 0.0
    dpi_y = height / (mm_h / MM_PER_INCH) if mm_h else 0.0
    dpi = min(dpi_x, dpi_y)
    if dpi >= 299.5:
        grade, label = "excellent", "qualité photo (≥300 dpi)"
    elif dpi >= 199.5:
        grade, label = "bon", "bon pour l'impression courante (≥200 dpi)"
    elif dpi >= 149.5:
        grade, label = "moyen", "acceptable en grand format / affiche"
    else:
        grade, label = "faible", "insuffisant, agrandir la source"
    return {"dpi": round(dpi, 1), "grade": grade, "label": label}
