"""Preflight: the structural checks a print shop runs before accepting a PDF.

The checker reads the PDF back from bytes — it does not trust what the
generator *meant* to write — and reports nine checks.  It understands the
uncompressed object syntax PrintPro writes (classic xref table, direct
``/Length``), which is all it is used for: verifying our own output before
the user downloads it.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import asdict, dataclass, field

import numpy as np

from .imaging import MM_PER_INCH
from .products import Product

PT_PER_MM = 72.0 / MM_PER_INCH
_NUM = r"(-?\d+(?:\.\d+)?)"


@dataclass
class Check:
    key: str
    label: str
    status: str                  # "ok" | "warn" | "fail"
    detail: str

    @property
    def ok(self) -> bool:
        return self.status != "fail"


@dataclass
class PreflightReport:
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, key: str, label: str, status: str, detail: str) -> None:
        self.checks.append(Check(key, label, status, detail))

    def as_dict(self) -> dict:
        return {"passed": self.passed,
                "count": len(self.checks),
                "ok": sum(c.status == "ok" for c in self.checks),
                "checks": [asdict(c) for c in self.checks]}

    def text(self) -> str:
        marks = {"ok": "✔", "warn": "!", "fail": "✘"}
        return "\n".join(f"  {marks[c.status]} {c.label} — {c.detail}"
                         for c in self.checks)


# --------------------------------------------------------------------------- #
# Minimal PDF reader
# --------------------------------------------------------------------------- #
@dataclass
class _Obj:
    num: int
    head: str                    # dictionary / array text (latin-1)
    stream: bytes | None = None

    def decoded(self) -> bytes | None:
        if self.stream is None:
            return None
        if "/FlateDecode" in self.head:
            return zlib.decompress(self.stream)
        return self.stream


def _read_objects(data: bytes) -> tuple[dict[int, _Obj], str]:
    tail = data[-1024:]
    match = re.search(rb"startxref\s+(\d+)", tail)
    if not match:
        raise ValueError("PDF sans table xref")
    xref = int(match.group(1))
    trailer_at = data.index(b"trailer", xref)
    trailer = data[trailer_at:trailer_at + 512].decode("latin-1")
    lines = data[xref:trailer_at].split(b"\n")
    start, count = (int(v) for v in lines[1].split())
    objects: dict[int, _Obj] = {}
    for index in range(1, count):
        entry = lines[2 + index - start]
        offset, _, kind = entry.split()[:3]
        if kind != b"n":
            continue
        pos = int(offset)
        header_end = data.index(b"obj", pos) + 3
        body_at = header_end + 1
        stream_at = data.find(b"stream\n", body_at)
        end_at = data.find(b"endobj", body_at)
        if 0 <= stream_at < end_at:
            head = data[body_at:stream_at].decode("latin-1")
            length = int(re.search(r"/Length (\d+)", head).group(1))
            raw = data[stream_at + 7:stream_at + 7 + length]
            objects[index] = _Obj(index, head, raw)
        else:
            objects[index] = _Obj(index, data[body_at:end_at].decode("latin-1"))
    return objects, trailer


def _ref(text: str, key: str) -> int | None:
    match = re.search(rf"/{key} (\d+) 0 R", text)
    return int(match.group(1)) if match else None


def _box(text: str, key: str) -> tuple[float, ...] | None:
    match = re.search(rf"/{key} \[{_NUM} {_NUM} {_NUM} {_NUM}\]", text)
    return tuple(float(v) for v in match.groups()) if match else None


def _string(text: str, key: str) -> str | None:
    match = re.search(rf"/{key} \(((?:\\.|[^\\)])*)\)", text)
    return re.sub(r"\\(.)", r"\1", match.group(1)) if match else None


def _mm(box) -> tuple[float, float]:
    return (box[2] - box[0]) / PT_PER_MM, (box[3] - box[1]) / PT_PER_MM


# --------------------------------------------------------------------------- #
def preflight_pdf(data: bytes, product: Product | None = None,
                  tac_limit: float = 300.0,
                  min_dpi: float | None = None) -> PreflightReport:
    """Run the nine checks on PDF *data*."""
    report = PreflightReport()
    objects, trailer = _read_objects(data)
    info = objects.get(_ref(trailer, "Info") or -1, _Obj(0, "")).head
    catalog = objects.get(_ref(trailer, "Root") or -1, _Obj(0, "")).head
    pages = [o for o in objects.values() if "/Type /Page " in o.head]
    images = {o.num: o for o in objects.values() if "/Subtype /Image" in o.head}
    min_dpi = min_dpi or (product.dpi if product else 300)

    # 1 — standard ----------------------------------------------------------
    version = data[5:8].decode("latin-1")
    pdfx = _string(info, "GTS_PDFXVersion")
    conformance = _string(info, "GTS_PDFXConformance")
    has_id = "/ID [" in trailer
    fonts = [o for o in objects.values() if "/Type /Font" in o.head
             and "/FontFile" not in o.head]
    if pdfx == "PDF/X-1:2001" and conformance == "PDF/X-1a:2001" \
            and version <= "1.4" and has_id and not fonts:
        report.add("standard", "Norme PDF/X-1a:2001", "ok",
                   f"PDF {version}, ISO 15930-1, identifiant de document présent")
    else:
        missing = []
        if pdfx != "PDF/X-1:2001" or conformance != "PDF/X-1a:2001":
            missing.append("clés GTS_PDFX absentes")
        if version > "1.4":
            missing.append(f"version PDF {version} > 1.4")
        if not has_id:
            missing.append("pas d'/ID")
        if fonts:
            missing.append("police non incorporée")
        report.add("standard", "Norme PDF/X-1a:2001", "fail", ", ".join(missing))

    # 2 — output intent ------------------------------------------------------
    intent_ref = re.search(r"/OutputIntents \[(\d+) 0 R", catalog)
    intent = objects[int(intent_ref.group(1))].head if intent_ref else ""
    condition = _string(intent, "OutputConditionIdentifier")
    if "/S /GTS_PDFX" in intent and condition:
        embedded = "profil ICC incorporé" if "/DestOutputProfile" in intent \
            else "condition enregistrée (registre ICC)"
        report.add("intent", "Intention de sortie", "ok",
                   f"{_string(intent, 'Info') or condition} — {embedded}")
    else:
        report.add("intent", "Intention de sortie", "fail",
                   "aucune OutputIntent GTS_PDFX")

    # 3 / 4 — boxes -----------------------------------------------------------
    trim_problems, bleed_problems, bleed_detail = [], [], ""
    trims = []
    for page in pages:
        trim = _box(page.head, "TrimBox")
        bleed = _box(page.head, "BleedBox") or trim
        if trim is None:
            trim_problems.append("TrimBox manquante")
            continue
        trims.append(trim)
        tw, th = _mm(trim)
        if product:
            wanted = sorted((product.width_mm, product.height_mm))
            if abs(sorted((tw, th))[0] - wanted[0]) > 0.2 or \
                    abs(sorted((tw, th))[1] - wanted[1]) > 0.2:
                trim_problems.append(f"{tw:.1f} × {th:.1f} mm au lieu de "
                                     f"{product.width_mm:g} × {product.height_mm:g}")
        sides = [(trim[0] - bleed[0]), (trim[1] - bleed[1]),
                 (bleed[2] - trim[2]), (bleed[3] - trim[3])]
        side_mm = min(sides) / PT_PER_MM
        need = product.bleed_mm if product else 0.0
        bleed_detail = f"+ {side_mm:.1f} mm sur chaque bord"
        if side_mm + 0.05 < need:
            bleed_problems.append(f"{side_mm:.1f} mm < {need:g} mm demandés")
        covered = _image_cover(page, objects)
        if need > 0 and covered is not None and not _contains(covered, bleed):
            bleed_problems.append("l'image ne couvre pas tout le fond perdu")
    if trims and not trim_problems:
        tw, th = _mm(trims[0])
        report.add("trim", "Format fini (TrimBox)", "ok",
                   f"{tw:.1f} × {th:.1f} mm" + (f" — {product.label}" if product else ""))
    else:
        report.add("trim", "Format fini (TrimBox)", "fail",
                   "; ".join(trim_problems) or "aucune page")
    if bleed_problems:
        report.add("bleed", "Fond perdu (BleedBox)", "fail", "; ".join(bleed_problems))
    elif product and product.bleed_mm == 0:
        report.add("bleed", "Fond perdu (BleedBox)", "ok", "non requis pour ce produit")
    else:
        report.add("bleed", "Fond perdu (BleedBox)", "ok",
                   bleed_detail + ", image jusqu'au bord")

    # 5 — resolution ---------------------------------------------------------
    worst = None
    for page in pages:
        for ppi in _placements_ppi(page, objects, images):
            worst = ppi if worst is None else min(worst, ppi)
    if worst is None:
        report.add("dpi", "Résolution effective", "fail", "aucune image placée")
    elif worst + 0.5 >= min_dpi * 0.99:
        report.add("dpi", "Résolution effective", "ok",
                   f"{worst:.0f} dpi à la taille d'impression (min. {min_dpi})")
    else:
        report.add("dpi", "Résolution effective", "fail",
                   f"{worst:.0f} dpi < {min_dpi} dpi")

    # 6 — colour spaces ------------------------------------------------------
    contents = [objects[n].decoded() or b"" for page in pages
                for n in [_ref(page.head, "Contents")] if n]
    rgb_objects = [o for o in objects.values()
                   if re.search(r"/(DeviceRGB|CalRGB|Lab)\b", o.head)]
    rgb_ops = any(re.search(rb"(^|\s)(rg|RG)\s", c) for c in contents)
    if rgb_objects or rgb_ops:
        report.add("cmyk", "Couleurs CMJN uniquement", "fail",
                   f"{len(rgb_objects)} objet(s) RVB" + (", tracés RVB" if rgb_ops else ""))
    else:
        report.add("cmyk", "Couleurs CMJN uniquement", "ok",
                   "DeviceCMYK + couleur de repérage, aucune donnée RVB")

    # 7 — total area coverage -----------------------------------------------
    max_tac = 0.0
    for obj in images.values():
        if "/DeviceCMYK" in obj.head:
            raw = np.frombuffer(obj.decoded(), np.uint8).reshape(-1, 4)
            for chunk in range(0, raw.shape[0], 2_000_000):
                part = raw[chunk:chunk + 2_000_000].astype(np.uint16).sum(axis=1)
                max_tac = max(max_tac, float(part.max()) * 100.0 / 255.0)
    if max_tac <= tac_limit + 0.5:
        report.add("tac", f"Encrage total ≤ {tac_limit:g} %", "ok",
                   f"maximum mesuré : {max_tac:.0f} %")
    else:
        report.add("tac", f"Encrage total ≤ {tac_limit:g} %", "fail",
                   f"maximum mesuré : {max_tac:.0f} %")

    # 8 — transparency -------------------------------------------------------
    transparent = [o for o in objects.values()
                   if re.search(r"/SMask \d|/(CA|ca) 0?\.\d+|/S /Transparency", o.head)]
    if transparent:
        report.add("transparency", "Aucune transparence", "fail",
                   f"{len(transparent)} objet(s) transparent(s)")
    else:
        report.add("transparency", "Aucune transparence", "ok",
                   "tout est aplati, rien à interpréter par le RIP")

    # 9 — crop marks ---------------------------------------------------------
    marks, intruding = 0, 0
    for page, content in zip(pages, contents):
        bleed = _box(page.head, "BleedBox") or _box(page.head, "TrimBox")
        for x1, y1, x2, y2 in re.findall(
                (_NUM + r" " + _NUM + r" m " + _NUM + r" " + _NUM + r" l S")
                .encode(), content):
            marks += 1
            seg = [float(v) for v in (x1, y1, x2, y2)]
            if bleed and _segment_inside(seg, bleed):
                intruding += 1
    if marks and not intruding:
        report.add("marks", "Traits de coupe", "ok",
                   f"{marks} traits en couleur de repérage, hors fond perdu")
    elif intruding:
        report.add("marks", "Traits de coupe", "fail",
                   f"{intruding} trait(s) empiètent sur la zone imprimée")
    else:
        report.add("marks", "Traits de coupe", "warn",
                   "absents — l'imprimeur coupera sur la TrimBox")
    return report


# --------------------------------------------------------------------------- #
_PLACE = re.compile((r"q " + _NUM + r" 0 0 " + _NUM + r" " + _NUM + r" " + _NUM +
                     r" cm /(\w+) Do Q").encode())


def _xobjects(page: _Obj) -> dict[str, int]:
    block = re.search(r"/XObject << (.*?) >>", page.head)
    if not block:
        return {}
    return {name: int(num) for name, num in
            re.findall(r"/(\w+) (\d+) 0 R", block.group(1))}


def _placements(page: _Obj, objects):
    content = objects[_ref(page.head, "Contents")].decoded() or b""
    names = _xobjects(page)
    for w, h, x, y, name in _PLACE.findall(content):
        yield (float(w), float(h), float(x), float(y),
               names.get(name.decode("latin-1")))


def _placements_ppi(page, objects, images):
    for w, h, _, _, num in _placements(page, objects):
        image = images.get(num)
        if image is None or w <= 0 or h <= 0:
            continue
        px_w = int(re.search(r"/Width (\d+)", image.head).group(1))
        px_h = int(re.search(r"/Height (\d+)", image.head).group(1))
        yield min(px_w / (w / 72.0), px_h / (h / 72.0))


def _image_cover(page, objects):
    boxes = [(x, y, x + w, y + h) for w, h, x, y, _ in _placements(page, objects)]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _contains(outer, inner, tol: float = 0.05) -> bool:
    return (outer[0] <= inner[0] + tol and outer[1] <= inner[1] + tol and
            outer[2] >= inner[2] - tol and outer[3] >= inner[3] - tol)


def _segment_inside(seg, box, tol: float = 0.01) -> bool:
    """Does the segment enter the box interior (beyond touching)?"""
    x1, y1, x2, y2 = seg
    lo_x, hi_x = min(x1, x2), max(x1, x2)
    lo_y, hi_y = min(y1, y2), max(y1, y2)
    return (hi_x > box[0] + tol and lo_x < box[2] - tol and
            hi_y > box[1] + tol and lo_y < box[3] - tol)
