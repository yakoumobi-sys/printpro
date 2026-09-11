import numpy as np

from printpro.bgremove import BgOptions, remove_background


def test_removes_uniform_background(disc_on_white):
    image, mask = disc_on_white
    result = remove_background(image, BgOptions(tolerance=12, feather=0))
    assert result.image[:, :, 3][mask].mean() > 250        # subject kept
    assert result.image[0, 0, 3] == 0                      # corner erased
    assert result.background_colors[0][0] > 200            # learnt white


def test_color_method_targets_requested_colour(disc_on_white):
    image, mask = disc_on_white
    result = remove_background(image, BgOptions(method="color", color="#fafafa",
                                                tolerance=10, feather=0))
    assert result.image[:, :, 3][mask].mean() > 250
    assert result.image[5, 5, 3] == 0


def test_interior_holes_are_punched_when_requested(ring_on_white):
    keep = remove_background(ring_on_white, BgOptions(tolerance=12, feather=0,
                                                      keep_holes=True))
    filled = remove_background(ring_on_white, BgOptions(tolerance=12, feather=0,
                                                        keep_holes=False))
    assert keep.image[80, 80, 3] == 0          # hole is transparent
    assert filled.image[80, 80, 3] > 200       # hole kept opaque


def test_edge_shift_grows_and_shrinks(disc_on_white):
    image, _ = disc_on_white
    base = remove_background(image, BgOptions(feather=0)).alpha > 128
    grown = remove_background(image, BgOptions(feather=0, edge_shift=2)).alpha > 128
    shrunk = remove_background(image, BgOptions(feather=0, edge_shift=-2)).alpha > 128
    assert shrunk.sum() < base.sum() < grown.sum()


def test_largest_only_drops_specks(disc_on_white):
    image, _ = disc_on_white
    image[5:8, 5:8, :3] = (0, 0, 0)            # a speck in the corner
    result = remove_background(image, BgOptions(feather=0, largest_only=True))
    assert result.image[6, 6, 3] == 0


def test_ai_method_falls_back_without_rembg(disc_on_white):
    image, _ = disc_on_white
    result = remove_background(image, BgOptions(method="ai"))
    assert result.engine in ("ai", "auto")     # never raises
    assert result.image.shape == image.shape


def test_despill_removes_background_tint():
    img = np.zeros((40, 40, 4), np.uint8)
    img[:, :, :3] = (0, 255, 0)                # green screen
    img[:, :, 3] = 255
    img[10:30, 10:30, :3] = (200, 60, 60)
    img[9, 10:30, :3] = (100, 155, 30)         # blended edge pixel
    result = remove_background(img, BgOptions(tolerance=25, softness=30))
    edge = result.image[9, 15]
    if edge[3] > 10:
        assert int(edge[1]) < 155              # green cast reduced


def test_defringe_removes_background_halo():
    img = np.zeros((60, 60, 4), np.uint8)
    img[:, :, :3] = (30, 180, 170)              # turquoise background
    img[:, :, 3] = 255
    img[20:40, 20:40, :3] = (255, 200, 40)      # yellow subject
    img[19, 20:40, :3] = (140, 190, 105)        # anti-aliased edge row
    img[40, 20:40, :3] = (140, 190, 105)

    without = remove_background(img, BgOptions(tolerance=14, feather=0,
                                               defringe=0))
    with_fix = remove_background(img, BgOptions(tolerance=14, feather=0,
                                                defringe=1.5))
    assert without.alpha[19, 30] > with_fix.alpha[19, 30]
    assert with_fix.alpha[30, 30] == 255        # subject untouched
