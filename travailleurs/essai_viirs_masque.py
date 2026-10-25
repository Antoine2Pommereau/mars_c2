"""Essai du masque des nuages VIIRS par le vrai chemin de téléchargement (lot viirs-masque, 25/10/2026), dans l'image
publique d'allenai, sur le Mac, sans jeton ni coût.

Le vrai script du travailleur (`run`, non local : `download`, vérification des fichiers, `lire_masque`,
`verifier_masque`, puis `run_pipeline` d'allenai avec tous ses filtres) télécharge par HTTP des fichiers NetCDF au
format de la NASA (DNB, géolocalisation, CLDMSK_L2 avec sa couche /geophysical_data/Clear_Sky_Confidence), fabriqués
ici sur une scène de mer profonde avec quatre bateaux de 80 à 100 nW. Un serveur local joue les trois sources réelles, avec leurs
comportements mesurés le 25/10/2026 :
* « LANCE » (localhost) sert DNB et géolocalisation au porteur du jeton ;
* « SIPS » (temps quasi réel du masque) renvoie vers la connexion Earthdata (/oauth/authorize), comme pour un jeton
  qu'il refuserait ;
* « LAADS » (produit standard) renvoie (307) vers un autre hôte (127.0.0.1, comme le stockage S3 de LAADS) qui sert le
  fichier sans jeton : le jeton n'y est pas transmis.
Attendu : à 90 % de lune, masque refusé par SIPS puis lu chez LAADS, 4 bateaux sur 4 ; sans masque, 0 et « masque des
nuages absent » (le serveur marque alors le granule non évaluable) ; masque tronqué, nommé, et le travailleur continue ;
à 50 % de lune sans masque, 4 sur 4 (le masque ne sert au modèle qu'au dessus de 80 %).

    bash scripts/verifier_images.sh viirs-masque
"""
import http.server
import importlib.util
import os
import sys
import tempfile
import threading

sys.path.insert(0, "/src")
import netCDF4  # noqa: E402
import numpy as np  # noqa: E402

H, W = 768, 1600
JETON = "jeton-essai-local"
# (ligne, colonne, nW/cm²/sr) : loin des bords et dans les trois quarts droits de la scène (filtre d'artefacts). Sous
# 90 % de lune, le classifieur final d'allenai (feedback_cnn, qui reçoit lune et masque en canaux) écarte des taches
# synthétiques plus faibles (mesuré : 2 sur 4 entre 40 et 70 nW, 4 sur 4 entre 80 et 100) ; le modèle seul les voit
# toutes (essai_viirs_lune.py)
BATEAUX = [(250, 600, 80.0), (400, 900, 90.0), (520, 1200, 100.0), (330, 1450, 85.0)]
REGIONS = [[-10.0, 40.0, 5.0, 55.0]]


def ecrire_dnb(path: str):
    rng = np.random.default_rng(1)
    dnb = np.abs(rng.normal(0.3, 0.05, (H, W)))
    yy, xx = np.mgrid[-2:3, -2:3]
    tache = np.exp(-(yy ** 2 + xx ** 2) / 1.2)                     # bateau étalé sur quelques pixels, comme le DNB
    for r, c, v in BATEAUX:
        dnb[r - 2:r + 3, c - 2:c + 3] += v * tache
    with netCDF4.Dataset(path, "w") as ds:
        ds.time_coverage_start = "2026-10-23T01:24:00.000Z"
        ds.time_coverage_end = "2026-10-23T01:30:00.000Z"
        ds.platform = "JPSS-1"
        ds.DayNightFlag = "Night"
        ds.startDirection = "Descending"
        ds.createDimension("y", H)
        ds.createDimension("x", W)
        g = ds.createGroup("observation_data")
        v = g.createVariable("DNB_observations", "f4", ("y", "x"))
        v.valid_min, v.valid_max, v.units = np.float32(0.0), np.float32(1e-6), "W cm-2 sr-1"
        v[:] = (dnb * 1e-9).astype("float32")
        q = g.createVariable("DNB_quality_flags", "i2", ("y", "x"))
        q[:] = np.zeros((H, W), dtype="int16")


def ecrire_geo(path: str, lune: float):
    lat, lon = np.meshgrid(np.linspace(50, 44, H), np.linspace(-8, 0, W), indexing="ij")
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("y", H)
        ds.createDimension("x", W)
        g = ds.createGroup("geolocation_data")
        for nom, val, typ in (("latitude", lat, "f4"), ("longitude", lon, "f4"),
                              ("land_water_mask", np.full((H, W), 7), "u1"),
                              ("moon_illumination_fraction", np.full((H, W), lune), "f4")):
            v = g.createVariable(nom, typ, ("y", "x"))
            v[:] = val.astype({"f4": "float32", "u1": "uint8"}[typ])


def ecrire_masque(path: str):
    """CLDMSK_L2 : grille des bandes M (plus petite que le DNB, allenai la redimensionne), ciel clair partout."""
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("y", 640)
        ds.createDimension("x", 1280)
        g = ds.createGroup("geophysical_data")
        v = g.createVariable("Clear_Sky_Confidence", "f4", ("y", "x"))
        v[:] = np.ones((640, 1280), dtype="float32")


def serveur(dossier: str) -> int:
    class Gestion(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def envoyer(self, code, corps=b"", type_="application/octet-stream", entetes=None):
            self.send_response(code)
            self.send_header("Content-Type", type_)
            self.send_header("Content-Length", str(len(corps)))
            for k, v in (entetes or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(corps)

        def fichier(self, nom):
            self.envoyer(200, open(os.path.join(dossier, nom), "rb").read())

        def do_GET(self):
            p, hote = self.path.split("?")[0], self.headers.get("Host", "")
            porteur = self.headers.get("Authorization") == "Bearer " + JETON
            if p.startswith("/lance/"):                                    # LANCE : jeton exigé
                return self.fichier(p[7:]) if porteur else self.envoyer(302, entetes={"Location": "/oauth/authorize"})
            if p.startswith("/sips/"):                                     # SIPS : jeton refusé, page de connexion
                return self.envoyer(302, entetes={"Location": "/oauth/authorize?client_id=sips"})
            if p.startswith("/laads/"):                                    # LAADS : vers le stockage, autre hôte
                if not porteur:
                    return self.envoyer(401, b"Unauthorized", "text/plain")
                port = hote.rsplit(":", 1)[-1]
                return self.envoyer(307, entetes={"Location": f"http://127.0.0.1:{port}/s3/{p[7:]}?X-Amz-Signature=x"})
            if p.startswith("/s3/"):                                       # stockage : sans jeton, ni attendu ni transmis
                if self.headers.get("Authorization"):
                    return self.envoyer(400, b"jeton transmis au stockage", "text/plain")
                return self.fichier(p[4:])
            if p.startswith("/tronque/"):
                return self.envoyer(200, b"\x89HDF\r\n\x1a\n" + b"\0" * 2_000_000)
            if p.startswith("/oauth/"):
                return self.envoyer(200, b"<!DOCTYPE html><html>Earthdata Login</html>", "text/html")
            self.envoyer(404)

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", 0), Gestion)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


LATS, LONS = np.linspace(50, 44, H), np.linspace(-8, 0, W)


def retrouves(dets: list) -> tuple[int, int]:
    """Bateaux retrouvés (une détection à moins de 3 pixels de chacun) et détections loin de tout bateau."""
    pres = lambda d, r, c: abs(d["lat"] - LATS[r]) <= 3 * 6 / H and abs(d["lon"] - LONS[c]) <= 3 * 8 / W
    vus = sum(1 for r, c, _v in BATEAUX if any(pres(d, r, c) for d in dets))
    autres = sum(1 for d in dets if not any(pres(d, r, c) for r, c, _v in BATEAUX))
    return vus, autres


def worker():
    spec = importlib.util.spec_from_file_location("travailleur_viirs", "/mars/viirs.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    d = tempfile.mkdtemp(prefix="essai_viirs_")
    ecrire_dnb(os.path.join(d, "VJ102DNB_NRT.A2026296.0124.021.nc"))
    for lune in (50, 90):
        ecrire_geo(os.path.join(d, f"geo{lune}.nc"), float(lune))
    ecrire_masque(os.path.join(d, "CLDMSK_L2_VIIRS_NOAA20.A2026296.0124.001.nc"))
    port = serveur(d)
    base = f"http://localhost:{port}"
    dnb = base + "/lance/VJ102DNB_NRT.A2026296.0124.021.nc"
    cas = [
        ("lune 90 %, SIPS refusé puis LAADS", 90, [base + "/sips/CLDMSK_L2_VIIRS_NOAA20.A2026296.0124.001.nrt.nc",
                                                   base + "/laads/CLDMSK_L2_VIIRS_NOAA20.A2026296.0124.001.nc"]),
        ("lune 90 %, aucun masque publié", 90, []),
        ("lune 90 %, masque tronqué", 90, [base + "/tronque/CLDMSK.nc"]),
        ("lune 50 %, aucun masque publié", 50, []),
    ]
    task = {"regions": REGIONS, "granules": [{"dnb": f"granule {k}", "satellite": "NOAA20", "dnb_url": dnb,
                                              "geo_url": f"{base}/lance/geo{lune}.nc", "masque_urls": urls}
                                             for k, (_n, lune, urls) in enumerate(cas)]}
    w = worker()
    res = w.run(task, local=False, token=JETON)
    attendu = {0: ("fourni", 4), 1: ("absent", 0), 2: ("absent", 0), 3: ("absent", 4)}
    erreurs = []
    for k, ((nom, lune, _u), g) in enumerate(zip(cas, res["granules"])):
        m = g.get("masque") or {}
        n, autres = retrouves(g.get("detections") or [])
        source = f" ({m.get('source')}, ciel clair {m.get('ciel_clair_part')})" if m.get("etat") == "fourni" else ""
        print(f"        {nom} : masque {m.get('etat')}{source} ; {n} bateau(x) sur {len(BATEAUX)} retrouvé(s), "
              f"{autres} autre(s) détection(s) ; lune moyenne {g.get('lune_moyenne')}"
              + (f" ; erreur {g['erreur']}" if g.get("erreur") else ""))
        if m.get("etat") != "fourni":
            print(f"          {m.get('motif')}")
        etat, nb = attendu[k]
        if g.get("erreur") or m.get("etat") != etat or (n != nb if nb else n + autres > 0):
            erreurs.append(nom)
    if res.get("erreur"):
        erreurs.append("travailleur arrêté : " + res["erreur"])
    print(f"        téléchargé {res['mesures']['telecharge_mo']} Mo, {res['mesures']['duree_s']} s, "
          f"mémoire {res['mesures']['rss_max_mo']} Mo")
    if erreurs:
        print("ÉCHEC   " + " ; ".join(erreurs))
        return 1
    print("OK      masque des nuages : lu par le vrai chemin, 4 sur 4 à 90 % de lune avec masque, absence nommée "
          "sans arrêt du travailleur")
    return 0


if __name__ == "__main__":
    sys.exit(main())
