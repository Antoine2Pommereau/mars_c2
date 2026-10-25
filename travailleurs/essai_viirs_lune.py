"""Essai du contrat d'entrée du modèle VIIRS d'allenai sous la lune (enquête du 25/10/2026), dans son image publique.

Scène synthétique de 768 × 1 600 pixels de DNB (mer profonde, fond de 0,3 nW, quatre bateaux de 20 à 60 nW), passée
au vrai modèle d'allenai (`model.vvd_cv_model`) dans quatre cas : lune à 50 % ou à 90 %, avec ou sans masque des
nuages (produit CLDMSK_L2, `phys_path` de `run_pipeline`). Notre travailleur n'en donne aucun : allenai remplit alors
le masque de zéros, c'est à dire un ciel couvert partout, et au dessus de 80 % de lune il éteint toute la scène.

    docker run --rm --platform linux/amd64 -w /src -v "$PWD/travailleurs:/mars:ro" --entrypoint python3 <image VIIRS> \\
      /mars/essai_viirs_lune.py      (ou : bash scripts/verifier_images.sh viirs-lune)
"""
import sys

sys.path.insert(0, "/src")
import numpy as np  # noqa: E402
from model import vvd_cv_model  # noqa: E402

H, W = 768, 1600
BATEAUX = [(200, 300, 20e-9), (400, 800, 35e-9), (600, 1200, 60e-9), (300, 1400, 25e-9)]


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


def main():
    res = {}
    for lune in (50.0, 90.0):
        for masque in (False, True):
            dets, _img = vvd_cv_model(scene(lune, masque))
            res[(lune, masque)] = len(dets)
            print(f"        lune {lune:.0f} %, masque des nuages {'fourni (ciel clair)' if masque else 'absent'} : "
                  f"{len(dets)} détections sur {len(BATEAUX)} bateaux")
    if res[(50.0, False)] >= 3 and res[(90.0, False)] == 0 and res[(90.0, True)] >= 3:
        print("OK      sans masque des nuages, au dessus de 80 % de lune, le modèle ne voit plus rien")
        return 0
    print("ÉCHEC   le comportement attendu n'est pas reproduit")
    return 1


if __name__ == "__main__":
    sys.exit(main())
