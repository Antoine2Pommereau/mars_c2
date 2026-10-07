"""Région affichée (sélecteur de la barre d'état) : Toute la France ou l'une des régions de la table regions.

Chaque route qui sert des objets situés accepte `region` et filtre au plus tôt dans la requête SQL, avec le
fragment `dans_region(colonne, n)` : `$n` vaut NULL (toute la France) ou la géométrie de la région en EWKT.
"""
from fastapi import HTTPException

FRANCE = ("bretagne", "manche", "gascogne", "mediterranee")
_cache: dict[str, tuple[str, list[float], str]] = {}


def dans_region(col: str, n: int) -> str:
    """Condition SQL : `col` (géométrie) dans la région passée en paramètre `$n`, ou vrai sans région."""
    return f"(${n}::text IS NULL OR ST_Intersects({col}, ST_GeomFromEWKT(${n}::text)))"


async def _load(c):
    if not _cache:
        for r in await c.fetch(
                "SELECT lower(name) AS key, name, ST_AsEWKT(geom::geometry) AS ewkt, ST_XMin(g) AS x0, ST_YMin(g) AS y0, "
                "ST_XMax(g) AS x1, ST_YMax(g) AS y1 FROM regions, LATERAL (SELECT geom::geometry AS g) b "
                "WHERE lower(name) = ANY($1::text[])", list(FRANCE)):
            _cache[r["key"]] = (r["ewkt"], [r["x0"], r["y0"], r["x1"], r["y1"]], r["name"])


async def region_ewkt(c, key: str | None) -> str | None:
    """Géométrie EWKT d'une région (en cache), None pour toute la France ; 404 pour une région inconnue."""
    if not key:
        return None
    await _load(c)
    k = key.lower()
    if k not in _cache:
        raise HTTPException(404, f"Région inconnue : {key}")
    return _cache[k][0]


async def regions_list(c) -> list[dict]:
    await _load(c)
    return [{"key": k, "name": _cache[k][2], "bbox": _cache[k][1]} for k in FRANCE if k in _cache]
