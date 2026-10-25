"""Minimal, dependency-free PDF writer for print-ready sheets.

Supports what a montage and a press-ready file need and nothing more:
multi-page documents, RGB or CMYK images (Flate or DCT encoded) with an
optional soft mask for transparency, vector lines for crop marks (optionally
in the *All* registration colour), a filled page background, TrimBox /
BleedBox, and the PDF/X-1a:2001 identification (output intent, version keys,
document ID).  Coordinates are
given in millimetres with the origin at the top-left corner, which matches the
rest of PrintPro; the writer converts to PDF points internally.
"""

from __future__ import annotations

import hashlib
import io
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .imaging import MM_PER_INCH, to_pil

PT_PER_MM = 72.0 / MM_PER_INCH


@dataclass
class PlacedImage:
    image: np.ndarray          # RGBA
    x_mm: float                # left
    y_mm: float                # top
    w_mm: float
    h_mm: float
    rotate: int = 0            # 0 or 90, applied when rendering
    colorspace: str = "rgb"    # "rgb" (RGBA array) or "cmyk" (CMYK array)


@dataclass
class Line:
    x1_mm: float
    y1_mm: float
    x2_mm: float
    y2_mm: float
    width_mm: float = 0.2
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    dash: tuple[float, float] | None = None
    registration: bool = False  # stroke in /All: shows on every plate


@dataclass
class Page:
    width_mm: float
    height_mm: float
    images: list[PlacedImage] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    background: tuple[int, int, int] | None = None
    # (x, y, w, h) in mm, top-left origin like everything else
    trim_box: tuple[float, float, float, float] | None = None
    bleed_box: tuple[float, float, float, float] | None = None


@dataclass
class PdfX:
    """PDF/X-1a:2001 output intent (ISO 15930-1)."""
    condition: str = "FOGRA39"
    info: str = "Coated FOGRA39 (ISO 12647-2:2004)"
    registry: str = "http://www.color.org"
    profile: bytes | None = None          # embedded as DestOutputProfile


class PdfWriter:
    """Assemble pages then ``save()`` / ``getvalue()``."""

    def __init__(self, title: str = "PrintPro", jpeg_quality: int = 0,
                 pdfx: PdfX | None = None):
        self.pages: list[Page] = []
        self.title = title
        self.jpeg_quality = jpeg_quality      # 0 = lossless Flate
        self.pdfx = pdfx                      # None = plain PDF 1.5

    def add_page(self, page: Page) -> None:
        self.pages.append(page)

    # ------------------------------------------------------------------ #
    def getvalue(self) -> bytes:
        objects: list[bytes] = []

        def add_object(payload: bytes) -> int:
            objects.append(payload)
            return len(objects)            # 1-based object numbers

        # PDF/X forbids non-embedded fonts; nothing draws text anyway.
        font_id = None if self.pdfx else add_object(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        all_id = None                         # /Separation /All colour space
        page_ids: list[int] = []
        pages_id_placeholder = len(objects) + 1
        # Reserve the /Pages object slot so kids can reference it.
        objects.append(b"")
        pages_id = pages_id_placeholder

        image_objects: dict[tuple, int] = {}
        for page in self.pages:
            resources: list[str] = []
            content = io.StringIO()
            if page.background and self.pdfx:
                r, g, b = (c / 255.0 for c in page.background)
                content.write(f"0 0 0 {1.0 - (r + g + b) / 3.0:.4f} k\n")
                content.write(f"0 0 {page.width_mm * PT_PER_MM:.3f} "
                              f"{page.height_mm * PT_PER_MM:.3f} re f\n")
            elif page.background:
                r, g, b = (c / 255.0 for c in page.background)
                content.write(f"{r:.4f} {g:.4f} {b:.4f} rg\n")
                content.write(f"0 0 {page.width_mm * PT_PER_MM:.3f} "
                              f"{page.height_mm * PT_PER_MM:.3f} re f\n")

            for index, placed in enumerate(page.images):
                name = f"Im{index}"
                # Reused artwork is compressed and embedded once across pages.
                key = (id(placed.image), placed.colorspace)
                if key not in image_objects:
                    image_objects[key] = self._write_image(placed.image, add_object,
                                                           placed.colorspace)
                img_id = image_objects[key]
                resources.append(f"/{name} {img_id} 0 R")
                x = placed.x_mm * PT_PER_MM
                w = placed.w_mm * PT_PER_MM
                h = placed.h_mm * PT_PER_MM
                # PDF origin is bottom-left; ours is top-left.
                y = (page.height_mm - placed.y_mm - placed.h_mm) * PT_PER_MM
                content.write(f"q {w:.3f} 0 0 {h:.3f} {x:.3f} {y:.3f} cm "
                              f"/{name} Do Q\n")

            for line in page.lines:
                if line.registration:
                    if all_id is None:
                        all_id = add_object(
                            b"[/Separation /All /DeviceCMYK << /FunctionType 2 "
                            b"/Domain [0 1] /C0 [0 0 0 0] /C1 [1 1 1 1] /N 1 >>]")
                    content.write("q /CSAll CS 1 SCN ")
                else:
                    r, g, b = line.color
                    if self.pdfx:   # no RGB allowed: plain K of the same weight
                        content.write(f"q 0 0 0 {1.0 - (r + g + b) / 3.0:.3f} K ")
                    else:
                        content.write(f"q {r:.3f} {g:.3f} {b:.3f} RG ")
                content.write(f"{line.width_mm * PT_PER_MM:.3f} w\n")
                if line.dash:
                    on, off = (d * PT_PER_MM for d in line.dash)
                    content.write(f"[{on:.2f} {off:.2f}] 0 d\n")
                x1 = line.x1_mm * PT_PER_MM
                y1 = (page.height_mm - line.y1_mm) * PT_PER_MM
                x2 = line.x2_mm * PT_PER_MM
                y2 = (page.height_mm - line.y2_mm) * PT_PER_MM
                content.write(f"{x1:.3f} {y1:.3f} m {x2:.3f} {y2:.3f} l S Q\n")

            stream = content.getvalue().encode("latin-1")
            packed = zlib.compress(stream, 6)
            content_id = add_object(
                b"<< /Length " + str(len(packed)).encode() +
                b" /Filter /FlateDecode >>\nstream\n" + packed + b"\nendstream")

            xobjects = ("/XObject << " + " ".join(resources) + " >> ") if resources else ""
            fonts = f"/Font << /F1 {font_id} 0 R >> " if font_id else ""
            spaces = f"/ColorSpace << /CSAll {all_id} 0 R >> " if all_id else ""
            boxes = ""
            for key, box in (("TrimBox", page.trim_box), ("BleedBox", page.bleed_box)):
                if box:
                    boxes += f"/{key} {_box(page, box)} "
            if self.pdfx and not page.trim_box:
                boxes += f"/TrimBox {_box(page, (0, 0, page.width_mm, page.height_mm))} "
            page_dict = (f"<< /Type /Page /Parent {pages_id} 0 R "
                         f"/MediaBox [0 0 {page.width_mm * PT_PER_MM:.3f} "
                         f"{page.height_mm * PT_PER_MM:.3f}] {boxes}"
                         f"/Resources << {xobjects}{fonts}{spaces}>> "
                         f"/Contents {content_id} 0 R >>")
            page_ids.append(add_object(page_dict.encode("latin-1")))

        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        objects[pages_id - 1] = (f"<< /Type /Pages /Count {len(page_ids)} "
                                 f"/Kids [{kids}] >>").encode("latin-1")
        if not self.pdfx:
            catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>"
                                    .encode("latin-1"))
            info_id = add_object(("<< /Producer (PrintPro) /Title (" +
                                  _escape(self.title) + ") >>").encode("latin-1"))
            return _serialize(objects, catalog_id, info_id)

        x = self.pdfx
        intent = (f"<< /Type /OutputIntent /S /GTS_PDFX "
                  f"/OutputConditionIdentifier ({_escape(x.condition)}) "
                  f"/OutputCondition ({_escape(x.info)}) "
                  f"/RegistryName ({_escape(x.registry)}) "
                  f"/Info ({_escape(x.info)})")
        if x.profile:
            packed = zlib.compress(x.profile, 6)
            profile_id = add_object(
                f"<< /N 4 /Filter /FlateDecode /Length {len(packed)} >>\nstream\n"
                .encode("latin-1") + packed + b"\nendstream")
            intent += f" /DestOutputProfile {profile_id} 0 R"
        intent_id = add_object((intent + " >>").encode("latin-1"))
        catalog_id = add_object(
            f"<< /Type /Catalog /Pages {pages_id} 0 R "
            f"/OutputIntents [{intent_id} 0 R] >>".encode("latin-1"))
        stamp = time.strftime("D:%Y%m%d%H%M%SZ", time.gmtime())
        info_id = add_object((
            f"<< /Title ({_escape(self.title)}) /Creator (PrintPro) "
            f"/Producer (PrintPro) /CreationDate ({stamp}) /ModDate ({stamp}) "
            f"/Trapped /False /GTS_PDFXVersion (PDF/X-1:2001) "
            f"/GTS_PDFXConformance (PDF/X-1a:2001) >>").encode("latin-1"))
        # File identifier, not a security feature (ISO 32000 suggests MD5).
        ident = hashlib.md5(stamp.encode(), usedforsecurity=False)
        for payload in objects[:8]:
            ident.update(payload)
        digest = ident.hexdigest()
        return _serialize(objects, catalog_id, info_id, version="1.3",
                          doc_id=digest)

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.getvalue())
        return path

    # ------------------------------------------------------------------ #
    def _write_image(self, rgba: np.ndarray, add_object,
                     colorspace: str = "rgb") -> int:
        rgba = np.asarray(rgba, dtype=np.uint8)
        h, w = rgba.shape[:2]
        if colorspace == "cmyk":
            return self._write_cmyk(rgba, add_object)
        if self.pdfx:
            raise ValueError("PDF/X-1a : seules les images CMJN sont admises")
        rgb = rgba[:, :, :3]
        alpha = rgba[:, :, 3] if rgba.shape[2] == 4 else None

        smask_id = None
        if alpha is not None and (alpha < 255).any():
            packed = zlib.compress(alpha.tobytes(), 6)
            smask_id = add_object(
                (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                 f"/ColorSpace /DeviceGray /BitsPerComponent 8 "
                 f"/Filter /FlateDecode /Length {len(packed)} >>\nstream\n"
                 ).encode("latin-1") + packed + b"\nendstream")

        if self.jpeg_quality:
            buf = io.BytesIO()
            to_pil(rgb).save(buf, format="JPEG", quality=int(self.jpeg_quality),
                             subsampling=0, optimize=True)
            data, filt = buf.getvalue(), "/DCTDecode"
        else:
            data, filt = zlib.compress(np.ascontiguousarray(rgb).tobytes(), 6), \
                "/FlateDecode"

        extra = f" /SMask {smask_id} 0 R" if smask_id else ""
        return self._image_object(add_object, w, h, "/DeviceRGB", data, filt, extra)

    def _write_cmyk(self, cmyk: np.ndarray, add_object) -> int:
        # Always lossless: CMYK JPEG inversion conventions differ between RIPs.
        h, w = cmyk.shape[:2]
        data = zlib.compress(np.ascontiguousarray(cmyk).tobytes(), 6)
        return self._image_object(add_object, w, h, "/DeviceCMYK", data,
                                  "/FlateDecode", "")

    @staticmethod
    def _image_object(add_object, w, h, space, data, filt, extra) -> int:
        header = (f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} "
                  f"/ColorSpace {space} /BitsPerComponent 8 /Filter {filt} "
                  f"/Length {len(data)}{extra} >>\nstream\n").encode("latin-1")
        return add_object(header + data + b"\nendstream")


def _escape(text: str) -> str:
    return (text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            .encode("latin-1", "replace").decode("latin-1"))


def _box(page: Page, box: tuple[float, float, float, float]) -> str:
    x, y, w, h = box
    llx, lly = x * PT_PER_MM, (page.height_mm - y - h) * PT_PER_MM
    return f"[{llx:.3f} {lly:.3f} {llx + w * PT_PER_MM:.3f} {lly + h * PT_PER_MM:.3f}]"


def _serialize(objects: list[bytes], catalog_id: int, info_id: int,
               version: str = "1.5", doc_id: str | None = None) -> bytes:
    out = io.BytesIO()
    out.write(f"%PDF-{version}\n".encode("latin-1") + b"%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, payload in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode("latin-1"))
        out.write(payload)
        out.write(b"\nendobj\n")
    xref_pos = out.tell()
    count = len(objects) + 1
    out.write(f"xref\n0 {count}\n".encode("latin-1"))
    out.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    ident = f" /ID [<{doc_id}> <{doc_id}>]" if doc_id else ""
    out.write(f"trailer\n<< /Size {count} /Root {catalog_id} 0 R "
              f"/Info {info_id} 0 R{ident} >>\nstartxref\n{xref_pos}\n%%EOF\n"
              .encode("latin-1"))
    return out.getvalue()
