"""Low level image helpers shared by every PrintPro module.

Images circulate inside PrintPro as ``numpy`` arrays of shape ``(H, W, 4)``
and dtype ``uint8`` (straight/unassociated RGBA, sRGB encoded).  Keeping a
single representation everywhere removes a whole class of bugs when the
processing steps are chained (detour -> upscale -> vectorise -> montage).
"""

from __future__ import annotations

import io
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageOps

Image.MAX_IMAGE_PIXELS = 512_000_000  # 512 Mpx: large print files are legit here

MM_PER_INCH = 25.4


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def load_rgba(source: str | Path | bytes | Image.Image) -> np.ndarray:
    """Read *source* and return a ``(H, W, 4)`` uint8 RGBA array."""
    if isinstance(source, Image.Image):
        img = source
    elif isinstance(source, (bytes, bytearray)):
        img = Image.open(io.BytesIO(bytes(source)))
    else:
        img = Image.open(str(source))

    img = ImageOps.exif_transpose(img)
    img = _to_srgb(img)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return np.asarray(img, dtype=np.uint8).copy()


def _to_srgb(img: Image.Image) -> Image.Image:
    """Convert a CMYK/embedded-profile image to sRGB when we can."""
    profile = img.info.get("icc_profile")
    if profile:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(profile))
            dst = ImageCms.createProfile("sRGB")
            mode = "RGBA" if img.mode in ("RGBA", "LA", "PA") else "RGB"
            return ImageCms.profileToProfile(img, src, dst, outputMode=mode)
        except Exception:  # pragma: no cover - broken profiles are common
            pass
    if img.mode == "CMYK":
        return img.convert("RGB")
    return img


def to_pil(arr: np.ndarray) -> Image.Image:
    """Wrap an RGBA/RGB/gray array into a PIL image."""
    arr = np.asarray(arr)
    if arr.dtype != np.uint8:
        arr = np.clip(arr, 0, 255).astype(np.uint8)
    if arr.ndim == 2:
        return Image.fromarray(arr, "L")
    if arr.shape[2] == 3:
        return Image.fromarray(arr, "RGB")
    return Image.fromarray(arr, "RGBA")


def save_image(arr: np.ndarray, path: str | Path, dpi: int | None = None,
               quality: int = 95) -> Path:
    """Save *arr*, picking sane defaults per format (and keeping alpha)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img = to_pil(arr)
    suffix = path.suffix.lower()
    params: dict = {}
    if dpi:
        params["dpi"] = (dpi, dpi)
    if suffix in (".jpg", ".jpeg"):
        img = flatten(arr, (255, 255, 255))
        img = to_pil(img[:, :, :3])
        params.update(quality=quality, subsampling=0, optimize=True)
    elif suffix == ".webp":
        params.update(quality=quality, method=6)
    elif suffix in (".tif", ".tiff"):
        params.update(compression="tiff_lzw")
    else:
        params.update(optimize=True)
    img.save(path, **params)
    return path


def encode(arr: np.ndarray, fmt: str = "PNG", dpi: int | None = None) -> bytes:
    buf = io.BytesIO()
    img = to_pil(arr)
    params: dict = {"dpi": (dpi, dpi)} if dpi else {}
    if fmt.upper() in ("JPEG", "JPG"):
        img = to_pil(flatten(arr, (255, 255, 255))[:, :, :3])
        params.update(quality=92, subsampling=0)
        fmt = "JPEG"
    img.save(buf, format=fmt.upper(), **params)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Geometry / composition
# --------------------------------------------------------------------------- #
def flatten(rgba: np.ndarray, background=(255, 255, 255)) -> np.ndarray:
    """Composite an RGBA array over a solid colour, returns RGBA (alpha=255)."""
    rgba = np.asarray(rgba, dtype=np.uint8)
    if rgba.shape[2] == 3:
        return np.dstack([rgba, np.full(rgba.shape[:2], 255, np.uint8)])
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    bg = np.array(background[:3], dtype=np.float32).reshape(1, 1, 3)
    rgb = rgba[:, :, :3].astype(np.float32) * alpha + bg * (1.0 - alpha)
    out = np.empty_like(rgba)
    out[:, :, :3] = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
    out[:, :, 3] = 255
    return out


def alpha_bbox(rgba: np.ndarray, threshold: int = 8) -> tuple[int, int, int, int] | None:
    """Bounding box ``(x0, y0, x1, y1)`` of pixels whose alpha > threshold."""
    if rgba.shape[2] < 4:
        return 0, 0, rgba.shape[1], rgba.shape[0]
    mask = rgba[:, :, 3] > threshold
    if not mask.any():
        return None
    ys, xs = np.where(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def trim(rgba: np.ndarray, padding: int = 0, threshold: int = 8) -> np.ndarray:
    """Crop transparent borders, optionally keeping *padding* pixels around."""
    box = alpha_bbox(rgba, threshold)
    if box is None:
        return rgba
    x0, y0, x1, y1 = box
    h, w = rgba.shape[:2]
    x0, y0 = max(0, x0 - padding), max(0, y0 - padding)
    x1, y1 = min(w, x1 + padding), min(h, y1 + padding)
    return rgba[y0:y1, x0:x1].copy()


def resize(rgba: np.ndarray, width: int, height: int,
           resample: int = Image.LANCZOS) -> np.ndarray:
    """Alpha-correct resize (premultiply -> resize -> unpremultiply)."""
    width, height = max(1, int(width)), max(1, int(height))
    if rgba.shape[1] == width and rgba.shape[0] == height:
        return rgba.copy()
    if rgba.shape[2] == 4 and (rgba[:, :, 3] < 255).any():
        f = rgba.astype(np.float32) / 255.0
        f[:, :, :3] *= f[:, :, 3:4]                     # premultiply
        pm = to_pil(np.clip(f * 255.0 + 0.5, 0, 255).astype(np.uint8))
        pm = pm.resize((width, height), resample)
        g = np.asarray(pm, dtype=np.float32) / 255.0
        a = np.maximum(g[:, :, 3:4], 1e-6)
        g[:, :, :3] = np.clip(g[:, :, :3] / a, 0.0, 1.0)  # unpremultiply
        return np.clip(g * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return np.asarray(to_pil(rgba).resize((width, height), resample), dtype=np.uint8)


def rotate(rgba: np.ndarray, degrees: float, expand: bool = True) -> np.ndarray:
    """Rotate counter-clockwise; multiples of 90° stay pixel exact."""
    deg = degrees % 360
    if deg == 0:
        return rgba.copy()
    if deg in (90, 180, 270):
        return np.rot90(rgba, k=int(deg // 90)).copy()
    img = to_pil(rgba).rotate(deg, resample=Image.BICUBIC, expand=expand)
    return np.asarray(img, dtype=np.uint8)


def mirror(rgba: np.ndarray, horizontal: bool = True) -> np.ndarray:
    """Mirror the image — required for heat-transfer / sublimation output."""
    return np.ascontiguousarray(rgba[:, ::-1] if horizontal else rgba[::-1, :])


# --------------------------------------------------------------------------- #
# Units
# --------------------------------------------------------------------------- #
def mm_to_px(mm: float, dpi: int) -> int:
    return max(1, int(round(mm / MM_PER_INCH * dpi)))


def px_to_mm(px: float, dpi: int) -> float:
    return px * MM_PER_INCH / dpi


def mm_to_pt(mm: float) -> float:
    return mm / MM_PER_INCH * 72.0


# --------------------------------------------------------------------------- #
# Colour science
# --------------------------------------------------------------------------- #
def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    c = np.asarray(rgb, dtype=np.float32) / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB uint8 -> CIE L*a*b* float32 (D65). Accepts any leading shape."""
    lin = srgb_to_linear(rgb)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
    xyz = lin @ m.T
    white = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)
    xyz = xyz / white
    eps, kappa = 216.0 / 24389.0, 24389.0 / 27.0
    f = np.where(xyz > eps, np.cbrt(np.maximum(xyz, 1e-9)), (kappa * xyz + 16.0) / 116.0)
    lab = np.empty_like(f)
    lab[..., 0] = 116.0 * f[..., 1] - 16.0
    lab[..., 1] = 500.0 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200.0 * (f[..., 1] - f[..., 2])
    return lab.astype(np.float32)


def delta_e(lab_a: np.ndarray, lab_b: np.ndarray) -> np.ndarray:
    """CIE76 colour difference — fast and good enough for segmentation."""
    return np.sqrt(np.sum((lab_a - lab_b) ** 2, axis=-1))


def luminance(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb, dtype=np.float32)
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    v = value.strip().lstrip("#")
    if len(v) == 3:
        v = "".join(ch * 2 for ch in v)
    if len(v) != 6:
        raise ValueError(f"couleur invalide: {value!r}")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def rgb_to_hex(rgb) -> str:
    r, g, b = (int(round(float(c))) for c in rgb[:3])
    return "#{:02x}{:02x}{:02x}".format(max(0, min(255, r)),
                                        max(0, min(255, g)),
                                        max(0, min(255, b)))


def human_size(num: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if abs(num) < 1024.0 or unit == "Go":
            return f"{num:.0f} {unit}" if unit == "o" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} Go"


def estimate_dpi(pixels: int, mm: float) -> float:
    """Resulting print resolution if *pixels* are printed over *mm*."""
    if mm <= 0:
        return 0.0
    return pixels / (mm / MM_PER_INCH)


def gaussian_blur(arr: np.ndarray, sigma: float) -> np.ndarray:
    """Separable gaussian blur on float arrays (channel aware)."""
    if sigma <= 0:
        return arr.astype(np.float32)
    from scipy.ndimage import gaussian_filter
    a = arr.astype(np.float32)
    if a.ndim == 2:
        return gaussian_filter(a, sigma, mode="nearest")
    return gaussian_filter(a, (sigma, sigma, 0), mode="nearest")


def is_photographic(rgba: np.ndarray, sample: int = 200_000) -> bool:
    """Heuristic: photos have many distinct colours, logos and line art do not."""
    rgb = rgba[:, :, :3].reshape(-1, 3)
    if rgb.shape[0] > sample:
        idx = np.linspace(0, rgb.shape[0] - 1, sample).astype(np.int64)
        rgb = rgb[idx]
    quant = (rgb >> 3).astype(np.uint32)          # 32 levels per channel
    keys = quant[:, 0] * 1024 + quant[:, 1] * 32 + quant[:, 2]
    unique = np.unique(keys).size
    return unique > max(600, rgb.shape[0] * 0.004)


def aspect_fit(w: int, h: int, max_w: int, max_h: int) -> tuple[int, int]:
    scale = min(max_w / max(w, 1), max_h / max(h, 1), 1.0)
    return max(1, int(math.floor(w * scale))), max(1, int(math.floor(h * scale)))
