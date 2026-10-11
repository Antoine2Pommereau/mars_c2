"""Outils géographiques partagés."""
import math


def utm_epsg(lon: float, lat: float) -> int:
    """Code EPSG de la zone UTM contenant le point."""
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def bbox_size_km(bbox) -> tuple[float, float]:
    """Largeur et hauteur approximatives d'une emprise (lon_min, lat_min, lon_max, lat_max), en km."""
    import math
    lon_min, lat_min, lon_max, lat_max = bbox
    lat_c = math.radians((lat_min + lat_max) / 2)
    return (lon_max - lon_min) * 111.32 * math.cos(lat_c), (lat_max - lat_min) * 110.57


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
