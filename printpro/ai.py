"""Cloud AI engines (Replicate) for background removal, upscaling and tracing.

Enabled as soon as ``REPLICATE_API_TOKEN`` is set; nothing else to install,
which is what makes AI usable on serverless hosts (Vercel) where ``rembg`` or
a Real-ESRGAN binary cannot run.  Only the standard library is used.

Default models, each overridable with an environment variable:

``PRINTPRO_AI_BG_MODEL``       ``bria/remove-background``   (RMBG 2.0 matting)
``PRINTPRO_AI_UPSCALE_MODEL``  ``topazlabs/image-upscale``  (Topaz Gigapixel)
``PRINTPRO_AI_VECTOR_MODEL``   ``recraft-ai/recraft-vectorize``

A model is written ``owner/name`` (official models) or
``owner/name:version`` (community models).  Every call sends the image as
``image``; the upscaler also receives ``upscale_factor`` and
``enhance_model``.

The traced SVG is parsed back into :class:`~printpro.vectorize.Layer`
objects so the preview, the montage and the vector upscaler keep working
with AI vectors exactly as with the built-in tracer.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import time
import urllib.error
import urllib.request
import uuid
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image
from scipy import ndimage

from .imaging import encode, is_photographic, load_rgba, resize

API = "https://api.replicate.com/v1"
DEFAULT_MODELS = {
    "bg": "bria/remove-background",
    "upscale": "topazlabs/image-upscale",
    "vector": "recraft-ai/recraft-vectorize",
}
TIMEOUT = float(os.environ.get("PRINTPRO_AI_TIMEOUT", "240"))
INLINE_LIMIT = 1_000_000          # larger uploads go through the Files API


class AIError(ValueError):
    """The AI service was asked for and failed: shown to the user as is."""


def configured() -> bool:
    return bool(_token())


def model(kind: str) -> str:
    env = {"bg": "PRINTPRO_AI_BG_MODEL", "upscale": "PRINTPRO_AI_UPSCALE_MODEL",
           "vector": "PRINTPRO_AI_VECTOR_MODEL"}[kind]
    return os.environ.get(env) or DEFAULT_MODELS[kind]


def _token() -> str:
    return os.environ.get("REPLICATE_API_TOKEN", "").strip()


# --------------------------------------------------------------------------- #
# Replicate client
# --------------------------------------------------------------------------- #
def _request(method: str, url: str, body: bytes | None = None,
             headers: dict | None = None, timeout: float = 90.0) -> dict:
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {_token()}", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        try:
            detail = json.loads(detail).get("detail") or detail
        except ValueError:
            pass
        if error.code in (401, 403):
            raise AIError("jeton Replicate refusé (REPLICATE_API_TOKEN)") from None
        if error.code == 402:
            raise AIError("crédit Replicate épuisé : ajoutez un moyen de "
                          "paiement sur replicate.com") from None
        if error.code == 429:
            raise AIError("trop de requêtes IA d'affilée : réessayez dans quelques "
                          "secondes (limite levée en ajoutant un moyen de paiement "
                          "sur replicate.com)") from None
        raise AIError(f"service IA : erreur {error.code} — {str(detail)[:300]}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AIError(f"service IA injoignable : {error}") from None


def _upload(data: bytes, mime: str, name: str) -> str:
    """Return a URL (or data URI) the model can read the file from."""
    if len(data) <= INLINE_LIMIT:
        return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"content\"; "
            f"filename=\"{name}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
    body += data + f"\r\n--{boundary}--\r\n".encode()
    created = _request("POST", f"{API}/files", body, {
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    return created["urls"]["get"]


def run(model_ref: str, inputs: dict) -> object:
    """Run a prediction and wait for its output."""
    if not configured():
        raise AIError("IA non configurée : définissez REPLICATE_API_TOKEN")
    headers = {"Content-Type": "application/json", "Prefer": "wait=60"}
    if ":" in model_ref:
        url, payload = f"{API}/predictions", {"version": model_ref.split(":", 1)[1],
                                              "input": inputs}
    else:
        url, payload = f"{API}/models/{model_ref}/predictions", {"input": inputs}
    prediction = _request("POST", url, json.dumps(payload).encode(), headers)
    deadline = time.monotonic() + TIMEOUT
    while prediction.get("status") not in ("succeeded", "failed", "canceled"):
        if time.monotonic() > deadline:
            raise AIError(f"le modèle {model_ref} n'a pas répondu à temps")
        time.sleep(1.5)
        prediction = _request("GET", prediction["urls"]["get"])
    if prediction["status"] != "succeeded":
        raise AIError(f"le modèle {model_ref} a échoué : "
                      f"{prediction.get('error') or prediction['status']}")
    return prediction.get("output")


def _download(output: object) -> bytes:
    url = output[0] if isinstance(output, list) and output else output
    if isinstance(url, dict):
        url = url.get("url") or url.get("image") or url.get("svg")
    if not isinstance(url, str):
        raise AIError("réponse inattendue du service IA")
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    if not url.startswith("https://"):
        raise AIError("réponse inattendue du service IA")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise AIError(f"téléchargement du résultat IA impossible : {error}") from None


def _image_url(rgba: np.ndarray, flatten: bool = False) -> str:
    data = encode(rgba[:, :, :3] if flatten else rgba, "PNG")
    return _upload(data, "image/png", "image.png")


# --------------------------------------------------------------------------- #
# Background removal
# --------------------------------------------------------------------------- #
def remove_background(rgba: np.ndarray) -> np.ndarray:
    """Alpha mask (uint8, same size as *rgba*) of the subject."""
    h, w = rgba.shape[:2]
    cut = load_rgba(_download(run(model("bg"), {"image": _image_url(rgba)})))
    alpha = cut[:, :, 3]
    if alpha.shape != (h, w):
        alpha = np.asarray(Image.fromarray(alpha).resize((w, h), Image.LANCZOS))
    return alpha


# --------------------------------------------------------------------------- #
# Upscaling
# --------------------------------------------------------------------------- #
def upscale(rgba: np.ndarray, tw: int, th: int) -> np.ndarray:
    """Super-resolve *rgba* to exactly *tw*×*th* (alpha is resampled)."""
    h, w = rgba.shape[:2]
    need = max(tw / w, th / h)
    factor = next((f for f in (2, 4, 6) if f >= need - 1e-6), 6)
    translucent = rgba.shape[2] == 4 and (rgba[:, :, 3] < 250).any()
    source = _bleed(rgba) if translucent else rgba
    inputs = {"image": _image_url(source, flatten=True),
              "upscale_factor": f"{factor}x",
              "enhance_model": "Standard V2" if is_photographic(rgba) else "CGI",
              "output_format": "png"}
    if model("upscale") != DEFAULT_MODELS["upscale"]:
        inputs = {"image": inputs["image"], "scale": factor}
    out = load_rgba(_download(run(model("upscale"), inputs)))
    out = np.array(resize(out, tw, th, Image.LANCZOS))
    if translucent:
        alpha = np.asarray(Image.fromarray(rgba[:, :, 3]).resize((tw, th), Image.LANCZOS))
        out[:, :, 3] = alpha
    return out


def _bleed(rgba: np.ndarray) -> np.ndarray:
    """Extend opaque colours under transparent pixels (no dark/white halos)."""
    out = rgba.copy()
    clear = rgba[:, :, 3] < 128
    if clear.all() or not clear.any():
        return out
    _, (iy, ix) = ndimage.distance_transform_edt(clear, return_indices=True)
    out[:, :, :3] = rgba[iy, ix, :3]
    return out


# --------------------------------------------------------------------------- #
# Vectorisation
# --------------------------------------------------------------------------- #
def vectorize(rgba: np.ndarray):
    """Trace with the AI model; returns a :class:`VectorResult`."""
    from .vectorize import VectorResult

    h, w = rgba.shape[:2]
    # Recraft accepts 256..4096 px per side and 16 Mpx.
    scale = min(1.0, 4096 / max(w, h), math.sqrt(16e6 / (w * h)))
    if min(w, h) * scale < 256:
        scale = 256 / min(w, h)
    while True:
        source = rgba if scale == 1.0 else resize(
            rgba, max(1, round(w * scale)), max(1, round(h * scale)), Image.LANCZOS)
        data = encode(source, "PNG")
        if len(data) <= 4_800_000 or min(source.shape[:2]) <= 300:   # 5 Mo max
            break
        scale *= 0.8
    url = _upload(data, "image/png", "image.png")
    svg = _download(run(model("vector"), {"image": url})).decode("utf-8")
    layers, nodes = svg_to_layers(svg, w, h)
    if not layers:
        raise AIError("le modèle IA n'a renvoyé aucune forme")
    palette = list(dict.fromkeys(layer.color for layer in layers))
    return VectorResult(svg=_fit_svg(svg, w, h), width=w, height=h,
                        layers=layers, palette=palette, nodes=nodes)


def _fit_svg(svg: str, w: int, h: int) -> str:
    """Give the SVG the source's pixel size (its viewBox keeps the geometry)."""
    def fix(match: re.Match) -> str:
        tag = re.sub(r'\s(width|height)="[^"]*"', "", match.group(0))
        return tag[:4] + f' width="{w}" height="{h}"' + tag[4:]
    return re.sub(r"<svg\b[^>]*>", fix, svg, count=1)


_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_CMD = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|" + _NUM.pattern)


def svg_to_layers(svg: str, width: int, height: int):
    """Parse filled SVG shapes into ``Layer`` objects in source pixels.

    Each painted element becomes its own layer so the painter's order of the
    file is respected by the rasteriser.
    """
    from .vectorize import Layer

    try:
        root = ET.fromstring(svg)
    except ET.ParseError as error:
        raise AIError(f"SVG renvoyé illisible : {error}") from None
    vb = [float(v) for v in _NUM.findall(root.get("viewBox", ""))]
    if len(vb) == 4 and vb[2] > 0 and vb[3] > 0:
        base = _matrix(width / vb[2], 0, 0, height / vb[3],
                       -vb[0] * width / vb[2], -vb[1] * height / vb[3])
    else:
        sw = _length(root.get("width"), width)
        sh = _length(root.get("height"), height)
        base = _matrix(width / sw, 0, 0, height / sh, 0, 0)

    layers: list = []
    nodes = 0

    def walk(node, matrix, fill):
        nonlocal nodes
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in ("defs", "clipPath", "mask", "style", "title", "metadata"):
            return
        matrix = matrix @ _transform(node.get("transform", ""))
        fill = _fill(node, fill)
        rings = _shape_rings(tag, node)
        if rings and fill is not None:
            pts = [_apply(matrix, ring) for ring in rings if len(ring) >= 3]
            pts = [p for p in pts if len(p) >= 3]
            if pts:
                nodes += sum(len(p) for p in pts)
                area = int(abs(sum(_area(p) for p in pts)))
                layers.append(Layer(color=fill, paths=[pts], area=area))
        for child in node:
            walk(child, matrix, fill)

    walk(root, base, (0, 0, 0))
    return layers, nodes


def _matrix(a, b, c, d, e, f) -> np.ndarray:
    return np.array([[a, c, e], [b, d, f], [0, 0, 1]], dtype=np.float64)


def _transform(text: str) -> np.ndarray:
    m = np.eye(3)
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text):
        v = [float(x) for x in _NUM.findall(args)]
        if name == "matrix" and len(v) == 6:
            t = _matrix(*v)
        elif name == "translate" and v:
            t = _matrix(1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale" and v:
            t = _matrix(v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            r = math.radians(v[0])
            t = _matrix(math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0, 0)
            if len(v) == 3:
                t = (_matrix(1, 0, 0, 1, v[1], v[2]) @ t @
                     _matrix(1, 0, 0, 1, -v[1], -v[2]))
        else:
            continue
        m = m @ t
    return m


def _apply(m: np.ndarray, pts: np.ndarray) -> np.ndarray:
    return pts @ m[:2, :2].T + m[:2, 2]


def _length(text: str | None, default: float) -> float:
    found = _NUM.findall(text or "")
    return float(found[0]) if found and float(found[0]) > 0 else float(default)


def _fill(node, inherited):
    style = dict(part.split(":", 1) for part in node.get("style", "").split(";")
                 if ":" in part)
    style = {k.strip(): v.strip() for k, v in style.items()}
    value = style.get("fill", node.get("fill"))
    if value is None or value == "inherit":
        return inherited
    if value == "none" or float(style.get("fill-opacity", node.get("fill-opacity", 1))) <= 0:
        return None
    return _color(value, inherited)


def _color(value: str, inherited):
    value = value.strip().lower()
    if value.startswith("#"):
        hexa = value[1:]
        if len(hexa) in (3, 4):
            hexa = "".join(ch * 2 for ch in hexa[:3])
        try:
            return tuple(int(hexa[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return inherited
    if value.startswith("rgb"):
        parts = _NUM.findall(value)[:3]
        if len(parts) == 3:
            pct = "%" in value
            return tuple(int(round(min(255, float(p) * (2.55 if pct else 1)))) for p in parts)
    named = {"black": (0, 0, 0), "white": (255, 255, 255)}
    return named.get(value, inherited)


def _area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _shape_rings(tag: str, node) -> list[np.ndarray]:
    num = lambda key: float(_NUM.findall(node.get(key, "0") or "0")[0]
                            if _NUM.findall(node.get(key, "0") or "0") else 0)
    if tag == "path":
        return _path_rings(node.get("d", ""))
    if tag in ("polygon", "polyline"):
        v = [float(x) for x in _NUM.findall(node.get("points", ""))]
        return [np.array(v[: len(v) // 2 * 2]).reshape(-1, 2)] if len(v) >= 6 else []
    if tag == "rect":
        x, y, w, h = num("x"), num("y"), num("width"), num("height")
        return [np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]])] if w > 0 and h > 0 else []
    if tag in ("circle", "ellipse"):
        cx, cy = num("cx"), num("cy")
        rx = num("r") if tag == "circle" else num("rx")
        ry = num("r") if tag == "circle" else num("ry")
        t = np.linspace(0, 2 * np.pi, 64, endpoint=False)
        return [np.stack([cx + rx * np.cos(t), cy + ry * np.sin(t)], 1)] if rx > 0 and ry > 0 else []
    return []


def _bezier(points: list, steps: int = 12) -> np.ndarray:
    p = np.asarray(points, dtype=np.float64)
    t = np.linspace(0, 1, steps + 1)[1:, None]
    if len(p) == 3:
        return (1 - t) ** 2 * p[0] + 2 * (1 - t) * t * p[1] + t ** 2 * p[2]
    return ((1 - t) ** 3 * p[0] + 3 * (1 - t) ** 2 * t * p[1] +
            3 * (1 - t) * t ** 2 * p[2] + t ** 3 * p[3])


def _path_rings(d: str) -> list[np.ndarray]:
    """Flatten an SVG path into closed polylines (one per sub-path)."""
    tokens = [m.group(1) or m.group(0) for m in _CMD.finditer(d)]
    rings: list[np.ndarray] = []
    pts: list = []
    cur = np.zeros(2)
    start = np.zeros(2)
    last_ctrl = None
    cmd = None
    i = 0

    def take(n):
        nonlocal i
        vals = [float(v) for v in tokens[i:i + n]]
        i += n
        if len(vals) < n:
            raise IndexError
        return vals

    def close():
        nonlocal pts
        if len(pts) >= 3:
            rings.append(np.asarray(pts, dtype=np.float64))
        pts = []

    try:
        while i < len(tokens):
            if tokens[i].isalpha():
                cmd = tokens[i]
                i += 1
                if cmd in "Zz":
                    close()
                    cur = start.copy()
                    last_ctrl = None
                    continue
            elif cmd is None:
                break
            rel = cmd.islower()
            off = cur if rel else np.zeros(2)
            c = cmd.upper()
            ctrl = None
            if c == "M":
                close()
                cur = np.array(take(2)) + off
                start = cur.copy()
                pts = [cur.copy()]
                cmd = "l" if rel else "L"
            elif c == "L":
                cur = np.array(take(2)) + off
                pts.append(cur.copy())
            elif c == "H":
                x = take(1)[0]
                cur = np.array([x + (cur[0] if rel else 0), cur[1]])
                pts.append(cur.copy())
            elif c == "V":
                y = take(1)[0]
                cur = np.array([cur[0], y + (cur[1] if rel else 0)])
                pts.append(cur.copy())
            elif c in "CS":
                if c == "C":
                    c1 = np.array(take(2)) + off
                else:
                    c1 = 2 * cur - last_ctrl if last_ctrl is not None else cur.copy()
                c2 = np.array(take(2)) + off
                end = np.array(take(2)) + off
                pts.extend(_bezier([cur, c1, c2, end]))
                cur, ctrl = end, c2
            elif c in "QT":
                if c == "Q":
                    q = np.array(take(2)) + off
                else:
                    q = 2 * cur - last_ctrl if last_ctrl is not None else cur.copy()
                end = np.array(take(2)) + off
                pts.extend(_bezier([cur, q, end]))
                cur, ctrl = end, q
            elif c == "A":
                rx, ry, rot, large, sweep, x, y = take(7)
                end = np.array([x, y]) + off
                pts.extend(_arc(cur, end, rx, ry, rot, large, sweep))
                cur = end
            last_ctrl = ctrl if (c in "CSQT") else None
    except (IndexError, ValueError):
        pass
    close()
    return rings


def _arc(p0, p1, rx, ry, rot, large, sweep, steps: int = 16) -> list:
    """SVG elliptical arc (endpoint parameterisation) as a polyline."""
    if rx == 0 or ry == 0 or np.allclose(p0, p1):
        return [p1]
    phi = math.radians(rot)
    cos, sin = math.cos(phi), math.sin(phi)
    dx, dy = (p0 - p1) / 2
    x1 = cos * dx + sin * dy
    y1 = -sin * dx + cos * dy
    rx, ry = abs(rx), abs(ry)
    lam = x1 ** 2 / rx ** 2 + y1 ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1 ** 2 - ry ** 2 * x1 ** 2
    den = rx ** 2 * y1 ** 2 + ry ** 2 * x1 ** 2
    k = math.sqrt(max(0.0, num / den)) if den else 0.0
    if bool(large) == bool(sweep):
        k = -k
    cx1, cy1 = k * rx * y1 / ry, -k * ry * x1 / rx
    mid = (p0 + p1) / 2
    cx = cos * cx1 - sin * cy1 + mid[0]
    cy = sin * cx1 + cos * cy1 + mid[1]
    a0 = math.atan2((y1 - cy1) / ry, (x1 - cx1) / rx)
    a1 = math.atan2((-y1 - cy1) / ry, (-x1 - cx1) / rx)
    delta = a1 - a0
    if sweep and delta < 0:
        delta += 2 * math.pi
    elif not sweep and delta > 0:
        delta -= 2 * math.pi
    out = []
    for t in np.linspace(0, 1, steps + 1)[1:]:
        a = a0 + delta * t
        x, y = rx * math.cos(a), ry * math.sin(a)
        out.append(np.array([cos * x - sin * y + cx, sin * x + cos * y + cy]))
    return out
