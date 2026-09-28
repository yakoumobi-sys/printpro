import numpy as np
import pytest

from printpro.montage import (MontageItem, MontageOptions, add_cut_contour,
                              build_montage, export_pdf, export_sheets,
                              page_size, render_page)


def item(size_mm=40.0, quantity=1, px=120, color=(200, 40, 40)):
    img = np.zeros((px, px, 4), np.uint8)
    img[:, :, :3] = color
    img[:, :, 3] = 255
    return MontageItem(image=img, width_mm=size_mm, quantity=quantity)


def test_page_presets_and_orientation():
    assert page_size(MontageOptions(page="A4")) == (210.0, 297.0)
    assert page_size(MontageOptions(page="A4", orientation="landscape")) == (297.0, 210.0)
    assert page_size(MontageOptions(page_width_mm=500, page_height_mm=700)) == (500.0, 700.0)


def test_grid_layout_fills_rows():
    result = build_montage([item(40, 12)],
                           MontageOptions(layout="grid", spacing_mm=2, margin_mm=5))
    assert result.placed == 12
    first_row = [p for p in result.pages[0] if p.y_mm == result.pages[0][0].y_mm]
    assert len(first_row) == 4                      # (210-10+2)/42 -> 4 columns


def test_fill_layout_maximises_copies():
    result = build_montage([item(50, 1)],
                           MontageOptions(layout="fill", spacing_mm=0, margin_mm=0))
    assert result.placed == 4 * 5                   # 210/50 x 297/50


def test_pack_layout_mixes_sizes_without_overlap():
    result = build_montage([item(60, 4), item(25, 20, color=(20, 80, 200))],
                           MontageOptions(layout="pack", spacing_mm=2))
    assert result.placed == 24
    for page in result.pages:
        for i, a in enumerate(page):
            for b in page[i + 1:]:
                separated = (a.x_mm + a.w_mm <= b.x_mm + 1e-6 or
                             b.x_mm + b.w_mm <= a.x_mm + 1e-6 or
                             a.y_mm + a.h_mm <= b.y_mm + 1e-6 or
                             b.y_mm + b.h_mm <= a.y_mm + 1e-6)
                assert separated, "deux visuels se chevauchent"


def test_items_stay_inside_the_printable_area():
    result = build_montage([item(45, 30)], MontageOptions(margin_mm=10))
    width, height = result.page_size_mm
    for page in result.pages:
        for placement in page:
            assert placement.x_mm >= 10 - 1e-6
            assert placement.y_mm >= 10 - 1e-6
            assert placement.x_mm + placement.w_mm <= width - 10 + 1e-6
            assert placement.y_mm + placement.h_mm <= height - 10 + 1e-6


def test_oversized_item_is_reported():
    result = build_montage([item(400, 1)], MontageOptions(page="A4"))
    assert result.placed == 0
    assert result.warnings and "dépasse" in result.warnings[0]


def test_multi_page_overflow():
    result = build_montage([item(90, 20)], MontageOptions(layout="pack"))
    assert result.page_count > 1
    assert result.placed == 20


def test_margins_too_large_raises():
    with pytest.raises(ValueError):
        build_montage([item()], MontageOptions(page="A6", margin_mm=90))


def test_render_page_dimensions_and_background():
    result = build_montage([item(40, 2)], MontageOptions(dpi=150, background="#ff0000"))
    sheet = render_page(result, 0)
    assert sheet.shape == (1754, 1240, 4)
    assert tuple(sheet[0, 0, :3]) == (255, 0, 0)


def test_transparent_background_is_preserved():
    result = build_montage([item(40, 1)], MontageOptions(dpi=72, background=None))
    sheet = render_page(result, 0)
    assert sheet[0, 0, 3] == 0


def test_mirror_flips_the_sheet():
    result = build_montage([item(40, 1)], MontageOptions(dpi=72, layout="grid",
                                                          margin_mm=5))
    normal = render_page(result, 0)
    result.options.mirror = True
    flipped = render_page(result, 0)
    assert np.array_equal(np.ascontiguousarray(normal[:, ::-1]), flipped)


def test_cut_contour_grows_the_artwork():
    img = np.zeros((60, 60, 4), np.uint8)
    img[20:40, 20:40, :3] = (10, 10, 10)
    img[20:40, 20:40, 3] = 255
    grown = add_cut_contour(img, 4, (255, 255, 255))
    assert grown.shape[0] > img.shape[0]
    assert (grown[:, :, 3] > 0).sum() > (img[:, :, 3] > 0).sum()


def test_exports_produce_files(tmp_path):
    result = build_montage([item(40, 3)], MontageOptions(dpi=100))
    sheets = export_sheets(result, tmp_path)
    pdf = export_pdf(result, tmp_path / "planche.pdf")
    assert all(p.exists() and p.stat().st_size > 0 for p in sheets)
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF-1.5") and data.rstrip().endswith(b"%%EOF")
    assert data.count(b"/Type /Page ") == result.page_count


def test_summary_reports_efficiency():
    result = build_montage([item(50, 6)], MontageOptions())
    summary = result.summary()
    assert summary["placed"] == 6
    assert 0 < summary["efficiency"] <= 100


def test_unplaced_copies_are_counted():
    result = build_montage([item(400, 3), item(40, 2)])
    assert result.requested == 5 and result.placed == 2
    assert any("3 exemplaire" in w for w in result.warnings)


def test_page_limit_warns():
    result = build_montage([item(150, 5)], MontageOptions(max_pages=1))
    assert result.placed < result.requested
    assert any("non placé" in w for w in result.warnings)


def test_grid_rotates_to_fit_and_rejects_excess_columns():
    image = np.full((100, 250, 4), 255, np.uint8)
    result = build_montage([MontageItem(image, width_mm=250)], MontageOptions(layout="grid"))
    assert result.pages[0][0].rotated
    assert result.pages[0][0].w_mm <= 198
    with pytest.raises(ValueError, match="colonnes"):
        build_montage([item(100)], MontageOptions(layout="grid", columns=20))


def test_fill_respects_rotation_lock():
    from printpro.montage import _fill_count
    assert _fill_count(90, 60, 100, 200, MontageOptions(spacing_mm=0), False) == 3


def test_duplicate_free_rectangles_survive_pruning():
    from printpro.montage import _MaxRects
    packer = _MaxRects(100, 100)
    packer.free = [[0, 0, 100, 100], [0, 0, 100, 100]]
    packer._prune()
    assert len(packer.free) == 1
    assert packer.insert(50, 50, True)


def test_cut_contour_preserves_artwork_scale():
    original = item(40, px=120)
    result = build_montage([original], MontageOptions(cut_contour_mm=2, cut_stroke=True))
    prepared = result.items[0]
    assert prepared.width_mm / prepared.image.shape[1] == pytest.approx(40 / 120)
    assert prepared.height_mm / prepared.image.shape[0] == pytest.approx(40 / 120)


def test_repeated_artwork_is_embedded_once_in_pdf(tmp_path):
    result = build_montage([item(40, 12)], MontageOptions(dpi=72, layout="grid"))
    data = export_pdf(result, tmp_path / "repeated.pdf").read_bytes()
    assert data.count(b"/Subtype /Image") == 1
