"""Volet 2 : montage, PDF, téléchargements, mobile, thème sombre."""
import base64, io, sys
sys.path.insert(0, "/tmp/printpro-webtests")
from harness import App, Report, FX, SP
from pypdf import PdfReader

R = Report()
app = App(fake_claude=True)
p = app.page


def set_job(rows):
    inputs = p.locator(".job-row input")
    for i, (w, q) in enumerate(rows):
        inputs.nth(i * 2).fill(str(w)); inputs.nth(i * 2).dispatch_event("change")
        inputs.nth(i * 2 + 1).fill(str(q)); inputs.nth(i * 2 + 1).dispatch_event("change")


def sheet():
    app.run("run-sheet", timeout=600000)
    return p.evaluate("""() => ({ pages: state.sheet.pages.length, placed: state.sheet.placed,
      requested: state.sheet.requested, warnings: state.sheet.warnings,
      size: state.sheet.pageSizeMm, eff: state.sheet.efficiency,
      rendered: state.sheet.rendered.map(r => ({ w: r.canvas.width, h: r.canvas.height, dpi: r.dpi, reduced: r.reduced })),
      placements: state.sheet.pages.map(pg => pg.map(x => [x.xMm, x.yMm, x.wMm, x.hMm, x.rotated])) })""")


def fetch_pdf(name):
    data = p.evaluate("""async () => {
      const blob = await Montage.exportPdf(state.sheet);
      const buffer = new Uint8Array(await blob.arrayBuffer());
      let s = ''; for (let i = 0; i < buffer.length; i += 8192) s += String.fromCharCode.apply(null, buffer.subarray(i, i + 8192));
      return btoa(s);
    }""")
    raw = base64.b64decode(data)
    (SP / name).write_bytes(raw)
    return raw


def no_overlap(placements):
    for page in placements:
        for i, a in enumerate(page):
            for b in page[i + 1:]:
                apart = (a[0] + a[2] <= b[0] + 1e-6 or b[0] + b[2] <= a[0] + 1e-6 or
                         a[1] + a[3] <= b[1] + 1e-6 or b[1] + b[3] <= a[1] + 1e-6)
                if not apart:
                    return False
    return True


# ---------------------------------------------------------------- mise en place
p.locator(".tile .drop-one").first.click(); p.wait_for_timeout(200)     # retire l'exemple
app.import_files(FX / "etoile-transparente.png", "/home/user/printpro/samples/logo.png")
set_job([(45, 10), (60, 6)])

# ---------------------------------------------------------------- dispositions
p.select_option("#m-layout", "pack"); p.select_option("#m-dpi", "300"); p.check("#m-marks")
s = sheet()
R.check("nesting : 16/16 placés sur une planche A4", s["placed"] == 16 and s["pages"] == 1, str(s["placed"]))
R.check("nesting : aucun chevauchement", no_overlap(s["placements"]))
R.check("nesting : tout dans la zone utile (marge 6 mm)", all(
    x >= 6 - 1e-6 and y >= 6 - 1e-6 and x + w <= 210 - 6 + 1e-6 and y + h <= 297 - 6 + 1e-6
    for page in s["placements"] for x, y, w, h, _ in page))
R.check("planche A4 à 300 dpi : rendu 2480×3508", (s["rendered"][0]["w"], s["rendered"][0]["h"]) == (2480, 3508), str(s["rendered"][0]))

pdf = fetch_pdf("p2-nesting.pdf")
reader = PdfReader(io.BytesIO(pdf))
page = reader.pages[0]
images = list(page.images)
R.check("PDF nesting : 1 page A4, 16 images placées", len(reader.pages) == 1 and len(images) == 16 and abs(float(page.mediabox.width) - 595.28) < 0.1)
xobjects = page["/Resources"]["/XObject"]
unique = len({obj.indirect_reference.idnum for obj in xobjects.values()})
R.check("PDF : visuels dédupliqués (au plus 2 par visuel : droit + tourné)", 2 <= unique <= 4, f"{unique} objets, {len(pdf)/1024:.0f} Ko")
star = next(img for img in images if img.image.mode == "RGBA")
R.check("PDF : masque alpha présent sur l'étoile détourée", star.image.mode == "RGBA")

# Test déterministe du liseré : un carré jaune dont la bordure est à 50 % d'opacité.
halo = p.evaluate("""async () => {
  const img = new ImageData(120, 120);
  for (let y = 0; y < 120; y++) for (let x = 0; x < 120; x++) {
    const i = (y * 120 + x) * 4; img.data[i] = 255; img.data[i+1] = 200; img.data[i+2] = 40;
    img.data[i+3] = (x >= 20 && x < 100 && y >= 20 && y < 100) ? 255 : 128;
  }
  const r = Montage.build([{ image: img, widthMm: 40, quantity: 1 }], { page: "A4", dpi: 150, background: "#ffffff", autoTrim: false });
  const blob = await Montage.exportPdf(r);
  const buffer = new Uint8Array(await blob.arrayBuffer());
  let s = ''; for (let i = 0; i < buffer.length; i += 8192) s += String.fromCharCode.apply(null, buffer.subarray(i, i + 8192));
  return btoa(s);
}""")
halo_img = list(PdfReader(io.BytesIO(base64.b64decode(halo))).pages[0].images)[0].image.convert("RGBA")
r, g, b, a = halo_img.getpixel((5, halo_img.height // 2))
R.check("PDF : un bord à 50 % garde sa vraie couleur (jaune, pas assombri)", 100 < a < 160 and r > 200 and g > 150 and b < 120, f"rgba={r},{g},{b},{a}")

p.select_option("#m-layout", "grid"); s = sheet()
R.check("grille : 16 placés, cellules alignées", s["placed"] == 16 and no_overlap(s["placements"]))
p.select_option("#m-layout", "fill"); s = sheet()
R.check("remplir : bien plus d'exemplaires que demandés", s["placed"] > 16 and s["placed"] == s["requested"], str(s["placed"]))
p.select_option("#m-layout", "pack")

# ---------------------------------------------------------------- options
p.select_option("#m-orient", "landscape"); s = sheet()
R.check("orientation paysage : 297×210", s["size"] == [297, 210], str(s["size"]))
p.select_option("#m-orient", "auto")

p.select_option("#m-page", "DTF-60"); p.select_option("#m-dpi", "300"); s = sheet()
R.check("DTF 60 cm à 300 dpi : aperçu réduit annoncé, PDF à 300 dpi", s["rendered"][0]["reduced"] and "le PDF reste à 300 dpi" in app.text("sheets"), app.text("sheets")[-90:])
p.select_option("#m-page", "A4")

p.check("#m-mirror"); p.check("#m-transparent"); s = sheet()
alpha_corner = p.evaluate("state.sheet.rendered[0].canvas.getContext('2d').getImageData(2,2,1,1).data[3]")
R.check("fond transparent : coin de planche transparent", alpha_corner == 0, str(alpha_corner))
pdf = fetch_pdf("p2-miroir.pdf")
reader = PdfReader(io.BytesIO(pdf))
content = reader.pages[0].get_contents().get_data().decode("latin-1")
R.check("PDF miroir : pas de rectangle de fond, images placées", " re f" not in content and content.count(" Do ") == 16)
p.uncheck("#m-mirror"); p.uncheck("#m-transparent")

p.fill("#m-cut", "2"); p.check("#m-reg"); p.check("#m-outline"); s = sheet()
R.check("contour de découpe 2 mm : visuels agrandis de 4 mm", any(abs(w - 49) < 0.01 for page in s["placements"] for _, _, w, _, _ in page), str(s["placements"][0][:2]))
R.check("repères de calage + cadres : rendu sans erreur", not app.real_errors(), str(app.real_errors()))
p.fill("#m-cut", "0"); p.uncheck("#m-reg"); p.uncheck("#m-outline")

set_job([(400, 1), (60, 6)]); s = sheet()
R.check("visuel de 400 mm : refusé avec avertissement, le reste placé", s["placed"] == 6 and s["warnings"] and "dépasse" in s["warnings"][0], str(s["warnings"]))
set_job([(90, 20), (60, 1)]); s = sheet()
R.check("débordement : plusieurs planches, tout placé", s["pages"] > 1 and s["placed"] == 21, f"{s['pages']} planches")
pdf = fetch_pdf("p2-multi.pdf")
R.check("PDF multipage : autant de pages que de planches", len(PdfReader(io.BytesIO(pdf)).pages) == s["pages"])
buttons = p.locator("#sheet-downloads button").count()
R.check("un bouton PNG par planche + le PDF", buttons == s["pages"] + 1, str(buttons))

# ---------------------------------------------------------------- téléchargements
set_job([(45, 4), (60, 2)]); sheet()
p.click("#sheet-downloads button >> nth=0"); app.wait_idle(); p.wait_for_timeout(300)
p.click("#sheet-downloads button >> nth=1"); app.wait_idle(); p.wait_for_timeout(300)
p.click("#save-png"); app.wait_idle(); p.wait_for_timeout(300)
app.tab("vec"); p.check("#vec-replace"); app.run("run-vec")
p.click("#save-svg"); p.wait_for_timeout(400)
saves = p.evaluate("window.__saves")
names = [s["filename"] for s in saves]
R.check("enregistrements via la capacité du visionneur : PDF, PNG planche, PNG visuel, SVG",
        any(n.endswith(".pdf") for n in names) and "planche-1.png" in names and
        any(n.endswith("-printpro.png") for n in names) and any(n.endswith(".svg") for n in names), str(names))
R.check("fichiers non vides, transmis en Blob", all(s["size"] > 100 and s["kind"] == "Blob" for s in saves), str(saves))
p.evaluate("window.__declineNext = true"); p.click("#save-png"); app.wait_idle(); p.wait_for_timeout(300)
R.check("refus par l'utilisateur : silencieux, sans erreur", not app.real_errors() and "impossible" not in app.flash(), app.flash())
R.check("aucune erreur JS sur tout le volet 2", not app.real_errors(), str(app.real_errors()))
app.close()

# ---------------------------------------------------------------- affichage
mobile = App(width=400, height=820)
mobile.import_files(FX / "etoile-transparente.png")
scroll = mobile.page.evaluate("document.documentElement.scrollWidth")
R.check("mobile 400 px : aucun défilement horizontal", scroll <= 400, f"scrollWidth={scroll}")
mobile.page.screenshot(path=str(SP / "shot-mobile.png"), full_page=False)
mobile.close()

dark = App(width=1400, height=950, dark=True)
dark.tab("vec")
dark.page.screenshot(path=str(SP / "shot-dark.png"), full_page=False)
contrast = dark.page.evaluate("getComputedStyle(document.body).backgroundColor + ' / ' + getComputedStyle(document.body).color")
R.check("thème sombre : fond sombre, texte clair", "13, 19, 23" in contrast and "231" in contrast, contrast)
R.check("thème sombre : aucune erreur JS", not dark.real_errors(), str(dark.real_errors()))
dark.close()

R.summary()
sys.exit(1 if R.failures else 0)
