from printpro.bgremove import BgOptions
from printpro.pipeline import prepare, prepare_file, quality_check, quick_montage
from printpro.upscale import UpscaleOptions
from printpro.vectorize import VectorOptions


def test_full_pipeline_runs_every_step(disc_on_white):
    image, _ = disc_on_white
    result = prepare(image, remove_bg=True, bg_options=BgOptions(tolerance=12),
                     upscale_options=UpscaleOptions(scale=2, method="lanczos"),
                     vector_options=VectorOptions(colors=3), auto_trim=True)
    names = [step.name for step in result.steps]
    assert names == ["détourage", "rognage", "agrandissement", "vectorisation"]
    assert result.svg.startswith("<svg")
    assert result.info["size_px"][0] > image.shape[1]      # trimmed then doubled
    assert "détourage" in result.report()


def test_prepare_file_writes_png_and_svg(tmp_path, disc_on_white):
    from printpro.imaging import save_image
    image, _ = disc_on_white
    source = tmp_path / "src.png"
    save_image(image, source)
    out = tmp_path / "out.png"
    prepare_file(source, out, remove_bg=True,
                 vector_options=VectorOptions(colors=3))
    assert out.exists()
    assert out.with_suffix(".svg").read_text().startswith("<svg")


def test_quality_check_recommends_a_factor(disc_on_white):
    image, _ = disc_on_white
    report = quality_check(image, 200, dpi=300)
    assert report["grade"] == "faible"
    assert report["upscale_factor"] > 10


def test_quick_montage_exports(tmp_path, disc_on_white):
    image, _ = disc_on_white
    result, sheets, pdf = quick_montage([image], 50, 4, out_dir=tmp_path)
    assert result.placed == 4
    assert sheets and sheets[0].exists()
    assert pdf.exists()
