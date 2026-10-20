"""Travailleur Sentinel 2 : détection optique des navires dans les fenêtres utiles d'un passage (lot S2).

Exécuté sur une instance GP1-S éphémère, dans l'image publique ghcr.io/allenai/sentinel2-vessel-detection (projet
rslearn d'AI2, licence Apache 2.0 pour le code et les poids), épinglée par empreinte. Le serveur transmet ce fichier
par cloud-init (mars/travailleurs.py), et la tâche par le réseau privé, une seule fois : paramètres, paire de clés S3
de Copernicus dédiée aux travailleurs, adresses R2 présignées des poids (jamais dans le cloud-init, un journal ou la
base ; clés gardées en mémoire, remises à rasterio par une AWSSession ou, sans boto3, par l'environnement le temps
d'une lecture : rasterio refuse les clés AWS en options GDAL).

Pour chaque tuile L1C (110 km) et chaque fenêtre de 24 km calculée par le serveur :
1. masque des nuages : SCL du L2A de la même tuile, lu dans la fenêtre (20 m, quelques centaines de kilooctets) ; une
   fenêtre trop nuageuse est close « nuages » sans lire les bandes ; sans L2A (« masque absent »), rien n'est fermé ;
2. les 13 bandes du L1C lues par requêtes partielles (/vsis3/ signé par GDAL), octets comptés dans le journal de GDAL ;
3. inférence d'AI2 (détection puis attributs : longueur, largeur, vitesse, cap, type) en une seule fois pour toutes
   les fenêtres, sur processeur ;
4. pour chaque navire du cœur de la fenêtre : part nuageuse dans le kilomètre autour de lui, vignette en couleurs réelles
   de 2 km et vignette de contexte de 10 km (PNG en palette), géoréférence pour superposer l'AIS.

Essai local (scripts/verifier_images.sh s2) : python3 sentinel2.py --local tache.json, adresses https:// (miroir public
de Google, mêmes fichiers qu'eodata) et poids déjà présents.
"""
import base64
import contextlib
import hashlib
import io
import json
import math
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # otel_mini, déposé à côté par cloud-init

BANDES = ["B01", "B02", "B03", "B04", "B05", "B06", "B07", "B08", "B8A", "B09", "B10", "B11", "B12"]
RSLP = "/opt/rslearn_projects"
CELLULE_M = 500                 # grille du masque renvoyée au serveur (navire AIS sous un nuage : pas d'alerte)
_S3: dict = {}                  # clés remises avec la tâche, en mémoire seulement


class Arret(Exception):
    """Cause commune à toutes les fenêtres (clés, poids, chaîne d'AI2) : arrêt avec son motif."""


def rss_mo() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


# Calculs purs (couverts par tests/test_sentinel2.py)

def nuages_pct(scl, classes) -> float | None:
    """Part des pixels valides (SCL non nul) masqués par les classes données, en pour cent ; None sans pixel valide."""
    import numpy as np
    valide = scl > 0
    n = int(valide.sum())
    return None if n == 0 else round(100.0 * float(np.isin(scl, list(classes)).sum()) / n, 1)


def grille(lons, lats, masque, valide, bbox: list, n: int, part_max_pct: float) -> list[str]:
    """Masque réduit en n × n cellules lon, lat sur l'emprise de la fenêtre, une chaîne par ligne du sud au nord :
    « 1 » nuageuse (part masquée au delà de part_max_pct), « 0 » dégagée, « . » sans donnée. Quelques kilooctets, lus
    par le serveur (sans pyproj) pour ne jamais conclure à l'absence d'un navire AIS caché par un nuage. Une part, pas
    un seul pixel : le SCL classe en nuage le navire lui même et son sillage (1 à 8 % autour de navires dégagés,
    essai du 20/10/2026)."""
    import numpy as np
    ix = np.clip(((lons - bbox[0]) / (bbox[2] - bbox[0]) * n).astype(int), 0, n - 1)
    iy = np.clip(((lats - bbox[1]) / (bbox[3] - bbox[1]) * n).astype(int), 0, n - 1)
    dedans = (lons >= bbox[0]) & (lons <= bbox[2]) & (lats >= bbox[1]) & (lats <= bbox[3])
    nuage = np.zeros((n, n))
    vu = np.zeros((n, n))
    np.add.at(nuage, (iy[dedans & masque & valide], ix[dedans & masque & valide]), 1)
    np.add.at(vu, (iy[dedans & valide], ix[dedans & valide]), 1)
    return ["".join("." if vu[y, x] == 0 else ("1" if 100 * nuage[y, x] / vu[y, x] > part_max_pct else "0")
                    for x in range(n)) for y in range(n)]


def cellule(lignes: list[str], bbox: list, lon: float, lat: float) -> str | None:
    """État de la cellule de la grille qui contient (lon, lat), None hors de l'emprise (même calcul côté serveur)."""
    if not lignes or not dans(lon, lat, bbox):
        return None
    n = len(lignes)
    x = min(n - 1, int((lon - bbox[0]) / (bbox[2] - bbox[0]) * n))
    y = min(n - 1, int((lat - bbox[1]) / (bbox[3] - bbox[1]) * n))
    return lignes[y][x]


def etirement(rgb, valide=None) -> tuple[float, float]:
    """Bornes d'étirement adaptées à la mer : la mer, sombre, occupe presque toute l'image ; centiles 1 et 99,7 de
    l'ensemble des trois bandes (couleurs réelles gardées), écart minimal pour ne pas exagérer le bruit."""
    import numpy as np
    v = rgb[valide] if valide is not None else rgb.reshape(-1, 3)
    if v.size == 0:
        return 0.0, 0.3
    lo, hi = float(np.percentile(v, 1)), float(np.percentile(v, 99.7))
    return lo, max(hi, lo + 0.08)


def png_rgb(rgb, lo: float, hi: float, gamma: float = 0.7, couleurs: int = 64) -> bytes:
    """Image en couleurs réelles (réflectances B04, B03, B02) étirée, PNG en palette de `couleurs` teintes."""
    import numpy as np
    from PIL import Image
    v = (np.clip((rgb - lo) / max(hi - lo, 1e-6), 0, 1) ** gamma * 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(v, "RGB").convert("P", palette=Image.ADAPTIVE, colors=couleurs).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def geo_affine(px_lonlat: list[tuple[float, float, float, float]], largeur: int, hauteur: int, m_par_px: float) -> dict:
    """Géoréférence d'une vignette (même format que Sentinel 1 et VIIRS) : pixel = a × lon + b × lat + c, par moindres
    carrés sur des points (x, y, lon, lat)."""
    import numpy as np
    A = np.c_[[p[2] for p in px_lonlat], [p[3] for p in px_lonlat], np.ones(len(px_lonlat))]
    cx = np.linalg.lstsq(A, [p[0] for p in px_lonlat], rcond=None)[0]
    cy = np.linalg.lstsq(A, [p[1] for p in px_lonlat], rcond=None)[0]
    return {"x": [round(float(c), 6) for c in cx], "y": [round(float(c), 6) for c in cy], "largeur": largeur,
            "hauteur": hauteur, "m_par_px": round(m_par_px, 2)}


def appliquer(T, x: float, y: float) -> tuple[float, float]:
    """Transformation affine appliquée à un point (sans l'opérateur « * » d'Affine, déprécié)."""
    return T.a * x + T.b * y + T.c, T.d * x + T.e * y + T.f


def dans(lon: float, lat: float, bbox: list) -> bool:
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def coeur(bbox: list, marge_m: float) -> list:
    """Emprise de la fenêtre réduite de sa marge : seuls les navires dedans sont gardés (objets coupés au bord)."""
    lat = (bbox[1] + bbox[3]) / 2
    dlat, dlon = marge_m / 110574.0, marge_m / (111320.0 * math.cos(math.radians(lat)))
    return [bbox[0] + dlon, bbox[1] + dlat, bbox[2] - dlon, bbox[3] - dlat]


OUVERTURE = 16384              # premier bloc lu par GDAL à l'ouverture d'un fichier, absent de son journal


def octets_journal(chemin: str, fichiers: int = 0) -> tuple[int, int]:
    """Octets et requêtes des lectures de GDAL : lignes « Downloading a-b » de son journal (CPL_LOG), plus le premier
    bloc de 16 Ko lu à l'ouverture de chaque fichier, qu'il ne journalise pas ; le journal est vidé après lecture."""
    n, req = OUVERTURE * fichiers, fichiers
    try:
        with open(chemin, errors="replace") as f:
            for ligne in f:
                m = re.search(r"Downloading (\d+)-(\d+)", ligne)
                if m:
                    n += int(m.group(2)) - int(m.group(1)) + 1
                    req += 1
        open(chemin, "w").close()
    except OSError:
        pass
    return n, req


# Réseau privé : tâche, résultats

def fetch_task(task: dict, traceparent: str | None = None) -> dict:
    """Tâche remise par le réseau privé, une seule fois : paramètres, clés S3 (gardées dans _S3), adresses des poids.
    Un refus (403, 409, 503) arrête le travailleur avec son motif."""
    data = json.dumps({"etat": "tache"}).encode()
    for k in range(3):
        try:
            headers = {"Authorization": "Bearer " + task["jeton"], "Content-Type": "application/json"}
            if traceparent:
                headers["traceparent"] = traceparent
            req = urllib.request.Request(task["retour"] + "/etat", data=data, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                body = json.loads(r.read().decode())
            _S3.update((body.pop("identifiants", None) or {}).get("s3") or {})
            return {**body, "retour": task["retour"], "jeton": task["jeton"]}
        except urllib.error.HTTPError as e:
            raise Arret("tâche refusée par le serveur (HTTP " + str(e.code) + ") : " + e.read(300).decode("utf-8", "replace"))
        except Exception as e:
            print("tâche non reçue (" + type(e).__name__ + "), nouvel essai", flush=True)
            time.sleep(10 * (k + 1))
    raise Arret("tâche non reçue du serveur après trois essais (réseau privé)")


def send(task: dict, path: str, body: dict, traceparent: str | None = None):
    data = json.dumps(body).encode()
    headers = {"Authorization": f"Bearer {task['jeton']}", "Content-Type": "application/json"}
    if traceparent:
        headers["traceparent"] = traceparent
    for k in range(6):
        try:
            req = urllib.request.Request(f"{task['retour']}{path}", data=data, method="POST", headers=headers)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if e.code < 500:
                return e.code
        except Exception as e:
            print(f"envoi impossible ({e}), nouvel essai", flush=True)
        time.sleep(10 * (k + 1))
    return None


# Poids et lecture des images

def refus_r2(code: int, corps: bytes) -> str:
    """Refus de R2 lisible : code HTTP, puis code et message de sa réponse XML (<Code>, <Message>), comme le contrôle
    d'accès du script de démarrage (travailleurs/demarrage.sh). Jamais l'adresse, qui porte la signature."""
    texte = corps.decode("utf-8", "replace")
    champs = [m.group(1) for m in (re.search(f"<{b}>([^<]*)</{b}>", texte) for b in ("Code", "Message")) if m]
    return f"R2 refuse la lecture (HTTP {code}" + (f" {' : '.join(champs)}" if champs else "") + ")"


def poids(task: dict, dossier: str, tr) -> dict:
    """Poids d'AI2 dans `dossier` (RSLP_PREFIX) : téléchargés depuis R2 (adresses présignées) et vérifiés par leur
    empreinte, ou déjà présents (essai local). Une empreinte fausse arrête tout."""
    out = {"octets": 0, "s": 0.0}
    t0 = time.time()
    for p in task.get("poids") or []:
        dest = os.path.join(dossier, p["chemin"])
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if p.get("fichier"):                         # essai local : fichier déjà présent, monté dans le conteneur
            if not os.path.exists(dest):
                os.symlink(p["fichier"], dest)
            continue
        with tr.span("GET R2 poids", **{"peer.service": "R2", "sentinel2.poids": p["chemin"]}) as sp:
            h = hashlib.sha256()
            n = 0
            req = urllib.request.Request(p["url"], headers={"User-Agent": "mars-c2-travailleur"})
            try:
                r = urllib.request.urlopen(req, timeout=120)
            except urllib.error.HTTPError as e:
                motif = refus_r2(e.code, e.read(2000))
                sp.erreur(motif)
                raise Arret(f"poids {p['chemin']} : {motif}")
            with r, open(dest, "wb") as f:
                while True:
                    b = r.read(8 << 20)
                    if not b:
                        break
                    h.update(b)
                    f.write(b)
                    n += len(b)
            sp.set(**{"sentinel2.octets": n})
            if h.hexdigest() != p["sha256"]:
                sp.erreur("empreinte fausse")
                raise Arret(f"poids {p['chemin']} : empreinte fausse ({n} octets reçus)")
            out["octets"] += n
    out["s"] = round(time.time() - t0, 1)
    return out


def chemin_gdal(href: str) -> str:
    """s3://eodata/… devient /vsis3/eodata/… (paire remise par env_gdal) ; https:// devient /vsicurl/ (essai local)."""
    if href.startswith("s3://"):
        return "/vsis3/" + href[len("s3://"):]
    if href.startswith("http"):
        return "/vsicurl/" + href
    return href


CLES_AWS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def session_aws():
    """Session rasterio portant la paire S3 de Copernicus, si boto3 est dans l'image ; None sinon."""
    try:
        import boto3  # noqa: F401
        from rasterio.session import AWSSession
    except ImportError:
        return None
    return AWSSession(aws_access_key_id=_S3["access_key"], aws_secret_access_key=_S3["secret_key"],
                      region_name=_S3.get("region", "default"),
                      endpoint_url=_S3.get("endpoint", "eodata.dataspace.copernicus.eu"))


@contextlib.contextmanager
def env_gdal(journal: str):
    """Lecture S3 par rasterio : lecture partielle, journal de GDAL pour compter les octets, paire S3 de Copernicus.

    rasterio refuse les clés AWS en options GDAL (EnvError « GDAL's AWS config options can not be directly set »,
    essai réel du 20/10/2026) : elles passent par une AWSSession si boto3 est présent, sinon par les variables
    d'environnement que GDAL lit, posées pour la seule durée de la lecture puis retirées. Les options non sensibles
    restent dans rasterio.Env. Aucune clé dans un journal ni dans un span."""
    import rasterio
    from rasterio.session import DummySession
    opts = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".jp2,.xml",
            "GDAL_HTTP_MAX_RETRY": "3", "GDAL_HTTP_RETRY_DELAY": "2", "CPL_DEBUG": "ON", "CPL_LOG": journal}
    session, avant = DummySession(), None
    if _S3.get("access_key"):
        opts.update({"AWS_S3_ENDPOINT": _S3.get("endpoint", "eodata.dataspace.copernicus.eu"),
                     "AWS_VIRTUAL_HOSTING": "FALSE", "AWS_HTTPS": "NO" if _S3.get("http") else "YES",
                     "AWS_REGION": _S3.get("region", "default")})
        session = session_aws()
        if session is None:
            avant = {k: os.environ.get(k) for k in CLES_AWS}
            os.environ.update({"AWS_ACCESS_KEY_ID": _S3["access_key"], "AWS_SECRET_ACCESS_KEY": _S3["secret_key"]})
            session = DummySession()
    try:
        with rasterio.Env(session=session, **opts):
            yield
    finally:
        if avant is not None:
            for k, v in avant.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def fenetre_utm(ds, bbox: list):
    """Fenêtre de l'image (UTM de la tuile) qui couvre l'emprise lon, lat ; arrondie aux pixels."""
    from pyproj import Transformer
    from rasterio.windows import from_bounds
    t = Transformer.from_crs(4326, ds.crs, always_xy=True)
    xs, ys = t.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    return from_bounds(min(xs), min(ys), max(xs), max(ys), ds.transform).round_offsets().round_lengths()


def lire_scl(href: str, bbox: list):
    """Masque de scène du L2A dans la fenêtre (20 m) : tableau, transformation et système de la fenêtre."""
    import rasterio
    with rasterio.open(chemin_gdal(href)) as ds:
        w = fenetre_utm(ds, bbox)
        return ds.read(1, window=w, boundless=True, fill_value=0), ds.window_transform(w), ds.crs


def lire_bandes(scene: dict, bbox: list, dossier: str) -> dict:
    """Les 13 bandes du L1C dans la fenêtre, en GeoTIFF local (résolution native de chaque bande) ; retourne aussi la
    couleur réelle (B04, B03, B02 en réflectance, 10 m) pour les vignettes."""
    import numpy as np
    import rasterio
    rgb, meta = {}, {}
    for b in BANDES:
        with rasterio.open(chemin_gdal(scene["bandes"][b])) as ds:
            w = fenetre_utm(ds, bbox)
            d = ds.read(1, window=w, boundless=True, fill_value=0)
            prof = {"driver": "GTiff", "dtype": d.dtype, "count": 1, "width": d.shape[1], "height": d.shape[0],
                    "crs": ds.crs, "transform": ds.window_transform(w)}
            with rasterio.open(os.path.join(dossier, f"{b}.tif"), "w", **prof) as o:
                o.write(d, 1)
            if b in ("B04", "B03", "B02"):
                # Réflectance : traitements 04.00 et suivants décalés de 1 000 (harmonisation, comme AI2)
                rgb[b] = np.clip((d.astype("float32") - 1000.0) / 10000.0, 0, None)
                rgb[b][d == 0] = np.nan
                meta = {"transform": ds.window_transform(w), "crs": ds.crs, "forme": d.shape}
    return {"rgb": np.dstack([rgb["B04"], rgb["B03"], rgb["B02"]]), **meta}


def vignette(img: dict, lon: float, lat: float, km: float, pas: int = 1) -> tuple[bytes, dict] | None:
    """Vignette en couleurs réelles de `km` de côté centrée sur (lon, lat), un pixel sur `pas` (contexte de 10 km à
    20 m) ; étirement de la vignette, adapté à la mer. None si le point est hors de la fenêtre lue."""
    import numpy as np
    from pyproj import Transformer
    T, rgb = img["transform"], img["rgb"]
    vers = Transformer.from_crs(4326, img["crs"], always_xy=True)
    retour = Transformer.from_crs(img["crs"], 4326, always_xy=True)
    x, y = vers.transform(lon, lat)
    col, row = appliquer(~T, x, y)
    demi = int(round(km * 1000 / abs(T.a) / 2))
    r0, c0 = int(round(row)) - demi, int(round(col)) - demi
    H, W = rgb.shape[:2]
    if r0 >= H or c0 >= W or r0 + 2 * demi <= 0 or c0 + 2 * demi <= 0:
        return None
    a = np.full((2 * demi, 2 * demi, 3), np.nan, dtype="float32")
    ys, xs = slice(max(r0, 0), min(r0 + 2 * demi, H)), slice(max(c0, 0), min(c0 + 2 * demi, W))
    a[ys.start - r0:ys.stop - r0, xs.start - c0:xs.stop - c0] = rgb[ys, xs]
    a = a[::pas, ::pas]
    valide = ~np.isnan(a).any(axis=2)
    lo, hi = etirement(np.nan_to_num(a), valide)
    png = png_rgb(np.nan_to_num(a, nan=lo), lo, hi)
    h, w = a.shape[:2]
    pts = []
    for i in (0, w - 1):
        for j in (0, h - 1):
            gx, gy = appliquer(T, c0 + i * pas + 0.5, r0 + j * pas + 0.5)
            lo_, la_ = retour.transform(gx, gy)
            pts.append((i + 0.5, j + 0.5, lo_, la_))
    return png, geo_affine(pts, w, h, abs(T.a) * pas)


def grille_fenetre(masque: dict, bbox: list) -> list[str]:
    """Grille du masque d'une fenêtre, cellules d'environ CELLULE_M mètres."""
    import numpy as np
    from pyproj import Transformer
    h, w = masque["scl"].shape
    T = masque["transform"]
    cols, rows = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    xs, ys = T.c + cols * T.a + rows * T.b, T.f + cols * T.d + rows * T.e
    lons, lats = Transformer.from_crs(masque["crs"], 4326, always_xy=True).transform(xs, ys)
    n = max(4, int(round(_hav_m(bbox[0], bbox[1], bbox[2], bbox[1]) / CELLULE_M)))
    return grille(np.asarray(lons), np.asarray(lats), masque["scl"], masque["valide"], bbox, n, masque["part_max"])


def _hav_m(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


# Analyse

def analyser(task: dict, tr) -> dict:
    import numpy as np
    from pyproj import Transformer
    t0 = time.time()
    travail = tempfile.mkdtemp(prefix="s2_", dir=task.get("travail"))
    rslp = task.get("rslp_prefix") or os.path.join(travail, "rslp")
    journal = os.path.join(travail, "gdal.log")
    with tr.span("sentinel2.poids") as sp:
        p = poids(task, rslp, tr)
        sp.set(**{"sentinel2.octets": p["octets"], "sentinel2.duree_s": p["s"]})
    classes = task.get("masque_classes") or [3, 8, 9, 10]
    seuil = float(task.get("nuages_fenetre_max_pct", 20))
    sortie, travaux = [], []
    for scene in task["scenes"]:
        r = {k: scene.get(k) for k in ("analyse", "id", "tuile", "debut", "fin", "satellite")}
        r.update({"masque": "present" if scene.get("scl") else "absent", "fenetres": [], "detections": [],
                  "octets_lus": 0, "octets_scl": 0})
        sortie.append(r)
        for j, f in enumerate(scene["fenetres"]):
            k = f.get("id", j)
            rf = {"index": k, "bbox": f["bbox"]}
            r["fenetres"].append(rf)
            with tr.span("sentinel2.fenetre", **{"sentinel2.tuile": scene.get("tuile"), "sentinel2.fenetre": k}) as sf:
                try:
                    masque = None
                    with env_gdal(journal):
                        if scene.get("scl"):
                            with tr.span("GET S3 eodata SCL fenêtre", **{"peer.service": "Copernicus"}) as sd:
                                scl, T_scl, crs_scl = lire_scl(scene["scl"], f["bbox"])
                                n, req = octets_journal(journal, 1)
                                sd.set(**{"sentinel2.octets": n, "sentinel2.requetes": req})
                                r["octets_scl"] += n
                                rf["nuages_pct"] = nuages_pct(scl, classes)
                                masque = {"scl": np.isin(scl, classes), "transform": T_scl, "crs": crs_scl,
                                          "valide": scl > 0, "part_max": float(task.get("masque_part_max_pct", 25))}
                            sf.set(**{"sentinel2.nuages_pct": rf["nuages_pct"]})
                            if rf["nuages_pct"] is None or rf["nuages_pct"] > seuil:
                                # Garde des nuages après lecture : pas de bandes, pas d'inférence
                                rf["etat"] = "nuages"
                                sf.set(**{"sentinel2.etat": "nuages"})
                                continue
                        dossier = os.path.join(travail, f"{r['tuile']}_{k}")
                        os.makedirs(dossier, exist_ok=True)
                        t = time.time()
                        with tr.span("GET S3 eodata 13 bandes fenêtre", **{"peer.service": "Copernicus"}) as sd:
                            img = lire_bandes(scene, f["bbox"], dossier)
                            n, req = octets_journal(journal, len(BANDES))
                            sd.set(**{"sentinel2.octets": n, "sentinel2.requetes": req})
                    rf.update({"octets": n, "requetes": req, "lecture_s": round(time.time() - t, 1), "etat": "lue"})
                    r["octets_lus"] += n
                    if masque is not None:
                        rf["grille"] = grille_fenetre(masque, f["bbox"])
                    travaux.append((r, rf, f, dossier, img, masque))
                except Arret:
                    raise
                except Exception as e:
                    rf.update({"etat": "erreur", "erreur": f"{type(e).__name__}: {e}"[:300]})
                    sf.exception(e)
    # Inférence en une seule fois : modèle chargé une fois pour toutes les fenêtres lues
    if travaux:
        taches = [{"image_files": [{"bands": [b], "fname": os.path.join(d, f"{b}.tif")} for b in BANDES],
                   "geojson_path": os.path.join(d, "detections.geojson")} for _r, _rf, _f, d, _i, _m in travaux]
        with tr.span("sentinel2.inference", **{"sentinel2.fenetres": len(taches)}) as si:
            t = time.time()
            env = {**os.environ, "RSLP_PREFIX": rslp}
            run = subprocess.run([sys.executable, "-m", "rslp.main", "sentinel2_vessels", "predict", "--tasks",
                                  json.dumps(taches), "--scratch_path", os.path.join(travail, "scratch")],
                                 cwd=RSLP, env=env, capture_output=True, text=True)
            duree = round(time.time() - t, 1)
            si.set(**{"sentinel2.duree_s": duree, "sentinel2.code": run.returncode})
            if run.returncode != 0:
                print((run.stdout + run.stderr)[-4000:], flush=True)
                si.erreur(f"inférence en échec (code {run.returncode})")
                raise Arret(f"inférence d'AI2 en échec (code {run.returncode}) : " + (run.stderr or run.stdout)[-300:])
        for r, rf, f, d, img, masque in travaux:
            rf["analyse_s"] = round(duree / len(travaux), 1)
            with tr.span("sentinel2.detections", **{"sentinel2.fenetre": rf["index"]}) as sd:
                dets = json.load(open(os.path.join(d, "detections.geojson"))).get("features", [])
                cible = coeur(f["bbox"], f.get("marge_m", 2000))
                gardees = ecartees = 0
                for feat in dets:
                    lon, lat = feat["geometry"]["coordinates"][:2]
                    if not dans(lon, lat, cible) or not any(dans(lon, lat, b) for b in task.get("regions") or [cible]):
                        ecartees += 1
                        continue
                    pr, at = feat["properties"], feat["properties"].get("attributes") or {}
                    if (pr.get("score") or 0) < float(task.get("confiance", 0.8)):
                        continue
                    det = {"lon": round(lon, 6), "lat": round(lat, 6), "score": round(float(pr["score"]), 3),
                           "longueur_m": at.get("length"), "largeur_m": at.get("width"), "vitesse_kn": at.get("speed"),
                           "cap_deg": at.get("heading"), "type": at.get("vessel_type")}
                    if masque is not None:
                        # Part masquée dans le carré de 2 × masque_voisinage_m autour du navire (le serveur l'écarte,
                        # « nuages », au delà de masque_part_max_pct : le navire et son sillage en font quelques %)
                        x, y = Transformer.from_crs(4326, masque["crs"], always_xy=True).transform(lon, lat)
                        c, l_ = appliquer(~masque["transform"], x, y)
                        rp = max(1, int(round(float(task.get("masque_voisinage_m", 500)) / abs(masque["transform"].a))))
                        l0, c0 = int(l_), int(c)
                        zone = masque["scl"][max(l0 - rp, 0):l0 + rp + 1, max(c0 - rp, 0):c0 + rp + 1]
                        det["nuages_pct"] = round(100.0 * float(zone.mean()), 1) if zone.size else None
                    with tr.span("sentinel2.vignettes") as sv:
                        v = vignette(img, lon, lat, float(task.get("vignette_km", 2)))
                        ctx = vignette(img, lon, lat, float(task.get("contexte_km", 10)), pas=2)
                        if v:
                            det["vignette"], det["vignette_geo"] = base64.b64encode(v[0]).decode(), v[1]
                        if ctx:
                            det["contexte"], det["contexte_geo"] = base64.b64encode(ctx[0]).decode(), ctx[1]
                        sv.set(**{"sentinel2.vignette_octets": len(v[0]) if v else 0,
                                  "sentinel2.contexte_octets": len(ctx[0]) if ctx else 0})
                    r["detections"].append(det)
                    gardees += 1
                rf.update({"detections": gardees, "ecartees_marge": ecartees})
                sd.set(**{"sentinel2.detections": gardees, "sentinel2.ecartees": ecartees})
            shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(travail, ignore_errors=True)
    for r in sortie:
        fs = r["fenetres"]
        if fs and all(x.get("etat") == "erreur" for x in fs):
            r["erreur"] = "aucune fenêtre analysée : " + fs[0].get("erreur", "")
    return {"scenes": sortie, "mesures": {"duree_s": round(time.time() - t0, 1), "rss_max_mo": rss_mo(),
                                          "poids_octets": p["octets"], "poids_s": p["s"]}}


def run(task: dict, tr=None) -> dict:
    import otel_mini
    tr = tr or otel_mini.Traceur(None, None, None)
    try:
        return analyser(task, tr)
    except Arret as e:
        return {"scenes": [], "erreur": str(e)[:500], "mesures": {"rss_max_mo": rss_mo()}}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--local":
        task = json.load(open(sys.argv[2]))
        print(json.dumps(run(task)))
        return
    import otel_mini
    task = json.load(open(os.environ.get("MARS_TACHE", "/mars/tache.json")))
    tr = otel_mini.depuis_tache(task, **{"travailleur.tache": "sentinel2"})
    with tr.span("travailleur.sentinel2") as racine:
        with tr.span("travailleur.signal_demarre"):
            send(task, "/etat", {"etat": "demarre"}, tr.traceparent())
        try:
            if task.get("tache_par_le_reseau"):
                with tr.span("travailleur.remise_tache"):
                    task = fetch_task(task, tr.traceparent())
            racine.set(**{"sentinel2.tuiles": len(task.get("scenes", []))})
            result = run(task, tr=tr)
        except Exception as e:
            traceback.print_exc()
            result = {"scenes": [], "erreur": f"{type(e).__name__}: {e}"[:1000], "mesures": {"rss_max_mo": rss_mo()}}
        if result.get("erreur"):
            racine.erreur(result["erreur"])
        with tr.span("travailleur.renvoi_resultats") as sr:
            sr.set(**{"http.response.status_code": send(task, "/resultats", result, tr.traceparent())})
    tr.envoyer()


if __name__ == "__main__":
    main()
