import numpy as np

from printpro.dtf import DtfOptions, halftone, make_dtf
from printpro.products import get_product

from conftest import solid


def badge_on_black():
    """A soft-edged orange disc on a black background (garment colour)."""
    img = solid(160, 160, (0, 0, 0))
    yy, xx = np.mgrid[0:160, 0:160]
    dist = np.sqrt((yy - 80) ** 2 + (xx - 80) ** 2)
    img[dist < 50, :3] = (240, 140, 20)
    return img


def test_dtf_film_is_clean():
    result = make_dtf(badge_on_black(), get_product("dtf-coeur"),
                      DtfOptions(garment="#000000", remove_background="no"))
    alpha = result.artwork[:, :, 3]
    assert set(np.unique(alpha)) <= {0, 255}                 # hard edges
    color, white = alpha > 0, result.white > 0
    assert color.any() and white.any()
    assert not (white & ~color).any()                       # white never outside
    assert (color & ~white).any()                           # choked
    assert all(c["status"] == "ok" for c in result.checks)
    assert result.artwork.shape[:2] == (1181, 1181)          # 100 mm at 300 dpi
    margin = 23                                             # 2 mm
    assert not color[:margin].any() and not color[:, -margin:].any()
    assert result.pdf.startswith(b"%PDF") and result.pdf.count(b"/Type /Page ") == 2


def test_dtf_removes_opaque_background():
    img = solid(120, 120, (255, 255, 255))
    img[30:90, 30:90, :3] = (20, 60, 200)
    result = make_dtf(img, get_product("dtf-coeur"))
    assert result.artwork[0, 0, 3] == 0
    assert "fond retiré" in " ".join(result.steps)


def test_halftone_coverage_is_linear():
    alpha = np.full((200, 200), 0, np.uint8)
    for value in (64, 128, 192):
        alpha[:] = value
        coverage = halftone(alpha, 300, 35).mean()
        assert abs(coverage - value / 255) < 0.05
    alpha[:] = 255
    assert halftone(alpha, 300, 35).all()


def test_dtf_halftone_and_mirror():
    img = badge_on_black()
    img[:, :, 3] = np.clip(img[:, :, 0], 0, 255)             # soft alpha
    plain = make_dtf(img, get_product("dtf-coeur"),
                     DtfOptions(remove_background="no", edges="halftone"))
    flipped = make_dtf(img, get_product("dtf-coeur"),
                       DtfOptions(remove_background="no", edges="halftone", mirror=True))
    assert set(np.unique(plain.artwork[:, :, 3])) <= {0, 255}
    assert np.array_equal(plain.white[:, ::-1], flipped.white)
