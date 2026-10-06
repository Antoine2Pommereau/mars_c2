"""Provisionnement des infrastructures sous marines d'une région, depuis EMODnet Human Activities.

Tout est téléchargé et stocké en base (table infrastructure), donc hors ligne ensuite. Appelable depuis
le script de ligne de commande (scripts/provision_region.py) comme depuis le backend. Les fonctions
reçoivent un curseur psycopg et écrivent en base.

Câbles télécoms, câbles électriques, pipelines et parcs éoliens, découpés par l'emprise de la région.
"""
import json

import requests

HA_WFS = "https://ows.emodnet-humanactivities.eu/geoserver/emodnet/wfs"
HA_LICENSE = "EMODnet Human Activities, CC BY 4.0"

# Chaque couche nationale ne couvre que son emprise, donc la plupart renvoient zéro hors de leur zone :
# on garde ce qui tombe dans la région. Les couches pcables* sont électriques, les autres télécoms.
POWER_CABLE_LAYERS = ["pcablesnve", "pcablesbshcontis", "pcablesrijks", "pcablesshom"]
TELECOM_CABLE_LAYERS = ["bshcontiscables", "rijkscables", "shomcables", "sigcables",
                        "cicacables", "ukfibrecables", "maltacables"]
PIPELINE_LAYERS = ["pipelines"]
WINDFARM_LAYERS = ["windfarmspoly"]

# Clés multilingues : navn/eier (Norvège, NVE), naam/eigenaar (Pays Bas, Rijks), génériques
NAME_KEYS = ["navn", "naam", "name", "cable_name", "kabel_nr", "label", "title", "NAME", "farm_name"]
OPERATOR_KEYS = ["eier", "eigenaar", "operator", "owner", "company", "OPERATOR", "power_comp"]


def pick(props, keys):
    for k in keys:
        v = props.get(k)
        if v not in (None, "", "NaN"):
            return str(v)
    return None


def normalize_attrs(layer, kind, props):
    """Quelques infos lisibles, normalisées selon la source, pour la fiche au clic."""
    a = {}
    if kind == "windfarm":
        a["type"] = "Parc éolien"
        for src, dst in (("STATUS", "statut"), ("POWER_MW", "puissance_mw"),
                         ("YEAR", "annee"), ("N_TURBINES", "eoliennes"), ("COUNTRY", "pays")):
            v = props.get(src) or props.get(src.lower())
            if v not in (None, "", "NaN"):
                a[dst] = v
    elif kind == "pipeline":
        a["type"] = "Pipeline"
    elif layer in POWER_CABLE_LAYERS:
        a["type"] = "Câble électrique"
        if props.get("spenning_k"):
            a["tension_kv"] = props["spenning_k"]
        if props.get("driftsatta"):
            a["annee"] = props["driftsatta"]
    else:
        a["type"] = "Câble télécom"
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
        "('coastline', 'operationnelle'), ('bathymetry', 'affichage'), ('infrastructure', 'operationnelle')"
        ") AS c(layer, usage) ON CONFLICT (region_id, layer) DO NOTHING",
        (region_id,))


def provision_infrastructure(cur, region_id, bbox):
    """Télécharge câbles, pipelines et parcs éoliens d'EMODnet pour l'emprise, et remplit la table."""
    lon_min, lat_min, lon_max, lat_max = bbox
    bbox_param = f"{lat_min},{lon_min},{lat_max},{lon_max},urn:ogc:def:crs:EPSG::4326"
    set_layer(cur, region_id, "infrastructure", status="en_cours")
    cur.execute("DELETE FROM infrastructure WHERE region_id = %s", (region_id,))
    groups = [("cable", POWER_CABLE_LAYERS + TELECOM_CABLE_LAYERS),
              ("pipeline", PIPELINE_LAYERS), ("windfarm", WINDFARM_LAYERS)]
    total = 0
    for kind, layers in groups:
        for layer in layers:
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
            kept = 0
            for f in feats:
                g = f.get("geometry")
                if not g:
                    continue
                props = f.get("properties", {})
                cur.execute(
                    "INSERT INTO infrastructure (region_id, kind, name, operator, source, attrs, geom) "
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb, "
                    "ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326))::geography)",
                    (region_id, kind, pick(props, NAME_KEYS), pick(props, OPERATOR_KEYS),
                     f"EMODnet:{layer}", json.dumps(normalize_attrs(layer, kind, props)), json.dumps(g)))
                kept += 1
                total += 1
            print(f"  {layer:22s} {kept:>4} objets ({kind})")
    set_layer(cur, region_id, "infrastructure", status="prete",
              source="EMODnet Human Activities (WFS)", source_url=HA_WFS, license=HA_LICENSE,
              feature_count=total)
    cur.execute("UPDATE region_layers SET fetched_at = now() WHERE region_id = %s AND layer = 'infrastructure'", (region_id,))
    return total


def provision(cur, region_id, bbox, which="infrastructure"):
    """Orchestre les fournisseurs. Pour l'instant une seule couche : infrastructure."""
    ensure_layers(cur, region_id)
    if which in ("all", "infrastructure"):
        provision_infrastructure(cur, region_id, bbox)
