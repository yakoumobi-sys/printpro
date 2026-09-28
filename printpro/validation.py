"""Bounded, documented request contracts for the local workshop API."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Request(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")


class AssetSettings(Request):
    width_mm: float = Field(default=100, gt=0, le=2000)
    quantity: int = Field(default=1, ge=1, le=1000)


class BackgroundRequest(Request):
    method: Literal["auto", "color", "ai"] = "auto"
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    tolerance: float = Field(default=12, ge=0, le=100)
    softness: float = Field(default=6, ge=0, le=50)
    edge_shift: float = Field(default=0, ge=-10, le=10)
    feather: float = Field(default=.6, ge=0, le=10)
    defringe: float = Field(default=1.5, ge=0, le=10)
    keep_holes: bool = True
    largest_only: bool = False
    trim: bool = False


class UpscaleRequest(Request):
    method: Literal["auto", "vector", "edge", "lanczos", "ai"] = "auto"
    scale: float = Field(default=2, ge=.1, le=20)
    target_mm: float | None = Field(default=None, gt=0, le=2000)
    dpi: int = Field(default=300, ge=72, le=600)
    denoise: float = Field(default=0, ge=0, le=1)
    sharpen: float = Field(default=.45, ge=0, le=1.5)


class VectorRequest(Request):
    colors: int = Field(default=8, ge=2, le=32)
    mode: Literal["color", "bw"] = "color"
    detail: float = Field(default=1, ge=.2, le=4)
    smoothing: float = Field(default=1, ge=0, le=2.5)
    min_area: int = Field(default=12, ge=1, le=200)
    blur: float = Field(default=0, ge=0, le=10)
    drop_background: bool = True
    polygons: bool = False
    replace: bool = True


class MontageEntry(AssetSettings):
    id: str = Field(min_length=1, max_length=64)
    height_mm: float = Field(default=0, ge=0, le=2000)
    rotatable: bool = True


class MontageRequest(Request):
    items: list[MontageEntry] = Field(default_factory=list, max_length=100)
    page: str = "A4"
    page_width_mm: float = Field(default=0, ge=0, le=2000)
    page_height_mm: float = Field(default=0, ge=0, le=5000)
    orientation: Literal["auto", "portrait", "landscape"] = "auto"
    dpi: int = Field(default=300, ge=72, le=600)
    margin_mm: float = Field(default=6, ge=0, le=100)
    spacing_mm: float = Field(default=3, ge=0, le=100)
    layout: Literal["pack", "grid", "fill"] = "pack"
    columns: int = Field(default=0, ge=0, le=100)
    mirror: bool = False
    background: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    crop_marks: bool = False
    registration_marks: bool = False
    cut_contour_mm: float = Field(default=0, ge=0, le=12)
    cut_stroke: bool = False
    outline: bool = False
    auto_trim: bool = True
    jpeg_quality: int = Field(default=0, ge=0, le=100)
    preview_only: bool = False
