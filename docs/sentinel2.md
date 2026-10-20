# Sentinel 2 : mesures avant le lot S2 (20/10/2026)

Rapport demandé avant de construire la détection optique (point 1 du lot S2). Toutes les mesures portent sur les 30
derniers jours (20/09 au 20/10/2026) et nos quatre zones collectées. Sources : catalogue STAC public de Copernicus Data
Space (`sentinel-2-l2a`, 1 862 tuiles ; `sentinel-2-l1c`), produits lus dans le miroir public de Google
(`gcp-public-data-sentinel-2`, mêmes fichiers SAFE et JP2 qu'eodata, sans clé), positions AIS du serveur par l'API en
lecture seule. Scripts de mesure dans le dossier temporaire de la session ; l'essai du modèle est rejouable par
`scripts/verifier_images.sh s2`.

## 1. Couverture

Grille de points en mer tous les 0,06° × 0,045° (environ 4,5 × 5 km), terre exclue par le masque `land`, « au large »
au delà de 20 km de toute côte. Un point est couvert par un passage si l'emprise de données d'une tuile L2A le contient
(les emprises du catalogue sont celles des pixels acquis : tuiles partielles de 0,95°² contre 1,44°² pleines).

| Zone | Points en mer | Au large (> 20 km) | Au large acquis au moins une fois | Passages par point au large en 30 jours (médiane) | Heure UTC des passages |
|---|---|---|---|---|---|
| Bretagne | 2 652 | 2 200 | 100 % | 18 | 11:00 à 11:30 |
| Manche | 3 862 | 2 076 | 100 % | 18 | 10:50 à 11:30 |
| Gascogne | 6 717 | 5 771 | 100 % | 18 | 10:50 à 11:30 |
| Méditerranée | 5 359 | 4 230 | 100 % | 9 | 10:00 à 10:40 |

Toute notre mer est acquise, large compris : trois satellites (S2A, S2B, S2C) passent sur un point de l'Atlantique et
de la Manche environ tous les 1,7 jours, sur un point de Méditerranée tous les 3,3 jours. Toujours en fin de matinée :
Sentinel 2 ne voit jamais la nuit, ni le soir.

## 2. Nuages

### Passages exploitables

Pour 60 points au large tirés au hasard par zone, chaque passage a été jugé sur le masque de scène (SCL) du produit
L2A, dans la fenêtre de 24 km centrée sur le point (nuages : classes 8, 9 et 10 du SCL, probabilité moyenne, forte,
cirrus). Un millier de tuiles lues en entier à 60 m (0,1 à 0,4 Mo chacune), 3 529 observations de fenêtre.

| Zone | Passages par point (30 jours) | Dégagés sous 10 % | Sous 20 % | Par semaine sous 10 % | Sous 20 % | Intervalle médian entre deux passages dégagés |
|---|---|---|---|---|---|---|
| Bretagne | 16,0 | 3,5 | 5,1 | **0,78** | 1,14 | 5 jours |
| Manche | 14,7 | 3,4 | 4,7 | **0,76** | 1,07 | 7,5 jours |
| Gascogne | 15,1 | 3,9 | 4,7 | **0,88** | 1,07 | 3 jours |
| Méditerranée | 13,0 | 5,1 | 6,0 | **1,15** | 1,36 | 3 jours |

Par semaine ISO, nombre de passages de la zone, puis nombre de ceux qui sont dégagés sur la moitié au moins des points
vus :

| Zone | S38 | S39 | S40 | S41 | S42 |
|---|---|---|---|---|---|
| Bretagne | 2 / 0 | 6 / 1 | 6 / 1 | 6 / 0 | 7 / 2 |
| Manche | 2 / 0 | 8 / 1 | 9 / 4 | 8 / 0 | 8 / 1 |
| Gascogne | 2 / 0 | 8 / 4 | 8 / 1 | 8 / 2 | 8 / 2 |
| Méditerranée | 1 / 1 | 8 / 5 | 8 / 1 | 7 / 1 | 9 / 4 |

Un passage sur quatre environ est utile en octobre. L'été sera meilleur, l'hiver pire : mesure à refaire en janvier.

### Annonce du catalogue contre masque de la fenêtre

L'annonce `eo:cloud_cover` porte sur la tuile entière (110 km, terre comprise), pas sur notre fenêtre :

| Annonce de la tuile | Fenêtres | Nuages de la fenêtre (médiane) | Fenêtres dégagées sous 10 % |
|---|---|---|---|
| 0 à 5 % | 410 | 0 % | 96 % |
| 5 à 20 % | 485 | 5 % | 64 % |
| 20 à 50 % | 920 | 27 % | 22 % |
| 50 % et plus | 1 714 | 96 % | 3 % |

Sous 20 % annoncés, 12 % des fenêtres sont en fait nuageuses au delà de 20 % ; au delà de 50 % annoncés, 3 % des
fenêtres restent dégagées. Exemples tirés au hasard : tuile annoncée à 3 %, fenêtre à 19 % ; 24 %, fenêtre à 76 % ;
34 %, fenêtre à 85 %. **L'annonce sert de premier tri (au delà de 50 %, ne pas lancer) ; le SCL de la fenêtre décide**,
et il ne coûte rien : 43 à 442 Ko par fenêtre, lus avant les bandes. Les ombres de nuages (classe 3) n'apparaissent
jamais sur la mer dans nos 12 fenêtres détaillées : le SCL les classe en eau ou en zone sombre. Le masque des ombres
ne pourra pas venir du seul SCL.

## 3. Format et lecture partielle

* Sur eodata, les produits L1C et L2A sont des **SAFE avec des JP2** (`s3://eodata/Sentinel-2/MSI/L2A/AAAA/MM/JJ/...SAFE`,
  `B02_10m.jp2` de 60 à 97 Mo, `SCL_20m.jp2` de 0,5 Mo). Aucune version COG n'est au catalogue de Copernicus Data Space.
  Même seau `eodata` que Sentinel 1 : mêmes clés dédiées et mêmes adresses signées par le serveur (requêtes partielles
  identiques, la signature passe dans l'adresse).
* GDAL (pilote JP2OpenJPEG, 3.12) lit une fenêtre de 24 km par requêtes partielles de 16 Ko, tuile JPEG 2000 par tuile :

| Lecture d'une fenêtre de 24 km | Fenêtres | Lu (médiane, extrêmes) | Fichiers entiers | Part | Requêtes |
|---|---|---|---|---|---|
| L2A : B02, B03, B04, B08 à 10 m et SCL à 20 m | 12 | 47 Mo (35 à 69) | 184 à 432 Mo | 15,5 % | 101 à 136 |
| L1C : les 13 bandes du modèle (tuile pleine) | 1 | 84,5 Mo | 568 Mo | 15 % | 348 |

4 à 7 s depuis le Mac ; sans effet sur le coût, qui est celui de l'heure de travailleur.
Reste à vérifier à l'essai réel : la même lecture par `/vsis3/` signée sur eodata (le miroir de Google sert les mêmes
fichiers, octet pour octet).

## 4. Modèle

### L'image d'AI2 épinglée ne contient pas le détecteur Sentinel 2

`vessel-detection-sentinels@sha256:4388…` (sha-fafc6c3) :

* `frcnn_cmp2/2feb47b-sentinel2-swinv2-small-tci-IR` : `cfg.json` seul, **sans poids** ;
* seul le modèle d'attributs Sentinel 2 a ses poids (`attr/2feb47b-sentinel2-attr-resnet-tci-IR/best.pth`, 96 Mo) ;
* ce détecteur demande en outre deux images antérieures du même lieu (`tci_overlap0`, `tci_overlap1`), soit trois
  produits à lire par analyse.

Le README d'AI2 dit que ces poids seront « fournis séparément » ; le dépôt Git ne les a pas non plus. Les dix étiquettes
publiques de l'image (`latest` est identique à `sha-fafc6c3`) ont la même couche de modèles de 291 Mo, celle du
Sentinel 1 et des deux modèles d'attributs : **aucune version publique de cette image n'a le détecteur Sentinel 2**.

### Le modèle Sentinel 2 actuel d'AI2 : rslearn

AI2 publie son modèle Sentinel 2 actuel, celui de Skylight, dans le projet `rslearn_projects` (code sous licence Apache
2.0, licence des poids à vérifier avant une démonstration publique) :

| | |
|---|---|
| Image | `ghcr.io/allenai/sentinel2-vessel-detection@sha256:0ce4cbee…` (étiquette `sentinel2_vessels_v0.0.22`), linux/amd64 seulement, 7,2 Go compressés, 19,8 Go installés ; Python 3.11, PyTorch 2.7 avec CUDA 12.8 (L4 compris) |
| Poids | hors de l'image, publics : détecteur `sentinel2_vessels/data_20250213_02_all_bands` (1,15 Go), attributs `sentinel2_vessel_attribute/data_20250205_regress_00` (1,09 Go), dépôt `ai2-rslearn-projects-data` |
| Entrée | produit **L1C** (pas L2A), 13 bandes, harmonisées (moins 1 000 pour les traitements 04.00 et suivants) ; aucune image antérieure |
| Modèle | Swin, FPN, Faster R CNN ; carreaux de 512 pixels ; seuil 0,8 ; précision 77,2 % et rappel 78,6 % sur la validation d'AI2 |
| Sortie | position, score, et pour chaque navire longueur, largeur, vitesse, cap, type (cargo, pétrolier, pêche…) |

Mesures dans la vraie image, sur processeur, en émulation amd64 sur le Mac (8 processeurs, 7 Go ; un GP1-S a 8 vCPU et
32 Go, sans émulation) :

| Fenêtre de 24 km | Détection (5 carreaux) | Chaîne complète | Mémoire au plus |
|---|---|---|---|
| Manche, Beachy Head, S2A 04/10/2026 11:08 | 73 à 80 s | 132 à 142 s | 4,3 Go |
| Manche, sud de Wight, S2B 17/10/2026 11:10 | 76 s | 136 s | 4,2 Go |

Il faut `--shm-size=2g` (le chargeur de données meurt avec les 64 Mo par défaut de Docker) et un dossier des poids
accessible en écriture (journal de Lightning).

### Confrontation à l'AIS (fenêtre du 17/10, AIS du serveur)

Six navires détectés ; cinq retrouvés dans l'AIS une fois les positions AIS ramenées à l'heure de la prise de vue :

| Navire AIS | Longueur déclarée | Longueur mesurée | Écart | Cap mesuré, cap AIS | Vitesse mesurée, AIS | Distance |
|---|---|---|---|---|---|---|
| VAASABORG | 132 m | 132 m | 0 % | 69°, 72° | 9,2, 10,7 nœuds | 97 m |
| CAVALLI | 99 m | 97 m | 2 % | 255°, 254° | 9,1, 9,7 nœuds | 130 m |
| SUPRA SOVEREIGN | 190 m | 193 m | 2 % | 253°, 256° | 10,7, 10,6 nœuds | 8 m |
| FEDERAL KATSURA | 190 m | 198 m | 4 % | 254°, 255° | 11,1, 12,4 nœuds | 14 m |
| LADY ARIETTE | 88 m | 76 m | 14 % | 66°, 74° | 8,4, 9,8 nœuds | 73 m |

Le sixième (48 m, classé « pêche », score 0,99) n'a aucun AIS à moins de 4,3 km : petit navire sans obligation d'AIS, ou navire
muet. C'est exactement le cas que Sentinel 2 doit montrer en couleurs réelles. Sur la fenêtre du 04/10 (avant le début
de notre collecte, le 05/10), quatre navires visibles, quatre détectés.

**Piège : l'heure du passage.** L'heure du catalogue et du nom de produit (`20261017T111049`) est le début de la prise
de vue, sur toute l'orbite ; la tuile T30UXA a été vue à 11:17:16 (`SENSING_TIME` de `MTD_TL.xml`), 387 s plus tard.
Avec l'heure du catalogue, chaque navire est décalé d'environ 2 km (6,5 minutes à 10 nœuds) ; avec celle de la tuile,
de 8 à 130 m. La fusion devra lire `SENSING_TIME` de chaque tuile (le calendrier du lot A garde l'heure de la prise de
vue, à corriger).

## 5. Délai de publication

| Produit | Médiane après la prise de vue | 90e centile |
|---|---|---|
| L1C | 3,7 h | 8,5 h |
| L2A | 4,8 h | 7,8 h |

Tip and cue : un passage optique de fin de matinée n'est analysable qu'en milieu ou fin d'après midi.

## 6. Coût

Un GP1-S (0,19 € de l'heure entamée) suffit : image de 7,2 Go (environ 2 min 30 s, d'après les 1 min 39 s mesurées
pour les 5 Go de l'image Sentinel 1), poids de 2,2 Go, puis environ 2 à 3 minutes par fenêtre au plus (mesure en
émulation, borne haute). Une session de quelques fenêtres tient dans l'heure : **0,19 € par passage analysé**, comme
Sentinel 1. Les passages optiques (10:00 à 11:30, publiés vers 15:00) ne tombent jamais dans la même session que les
passages radar (vers 06:00 et 17:30).

Avec environ un passage dégagé par semaine et par point surveillé, une session par jour au plus suffit : 2 à 4 sessions
par semaine selon les points de déclenchement, soit **1,60 à 3,30 € par mois**, dans le budget de 8 € partagé.

## 7. Conclusion

**La suite du lot se justifie, mais avec un autre modèle que celui prévu.**

Ce que Sentinel 2 apporte sur nos zones : toute la mer acquise, large compris ; en octobre, **0,8 passage dégagé par
semaine sur un point de l'Atlantique ou de la Manche, 1,15 en Méditerranée** (un passage sur quatre), et 1 à 5 passages
par semaine et par zone dégagés sur la moitié des points. Sur une image dégagée, le modèle d'AI2 retrouve les navires,
mesure leur longueur à 4 % près dans quatre cas sur cinq (14 % pour le cinquième), leur cap et leur vitesse, et en
montre un sans AIS. C'est un complément rare mais très parlant du radar : identification visuelle, preuve en couleurs
réelles, jamais la nuit ni sous les nuages.

Décisions à prendre avant de construire :

1. **Modèle** : l'image épinglée n'a pas le détecteur Sentinel 2. Je propose l'image rslearn d'AI2 (`sentinel2_vessels_v0.0.22`,
   épinglée par empreinte) et ses poids publics, recopiés sur R2 pour ne pas dépendre du dépôt d'AI2. Conséquences :
   entrée L1C à 13 bandes (et non L2A), image de 7,2 Go en plus de celle de Sentinel 1, travailleur GP1-S inchangé.
2. **Nuages** : bandes lues dans le L1C, masque lu dans le SCL du L2A de la même tuile (mesuré ici, publié environ une
   heure après le L1C). Autre possibilité, le masque de nuages `MSK_CLASSI` du L1C, disponible plus tôt mais pas encore
   comparé au SCL.
3. **Heure de la tuile** : la fusion lit `SENSING_TIME` dans `MTD_TL.xml` (quelques Ko) ; à corriger aussi dans le
   calendrier des passages Sentinel 2.

Décisions d'Antoine (20/10/2026) : les trois propositions sont retenues ; masque attendu 3 h puis analyse « masque
absent » ; comparaison avec `MSK_CLASSI` notée comme amélioration future. Construction ci dessous.

## 8. Licence des poids

Le README de `allenai/rslearn_projects` (dépôt GitHub, vérifié le 20/10/2026) indique : « Model weights and Code :
Licensed under Apache License 2.0 » ; les annotations d'entraînement sont sous CC BY 4.0. Les poids peuvent donc être
recopiés sur R2 et utilisés, y compris pour une démonstration publique, à condition de garder la mention de la
licence et de l'auteur (Allen Institute for AI, projet rslearn). Les images Sentinel 2 sont des données Copernicus,
libres avec mention de la source : chaque vignette porte dans l'interface « Contient des données Copernicus Sentinel
modifiées ».

## 9. Poids sur R2 et quota gratuit

Les deux fichiers de poids (1,15 Go et 1,09 Go, soit 2,24 Go) sont recopiés une fois sur R2 par
`taches.py sentinel2-poids` (copie en flux sans disque, empreinte SHA 256 vérifiée à l'envoi, puis par le travailleur
après chaque téléchargement ; empreintes versionnées dans `config/rules.yaml`). Ils occupent **22 % des 10 Go gratuits**
de R2. Avec l'archive (environ 26 Mo par jour depuis le 05/10/2026) et les sauvegardes (moins de 70 Mo), le quota
gratuit serait atteint vers le **28/07/2027** au lieu du 21/10/2027, soit environ 85 jours plus tôt. Au delà, les poids
coûtent 0,03 dollar par mois (0,015 dollar par Go et par mois). Les vignettes optiques pèsent environ 30 Ko (2 km,
gardées 30 jours) et 140 à 180 Ko pour la vignette de contexte (10 km, seulement pour une détection liée à une alerte,
gardée sans limite) : négligeables.

## 10. Construction (lot S2, règles 2026.10.34, migration 32)

| Partie | Où | Ce qui est fait |
|---|---|---|
| Calendrier | `mars/sentinel2.py` (`corriger_heures`), `scripts/taches.py` (`update_passes`) | heure propre des tuiles des trois derniers jours lue chaque nuit (4 Ko de `MTD_TL.xml` par tuile, clés du serveur) ; `acquired_at` et `ended_at` deviennent la première et la dernière tuile sur nos régions, l'heure du catalogue reste dans `heure_prise_de_vue` ; l'upsert du calendrier ne la remplace plus |
| Sélection | `plan`, `launch` | mêmes points de déclenchement que Sentinel 1 (navires des listes en mer, alertes VIIRS, demandes d'observation), tuiles L1C qui les couvrent, garde des nuages annoncés (au delà de 50 %, tuile écartée avec son motif), masque L2A attendu 3 h puis « absent », heure propre de chaque tuile ; plafond propre (`sentinel2.scenes_max_par_jour`, 0 à la livraison), budget mensuel partagé avec Sentinel 1, GP1-S puis POP2 ; essai manuel avec `SENTINEL_PLAFOND_ESSAI` |
| Travailleur | `travailleurs/sentinel2.py` | poids téléchargés depuis R2 (adresses présignées de 3 h remises avec la tâche, jamais en base) et vérifiés ; SCL du L2A lu dans chaque fenêtre, fenêtre au delà de 20 % de nuages close « nuages » sans lire les bandes ; 13 bandes du L1C lues par `/vsis3/`, octets comptés dans le journal de GDAL ; inférence d'AI2 une seule fois pour toutes les fenêtres ; pour chaque navire du cœur de la fenêtre, part nuageuse dans le kilomètre autour, vignette de 2 km et de contexte de 10 km (PNG en palette, étirement adapté à la mer), géoréférence ; grille du masque (cellules de 500 m) |
| Résultats | `ingest`, `fuse_analysis` | détections (longueur, largeur, cap, vitesse, type, part nuageuse, état du masque) ; fusion AIS à l'heure de la tuile, sans tolérance Doppler (cercle de 300 m) ; navire sous un nuage (plus de 25 % masqué autour de lui) écarté « nuages » ; DARK_SHIP, AIS_UNCONFIRMED, IDENTITY_MISMATCH de source `sentinel2`, même évaluation (zone de réception fiable) ; AIS_UNCONFIRMED seulement dans le cœur d'une fenêtre lue, sur une cellule dégagée, jamais sans masque ; sans masque, toute alerte DARK_SHIP est « à confirmer » ; vignette de contexte rangée pour les seules détections liées à une alerte ; résultat de l'analyse (analysée, nuages, sans détection) |
| Tip and cue | `mars/tip_and_cue.py` | passages Sentinel 2 candidats quand Sentinel 2 est en service ; passage acquis annoncé au delà de 50 % de nuages écarté (motif d'attente) ; cercle sous les nuages au passage optique : la demande repart en attente pour un autre passage (jamais « absent ») ; résultat « vu en optique » sur l'alerte d'origine (`capteur: optique`) |
| API | `backend/sentinel.py`, `backend/preuves.py`, `backend/satellites.py`, `backend/travailleurs.py` | détections radar et optiques dans la couche, fiche optique (vignette, contexte, nuages de la fenêtre), vérification satellite avec la source, résultat optique des passages, état et part du budget, mesures `mars_sentinel2_*` ; remise de la tâche avec les adresses des poids |
| Interface | `web/src` | point plein pour l'optique (anneau pour le radar), fiche « Détection optique Sentinel 2 » (image en grand, mesures, nuages, navire apparié, contexte, alertes), vérification satellite radar et optique confondus, frise (passage nuageux estompé, nuages annoncés au survol), panneau Observations (icône du capteur), alertes de source optique, barre d'état |
| Grafana | tableau Satellites | section « Sentinel 2, optique » : passages dégagés à l'annonce, analyses exploitables, octets lus, coût, analyses par résultat, coût par capteur, table des analyses ; coût et part du budget Sentinel sur les deux capteurs |
| Traces | serveur et travailleur | `sentinel2.selection`, `sentinel2.garde_nuages_annonce`, `GET S3 eodata MTD_TL.xml`, `sentinel2.fenetres`, `sentinel2.verification_cles`, `sentinel2.choix_calcul`, `travailleur.lancement` ; au travailleur `sentinel2.poids` (`GET R2 poids`), `sentinel2.fenetre`, `GET S3 eodata SCL fenêtre` et `GET S3 eodata 13 bandes fenêtre` (octets, requêtes), `sentinel2.inference`, `sentinel2.detections`, `sentinel2.vignettes` ; puis `sentinel2.fusion_et_alertes`, `sentinel2.vignettes` et la destruction |

Vérifications : `tests/test_sentinel2.py` (18 tests : réglages versionnés, tuile, heure, garde des nuages avant et
après lecture, masque du L2A attendu puis absent, part nuageuse et grille, vignette, octets lus, fusion sans Doppler,
chaîne complète sur la base de test, plafond propre et budget partagé, tip and cue sur un passage optique, cercle sous
les nuages, calendrier) ; `scripts/verifier_images.sh s2` lance le vrai script du travailleur dans l'image d'AI2 sur la
fenêtre de Beachy Head (essai du 20/10/2026 : 4 navires, masque présent, 0,4 % de nuages, SCL 16 Ko, 13 bandes 33,6 Mo
en 201 requêtes et 5 s, part nuageuse autour des navires 0 à 1,3 %, vignettes de 29 à 30 Ko, contextes de 136 à
177 Ko, 141 s dont 133 s d'inférence en émulation).

## 11. Essai réel pas à pas

Sentinel 2 reste coupé (`scenes_max_par_jour: 0`) : l'essai passe par la commande manuelle et le plafond d'essai.

1. **Préalables** (une fois, après le déploiement) : migration 32 appliquée, poids sur R2, heures des tuiles :
   ```bash
   docker compose exec taches python scripts/taches.py sentinel2-poids      # 2,24 Go copiés et vérifiés, quelques minutes
   docker compose exec taches python scripts/taches.py sentinel2-heures 3   # {"passages": N, "tuiles": …, "decalage_max_s": …}
   ```
   On doit lire deux lignes « copié et vérifié (… octets, SHA 256 conforme) », puis un décalage maximal de plusieurs
   minutes (387 s attendus pour une tuile de la Manche).
2. **Choisir un passage dégagé récent** (lecture seule, rien n'est lancé) :
   ```bash
   docker compose exec taches python scripts/taches.py sentinel2-candidats 72
   ```
   Prendre un passage dont une tuile est annoncée sous 20 %, « masque present », sans « heure du catalogue », avec une
   ou deux fenêtres (environ 0,19 €). Sans candidat (nuages, aucun navire des listes en mer), réessayer le lendemain
   vers 16:00 UTC, quand les produits du jour sont publiés.
3. **Lancer** :
   ```bash
   docker compose exec -e SENTINEL_PLAFOND_ESSAI=1 taches python scripts/taches.py sentinel2-analyser <passage>
   ```
   On doit lire le travailleur créé (GP1-S en fr-par-2), le coût estimé et la durée de vie.
4. **Suivre** : barre d'état (Satellites, « Dernier passage Sentinel 2 analysé »), Grafana Satellites (section
   Sentinel 2, table « Analyses Sentinel 2 », colonne Trace), puis la trace dans Tempo. Spans attendus, dans l'ordre :
   `sentinel2.selection`, `GET S3 eodata MTD_TL.xml`, `sentinel2.garde_nuages_annonce`, `travailleur.lancement`,
   étapes du démarrage (Docker, image de 7,2 Go), `GET R2 poids` (deux), `GET S3 eodata SCL fenêtre`,
   `GET S3 eodata 13 bandes fenêtre`, `sentinel2.inference`, `sentinel2.vignettes`, `travailleur.renvoi_resultats`,
   `sentinel2.fusion_et_alertes`, destruction de l'instance.
   ```bash
   docker compose exec db psql -U mars -d mars -c "SELECT id, etat, commercial_type, cree_le, detruit_le, cout_reel_eur, left(erreur, 200) FROM travailleurs WHERE tache = 'sentinel2' ORDER BY id DESC LIMIT 1"
   ```
5. **Mesures à relever** (à reporter dans ce document, puis dans `config/rules.yaml`) :
   ```bash
   docker compose exec db psql -U mars -d mars -c "SELECT a.id, a.status, a.summary->>'resultat' AS resultat, a.octets_lus, f->>'etat' AS etat, f->>'nuages_pct' AS nuages, f->>'octets' AS octets_bandes, f->>'requetes' AS requetes, f->>'lecture_s' AS lecture_s, f->>'analyse_s' AS inference_s, t->>'octets_scl' AS octets_scl FROM analyses a, jsonb_each(a.timings) AS x(k, t), jsonb_array_elements(t->'fenetres') AS f WHERE a.source = 'sentinel2' ORDER BY a.id DESC LIMIT 6"
   docker compose exec db psql -U mars -d mars -c "SELECT mesures->'mesures_travailleur' FROM travailleurs WHERE tache = 'sentinel2' ORDER BY id DESC LIMIT 1"
   ```
   * octets lus par `/vsis3/` sur eodata pour les 13 bandes et pour le SCL (attendus près de 33,6 Mo et 16 à 450 Ko,
     mesurés sur le miroir de Google) ;
   * durée de la création à la destruction, téléchargement de l'image, poids (`poids_s`), inférence par fenêtre sur un
     vrai GP1-S (133 s en émulation), mémoire (`rss_max_mo`, 4,3 Go en émulation), coût réel ;
   * dans l'interface : fiche de chaque navire (longueur, cap, vitesse, type contre l'AIS), écart de position au navire
     apparié (8 à 130 m attendus à l'heure de la tuile), vignettes en couleurs réelles avec l'AIS superposé.
6. **Ensuite** : recaler `minutes_fixes_cpu` et `minutes_par_fenetre_cpu` sur les durées mesurées, puis décider d'un
   plafond (`scenes_max_par_jour: 1` ou `2`, nouvelle version des règles).

## 12. Limites

* Masque des nuages : le SCL classe en nuage le navire lui même et son sillage ; la règle porte donc sur une part
  (25 % du kilomètre autour), pas sur un pixel. Un petit nuage sur le navire seul n'est pas vu. Les ombres de nuages
  sur la mer sont rarement classées : le masque ne les couvre presque pas.
* Sans masque (L2A publié plus de 3 h après la prise de vue), un nuage brillant peut passer pour un navire : alertes
  « à confirmer », aucune absence déclarée.
* Le modèle manque environ un navire sur cinq (rappel de 78,6 % chez AI2) et les petites unités ; l'absence d'un navire
  AIS dans une fenêtre dégagée reste un indice, comme au radar.
* L'heure propre des tuiles prévues (plans de l'ESA) reste celle du plan : seule celle des passages acquis est
  corrigée.
* Les octets lus sur eodata ne sont pas encore mesurés (essai réel) ; le comptage ajoute 16 Ko par fichier ouvert,
  lecture d'ouverture que GDAL ne journalise pas.
* Le rejeu du tip and cue (`tip-rejeu`) ne compte encore que les passages Sentinel 1.
