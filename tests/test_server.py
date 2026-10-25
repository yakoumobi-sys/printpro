import io

import pytest

from printpro.imaging import encode

fastapi_testclient = pytest.importorskip("fastapi.testclient")


@pytest.fixture
def client(tmp_path):
    from printpro.server import create_app
    return fastapi_testclient.TestClient(create_app(tmp_path / "ws"))


@pytest.fixture
def upload(client, disc_on_white):
    image, _ = disc_on_white
    response = client.post("/api/assets", files={
        "files": ("logo.png", io.BytesIO(encode(image, "PNG")), "image/png")})
    assert response.status_code == 200
    return response.json()[0]


def test_index_and_capabilities(client):
    assert "<title>PrintPro" in client.get("/").text
    caps = client.get("/api/capabilities").json()
    assert "A4" in caps["pages"]
    assert isinstance(caps["ai_background"], bool)


def test_upload_and_preview(client, upload):
    assert upload["width"] == 140 and upload["height"] == 140
    assert client.get(upload["preview"]).headers["content-type"] == "image/png"
    assert len(client.get("/api/assets").json()) == 1


def test_upload_rejects_garbage(client):
    response = client.post("/api/assets", files={
        "files": ("x.png", io.BytesIO(b"not an image"), "image/png")})
    assert response.status_code == 400


def test_background_then_undo(client, upload):
    cut = client.post(f"/api/assets/{upload['id']}/background",
                      json={"tolerance": 12, "trim": True}).json()
    assert cut["transparent"] is True
    assert cut["versions"] == 2
    back = client.post(f"/api/assets/{upload['id']}/undo").json()
    assert back["versions"] == 1
    assert client.post(f"/api/assets/{upload['id']}/undo").status_code == 400


def test_upscale_and_vectorize(client, upload):
    up = client.post(f"/api/assets/{upload['id']}/upscale",
                     json={"scale": 2, "method": "lanczos"}).json()
    assert up["width"] == 280
    vec = client.post(f"/api/assets/{upload['id']}/vectorize",
                      json={"colors": 3}).json()
    assert vec["shapes"] >= 1 and vec["has_svg"]
    svg = client.get(f"/api/assets/{upload['id']}/svg")
    assert svg.text.startswith("<svg")
    assert "attachment" in svg.headers["content-disposition"]


def test_svg_before_vectorizing_is_404(client, upload):
    assert client.get(f"/api/assets/{upload['id']}/svg").status_code == 404


def test_montage_endpoint_returns_downloads(client, upload):
    response = client.post("/api/montage", json={
        "items": [{"id": upload["id"], "width_mm": 40, "quantity": 6}],
        "page": "A4", "dpi": 100, "layout": "pack"})
    data = response.json()
    assert data["placed"] == 6 and data["pages"] == 1
    assert client.get(data["pdf"]).content.startswith(b"%PDF")
    assert client.get(data["sheets"][0]).status_code == 200
    assert client.get(data["previews"][0]).status_code == 200


def test_montage_without_items_is_rejected(client):
    assert client.post("/api/montage", json={"items": []}).status_code == 400


def test_output_path_traversal_is_blocked(client, upload):
    response = client.get("/api/sorties/..%2F..%2Fetc/passwd")
    assert response.status_code == 404


def test_settings_and_quality(client, upload):
    updated = client.post(f"/api/assets/{upload['id']}/settings",
                          json={"width_mm": 80, "quantity": 5}).json()
    assert updated["width_mm"] == 80 and updated["quantity"] == 5
    report = client.get(f"/api/assets/{upload['id']}/quality?mm=80").json()
    assert report["dpi"] > 0 and "grade" in report


def test_delete_and_clear(client, upload):
    assert client.delete(f"/api/assets/{upload['id']}").json() == {"ok": True}
    assert client.get("/api/assets").json() == []
    assert client.post("/api/workspace/clear").json() == {"ok": True}


def test_unknown_asset_is_404(client):
    assert client.get("/api/assets/nope/preview").status_code == 404


def test_landing_and_workshop_pages(client):
    assert "Déposez votre visuel" in client.get("/").text
    assert "Composer la planche" in client.get("/atelier").text
    assert client.get("/static/landing.js").status_code == 200


def test_products_and_inspect(client, upload):
    products = client.get("/api/products").json()
    assert {"flyer-a5", "dtf-a4"} <= {p["key"] for p in products}
    report = client.post(f"/api/assets/{upload['id']}/inspect",
                         json={"product": "affiche-a3"}).json()
    assert report["issues"] >= 1 and report["checks"]
    bad = client.post(f"/api/assets/{upload['id']}/inspect", json={"product": "nope"})
    assert bad.status_code == 400


def test_print_job(client, upload):
    report = client.post(f"/api/assets/{upload['id']}/print",
                         json={"product": "carte-visite"}).json()
    assert report["preflight"]["passed"] and report["preflight"]["count"] == 9
    pdf = client.get(report["pdf"])
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-1.3")
    assert client.get(report["proof"]).status_code == 200
    dtf_refused = client.post(f"/api/assets/{upload['id']}/print",
                              json={"product": "dtf-a4"})
    assert dtf_refused.status_code == 400


def test_dtf_job(client, upload):
    report = client.post(f"/api/assets/{upload['id']}/dtf",
                         json={"product": "dtf-coeur", "edges": "halftone"}).json()
    assert report["preflight"]["passed"]
    for key in ("png", "white", "pdf", "proof"):
        assert client.get(report[key]).status_code == 200


def test_upload_name_is_sanitised(client, disc_on_white):
    image, _ = disc_on_white
    hostile = '../x"><img src=x onerror=alert(1)>.png'
    asset = client.post("/api/assets", files={
        "files": (hostile, io.BytesIO(encode(image, "PNG")), "image/png")}).json()[0]
    assert not set('<>"') & set(asset["name"])
    report = client.post(f"/api/assets/{asset['id']}/print",
                         json={"product": "carte-visite"}).json()
    assert not set('<>"\' ') & set(report["pdf"])
    assert client.get(report["pdf"]).status_code == 200


@pytest.mark.parametrize("route,body", [
    ("inspect", {"product": "nanxnan"}),
    ("inspect", {"product": "100000x100000"}),
    ("inspect", {"product": "flyer-a5", "dpi": "abc"}),
    ("inspect", {"product": "flyer-a5", "orientation": "diagonal"}),
    ("print", {"product": "flyer-a5", "tac": "x"}),
    ("print", {"product": "flyer-a5", "tac": 1000}),
    ("print", {"product": "flyer-a5", "background": "rouge"}),
    ("print", {"product": "flyer-a5", "fit": "zoom"}),
    ("print", {"product": "3000x3000", "dpi": 1200}),
    ("dtf", {"product": "dtf-coeur", "garment": "#zzz"}),
    ("dtf", {"product": "dtf-coeur", "choke_mm": "inf"}),
    ("dtf", {"product": "dtf-coeur", "edges": "flou"}),
])
def test_invalid_input_is_a_400(client, upload, route, body):
    response = client.post(f"/api/assets/{upload['id']}/{route}", json=body)
    assert response.status_code == 400, response.text


def test_outputs_cannot_escape_the_workspace(client, upload):
    for url in ("/api/sorties/..%2F..%2F/etc",
                "/api/sorties/x/..%2F..%2Fassets", "/api/sorties/nope/rapport.json"):
        assert client.get(url).status_code == 404


def test_vercel_entrypoint_uses_tmp_workspace(tmp_path, monkeypatch):
    import importlib
    import sys
    from printpro.server import default_workspace
    monkeypatch.delenv("PRINTPRO_WORKSPACE", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert str(default_workspace()) == "/tmp/printpro-workspace"
    monkeypatch.setenv("PRINTPRO_WORKSPACE", str(tmp_path / "ws"))
    sys.modules.pop("app", None)
    entry = importlib.import_module("app")
    client = fastapi_testclient.TestClient(entry.app)
    assert client.get("/api/products").status_code == 200
    assert (tmp_path / "ws" / "assets").is_dir()


def test_workspace_survives_restart(tmp_path, disc_on_white):
    from printpro.server import create_app
    root = tmp_path / "persistent"
    first = fastapi_testclient.TestClient(create_app(root))
    created = first.post("/api/assets", files={"files": ("été.png", encode(disc_on_white[0], "PNG"), "image/png")}).json()[0]
    first.post(f"/api/assets/{created['id']}/settings", json={"width_mm": 75.5, "quantity": 9})
    first.post(f"/api/assets/{created['id']}/upscale", json={"scale": 2, "method": "lanczos"})
    second = fastapi_testclient.TestClient(create_app(root))
    restored = second.get("/api/assets").json()[0]
    assert (restored["width_mm"], restored["quantity"], restored["versions"]) == (75.5, 9, 2)
    assert second.get(restored["preview"]).status_code == 200
    assert second.post(f"/api/assets/{created['id']}/undo").json()["width"] == 140


@pytest.mark.parametrize("operation,body", [
    ("settings", {"width_mm": -1}), ("settings", {"quantity": 2.5}),
    ("upscale", {"scale": 1000}), ("vectorize", {"colors": 0}),
    ("background", {"method": "unknown"}), ("background", {"color": "<script>"})])
def test_invalid_processing_settings(client, upload, operation, body):
    assert client.post(f"/api/assets/{upload['id']}/{operation}", json=body).status_code == 422
    assert client.get("/api/assets").json()[0]["versions"] == 1


def test_preview_does_not_write_exports(client, upload, tmp_path):
    response = client.post("/api/montage", json={"items": [{"id": upload['id'], "width_mm": 40, "quantity": 7}], "preview_only": True, "page": "DTF-40"})
    data = response.json()
    assert response.status_code == 200 and data["placed"] == 7
    assert data["previews"][0].startswith("data:image/png;base64,")
    assert "pdf" not in data
    assert not list((tmp_path / "ws" / "sorties").iterdir())
    assert any("dpi" in warning for warning in data["warnings"])


def test_oversize_and_invalid_page_rejected(client, upload):
    body = {"items": [{"id": upload['id'], "width_mm": 1000}], "page": "A4"}
    assert client.post("/api/montage", json=body).status_code == 400
    body["page"] = "oops"
    assert client.post("/api/montage", json=body).status_code == 400


def test_original_preview_and_svg_invalidation(client, upload):
    asset_id = upload['id']
    original = client.get(f"/api/assets/{asset_id}/preview?original=true&full=1").content
    vec = client.post(f"/api/assets/{asset_id}/vectorize", json={"colors": 3})
    assert vec.json()["has_svg"]
    assert client.get(f"/api/assets/{asset_id}/preview?original=true&full=1").content == original
    up = client.post(f"/api/assets/{asset_id}/upscale", json={"scale": 2, "method": "lanczos"})
    assert not up.json()["has_svg"]
    assert client.get(f"/api/assets/{asset_id}/quality?mm=0").status_code == 422


def test_custom_page_and_settings_partial_update(client, upload):
    client.post(f"/api/assets/{upload['id']}/settings", json={"quantity": 7})
    result = client.post(f"/api/assets/{upload['id']}/settings", json={"width_mm": 40}).json()
    assert result['quantity'] == 7
    response = client.post("/api/montage", json={"items": [{"id": upload['id']}], "page": "custom", "page_width_mm": 400, "page_height_mm": 350, "preview_only": True})
    assert response.json()['placed'] == 7
    assert response.json()['page_size_mm'] == [400, 350]


def test_png_download_embeds_physical_resolution(client, upload):
    from PIL import Image
    client.post(f"/api/assets/{upload['id']}/settings", json={"width_mm": 40})
    response = client.get(f"/api/assets/{upload['id']}/download")
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.info['dpi'][0] == pytest.approx(140 * 25.4 / 40, abs=.02)
