"""Web interface for PrintPro (FastAPI).

The server keeps a workspace on disk: every uploaded file becomes an *asset*
with a version history, so each operation (detour, upscale, trace) can be
undone.  The heavy lifting is done by the very same functions the CLI uses.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import math
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import List
from pathlib import Path
from urllib.parse import quote

from PIL import Image

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile, Query
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, Response)
from fastapi.staticfiles import StaticFiles

from .bgremove import BgOptions, ai_available, remove_background
from .cmyk import CmykOptions
from .dtf import DtfOptions, make_dtf
from .imaging import (aspect_fit, encode, hex_to_rgb, load_rgba, px_to_mm,
                      resize, save_image)
from .montage import (PAGE_PRESETS, MontageItem, MontageOptions, build_montage,
                      export_pdf, export_sheets, render_page)
from .pipeline import quality_check
from .prepress import PressOptions, inspect_artwork, make_press_ready
from .products import PRODUCTS, resolve_product
from .upscale import UpscaleOptions, esrgan_available, upscale
from .vectorize import VectorOptions, vectorize

from .validation import (AssetSettings, BackgroundRequest, UpscaleRequest,
                         VectorRequest, MontageRequest)

STATIC_DIR = Path(__file__).parent / "static"
THUMB_MAX = 420


def default_workspace() -> Path:
    """Where uploads and outputs live: ``PRINTPRO_WORKSPACE``, else
    ``/tmp`` on serverless hosts (read-only code directory), else
    ``./workspace``."""
    if os.environ.get("PRINTPRO_WORKSPACE"):
        return Path(os.environ["PRINTPRO_WORKSPACE"])
    if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return Path("/tmp/printpro-workspace")
    return Path("workspace")


def clean_name(filename: str | None) -> str:
    """Display name of an upload: no path, no markup characters, bounded."""
    name = Path(str(filename or "").replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f\x7f<>\"'`&]", "", name).strip()[:120]
    return name or "image"


def file_stem(name: str) -> str:
    """Safe stem for files written to disk and served back."""
    stem = re.sub(r"[^\w.-]+", "-", Path(name).stem, flags=re.UNICODE).strip(".-")
    return stem[:60] or "visuel"


def color(body: dict, key: str, default: str | None) -> str | None:
    """A ``#rrggbb`` field of a JSON body (or the default), 400 if malformed."""
    value = body.get(key) or default
    if value is None:
        return None
    try:
        hex_to_rgb(str(value))
    except ValueError:
        raise HTTPException(400, f"{key} : couleur #rrggbb attendue")
    return str(value)


def choice(body: dict, key: str, default: str, allowed: tuple[str, ...]) -> str:
    value = str(body.get(key) or default)
    if value not in allowed:
        raise HTTPException(400, f"{key} : une valeur parmi {', '.join(allowed)}")
    return value


def number(body: dict, key: str, default: float, lo: float, hi: float) -> float:
    """Read a numeric field of a JSON body, 400 if missing the bounds."""
    value = body.get(key, default)
    try:
        value = float(default if value is None else value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key} : nombre attendu")
    if not math.isfinite(value) or not lo <= value <= hi:
        raise HTTPException(400, f"{key} : valeur hors limites ({lo:g} à {hi:g})")
    return value


@dataclass
class Asset:
    id: str
    name: str
    versions: list[Path] = field(default_factory=list)
    svg: str | None = None
    history: list[str] = field(default_factory=list)
    width_mm: float = 100.0
    quantity: int = 1

    @property
    def path(self) -> Path:
        return self.versions[-1]


class Workspace:
    """Asset store shared by all requests (single-user, local tool)."""

    def __init__(self, root: str | Path = "workspace"):
        self.root = Path(root)
        self.assets_dir = self.root / "assets"
        self.out_dir = self.root / "sorties"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.assets: dict[str, Asset] = {}
        self.info: dict[str, dict] = {}
        self.manifest = self.root / "atelier.json"
        if self.manifest.exists():
            for record in json.loads(self.manifest.read_text(encoding="utf-8")):
                record["versions"] = [self.assets_dir / name for name in record["versions"]]
                if record["versions"] and all(p.is_file() and p.resolve().is_relative_to(self.assets_dir.resolve()) for p in record["versions"]):
                    self.assets[record["id"]] = Asset(**record)

    def save(self) -> None:
        """Replace the manifest atomically; never truncate the last saved session."""
        records = [dict(id=a.id, name=a.name, versions=[p.name for p in a.versions],
                        svg=a.svg, history=a.history, width_mm=a.width_mm,
                        quantity=a.quantity) for a in self.assets.values()]
        temporary = self.manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.manifest)

    # -- assets ---------------------------------------------------------- #
    def add(self, name: str, data: bytes) -> Asset:
        asset_id = uuid.uuid4().hex[:12]
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("image limitée à 40 mégapixels")
        image = load_rgba(data)
        path = self.assets_dir / f"{asset_id}-v0.png"
        save_image(image, path)
        asset = Asset(id=asset_id, name=Path(name.replace("\\", "/")).name, versions=[path],
                      history=["import"])
        h, w = image.shape[:2]
        asset.width_mm = max(0.1, min(2000, round(px_to_mm(w, 300), 1)))
        self.assets[asset_id] = asset
        self.save()
        return asset

    def get(self, asset_id: str) -> Asset:
        if asset_id not in self.assets:
            raise KeyError(asset_id)
        return self.assets[asset_id]

    def push_version(self, asset: Asset, image: np.ndarray, label: str) -> None:
        path = self.assets_dir / f"{asset.id}-v{len(asset.versions)}.png"
        save_image(image, path)
        asset.versions.append(path)
        asset.history.append(label)
        asset.svg = None
        self.save()

    def undo(self, asset: Asset) -> bool:
        if len(asset.versions) <= 1:
            return False
        removed = asset.versions.pop()
        asset.history.pop()
        removed.unlink(missing_ok=True)
        asset.svg = None
        self.info.pop(str(removed), None)
        self.save()
        return True

    def describe(self, asset: Asset) -> dict:
        key = str(asset.path)
        if key not in self.info:
            image = load_rgba(asset.path)
            self.info[key] = dict(width=int(image.shape[1]), height=int(image.shape[0]),
                                  transparent=bool((image[:, :, 3] < 250).any()))
        info = self.info[key]
        w, h = info["width"], info["height"]
        return {
            "id": asset.id,
            "name": asset.name,
            "width": int(w),
            "height": int(h),
            "versions": len(asset.versions),
            "history": asset.history,
            "has_svg": bool(asset.svg),
            "width_mm": asset.width_mm,
            "quantity": asset.quantity,
            "transparent": info["transparent"],
            "native_mm": [round(px_to_mm(w, 300), 1), round(px_to_mm(h, 300), 1)],
        }


def create_app(workspace: str | Path = "workspace") -> FastAPI:
    store = Workspace(workspace)
    app = FastAPI(title="PrintPro", docs_url="/api/docs")
    lock = asyncio.Lock()

    @app.middleware("http")
    async def serialize_workspace(request, call_next):
        # One local workshop: mutations and reads must see a consistent version.
        if request.url.path.startswith("/api/"):
            async with lock:
                response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid_operation(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    def asset_or_404(asset_id: str) -> Asset:
        try:
            return store.get(asset_id)
        except KeyError:
            raise HTTPException(404, "visuel introuvable")

    def payload(asset: Asset, extra: dict | None = None) -> dict:
        data = store.describe(asset)
        data["preview"] = f"/api/assets/{asset.id}/preview?v={len(asset.versions)}"
        if extra:
            data.update(extra)
        return data

    # -- pages ----------------------------------------------------------- #
    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/atelier", response_class=HTMLResponse)
    def workshop() -> str:
        return (STATIC_DIR / "atelier.html").read_text(encoding="utf-8")

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/api/capabilities")
    def capabilities() -> dict:
        return {
            "pages": {name: list(size) for name, size in PAGE_PRESETS.items()},
            "ai_background": ai_available(),
            "ai_upscale": esrgan_available(),
        }

    # -- assets ---------------------------------------------------------- #
    @app.post("/api/assets")
    async def upload(files: List[UploadFile] = File(...)) -> JSONResponse:
        created = []
        for upload_file in files:
            data = await upload_file.read(25 * 1024 * 1024 + 1)
            if len(data) > 25 * 1024 * 1024:
                raise HTTPException(413, "Chaque fichier est limité à 25 Mo.")
            if not data:
                continue
            try:
                asset = store.add(clean_name(upload_file.filename), data)
            except Exception as error:
                raise HTTPException(400, f"fichier illisible : {error}")
            created.append(payload(asset))
        if not created:
            raise HTTPException(400, "aucun fichier valide")
        return JSONResponse(created)

    @app.get("/api/assets")
    def list_assets() -> List[dict]:
        return [payload(asset) for asset in store.assets.values()]

    @app.get("/api/assets/{asset_id}/preview")
    def preview(asset_id: str, full: int = 0, original: bool = False) -> Response:
        asset = asset_or_404(asset_id)
        image = load_rgba(asset.versions[0] if original else asset.path)
        if not full:
            h, w = image.shape[:2]
            image = resize(image, *aspect_fit(w, h, THUMB_MAX, THUMB_MAX))
        return Response(encode(image, "PNG"), media_type="image/png")

    @app.delete("/api/assets/{asset_id}")
    def delete_asset(asset_id: str) -> dict:
        asset = asset_or_404(asset_id)
        for path in asset.versions:
            path.unlink(missing_ok=True)
        store.assets.pop(asset_id, None)
        store.info.clear()
        store.save()
        return {"ok": True}

    @app.post("/api/assets/{asset_id}/undo")
    def undo(asset_id: str) -> dict:
        asset = asset_or_404(asset_id)
        if not store.undo(asset):
            raise HTTPException(400, "aucune version précédente")
        return payload(asset)

    @app.post("/api/assets/{asset_id}/settings")
    def settings(asset_id: str, body: AssetSettings) -> dict:
        body = body.model_dump(exclude_unset=True)
        asset = asset_or_404(asset_id)
        if "width_mm" in body:
            asset.width_mm = float(body["width_mm"])
        if "quantity" in body:
            asset.quantity = max(1, int(body["quantity"]))
        store.save()
        return payload(asset)

    # -- operations ------------------------------------------------------ #
    @app.post("/api/assets/{asset_id}/background")
    def background(asset_id: str, body: BackgroundRequest) -> dict:
        body = body.model_dump(exclude_unset=True)
        asset = asset_or_404(asset_id)
        options = BgOptions(
            method=body.get("method", "auto"),
            color=body.get("color") or None,
            tolerance=float(body.get("tolerance", 12)),
            softness=float(body.get("softness", 6)),
            edge_shift=float(body.get("edge_shift", 0)),
            feather=float(body.get("feather", 0.6)),
            defringe=float(body.get("defringe", 1.5)),
            keep_holes=bool(body.get("keep_holes", True)),
            largest_only=bool(body.get("largest_only", False)),
            post_trim=bool(body.get("trim", False)))
        started = time.perf_counter()
        result = remove_background(load_rgba(asset.path), options)
        store.push_version(asset, result.image, f"détourage ({result.engine})")
        return payload(asset, {
            "engine": result.engine,
            "coverage": round(result.coverage * 100, 1),
            "background_colors": [list(c) for c in result.background_colors],
            "seconds": round(time.perf_counter() - started, 2)})

    @app.post("/api/assets/{asset_id}/upscale")
    def upscale_asset(asset_id: str, body: UpscaleRequest) -> dict:
        body = body.model_dump(exclude_unset=True)
        asset = asset_or_404(asset_id)
        image = load_rgba(asset.path)
        options = UpscaleOptions(
            scale=float(body.get("scale", 2)),
            method=body.get("method", "auto"),
            denoise=float(body.get("denoise", 0)),
            sharpen=float(body.get("sharpen", 0.45)), max_pixels=40_000_000)
        if body.get("target_mm"):
            h, w = image.shape[:2]
            mm = float(body["target_mm"])
            options.target_mm = (mm, mm * h / max(w, 1))
            options.target_dpi = int(body.get("dpi", 300))
        started = time.perf_counter()
        result = upscale(image, options)
        store.push_version(asset, result.image,
                           f"agrandissement ×{result.factor:.2f} ({result.method})")
        return payload(asset, {"method": result.method,
                               "factor": round(result.factor, 2),
                               "notes": result.notes,
                               "seconds": round(time.perf_counter() - started, 2)})

    @app.post("/api/assets/{asset_id}/vectorize")
    def vectorize_asset(asset_id: str, body: VectorRequest) -> dict:
        body = body.model_dump(exclude_unset=True)
        from .raster import render_layers
        asset = asset_or_404(asset_id)
        options = VectorOptions(
            colors=int(body.get("colors", 8)),
            mode=body.get("mode", "color"),
            detail=float(body.get("detail", 1.0)),
            smoothing=float(body.get("smoothing", 1.0)),
            min_area=int(body.get("min_area", 12)),
            blur=float(body.get("blur", 0)),
            drop_background=bool(body.get("drop_background", True)),
            curves=not bool(body.get("polygons", False)))
        started = time.perf_counter()
        image = load_rgba(asset.path)
        result = vectorize(image, options)
        if body.get("replace", True):
            preview_image = render_layers(result.layers, result.width,
                                          result.height, scale=1.0)
            store.push_version(asset, preview_image,
                               f"vectorisation ({result.shapes} formes)")
        asset.svg = result.svg
        store.save()
        return payload(asset, {
            "shapes": result.shapes, "nodes": result.nodes,
            "palette": [list(c) for c in result.palette],
            "svg_bytes": len(result.svg.encode("utf-8")),
            "seconds": round(time.perf_counter() - started, 2)})

    @app.get("/api/assets/{asset_id}/svg")
    def download_svg(asset_id: str) -> Response:
        asset = asset_or_404(asset_id)
        if not asset.svg:
            raise HTTPException(404, "ce visuel n'a pas encore été vectorisé")
        name = Path(asset.name).stem + ".svg"
        return PlainTextResponse(
            asset.svg, media_type="image/svg+xml",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.get("/api/assets/{asset_id}/download")
    def download_asset(asset_id: str) -> Response:
        asset = asset_or_404(asset_id)
        name = Path(asset.name).stem + "-printpro.png"
        image = load_rgba(asset.path)
        effective_dpi = image.shape[1] * 25.4 / asset.width_mm
        return Response(encode(image, "PNG", dpi=effective_dpi), media_type="image/png",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})

    @app.get("/api/assets/{asset_id}/quality")
    def quality(asset_id: str, mm: float = Query(100.0, gt=0, le=2000), dpi: int = Query(300, ge=72, le=600)) -> dict:
        asset = asset_or_404(asset_id)
        report = quality_check(load_rgba(asset.path), mm, None, dpi)
        report["resampled"] = any("agrandissement" in label for label in asset.history)
        return report

    # -- prêt à imprimer ------------------------------------------------- #
    def product_or_400(body: dict):
        try:
            return resolve_product(str(body.get("product", "flyer-a5")),
                                   bleed_mm=body.get("bleed_mm"),
                                   dpi=body.get("dpi"))
        except (KeyError, ValueError) as error:
            raise HTTPException(400, str(error).strip("'\""))

    def output_url(job: str, path: Path) -> str:
        return f"/api/sorties/{job}/{quote(path.name)}"

    def save_thumb(image, path: Path, size: int = 900) -> None:
        h, w = image.shape[:2]
        save_image(resize(image, *aspect_fit(w, h, size, size)), path)

    @app.get("/api/products")
    def products() -> List[dict]:
        return [p.as_dict() for p in PRODUCTS.values()]

    @app.post("/api/assets/{asset_id}/inspect")
    def inspect(asset_id: str, body: dict) -> dict:
        asset = asset_or_404(asset_id)
        return inspect_artwork(load_rgba(asset.path), product_or_400(body),
                               choice(body, "orientation", "auto",
                                      ("auto", "portrait", "landscape")))

    @app.post("/api/assets/{asset_id}/print")
    def press_ready(asset_id: str, body: dict) -> dict:
        asset = asset_or_404(asset_id)
        product = product_or_400(body)
        if product.family == "dtf":
            raise HTTPException(400, "produit DTF : utilisez /dtf")
        options = PressOptions(
            fit=choice(body, "fit", "cover", ("cover", "contain")),
            bleed_mode=choice(body, "bleed_mode", "mirror", ("mirror", "edge", "scale")),
            orientation=choice(body, "orientation", "auto",
                               ("auto", "portrait", "landscape")),
            crop_marks=bool(body.get("crop_marks", True)),
            background=color(body, "background", "#ffffff"),
            cmyk=CmykOptions(tac=number(body, "tac", 300, 150, 400)),
            title=file_stem(asset.name))
        try:
            result = make_press_ready(load_rgba(asset.path), product, options)
        except ValueError as error:
            raise HTTPException(400, str(error))
        job = uuid.uuid4().hex[:10]
        job_dir = store.out_dir / job
        job_dir.mkdir(parents=True, exist_ok=True)
        stem = f"{file_stem(asset.name)}-{product.key}"
        pdf = job_dir / f"{stem}.pdf"
        pdf.write_bytes(result.pdf)
        save_thumb(result.proof(), job_dir / "epreuve.png")
        report = {
            "job": job,
            "product": product.as_dict(),
            "file": pdf.name,
            "pdf": output_url(job, pdf),
            "proof": f"/api/sorties/{job}/epreuve.png",
            "specs": result.specs,
            "steps": result.steps,
            "preflight": result.preflight.as_dict()}
        (job_dir / "rapport.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    @app.post("/api/assets/{asset_id}/dtf")
    def dtf(asset_id: str, body: dict) -> dict:
        asset = asset_or_404(asset_id)
        product = product_or_400({"product": "dtf-a4", **body})
        preview_color = color(body, "preview_garment", None)
        options = DtfOptions(
            remove_background=choice(body, "remove_background", "auto",
                                     ("auto", "yes", "no")),
            garment=color(body, "garment", None),
            garment_tolerance=number(body, "garment_tolerance", 14, 0, 100),
            edges=choice(body, "edges", "threshold", ("threshold", "halftone")),
            halftone_lpi=number(body, "lpi", 35, 5, 150),
            choke_mm=number(body, "choke_mm", 0.25, 0, 3),
            margin_mm=number(body, "margin_mm", 2, 0, 50),
            mirror=bool(body.get("mirror", False)))
        try:
            result = make_dtf(load_rgba(asset.path), product, options)
        except ValueError as error:
            raise HTTPException(400, str(error))
        job = uuid.uuid4().hex[:10]
        job_dir = store.out_dir / job
        stem = f"{file_stem(asset.name)}-{product.key}"
        film = save_image(result.artwork, job_dir / f"{stem}.png", dpi=product.dpi)
        white = save_image(result.white_plate_rgba(), job_dir / f"{stem}-blanc.png",
                           dpi=product.dpi)
        pdf = job_dir / f"{stem}.pdf"
        pdf.write_bytes(result.pdf)
        save_thumb(result.preview(preview_color or options.garment or "#2b2b2b"),
                   job_dir / "epreuve.png")
        report = {
            "job": job,
            "product": product.as_dict(),
            "file": film.name,
            "png": output_url(job, film),
            "white": output_url(job, white),
            "pdf": output_url(job, pdf),
            "proof": f"/api/sorties/{job}/epreuve.png",
            "specs": result.specs,
            "steps": result.steps,
            "preflight": {"passed": all(c["status"] != "fail" for c in result.checks),
                          "count": len(result.checks),
                          "ok": sum(c["status"] == "ok" for c in result.checks),
                          "checks": result.checks}}
        (job_dir / "rapport.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

    # -- montage --------------------------------------------------------- #
    @app.post("/api/montage")
    def montage(body: MontageRequest) -> dict:
        body = body.model_dump(exclude_unset=True)
        entries = body.get("items") or []
        if not entries:
            raise HTTPException(400, "aucun visuel sélectionné")
        if sum(entry.get("quantity", store.get(entry["id"]).quantity) for entry in entries if entry["id"] in store.assets) > 2000:
            raise HTTPException(400, "Maximum 2 000 exemplaires par montage.")
        items: list[MontageItem] = []
        for entry in entries:
            asset = asset_or_404(entry["id"])
            items.append(MontageItem(
                image=load_rgba(asset.path),
                width_mm=float(entry.get("width_mm") or asset.width_mm),
                height_mm=float(entry.get("height_mm") or 0),
                quantity=int(entry.get("quantity") or asset.quantity),
                rotatable=bool(entry.get("rotatable", True)),
                label=asset.name))

        options = MontageOptions(
            page=body.get("page", "A4"),
            page_width_mm=float(body.get("page_width_mm") or 0),
            page_height_mm=float(body.get("page_height_mm") or 0),
            orientation=body.get("orientation", "auto"),
            dpi=int(body.get("dpi", 300)),
            margin_mm=float(body.get("margin_mm", 6)),
            spacing_mm=float(body.get("spacing_mm", 3)),
            layout=body.get("layout", "pack"),
            columns=int(body.get("columns", 0)),
            mirror=bool(body.get("mirror", False)),
            background=body.get("background") or None,
            crop_marks=bool(body.get("crop_marks", False)),
            registration_marks=bool(body.get("registration_marks", False)),
            cut_contour_mm=float(body.get("cut_contour_mm", 0)),
            cut_stroke=bool(body.get("cut_stroke", False)),
            outline=bool(body.get("outline", False)),
            auto_trim=bool(body.get("auto_trim", True)), max_pages=20)

        started = time.perf_counter()
        result = build_montage(items, options)
        if not result.placed:
            raise HTTPException(400, "Aucun visuel ne tient sur la planche. " + " ; ".join(result.warnings))
        preflight = []
        for item in result.items:
            width, height = item.size_mm()
            report = quality_check(item.image, width, height, options.dpi)
            if report["dpi"] < options.dpi:
                preflight.append(f"{item.label} : {report['dpi']} dpi effectifs (cible {options.dpi}).")
        result.warnings.extend(preflight)
        if body.get("preview_only"):
            summary = result.summary()
            preview_dpi = min(72, 700 * 25.4 / max(result.page_size_mm))
            summary["previews"] = ["data:image/png;base64," + base64.b64encode(
                encode(render_page(result, i, dpi=preview_dpi), "PNG")).decode("ascii")
                for i in range(result.page_count)]
            summary["seconds"] = round(time.perf_counter() - started, 2)
            return summary
        pw, ph = result.page_size_mm
        pixels = round(pw / 25.4 * options.dpi) * round(ph / 25.4 * options.dpi)
        if pixels > 100_000_000 or pixels * result.page_count > 250_000_000:
            raise HTTPException(400, "Export trop volumineux. Réduisez la résolution, la longueur ou les quantités ; l’aperçu reste disponible.")
        job = uuid.uuid4().hex[:10]
        job_dir = store.out_dir / job
        sheets = export_sheets(result, job_dir)
        pdf = export_pdf(result, job_dir / "planche.pdf",
                         jpeg_quality=int(body.get("jpeg_quality", 0)))

        previews = []
        for index, sheet in enumerate(sheets):
            image = load_rgba(sheet)
            h, w = image.shape[:2]
            thumb = resize(image, *aspect_fit(w, h, 700, 700))
            thumb_path = job_dir / f"apercu-{index + 1:02d}.png"
            save_image(thumb, thumb_path)
            previews.append(f"/api/sorties/{job}/{thumb_path.name}")

        summary = result.summary()
        summary.update({
            "job": job,
            "previews": previews,
            "sheets": [f"/api/sorties/{job}/{p.name}" for p in sheets],
            "pdf": f"/api/sorties/{job}/{pdf.name}",
            "seconds": round(time.perf_counter() - started, 2)})
        (job_dir / "rapport.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary

    @app.get("/api/sorties/{job}/{name}")
    def download_output(job: str, name: str) -> Response:
        root = store.out_dir.resolve()
        path = (root / job / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "fichier introuvable")
        media = {".pdf": "application/pdf", ".json": "application/json"}.get(
            path.suffix, "image/png")
        return FileResponse(path, media_type=media, filename=path.name)

    @app.post("/api/workspace/clear")
    def clear() -> dict:
        shutil.rmtree(store.assets_dir, ignore_errors=True)
        shutil.rmtree(store.out_dir, ignore_errors=True)
        store.assets_dir.mkdir(parents=True, exist_ok=True)
        store.out_dir.mkdir(parents=True, exist_ok=True)
        store.assets.clear()
        store.info.clear()
        store.save()
        return {"ok": True}

    return app


def serve(host: str = "127.0.0.1", port: int = 8000,
          workspace: str | Path = "workspace") -> None:  # pragma: no cover
    import uvicorn
    print(f"PrintPro → http://{host}:{port}")
    uvicorn.run(create_app(workspace), host=host, port=port, log_level="info")
