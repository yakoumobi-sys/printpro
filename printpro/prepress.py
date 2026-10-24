"""Press-ready PDF from any image: the one-click path for paper products.

``make_press_ready(image, product)`` does what a prepress operator would:

1. orients the product like the artwork and fits the artwork to the trim;
2. upscales to the product's resolution (300 dpi for small formats);
3. adds the bleed (mirrored edges by default, so nothing is cropped twice);
4. flattens transparency and separates to CMYK with a TAC limit;
5. writes a PDF/X-1a:2001 with TrimBox, BleedBox and registration crop marks;
6. reads the PDF back and runs the nine preflight checks.

``inspect_artwork(image, product)`` is the free check shown before any work:
what a print shop would say about the file as it is.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .cmyk import CmykOptions, cmyk_preview, rgb_to_cmyk
from .imaging import MM_PER_INCH, flatten, hex_to_rgb, mm_to_px, resize
from .pdfwriter import PT_PER_MM, Line, Page, PdfWriter, PdfX, PlacedImage
from .preflight import PreflightReport, preflight_pdf
from .products import Product
from .upscale import UpscaleOptions, upscale


@dataclass
class PressOptions:
    fit: str = "cover"            # cover: fill the trim | contain: whole image
    bleed_mode: str = "mirror"    # mirror | edge | scale (zoom into the bleed)
    orientation: str = "auto"     # auto | portrait | landscape
    crop_marks: bool = True
    mark_offset_mm: float = 3.0   # gap between trim corner and mark
    mark_length_mm: float = 5.0
    mark_weight_pt: float = 0.25
    background: str = "#ffffff"   # behind transparent / contained artwork
    upscale_method: str = "auto"
    cmyk: CmykOptions = field(default_factory=CmykOptions)
    title: str = "PrintPro"


@dataclass
class PressResult:
    product: Product
    width_mm: float               # trim, as oriented
    height_mm: float
    pdf: bytes
    artwork: np.ndarray           # RGBA, trim + bleed, before separation
    cmyk: np.ndarray
    preflight: PreflightReport
    steps: list[str] = field(default_factory=list)
    specs: dict = field(default_factory=dict)

    def proof(self) -> np.ndarray:
        """On-screen proof of the separated file (RGBA)."""
        return cmyk_preview(self.cmyk)


def orient(product: Product, image: np.ndarray, mode: str = "auto") -> tuple[float, float]:
    w, h = product.width_mm, product.height_mm
    if mode == "portrait":
        return min(w, h), max(w, h)
    if mode == "landscape":
        return max(w, h), min(w, h)
    ih, iw = image.shape[:2]
    if (iw > ih) != (w > h) and iw != ih and w != h:
        return h, w
    return w, h


# --------------------------------------------------------------------------- #
def inspect_artwork(image: np.ndarray, product: Product,
                    orientation: str = "auto") -> dict:
    """What a print shop would flag on this file, before any preparation."""
    image = np.asarray(image, dtype=np.uint8)
    ih, iw = image.shape[:2]
    w, h = orient(product, image, orientation)
    b = product.bleed_mm
    checks = []

    def add(key, label, status, detail):
        checks.append({"key": key, "label": label, "status": status, "detail": detail})

    # Resolution if the artwork covers the trim (+ bleed).
    native = min(iw / ((w + 2 * b) / MM_PER_INCH), ih / ((h + 2 * b) / MM_PER_INCH)) \
        if product.family == "papier" else \
        min(iw / (w / MM_PER_INCH), ih / (h / MM_PER_INCH))
    factor = product.dpi / max(native, 1e-6)
    if native >= product.dpi * 0.99:
        add("dpi", "Résolution", "ok", f"{native:.0f} dpi à {w:g} × {h:g} mm")
    else:
        add("dpi", "Résolution", "warn",
            f"{native:.0f} dpi à {w:g} × {h:g} mm — sera agrandie ×{factor:.1f} "
            f"jusqu'à {product.dpi} dpi")

    alpha = image[:, :, 3]
    has_alpha = bool((alpha < 250).any())
    partial = float(((alpha > 8) & (alpha < 247)).mean())

    if product.family == "papier":
        add("color", "Couleurs", "warn",
            "RVB (écran) — sera converti en CMJN avec encrage limité à "
            f"{CmykOptions().tac:g} %")
        ratio_img, ratio_trim = iw / ih, w / h
        crop = 1.0 - min(ratio_img, ratio_trim) / max(ratio_img, ratio_trim)
        if crop < 0.01:
            add("ratio", "Proportions", "ok", "identiques au format fini")
        else:
            add("ratio", "Proportions", "warn",
                f"{crop * 100:.0f} % de l'image dépasse du format — recadrage centré "
                "(ou « image entière » pour ne rien couper)")
        add("bleed", "Fond perdu", "warn" if b else "ok",
            f"absent — {b:g} mm seront ajoutés sur chaque bord" if b else "non requis")
        add("marks", "Traits de coupe / TrimBox", "warn",
            "absents — seront ajoutés au PDF/X-1a")
        if has_alpha:
            add("alpha", "Transparence", "warn", "sera aplatie sur fond blanc")
    else:
        if has_alpha:
            add("alpha", "Fond transparent", "ok", "le fond est déjà retiré")
        else:
            add("alpha", "Fond transparent", "warn",
                "fond plein — il sera détouré, sinon il serait imprimé sur le textile")
        if partial > 0.002:
            add("edges", "Bords nets", "warn",
                f"{partial * 100:.1f} % de pixels semi-transparents — "
                "ils seront rendus nets (ou tramés)")
        else:
            add("edges", "Bords nets", "ok", "aucun pixel semi-transparent")
        add("white", "Sous-couche blanche", "warn",
            "absente — une couche blanche rétrécie sera générée")

    return {
        "product": product.as_dict(),
        "size_mm": [w, h],
        "pixels": [int(iw), int(ih)],
        "native_dpi": round(native, 1),
        "upscale_factor": round(max(factor, 1.0), 2),
        "checks": checks,
        "issues": sum(c["status"] != "ok" for c in checks),
    }


# --------------------------------------------------------------------------- #
def make_press_ready(image: np.ndarray, product: Product,
                     options: PressOptions | None = None) -> PressResult:
    opt = options or PressOptions()
    image = np.asarray(image, dtype=np.uint8)
    steps: list[str] = []
    started = time.perf_counter()

    w, h = orient(product, image, opt.orientation)
    dpi, b = product.dpi, product.bleed_mm
    tw, th = mm_to_px(w, dpi), mm_to_px(h, dpi)
    bp = int(round(b / MM_PER_INCH * dpi)) if b > 0 else 0
    fw, fh = tw + 2 * bp, th + 2 * bp
    background = hex_to_rgb(opt.background)
    ih, iw = image.shape[:2]

    # -- fit + upscale -------------------------------------------------------
    if opt.fit == "contain":
        scale = min(tw / iw, th / ih)
        art, native_scale = _scaled(image, scale, opt, steps)
        canvas = np.zeros((fh, fw, 4), np.uint8)
        canvas[:, :, :3] = background
        canvas[:, :, 3] = 255
        y0 = bp + (th - art.shape[0]) // 2
        x0 = bp + (tw - art.shape[1]) // 2
        flat = flatten(art, background)
        canvas[y0:y0 + art.shape[0], x0:x0 + art.shape[1]] = flat
        steps.append(f"image entière centrée, fond {opt.background}")
    elif opt.bleed_mode == "scale" or bp == 0:
        scale = max(fw / iw, fh / ih)
        art, native_scale = _scaled(image, scale, opt, steps)
        canvas = flatten(_center_crop(art, fw, fh), background)
        if bp:
            steps.append(f"fond perdu {b:g} mm pris dans l'image (léger zoom)")
    else:
        scale = max(tw / iw, th / ih)
        art, native_scale = _scaled(image, scale, opt, steps)
        trim_art = flatten(_center_crop(art, tw, th), background)
        mode = "symmetric" if opt.bleed_mode == "mirror" else "edge"
        canvas = np.pad(trim_art, ((bp, bp), (bp, bp), (0, 0)), mode=mode)
        steps.append(f"fond perdu {b:g} mm ajouté par "
                     f"{'miroir des bords' if mode == 'symmetric' else 'extension des bords'}")

    # -- separation ----------------------------------------------------------
    sep = rgb_to_cmyk(canvas, opt.cmyk)
    steps.append(f"séparation CMJN ({'profil ICC' if sep.engine == 'icc' else 'GCR intégré'}), "
                 f"encrage max {sep.max_tac:.0f} %")

    # -- PDF/X-1a ------------------------------------------------------------
    slug = b
    if opt.crop_marks:
        slug = max(b, opt.mark_offset_mm) + opt.mark_length_mm + 2.0
    page = Page(width_mm=w + 2 * slug, height_mm=h + 2 * slug,
                trim_box=(slug, slug, w, h),
                bleed_box=(slug - b, slug - b, w + 2 * b, h + 2 * b))
    page.images.append(PlacedImage(sep.cmyk, slug - b, slug - b,
                                   w + 2 * b, h + 2 * b, colorspace="cmyk"))
    if opt.crop_marks:
        page.lines.extend(crop_marks(slug, slug, w, h,
                                     max(opt.mark_offset_mm, b),
                                     opt.mark_length_mm,
                                     opt.mark_weight_pt / PT_PER_MM))
        steps.append(f"traits de coupe {opt.mark_weight_pt:g} pt, "
                     f"décalage {max(opt.mark_offset_mm, b):g} mm")
    pdfx = PdfX(profile=sep.profile)
    if sep.engine == "icc":
        pdfx.condition, pdfx.info, pdfx.registry = "Custom", sep.profile_name, ""
    writer = PdfWriter(title=opt.title, pdfx=pdfx)
    writer.add_page(page)
    pdf = writer.getvalue()

    report = preflight_pdf(pdf, product, tac_limit=opt.cmyk.tac)
    steps.append(f"contrôle prépresse : {sum(c.status == 'ok' for c in report.checks)}"
                 f"/{len(report.checks)} OK")

    native_dpi = dpi / native_scale if native_scale > 0 else dpi
    specs = {
        "format": "PDF/X-1a:2001",
        "standard": "ISO 15930-1",
        "dpi": dpi,
        "pixels": [int(fw), int(fh)],
        "trim_mm": [w, h],
        "bleed_mm": b,
        "media_mm": [round(page.width_mm, 2), round(page.height_mm, 2)],
        "color": "CMJN " + ("(profil ICC incorporé)" if sep.profile else
                            "— intention de sortie Coated FOGRA39"),
        "profile": sep.profile_name,
        "engine": sep.engine,
        "tac_max": round(sep.max_tac, 1),
        "tac_limit": opt.cmyk.tac,
        "crop_marks": (f"couleur de repérage, {opt.mark_weight_pt:g} pt, "
                       f"décalage {max(opt.mark_offset_mm, b):g} mm")
        if opt.crop_marks else "aucun",
        "native_dpi": round(native_dpi, 1),
        "upscale_factor": round(native_scale, 2),
        "bytes": len(pdf),
        "seconds": round(time.perf_counter() - started, 2),
    }
    return PressResult(product, w, h, pdf, canvas, sep.cmyk, report, steps, specs)


def crop_marks(x: float, y: float, w: float, h: float, offset: float,
               length: float, width_mm: float) -> list[Line]:
    """Eight corner marks around the trim, outside the bleed."""
    lines = []
    for cx, sx in ((x, -1), (x + w, 1)):
        for cy, sy in ((y, -1), (y + h, 1)):
            # horizontal mark, level with the trim edge
            lines.append(Line(cx + sx * offset, cy, cx + sx * (offset + length), cy,
                              width_mm=width_mm, registration=True))
            # vertical mark
            lines.append(Line(cx, cy + sy * offset, cx, cy + sy * (offset + length),
                              width_mm=width_mm, registration=True))
    return lines


# --------------------------------------------------------------------------- #
def _scaled(image: np.ndarray, scale: float, opt: PressOptions,
            steps: list[str]) -> tuple[np.ndarray, float]:
    ih, iw = image.shape[:2]
    tw, th = max(1, int(round(iw * scale))), max(1, int(round(ih * scale)))
    if scale > 1.02:
        result = upscale(image, UpscaleOptions(target_width=tw, target_height=th,
                                               method=opt.upscale_method))
        steps.append(f"agrandissement ×{scale:.2f} ({result.method}) : "
                     f"{iw}×{ih} → {tw}×{th} px")
        return result.image, scale
    if abs(scale - 1.0) > 1e-3:
        steps.append(f"mise à l'échelle ×{scale:.2f} : {iw}×{ih} → {tw}×{th} px")
        return resize(image, tw, th), scale
    return image, 1.0


def _center_crop(image: np.ndarray, w: int, h: int) -> np.ndarray:
    ih, iw = image.shape[:2]
    if iw < w or ih < h:          # rounding: pad by edge replication
        image = np.pad(image, ((0, max(0, h - ih)), (0, max(0, w - iw)), (0, 0)),
                       mode="edge")
        ih, iw = image.shape[:2]
    x0, y0 = (iw - w) // 2, (ih - h) // 2
    return image[y0:y0 + h, x0:x0 + w]
