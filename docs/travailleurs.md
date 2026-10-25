# Travailleurs éphémères : architecture commune (VIIRS, Sentinel 1, Sentinel 2)

Document de référence des travailleurs, rédigé le 24/10/2026 par fusion de `docs/archives/travailleurs_viirs.md` et
de la partie travailleurs de `docs/sentinel.md`. L'analyse propre à chaque capteur reste dans `docs/sentinel.md`
(radar), `docs/sentinel2.md` (optique) et la section « VIIRS » ci dessous ; le pilotage (pauses, plafonds, budget)
dans `docs/controle.md` ; les gestes d'exploitation et les échéances des secrets dans `docs/exploitation.md`.

> **État au 24/10/2026**
> * VIIRS (DEV1-M, chaque nuit à 06:30 UTC) : **en service**, repris au déploiement de 29bcfff après la pause du
>   21/10 ; dernière nuit relevée par le bilan, 21/10 : 12 granules, 0 détection, 0,02 € ; 12 travailleurs réussis
>   et 5 en échec depuis le début (essais d'octobre compris).
> * Sentinel 1 : **en pause** depuis le 21/10 (panneau de contrôle) ; **processeur seul** depuis le lot Z4 (GP1-S,
>   POP2-8C-32G en secours, lots de 4 fenêtres) ; **une seule analyse réelle aboutie** (analyse 97, 19/10, GP1-S). La
>   voie GPU (L4, L40S, image GPU OS, contrôle du GPU, types incompatibles), jamais aboutie, est retirée (lot Z4).
> * Sentinel 2 (GP1-S) : **en pause** depuis le 21/10 ; 3 échecs réels le 20/10, correctif livré, jamais rejoué.
> * Sécurité du lot Z1 en place : groupe de sécurité `mars-travailleurs`, jeton Earthdata et paire Copernicus remis
>   une seule fois par le réseau privé, résultats validés contre la tâche.
> * Dépense réelle des travailleurs en octobre : facture Scaleway du 01 au 23/10, RENDER-S 1,23 € (essai raté du
>   19/10), GP1-S 0,77 € (quatre essais Sentinel), L4 0,06 € ; VIIRS 0,34 € (17 travailleurs, selon `/api/controle/etat`).

## 1. Principe

Le serveur (Scaleway DEV1-M, 4 Go de mémoire, 20 Go de disque depuis le 19/10/2026) ne fait tourner aucun modèle de
détection satellite : il **décide et reçoit**. Des **travailleurs éphémères**, instances Scaleway créées à la demande
par l'API, téléchargent les données, détectent, renvoient leurs résultats et sont détruits par le serveur.

```
conteneur taches (serveur, fr-par-1)                     instance Scaleway éphémère (travailleur)
  décide : nuit VIIRS (06:30 UTC), passages Sentinel 1     créée par l'API (type selon la tâche), réseau privé,
  et 2 utiles (toutes les 30 min), plafonds, budget  ─API─▶  groupe de sécurité mars-travailleurs
  cloud-init : script de démarrage, adresse de retour,     démarrage : interface privée, contact du serveur,
  jeton de l'exécution (ni tâche ni secret)                 contrôle des accès R2, Docker
  nginx :8090 (réseau privé) ───────────────────────────▶  POST …/etat {"etat":"tache"} : tâche et secret, une fois
                                                            image d'analyse publique (allenai), données lues
                                                            (NASA, Copernicus en S3), détection, vignettes
  nginx :8090 (réseau privé) ◀───────────────────────────  POST /api/travailleurs/<id>/resultats (jeton)
  chaque minute : résultats validés puis traités,
  instance détruite (et volume bloc), orphelins détruits,
  coût réel, journal task_runs
```

* **Le serveur ne télécharge jamais d'image satellite** : il lit des métadonnées (catalogue CMR de la NASA, catalogue
  STAC de Copernicus) et reçoit des détections et des vignettes (quelques kilooctets chacune).
* Code : `mars/travailleurs.py` (orchestrateur commun : client Scaleway, création, surveillance, destruction gardée,
  coûts), `travailleurs/demarrage.sh` (script de démarrage), `travailleurs/viirs.py`, `travailleurs/sentinel.py`,
  `travailleurs/sentinel2.py` (scripts d'analyse), `backend/travailleurs.py` (routes de retour et remise de la tâche),
  `mars/validation.py` (validation des résultats), `mars/viirs.py`, `mars/sentinel.py`, `mars/sentinel2.py` (décision
  et traitement des résultats côté serveur). Réglages : section `travailleurs` de `config/rules.yaml`.

## 2. Cycle de vie

### 2.1 Création vérifiée

Ordre imposé par `mars/travailleurs.py`, chaque étape vérifiée avant la suivante :

1. **Image** : Ubuntu 24.04 officielle résolue dans le catalogue (zone, type, `x86_64`, fiche de l'image), en
   `instance_local` (disque local) ou `instance_sbs` (volume bloc, POP2). Un message par cause : introuvable,
   architecture, pas de volume racine, taille inconnue.
2. **Groupe de sécurité** `mars-travailleurs` de la zone, créé s'il manque (section 4).
3. **Création éteinte**, volume racine décrit par sa seule taille et son type (disque local `l_ssd` ou volume bloc
   `sbs_volume`), étiquettes `mars-c2-travailleur` et `run-<n>`, nom `mars-travailleur-<n>`.
4. **Relecture** de l'instance (image, démarrage local, volume, groupe de sécurité) : une instance non conforme est
   détruite aussitôt. Pour un volume bloc, la taille est lue dans l'API Block Storage
   (`/block/v1alpha1/zones/<zone>/volumes/<id>`), l'API Instances la donnant nulle ; illisible, elle ne fait pas
   refuser à elle seule. Les volumes bloc sont **enregistrés en base dès la création**.
5. **Carte du réseau privé** rattachée, attendue « available » (un seul span de sondage,
   `scaleway.attente_reseau_prive`).
6. **`cloud-init`** avec la MAC de la carte privée, le script de démarrage, l'adresse de retour et le jeton.
7. **Démarrage**, attendu « running » (`scaleway.attente_demarrage`).

Une étape en échec : lancement « echec » dans `task_runs`, instance (et volume) détruits aussitôt.

### 2.2 Démarrage (`travailleurs/demarrage.sh`)

Étapes, chacune écrite dans `/var/log/mars-travailleur.log`, sur la console série (lignes « MARS ») et en span :
`interface_privee` (interface trouvée par sa MAC, configurée en DHCP si le système ne l'a pas fait),
`contact_serveur` (état « reseau » : premier signe de vie, journal envoyé dès lors), `controle_r2` (Sentinel 2 :
adresses des poids présignées pour 5 minutes, un octet lu de chacune avant Docker), `installation_docker`, `telechargement_image`, `analyse`.

* **Délai de démarrage** : 10 minutes sans signe de vie (le réseau privé n'a pas été joint) : destruction, motif
  enregistré.
* **Échec signalé tout de suite** : toute sortie en échec envoie l'état « echec » avec l'étape et le motif, puis le
  journal, avant un arrêt différé de 10 minutes (le temps de lire la console). Le serveur détruit l'instance à la
  minute suivante. (L'état « gpu_inutilisable » et l'étape `controle_gpu` sont retirés au lot Z4.)
* Le travailleur n'a **pas de SSH** : son journal arrive au serveur (`taches.py journal-travailleur`), sinon console
  série Scaleway.

### 2.3 Remise de la tâche

Pour les types marqués `tache_par_le_reseau` (aujourd'hui les trois : `viirs`, `sentinel`, `sentinel2`), le
`cloud-init` ne contient que l'adresse de retour, le jeton, le script et ce drapeau. Le script d'analyse demande sa
tâche par `POST /api/travailleurs/<id>/etat` avec `{"etat": "tache"}` et son jeton. L'API rend les paramètres (en
base, sans secret) et le secret du type, lu dans son environnement :

| Type | Secret remis | Variables |
|---|---|---|
| `viirs` | jeton NASA Earthdata (lot Z1, 22/10/2026 ; avant, dans le `cloud-init`) | `EARTHDATA_TOKEN` |
| `sentinel`, `sentinel2` | paire Copernicus dédiée aux travailleurs | `CDSE_TRAVAILLEUR_ACCESS_KEY`, `CDSE_TRAVAILLEUR_SECRET_KEY` |
| `sentinel2` | en plus, adresses des poids présignées pour 3 h par le jeton du seau des modèles | `R2_MODELES_*` |

* **Une seule fois** : la remise est notée (`mesures.tache_remise_le`) dans la même requête que sa vérification ; une
  seconde demande reçoit 409, même concurrente. Secret absent du `.env` : 503 sans marquer la remise.
* Le secret n'est **jamais** dans le `cloud-init` (lisible par l'API Scaleway et par tout processus de l'instance), ni
  en base, ni dans un journal ; il reste en mémoire du script, détruit avec l'instance. La remise est journalisée sans
  lui (`travailleur.secret_remis`).
* La paire Copernicus du serveur (`CDSE_ACCESS_KEY`) ne quitte jamais le serveur ; le jeton R2 principal, filtré sur
  l'IP du serveur, ne présigne jamais pour un travailleur (`docs/sentinel2.md`, section 9).
* Pourquoi des clés et non des adresses signées : Copernicus refuse les adresses présignées (constat du 14/10/2026,
  `docs/sentinel.md`).

### 2.4 Résultats

`POST /api/travailleurs/<id>/resultats` avec le jeton. Un jeton faux est refusé (403), un second envoi aussi (409).
Les résultats sont **validés contre la tâche** (section 4) avant toute écriture ; acceptés, ils sont traités par le
conteneur taches à la minute suivante, **en une transaction** (rien à moitié en base), sur une ligne verrouillée (un
résultat traité une seule fois) ; une même détection ne peut exister deux fois (index unique). Un travailleur qui
finit en « code 0 » sans aucune scène aboutie est classé en échec par le serveur.

### 2.5 Destruction et sa garde

* **Quand** : dès les résultats traités ; à la fin de la durée de vie ; après un échec signalé ou un délai de
  démarrage dépassé ; instance « stopped in place » ou « stopped » d'un travailleur encore « en cours » (traitée comme
  un échec de la machine).
* **Comment** : une instance en marche est arrêtée par `terminate` si elle n'a que son disque local ; une instance
  éteinte depuis son système (« stopped in place ») est d'abord mise hors tension (poweroff), attendue « stopped »,
  puis supprimée avec ses volumes (Scaleway refuse sinon, « should be powered off »). **Volume bloc** (POP2 ;
  autrefois L4 et L40S) : jamais `terminate`, qui laisserait le volume, facturé ; extinction, suppression de l'instance, puis du volume
  par l'API Block Storage (droit BlockStorageFullAccess), retentée chaque minute jusqu'au bout ; les volumes supprimés
  sont nommés dans le journal (`volumes_bloc_supprimes`).
* **Orphelins** : chaque minute, et au démarrage du conteneur, toute instance étiquetée `mars-c2-travailleur` inconnue
  de la base, ou plus vieille que sa durée de vie, est détruite (même après une base restaurée).
* **Jamais une autre instance** : toute destruction passe par `Scaleway.destroy`, qui relit l'instance chez Scaleway
  et refuse si : identifiant du serveur principal (`SCW_SERVEUR_PRINCIPAL`, et identifiant lu dans les métadonnées de
  l'instance qui exécute le code), protection contre la suppression active, autre projet, nom autre que
  `mars-travailleur-<n>`, étiquettes `mars-c2-travailleur` et `run-<n>` absentes ou discordantes. Le filtre
  d'étiquette de l'API est revérifié ; `action` n'accepte que le démarrage ; aucun autre chemin destructeur. Une
  destruction refusée est journalisée et la ligne close, sans aucun appel chez Scaleway. Couvert par
  `tests/test_travailleurs.py`.
* **Arrêt d'urgence** : `docker compose exec taches python scripts/taches.py detruire-travailleurs` (toutes zones des
  types).

## 3. Types et ordre de repli

### 3.1 Table des types (`travailleurs.types`)

| Tâche | Type | Zone | Ressources | Stockage | Prix HT | Facturation | État |
|---|---|---|---|---|---|---|---|
| VIIRS | DEV1-M | fr-par-1 | 3 vCPU, 4 Go | disque local 20 Go | 0,0202 €/h | heure entamée | en service |
| Sentinel 1 | GP1-S | fr-par-2 | 8 vCPU, 32 Go | disque local 60 Go | 0,1907 €/h | heure entamée | éprouvé une fois (analyse 97) |
| Sentinel 1, secours | POP2-8C-32G | fr-par-2 | 8 vCPU, 32 Go | volume bloc 60 Go | 0,29 €/h | heure entamée | jamais lancé |
| (retirés au lot Z4) | L4-1-24G, L40S-1-48G | fr-par-2, fr-par-1 | GPU | volume bloc | 0,7875 et 1,4699 €/h | minute | jamais aboutis |
| Sentinel 2 | GP1-S | fr-par-2 | 8 vCPU, 32 Go | disque local 80 Go | 0,1907 €/h | heure entamée | 3 échecs le 20/10 |
| Sentinel 2, secours | POP2-8C-32G | fr-par-2 | 8 vCPU, 32 Go | volume bloc 80 Go | 0,29 €/h | heure entamée | jamais lancé |
| (retiré le 19/10) | RENDER-S | fr-par-2 | P100 16 Go | | 1,221 €/h | heure entamée | incompatible |

Région fr-par seulement : le réseau privé qui ramène les résultats est régional (fr-par-1 et fr-par-2 le partagent ;
pl-waw-2 est injoignable). La voie GPU (L4, L40S) est retirée au lot Z4, le 24/10/2026 : jamais aboutie (rupture du L4
le 16/10, RENDER-S Pascal incompatible le 19/10), et le processeur suffit aux fenêtres utiles. Elle reste dans
l'historique Git (`docs/journal.md`).

### 3.2 Choix du calcul pour Sentinel 1 (processeur seul, lot Z4)

| Passage | Calcul | Motif |
|---|---|---|
| 4 fenêtres au plus (`fenetres_max_cpu`) | une session : GP1-S, POP2-8C-32G si le GP1-S est en rupture, sinon attente | « processeur » |
| Au delà | lots de 4 fenêtres, le suivant au cycle d'après (30 min) | « processeur par lots » |
| Scène entière (passage choisi à la main sans aucun point d'intérêt) | refusée, sans appel à Scaleway | motif du refus |

Le choix, le type et la raison sont enregistrés dans l'analyse (`calcul`, `calcul_type`, `calcul_motif`) et dans la
trace (`sentinel.choix_calcul`, `sentinel.essai_type`). Détail : `docs/sentinel.md`. Sentinel 2 va toujours sur GP1-S, POP2 en secours
(`docs/sentinel2.md`).

### 3.3 Rupture de stock

* **Disponibilité lue avant toute création** (`products/servers/availability`, toutes les pages lues) : un type
  « shortage » n'est jamais essayé. « scarce » ne garantit rien : un `out_of_stock` à la création ou au démarrage fait
  supprimer instance et volume, marquer le travailleur `rupture_stock` (hors plafond, coût nul, pas d'alerte « deux
  échecs de suite ») et essayer le type suivant dans le même cycle.
* **Aucun type lançable** : analyse en « attente_gpu » avec son motif et l'heure du prochain essai, reprise à chaque
  cycle tant que la scène reste utile (`sentinel.fenetre_utile_h`, 24 h) ; au delà, abandon et notification ntfy
  (« Analyse Sentinel 1 abandonnée faute de calcul »). L'état garde son nom historique, « attente_gpu ».
* La table `types_incompatibles` (marquages des GPU inutilisables, lot C4) est gardée pour son historique ; plus rien
  ne l'écrit depuis le lot Z4.

## 4. Réseau privé et sécurité

* **Aucun port public sur le serveur** hormis SSH. nginx publie le **port 8090 sur l'adresse du réseau privé
  seulement** (`MARS_IP_PRIVEE`) et n'y relaie que les routes des travailleurs : `POST /api/travailleurs/<id>/etat`,
  `/resultats`, `/journal`, et les traces (vérifiées par `auth_request` sur `/otel`, même jeton, refusées dès
  l'instance détruite).
* **Jeton par exécution** : aléatoire, seule son empreinte SHA 256 est en base ; comparaison à temps constant.
* **Groupe de sécurité `mars-travailleurs`** (lot Z1), un par zone (les groupes de Scaleway sont zonaux) : entrée
  refusée sur l'adresse publique, sortie libre, à état (les réponses aux connexions sortantes passent). Créé au besoin
  par `taches.py sentinel-diagnostic` (sa seule écriture) ou au premier lancement, **vérifié à chaque création** (une
  instance rattachée à un autre groupe est refusée). Le travailleur garde une IPv4 publique dynamique pour ses
  téléchargements.
* **Validation des résultats** (lot Z1, `mars/validation.py`, `travailleurs.validation`) : un travailleur tourne sur
  une image publique ancienne avec une adresse publique ; compromis, il pourrait fabriquer des alertes. Ne sont
  acceptés que les résultats qui correspondent à la tâche : mêmes granules ou scènes (et mêmes analyses), heures à
  moins de 3 h de celles de la tâche, positions dans nos zones collectées à 0,5° près, au plus 500 détections par
  granule VIIRS, 1 000 par scène Sentinel 1 ou tuile Sentinel 2, 200 fenêtres par scène, vignettes de 400 000 octets
  au plus, types et bornes de chaque mesure. Un refus donne un motif lisible (jamais la valeur reçue au delà de
  quelques caractères), est journalisé et passe le travailleur en échec. Les seuils par défaut sont dans le code : la
  validation ne dépend pas de la présence du fichier des règles.
* **Secrets remis une fois** par le réseau privé (section 2.3). Échéances relevées et notifiées par
  `taches.py echeances` : paire Copernicus des travailleurs (`CDSE_TRAVAILLEUR_EXPIRENT_LE`), jeton Earthdata
  (`EARTHDATA_EXPIRENT_LE`, 60 jours) ; renouvellement dans `docs/exploitation.md`.
* Clé d'API Scaleway limitée au projet ; politique de l'application `mars-c2-travailleurs` (portée Project) avec
  PrivateNetworksFullAccess (rattacher une carte, ReadOnly ne suffit pas) et BlockStorageFullAccess (volumes bloc).

## 5. Facturation, plafonds, budget, coûts mesurés

### 5.1 Plafonds communs (`config/rules.yaml`, section `travailleurs`)

| Garde fou | Valeur |
|---|---|
| Durée de vie | 45 min par défaut ; sur processeur, calculée au lancement (`minutes_fixes_cpu` + 3 min par fenêtre + `cpu_marge_min`, soit 23 min pour une fenêtre et 32 pour quatre en Sentinel 1), enregistrée dans `travailleurs.duree_max_min` |
| Plafonds quotidiens | 4 travailleurs et 180 minutes par jour, comptés sur les seules instances vraiment créées chez Scaleway (un lancement refusé avant, image, quota, clés, ne compte pas) |
| Place réservée à VIIRS | tant que la nuit du jour n'a pas eu son travailleur, les autres tâches lui laissent 1 travailleur et 60 minutes (`reserve_viirs`) |
| Verrou | un seul travailleur actif par tâche (index unique en base) : boucle et commande manuelle ne lancent jamais deux fois la même nuit ou le même passage |
| Plafond de scènes | `sentinel.scenes_max_par_jour` (2 au fichier) et `sentinel2.scenes_max_par_jour` (0 au fichier), réglables dans le panneau de 0 à 4 ; **l'état du panneau en base prime sur le fichier** |
| Pause | interrupteurs `interrupteurs_satellites` (Sentinel 1, Sentinel 2, VIIRS, tip and cue) : aucun travailleur créé, décision consignée dans `task_runs` |

### 5.2 Budget

* **Budget Sentinel de 8 € par mois** (`sentinel.budget_mensuel_eur`, 6 € jusqu'au 20/10/2026), partagé entre
  Sentinel 1, Sentinel 2 et tip and cue. Le coût estimé avant lancement (selon le type réellement lancé et le nombre
  de fenêtres) s'ajoute au coût engagé du mois ; au delà, refus clair.
* VIIRS est compté **hors budget** (0,02 € par nuit).
* Estimation : une heure entamée par session de quatre fenêtres au plus (0,19 € pour un GP1-S), toutes les sessions
  d'un passage comptées avant le premier lancement.
* **Coût réel** de chaque travailleur, calculé à sa destruction (de la création à la destruction, selon le mode de
  facturation du type) : `travailleurs.cout_reel_eur`, à côté du coût prévu (`cout_prevu_eur`) ; span
  `travailleur.cout` avec l'écart ; jauge du panneau (Coulisses, Budget).

### 5.3 Coûts mesurés et tarifs

| Poste | Valeur |
|---|---|
| Nuit VIIRS (DEV1-M) | 0,0202 €/h, à l'heure entamée : environ 0,02 € par nuit (6 à 10 minutes) ; 0,34 € pour 17 travailleurs en octobre |
| IPv4 dynamique | 0,005 € de l'heure depuis le 01/06/2026, environ 0,15 € par mois pour VIIRS |
| Réseau privé, catalogues CMR et STAC, données NASA et Copernicus, images publiques d'AI2 | gratuits |
| Analyse Sentinel 1 sur GP1-S (analyse 97, 19/10) | 0,19 € HT, 6 min 15 s de la création à la destruction |
| Échec au démarrage (5 min) | GP1-S 0,19 €, POP2 0,29 € (RENDER-S 1,22 €, 1,23 € facturés à l'essai 26) |
| Facture Scaleway, 01 au 23/10/2026 | RENDER-S 1,23 €, GP1-S 0,77 € (quatre essais Sentinel), L4 0,06 € |

Ordres de grandeur par mois : VIIRS environ 0,76 € HT (0,61 € d'instance et 0,15 € d'IPv4, estimation du 07/10) ;
Sentinel 1, une session par jour sur GP1-S 5,7 €, deux 11,4 € (le budget de 8 € arrête proprement au delà) ;
Sentinel 2, 1,60 à 3,30 € estimés (`docs/sentinel2.md`, section 6).

## 6. Surveillance et diagnostic

Chaque minute, le conteneur taches : traite les résultats reçus, détruit les travailleurs finis, expirés ou muets, les
orphelins, relève l'état Scaleway d'un travailleur muet avant sa destruction, calcule le coût réel, journalise chaque
exécution dans `task_runs` (tâches `travailleur_viirs`, `travailleur_sentinel`, `travailleur_sentinel2` : durée,
mémoire, volume téléchargé, coût, résultat) ; la table `travailleurs` garde état, mesures, journal (200 000 derniers
caractères) et erreur.

| Commande (`docker compose exec taches python scripts/taches.py …`) | Rôle |
|---|---|
| `travailleurs` | état des travailleurs, une surveillance tout de suite (identifiant de trace affiché) |
| `journal-travailleur [ID]` | journal envoyé par un travailleur (le dernier par défaut), état Scaleway s'il est muet |
| `detruire-travailleurs` | arrêt d'urgence : détruit toute instance étiquetée |
| `sentinel-diagnostic` | lecture seule hormis le groupe de sécurité : paires Copernicus (lecture réelle de 16 octets, expiration), clés Scaleway et réseau privé, image du type de base et sa fiche brute, disponibilité et prix de chaque type, stockage bloc, droits, volumes bloc des 30 derniers jours ; PRÊT, MANQUE ou À VÉRIFIER par ligne, code de sortie non nul s'il manque quelque chose |
| `sentinel2-diagnostic` | seau des modèles, variables dans taches et backend, poids, un octet lu par adresse présignée |
| `essai-reel` | lot Z6, lecture seule : travailleurs en vie, budget des essais (8 € HT) et du mois, pauses, clés, types éprouvés ou non, passages candidats Sentinel 1 et 2, requête de vérification ; code de sortie non nul s'il manque quelque chose |
| `essai-reel --lancer ID` | lot Z6 : un essai réel sur le passage ID (Sentinel 1 ou 2), malgré la pause (consigné), plafond du jour en vigueur et au moins une scène ; refusé si un travailleur vit encore ou si le budget des essais est épuisé ; **tout type jamais éprouvé est refusé** (POP2 compris) ; consigné dans `task_runs` (`essai_reel`) |
| `viirs-refusionner (--nuit J \| --du J [--au J] \| --toutes) [--simulation]` | gratuit, sans travailleur : appariement de chaque détection VIIRS refait avec les règles en vigueur (maintien à l'arrêt 120 min, prudence) ; alertes DARK_SHIP mises à jour en place : classées par MARS C2 en faux positif avec le navire qui les explique si encore nouvelles, abaissées en faible à confirmer si un navire est possiblement compatible, gardées sinon ; décisions des opérateurs gardées, rien d'effacé ; demandes d'observation encore actives nées d'une alerte classée annulées avec le même motif. `--simulation` : transaction en lecture seule (PostgreSQL refuse toute écriture), rapport complet |
| `sentinel-refusionner ID` | gratuit, sans travailleur : fusion de l'analyse ID (Sentinel 1 ou 2, terminée) refaite sur ses détections en base avec les règles en vigueur ; alertes mises à jour en place, classées automatiquement si encore nouvelles, décisions des opérateurs gardées ; tracé, consigné (`sentinel_refusion`) |
| `essai-reel --lancer ID --refaire` | réanalyse d'un passage Sentinel 1 déjà analysé : nouvelle analyse liée à la précédente (`analyses.refait`), l'ancienne et ses détections gardées telles quelles, les deux dans la fiche du passage ; refusée si une analyse de ce passage est en cours. Tout refus s'affiche « REFUS : motif », code de sortie non nul (« analyse N en cours », « déjà analysé », travailleur en vie, pause, rien à analyser) |
| `viirs-bilan [--du AAAA-MM-JJ]` | lot Z6, lecture seule : une ligne par nuit (nuits manquées comprises) : lune moyenne, masques des nuages reçus sur granules exploitables, granules et erreurs, détections avec AIS, sans AIS, non évaluables, écartées, alertes DARK_SHIP, travailleurs (état, durée, mémoire, coût) ; une nuit sous une lune forte sans masque est « NON ÉVALUABLE », jamais « 0 détection » |
| `viirs --nuit AAAA-MM-JJ [--nuit …]` | lot viirs-masque : retraitement de ces nuits par un travailleur DEV1-M, granules déjà traitées comprises (environ 0,02 €), tant que LANCE les sert (environ 7 jours) |
| `echeances` | échéances des secrets, relevées et notifiées |
| `satellites-pause on\|off [source]` | pause ou reprise (aussi dans Coulisses, Sources et capteurs) ; les commandes manuelles sont refusées en pause sauf `--malgre-pause` |

Ailleurs : indicateur « Satellites » et Coulisses (Activité satellite, Budget, avec le coût par résultat depuis le
lot Z6 : dépense de chaque source, échecs compris, rapportée à ses nuits traitées, analyses terminées ou demandes
analysées) ; Grafana, tableau Satellites
(travailleurs : durée, mémoire, coût, motif ; ruptures de stock ; coût du mois) ; notifications ntfy
de la catégorie « travailleurs » (nuit VIIRS absente, analyse abandonnée, pauses et reprises). Chaque lancement est une trace de bout
en bout, du serveur au travailleur et retour (`docs/opentelemetry.md`). Après un essai, vérifier dans la console
Scaleway (Instances en fr-par-1 et fr-par-2, Block Storage) qu'aucune instance `mars-travailleur-*` ni aucun volume
ne reste.

## 7. VIIRS (lot B)

**Données** : bande Day/Night (DNB, 750 m) de Suomi NPP (`VNP02DNB`), NOAA 20 (`VJ102DNB`) et NOAA 21 (`VJ202DNB`),
avec leur géolocalisation (`VNP03DNB`, `VJ103DNB`, `VJ203DNB`), cherchées dans le catalogue CMR public de la NASA
(nuit de 18 h à 6 h UTC, emprise de nos quatre régions). Produits en temps quasi réel (`_NRT`, LANCE) publiés 2 à 3
heures après le passage ; produits standard en repli (nuit rattrapée plus tard). Nuit du 06/10/2026 : **12 granules**
(4 Suomi NPP, 5 NOAA 20, 3 NOAA 21), entre 00:18 et 03:06 UTC, environ 1,3 Go téléchargés par le travailleur (40 Mo
de DNB et 65 Mo de géolocalisation par granule), avec le jeton Earthdata. Chaque fichier est vérifié (taille,
signature HDF5) et la cause nommée (licence LANCE non acceptée, jeton manquant).

**Modèle** : [allenai/vessel-detection-viirs](https://github.com/allenai/vessel-detection-viirs) (Apache 2.0), image
publique `ghcr.io/allenai/vessel-detection-viirs` épinglée par empreinte (886 Mo compressés, 3,6 Go installée,
amd64) : aucun quota de notre registre privé, aucun jeton. Le modèle filtre déjà éclairs, torchères, artefacts, bords
de granule, abords du rivage et nuages éclairés par la lune. Mesures (jeu d'exemple d'allenai, une granule NOAA 20,
émulation amd64 sur le Mac, durée majorée) : 12,5 s par granule, **1,45 Go de mémoire au plus**, 46 détections
identiques à la référence. DEV1-S (2 Go) serait trop juste, DEV1-M laisse de la marge.

**Déclenchement** : chaque jour à `VIIRS_HEURE` (06:30 UTC), après la dernière granule de la nuit. Les trois
dernières nuits sont relues : une nuit manquée (serveur arrêté, NASA injoignable, travailleur en échec, pause) est
rattrapée au passage suivant, le jour même d'une reprise ; une granule en échec est retentée une fois. Une nuit de
plus dans le même travailleur ne coûte rien tant qu'il reste sous l'heure.

**Côté serveur** (`mars/viirs.py`), pour chaque détection :
1. **Instant** : une granule balaie 6 minutes ; la position du point entre la première et la dernière ligne de
   balayage donne son instant.
2. **Appariement AIS** : position de chaque navire à cet instant (interpolée, ou estimée sur 20 minutes au plus),
   tolérance de 1 500 m (deux pixels) augmentée de la moitié du chemin parcouru pendant l'estime ; un pour un, par
   distance croissante.
3. **Côtes** : écartée à moins de 5 km de la terre (trait de côte GSHHG, table `land`).
4. **Lumières fixes** (plateformes, éoliennes, phares) : revue à moins de 1 km sur au moins 3 nuits distinctes en 30
   jours, elle entre au registre `viirs_lumieres_fixes` ; ses détections antérieures sont écartées et les alertes encore
   vierges qu'elles avaient levées classées (faux positif) avec une note.
5. **Non évaluable** (aucune alerte ; règle commune aux détections satellites, `mars/evaluation.py`) : coupure du flux
   AIS à l'heure du passage (au moins 3 minutes sous 20 % de la médiane, à 5 minutes près), ou hors de la zone de
   réception fiable dès qu'elle est construite (elle seule décide alors) ; sinon, critère provisoire : aucun navire AIS
   reçu à moins de 30 km dans la demi heure autour du passage. Chaque alerte note le critère (`critere_evaluation`).
6. **Alerte DARK_SHIP** de source VIIRS pour chaque détection restante sans AIS : gravité **faible, « à confirmer »**
   (AIS incomplet, petite pêche sans obligation d'AIS), sauf à moins de 2 milles d'une infrastructure ou à moins de
   10 km d'un navire des listes (élevée ; les deux : critique) et au delà de 12 milles des côtes (22 km, moyenne).

**Mesures des trois premières nuits (06 au 09/10/2026, 152 détections)** : la distance au navire AIS le plus proche est
bimodale (59 détections à moins de 1 km, presque rien entre 1,5 et 10 km, 80 au delà de 10 km). Élargir la tolérance
n'y change rien (appariées : 39 % à 1 000 m, 40 % à 1 500 m, 41 % à 5 000 m ; estime de 5 à 30 min : 32 à 42 %). Les
détections sans AIS sont isolées (5 paires seulement), à 42 km des côtes en médiane, 47 sur 87 en Méditerranée, et 68
sur 87 sans aucun navire AIS reçu à moins de 20 km (4 en médiane autour des détections appariées) : c'est la réception
AIS qui manque, d'où « non évaluable ». Appliqué aux 87 alertes : 61 non évaluables (retirées), 10 élevées, 6
moyennes, 10 faibles à confirmer. Aucune coupure du flux ces trois nuits.

Éprouvé sur la base de test (trois nuits simulées, retour par la vraie route) : une détection appariée, une écartée
près de la côte, la lumière fixe reconnue à la troisième nuit avec ses deux alertes antérieures classées, une alerte
élevée près d'un câble, une critique près d'un câble et d'un navire sanctionné.

**Preuves images** : pour chaque détection, le travailleur tire de l'image en mémoire une vignette PNG de 240 × 240
pixels (40 pixels DNB agrandis six fois, environ 30 km de côté), étirée en logarithme de 0 à 100 nW/cm²/sr, avec un
marqueur, une échelle de 10 km, la flèche du nord, le satellite, la date, la lune et le ciel clair, et sa géoréférence
(moindres carrés sur la géolocalisation du découpage : la détection retombe à un pixel près de son centre). Le
conteneur taches range les vignettes sur R2 (`vignettes/viirs/<nuit>/<granule>/<détection>.png`) et les inscrit au
registre `preuves_images` (aucun fichier sur le disque du serveur ; le texte base64 disparaît de la base avec le
résultat traité). L'API les relit par adresse présignée (`/api/preuves/<id>.png`, sans boto3) ; la fiche de la
détection superpose les positions AIS à moins de 5 km à l'heure du passage (navire apparié en blanc). Conservation :
sans limite pour une image liée à une alerte, 30 jours sinon (tâche `preuves`, chaque nuit) ; image retirée aussitôt
si sa détection disparaît. Même registre pour Sentinel 1 et 2 (`mars/preuves.py`, `web/src/registres/preuves.ts`).
Mesure sur le jeu d'exemple (46 détections) : **4,7 Ko en moyenne par vignette** (5,0 Ko au plus), environ 0,25 Mo par
nuit, 7 Mo par mois avant la purge, quelques dizaines de kilooctets par nuit gardés sans limite : négligeable devant
les 10 Go gratuits de R2.

**Limites de VIIRS**
* Le modèle trouve les navires **éclairés** ; un navire tous feux éteints n'est pas vu. Pleine lune et nuages dégradent
  la détection (filtrés par le modèle, au prix du rappel).
* Résolution de 750 m : deux navires proches ne font qu'une détection ; l'appariement est un pour un.
* Seuils (tolérance, côte, persistance) : valeurs de départ, à calibrer.
* Données en ligne 2 à 3 heures après le passage : une alerte VIIRS arrive le matin, pas en direct.
* Mémoire et durée de détection par granule viennent encore de l'émulation sur le Mac ; les nuits réelles les
  consignent dans `task_runs` (`travailleur_viirs`) et `travailleurs.mesures`, sans relevé consolidé dans ce document.

## 8. Mise en place (une fois)

1. **Réseau privé** (console Scaleway, VPC, région Paris) : réseau privé `mars-c2-travailleurs`, serveur rattaché ;
   son adresse sur ce réseau (`ip -4 addr` sur le serveur, nouvelle interface) est `MARS_IP_PRIVEE`.
   `SCW_PRIVATE_NETWORK_ID` est l'identifiant du réseau privé (onglet Overview), pas celui du VPC. Régional : le même
   sert en fr-par-2. Gratuit.
2. **Clé d'API Scaleway** limitée au projet, droits de la section 4 (`docs/deploiement.md`).
3. **Jeton Earthdata** : https://urs.earthdata.nasa.gov, Generate Token, licence LANCE NRT acceptée sur le compte ;
   **60 jours** ; date dans `EARTHDATA_EXPIRENT_LE`.
4. **Copernicus Data Space** : deux paires de clés S3 (serveur, travailleurs), procédure dans `docs/sentinel.md`.
5. **Seau des modèles** pour Sentinel 2 : `docs/sentinel2.md`, section 9.
6. Le `.env` est lu par l'API (qui remet les secrets) et par taches : redémarrer les deux après un changement.

## 9. Pièges Scaleway (vivants)

* PrivateNetworksReadOnly ne permet pas de rattacher une carte au réseau privé ; le rattachement est asynchrone
  (attendre « available ») ; l'image ne configure pas toujours l'interface privée : DHCP par le script de démarrage.
* Image et volume racine : résoudre l'image (zone, type, architecture, fiche) et ne décrire le volume racine que par
  sa taille et son type ; sans cela, « UEFI Interactive Shell » à la console (aucun système amorçable).
* Pour un volume bloc (`sbs_volume`, `sbs_snapshot`), l'API Instances donne une taille nulle : la lire dans l'API Block
  Storage `v1alpha1`.
* `terminate` laisse le volume bloc, facturé ; une instance « stopped in place » refuse la suppression tant qu'elle
  n'est pas mise hors tension.
* La disponibilité varie d'heure en heure (« shortage », « scarce ») et sa réponse est paginée (100 types par page).
* Instances processeur (DEV1, GP1, POP2, RENDER) facturées à l'heure entamée, même pour un échec au démarrage.
* Une image publique (allenai) n'entame pas le quota de 500 Mo des paquets privés GitHub.
* Un volume bloc dont l'instance aurait été supprimée hors de MARS C2 (console) n'est pas retrouvé automatiquement :
  `sentinel-diagnostic` relit les volumes enregistrés des 30 derniers jours.

## 10. Historique

| Date | Fait |
|---|---|
| 07/10/2026 | Lot B livré (architecture commune, VIIRS), éprouvé avec un faux client Scaleway |
| 07 et 08/10/2026 | Premiers essais réels : droit PrivateNetworksReadOnly insuffisant, interface privée non configurée (travailleur injoignable), deux lancements simultanés (d'où le verrou), volume de démarrage sans système (« UEFI Interactive Shell »), 39 granules en échec faute de licence LANCE (page HTML de 10 Ko), destruction refusée d'une instance « stopped in place » ; corrigés le 08/10 (migration 21) |
| 06 au 09/10/2026 | Trois premières nuits VIIRS mesurées (152 détections) : réception AIS insuffisante au large, d'où « non évaluable » (règles 2026.10.20) |
| 11 et 12/10/2026 | Lot C (Sentinel 1) livré ; sept refus de Scaleway en 0,1 s épuisent le plafond et privent la nuit VIIRS : seules les instances créées comptent désormais, place réservée à VIIRS ; lancements 10 à 16 refusés (fiche de l'image GPU sur volume bloc), corrigé le 12, confirmé le 13 |
| 14/10/2026 | Essai 19 (L4) refusé par notre contrôle du volume bloc (taille nulle), corrigé ; Copernicus refuse les adresses présignées : paire dédiée remise par le réseau privé ; essai 20 (L4) : GPU vu, échec à l'import de la chaîne d'AI2, corrigé |
| 16/10/2026 | `out_of_stock` au démarrage d'un L4 : disponibilité lue avant création, attente de GPU, types de secours |
| 19/10/2026 | Essai 26 (RENDER-S) : NVML inutilisable, 1,23 € facturés ; RENDER-S retiré, contrôle du GPU, échec signalé tout de suite, facturation par type ; **analyse 97 sur GP1-S, premier essai réel abouti** ; processeur en premier (lot C5) ; serveur passé en DEV1-M |
| 20/10/2026 | Sentinel 2 : travailleur 29 refusé par R2 (403, jeton principal filtré sur l'IP), puis EnvError de GDAL deux fois (dont le travailleur 30 : rasterio refuse les clés AWS en options GDAL) ; seau des modèles dédié, contrôle `controle_r2`, cible `s2-s3` ; correctif jamais rejoué |
| 21/10/2026 | Pause de Sentinel 1, Sentinel 2, tip and cue et VIIRS par le panneau de contrôle |
| 22/10/2026 | Lot Z1 : jeton Earthdata hors du `cloud-init`, groupe de sécurité, validation des résultats |
| après le 22/10/2026 | VIIRS repris au déploiement de 29bcfff ; Sentinel 1, Sentinel 2 et tip and cue toujours en pause |

Récits détaillés des essais Sentinel : `docs/sentinel.md` et `docs/sentinel2.md`.

## 11. Essais réels (lot Z6)

Objectif : la boucle complète tourne au moins une fois en réel et la Veille montre de vraies preuves. Budget des
essais : 8 € HT (`essai_reel.budget_eur`), compté sur les travailleurs lancés par `essai-reel`. Un essai à la fois,
dans l'ordre : a) Sentinel 2 sur le passage 325 ; b) Sentinel 1 sur processeur, deux passages ; c) tip and cue repris
une semaine. Règles : tout est tracé ; aucune reprise automatique sans décision (`essai-reel --lancer` passe malgré la
pause, consigné, sans rien reprendre) ; aucun type facturé à l'heure jamais éprouvé (le POP2 est refusé pendant un
essai : un GP1-S en rupture laisse l'analyse en échec, avec son motif).

Avant et après chaque essai :

```bash
docker compose exec -T taches python scripts/taches.py essai-reel        # « Prêt pour un essai : rien ne manque. »
docker compose exec -T db psql -U mars -d mars -c "SELECT id, tache, etat, commercial_type, zone, cree_le FROM travailleurs WHERE detruit_le IS NULL ORDER BY id"
```

La requête ne doit rendre aucune ligne avant un essai, ni après sa fin (destruction au plus tard à la durée de vie du
travailleur) ; la console Scaleway (Instances en fr-par-1 et fr-par-2, Block Storage) le confirme.

| Essai | Commande | Durée attendue | Coût attendu |
|---|---|---|---|
| a) Sentinel 2, passage 325 | `essai-reel --lancer 325` | 15 à 30 min (image de 7,2 Go en 4 min 23 s mesurées, poids de 2,7 Go, inférence d'une fenêtre sur 8 processeurs) | 0,19 € (une heure entamée de GP1-S), 0,38 € au delà d'une heure |
| b) Sentinel 1, deux passages | `essai-reel --lancer <ID>` deux fois, le second après la destruction du premier | 6 à 15 min chacun (6 min 15 s pour une fenêtre à l'analyse 97) | 0,19 € chacun |
| c) Tip and cue, une semaine | `satellites-pause off tip_and_cue` et `satellites-pause off sentinel1` (décision), puis `on` sept jours après | une semaine | 0,6 € attendus (2,45 € par mois au rejeu), 2,66 € au plus (2 sessions par jour) |

Tip and cue ne demande qu'aux capteurs en service (`missions_retenues`) : repris seul, Sentinel 1 restant en pause,
il ne produirait aucune observation. Le reprendre avec Sentinel 1 remet aussi en route la sélection automatique de
Sentinel 1 (corridors et navires des listes), dans son plafond de 2 scènes par jour et le budget de 8 € du mois.
`--jusqua` ne sert pas ici : il programme une reprise, pas une fin ; la remise en pause se fait à la main.

