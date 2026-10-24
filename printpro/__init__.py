"""PrintPro — fichiers prêts pour l'imprimeur : PDF/X-1a, DTF, vectorisation, montage."""

from .bgremove import BgOptions, remove_background
from .cmyk import CmykOptions, rgb_to_cmyk
from .dtf import DtfOptions, make_dtf
from .imaging import load_rgba, save_image
from .montage import (PAGE_PRESETS, MontageItem, MontageOptions, build_montage,
                      export_pdf, export_sheets, render_page)
from .pipeline import prepare, prepare_file, quality_check, quick_montage
from .preflight import preflight_pdf
from .prepress import PressOptions, inspect_artwork, make_press_ready
from .products import PRODUCTS, get_product, resolve_product
from .upscale import UpscaleOptions, upscale
from .vectorize import VectorOptions, vectorize

__version__ = "2.0.0"
__all__ = [
    "BgOptions", "remove_background", "UpscaleOptions", "upscale",
    "VectorOptions", "vectorize", "MontageItem", "MontageOptions",
    "build_montage", "render_page", "export_pdf", "export_sheets",
    "PAGE_PRESETS", "prepare", "prepare_file", "quick_montage",
    "quality_check", "load_rgba", "save_image", "__version__",
    "PRODUCTS", "get_product", "resolve_product", "PressOptions",
    "make_press_ready", "inspect_artwork", "preflight_pdf", "CmykOptions",
    "rgb_to_cmyk", "DtfOptions", "make_dtf",
]
