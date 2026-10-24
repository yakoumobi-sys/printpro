import re

import numpy as np
import pytest

from printpro.cmyk import CmykOptions, ink_coverage, limit_tac, rgb_to_cmyk
from printpro.pdfwriter import Page, PdfWriter, PlacedImage
from printpro.preflight import preflight_pdf
from printpro.prepress import PressOptions, inspect_artwork, make_press_ready
from printpro.products import PRODUCTS, get_product, resolve_product

from conftest import solid


def gradient(width=150, height=100):
    img = np.zeros((height, width, 4), np.uint8)
    img[:, :, 0] = np.linspace(0, 255, width)[None, :]
    img[:, :, 1] = np.linspace(255, 0, height)[:, None]
    img[:, :, 2] = 90
    img[:, :, 3] = 255
    return img


# ------------------------------------------------------------------ products
def test_products_and_custom_sizes():
    assert PRODUCTS["affiche-a3"].bleed_mm == 3
    assert get_product("dtf-a4").bleed_mm == 0
    custom = resolve_product("320x450", bleed_mm=5)
    assert (custom.width_mm, custom.height_mm, custom.bleed_mm) == (320, 450, 5)
    assert resolve_product("flyer-a5", dpi=600).dpi == 600
    with pytest.raises(KeyError):
        resolve_product("inconnu")


# ---------------------------------------------------------------------- cmyk
def test_cmyk_black_white_and_tac():
    img = solid(4, 4, (0, 0, 0))
    img[0, 0, :3] = (255, 255, 255)
    img[1, 1, :3] = (30, 20, 25)
    sep = rgb_to_cmyk(img, CmykOptions(tac=280))
    assert sep.engine == "builtin"
    assert tuple(sep.cmyk[0, 0]) == (0, 0, 0, 0)          # paper white: no ink
    assert sep.cmyk[2, 2, 3] == 255                        # black carried by K
    assert ink_coverage(sep.cmyk).max() <= 280 + 0.5
    assert sep.max_tac <= 280.5


def test_limit_tac_keeps_black():
    cmyk = np.full((2, 2, 4), 255, np.uint8)                # 400 %
    out = limit_tac(cmyk, 300)
    assert ink_coverage(out).max() <= 300
    assert (out[:, :, 3] == 255).all()


# ------------------------------------------------------------- press ready
def test_press_ready_pdf_passes_preflight():
    product = get_product("carte-visite")
    result = make_press_ready(gradient(), product)
    assert result.preflight.passed
    assert len(result.preflight.checks) == 9
    assert all(c.status == "ok" for c in result.preflight.checks)
    pdf = result.pdf
    assert pdf.startswith(b"%PDF-1.3")
    assert b"/GTS_PDFXVersion (PDF/X-1:2001)" in pdf
    assert b"/OutputIntents" in pdf and b"/TrimBox" in pdf and b"/BleedBox" in pdf
    assert b"/DeviceRGB" not in pdf and b"/SMask" not in pdf
    # 85 x 55 mm + 2 mm bleed each side at 300 dpi
    bleed = round(2 / 25.4 * 300)
    assert result.cmyk.shape[:2] == (650 + 2 * bleed, 1004 + 2 * bleed)
    assert result.specs["upscale_factor"] > 1


def test_trimbox_matches_product():
    result = make_press_ready(gradient(100, 150), get_product("flyer-a6"))
    match = re.search(rb"/TrimBox \[([\d. ]+)\]", result.pdf)
    x0, y0, x1, y1 = (float(v) for v in match.group(1).split())
    assert abs((x1 - x0) / 72 * 25.4 - 105) < 0.05
    assert abs((y1 - y0) / 72 * 25.4 - 148) < 0.05


def test_orientation_follows_artwork():
    landscape = make_press_ready(gradient(150, 100), get_product("flyer-a6"))
    assert landscape.width_mm > landscape.height_mm
    forced = make_press_ready(gradient(150, 100), get_product("flyer-a6"),
                              PressOptions(orientation="portrait"))
    assert forced.width_mm < forced.height_mm


def test_contain_and_no_marks():
    result = make_press_ready(gradient(), get_product("carre-20"),
                              PressOptions(fit="contain", crop_marks=False))
    marks = [c for c in result.preflight.checks if c.key == "marks"][0]
    assert marks.status == "warn" and result.preflight.passed


def test_preflight_rejects_plain_rgb_pdf():
    writer = PdfWriter()
    page = Page(100, 100)
    page.images.append(PlacedImage(solid(50, 50, (255, 0, 0)), 0, 0, 100, 100))
    writer.add_page(page)
    report = preflight_pdf(writer.getvalue(), get_product("carre-20"))
    assert not report.passed
    failed = {c.key for c in report.checks if c.status == "fail"}
    assert {"standard", "intent", "cmyk", "dpi"} <= failed


def test_inspect_artwork_flags_problems():
    report = inspect_artwork(gradient(), get_product("affiche-a3"))
    keys = {c["key"]: c["status"] for c in report["checks"]}
    assert keys["dpi"] == "warn" and keys["bleed"] == "warn"
    assert report["upscale_factor"] > 10
    dtf = inspect_artwork(gradient(), get_product("dtf-coeur"))
    assert {c["key"] for c in dtf["checks"]} >= {"alpha", "edges", "white"}


def test_product_bounds():
    for spec in ("nanxnan", "0x10", "10x99999", "infx10"):
        with pytest.raises(ValueError):
            resolve_product(spec)
    with pytest.raises(ValueError):
        resolve_product("flyer-a5", dpi=10_000)
    with pytest.raises(ValueError):
        resolve_product("flyer-a5", bleed_mm=-1)


def test_oversized_job_is_refused_before_allocating():
    with pytest.raises(ValueError, match="trop grand"):
        make_press_ready(gradient(), resolve_product("3000x3000", dpi=600))


def test_cover_crops_before_upscaling():
    # A 20:1 panorama on a square must not be upscaled to 20× the canvas.
    panorama = gradient(400, 20)
    result = make_press_ready(panorama, get_product("carte-visite"),
                              PressOptions(orientation="portrait"))
    assert result.preflight.passed
    assert any("recadrage centré" in step for step in result.steps)
