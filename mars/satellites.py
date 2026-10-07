"""Calendrier des passages Sentinel 1 et Sentinel 2 sur nos régions (étape 3, lot A).

Deux sources publiques, lues sans compte et sans télécharger la moindre image :
  * passages acquis : catalogue STAC de Copernicus Data Space (https://stac.dataspace.copernicus.eu/v1), recherche
    par emprise (les quatre régions) et par date, réponses réduites aux champs utiles. Un produit est une tranche
    (Sentinel 1, environ 25 s) ou une tuile de 110 km (Sentinel 2) : les produits d'une même prise de vue sont réunis
    en un passage ;
  * passages à venir : plans d'acquisition publiés par l'ESA (fichiers KML, un par satellite, environ 20 jours, 2 Mo),
    lus en mémoire. Un passage prévu devient « acquis » quand le catalogue publie le même passage : même satellite et
    même orbite absolue. L'identifiant de prise de vue de Sentinel 1 ne convient pas : il est réattribué à
    l'exécution (77888 au plan, 77889 au catalogue), sauf pour les plans publiés la veille.

Chaque passage est découpé sur l'union des régions (emprise utile seulement, simplifiée), puis rapproché des
infrastructures et des navires des listes qui s'y trouvaient (base du déclenchement automatique des analyses).
Les fonctions de lecture (regroupement, plans, liens) sont pures et couvertes par tests/test_satellites.py.
"""
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from mars.ais.live import ZONES

STAC = "https://stac.dataspace.copernicus.eu/v1/search"
COLLECTIONS = {"S1": "sentinel-1-grd", "S2": "sentinel-2-l1c"}
PLAN_PAGES = {"S1": "https://sentinels.copernicus.eu/copernicus/sentinel-1/acquisition-plans",
              "S2": "https://sentinels.copernicus.eu/copernicus/sentinel-2/acquisition-plans"}
SITE = "https://sentinels.copernicus.eu"
HEADERS = {"User-Agent": "mars-c2 (surveillance maritime, projet personnel)"}
FIELDS = ["id", "geometry", "properties.datetime", "properties.start_datetime", "properties.end_datetime",
          "properties.platform", "properties.sat:relative_orbit", "properties.sat:absolute_orbit",
          "properties.sat:orbit_state", "properties.sar:instrument_mode", "properties.eopf:datatake_id"]
FRANCE = ("bretagne", "manche", "gascogne", "mediterranee")
SIMPLIFY_DEG = 0.01          # emprise simplifiée à environ 1 km : une emprise de passage n'a pas besoin de plus
WATCH_MARGIN_MIN = 30        # navire des listes vu dans l'emprise à 30 minutes près de l'acquisition


def extent() -> tuple[float, float, float, float]:
    z = list(ZONES.values())
    return (min(v[1] for v in z), min(v[0] for v in z), max(v[3] for v in z), max(v[2] for v in z))


def zone_polygons() -> list[dict]:
    """Les régions, une par une : réunies en un multipolygone, elles se chevauchent (Bretagne et Manche), et le
    catalogue rejette une géométrie invalide (erreur 500, « TopologyException »)."""
    return [{"type": "Polygon", "coordinates": [[[b, a], [d, a], [d, c], [b, c], [b, a]]]} for a, b, c, d in ZONES.values()]


def _ts(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def satellite_code(platform: str) -> str:
    """« sentinel-1d » devient « S1D »."""
    m = re.fullmatch(r"sentinel-(\d)([a-z])", (platform or "").lower())
    return f"S{m.group(1)}{m.group(2).upper()}" if m else (platform or "?").upper()


def pass_key(sat: str, absolute_orbit: int | None) -> str:
    """Clé d'un passage, commune au plan et au catalogue : satellite et orbite absolue (une orbite ne traverse nos
    régions qu'une fois)."""
    return f"{sat}_OR{absolute_orbit}"


def descending_s1(t: datetime) -> bool:
    """Sens de l'orbite d'un passage Sentinel 1 prévu (le plan ne le donne pas) : orbite crépusculaire, passage
    descendant vers 6 h et ascendant vers 18 h, heure solaire ; nos régions sont proches du méridien de Greenwich."""
    return t.hour < 12


def group_catalogue(features: list[dict], mission: str) -> list[dict]:
    """Produits du catalogue réunis par prise de vue. Sentinel 1 : l'identifiant de prise de vue est décimal dans le
    catalogue (37753) et hexadécimal dans le nom du produit et le plan (009379)."""
    out: dict[str, dict] = {}
    for f in features:
        p = f.get("properties", {})
        sat = satellite_code(p.get("platform"))
        dt = p.get("eopf:datatake_id")
        datatake = int(dt) if mission == "S1" and dt not in (None, "") and str(dt).isdigit() else None
        absolute = p.get("sat:absolute_orbit")
        if absolute is None:
            continue
        key = pass_key(sat, absolute)
        t0 = _ts(p.get("start_datetime") or p["datetime"])
        t1 = _ts(p.get("end_datetime") or p["datetime"])
        g = out.setdefault(key, {
            "key": key, "mission": mission, "satellite": sat, "platform": (p.get("platform") or "").lower(),
            "mode": p.get("sar:instrument_mode") or ("MSI" if mission == "S2" else None),
            "orbit_direction": p.get("sat:orbit_state"), "relative_orbit": p.get("sat:relative_orbit"),
            "absolute_orbit": absolute, "datatake": str(datatake) if datatake is not None else None,
            "start": t0, "end": t1, "geoms": [], "produits": 0, "statut": "acquis", "source": "catalogue"})
        g["start"], g["end"] = min(g["start"], t0), max(g["end"], t1)
        g["produits"] += 1
        if f.get("geometry"):
            g["geoms"].append(f["geometry"])
    return list(out.values())


def plan_links(html: str, mission: str) -> dict[str, list[tuple[datetime, datetime, str]]]:
    """Fichiers de plan cités par la page de l'ESA, par satellite : (début, fin, adresse), du plus récent au plus
    ancien. Noms : s1d_mp_user_20261007t190903_20261027t210500, s2c_mp_acq__kml_20261001t113000_20261019t143000."""
    pat = (r"/documents/d/sentinel/(s1[a-d])_mp_user_(\d{8}t\d{6})_(\d{8}t\d{6})" if mission == "S1"
           else r"/documents/d/sentinel/(s2[a-d])_mp_acq__kml_(\d{8}t\d{6})_(\d{8}t\d{6})")
    out: dict[str, set] = {}
    for m in re.finditer(pat, html):
        t0, t1 = (datetime.strptime(x, "%Y%m%dt%H%M%S").replace(tzinfo=timezone.utc) for x in m.group(2, 3))
        out.setdefault(m.group(1).upper(), set()).add((t0, t1, SITE + m.group(0)))
    return {k: sorted(v, reverse=True) for k, v in out.items()}


def current_plans(links: dict, now: datetime) -> list[tuple[str, str]]:
    """Pour chaque satellite, le plan le plus récent qui couvre encore l'avenir : (satellite, adresse)."""
    out = []
    for sat, files in links.items():
        for t0, t1, url in files:
            if t0 <= now + timedelta(days=1) and t1 > now:
                out.append((sat, url))
                break
    return sorted(out)


def _bbox(coords: list[tuple[float, float]]):
    xs, ys = [c[0] for c in coords], [c[1] for c in coords]
    return min(xs), min(ys), max(xs), max(ys)


def over_regions(t0: datetime, t1: datetime, y0: float, y1: float, ascending: bool,
                 strip_box: tuple, zones=None) -> tuple[datetime, datetime] | None:
    """Heures d'entrée et de sortie de nos régions, interpolées en latitude le long de la bande (le satellite monte
    vers le nord en orbite ascendante, descend sinon) : un plan Sentinel 1 donne le début de la prise de vue, souvent
    plusieurs minutes avant nos côtes. None si la bande ne touche aucune région."""
    zones = zones if zones is not None else list(ZONES.values())
    x0, _, x1, _ = strip_box
    lats = [(max(a, y0), min(c, y1)) for a, b, c, d in zones if b <= x1 and d >= x0 and a <= y1 and c >= y0]
    if not lats or y1 <= y0:
        return None
    lo, hi = min(v[0] for v in lats), max(v[1] for v in lats)
    span = (t1 - t0).total_seconds()
    frac = (lambda y: (y - y0) / (y1 - y0)) if ascending else (lambda y: (y1 - y) / (y1 - y0))
    a, b = sorted((frac(lo), frac(hi)))
    return t0 + timedelta(seconds=a * span), t0 + timedelta(seconds=b * span)


def parse_plan(data: bytes, sat: str, mission: str, now: datetime, box=None) -> list[dict]:
    """Passages prévus d'un fichier KML de l'ESA, réduits à ceux qui touchent l'emprise `box` et ne sont pas
    terminés. Lecture en flux : un fichier de 2 Mo n'est jamais transformé en arbre complet."""
    box = box or extent()
    out = []
    for _, el in ET.iterparse(io.BytesIO(data), events=("end",)):
        if not el.tag.endswith("Placemark"):
            continue
        data_ = {d.get("name"): (d.findtext("{*}value") or "").strip() for d in el.iter() if d.tag.endswith("}Data")}
        coords_txt = next((c.text for c in el.iter() if c.tag.endswith("coordinates") and c.text), None)
        el.clear()
        if not coords_txt or "ObservationTimeStart" not in data_:
            continue
        coords = [tuple(float(v) for v in c.split(",")[:2]) for c in coords_txt.split()]
        x0, y0, x1, y1 = _bbox(coords)
        if x1 - x0 > 180 or x1 < box[0] or x0 > box[2] or y1 < box[1] or y0 > box[3]:
            continue                                   # hors de nos régions, ou à cheval sur l'antiméridien
        t0, t1 = _ts(data_["ObservationTimeStart"]), _ts(data_.get("ObservationTimeStop") or data_["ObservationTimeStart"])
        if t1 < now - timedelta(hours=1):
            continue
        s = data_.get("SatelliteId") or sat
        ascending = mission == "S1" and not descending_s1(t0)
        win = over_regions(t0, t1, y0, y1, ascending, (x0, y0, x1, y1))
        if win is None:
            continue
        if mission == "S1":
            # Sentinel 2 garde le début de la prise de vue : le catalogue date chaque tuile de la même façon
            t0, t1 = win
        absolute = int(data_["OrbitAbsolute"]) if data_.get("OrbitAbsolute", "").isdigit() else None
        if absolute is None:
            continue
        datatake = int(data_["DatatakeId"], 16) if mission == "S1" and data_.get("DatatakeId") else None
        out.append({
            "key": pass_key(s, absolute), "mission": mission, "satellite": s,
            "platform": f"sentinel-{s[1]}{s[2].lower()}", "mode": data_.get("Mode"),
            "orbit_direction": "ascending" if ascending else "descending",
            "relative_orbit": int(data_["OrbitRelative"]) if data_.get("OrbitRelative", "").isdigit() else None,
            "absolute_orbit": absolute, "datatake": str(datatake) if datatake is not None else None,
            "start": t0, "end": t1, "geoms": [{"type": "Polygon", "coordinates": [[list(c) for c in coords]]}],
            "produits": 0, "statut": "prevu", "source": "plan"})
    # Une orbite découpée en segments (10818-1, 10818-2 pour Sentinel 2, plusieurs prises de vue pour Sentinel 1) :
    # réunis sous la même clé
    merged: dict[str, dict] = {}
    for p in out:
        if p["key"] in merged:
            m = merged[p["key"]]
            m["start"], m["end"] = min(m["start"], p["start"]), max(m["end"], p["end"])
            m["geoms"] += p["geoms"]
        else:
            merged[p["key"]] = p
    return list(merged.values())


# Base de données (psycopg)

UPSERT = """
WITH fp AS (
    SELECT ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_SimplifyPreserveTopology(ST_Intersection(
               ST_UnaryUnion(ST_CollectionExtract(ST_SetSRID(ST_GeomFromGeoJSON(%(geoms)s), 4326), 3)),
               (SELECT ST_Union(geom::geometry) FROM regions WHERE lower(name) = ANY(%(france)s))), %(tol)s)), 3)) AS g
)
INSERT INTO sar_passes (product_name, platform, acquired_at, ended_at, orbit_direction, footprint, mission, satellite,
                        mode, relative_orbit, absolute_orbit, datatake, statut, source, produits, regions, mis_a_jour_le)
SELECT %(key)s, %(platform)s, %(start)s, %(end)s, %(orbit_direction)s, fp.g::geography, %(mission)s, %(satellite)s,
       %(mode)s, %(relative_orbit)s, %(absolute_orbit)s, %(datatake)s, %(statut)s, %(source)s, %(produits)s,
       ARRAY(SELECT lower(r.name) FROM regions r WHERE lower(r.name) = ANY(%(france)s)
             AND ST_Intersects(r.geom::geometry, fp.g) ORDER BY 1), now()
FROM fp WHERE NOT ST_IsEmpty(fp.g)
ON CONFLICT (product_name) DO UPDATE SET
    platform = EXCLUDED.platform, acquired_at = EXCLUDED.acquired_at, ended_at = EXCLUDED.ended_at,
    orbit_direction = EXCLUDED.orbit_direction, footprint = EXCLUDED.footprint, mode = EXCLUDED.mode,
    relative_orbit = EXCLUDED.relative_orbit, absolute_orbit = EXCLUDED.absolute_orbit,
    datatake = EXCLUDED.datatake, statut = EXCLUDED.statut, source = EXCLUDED.source, produits = EXCLUDED.produits,
    regions = EXCLUDED.regions, mis_a_jour_le = now()
WHERE sar_passes.statut IS DISTINCT FROM 'acquis' OR EXCLUDED.statut = 'acquis'
RETURNING (xmax = 0) AS nouveau
"""

COVERAGE = """
UPDATE sar_passes s SET
    infra_ids = ARRAY(
        -- Bretagne et Manche se chevauchent : un tracé commun y est stocké deux fois, on n'en garde qu'un
        SELECT min(i.id) FROM infrastructure i JOIN regions r ON r.id = i.region_id
        WHERE lower(r.name) = ANY(%(france)s) AND ST_Intersects(i.geom::geometry, s.footprint::geometry)
        GROUP BY i.kind, coalesce(i.name, ''), coalesce(i.attrs->>'type', ''),
                 ST_AsText(ST_SnapToGrid(ST_Envelope(i.geom::geometry), 0.01))
        ORDER BY 1),
    watch_ids = CASE WHEN s.acquired_at > now() THEN '{}'::bigint[] ELSE ARRAY(
        SELECT DISTINCT p.vessel_id FROM positions p
        JOIN vessel_watch w ON w.vessel_id = p.vessel_id AND w.level <> 'autre_risque'
        WHERE p.ts BETWEEN s.acquired_at - make_interval(mins => %(margin)s)
                       AND coalesce(s.ended_at, s.acquired_at) + make_interval(mins => %(margin)s)
          AND ST_Intersects(p.geom::geometry, s.footprint::geometry)
        ORDER BY 1) END,
    couverture_le = now()
WHERE s.mission IS NOT NULL AND s.acquired_at >= %(since)s
"""


def stac_search(session, mission: str, start: datetime, end: datetime, page: int = 200) -> list[dict]:
    """Produits du catalogue qui touchent nos régions, sans doublon (un produit peut toucher deux régions)."""
    out: dict[str, dict] = {}
    for poly in zone_polygons():
        body = {"collections": [COLLECTIONS[mission]], "intersects": poly,
                "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}", "limit": page,
                "fields": {"include": FIELDS, "exclude": ["assets", "links"]}}
        url = STAC
        for _ in range(100):                              # 100 pages au plus : garde fou
            r = session.post(url, json=body, headers=HEADERS, timeout=60)
            r.raise_for_status()
            d = r.json()
            for f in d.get("features", []):
                out[f["id"]] = f
            nxt = next((ln for ln in d.get("links", []) if ln.get("rel") == "next"), None)
            if not nxt or not d.get("features"):
                break
            url, body = nxt["href"], nxt.get("body", body)
    return list(out.values())


def upsert(conn, passes: list[dict]) -> dict:
    new = updated = 0
    for p in passes:
        row = conn.execute(UPSERT, {**{k: p.get(k) for k in (
            "key", "platform", "start", "end", "orbit_direction", "mission", "satellite", "mode", "relative_orbit",
            "absolute_orbit", "datatake", "statut", "source", "produits")},
            "geoms": json.dumps({"type": "GeometryCollection", "geometries": p["geoms"]}),
            "france": list(FRANCE), "tol": SIMPLIFY_DEG}).fetchone()
        if row is not None:
            new += row[0]
            updated += not row[0]
    return {"nouveaux": new, "mis_a_jour": updated}


def update(conn, days: int | None = None, now: datetime | None = None, session=None) -> dict:
    """Mise à jour quotidienne : passages acquis des derniers jours (30 jours au premier passage), plans à venir,
    passages prévus non confirmés retirés après deux jours, puis couverture (infrastructures, navires des listes)."""
    import requests
    session = session or requests.Session()
    now = now or datetime.now(timezone.utc)
    first = not conn.execute("SELECT EXISTS (SELECT 1 FROM sar_passes WHERE mission IS NOT NULL)").fetchone()[0]
    days = days or (30 if first else 3)
    out: dict = {"jours": days}
    errors = {}
    for mission in ("S1", "S2"):
        try:
            feats = stac_search(session, mission, now - timedelta(days=days), now)
            passes = group_catalogue(feats, mission)
            out[f"catalogue_{mission}"] = {"produits": len(feats), "passages": len(passes), **upsert(conn, passes)}
        except Exception as e:                                # une source en échec n'empêche pas l'autre
            errors[f"catalogue_{mission}"] = str(e)[:300]
        try:
            r = session.get(PLAN_PAGES[mission], headers=HEADERS, timeout=60)
            r.raise_for_status()
            plans = current_plans(plan_links(r.text, mission), now)
            got = []
            for sat, url in plans:
                k = session.get(url, headers=HEADERS, timeout=120)
                k.raise_for_status()
                got += parse_plan(k.content, sat, mission, now)
            out[f"plans_{mission}"] = {"fichiers": [u.rsplit("/", 1)[-1] for _, u in plans], "passages": len(got),
                                       **upsert(conn, got)}
        except Exception as e:
            errors[f"plans_{mission}"] = str(e)[:300]
    out["prevus_retires"] = conn.execute(
        "DELETE FROM sar_passes s WHERE s.statut = 'prevu' AND s.ended_at < %s "
        "AND NOT EXISTS (SELECT 1 FROM analyses a WHERE a.pass_id = s.id)", (now - timedelta(days=2),)).rowcount
    conn.execute(COVERAGE, {"france": list(FRANCE), "margin": WATCH_MARGIN_MIN, "since": now - timedelta(days=days + 1)})
    if errors:
        out["erreurs"] = errors
        if len(errors) == 4:
            raise RuntimeError(f"toutes les sources en échec : {errors}")
    return out
