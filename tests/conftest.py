import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def solid(width=120, height=120, color=(255, 255, 255)):
    img = np.zeros((height, width, 4), dtype=np.uint8)
    img[:, :, :3] = color
    img[:, :, 3] = 255
    return img


@pytest.fixture
def disc_on_white():
    """A red disc centred on a white background."""
    img = solid(140, 140, (250, 250, 250))
    yy, xx = np.mgrid[0:140, 0:140]
    mask = (yy - 70) ** 2 + (xx - 70) ** 2 < 45 ** 2
    img[mask, :3] = (200, 30, 40)
    return img, mask


@pytest.fixture
def ring_on_white():
    """A red ring: one shape with one hole."""
    img = solid(160, 160, (255, 255, 255))
    yy, xx = np.mgrid[0:160, 0:160]
    dist = (yy - 80) ** 2 + (xx - 80) ** 2
    img[(dist < 60 ** 2) & (dist > 30 ** 2), :3] = (10, 90, 200)
    return img


@pytest.fixture
def sticker():
    img = np.zeros((200, 100, 4), dtype=np.uint8)
    img[20:180, 10:90, :3] = (40, 160, 90)
    img[20:180, 10:90, 3] = 255
    return img
