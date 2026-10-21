"""Dossiers (lot U, refonte de l'interface, docs/ux_refonte.md) : quel navire regarder maintenant, et pourquoi.

Lecture : liste des dossiers de la fenêtre (72 h avant l'instant affiché), récit d'un dossier (signaux, vérifications
satellites, détections, route AIS et preuve sur la carte), devis d'une vérification satellite. Écriture : une décision
sur un dossier (Vu, Confirmer, Classer avec motif, Rouvrir) s'applique à chacune de ses alertes ouvertes et se journalise
alerte par alerte dans alert_actions, comme la route des alertes ; rien n'est supprimé. Calcul : mars/dossiers.py. Le
moteur des alertes ne change pas. Chaque route ouvre son span (dossiers.liste, dossiers.recit, dossiers.decision,
dossiers.verification)."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from mars import dossiers as D
from mars import traces
from mars.config import load_rules
from regions import dans_region, region_ewkt

router = APIRouter()

# Statut d'une alerte, celui de l'instant en rejeu tel qu'il était (dernière décision antérieure), sinon l'actuel
STATUT = """CASE WHEN $3::boolean THEN coalesce((
    SELECT CASE x.action WHEN 'acquitter' THEN 'acquittee' WHEN 'confirmer' THEN 'confirmee' WHEN 'classer' THEN 'classee'
                         ELSE 'nouvelle' END
    FROM alert_actions x WHERE x.alert_id = a.id AND x.at <= $2 AND x.action <> 'commenter'
    ORDER BY x.at DESC LIMIT 1), 'nouvelle') ELSE a.status END"""


def _p(rules: dict) -> dict:
    return rules["dossiers"]


async def _alertes(c, debut, instant, rejeu: bool, ewkt, p: dict) -> list[dict]:
    rows = await c.fetch(
        f"""SELECT a.id, a.type, a.severity, {STATUT} AS status, a.event_time, a.details, a.detected_at,
                   ST_X(a.geom::geometry) AS lon, ST_Y(a.geom::geometry) AS lat,
                   (SELECT min(e.evidence_id) FROM alert_evidence e WHERE e.alert_id = a.id
                      AND e.evidence_type = 'vessel') AS vessel_id,
                   (SELECT array_agg(e.evidence_id) FROM alert_evidence e WHERE e.alert_id = a.id
                      AND e.evidence_type = 'detection') AS detections,
                   CASE WHEN a.type = 'AIS_GAP' THEN (
                     SELECT json_build_object('nom', i.name, 'type', i.attrs->>'type') FROM infrastructure i
                     WHERE ST_DWithin(i.geom, a.geom, $4) ORDER BY i.geom <-> a.geom LIMIT 1) END AS infrastructure
            FROM alerts a
            WHERE a.event_time BETWEEN $1 AND $2 AND a.detected_at <= $2 AND {dans_region("a.geom::geometry", 5)}
            ORDER BY a.event_time""", debut, instant, rejeu, float(p["infrastructure_m"]), ewkt)
    return [dict(r) for r in rows]


async def _navires(c, ids: list[int]) -> dict:
    return {r["id"]: {"nom": r["name"], "pavillon": r["flag"], "type": r["ship_type"], "longueur": r["length_m"]}
            for r in await c.fetch("SELECT id, name, flag, ship_type, length_m FROM vessels WHERE id = ANY($1)", ids)}


async def _contextes(c, alertes: list[dict], instant, rules: dict) -> dict:
    """Navires des listes : séjour prolongé (détail de l'alerte WATCHLIST), en mer près d'une infrastructure (dernière
    position à l'instant à moins d'un mille d'une infrastructure, à plus de en_mer_m de la côte, et lent : sous
    transit_kn, sinon il passe)."""
    p, seuil = _p(rules), float(rules["watchlist"]["sejour_prolonge_h"])
    out: dict = {}
    for a in alertes:
        if a["type"] != "WATCHLIST" or a.get("vessel_id") is None or a["vessel_id"] in out:
            continue
        sejour = (a.get("details") or {}).get("sejour") or {}
        r = await c.fetchrow(
            """WITH d AS (SELECT geom, sog_kn FROM positions WHERE vessel_id = $1 AND ts <= $2 ORDER BY ts DESC LIMIT 1)
               SELECT EXISTS (SELECT 1 FROM infrastructure i, d WHERE ST_DWithin(i.geom, d.geom, $3)) AS pres,
                      NOT EXISTS (SELECT 1 FROM land l, d WHERE ST_DWithin(l.geom, d.geom, $4)) AS en_mer,
                      (SELECT sog_kn FROM d) AS sog""",
            a["vessel_id"], instant, float(p["infrastructure_m"]), float(p["en_mer_m"]))
        # En transit (au delà de transit_kn), un navire des listes reste « à noter » même près d'une infrastructure
        lent = r is not None and r["sog"] is not None and r["sog"] < float(p["transit_kn"])
        out[a["vessel_id"]] = {"sejour_prolonge": float(sejour.get("duree_h") or 0) >= seuil,
                               "en_mer_pres_infra": bool(r and r["pres"] and r["en_mer"] and lent)}
    return out


async def calculer(c, instant: datetime, rejeu: bool, region: str | None, rules: dict) -> list[dict]:
    p = _p(rules)
    debut, fin = D.fenetre(instant, p)
    alertes = await _alertes(c, debut, fin, rejeu, await region_ewkt(c, region), p)
    navires = await _navires(c, sorted({a["vessel_id"] for a in alertes if a.get("vessel_id") is not None}))
    return D.regrouper(alertes, p, navires, await _contextes(c, alertes, instant, rules))


def _instant(a_l_instant: datetime | None, end: datetime | None) -> tuple[datetime, bool]:
    t = a_l_instant or end or datetime.now(timezone.utc)
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)), a_l_instant is not None


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def _sortie(d: dict) -> dict:
    return {**{k: _iso(v) for k, v in d.items() if k != "signaux"},
            "signaux": [{**s, "t": _iso(s["t"])} for s in d["signaux"]]}


@router.get("/api/dossiers")
async def liste(request: Request, end: datetime | None = None, region: str | None = None,
                a_l_instant: datetime | None = None):
    """Dossiers ouverts à l'instant (fin de la plage, ou `a_l_instant` en rejeu tel qu'il était), triés : niveau,
    nombre de signaux, ancienneté. Compteurs par niveau ; dernier dossier à examiner, pour l'état calme."""
    rules = load_rules()
    instant, rejeu = _instant(a_l_instant, end)
    with traces.span("dossiers.liste", **{"dossiers.rejeu": rejeu, "dossiers.region": region or ""}) as sp:
        async with request.app.state.pool.acquire() as c:
            ds = await calculer(c, instant, rejeu, region, rules)
            dernier = await c.fetchval(
                "SELECT max(event_time) FROM alerts WHERE event_time <= $1 AND detected_at <= $1 "
                "AND severity IN ('elevee', 'critique')", instant)
            # Carte calme (Veille) : seules les infrastructures à moins d'un mille d'un dossier restent nettes
            proches = [r["id"] for r in await c.fetch(
                """SELECT DISTINCT i.id FROM infrastructure i,
                          unnest($1::float8[], $2::float8[]) AS p(lon, lat)
                   WHERE ST_DWithin(i.geom, ST_SetSRID(ST_MakePoint(p.lon, p.lat), 4326)::geography, $3)""",
                [d["lon"] for d in ds], [d["lat"] for d in ds], float(_p(rules)["infrastructure_m"]))] if ds else []
        compte = {k: sum(1 for d in ds if d["niveau_cle"] == k) for k in D.NIVEAUX.values()}
        sp.set_attributes({"dossiers.total": len(ds), **{f"dossiers.{k}": v for k, v in compte.items()},
                           "dossiers.alertes": sum(len(d["alertes"]) for d in ds)})
    return {"instant": instant.isoformat(), "fenetre_h": _p(rules)["fenetre_h"], "compteurs": compte,
            "dernier_inhabituel": _iso(dernier), "infrastructures_proches": proches,
            "dossiers": [_sortie(d) for d in ds]}


async def _trouver(c, cle: str, instant, rejeu, rules) -> dict:
    for d in await calculer(c, instant, rejeu, None, rules):
        if d["cle"] == cle:
            return d
    raise HTTPException(404, "Dossier introuvable ou clos à cet instant")


@router.get("/api/decisions/mesures")
async def mesures(request: Request, jours: int = 30):
    """Vrais et faux positifs (lot U, étape 5) : dernière décision de chaque alerte décidée sur la période, par type
    d'alerte ; part des vrais positifs = confirmées / (confirmées + faux positifs). Les alertes vues, ou classées
    comme activité légitime ou doublon, ne comptent ni pour ni contre."""
    jours = max(1, min(jours, 365))
    with traces.span("dossiers.mesures", **{"mesures.jours": jours}) as sp:
        async with request.app.state.pool.acquire() as c:
            rows = await c.fetch(
                """SELECT a.type, x.action, x.motif, count(*) AS n FROM alerts a
                   JOIN LATERAL (SELECT action, motif FROM alert_actions y WHERE y.alert_id = a.id
                                   AND y.action <> 'commenter' ORDER BY y.at DESC LIMIT 1) x ON true
                   WHERE EXISTS (SELECT 1 FROM alert_actions z WHERE z.alert_id = a.id
                                   AND z.at >= now() - make_interval(days => $1))
                   GROUP BY 1, 2, 3""", jours)
        par_type: dict = {}
        for r in rows:
            cle = {"confirmer": "confirmees", "acquitter": "vues", "rouvrir": "rouvertes"}.get(r["action"]) \
                or (r["motif"] if r["action"] == "classer" else r["action"])
            t = par_type.setdefault(r["type"], {})
            t[cle] = t.get(cle, 0) + r["n"]
        for t in par_type.values():
            vp, fp = t.get("confirmees", 0), t.get("faux_positif", 0)
            t["part_vrais"] = round(vp / (vp + fp), 3) if vp + fp else None
        total = {k: sum(t.get(k, 0) for t in par_type.values()) for k in ("confirmees", "faux_positif")}
        sp.set_attributes({"mesures.types": len(par_type), **{f"mesures.{k}": v for k, v in total.items()}})
    return {"jours": jours, "par_type": par_type, **total,
            "part_vrais": round(total["confirmees"] / sum(total.values()), 3) if sum(total.values()) else None}


@router.get("/api/dossiers/{cle}")
async def recit(request: Request, cle: str, a_l_instant: datetime | None = None, end: datetime | None = None):
    """Récit d'un dossier : en tête (niveau, nom, phrase, contributions), chronologie des signaux avec leurs détails et
    décisions, vérifications satellites, détections, route AIS jusqu'à l'instant, et la preuve à dessiner sur la carte
    (dernière position AIS, position estimée et cercle, écho, distances)."""
    rules = load_rules()
    instant, rejeu = _instant(a_l_instant, end)
    with traces.span("dossiers.recit", **{"dossiers.cle": cle}) as sp:
        async with request.app.state.pool.acquire() as c:
            d = await _trouver(c, cle, instant, rejeu, rules)
            ids = d["alertes"]
            alertes = {r["id"]: dict(r) for r in await c.fetch(
                "SELECT id, type, severity, status, event_time, detected_at, details, rule_version, controle_empreinte, "
                "ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat FROM alerts WHERE id = ANY($1)", ids)}
            decisions = await c.fetch("SELECT alert_id, action, note, motif, author, at FROM alert_actions "
                                      "WHERE alert_id = ANY($1) AND at <= $2 ORDER BY at", ids, instant)
            verifs = await c.fetch(
                """SELECT d.id, d.tip, d.etat, d.resultat, d.cree_le, d.heure_passage, d.rayon_m, d.distance_m,
                          d.detection_id, p.satellite, d.motif,
                          ST_X(d.position::geometry) AS lon, ST_Y(d.position::geometry) AS lat,
                          ST_X(d.position_predite::geometry) AS lon_predite, ST_Y(d.position_predite::geometry) AS lat_predite
                   FROM demandes_observation d LEFT JOIN sar_passes p ON p.id = d.pass_id
                   WHERE d.cree_le <= $3 AND (($1::bigint IS NOT NULL AND d.vessel_id = $1)
                      OR EXISTS (SELECT 1 FROM demandes_origines o WHERE o.demande_id = d.id AND o.alerte_id = ANY($2)))
                   ORDER BY d.cree_le""", d["vessel_id"], ids, instant)
            det_ids = sorted({r["evidence_id"] for r in await c.fetch(
                "SELECT evidence_id FROM alert_evidence WHERE alert_id = ANY($1) AND evidence_type = 'detection'", ids)})
            detections = await c.fetch(
                """SELECT d.id, d.ts, d.length_m, d.score, a.source, ST_X(d.geom::geometry) AS lon,
                          ST_Y(d.geom::geometry) AS lat FROM detections d JOIN analyses a ON a.id = d.analysis_id
                   WHERE d.id = ANY($1)""", det_ids)
            route = []
            if d["vessel_id"] is not None:
                route = [[r["lon"], r["lat"], _iso(r["ts"])] for r in await c.fetch(
                    """SELECT lon, lat, ts FROM (SELECT ST_X(geom::geometry) AS lon, ST_Y(geom::geometry) AS lat, ts,
                              row_number() OVER (ORDER BY ts) AS k, count(*) OVER () AS n FROM positions
                              WHERE vessel_id = $1 AND ts BETWEEN $2 AND $3) x
                       WHERE n <= 1500 OR k % ceil(n / 1500.0)::int = 0 OR k = n ORDER BY ts""",
                    d["vessel_id"], instant - timedelta(hours=_p(rules)["fenetre_h"]), instant)]
        sp.set_attributes({"dossiers.alertes": len(ids), "dossiers.verifications": len(verifs),
                           "dossiers.detections": len(detections), "dossiers.route_points": len(route)})
    return {**_sortie(d), "instant": instant.isoformat(),
            "alertes_detail": [{k: _iso(v) for k, v in alertes[i].items()} for i in ids],
            "decisions": [{k: _iso(v) for k, v in dict(r).items()} for r in decisions],
            "verifications": [{k: _iso(v) for k, v in dict(r).items()} for r in verifs],
            "detections": [{k: _iso(v) for k, v in dict(r).items()} for r in detections],
            "route": route, "preuve": preuve(alertes, verifs, detections)}


def preuve(alertes: dict, verifs, detections) -> dict | None:
    """Ce que la carte du dossier dessine : dernière position AIS avant le silence, position estimée et cercle
    d'incertitude de la vérification, écho, et les distances (estimée vers écho, dernière position vers écho)."""
    from mars.dossiers import _dist_m
    gap = next((a for a in alertes.values() if a["type"] == "AIS_GAP"), None)
    derniere = None
    if gap:
        dp = (gap["details"] or {}).get("derniere_position")
        derniere = {"lon": dp[0], "lat": dp[1]} if isinstance(dp, list) and len(dp) == 2 else \
            {"lon": gap["lon"], "lat": gap["lat"]}
        derniere["t"] = (gap["details"] or {}).get("dernier_message")
    v = next((x for x in verifs if x["detection_id"] is not None), None) or (verifs[-1] if verifs else None)
    echo = next((x for x in detections if v and x["id"] == v["detection_id"]), None) or (detections[0] if detections else None)
    if not (derniere or v or echo):
        return None
    out = {"derniere_position": derniere, "echo": echo and {"lon": echo["lon"], "lat": echo["lat"], "t": _iso(echo["ts"]),
                                                          "longueur_m": echo["length_m"], "source": echo["source"]}}
    if v and v["lon_predite"] is not None:
        out["estimee"] = {"lon": v["lon_predite"], "lat": v["lat_predite"], "rayon_m": v["rayon_m"],
                          "heure_passage": _iso(v["heure_passage"])}
        if echo:
            out["distance_estimee_echo_m"] = round(_dist_m((v["lon_predite"], v["lat_predite"]), (echo["lon"], echo["lat"])))
    if derniere and echo:
        out["distance_derniere_echo_m"] = round(_dist_m((derniere["lon"], derniere["lat"]), (echo["lon"], echo["lat"])))
    return out


class Decision(BaseModel):
    action: str                                       # vu, confirmer, classer, rouvrir
    motif: str | None = None
    note: str | None = Field(default=None, max_length=1000)
    author: str | None = Field(default=None, max_length=80)
    instant: datetime | None = None                   # instant affiché (plage) ; jamais au delà de maintenant


ACTIONS = {"vu": ("acquitter", "acquittee"), "confirmer": ("confirmer", "confirmee"), "classer": ("classer", "classee"),
           "rouvrir": ("rouvrir", "nouvelle")}
MOTIFS = ("faux_positif", "activite_legitime", "doublon")


@router.post("/api/dossiers/{cle}/decision")
async def decision(request: Request, cle: str, x: Decision):
    """Décision sur un dossier : appliquée à chacune de ses alertes ouvertes (Rouvrir : à ses alertes classées), et
    journalisée alerte par alerte dans alert_actions avec la mention du dossier ; rien n'est supprimé. Auteur :
    l'opérateur connecté, sinon le champ auteur, comme la route des alertes."""
    from controle import operateur_connecte
    if x.action not in ACTIONS:
        raise HTTPException(422, f"Action inconnue : {x.action} ({', '.join(ACTIONS)})")
    if x.action == "classer" and x.motif not in MOTIFS:
        raise HTTPException(422, f"Motif de classement obligatoire : {', '.join(MOTIFS)}")
    rules = load_rules()
    auteur = await operateur_connecte(request) or (x.author or "").strip() or "Opérateur"
    # Fenêtre du dossier à l'instant affiché (un dossier d'une plage passée reste décidable), statuts actuels
    instant = datetime.now(timezone.utc)
    if x.instant is not None:
        instant = min(instant, x.instant if x.instant.tzinfo else x.instant.replace(tzinfo=timezone.utc))
    action, statut = ACTIONS[x.action]
    note = f"Décision prise sur le dossier {cle}" + (f" : {x.note.strip()}" if (x.note or "").strip() else "")
    with traces.span("dossiers.decision", **{"dossiers.cle": cle, "dossiers.action": x.action}) as sp:
        async with request.app.state.pool.acquire() as c:
            if x.action == "rouvrir":
                vid = int(cle.split(":")[1]) if cle.startswith("navire:") else None
                # Alertes classées du navire dans la fenêtre, et celles classées par une décision sur ce dossier
                # (un écho rattaché par sa détection ne porte pas le navire en preuve)
                ids = [r["id"] for r in await c.fetch(
                    """SELECT a.id FROM alerts a WHERE a.status = 'classee' AND a.event_time >= $2 AND (
                         ($1::bigint IS NOT NULL AND EXISTS (SELECT 1 FROM alert_evidence e WHERE e.alert_id = a.id
                            AND e.evidence_type = 'vessel' AND e.evidence_id = $1)) OR a.id = $3
                         OR EXISTS (SELECT 1 FROM alert_actions x WHERE x.alert_id = a.id AND x.action = 'classer'
                                      AND x.note LIKE $4))""",
                    vid, instant - timedelta(hours=_p(rules)["fenetre_h"]),
                    int(cle.split(":")[1]) if cle.startswith("point:") else -1,
                    f"Décision prise sur le dossier {cle}%")]
            else:
                ids = (await _trouver(c, cle, instant, False, rules))["alertes"]
            async with c.transaction():
                for i in ids:
                    await c.execute("UPDATE alerts SET status = $2 WHERE id = $1", i, statut)
                    await c.execute("INSERT INTO alert_actions (alert_id, action, note, motif, author) "
                                    "VALUES ($1, $2, $3, $4, $5)", i, action, note,
                                    x.motif if x.action == "classer" else None, auteur)
        sp.set_attributes({"dossiers.alertes_touchees": len(ids), "dossiers.auteur": auteur})
    return {"cle": cle, "action": x.action, "statut": statut, "alertes": ids, "auteur": auteur}


@router.get("/api/dossiers/{cle}/verification")
async def devis_verification(request: Request, cle: str, end: datetime | None = None):
    """Avant une vérification satellite (touche V) : prochain passage qui couvre la dernière position, coût estimé
    d'une fenêtre, et refus si le capteur ou la vérification satellite est en pause (panneau de contrôle)."""
    from mars import sentinel
    rules = load_rules()
    instant = datetime.now(timezone.utc)
    # Dossier lu à l'instant affiché (jamais au delà de maintenant) ; le prochain passage se cherche après maintenant
    vu_a = min(instant, end if end is None or end.tzinfo else end.replace(tzinfo=timezone.utc)) if end else instant
    with traces.span("dossiers.verification", **{"dossiers.cle": cle}) as sp:
        async with request.app.state.pool.acquire() as c:
            d = await _trouver(c, cle, vu_a, False, rules)
            pauses = {r["source"]: r["motif"] for r in await c.fetch(
                "SELECT source, motif FROM interrupteurs_satellites WHERE en_pause")}
            missions = [m for m, s in (("S1", "sentinel1"), ("S2", "sentinel2")) if s not in pauses]
            prochain = await c.fetchrow(
                """SELECT satellite, mission, acquired_at FROM sar_passes WHERE mission = ANY($1) AND acquired_at > $2
                     AND acquired_at < $2 + interval '36 hours' AND footprint IS NOT NULL
                     AND ST_Intersects(footprint, ST_SetSRID(ST_MakePoint($3, $4), 4326)::geography)
                   ORDER BY acquired_at LIMIT 1""", missions, instant, d["lon"], d["lat"])
        refus = None
        if "tip_and_cue" in pauses:
            refus = f"Vérification satellite en pause : {pauses['tip_and_cue'] or 'sans motif'}"
        elif not missions:
            refus = "Sentinel 1 et Sentinel 2 en pause"
        cout = sentinel.estimation(rules, rules["sentinel"], 1, 0, "cpu")
        sp.set_attributes({"dossiers.refus": refus or "", "dossiers.passage": bool(prochain)})
    return {"cle": cle, "refus": refus, "cout_estime_eur": round(cout, 2), "pauses": pauses,
            "prochain_passage": prochain and {"satellite": prochain["satellite"], "mission": prochain["mission"],
                                              "heure": prochain["acquired_at"].isoformat()},
            "vessel_id": d["vessel_id"], "lon": d["lon"], "lat": d["lat"]}
