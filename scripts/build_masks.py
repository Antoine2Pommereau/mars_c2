"""Construit les masques géographiques : terres émergées et zones de stationnement observées dans l'AIS.

Trait de côte par défaut : GSHHG en pleine résolution (contient les petites îles, absentes de Natural Earth
au 1:10 000 000, ce qui provoquait de faux rendez vous dans les ports d'îles comme Sejerø).

Exemple :
    python scripts/build_masks.py --region 4 53 17 60
"""
import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
import shapefile
import shapely
from shapely.geometry import Polygon, shape as to_shape

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


def gshhg_shapefile(folder: Path) -> Path:
    base = folder / "GSHHS_f_L1"
    if not base.with_suffix(".shp").exists():
        archive = None
        for url in GSHHG_URLS:
            try:
                archive = download(url, folder / "gshhg-shp.zip")
                break
            except requests.HTTPError as e:
                print(f"  indisponible ({e.response.status_code}), essai suivant")
        if archive is None:
            raise SystemExit("GSHHG introuvable sur les miroirs connus : relancer avec --source naturalearth")
        with zipfile.ZipFile(archive) as z:
            for ext in (".shp", ".shx", ".dbf", ".prj"):
                base.with_suffix(ext).write_bytes(z.read(GSHHG_MEMBER + ext))
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


def load_shapes(cur, path: Path, source: str, region) -> int:
    x0, y0, x1, y1 = region
    n = 0
    for sh in shapefile.Reader(str(path)).iterShapes():
        bx0, by0, bx1, by1 = sh.bbox
        if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1:
            continue
        geom = Polygon(sh.points) if len(sh.parts) == 1 else to_shape(sh.__geo_interface__)
        geom = shapely.clip_by_rect(shapely.make_valid(geom), x0, y0, x1, y1)
        if geom.is_empty:
            continue
        cur.execute(INSERT, (source, geom.wkb_hex))
        n += cur.rowcount
    return n


def main():
    ap = argparse.ArgumentParser(description="Masques géographiques de MARS C2")
    ap.add_argument("--region", nargs=4, type=float, default=[4, 53, 17, 60],
                    metavar=("LON_MIN", "LAT_MIN", "LON_MAX", "LAT_MAX"))
    ap.add_argument("--source", choices=["gshhg", "naturalearth"], default="gshhg")
    ap.add_argument("--api", default="http://localhost:8000/api")   # API en direct : pas de délai du relais nginx
    args = ap.parse_args()
    load_env()
    folder = ROOT / "data" / "masks"
    folder.mkdir(parents=True, exist_ok=True)

    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM land")
        if args.source == "gshhg":
            n = load_shapes(cur, gshhg_shapefile(folder), "gshhg_f_l1", args.region)
            print(f"GSHHG pleine résolution : {n} polygones dans la région")
        else:
            for path in natural_earth_shapefiles(folder):
                n = load_shapes(cur, path, path.stem, args.region)
                print(f"{path.stem} : {n} polygones dans la région")
        conn.commit()

    r = requests.post(f"{args.api}/masks/stationary", timeout=1800)
    r.raise_for_status()
    print("Zones de stationnement :", r.json())


if __name__ == "__main__":
    main()
