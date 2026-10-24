"""Lot T, phase 1 : mesures sur l'archive R2, sur le serveur seulement (le jeton principal est filtré sur son IP).
Lecture seule : objets R2 lus un à un dans un dossier de travail puis effacés, base interrogée en SELECT (archives,
land). Commande : `taches.py trajectoire-mesures` (docs/trajectoire.md, docs/deploiement.md).

Mémoire bornée (conteneur de 768 Mo) : une zone à la fois ; à la lecture, une position par navire et par tranche de
dix minutes (le pas de rééchantillonnage de l'article ; assez pour la coupure à 2 h de silence et le rejet des
sauts), types compacts (entiers et flottants 32 bits), préparation par groupes de navires ; la mémoire maximale
atteinte est rapportée. Mesuré sur une archive synthétique à l'échelle de la Manche (16 jours, 500 000 positions par
jour) : voir docs/trajectoire.md.
"""
from __future__ import annotations

import resource
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from mars import traces
from mars import trajectoire as T

ARTICLE_TRAJETS = 10_605         # trajets d'entraînement du jeu publié de TrAISformer (DMA, cargos et pétroliers)


def _jour(key: str) -> date | None:
    try:
        return date.fromisoformat(key.split("/date=", 1)[1][:10])
    except (IndexError, ValueError):
        return None


def _zone(key: str) -> str | None:
    return key.split("/zone=", 1)[1].split("/", 1)[0] if "/zone=" in key else None


def inventaire(objets: list[dict], lignes_base: dict, du: date, au: date, zones: list[str]) -> dict:
    """Objets R2 (positions, statiques) par zone et par jour, lignes inscrites en base, journées manquantes."""
    par = defaultdict(lambda: {"objets": 0, "octets": 0})
    for o in objets:
        j, z = _jour(o["key"]), _zone(o["key"])
        kind = o["key"].split("/")[1] if o["key"].count("/") > 1 else "?"
        if j is None or z is None or not (du <= j <= au):
            continue
        par[(kind, z, j)]["objets"] += 1
        par[(kind, z, j)]["octets"] += int(o["bytes"])
    jours = [du + timedelta(days=k) for k in range((au - du).days + 1)]
    out = {"du": str(du), "au": str(au), "jours": len(jours), "zones": {}}
    for z in zones:
        pos = [par.get(("positions", z, j)) for j in jours]
        out["zones"][z] = {
            "journees_manquantes": [str(j) for j, x in zip(jours, pos) if not x],
            "octets_positions_par_jour_mo": round(np.mean([x["octets"] for x in pos if x] or [0]) / 1e6, 1),
            "octets_statiques_par_jour_mo": round(np.mean([x["octets"] for j in jours
                                                           if (x := par.get(("statiques", z, j)))] or [0]) / 1e6, 2),
            "lignes_positions_par_jour": int(np.mean([lignes_base.get(("positions", z, j), 0) for j in jours])),
            "par_jour": {str(j): {"octets": (x or {}).get("octets", 0), "lignes": lignes_base.get(("positions", z, j), 0)}
                         for j, x in zip(jours, pos)},
        }
    out["octets_total_mo"] = round(sum(x["octets"] for x in par.values()) / 1e6, 1)
    return out


TRANCHE_S = 600                  # une position par navire et par tranche de 10 minutes (le pas de l'article)
GROUPES = 8                      # préparation par groupes de navires (MMSI modulo 8)


def secondes(ts) -> np.ndarray:
    """Horodatages de la collecte (« 2026-10-05 12:39:19.104049717 +0000 UTC », partie décimale de longueur variable)
    vers des secondes depuis 1970, en Arrow : ni objet Python par ligne, ni dépendance à la résolution que pandas
    déduirait du nombre de décimales. Illisible : -1."""
    import pyarrow as pa
    import pyarrow.compute as pc
    a = ts if isinstance(ts, (pa.Array, pa.ChunkedArray)) else pa.array(ts, pa.string())
    a = pc.cast(a, pa.string())
    d = pc.strptime(pc.utf8_slice_codeunits(a, 0, 19), format="%Y-%m-%d %H:%M:%S", unit="s", error_is_null=True)
    return pc.fill_null(pc.cast(d, pa.int64()), -1).to_numpy(zero_copy_only=False)


def reduire(table, tranche_s: int = TRANCHE_S) -> pd.DataFrame:
    """Positions brutes de l'archive (table Arrow ou DataFrame) vers le format compact : une par navire et par tranche
    (la dernière), codes « non disponible » de l'AIS mis à NaN (vitesse 102,3, route 360)."""
    import pyarrow as pa
    if isinstance(table, pd.DataFrame):
        table = pa.Table.from_pandas(table, preserve_index=False)
    num = lambda c: np.asarray(pd.to_numeric(table[c].to_pandas(), errors="coerce"), dtype="float64")  # noqa: E731
    mmsi, t = num("mmsi"), secondes(table["ts"])
    ok = (t >= 0) & (mmsi >= 100_000_000) & (mmsi <= 999_999_999)
    mmsi, t = mmsi[ok].astype("int32"), t[ok]
    lat, lon = num("lat")[ok].astype("float32"), num("lon")[ok].astype("float32")
    sog, cog = num("sog")[ok].astype("float32"), num("cog")[ok].astype("float32")
    sog[sog >= 102.2], cog[cog >= 360] = np.nan, np.nan
    k = t // tranche_s
    ordre = np.lexsort((t, k, mmsi))
    mmsi, k = mmsi[ordre], k[ordre]
    dernier = np.ones(len(ordre), bool)
    dernier[:-1] = (mmsi[1:] != mmsi[:-1]) | (k[1:] != k[:-1])
    sel = ordre[dernier]
    return pd.DataFrame({"mmsi": mmsi[dernier], "t": t[sel], "lat": lat[sel], "lon": lon[sel], "sog": sog[sel], "cog": cog[sel]})


def _memoire_mo() -> float:
    import sys
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024), 0)


def statiques(df: pd.DataFrame) -> tuple[dict, dict]:
    """Type AIS le plus fréquent par navire, et instants où la destination déclarée change (vide ignorée)."""
    d = pd.DataFrame({"mmsi": pd.to_numeric(df["mmsi"], errors="coerce"), "t": secondes(df["ts"].astype(str).tolist()),
                      "type": pd.to_numeric(df.get("ship_type"), errors="coerce"),
                      "dest": df.get("destination", pd.Series([None] * len(df))).astype("string").str.upper().str.strip()})
    d = d.dropna(subset=["mmsi"]).sort_values(["mmsi", "t"])
    types = d.dropna(subset=["type"]).groupby("mmsi")["type"].agg(lambda s: int(s.mode().iloc[0])).to_dict()
    changements: dict = defaultdict(list)
    dd = d[d.dest.notna() & (d.dest != "")]
    prec = dd.groupby("mmsi")["dest"].shift()
    for m, t in dd[(prec.notna()) & (dd.dest != prec)][["mmsi", "t"]].itertuples(index=False):
        changements[int(m)].append(float(t))
    return {int(k): v for k, v in types.items()}, dict(changements)


def heures_creuses(compte: Counter, du: date, au: date) -> dict:
    """Heures sans aucune position reçue dans la zone, et coupures d'une heure ou plus."""
    debut = int(datetime.combine(du, datetime.min.time(), timezone.utc).timestamp()) // 3600
    fin = int(datetime.combine(au + timedelta(days=1), datetime.min.time(), timezone.utc).timestamp()) // 3600
    vides = [h for h in range(debut, fin) if not compte.get(h)]
    trous, i = [], 0
    while i < len(vides):
        j = i
        while j + 1 < len(vides) and vides[j + 1] == vides[j] + 1:
            j += 1
        trous.append({"debut": datetime.fromtimestamp(vides[i] * 3600, timezone.utc).strftime("%d/%m/%Y %H:00"),
                      "heures": j - i + 1})
        i = j + 1
    return {"heures_sans_position": len(vides), "heures": fin - debut, "trous": trous[:40]}


def mesurer(conn, r2, du: date, au: date, zones: list[str], test_jours: int, workdir: Path, log=print,
            p: dict = T.PARAMS, regles: dict | None = None, cercle_seul: bool = False,
            hypotheses: bool = False) -> dict:
    import pyarrow as pa
    # L'allocateur par défaut d'Arrow (mimalloc) garde la mémoire libérée d'une journée à l'autre : 447 Mo après
    # 16 journées de la Manche synthétique contre 257 Mo avec l'allocateur du système (mesuré le 22/10/2026)
    pa.set_memory_pool(pa.system_memory_pool())
    t_debut = time.time()
    workdir.mkdir(parents=True, exist_ok=True)
    objets = r2.list("ais/")
    lignes = {(k, z, d): int(n) for k, z, d, n in conn.execute(
        "SELECT kind, zone, day, sum(rows) FROM archives WHERE day BETWEEN %s AND %s GROUP BY 1, 2, 3", (du, au))}
    out = {"version": 1, "parametres": {k: v for k, v in p.items()}, "inventaire": inventaire(objets, lignes, du, au, zones),
           "zones": {}}
    coupe = datetime.combine(au - timedelta(days=test_jours - 1), datetime.min.time(), timezone.utc).timestamp()
    evals, tous, cercle_a, cercle_t = [], [], [], []
    dep_a, dep_t = [], []                           # étape 2 : départs et déplacements réels, par zone
    cercle_seul = cercle_seul or hypotheses
    from mars.ais.live import ZONES
    for z in zones:
        with traces.span("trajectoire.zone", **{"trajectoire.zone": z}) as sp:
            frames, compte, stats_frames = [], Counter(), []
            for o in sorted(objets, key=lambda o: o["key"]):
                j = _jour(o["key"])
                if _zone(o["key"]) != z or j is None or not (du <= j <= au):
                    continue
                local = workdir / Path(o["key"]).name
                with traces.span("trajectoire.lecture", **{"trajectoire.objet": o["key"]}):
                    r2.download(o["key"], local)
                    try:
                        if "/positions/" in o["key"]:
                            r = reduire(pq.read_table(local, columns=["mmsi", "ts", "lat", "lon", "sog", "cog"],
                                                      use_threads=False))          # un fil : moins de tampons
                            compte.update((r.t // 3600).tolist())
                            frames.append(r)
                        elif "/statiques/" in o["key"]:
                            cols = [c for c in ("mmsi", "ts", "ship_type", "destination")
                                    if c in pq.read_schema(local).names]
                            stats_frames.append(pd.read_parquet(local, columns=cols))
                    finally:
                        local.unlink(missing_ok=True)
            if not frames:
                out["zones"][z] = {"positions": 0}
                continue
            df = pd.concat(frames, ignore_index=True)
            del frames
            types, destinations = statiques(pd.concat(stats_frames, ignore_index=True)) if stats_frames else ({}, {})
            a, b, c, d = ZONES[z]
            land = [row[0] for row in conn.execute(
                "SELECT ST_AsBinary(geom::geometry) FROM land WHERE ST_Intersects(geom::geometry, "
                "ST_MakeEnvelope(%s, %s, %s, %s, 4326))", (b - 0.2, a - 0.2, d + 0.2, c + 0.2))]
            cote = T.filtre_cotier(land, p["cote_m"]) if land else None
            large = T.filtre_cotier(land, 10_000.0) if land else None
            # Préparation par groupes de navires (les trajets d'un navire ne dépendent que de lui) : la mémoire des
            # calculs intermédiaires est divisée par le nombre de groupes
            trs, st = [], Counter()
            for g in range(GROUPES):
                part = df[df.mmsi.values % GROUPES == g]
                trs_g, st_g = T.trajets(part, p, destinations, cote)
                trs += trs_g
                st.update({k: v for k, v in st_g.items() if not isinstance(v, bool)})
            st = {**dict(st), "filtre_cotier": cote is not None}
            n_pos, n_nav = len(df), int(df.mmsi.nunique())
            verite = T.Verite(df[["mmsi", "t", "lat", "lon"]])          # où le navire réapparaît, même après un silence
            del df
            for tr in trs:
                tr.meta = {"categorie": T.categorie(types.get(tr.mmsi)), "zone_trajet": z}
            tab = pd.DataFrame([{"categorie": t.meta["categorie"], "duree_h": t.duree_h, "fin": t.fin,
                                 "jour": datetime.fromtimestamp(t.t[0], timezone.utc).date(),
                                 "semaine": int(pd.Timestamp(t.t[0], unit="s").isocalendar().week),
                                 "au_large": (bool(not large(t.lat[-1:], t.lon[-1:])[0]) if (large and t.fin == "silence") else None)}
                                for t in trs])
            train = [t for t in trs if t.t[-1] < coupe]
            test = [t for t in trs if t.t[0] >= coupe]
            routes = T.Routes(p) if cercle_seul else T.Routes(p).apprendre(train)
            ev = pd.DataFrame() if cercle_seul else T.evaluer(test, routes, p)
            if not ev.empty:
                evals.append(ev)
            # Contenance de l'estime de 1 à 30 h (règles 2026.10.45) : hors pêche (écartée de la vérification
            # satellite), 1 nœud et plus ; vérité : position où le navire réapparaît dans la zone
            with traces.span("trajectoire.cercle", **{"trajectoire.zone": z}) as sc:
                hors_peche = lambda xs: [t for t in xs if t.meta["categorie"] != "peche"]  # noqa: E731
                for xs, acc, dep in ((hors_peche(train), cercle_a, dep_a), (hors_peche(test), cercle_t, dep_t)):
                    if hypotheses:
                        dd = T.deplacements(xs, verite)
                        if dd:
                            dep.append(dd)
                        continue
                    e = T.evaluer_longs(xs, verite)
                    if not e.empty:
                        acc.append(e.drop(columns=[c for c in ("zone_trajet",) if c in e]))
                sc.set_attribute("trajectoire.cercle_fenetres", int(sum(len(x) for x in cercle_a + cercle_t)))
            del verite
            jours_mesures = (au - du).days + 1
            ct = tab[tab.categorie.isin(["cargo", "petrolier"])] if not tab.empty else tab
            par_jour_ct = len(ct) / jours_mesures if len(ct) else 0.0
            zr = {
                "positions_reduites": n_pos, "navires": n_nav, "navires_types": sum(1 for m in types if m),
                "heures": heures_creuses(compte, du, au), "preparation": st,
                "trajets": int(len(tab)),
                "par_categorie": tab.categorie.value_counts().to_dict() if len(tab) else {},
                "par_semaine": {str(k): int(v) for k, v in tab.semaine.value_counts().sort_index().items()} if len(tab) else {},
                "par_semaine_categorie": {f"{s}/{c}": int(n) for (s, c), n in tab.groupby(["semaine", "categorie"]).size().items()} if len(tab) else {},
                "duree_h": {"mediane": round(float(tab.duree_h.median()), 1), "p90": round(float(tab.duree_h.quantile(.9)), 1)} if len(tab) else {},
                "fin": tab.fin.value_counts().to_dict() if len(tab) else {},
                "silences_au_large": round(float(tab[tab.fin == "silence"].au_large.dropna().mean()), 3) if len(tab) and large else None,
                "cargo_petrolier_par_jour": round(par_jour_ct, 1),
                "projection": {
                    "jours_pour_le_volume_de_l_article": round(ARTICLE_TRAJETS / par_jour_ct) if par_jour_ct else None,
                    "date": str(du + timedelta(days=round(ARTICLE_TRAJETS / par_jour_ct))) if par_jour_ct else None,
                    "tous_types_par_jour": round(len(tab) / jours_mesures, 1),
                },
                "separation": {"trajets_apprentissage": len(train), "trajets_test": len(test), "fenetres": int(len(ev)),
                               "cellules_routes": len(routes.somme)},
                "references": T.resumer(ev, p=p),
                "memoire_max_mo": _memoire_mo(),
            }
            out["zones"][z] = zr
            sp.set_attributes({"trajectoire.positions": n_pos, "trajectoire.trajets": int(len(tab)),
                               "trajectoire.fenetres": int(len(ev)), "trajectoire.memoire_mo": zr["memoire_max_mo"]})
            tous.append(tab.assign(zone=z))
            log(f"{z} : {n_pos} positions réduites, {len(tab)} trajets, {len(ev)} fenêtres, mémoire {zr['memoire_max_mo']} Mo")
    if evals:
        ev = pd.concat(evals, ignore_index=True)
        ev["vitesse"] = pd.cut(ev.vitesse_kn, [0, 5, 10, 15, 25, 99], labels=["0-5", "5-10", "10-15", "15-25", "25+"])
        out["references"] = {"ensemble": T.resumer(ev, p=p), "par_zone": T.resumer(ev, "zone", p),
                             "par_categorie": T.resumer(ev, "categorie", p), "par_vitesse": T.resumer(ev, "vitesse", p),
                             "cargo_petrolier": T.resumer(ev[ev.categorie.isin(["cargo", "petrolier"])], p=p)}
        out["rayons_80_90"] = rayons(ev, p)
    if cercle_a and cercle_t and regles:
        a, t_ = pd.concat(cercle_a, ignore_index=True), pd.concat(cercle_t, ignore_index=True)
        table = T.table_quantiles(a)
        tc = regles["tip_and_cue"]
        seuil = float((tc["prediction"].get("cercle_appris") or {}).get("contenance_min", 0.4))
        out["cercle_appris"] = {"table": table, "fenetres_apprentissage": int(len(a)), "fenetres_test": int(len(t_)),
                                "seuil": seuil,
                                "gain": T.gain_garde(t_, table, regles["sentinel"], tc["fenetres_max_par_demande"], seuil)}
    if dep_a and dep_t and regles:
        with traces.span("trajectoire.hypotheses") as sh:
            fusion = lambda xs: {k: np.concatenate([x[k] for x in xs]) for k in xs[0]}  # noqa: E731
            a, t_ = fusion(dep_a), fusion(dep_t)
            tc = regles["tip_and_cue"]
            table = tc["prediction"]["cercle_appris"]
            seuil = float(table.get("contenance_min", 0.4))
            out["hypotheses"] = {"departs_apprentissage": int(len(a["t"])), "departs_test": int(len(t_["t"])),
                                 "seuil": seuil, "sans_meme_navire": True, "garde": "borne basse de Wilson à 90 %"}
            for min_ex in (20, 50):
                an = T.Analogues(min_exemples=min_ex).apprendre(a)
                for nom, cats, excl in (("cargo_petrolier", ("cargo", "petrolier"), None),
                                        ("autres", None, ("cargo", "petrolier")), ("ensemble", None, None)):
                    out["hypotheses"][f"{nom}_min{min_ex}"] = T.evaluer_hypotheses(
                        an, t_, table, regles["sentinel"], tc["fenetres_max_par_demande"], seuil,
                        categories=cats, exclure=excl, max_departs=2000)
                    log(f"hypothèses {nom}, {min_ex} exemples au moins : évaluées")
            sh.set_attributes({"trajectoire.departs_apprentissage": int(len(a["t"])),
                               "trajectoire.departs_test": int(len(t_["t"]))})
    out["duree_s"] = round(time.time() - t_debut, 1)
    out["memoire_max_mo"] = _memoire_mo()
    return out


def rayons(ev: pd.DataFrame, p: dict = T.PARAMS) -> list[dict]:
    """Rayon qui aurait contenu 80 % et 90 % des positions réelles, par horizon et tranche de vitesse (navires à
    5 nœuds et plus), pour l'estime et les routes fréquentes : la barre d'un cercle « appris »."""
    out = []
    for v, g in ev[ev.vitesse_kn >= 5].groupby("vitesse", observed=True):
        for h in p["horizons_h"]:
            l = {"vitesse": str(v), "horizon_h": h, "fenetres": int(len(g)),
                 "cercle_actuel_km": round(float(g[f"cercle_{h}h_km"].mean()), 1),
                 "couverture_actuelle": round(float(g[f"dans_cercle_{h}h"].mean()), 3)}
            for pred in ("estime", "routes"):
                c = f"{pred}_fde_{h}h"
                if c in g:
                    l[f"{pred}_r80_km"] = round(float(g[c].quantile(0.8)), 1)
                    l[f"{pred}_r90_km"] = round(float(g[c].quantile(0.9)), 1)
            out.append(l)
    return out
