"""Catalogue des passages Sentinel 1 (Sentinel Hub, Copernicus Data Space Ecosystem). Sans dépendance raster."""
import pandas as pd
import requests

TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
SH_BASE = "https://sh.dataspace.copernicus.eu"


def iso(t: pd.Timestamp) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_token(client_id: str, client_secret: str) -> str:
    if not client_id or not client_secret:
        raise ValueError("Identifiants Sentinel Hub manquants (SH_CLIENT_ID, SH_CLIENT_SECRET dans .env)")
    r = requests.post(TOKEN_URL, data={"grant_type": "client_credentials", "client_id": client_id,
                                       "client_secret": client_secret}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def search_passes(token: str, bbox, t_from: pd.Timestamp, t_to: pd.Timestamp) -> list[dict]:
    """Passages Sentinel 1 en mode IW couvrant l'emprise, triés par date."""
    payload = {"bbox": list(bbox), "datetime": f"{iso(t_from)}/{iso(t_to)}",
               "collections": ["sentinel-1-grd"], "limit": 100}
    r = requests.post(f"{SH_BASE}/api/v1/catalog/1.0.0/search", headers={"Authorization": f"Bearer {token}"},
                      json=payload, timeout=60)
    r.raise_for_status()
    passes = []
    for f in r.json().get("features", []):
        p = f["properties"]
        if p.get("sar:instrument_mode") not in (None, "IW"):
            continue
        passes.append({
            "product_name": f["id"],
            "acquired_at": pd.Timestamp(p["datetime"]).tz_convert("UTC"),
            "platform": p.get("platform"),
            "orbit_direction": p.get("sat:orbit_state"),
            "footprint": f.get("geometry"),
        })
    return sorted(passes, key=lambda x: x["acquired_at"])
