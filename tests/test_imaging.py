import numpy as np
import pytest

from printpro import imaging


def test_mm_px_roundtrip():
    assert imaging.mm_to_px(210, 300) == 2480
    assert round(imaging.px_to_mm(2480, 300), 1) == 210.0
    assert round(imaging.mm_to_pt(210), 2) == 595.28


def test_resize_keeps_alpha_clean(sticker):
    out = imaging.resize(sticker, 50, 100)
    assert out.shape == (100, 50, 4)
    # No colour bleeding from the transparent area into the opaque one.
    opaque = out[:, :, 3] > 200
    assert opaque.any()
    assert out[opaque][:, 1].mean() > 120


def test_trim_removes_transparent_border(sticker):
    trimmed = imaging.trim(sticker)
    assert trimmed.shape[0] == 160 and trimmed.shape[1] == 80


def test_flatten_over_background():
    rgba = np.zeros((2, 2, 4), np.uint8)
    rgba[:, :, 0] = 255
    rgba[:, :, 3] = 128
    flat = imaging.flatten(rgba, (0, 0, 255))
    assert flat[0, 0, 3] == 255
    assert flat[0, 0, 0] == pytest.approx(128, abs=2)
    assert flat[0, 0, 2] == pytest.approx(127, abs=2)


def test_rgb_to_lab_reference_values():
    white = imaging.rgb_to_lab(np.full((1, 1, 3), 255, np.uint8))[0, 0]
    assert white[0] == pytest.approx(100.0, abs=0.3)
    assert abs(white[1]) < 0.5 and abs(white[2]) < 0.5


def test_mirror_and_rotate(sticker):
    assert np.array_equal(imaging.mirror(imaging.mirror(sticker)), sticker)
    assert imaging.rotate(sticker, 90).shape[:2] == sticker.shape[:2][::-1]
    assert np.array_equal(imaging.rotate(sticker, 360), sticker)


def test_hex_conversions():
    assert imaging.hex_to_rgb("#f0a") == (255, 0, 170)
    assert imaging.rgb_to_hex((16, 32, 48)) == "#102030"
    with pytest.raises(ValueError):
        imaging.hex_to_rgb("nope")


def test_is_photographic_discriminates(disc_on_white):
    flat, _ = disc_on_white
    rng = np.random.default_rng(0)
    noisy = flat.copy()
    noisy[:, :, :3] = rng.integers(0, 255, (140, 140, 3), dtype=np.uint8)
    assert not imaging.is_photographic(flat)
    assert imaging.is_photographic(noisy)
