import re

import numpy as np

from printpro.pdfwriter import Line, Page, PdfWriter, PlacedImage


def art(alpha=255):
    img = np.zeros((30, 20, 4), np.uint8)
    img[:, :, 0] = 220
    img[:, :, 3] = alpha
    return img


def test_structure_is_parseable():
    writer = PdfWriter("test")
    writer.add_page(Page(210, 297, [PlacedImage(art(), 10, 10, 50, 75)]))
    data = writer.getvalue()
    assert data.startswith(b"%PDF-1.5")
    assert data.rstrip().endswith(b"%%EOF")
    offset = int(re.search(rb"startxref\s+(\d+)", data).group(1))
    assert data[offset:offset + 4] == b"xref"


def test_mediabox_uses_points():
    writer = PdfWriter()
    writer.add_page(Page(210, 297))
    data = writer.getvalue().decode("latin-1")
    assert "/MediaBox [0 0 595.276 841.890]" in data


def test_alpha_generates_a_soft_mask():
    opaque = PdfWriter()
    opaque.add_page(Page(100, 100, [PlacedImage(art(255), 0, 0, 50, 50)]))
    assert b"/SMask" not in opaque.getvalue()

    translucent = PdfWriter()
    translucent.add_page(Page(100, 100, [PlacedImage(art(120), 0, 0, 50, 50)]))
    assert b"/SMask" in translucent.getvalue()


def test_multi_page_and_lines():
    writer = PdfWriter()
    writer.add_page(Page(100, 100, lines=[Line(0, 0, 10, 10, dash=(2, 2))]))
    writer.add_page(Page(50, 50))
    data = writer.getvalue()
    assert data.count(b"/Type /Page ") == 2
    assert b"/Count 2" in data


def test_jpeg_mode_shrinks_output():
    photo = np.random.default_rng(0).integers(0, 255, (200, 200, 4), dtype=np.uint8)
    photo[:, :, 3] = 255
    flate = PdfWriter()
    flate.add_page(Page(100, 100, [PlacedImage(photo, 0, 0, 90, 90)]))
    jpeg = PdfWriter(jpeg_quality=70)
    jpeg.add_page(Page(100, 100, [PlacedImage(photo, 0, 0, 90, 90)]))
    assert len(jpeg.getvalue()) < len(flate.getvalue())
    assert b"/DCTDecode" in jpeg.getvalue()


def test_save_writes_file(tmp_path):
    writer = PdfWriter()
    writer.add_page(Page(100, 100))
    path = writer.save(tmp_path / "nested" / "out.pdf")
    assert path.exists() and path.stat().st_size > 100
