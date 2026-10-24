"""Print products: the trim, bleed and resolution a print shop expects.

A *product* is what the customer orders (a flyer, a poster, a DTF transfer),
not a sheet size.  Each preset carries the specification the print shop's
preflight checks against, so the user only picks "A5 flyer" and every number
that matters follows from it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Product:
    key: str
    label: str
    family: str                 # "papier" | "dtf"
    width_mm: float             # trim (finished) size, portrait
    height_mm: float
    bleed_mm: float = 3.0       # artwork extends this far past the trim
    dpi: int = 300              # minimum effective resolution at size
    safe_mm: float = 3.0        # keep text this far inside the trim
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


PRODUCTS: dict[str, Product] = {p.key: p for p in [
    # -- papier : imprimerie offset / numérique ---------------------------- #
    Product("carte-visite", "Carte de visite 85 × 55", "papier", 85, 55,
            bleed_mm=2, safe_mm=3),
    Product("flyer-a6", "Flyer A6", "papier", 105, 148, bleed_mm=2),
    Product("flyer-a5", "Flyer A5", "papier", 148, 210, bleed_mm=2),
    Product("flyer-dl", "Flyer DL 99 × 210", "papier", 99, 210, bleed_mm=2),
    Product("depliant-a4", "Dépliant A4", "papier", 210, 297),
    Product("affiche-a3", "Affiche A3", "papier", 297, 420),
    Product("affiche-a2", "Affiche A2", "papier", 420, 594, dpi=200,
            safe_mm=5, note="200 dpi suffisent à distance de lecture"),
    Product("affiche-a1", "Affiche A1", "papier", 594, 841, dpi=150,
            safe_mm=10, note="150 dpi suffisent à distance de lecture"),
    Product("carre-20", "Carré 200 × 200", "papier", 200, 200),
    # -- DTF : transfert sur textile, sans fond perdu ---------------------- #
    Product("dtf-coeur", "DTF cœur / poitrine 100 mm", "dtf", 100, 100,
            bleed_mm=0, safe_mm=0, note="logo côté cœur"),
    Product("dtf-a4", "DTF A4 (dos enfant, devant)", "dtf", 210, 297,
            bleed_mm=0, safe_mm=0),
    Product("dtf-a3", "DTF A3 (dos adulte)", "dtf", 297, 420,
            bleed_mm=0, safe_mm=0),
    Product("dtf-manche", "DTF manche 70 × 250", "dtf", 70, 250,
            bleed_mm=0, safe_mm=0),
]}


def get_product(key: str) -> Product:
    if key not in PRODUCTS:
        raise KeyError(f"produit inconnu : {key!r} "
                       f"(disponibles : {', '.join(PRODUCTS)})")
    return PRODUCTS[key]


LIMITS = {"size_mm": (5.0, 3000.0), "bleed_mm": (0.0, 20.0), "dpi": (72, 1200)}


def _bounded(name: str, value, kind=float):
    """Parse *value* and check it against :data:`LIMITS` (ValueError if not)."""
    try:
        number = kind(float(value))
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} invalide : {value!r}") from None
    lo, hi = LIMITS[name]
    if not (lo <= number <= hi):      # also rejects NaN
        raise ValueError(f"{name} hors limites : {value!r} (de {lo:g} à {hi:g})")
    return number


def custom_product(width_mm: float, height_mm: float, bleed_mm: float = 3.0,
                   dpi: int = 300, family: str = "papier") -> Product:
    """A product for a size that is not in the catalogue (``320x450``)."""
    width_mm = _bounded("size_mm", width_mm)
    height_mm = _bounded("size_mm", height_mm)
    bleed_mm = _bounded("bleed_mm", bleed_mm)
    dpi = _bounded("dpi", dpi, int)
    return Product(f"{width_mm:g}x{height_mm:g}",
                   f"Sur mesure {width_mm:g} × {height_mm:g}", family,
                   float(width_mm), float(height_mm), bleed_mm=float(bleed_mm),
                   dpi=int(dpi), safe_mm=0 if family == "dtf" else 3.0)


def resolve_product(spec: str, bleed_mm: float | None = None,
                    dpi: int | None = None) -> Product:
    """``"affiche-a3"`` or ``"320x450"`` → :class:`Product`."""
    spec = str(spec).strip().lower()
    if bleed_mm is not None:
        bleed_mm = _bounded("bleed_mm", bleed_mm)
    if dpi is not None:
        dpi = _bounded("dpi", dpi, int)
    if spec in PRODUCTS:
        product = PRODUCTS[spec]
        if bleed_mm is None and dpi is None:
            return product
        return Product(**{**product.as_dict(),
                          "bleed_mm": product.bleed_mm if bleed_mm is None else bleed_mm,
                          "dpi": product.dpi if dpi is None else dpi})
    if "x" in spec:
        w, h = spec.split("x", 1)
        return custom_product(float(w), float(h),
                              3.0 if bleed_mm is None else bleed_mm,
                              300 if dpi is None else dpi)
    return get_product(spec)
