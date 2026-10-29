"""Panneau de contrôle (lot P) : état en base qui prime sur config/rules.yaml, journal de chaque action.

Partagé par le conteneur taches (psycopg) et l'API (asyncpg, backend/controle.py) : les calculs sont purs, les accès
à la base passent par des requêtes simples écrites ici une fois.

* Interrupteurs des analyses satellites (sentinel1, sentinel2, viirs, tip_and_cue) : une source en pause ne crée aucun
  travailleur ; reprise automatique à la date prévue, notification la veille.
* Réglages bornés par le fichier : plafond quotidien de scènes de Sentinel 1 et 2 (section controle.plafonds).
* `appliquer(conn, rules)` : copie des règles où l'état du panneau est appliqué (plafonds), avec `rules["_controle"]`
  (pauses) ; les modules (sentinel, sentinel2, tip_and_cue) ne lisent que ce dictionnaire, jamais la base pour cela.
* Journal (`journaliser`) : qui, quand, quoi, avant, après, motif, trace.
"""
from __future__ import annotations

import copy
import hashlib
import re
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from mars import traces

SOURCES = ("sentinel1", "sentinel2", "viirs", "tip_and_cue")
TOUT = ("sentinel1", "sentinel2", "tip_and_cue")          # « tout » : VIIRS seulement s'il est nommé
NOMS = {"sentinel1": "Sentinel 1", "sentinel2": "Sentinel 2", "viirs": "VIIRS", "tip_and_cue": "Tip and cue"}
TACHE = {"sentinel1": "sentinel", "sentinel2": "sentinel2", "viirs": "viirs"}   # travailleurs.tache
PLAFONDS = {"sentinel1": "sentinel", "sentinel2": "sentinel2"}                   # section de config/rules.yaml


# Journal

def journaliser(conn, auteur: str, action: str, cible: str | None, avant=None, apres=None, motif: str | None = None):
    """Une action du panneau ou d'une commande équivalente (psycopg). Jamais de secret dans avant ou après."""
    conn.execute("INSERT INTO journal_controle (auteur, action, cible, avant, apres, motif, trace_id) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                 (auteur, action, cible, _json(avant), _json(apres), motif, traces.trace_id()))
    traces.attributs(**{"controle.action": action, "controle.cible": cible or "", "controle.auteur": auteur})


def _json(v):
    return None if v is None else json.dumps(v, default=str, ensure_ascii=False)


# Interrupteurs

COLS_INTERRUPTEUR = "source, en_pause, depuis, jusqu_a, motif, auteur, veille_notifiee_le"


def interrupteur(row) -> dict:
    return dict(zip(COLS_INTERRUPTEUR.split(", "), row)) if not hasattr(row, "keys") else dict(row)


def pauses(conn) -> dict[str, dict]:
    """Sources en pause : {source: {motif, depuis, jusqu_a, auteur}}. Une pause échue compte encore jusqu'à sa reprise
    par la boucle (une minute au plus)."""
    rows = conn.execute(f"SELECT {COLS_INTERRUPTEUR} FROM interrupteurs_satellites WHERE en_pause").fetchall()
    return {r[0]: {k: v for k, v in interrupteur(r).items() if k != "source"} for r in rows}


def en_pause(rules: dict, source: str) -> str | None:
    """Motif de la pause de `source` d'après les règles appliquées (None si en service ou règles du fichier seules)."""
    p = ((rules.get("_controle") or {}).get("pauses") or {}).get(source)
    if p is None:
        return None
    fin = p.get("jusqu_a")
    reprise = f", reprise le {_paris(fin):%d/%m/%Y à %H:%M}" if fin else ""
    return f"{NOMS[source]} en pause ({p.get('motif') or 'sans motif'}{reprise})"


def _paris(t: datetime) -> datetime:
    from zoneinfo import ZoneInfo
    return t.astimezone(ZoneInfo("Europe/Paris"))


def basculer(conn, source: str, en_pause_: bool, auteur: str, motif: str | None = None,
             jusqu_a: datetime | None = None, envoi=None) -> dict:
    """Met `source` en pause ou en service (psycopg), journalise et notifie (ntfy). Retourne l'état après."""
    if source not in SOURCES:
        raise ValueError(f"source inconnue : {source} ({', '.join(SOURCES)})")
    if en_pause_ and not (motif or "").strip():
        raise ValueError("motif obligatoire pour une pause")
    if jusqu_a is not None and jusqu_a <= datetime.now(timezone.utc):
        raise ValueError("date de reprise déjà passée")
    avant = interrupteur(conn.execute(f"SELECT {COLS_INTERRUPTEUR} FROM interrupteurs_satellites WHERE source = %s "
                                      "FOR UPDATE", (source,)).fetchone())
    if en_pause_:
        conn.execute("UPDATE interrupteurs_satellites SET en_pause = true, depuis = CASE WHEN en_pause THEN depuis "
                     "ELSE now() END, jusqu_a = %s, motif = %s, auteur = %s, veille_notifiee_le = NULL "
                     "WHERE source = %s", (jusqu_a, motif.strip(), auteur, source))
    else:
        conn.execute("UPDATE interrupteurs_satellites SET en_pause = false, depuis = now(), jusqu_a = NULL, motif = %s, "
                     "auteur = %s, veille_notifiee_le = NULL WHERE source = %s", ((motif or "").strip() or None,
                                                                                  auteur, source))
    apres = interrupteur(conn.execute(f"SELECT {COLS_INTERRUPTEUR} FROM interrupteurs_satellites WHERE source = %s",
                                      (source,)).fetchone())
    action = "pause" if en_pause_ else "reprise"
    journaliser(conn, auteur, action, source, _public(avant), _public(apres), motif)
    if bool(avant["en_pause"]) != en_pause_ or en_pause_:
        fin = f" jusqu'au {_paris(jusqu_a):%d/%m/%Y %H:%M}" if jusqu_a else ""
        (envoi if envoi is not None else lambda *a: notifier(*a, conn=conn))("travailleurs", "info",
                 f"MARS C2 : {NOMS[source]} {'en pause' if en_pause_ else 'repris'}",
                 f"{'Pause' if en_pause_ else 'Reprise'}{fin} par {auteur}" + (f" : {motif}" if motif else ""))
    return apres


def _public(etat: dict) -> dict:
    return {k: v for k, v in etat.items() if k in ("en_pause", "jusqu_a", "motif")}


def echeances(conn, now: datetime, envoi=None) -> dict:
    """Chaque minute (taches) : reprise automatique des pauses échues, notification la veille d'une reprise prévue."""
    envoi = envoi if envoi is not None else (lambda *a: notifier(*a, conn=conn))
    out = {"reprises": [], "veilles": []}
    for (source,) in conn.execute("SELECT source FROM interrupteurs_satellites WHERE en_pause AND jusqu_a <= %s",
                                  (now,)).fetchall():
        basculer(conn, source, False, "automatique", "reprise prévue atteinte", envoi=envoi)
        out["reprises"].append(source)
    for source, fin in conn.execute(
            "SELECT source, jusqu_a FROM interrupteurs_satellites WHERE en_pause AND jusqu_a > %s AND jusqu_a <= %s "
            "AND veille_notifiee_le IS NULL", (now, now + timedelta(hours=24))).fetchall():
        conn.execute("UPDATE interrupteurs_satellites SET veille_notifiee_le = now() WHERE source = %s", (source,))
        envoi("travailleurs", "info", f"MARS C2 : reprise de {NOMS[source]} demain",
                 f"Reprise automatique le {_paris(fin):%d/%m/%Y à %H:%M} (heure de Paris)")
        out["veilles"].append(source)
    return out


# Plafonds quotidiens de scènes, bornés par config/rules.yaml (controle.plafonds)

def bornes_plafond(rules: dict, source: str) -> tuple[int, int]:
    b = ((rules.get("controle") or {}).get("plafonds") or {}).get(source) or {}
    return int(b.get("min", 0)), int(b.get("max", 4))


def verifier_plafond(rules: dict, source: str, valeur: int) -> int:
    if source not in PLAFONDS:
        raise ValueError(f"aucun plafond réglable pour {source}")
    lo, hi = bornes_plafond(rules, source)
    if not lo <= int(valeur) <= hi:
        raise ValueError(f"plafond de {NOMS[source]} hors de la plage {lo} à {hi} (config/rules.yaml, controle.plafonds)")
    return int(valeur)


def regler_plafond(conn, rules: dict, source: str, valeur: int, auteur: str, motif: str | None) -> int:
    """Plafond quotidien de scènes réglé hors du panneau (psycopg, commande taches.py plafond), comme la route
    POST /api/controle/plafonds : borné par le fichier, motif obligatoire, journalisé. Retourne la valeur écrite."""
    if not (motif or "").strip():
        raise ValueError("motif obligatoire pour un plafond")
    v = verifier_plafond(rules, source, valeur)
    cle = f"plafond:{source}"
    r = conn.execute("SELECT valeur FROM reglages_controle WHERE cle = %s FOR UPDATE", (cle,)).fetchone()
    conn.execute("INSERT INTO reglages_controle (cle, valeur, auteur) VALUES (%s, %s::jsonb, %s) "
                 "ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur, auteur = EXCLUDED.auteur, "
                 "mis_a_jour_le = now()", (cle, json.dumps(v), auteur))
    journaliser(conn, auteur, "plafond", source, {"valeur": r[0] if r else None}, {"valeur": v}, motif.strip())
    return v


def reglages(conn) -> dict:
    return {k: v for k, v in conn.execute("SELECT cle, valeur FROM reglages_controle").fetchall()}


def appliquer_reglages(rules: dict, regl: dict, pauses_: dict, zones: list | None = None) -> dict:
    """Règles appliquées (calcul pur) : plafonds du panneau dans leurs bornes, pauses, modes des règles, zones de
    vigilance actives (profil connu du fichier), priorité aux navires suivis, et empreinte de l'état qui influence les
    alertes, dans rules["_controle"]."""
    r = copy.deepcopy(rules)
    for source, section in PLAFONDS.items():
        v = regl.get(f"plafond:{source}")
        if v is not None and section in r:
            lo, hi = bornes_plafond(rules, source)
            r[section]["scenes_max_par_jour"] = max(lo, min(hi, int(v)))
    modes = {k[len("mode:"):]: v for k, v in regl.items() if k.startswith("mode:") and v in MODES}
    profils = profils_zones(rules)
    zones = sorted([z for z in zones or [] if z.get("profil") in profils], key=lambda z: z["id"])
    suivis = bool(regl.get("suivis:prioritaires", False))
    etat = {"modes": dict(sorted(modes.items()))}
    if zones:                    # sans zone, l'empreinte reste celle de la phase C
        etat["zones"] = [[z["id"], z["profil"]] for z in zones]
    if suivis:
        etat["suivis_prioritaires"] = True
    r["_controle"] = {"pauses": pauses_, "modes": modes, "zones": zones, "suivis_prioritaires": suivis, "etat": etat,
                      "empreinte": empreinte(etat), "observees": {}}
    return r


# Modes des règles d'alerte (phase C)

MODES = ("actif", "observation", "coupe")


def empreinte(etat: dict) -> str:
    """Empreinte courte et stable de l'état du panneau qui influence les alertes."""
    return hashlib.sha256(json.dumps(etat, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def catalogue(rules: dict) -> dict:
    return (rules.get("controle") or {}).get("regles") or {}


def regle_de(kind: str, details: dict | None) -> tuple[str | None, str | None]:
    """Règle du panneau et sous type d'une alerte, d'après son type et sa source."""
    d = details or {}
    src = d.get("source")
    if src in ("sentinel1", "sentinel2"):
        return src, kind.lower()
    if src == "viirs":
        return "viirs", None
    return {"AIS_GAP": ("ais_gap", None), "RENDEZVOUS": ("rendezvous", None), "WATCHLIST": ("watchlist", None),
            "IDENTITY_CHANGE": ("identite", {"nom": "nom", "omi_autre_mmsi": "omi"}.get(d.get("changement")))
            }.get(kind, (None, None))


def mode_fichier(rules: dict, regle: str) -> str:
    """Mode du fichier : ais_gap.mode (observation ou alertes) ; les autres règles sont actives."""
    if regle == "ais_gap":
        return "observation" if (rules.get("ais_gap") or {}).get("mode") == "observation" else "actif"
    return "actif"


def mode_effectif(rules: dict, regle: str | None, sous_type: str | None = None) -> str:
    if regle is None:
        return "actif"
    modes = (rules.get("_controle") or {}).get("modes") or {}
    if sous_type and f"{regle}.{sous_type}" in modes:
        return modes[f"{regle}.{sous_type}"]
    return modes.get(regle) or mode_fichier(rules, regle)


def regle_coupee(rules: dict, regle: str) -> bool:
    """Règle entière coupée (et chacun de ses sous types) : elle n'est même pas calculée."""
    sous = (catalogue(rules).get(regle) or {}).get("sous_types") or {}
    if not sous:
        return mode_effectif(rules, regle) == "coupe"
    return all(mode_effectif(rules, regle, s) == "coupe" for s in sous)


def decision(rules: dict, kind: str, details: dict | None) -> tuple[str, str | None, str | None]:
    """Mode qui s'applique à une alerte à écrire : (mode, règle, sous type)."""
    regle, sous = regle_de(kind, details)
    return mode_effectif(rules, regle, sous), regle, sous


def compter_observation(rules: dict, regle: str, sous: str | None):
    obs = (rules.setdefault("_controle", {})).setdefault("observees", {})
    k = f"{regle}.{sous}" if sous else regle
    obs[k] = obs.get(k, 0) + 1


EPISODE_SQL = """INSERT INTO episodes_observes (regle, sous_type, cle, gravite, controle_empreinte)
VALUES ({p1}, {p2}, {p3}, {p4}, {p5}) ON CONFLICT (regle, cle) DO UPDATE
SET derniere_le = now(), gravite = EXCLUDED.gravite, controle_empreinte = EXCLUDED.controle_empreinte"""
EMPREINTE_SQL = "INSERT INTO empreintes_controle (empreinte, etat) VALUES ({p1}, {p2}) ON CONFLICT DO NOTHING"


def stamp(conn, rules: dict) -> str | None:
    """Empreinte de l'état du panneau à joindre aux alertes (psycopg) ; l'état complet est gardé une fois."""
    c = rules.get("_controle")
    if not c:
        return None
    if not c.get("_empreinte_ecrite"):
        conn.execute(EMPREINTE_SQL.format(p1="%s", p2="%s"), (c["empreinte"], json.dumps(c["etat"])))
        c["_empreinte_ecrite"] = True
    return c["empreinte"]


def filtrer_sync(conn, rules: dict, kind: str, details: dict, cle: str, gravite: str | None,
                 lon: float | None = None, lat: float | None = None) -> str | None:
    """Alerte à écrire (psycopg : VIIRS, Sentinel) : la gravité à écrire si la règle est active (zone de vigilance
    appliquée, inscrite dans `details`) ; en observation, l'épisode est compté et gardé ; coupée, rien. None : rien à
    écrire."""
    mode, regle, sous = decision(rules, kind, details)
    gravite = marquer_zone(rules, gravite, details, lon, lat) if gravite else gravite
    if mode == "observation":
        compter_observation(rules, regle, sous)
        conn.execute(EPISODE_SQL.format(p1="%s", p2="%s", p3="%s", p4="%s", p5="%s"),
                     (regle, sous, cle, gravite, stamp(conn, rules)))
    return gravite if mode == "actif" else None


def seuils(section: dict, prefixe: str = "", limite: int = 40) -> list[list]:
    """Seuils d'une section de config/rules.yaml à plat, en lecture seule : valeurs simples et courtes listes."""
    out: list[list] = []
    for k, v in (section or {}).items():
        cle = f"{prefixe}{k}"
        if isinstance(v, dict):
            out += seuils(v, f"{cle}.", limite)
        elif isinstance(v, (int, float, str, bool)) or v is None:
            out.append([cle, v])
        elif isinstance(v, list) and len(v) <= 10 and all(isinstance(x, (int, float, str)) for x in v):
            out.append([cle, v])
    return out[:limite]


def verifier_mode(rules: dict, regle: str, sous_type: str | None, mode: str) -> str:
    """Clé du réglage d'un mode, après contrôle de la règle, du sous type et du mode."""
    cat = catalogue(rules)
    if regle not in cat:
        raise ValueError(f"règle inconnue : {regle}")
    if sous_type is not None and sous_type not in ((cat[regle].get("sous_types") or {})):
        raise ValueError(f"sous type inconnu pour {regle} : {sous_type}")
    if mode not in MODES:
        raise ValueError(f"mode inconnu : {mode} ({', '.join(MODES)})")
    return f"mode:{regle}.{sous_type}" if sous_type else f"mode:{regle}"


def concerne(rules_regle: tuple[str, str | None], kind: str, details: dict | None) -> bool:
    """L'alerte (type, détails) relève-t-elle de la règle (et du sous type, s'il est donné) ?"""
    regle, sous = regle_de(kind, details)
    return regle == rules_regle[0] and (rules_regle[1] is None or sous == rules_regle[1])


def appliquer(conn, rules: dict) -> dict:
    """Règles du fichier, avec l'état du panneau qui prime (psycopg)."""
    return appliquer_reglages(rules, reglages(conn), pauses(conn), [dict(zip(COLS_ZONE, r)) for r in conn.execute(
        ZONES_ACTIVES_SQL).fetchall()])


# Zones de vigilance (phase E)

COLS_ZONE = ("id", "nom", "profil", "geometrie")
ZONES_ACTIVES_SQL = """SELECT id, nom, profil, ST_AsGeoJSON(geom, 6)::json FROM zones_vigilance
WHERE retiree_le IS NULL AND (fin_le IS NULL OR fin_le > now()) ORDER BY id"""


def profils_zones(rules: dict) -> dict:
    return (rules.get("zones_vigilance") or {}).get("profils") or {}


def _dans_anneau(lon: float, lat: float, anneau: list) -> bool:
    dedans, j = False, len(anneau) - 1
    for i in range(len(anneau)):
        xi, yi = anneau[i][0], anneau[i][1]
        xj, yj = anneau[j][0], anneau[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-30) + xi:
            dedans = not dedans
        j = i
    return dedans


def dans_geometrie(g: dict, lon: float, lat: float) -> bool:
    """Point dans un polygone ou un multipolygone GeoJSON (trous compris), calcul plan en degrés."""
    t = (g or {}).get("type")
    polys = [g["coordinates"]] if t == "Polygon" else g.get("coordinates", []) if t == "MultiPolygon" else []
    if t == "GeometryCollection":
        return any(dans_geometrie(x, lon, lat) for x in g.get("geometries", []))
    return any(p and _dans_anneau(lon, lat, p[0]) and not any(_dans_anneau(lon, lat, h) for h in p[1:]) for p in polys)


def zones_de(rules: dict, lon: float | None, lat: float | None) -> list[dict]:
    if lon is None or lat is None:
        return []
    return [z for z in (rules.get("_controle") or {}).get("zones") or [] if dans_geometrie(z["geometrie"], lon, lat)]


def seuil_zone(rules: dict, cle: str, defaut: float, lon: float | None, lat: float | None) -> float:
    """Seuil applicable en un point : le plus bas des profils des zones qui le contiennent, jamais au dessus du fichier."""
    profils = profils_zones(rules)
    vals = [(profils[z["profil"]].get("seuils") or {}).get(cle) for z in zones_de(rules, lon, lat)]
    return min([defaut] + [float(v) for v in vals if v is not None])


def seuil_min(rules: dict, cle: str, defaut: float) -> float:
    """Seuil le plus bas sur toutes les zones actives : la recherche part de lui, puis chaque épisode est jugé au seuil
    de sa position (seuil_zone). Sans zone, le seuil du fichier : le calcul est inchangé."""
    profils = profils_zones(rules)
    vals = [(profils[z["profil"]].get("seuils") or {}).get(cle) for z in (rules.get("_controle") or {}).get("zones") or []]
    return min([defaut] + [float(v) for v in vals if v is not None])


def marquer_zone(rules: dict, gravite: str, details: dict, lon: float | None, lat: float | None) -> str:
    """Alerte dans une zone de vigilance : zone et profil appliqués inscrits dans les détails ; gravité relevée au
    minimum du profil. Retourne la gravité à écrire."""
    zs = zones_de(rules, lon, lat)
    if not zs:
        return gravite
    profils = profils_zones(rules)
    z = max(zs, key=lambda x: GRAVITES.index(profils[x["profil"]].get("gravite_min", "faible")))
    p = profils[z["profil"]]
    plancher = p.get("gravite_min")
    apres = gravite
    if plancher in GRAVITES and gravite in GRAVITES and GRAVITES.index(gravite) < GRAVITES.index(plancher):
        apres = plancher
    details["zone_vigilance"] = {"id": z["id"], "nom": z["nom"], "profil": z["profil"], "profil_nom": p.get("nom"),
                                 "autres": [x["id"] for x in zs if x is not z], "gravite_avant": gravite}
    return apres


def priorite_zone(rules: dict, lon: float | None, lat: float | None, cle: str = "priorite_plus") -> int:
    profils = profils_zones(rules)
    return max([0] + [int(profils[z["profil"]].get(cle) or 0) for z in zones_de(rules, lon, lat)])


# Budget du mois et coût des travailleurs

def budget(conn, rules: dict, now: datetime) -> dict:
    """Dépensé (coût réel des travailleurs détruits, estimé à défaut), réservé (travailleurs en vie, coût estimé de
    leurs analyses ou de leur durée de vie) et restant, par source et au total. Le budget mensuel (sentinel.
    budget_mensuel_eur) est partagé par Sentinel 1 et 2 ; VIIRS est compté hors de ce budget."""
    mois = debut_mois(now)
    return budget_calcul(conn.execute(BUDGET_SQL.format(p="%s"), (mois,)).fetchall(), rules, mois)


def debut_mois(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


# {p} : marque du paramètre, %s pour psycopg, $1 pour asyncpg
BUDGET_SQL = """SELECT t.tache,
       sum(coalesce(t.cout_reel_eur, (t.mesures->>'cout_estime_eur')::float8)) FILTER (WHERE t.detruit_le IS NOT NULL),
       sum(coalesce((SELECT sum(a.cout_estime_eur) FROM analyses a WHERE a.travailleur_id = t.id),
                    (t.mesures->>'cout_estime_eur')::float8, 0)) FILTER (WHERE t.detruit_le IS NULL),
       count(*)
FROM travailleurs t WHERE t.cree_le >= {p} GROUP BY t.tache"""


def budget_calcul(rows, rules: dict, mois: datetime) -> dict:
    lignes = {r[0]: {"depense": float(r[1] or 0), "reserve": float(r[2] or 0), "travailleurs": int(r[3])} for r in rows}
    plafond = float(rules["sentinel"]["budget_mensuel_eur"])
    out = {}
    for source, tache in TACHE.items():
        x = lignes.get(tache, {"depense": 0.0, "reserve": 0.0, "travailleurs": 0})
        out[source] = {k: round(v, 2) if isinstance(v, float) else v for k, v in x.items()}
    partage = sum(out[s]["depense"] + out[s]["reserve"] for s in ("sentinel1", "sentinel2"))
    total = {"depense": round(sum(o["depense"] for o in out.values()), 2),
             "reserve": round(sum(o["reserve"] for o in out.values()), 2)}
    return {"mois": mois.date().isoformat(), "sources": out, "total": total,
            "budget_sentinel": {"plafond": plafond, "engage": round(partage, 2),
                                "restant": round(max(plafond - partage, 0.0), 2)}}


# Coût par résultat (lot Z6) : ce qu'a coûté chaque résultat utile, sur le mois et depuis le début. Un résultat est
# une nuit VIIRS traitée sans erreur, une analyse Sentinel 1 ou 2 terminée, une demande d'observation analysée. Le
# coût d'une source est celui de ses travailleurs détruits (réel, estimé à défaut), échecs compris : un résultat porte
# aussi le prix des essais ratés. Tip and cue n'a pas de travailleur à lui : son coût est celui des analyses qui ont
# porté ses demandes (déjà compté dans Sentinel 1 et 2, jamais ajouté au total).

COUT_TRAVAILLEURS_SQL = """SELECT tache, cree_le >= {p} AS ce_mois, count(*), count(*) FILTER (WHERE etat = 'termine'),
       coalesce(sum(coalesce(cout_reel_eur, (mesures->>'cout_estime_eur')::float8, 0)), 0),
       coalesce(sum(coalesce(cout_reel_eur, (mesures->>'cout_estime_eur')::float8, 0))
                FILTER (WHERE etat <> 'termine'), 0)
FROM travailleurs WHERE detruit_le IS NOT NULL GROUP BY 1, 2"""

COUT_RESULTATS_SQL = """SELECT 'viirs', nuit >= ({p})::timestamptz::date, count(DISTINCT nuit) FROM viirs_granules
  WHERE erreur IS NULL GROUP BY 1, 2
UNION ALL
SELECT source, completed_at >= {p}, count(*) FROM analyses
  WHERE status = 'done' AND source IN ('sentinel1', 'sentinel2') GROUP BY 1, 2
UNION ALL
SELECT 'tip_and_cue', mis_a_jour_le >= {p}, count(*) FROM demandes_observation WHERE etat = 'analysee' GROUP BY 1, 2"""

COUT_TIP_SQL = """SELECT d.mis_a_jour_le >= {p}, coalesce(sum(a.cout_estime_eur), 0)
FROM (SELECT DISTINCT ON (analysis_id) analysis_id, mis_a_jour_le FROM demandes_observation
      WHERE etat = 'analysee' AND analysis_id IS NOT NULL) d JOIN analyses a ON a.id = d.analysis_id GROUP BY 1"""

UNITES = {"viirs": "nuit traitée", "sentinel1": "analyse terminée", "sentinel2": "analyse terminée",
          "tip_and_cue": "demande analysée"}


def cout_par_resultat(travailleurs: list, resultats: list, tip: list) -> dict:
    """Lignes de COUT_TRAVAILLEURS_SQL, COUT_RESULTATS_SQL et COUT_TIP_SQL vers {"mois", "total"} : par source,
    dépense, dont échecs, travailleurs, aboutis, résultats, unité et coût par résultat (None sans résultat)."""
    out = {}
    for periode, garder in (("mois", lambda m: bool(m)), ("total", lambda _m: True)):
        par = {s: {"depense": 0.0, "depense_echecs": 0.0, "travailleurs": 0, "aboutis": 0, "resultats": 0}
               for s in UNITES}
        for tache, ce_mois, n, ok, cout, echecs in travailleurs:
            source = next((s for s, t in TACHE.items() if t == tache), None)
            if source and garder(ce_mois):
                p = par[source]
                p["travailleurs"] += int(n)
                p["aboutis"] += int(ok)
                p["depense"] += float(cout or 0)
                p["depense_echecs"] += float(echecs or 0)
        for source, ce_mois, n in resultats:
            if source in par and garder(ce_mois):
                par[source]["resultats"] += int(n)
        for ce_mois, cout in tip:
            if garder(ce_mois):
                par["tip_and_cue"]["depense"] += float(cout or 0)
        for s, p in par.items():
            p["depense"], p["depense_echecs"] = round(p["depense"], 2), round(p["depense_echecs"], 2)
            p["unite"] = UNITES[s]
            p["cout_par_resultat"] = round(p["depense"] / p["resultats"], 2) if p["resultats"] else None
        out[periode] = par
    return out


# Notifications (phase D) : préférences, heures calmes, résumé au réveil

CATEGORIES = ("pannes", "disque", "collecte", "travailleurs")
GRAVITES = ("faible", "moyenne", "elevee", "critique")


def preferences(rules: dict, en_base: dict | None) -> dict:
    """Préférences effectives : celles du panneau (objets de premier niveau entiers) par dessus celles du fichier,
    comme la fonction SQL preferences_notification."""
    return {**copy.deepcopy((rules.get("controle") or {}).get("notifications") or {}), **(en_base or {})}


def en_heures_calmes(prefs: dict, now: datetime) -> bool:
    """Même calcul que la fonction SQL heures_calmes : heure de Paris, plage qui peut passer minuit."""
    hc = prefs.get("heures_calmes") or {}
    if not hc.get("actif", True):
        return False
    d, f = (datetime.strptime(hc.get(k, v), "%H:%M").time() for k, v in (("debut", "23:00"), ("fin", "07:00")))
    h = _paris(now).time()
    if d == f:
        return False
    return d <= h < f if d < f else (h >= d or h < f)


def verifier_preferences(rules: dict, p: dict) -> dict:
    """Préférences envoyées par le panneau : clés, gravités, types, catégories et heures connues seulement."""
    defaut = preferences(rules, None)
    out = {}
    a = p.get("alertes") or {}
    if a.get("gravite_min") not in GRAVITES:
        raise ValueError(f"gravité inconnue : {a.get('gravite_min')}")
    types = defaut["alertes"]["types"]
    out["alertes"] = {"gravite_min": a["gravite_min"],
                      "types": {t: bool((a.get("types") or {}).get(t, v)) for t, v in types.items()}}
    out["tip_and_cue"] = bool(p.get("tip_and_cue", True))
    out["categories"] = {c: bool((p.get("categories") or {}).get(c, True)) for c in CATEGORIES}
    hc = p.get("heures_calmes") or {}
    for k in ("debut", "fin"):
        try:
            datetime.strptime(hc.get(k, ""), "%H:%M")
        except ValueError:
            raise ValueError(f"heure {k} invalide (HH:MM) : {hc.get(k)}") from None
    out["heures_calmes"] = {"actif": bool(hc.get("actif", True)), "debut": hc["debut"], "fin": hc["fin"]}
    return out


def decision_notification(prefs: dict, categorie: str, gravite: str | None, now: datetime,
                          type_alerte: str | None = None) -> str:
    """envoyer, retenir (heures calmes) ou taire (préférence coupée). Catégories : les quatre catégories système,
    tip_and_cue, alerte (avec type_alerte et gravité). Une panne grave n'est jamais retenue."""
    if categorie in CATEGORIES:
        if not (prefs.get("categories") or {}).get(categorie, True):
            return "taire"
    elif categorie == "tip_and_cue":
        if not prefs.get("tip_and_cue", True):
            return "taire"
    elif categorie == "alerte":
        a = prefs.get("alertes") or {}
        if not (a.get("types") or {}).get(type_alerte, True):
            return "taire"
        if gravite not in GRAVITES or GRAVITES.index(gravite) < GRAVITES.index(a.get("gravite_min", "elevee")):
            return "taire"
    if gravite != "grave" and en_heures_calmes(prefs, now):
        return "retenir"
    return "envoyer"


def preferences_en_base(conn) -> dict | None:
    r = conn.execute("SELECT valeur FROM reglages_controle WHERE cle = 'notif:preferences'").fetchone()
    return r[0] if r else None


def notifier(categorie: str, gravite: str, titre: str, message: str, conn=None, rules: dict | None = None,
             type_alerte: str | None = None, now: datetime | None = None, lien: str | None = None) -> str:
    """Notification selon les préférences (psycopg) : envoyée, retenue pour le résumé du réveil, ou tue. Sans connexion,
    envoyée telle quelle (base indisponible). `lien` : objet ouvert d'un appui (lien_interface). Retourne la décision."""
    if conn is not None:
        d = decider(conn, categorie, gravite, titre, message, rules, type_alerte, now)
        if d != "envoyer":
            return d
    envoyer(gravite, titre, message, "satellite" if categorie in ("travailleurs", "tip_and_cue") else "warning", lien)
    return "envoyer"


def lien_interface(sel: str | None = None) -> str:
    """Adresse d'un objet dans l'interface (lot UX1) : « alerte:12 » ouvre l'alerte et son dossier ; sans objet, la
    Veille. Base MARS_URL_INTERFACE (le tunnel SSH par défaut) ; aucune donnée sensible, un numéro d'alerte seulement."""
    from urllib.parse import quote
    base = (os.environ.get("MARS_URL_INTERFACE") or "http://localhost:8080").rstrip("/")
    return f"{base}/?sel={quote(sel, safe=':')}" if sel else f"{base}/"


def decider(conn, categorie: str, gravite: str, titre: str, message: str, rules: dict | None = None,
            type_alerte: str | None = None, now: datetime | None = None) -> str:
    """Décision selon les préférences (psycopg) ; une notification retenue est gardée pour le résumé du réveil."""
    from mars.config import load_rules
    prefs = preferences(rules or load_rules(), preferences_en_base(conn))
    d = decision_notification(prefs, categorie, gravite, now or datetime.now(timezone.utc), type_alerte)
    if d == "retenir":
        conn.execute("INSERT INTO notifications_retenues (categorie, gravite, titre, message) VALUES (%s, %s, %s, %s)",
                     (categorie, gravite, titre, message))
    traces.attributs(**{"notification.categorie": categorie, "notification.decision": d})
    return d


def envoyer(gravite: str, titre: str, message: str, tags: str = "warning", lien: str | None = None):
    from mars import supervision
    n = supervision.Ntfy.from_env()
    if n is None:
        return None
    try:
        return n.envoyer(titre, message, priorite="high" if gravite in ("grave", "elevee", "critique") else "default",
                         tags=tags, clic=lien)
    except Exception as e:                       # une notification ne bloque jamais l'action
        traces.erreur(f"ntfy : {e}")
        return None


# Système (phase B) : liens vers Grafana et vers une trace

def grafana_url() -> str:
    """Adresse de Grafana vue du navigateur de l'opérateur (par le tunnel SSH par défaut)."""
    return (os.environ.get("MARS_GRAFANA_URL") or "http://localhost:3000").rstrip("/")


def lien_trace(trace_id: str | None) -> str | None:
    """Exploration Tempo de Grafana sur une trace (vue Gantt), comme les liens des tableaux (grafana/generer.py)."""
    if not trace_id:
        return None
    from urllib.parse import quote
    pane = {"a": {"datasource": "tempo", "queries": [{"refId": "A", "datasource": {"type": "tempo", "uid": "tempo"},
                                                         "queryType": "traceql", "query": trace_id}],
                  "range": {"from": "now-3d", "to": "now"}}}
    return f"{grafana_url()}/explore?schemaVersion=1&orgId=1&panes=" + quote(json.dumps(pane, separators=(",", ":")),
                                                                              safe="")


# Opérateurs : mot de passe haché dans le .env (MARS_OPERATEURS), sessions par cookie

ITERATIONS = 600_000


def hacher(mot_de_passe: str, sel: bytes | None = None, iterations: int = ITERATIONS) -> str:
    """Empreinte PBKDF2 SHA 256 au format pbkdf2_sha256:<itérations>:<sel hex>:<empreinte hex> (ni $ ni espace : le
    .env la garde telle quelle)."""
    sel = sel or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode(), sel, iterations)
    return f"pbkdf2_sha256:{iterations}:{sel.hex()}:{h.hex()}"


def verifier_mot_de_passe(mot_de_passe: str, empreinte: str) -> bool:
    try:
        algo, it, sel, h = empreinte.split(":")
        if algo != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode(), bytes.fromhex(sel), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except (ValueError, TypeError):
        return False


# Empreinte jamais valable, vérifiée quand le nom est inconnu : même coût de calcul qu'un vrai opérateur
EMPREINTE_FACTICE = f"pbkdf2_sha256:{ITERATIONS}:{'00' * 16}:{'00' * 32}"

EMPREINTE = re.compile(r"pbkdf2_sha256:[0-9]{4,7}:[0-9a-f]{32}:[0-9a-f]{64}")


def _entrees(env: str | None) -> list[tuple[str, str]]:
    out = []
    for e in (env if env is not None else os.environ.get("MARS_OPERATEURS", "")).split(","):
        nom, _, h = e.strip().partition(":")
        if nom.strip():
            out.append((nom.strip(), h.strip()))
    return out


def operateurs(env: str | None = None) -> dict[str, str]:
    """MARS_OPERATEURS=nom:pbkdf2_sha256:…,autre:pbkdf2_sha256:… ; noms sans deux points ni virgule. Seules les
    entrées bien formées comptent (voir operateurs_abimes)."""
    return {nom: h for nom, h in _entrees(env) if EMPREINTE.fullmatch(h)}


def operateurs_abimes(env: str | None = None) -> dict[str, str]:
    """Entrées de MARS_OPERATEURS abîmées, avec la raison : empreinte qui n'a pas 64 caractères hexadécimaux (ligne
    copiée avec un suffixe de trace, le 21/10/2026 : 131 caractères), format ou sel faux. Jamais l'empreinte."""
    out = {}
    for nom, h in _entrees(env):
        if EMPREINTE.fullmatch(h):
            continue
        parts = h.split(":")
        if len(parts) != 4 or parts[0] != "pbkdf2_sha256":
            out[nom] = "format attendu pbkdf2_sha256:itérations:sel:empreinte"
        elif len(parts[3]) != 64 or any(c not in "0123456789abcdef" for c in parts[3]):
            out[nom] = f"empreinte de {len(parts[3])} caractères au lieu de 64 caractères hexadécimaux"
        else:
            out[nom] = "itérations ou sel invalides"
    return out


def message_abime(nom: str, raison: str) -> str:
    return (f"ligne MARS_OPERATEURS abîmée pour {nom} : {raison} ; la refaire avec taches.py operateur-hacher "
            "(sans rien après l'empreinte)")


def jeton_session() -> tuple[str, str]:
    """Jeton du cookie et son empreinte (seule l'empreinte va en base)."""
    j = secrets.token_urlsafe(32)
    return j, hashlib.sha256(j.encode()).hexdigest()


def empreinte_jeton(j: str) -> str:
    return hashlib.sha256(j.encode()).hexdigest()


def resume_du_reveil(conn, rules: dict, now: datetime) -> dict:
    """Hors des heures calmes : notifications retenues envoyées en un seul résumé (psycopg)."""
    prefs = preferences(rules, preferences_en_base(conn))
    if en_heures_calmes(prefs, now):
        return {"retenues": None}
    rows = conn.execute("SELECT id, titre FROM notifications_retenues WHERE envoyee_le IS NULL ORDER BY le").fetchall()
    if not rows:
        return {"retenues": 0}
    titres = [t.removeprefix("MARS C2 : ") for _i, t in rows]
    message = "\n".join(titres[:10]) + (f"\n… et {len(titres) - 10} autres" if len(titres) > 10 else "")
    envoyer("info", f"MARS C2 : {len(rows)} notification(s) pendant les heures calmes", message, "sunrise")
    conn.execute("UPDATE notifications_retenues SET envoyee_le = now() WHERE id = ANY(%s)", ([i for i, _t in rows],))
    return {"retenues": len(rows)}


def notifier_alertes(conn, rules: dict, now: datetime, envoi=None) -> dict:
    """Chaque minute (taches) : nouvelles alertes depuis la dernière vue, notifiées selon la gravité et le type (au delà
    de par_minute_max, un seul message de synthèse). Au premier passage, la dernière alerte est seulement notée."""
    envoi = envoi or notifier
    prefs = preferences(rules, preferences_en_base(conn))
    r = conn.execute("SELECT (valeur->>'id')::bigint FROM notifications_etat WHERE cle = 'derniere_alerte'").fetchone()
    dernier = conn.execute("SELECT coalesce(max(id), 0) FROM alerts").fetchone()[0]
    conn.execute("INSERT INTO notifications_etat (cle, valeur) VALUES ('derniere_alerte', %s) ON CONFLICT (cle) DO UPDATE "
                 "SET valeur = EXCLUDED.valeur, mis_a_jour_le = now()", (json.dumps({"id": dernier}),))
    if r is None:
        return {"nouvelles": 0, "initialise": dernier}
    rows = conn.execute("SELECT id, type, severity, details->>'source' FROM alerts WHERE id > %s AND id <= %s ORDER BY id",
                        (r[0], dernier)).fetchall()
    retenues = [x for x in rows if decision_notification(prefs, "alerte", x[2], now, x[1]) != "taire"]
    nmax = int(prefs.get("par_minute_max", 5))
    for aid, typ, sev, src in retenues[:nmax]:
        envoi("alerte", sev, f"MARS C2 : alerte {aid}, {typ} {sev}", f"Nouvelle alerte {typ}"
              + (f" ({src})" if src else "") + f", gravité {sev}", conn=conn, rules=rules, type_alerte=typ, now=now,
              lien=lien_interface(f"alerte:{aid}"))
    if len(retenues) > nmax:
        envoi("alerte", "elevee", f"MARS C2 : {len(retenues) - nmax} autres alertes",
              f"{len(retenues)} nouvelles alertes notifiables en une minute", conn=conn, rules=rules,
              type_alerte=None, now=now, lien=lien_interface())
    return {"nouvelles": len(rows), "notifiees": min(len(retenues), nmax)}


def recopier_defauts(conn, rules: dict):
    """Préférences par défaut du fichier, recopiées pour la fonction SQL preferences_notification (Grafana)."""
    conn.execute("INSERT INTO notifications_etat (cle, valeur) VALUES ('defauts', %s) ON CONFLICT (cle) DO UPDATE "
                 "SET valeur = EXCLUDED.valeur, mis_a_jour_le = now()",
                 (json.dumps((rules.get("controle") or {}).get("notifications") or {}),))

