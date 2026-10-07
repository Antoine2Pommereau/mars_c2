# Travailleurs éphémères et détections nocturnes VIIRS

Étape 3, lot B. Le serveur (Scaleway DEV1-S, 2 Go) ne peut pas faire tourner les modèles de détection satellite : il
**décide et reçoit**, et des **travailleurs éphémères** font l'analyse. Mesures du 07/10/2026.

## Architecture commune aux lots B et C

```
conteneur taches (serveur)                          instance Scaleway éphémère (travailleur)
  chaque nuit à 06:30 UTC :
  granules VIIRS à traiter (catalogue CMR) ──API──▶ créée (DEV1-M, image « docker »), réseau privé
  cloud-init : script + tâche + jeton                 docker pull de l'image d'analyse (registre GitHub)
                                                      téléchargement des données (NASA, jeton Earthdata)
                                                      détection, données effacées
  nginx :8090 (réseau privé seulement) ◀─────────────  POST /api/travailleurs/<id>/resultats (jeton)
  chaque minute : résultats traités, instance
  détruite, orphelins détruits, journal task_runs
```

* **Le serveur ne télécharge jamais d'image satellite** : il lit des métadonnées (catalogue CMR de la NASA) et reçoit
  des détections (quelques kilooctets).
* **Travailleur** (`mars/travailleurs.py`) : instance créée par l'API Scaleway (clé limitée au projet) sur l'image
  Scaleway « docker », avec une adresse IPv4 dynamique (téléchargements) et une carte sur le réseau privé (retour). Le
  script (`travailleurs/viirs.py`) et la tâche passent par cloud-init ; le travailleur tire l'image d'analyse, travaille,
  renvoie son résultat, puis s'éteint. Le serveur le détruit dès réception.
* **Retour par le réseau privé** : le serveur n'ouvre toujours aucun port sur Internet. nginx publie le port 8090 sur
  l'adresse du réseau privé seulement (`MARS_IP_PRIVEE`) et n'y relaie que `POST /api/travailleurs/<id>/etat` et
  `/resultats`. Chaque exécution a son propre jeton aléatoire ; seule son empreinte SHA 256 est en base ; un jeton faux
  est refusé (403), un second envoi aussi (409).
* **Type d'instance** : paramètre de `config/rules.yaml` (`travailleurs.types`) : `viirs` sur DEV1-M (CPU), `sentinel`
  sur L4-1-24G (GPU, image Scaleway GPU avec pilotes NVIDIA) pour le lot C.

### Garde fous

| Garde fou | Valeur (`config/rules.yaml`, section `travailleurs`) |
|---|---|
| Durée de vie maximale | 45 min : au delà, destruction forcée par le serveur |
| Plafonds quotidiens | 4 travailleurs et 180 minutes par jour ; au delà, refus consigné |
| Un seul travailleur VIIRS à la fois | une nuit n'est pas lancée deux fois |
| Orphelins | chaque minute, et au démarrage du conteneur : toute instance étiquetée `mars-c2-travailleur` inconnue de la base, ou plus vieille que la durée de vie, est détruite (même après une base restaurée) |
| Journal | chaque exécution dans `task_runs` (tâche `travailleur_viirs`) : durée, mémoire, volume téléchargé, coût estimé, résultat ; la table `travailleurs` garde l'état et les mesures |
| Arrêt d'urgence | `docker compose exec taches python scripts/taches.py detruire-travailleurs` |
| Traitement tout ou rien | un résultat est écrit en une transaction : rien à moitié en base |

Éprouvé avec un faux client Scaleway : lancement (image, création, cloud-init, réseau privé, démarrage), instance
orpheline détruite à la première minute, destruction forcée après la durée de vie, plafond quotidien refusé.

## VIIRS : données, modèle, traitement

**Données** : bande Day/Night (DNB, 750 m) de Suomi NPP (`VNP02DNB`), NOAA 20 (`VJ102DNB`) et NOAA 21 (`VJ202DNB`),
avec leur géolocalisation (`VNP03DNB`, `VJ103DNB`, `VJ203DNB`), cherchées dans le catalogue CMR public de la NASA
(nuit, emprise de nos quatre régions). Produits en temps quasi réel (`_NRT`, LANCE) publiés 2 à 3 heures après le
passage ; produits standard en repli (une nuit rattrapée plus tard). Nuit du 06/10/2026 : **12 granules** sur nos
régions (4 Suomi NPP, 5 NOAA 20, 3 NOAA 21), entre 00:18 et 03:06 UTC, soit environ 1,3 Go téléchargés par le
travailleur (40 Mo de DNB et 65 Mo de géolocalisation par granule). Téléchargement avec le jeton Earthdata
(`EARTHDATA_TOKEN`).

**Modèle** : [allenai/vessel-detection-viirs](https://github.com/allenai/vessel-detection-viirs) (Apache 2.0),
image publique `ghcr.io/allenai/vessel-detection-viirs` épinglée par empreinte (886 Mo compressés, 3,6 Go installée,
amd64). Elle est publique : aucun quota de notre registre privé, aucun jeton. Le modèle filtre déjà éclairs, torchères,
artefacts, bords de granule, abords du rivage et nuages éclairés par la lune.

**Mesures** (jeu d'exemple d'allenai, une granule NOAA 20, image en émulation amd64 sur le Mac, donc durée majorée) :
12,5 s de détection par granule, **1,45 Go de mémoire au plus**, 46 détections (identiques à la réponse de référence).
DEV1-S (2 Go) serait trop juste ; **DEV1-M** (3 vCPU, 4 Go, 0,0202 € de l'heure) laisse de la marge.

**Côté serveur** (`mars/viirs.py`), pour chaque détection :
1. **Instant** : une granule balaie 6 minutes ; la position du point entre la première et la dernière ligne de
   balayage donne son instant.
2. **Appariement AIS** : position de chaque navire à cet instant (interpolée, ou estimée sur 20 minutes au plus),
   tolérance de 1 500 m (deux pixels) augmentée de la moitié du chemin parcouru pendant l'estime ; un pour un, par
   distance croissante.
3. **Côtes** : écartée à moins de 5 km de la terre (trait de côte GSHHG, table `land`).
4. **Lumières fixes** (plateformes, éoliennes, phares) : revue à moins de 1 km sur au moins 3 nuits distinctes en 30
   jours, elle entre au registre `viirs_lumieres_fixes` ; ses détections antérieures sont écartées et les alertes encore
   vierges qu'elles avaient levées sont classées (faux positif) avec une note.
5. **Alerte DARK_SHIP** de source VIIRS pour chaque détection restante sans AIS : gravité moyenne, élevée à moins de
   2 milles d'une infrastructure ou à moins de 10 km d'un navire des listes, critique pour les deux.

Éprouvé sur la base de test (trois nuits simulées, retour par la vraie route) : une détection appariée, une écartée
près de la côte, la lumière fixe reconnue à la troisième nuit avec ses deux alertes antérieures classées, une alerte
élevée près d'un câble, une critique près d'un câble et d'un navire sanctionné.

**Déclenchement** : chaque jour à `VIIRS_HEURE` (06:30 UTC), après la publication de la dernière granule de la nuit.
Les trois dernières nuits sont relues : une nuit manquée (serveur arrêté, NASA injoignable, travailleur en échec) est
rattrapée au passage suivant ; une granule en échec est retentée une fois.

## Coûts (hors taxes)

| Poste | Mesure ou tarif | Par mois (30 nuits) |
|---|---|---|
| Instance DEV1-M | 0,0202 € de l'heure, facturée à l'heure entamée (une heure au moins) ; une nuit dure environ 6 à 10 minutes (démarrage, image, 12 granules) | 0,61 € |
| IPv4 dynamique | 0,005 € de l'heure depuis le 01/06/2026 | 0,15 € |
| Réseau privé, catalogue CMR, données NASA, image allenai | gratuits | 0 € |
| **Total VIIRS** | | **environ 0,76 €** (0,91 € TTC) |

Rattrapage : une nuit de plus dans le même travailleur ne coûte rien de plus tant qu'il reste sous l'heure. Lot C à
titre indicatif : L4-1-24G à 0,7875 € de l'heure, facturé à la minute, soit environ 0,13 € pour 10 minutes par passage
analysé.

## Mise en place (une fois)

1. **Réseau privé** (console Scaleway, VPC, région Paris) : créer un réseau privé `mars-c2-travailleurs`, y attacher le
   serveur ; relever son adresse sur ce réseau (`ip -4 addr` sur le serveur, nouvelle interface) : c'est
   `MARS_IP_PRIVEE`. Gratuit.
2. **Clé d'API Scaleway** limitée au projet (voir docs/deploiement.md).
3. **Jeton Earthdata** : https://urs.earthdata.nasa.gov, Generate Token. **Il expire au bout de 60 jours** : à
   renouveler ; un jeton périmé se voit dans la barre d'état (travailleur en échec, granules en échec).

## Limites

* Durée par nuit non encore mesurée sur une vraie instance (pas de clé au moment du développement) : la mémoire et le
  temps de détection viennent de l'émulation sur le Mac. La première nuit réelle donnera durée, mémoire et volume dans
  `task_runs` (tâche `travailleur_viirs`).
* Le modèle trouve les navires **éclairés** ; un navire tous feux éteints n'est pas vu. Pleine lune et nuages dégradent
  la détection (filtrés par le modèle, au prix du rappel).
* Résolution de 750 m : deux navires proches ne font qu'une détection ; l'appariement est un pour un.
* Les seuils (tolérance, côte, persistance) sont des valeurs de départ, à calibrer sur les premières nuits.
* Les données de la nuit sont en ligne 2 à 3 heures après le passage : une alerte VIIRS arrive le matin, pas en direct.
