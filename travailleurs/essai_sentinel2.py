"""Essai du travailleur Sentinel 2 (travailleurs/sentinel2.py) dans la vraie image d'AI2, sur processeur, lancé par
scripts/verifier_images.sh s2 (lot S2, docs/sentinel2.md).

Fenêtre réelle de 24 km, comme le serveur la calcule : passage S2A du 04/10/2026 à 11:08 UTC, rail montant de la
Manche au large de Beachy Head (0,4 E, 50,4 N), 0,2 % de nuages. Les 13 bandes du L1C et le masque de scène (SCL) du
L2A sont lus par requêtes partielles dans le miroir public de Google (mêmes fichiers qu'eodata, sans clé) ; le script
de production fait tout le reste : garde des nuages, lecture, inférence d'AI2, masque, vignettes, mesures.
Attendu : quatre navires dans le cœur de la fenêtre, masque présent, fenêtre dégagée, vignettes de 2 et 10 km.

    python /mars/essai_sentinel2.py        lecture par /vsicurl/ dans le miroir (cible s2)
    python /mars/essai_sentinel2.py --s3   chemin S3 de production (cible s2-s3) : adresses s3://eodata/…, paire de clés
                                           factice remise comme par le serveur, faux S3 local qui vérifie la signature
                                           AWS version 4 de chaque requête et relaie les plages d'octets vers le miroir

Ajouté après l'essai réel du 20/10/2026 : rasterio refusait les clés AWS en options GDAL (EnvError), alors que la
cible s2, qui lisait sans clé, passait. En --s3, on vérifie aussi la voie sans boto3 (variables d'environnement
retirées après la lecture), le refus d'une signature fausse, et qu'aucune clé n'apparaît dans le résultat, les spans
ou l'environnement."""
import json
import math
import os
import sys
import time

sys.path.insert(0, "/mars")
import sentinel2  # noqa: E402

MIROIR = "https://storage.googleapis.com/gcp-public-data-sentinel-2/"
L1C = ("tiles/31/U/CR/S2A_MSIL1C_20261004T110831_N0513_R137_T31UCR_20261004T162422.SAFE/GRANULE/"
       "L1C_T31UCR_A058936_20261004T111122/IMG_DATA/T31UCR_20261004T110831_{}.jp2")
SCL = ("L2/tiles/31/U/CR/S2A_MSIL2A_20261004T110831_N0513_R137_T31UCR_20261004T174703.SAFE/GRANULE/"
       "L2A_T31UCR_A058936_20261004T111122/IMG_DATA/R20m/T31UCR_20261004T110831_SCL_20m.jp2")
LON, LAT, DEMI_M = 0.4, 50.4, 12000
SATLAS = "satlas/sentinel2_swinb_si_ms.pth"
SATLAS_SHA256 = "788c3c9c7b74d4128627b4b27ebf3c3d3f715132c31271148f1cd5198b5be99a"
ATTENDUS = 4


CLE, SECRET = "CLEESSAIS3", "SECRETESSAIS3NEJAMAISAFFICHER"


def main(s3: bool = False) -> int:
    faux = None
    prefixe = MIROIR
    if s3:
        import faux_s3
        faux = faux_s3.FauxS3(CLE, SECRET, miroir=MIROIR)
        port = faux.demarrer()
        prefixe = "s3://eodata/"
        # Remise comme par fetch_task : la paire va dans _S3, jamais dans la tâche
        sentinel2._S3.update({"endpoint": f"127.0.0.1:{port}", "http": True, "region": "default",
                              "access_key": CLE, "secret_key": SECRET})
    dlat, dlon = DEMI_M / 110574.0, DEMI_M / (111320.0 * math.cos(math.radians(LAT)))
    bbox = [LON - dlon, LAT - dlat, LON + dlon, LAT + dlat]
    tache = {"scenes": [{"analyse": 0, "id": "S2A_MSIL1C_20261004T110831_N0513_R137_T31UCR", "tuile": "T31UCR",
                         "debut": "2026-10-04T11:08:31Z", "fin": "2026-10-04T11:08:31Z", "satellite": "S2A",
                         "bandes": {b: prefixe + L1C.format(b) for b in sentinel2.BANDES}, "scl": prefixe + SCL,
                         "fenetres": [{"id": 0, "bbox": bbox, "marge_m": 2000, "points": 1}]}],
             "regions": [[-6.8, 41.2, 9.8, 51.2]], "confiance": 0.8, "nuages_fenetre_max_pct": 20,
             "masque_classes": [3, 8, 9, 10], "masque_voisinage_m": 500, "masque_part_max_pct": 25,
             "vignette_km": 2, "contexte_km": 10,
             "rslp_prefix": "/rslp",
             # Squelette SatlasPretrain : fichier du cache (monté dans /rslp), servi localement après vérification de son
             # empreinte ; Hugging Face est rendu injoignable dans le conteneur par verifier_images.sh
             "poids": [{"chemin": SATLAS, "fichier": "/rslp/" + SATLAS, "satlas": "Sentinel2_SwinB_SI_MS",
                        "sha256": SATLAS_SHA256}]}
    import otel_mini
    tr = otel_mini.Traceur(None, None, None)
    t0 = time.time()
    r = sentinel2.run(tache, tr=tr)
    duree = time.time() - t0
    sc = (r.get("scenes") or [{}])[0]
    f = (sc.get("fenetres") or [{}])[0]
    if r.get("erreur") or f.get("erreur"):
        print(f"ÉCHEC  s2 : {r.get('erreur') or f.get('erreur')}")
        if faux:
            print(f"        faux S3 : {faux.requetes} requêtes, {faux.refus} refusées, {faux.envoyes / 1e6:.1f} Mo envoyés")
        return 1
    print(f"OK      s2 : masque {sc['masque']}, nuages de la fenêtre {f.get('nuages_pct')} %, SCL "
          f"{sc['octets_scl'] / 1e3:.0f} Ko, 13 bandes {sc['octets_lus'] / 1e6:.1f} Mo en {f.get('requetes')} requêtes "
          f"et {f.get('lecture_s')} s")
    for d in sc["detections"]:
        v, c = len(d.get("vignette") or "") * 3 // 4, len(d.get("contexte") or "") * 3 // 4
        print(f"        {d['lon']:.4f} {d['lat']:.4f} score {d['score']:.2f} longueur {d['longueur_m']:.0f} m largeur "
              f"{d['largeur_m']:.0f} m vitesse {d['vitesse_kn']:.1f} nœuds cap {d['cap_deg']:.0f} {d['type']} ; "
              f"nuages {d.get('nuages_pct')} % ; vignette {v / 1e3:.0f} Ko, contexte {c / 1e3:.0f} Ko")
    n = len(sc["detections"])
    if n < ATTENDUS or sc["masque"] != "present" or f.get("etat") != "lue" or not f.get("grille"):
        print(f"ÉCHEC  s2 : {n} navire(s) sur {ATTENDUS} attendus, masque {sc['masque']}, fenêtre {f.get('etat')}")
        return 1
    if any((d.get("nuages_pct") or 0) > 25 for d in sc["detections"]):
        print("ÉCHEC  s2 : un navire dégagé serait écarté comme nuageux (navire et sillage pris pour un nuage)")
        return 1
    if not all(d.get("vignette") and d.get("contexte") and d.get("vignette_geo") for d in sc["detections"]):
        print("ÉCHEC  s2 : vignette ou géoréférence manquante")
        return 1
    print(f"OK      s2 : {n} navires détectés, décrits et illustrés en {duree:.0f} s sur processeur "
          f"(inférence {f.get('analyse_s')} s)")
    if faux:
        return controles_s3(faux, tache, r, tr)
    return 0


def controles_s3(faux, tache: dict, r: dict, tr) -> int:
    """Après l'analyse de bout en bout par le chemin S3 : signatures toutes acceptées, aucune clé laissée ou divulguée,
    voie sans boto3, signature fausse refusée."""
    import rasterio
    sc = r["scenes"][0]
    erreurs = []
    print(f"        faux S3 : {faux.requetes} requêtes signées, {faux.refus} refusées, {faux.envoyes / 1e6:.1f} Mo "
          f"envoyés ({len(faux.lus)} fichiers) ; compté par le travailleur {(sc['octets_lus'] + sc['octets_scl']) / 1e6:.1f} Mo")
    if faux.refus:
        erreurs.append(f"{faux.refus} requêtes refusées avec la bonne paire")
    if len(faux.lus) < 14:
        erreurs.append(f"{len(faux.lus)} fichiers lus par le faux S3 sur 14 (13 bandes et SCL)")
    if sentinel2.session_aws() is None:
        print("        boto3 absent de l'image : clés passées par l'environnement le temps de la lecture")
    traces = json.dumps(tr.finis)
    if any(SECRET in x or CLE in x for x in (json.dumps(r), traces)):
        erreurs.append("une clé apparaît dans le résultat ou dans un span")
    if any(k in os.environ for k in sentinel2.CLES_AWS):
        erreurs.append("clés AWS laissées dans l'environnement après la lecture")
    journal = "/tmp/essai_s2_gdal.log"
    bbox = tache["scenes"][0]["fenetres"][0]["bbox"]
    href = tache["scenes"][0]["scl"]
    # Voie sans boto3 : variables d'environnement pendant la lecture seulement ; lecture non mise en cache
    vraie = sentinel2.session_aws
    sentinel2.session_aws = lambda: None
    refus = faux.refus
    try:
        with sentinel2.env_gdal(journal), rasterio.Env(CPL_VSIL_CURL_NON_CACHED="/vsis3/eodata/"):
            pendant = all(os.environ.get(k) for k in sentinel2.CLES_AWS)
            scl = sentinel2.lire_scl(href, bbox)[0]
        apres = not any(k in os.environ for k in sentinel2.CLES_AWS)
        print(f"        sans boto3 : SCL relu ({scl.size} pixels), clés posées pendant la lecture {pendant}, "
              f"retirées après {apres}")
        if not (pendant and apres and scl.size and faux.refus == refus):
            erreurs.append("voie sans boto3 en échec")
    except Exception as e:
        erreurs.append(f"voie sans boto3 : {type(e).__name__}: {e}"[:300])
    finally:
        sentinel2.session_aws = vraie
    # Signature fausse : la lecture doit échouer et le faux S3 la refuser
    sentinel2._S3["secret_key"] = "FAUX"
    refus = faux.refus
    try:
        with sentinel2.env_gdal(journal), rasterio.Env(CPL_VSIL_CURL_NON_CACHED="/vsis3/eodata/"):
            sentinel2.lire_scl(href, bbox)
        erreurs.append("une signature fausse n'est pas refusée")
    except Exception as e:
        print(f"        signature fausse refusée ({faux.refus - refus} requêtes) : {type(e).__name__}")
        if faux.refus == refus:
            erreurs.append("lecture en échec sans refus du faux S3")
    sentinel2._S3["secret_key"] = SECRET
    if erreurs:
        print("ÉCHEC  s2-s3 : " + " ; ".join(erreurs))
        return 1
    print("OK      s2-s3 : SCL et 13 bandes lus par S3 signé (AWS version 4), clés ni gardées ni divulguées, "
          "voie sans boto3 et refus d'une signature fausse vérifiés")
    return 0


if __name__ == "__main__":
    sys.exit(main(s3="--s3" in sys.argv[1:]))
