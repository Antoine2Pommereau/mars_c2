"""Travailleur VIIRS : détection des navires éclairés la nuit (bande Day/Night de Suomi NPP, NOAA 20 et NOAA 21).

Exécuté sur une instance éphémère, dans l'image publique ghcr.io/allenai/vessel-detection-viirs (modèle et chaîne de
traitement d'allenai, licence Apache 2.0, code dans /src). Le serveur de MARS C2 transmet ce fichier et la tâche par
cloud-init (mars/travailleurs.py) ; ce script n'utilise que la bibliothèque standard, requests et le code de /src.

Pour chaque granule (fichier DNB et fichier de géolocalisation) : téléchargement avec le jeton Earthdata, détection,
détections réduites à nos régions, fichiers effacés aussitôt. Puis envoi au serveur, par son réseau privé, des
détections, de l'emprise de chaque granule et des mesures (durées, mémoire, volume téléchargé). Une granule en échec
n'arrête pas les autres. Le serveur détruit l'instance dès réception (ou à l'échéance de sa durée de vie).

Variables : MARS_TACHE (fichier JSON de la tâche), EARTHDATA_TOKEN. Essai local sur des fichiers déjà présents :
    python3 viirs.py --local tache.json   (granules avec « dnb_path » et « geo_path », résultat sur la sortie standard)
"""
import json
import os
import resource
import sys
import tempfile
import time
import traceback

sys.path.insert(0, "/src")

import requests  # noqa: E402

TIMEOUT_S = 300


def rss_mo() -> float:
    """Mémoire maximale du processus depuis son démarrage, en Mo (ru_maxrss est en kilooctets sous Linux)."""
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


def download(url: str, dest: str, token: str) -> int:
    with requests.get(url, headers={"Authorization": f"Bearer {token}"}, stream=True, timeout=TIMEOUT_S) as r:
        r.raise_for_status()
        n = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                n += len(chunk)
    return n


def in_regions(lon: float, lat: float, boxes: list) -> bool:
    return any(x0 <= lon <= x1 and y0 <= lat <= y1 for x0, y0, x1, y1 in boxes)


def finite(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v == v and abs(v) != float("inf") else None


def detect(vvd, dnb_path: str, geo_path: str, tmp: str, boxes: list) -> dict:
    import utils                                     # code d'allenai (/src)
    all_det, image, ds, status = vvd.run_pipeline(dnb_path, geo_path, tmp)
    chips = utils.get_chips(image, all_det["vessel_detections"], ds)
    start, end = utils.get_acquisition_time(ds)
    dets = []
    for c in chips.values():
        lon, lat = finite(c["longitude"]), finite(c["latitude"])
        if lon is None or lat is None or not in_regions(lon, lat, boxes):
            continue
        dets.append({"lon": round(lon, 5), "lat": round(lat, 5), "nanowatts": finite(c["max_nanowatts"]),
                     "orientation": finite(c["orientation"]), "lune": finite(c["moonlight_illumination"]),
                     "ciel_clair": finite(c["clear_sky_confidence"])})
    return {"statut": status, "debut": str(start), "fin": str(end), "plateforme": utils.get_provider_name(ds),
            "lune_moyenne": finite(utils.get_average_moonlight(ds)),
            "emprise": [[finite(x), finite(y)] for x, y in utils.get_frame_extents(ds)],
            "eclairs": int(all_det.get("lightning_count") or 0), "torcheres": int(all_det.get("gas_flare_count") or 0),
            "detections": dets}


def run(task: dict, local: bool = False) -> dict:
    t0 = time.time()
    from pipeline import VIIRSVesselDetection          # code d'allenai (/src)
    vvd = VIIRSVesselDetection()
    token = os.environ.get("EARTHDATA_TOKEN", "")
    out, octets = [], 0
    for g in task["granules"]:
        r = {"dnb": g["dnb"], "satellite": g.get("satellite")}
        t = time.time()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                if local:
                    dnb, geo = g["dnb_path"], g["geo_path"]
                else:
                    dnb, geo = os.path.join(tmp, "dnb.nc"), os.path.join(tmp, "geo.nc")
                    octets += download(g["dnb_url"], dnb, token) + download(g["geo_url"], geo, token)
                r["telecharge_s"] = round(time.time() - t, 1)
                t1 = time.time()
                r.update(detect(vvd, dnb, geo, tmp, task["regions"]))
                r["detection_s"] = round(time.time() - t1, 1)
        except Exception as e:                           # une granule en échec n'arrête pas les autres
            r["erreur"] = f"{type(e).__name__}: {e}"[:500]
            traceback.print_exc()
        r["rss_max_mo"] = rss_mo()
        out.append(r)
        print(json.dumps({k: v for k, v in r.items() if k != "detections"} | {"n": len(r.get("detections", []))}),
              flush=True)
    return {"granules": out, "mesures": {"duree_s": round(time.time() - t0, 1), "rss_max_mo": rss_mo(),
                                          "telecharge_mo": round(octets / 1e6, 1), "cpu": os.cpu_count()}}


def send(task: dict, path: str, body: dict):
    """Envoi au serveur, avec quelques nouvelles tentatives (le serveur peut redémarrer pendant l'analyse)."""
    for k in range(6):
        try:
            r = requests.post(f"{task['retour']}{path}", json=body, timeout=60,
                              headers={"Authorization": f"Bearer {task['jeton']}"})
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
    send(task, "/etat", {"etat": "demarre"})
    try:
        result = run(task)
    except Exception as e:
        result = {"granules": [], "erreur": f"{type(e).__name__}: {e}"[:1000], "mesures": {"rss_max_mo": rss_mo()}}
    send(task, "/resultats", result)


if __name__ == "__main__":
    main()
