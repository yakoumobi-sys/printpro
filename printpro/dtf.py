"""DTF (direct-to-film) transfers: the film a DTF printer can print as is.

What goes wrong on a DTF film, and what this module does about it:

* **no white plate** — DTF inks are transparent; a white underbase is printed
  behind the colour.  It is generated from the artwork and *choked* (pulled
  in by a fraction of a millimetre) so no white halo shows around the colour.
* **garment colour painted in** — a black background on a design meant for a
  black t-shirt must not be printed: pixels close to the garment colour are
  knocked out so the fabric shows through.
* **soft, half-transparent edges** — powder sticks to any ink, even 5 %; the
  result is a dirty fringe.  Alpha becomes strictly 0 or 255, either by a
  threshold or by an AM halftone that turns soft shadows into printable dots.
* **ink on the cut edge** — the design is fitted inside the transfer with a
  clear margin, and specks too small to survive the press are removed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from .bgremove import BgOptions, remove_background
from .imaging import (MM_PER_INCH, delta_e, hex_to_rgb, mm_to_px, mirror,
                      resize, rgb_to_lab, trim)
from .pdfwriter import Page, PdfWriter, PlacedImage
from .products import Product
from .upscale import UpscaleOptions, upscale


@dataclass
class DtfOptions:
    remove_background: str = "auto"   # auto (if opaque) | yes | no
    garment: str | None = None        # "#000000": knock this colour out
    garment_tolerance: float = 14.0   # Delta-E
    edges: str = "threshold"          # threshold | halftone
    alpha_threshold: int = 128
    halftone_lpi: float = 35.0
    halftone_angle: float = 22.5
    choke_mm: float = 0.25            # white underbase pulled in by this much
    margin_mm: float = 2.0            # clear film around the design
    min_speck_mm: float = 0.4         # smaller islands do not transfer
    mirror: bool = False              # some RIPs expect a mirrored film
    upscale_method: str = "auto"


@dataclass
class DtfResult:
    product: Product
    artwork: np.ndarray               # RGBA, binary alpha, product canvas
    white: np.ndarray                 # (H, W) uint8, 255 = white ink
    pdf: bytes
    checks: list[dict] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    specs: dict = field(default_factory=dict)

    def preview(self, garment: str | tuple = "#2b2b2b") -> np.ndarray:
        """The transfer as it will look on the garment (RGBA)."""
        rgb = hex_to_rgb(garment) if isinstance(garment, str) else garment
        out = np.empty_like(self.artwork)
        out[:, :, :3] = rgb
        out[:, :, 3] = 255
        on = self.artwork[:, :, 3] > 0
        out[on, :3] = self.artwork[on, :3]
        return out

    def white_plate_rgba(self) -> np.ndarray:
        """The underbase as a printable layer (white where ink, else clear)."""
        out = np.zeros(self.white.shape + (4,), np.uint8)
        out[:, :, :3] = 255
        out[:, :, 3] = self.white
        return out


# --------------------------------------------------------------------------- #
def make_dtf(image: np.ndarray, product: Product,
             options: DtfOptions | None = None) -> DtfResult:
    opt = options or DtfOptions()
    image = np.asarray(image, dtype=np.uint8)
    dpi = product.dpi
    steps: list[str] = []

    # 1 — background ---------------------------------------------------------
    opaque = not bool((image[:, :, 3] < 250).any())
    if opt.remove_background == "yes" or (opt.remove_background == "auto" and opaque):
        cut = remove_background(image, BgOptions(post_trim=False))
        image = cut.image
        steps.append(f"fond retiré (moteur {cut.engine})")

    # 2 — garment colour knock-out --------------------------------------------
    knocked = 0.0
    if opt.garment:
        garment = np.array(hex_to_rgb(opt.garment), np.uint8)
        dist = delta_e(rgb_to_lab(image[:, :, :3]), rgb_to_lab(garment))
        soft = 6.0
        keep = np.clip((dist - opt.garment_tolerance) / soft, 0.0, 1.0)
        before = image[:, :, 3] > 0
        image = image.copy()
        image[:, :, 3] = (image[:, :, 3] * keep).astype(np.uint8)
        knocked = float((before & (image[:, :, 3] == 0)).sum()) / max(before.sum(), 1)
        steps.append(f"couleur du textile {opt.garment} retirée "
                     f"({knocked * 100:.1f} % de la surface)")

    image = trim(image, padding=0, threshold=8)

    # 3 — fit inside the transfer, at resolution ------------------------------
    w_mm, h_mm = product.width_mm, product.height_mm
    ih, iw = image.shape[:2]
    if (iw > ih) != (w_mm > h_mm) and w_mm != h_mm:
        w_mm, h_mm = h_mm, w_mm
    cw, ch = mm_to_px(w_mm, dpi), mm_to_px(h_mm, dpi)
    margin = mm_to_px(opt.margin_mm, dpi) if opt.margin_mm > 0 else 0
    scale = min((cw - 2 * margin) / iw, (ch - 2 * margin) / ih)
    tw, th = max(1, int(iw * scale)), max(1, int(ih * scale))
    native_dpi = dpi / scale
    if scale > 1.02:
        image = upscale(image, UpscaleOptions(target_width=tw, target_height=th,
                                              method=opt.upscale_method)).image
        steps.append(f"agrandissement ×{scale:.2f} → {tw}×{th} px")
    else:
        image = resize(image, tw, th)
    canvas = np.zeros((ch, cw, 4), np.uint8)
    y0, x0 = (ch - th) // 2, (cw - tw) // 2
    canvas[y0:y0 + th, x0:x0 + tw] = image

    # 4 — hard edges ----------------------------------------------------------
    alpha = canvas[:, :, 3]
    if opt.edges == "halftone":
        solid = halftone(alpha, dpi, opt.halftone_lpi, opt.halftone_angle)
        steps.append(f"bords et ombres tramés ({opt.halftone_lpi:g} lpi, "
                     f"{opt.halftone_angle:g}°)")
    else:
        solid = alpha >= opt.alpha_threshold
        steps.append(f"bords rendus nets (seuil {opt.alpha_threshold})")

    specks = 0
    if opt.min_speck_mm > 0:
        min_px = max(1, int(round((opt.min_speck_mm / MM_PER_INCH * dpi) ** 2)))
        labels, count = ndimage.label(solid)
        if count:
            sizes = ndimage.sum(solid, labels, index=np.arange(1, count + 1))
            small = np.zeros(count + 1, bool)
            small[1:] = sizes < min_px
            if opt.edges == "halftone":   # dots are meant to be small
                small[1:] &= sizes < 2
            specks = int(small.sum())
            solid &= ~small[labels]
    if specks:
        steps.append(f"{specks} détail(s) trop petit(s) supprimé(s)")

    artwork = canvas.copy()
    artwork[:, :, 3] = np.where(solid, 255, 0).astype(np.uint8)
    artwork[~solid, :3] = 0

    # 5 — white underbase -----------------------------------------------------
    choke = int(round(opt.choke_mm / MM_PER_INCH * dpi))
    white = solid
    if choke > 0:
        white = ndimage.binary_erosion(solid, iterations=choke)
    white_u8 = np.where(white, 255, 0).astype(np.uint8)
    steps.append(f"sous-couche blanche générée, rétrécie de {opt.choke_mm:g} mm "
                 f"({choke} px)")

    if opt.mirror:
        artwork, white_u8 = mirror(artwork), mirror(white_u8)
        steps.append("film en miroir")

    # 6 — PDF: page 1 colour, page 2 white plate ------------------------------
    writer = PdfWriter(title=f"PrintPro DTF — {product.label}")
    color_page = Page(w_mm, h_mm)
    color_page.images.append(PlacedImage(artwork, 0, 0, w_mm, h_mm))
    writer.add_page(color_page)
    plate = np.zeros(white_u8.shape + (4,), np.uint8)
    plate[:, :, 3] = white_u8           # shown in black on the proof page
    white_page = Page(w_mm, h_mm)
    white_page.images.append(PlacedImage(plate, 0, 0, w_mm, h_mm))
    writer.add_page(white_page)
    pdf = writer.getvalue()

    result = DtfResult(product, artwork, white_u8, pdf, steps=steps)
    result.checks = dtf_checks(result, opt, native_dpi, knocked)
    result.specs = {
        "format": "PNG transparent + sous-couche blanche + PDF 2 pages",
        "dpi": dpi,
        "pixels": [int(cw), int(ch)],
        "size_mm": [w_mm, h_mm],
        "design_mm": [round(tw / dpi * MM_PER_INCH, 1), round(th / dpi * MM_PER_INCH, 1)],
        "margin_mm": opt.margin_mm,
        "choke_mm": opt.choke_mm,
        "edges": opt.edges,
        "native_dpi": round(native_dpi, 1),
        "white_coverage": round(float(white.mean()) * 100, 1),
    }
    return result


def dtf_checks(result: DtfResult, opt: DtfOptions, native_dpi: float,
               knocked: float) -> list[dict]:
    art, white = result.artwork, result.white > 0
    alpha = art[:, :, 3]
    color = alpha > 0
    checks = []

    def add(key, label, ok, detail):
        checks.append({"key": key, "label": label,
                       "status": "ok" if ok else "fail", "detail": detail})

    partial = int(((alpha > 0) & (alpha < 255)).sum())
    add("edges", "Bords nets (0 ou 100 %)", partial == 0,
        "aucun pixel semi-transparent" if partial == 0 else f"{partial} pixels flous")
    outside = int((white & ~color).sum())
    add("white", "Sous-couche sous la couleur", outside == 0 and white.any(),
        f"{white.mean() * 100:.1f} % du film, jamais au-delà de la couleur"
        if outside == 0 else f"{outside} px de blanc dépassent")
    if opt.choke_mm > 0:
        halo = int((color & ~white).sum())
        add("choke", "Blanc rétréci (pas de halo)", halo > 0 or not color.any(),
            f"retrait de {opt.choke_mm:g} mm sur le pourtour")
    if opt.garment:
        add("garment", "Couleur du textile retirée", True,
            f"{opt.garment} : {knocked * 100:.1f} % de la surface laissée au textile")
    margin_px = mm_to_px(opt.margin_mm, result.product.dpi) - 1 if opt.margin_mm else 0
    border = np.zeros_like(color)
    if margin_px > 0:
        border[:margin_px], border[-margin_px:] = True, True
        border[:, :margin_px], border[:, -margin_px:] = True, True
    add("margin", "Bord du film dégagé", not (color & border).any(),
        f"{opt.margin_mm:g} mm sans encre autour du motif")
    corners = alpha[[0, 0, -1, -1], [0, -1, 0, -1]]
    add("background", "Fond transparent", bool((corners == 0).all()),
        "rien n'est imprimé hors du motif")
    add("dpi", "Résolution", True,
        f"{result.product.dpi} dpi à la taille (source {native_dpi:.0f} dpi)")
    return checks


# --------------------------------------------------------------------------- #
def halftone(alpha: np.ndarray, dpi: int, lpi: float = 35.0,
             angle: float = 22.5) -> np.ndarray:
    """AM halftone of an alpha channel → boolean ink mask.

    Round dots on a rotated screen; the threshold is the exact area of a dot
    in its cell, so 30 % alpha gives 30 % ink coverage.  Solid areas stay
    solid, soft shadows become dots the powder can hold on to.
    """
    h, w = alpha.shape
    cell = max(2.0, dpi / max(lpi, 1.0))
    cos_t, sin_t = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    out = np.zeros((h, w), bool)
    xx = np.arange(w, dtype=np.float32)[None, :]
    rows = max(1, 1_000_000 // max(w, 1))      # bounded memory on big films
    for y0 in range(0, h, rows):
        yy = np.arange(y0, min(h, y0 + rows), dtype=np.float32)[:, None]
        u = (xx * cos_t + yy * sin_t) / cell
        v = (-xx * sin_t + yy * cos_t) / cell
        fu, fv = u - np.floor(u) - 0.5, v - np.floor(v) - 0.5
        d = fu * fu + fv * fv                 # squared radius, 0..0.5
        r = np.sqrt(d)
        outer = math.pi * d - 4.0 * (
            d * np.arccos(np.clip(0.5 / np.maximum(r, 1e-6), -1.0, 1.0))
            - 0.5 * np.sqrt(np.maximum(d - 0.25, 0.0)))
        coverage = np.where(d <= 0.25, math.pi * d, outer)
        block = alpha[y0:y0 + rows]
        out[y0:y0 + rows] = (block.astype(np.float32) / 255.0 >
                             np.clip(coverage, 0.0, 0.9999)) & (block > 0)
    return out
