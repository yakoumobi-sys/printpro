"""Volet 1 : import, détourage, agrandissement, vectorisation, historique."""
import sys, time
sys.path.insert(0, "/tmp/printpro-webtests")
from harness import App, Report, FX

R = Report()
app = App()
p = app.page
timings = {}

# ---------------------------------------------------------------- démarrage
c = app.current()
R.check("démarrage : visuel d'exemple chargé", c and c.count == 1 and c.name == "exemple-etoile")
R.check("démarrage : aucune erreur JS", not app.real_errors(), str(app.real_errors()))

# ---------------------------------------------------------------- imports
app.import_files(FX / "etoile-transparente.png")
c = app.current()
R.check("import PNG transparent : transparence détectée", c.transparent and "fond transparent" in app.text("dims"))

app.import_files(FX / "exif6.jpg")
c = app.current()
R.check("import JPEG EXIF orientation 6 : image redressée en 200×300", (c.width, c.height) == (200, 300), f"{c.width}×{c.height}")
px = app.pixel(165, 35)
R.check("import EXIF : repère rouge passé en haut à droite", px[0] > 150 and px[2] < 90, str(px))

before = app.current().count
app.import_files(FX / "corrompu.png")
R.check("fichier corrompu : refusé sans planter", app.current().count == before and not app.real_errors(),
        app.flash())
R.check("fichier corrompu : message explicite", "pris en charge" in app.flash(), app.flash())

app.import_files(FX / "pixel.png")
t = app.run("run-cut"); app.tab("up"); app.set_range("up-value", 20); t2 = app.run("run-up")
app.tab("vec"); t3 = app.run("run-vec")
R.check("image 1×1 : détourage, agrandissement ×20 et vectorisation sans erreur",
        not app.real_errors() and app.current().width == 20, str(app.real_errors()) + f" w={app.current().width}")

# ---------------------------------------------------------------- détourage
app.import_files(FX / "anneau.png")
app.tab("cut")
p.uncheck("#cut-trim")
p.check("#cut-holes")
app.run("run-cut")
R.check("anneau, fonds intérieurs transparents : le trou est vide", app.pixel(120, 120)[3] == 0, str(app.pixel(120, 120)))
p.click("#undo")
p.uncheck("#cut-holes")
app.run("run-cut")
R.check("anneau, fonds intérieurs conservés : le trou reste opaque", app.pixel(120, 120)[3] == 255, str(app.pixel(120, 120)))
R.check("annuler l'étape : une seule version après undo+redo", app.current().versions == 2)

app.import_files("/home/user/printpro/samples/etoile.png")
p.check("#cut-holes"); p.uncheck("#cut-trim")
app.set_range("cut-tol", 16)
timings["détourage 360×360"] = app.run("run-cut")
c = app.current()
share = c.opaque / (c.width * c.height)
R.check("étoile : part du sujet = aire géométrique + contour (20–28 %)", 0.20 <= share <= 0.28, f"{share*100:.1f} %")
halo = p.evaluate("""() => {
  const a = state.assets.find(x => x.id === state.currentId); const img = a.versions[a.versions.length-1];
  let visible = 0, turquoise = 0;
  for (let i = 0; i < img.data.length; i += 4) { if (img.data[i+3] > 128) { visible++; if (img.data[i+2] > 140 && img.data[i] < 120) turquoise++; } }
  return turquoise / visible;
}""")
R.check("étoile : liseré turquoise éliminé (< 0,5 % des pixels visibles)", halo < 0.005, f"{halo*100:.2f} %")
base = c.opaque
p.click("#undo"); app.set_range("cut-shift", -2); app.run("run-cut")
shrunk = app.current().opaque
p.click("#undo"); app.set_range("cut-shift", 3); app.run("run-cut")
grown = app.current().opaque
R.check("épaisseur du sujet : −2 rétrécit, +3 élargit", shrunk < base < grown, f"{shrunk} < {base} < {grown}")
p.click("#undo"); app.set_range("cut-shift", 0)
p.select_option("#cut-method", "color"); p.fill("#cut-color", "#1fb2aa"); p.dispatch_event("#cut-color", "input")
app.run("run-cut")
R.check("méthode couleur précise : fond turquoise retiré", app.pixel(3, 3)[3] == 0 and app.current().opaque > 30000, str(app.pixel(3, 3)))
p.select_option("#cut-method", "auto")
p.click("#undo"); p.check("#cut-trim"); app.run("run-cut")
c = app.current()
R.check("rogner les bords vides : image réduite", c.width < 360 and c.height < 360, f"{c.width}×{c.height}")

# ---------------------------------------------------------------- agrandissement
app.import_files("/home/user/printpro/samples/logo.png")
app.tab("up")
p.select_option("#up-method", "auto"); p.select_option("#up-target", "scale"); p.fill("#up-value", "2")
timings["agrandissement auto ×2 (420×300)"] = app.run("run-up")
R.check("auto sur un logo : méthode vectorielle choisie et aboutie, 840×600",
        "vectorielle" in app.text("up-note") and "non concluante" not in app.text("up-note") and app.current().width == 840, app.text("up-note"))
unique = p.evaluate("""() => { const a = state.assets.find(x => x.id === state.currentId); const img = a.versions[a.versions.length-1];
  const seen = new Set(); for (let i = 0; i < img.data.length; i += 4) if (img.data[i+3] > 200) seen.add((img.data[i]>>2)+','+(img.data[i+1]>>2)+','+(img.data[i+2]>>2)); return seen.size; }""")
R.check("agrandissement vectoriel : aplats nets (peu de couleurs distinctes)", unique < 120, f"{unique} couleurs")
p.click("#undo")
p.select_option("#up-method", "edge"); app.set_range("up-denoise", 0.3)
timings["agrandissement photo + débruitage ×2"] = app.run("run-up")
R.check("méthode photo + débruitage : 840×600", app.current().width == 840)
p.click("#undo"); app.set_range("up-denoise", 0)
p.select_option("#up-method", "smooth"); app.run("run-up")
R.check("méthode lissée : 840×600", app.current().width == 840)
p.click("#undo")
p.select_option("#up-target", "mm"); p.fill("#up-value", "200"); p.select_option("#up-dpi", "300")
p.select_option("#up-method", "smooth"); app.run("run-up")
R.check("cible 200 mm à 300 dpi : 2362 px de large", app.current().width == 2362, str(app.current().width))
p.click("#undo")
p.select_option("#up-target", "scale"); p.fill("#up-value", "20"); app.run("run-up")
c = app.current()
R.check("×20 sur 420×300 : plafonné avec avertissement", "limitée" in app.text("up-note") and c.width * c.height <= 40e6, app.text("up-note"))
p.click("#undo")

# ---------------------------------------------------------------- vectorisation
app.tab("vec")
p.select_option("#vec-mode", "color"); app.set_range("vec-colors", 6); p.uncheck("#vec-replace")
timings["vectorisation 420×300, 6 couleurs"] = app.run("run-vec")
c = app.current()
R.check("aperçu non remplacé : version inchangée, SVG disponible", c.versions == 1 and c.hasSvg and not p.is_disabled("#save-svg"))
error = p.evaluate("""() => {
  const a = state.assets.find(x => x.id === state.currentId); const src = a.versions[a.versions.length-1];
  const r = Tools.vectorize(src, { colors: 6, dropBackground: false });
  const out = Tools.renderLayers(r.layers, r.width, r.height, 1);
  let sum = 0, n = 0;
  for (let i = 0; i < src.data.length; i += 4) {
    const a1 = out.data[i+3] / 255;
    for (let c = 0; c < 3; c++) { const o = out.data[i+c] * a1 + 255 * (1 - a1); sum += Math.abs(o - src.data[i+c]); n++; }
  }
  return sum / n;
}""")
R.check("fidélité du tracé (fond conservé) : erreur moyenne < 9/255", error < 9, f"{error:.2f}")
p.check("#vec-replace"); p.select_option("#vec-mode", "bw"); app.run("run-vec")
R.check("mode noir et blanc : rendu remplacé, historique noté", "vectorisation" in app.current().history[-1])
p.click("#undo")

# ---------------------------------------------------------------- 20 Mpx
t0 = time.perf_counter(); app.import_files(FX / "grand-20mpx.jpg"); timings["import 20 Mpx"] = time.perf_counter() - t0
c = app.current()
R.check("20 Mpx importée à taille réelle (Chrome)", (c.width, c.height) == (5000, 4000), f"{c.width}×{c.height}")
app.tab("cut"); p.uncheck("#cut-trim"); app.set_range("cut-tol", 14)
timings["détourage 20 Mpx"] = app.run("run-cut", timeout=600000)
R.check("détourage 20 Mpx sans erreur", not app.real_errors() and app.current().transparent, str(app.real_errors()))
app.tab("vec"); p.select_option("#vec-mode", "color"); app.set_range("vec-colors", 6); p.uncheck("#vec-replace")
timings["vectorisation 20 Mpx (tracé réduit)"] = app.run("run-vec", timeout=600000)
c = app.current()
R.check("vectorisation > 4 Mpx : SVG à l'échelle réelle 5000×4000", 'width="5000" height="4000"' in (c.svgHead or ""), c.svgHead)
R.check("vectorisation > 4 Mpx : note de réduction affichée", "tracé sur" in app.text("vec-note"), app.text("vec-note"))
app.tab("up"); p.select_option("#up-method", "edge"); p.select_option("#up-target", "scale"); p.fill("#up-value", "1.4")
timings["agrandissement photo 20 → 39 Mpx"] = app.run("run-up", timeout=600000)
R.check("agrandissement 39 Mpx sans erreur", not app.real_errors() and app.current().width == 7000, f"{app.current().width} {app.real_errors()}")

# ---------------------------------------------------------------- gestion
n = app.current().count
p.locator(".tile .drop-one").first.click(); p.wait_for_timeout(200)
R.check("retirer un visuel", app.current().count == n - 1)
p.locator(".tile .pick").first.uncheck(); p.wait_for_timeout(200)
rows = p.locator(".job-row").count()
R.check("décocher un visuel le retire de la planche", rows == app.current().count - 1, f"{rows} lignes / {app.current().count} visuels")
p.on("dialog", lambda d: d.accept())
p.click("#reset"); p.wait_for_timeout(300)
R.check("vider l'atelier", p.evaluate("state.assets.length") == 0 and "Déposez" in app.text("stage"))
R.check("aucune erreur JS sur tout le volet 1", not app.real_errors(), str(app.real_errors()))

print("\nTemps mesurés :")
for label, seconds in timings.items():
    print(f"  {label:<44} {seconds:6.1f} s")
R.summary()
app.close()
sys.exit(1 if R.failures else 0)
