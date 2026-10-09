# Déploiement sur le serveur Scaleway (DEV1-S)

Le serveur (`/opt/mars_c2`) fait tourner en permanence, avec Docker Compose, six services : la base (`db`),
l'API (`backend`), l'interface (`web`), la collecte AISStream (`collector`), l'ingestion en base (`ingest`) et les
tâches planifiées (`taches` : règles en continu, archivage sur R2, purge, sauvegarde, surveillance du disque). Pas de
cron sur l'hôte : tout redémarre avec la plateforme (`restart: unless-stopped`). Les images sont construites sur le
Mac en linux/amd64, publiées sur le registre GitHub (paquets privés `ghcr.io/antoine2pommereau/mars_c2-backend`,
`mars_c2-web` et `mars_c2-scripts`), puis téléchargées par le serveur (section « Construire sur le Mac et déployer ») :
le serveur n'a ni la place ni la mémoire pour les construire, et les gros transferts par SSH échouent depuis le Mac
(« Result too large »). Le service d'inférence radar reste sur le Mac (GPU
Apple) ; sur le serveur, l'API le signale « injoignable », ce qui est attendu.

Rien n'est ouvert sur Internet hormis SSH : la base n'a aucun port publié, l'API et l'interface écoutent sur
127.0.0.1 et se consultent par un tunnel SSH. C'est le rôle de `docker-compose.serveur.yml`.

## Première installation

Sur le serveur (Ubuntu 24.04, `root`) :

```bash
# Docker et Docker Compose (2.24.4 au moins, pour les étiquettes !reset et !override)
curl -fsSL https://get.docker.com | sh
docker compose version

# Mémoire d'échange : la DEV1-S n'a que 2 Go de mémoire
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

git clone https://github.com/Antoine2Pommereau/mars_c2.git /opt/mars_c2 && cd /opt/mars_c2
mkdir -p data/ais_live data/listes
```

Fichier `/opt/mars_c2/.env` (jamais versionné, `chmod 600`) :

```bash
AISSTREAM_API_KEY=ta_cle
COMPOSE_FILE=docker-compose.yml:docker-compose.serveur.yml
COMPOSE_PROFILES=direct
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
# Seau en juridiction européenne : https://<identifiant_du_compte>.eu.r2.cloudflarestorage.com
R2_ENDPOINT=https://<identifiant_du_compte>.r2.cloudflarestorage.com
R2_BUCKET=mars-c2
```

`COMPOSE_FILE` et `COMPOSE_PROFILES` font qu'un simple `docker compose up -d` applique la surcouche du serveur et
démarre collecte, ingestion et tâches (profil `direct` inactif sur le Mac : une seule collecte par clé).

Connexion du serveur au registre GitHub, une fois, avec un jeton personnel **en lecture seule** (GitHub, Settings,
Developer settings, Personal access tokens, Tokens (classic), portée `read:packages`) enregistré dans un fichier lisible
par root seulement :

```bash
umask 077 && nano /root/.ghcr_lecture            # coller le jeton, enregistrer
docker login ghcr.io -u antoine2pommereau --password-stdin < /root/.ghcr_lecture
```

Premier démarrage : publier les images depuis le Mac (section « Construire sur le Mac et déployer », étapes 1 et 2),
télécharger les images, démarrer, charger les listes de surveillance (téléchargées par le conteneur taches, rien à
copier), puis construire les masques France :

```bash
cd /opt/mars_c2 && docker compose pull && docker compose up -d --no-build
docker compose exec taches python scripts/taches.py listes
docker compose exec taches python scripts/build_masks.py --sans-cache --jours 7
```

## Configurer Cloudflare R2

1. Tableau de bord Cloudflare, R2, « Create bucket » : nom `mars-c2`, emplacement automatique (Europe de
   préférence). Ne pas activer d'accès public.
2. R2, « Manage R2 API Tokens », « Create API token » : permission **Object Read & Write**, limitée au seau
   `mars-c2`. Noter l'identifiant de clé, le secret et le point d'accès S3
   (`https://<identifiant_du_compte>.r2.cloudflarestorage.com`) ; les reporter dans `.env`.
3. Ne pas mettre de règle de cycle de vie sur le seau : la suppression des anciennes sauvegardes est faite par la
   tâche elle même, après vérification de la nouvelle.

**Seau en juridiction européenne.** Un seau créé avec la juridiction « European Union » n'a pas la même adresse :
`R2_ENDPOINT=https://<identifiant_du_compte>.eu.r2.cloudflarestorage.com`. Avec l'adresse sans `.eu`, tout accès est
refusé (`AccessDenied` ou `NoSuchBucket`, alors que la clé et le nom du seau sont justes) : cas rencontré au premier
déploiement. Le tableau de bord du seau, onglet « Settings », donne l'adresse S3 exacte à reporter.

Contrôle d'accès, depuis le serveur :

```bash
docker compose exec taches python scripts/taches.py sauvegardes   # liste vide ou sauvegardes, sans erreur
```

Organisation du seau :

| Préfixe | Contenu |
|---|---|
| `ais/positions/zone=…/date=AAAA-MM-JJ/` | Positions brutes d'une journée et d'une zone, un fichier Parquet par passage d'archivage (zstd, trié par navire puis par instant, colonnes identiques à la collecte) |
| `ais/statiques/zone=…/date=AAAA-MM-JJ/` | Messages statiques, même organisation |
| `sauvegardes/mars_AAAAMMJJTHHMMSS.dump` | Sauvegardes de la base (`pg_dump` au format personnalisé), les 7 plus récentes |

## Tâches planifiées (service `taches`)

Chaque nuit à 02:30 UTC (`TACHES_HEURE`), dans cet ordre, chacune consignée dans la table `task_runs` :

1. **Archivage** des journées terminées (veille ou avant, 30 minutes après minuit) : par dossier zone et type,
   les petits fichiers Parquet **déjà ingérés** sont regroupés en un fichier, envoyé sur R2 en une seule requête avec
   son MD5 (R2 refuse un contenu altéré), puis vérifié (voir « Confirmation des envois » ci dessous). L'archive est inscrite dans la table `archives` avec la
   liste des fichiers d'origine ; seuls ces fichiers, ingérés et confirmés, sont supprimés du serveur. Un fichier pas
   encore ingéré reste en place (signalé dans le journal) et sera archivé plus tard dans une seconde partie. Après un
   arrêt entre l'envoi et la suppression, la tâche supprime sans renvoyer : pas de doublon sur R2.
2. **Purge** des positions de plus de 30 jours (`CONSERVATION_JOURS`), journée par journée, **seulement si la
   journée est archivée** ; retrait de la journée de `ais_days` et du registre d'ingestion des dossiers disparus.
3. **Sauvegarde** : `pg_dump` compressé envoyé en flux sur R2, sans fichier local, **sans les données de
   `positions`** (dans l'archive) ni du registre d'ingestion. Vérifiée par le code de sortie de `pg_dump`, puis par
   la taille et le MD5 de l'objet stocké (relecture) ; une sauvegarde invalide est supprimée. Les 7 dernières sont gardées (`SAUVEGARDES_GARDEES`), les plus
   anciennes supprimées seulement après la vérification de la nouvelle.

4. **Listes de surveillance**, consignées comme les autres tâches (`listes_opensanctions`, `listes_gur`) :
   * **OpenSanctions, chaque jour** : jeu maritime en téléchargement direct
     (`https://data.opensanctions.org/datasets/latest/maritime/maritime.csv`, environ 5 Mo), licence CC BY NC 4.0,
     attribution « Données OpenSanctions.org » affichée dans le détail des listes de la barre d'état ;
   * **GUR, chaque semaine** : empreinte de `Vessels1.db` lue par l'API GitHub dans le dépôt
     `FormerLab/shadow-fleet-tracker-light` (licence MIT) ; téléchargement et import seulement si elle a changé.

   Le fichier est lu en mémoire (aucun fichier écrit sur le disque), comparé à la liste en base, et la source n'est
   remplacée que s'il y a du changement, en une transaction avec le rafraîchissement de `vessel_watch`. Le journal
   (`task_runs.details`) donne le nombre de navires ajoutés, retirés et modifiés, avec au plus 20 exemples de
   chaque ; les règles des listes (alertes WATCHLIST, gravité des changements d'identité) sont relancées aussitôt.
   En cas d'échec (site injoignable, fichier vide ou illisible), la liste précédente reste en place, la tâche est
   retentée une heure plus tard, et l'indicateur « Listes » de la barre d'état passe à l'orange avec la mention
   « mise à jour en échec, liste précédente conservée ».

5. **Calendrier des passages Sentinel 1 et 2**, chaque jour (tâche `passages`) : passages acquis des trois derniers
   jours (trente au premier passage) depuis le catalogue public de Copernicus Data Space, passages prévus depuis les
   plans d'acquisition de l'ESA (cinq fichiers KML d'environ 2 Mo, lus en mémoire), puis infrastructures et navires
   des listes couverts par chaque emprise. Métadonnées seulement, aucune image. Sources, fiabilité et limites :
   `docs/passages_satellites.md`. Une source en échec n'empêche pas les autres (détail dans `task_runs`) ; la barre
   d'état indique le prochain passage sur la région affichée et la date de la dernière mise à jour.
6. **Détections nocturnes VIIRS**, chaque jour à 06:30 UTC (`VIIRS_HEURE`, tâche `viirs`), après la publication des
   données de la nuit : un travailleur éphémère Scaleway analyse les granules des trois dernières nuits pas encore
   traitées (rattrapage automatique). Chaque minute, le conteneur surveille les travailleurs : résultats traités,
   instances finies ou trop vieilles détruites, orphelins détruits, chaque exécution journalisée dans `task_runs`
   (tâche `travailleur_viirs` : durée, coût estimé, résultat). Détail : `docs/travailleurs_viirs.md`.

**Confirmation des envois.** L'ETag d'un objet envoyé en une seule requête est son MD5 : il est comparé au MD5
calculé avant l'envoi, avec la taille. L'ETag d'un objet envoyé en plusieurs morceaux (au delà de 16 Mo, cas des
sauvegardes en flux) n'est pas le MD5 du fichier : il se termine par « tiret, nombre de morceaux ». L'objet est
alors relu depuis R2 et son MD5 recalculé, comparé à celui calculé pendant l'envoi. Les archives quotidiennes
(environ 6 Mo par zone et par type) partent toujours en une requête ; la relecture ne concerne que les envois en
morceaux. Dans tous les cas, un fichier n'est supprimé du serveur que s'il est ingéré et confirmé.

**Règles en continu** (toutes les 5 minutes, section `continu` de `config/rules.yaml`), sur une fenêtre glissante
de 24 heures bornée par la dernière position reçue :

| Règle | Alerte | Prérequis |
|---|---|---|
| Rendez vous | `RENDEZVOUS` | trait de côte (`land`) |
| Coupure AIS | `AIS_GAP` | zone de réception fiable (`reception_cells`) |
| Navire d'une liste dans nos eaux | `WATCHLIST`, une par passage (12 h sans position ouvrent un passage nouveau), niveaux fort, sanctionné, flotte fantôme, suspect GUR | listes importées |
| Changement d'identité | `IDENTITY_CHANGE` : nouveau nom confirmé 6 h, ou même OMI sous un autre MMSI | aucun |

Après les règles, chaque cycle tient aussi les statistiques de la frise de l'interface (`stats_minute`,
`stats_10min` : positions par minute, navires par tranche de 10 minutes ; `stats_10min_region` : la même chose par
région, pour le sélecteur de la barre d'état). Au premier cycle après la migration 19, les statistiques par région
sont rattrapées sur toutes les positions en base : environ 9 s par journée (mesuré en émulation sur 288 000
positions, 3,2 s), soit 4 à 5 minutes une seule fois pour 30 jours, pendant lesquelles ce cycle des règles est
retardé. Sur une base neuve, toutes les statistiques sont calculées au premier cycle ; l'histogramme et les coupures
du flux apparaissent donc dans la frise dans les 5 minutes qui suivent le déploiement.

Une règle dont le prérequis manque est sautée (le motif apparaît dans `task_runs`) : lancer d'abord la construction
des masques. Les alertes sont mises à jour en place (clé `rule_key`) : statut et décisions des opérateurs sont
conservés ; une alerte encore vierge qui n'est plus détectée est retirée. Les minutes où le flux AIS est coupé
(moins de 20 % de la médiane des positions par minute) ne comptent pas dans la durée d'un silence : une interruption
d'AISStream ne fait pas apparaître une coupure sur chaque navire. Un cycle n'est journalisé que s'il crée ou retire
des alertes ; chaque cycle est consigné dans `task_runs` (tâche `regles`, deux jours gardés).

Toutes les 10 minutes : espace libre du disque. Sous 15 % (`ALERTE_DISQUE_PCT`), une ligne `ALERTE DISQUE` dans
le journal du service, et `"alerte_disque": true` dans `/api/ingestion`.

Une tâche réussie ne rejoue pas avant sa période (un jour, sept pour le GUR) ; une tâche en échec est retentée une
heure plus tard. Au premier démarrage dans la journée, après 02:30, les tâches dues s'exécutent tout de suite. Commandes manuelles :

```bash
docker compose exec taches python scripts/taches.py archiver      # ou purger, sauvegarder, disque, regles
docker compose exec taches python scripts/taches.py listes        # les deux listes, sans attendre la nuit
docker compose exec taches python scripts/taches.py passages      # calendrier des passages, sans attendre la nuit
docker compose exec taches python scripts/taches.py viirs         # nuits VIIRS à traiter, sans attendre 06:30
docker compose exec taches python scripts/taches.py travailleurs  # état des travailleurs (dix derniers)
docker compose exec taches python scripts/taches.py journal-travailleur [ID]   # journal et état Scaleway d'un travailleur
docker compose exec taches python scripts/taches.py viirs-reevaluer             # règles VIIRS appliquées aux détections en base
docker compose exec taches python scripts/taches.py detruire-travailleurs   # arrêt d'urgence de toute instance
docker compose exec taches python scripts/taches.py sauvegardes   # liste des sauvegardes sur R2
docker compose exec taches python scripts/import_watchlist.py --gur data/listes/Vessels1.db   # fichier local
```

**Mesures pour la recalibration** (`scripts/mesures_calibration.py`, lecture seule, aucun seuil modifié) :
intervalles entre messages par type de navire, par navire et par zone ; paires d'intervalles par cellule et surface
de la zone de réception fiable pour plusieurs valeurs de `reception.min_pairs`, `min_coverage` et `max_interval_s` ;
épisodes et alertes par règle et par jour ; puis, pour chaque seuil de `config/rules.yaml`, sa valeur, la mesure et
une proposition argumentée ; enfin la **portée de réception** : par région, distance à la côte des positions reçues
(médiane, 9e décile, maximum, part au delà de 12 milles, source des positions), sur 3 000 positions au plus par région
tirées au hasard (il faut le trait de côte, table `land`). Calcul journée par journée : 140 Mo de mémoire mesurés pour le processus (limite du
conteneur : 768 Mo) et quelques dizaines de Mo de
fichiers temporaires PostgreSQL par journée. Le rapport sort sur la sortie standard, à rapatrier sur le Mac :

```bash
# Sur le serveur
cd /opt/mars_c2 && docker compose exec -T taches python scripts/taches.py mesures --jours 14 > /tmp/mesures_calibration.md
# Sur le Mac (petit fichier : scp convient)
scp root@IP_DU_SERVEUR:/tmp/mesures_calibration.md ~/Documents/"Projet Perso"/MarsC2/mars_c2/mars_c2/docs/
```

## Travailleurs éphémères Scaleway (étape 3)

Une fois, avant la première nuit VIIRS (détail et coûts : `docs/travailleurs_viirs.md`) :

1. **Réseau privé** : console Scaleway, Network, VPC, région Paris, Private Networks, Create : `mars-c2-travailleurs`
   (dans le VPC par défaut). Puis Instances, le serveur, onglet Private Networks, Attach. Sur le serveur, l'adresse
   attribuée (nouvelle interface, en 172.16.x.x en général) :
   ```bash
   ip -4 -br addr        # l'interface du réseau privé et son adresse : MARS_IP_PRIVEE
   ```
   Copier aussi l'identifiant **du réseau privé** : page du réseau `mars-c2-travailleurs`, onglet **Overview**, champ
   ID. C'est `SCW_PRIVATE_NETWORK_ID`. Ce n'est **pas** l'identifiant du VPC qui le contient (page du VPC) : avec
   celui du VPC, le rattachement échoue.
2. **Clé d'API limitée au projet** : console, Identity and Access Management (IAM), Applications, Create application
   `mars-c2-travailleurs` (une application, pas un utilisateur) ; Policies, Create policy rattachée à cette application,
   une règle avec la portée **Project** (le projet de MARS C2 seulement, jamais l'organisation) et exactement deux
   ensembles de droits : **InstancesFullAccess** (créer, démarrer, détruire les instances, cloud-init, cartes réseau)
   et **PrivateNetworksFullAccess** (rattacher une carte au réseau privé ; PrivateNetworksReadOnly ne suffit pas, le
   rattachement est refusé, constaté au premier essai). Puis, sur l'application, API keys, Generate : garder la clé
   d'accès et la clé secrète, et l'identifiant du projet (Project settings).
3. **Jeton Earthdata** : https://urs.earthdata.nasa.gov (compte gratuit), Generate Token. Valable 60 jours.
   **Accepter aussi la licence des données LANCE NRT** sur ce compte (sinon la NASA renvoie une page HTML au lieu du
   fichier). Vérifier depuis le serveur, avec une granule récente (adresse donnée par le catalogue CMR) :
   `curl -sL -H "Authorization: Bearer $EARTHDATA_TOKEN" -o /tmp/essai.nc "<adresse>" && ls -l /tmp/essai.nc && head -c 8 /tmp/essai.nc | od -c && rm /tmp/essai.nc`
   : environ 40 Mo commençant par `211   H   D   F` (signature HDF5).
4. **Variables à ajouter au `.env` du serveur** (sans commentaire en fin de ligne) :
   ```
   SCW_ACCESS_KEY=SCW...
   SCW_SECRET_KEY=...
   SCW_PROJECT_ID=...
   SCW_PRIVATE_NETWORK_ID=...
   SCW_SERVEUR_PRINCIPAL=...
   MARS_IP_PRIVEE=172.16.x.x
   EARTHDATA_TOKEN=...
   ```
   `SCW_ACCESS_KEY` sert à la ligne de commande Scaleway, MARS C2 ne la lit pas ; `SCW_SERVEUR_PRINCIPAL` est
   l'identifiant du serveur mars-c2, jamais détruit. Pas de commentaire en fin de ligne dans ce fichier.
   L'identifiant du serveur se lit sur le serveur lui même :
   `curl -s "http://169.254.42.42/conf?format=json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['id'], d['name'], d['location']['zone_id'])"`.
   Protection supplémentaire, à poser une fois : la protection contre la suppression de Scaleway, qui bloque toute
   destruction du serveur (API et console) tant qu'elle n'est pas levée, et que MARS C2 vérifie aussi :
   `curl -s -X PATCH -H "X-Auth-Token: $SCW_SECRET_KEY" -H "Content-Type: application/json" -d '{"protected": true}' https://api.scaleway.com/instance/v1/zones/fr-par-1/servers/$SCW_SERVEUR_PRINCIPAL`
   (zone du serveur à adapter ; la réponse doit contenir `"protected": true`).
   La zone et le type d'instance des travailleurs sont dans `config/rules.yaml` (`travailleurs`). Puis
   `docker compose up -d --no-build web taches` (nginx publie le port 8090 sur l'adresse privée).
5. **Vérifier** sans attendre 06:30 : procédure d'essai ci dessous.

**Comment un travailleur démarre.** L'image de démarrage est résolue à chaque lancement dans le catalogue Scaleway :
Ubuntu 24.04 officielle (`ubuntu_noble`), zone fr-par-1, type DEV1-M, architecture x86_64, sur disque local ; sa fiche
donne son volume racine (type `l_ssd`, 10 Go). Le volume de démarrage est créé à partir de cette image, agrandi à
20 Go (`disque_go`). Après la création, l'instance est relue : image attendue, démarrage sur disque local, volume
racine présent, local, au moins aussi grand que celui de l'image ; sinon échec clair et destruction immédiate. Docker
est installé par le script de démarrage (paquet `docker.io` d'Ubuntu, environ une minute). Ensuite, ordre vérifié :
instance créée éteinte,
rattachée au réseau privé (attendu jusqu'à l'état « available » de la carte), cloud-init écrit avec l'adresse MAC de la
carte privée, démarrage (attendu jusqu'à « running »). Une étape en échec : le lancement est journalisé « echec » dans
`task_runs` avec son motif, l'indicateur Satellites passe à l'orange, et l'instance est détruite aussitôt (par le
garde). Au démarrage, `travailleurs/demarrage.sh` trouve l'interface privée par sa MAC, la configure en DHCP si le
système ne l'a pas fait, puis signale au serveur qu'il le joint (état « reseau », qui compte comme signe de vie) et
envoie son journal ; il le renvoie après le téléchargement de l'image puis à la fin de l'analyse. Un travailleur sans
signe de vie 10 minutes après sa création (`travailleurs.delai_demarrage_min`) est détruit, motif enregistré. Un seul
travailleur VIIRS actif à la fois : le verrou est un index unique en base, valable entre la boucle (rattrapage au
démarrage du conteneur) et la commande manuelle.

**Vérifier l'image et le volume d'un travailleur dans la console Scaleway** (Instances, zone fr-par-1, l'instance
`mars-travailleur-<n>`) :
* onglet **Overview** : champ **Image** « Ubuntu 24.04 Noble Numbat » (pas vide, pas une autre image) ; type DEV1-M ;
* onglet **Storage** (ou Volumes) : un seul volume, **Local Storage**, de 20 Go, marqué volume de démarrage ;
* la **console série** (bouton Console) : en quelques dizaines de secondes, le chargeur GRUB puis les messages de
  démarrage d'Ubuntu et de cloud-init, puis les lignes « MARS » du script de démarrage.

**L'écran « UEFI Interactive Shell v2.2 »**, suivi d'une table de correspondance (`BLK0: PciRoot(0x0)/Pci(...)`) et
de l'invite `Shell>`, est l'interpréteur du micrologiciel : il s'affiche quand aucun système amorçable n'est trouvé
sur le volume de démarrage (volume vide, ou image non amorçable). C'était la cause réelle des premiers essais : aucun
système ne démarrait, d'où aucun contact, ni SSH ni ping. Le lancement le refuse désormais (vérification de l'image et
du volume) ; si l'écran réapparaît malgré tout, noter l'image et le volume affichés par la console, puis
`taches.py detruire-travailleurs`.

**Données inaccessibles.** Le travailleur vérifie chaque fichier (taille d'au moins 1 Mo, signature HDF5) et nomme la
cause : « licence LANCE non acceptée sur le compte Earthdata » (redirection vers /profiles/licenses), « jeton
EARTHDATA_TOKEN absent, invalide ou expiré » (redirection vers la connexion Earthdata, ou HTTP 401 et 403), sinon le
code HTTP, le type de contenu et les premiers octets reçus. Données inaccessibles, ou premier granule en échec : il
s'arrête aussitôt sans télécharger les autres ; l'exécution est en échec avec ce motif (`taches.py travailleurs`,
`task_runs`, indicateur Satellites orange avec son motif).

**Destruction d'un travailleur**, toujours par le garde, une étape par minute selon l'état chez Scaleway : en marche,
action `terminate` (instance, volume local et adresse supprimés ensemble) ; « stopped in place » (éteint depuis son
système, encore alloué : la suppression y est refusée, « resource_still_in_use, instance should be powered off »),
action `poweroff`, puis, une fois « stopped », suppression de l'instance et de ses volumes ; en transition (starting,
stopping), attente. Le script de démarrage ne s'éteint plus qu'au bout de 10 minutes après son résultat : le serveur
le détruit en marche, par `terminate`. Une étape refusée par l'API est reprise à la minute suivante sans bloquer la
surveillance des autres travailleurs.

**Preuves images** (vignettes des détections VIIRS) : rangées sur R2 par le conteneur taches, relues par l'API avec
les mêmes identifiants R2 du `.env` (le service backend lit déjà ce fichier). Purge chaque nuit (tâche `preuves`) :
30 jours sans alerte liée, sans limite sinon. Vérifier : `curl -s -o /tmp/v.png -w "%{http_code} %{size_download}\n"
localhost:8000/api/preuves/<id>.png` (200, environ 5 Ko ; identifiants dans `/api/viirs/detections/<id>`).

**Diagnostic d'un travailleur muet**, sans ouvrir de port :
* avant de détruire un travailleur sans signe de vie (ou dont la durée de vie est dépassée), le serveur relève son
  état chez Scaleway (état, image, mode de démarrage, volume de démarrage, cartes du réseau privé et leur adresse
  attribuée) dans `travailleurs.diagnostic`, affiché par `taches.py journal-travailleur <n>` ; l'adresse privée vient
  de l'API IPAM (« inconnue » si la clé n'y a pas droit, sans effet sur le reste) ;
* s'il a joint le serveur au moins une fois, son journal est en base :
  `docker compose exec taches python scripts/taches.py journal-travailleur` (le dernier) ou `... journal-travailleur 3` ;
* sinon, lire sa **console série** : console Scaleway, Instances, l'instance `mars-travailleur-<n>` (zone fr-par-1),
  bouton **Console** en haut à droite ; sans se connecter, on y lit les lignes « MARS » du script de démarrage (interface
  trouvée ou non, adresse DHCP obtenue ou non, serveur joint ou non, avec `ip -br addr` et `ip route` en cas d'échec).
  La fenêtre est de 10 minutes après la création, avant la destruction par le serveur. Pour diagnostiquer plus
  longtemps, porter temporairement `delai_demarrage_min` à 30 dans `config/rules.yaml` du serveur (relu sans
  redémarrage), puis le remettre à 10 ;
* état des derniers travailleurs et de leurs motifs : `docker compose exec taches python scripts/taches.py travailleurs`.

**Procédure d'essai d'un travailleur** (deux terminaux sur le serveur, plus la console Scaleway) :

| Étape | Commande ou lieu | Ce qu'on doit lire |
|---|---|---|
| 0. Rien d'actif | `docker compose exec taches python scripts/taches.py travailleurs` | aucun travailleur d'état `demande`, `cree` ou `demarre` sans date de destruction ; console : aucune instance `mars-travailleur-*` |
| 1. Suivre | terminal 2 : `docker compose logs -f taches \| grep -iE "travailleur\|viirs"` | rien encore |
| 2. Lancer | terminal 1 : `docker compose exec taches python scripts/taches.py viirs` | une ligne `viirs : {"travailleur": N, "instance": "...", "type": "DEV1-M", "mac_privee": "02:00:...", ...}` en moins de 2 minutes ; sinon `viirs : ÉCHEC` et son motif (image, volume de démarrage, rattachement, démarrage), instance déjà détruite |
| 2 bis. Image | console Scaleway, l'instance : Overview, puis Console | image « Ubuntu 24.04 Noble Numbat », volume local de 20 Go ; à la console, GRUB et Ubuntu, jamais `Shell>` |
| 3. Doublon | relancer aussitôt la même commande | `viirs : {"en_cours": N}`, aucune seconde instance |
| 4. Réseau | dans les 2 à 3 minutes : `taches.py journal-travailleur` | lignes « interface privée ens… : 172.16.8.x/22 », « serveur joint sur le réseau privé », « système : Ubuntu 24.04 », « installation de Docker » ; depuis le serveur, `ping -c 2 172.16.8.x` répond ; si rien au bout de 5 minutes : console série (ci dessus) |
| 5. Image et analyse | `taches.py journal-travailleur`, quelques minutes plus tard | « téléchargement de l'image », puis les lignes du script VIIRS (une par granule, avec `telecharge_s`, `detection_s`, `rss_max_mo` et `n` détections) ; en cas de données inaccessibles, une seule ligne de granule, le motif, puis « arrêt du travailleur » |
| 6. Résultat | terminal 2 | `travailleurs : {"traites": 1, ...}` puis `{"detruits": 1, ...}` ; console : l'instance disparaît |
| 7. Bilan | `curl -s localhost:8000/api/ingestion \| python3 -m json.tool \| grep -A12 '"viirs"'` et `curl -s localhost:8000/api/metrics \| grep travailleurs` | dernière nuit traitée, travailleur `termine`, `cout_eur` 0,0202 ; dans l'interface, indicateur Satellites vert, couche VIIRS et piste des nuits remplies |

En cas d'échec à n'importe quelle étape : l'instance est détruite par le serveur (aussitôt, ou au plus tard 10 minutes
après la création) ; le motif est dans `taches.py travailleurs`, l'indicateur Satellites passe à l'orange.

**Registre GitHub** : `.github/workflows/nettoyage_registre.yml` ne garde que les trois dernières versions de chaque
image (chaque lundi, et à la demande dans l'onglet Actions). Une fois par paquet (`mars_c2-backend`, `mars_c2-web`,
`mars_c2-scripts`) : github.com, Packages, le paquet, Package settings, Manage Actions access, Add repository
`mars_c2`, rôle **Admin**. Les images portent l'étiquette `org.opencontainers.image.source`, qui relie le paquet au
dépôt à la publication suivante.

**Mesures pour Grafana** : `curl -s localhost:8000/api/metrics` (format texte de Prometheus : exécutions et durée de
chaque tâche, travailleurs, minutes et coût, détections VIIRS, passages, alertes). Aucun outil installé ; un agent
Grafana pourra lire cette adresse plus tard.

## Masques France (trait de côte, mouillages, réception)

Ponctuellement, dans le conteneur `taches` (dépendances incluses dans l'image des scripts) :

```bash
docker compose exec taches python scripts/build_masks.py --sans-cache --jours 7
```

* **Trait de côte** : GSHHG pleine résolution, découpé sur l'emprise des quatre zones avec 0,5° de marge
  (2 005 polygones après subdivision, 3,6 Mo en base). `--sans-cache` lit le shapefile directement dans l'archive
  téléchargée, sans l'extraire, dans un dossier temporaire du conteneur effacé à la fin.
* **Zones de mouillage et zone de réception fiable** : calculées par l'API sur les 7 derniers jours (`--jours 7`) ;
  sans cette option, sur toutes les positions en base, ce qui coûte plus de mémoire et de fichiers temporaires à
  PostgreSQL. Les seuils (`stationary_zones`, `reception`) sont ceux de la calibration danoise, à recalibrer.
* **Coût mesuré** (image linux/amd64 émulée sur le Mac, base de test, 06/10/2026) : 61 s au total dont le
  téléchargement de 149 Mo ; **300 Mo de mémoire au plus** pendant une minute (539 Mo avant lecture économe du
  shapefile : le polygone de l'Eurasie compte plus d'un million de points) ; **142 Mo de disque au plus**,
  l'archive GSHHG seule, effacée à la fin. Sur le serveur, compter le temps de téléchargement en plus ; prévoir au
  moins 300 Mo de disque libre.
* À relancer de temps en temps (chaque semaine par exemple) pour les zones de mouillage et la réception ; le trait
  de côte ne change pas (`--skip-land` pour ne recalculer que les masques déduits de l'AIS).

## Construire sur le Mac et déployer

Le serveur ne construit rien. Les trois images sont construites sur le Mac pour linux/amd64, publiées sur le registre
GitHub, puis téléchargées par le serveur :

| Image | Services | Taille (07/10/2026) |
|---|---|---|
| `ghcr.io/antoine2pommereau/mars_c2-backend` | `backend` | 160 Mo |
| `ghcr.io/antoine2pommereau/mars_c2-web` | `web` | 21 Mo |
| `ghcr.io/antoine2pommereau/mars_c2-scripts` | `collector`, `ingest`, `taches` | 190 Mo |

Chaque publication porte deux étiquettes : `latest`, que le serveur utilise par défaut, et l'empreinte courte du
commit (par exemple `4238202`), pour revenir à une version précise (`MARS_TAG=4238202` dans le `.env` du serveur).
Les couches inchangées ne sont ni republiées ni retéléchargées.

**Connexion du Mac au registre, une fois**, avec un jeton personnel en **écriture** (portée `write:packages`, qui
inclut la lecture), gardé dans le trousseau de macOS plutôt que dans un fichier ou l'historique du terminal :

```zsh
security add-generic-password -a antoine2pommereau -s ghcr-mars-c2 -w      # le jeton est demandé, sans écho
security find-generic-password -a antoine2pommereau -s ghcr-mars-c2 -w | docker login ghcr.io -u antoine2pommereau --password-stdin
```

Les trois paquets sont privés : au premier envoi, GitHub les crée privés ; le jeton de lecture du serveur y a accès
parce qu'ils appartiennent au même compte.

**1. Sur le Mac : construire**, depuis le dépôt à jour (commandes compatibles zsh) :

```zsh
cd ~/Documents/"Projet Perso"/MarsC2/mars_c2/mars_c2 && git pull origin main
touch .env                                         # la surcouche lit .env ; un fichier vide suffit pour construire
DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose -f docker-compose.yml -f docker-compose.serveur.yml \
  --profile direct build backend web collector
R=ghcr.io/antoine2pommereau
for i in mars_c2-backend mars_c2-web mars_c2-scripts; do docker image inspect "${R}/${i}:latest" --format "${i} {{.Architecture}}"; done
```

Les trois doivent afficher `amd64`. Ces images remplacent sur le Mac celles du même nom : pour retrouver des images
natives en local, relancer ensuite `docker compose build backend web`.

**2. Sur le Mac : publier**, avec l'étiquette du commit en plus de `latest` :

```zsh
R=ghcr.io/antoine2pommereau
TAG=$(git rev-parse --short HEAD)
for i in mars_c2-backend mars_c2-web mars_c2-scripts; do
  docker tag "${R}/${i}:latest" "${R}/${i}:${TAG}"
  docker push "${R}/${i}:latest"
  docker push "${R}/${i}:${TAG}"
done
```

Ne publier que les images qui changent est possible (retirer les autres de la liste), mais pas nécessaire : une
couche déjà sur le registre n'est pas renvoyée.

**3. Sur le serveur : télécharger et démarrer.** Code et migrations d'abord (une nouvelle migration de `db/init` ne
s'applique pas seule à une base existante, et la nouvelle API peut en dépendre) :

```bash
cd /opt/mars_c2 && git pull origin main
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mars -d mars < db/init/1X_nom.sql   # chaque migration nouvelle
docker compose pull backend web collector ingest taches
docker compose up -d --no-build
docker image prune -f                              # anciennes versions devenues sans étiquette
```

**Disque juste.** `docker compose pull` télécharge les couches compressées (environ 330 Mo pour les trois images
quand tout change) puis les décompresse (environ 370 Mo) pendant que les anciennes images sont encore présentes et
utilisées : prévoir environ 800 Mo libres. S'il n'y en a pas assez, libérer d'abord, dans cet ordre (la base et ses
données ne sont pas touchées ; la collecte s'interrompt quelques minutes, sans conséquence sur les règles, les minutes
de flux coupé ne comptant pas comme des silences) :

```bash
cd /opt/mars_c2 && git pull origin main
docker compose exec -T db psql -v ON_ERROR_STOP=1 -U mars -d mars < db/init/1X_nom.sql   # migrations nouvelles
docker compose rm -sf backend web collector ingest taches   # arrêt et suppression des conteneurs, pas des volumes
docker image prune -af                                      # images sans conteneur ; la base, en service, garde la sienne
df -h /                                                     # vérifier la place libre avant le téléchargement
docker compose pull backend web collector ingest taches
docker compose up -d --no-build
```

`docker image prune -af` ne retire que les images qu'aucun conteneur n'utilise : celle de la base, en service, reste.
Au passage aux images du registre, les anciennes images locales (`mars_c2-backend`, `mars_c2-web`,
`mars_c2-scripts`, et plus anciennes `mars_c2-collector`, `mars_c2-ingest`, `mars_c2-taches`) partent avec elles.

**Revenir à une version précédente** : `MARS_TAG=<empreinte> docker compose pull` puis
`MARS_TAG=<empreinte> docker compose up -d --no-build` (ou inscrire `MARS_TAG` dans le `.env` du serveur).

**Migration 24 (origine des positions).** `db/init/24_sources_sejours.sql` ajoute la colonne `positions.source`
(aujourd'hui toujours `aisstream`). Valeur par défaut constante : l'ajout est immédiat, sans réécrire la table, quel
que soit le nombre de positions. Elle doit précéder le démarrage de la nouvelle image des scripts : la nouvelle
ingestion écrit cette colonne. Les fichiers Parquet de la collecte portent le même champ ; les fichiers et archives
antérieurs, qui ne l'ont pas, sont lus comme `aisstream` (ingestion, rechargement depuis R2 et compactage de
l'archive le complètent). Contrôle : `SELECT source, count(*) FROM positions WHERE ts > now() - interval '1 hour'
GROUP BY 1`.

Ne jamais lancer `docker system prune --volumes` : il effacerait le volume de la base.

## Vérifier

```bash
docker compose ps                                   # six services « running »
docker compose logs --tail 5 collector              # une ligne par minute : messages, navires par zone
docker compose logs --tail 5 ingest                 # une ligne par cycle : positions lues, conservées, allégées
docker compose logs --tail 20 taches                # tâches de la nuit, alertes disque
curl -s localhost:8000/api/ingestion                # retard, disque, dernière exécution de chaque tâche
curl -s localhost:8000/api/clock                    # "live": true, "speed": 1.0
```

Ce qu'on doit lire :
* `/api/ingestion` : `retard_s` de l'ordre de la minute ; `alerte_disque` à `false` ; `disque` avec une mesure de
  moins de 30 minutes (`perimee` à `false`) ; dans `taches`, `archivage`, `purge`, `sauvegarde` et `regles` à `ok`
  (`regles` daté de moins de 5 minutes).
* Les règles, cycle par cycle, et les alertes produites :

```bash
docker compose exec taches python scripts/taches.py regles      # un cycle à la demande, détail complet
docker compose exec db psql -U mars -d mars -c "SELECT type, severity, status, count(*) FROM alerts
  WHERE detected_at > now() - interval '1 day' GROUP BY 1, 2, 3 ORDER BY 1, 2"
```

  Ce qu'on doit lire : pour `rendezvous` et `ais_gap`, des compteurs (et non « trait de côte absent » ou « zone de
  réception absente », signe que les masques n'ont pas été construits) ; `exclus_coupure_du_flux` non nul seulement
  après une interruption d'AISStream ; pour `watchlist`, autant de passages que de navires des listes présents.
* Le journal de l'archivage, chaque matin : `fichiers_non_ingeres` à 0, `fichiers_supprimes` égal au nombre de
  fichiers de la veille (environ 11 500 pour quatre zones), `octets_envoyes` de l'ordre de 25 Mo.
* Contrôle croisé en base :

```bash
docker compose exec db psql -U mars -d mars -c "SELECT day, kind, count(*) AS parties, sum(rows) AS lignes,
  pg_size_pretty(sum(bytes)) AS taille FROM archives GROUP BY 1, 2 ORDER BY 1 DESC, 2 LIMIT 8"
docker compose exec db psql -U mars -d mars -c "SELECT task, status, started_at, details FROM task_runs
  ORDER BY id DESC LIMIT 6"
```

Liste de surveillance en base :

```bash
docker compose exec db psql -U mars -d mars -c "SELECT v.name, v.mmsi, v.imo, v.flag, w.level, w.matched_by
  FROM vessel_watch w JOIN vessels v ON v.id = w.vessel_id ORDER BY w.rank"
```

## Restauration complète

La sauvegarde contient le schéma et toutes les tables sauf les données de `positions` et du registre
d'ingestion. Les positions se rechargent depuis l'archive R2 ; celles des fichiers encore sur le serveur (journée
en cours et veille non archivée) sont rechargées par l'ingestion elle même, puisque le registre restauré est vide.

```bash
cd /opt/mars_c2
docker compose stop backend ingest taches          # la collecte peut continuer : elle n'écrit que du Parquet

# 1. Récupérer la sauvegarde voulue (la plus récente en bas de liste)
docker compose run --rm taches python scripts/taches.py sauvegardes
docker compose run --rm -v /opt/mars_c2/restauration:/restauration taches \
  python scripts/taches.py telecharger sauvegardes/mars_AAAAMMJJTHHMMSS.dump /restauration/mars.dump

# 2. Recréer la base vide et y restaurer schéma et tables
docker compose exec db dropdb -U mars --maintenance-db=postgres mars
docker compose exec db createdb -U mars mars
docker compose exec -T db pg_restore -U mars -d mars --no-owner --exit-on-error < restauration/mars.dump

# 3. Positions des journées archivées, depuis R2 (au plus les 30 derniers jours : la purge retirerait les autres)
docker compose run --rm taches python scripts/taches.py restaurer-positions --du AAAA-MM-JJ --au AAAA-MM-JJ

# 4. Relance : l'ingestion recharge les fichiers encore présents sur le disque
docker compose up -d backend ingest taches
rm -r restauration
```

Le rechargement depuis l'archive a son propre allègement, dans l'ordre chronologique et toutes zones réunies :
l'ingestion en direct écarterait ces messages comme arrivés en retard. Il refuse une journée qui a déjà des
positions en base. Éprouvé le 06/10/2026 sur une base de test : 11 554 positions et 1 079 navires rechargés pour
le 05/10, exactement le résultat de l'ingestion d'origine.

## Consulter l'interface

Depuis le Mac : `ssh -N -L 8080:localhost:8080 root@IP_DU_SERVEUR`, puis http://localhost:8080. L'horloge est en
direct (vitesse 1) ; la frise permet de revenir sur une période passée (sélecteur de vitesse du rejeu), le bouton
« Direct » d'y revenir.

## Volumes à l'équilibre (débit du 06/10/2026)

Débit : environ 680 messages par minute pour les quatre zones, soit 979 000 par jour (76 % de positions, 24 % de
messages statiques, proportions de la collecte du 05/10), et 34 000 positions conservées par heure en base.

**Base** (30 jours de positions) :
* 34 000 × 24 × 30 = 24,5 millions de positions ;
* 282 octets par position index compris, mesurés sur 2 millions de positions : table 101, index spatial 66, index
  navire et instant 54, index sur l'instant 39, clé primaire 22 ;
* soit 6,9 Go, environ **7,5 Go** avec l'espace libéré par la purge quotidienne et les autres tables (navires,
  identités, registre d'ingestion, listes) ;
* négligeables : calendrier des passages, 3,5 Ko par passage index compris, une dizaine par jour, soit 35 Ko par jour
  (13 Mo par an, gardés) ; statistiques de la frise par région, 576 lignes par jour, environ 2 Mo à 35 jours.
* VIIRS : 12 granules par nuit (environ 1 Ko chacune) et quelques dizaines de détections (environ 300 octets chacune
  index compris), soit moins de 30 Ko par nuit ; aucune image n'est gardée, ni sur le serveur ni sur le travailleur.

**Disque du serveur** : base 7,5 Go, images Docker et système environ 6 Go, Parquet local de moins de deux jours
environ 0,3 Go, journaux bornés à 0,2 Go : environ 14 Go sur 40, 65 % libres. **Tant que le disque fait 8 Go, il
se remplit** : la base croît de 230 Mo par jour jusqu'à l'équilibre, et l'alerte des 15 % se déclenchera en une à
deux semaines. Agrandir le disque avant.

**R2** :
* archive : 27,4 octets par position et 22,5 par message statique après compactage (mesuré sur l'archivage de
  test), soit environ 26 Mo par jour, 0,77 Go par mois, 9,4 Go par an ;
* sauvegardes sans positions : moins de 10 Mo chacune, moins de 70 Mo pour les 7 ;
* les 10 Go gratuits sont atteints après **environ 13 mois** de collecte. Au delà, 0,015 dollar par Go et par mois.

**Règles en continu** : un cycle sur 867 000 positions (24 heures au débit actuel), mesuré sur le Mac en émulation
linux/amd64, donc une borne haute : 1,4 s pour les coupures AIS, presque rien pour les listes et les identités, et
33 s pour les rendez vous dans un cas volontairement extrême (600 navires immobiles serrés, 5 700 épisodes) ;
181 Mo de mémoire au plus pour le processus, environ 100 Mo de fichiers temporaires PostgreSQL. Sur le serveur, les
rendez vous montaient à 132 s quand la fenêtre de 24 heures se remplissait (4 485 épisodes dont 4 483 côtiers) :
depuis la version 2026.10.18, les positions à moins de 1 km de la terre (`rendezvous.coast_prefilter_m`) sont
écartées avant de former les paires (mesures dans CLAUDE.md, section 9).

**Compression TimescaleDB** : pas utile à cette échelle. Avec 30 jours en base, 7,5 Go tiennent largement sur
40 Go ; la compression (de l'ordre de 10 fois) demanderait de changer l'image de la base, de refaire la clé de
`positions` et de transformer la purge, pour un gain d'espace dont on n'a pas besoin. Elle deviendrait intéressante
si l'on voulait garder 6 à 12 mois en base. Levier plus simple si l'espace manquait : l'index spatial de
`positions` (23 % de la taille) n'est utile qu'aux analyses radar, qui filtrent d'abord sur un quart d'heure.
