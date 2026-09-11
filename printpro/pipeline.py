"""One-shot preparation pipeline: detour -> upscale -> vectorise -> montage.

This is the layer both the CLI and the web server call, so a file prepared
from the terminal and one prepared from the browser go through exactly the
same code.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .bgremove import BgOptions, remove_background
from .imaging import load_rgba, px_to_mm, save_image, trim
from .montage import (MontageItem, MontageOptions, build_montage, export_pdf,
                      export_sheets)
from .upscale import UpscaleOptions, print_quality, upscale
from .vectorize import VectorOptions, vectorize


@dataclass
class StepReport:
    name: str
    detail: str
    seconds: float


@dataclass
class PipelineResult:
    image: np.ndarray
    svg: str | None = None
    steps: list[StepReport] = field(default_factory=list)
    info: dict = field(default_factory=dict)

    def report(self) -> str:
        lines = [f"  • {s.name}: {s.detail} ({s.seconds * 1000:.0f} ms)"
                 for s in self.steps]
        return "\n".join(lines)


def prepare(image: np.ndarray,
            remove_bg: bool = False,
            bg_options: BgOptions | None = None,
            upscale_options: UpscaleOptions | None = None,
            vector_options: VectorOptions | None = None,
            auto_trim: bool = False) -> PipelineResult:
    """Run the requested steps in the only order that makes sense.

    Detouring first keeps the background out of the upscaler and the tracer;
    tracing last means it works on the cleanest, largest version available.
    """
    result = PipelineResult(image=np.asarray(image, dtype=np.uint8))

    if remove_bg:
        start = time.perf_counter()
        cut = remove_background(result.image, bg_options or BgOptions())
        result.image = cut.image
        result.info["background_colors"] = cut.background_colors
        result.info["bg_engine"] = cut.engine
        result.steps.append(StepReport(
            "détourage",
            f"moteur {cut.engine}, sujet {cut.coverage * 100:.0f}% de l'image",
            time.perf_counter() - start))

    if auto_trim:
        start = time.perf_counter()
        before = result.image.shape[:2]
        result.image = trim(result.image, padding=2)
        result.steps.append(StepReport(
            "rognage", f"{before[1]}×{before[0]} -> "
                       f"{result.image.shape[1]}×{result.image.shape[0]} px",
            time.perf_counter() - start))

    if upscale_options is not None:
        start = time.perf_counter()
        up = upscale(result.image, upscale_options)
        result.image = up.image
        result.info["upscale_method"] = up.method
        detail = (f"{up.source_size[0]}×{up.source_size[1]} -> "
                  f"{up.target_size[0]}×{up.target_size[1]} px "
                  f"(×{up.factor:.2f}, {up.method})")
        if up.notes:
            detail += " — " + "; ".join(up.notes)
        result.steps.append(StepReport("agrandissement", detail,
                                       time.perf_counter() - start))

    if vector_options is not None:
        start = time.perf_counter()
        vec = vectorize(result.image, vector_options)
        result.svg = vec.svg
        result.info["vector_shapes"] = vec.shapes
        result.info["vector_nodes"] = vec.nodes
        result.info["vector_palette"] = [list(c) for c in vec.palette]
        result.steps.append(StepReport(
            "vectorisation",
            f"{vec.shapes} formes, {vec.nodes} nœuds, "
            f"{len(vec.palette)} couleurs",
            time.perf_counter() - start))

    h, w = result.image.shape[:2]
    result.info["size_px"] = [int(w), int(h)]
    return result


def prepare_file(path: str | Path, output: str | Path | None = None,
                 **kwargs) -> PipelineResult:
    result = prepare(load_rgba(path), **kwargs)
    if output:
        output = Path(output)
        if result.svg and output.suffix.lower() == ".svg":
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(result.svg, encoding="utf-8")
        else:
            save_image(result.image, output)
            if result.svg:
                svg_path = output.with_suffix(".svg")
                svg_path.write_text(result.svg, encoding="utf-8")
    return result


def quick_montage(images: list[np.ndarray], width_mm: float, quantity: int,
                  options: MontageOptions | None = None,
                  out_dir: str | Path = "out", stem: str = "planche"):
    """Build a montage from plain images and export PNG sheets + a PDF."""
    opt = options or MontageOptions()
    items = [MontageItem(image=img, width_mm=width_mm, quantity=quantity)
             for img in images]
    result = build_montage(items, opt)
    out_dir = Path(out_dir)
    sheets = export_sheets(result, out_dir, stem=stem)
    pdf = export_pdf(result, out_dir / f"{stem}.pdf")
    return result, sheets, pdf


def quality_check(image: np.ndarray, width_mm: float, height_mm: float | None = None,
                  dpi: int = 300) -> dict:
    """Is this file printable at that physical size?"""
    h, w = image.shape[:2]
    if height_mm is None:
        height_mm = width_mm * h / max(w, 1)
    report = print_quality(w, h, width_mm, height_mm)
    report["needed_px"] = [int(round(width_mm / 25.4 * dpi)),
                           int(round(height_mm / 25.4 * dpi))]
    report["current_px"] = [int(w), int(h)]
    report["upscale_factor"] = round(
        max(report["needed_px"][0] / max(w, 1),
            report["needed_px"][1] / max(h, 1)), 2)
    report["native_size_mm"] = [round(px_to_mm(w, dpi), 1),
                                round(px_to_mm(h, dpi), 1)]
    return report
