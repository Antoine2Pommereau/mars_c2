"""Outils géographiques partagés."""


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
