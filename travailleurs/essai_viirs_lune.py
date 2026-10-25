"""Essai du contrat d'entrée du modèle VIIRS d'allenai sous la lune (enquête du 25/10/2026), dans son image publique.

1. Modèle seul (`model.vvd_cv_model`) : scène synthétique de 768 × 1 600 pixels de DNB (mer profonde, fond de 0,3 nW,
   quatre bateaux de 20 à 60 nW), lune de 50 à 90 %, avec ou sans masque des nuages (produit CLDMSK_L2,
   `phys_path` de `run_pipeline`). Sans masque, allenai remplit le masque de zéros (ciel couvert partout) et, au
   dessus de 80 % de lune moyenne, éteint toute la scène : 4 bateaux sur 4 jusqu'à 80 %, 0 au dessus.
2. Chaîne complète (`run_pipeline`, tous les filtres, fichiers NetCDF d'essai_viirs_masque.py), bateaux faibles de 20
   à 40 nW, sans masque, de 60 à 80 % : son classifieur final (feedback_cnn, qui reçoit la lune en canal) les écarte
   peu à peu, bien avant 80 % (mesure du 25/10/2026 : 4 sur 4 jusqu'à 58 %, 3 à 60 %, 2 à 65 %, 1 à 70 %, 0 à
   75 %). D'où le seuil de 55 % au dessus duquel une absence de détection ne prouve rien (`viirs.lune_max_absence`).

    bash scripts/verifier_images.sh viirs-lune
"""
import sys
import tempfile

sys.path.insert(0, "/src")
sys.path.insert(0, "/mars")
import numpy as np  # noqa: E402
from model import vvd_cv_model  # noqa: E402

H, W = 768, 1600
BATEAUX = [(200, 300, 20e-9), (400, 800, 35e-9), (600, 1200, 60e-9), (300, 1400, 25e-9)]
FAIBLES = (20.0, 30.0, 40.0, 25.0)


def scene(lune: float, masque: bool) -> dict:
    rng = np.random.default_rng(1)
    dnb = np.abs(rng.normal(0.3e-9, 0.05e-9, (H, W))).astype("float32")
    for r, c, v in BATEAUX:
        dnb[r, c] = v
    lat, lon = np.meshgrid(np.linspace(44, 50, H), np.linspace(-8, 0, W), indexing="ij")
    return {"dnb": {"data": dnb, "metadata": {"valid_min": 0.0, "valid_max": 1e-6}, "quality": np.zeros((H, W))},
            "latitude": lat, "longitude": lon, "land_sea_mask": np.full((H, W), 7, dtype=np.uint8),
            "moonlight": np.full((H, W), lune),
            "cloud_mask": np.ones((H, W), dtype="float32") if masque else np.zeros((H, W), dtype="float32"), "m10_band": None}


def chaine(lunes: tuple) -> dict:
    """Bateaux faibles retrouvés par la chaîne complète d'allenai, sans masque, pour chaque lune."""
    import essai_viirs_masque as e
    from pipeline import VIIRSVesselDetection
    e.BATEAUX = [(r, c, v) for (r, c, _v), v in zip(e.BATEAUX, FAIBLES)]
    vvd, out = VIIRSVesselDetection(), {}
    for lune in lunes:
        d = tempfile.mkdtemp()
        e.ecrire_dnb(d + "/dnb.nc")
        e.ecrire_geo(d + "/geo.nc", float(lune))
        dets = vvd.run_pipeline(d + "/dnb.nc", d + "/geo.nc", d, None)[0]["vessel_detections"].values()
        out[lune] = e.retrouves([{"lat": v["latitude"], "lon": v["longitude"]} for v in dets])[0]
    return out


def main():
    res = {}
    for lune in (50.0, 60.0, 65.0, 70.0, 75.0, 80.0, 80.5, 90.0):
        for masque in (False, True):
            dets, _img = vvd_cv_model(scene(lune, masque))
            res[(lune, masque)] = len(dets)
        print(f"        modèle seul, lune {lune:g} % : sans masque {res[(lune, False)]}, avec masque "
              f"{res[(lune, True)]} détections sur {len(BATEAUX)} bateaux")
    ch = chaine((55, 58, 60, 65, 70, 75, 80))
    for lune, n in ch.items():
        print(f"        chaîne complète, bateaux de 20 à 40 nW, sans masque, lune {lune} % : {n} sur {len(FAIBLES)}")
    ok = (all(res[(lu, False)] >= 3 for lu in (50.0, 60.0, 65.0, 70.0, 75.0, 80.0))
          and res[(80.5, False)] == 0 and res[(90.0, False)] == 0 and res[(90.0, True)] >= 3
          and ch[55] == 4 and ch[58] == 4 and ch[60] <= 3 and ch[70] <= 1 and ch[75] == 0)
    if ok:
        print("OK      modèle seul éteint au dessus de 80 % sans masque ; chaîne complète : bateaux faibles perdus dès "
              "60 % (seuil d'absence à 55 %)")
        return 0
    print("ÉCHEC   le comportement attendu n'est pas reproduit")
    return 1


if __name__ == "__main__":
    sys.exit(main())
