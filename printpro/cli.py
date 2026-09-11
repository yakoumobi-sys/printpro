"""PrintPro command line interface (French facing, English internals)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bgremove import BgOptions, ai_available
from .imaging import human_size, load_rgba, save_image
from .montage import (PAGE_PRESETS, MontageItem, MontageOptions, build_montage,
                      export_pdf, export_sheets)
from .pipeline import prepare, quality_check
from .upscale import UpscaleOptions, esrgan_available
from .vectorize import VectorOptions

BANNER = "PrintPro — préparation de fichiers pour l'impression"


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="printpro", description=BANNER)
    sub = parser.add_subparsers(dest="command", required=True)

    # -- détourage ---------------------------------------------------------- #
    p = sub.add_parser("detourer", aliases=["bg"],
                       help="supprimer l'arrière-plan")
    p.add_argument("source")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--methode", default="auto", choices=["auto", "color", "ai"])
    p.add_argument("--couleur", help="couleur de fond à retirer, ex. #ffffff")
    p.add_argument("--tolerance", type=float, default=12.0)
    p.add_argument("--douceur", type=float, default=6.0,
                   help="largeur de la transition (Delta-E)")
    p.add_argument("--contour", type=float, default=0.0,
                   help="décalage du bord en px (négatif = rétrécir)")
    p.add_argument("--adoucir", type=float, default=0.6, help="flou du masque, px")
    p.add_argument("--defrange", type=float, default=1.5, dest="defringe",
                   help="nettoyage du liseré de fond sur le bord, en px")
    p.add_argument("--garder-plus-grand", action="store_true")
    p.add_argument("--boucher-trous", action="store_true",
                   help="ne pas rendre transparents les fonds intérieurs")
    p.add_argument("--rogner", action="store_true")

    # -- agrandissement ----------------------------------------------------- #
    p = sub.add_parser("agrandir", aliases=["upscale"],
                       help="agrandir une image pour l'impression")
    p.add_argument("source")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--facteur", type=float, default=2.0)
    p.add_argument("--largeur", type=int, help="largeur cible en px")
    p.add_argument("--hauteur", type=int, help="hauteur cible en px")
    p.add_argument("--mm", type=float, help="largeur physique cible en mm")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--methode", default="auto",
                   choices=["auto", "lanczos", "edge", "vector", "ai"])
    p.add_argument("--debruiter", type=float, default=0.0)
    p.add_argument("--nettete", type=float, default=0.45)

    # -- vectorisation ------------------------------------------------------ #
    p = sub.add_parser("vectoriser", aliases=["vector"],
                       help="convertir un bitmap en SVG")
    p.add_argument("source")
    p.add_argument("-o", "--output", required=True, help="fichier .svg")
    p.add_argument("--couleurs", type=int, default=8)
    p.add_argument("--mode", default="color", choices=["color", "bw"])
    p.add_argument("--detail", type=float, default=1.0,
                   help="tolérance en px (plus bas = plus fidèle)")
    p.add_argument("--lissage", type=float, default=1.0)
    p.add_argument("--aire-min", type=int, default=12)
    p.add_argument("--flou", type=float, default=0.0)
    p.add_argument("--nettoyage-bords", type=float, default=0.6,
                   help="absorbe les liserés d'anti-crénelage (0 = off)")
    p.add_argument("--garder-fond", action="store_true")
    p.add_argument("--polygones", action="store_true",
                   help="pas de courbes de Bézier")
    p.add_argument("--apercu", help="PNG de contrôle rendu depuis le vecteur")

    # -- préparation complète ----------------------------------------------- #
    p = sub.add_parser("preparer", aliases=["prep"],
                       help="détourer + agrandir + vectoriser en une passe")
    p.add_argument("source")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--detourer", action="store_true")
    p.add_argument("--tolerance", type=float, default=12.0)
    p.add_argument("--mm", type=float, help="largeur physique cible en mm")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--facteur", type=float)
    p.add_argument("--methode", default="auto",
                   choices=["auto", "lanczos", "edge", "vector", "ai"])
    p.add_argument("--vectoriser", action="store_true")
    p.add_argument("--couleurs", type=int, default=8)
    p.add_argument("--rogner", action="store_true")

    # -- montage ------------------------------------------------------------ #
    p = sub.add_parser("montage", aliases=["planche"],
                       help="placer les visuels sur une planche imprimable")
    p.add_argument("sources", nargs="*", help="images à placer")
    p.add_argument("--item", action="append", default=[],
                   metavar="CHEMIN:w=40:h=0:q=10:rot=1",
                   help="élément détaillé (répétable)")
    p.add_argument("-o", "--output", default="out", help="dossier de sortie")
    p.add_argument("--page", default="A4",
                   help=f"format ({', '.join(PAGE_PRESETS)}) ou LxH en mm")
    p.add_argument("--orientation", default="auto",
                   choices=["auto", "portrait", "landscape"])
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--largeur", type=float, default=0.0,
                   help="largeur de chaque visuel en mm")
    p.add_argument("--quantite", type=int, default=1)
    p.add_argument("--disposition", default="pack",
                   choices=["pack", "grid", "fill"])
    p.add_argument("--colonnes", type=int, default=0)
    p.add_argument("--marge", type=float, default=6.0)
    p.add_argument("--espacement", type=float, default=3.0)
    p.add_argument("--fond", default="#ffffff",
                   help="couleur de fond, ou 'aucun' pour transparent")
    p.add_argument("--miroir", action="store_true",
                   help="inverser (flocage / sublimation)")
    p.add_argument("--reperes", action="store_true", help="traits de coupe")
    p.add_argument("--reperes-calage", action="store_true")
    p.add_argument("--contour-decoupe", type=float, default=0.0,
                   help="marge de découpe autour de chaque sticker, en mm")
    p.add_argument("--trait-decoupe", action="store_true")
    p.add_argument("--cadres", action="store_true")
    p.add_argument("--sans-pdf", action="store_true")
    p.add_argument("--jpeg", type=int, default=0,
                   help="qualité JPEG dans le PDF (0 = sans perte)")

    # -- contrôle ----------------------------------------------------------- #
    p = sub.add_parser("controler", aliases=["check"],
                       help="vérifier la résolution pour une taille d'impression")
    p.add_argument("source")
    p.add_argument("--mm", type=float, required=True, help="largeur d'impression")
    p.add_argument("--hauteur-mm", type=float)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--json", action="store_true")

    # -- divers ------------------------------------------------------------- #
    sub.add_parser("formats", help="lister les formats de page")
    p = sub.add_parser("serve", help="lancer l'interface web")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--workspace", default="workspace")
    return parser


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command

    if command in ("detourer", "bg"):
        return _cmd_bg(args)
    if command in ("agrandir", "upscale"):
        return _cmd_upscale(args)
    if command in ("vectoriser", "vector"):
        return _cmd_vector(args)
    if command in ("preparer", "prep"):
        return _cmd_prepare(args)
    if command in ("montage", "planche"):
        return _cmd_montage(args)
    if command in ("controler", "check"):
        return _cmd_check(args)
    if command == "formats":
        return _cmd_formats()
    if command == "serve":
        from .server import serve
        serve(host=args.host, port=args.port, workspace=args.workspace)
        return 0
    return 1


def _cmd_bg(args) -> int:
    image = load_rgba(args.source)
    options = BgOptions(method=args.methode, color=args.couleur,
                        tolerance=args.tolerance, softness=args.douceur,
                        edge_shift=args.contour, feather=args.adoucir,
                        defringe=args.defringe,
                        keep_holes=args.boucher_trous,
                        largest_only=args.garder_plus_grand,
                        post_trim=args.rogner)
    if args.couleur and args.methode == "auto":
        options.method = "color"
    if options.method == "ai" and not ai_available():
        print("! rembg n'est pas installé, repli sur le détourage colorimétrique",
              file=sys.stderr)
    result = prepare(image, remove_bg=True, bg_options=options)
    save_image(result.image, args.output)
    print(f"{BANNER}\n{result.report()}\n→ {args.output} "
          f"({human_size(Path(args.output).stat().st_size)})")
    return 0


def _cmd_upscale(args) -> int:
    image = load_rgba(args.source)
    options = UpscaleOptions(scale=args.facteur, method=args.methode,
                             denoise=args.debruiter, sharpen=args.nettete,
                             target_width=args.largeur, target_height=args.hauteur)
    if args.mm:
        h, w = image.shape[:2]
        options.target_mm = (args.mm, args.mm * h / max(w, 1))
        options.target_dpi = args.dpi
    if args.methode == "ai" and not esrgan_available():
        print("! Real-ESRGAN introuvable, repli sur 'edge'", file=sys.stderr)
    result = prepare(image, upscale_options=options)
    save_image(result.image, args.output, dpi=args.dpi)
    print(f"{BANNER}\n{result.report()}\n→ {args.output} "
          f"({human_size(Path(args.output).stat().st_size)})")
    return 0


def _cmd_vector(args) -> int:
    image = load_rgba(args.source)
    options = VectorOptions(colors=args.couleurs, mode=args.mode,
                            detail=args.detail, smoothing=args.lissage,
                            min_area=args.aire_min, blur=args.flou,
                            edge_cleanup=args.nettoyage_bords,
                            drop_background=not args.garder_fond,
                            curves=not args.polygones)
    result = prepare(image, vector_options=options)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.svg or "", encoding="utf-8")
    print(f"{BANNER}\n{result.report()}\n→ {out} ({human_size(out.stat().st_size)})")

    if args.apercu:
        from .raster import render_layers
        from .vectorize import vectorize
        traced = vectorize(image, options)
        preview = render_layers(traced.layers, traced.width, traced.height,
                                scale=1.0, background=(255, 255, 255))
        save_image(preview, args.apercu)
        print(f"→ aperçu : {args.apercu}")
    return 0


def _cmd_prepare(args) -> int:
    image = load_rgba(args.source)
    up = None
    if args.mm or args.facteur:
        up = UpscaleOptions(method=args.methode)
        if args.mm:
            h, w = image.shape[:2]
            up.target_mm = (args.mm, args.mm * h / max(w, 1))
            up.target_dpi = args.dpi
        else:
            up.scale = args.facteur
    vector = VectorOptions(colors=args.couleurs) if args.vectoriser else None
    result = prepare(image, remove_bg=args.detourer,
                     bg_options=BgOptions(tolerance=args.tolerance),
                     upscale_options=up, vector_options=vector,
                     auto_trim=args.rogner)
    out = Path(args.output)
    if result.svg and out.suffix.lower() == ".svg":
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(result.svg, encoding="utf-8")
    else:
        save_image(result.image, out, dpi=args.dpi)
        if result.svg:
            out.with_suffix(".svg").write_text(result.svg, encoding="utf-8")
            print(f"→ {out.with_suffix('.svg')}")
    print(f"{BANNER}\n{result.report()}\n→ {out}")
    return 0


def _parse_item(spec: str, default_width: float, default_qty: int) -> MontageItem:
    parts = spec.split(":")
    path = parts[0]
    width = default_width
    height = 0.0
    qty = default_qty
    rot = True
    for token in parts[1:]:
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        key = key.strip().lower()
        if key in ("w", "largeur"):
            width = float(value)
        elif key in ("h", "hauteur"):
            height = float(value)
        elif key in ("q", "n", "qte", "quantite"):
            qty = int(value)
        elif key in ("rot", "rotation"):
            rot = value not in ("0", "non", "false")
    return MontageItem(image=load_rgba(path), width_mm=width, height_mm=height,
                       quantity=qty, rotatable=rot, label=Path(path).name)


def _cmd_montage(args) -> int:
    items = [_parse_item(spec, args.largeur, args.quantite) for spec in args.item]
    items += [MontageItem(image=load_rgba(src), width_mm=args.largeur,
                          quantity=args.quantite, label=Path(src).name)
              for src in args.sources]
    if not items:
        print("aucune image fournie", file=sys.stderr)
        return 2

    options = MontageOptions(
        page=args.page, orientation=args.orientation, dpi=args.dpi,
        margin_mm=args.marge, spacing_mm=args.espacement,
        layout=args.disposition, columns=args.colonnes, mirror=args.miroir,
        background=None if args.fond in ("aucun", "none", "") else args.fond,
        crop_marks=args.reperes, registration_marks=args.reperes_calage,
        cut_contour_mm=args.contour_decoupe, cut_stroke=args.trait_decoupe,
        outline=args.cadres)
    if "x" in args.page.lower() and args.page not in PAGE_PRESETS:
        try:
            w, h = (float(v) for v in args.page.lower().split("x"))
            options.page_width_mm, options.page_height_mm = w, h
        except ValueError:
            print(f"format de page inconnu : {args.page}", file=sys.stderr)
            return 2

    result = build_montage(items, options)
    out_dir = Path(args.output)
    sheets = export_sheets(result, out_dir)
    print(BANNER)
    for warning in result.warnings:
        print(f"  ! {warning}")
    info = result.summary()
    print(f"  • {info['placed']}/{info['requested']} visuels placés sur "
          f"{info['pages']} planche(s) {info['page_size_mm'][0]}×"
          f"{info['page_size_mm'][1]} mm à {info['dpi']} dpi "
          f"(remplissage {info['efficiency']}%)")
    for path in sheets:
        print(f"→ {path} ({human_size(path.stat().st_size)})")
    if not args.sans_pdf:
        pdf = export_pdf(result, out_dir / "planche.pdf", jpeg_quality=args.jpeg)
        print(f"→ {pdf} ({human_size(pdf.stat().st_size)})")
    return 0


def _cmd_check(args) -> int:
    image = load_rgba(args.source)
    report = quality_check(image, args.mm, args.hauteur_mm, args.dpi)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"{BANNER}\n  source      : {args.source} "
          f"({report['current_px'][0]}×{report['current_px'][1]} px)")
    print(f"  impression  : {args.mm} mm de large à {args.dpi} dpi")
    print(f"  nécessaire  : {report['needed_px'][0]}×{report['needed_px'][1]} px")
    print(f"  résolution  : {report['dpi']} dpi — {report['label']}")
    if report["upscale_factor"] > 1.0:
        print(f"  conseil     : agrandir ×{report['upscale_factor']} "
              f"(printpro agrandir {args.source} --mm {args.mm} "
              f"--dpi {args.dpi} -o sortie.png)")
    return 0


def _cmd_formats() -> int:
    print(BANNER)
    for name, (w, h) in PAGE_PRESETS.items():
        print(f"  {name:<12} {w:>7.1f} × {h:>7.1f} mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
