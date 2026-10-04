"""Provisionnement de la donnée statique d'une région (voir docs/spec_regions.md).

Tout est téléchargé et stocké en local, donc hors ligne. Appelable depuis le script de ligne de
commande (scripts/provision_region.py) comme depuis le backend (tâche de fond). Les fonctions
reçoivent un curseur psycopg et écrivent en base ; les fichiers vont dans data/zones/<id>/.
"""
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]

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
CONTOUR_LEVELS = [-500, -200, -100, -50, -20, -10]

# Aires marines protégées : Natura 2000 (couche mixte, filtrée au marin) et MPA des conventions régionales
PROTECTED_LAYERS = ["natura2000areas", "marineprotectedareas"]
PROTECTED_LICENSE = "EMODnet Human Activities, source EEA, réutilisation libre avec attribution"
PA_NAME_KEYS = ["sitename", "name", "orig_name"]

# Clés multilingues : navn/eier (Norvège, NVE), naam/eigenaar (Pays Bas, Rijks), génériques
NAME_KEYS = ["navn", "naam", "name", "cable_name", "kabel_nr", "label", "title"]
OPERATOR_KEYS = ["eier", "eigenaar", "operator", "owner", "company"]


def pick(props, keys):
    for k in keys:
        v = props.get(k)
        if v not in (None, "", "NaN"):
            return str(v)
    return None


def normalize_attrs(layer, props):
    """Quelques infos lisibles, normalisées selon la source, pour la fiche au clic."""
    a = {}
    if layer == "pcablesnve":
        a["type"] = "Câble électrique"
        if props.get("spenning_k"):
            a["tension_kv"] = props["spenning_k"]
        if props.get("driftsatta"):
            a["annee"] = props["driftsatta"]
        if props.get("nettnivaa"):
            a["reseau"] = props["nettnivaa"]
    elif layer == "rijkscables":
        a["type"] = props.get("kabelsoort") or props.get("kabel_type") or "Câble"
        if props.get("trace_van") and props.get("trace_tot"):
            a["trace"] = f"{props['trace_van']} vers {props['trace_tot']}"
        if props.get("omschrijvi"):
            a["description"] = props.get("omschrijvi")
    elif layer in PIPELINE_LAYERS:
        a["type"] = "Pipeline"
    return {k: v for k, v in a.items() if v not in (None, "", "NaN")}


def normalize_pa_attrs(layer, props):
    """Infos lisibles d'une aire protégée, normalisées selon la source, pour la fiche au clic."""
    a = {}
    if layer == "natura2000areas":
        a["designation"] = props.get("sitedesc") or props.get("directive")
        if props.get("sitecode"):
            a["code"] = props["sitecode"]
        if props.get("area_ha"):
            a["surface_ha"] = props["area_ha"]
    elif layer == "marineprotectedareas":
        a["designation"] = props.get("designatio")
        if props.get("rsc"):
            a["convention"] = props["rsc"]
        if props.get("iucn_cat"):
            a["iucn"] = props["iucn_cat"]
        if props.get("status"):
            a["statut"] = props["status"]
        if props.get("mang_auth"):
            a["autorite"] = props["mang_auth"]
    return {k: v for k, v in a.items() if v not in (None, "", "NaN")}


def leaves_minmax(coords, acc):
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
    """GeoServer rend normalement du GeoJSON en lon,lat. Garde fou : inverse si les abscisses tombent
    dans la plage des latitudes de la région et les ordonnées dans celle des longitudes."""
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


def ensure_layers(cur, region_id):
    """Inscrit les couches attendues de la région si elles n'existent pas encore."""
    cur.execute(
        "INSERT INTO region_layers (region_id, layer, usage) "
        "SELECT %s, c.layer, c.usage FROM (VALUES "
        "('coastline', 'operationnelle'), ('bathymetry', 'affichage'), ('infrastructure', 'operationnelle'), "
        "('protected_areas', 'operationnelle')"
        ") AS c(layer, usage) ON CONFLICT (region_id, layer) DO NOTHING",
        (region_id,))


def provision_infrastructure(cur, region_id, bbox):
    lon_min, lat_min, lon_max, lat_max = bbox
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
                "INSERT INTO infrastructure (region_id, kind, name, operator, source, attrs, geom) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)::geography)",
                (region_id, kind, pick(props, NAME_KEYS), pick(props, OPERATOR_KEYS),
                 f"EMODnet:{layer}", json.dumps(normalize_attrs(layer, props)), json.dumps(g)))
            total += 1
        print(f"  {layer:22s} {len(feats):>4} objets")
    set_layer(cur, region_id, "infrastructure", status="prete",
              source="EMODnet Human Activities (WFS)", source_url=HA_WFS, license=HA_LICENSE,
              feature_count=total)
    cur.execute("UPDATE region_layers SET fetched_at = now() WHERE region_id = %s AND layer = 'infrastructure'", (region_id,))
    return total


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
    png = render_shading(out)
    print(f"  ombrage coloré -> {png.relative_to(ROOT)} ({png.stat().st_size/1e6:.1f} Mo)")
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


def render_shading(tif_path):
    """Image colorée du fond marin (dégradé par profondeur), transparente sur la terre, pour l'overlay carte."""
    import numpy as np
    import rasterio
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.colors as mcolors
    import matplotlib.image as mpimg
    with rasterio.open(tif_path) as ds:
        z = ds.read(1, masked=True)
    depth = np.asarray(-z.filled(np.nan))  # profondeur positive en mer, négative sur terre, nan hors donnée
    sea = np.isfinite(depth) & (depth > 0)
    norm = mcolors.Normalize(vmin=0, vmax=300, clip=True)  # dégradé sur 0 à 300 m, au delà saturé
    # Dégradé bleu sombre et désaturé : hauts fonds plus clairs mais jamais vifs, grands fonds proches du fond de carte
    cmap = mcolors.LinearSegmentedColormap.from_list("profondeur", ["#6fa3b6", "#3f7284", "#274f5f", "#17323d"])
    rgba = cmap(norm(np.where(sea, depth, 0.0)))
    rgba[..., 3] = np.where(sea, 1.0, 0.0)  # opaque en mer, transparent ailleurs ; l'opacité finale est réglée sur la couche
    out = tif_path.with_name("bathymetry.png")
    mpimg.imsave(out, rgba)
    return out


def provision_protected_areas(cur, region_id, bbox):
    lon_min, lat_min, lon_max, lat_max = bbox
    bbox_param = f"{lat_min},{lon_min},{lat_max},{lon_max},urn:ogc:def:crs:EPSG::4326"
    set_layer(cur, region_id, "protected_areas", status="en_cours")
    cur.execute("DELETE FROM protected_areas WHERE region_id = %s", (region_id,))
    total = 0
    for layer in PROTECTED_LAYERS:
        kind = "natura2000" if layer == "natura2000areas" else "mpa"
        params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                  "typeNames": f"emodnet:{layer}", "outputFormat": "application/json",
                  "srsName": "EPSG:4326", "bbox": bbox_param}
        r = requests.get(HA_WFS, params=params, timeout=180)
        r.raise_for_status()
        feats = r.json().get("features", [])
        if layer == "natura2000areas":   # couche mixte terre et mer : on ne garde que le marin
            feats = [f for f in feats if (f.get("properties", {}).get("coast_mar") == 1
                                          or (f.get("properties", {}).get("mar_perc") or 0) > 0)]
        if not feats:
            continue
        geoms = [f["geometry"] for f in feats if f.get("geometry")]
        ensure_lonlat(geoms, bbox)
        for f in feats:
            g = f.get("geometry")
            if not g:
                continue
            props = f.get("properties", {})
            country = props.get("country") or props.get("ms")
            designation = props.get("sitedesc") or props.get("designatio")
            # Simplifié à environ 100 m : des polygones Natura 2000 bruts ont des milliers de sommets, ce qui
            # rend le test « navire dans la zone » très lent sans rien apporter à l'échelle d'un navire.
            cur.execute(
                "INSERT INTO protected_areas (region_id, kind, name, designation, country, source, attrs, geom) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, "
                "ST_CollectionExtract(ST_MakeValid(ST_SimplifyPreserveTopology("
                "    ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), 0.001)), 3)::geography)",
                (region_id, kind, pick(props, PA_NAME_KEYS), designation, country,
                 f"EMODnet:{layer}", json.dumps(normalize_pa_attrs(layer, props)), json.dumps(g)))
            total += 1
        print(f"  {layer:22s} {len(feats):>4} objets")
    set_layer(cur, region_id, "protected_areas", status="prete",
              source="EMODnet Human Activities (WFS)", source_url=HA_WFS, license=PROTECTED_LICENSE,
              feature_count=total)
    cur.execute("UPDATE region_layers SET fetched_at = now() WHERE region_id = %s AND layer = 'protected_areas'", (region_id,))
    return total


def provision(cur, region_id, bbox, which="all", refresh=False):
    """Orchestre les fournisseurs. which vaut all, infrastructure, bathymetry ou protected_areas."""
    ensure_layers(cur, region_id)
    if which in ("all", "infrastructure"):
        provision_infrastructure(cur, region_id, bbox)
    if which in ("all", "bathymetry"):
        provision_bathymetry(cur, region_id, bbox, refresh=refresh)
    if which in ("all", "protected_areas"):
        provision_protected_areas(cur, region_id, bbox)
