"""PrintPro — vectorisation, agrandissement, détourage et montage pour l'impression."""

from .bgremove import BgOptions, remove_background
from .imaging import load_rgba, save_image
from .montage import (PAGE_PRESETS, MontageItem, MontageOptions, build_montage,
                      export_pdf, export_sheets, render_page)
from .pipeline import prepare, prepare_file, quality_check, quick_montage
from .upscale import UpscaleOptions, upscale
from .vectorize import VectorOptions, vectorize

__version__ = "1.0.0"
__all__ = [
    "BgOptions", "remove_background", "UpscaleOptions", "upscale",
    "VectorOptions", "vectorize", "MontageItem", "MontageOptions",
    "build_montage", "render_page", "export_pdf", "export_sheets",
    "PAGE_PRESETS", "prepare", "prepare_file", "quick_montage",
    "quality_check", "load_rgba", "save_image", "__version__",
]
