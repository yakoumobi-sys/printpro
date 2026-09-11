import numpy as np

from printpro.upscale import (UpscaleOptions, print_quality, target_size,
                              upscale)


def test_scale_factor(disc_on_white):
    image, _ = disc_on_white
    result = upscale(image, UpscaleOptions(scale=3, method="lanczos"))
    assert result.target_size == (420, 420)
    assert result.image.shape[:2] == (420, 420)


def test_explicit_target_width_keeps_ratio():
    image = np.zeros((50, 100, 4), np.uint8)
    image[:, :, 3] = 255
    assert target_size(image, UpscaleOptions(target_width=400)) == (400, 200)
    assert target_size(image, UpscaleOptions(target_height=200)) == (400, 200)


def test_physical_target_uses_dpi():
    image = np.zeros((10, 10, 4), np.uint8)
    options = UpscaleOptions(target_mm=(100.0, 50.0), target_dpi=300)
    assert target_size(image, options) == (1181, 591)


def test_max_pixels_guard():
    image = np.zeros((1000, 1000, 4), np.uint8)
    image[:, :, 3] = 255
    result = upscale(image, UpscaleOptions(scale=20, method="lanczos",
                                           max_pixels=4_000_000))
    assert result.image.shape[0] * result.image.shape[1] <= 4_000_000
    assert result.notes


def test_auto_picks_vector_for_flat_art(disc_on_white):
    image, _ = disc_on_white
    result = upscale(image, UpscaleOptions(scale=2, method="auto"))
    assert result.method == "vector"


def test_auto_picks_edge_for_photos():
    rng = np.random.default_rng(1)
    image = np.zeros((80, 80, 4), np.uint8)
    image[:, :, :3] = rng.integers(0, 255, (80, 80, 3), dtype=np.uint8)
    image[:, :, 3] = 255
    result = upscale(image, UpscaleOptions(scale=2, method="auto"))
    assert result.method == "edge"


def test_vector_upscale_stays_sharp(disc_on_white):
    image, _ = disc_on_white
    result = upscale(image, UpscaleOptions(scale=4, method="vector"))
    centre = result.image[280, 280, :3]
    assert centre[0] > 150 and centre[1] < 90          # still solid red
    # A vector render has far fewer intermediate colours than a blur.
    unique = np.unique(result.image[:, :, :3].reshape(-1, 3), axis=0)
    assert len(unique) < 40


def test_ai_falls_back_without_binary(disc_on_white):
    image, _ = disc_on_white
    result = upscale(image, UpscaleOptions(scale=2, method="ai"))
    assert result.method in ("ai", "edge")


def test_print_quality_grades():
    assert print_quality(1181, 1181, 100, 100)["grade"] == "excellent"
    assert print_quality(600, 600, 100, 100)["grade"] == "moyen"
    assert print_quality(200, 200, 100, 100)["grade"] == "faible"
