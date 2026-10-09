"""Registre des preuves images des détections satellites (vignettes rangées sur R2, jamais sur le disque du serveur).

Une source déclare l'objet qu'elle prouve et le type de preuve d'alerte correspondant (alert_evidence) ; VIIRS est en
place (lot B), Sentinel 1 et Sentinel 2 suivront au lot C avec la même table, le même rangement et la même purge.
Conservation : sans limite pour une image liée à une alerte, `jours` (30) sinon ; une image dont l'objet a disparu
(granule retraitée) est retirée aussitôt.
"""
import json
from datetime import datetime

SOURCES = {
    "viirs": {"objet": "viirs_detection", "preuve": "viirs", "table": "viirs_detections", "prefixe": "vignettes/viirs"},
    "sentinel1": {"objet": "detection", "preuve": "detection", "table": "detections", "prefixe": "vignettes/sentinel1"},
    "sentinel2": {"objet": "detection", "preuve": "detection", "table": "detections", "prefixe": "vignettes/sentinel2"},
}


def key(source: str, jour: str, lot: str, objet_id: int) -> str:
    """Objet R2 d'une preuve : vignettes/<source>/<nuit ou jour>/<granule ou passage>/<objet>.png"""
    return f"{SOURCES[source]['prefixe']}/{jour}/{lot}/{objet_id}.png"


def store(conn, r2, source: str, objet_id: int, png: bytes, geo: dict | None, prise_le: datetime, cle: str) -> int:
    """Envoie l'image sur R2 (envoi vérifié) puis l'inscrit au registre ; retourne l'identifiant de la preuve."""
    r2.put_verified(png, cle)
    return conn.execute(
        """INSERT INTO preuves_images (source, objet, objet_id, cle, octets, largeur, hauteur, geo, prise_le)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (cle) DO UPDATE SET octets = EXCLUDED.octets, geo = EXCLUDED.geo, supprime_le = NULL
           RETURNING id""",
        (source, SOURCES[source]["objet"], objet_id, cle, len(png), (geo or {}).get("largeur"), (geo or {}).get("hauteur"),
         json.dumps(geo) if geo else None, prise_le)).fetchone()[0]


def to_purge(conn, jours: int = 30) -> list[tuple[int, str]]:
    """Preuves à retirer : objet disparu, ou plus vieille que `jours` sans alerte liée."""
    parts = []
    for s in SOURCES.values():
        parts.append(f"""SELECT p.id, p.cle FROM preuves_images p
            WHERE p.supprime_le IS NULL AND p.objet = '{s['objet']}'
              AND (NOT EXISTS (SELECT 1 FROM {s['table']} o WHERE o.id = p.objet_id)
                   OR (p.prise_le < now() - make_interval(days => %(jours)s)
                       AND NOT EXISTS (SELECT 1 FROM alert_evidence e
                                       WHERE e.evidence_type = '{s['preuve']}' AND e.evidence_id = p.objet_id)))""")
    sql = " UNION ".join(dict.fromkeys(parts))           # sentinel1 et sentinel2 partagent la même requête
    return conn.execute(sql, {"jours": jours}).fetchall()


def purge(conn, r2, jours: int = 30) -> dict:
    """Retire de R2 et marque au registre les preuves à ne plus garder."""
    rows = to_purge(conn, jours)
    for pid, cle in rows:
        r2.delete(cle)
        conn.execute("UPDATE preuves_images SET supprime_le = now() WHERE id = %s", (pid,))
    kept = conn.execute("SELECT count(*), coalesce(sum(octets), 0) FROM preuves_images WHERE supprime_le IS NULL").fetchone()
    return {"retirees": len(rows), "gardees": kept[0], "octets_gardes": int(kept[1])}
