"""Cloud AI engines, with the Replicate API replaced by local fakes."""

import io

import numpy as np
import pytest

from printpro import ai
from printpro.bgremove import BgOptions, ai_available, remove_background
from printpro.imaging import encode, load_rgba
from printpro.raster import render_layers
from printpro.upscale import UpscaleOptions, esrgan_available, upscale

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 280 280"
     width="280" height="280">
  <path fill="#ffffff" d="M0 0H280V280H0Z"/>
  <g transform="translate(140 140)">
    <path style="fill:rgb(200,30,40)"
          d="M-90 0A90 90 0 1 0 90 0A90 90 0 1 0 -90 0Z"/>
  </g>
  <path fill="#0a5ac8" d="m120 120 c10 0 40 0 40 0 l0 40 q-20 0 -40 0 z"/>
  <path fill="none" stroke="#000" d="M0 0L10 10"/>
</svg>"""


@pytest.fixture
def fake_replicate(monkeypatch):
    """Answer like the three default models, recording the calls made."""
    calls = []

    def run(model_ref, inputs):
        calls.append((model_ref, inputs))
        image = load_rgba(ai._download(inputs["image"]))
        if model_ref == ai.DEFAULT_MODELS["bg"]:
            out = image.copy()
            red = (image[:, :, 0] > 150) & (image[:, :, 1] < 100)
            out[:, :, 3] = np.where(red, 255, 0)
            return "data:image/png;base64," + _b64(encode(out, "PNG"))
        if model_ref == ai.DEFAULT_MODELS["upscale"]:
            f = int(inputs["upscale_factor"][0])
            big = np.repeat(np.repeat(image, f, 0), f, 1)
            return "data:image/png;base64," + _b64(encode(big[:, :, :3], "PNG"))
        if model_ref == ai.DEFAULT_MODELS["vector"]:
            return "data:image/svg+xml;base64," + _b64(SVG.encode())
        raise AssertionError(model_ref)

    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_test")
    monkeypatch.setattr(ai, "run", run)
    return calls


def _b64(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode()


def test_disabled_without_token(monkeypatch):
    monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
    assert not ai.configured()
    with pytest.raises(ai.AIError, match="REPLICATE_API_TOKEN"):
        ai.run("bria/remove-background", {})


def test_capabilities_follow_token(fake_replicate):
    assert ai_available() and esrgan_available()


def test_ai_background_uses_model_mask(fake_replicate, disc_on_white):
    image, mask = disc_on_white
    result = remove_background(image, BgOptions(method="ai", feather=0))
    assert result.engine == "ai"
    assert fake_replicate[0][0] == "bria/remove-background"
    assert result.image[:, :, 3][mask].mean() > 250
    assert result.image[0, 0, 3] == 0


def test_ai_failure_is_reported_not_hidden(monkeypatch, disc_on_white):
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_test")

    def broken(model_ref, inputs):
        raise ai.AIError("crédit Replicate épuisé")
    monkeypatch.setattr(ai, "run", broken)
    with pytest.raises(ai.AIError, match="crédit"):
        remove_background(disc_on_white[0], BgOptions(method="ai"))


def test_ai_upscale_hits_exact_size_and_keeps_alpha(fake_replicate, sticker):
    result = upscale(sticker, UpscaleOptions(method="ai", scale=3))
    assert result.method == "ai"
    assert result.image.shape[:2] == (600, 300)
    _, inputs = fake_replicate[0]
    assert inputs["upscale_factor"] == "4x"            # smallest factor >= 3
    assert result.image[0, 0, 3] == 0                  # transparent corner kept
    assert result.image[300, 150, 3] == 255


def test_ai_vectorize_parses_svg_into_layers(fake_replicate, disc_on_white):
    image, mask = disc_on_white
    result = ai.vectorize(image)
    assert len(result.layers) == 3                     # stroke-only path skipped
    assert (200, 30, 40) in result.palette
    assert 'width="140" height="140"' in result.svg
    rendered = render_layers(result.layers, result.width, result.height,
                             curves=False)
    assert (rendered[70, 35, :3] == (200, 30, 40)).all()   # disc, scaled ½
    assert (rendered[70, 70, :3] == (10, 90, 200)).all()   # square on top
    assert (rendered[5, 5, :3] == (255, 255, 255)).all()


def test_small_images_are_enlarged_for_the_tracer(fake_replicate, sticker):
    ai.vectorize(sticker)
    sent = load_rgba(ai._download(fake_replicate[0][1]["image"]))
    assert min(sent.shape[:2]) >= 256


def test_server_ai_vectorize(fake_replicate, tmp_path, disc_on_white):
    from fastapi.testclient import TestClient
    from printpro.server import create_app
    client = TestClient(create_app(tmp_path / "ws"))
    assert client.get("/api/capabilities").json()["ai_vectorize"] is True
    asset = client.post("/api/assets", files={"files": (
        "logo.png", io.BytesIO(encode(disc_on_white[0], "PNG")), "image/png")}).json()[0]
    result = client.post(f"/api/assets/{asset['id']}/vectorize",
                         json={"engine": "ai"}).json()
    assert result["engine"] == "ai" and result["shapes"] == 3
    assert "<svg" in client.get(f"/api/assets/{asset['id']}/svg").text


def test_server_reports_ai_errors(monkeypatch, tmp_path, disc_on_white):
    from fastapi.testclient import TestClient
    from printpro.server import create_app
    monkeypatch.setenv("REPLICATE_API_TOKEN", "r8_test")
    monkeypatch.setattr(ai, "run", lambda *a: (_ for _ in ()).throw(
        ai.AIError("jeton Replicate refusé")))
    client = TestClient(create_app(tmp_path / "ws"))
    asset = client.post("/api/assets", files={"files": (
        "logo.png", io.BytesIO(encode(disc_on_white[0], "PNG")), "image/png")}).json()[0]
    response = client.post(f"/api/assets/{asset['id']}/background",
                           json={"method": "ai"})
    assert response.status_code == 400
    assert "jeton" in response.json()["detail"]
