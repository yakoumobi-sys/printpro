# Tests navigateur de la web app

Batterie Playwright (Chromium) qui pilote `web/index.html` comme un utilisateur :
import (PNG transparent, JPEG avec orientation EXIF, fichier corrompu, 1×1, 20 Mpx),
détourage, agrandissement, vectorisation, historique, montage dans toutes les
dispositions, PDF relus par pypdf (déduplication, masque alpha, couleurs des
bords), enregistrement via la capacité `downloads`, mobile 400 px, thème sombre.

La section « qualité du tracé » du volet 1 mesure le vectoriseur sur les deux
pièges classiques, en comparant le rendu SVG à la source pixel à pixel :
position exacte d'une pointe d'étoile, survie d'un contour d'un seul pixel sans
épaississement, absence de vide entre les couches, épaisseur et contre-formes
d'un texte de 15 px, et compacité du tracé.

```bash
pip install playwright pypdf pillow numpy scipy
mkdir -p /tmp/printpro-webtests/fx      # fixtures : voir harness.py / test_part1.py
python web/tests/test_part1.py           # 41 contrôles
python web/tests/test_part2.py           # 27 contrôles
```

Les fixtures sont générées par le script Python de la version serveur
(`samples/` + détourage de l'étoile, JPEG EXIF 6, anneau, image 20 Mpx).
