"""Choix des passages antérieurs Sentinel 1 (analyse 106, 27/10/2026), sur les géométries réelles du catalogue de
Copernicus (tests/donnees/s1_historique_106_103.json) : la fenêtre NOSTOS dépassait l'image du jour de 19 % ; exiger
ses quatre coins écartait tous les passages de la même trace, et le motif disait « aucun passage antérieur ». Désormais
seule la part de la fenêtre dans l'image du jour compte ; l'analyse 103 (fenêtre entière dans l'image) est inchangée."""
import copy
import json
from pathlib import Path

from mars import sentinel as S1
from mars.config import load_rules

S = load_rules()["sentinel"]
DONNEES = json.loads((Path(__file__).parent / "donnees" / "s1_historique_106_103.json").read_text())


class Catalogue:
    def __init__(self, feats):
        self.feats = feats

    def post(self, *_a, **_k):
        feats = self.feats

        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"features": feats}
        return R()


def _scene(cas, fenetres=None, points=None):
    sc = S1.scene_from_item(DONNEES[cas]["scene"])
    sc["fenetres"] = fenetres or S1.fenetres(sc["emprise"], points, S)
    for k, f in enumerate(sc["fenetres"]):
        f["id"] = k
    return sc


NOSTOS_ALTAI = [{"lon": 0.30427, "lat": 50.57482}, {"lon": 1.73215, "lat": 51.19988}]


def test_106_old_rule_rejected_every_pass_of_the_same_track():
    sc = _scene("106", points=NOSTOS_ALTAI)
    nostos = sc["fenetres"][0]
    assert not S1.covers(sc["emprise"], nostos["bbox"][0], nostos["bbox"][1], 0)     # coin hors de l'image du jour
    cands = [S1.scene_from_item(f) for f in DONNEES["106"]["candidats"]]
    assert not any(c and S1._couvre_fenetres(c["emprise"], sc["fenetres"]) for c in cands)
    # Emprise du catalogue : 3 points sur 81 hors de l'image (l'image réelle en perd davantage, bande sans données)
    assert sum(1 for p in S1._grille(nostos["bbox"]) if S1.covers(sc["emprise"], *p, 0)) == 78


def test_106_now_gets_the_two_previous_passes_with_an_exact_motive():
    sc = _scene("106", points=NOSTOS_ALTAI)
    h, motif = S1.historique(Catalogue(DONNEES["106"]["candidats"]), sc, S)
    assert [(x["debut"][:10], x["satellite"]) for x in h] == [("2026-10-19", "S1C"), ("2026-10-13", "S1D")]
    assert motif == "2 passages antérieurs (19/10/2026 S1C, 13/10/2026 S1D)"
    assert not any(f.get("historique") for f in sc["fenetres"])        # les mêmes passages pour les deux fenêtres


def test_103_is_unchanged():
    fen = [{"bbox": [-2.53648, 50.45449, -2.19708, 50.67153], "marge_m": 2000, "points": 1, "km2": 600}]
    sc = _scene("103", fenetres=fen)
    h, motif = S1.historique(Catalogue(DONNEES["103"]["candidats"]), sc, S)
    assert [(x["debut"][:10], x["satellite"]) for x in h] == [("2026-10-17", "S1C"), ("2026-10-11", "S1D")]
    # Règle d'avant (quatre coins) : même choix
    anciens = sorted((c for c in (S1.scene_from_item(f) for f in DONNEES["103"]["candidats"]) if c
                      and c["orbite_relative"] == sc["orbite_relative"] and c["orbite"] == sc["orbite"]
                      and c["debut"] < sc["debut"] and S1._couvre_fenetres(c["emprise"], fen)),
                     key=lambda c: c["debut"], reverse=True)
    assert [x["id"] for x in h] == [c["id"] for c in anciens][:2]


def test_passes_chosen_per_window_when_none_fits_all():
    """Le passage du 19/10 rogné pour ne plus couvrir la fenêtre ALTAI : il ne sert qu'à NOSTOS."""
    cands = copy.deepcopy(DONNEES["106"]["candidats"])
    for f in cands:
        if "20261019T174030" in f["id"]:
            f["geometry"] = {"type": "Polygon", "coordinates": [[[0.0, 50.3], [0.8, 50.3], [0.8, 50.8], [0.0, 50.8],
                                                                  [0.0, 50.3]]]}
        if "20261007" in f["id"] or "20261001" in f["id"]:
            f["properties"]["sat:relative_orbit"] = 0             # hors jeu : seul reste le 13/10 en entier
    sc = _scene("106", points=NOSTOS_ALTAI)
    h, motif = S1.historique(Catalogue(cands), sc, S)
    assert [x["debut"][:10] for x in h] == ["2026-10-13"]
    assert [x["debut"][:10] for x in sc["fenetres"][0]["historique"]] == ["2026-10-19", "2026-10-13"]
    assert "historique" not in sc["fenetres"][1]
    assert motif.startswith("passages antérieurs choisis par fenêtre : fenêtre 0 : 19/10/2026 S1C, 13/10/2026 S1D")


def test_no_pass_covering_the_window_says_so():
    cands = copy.deepcopy(DONNEES["106"]["candidats"])
    for f in cands:
        f["geometry"] = {"type": "Polygon", "coordinates": [[[5, 40], [6, 40], [6, 41], [5, 41], [5, 40]]]}
    sc = _scene("106", points=NOSTOS_ALTAI)
    h, motif = S1.historique(Catalogue(cands), sc, S)
    assert h == [] and "aucun ne couvre la part dans l'image du jour de la fenêtre 0, 1" in motif
    assert "orbite 59 trouvé(s)" in motif and "0,95" in motif


def test_guard_reports_a_window_analysed_without_history(monkeypatch):
    import os
    import pytest
    url = os.environ.get("MARS_TEST_DATABASE_URL")
    if not url:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    from mars import controle
    envois = []
    monkeypatch.setattr(controle, "notifier", lambda *a, **k: envois.append(a) or "envoyer")
    rules = load_rules()
    with psycopg.connect(url) as conn:
        pid = conn.execute("INSERT INTO sar_passes (product_name, acquired_at, mission, satellite) VALUES "
                           "('S1D_ESSAI_GARDE', now(), 'S1', 'S1D') RETURNING id").fetchone()[0]
        aid = conn.execute(
            "INSERT INTO analyses (pass_id, aoi, mode, status, source, scenes, requested_at, summary) VALUES "
            "(%s, ST_MakeEnvelope(0, 50, 1, 51, 4326)::geography, 'full', 'running', 'sentinel1', %s, now(), '{}') "
            "RETURNING id", (pid, json.dumps([{"id": "S1D_106", "historique_motif": "motif du choix"}]))).fetchone()[0]
        mes = {"S1D_106": {}}
        scenes = [{"id": "S1D_106", "fenetres": [{"index": 0, "historique": []},
                                                {"index": 1, "historique": ["S1C_A", "S1D_B"]}]}]
        sans = S1._garde_historique(conn, aid, scenes, mes, rules)
        assert sans == [{"scene": "S1D_106", "fenetres": [0], "motif": "motif du choix"}]
        assert mes["S1D_106"]["historique_motif"] == "motif du choix"
        assert len(envois) == 1 and envois[0][0] == "travailleurs" and f"analyse Sentinel 1 {aid}" in envois[0][2]
        # Une analyse déjà signalée (lots suivants) ne notifie plus
        conn.execute("UPDATE analyses SET summary = '{\"sans_historique\": []}' WHERE id = %s", (aid,))
        S1._garde_historique(conn, aid, scenes, mes, rules)
        assert len(envois) == 1
        conn.rollback()
