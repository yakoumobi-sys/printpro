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
    assert "Montage sur la planche" in client.get("/atelier").text
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
