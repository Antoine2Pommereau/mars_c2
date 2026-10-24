"""Lot Z1 : toute route d'écriture de l'API exige une session d'opérateur, sauf la liste blanche ECRITURES_LIBRES de
backend/controle.py. Le test parcourt toutes les routes de l'application : une route d'écriture ajoutée plus tard sans
`Depends(exiger_operateur)` et hors de la liste le fait échouer. Puis chaque route protégée est appelée sans session
(aucune base requise : le refus a lieu avant toute requête SQL)."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
pytest.importorskip("fastapi")


@pytest.fixture(scope="module")
def api():
    # Un test précédent a pu charger travailleurs/sentinel.py sous le nom « sentinel » : modules homonymes du dossier
    # backend retirés du cache pour que l'application importe les siens
    for nom in [p.stem for p in (ROOT / "backend").glob("*.py")]:
        m = sys.modules.get(nom)
        if m is not None and Path(getattr(m, "__file__", "") or "").parent != ROOT / "backend":
            del sys.modules[nom]
    if str(ROOT / "backend") in sys.path:
        sys.path.remove(str(ROOT / "backend"))
    sys.path.insert(0, str(ROOT / "backend"))
    import app as module
    import controle
    return module.app, controle


def _routes(routes):
    """Routes à plat : FastAPI récent enveloppe les routeurs inclus (_IncludedRouter, original_router)."""
    for r in routes:
        if hasattr(r, "original_router"):
            yield from _routes(r.original_router.routes)
        elif hasattr(r, "routes") and not hasattr(r, "dependant"):
            yield from _routes(r.routes)
        else:
            yield r


def _appels(dependant):
    for d in dependant.dependencies:
        yield d.call
        yield from _appels(d)


def _ecritures(app, controle):
    for r in _routes(app.routes):
        for m in sorted((getattr(r, "methods", None) or set()) & controle.ECRITURE):
            yield m, r


def test_every_write_route_is_protected_or_whitelisted(api):
    app, controle = api
    nues, protegees = [], 0
    for m, r in _ecritures(app, controle):
        if controle.exiger_operateur in set(_appels(r.dependant)):
            protegees += 1
            assert (m, r.path) not in controle.ECRITURES_LIBRES, f"{m} {r.path} : protégée et dans la liste blanche"
        elif (m, r.path) not in controle.ECRITURES_LIBRES:
            nues.append(f"{m} {r.path}")
    assert not nues, f"routes d'écriture sans session ni place dans ECRITURES_LIBRES : {nues}"
    assert protegees >= 18       # plancher : 20 au lot Z4 (horloge partagée, chaîne du Mac et règles à la demande retirées)


def test_whitelist_is_short_and_points_to_real_routes(api):
    app, controle = api
    reelles = {(m, r.path) for m, r in _ecritures(app, controle)}
    assert set(controle.ECRITURES_LIBRES) <= reelles, "entrée de la liste blanche sans route"
    assert len(controle.ECRITURES_LIBRES) <= 6
    for (m, chemin), motif in controle.ECRITURES_LIBRES.items():
        assert motif, f"{m} {chemin} : motif de la liste blanche vide"
        assert chemin.startswith(("/api/controle/", "/api/travailleurs/"))


def _chemin(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def test_protected_routes_refuse_without_session_cross_site_and_in_demo(api):
    from fastapi.testclient import TestClient
    import demo
    app, controle = api
    c = TestClient(app)                     # sans lifespan : aucune base, le refus précède toute requête SQL
    vues = 0
    for m, r in _ecritures(app, controle):
        if (m, r.path) in controle.ECRITURES_LIBRES:
            continue
        url = _chemin(r.path)
        # Requête intersite « simple » : corps JSON sans Content-Type (fetch no-cors), ou text/plain
        assert c.request(m, url, content=b'{"action": "classer"}').status_code == 415, f"{m} {url} sans Content-Type"
        assert c.request(m, url, content=b"{}", headers={"content-type": "text/plain"}).status_code == 415
        # JSON correct mais aucune session
        assert c.request(m, url, json={}).status_code == 401, f"{m} {url} sans session"
        # Autre origine : refusée avant tout
        assert c.request(m, url, json={}, headers={"origin": "https://ailleurs.example"}).status_code == 403
        # Mode démonstration : lecture seule
        c.cookies.set(demo.COOKIE, "0" * 32, path="/api")
        assert c.request(m, url, json={}).status_code == 403, f"{m} {url} en démonstration"
        c.cookies.clear()
        vues += 1
    assert vues >= 18


def test_author_never_comes_from_the_request_body():
    """Aucun modèle de corps d'une route d'écriture ne déclare de champ auteur : l'auteur vient de la session."""
    for f in ("app.py", "contenu.py", "dossiers.py", "demandes.py", "controle.py"):
        texte = (ROOT / "backend" / f).read_text()
        assert not re.search(r"^\s+author: str", texte, re.M), f"backend/{f} : champ author dans un corps"
        assert ".author or" not in texte, f"backend/{f} : auteur lu dans le corps"
