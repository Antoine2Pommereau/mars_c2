"""Outils géographiques partagés."""
import math


TERRE_M = 6371000.0          # rayon moyen de la Terre


def distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Distance du grand cercle (haversine), en mètres. Seule version du serveur depuis le lot Z4 ; la trajectoire garde
    sa version numpy (mars/trajectoire.py), et les scripts des travailleurs la leur (envoyés seuls par cloud-init)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * TERRE_M * math.asin(math.sqrt(h))


def utm_epsg(lon: float, lat: float) -> int:
    """Code EPSG de la zone UTM contenant le point."""
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def local_projector(lon0: float, lat0: float):
    """Projection plane locale en mètres autour de (lon0, lat0), x vers l'est et y vers le nord : pyproj (UTM) s'il est
    installé, sinon tangente locale en numpy (image des scripts, sans pyproj). Sur l'emprise d'une scène Sentinel 1
    (250 km), l'écart entre les deux reste de l'ordre de 0,1 % des distances, bien sous les tolérances d'appariement."""
    import numpy as np
    try:
        from pyproj import Transformer
        t = Transformer.from_crs("EPSG:4326", f"EPSG:{utm_epsg(lon0, lat0)}", always_xy=True)
        return lambda lon, lat: t.transform(np.asarray(lon, float), np.asarray(lat, float))
    except ImportError:
        k = math.cos(math.radians(lat0))
        return lambda lon, lat: ((np.asarray(lon, float) - lon0) * 111_320.0 * k, (np.asarray(lat, float) - lat0) * 110_574.0)
