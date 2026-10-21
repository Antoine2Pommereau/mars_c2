"""Dossiers (lot U, refonte de l'interface) : la question de l'opérateur est « quel navire regarder maintenant, et
pourquoi ? ». Un dossier réunit les alertes ouvertes d'un navire (ou d'un point sans navire), avec un niveau de
suspicion à trois niveaux et une phrase d'explication tirée des faits. Calcul pur, sur les alertes lues par l'API
(backend/dossiers.py) ; le moteur des alertes ne change pas. Règles : docs/ux_refonte.md, section 8, et la section
`dossiers` de config/rules.yaml.

* Navire : alertes qui ont le navire pour preuve (alert_evidence vessel). Point : alerte sans navire (lumière VIIRS,
  écho radar ou optique sans AIS), rattaché au dossier d'un navire quand une vérification satellite les relie (même
  détection en preuve), sinon fusionné avec un autre point à moins de point_fusion_m et point_fusion_h.
* Niveau : base selon la gravité la plus haute (navire des listes seul : À noter, sauf en mer près d'une
  infrastructure ou en séjour prolongé surtout à l'arrêt hors port : À examiner) ; plus un si deux familles de signaux au moins se recoupent ; plus
  un si une vérification satellite a vu le navire sans AIS ; ramené à 1 si tout est à confirmer ; de 1 à 3.
* Phrase : un fragment par type de signal, du plus fort au plus faible, trois au plus.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

OUVERTS = ("nouvelle", "acquittee", "confirmee")
NIVEAUX = {1: "a_noter", 2: "a_examiner", 3: "urgent"}
FAMILLE = {"WATCHLIST": "liste", "AIS_GAP": "ais", "AIS_UNCONFIRMED": "ais", "RENDEZVOUS": "comportement",
           "IDENTITY_CHANGE": "identite", "IDENTITY_MISMATCH": "identite", "DARK_SHIP": "satellite"}
POIDS = {"WATCHLIST": 3, "AIS_GAP": 2, "DARK_SHIP": 2, "RENDEZVOUS": 2, "IDENTITY_CHANGE": 1, "IDENTITY_MISMATCH": 1,
         "AIS_UNCONFIRMED": 1}
LISTES = {"fort": "Sur liste GUR", "sanctionne": "Sanctionné", "flotte_fantome": "Flotte fantôme",
          "suspect_gur": "Suspect (GUR)"}
NOMS_FAMILLES = {"liste": "liste", "ais": "AIS", "satellite": "satellite", "comportement": "comportement",
                 "identite": "identité"}


def duree(minutes: float) -> str:
    return f"{round(minutes / 60)} h" if minutes >= 90 else f"{round(minutes)} min"


def icone(a: dict) -> str:
    """Type affiché par une icône : la lumière VIIRS et l'image optique ont la leur."""
    src = (a.get("details") or {}).get("source")
    if a["type"] == "DARK_SHIP" and src == "viirs":
        return "VIIRS"
    if a["type"] == "DARK_SHIP" and src == "sentinel2":
        return "OPTIQUE"
    return a["type"]


def fragment(a: dict) -> str:
    """Cinq mots au plus, vocabulaire de l'opérateur (glossaire de docs/ux_refonte.md)."""
    t, d = a["type"], a.get("details") or {}
    if t == "WATCHLIST":
        return LISTES.get(d.get("niveau"), "Navire des listes")
    if t == "AIS_GAP":
        f = f"AIS coupé {duree(float(d.get('duree_min') or 0))}"
        infra = a.get("infrastructure")
        if infra:
            f += f" près de {infra['nom']}" if infra.get("nom") else f" près d'un {infra['type'].lower()}"
        return f
    if t == "AIS_UNCONFIRMED":
        return "Déclaré mais non vu"
    if t == "DARK_SHIP":
        return {"viirs": "Lumière la nuit, sans AIS", "sentinel2": "Vu en image, sans AIS"}.get(
            d.get("source"), "Écho radar sans AIS")
    if t == "RENDEZVOUS":
        return f"Rendez vous de {duree(float(d.get('duree_min') or 0))}"
    if t == "IDENTITY_CHANGE":
        return "Nom changé" if d.get("changement") == "nom" else "Même OMI, autre MMSI"
    if t == "IDENTITY_MISMATCH":
        return "Taille déclarée contredite"
    return t


def _verifie_sans_ais(a: dict) -> bool:
    return ((a.get("details") or {}).get("verification_satellite") or {}).get("resultat") == "present_sans_ais"


def _a_confirmer(a: dict) -> bool:
    return bool((a.get("details") or {}).get("a_confirmer"))


def sejour(s: dict | None, seuil_h: float, seuil_part: float) -> str | None:
    """Séjour prolongé d'un navire des listes (détail de l'alerte WATCHLIST) : « hors_port » (relève le niveau),
    « quai_ou_transit », « non_mesure » (alerte écrite avant les règles 2026.10.43), ou None (pas de séjour prolongé)."""
    s = s or {}
    if float(s.get("duree_h") or 0) < seuil_h:
        return None
    part = s.get("arret_hors_port_part")
    if part is None:
        return "non_mesure"
    return "hors_port" if float(part) >= seuil_part else "quai_ou_transit"


def niveau(alertes: list[dict], p: dict, contexte: dict | None = None) -> tuple[int, list[str]]:
    """Niveau (1 à 3) et ses contributions, lisibles. `contexte` (navire) : en_mer_pres_infra, sejour_prolonge."""
    ctx = contexte or {}
    base_g = p["base"]
    bases = []
    for a in alertes:
        if a["type"] == "WATCHLIST":
            fort = ctx.get("sejour_prolonge") or ctx.get("en_mer_pres_infra")
            bases.append((2 if fort else 1, "navire des listes " + (
                "en séjour prolongé, surtout à l'arrêt hors port" if ctx.get("sejour_prolonge") else
                "en mer près d'une infrastructure" if fort else
                {"quai_ou_transit": "en séjour prolongé, mais à quai ou en transit",
                 "non_mesure": "en séjour prolongé, part hors port non mesurée (règles antérieures)"}.get(
                    ctx.get("sejour"), "à quai ou en transit"))))
        else:
            bases.append((base_g.get(a["severity"], 1), f"gravité {a['severity']}"))
    n, pourquoi = max(bases, key=lambda b: b[0])
    contrib = [f"{pourquoi} : niveau {n}"]
    familles = sorted({FAMILLE.get(a["type"], "autre") for a in alertes})
    if len(familles) >= 2:
        n += 1
        contrib.append(f"signaux recoupés ({', '.join(NOMS_FAMILLES.get(f, f) for f in familles)}) : plus un")
    if any(_verifie_sans_ais(a) for a in alertes):
        n += 1
        contrib.append("vérification satellite, vu sans AIS : plus un")
    if all(_a_confirmer(a) for a in alertes):
        n = 1
        contrib.append("tous les signaux sont à confirmer : ramené à 1")
    return max(1, min(3, n)), contrib


def phrase(alertes: list[dict]) -> str:
    rang = {"critique": 3, "elevee": 2, "moyenne": 1, "faible": 0}
    ordre = sorted(alertes, key=lambda a: (-POIDS.get(a["type"], 1), -rang.get(a["severity"], 0), a["event_time"]))
    par_type: dict = {}
    for a in ordre:
        par_type.setdefault(a["type"], []).append(a)
    frags = []
    for t, xs in par_type.items():
        frags.append(f"AIS coupé {len(xs)} fois" if t == "AIS_GAP" and len(xs) > 1 else fragment(xs[0]))
    if any(_verifie_sans_ais(a) for a in alertes):
        frags = [f for f in frags if f != "Écho radar sans AIS"]
        frags.insert(min(2, len(frags)), "revu au radar sans AIS")
    s = ", ".join(dict.fromkeys(frags[:3]))
    return s[:1].upper() + s[1:]


def _dist_m(a: tuple, b: tuple) -> float:
    la1, la2 = math.radians(a[1]), math.radians(b[1])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b[0] - a[0]) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def regrouper(alertes: list[dict], p: dict, navires: dict, contextes: dict | None = None) -> list[dict]:
    """Dossiers à partir des alertes ouvertes (dicts : id, type, severity, status, event_time, details, lon, lat,
    vessel_id, detections, infrastructure). `navires` : vessel_id vers {nom, pavillon, type, longueur}. Triés par
    niveau, nombre de signaux puis ancienneté du dernier."""
    ouvertes = [a for a in alertes if a["status"] in OUVERTS]
    par_navire: dict = {}
    points: list = []
    for a in ouvertes:
        (par_navire.setdefault(a["vessel_id"], []) if a.get("vessel_id") is not None else points).append(a)
    detections_navire = {v: {d for a in xs for d in (a.get("detections") or [])} for v, xs in par_navire.items()}
    groupes_points: list[list[dict]] = []
    for a in sorted(points, key=lambda x: x["event_time"]):
        lie = next((v for v, ds in detections_navire.items() if ds & set(a.get("detections") or [])), None)
        if lie is not None:                     # vérification satellite : le point appartient au dossier du navire
            par_navire[lie].append(a)
            continue
        g = next((g for g in groupes_points if any(
            _dist_m((a["lon"], a["lat"]), (b["lon"], b["lat"])) <= p["point_fusion_m"]
            and abs(a["event_time"] - b["event_time"]) <= timedelta(hours=p["point_fusion_h"]) for b in g)), None)
        (g.append(a) if g is not None else groupes_points.append([a]))
    out = []
    for vid, xs in par_navire.items():
        nv = navires.get(vid) or {}
        out.append(_dossier(f"navire:{vid}", "navire", nv.get("nom") or "Sans nom", nv.get("pavillon"), vid, xs, p,
                            (contextes or {}).get(vid), nv))
    for g in groupes_points:
        src = (g[0].get("details") or {}).get("source")
        out.append(_dossier(f"point:{g[0]['id']}", "point", {"viirs": "Lumière en mer"}.get(src, "Écho sans AIS"),
                            None, None, g, p, None, {}))
    return sorted(out, key=lambda d: (-d["niveau"], -len(d["alertes"]), -d["dernier"].timestamp()))


def _dossier(cle, genre, nom, pavillon, vid, xs, p, ctx, nv) -> dict:
    n, contrib = niveau(xs, p, ctx)
    recent = max(xs, key=lambda a: a["event_time"])
    return {"cle": cle, "genre": genre, "nom": nom, "pavillon": pavillon, "vessel_id": vid,
            "type_navire": nv.get("type"), "longueur_m": nv.get("longueur"),
            "lon": recent["lon"], "lat": recent["lat"], "niveau": n, "niveau_cle": NIVEAUX[n],
            "phrase": phrase(xs), "contributions": contrib,
            "icones": list(dict.fromkeys(icone(a) for a in sorted(xs, key=lambda a: -POIDS.get(a["type"], 1))))[:3],
            "alertes": sorted(a["id"] for a in xs), "statuts": sorted({a["status"] for a in xs}),
            "signaux": [{"alerte": a["id"], "type": a["type"], "icone": icone(a), "gravite": a["severity"],
                         "statut": a["status"], "famille": FAMILLE.get(a["type"], "autre"), "fragment": fragment(a),
                         "t": a["event_time"]} for a in sorted(xs, key=lambda a: a["event_time"])],
            "premier": min(a["event_time"] for a in xs), "dernier": recent["event_time"]}


def fenetre(instant: datetime, p: dict) -> tuple[datetime, datetime]:
    return instant - timedelta(hours=p["fenetre_h"]), instant
