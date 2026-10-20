"""Essai du travailleur Sentinel 2 (travailleurs/sentinel2.py) dans la vraie image d'AI2, sur processeur, lancé par
scripts/verifier_images.sh s2 (lot S2, docs/sentinel2.md).

Fenêtre réelle de 24 km, comme le serveur la calcule : passage S2A du 04/10/2026 à 11:08 UTC, rail montant de la
Manche au large de Beachy Head (0,4 E, 50,4 N), 0,2 % de nuages. Les 13 bandes du L1C et le masque de scène (SCL) du
L2A sont lus par requêtes partielles dans le miroir public de Google (mêmes fichiers qu'eodata, sans clé) ; le script
de production fait tout le reste : garde des nuages, lecture, inférence d'AI2, masque, vignettes, mesures.
Attendu : quatre navires dans le cœur de la fenêtre, masque présent, fenêtre dégagée, vignettes de 2 et 10 km."""
import math
import sys
import time

sys.path.insert(0, "/mars")
import sentinel2  # noqa: E402

MIROIR = "https://storage.googleapis.com/gcp-public-data-sentinel-2/"
L1C = ("tiles/31/U/CR/S2A_MSIL1C_20261004T110831_N0513_R137_T31UCR_20261004T162422.SAFE/GRANULE/"
       "L1C_T31UCR_A058936_20261004T111122/IMG_DATA/T31UCR_20261004T110831_{}.jp2")
SCL = ("L2/tiles/31/U/CR/S2A_MSIL2A_20261004T110831_N0513_R137_T31UCR_20261004T174703.SAFE/GRANULE/"
       "L2A_T31UCR_A058936_20261004T111122/IMG_DATA/R20m/T31UCR_20261004T110831_SCL_20m.jp2")
LON, LAT, DEMI_M = 0.4, 50.4, 12000
ATTENDUS = 4


def main() -> int:
    dlat, dlon = DEMI_M / 110574.0, DEMI_M / (111320.0 * math.cos(math.radians(LAT)))
    bbox = [LON - dlon, LAT - dlat, LON + dlon, LAT + dlat]
    tache = {"scenes": [{"analyse": 0, "id": "S2A_MSIL1C_20261004T110831_N0513_R137_T31UCR", "tuile": "T31UCR",
                         "debut": "2026-10-04T11:08:31Z", "fin": "2026-10-04T11:08:31Z", "satellite": "S2A",
                         "bandes": {b: MIROIR + L1C.format(b) for b in sentinel2.BANDES}, "scl": MIROIR + SCL,
                         "fenetres": [{"id": 0, "bbox": bbox, "marge_m": 2000, "points": 1}]}],
             "regions": [[-6.8, 41.2, 9.8, 51.2]], "confiance": 0.8, "nuages_fenetre_max_pct": 20,
             "masque_classes": [3, 8, 9, 10], "masque_voisinage_m": 500, "masque_part_max_pct": 25,
             "vignette_km": 2, "contexte_km": 10,
             "rslp_prefix": "/rslp", "poids": []}
    t0 = time.time()
    r = sentinel2.run(tache)
    duree = time.time() - t0
    if r.get("erreur"):
        print(f"ÉCHEC  s2 : {r['erreur']}")
        return 1
    sc = r["scenes"][0]
    f = sc["fenetres"][0]
    print(f"OK      s2 : masque {sc['masque']}, nuages de la fenêtre {f.get('nuages_pct')} %, SCL "
          f"{sc['octets_scl'] / 1e3:.0f} Ko, 13 bandes {sc['octets_lus'] / 1e6:.1f} Mo en {f.get('requetes')} requêtes "
          f"et {f.get('lecture_s')} s")
    for d in sc["detections"]:
        v, c = len(d.get("vignette") or "") * 3 // 4, len(d.get("contexte") or "") * 3 // 4
        print(f"        {d['lon']:.4f} {d['lat']:.4f} score {d['score']:.2f} longueur {d['longueur_m']:.0f} m largeur "
              f"{d['largeur_m']:.0f} m vitesse {d['vitesse_kn']:.1f} nœuds cap {d['cap_deg']:.0f} {d['type']} ; "
              f"nuages {d.get('nuages_pct')} % ; vignette {v / 1e3:.0f} Ko, contexte {c / 1e3:.0f} Ko")
    n = len(sc["detections"])
    if n < ATTENDUS or sc["masque"] != "present" or f.get("etat") != "lue" or not f.get("grille"):
        print(f"ÉCHEC  s2 : {n} navire(s) sur {ATTENDUS} attendus, masque {sc['masque']}, fenêtre {f.get('etat')}")
        return 1
    if any((d.get("nuages_pct") or 0) > 25 for d in sc["detections"]):
        print("ÉCHEC  s2 : un navire dégagé serait écarté comme nuageux (navire et sillage pris pour un nuage)")
        return 1
    if not all(d.get("vignette") and d.get("contexte") and d.get("vignette_geo") for d in sc["detections"]):
        print("ÉCHEC  s2 : vignette ou géoréférence manquante")
        return 1
    print(f"OK      s2 : {n} navires détectés, décrits et illustrés en {duree:.0f} s sur processeur "
          f"(inférence {f.get('analyse_s')} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
