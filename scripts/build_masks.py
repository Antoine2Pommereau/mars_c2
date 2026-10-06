"""Construit les masques géographiques : terres émergées et zones de stationnement observées dans l'AIS.

Trait de côte par défaut : GSHHG en pleine résolution (contient les petites îles, absentes de Natural Earth
au 1:10 000 000, ce qui provoquait de faux rendez vous dans les ports d'îles comme Sejerø).

Par défaut, l'emprise couvre les quatre zones collectées en France (mars/ais/live.py), avec une marge de 0,5°.

Exemples :
    python scripts/build_masks.py                    # trait de côte et masques déduits de l'AIS, France
    # Sur le serveur, dans le conteneur taches : sans cache (archive GSHHG effacée après usage), 7 derniers jours
    docker compose exec taches python scripts/build_masks.py --sans-cache --jours 7
"""
import argparse
import os
import shutil
import struct
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import requests
import shapefile
import shapely
from shapely.geometry import Polygon, shape as to_shape

from mars.ais.live import zones_extent
from mars.config import ROOT, load_env
from mars.db import connect

# Miroirs essayés dans l'ordre : l'adresse « latest » de la NOAA a changé, les versions archivées restent stables
GSHHG_URLS = [
    "https://www.soest.hawaii.edu/pwessel/gshhg/gshhg-shp-2.3.7.zip",
    "https://www.ngdc.noaa.gov/mgg/shorelines/data/gshhg/oldversions/version2.3.6/gshhg-shp-2.3.6.zip",
]
GSHHG_MEMBER = "GSHHS_shp/f/GSHHS_f_L1"
NATURAL_EARTH = {
    "ne_10m_land": "https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip",
    "ne_10m_minor_islands": "https://naciscdn.org/naturalearth/10m/physical/ne_10m_minor_islands.zip",
}

INSERT = (
    "INSERT INTO land (source, geom) "
    "SELECT %s, ST_Subdivide(g, 256)::geography FROM ("
    "  SELECT ST_CollectionExtract(ST_MakeValid(ST_SetSRID(ST_GeomFromWKB(decode(%s, 'hex')), 4326)), 3) AS g) x "
    "WHERE NOT ST_IsEmpty(g)"
)


REGION_MARGIN = 0.5      # degrés autour des zones collectées

def download(url: str, target: Path) -> Path:
    if not target.exists():
        print(f"Téléchargement de {url} ...")
        partial = target.with_suffix(".part")
        with requests.get(url, stream=True, timeout=300, headers={"User-Agent": "mars_c2"}) as r:
            r.raise_for_status()
            with open(partial, "wb") as f:
                for block in r.iter_content(chunk_size=8 * 1024 * 1024):
                    f.write(block)
        partial.rename(target)   # un téléchargement interrompu ne laisse pas d'archive tronquée
    return target


def gshhg_archive(folder: Path) -> Path:
    for url in GSHHG_URLS:
        try:
            return download(url, folder / "gshhg-shp.zip")
        except requests.HTTPError as e:
            print(f"  indisponible ({e.response.status_code}), essai suivant")
    raise SystemExit("GSHHG introuvable sur les miroirs connus : relancer avec --source naturalearth")


def gshhg_stream_from_zip(folder: Path):
    """Lecture directe dans l'archive, sans extraction : sur le disque, seule l'archive (149 Mo) est présente, au
    lieu de l'archive et des 201 Mo extraits."""
    return zipfile.ZipFile(gshhg_archive(folder)).open(GSHHG_MEMBER + ".shp")


def iter_polygons(stream, region):
    """Polygones d'un fichier .shp lu séquentiellement, sans pyshp : les coordonnées passent par un tableau numpy
    (16 octets par point) au lieu d'une liste de tuples Python (environ 120 octets par point). Le polygone de
    l'Eurasie compte plus d'un million de points : c'est ce qui portait la mémoire à plus de 500 Mo.
    Les enregistrements hors de la région sont sautés sans être décodés."""
    x0, y0, x1, y1 = region
    stream.read(100)                                         # en tête du fichier
    while True:
        head = stream.read(8)
        if len(head) < 8:
            return
        length = struct.unpack(">i", head[4:])[0] * 2       # longueur en mots de 16 bits
        content = stream.read(length)
        if struct.unpack("<i", content[:4])[0] != 5:         # 5 : polygone
            continue
        bx0, by0, bx1, by1 = struct.unpack("<4d", content[4:36])
        if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1:
            continue
        n_parts, n_points = struct.unpack("<2i", content[36:44])
        parts = [int(k) for k in np.frombuffer(content, "<i4", n_parts, 44)] + [n_points]
        coords = np.frombuffer(content, "<f8", 2 * n_points, 44 + 4 * n_parts).reshape(-1, 2)
        rings = [coords[parts[k]:parts[k + 1]] for k in range(n_parts)]
        yield Polygon(rings[0], rings[1:])


def gshhg_shapefile(folder: Path) -> Path:
    base = folder / "GSHHS_f_L1"
    if not base.with_suffix(".shp").exists():
        archive = gshhg_archive(folder)
        with zipfile.ZipFile(archive) as z:
            for ext in (".shp", ".shx", ".dbf", ".prj"):
                with z.open(GSHHG_MEMBER + ext) as src, open(base.with_suffix(ext), "wb") as dst:
                    shutil.copyfileobj(src, dst, 8 * 1024 * 1024)      # sans charger le fichier en mémoire
    return base.with_suffix(".shp")


def natural_earth_shapefiles(folder: Path) -> list[Path]:
    paths = []
    for name, url in NATURAL_EARTH.items():
        shp = folder / f"{name}.shp"
        if not shp.exists():
            with zipfile.ZipFile(download(url, folder / f"{name}.zip")) as z:
                z.extractall(folder)
        paths.append(shp)
    return paths


def _pyshp_polygons(path: Path, region):
    x0, y0, x1, y1 = region
    for sh in shapefile.Reader(str(path)).iterShapes():
        bx0, by0, bx1, by1 = sh.bbox
        if not (bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1):
            yield Polygon(sh.points) if len(sh.parts) == 1 else to_shape(sh.__geo_interface__)


def load_shapes(cur, path, source: str, region) -> int:
    """`path` : chemin d'un shapefile, ou flux .shp ouvert (lecture économe, voir iter_polygons)."""
    x0, y0, x1, y1 = region
    n = 0
    shapes = _pyshp_polygons(Path(path), region) if isinstance(path, (str, Path)) else iter_polygons(path, region)
    for geom in shapes:
        # Découpe d'abord, réparation ensuite : réparer tout le polygone de l'Eurasie (plus d'un million de points)
        # coûte des centaines de Mo, la découpe sur la région n'en garde qu'une petite partie
        geom = shapely.make_valid(shapely.clip_by_rect(geom, x0, y0, x1, y1))
        if geom.is_empty:
            continue
        cur.execute(INSERT, (source, geom.wkb_hex))
        n += cur.rowcount
    return n


def main():
    ap = argparse.ArgumentParser(description="Masques géographiques de MARS C2")
    lon0, lat0, lon1, lat1 = zones_extent()
    ap.add_argument("--region", nargs=4, type=float,
                    default=[lon0 - REGION_MARGIN, lat0 - REGION_MARGIN, lon1 + REGION_MARGIN, lat1 + REGION_MARGIN],
                    metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    ap.add_argument("--source", choices=["gshhg", "naturalearth"], default="gshhg")
    ap.add_argument("--skip-land", action="store_true", help="Ne recalculer que les masques déduits de l'AIS")
    # API en direct, sans le relais nginx (délais) ; dans le conteneur taches : http://backend:8000/api
    ap.add_argument("--api", default=os.environ.get("MARS_API", "http://localhost:8000/api"))
    ap.add_argument("--sans-cache", action="store_true",
                    help="Téléchargements dans un dossier temporaire effacé à la fin (serveur à disque juste)")
    ap.add_argument("--jours", type=int, help="Masques déduits de l'AIS sur les N derniers jours seulement")
    args = ap.parse_args()
    load_env()
    tmp = tempfile.TemporaryDirectory(prefix="masques_") if args.sans_cache else None
    folder = Path(tmp.name) if tmp else ROOT / "data" / "masks"
    folder.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    with connect() as conn, conn.cursor() as cur:
        if args.skip_land:
            print("Trait de côte conservé")
        else:
            cur.execute("DELETE FROM land")
        if args.skip_land:
            pass
        elif args.source == "gshhg":
            src = gshhg_stream_from_zip(folder) if args.sans_cache else gshhg_shapefile(folder)
            n = load_shapes(cur, src, "gshhg_f_l1", args.region)
            print(f"GSHHG pleine résolution : {n} polygones dans la région")
        else:
            for path in natural_earth_shapefiles(folder):
                n = load_shapes(cur, path, path.stem, args.region)
                print(f"{path.stem} : {n} polygones dans la région")
        conn.commit()
    if tmp:
        tmp.cleanup()                 # archive et fichiers extraits effacés avant le calcul des masques
    print(f"Trait de côte en {time.time() - t0:.0f} s")

    params = {"jours": args.jours} if args.jours else {}
    for kind, label in [("stationary", "Zones de stationnement"), ("reception", "Zone de réception fiable")]:
        t = time.time()
        r = requests.post(f"{args.api}/masks/{kind}", params=params, timeout=1800)
        r.raise_for_status()
        print(f"{label} en {time.time() - t:.0f} s :", r.json())


if __name__ == "__main__":
    main()
