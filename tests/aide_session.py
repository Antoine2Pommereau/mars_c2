"""Aide des tests d'API (lot Z1) : toute écriture exige une session d'opérateur. Connecte un opérateur d'essai sur un
TestClient (mot de passe haché à 1 000 itérations pour la vitesse)."""
MOT_DE_PASSE = "bon mot de passe"


def connecter(client, monkeypatch, nom: str = "antoine"):
    from mars import controle
    monkeypatch.setenv("MARS_OPERATEURS", f"{nom}:{controle.hacher(MOT_DE_PASSE, iterations=1000)}")
    r = client.post("/api/controle/connexion", json={"operateur": nom, "mot_de_passe": MOT_DE_PASSE})
    assert r.status_code == 200, r.text
    return nom
