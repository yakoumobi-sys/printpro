import json

import pytest

from printpro.cli import main
from printpro.imaging import load_rgba, save_image


@pytest.fixture
def source(tmp_path, disc_on_white):
    image, _ = disc_on_white
    path = tmp_path / "logo.png"
    save_image(image, path)
    return path


def test_formats_lists_presets(capsys):
    assert main(["formats"]) == 0
    assert "A4" in capsys.readouterr().out


def test_check_reports_resolution(source, capsys):
    assert main(["controler", str(source), "--mm", "200"]) == 0
    out = capsys.readouterr().out
    assert "dpi" in out and "agrandir" in out


def test_check_json(source, capsys):
    assert main(["check", str(source), "--mm", "50", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["needed_px"][0] == 591


def test_detour_writes_transparent_png(tmp_path, source):
    out = tmp_path / "cut.png"
    assert main(["detourer", str(source), "-o", str(out), "--tolerance", "12"]) == 0
    assert (load_rgba(out)[:, :, 3] == 0).any()


def test_upscale_to_physical_size(tmp_path, source):
    out = tmp_path / "big.png"
    assert main(["agrandir", str(source), "-o", str(out), "--mm", "50",
                 "--dpi", "300", "--methode", "lanczos"]) == 0
    assert load_rgba(out).shape[1] == 591


def test_vectorize_writes_svg_and_preview(tmp_path, source):
    svg = tmp_path / "out.svg"
    preview = tmp_path / "preview.png"
    assert main(["vectoriser", str(source), "-o", str(svg), "--couleurs", "3",
                 "--apercu", str(preview)]) == 0
    assert svg.read_text().startswith("<svg")
    assert preview.exists()


def test_prepare_chains_steps(tmp_path, source):
    out = tmp_path / "prep.png"
    assert main(["preparer", str(source), "-o", str(out), "--detourer",
                 "--facteur", "2", "--methode", "lanczos", "--vectoriser",
                 "--rogner"]) == 0
    assert out.exists() and out.with_suffix(".svg").exists()


def test_montage_creates_sheets_and_pdf(tmp_path, source):
    out = tmp_path / "planches"
    assert main(["montage", str(source), "-o", str(out), "--largeur", "40",
                 "--quantite", "8", "--dpi", "100", "--reperes"]) == 0
    assert (out / "planche-01.png").exists()
    assert (out / "planche.pdf").read_bytes().startswith(b"%PDF")


def test_montage_item_syntax(tmp_path, source):
    out = tmp_path / "p2"
    assert main(["montage", "--item", f"{source}:w=30:q=5", "-o", str(out),
                 "--dpi", "100", "--sans-pdf"]) == 0
    assert (out / "planche-01.png").exists()
    assert not (out / "planche.pdf").exists()


def test_montage_custom_page_size(tmp_path, source):
    out = tmp_path / "p3"
    assert main(["montage", str(source), "-o", str(out), "--page", "150x150",
                 "--largeur", "40", "--dpi", "72"]) == 0
    from printpro.imaging import load_rgba as load
    assert load(out / "planche-01.png").shape[0] == 425      # 150 mm at 72 dpi


def test_montage_without_source_fails(tmp_path, capsys):
    assert main(["montage", "-o", str(tmp_path)]) == 2
