"""Prévision de trajectoire, lot T, phase 1 (docs/trajectoire.md) : préparation des trajets et références avant tout
modèle. Calcul pur (numpy, pandas), sans base ni réseau : la commande `taches.py trajectoire-mesures` lui fournit les
positions lues dans l'archive R2, la pré mesure du Mac des positions lues par l'API.

* Préparation, comme TrAISformer (Nguyen et Fablet) : vitesse déclarée de 30 nœuds ou plus écartée, saut de position
  impliquant plus de 40 nœuds écarté, coupure après 2 h de silence, à l'arrêt (sous 0,5 nœud) et au changement de
  destination, rééchantillonnage à 10 minutes, positions à moins d'un mille de la côte écartées, trajets de 4 h au
  moins.
* Références : l'estime de la vérification satellite (dernière vitesse et dernière route déclarées, mars/tip_and_cue.py),
  la même estime sur la vitesse mesurée entre les deux derniers points, et un suivi des routes fréquentes (grille de
  densité des caps appris sur la période d'entraînement).
* Erreurs : écart final (FDE) et moyen (ADE) à 1, 2 et 3 h, en kilomètres ; part des positions réelles dans le cercle
  actuel de la vérification satellite (2 km plus 30 % de la distance parcourue, borné à 30 km).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

R_TERRE = 6_371_000.0
NOEUD = 1852.0 / 3600.0                       # m/s

PARAMS = {
    "pas_s": 600,                 # rééchantillonnage à 10 minutes
    "silence_s": 7200,            # coupure après 2 h sans position
    "arret_kn": 0.5,              # à l'arrêt : retiré du trajet, qui est coupé
    "sog_max_kn": 30.0,           # vitesse déclarée aberrante (article)
    "saut_max_kn": 40.0,          # saut de position impliquant plus de 40 nœuds (article)
    "duree_min_h": 4.0,           # trajets de 4 h au moins (article)
    "cote_m": 1852.0,             # positions à moins d'un mille de la côte écartées (article)
    "historique_h": 3.0,          # 3 h d'historique (article)
    "horizons_h": (1, 2, 3),
    "fenetre_pas_h": 1.0,         # une fenêtre d'évaluation par heure de trajet
    "grille_deg": 0.02,           # routes fréquentes : cellules de 0,02 degré (environ 2 km)
    "secteurs": 8,                # et huit secteurs de cap, pour séparer les voies opposées
    "grille_min": 5,              # passages minimum dans une cellule pour en suivre le cap
    # Cercle actuel de la vérification satellite (config/rules.yaml, tip_and_cue.prediction)
    "rayon_depart_m": 2000.0, "part_vitesse_par_h": 0.3, "rayon_max_m": 30000.0,
}

CATEGORIES = ("cargo", "petrolier", "passagers", "peche", "autre", "inconnu")


def categorie(code) -> str:
    """Type AIS vers une catégorie : cargo 70 à 79, pétrolier 80 à 89, passagers 60 à 69, pêche 30."""
    if code is None or (isinstance(code, float) and math.isnan(code)):
        return "inconnu"
    c = int(code)
    return ("cargo" if 70 <= c <= 79 else "petrolier" if 80 <= c <= 89 else "passagers" if 60 <= c <= 69
            else "peche" if c == 30 else "inconnu" if c == 0 else "autre")


LIBELLES = {"Cargo": "cargo", "Tanker": "petrolier", "Passenger": "passagers", "Fishing": "peche"}


def categorie_libelle(libelle) -> str:
    """Libellé de type de l'API (mars/ais/live.py, ship_type_label) vers une catégorie."""
    if not libelle:
        return "inconnu"
    return LIBELLES.get(str(libelle), "autre")


def zone_de(lat: float, lon: float) -> str | None:
    """Zone collectée, dans l'ordre de la collecte (scripts/ais_live.py, zone_of) : la Bretagne l'emporte sur la
    Manche là où elles se chevauchent."""
    from mars.ais.live import ZONES
    for nom, (a, b, c, d) in ZONES.items():
        if a <= lat <= c and b <= lon <= d:
            return nom
    return None


def filtre_cotier(polygones_wkb: list[bytes], cote_m: float = PARAMS["cote_m"]):
    """Fonction (lat, lon) -> masque des positions à moins de cote_m de la terre. Polygones de la table land (déjà
    subdivisés) projetés localement en mètres par bande d'un degré de latitude, arbre spatial, requête « à moins
    de » vectorisée."""
    import shapely
    geoms = [shapely.from_wkb(w) for w in polygones_wkb]
    arbres: dict = {}

    def projeter(lat0):
        k = math.cos(math.radians(lat0)) * 111_320.0
        return lambda x: np.column_stack([x[:, 0] * k, x[:, 1] * 110_540.0])

    def masque(lat, lon):
        lat, lon = np.asarray(lat, float), np.asarray(lon, float)
        if not geoms or not len(lat):
            return np.zeros(len(lat), bool)
        lat0 = round(float(np.mean(lat)))
        if lat0 not in arbres:
            pr = projeter(lat0)
            arbres[lat0] = (shapely.STRtree([shapely.transform(g, pr) for g in geoms]), pr)
        arbre, pr = arbres[lat0]
        pts = shapely.points(pr(np.column_stack([lon, lat])))
        idx, _ = arbre.query(pts, predicate="dwithin", distance=cote_m)
        out = np.zeros(len(lat), bool)
        out[np.unique(idx)] = True
        return out
    return masque


def haversine_m(lat1, lon1, lat2, lon2):
    la1, la2 = np.radians(lat1), np.radians(lat2)
    h = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin(np.radians(np.asarray(lon2) - lon1) / 2) ** 2
    return 2 * R_TERRE * np.arcsin(np.sqrt(np.clip(h, 0, 1)))


def avancer(lat, lon, cap_deg, distance_m):
    """Point atteint en suivant un cap constant (approximation locale, exacte à mieux que 0,1 % sur 100 km)."""
    c = np.radians(cap_deg)
    dlat = distance_m * np.cos(c) / R_TERRE
    dlon = distance_m * np.sin(c) / (R_TERRE * np.cos(np.radians(lat)))
    return lat + np.degrees(dlat), lon + np.degrees(dlon)


# Préparation

def nettoyer(df: pd.DataFrame, p: dict = PARAMS) -> tuple[pd.DataFrame, dict]:
    """Positions (mmsi, t en secondes, lat, lon, sog, cog) triées par navire et instant ; écarte les vitesses
    déclarées aberrantes et les points isolés impossibles (plus de 40 nœuds pour y arriver et pour en repartir, ou
    pour y arriver s'il est le dernier du navire)."""
    st = {"positions_lues": len(df), "vitesse_declaree_aberrante": 0, "saut_impossible": 0}
    if df.empty:
        return df.reset_index(drop=True), st
    df = df.dropna(subset=["lat", "lon", "t"]).sort_values(["mmsi", "t"], kind="stable")
    df = df.drop_duplicates(["mmsi", "t"])
    n = len(df)
    df = df[~(df.sog >= p["sog_max_kn"])]
    st["vitesse_declaree_aberrante"] = n - len(df)
    meme = df.mmsi.values[1:] == df.mmsi.values[:-1]
    dt = np.diff(df.t.values)
    d = haversine_m(df.lat.values[:-1], df.lon.values[:-1], df.lat.values[1:], df.lon.values[1:])
    v = np.where(dt > 0, d / np.maximum(dt, 1e-9) / NOEUD, np.inf)
    vite = meme & (v > p["saut_max_kn"]) & (d > 500)            # entre le point k et le point k+1
    arrivee = np.concatenate([[False], vite])
    depart = np.concatenate([vite, [False]])
    dernier = np.concatenate([~meme, [True]])                     # dernier point de son navire
    saut = arrivee & (depart | dernier)
    df = df[~saut]
    st["saut_impossible"] = int(saut.sum())
    return df.reset_index(drop=True), st


@dataclass
class Trajet:
    mmsi: int
    t: np.ndarray                 # instants rééchantillonnés (s)
    lat: np.ndarray
    lon: np.ndarray
    sog: np.ndarray               # vitesse et route déclarées au dernier message avant chaque instant
    cog: np.ndarray
    fin: str = ""                 # motif de la coupure qui termine le trajet
    meta: dict = field(default_factory=dict)

    @property
    def duree_h(self) -> float:
        return (self.t[-1] - self.t[0]) / 3600 if len(self.t) else 0.0


def decouper(df: pd.DataFrame, p: dict = PARAMS, destinations: dict | None = None) -> tuple[list[tuple], dict]:
    """Segments bruts (mmsi, indices de début et de fin, motif de fin) : coupure au silence, à l'arrêt (positions
    à l'arrêt retirées), au changement de destination (`destinations` : mmsi vers instants de changement), et au saut
    impossible qui n'est pas un point isolé (deux navires sous un même MMSI, position fausse durable)."""
    st = {"coupure_silence": 0, "coupure_arret": 0, "coupure_destination": 0, "coupure_saut": 0}
    lat, lon = df.lat.values, df.lon.values
    segs = []
    m, t, sog = df.mmsi.values, df.t.values, df.sog.values
    arret = np.nan_to_num(sog, nan=99) < p["arret_kn"]
    debut = None
    for i in range(len(df)):
        nouveau = i == 0 or m[i] != m[i - 1]
        if not nouveau and debut is not None:
            motif = None
            if t[i] - t[i - 1] > p["silence_s"]:
                motif = "silence"
            elif (d := haversine_m(lat[i - 1], lon[i - 1], lat[i], lon[i])) > 5000 and \
                    d / max(t[i] - t[i - 1], 1) / NOEUD > p["saut_max_kn"]:
                motif = "saut"
            elif destinations and m[i] in destinations and any(t[i - 1] < c <= t[i] for c in destinations[m[i]]):
                motif = "destination"
            if motif:
                segs.append((m[i - 1], debut, i - 1, motif))
                st[f"coupure_{motif}"] += 1
                debut = None
        elif nouveau and debut is not None:
            segs.append((m[i - 1], debut, i - 1, "fin_donnees"))
            debut = None
        if arret[i]:
            if debut is not None:
                segs.append((m[i], debut, i - 1, "arret"))
                st["coupure_arret"] += 1
                debut = None
            continue
        if debut is None:
            debut = i
    if debut is not None and len(df):
        segs.append((m[-1], debut, len(df) - 1, "fin_donnees"))
    return segs, st


def reechantillonner(df: pd.DataFrame, seg: tuple, p: dict = PARAMS) -> Trajet | None:
    """Trajet sur une grille de 10 minutes (interpolation linéaire des positions) ; vitesse et route déclarées du
    dernier message à chaque instant de la grille (ce que l'estime aurait connu)."""
    mmsi, a, b, motif = seg
    t = df.t.values[a:b + 1]
    if len(t) < 2:
        return None
    pas = p["pas_s"]
    g = np.arange(math.ceil(t[0] / pas) * pas, t[-1] + 1e-9, pas)
    if len(g) < 2:
        return None
    # Flottants 32 bits (0,4 m de résolution à nos latitudes) : la mémoire d'une zone entière reste bornée
    lat = np.interp(g, t, df.lat.values[a:b + 1]).astype("float32")
    lon = np.interp(g, t, df.lon.values[a:b + 1]).astype("float32")
    k = np.clip(np.searchsorted(t, g, side="right") - 1, 0, len(t) - 1)
    return Trajet(int(mmsi), g, lat, lon, np.asarray(df.sog.values[a:b + 1][k], "float32"),
                  np.asarray(df.cog.values[a:b + 1][k], "float32"), motif)


def couper_masque(tr: Trajet, garder: np.ndarray, motif: str) -> list[Trajet]:
    """Retire les instants non gardés (près de la côte) en coupant le trajet à chaque trou."""
    out, i, n = [], 0, len(tr.t)
    while i < n:
        if not garder[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and garder[j + 1]:
            j += 1
        s = slice(i, j + 1)
        out.append(Trajet(tr.mmsi, tr.t[s], tr.lat[s], tr.lon[s], tr.sog[s], tr.cog[s],
                          tr.fin if j == n - 1 else motif, dict(tr.meta)))
        i = j + 1
    return out


def trajets(df: pd.DataFrame, p: dict = PARAMS, destinations: dict | None = None, pres_cote=None) -> tuple[list[Trajet], dict]:
    """Chaîne complète. `pres_cote(lat, lon) -> masque` : positions à moins de cote_m de la côte (None : sans
    filtre côtier, signalé dans le décompte)."""
    df, st = nettoyer(df, p)
    segs, st2 = decouper(df, p, destinations)
    st.update(st2)
    out, courts, cote = [], 0, 0
    for seg in segs:
        tr = reechantillonner(df, seg, p)
        if tr is None:
            courts += 1
            continue
        morceaux = [tr]
        if pres_cote is not None:
            masque = np.asarray(pres_cote(tr.lat, tr.lon), dtype=bool)
            cote += int(masque.sum())
            morceaux = couper_masque(tr, ~masque, "cote")
        for m in morceaux:
            if m.duree_h >= p["duree_min_h"]:
                out.append(m)
            else:
                courts += 1
    st.update({"segments": len(segs), "trop_courts": courts, "points_pres_cote": cote,
               "filtre_cotier": pres_cote is not None, "trajets": len(out)})
    return out, st


# Références

def estime(tr: Trajet, i: int, pas_futurs: int, p: dict = PARAMS, mesuree: bool = False) -> np.ndarray:
    """Estime depuis l'instant i : vitesse et route déclarées (comme la vérification satellite), ou mesurées entre
    les instants i-1 et i. Renvoie (pas_futurs, 2) positions."""
    if mesuree:
        d = haversine_m(tr.lat[i - 1], tr.lon[i - 1], tr.lat[i], tr.lon[i])
        v = d / p["pas_s"]
        cap = math.degrees(math.atan2(math.radians(tr.lon[i] - tr.lon[i - 1]) * math.cos(math.radians(tr.lat[i])),
                                      math.radians(tr.lat[i] - tr.lat[i - 1]))) % 360
    else:
        v = (0.0 if np.isnan(tr.sog[i]) else float(tr.sog[i])) * NOEUD
        cap = float(tr.cog[i]) if not np.isnan(tr.cog[i]) else 0.0
    k = np.arange(1, pas_futurs + 1)
    lat, lon = avancer(tr.lat[i], tr.lon[i], cap, v * p["pas_s"] * k)
    return np.stack([lat, lon], axis=1)


class Routes:
    """Routes fréquentes : pour chaque cellule de la grille et chaque secteur du cap d'arrivée, le cap moyen avec
    lequel le trafic d'entraînement en repart (les virages des routes sont appris). La prévision avance pas à pas à
    vitesse constante en prenant ce cap quand la cellule est assez fréquentée, sinon garde le sien."""

    def __init__(self, p: dict = PARAMS):
        self.p, self.somme = p, {}

    def _cle(self, lat, lon, cap):
        g, s = self.p["grille_deg"], self.p["secteurs"]
        return (int(math.floor(lat / g)), int(math.floor(lon / g)), int((cap % 360) // (360 / s)))

    def apprendre(self, trs: list[Trajet]) -> "Routes":
        for tr in trs:
            for i in range(1, len(tr.t) - 1):
                arrivee = _cap(tr.lat[i - 1], tr.lon[i - 1], tr.lat[i], tr.lon[i])
                cap = _cap(tr.lat[i], tr.lon[i], tr.lat[i + 1], tr.lon[i + 1])
                if arrivee is None or cap is None:
                    continue
                k = self._cle(tr.lat[i], tr.lon[i], arrivee)
                s = self.somme.setdefault(k, [0.0, 0.0, 0])
                s[0] += math.sin(math.radians(cap))
                s[1] += math.cos(math.radians(cap))
                s[2] += 1
        return self

    def prevoir(self, tr: Trajet, i: int, pas_futurs: int) -> np.ndarray:
        d = haversine_m(tr.lat[i - 1], tr.lon[i - 1], tr.lat[i], tr.lon[i])
        v = d / self.p["pas_s"]
        cap = _cap(tr.lat[i - 1], tr.lon[i - 1], tr.lat[i], tr.lon[i]) or 0.0
        lat, lon, out = tr.lat[i], tr.lon[i], []
        for _ in range(pas_futurs):
            s = self.somme.get(self._cle(lat, lon, cap))
            if s and s[2] >= self.p["grille_min"]:
                cap = math.degrees(math.atan2(s[0], s[1])) % 360
            lat, lon = avancer(lat, lon, cap, v * self.p["pas_s"])
            out.append((lat, lon))
        return np.array(out)


def _cap(lat1, lon1, lat2, lon2):
    dy = math.radians(lat2 - lat1)
    dx = math.radians(lon2 - lon1) * math.cos(math.radians(lat1))
    if abs(dx) + abs(dy) < 1e-9:
        return None
    return math.degrees(math.atan2(dx, dy)) % 360


def rayon_actuel(sog_kn: float, h: float, p: dict = PARAMS) -> float:
    """Cercle actuel de la vérification satellite (mars/tip_and_cue.py, predire)."""
    parcours = (0.0 if np.isnan(sog_kn) else sog_kn) * 1852.0 * h
    return min(p["rayon_depart_m"] + p["part_vitesse_par_h"] * parcours, p["rayon_max_m"])


def evaluer(trs: list[Trajet], routes: Routes | None, p: dict = PARAMS,
            predicteurs: tuple = ("estime", "estime_mesuree", "routes")) -> pd.DataFrame:
    """Une ligne par fenêtre (3 h d'historique, 3 h à prévoir, une fenêtre par heure) : erreurs finale et moyenne de
    chaque prédicteur à chaque horizon (km), dans le cercle actuel ou non."""
    pas_h = int(3600 / p["pas_s"])
    hist, hor = int(p["historique_h"] * pas_h), max(p["horizons_h"]) * pas_h
    lignes = []
    for tr in trs:
        for i in range(hist - 1, len(tr.t) - hor, int(p["fenetre_pas_h"] * pas_h)):
            vrai = np.stack([tr.lat[i + 1:i + 1 + hor], tr.lon[i + 1:i + 1 + hor]], axis=1)
            preds = {"estime": estime(tr, i, hor, p)}
            if "estime_mesuree" in predicteurs:
                preds["estime_mesuree"] = estime(tr, i, hor, p, mesuree=True)
            if routes is not None and "routes" in predicteurs:
                preds["routes"] = routes.prevoir(tr, i, hor)
            # Zone de la dernière position observée (un navire passe d'une zone à l'autre)
            l = {"mmsi": tr.mmsi, "t": int(tr.t[i]), **tr.meta, "zone": zone_de(tr.lat[i], tr.lon[i]) or tr.meta.get("zone"),
                 "sog_kn": float(tr.sog[i]),
                 "vitesse_kn": float(haversine_m(tr.lat[i - 1], tr.lon[i - 1], tr.lat[i], tr.lon[i]) / p["pas_s"] / NOEUD)}
            for nom, pr in preds.items():
                e = haversine_m(vrai[:, 0], vrai[:, 1], pr[:, 0], pr[:, 1]) / 1000
                for h in p["horizons_h"]:
                    l[f"{nom}_fde_{h}h"] = float(e[h * pas_h - 1])
                    l[f"{nom}_ade_{h}h"] = float(e[:h * pas_h].mean())
            for h in p["horizons_h"]:
                r = rayon_actuel(float(tr.sog[i]), h, p)
                l[f"cercle_{h}h_km"] = r / 1000
                l[f"dans_cercle_{h}h"] = bool(l[f"estime_fde_{h}h"] * 1000 <= r)
            lignes.append(l)
    return pd.DataFrame(lignes)


def resumer(ev: pd.DataFrame, par: str | None = None, p: dict = PARAMS) -> list[dict]:
    """Moyenne, médiane et 90e centile des erreurs finales (km) par prédicteur et horizon, et couverture du cercle
    actuel, éventuellement par zone ou catégorie."""
    if ev.empty:
        return []
    groupes = [("ensemble", ev)] if par is None else list(ev.groupby(par))
    out = []
    for nom, g in groupes:
        l = {"groupe": nom, "fenetres": int(len(g)), "navires": int(g.mmsi.nunique())}
        for pred in ("estime", "estime_mesuree", "routes"):
            for h in p["horizons_h"]:
                c = f"{pred}_fde_{h}h"
                if c in g:
                    l[f"{pred}_{h}h"] = {"moyenne": round(float(g[c].mean()), 2), "mediane": round(float(g[c].median()), 2),
                                         "p90": round(float(g[c].quantile(0.9)), 2),
                                         "ade": round(float(g[f"{pred}_ade_{h}h"].mean()), 2)}
        for h in p["horizons_h"]:
            l[f"cercle_{h}h"] = {"couverture": round(float(g[f"dans_cercle_{h}h"].mean()), 3),
                                 "rayon_moyen_km": round(float(g[f"cercle_{h}h_km"].mean()), 1)}
        out.append(l)
    return out


# Cercle appris (phase 2, étape 1) : table des rayons et gain sur la période tenue à l'écart

PARAMS_CERCLE = {**PARAMS, "historique_h": 1 / 3, "horizons_h": (1, 2, 3, 4, 5, 6)}   # l'estime n'a besoin que du dernier point
VITESSES_KN = (1.0, 5.0, 10.0, 15.0)          # bornes basses des tranches de vitesse déclarée


def table_cercle(ev: pd.DataFrame, contenance: float = 0.8, vitesses=VITESSES_KN,
                 horizons=PARAMS_CERCLE["horizons_h"], rayon_min_m: float = 1000.0, min_fenetres: int = 30) -> dict:
    """Rayon qui contient `contenance` des positions réelles autour de l'estime, par tranche de vitesse déclarée et par
    horizon (fenêtres d'apprentissage). Une tranche trop peu fournie prend les rayons de la tranche voisine mesurée ;
    les rayons croissent avec l'horizon (on corrige un quantile qui baisserait par hasard)."""
    bornes = [*vitesses, 1e9]
    rayons, effectifs = [], []
    for k in range(len(vitesses)):
        g = ev[(ev.sog_kn >= bornes[k]) & (ev.sog_kn < bornes[k + 1])]
        effectifs.append(int(len(g)))
        if len(g) < min_fenetres:
            rayons.append(None)
            continue
        r = [float(g[f"estime_fde_{h}h"].quantile(contenance)) * 1000 for h in horizons]
        rayons.append([round(max(r[:j + 1]), -2) for j in range(len(r))])
    for k in range(len(rayons)):
        if rayons[k] is None:
            voisin = next((rayons[j] for j in sorted(range(len(rayons)), key=lambda j: abs(j - k)) if rayons[j]), None)
            rayons[k] = list(voisin) if voisin else [rayon_min_m] * len(horizons)
    return {"contenance": contenance, "vitesses_kn": list(vitesses), "horizons_h": list(horizons),
            "rayon_min_m": rayon_min_m, "rayons_m": rayons, "fenetres_par_tranche": effectifs}


def gain_cercle(ev: pd.DataFrame, table: dict, s: dict, fenetres_max: int, p: dict = PARAMS) -> list[dict]:
    """Par horizon, sur des fenêtres tenues à l'écart : contenance, rayon moyen, part des demandes faisables
    (fenêtres satellites sous le maximum) et part « trouvée » (dans le cercle et faisable) ; cercle actuel, appris,
    et appris plafonné au rayon que couvrent les fenêtres permises."""
    from mars.tip_and_cue import centres_fenetres, rayon_appris, rayon_couvrable
    plafond = rayon_couvrable(s, fenetres_max)
    out = []
    for h in table["horizons_h"]:
        c = f"estime_fde_{h}h"
        if c not in ev:
            continue
        err = ev[c].values * 1000
        lignes = {"horizon_h": h, "fenetres": int(len(ev))}
        appris = lambda sog: rayon_appris(table, 0.0 if np.isnan(sog) else sog, h)  # noqa: E731
        for nom, rayon in (("actuel", lambda sog: rayon_actuel(sog, h, p)), ("appris", appris),
                           ("appris_plafonne", lambda sog: min(appris(sog), plafond))):
            r = np.array([rayon(float(x)) for x in ev.sog_kn.values])
            nf = np.array([len(centres_fenetres(0.0, 48.0, float(x), s)) for x in r])
            dedans, faisable = err <= r, nf <= fenetres_max
            lignes[nom] = {"contenance": round(float(dedans.mean()), 3), "rayon_moyen_km": round(float(r.mean()) / 1000, 1),
                           "faisable": round(float(faisable.mean()), 3), "trouve": round(float((dedans & faisable).mean()), 3),
                           "fenetres_moyennes": round(float(nf[faisable].mean()), 2) if faisable.any() else None}
        out.append(lignes)
    return out
