"""Historique du détecteur Sentinel 1 (lot historique, 25/10/2026) : choix des passages antérieurs par le serveur,
seuil du travailleur avec ou sans historique, fiche de la détection.

Le comportement du modèle avec et sans historique, sur les vraies scènes publiques des analyses 103 et 97, est éprouvé
dans l'image d'AI2 par `scripts/verifier_images.sh ai2-historique` (environ 40 min sur le Mac)."""
import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from mars import sentinel  # noqa: E402
from sentinel import historique_de  # noqa: E402   backend/sentinel.py, importé comme par l'API
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text())
S = RULES["sentinel"]
FENETRE = {"bbox": [-2.53648, 50.45449, -2.19708, 50.67153], "marge_m": 2000, "id": 0}
SCENE = {"id": "S1D_IW_GRDH_1SDV_20261023T175704", "debut": "2026-10-23T17:57:04Z", "orbite": "ascending",
         "orbite_relative": 30, "fenetres": [FENETRE]}


def _poly(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


GRANDE, PETITE = _poly(-4, 49.5, -1, 51.5), _poly(-2.4, 50.5, -2.3, 50.6)


def _item(ident, debut, orbite=30, sens="ascending", emprise=GRANDE, plateforme="sentinel-1c"):
    return {"id": ident, "geometry": emprise,
            "properties": {"start_datetime": debut, "end_datetime": debut, "datetime": debut, "platform": plateforme,
                           "sat:orbit_state": sens, "sat:relative_orbit": orbite, "sar:instrument_mode": "IW"},
            "assets": {"vv": {"href": f"s3://eodata/x/{ident}/vv.tiff"}, "vh": {"href": f"s3://eodata/x/{ident}/vh.tiff"}}}


class Session:
    def __init__(self, items):
        self.items, self.corps = items, []

    def post(self, _url, json=None, **_kw):
        self.corps.append(json)
        items = self.items

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"features": items}
        return R()


def test_deux_passages_les_plus_recents_de_la_meme_orbite():
    items = [_item("S1C_17", "2026-10-17T17:56:51Z"), _item("S1D_11", "2026-10-11T17:57:04Z", plateforme="sentinel-1d"),
             _item("S1C_05", "2026-10-05T17:56:51Z"), _item("AUTRE_ORBITE", "2026-10-20T06:23:04Z", orbite=154),
             _item("DESCENDANT", "2026-10-21T17:00:00Z", sens="descending"),
             _item("NE_COUVRE_PAS", "2026-10-22T17:57:00Z", emprise=PETITE),
             _item("MEME_DATE", "2026-10-17T17:56:26Z")]           # tranche précédente du même passage
    sess = Session(items)
    h, motif = sentinel.historique(sess, SCENE, S)
    assert [x["id"] for x in h] == ["S1C_17", "S1D_11"]
    assert h[0]["vv"].endswith("vv.tiff") and h[1]["satellite"] == "S1D"
    assert motif == "2 passages antérieurs (17/10/2026 S1C, 11/10/2026 S1D)"
    corps = sess.corps[0]
    assert corps["datetime"].startswith("2026-09-29T17:57:04Z/") and corps["bbox"] == FENETRE["bbox"]


def test_un_seul_passage_ou_aucun_le_dit():
    h, motif = sentinel.historique(Session([_item("S1C_17", "2026-10-17T17:56:51Z")]), SCENE, S)
    assert len(h) == 1 and motif.startswith("un seul passage antérieur dans les 24 jours")
    h, motif = sentinel.historique(Session([_item("VIEUX", "2026-09-01T17:56:51Z")]), SCENE, S)
    assert h == []                                                  # hors des 24 jours
    h, motif = sentinel.historique(Session([]), SCENE, S)
    assert h == [] and motif == "aucun passage antérieur de l'orbite 30 dans les 24 jours : sans historique, seuil prudent de 0,95"


def test_historique_coupe_ou_orbite_inconnue_sans_appel_au_catalogue():
    sess = Session([_item("S1C_17", "2026-10-17T17:56:51Z")])
    h, motif = sentinel.historique(sess, SCENE, {**S, "historique": {**S["historique"], "passages": 0}})
    assert h == [] and "coupé" in motif
    h, motif = sentinel.historique(sess, {**SCENE, "orbite_relative": None}, S)
    assert h == [] and "inconnue" in motif and sess.corps == []


def test_orbite_relative_lue_dans_le_catalogue():
    sc = sentinel.scene_from_item(_item("S1D_23", "2026-10-23T17:57:04Z", plateforme="sentinel-1d"))
    assert sc["orbite_relative"] == 30 and sc["satellite"] == "S1D"


def test_seuil_du_travailleur():
    spec = importlib.util.spec_from_file_location("travailleur_sentinel_h", ROOT / "travailleurs" / "sentinel.py")
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    task = {"confiance": S["confiance"], "confiance_sans_historique": S["historique"]["confiance_sans_historique"]}
    assert w.seuil(task, 2) == 0.9 and w.seuil(task, 1) == 0.9 and w.seuil(task, 0) == 0.95
    assert w.seuil({"confiance": 0.9}, 0) == 0.9                     # tâche d'avant l'historique
    assert w.nom_local("vv").split("-")[3] == "vv"                    # AI2 lit la polarisation dans ce champ


def test_fiche_detection_historique():
    sc = "S1D_IW_GRDH_1SDV_20261023T175704"
    t = {sc: {"historique": {"prevus": ["a", "b"], "passages_min": 2},
              "fenetres": [{"index": 0, "confiance": 0.9}]}}
    assert historique_de(t, sc) == {"passages": 2, "prevus": 2, "seuil": 0.9, "motif": None}
    t[sc]["historique"]["passages_min"] = 0
    t[sc]["fenetres"][0]["confiance"] = 0.95
    t[sc]["historique_motif"] = "aucun ne couvre la fenêtre 0"          # garde de l'analyse 106 : motif dans la fiche
    assert historique_de(t, sc) == {"passages": 0, "prevus": 2, "seuil": 0.95, "motif": "aucun ne couvre la fenêtre 0"}
    assert historique_de({sc: {"fenetres": []}}, sc) is None            # analyse antérieure à l'historique
