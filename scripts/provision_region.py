"""Provisionne la donnée statique d'une région (voir docs/spec_regions.md).

Tout est téléchargé et stocké en local, donc hors ligne. Fournisseurs de la tranche verticale :
  infrastructure  câbles et pipelines EMODnet Human Activities (WFS), insérés en base
  bathymetry      modèle numérique de terrain EMODnet Bathymetry (WCS), GeoTIFF découpé en local

Exemples :
    python scripts/provision_region.py                      # région active, tous les fournisseurs
    python scripts/provision_region.py --region "Skagerrak et Kattegat"
    python scripts/provision_region.py --only infrastructure
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from mars.config import load_env
from mars.db import connect

ROOT = Path(__file__).resolve().parents[1]

HA_WFS = "https://ows.emodnet-humanactivities.eu/geoserver/emodnet/wfs"
BATHY_WCS = "https://ows.emodnet-bathymetry.eu/wcs"
HA_LICENSE = "EMODnet Human Activities, CC BY 4.0"
BATHY_LICENSE = "EMODnet Bathymetry, CC BY 4.0"

# Couches câbles et pipelines à interroger. Chacune ne couvre que son emprise nationale,
# donc la plupart renvoient zéro hors de leur zone : on garde ce qui tombe dans la région.
CABLE_LAYERS = ["pcablesnve", "pcablesbshcontis", "pcablesrijks", "pcablesshom",
                "bshcontiscables", "rijkscables", "shomcables", "sigcables",
                "cicacables", "ukfibrecables", "maltacables"]
PIPELINE_LAYERS = ["pipelines"]

NAME_KEYS = ["name", "NAME", "cable_name", "label", "LABEL", "title"]
OPERATOR_KEYS = ["operator", "owner", "company", "OPERATOR", "OWNER"]


def pick(props, keys):
    for k in keys:
        v = props.get(k)
        if v not in (None, "", "NaN"):
            return str(v)
    return None


def leaves_minmax(coords, acc):
    """Étendue (xmin, xmax, ymin, ymax) des points d'une géométrie GeoJSON imbriquée."""
    if coords and isinstance(coords[0], (int, float)):
        acc[0] = min(acc[0], coords[0]); acc[1] = max(acc[1], coords[0])
        acc[2] = min(acc[2], coords[1]); acc[3] = max(acc[3], coords[1])
    else:
        for c in coords:
            leaves_minmax(c, acc)


def swap_xy(coords):
    if coords and isinstance(coords[0], (int, float)):
        return [coords[1], coords[0]] + list(coords[2:])
    return [swap_xy(c) for c in coords]


def ensure_lonlat(geoms, bbox):
    """GeoServer rend normalement du GeoJSON en lon,lat. Garde fou : si les abscisses tombent
    dans la plage des latitudes de la région et les ordonnées dans celle des longitudes, on inverse."""
    lon_min, lat_min, lon_max, lat_max = bbox
    acc = [1e9, -1e9, 1e9, -1e9]
    for g in geoms:
        leaves_minmax(g["coordinates"], acc)
    x_mid, y_mid = (acc[0] + acc[1]) / 2, (acc[2] + acc[3]) / 2
    looks_swapped = (lat_min - 1 <= x_mid <= lat_max + 1) and (lon_min - 1 <= y_mid <= lon_max + 1) \
        and not (lon_min - 1 <= x_mid <= lon_max + 1)
    if looks_swapped:
        for g in geoms:
            g["coordinates"] = swap_xy(g["coordinates"])
    return geoms


def set_layer(cur, region_id, layer, **cols):
    sets = ", ".join(f"{k} = %s" for k in cols)
    cur.execute(f"UPDATE region_layers SET {sets} WHERE region_id = %s AND layer = %s",
                (*cols.values(), region_id, layer))


def provision_infrastructure(cur, region_id, bbox):
    lon_min, lat_min, lon_max, lat_max = bbox
    # bbox WFS en urn CRS 4326 : ordre lat,lon
    bbox_param = f"{lat_min},{lon_min},{lat_max},{lon_max},urn:ogc:def:crs:EPSG::4326"
    set_layer(cur, region_id, "infrastructure", status="en_cours")
    cur.execute("DELETE FROM infrastructure WHERE region_id = %s", (region_id,))
    total = 0
    for layer in CABLE_LAYERS + PIPELINE_LAYERS:
        kind = "pipeline" if layer in PIPELINE_LAYERS else "cable"
        params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                  "typeNames": f"emodnet:{layer}", "outputFormat": "application/json",
                  "srsName": "EPSG:4326", "bbox": bbox_param}
        r = requests.get(HA_WFS, params=params, timeout=120)
        r.raise_for_status()
        feats = r.json().get("features", [])
        if not feats:
            continue
        geoms = [f["geometry"] for f in feats if f.get("geometry")]
        ensure_lonlat(geoms, bbox)
        for f in feats:
            g = f.get("geometry")
            if not g:
                continue
            props = f.get("properties", {})
            cur.execute(
                "INSERT INTO infrastructure (region_id, kind, name, operator, source, geom) "
                "VALUES (%s, %s, %s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)::geography)",
                (region_id, kind, pick(props, NAME_KEYS), pick(props, OPERATOR_KEYS),
                 f"EMODnet:{layer}", json.dumps(g)))
            total += 1
        print(f"  {layer:22s} {len(feats):>4} objets")
    # Vérification : les objets doivent bien tomber dans la région
    cur.execute("SELECT count(*) FROM infrastructure i JOIN regions r ON r.id = i.region_id "
                "WHERE i.region_id = %s AND ST_Intersects(i.geom, r.geom)", (region_id,))
    inside = cur.fetchone()[0]
    print(f"  total {total} objets, dont {inside} dans l'emprise")
    set_layer(cur, region_id, "infrastructure", status="prete",
              source="EMODnet Human Activities (WFS)", source_url=HA_WFS, license=HA_LICENSE,
              feature_count=total)
    cur.execute("UPDATE region_layers SET fetched_at = now() WHERE region_id = %s AND layer = 'infrastructure'", (region_id,))
    return total


CONTOUR_LEVELS = [-500, -200, -100, -50, -20, -10]


def provision_bathymetry(cur, region_id, bbox, refresh=False):
    lon_min, lat_min, lon_max, lat_max = bbox
    set_layer(cur, region_id, "bathymetry", status="en_cours")
    out_dir = ROOT / "data" / "zones" / str(region_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "bathymetry.tif"
    if out.exists() and out.stat().st_size > 0 and not refresh:
        print(f"  GeoTIFF déjà présent ({out.stat().st_size/1e6:.1f} Mo), téléchargement ignoré")
    else:
        params = {"service": "WCS", "version": "2.0.1", "request": "GetCoverage",
                  "coverageId": "emodnet__mean", "format": "image/tiff",
                  "subset": [f"Lat({lat_min},{lat_max})", f"Long({lon_min},{lon_max})"]}
        r = requests.get(BATHY_WCS, params=params, timeout=180)
        r.raise_for_status()
        if "tif" not in r.headers.get("content-type", ""):
            set_layer(cur, region_id, "bathymetry", status="echec")
            raise RuntimeError(f"WCS n'a pas renvoyé un GeoTIFF : {r.text[:300]}")
        tmp = out.with_suffix(".tif.tmp")
        tmp.write_bytes(r.content)
        tmp.replace(out)
        print(f"  bathymétrie {out.stat().st_size/1e6:.1f} Mo -> {out.relative_to(ROOT)}")
    n = compute_contours(cur, region_id, out)
    print(f"  {n} isobathes calculées aux profondeurs {CONTOUR_LEVELS} m")
    set_layer(cur, region_id, "bathymetry", status="prete",
              source="EMODnet Bathymetry emodnet__mean (WCS)", source_url=BATHY_WCS,
              license=BATHY_LICENSE, size_bytes=out.stat().st_size)
    cur.execute("UPDATE region_layers SET fetched_at = now() WHERE region_id = %s AND layer = 'bathymetry'", (region_id,))
    return out.stat().st_size


def compute_contours(cur, region_id, tif_path):
    """Isobathes vectorielles à partir du raster, pour l'affichage. Sous échantillonne pour rester léger."""
    import numpy as np
    import rasterio
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from shapely.geometry import LineString
    with rasterio.open(tif_path) as ds:
        step = max(1, max(ds.width, ds.height) // 1200)
        z = ds.read(1, masked=True).astype("float32").filled(np.nan)[::step, ::step]
        h, w = z.shape
        lons = np.linspace(ds.bounds.left, ds.bounds.right, ds.width)[::step][:w]
        lats = np.linspace(ds.bounds.top, ds.bounds.bottom, ds.height)[::step][:h]
    X, Y = np.meshgrid(lons, lats)
    fig = plt.figure()
    cs = plt.contour(X, Y, z, levels=CONTOUR_LEVELS)
    cur.execute("DELETE FROM bathymetry_contours WHERE region_id = %s", (region_id,))
    n = 0
    for level, segs in zip(cs.levels, cs.allsegs):
        for seg in segs:
            if len(seg) < 2:
                continue
            line = LineString([(float(x), float(y)) for x, y in seg])
            cur.execute("INSERT INTO bathymetry_contours (region_id, depth_m, geom) "
                        "VALUES (%s, %s, ST_SetSRID(ST_GeomFromText(%s), 4326)::geography)",
                        (region_id, float(level), line.wkt))
            n += 1
    plt.close(fig)
    return n


def main():
    ap = argparse.ArgumentParser(description="Provisionnement de la donnée statique d'une région")
    ap.add_argument("--region", help="Nom ou identifiant de région ; par défaut la région active")
    ap.add_argument("--only", choices=["infrastructure", "bathymetry"], help="Un seul fournisseur")
    ap.add_argument("--refresh", action="store_true", help="Retélécharge même si le fichier local existe")
    args = ap.parse_args()
    load_env()

    with connect() as conn, conn.cursor() as cur:
        region_id, name = active_region(cur, args.region)
        bbox = region_bbox(cur, region_id)
        print(f"Région {region_id} : {name}")
        print(f"  emprise {bbox[0]:.2f} {bbox[1]:.2f} {bbox[2]:.2f} {bbox[3]:.2f}")
        if args.only in (None, "infrastructure"):
            t = time.time()
            print("Infrastructures (EMODnet Human Activities)...")
            provision_infrastructure(cur, region_id, bbox)
            print(f"  en {time.time() - t:.0f} s")
        if args.only in (None, "bathymetry"):
            t = time.time()
            print("Bathymétrie (EMODnet Bathymetry)...")
            provision_bathymetry(cur, region_id, bbox, refresh=args.refresh)
            print(f"  en {time.time() - t:.0f} s")
        conn.commit()
    print("Provisionnement terminé.")


def active_region(cur, name):
    if name:
        cur.execute("SELECT id, name FROM regions WHERE name = %s OR id::text = %s", (name, name))
    else:
        cur.execute("SELECT id, name FROM regions WHERE active ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if not row:
        sys.exit("Aucune région trouvée")
    return row


def region_bbox(cur, region_id):
    cur.execute("SELECT ST_XMin(b), ST_YMin(b), ST_XMax(b), ST_YMax(b) FROM "
                "(SELECT ST_Envelope(geom::geometry) AS b FROM regions WHERE id = %s) q", (region_id,))
    return cur.fetchone()


if __name__ == "__main__":
    main()
