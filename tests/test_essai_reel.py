"""Lot Z6, phase 1 : coût par résultat (Coulisses, Budget), `taches.py essai-reel` (préparation et --lancer ID, types
jamais éprouvés refusés, budget des essais), bilan des nuits VIIRS. Essais sur base ignorés sans
MARS_TEST_DATABASE_URL ; aucun appel à Scaleway (faux client)."""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mars import controle, essai_reel
from mars import travailleurs as T
from mars.config import load_rules

ROOT = Path(__file__).resolve().parents[1]
RULES = load_rules()
URL = os.environ.get("MARS_TEST_DATABASE_URL")
MOIS = datetime(2026, 10, 1, tzinfo=timezone.utc)


# Coût par résultat (calcul pur)

def test_cost_per_result_counts_failures_and_divides_by_useful_results():
    travailleurs = [("sentinel", True, 3, 1, 1.61, 1.42), ("sentinel", False, 2, 0, 0.40, 0.40),
                    ("viirs", True, 8, 7, 0.16, 0.02), ("sentinel2", True, 3, 0, 0.57, 0.57)]
    resultats = [("sentinel1", True, 1), ("viirs", True, 6), ("viirs", False, 4), ("tip_and_cue", True, 2)]
    c = controle.cout_par_resultat(travailleurs, resultats, [(True, 0.38)])
    s1 = c["mois"]["sentinel1"]
    assert (s1["depense"], s1["depense_echecs"], s1["travailleurs"], s1["resultats"], s1["cout_par_resultat"]) == (
        1.61, 1.42, 3, 1, 1.61)
    assert c["total"]["sentinel1"]["depense"] == 2.01 and c["total"]["sentinel1"]["travailleurs"] == 5
    assert c["mois"]["viirs"]["cout_par_resultat"] == round(0.16 / 6, 2) and c["total"]["viirs"]["resultats"] == 10
    assert c["mois"]["sentinel2"]["cout_par_resultat"] is None                   # 0,57 € et aucun résultat
    assert c["mois"]["tip_and_cue"]["cout_par_resultat"] == 0.19 and c["mois"]["tip_and_cue"]["unite"] == "demande analysée"


def test_budget_panel_shows_cost_per_result():
    texte = (ROOT / "web" / "src" / "components" / "Reglages.tsx").read_text()
    assert "<CoutParResultat" in texte and "data-cout-resultat" in texte
    assert "cout_par_resultat" in (ROOT / "backend" / "controle.py").read_text()


# Sur base

@pytest.fixture
def conn(monkeypatch):
    if not URL:
        pytest.skip("MARS_TEST_DATABASE_URL absente : base de test requise")
    import psycopg
    c = psycopg.connect(URL)
    # Aucun travailleur en vie au départ (annulé avec la transaction)
    c.execute("UPDATE travailleurs SET detruit_le = now() WHERE detruit_le IS NULL")
    yield c
    c.rollback()
    c.close()


def _travailleur(conn, tache="sentinel", ctype="GP1-S", zone="fr-par-2", etat="termine", cout=0.19, detruit=True):
    return conn.execute(
        "INSERT INTO travailleurs (tache, commercial_type, zone, jeton_hash, etat, cout_reel_eur, cree_le, detruit_le) "
        "VALUES (%s, %s, %s, 'x', %s, %s, now() - interval '1 hour', %s) RETURNING id",
        (tache, ctype, zone, etat, cout, datetime.now(timezone.utc) if detruit else None)).fetchone()[0]


def _passage(conn, mission="S2"):
    return conn.execute("INSERT INTO sar_passes (product_name, acquired_at, mission, satellite, statut) VALUES "
                        "(%s, now() - interval '5 hours', %s, %s, 'acquis') RETURNING id",
                        (f"Z6_{mission}_{datetime.now().timestamp()}", mission,
                         "S2B" if mission == "S2" else "S1C")).fetchone()[0]


def _somme(conn, mois=MOIS):
    rows = [[tuple(r) for r in conn.execute(getattr(controle, n).format(p="%(m)s"), {"m": mois}).fetchall()]
            for n in ("COUT_TRAVAILLEURS_SQL", "COUT_RESULTATS_SQL", "COUT_TIP_SQL")]
    return controle.cout_par_resultat(*rows)


def test_cost_per_result_queries_on_the_database(conn):
    avant = _somme(conn)["total"]["sentinel1"]
    _travailleur(conn, cout=0.19)
    _travailleur(conn, ctype="RENDER-S", etat="echec", cout=1.23)
    pid = _passage(conn, "S1")
    conn.execute("INSERT INTO analyses (pass_id, aoi, mode, status, source, completed_at) VALUES (%s, "
                 "ST_MakeEnvelope(-6, 48, -5, 49, 4326)::geography, 'full', 'done', 'sentinel1', now())", (pid,))
    apres = _somme(conn)["total"]["sentinel1"]
    assert round(apres["depense"] - avant["depense"], 2) == 1.42
    assert round(apres["depense_echecs"] - avant["depense_echecs"], 2) == 1.23
    assert apres["resultats"] - avant["resultats"] == 1 and apres["cout_par_resultat"] is not None


def test_preparation_says_what_is_missing_and_never_launches(conn):
    lignes = {r: (s, d) for s, r, d in essai_reel.preparation(conn, RULES, datetime.now(timezone.utc), env={})}
    assert lignes["Travailleurs en vie"] == (essai_reel.PRET, "aucun")
    assert lignes["Clés et réseau privé"][0] == essai_reel.MANQUE and "SCW_SECRET_KEY" in lignes["Clés et réseau privé"][1]
    assert lignes["Sentinel 1 : GP1-S en fr-par-2"][1].endswith("éprouvé") or "jamais" in lignes["Sentinel 1 : GP1-S en fr-par-2"][1]
    assert "jamais éprouvé" in lignes["Sentinel 1 : POP2-8C-32G en fr-par-2"][1]
    wid = _travailleur(conn, tache="viirs", ctype="DEV1-M", zone="fr-par-1", etat="demarre", detruit=False)
    lignes = {r: (s, d) for s, r, d in essai_reel.preparation(conn, RULES, datetime.now(timezone.utc), env={})}
    assert lignes["Travailleurs en vie"][0] == essai_reel.MANQUE and f"{wid} viirs" in lignes["Travailleurs en vie"][1]
    assert "MANQUE" in essai_reel.rapport(list((s, r, d) for r, (s, d) in lignes.items()))


def test_launch_is_refused_while_a_worker_lives_or_the_trial_budget_is_spent(conn):
    assert "inconnu" in essai_reel.verifier_lancement(conn, RULES, 999_999_999)[1]
    pid = _passage(conn, "S2")
    assert essai_reel.verifier_lancement(conn, RULES, pid) == ("S2", None)
    wid = _travailleur(conn, etat="demarre", detruit=False)
    assert f"{wid} (sentinel, demarre)" in essai_reel.verifier_lancement(conn, RULES, pid)[1]
    conn.execute("UPDATE travailleurs SET detruit_le = now() WHERE id = %s", (wid,))
    cher = _travailleur(conn, cout=8.0)
    conn.execute("INSERT INTO task_runs (task, run_day, status, details) VALUES ('essai_reel', current_date, 'ok', %s)",
                 (json.dumps({"travailleur": cher}),))
    assert essai_reel.depense_essais(conn)[0] >= 8.0
    assert "budget des essais épuisé" in essai_reel.verifier_lancement(conn, RULES, pid)[1]


def test_never_proven_type_is_refused_during_a_trial_only(conn, monkeypatch):
    from test_travailleurs_base import FakeScw
    conn.execute("UPDATE travailleurs SET etat = 'echec' WHERE commercial_type = 'POP2-8C-32G' AND etat = 'termine'")
    pop2 = T.variantes(RULES, "sentinel")[1]
    monkeypatch.setattr(T, "ESSAI_EPROUVES_SEULS", True)
    r = T.launch(conn, FakeScw(), RULES, "sentinel", {"scenes": []}, {}, variante=pop2)
    assert "POP2-8C-32G en fr-par-2 jamais éprouvé" in r["refus"]
    _travailleur(conn, ctype="GP1-S")                                             # GP1-S éprouvé
    monkeypatch.delenv("MARS_IP_PRIVEE", raising=False)
    r = T.launch(conn, FakeScw(), RULES, "sentinel", {"scenes": []}, {})
    assert "jamais éprouvé" not in r.get("refus", "")                             # garde passée (refus suivant : réseau)
    monkeypatch.setattr(T, "ESSAI_EPROUVES_SEULS", False)
    r = T.launch(conn, FakeScw(), RULES, "sentinel", {"scenes": []}, {}, variante=pop2)
    assert "jamais éprouvé" not in r.get("refus", "")                             # hors essai : inchangé


def _args(**kw):
    a = argparse.Namespace(args=[], lancer=None, motif=None, malgre_pause=False, du=None)
    a.__dict__.update(kw)
    return a


def test_launch_command_goes_through_the_manual_launch_and_is_recorded(conn, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    import taches
    pid = _passage(conn, "S2")
    vu = {}

    def faux(c, pass_id, malgre_pause=False, motif_pause=None):
        vu.update(pass_id=pass_id, malgre_pause=malgre_pause, motif=motif_pause, eprouves=T.ESSAI_EPROUVES_SEULS,
                  plafond=os.environ.get("SENTINEL_PLAFOND_ESSAI"))
        wid = _travailleur(c, tache="sentinel2", etat="cree", cout=None, detruit=False)
        return {"travailleur": wid, "type": "GP1-S", "zone": "fr-par-2", "cout_estime_eur": 0.19}
    monkeypatch.setattr(taches, "launch_sentinel2", faux)
    monkeypatch.setattr(taches, "launch_sentinel", lambda *_a, **_k: pytest.fail("Sentinel 1 appelé pour un passage S2"))
    monkeypatch.setattr(T, "ESSAI_EPROUVES_SEULS", False)
    monkeypatch.setenv("SENTINEL_PLAFOND_ESSAI", "x")                       # enregistrée, donc retirée après le test
    monkeypatch.delenv("SENTINEL_PLAFOND_ESSAI")
    taches._essai_reel(conn, _args(lancer=pid))
    assert vu == {"pass_id": pid, "malgre_pause": True, "eprouves": True, "plafond": "1",
                  "motif": f"essai réel du lot Z6, passage {pid} (taches.py essai-reel --lancer)"}
    d = conn.execute("SELECT status, details FROM task_runs WHERE task = 'essai_reel' ORDER BY id DESC LIMIT 1").fetchone()
    assert d[0] == "ok" and d[1]["passage"] == pid and d[1]["mission"] == "S2" and d[1]["travailleur"]
    # Un second essai est refusé tant que ce travailleur vit, sans rien appeler
    monkeypatch.setattr(taches, "launch_sentinel2", lambda *_a, **_k: pytest.fail("second essai lancé"))
    with pytest.raises(SystemExit, match="REFUS : travailleur"):
        taches._essai_reel(conn, _args(lancer=pid))


def test_viirs_report_lists_every_night_with_its_detections_and_workers(conn):
    from mars import viirs
    hier = datetime.now(timezone.utc).date() - timedelta(days=1)
    avant = hier - timedelta(days=1)
    wid = _travailleur(conn, tache="viirs", ctype="DEV1-M", zone="fr-par-1", cout=0.02)
    gid = conn.execute(
        "INSERT INTO viirs_granules (nom, satellite, nuit, debut, fin, travailleur_id, detections) VALUES "
        "(%s, 'NOAA20', %s, now(), now(), %s, 3) RETURNING id", (f"Z6.{hier}", hier, wid)).fetchone()[0]
    conn.execute("INSERT INTO viirs_granules (nom, satellite, nuit, debut, fin, travailleur_id, erreur) VALUES "
                 "(%s, 'SNPP', %s, now(), now(), %s, 'NetCDF: Unknown file format')", (f"Z6b.{hier}", hier, wid))
    for k, (masque, non_eval) in enumerate(((None, None), (None, "coupure du flux"), ("cote", None))):
        conn.execute("INSERT INTO viirs_detections (granule_id, ts, geom, mask_reason, non_evaluable) "
                     "VALUES (%s, now(), ST_SetSRID(ST_MakePoint(%s, 48), 4326)::geography, %s, %s)",
                     (gid, -5 - k / 10, masque, non_eval))
    conn.execute("INSERT INTO alerts (type, severity, event_time, geom, rule_version, details) VALUES ('DARK_SHIP', "
                 "'faible', now(), ST_SetSRID(ST_MakePoint(-5, 48), 4326)::geography, 'z6', %s)",
                 (json.dumps({"source": "viirs", "nuit": str(hier)}),))
    b = viirs.bilan(conn, avant)
    nuits = {n["nuit"]: n for n in b["nuits"]}
    assert set(nuits) == {avant, hier} and nuits[avant]["granules"] == 0          # nuit manquée, listée
    n = nuits[hier]
    assert (n["granules"], n["en_erreur"], n["detections"], n["sans_ais"], n["non_evaluables"], n["ecartees"]) == (
        2, 1, 3, 1, 1, 1)
    assert n["alertes"] >= 1 and f"{wid} termine" in n["travailleurs"] and float(n["cout_eur"]) == 0.02
    assert b["total"]["nuits"] == 2 and b["total"]["nuits_traitees"] == 1
