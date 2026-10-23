"""Prototype du modèle des dossiers (lot U, phase 1) : regroupement par navire et par point, niveau de suspicion à
trois niveaux, explication en une phrase, sur la base de test. Sert aux maquettes (generer.py) et à éprouver le modèle
avant la route /api/dossiers de la phase 2 ; aucune écriture.

Règles (docs/ux_refonte.md, section 6) :
* Fenêtre : alertes ouvertes (nouvelle, acquittée, confirmée) dont l'instant est dans les 72 heures précédant l'instant
  affiché et déjà levées à cet instant ; un dossier dont toutes les alertes sont classées est clos (rouvert par une
  nouvelle alerte).
* Navire : toute alerte qui a le navire pour preuve (alert_evidence vessel). Point : alerte sans navire (lumière VIIRS,
  écho radar ou optique sans AIS) ; rattaché au dossier d'un navire quand une vérification satellite les relie (même
  détection en preuve), sinon fusionné avec un autre point à moins de 2 km et 6 heures.
* Niveau : base selon la gravité la plus haute (critique 3, élevée 2, moyenne et faible 1) ; plus un si deux familles
  de signaux indépendantes au moins se recoupent ; plus un si une vérification satellite a vu le navire sans AIS ;
  ramené à 1 si tous les signaux sont « à confirmer » ; borné de 1 à 3. Chaque contribution est listée.
* Phrase : un fragment par signal, du plus fort au plus faible, trois au plus, cinq mots chacun au plus.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

OUVERTS = ("nouvelle", "acquittee", "confirmee")
NIVEAUX = {1: "À noter", 2: "À examiner", 3: "Urgent"}
BASE = {"critique": 3, "elevee": 2, "moyenne": 1, "faible": 1}
FAMILLE = {"WATCHLIST": "liste", "AIS_GAP": "ais", "AIS_UNCONFIRMED": "ais", "RENDEZVOUS": "comportement",
           "IDENTITY_CHANGE": "identite", "IDENTITY_MISMATCH": "identite", "DARK_SHIP": "capteur"}
NIVEAUX_LISTE = {"fort": "Sur liste GUR", "sanctionne": "Sanctionné", "flotte_fantome": "Flotte fantôme",
                 "suspect_gur": "Suspect (GUR)"}


@dataclass
class Signal:
    famille: str
    alerte: int
    type: str
    gravite: str
    t: datetime
    fragment: str
    poids: int
    a_confirmer: bool = False
    verifie_sans_ais: bool = False


@dataclass
class Dossier:
    cle: str
    nom: str
    pavillon: str | None
    lon: float
    lat: float
    signaux: list = field(default_factory=list)
    niveau: int = 1
    contributions: list = field(default_factory=list)
    phrase: str = ""
    dernier: datetime | None = None


def _duree(minutes: float) -> str:
    return f"{round(minutes / 60)} h" if minutes >= 90 else f"{round(minutes)} min"


def fragment(typ: str, d: dict, infra: str | None) -> str:
    """Cinq mots au plus, vocabulaire d'opérateur (glossaire de docs/ux_refonte.md)."""
    if typ == "WATCHLIST":
        return NIVEAUX_LISTE.get(d.get("niveau"), "Navire des listes")
    if typ == "AIS_GAP":
        f = f"AIS coupé {_duree(float(d.get('duree_min') or 0))}"
        if not infra:
            return f
        return f + (f" près d'un {infra[3:]}" if infra.startswith("un ") else f" près de {infra}")   # sans nom : « d'un câble »
    if typ == "AIS_UNCONFIRMED":
        return "Déclaré mais non vu"
    if typ == "DARK_SHIP":
        return {"viirs": "Lumière la nuit, sans AIS", "sentinel2": "Vu en image, sans AIS"}.get(
            d.get("source"), "Écho radar sans AIS")
    if typ == "RENDEZVOUS":
        return f"Rendez vous de {_duree(float(d.get('duree_min') or 0))}"
    if typ == "IDENTITY_CHANGE":
        return "Nom changé" if d.get("changement") == "nom" else "Même OMI, autre MMSI"
    if typ == "IDENTITY_MISMATCH":
        return "Taille déclarée contredite"
    return typ


def evaluer(dossier: Dossier) -> Dossier:
    s = dossier.signaux
    base = max(BASE.get(x.gravite, 1) for x in s)
    contrib = [f"gravité la plus haute : {max(s, key=lambda x: BASE.get(x.gravite, 1)).gravite} (niveau {base})"]
    niveau = base
    familles = {x.famille for x in s}
    if len(familles) >= 2:
        niveau += 1
        contrib.append(f"signaux recoupés : {', '.join(sorted(familles))} (plus un)")
    if any(x.verifie_sans_ais for x in s):
        niveau += 1
        contrib.append("vérification satellite : vu sans AIS (plus un)")
    if all(x.a_confirmer for x in s):
        niveau = 1
        contrib.append("tous les signaux sont à confirmer (ramené à 1)")
    dossier.niveau = max(1, min(3, niveau))
    ordre = sorted(s, key=lambda x: (-x.poids, -BASE.get(x.gravite, 1), x.t))
    # Un fragment par type : plusieurs alertes du même type se résument (« AIS coupé 2 fois, 5 h et 3 h »)
    par_type: dict = {}
    for x in ordre:
        par_type.setdefault(x.type, []).append(x)
    frags = []
    for typ, xs in par_type.items():
        if typ == "AIS_GAP" and len(xs) > 1:
            frags.append(f"AIS coupé {len(xs)} fois")
        else:
            frags.append(xs[0].fragment)
    if any(x.verifie_sans_ais for x in s):
        frags = [f for f in frags if f != "Écho radar sans AIS"]
        frags.insert(min(2, len(frags)), "revu au radar sans AIS")
    p = ", ".join(frags[:3])
    dossier.phrase = p[:1].upper() + p[1:]
    dossier.contributions = contrib
    dossier.dernier = max(x.t for x in s)
    return dossier


POIDS = {"WATCHLIST": 3, "AIS_GAP": 2, "DARK_SHIP": 2, "RENDEZVOUS": 2, "IDENTITY_CHANGE": 1, "IDENTITY_MISMATCH": 1,
         "AIS_UNCONFIRMED": 1}


def dossiers(conn, instant: datetime, fenetre_h: int = 72, nom=lambda n: n) -> list[Dossier]:
    """Dossiers ouverts à `instant` (psycopg) ; `nom` : masquage éventuel des noms (démonstration)."""
    rows = conn.execute(
        """SELECT a.id, a.type, a.severity, a.status, a.event_time, a.details, ST_X(a.geom::geometry), ST_Y(a.geom::geometry),
                  (SELECT min(evidence_id) FROM alert_evidence e WHERE e.alert_id = a.id AND e.evidence_type = 'vessel'),
                  (SELECT array_agg(evidence_id) FROM alert_evidence e WHERE e.alert_id = a.id
                     AND e.evidence_type = 'detection'),
                  (SELECT coalesce(i.name, 'un ' || lower(i.attrs->>'type')) FROM infrastructure i
                     WHERE ST_DWithin(i.geom, a.geom, 3704) ORDER BY i.geom <-> a.geom LIMIT 1)
           FROM alerts a WHERE a.event_time BETWEEN %s AND %s AND a.detected_at <= %s ORDER BY a.event_time""",
        (instant - timedelta(hours=fenetre_h), instant, instant)).fetchall()
    navires: dict = {}
    points: list = []
    for aid, typ, sev, st, t, d, lon, lat, vid, dets, infra in rows:
        if st not in OUVERTS:
            continue
        d = d or {}
        verif = (d.get("verification_satellite") or {}).get("resultat") == "present_sans_ais"
        sig = Signal(FAMILLE.get(typ, "autre") if typ != "DARK_SHIP" else "capteur", aid, typ, sev, t,
                     fragment(typ, d, infra), POIDS.get(typ, 1), bool(d.get("a_confirmer")), verif)
        if vid is not None:
            if vid not in navires:
                v = conn.execute("SELECT name, flag FROM vessels WHERE id = %s", (vid,)).fetchone()
                navires[vid] = Dossier(f"navire:{vid}", nom(v[0]) if v and v[0] else "Sans nom", v[1] if v else None,
                                       lon, lat)
            navires[vid].signaux.append(sig)
            navires[vid].lon, navires[vid].lat = lon, lat
            navires[vid].detections = set(getattr(navires[vid], "detections", set())) | set(dets or [])
        else:
            points.append((sig, lon, lat, set(dets or [])))
    out = list(navires.values())
    for sig, lon, lat, dets in points:
        lie = next((n for n in out if dets & getattr(n, "detections", set())), None)
        if lie is not None:                     # vérification satellite : le point appartient au dossier du navire
            sig.famille = "capteur"
            lie.signaux.append(sig)
            continue
        out.append(Dossier(f"point:{sig.alerte}", {"viirs": "Lumière en mer"}.get(
            sig.fragment.startswith("Lumière") and "viirs", "Écho sans AIS"), None, lon, lat, [sig]))
    return sorted((evaluer(x) for x in out), key=lambda x: (-x.niveau, -len(x.signaux), -x.dernier.timestamp()))
