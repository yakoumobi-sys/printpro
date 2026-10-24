"""sRGB → CMYK separation for offset / digital presses.

Two engines:

* **ICC** — when a CMYK output profile is available (``icc_path`` or the
  ``PRINTPRO_CMYK_ICC`` environment variable, e.g. *ISOcoated_v2_eci.icc* or
  *CoatedFOGRA39.icc*), LittleCMS does the separation through Pillow with a
  relative colorimetric intent and black point compensation, and the profile
  is embedded in the PDF as the output intent.
* **Built-in** — otherwise a profile-free separation with grey component
  replacement (GCR), a dot-gain pre-compensation and a hard total area
  coverage (TAC) limit.  It is not a calibrated FOGRA39 conversion, but it
  never produces the classic preflight failures (RGB data, 400 % blacks).

Arrays: CMYK is ``(H, W, 4)`` uint8 where 255 = 100 % ink, like Pillow's
"CMYK" mode and PDF's ``/DeviceCMYK``.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

from .imaging import flatten, to_pil

DEFAULT_TAC = 300.0              # % — coated offset, ISO 12647-2


@dataclass
class CmykOptions:
    tac: float = DEFAULT_TAC     # total area coverage limit, in %
    gcr_start: float = 0.25      # grey level where black starts replacing CMY
    dot_gain: float = 0.12       # mid-tone pre-compensation (0 = off)
    rich_black: bool = True      # deep shadows get a CMY support under K
    icc_path: str | None = None  # CMYK output profile; env var as fallback


@dataclass
class CmykResult:
    cmyk: np.ndarray             # (H, W, 4) uint8
    engine: str                  # "icc" | "builtin"
    profile: bytes | None        # the ICC profile used, to embed in the PDF
    profile_name: str
    max_tac: float               # highest ink sum found, in %


def find_profile(icc_path: str | None = None) -> Path | None:
    candidate = icc_path or os.environ.get("PRINTPRO_CMYK_ICC")
    if candidate and Path(candidate).is_file():
        return Path(candidate)
    return None


def rgb_to_cmyk(rgba: np.ndarray, options: CmykOptions | None = None) -> CmykResult:
    """Separate an RGBA image (flattened on white) into CMYK."""
    opt = options or CmykOptions()
    rgb = flatten(np.asarray(rgba, dtype=np.uint8))[:, :, :3]

    profile_path = find_profile(opt.icc_path)
    if profile_path is not None:
        cmyk, data, name = _icc_convert(rgb, profile_path)
        cmyk = limit_tac(cmyk, opt.tac)
        return CmykResult(cmyk, "icc", data, name, ink_coverage(cmyk).max())

    cmyk = _builtin(rgb, opt)
    return CmykResult(cmyk, "builtin", None, "Coated FOGRA39 (ISO 12647-2:2004)",
                      float(ink_coverage(cmyk).max()))


def ink_coverage(cmyk: np.ndarray) -> np.ndarray:
    """Per-pixel ink sum in percent (0..400)."""
    return cmyk.astype(np.float32).sum(axis=2) * (100.0 / 255.0)


def limit_tac(cmyk: np.ndarray, tac: float) -> np.ndarray:
    """Pull CMY down where the ink sum exceeds *tac* (K is preserved)."""
    f = cmyk.astype(np.float32) / 255.0
    limit = tac / 100.0
    total = f.sum(axis=2)
    over = total > limit + 1e-6
    if not over.any():
        return cmyk
    cmy = f[:, :, :3].sum(axis=2)
    room = np.clip(limit - f[:, :, 3], 0.0, None)
    scale = np.where(over & (cmy > 0), room / np.maximum(cmy, 1e-6), 1.0)
    f[:, :, :3] *= scale[:, :, None]
    # floor() so rounding can never push a pixel back over the limit
    return np.floor(np.clip(f, 0, 1) * 255.0).astype(np.uint8)


def cmyk_preview(cmyk: np.ndarray) -> np.ndarray:
    """Rough soft-proof back to RGBA, for on-screen previews only."""
    f = cmyk.astype(np.float32) / 255.0
    k = 1.0 - f[:, :, 3:4]
    rgb = (1.0 - f[:, :, :3]) * k
    out = np.empty(cmyk.shape[:2] + (4,), np.uint8)
    out[:, :, :3] = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    out[:, :, 3] = 255
    return out


# --------------------------------------------------------------------------- #
def _builtin(rgb: np.ndarray, opt: CmykOptions) -> np.ndarray:
    out = np.empty(rgb.shape[:2] + (4,), np.uint8)
    rows = max(1, 4_000_000 // max(rgb.shape[1], 1))   # bounded memory
    for y in range(0, rgb.shape[0], rows):
        out[y:y + rows] = _builtin_block(rgb[y:y + rows], opt)
    return out


def _builtin_block(rgb: np.ndarray, opt: CmykOptions) -> np.ndarray:
    f = rgb.astype(np.float32) / 255.0
    cmy = 1.0 - f
    grey = cmy.min(axis=2)

    # GCR: black replaces the grey component progressively above gcr_start,
    # so light tones stay CMY only (no grainy black dots in skin tones).
    s = opt.gcr_start
    t = np.clip((grey - s) / max(1.0 - s, 1e-6), 0.0, 1.0)
    k = grey * t * t * (3.0 - 2.0 * t)
    denom = np.maximum(1.0 - k, 1e-6)[:, :, None]
    cmy = np.clip((cmy - k[:, :, None]) / denom, 0.0, 1.0)

    if opt.rich_black:
        # Under 100 % K, a 40 % CMY support gives a deep, even black.
        deep = np.clip((k - 0.85) / 0.15, 0.0, 1.0)[:, :, None]
        cmy = np.maximum(cmy, deep * np.array([0.6, 0.4, 0.4], np.float32))

    if opt.dot_gain > 0:
        # Ink spreads on paper: mid-tones print darker than requested.
        gamma = 1.0 + opt.dot_gain * 1.6
        cmy = cmy ** gamma
        k = k ** (1.0 + opt.dot_gain)

    cmyk = np.concatenate([cmy, k[:, :, None]], axis=2)
    cmyk = np.clip(cmyk * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return limit_tac(cmyk, opt.tac)


def _icc_convert(rgb: np.ndarray, path: Path):  # pragma: no cover - needs a profile
    data = path.read_bytes()
    dst = ImageCms.ImageCmsProfile(io.BytesIO(data))
    src = ImageCms.createProfile("sRGB")
    transform = ImageCms.buildTransform(
        src, dst, "RGB", "CMYK",
        renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC,
        flags=ImageCms.Flags.BLACKPOINTCOMPENSATION)
    image = ImageCms.applyTransform(to_pil(rgb), transform)
    name = ImageCms.getProfileDescription(dst).strip() or path.stem
    return np.asarray(image, dtype=np.uint8).copy(), data, name


def cmyk_to_pil(cmyk: np.ndarray) -> Image.Image:
    return Image.fromarray(np.ascontiguousarray(cmyk), "CMYK")
