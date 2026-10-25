"""Essai de l'historique du travailleur Sentinel 1 (lot historique, 25/10/2026), dans l'image d'AI2, sur le Mac.

Le vrai script du travailleur (`run`, fenêtres, lecture des deux passages antérieurs, seuil) sur les vraies scènes des
analyses 103 et 97, lues dans la copie publique de Planetary Computer (mêmes images que Copernicus, jeton anonyme,
aucun coût). Attendu :
* 103 (baie de Weymouth, S1D du 23/10/2026, historique S1C du 17/10 et S1D du 11/10) : SMYRTOS, au mouillage, détecté
  au dessus du seuil de 0,9 ; rien sur le brise lames de Portland (détection 3 de la production, 0,909 sans
  historique) ni sur les quais ;
* 97 (approches de Marseille, S1D du 19/10/2026, historique S1C du 13/10 et S1D du 07/10) : aucune détection sur un
  quai, une digue ou à terre sans navire AIS à moins de `QUAI_M` ;
* les octets lus et les durées de chaque fenêtre (image et passages antérieurs), pour le coût.

    bash scripts/verifier_images.sh ai2-historique        (environ 40 min en émulation sur le Mac)
    python3 /mars/essai_historique_s1.py 97                 (un seul cas)
"""
from __future__ import annotations

import importlib.util
import json
import math
import sys
import time
import urllib.request

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-grd/items/"
JETON = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-grd"
QUAI_M = 300
# Positions AIS à 10 min du passage de 97 (lon, lat, vitesse en nœuds, écart en secondes à la prise de vue), relevées
# en lecture seule par l'API du serveur le 25/10/2026 : 45 navires, la plupart à quai dans le Grand Port
AIS_97 = [
    (5.31711, 43.35793, 0, -552), (5.35338, 43.31959, 0, -582), (5.36091, 43.31284, 0, -376), (5.29831, 43.35949, 0, -526),
    (5.3409, 43.33435, 0, -287), (5.31859, 43.35667, 0, -78), (5.32623, 43.35606, 0, -413), (5.32676, 43.35502, 0, -25),
    (5.36368, 43.3041, 0, -132), (5.36192, 43.32114, 0, -318), (5.32048, 43.35567, 0, -155), (5.30027, 43.36022, 0, -341),
    (5.36155, 43.32257, 0, -469), (5.35943, 43.3246, 0, -216), (5.36313, 43.30758, 0, -264), (5.32237, 43.35554, 0, -131),
    (5.36247, 43.30858, 6.5, -31), (5.35197, 43.32107, 0, -160), (5.34827, 43.33041, 0, -50), (5.30076, 43.36033, 0, -85),
    (5.35583, 43.31656, 0, -263), (5.35876, 43.32327, 0, -372), (5.30152, 43.36033, 0, -558), (5.35131, 43.32258, 0, -363),
    (5.30052, 43.36016, 0, -562), (5.32563, 43.3436, 0.3, -387), (5.35883, 43.32268, 0, -460), (5.3659, 43.24375, 0, -11),
    (5.30115, 43.36015, 0, -474), (5.32189, 43.35562, 0.7, -548), (5.34223, 43.33247, 0, -431), (5.32269, 43.35664, 0, -229),
    (5.30234, 43.36119, 0, -579), (5.32413, 43.35266, 0, -86), (5.35871, 43.32133, 0, -483), (5.33861, 43.33913, 0.3, -176),
    (5.26954, 43.30779, 10.2, -150), (5.33961, 43.33675, 8.1, -48), (5.28992, 43.29843, 19.9, -111), (5.35148, 43.29458, 10.4, -409),
    (5.3602, 43.32385, 0, -493), (5.35247, 43.32057, 0, -246), (5.33157, 43.34951, 0, -225), (5.36092, 43.32338, 0, -58),
    (5.21224, 43.28722, 8.3, -135),
]
CAS = [
    {"nom": "103, baie de Weymouth", "analyse": 103,
     "produit": "S1D_IW_GRDH_1SDV_20261023T175704_20261023T175729_005146_009C13",
     "historique": ["S1C_IW_GRDH_1SDV_20261017T175651_20261017T175716_009928_013CB0",
                    "S1D_IW_GRDH_1SDV_20261011T175704_20261011T175729_004971_009615"],
     "bbox": [-2.53648, 50.45449, -2.19708, 50.67153],
     "navire": ("SMYRTOS", -2.36678, 50.56301),
     "fixes": [("brise lames de Portland (détection 3)", -2.42422, 50.59038),
               ("brise lames, extrémité nord", -2.42001, 50.57175), ("quai de Portland", -2.44361, 50.57115)]},
    {"nom": "97, approches de Marseille", "analyse": 97,
     "produit": "S1D_IW_GRDH_1SDV_20261019T054338_20261019T054403_005080_0099C9",
     "historique": ["S1C_IW_GRDH_1SDV_20261013T054325_20261013T054350_009862_013A8B",
                    "S1D_IW_GRDH_1SDV_20261007T054339_20261007T054404_004905_0093B7"],
     "bbox": [5.194, 43.22397, 5.4904, 43.44101], "navire": None, "fixes": []},
]


def worker():
    spec = importlib.util.spec_from_file_location("travailleur_sentinel", "/mars/sentinel.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def metres(a, b) -> float:
    return math.hypot((a[0] - b[0]) * 111320 * math.cos(math.radians(b[1])), (a[1] - b[1]) * 110574)


def adresses(produit: str, jeton: str) -> dict:
    item = json.load(urllib.request.urlopen(STAC + produit, timeout=60))
    return {"id": produit, **{f"{p}_path": "/vsicurl/" + item["assets"][p]["href"] + "?" + jeton for p in ("vv", "vh")}}


def main() -> int:
    w = worker()
    sys.path.insert(0, w.AI2)
    erreurs = []
    choisis = [a for a in sys.argv[1:] if a.isdigit()]               # « 97 » : ce cas seulement
    for cas in CAS:
        if choisis and str(cas["analyse"]) not in choisis:
            continue
        # Jeton anonyme valable environ une heure : un par cas (l'analyse d'un cas dure plus en émulation)
        jeton = json.load(urllib.request.urlopen(JETON, timeout=60))["token"]
        sc = adresses(cas["produit"], jeton)
        p = cas["produit"]
        debut = f"{p[17:21]}-{p[21:23]}-{p[23:25]}T{p[26:28]}:{p[28:30]}:{p[30:32]}Z"
        scene = {**sc, "analyse": cas["analyse"], "debut": debut, "fin": debut, "satellite": p[:3], "fenetres": [{"id": 0, "bbox": cas["bbox"], "marge_m": 2000}],
                 "historique": [adresses(p, jeton) for p in cas["historique"]]}
        task = {"regions": [[-6.8, 41.2, 9.8, 51.2]], "confiance": 0.9, "confiance_sans_historique": 0.95,
                "nms_m": 10, "vignette_km": 3, "scenes": [scene]}
        t = time.time()
        r = w.run(task, local=True)["scenes"][0]
        if r.get("erreur"):
            erreurs.append(f"{cas['nom']} : {r['erreur']}")
            continue
        f = r["fenetres"][0]
        dets = r["detections"]
        print(f"        {cas['nom']} : {len(dets)} détection(s) au seuil {f.get('confiance')}, historique "
              f"{len(f.get('historique') or [])} passage(s) ; image {f.get('octets', 0) / 1e6:.1f} Mo en "
              f"{f.get('lecture_s')} s, passages antérieurs {f.get('octets_historique', 0) / 1e6:.1f} Mo en "
              f"{f.get('lecture_historique_s')} s ; analyse {f.get('analyse_s')} s ; en tout {time.time() - t:.0f} s")
        for d in sorted(dets, key=lambda d: -(d["score"] or 0)):
            print(f"          {d['lon']:.5f} {d['lat']:.5f} score {d['score']:.3f} longueur {d.get('longueur_m')}")
        if len(f.get("historique") or []) != 2:
            erreurs.append(f"{cas['nom']} : {len(f.get('historique') or [])} passage(s) antérieur(s) lus au lieu de 2 "
                           f"({f.get('historique_erreurs')})")
        if cas["navire"]:
            nom, lon, lat = cas["navire"]
            vus = [d for d in dets if metres((d["lon"], d["lat"]), (lon, lat)) < 400]
            if not vus:
                erreurs.append(f"{cas['nom']} : {nom} non détecté au dessus du seuil")
        for nom, lon, lat in cas["fixes"]:
            if any(metres((d["lon"], d["lat"]), (lon, lat)) < 300 for d in dets):
                erreurs.append(f"{cas['nom']} : détection sur {nom}")
        positions = AIS_97 if cas["analyse"] == 97 else []
        if positions:
            # Un écho est un navire s'il tombe près d'une position AIS, à l'estime près (vitesse × écart de temps)
            seules = [d for d in dets if all(metres((d["lon"], d["lat"]), (lon, lat)) > QUAI_M + sog * 0.5144 * abs(dt)
                                             for lon, lat, sog, dt in positions)]
            for d in seules:
                print(f"          sans AIS à moins de {QUAI_M} m (plus l'estime) : {d['lon']:.5f} {d['lat']:.5f} "
                      f"score {d['score']:.3f}")
            if cas["analyse"] == 97 and seules:
                erreurs.append(f"{cas['nom']} : {len(seules)} détection(s) sans navire AIS (quai ou digue ?)")
    if erreurs:
        print("ÉCHEC   " + " ; ".join(erreurs))
        return 1
    print("OK      historique : SMYRTOS au dessus du seuil, brise lames et quais en dessous, aucun quai à Marseille")
    return 0


if __name__ == "__main__":
    sys.exit(main())
