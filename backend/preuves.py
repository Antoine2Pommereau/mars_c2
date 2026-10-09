"""Preuves images (vignettes des détections satellites) : relais depuis R2 par une adresse présignée, sans boto3 et
sans écriture disque ; quelques images gardées en mémoire. Le registre est la table preuves_images (mars/preuves.py)."""
from collections import OrderedDict

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from mars.r2_signature import r2_get_url
from mars.viirs import haversine_m, position_at

router = APIRouter()
_CACHE: OrderedDict[int, bytes] = OrderedDict()
_CACHE_MAX = 64
AIS_RAYON_M = 5000


@router.get("/api/preuves/{pid}.png")
async def proof_image(request: Request, pid: int):
    if pid in _CACHE:
        _CACHE.move_to_end(pid)
        return Response(_CACHE[pid], media_type="image/png", headers={"Cache-Control": "max-age=86400"})
    async with request.app.state.pool.acquire() as c:
        row = await c.fetchrow("SELECT cle, supprime_le FROM preuves_images WHERE id = $1", pid)
    if row is None:
        raise HTTPException(404, "Preuve inconnue")
    if row["supprime_le"] is not None:
        raise HTTPException(410, "Preuve retirée (conservation de 30 jours sans alerte)")
    url = r2_get_url(row["cle"])
    if url is None:
        raise HTTPException(503, "R2 non configuré")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url)
    if r.status_code != 200:
        raise HTTPException(502, f"R2 : {r.status_code}")
    _CACHE[pid] = r.content
    while len(_CACHE) > _CACHE_MAX:
        _CACHE.popitem(last=False)
    return Response(r.content, media_type="image/png", headers={"Cache-Control": "max-age=86400", "X-Source": "R2"})


async def proof_of(c, objet: str, objet_id: int) -> dict | None:
    """Dernière preuve image d'un objet (viirs_detection, detection) : identifiant, taille, géoréférence."""
    r = await c.fetchrow("SELECT id, source, octets, largeur, hauteur, geo, prise_le FROM preuves_images "
                         "WHERE objet = $1 AND objet_id = $2 AND supprime_le IS NULL ORDER BY id DESC LIMIT 1", objet, objet_id)
    return {**dict(r), "prise_le": r["prise_le"].isoformat(), "url": f"/api/preuves/{r['id']}.png"} if r else None


async def ais_near(c, lon: float, lat: float, ts, matched: int | None, radius_m: float = AIS_RAYON_M) -> list[dict]:
    """Positions AIS à l'heure du passage (interpolées, ou estimées sur 20 minutes au plus) des navires à moins de
    `radius_m`, pour la surimpression sur la vignette ; le navire apparié est marqué."""
    rows = await c.fetch(
        """SELECT p.vessel_id, p.ts, ST_X(p.geom::geometry) AS lon, ST_Y(p.geom::geometry) AS lat, p.sog_kn, p.cog_deg,
                  v.name, v.mmsi
           FROM positions p JOIN vessels v ON v.id = p.vessel_id
           WHERE p.ts BETWEEN $3::timestamptz - interval '20 minutes' AND $3::timestamptz + interval '20 minutes'
             AND ST_DWithin(p.geom, ST_SetSRID(ST_MakePoint($1, $2), 4326)::geography, $4::float8)
           ORDER BY p.vessel_id, p.ts""", lon, lat, ts, radius_m + 8000)
    tracks: dict[int, list] = {}
    names = {}
    for r in rows:
        tracks.setdefault(r["vessel_id"], []).append({"ts": r["ts"], "lon": r["lon"], "lat": r["lat"],
                                                       "sog": r["sog_kn"], "cog": r["cog_deg"]})
        names[r["vessel_id"]] = (r["name"], r["mmsi"])
    out = []
    for vid, tr in tracks.items():
        pos = position_at(tr, ts, 20)
        if pos is None:
            continue
        d = haversine_m(lon, lat, pos[0], pos[1])
        if d <= radius_m or vid == matched:
            out.append({"vessel_id": vid, "name": names[vid][0], "mmsi": names[vid][1], "lon": round(pos[0], 5),
                        "lat": round(pos[1], 5), "distance_m": round(d), "estime_s": round(pos[2]),
                        "apparie": vid == matched})
    return sorted(out, key=lambda x: x["distance_m"])

