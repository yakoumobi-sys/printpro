import numpy as np

from printpro.raster import rasterize_polygons, render_layers
from printpro.vectorize import (VectorOptions, _rdp, _ring_area, _trace,
                                vectorize)


def test_trace_square_gives_exact_outline():
    mask = np.zeros((20, 20), bool)
    mask[5:15, 4:16] = True
    rings = _trace(mask)
    assert len(rings) == 1
    assert abs(_ring_area(rings[0])) == 120.0        # 10 x 12 pixels


def test_trace_detects_hole():
    mask = np.zeros((30, 30), bool)
    mask[5:25, 5:25] = True
    mask[12:18, 12:18] = False
    rings = _trace(mask)
    assert len(rings) == 2
    areas = sorted(abs(_ring_area(r)) for r in rings)
    assert areas == [36.0, 400.0]


def test_rdp_keeps_corners():
    line = np.array([[0, 0], [1, 0], [2, 0], [3, 0], [3, 3]], float)
    simplified = _rdp(line, 0.2)
    assert len(simplified) == 3                       # start, corner, end


def test_vectorize_square_is_one_shape():
    img = np.zeros((80, 80, 4), np.uint8)
    img[:, :, :3] = 255
    img[:, :, 3] = 255
    img[20:60, 20:60, :3] = 0
    result = vectorize(img, VectorOptions(colors=2, detail=0.5))
    assert result.shapes == 1
    assert "<svg" in result.svg and "</svg>" in result.svg
    assert result.svg.count("<path") == 1


def test_vectorized_ring_matches_source(ring_on_white):
    result = vectorize(ring_on_white, VectorOptions(colors=3, detail=0.8))
    rendered = render_layers(result.layers, result.width, result.height,
                             background=(255, 255, 255))
    error = np.abs(rendered[:, :, :3].astype(int) -
                   ring_on_white[:, :, :3].astype(int)).mean()
    assert error < 8.0                                # sub-pixel fidelity


def test_bw_mode_uses_two_colours():
    img = np.zeros((60, 60, 4), np.uint8)
    img[:, :, :3] = 240
    img[:, :, 3] = 255
    img[10:50, 25:35, :3] = 15
    result = vectorize(img, VectorOptions(mode="bw"))
    assert len(result.palette) == 2
    assert result.shapes >= 1


def test_transparent_areas_are_not_traced(sticker):
    result = vectorize(sticker, VectorOptions(colors=2))
    for layer in result.layers:
        for shape in layer.paths:
            xs = shape[0][:, 0]
            assert xs.min() >= 8 and xs.max() <= 92


def test_fringes_are_dissolved_but_thin_shapes_survive():
    img = np.zeros((100, 100, 4), np.uint8)
    img[:, :, :3] = 255
    img[:, :, 3] = 255
    img[20:80, 20:80, :3] = (200, 30, 30)
    img[19, 20:80, :3] = (228, 143, 143)     # anti-aliasing halo
    img[85:88, 10:90, :3] = (0, 0, 0)        # thin but real black bar
    result = vectorize(img, VectorOptions(colors=6, detail=0.6))
    colours = [c for c in (layer.color for layer in result.layers)]
    assert any(c[0] > 150 and c[1] < 80 for c in colours)      # red kept
    assert any(sum(c) < 120 for c in colours)                  # black bar kept
    assert not any(c[0] > 200 and 100 < c[1] < 200 for c in colours)  # halo gone


def test_rasterize_coverage_is_exact():
    square = np.array([[2, 2], [12, 2], [12, 9], [2, 9]], float)
    cov = rasterize_polygons([square], 20, 20)
    assert abs(cov.sum() - 70.0) < 0.6
