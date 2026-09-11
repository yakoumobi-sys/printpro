"""Sheet montage (imposition / nesting).

Arranges any number of artworks on print sheets — A4/A3 sheets, DTF or vinyl
rolls, custom sizes — with margins, gutters, crop marks, kiss-cut contours and
mirroring for heat transfer.  Three layouts:

``grid``  regular rows and columns, the classic sticker sheet,
``fill``  fill the sheet with as many copies as physically fit,
``pack``  MaxRects bin packing (best short side fit, with rotation) for mixed
          sizes — this is what keeps roll waste low.

Everything is computed in millimetres; pixels only appear when rendering.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import ImageDraw
from scipy import ndimage

from .imaging import (hex_to_rgb, mm_to_px, px_to_mm, resize, rotate, mirror,
                      trim, to_pil, save_image)
from .pdfwriter import Line, Page, PdfWriter, PlacedImage

# Width x height in mm, portrait.
PAGE_PRESETS: dict[str, tuple[float, float]] = {
    "A6": (105.0, 148.0),
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "A2": (420.0, 594.0),
    "A1": (594.0, 841.0),
    "A0": (841.0, 1189.0),
    "Letter": (215.9, 279.4),
    "Legal": (215.9, 355.6),
    "Tabloid": (279.4, 431.8),
    "DTF-30": (300.0, 1000.0),       # 30 cm film roll, 1 m section
    "DTF-60": (600.0, 1000.0),       # 60 cm film roll
    "Vinyl-50": (500.0, 1000.0),
    "Mug": (200.0, 90.0),            # standard mug wrap
    "T-shirt-A3": (297.0, 420.0),
}


@dataclass
class MontageItem:
    image: np.ndarray
    width_mm: float = 0.0       # 0 -> derived from height, or from source dpi
    height_mm: float = 0.0
    quantity: int = 1
    rotatable: bool = True
    label: str = ""
    source_dpi: int = 300

    def size_mm(self) -> tuple[float, float]:
        h, w = self.image.shape[:2]
        ratio = h / max(w, 1)
        if self.width_mm and self.height_mm:
            return float(self.width_mm), float(self.height_mm)
        if self.width_mm:
            return float(self.width_mm), float(self.width_mm) * ratio
        if self.height_mm:
            return float(self.height_mm) / max(ratio, 1e-6), float(self.height_mm)
        dpi = max(1, int(self.source_dpi))
        return px_to_mm(w, dpi), px_to_mm(h, dpi)


@dataclass
class MontageOptions:
    page: str = "A4"
    page_width_mm: float = 0.0          # overrides the preset when set
    page_height_mm: float = 0.0
    orientation: str = "auto"           # auto | portrait | landscape
    dpi: int = 300
    margin_mm: float = 6.0
    spacing_mm: float = 3.0
    layout: str = "pack"                # pack | grid | fill
    columns: int = 0                    # grid only, 0 = automatic
    mirror: bool = False                # heat transfer / sublimation
    background: str | None = "#ffffff"  # None keeps the sheet transparent
    crop_marks: bool = False
    registration_marks: bool = False
    cut_contour_mm: float = 0.0         # kiss-cut border around each sticker
    cut_contour_color: str = "#ffffff"
    cut_stroke: bool = False            # draw the cut line itself
    outline: bool = False               # thin frame around each slot
    auto_trim: bool = True              # crop transparent borders first
    max_pages: int = 50
    sort: str = "area"                  # area | none


@dataclass
class Placement:
    item: int
    page: int
    x_mm: float
    y_mm: float
    w_mm: float
    h_mm: float
    rotated: bool = False


@dataclass
class MontageResult:
    pages: list[list[Placement]]
    page_size_mm: tuple[float, float]
    options: MontageOptions
    items: list[MontageItem] = field(default_factory=list)
    placed: int = 0
    requested: int = 0
    efficiency: float = 0.0            # ink-covered share of the usable area
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def summary(self) -> dict:
        w, h = self.page_size_mm
        return {
            "pages": self.page_count,
            "placed": self.placed,
            "requested": self.requested,
            "page_size_mm": [round(w, 1), round(h, 1)],
            "efficiency": round(self.efficiency * 100, 1),
            "dpi": self.options.dpi,
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- #
def page_size(opt: MontageOptions) -> tuple[float, float]:
    if opt.page_width_mm and opt.page_height_mm:
        w, h = float(opt.page_width_mm), float(opt.page_height_mm)
    else:
        w, h = PAGE_PRESETS.get(opt.page, PAGE_PRESETS["A4"])
    if opt.orientation == "landscape" and h > w:
        w, h = h, w
    elif opt.orientation == "portrait" and w > h:
        w, h = h, w
    return w, h


def build_montage(items: list[MontageItem],
                  options: MontageOptions | None = None) -> MontageResult:
    opt = options or MontageOptions()
    pw, ph = page_size(opt)
    usable_w = pw - 2 * opt.margin_mm
    usable_h = ph - 2 * opt.margin_mm
    warnings: list[str] = []

    if usable_w <= 0 or usable_h <= 0:
        raise ValueError("marges trop grandes pour ce format de page")

    prepared: list[MontageItem] = []
    for item in items:
        img = item.image
        if opt.auto_trim:
            img = trim(img, padding=1)
        if opt.cut_contour_mm > 0:
            w_mm, _ = item.size_mm()
            px_per_mm = img.shape[1] / max(w_mm, 1e-6)
            img = add_cut_contour(img, opt.cut_contour_mm * px_per_mm,
                                  hex_to_rgb(opt.cut_contour_color),
                                  stroke=opt.cut_stroke)
        prepared.append(MontageItem(image=img, width_mm=item.width_mm,
                                    height_mm=item.height_mm,
                                    quantity=max(1, int(item.quantity)),
                                    rotatable=item.rotatable, label=item.label,
                                    source_dpi=item.source_dpi))

    # Growing each item by the cut contour keeps the requested artwork size.
    if opt.cut_contour_mm > 0:
        for original, item in zip(items, prepared):
            w_mm, h_mm = original.size_mm()
            item.width_mm = w_mm + 2 * opt.cut_contour_mm
            item.height_mm = h_mm + 2 * opt.cut_contour_mm

    requests: list[tuple[int, float, float]] = []
    for index, item in enumerate(prepared):
        w_mm, h_mm = item.size_mm()
        if w_mm > usable_w + 1e-6 or h_mm > usable_h + 1e-6:
            if item.rotatable and h_mm <= usable_w + 1e-6 and w_mm <= usable_h + 1e-6:
                pass                     # rotation will save it
            else:
                warnings.append(
                    f"« {item.label or f'élément {index + 1}'} » "
                    f"({w_mm:.0f}×{h_mm:.0f} mm) dépasse la zone utile "
                    f"({usable_w:.0f}×{usable_h:.0f} mm)")
                continue
        count = item.quantity if opt.layout != "fill" else _fill_count(
            w_mm, h_mm, usable_w, usable_h, opt)
        requests.extend([(index, w_mm, h_mm)] * count)

    if opt.sort == "area":
        requests.sort(key=lambda r: -(r[1] * r[2]))

    if opt.layout == "grid":
        pages = _layout_grid(requests, prepared, usable_w, usable_h, opt)
    else:
        pages = _layout_pack(requests, prepared, usable_w, usable_h, opt)

    if len(pages) > opt.max_pages:
        warnings.append(f"sortie limitée à {opt.max_pages} pages")
        pages = pages[:opt.max_pages]

    for page in pages:                       # margins are applied at the end
        for placement in page:
            placement.x_mm += opt.margin_mm
            placement.y_mm += opt.margin_mm

    placed = sum(len(p) for p in pages)
    area_used = sum(p.w_mm * p.h_mm for page in pages for p in page)
    total_area = max(1e-6, usable_w * usable_h * max(1, len(pages)))
    return MontageResult(pages=pages, page_size_mm=(pw, ph), options=opt,
                         items=prepared, placed=placed, requested=len(requests),
                         efficiency=min(1.0, area_used / total_area),
                         warnings=warnings)


def _fill_count(w_mm: float, h_mm: float, usable_w: float, usable_h: float,
                opt: MontageOptions) -> int:
    """How many copies fit on one sheet (both orientations considered)."""
    gap = opt.spacing_mm
    best = 0
    for a, b in ((w_mm, h_mm), (h_mm, w_mm)):
        cols = int((usable_w + gap) // (a + gap))
        rows = int((usable_h + gap) // (b + gap))
        best = max(best, cols * rows)
    return max(1, best)


def _layout_grid(requests, items, usable_w, usable_h, opt) -> list[list[Placement]]:
    if not requests:
        return []
    w_mm = max(r[1] for r in requests)
    h_mm = max(r[2] for r in requests)
    gap = opt.spacing_mm
    cols = opt.columns or max(1, int((usable_w + gap) // (w_mm + gap)))
    rows = max(1, int((usable_h + gap) // (h_mm + gap)))
    per_page = max(1, cols * rows)

    pages: list[list[Placement]] = []
    for position, (index, iw, ih) in enumerate(requests):
        page_no, slot = divmod(position, per_page)
        if page_no >= opt.max_pages:
            break
        while len(pages) <= page_no:
            pages.append([])
        row, col = divmod(slot, cols)
        x = col * (w_mm + gap) + (w_mm - iw) / 2.0        # centred in its cell
        y = row * (h_mm + gap) + (h_mm - ih) / 2.0
        pages[page_no].append(Placement(index, page_no, x, y, iw, ih))
    return pages


def _layout_pack(requests, items, usable_w, usable_h, opt) -> list[list[Placement]]:
    gap = opt.spacing_mm
    pages: list[list[Placement]] = []
    bins: list[_MaxRects] = []
    for index, w_mm, h_mm in requests:
        placed = False
        for page_no, packer in enumerate(bins):
            spot = packer.insert(w_mm + gap, h_mm + gap,
                                 items[index].rotatable)
            if spot:
                x, y, rotated = spot
                pages[page_no].append(
                    Placement(index, page_no, x, y,
                              h_mm if rotated else w_mm,
                              w_mm if rotated else h_mm, rotated))
                placed = True
                break
        if placed:
            continue
        if len(bins) >= opt.max_pages:
            break
        packer = _MaxRects(usable_w + gap, usable_h + gap)
        spot = packer.insert(w_mm + gap, h_mm + gap, items[index].rotatable)
        bins.append(packer)
        pages.append([])
        if spot:
            x, y, rotated = spot
            pages[-1].append(Placement(index, len(bins) - 1, x, y,
                                       h_mm if rotated else w_mm,
                                       w_mm if rotated else h_mm, rotated))
    return pages


class _MaxRects:
    """MaxRects bin packer, best short side fit heuristic."""

    def __init__(self, width: float, height: float):
        self.free: list[list[float]] = [[0.0, 0.0, width, height]]

    def insert(self, w: float, h: float, allow_rotate: bool):
        best = None
        best_score = (math.inf, math.inf)
        for rect in self.free:
            for rotated in ((False, True) if allow_rotate else (False,)):
                rw, rh = (h, w) if rotated else (w, h)
                if rw <= rect[2] + 1e-9 and rh <= rect[3] + 1e-9:
                    leftover_x = rect[2] - rw
                    leftover_y = rect[3] - rh
                    score = (min(leftover_x, leftover_y),
                             max(leftover_x, leftover_y))
                    if score < best_score:
                        best_score = score
                        best = (rect[0], rect[1], rw, rh, rotated)
        if best is None:
            return None
        x, y, rw, rh, rotated = best
        self._split(x, y, rw, rh)
        self._prune()
        return x, y, rotated

    def _split(self, x: float, y: float, w: float, h: float) -> None:
        updated: list[list[float]] = []
        for fx, fy, fw, fh in self.free:
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                updated.append([fx, fy, fw, fh])
                continue
            if x > fx:
                updated.append([fx, fy, x - fx, fh])
            if x + w < fx + fw:
                updated.append([x + w, fy, fx + fw - (x + w), fh])
            if y > fy:
                updated.append([fx, fy, fw, y - fy])
            if y + h < fy + fh:
                updated.append([fx, y + h, fw, fy + fh - (y + h)])
        self.free = [r for r in updated if r[2] > 1e-6 and r[3] > 1e-6]

    def _prune(self) -> None:
        keep: list[list[float]] = []
        for i, a in enumerate(self.free):
            contained = any(
                i != j and a[0] >= b[0] - 1e-9 and a[1] >= b[1] - 1e-9 and
                a[0] + a[2] <= b[0] + b[2] + 1e-9 and
                a[1] + a[3] <= b[1] + b[3] + 1e-9
                for j, b in enumerate(self.free))
            if not contained:
                keep.append(a)
        self.free = keep


# --------------------------------------------------------------------------- #
# Cut contour
# --------------------------------------------------------------------------- #
def add_cut_contour(rgba: np.ndarray, offset_px: float,
                    color=(255, 255, 255), stroke: bool = False) -> np.ndarray:
    """Grow an opaque border around the artwork (kiss-cut sticker outline)."""
    offset = max(1, int(round(offset_px)))
    pad = offset + (2 if stroke else 0) + 2
    padded = np.zeros((rgba.shape[0] + 2 * pad, rgba.shape[1] + 2 * pad, 4),
                      dtype=np.uint8)
    padded[pad:-pad, pad:-pad] = rgba

    alpha = padded[:, :, 3] > 40
    grown = ndimage.binary_dilation(
        alpha, ndimage.generate_binary_structure(2, 1), iterations=offset)
    border = grown & ~alpha
    padded[border, :3] = np.array(color[:3], dtype=np.uint8)
    padded[border, 3] = 255

    if stroke:
        edge = ndimage.binary_dilation(grown, iterations=2) & ~grown
        padded[edge, :3] = (60, 60, 60)
        padded[edge, 3] = 255
    return padded


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _item_for(result: MontageResult, placement: Placement, dpi: int) -> np.ndarray:
    item = result.items[placement.item]
    img = item.image
    if placement.rotated:
        img = rotate(img, 90)
    target_w = mm_to_px(placement.w_mm, dpi)
    target_h = mm_to_px(placement.h_mm, dpi)
    return resize(img, target_w, target_h)


def render_page(result: MontageResult, page_index: int,
                dpi: int | None = None) -> np.ndarray:
    """Rasterise one sheet as an RGBA array, ready to save as PNG/TIFF."""
    opt = result.options
    dpi = dpi or opt.dpi
    pw, ph = result.page_size_mm
    width, height = mm_to_px(pw, dpi), mm_to_px(ph, dpi)
    canvas = np.zeros((height, width, 4), dtype=np.uint8)
    if opt.background:
        canvas[:, :, :3] = np.array(hex_to_rgb(opt.background), dtype=np.uint8)
        canvas[:, :, 3] = 255

    for placement in result.pages[page_index]:
        patch = _item_for(result, placement, dpi)
        x = mm_to_px(placement.x_mm, dpi) if placement.x_mm else 0
        y = mm_to_px(placement.y_mm, dpi) if placement.y_mm else 0
        _blend(canvas, patch, x, y)

    if opt.outline or opt.crop_marks or opt.registration_marks:
        canvas = _draw_marks(canvas, result, page_index, dpi)
    if opt.mirror:
        canvas = mirror(canvas, horizontal=True)
    return canvas


def _blend(canvas: np.ndarray, patch: np.ndarray, x: int, y: int) -> None:
    h, w = patch.shape[:2]
    ch, cw = canvas.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(cw, x + w), min(ch, y + h)
    if x1 <= x0 or y1 <= y0:
        return
    src = patch[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    dst = canvas[y0:y1, x0:x1].astype(np.float32)
    sa = src[:, :, 3:4] / 255.0
    da = dst[:, :, 3:4] / 255.0
    out_a = sa + da * (1.0 - sa)
    rgb = src[:, :, :3] * sa + dst[:, :, :3] * da * (1.0 - sa)
    safe = np.maximum(out_a, 1e-6)
    canvas[y0:y1, x0:x1, :3] = np.clip(rgb / safe + 0.5, 0, 255).astype(np.uint8)
    canvas[y0:y1, x0:x1, 3] = np.clip(out_a[:, :, 0] * 255 + 0.5, 0, 255).astype(np.uint8)


def _draw_marks(canvas: np.ndarray, result: MontageResult, page_index: int,
                dpi: int) -> np.ndarray:
    opt = result.options
    img = to_pil(canvas)
    draw = ImageDraw.Draw(img)
    pw, ph = result.page_size_mm
    mark = mm_to_px(4.0, dpi)
    thin = max(1, mm_to_px(0.2, dpi))

    if opt.outline:
        for placement in result.pages[page_index]:
            x0, y0 = mm_to_px(placement.x_mm, dpi), mm_to_px(placement.y_mm, dpi)
            x1 = x0 + mm_to_px(placement.w_mm, dpi)
            y1 = y0 + mm_to_px(placement.h_mm, dpi)
            draw.rectangle([x0, y0, x1, y1], outline=(150, 150, 150, 255), width=thin)

    if opt.crop_marks:
        for placement in result.pages[page_index]:
            x0, y0 = mm_to_px(placement.x_mm, dpi), mm_to_px(placement.y_mm, dpi)
            x1 = x0 + mm_to_px(placement.w_mm, dpi)
            y1 = y0 + mm_to_px(placement.h_mm, dpi)
            for (cx, cy) in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                draw.line([cx - mark, cy, cx - mark // 3, cy],
                          fill=(0, 0, 0, 255), width=thin)
                draw.line([cx, cy - mark, cx, cy - mark // 3],
                          fill=(0, 0, 0, 255), width=thin)

    if opt.registration_marks:
        radius = mm_to_px(3.0, dpi)
        inset = mm_to_px(max(3.0, opt.margin_mm / 2), dpi)
        for cx, cy in ((inset, inset), (canvas.shape[1] - inset, inset),
                       (inset, canvas.shape[0] - inset),
                       (canvas.shape[1] - inset, canvas.shape[0] - inset)):
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius],
                         outline=(0, 0, 0, 255), width=thin)
            draw.line([cx - radius * 1.5, cy, cx + radius * 1.5, cy],
                      fill=(0, 0, 0, 255), width=thin)
            draw.line([cx, cy - radius * 1.5, cx, cy + radius * 1.5],
                      fill=(0, 0, 0, 255), width=thin)
    return np.asarray(img, dtype=np.uint8)


def export_pdf(result: MontageResult, path: str | Path,
               jpeg_quality: int = 0) -> Path:
    """Vector sheet: each artwork is embedded once per placement, at *dpi*."""
    opt = result.options
    pw, ph = result.page_size_mm
    writer = PdfWriter("PrintPro montage", jpeg_quality=jpeg_quality)
    background = hex_to_rgb(opt.background) if opt.background else None

    for page_index, placements in enumerate(result.pages):
        page = Page(pw, ph, background=background)
        for placement in placements:
            patch = _item_for(result, placement, opt.dpi)
            x_mm = placement.x_mm
            if opt.mirror:
                x_mm = pw - placement.x_mm - placement.w_mm
                patch = mirror(patch, horizontal=True)
            page.images.append(PlacedImage(patch, x_mm, placement.y_mm,
                                           placement.w_mm, placement.h_mm))
            if opt.crop_marks:
                page.lines.extend(_crop_lines(x_mm, placement.y_mm,
                                              placement.w_mm, placement.h_mm))
            if opt.outline:
                page.lines.extend(_frame_lines(x_mm, placement.y_mm,
                                               placement.w_mm, placement.h_mm))
        writer.add_page(page)
    return writer.save(path)


def _crop_lines(x: float, y: float, w: float, h: float, length: float = 4.0):
    lines = []
    for cx, cy in ((x, y), (x + w, y), (x, y + h), (x + w, y + h)):
        lines.append(Line(cx - length, cy, cx - length / 3, cy, 0.15))
        lines.append(Line(cx, cy - length, cx, cy - length / 3, 0.15))
    return lines


def _frame_lines(x: float, y: float, w: float, h: float):
    grey = (0.6, 0.6, 0.6)
    return [Line(x, y, x + w, y, 0.1, grey), Line(x + w, y, x + w, y + h, 0.1, grey),
            Line(x + w, y + h, x, y + h, 0.1, grey), Line(x, y + h, x, y, 0.1, grey)]


def export_sheets(result: MontageResult, directory: str | Path,
                  stem: str = "planche", fmt: str = "png") -> list[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(result.page_count):
        page = render_page(result, index)
        paths.append(save_image(page, directory / f"{stem}-{index + 1:02d}.{fmt}",
                                dpi=result.options.dpi))
    return paths
