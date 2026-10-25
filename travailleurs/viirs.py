"""Travailleur VIIRS : détection des navires éclairés la nuit (bande Day/Night de Suomi NPP, NOAA 20 et NOAA 21).

Exécuté sur une instance éphémère, dans l'image publique ghcr.io/allenai/vessel-detection-viirs (modèle et chaîne de
traitement d'allenai, licence Apache 2.0, code dans /src). Le serveur de MARS C2 transmet ce fichier et la tâche par
cloud-init (mars/travailleurs.py) ; ce script n'utilise que la bibliothèque standard, requests et le code de /src.

Pour chaque granule (fichier DNB, fichier de géolocalisation et masque des nuages CLDMSK_L2 de la même minute) :
téléchargement avec le jeton Earthdata, fichier vérifié (taille et signature HDF5, sinon motif explicite : licence LANCE non acceptée, jeton refusé, ou code HTTP,
type de contenu et premiers octets), détection, détections réduites à nos régions, fichiers effacés aussitôt. Données
inaccessibles (licence, jeton) ou premier granule en échec : arrêt aussitôt, motif renvoyé au serveur. Le masque des
nuages (lot viirs-masque, 25/10/2026) n'arrête jamais rien : sans lui, le granule part avec « masque des nuages absent »
et son motif, et le serveur ne conclut à rien sous une lune forte (au dessus de 80 %, allenai éteint toute la scène
quand le masque manque). Pour chaque
détection, une vignette PNG d'environ 30 km de côté, tirée de l'image déjà en mémoire (marqueur, échelle, nord, date,
satellite, lune, ciel clair), avec sa transformation longitude, latitude vers pixels pour superposer l'AIS ; elle part
avec les résultats, le serveur la range sur R2. Puis envoi au serveur, par son réseau privé, des
détections, de l'emprise de chaque granule et des mesures (durées, mémoire, volume téléchargé). Une granule en échec
n'arrête pas les autres. Le serveur détruit l'instance dès réception (ou à l'échéance de sa durée de vie).

Tâche et jeton Earthdata remis par le réseau privé, une seule fois, au travailleur qui présente le jeton de son
exécution (backend/travailleurs.py, lot Z1) : le jeton n'est ni dans le cloud-init, ni dans l'environnement du conteneur,
ni dans un journal ; il reste en mémoire de ce script. Variables : MARS_TACHE (fichier JSON du lancement : adresse de
retour et jeton d'exécution), EARTHDATA_TOKEN pour l'essai local seulement. Essai local sur des fichiers déjà présents :
    python3 viirs.py --local tache.json   (granules avec « dnb_path », « geo_path » et, facultatif, « masque_path »,
                                           résultat sur la sortie standard)
"""
import base64
import io
import json
import math
import os
import resource
import sys
import tempfile
import time
import traceback

sys.path.insert(0, "/src")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # otel_mini, déposé à côté par cloud-init

import otel_mini  # noqa: E402
import requests  # noqa: E402

TIMEOUT_S = 300
HDF5 = b"\x89HDF\r\n\x1a\n"           # signature d'un fichier HDF5 (NetCDF 4) ; « CDF » pour le NetCDF classique
TAILLE_MIN = 1_000_000                   # un fichier DNB ou de géolocalisation fait 40 à 65 Mo ; une page HTML, 10 Ko


class DonneesInaccessibles(Exception):
    """Licence non acceptée ou jeton refusé : les autres granules échoueraient de même, le travailleur s'arrête."""


class FichierInvalide(Exception):
    """Réponse ou fichier inattendu pour une granule."""


def check_download(status: int, content_type: str, urls: list[str], first: bytes, size: int | None = None):
    """Vérifie un téléchargement : redirections suivies (`urls`), code HTTP, type de contenu, premiers octets et, une
    fois le fichier écrit, sa taille. Lève DonneesInaccessibles ou FichierInvalide avec un motif lisible."""
    # Ordre constaté sur la NASA : sans jeton valide, la redirection vers /profiles/licenses se poursuit vers la page
    # de connexion (/oauth/login, /oauth/authorize) ; avec un jeton valide mais sans licence, elle s'y arrête.
    if status in (401, 403) or any("/oauth/" in u for u in urls):
        raise DonneesInaccessibles(f"accès refusé par la NASA (HTTP {status}, redirection vers la connexion Earthdata) : "
                                   "jeton EARTHDATA_TOKEN absent, invalide ou expiré")
    if any("/profiles/licenses" in u for u in urls):
        raise DonneesInaccessibles("licence LANCE non acceptée sur le compte Earthdata (redirection vers "
                                   "/profiles/licenses) : l'accepter sur urs.earthdata.nasa.gov")
    if status != 200 or not (first.startswith(HDF5) or first.startswith(b"CDF")):
        raise FichierInvalide(f"réponse inattendue : HTTP {status}, type {content_type or 'inconnu'}, "
                              f"premiers octets {first[:60]!r}")
    if size is not None and size < TAILLE_MIN:
        raise FichierInvalide(f"fichier tronqué : {size} octets (au moins {TAILLE_MIN} attendus)")


def rss_mo() -> float:
    """Mémoire maximale du processus depuis son démarrage, en Mo (ru_maxrss est en kilooctets sous Linux)."""
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def download(url: str, dest: str, token: str) -> int:
    with requests.get(url, headers={"Authorization": f"Bearer {token}"}, stream=True, timeout=TIMEOUT_S) as r:
        urls = [h.headers.get("location", "") for h in r.history] + [h.url for h in r.history] + [r.url]
        chunks = r.iter_content(1 << 20)
        first = next(chunks, b"")
        check_download(r.status_code, r.headers.get("content-type", ""), urls, first)
        n = len(first)
        with open(dest, "wb") as f:
            f.write(first)
            for chunk in chunks:
                f.write(chunk)
                n += len(chunk)
    check_download(200, "", [], first, n)
    return n


# Masque des nuages : couche lue par allenai (preprocessor.CLOUD_PATH), confiance de ciel clair de 0 à 1 ; au dessus de
# 0,99 le pixel compte comme clair (utils.clear_sky_mask)
NUAGES = "/geophysical_data/Clear_Sky_Confidence"
CIEL_CLAIR = 0.99


def verifier_masque(path: str) -> float:
    """Ouvre le masque comme allenai et lit sa couche de ciel clair. allenai remplace en silence un masque illisible par
    des zéros (ciel couvert partout) : ici l'échec est nommé. Retourne la part des pixels de ciel clair."""
    import netCDF4
    import numpy as np
    try:
        with netCDF4.Dataset(path) as ds:
            a = np.ma.filled(ds[NUAGES][:].astype("float64"), np.nan)
    except (OSError, IndexError, KeyError) as e:
        raise FichierInvalide(f"masque des nuages illisible ({NUAGES}) : {type(e).__name__}: {e}"[:300])
    if a.size == 0 or not np.isfinite(a).any():
        raise FichierInvalide("masque des nuages vide")
    return float(np.mean(np.nan_to_num(a, nan=0.0) > CIEL_CLAIR))


def lire_masque(urls: list, dest: str, token: str, tr) -> dict:
    """Premier masque lisible parmi les adresses du serveur (temps quasi réel de SIPS, puis produit standard de LAADS),
    avec le même jeton Earthdata. Jamais bloquant : sans masque, l'état « absent » et le motif de chaque essai."""
    essais = []
    for u in urls:
        hote = u.split("/")[2] if "//" in u else u
        with tr.span("GET masque des nuages", **{"peer.service": hote, "viirs.fichier": "masque_nuages"}) as sm:
            try:
                n = download(u, dest, token)
                part = verifier_masque(dest)
                sm.set(**{"viirs.octets": n, "viirs.ciel_clair_part": round(part, 3)})
                return {"etat": "fourni", "source": hote, "fichier": u.rsplit("/", 1)[-1], "octets": n,
                        "ciel_clair_part": round(part, 3), "essais": essais}
            except Exception as e:                     # licence, jeton, fichier tronqué : le suivant, ou absent
                motif = str(e)[:300] if isinstance(e, (DonneesInaccessibles, FichierInvalide)) else f"{type(e).__name__}: {e}"[:300]
                sm.erreur(motif)
                essais.append(f"{hote} : {motif}")
    motif = "; ".join(essais) if essais else "aucun masque publié pour ce granule"
    return {"etat": "absent", "motif": "masque des nuages absent : " + motif, "essais": essais}


VIGNETTE_PX = 40           # 40 pixels DNB de 750 m : 30 km de côté
VIGNETTE_ZOOM = 6          # 240 pixels à l'écran
SATELLITES = {"Suomi-NPP": "Suomi NPP", "NPP": "Suomi NPP", "JPSS-1": "NOAA 20", "NOAA-20": "NOAA 20",
              "JPSS-2": "NOAA 21", "NOAA-21": "NOAA 21"}


def _hav_m(lon1, lat1, lon2, lat2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def vignette(radiance, lats, lons, row: int, col: int, ascending: bool, legend: dict) -> tuple[bytes, dict]:
    """Vignette PNG centrée sur la détection (ligne, colonne de la granule) et sa géoréférence : coefficients
    x = a·lon + b·lat + c et y = d·lon + e·lat + f en pixels de la vignette (moindres carrés sur la géolocalisation
    du découpage), mètres par pixel. Orientée à peu près nord en haut (rotation d'un demi tour en orbite
    ascendante, comme le fait allenai). `legend` : satellite, date, lune, ciel clair."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    h = VIGNETTE_PX // 2
    r0, c0 = max(0, row - h), max(0, col - h)
    r1, c1 = min(radiance.shape[0], row + h), min(radiance.shape[1], col + h)
    img = np.array(radiance[r0:r1, c0:c1], dtype="float64")
    la, lo = np.array(lats[r0:r1, c0:c1], dtype="float64"), np.array(lons[r0:r1, c0:c1], dtype="float64")
    cy, cx = row - r0, col - c0
    if ascending:
        img, la, lo = img[::-1, ::-1], la[::-1, ::-1], lo[::-1, ::-1]
        cy, cx = img.shape[0] - 1 - cy, img.shape[1] - 1 - cx
    z = VIGNETTE_ZOOM
    # Étirement logarithmique de 0 à 100 nW/cm²/sr : les lumières ressortent sans écraser le fond
    v = np.nan_to_num(np.log1p(np.clip(img, 0, 100)) / math.log1p(100) * 255).astype("uint8")
    pic = Image.fromarray(v).resize((v.shape[1] * z, v.shape[0] * z), Image.NEAREST).convert("RGB")
    W, H = pic.size
    # Géoréférence : pixels de la vignette (centres des pixels DNB agrandis) en fonction de lon, lat
    ii, jj = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    ok = np.isfinite(la) & np.isfinite(lo)
    A = np.c_[lo[ok], la[ok], np.ones(ok.sum())]
    cxy = np.linalg.lstsq(A, (jj[ok] + 0.5) * z, rcond=None)[0]
    cyy = np.linalg.lstsq(A, (ii[ok] + 0.5) * z, rcond=None)[0]
    mid = img.shape[0] // 2
    mpp = _hav_m(lo[mid, 0], la[mid, 0], lo[mid, -1], la[mid, -1]) / max(1, (img.shape[1] - 1) * z)
    d = ImageDraw.Draw(pic)
    font = ImageFont.load_default()
    px, py = (cx + 0.5) * z, (cy + 0.5) * z
    d.ellipse([px - 11, py - 11, px + 11, py + 11], outline=(232, 91, 199), width=2)      # marqueur, sans masquer le point
    bar = 10_000 / mpp if mpp > 0 else 0                                                 # échelle de 10 km
    if 0 < bar < W - 20:
        d.rectangle([8, H - 22, 8 + bar, H - 19], fill=(230, 236, 240))
        d.text((8, H - 16), "10 km", fill=(230, 236, 240), font=font)
    d.rectangle([0, 0, W, 13], fill=(14, 20, 25))
    # Nord : direction de la latitude croissante dans la vignette, flèche sous le bandeau
    nx, ny = cxy[1], cyy[1]
    n = math.hypot(nx, ny) or 1
    ax, ay = W - 16, 40
    d.line([ax, ay, ax + 14 * nx / n, ay + 14 * ny / n], fill=(230, 236, 240), width=2)
    d.text((ax + 20 * nx / n - 3, ay + 20 * ny / n - 5), "N", fill=(230, 236, 240), font=font)
    d.text((3, 1), f"{legend.get('satellite', '')}  {legend.get('date', '')}", fill=(230, 236, 240), font=font)
    lune, ciel = legend.get("lune"), legend.get("ciel_clair")
    txt = f"Lune {lune:.0f} %" if lune is not None else ""
    if ciel is not None:
        txt += f"  Ciel clair {ciel:.2f}".replace(".", ",")
    tw = d.textlength(txt, font=font)
    d.rectangle([W - tw - 6, H - 14, W, H], fill=(14, 20, 25))
    d.text((W - tw - 3, H - 13), txt, fill=(230, 236, 240), font=font)
    buf = io.BytesIO()
    pic.save(buf, "PNG", optimize=True)
    geo = {"x": [round(float(c), 6) for c in cxy], "y": [round(float(c), 6) for c in cyy], "largeur": W, "hauteur": H,
           "m_par_px": round(mpp, 2)}
    return buf.getvalue(), geo


def in_regions(lon: float, lat: float, boxes: list) -> bool:
    return any(x0 <= lon <= x1 and y0 <= lat <= y1 for x0, y0, x1, y1 in boxes)


def finite(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def detect(vvd, dnb_path: str, geo_path: str, tmp: str, boxes: list, phys_path: str | None = None) -> dict:
    import utils                                     # code d'allenai (/src)
    # phys_path : masque des nuages, que le modèle attend (sans lui, ciel couvert partout pour allenai)
    all_det, image, ds, status = vvd.run_pipeline(dnb_path, geo_path, tmp, phys_path)
    chips = utils.get_chips(image, all_det["vessel_detections"], ds)
    start, end = utils.get_acquisition_time(ds)
    sat = utils.get_provider_name(ds)
    ascending = ds["dnb"]["metadata"].get("startDirection") == "Ascending"
    dets = []
    for c in chips.values():
        lon, lat = finite(c["longitude"]), finite(c["latitude"])
        if lon is None or lat is None or not in_regions(lon, lat, boxes):
            continue
        det = {"lon": round(lon, 5), "lat": round(lat, 5), "nanowatts": finite(c["max_nanowatts"]),
               "orientation": finite(c["orientation"]), "lune": finite(c["moonlight_illumination"]),
               "ciel_clair": finite(c["clear_sky_confidence"])}
        try:                                         # une vignette en échec n'empêche pas la détection
            png, geo = vignette(image, ds["latitude"], ds["longitude"], int(c["coords_pix"][0]), int(c["coords_pix"][1]),
                                ascending, {"satellite": SATELLITES.get(sat, sat), "date": f"{str(start)[8:10]}/{str(start)[5:7]}/{str(start)[:4]} {str(start)[11:16]} UTC",
                                            "lune": det["lune"], "ciel_clair": det["ciel_clair"]})
            det["vignette"], det["vignette_geo"] = base64.b64encode(png).decode(), geo
        except Exception as e:
            det["vignette_erreur"] = f"{type(e).__name__}: {e}"[:200]
        dets.append(det)
    return {"statut": status, "debut": str(start), "fin": str(end), "plateforme": utils.get_provider_name(ds),
            "lune_moyenne": finite(utils.get_average_moonlight(ds)),
            "emprise": [[finite(x), finite(y)] for x, y in utils.get_frame_extents(ds)],
            "eclairs": int(all_det.get("lightning_count") or 0), "torcheres": int(all_det.get("gas_flare_count") or 0),
            "detections": dets}


class Arret(Exception):
    """Tâche refusée ou injoignable : le travailleur s'arrête avec ce motif."""


def fetch_task(task: dict, traceparent: str | None = None) -> tuple[dict, str]:
    """Tâche remise par le réseau privé, une seule fois : paramètres (granules, régions) et jeton Earthdata, rendu à
    part pour ne jamais rejoindre la tâche ni un journal. Un refus (403, 409, 503) arrête le travailleur avec son motif."""
    headers = {"Authorization": "Bearer " + task["jeton"], "Content-Type": "application/json"}
    if traceparent:
        headers["traceparent"] = traceparent
    for k in range(3):
        try:
            r = requests.post(task["retour"] + "/etat", json={"etat": "tache"}, headers=headers, timeout=60)
        except requests.RequestException as e:
            print("tâche non reçue (" + type(e).__name__ + "), nouvel essai", flush=True)
            time.sleep(10 * (k + 1))
            continue
        if r.status_code != 200:
            raise Arret("tâche refusée par le serveur (HTTP " + str(r.status_code) + ") : " + r.text[:300])
        body = r.json()
        jeton = ((body.pop("identifiants", None) or {}).get("earthdata") or "")
        if not jeton:
            raise Arret("tâche reçue sans jeton Earthdata")
        return {**body, "retour": task["retour"], "jeton": task["jeton"]}, jeton
    raise Arret("tâche non reçue du serveur après trois essais (réseau privé)")


def run(task: dict, local: bool = False, tr: "otel_mini.Traceur | None" = None, token: str | None = None) -> dict:
    t0 = time.time()
    tr = tr or otel_mini.Traceur(None, None, None)
    with tr.span("viirs.chargement_modele"):
        from pipeline import VIIRSVesselDetection      # code d'allenai (/src)
        vvd = VIIRSVesselDetection()
    token = token if token is not None else os.environ.get("EARTHDATA_TOKEN", "")      # essai local seulement
    out, octets, stop = [], 0, None
    for k, g in enumerate(task["granules"]):
        r = {"dnb": g["dnb"], "satellite": g.get("satellite")}
        t = time.time()
        with tr.span("viirs.granule", **{"viirs.index": k, "viirs.satellite": g.get("satellite")}) as sg:
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    if local:
                        dnb, geo = g["dnb_path"], g["geo_path"]
                        if g.get("masque_path"):
                            masque = {"etat": "fourni", "source": "local", "fichier": os.path.basename(g["masque_path"]),
                                      "ciel_clair_part": round(verifier_masque(g["masque_path"]), 3), "essais": []}
                        else:
                            masque = {"etat": "absent", "motif": "masque des nuages absent : aucun fichier local",
                                      "essais": []}
                        phys = g.get("masque_path")
                    else:
                        dnb, geo = os.path.join(tmp, "dnb.nc"), os.path.join(tmp, "geo.nc")
                        for nom, url, dest in (("dnb", g["dnb_url"], dnb), ("geolocalisation", g["geo_url"], geo)):
                            with tr.span("GET LANCE " + {"dnb": "DNB"}.get(nom, nom), **{"peer.service": "NASA",
                                                                                          "viirs.fichier": nom}) as sd:
                                n = download(url, dest, token)
                                sd.set(**{"viirs.octets": n})
                                octets += n
                        phys = os.path.join(tmp, "masque.nc")
                        masque = lire_masque(g.get("masque_urls") or [], phys, token, tr)
                        octets += masque.get("octets") or 0
                        if masque["etat"] != "fourni":
                            phys = None
                    r["masque"] = masque
                    r["telecharge_s"] = round(time.time() - t, 1)
                    t1 = time.time()
                    with tr.span("viirs.inference") as si:
                        r.update(detect(vvd, dnb, geo, tmp, task["regions"], phys))
                        si.set(**{"viirs.detections": len(r.get("detections", []))})
                    r["detection_s"] = round(time.time() - t1, 1)
                    sg.set(**{"viirs.lune": r.get("lune_moyenne"), "viirs.masque": masque["etat"],
                              "viirs.ciel_clair_part": masque.get("ciel_clair_part")})
            except (DonneesInaccessibles, FichierInvalide) as e:
                r["erreur"] = str(e)[:500]
                sg.erreur(r["erreur"])
                print(f"granule {g['dnb']} : {e}", flush=True)
                # Données inaccessibles, ou premier granule déjà en échec : inutile de télécharger les autres
                if isinstance(e, DonneesInaccessibles) or k == 0:
                    stop = str(e)[:500]
            except Exception as e:                       # une autre granule en échec n'arrête pas les suivantes
                r["erreur"] = f"{type(e).__name__}: {e}"[:500]
                sg.exception(e)
                traceback.print_exc()
            r["rss_max_mo"] = rss_mo()
            sg.set(**{"viirs.rss_max_mo": r["rss_max_mo"]})
        tr.envoyer()                                     # spans envoyés après chaque granule
        out.append(r)
        print(json.dumps({x: v for x, v in r.items() if x != "detections"} | {"n": len(r.get("detections", []))}),
              flush=True)
        if stop:
            print(f"arrêt du travailleur : {stop}", flush=True)
            break
    result = {"granules": out, "mesures": {"duree_s": round(time.time() - t0, 1), "rss_max_mo": rss_mo(),
                                            "telecharge_mo": round(octets / 1e6, 1), "cpu": os.cpu_count()}}
    if stop:
        result["erreur"] = stop
    return result


def send(task: dict, path: str, body: dict, traceparent: str | None = None):
    """Envoi au serveur, avec quelques nouvelles tentatives (le serveur peut redémarrer pendant l'analyse) ; le
    contexte de trace suit la requête (l'API rejoint la trace du travailleur)."""
    headers = {"Authorization": f"Bearer {task['jeton']}"}
    if traceparent:
        headers["traceparent"] = traceparent
    for k in range(6):
        try:
            r = requests.post(f"{task['retour']}{path}", json=body, timeout=60, headers=headers)
            if r.status_code < 500:
                return r.status_code
        except requests.RequestException as e:
            print(f"envoi impossible ({e}), nouvel essai", flush=True)
        time.sleep(10 * (k + 1))
    return None


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--local":
        task = json.load(open(sys.argv[2]))
        print(json.dumps(run(task, local=True)))
        return
    task = json.load(open(os.environ.get("MARS_TACHE", "/mars/tache.json")))
    tr = otel_mini.depuis_tache(task, **{"travailleur.tache": "viirs"})
    with tr.span("travailleur.viirs") as racine:
        token = None
        if task.get("tache_par_le_reseau"):
            with tr.span("travailleur.reception_tache"):
                try:
                    task, token = fetch_task(task, tr.traceparent())
                except Arret as e:
                    racine.erreur(str(e))
                    send(task, "/resultats", {"granules": [], "erreur": str(e)[:1000], "mesures": {"rss_max_mo": rss_mo()}},
                         tr.traceparent())
                    tr.envoyer()
                    return
        racine.set(**{"viirs.granules": len(task.get("granules", []))})
        with tr.span("travailleur.signal_demarre"):
            send(task, "/etat", {"etat": "demarre"}, tr.traceparent())
        try:
            result = run(task, tr=tr, token=token)
        except Exception as e:
            result = {"granules": [], "erreur": f"{type(e).__name__}: {e}"[:1000], "mesures": {"rss_max_mo": rss_mo()}}
        if result.get("erreur"):
            racine.erreur(result["erreur"])
        racine.set(**{"viirs.duree_s": result.get("mesures", {}).get("duree_s"),
                      "viirs.telecharge_mo": result.get("mesures", {}).get("telecharge_mo")})
        with tr.span("travailleur.renvoi_resultats") as sr:
            sr.set(**{"http.response.status_code": send(task, "/resultats", result, tr.traceparent())})
    tr.envoyer()


if __name__ == "__main__":
    main()
