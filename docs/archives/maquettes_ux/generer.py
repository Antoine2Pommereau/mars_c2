"""Maquettes statiques de la refonte (lot U, phase 1) : pages HTML autonomes, données de la base de test, navires
masqués (pseudonymes du mode démonstration). Aucune image binaire : la carte est un SVG tiré de la base (trait de côte
de `land`, câbles EMODnet, trafic à l'instant, dossiers).

    MARS_TEST_DATABASE_URL=postgresql://mars:mars@localhost:55432/mars python docs/ux/maquettes/generer.py
"""
import html
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

import psycopg

ICI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ICI)
sys.path.insert(0, os.path.join(ICI, "..", "..", "..", "backend"))
import demo  # noqa: E402
import dossiers as D  # noqa: E402

URL = os.environ.get("MARS_TEST_DATABASE_URL", "postgresql://mars:mars@localhost:55432/mars")
INSTANT = datetime(2026, 10, 9, 21, tzinfo=timezone.utc)
BOITE = (-5.0, 47.6, 2.0, 51.25)                     # lon min, lat min, lon max, lat max (Manche et Bretagne nord)
MASQUE = demo.Masque("0123456789abcdef0123456789abcdef")
URGENCE = {3: "#ef6461", 2: "#f0a84b", 1: "#aab6bf"}
e = html.escape


# Projection équirectangulaire corrigée de la latitude moyenne

def projeteur(w: float, h: float, boite=BOITE):
    lo0, la0, lo1, la1 = boite
    k = math.cos(math.radians((la0 + la1) / 2))
    sx = w / ((lo1 - lo0) * k)
    sy = h / (la1 - la0)
    s = min(sx, sy)
    return lambda lon, lat: ((lon - lo0) * k * s, (la1 - lat) * s)


def chemin(coords, p) -> str:
    return "M" + "L".join(f"{x:.1f},{y:.1f}" for x, y in (p(*c[:2]) for c in coords))


def geometries(conn, sql, args):
    return [json.loads(r[0]) if isinstance(r[0], str) else r[0] for r in conn.execute(sql, args).fetchall()]


def ajuster(boite, w, h):
    """Emprise étendue en latitude pour remplir le cadre (projection corrigée de la latitude moyenne)."""
    lo0, la0, lo1, la1 = boite
    k = math.cos(math.radians((la0 + la1) / 2))
    voulu = (lo1 - lo0) * k * h / w
    c = (la0 + la1) / 2
    if voulu > la1 - la0:
        return (lo0, c - voulu / 2, lo1, c + voulu / 2)
    larg = (la1 - la0) * w / h / k
    cl = (lo0 + lo1) / 2
    return (cl - larg / 2, la0, cl + larg / 2, la1)


def carte(conn, w, h, dossiers_, boite=BOITE, trace=None, accent=None, poussiere=True) -> str:
    boite = ajuster(boite, w, h)
    p = projeteur(w, h, boite)
    env = "ST_MakeEnvelope(%s, %s, %s, %s, 4326)"
    out = [f'<svg viewBox="0 0 {w} {h}" class="carte" xmlns="http://www.w3.org/2000/svg">',
           f'<rect width="{w}" height="{h}" fill="#101820"/>']
    # Terre : morceaux subdivisés de `land` réunis (sinon leurs coutures se voient), puis simplifiés
    for g in geometries(conn, f"""SELECT ST_AsGeoJSON(ST_SimplifyPreserveTopology(ST_Union(ST_Intersection(geom::geometry, {env})),
                                         0.003), 4) FROM land WHERE ST_Intersects(geom::geometry, {env})""", (*boite, *boite)):
        if g is None:
            continue
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
        for poly in polys:
            out.append(f'<path d="{"".join(chemin(r, p) + "Z" for r in poly)}" fill="#1b252e" fill-rule="evenodd" '
                       'stroke="#2c3946" stroke-width="0.7"/>')
    for g in geometries(conn, f"""SELECT ST_AsGeoJSON(ST_Simplify(ST_Intersection(geom::geometry, {env}), 0.003), 4)
                                  FROM infrastructure WHERE ST_Intersects(geom::geometry, {env})""", (*boite, *boite)):
        lignes = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"] if g["type"] == "MultiLineString" else []
        for ln in lignes:
            out.append(f'<path d="{chemin(ln, p)}" fill="none" stroke="#3a4a58" stroke-width="0.5" opacity="0.35"/>')
    for nom, lon, lat in (("Brest", -4.49, 48.39), ("Cherbourg", -1.62, 49.64), ("Le Havre", 0.11, 49.49),
                          ("Calais", 1.86, 50.95), ("Portsmouth", -1.09, 50.80), ("Plymouth", -4.14, 50.37)):
        if boite[0] < lon < boite[2] and boite[1] < lat < boite[3]:
            x, y = p(lon, lat)
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2" fill="#7c8b97"/><text x="{x + 5:.1f}" y="{y - 4:.1f}" '
                       f'class="lieu">{nom}</text>')
    if poussiere:
        for lon, lat in conn.execute(
                f"""SELECT DISTINCT ON (vessel_id) ST_X(geom::geometry), ST_Y(geom::geometry) FROM positions
                    WHERE ts BETWEEN %s AND %s AND ST_Intersects(geom::geometry, {env}) ORDER BY vessel_id, ts DESC""",
                (INSTANT - timedelta(minutes=30), INSTANT, *boite)).fetchall():
            x, y = p(lon, lat)
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6" fill="#55636f" opacity="0.75"/>')
    if trace:
        for k, seg in enumerate(trace):
            pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (p(lo, la) for lo, la in seg["points"]))
            out.append(f'<polyline points="{pts}" fill="none" stroke="{seg["couleur"]}" stroke-width="1.6" '
                       f'stroke-dasharray="{seg.get("tirets", "")}" opacity="0.95"/>')
    for d in dossiers_:
        x, y = p(d.lon, d.lat)
        c = URGENCE[d.niveau]
        r = {3: 9, 2: 7, 1: 4.5}[d.niveau]
        if d.cle.startswith("point"):
            out.append(f'<rect x="{x - r * .7:.1f}" y="{y - r * .7:.1f}" width="{r * 1.4:.1f}" height="{r * 1.4:.1f}" '
                       f'transform="rotate(45 {x:.1f} {y:.1f})" fill="none" stroke="{c}" stroke-width="1.6"/>')
        else:
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{c}" fill-opacity="{0.25 if d.niveau > 1 else 0}" '
                       f'stroke="{c}" stroke-width="{2 if d.niveau > 1 else 1.2}"/>')
        if d.niveau >= 2 or (accent and d.cle == accent):
            out.append(f'<text x="{x + r + 4:.1f}" y="{y + 4:.1f}" class="etiquette">{e(d.nom)}</text>')
    if accent:
        for d in dossiers_:
            if d.cle == accent:
                x, y = p(d.lon, d.lat)
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="16" fill="none" stroke="#4fb6c8" stroke-width="1.2"/>')
    out.append("</svg>")
    return "".join(out)


# Icônes : le type d'événement passe par l'icône, jamais par la couleur

ICONES = {
    "WATCHLIST": '<path d="M8 1.5 13 3.5v4c0 3.2-2.2 5.6-5 7-2.8-1.4-5-3.8-5-7v-4z"/>',
    "AIS_GAP": '<path d="M2 6.5a9 9 0 0 1 12 0M4.3 9a5.6 5.6 0 0 1 7.4 0M8 12h.01M2 2l12 12"/>',
    "DARK_SHIP": '<circle cx="8" cy="8" r="6"/><circle cx="8" cy="8" r="2.4"/><path d="M8 8l4.2-4.2"/>',
    "VIIRS": '<path d="M12.5 9.5A5 5 0 1 1 6.5 3.5a4 4 0 0 0 6 6z"/>',
    "AIS_UNCONFIRMED": '<path d="M1.5 8s2.4-4.5 6.5-4.5S14.5 8 14.5 8 12.1 12.5 8 12.5 1.5 8 1.5 8z"/><path d="M2 2l12 12"/>',
    "RENDEZVOUS": '<path d="M2 11h5l2-6h5M10 3l4 2-4 2"/>',
    "IDENTITY_CHANGE": '<rect x="2" y="3.5" width="12" height="9" rx="1.5"/><path d="M5 7h3M5 9.5h6"/>',
    "SATELLITE": '<path d="M4 4l3 3M9 9l3 3M7 7l2 2M2.5 6.5l4-4 2 2-4 4zM9.5 13.5l4-4-2-2-4 4z"/>',
}


def icone(typ: str, taille: int = 14, couleur: str = "currentColor") -> str:
    return (f'<svg width="{taille}" height="{taille}" viewBox="0 0 16 16" fill="none" stroke="{couleur}" '
            f'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">{ICONES.get(typ, ICONES["SATELLITE"])}</svg>')


def type_affiche(s: D.Signal) -> str:
    return "VIIRS" if s.type == "DARK_SHIP" and "Lumière" in s.fragment else s.type


CSS = """
:root{--fond:#0e1419;--panneau:#141c23;--sur:#1b252e;--filet:#26323d;--texte:#e6ecf0;--second:#7c8b97;--discret:#4c5a66;
--signal:#4fb6c8;--u3:#ef6461;--u2:#f0a84b;--u1:#aab6bf}
*{box-sizing:border-box}body{margin:0;background:var(--fond);color:var(--texte);font:13.5px/1.45 "IBM Plex Sans",system-ui,sans-serif;
font-variant-numeric:tabular-nums}
.ecran{width:1440px;height:900px;display:grid;grid-template-rows:40px 1fr;overflow:hidden;position:relative;border:1px solid var(--filet)}
.barre{display:flex;align-items:center;gap:18px;padding:0 16px;border-bottom:1px solid var(--filet);background:var(--panneau);font-size:12.5px}
.marque{font-weight:600;letter-spacing:.04em}.espaces{display:flex;gap:4px}.espaces a{color:var(--second);text-decoration:none;
padding:5px 10px;border-radius:5px}.espaces a.on{background:var(--sur);color:var(--texte)}
.temps{margin-left:auto;color:var(--second)}.temps b{color:var(--texte);font-weight:500}.cmdk{border:1px solid var(--filet);
border-radius:5px;padding:3px 9px;color:var(--second)}.systeme{display:flex;align-items:center;gap:6px;color:var(--second)}
.pastille{width:7px;height:7px;border-radius:50%;background:#3f8f62;display:inline-block}
.corps{display:grid;grid-template-columns:380px 1fr;min-height:0}
.liste{border-right:1px solid var(--filet);background:var(--panneau);display:flex;flex-direction:column;min-height:0}
.tete{padding:16px 18px 10px;display:flex;align-items:baseline;justify-content:space-between}
.tete h1{margin:0;font-size:22px;font-weight:600}.tete h1 small{font-size:13px;color:var(--second);font-weight:400;margin-left:6px}
.raccourcis{color:var(--discret);font-size:11.5px}.raccourcis kbd,.touches kbd{font:inherit;border:1px solid var(--filet);
border-radius:3px;padding:0 4px;color:var(--second)}
.dossier{display:grid;grid-template-columns:4px 1fr auto;gap:0 12px;padding:11px 18px 11px 0;border-top:1px solid var(--filet);
cursor:default}.dossier .niv{border-radius:0 2px 2px 0}.dossier .nom{font-weight:600;font-size:14px}
.dossier .nom small{font-weight:400;color:var(--second);font-size:11px;border:1px solid var(--filet);border-radius:3px;padding:0 4px;margin-left:6px}
.dossier .pourquoi{color:#c9d2d8;font-size:12.5px;margin-top:2px}.dossier .icones{display:flex;gap:6px;color:var(--second);margin-top:5px}
.dossier .quand{color:var(--second);font-size:12px;text-align:right}.dossier.focus{background:var(--sur);outline:1px solid var(--signal)}
.noter{padding:10px 18px;color:var(--second);border-top:1px solid var(--filet);font-size:12.5px}
.vide{padding:40px 24px;text-align:center;color:var(--second)}.vide b{display:block;color:var(--texte);font-size:16px;font-weight:500;margin-bottom:6px}
.zone-carte{position:relative;min-height:0}.carte{width:100%;height:100%;display:block}
.lieu{fill:#7c8b97;font-size:11px}.etiquette{fill:#e6ecf0;font-size:11.5px;font-weight:500;paint-order:stroke;stroke:#0e1419;stroke-width:3px}
.vues{position:absolute;left:14px;top:14px;display:flex;gap:4px;background:rgba(20,28,35,.92);border:1px solid var(--filet);border-radius:6px;padding:3px}
.vues span{padding:4px 10px;border-radius:4px;color:var(--second);font-size:12px}.vues span.on{background:var(--sur);color:var(--texte)}
.legende{position:absolute;left:14px;bottom:14px;background:rgba(20,28,35,.92);border:1px solid var(--filet);border-radius:6px;
padding:8px 12px;font-size:11.5px;color:var(--second);display:flex;gap:14px;align-items:center}
.legende i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:-1px}
.enquete{display:grid;grid-template-columns:520px 1fr;min-height:0}.recit{background:var(--panneau);border-right:1px solid var(--filet);
display:flex;flex-direction:column;min-height:0}.recit .haut{padding:18px 22px 14px;border-bottom:1px solid var(--filet)}
.niveau{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;padding:2px 9px;border-radius:999px}
.recit h2{margin:8px 0 2px;font-size:22px}.recit .qui{color:var(--second);font-size:12.5px}.recit .phrase{font-size:15px;margin-top:10px;line-height:1.5}
.chrono{padding:14px 22px;overflow:hidden;flex:1}.fait{display:grid;grid-template-columns:62px 22px 1fr;gap:0 8px;padding:8px 0;position:relative}
.fait .h{color:var(--second);font-size:12px;padding-top:1px}.fait .t{font-size:13.5px}.fait .t em{color:var(--second);font-style:normal;font-size:12px;display:block}
.preuve{margin-top:6px;border:1px solid var(--filet);border-radius:6px;background:#0f161c;height:86px;display:flex;align-items:center;
justify-content:center;color:var(--discret);font-size:11.5px}
.gestes{display:flex;gap:8px;padding:12px 22px;border-top:1px solid var(--filet);background:var(--panneau)}
.geste{flex:1;border:1px solid var(--filet);border-radius:6px;padding:8px 10px;text-align:center;font-size:13px}
.geste kbd{font:inherit;color:var(--second);border:1px solid var(--filet);border-radius:3px;padding:0 4px;margin-left:6px;font-size:11px}
.details{padding:0 22px 10px;color:var(--second);font-size:12px}.details summary{cursor:pointer}
.alerte-systeme{position:absolute;left:50%;transform:translateX(-50%);top:52px;background:#2a1517;border:1px solid var(--u3);
color:#ffd9d6;border-radius:6px;padding:8px 14px;font-size:13px;z-index:3}
.grille{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;padding:18px;overflow:hidden}.carte-c{background:var(--panneau);
border:1px solid var(--filet);border-radius:8px;padding:14px 16px}.carte-c h3{margin:0 0 8px;font-size:14px}.carte-c p{margin:3px 0;color:var(--second);font-size:12.5px}
.carte-c .ok{color:#7fbf9a}.carte-c .att{color:var(--u2)}
.pop{position:absolute;background:var(--sur);border:1px solid var(--filet);border-radius:8px;padding:12px 14px;width:300px;box-shadow:0 10px 30px rgba(0,0,0,.5)}
.pop h4{margin:0 0 8px;font-size:13px}.pop .m{display:flex;justify-content:space-between;padding:6px 8px;border-radius:5px;color:#c9d2d8}
.pop .m.on{background:#24313c;color:var(--texte)}.touches{position:absolute;right:14px;bottom:14px;background:rgba(20,28,35,.95);
border:1px solid var(--filet);border-radius:6px;padding:10px 14px;font-size:12px;color:var(--second);line-height:1.9}
.page{max-width:1100px;margin:30px auto;padding:0 20px}.page a{color:var(--signal)}table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid var(--filet);padding:6px 8px;text-align:left;font-size:13px;vertical-align:top}th{color:var(--second);font-weight:500}
"""


def page(titre: str, corps: str) -> str:
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>{e(titre)}</title>'
            '<link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">'
            f'<style>{CSS}</style></head><body>{corps}</body></html>')


def barre(espace: str, systeme: str | None = None) -> str:
    sysm = (f'<span class="systeme" style="color:var(--u3)"><span class="pastille" style="background:var(--u3)"></span>{e(systeme)}</span>'
            if systeme else '<span class="systeme" title="Système : tout va bien"><span class="pastille"></span></span>')
    on = lambda x: ' class="on"' if x == espace else ""                  # noqa: E731
    return (f'<div class="barre"><span class="marque">MARS C2</span><nav class="espaces"><a{on("veille")}>Veille</a>'
            f'<a{on("enquete")}>Enquête</a><a{on("coulisses")}>Coulisses</a></nav><span class="temps">Manche · '
            f'<b>jeu 09/10 21:00</b> · plage 24 h</span><span class="cmdk">Rechercher ⌘K</span>{sysm}</div>')


def depuis(t: datetime) -> str:
    m = (INSTANT - t).total_seconds() / 60
    return f"il y a {round(m)} min" if m < 90 else f"il y a {round(m / 60)} h"


def ligne(d: D.Dossier, focus=False) -> str:
    types = list(dict.fromkeys(type_affiche(s) for s in d.signaux))[:3]
    return (f'<div class="dossier{" focus" if focus else ""}"><div class="niv" style="background:{URGENCE[d.niveau] if d.niveau > 1 else "transparent"}"></div>'
            f'<div><div class="nom">{e(d.nom)}{f"<small>{e(d.pavillon)}</small>" if d.pavillon else ""}</div>'
            f'<div class="pourquoi">{e(d.phrase)}</div><div class="icones">{"".join(icone(t) for t in types)}</div></div>'
            f'<div class="quand">{depuis(d.dernier)}</div></div>')


def veille(conn, ds, focus=None, extra="", systeme=None, vide=False) -> str:
    examiner = [d for d in ds if d.niveau >= 2]
    noter = [d for d in ds if d.niveau == 1]
    if vide:
        liste = ('<div class="vide"><b>Aucun navire inhabituel depuis 6 h</b>196 navires reçus en Manche, AIS continu, '
                 'dernière nuit VIIRS traitée à 08:40.</div>')
        examiner, tete = [], "Rien à examiner"
    else:
        liste = "".join(ligne(d, focus == d.cle) for d in examiner)
        liste += f'<div class="noter">› {len(noter)} à noter</div>'
        tete = f'{len(examiner)}<small>à examiner</small>'
    return (f'<div class="ecran">{barre("veille", systeme)}<div class="corps"><aside class="liste"><div class="tete"><h1>{tete}</h1>'
            '<span class="raccourcis"><kbd>J</kbd> <kbd>K</kbd> suivant</span></div>'
            f'{liste}</aside><main class="zone-carte">{carte(conn, 1060, 860, [] if vide else ds, accent=focus)}'
            '<div class="vues"><span class="on">Veille</span><span>Trafic complet</span><span>Satellites</span><span>Couches</span></div>'
            '<div class="legende"><span><i style="background:var(--u3)"></i>Urgent</span><span><i style="background:var(--u2)"></i>'
            'À examiner</span><span><i style="border:1px solid var(--u1)"></i>À noter</span><span><i style="background:#55636f;width:4px;height:4px"></i>'
            'autres navires</span></div></main></div>'
            f'{extra}</div>')


def enquete(conn, d: D.Dossier, vid: int | None) -> str:
    sigs = sorted(d.signaux, key=lambda s: s.t)
    faits = []
    for s in sigs:
        det = ""
        if s.type == "AIS_GAP":
            det = "<em>Dernier message 07:58, en route à 11 nœuds ; aucune réapparition depuis</em>"
        elif s.type == "WATCHLIST":
            det = "<em>Union européenne, Royaume Uni : sanctions ; reconnu par son MMSI</em>"
        elif s.type == "DARK_SHIP" and type_affiche(s) == "VIIRS":
            det = ('<em>Nuit du 08 au 09/10, satellite NOAA 20, faible intensité, lune à 41 % ; aucun navire AIS à moins '
                   'de 9 km alors que 3 sont reçus à 30 km</em><div class="preuve">image de nuit de 30 km, lumière au centre, '
                   'positions AIS superposées</div><em style="margin-top:6px">À confirmer : la petite pêche n\'a pas '
                   'l\'obligation d\'AIS</em>')
        elif s.type == "DARK_SHIP":
            det = ('<em>Passage Sentinel 1 de 18:12, écho de 182 m à 4,1 km de la position estimée</em>'
                   '<div class="preuve">vignette radar de 3 km, écho au centre, aucune position AIS</div>')
        faits.append(f'<div class="fait"><div class="h">{s.t:%d/%m %H:%M}</div><div>{icone(type_affiche(s), 16, "#c9d2d8")}</div>'
                     f'<div class="t">{e(s.fragment)}{det}</div></div>')
    if vid:
        faits.insert(2, '<div class="fait"><div class="h">09/10 08:03</div><div>' + icone("SATELLITE", 16, "#c9d2d8") +
                     '</div><div class="t">Vérification satellite demandée<em>Prochain passage radar 18:12, cercle de 9 km</em></div></div>')
    trace = None
    if vid:
        pts = conn.execute("SELECT ST_X(geom::geometry), ST_Y(geom::geometry), ts FROM positions WHERE vessel_id = %s "
                           "AND ts <= %s ORDER BY ts", (vid, INSTANT)).fetchall()
        suivi = [(lo, la) for lo, la, _t in pts]
        trace = [{"points": suivi, "couleur": "#c9d2d8"},
                 {"points": [suivi[-1], (d.lon, d.lat)], "couleur": URGENCE[3], "tirets": "4 4"}] if suivi else None
    lo, la = d.lon, d.lat
    boite = (lo - 1.6, la - 0.75, lo + 1.6, la + 0.75)
    n = D.NIVEAUX[d.niveau]
    return (f'<div class="ecran">{barre("enquete")}<div class="enquete"><section class="recit"><div class="haut">'
            f'<span class="niveau" style="background:{URGENCE[d.niveau]}22;color:{URGENCE[d.niveau]}">● {n}</span>'
            f'<h2>{e(d.nom)}</h2><div class="qui">{"Pétrolier, 150 m, pavillon " + e(d.pavillon or "") if vid else "Point en mer, sans navire identifié"}</div>'
            f'<div class="phrase">{e(d.phrase)}.</div></div><div class="chrono">{"".join(faits)}</div>'
            '<details class="details"><summary>Détail technique</summary>scores, fenêtres lues, versions des règles, empreinte du panneau</details>'
            '<div class="gestes"><span class="geste">Classer<kbd>C</kbd></span><span class="geste">Suivre<kbd>S</kbd></span>'
            '<span class="geste">Vérifier par satellite<kbd>V</kbd></span></div></section>'
            f'<main class="zone-carte">{carte(conn, 920, 860, [d], boite=boite, trace=trace, accent=d.cle)}'
            + ('<div class="legende"><span>trait plein : route AIS</span><span style="color:var(--u3)">tirets : silence AIS '
               'jusqu\'à l\'écho radar</span></div>' if vid else '') + '</main></div></div>')


def coulisses() -> str:
    cartes = [
        ("Système", ['<span class="ok">●</span> Collecte AIS : fichier il y a 40 s', '<span class="ok">●</span> Ingestion : retard 1 min',
                     '<span class="ok">●</span> Disque : 41 % libres', 'Versions : 04b8377 partout']),
        ("Capteurs et dépenses", ['Sentinel 1 <span class="att">en pause</span> depuis le 21/10', 'Sentinel 2 en pause',
                                  'VIIRS en service, tip and cue en service', 'Budget Sentinel : 0,19 € sur 8 €']),
        ("Règles", ["Coupure AIS : observation", "Rendez vous, identité, listes : actifs",
                    "Faux positifs sur 7 jours : 3 sur 41 décisions", "Version des règles 2026.10.40"]),
        ("Satellites", ["Prochain passage : S1A demain 06:00", "Dernière nuit VIIRS : 08/10, 3 détections",
                        "Analyses, passages, travailleurs, coûts"]),
        ("Notifications", ["Heures calmes 23:00 à 07:00", "Alertes élevées et plus", "Notification de test"]),
        ("Zones de vigilance", ["1 zone active : rail d'Ouessant (renforcé)", "Priorité aux navires suivis : oui"]),
        ("Démonstration", ["Masquage des navires", "Rejeu tel qu'il était", "Scénarios enregistrés : 2"]),
        ("Couches détaillées", ["Infrastructures par type, couverture, réception fiable", "Mouillages, passages, détections"]),
        ("Journal", ["Décisions des opérateurs et actions du panneau", "Liens vers Grafana et les traces"]),
    ]
    g = "".join(f'<div class="carte-c"><h3>{t}</h3>{"".join(f"<p>{x}</p>" for x in ls)}</div>' for t, ls in cartes)
    return f'<div class="ecran">{barre("coulisses")}<div class="grille">{g}</div></div>'


def triage(conn, ds) -> str:
    focus = [d for d in ds if d.niveau >= 2][1]
    pop = ('<div class="pop" style="left:392px;top:205px"><h4>Classer ' + e(focus.nom) + ' : motif</h4>'
           '<div class="m on">Faux positif <kbd>1</kbd></div><div class="m">Activité légitime <kbd>2</kbd></div>'
           '<div class="m">Doublon <kbd>3</kbd></div><div style="color:var(--second);font-size:11.5px;margin-top:8px">'
           'Appliqué à ses 1 alertes ouvertes, journalisé alerte par alerte</div></div>'
           '<div class="touches"><kbd>J</kbd> <kbd>K</kbd> suivant, précédent · <kbd>Entrée</kbd> ouvrir le dossier<br>'
           '<kbd>C</kbd> classer · <kbd>S</kbd> suivre · <kbd>V</kbd> vérifier par satellite · <kbd>?</kbd> aide</div>')
    return veille(conn, ds, focus=focus.cle, extra=pop)


def main():
    conn = psycopg.connect(URL)
    ds = D.dossiers(conn, INSTANT, nom=MASQUE.nom)
    navire = ds[0]
    point = next(d for d in ds if d.cle.startswith("point"))
    pages = {
        "veille.html": ("Veille", veille(conn, ds)),
        "dossier_navire.html": ("Dossier d'un navire", enquete(conn, navire, int(navire.cle.split(":")[1]))),
        "dossier_point.html": ("Dossier point", enquete(conn, point, None)),
        "coulisses.html": ("Coulisses", coulisses()),
        "calme.html": ("État calme", veille(conn, ds, vide=True)),
        "degrade.html": ("État dégradé", veille(conn, ds, systeme="AIS coupé depuis 12 min",
                                                extra='<div class="alerte-systeme">AIS coupé depuis 12 min : la veille est '
                                                      'aveugle. Les dossiers ci dessous datent d\'avant 20:48.</div>')),
        "triage.html": ("Triage au clavier", triage(conn, ds)),
    }
    for nom, (titre, corps) in pages.items():
        open(os.path.join(ICI, nom), "w").write(page(f"MARS C2, maquette : {titre}", corps))
    lignes = "".join(f"<tr><td>{D.NIVEAUX[d.niveau]}</td><td>{e(d.nom)}</td><td>{e(d.phrase)}</td>"
                     f"<td>{', '.join(s.type for s in d.signaux)}</td><td>{'<br>'.join(e(c) for c in d.contributions)}</td></tr>"
                     for d in ds)
    index = ('<div class="page"><h1>MARS C2, maquettes de la refonte (lot U, phase 1)</h1><p>Données de la base de test '
             '(Manche, 09/10/2026 à 21:00, 503 navires réels, alertes du moteur inchangé), navires masqués par le mode '
             'démonstration. Voir docs/ux_refonte.md.</p><ul>'
             + "".join(f'<li><a href="{n}">{t}</a></li>' for n, (t, _c) in pages.items())
             + '</ul><h2>Dossiers calculés par le modèle</h2><table><tr><th>Niveau</th><th>Dossier</th><th>Phrase</th>'
             f'<th>Alertes</th><th>Contributions au niveau</th></tr>{lignes}</table></div>')
    open(os.path.join(ICI, "index.html"), "w").write(page("MARS C2, maquettes", index))
    print(len(ds), "dossiers ;", ", ".join(pages))


if __name__ == "__main__":
    main()
