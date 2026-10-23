# MARS C2 : pièges vivants, détail

Texte complet des pièges encore utiles, repris de CLAUDE.md le 24/10/2026 (lot Z3) ; CLAUDE.md, section 11, en garde
le résumé. Pièges résolus : `docs/pieges_archives.md`.

* **Lot Z1, écritures** : toute nouvelle route POST, PUT, PATCH ou DELETE prend `Depends(exiger_operateur)` et ne lit
  jamais d'auteur dans le corps ; un script qui écrivait par l'API (masques, règles à la demande) doit écrire en base
  lui même (`scripts/build_masks.py`). Les appels d'écriture de l'interface passent par `ecrire` de `lib/api.ts`.
* **Variables par service** : une variable nouvelle lue par le code doit être ajoutée au service dans Compose, ou
  déclarée dans `FACULTATIVES` de `tests/test_environnement.py` avec sa raison. YAML ne partage pas les ancres entre
  fichiers : la surcouche du serveur recopie les ancres `x-*` (test d'égalité).
* **FastAPI récent** enveloppe les routeurs inclus (`_IncludedRouter`, `original_router`) : parcourir les routes à plat.
* **zsh** : `$i:latest` applique le modificateur `:l` (minuscules) ; écrire `${i}:latest`.
* Toute migration à partir de 39 se termine par l'inscription de son nom dans `schema_migrations`.
* **Horodatages de l'archive** : selon le nombre de décimales, pandas lit les instants en microsecondes ou en
  nanosecondes ; une conversion en secondes par division fixe se trompe d'un facteur 1 000. Lire les 19 premiers
  caractères avec Arrow (`trajectoire_mesures.secondes`).
* **Mémoire d'Arrow** : l'allocateur par défaut (mimalloc) garde la mémoire libérée de journée en journée (447 Mo après
  16 journées de lecture contre 257 Mo avec l'allocateur du système) ; pour une lecture longue dans le conteneur des
  tâches (768 Mo), `pa.set_memory_pool(pa.system_memory_pool())` et `read_table(use_threads=False)`.
* **Mesures longues** : un dictionnaire Python par fenêtre (vingt valeurs) coûte environ 0,6 Ko ; pour des centaines
  de milliers de fenêtres, construire des colonnes numpy par trajet (`evaluer_longs`). La vraie archive coûte environ
  1,65 fois l'archive synthétique en mémoire : lancer une mesure lourde avec `TACHES_MEM_LIMIT=1200m docker compose
  run --rm --no-deps taches …`, jamais dans le conteneur en service.
* **Dossiers** : une décision ou un devis lit le dossier à l'instant affiché (`instant`, `end`), plafonné à
  maintenant ; à l'instant réel seul, un dossier d'une plage passée est hors de la fenêtre de 72 h (404).
* La carte MapLibre ne se redimensionne qu'avec la fenêtre : quand un panneau change de largeur, `map.resize()` est
  appelé par un ResizeObserver sur son conteneur (`MapView.tsx`), sinon elle s'étire ou se décale.
* Une poignée posée dans une zone qui défile défile avec elle : la fiche de droite a une enveloppe (la poignée) et une
  zone intérieure qui défile.
* Raccourcis : un écouteur qui doit passer avant celui de l'application (Échap qui fermerait la fiche) s'inscrit en
  capture (`addEventListener(…, true)`) et arrête la propagation.
* Ne jamais remplacer un dossier par le Finder : utiliser `ditto`.
* **Tailwind 4** : une règle hors couche l'emporte ; la carte MapLibre se positionne par style direct.
* MapLibre : pas de `line-dasharray` dépendant des données (deux couches filtrées).
* NaN devient NULL avant JSON ou base ; typer les paramètres asyncpg (`$1::timestamptz`) ; psycopg n'adapte pas
  les entiers numpy.
* Les scripts visent le port 8000 (nginx coupait les requêtes longues).
* Statuts AIS déclaratifs : indices seulement. Avant de conclure à un manque du détecteur, regarder la vignette et
  les scores bruts.
* **Horodatages AISStream** : partie décimale de longueur variable, toujours `format="ISO8601"`, arrondir à la
  microseconde.
* **Identités qui alternent** (MMSI 227000000 partagé par la Marine nationale, « MUTIN » et « FS MUTIN ») : une
  identité déjà vue est reprise.
* **Entrées GUR sans nom** : l'appartenance au catalogue ne se lit pas sur le nom (cas PASIPHAE, OMI 9289518).
* Surcouche Compose : `ports` se cumule, utiliser `!override` ou `!reset`.
* **R2 et boto3** : `request_checksum_calculation="when_required"`, MD5 fourni ; l'ETag d'un envoi simple est le MD5,
  celui d'un envoi en plusieurs morceaux (sauvegardes en flux) ne l'est pas : `R2.verify` relit alors l'objet.
* **Seau R2 européen** : adresse en `https://<compte>.eu.r2.cloudflarestorage.com`, sinon tout accès est refusé.
* **Deux jetons R2, deux seaux** (essai Sentinel 2 du 20/10/2026, 403 AccessDenied au travailleur 29) : le jeton
  principal (`mars-c2`) est filtré sur l'IP du serveur et doit le rester ; une adresse qu'il présigne est refusée depuis
  toute autre IP. Toute adresse R2 remise à un travailleur vient du seau des modèles (`R2_MODELES_*`, jeton jamais
  filtré par IP : les travailleurs sortent par des IP variables), par `r2_modeles_get_url` ; ne jamais présigner pour
  un travailleur avec `r2_get_url`. Un test depuis le serveur ne révèle pas un filtre IP : seul le contrôle du
  travailleur (étape `controle_r2`) le fait.
* **satlaspretrain_models télécharge son squelette à chaque chargement** (467 Mo depuis Hugging Face, `requests.get`
  codé en dur, ni cache ni variable pour le couper) : le travailleur Sentinel 2 le reçoit de R2 comme les autres poids
  et le sert par `servir_satlas` dans le lanceur de l'inférence ; tout autre téléchargement de la bibliothèque est
  refusé. Le champ `satlas` doit suivre dans les paramètres de la tâche (`mars/sentinel2.py`), sinon l'inférence
  s'arrête. Un `restore_path` de configuration rslearn n'est lu qu'à l'entraînement.
* **rasterio refuse les clés AWS en options GDAL** (essai Sentinel 2 du 20/10/2026, analyse 99, travailleur 30 :
  « EnvError: GDAL's AWS config options can not be directly set », 0 octet lu) : `AWS_ACCESS_KEY_ID` et
  `AWS_SECRET_ACCESS_KEY` ne passent jamais dans `rasterio.Env(...)`. Les remettre par une
  `rasterio.session.AWSSession` (boto3 présent dans l'image rslearn) ou, sans boto3, par les variables d'environnement
  posées le temps de la lecture puis retirées (`env_gdal` de `travailleurs/sentinel2.py`) ; options non sensibles
  (`AWS_S3_ENDPOINT`, `AWS_VIRTUAL_HOSTING`, `AWS_HTTPS`) dans `rasterio.Env`. GDAL seul (`gdal.SetConfigOption`,
  travailleur Sentinel 1) n'a pas cette limite. Leçon : une lecture S3 d'un travailleur s'éprouve par son chemin S3
  réel, signature comprise, jamais seulement sur des fichiers locaux ou un miroir sans clé (`verifier_images.sh s2-s3`,
  `fenetres`, `tests/test_lectures_s3.py`).
* Pas de commentaire en fin de ligne dans `.env` : `mars/config.py` ne les retire pas.
* **Noms d'images fixes** (`mars_c2-backend`, `mars_c2-web`, `mars_c2-scripts`) : sans eux, le nom dépend du dossier
  du projet, et une image construite sur le Mac n'aurait pas le nom attendu sur le serveur.
* `LIKE 'préfixe%'` n'utilise pas un index btree avec la collation par défaut : charger les alertes en une requête
  plutôt qu'une recherche par épisode.
* Lire GSHHG avec pyshp coûte environ 120 octets par point (liste de tuples) : le polygone de l'Eurasie porte la
  mémoire au delà de 500 Mo. `build_masks.py` lit les enregistrements en numpy (16 octets par point), et découpe
  avant de réparer la géométrie.
* Une règle en continu ne doit jamais effacer puis recréer ses alertes : les décisions des opérateurs seraient
  perdues à chaque cycle (`save_alerts`).
* `pg_dump` en version 16 (dépôt PGDG dans l'image des scripts).
* Recharger d'anciens jours ne passe pas par l'ingestion en direct : `taches.py restaurer-positions`.
* La collecte écrit dans un fichier caché puis renomme ; l'ingestion ignore les fichiers de moins de 5 s.
* Tester R2 en local : `moto_server` dans un conteneur Python (MinIO n'est plus publié).
* Le navigateur sans affichage de gstack n'a pas WebGL. Le Chromium de Playwright, lui, dessine la carte sans
  affichage (`--use-angle=swiftshader --enable-unsafe-swiftshader`, Metal fonctionne aussi) : captures de l'audit UX
  du lot U. Une limite d'erreur (`components/Garde.tsx`) garde le reste de l'écran si WebGL manque.
* **Deux formes du trafic en colonnes** : le flux en direct envoie `{navires: {cols, rows}}`, `/api/traffic` (plage et
  rejeu) `{at, cols, rows}` ; `decodeTraffic` accepte les deux (lot A, 21/10/2026 : n'accepter que la première vidait
  la carte hors du direct, depuis des jours). Test : `npm test` dans `web/` (node --test, `web/tests/`).
* **nginx et proxy_pass sur une variable** : l'en tête Host devient « backend:8000 ». Toute vérification qui compare
  l'hôte à l'en tête Origin (écritures du panneau de contrôle) refusait tout (« Origine refusée », connexion des
  opérateurs impossible le 21/10/2026). `web/nginx.conf` garde l'hôte du navigateur (`proxy_set_header Host
  $http_host`, et X-Forwarded-Host) ; l'API lit X-Forwarded-Host d'abord ; cible `verifier_images.sh controle`.
* **Ligne à copier dans un fichier** (`.env`, configuration) : jamais par `print` pendant un span, qui ajoute
  « trace_id=… span_id=… » (`mars/traces.py`, journaux) ; `traces.ligne_brute`. Le 21/10/2026, une ligne
  MARS_OPERATEURS copiée avec son suffixe (empreinte de 131 caractères au lieu de 64) rendait la connexion impossible ;
  l'API refuse désormais une entrée abîmée en le disant (`controle.operateurs_abimes`, panneau, `taches.py
  controle-diagnostic`).
* Cookie de session : Secure seulement en https (`_https`) ; Safari ne renvoie pas un cookie Secure sur
  http://localhost:8080.
* Une requête en mode plage a une clé fixe : sans nouvelle tentative, une erreur passagère (redémarrage de l'API) y
  reste affichée. Garder `retry` sur les requêtes de la frise et du trafic.
* Pour savoir quelle version d'API tourne sur le serveur : `curl -s localhost:8000/openapi.json` (liste des routes) ;
  un comportement inattendu venait d'une image non déployée, pas du code.
* `tsconfig.json` impose `noUnusedLocals` : les imports et variables inutilisés sont des erreurs de types.
* `Vessels1.db` est publié en mode WAL (octets 18 et 19 de l'en tête à 2) : `sqlite3.deserialize` le refuse
  (« unable to open database file ») ; `load_gur` les remet à 1 en mémoire.
* Les règles en continu font sur le serveur l'essentiel de leur coût dans les ports : tout filtre qui retire des
  positions avant un appariement deux à deux doit garder une marge sous le seuil final (cas du préfiltre côtier).
* **Catalogue STAC de Copernicus** : un multipolygone dont les parties se chevauchent (Bretagne et Manche) est
  invalide et renvoie une erreur 500 (« TopologyException ») ; chercher région par région et dédoublonner.
* **Identifiant de prise de vue Sentinel 1** : réattribué à l'exécution (le plan et le catalogue diffèrent de
  quelques unités) ; rapprocher plan et catalogue par satellite et orbite absolue.
* Sous zsh, une variable de boucle nommée `path` écrase le `PATH` (plus aucune commande trouvée).
* Travailleurs : **jamais une autre instance détruite**. Toute destruction passe par `Scaleway.destroy`, qui relit
  l'instance et refuse le serveur principal (`SCW_SERVEUR_PRINCIPAL`, métadonnées), une instance protégée, un autre
  projet, un nom autre que `mars-travailleur-<n>` ou des étiquettes discordantes ; ne jamais ajouter de chemin
  destructeur qui l'évite (`action` n'accepte que le démarrage). Couvert par tests/test_travailleurs.py.
* `SCW_PRIVATE_NETWORK_ID` est l'identifiant du réseau privé (onglet Overview du réseau), pas celui du VPC.
* **VIIRS sans AIS ne veut pas dire navire sombre** (mesure du 09/10, 152 détections) : la distance à l'AIS le plus
  proche est bimodale (moins de 1 km, ou plus de 10 km), la tolérance n'y change rien ; 68 des 87 détections sans AIS
  n'avaient aucun navire AIS reçu à moins de 20 km : c'est la réception AISStream qui manque au large (Méditerranée
  surtout). D'où « non évaluable » (coupure du flux, ou aucune réception à 30 km) et la gravité faible « à confirmer »
  par défaut (règles 2026.10.20, `taches.py viirs-reevaluer` pour l'existant).
* Travailleurs : le serveur n'expose aucun port sur Internet ; le retour passe par le réseau privé Scaleway (nginx,
  port 8090 sur `MARS_IP_PRIVEE`). Les instances CPU sont facturées à l'heure entamée, les GPU à la minute.
* Un élément `fixed` placé dans un panneau à `backdrop-blur` est contenu par ce panneau (le flou crée un bloc
  conteneur) : une vue agrandie passe par un portail (`createPortal` vers `document.body`).
* Les tests en base (`MARS_TEST_DATABASE_URL`) vident des tables de la base de test (positions, zone de réception) :
  sauvegarder d'abord un jeu chargé pour une mesure (`pg_dump -Fc -t positions …`).
* Distance à la côte de nombreux points : jamais `ST_DWithin` à grand rayon sur `land` (distance géodésique à des
  centaines de polygones par point) ; table subdivisée et indexée, plus proches voisins puis distance exacte.
* Une règle en continu ne voit que sa fenêtre (24 h) : une durée qui la dépasse (séjour d'un navire des listes) se
  mesure depuis le début repris de l'alerte précédente, en relisant les positions.
* **Image d'AI2 pour Sentinel** (`sha-fafc6c3`) : Ubuntu 20.04, donc **Python 3.8** (pas de `str | None` hors
  annotations différées, pas de fusion de dictionnaires par `|`) ; son `main.py` passe à `detect_vessels` des arguments
  qu'elle n'accepte pas : appeler `prepare_scenes` puis `detect_vessels` (16 arguments) directement. Son code importe
  tout en `src.*` : mettre `/home/vessel_detection` (pas `.../src`) sur `sys.path` et importer `src.data.image`,
  `src.inference.pipeline` (second essai réel du 14/10/2026, « No module named 'src' »). `nms_thresh` doit être un
  entier (grille en division entière). Éprouver le script dans la vraie image avant tout essai réel :
  `scripts/verifier_images.sh ai2` (imports, une minute) ou `ai2-complet` (scène synthétique sur CPU, environ 5 min,
  4 cibles retrouvées à moins de 50 m).
* **Taille d'un volume bloc** : l'API Instances renvoie `size` 0 pour un volume `sbs_volume` (et pour l'instantané
  `sbs_snapshot` de l'image) ; la taille se lit dans l'API Block Storage. Premier essai réel du 14/10/2026 (passage
  110, travailleur 19) refusé pour « 0 Go » ; `boot_problem` la lit désormais là, sans refus si elle reste illisible.
* **GPU L4 chez Scaleway** : pas de disque local, volume bloc (`sbs_volume`, image de type `instance_sbs`) ; `terminate`
  laisse le volume bloc (facturé) : extinction, suppression de l'instance, puis du volume par l'API Block Storage
  (droit BlockStorageFullAccess) ; volumes enregistrés en base dès la création.
* Le catalogue STAC `sentinel-1-grd` ne renvoie que la version COG des produits (`IW_GRDH_1S-COG`, fichiers
  `-cog.tiff`) ; le produit d'origine reste trouvable par OData (`S3Path`).
* **Image GPU sur volume bloc** : la fiche de l'API Instances d'une image `instance_sbs` peut donner une taille nulle
  pour son volume racine (instantané bloc) : la lire dans l'API Block Storage, comme la ligne de commande Scaleway.
* Les tests passent depuis le dépôt même si une image ne copie pas un module (cas de `backend/sentinel.py`, 11/10) :
  `tests/test_images.py` et `scripts/verifier_images.sh` avant chaque publication.
* Un trou de notre collecte agit de trois façons sur la règle des coupures : durée des silences, début inconnu des
  silences qui commencent dedans, historique manquant dans l'heure qui suit. Ne retirer que les minutes ne suffit pas
  (arrêt de 20 min : une coupure élevée fabriquée, un point collectif perdu, avant correction).
* La disponibilité des types d'instance (`/products/servers/availability`) est paginée, 100 types par page : les GPU ne
  sont pas sur la première (« illisible (HTTP 200) » au diagnostic du 13/10/2026). Lire toutes les pages.
* L'expiration des clés S3 de Copernicus n'est pas lisible avec ces clés : `CDSE_CLES_EXPIRENT_LE` et
  `CDSE_TRAVAILLEUR_EXPIRENT_LE` dans le `.env`.
* **Copernicus refuse les adresses présignées** (test du 14/10/2026 sur le serveur, même objet, mêmes clés) :
  `get_object` de boto3 (signature par en tête) lit ; l'adresse présignée de boto3 et celle de MARS C2 donnent
  toutes deux 403 InvalidAccessKeyId. Ce n'est ni la clé ni notre signature : `eodata.dataspace.copernicus.eu`
  n'accepte que l'en tête `Authorization`. Le travailleur Sentinel lit donc lui même, avec une paire dédiée
  (`CDSE_TRAVAILLEUR_*`) que l'API lui remet une seule fois, sur jeton, par le réseau privé (route `etat`,
  `{"etat": "tache"}`), jamais dans le cloud-init, en base ni dans un journal ; la paire du serveur ne le quitte
  jamais. Test à trois lignes et garde fous : `docs/sentinel.md`.
* L'image d'AI2 (`vessel-detection-sentinels`) tourne en Python 3.8 : le script du travailleur reste en
  bibliothèque standard (signature AWS version 4 écrite à la main, identique à botocore, testé).
* PostgreSQL 12 et plus déplie une sous requête `WITH` utilisée une fois : une géométrie lourde (union du trait de
  côte) y est recalculée pour chaque ligne. La marquer `MATERIALIZED` (cas de `find_gaps`, 68 s ramenées à 0,7 s).
* `scripts/verifier_images.sh` : l'image PostGIS initialise sa base par un serveur temporaire qui n'ouvre que la
  socket locale ; `pg_isready` sans `-h` le croit prêt et l'API se connecte pendant son arrêt (faux échec,
  ConnectionRefused). Attendre en TCP (`pg_isready -h 127.0.0.1`). La publication s'enchaîne par `&&` après la
  vérification.
* Docker (hors Swarm) ne redémarre jamais un conteneur « unhealthy ». Le contrôle de santé arrête lui même le processus
  figé, et `restart` relance le conteneur ; il faut `init: true`, car le noyau ignore un signal envoyé au processus
  numéro 1 depuis son conteneur quand il ne l'a pas prévu. Ne pas donner le socket Docker à un conteneur (droits root).
* **Grafana 12** remplace les variables d'environnement dans la source de données et le canal d'alerte, **pas dans les
  règles d'alerte provisionnées** (`$SEUIL_…` restait tel quel dans le SQL) : seuils lus dans `parametres_supervision`.
  Et l'adresse d'un canal webhook est elle même un gabarit : `{{ "{{.title}}" }}` pour laisser `{{.title}}` à ntfy.
* Tableaux Grafana : ne jamais lire la table `positions` ; `ingested_files` ne garde que deux jours (les journées
  archivées en sortent), `task_runs` des règles aussi.
* **Heure d'une tuile Sentinel 2** : l'heure du catalogue et du nom de produit est le début de la prise de vue sur
  toute l'orbite ; la tuile est vue plusieurs minutes plus tard (387 s pour T30UXA le 17/10/2026, soit 2 km de
  décalage à 10 nœuds). Lire `SENSING_TIME` dans `MTD_TL.xml` pour la fusion.
* **Analyse par fenêtres** : toute conclusion d'absence (AIS_UNCONFIRMED, « absent » du tip and cue) ne vaut que dans
  le cœur des fenêtres réellement lues, jamais sur l'emprise de la scène (défaut corrigé le 21/10/2026 pour Sentinel 1,
  `sentinel.dans_coeur`).
* **Masque SCL et navires** : le SCL du L2A classe en nuage le navire lui même et son sillage (1 à 8 % des pixels
  autour d'un navire dégagé) : juger une part du voisinage, jamais un seul pixel.
* GDAL ne journalise pas le premier bloc de 16 Ko lu à l'ouverture d'un fichier par `/vsicurl/` ou `/vsis3/` : le
  compter à part (`travailleurs/sentinel2.py`, `octets_journal`).
* L'image `vessel-detection-sentinels` d'AI2 n'a pas les poids de son détecteur Sentinel 2 (seulement `cfg.json`) ; le
  modèle Sentinel 2 d'AI2 est celui de rslearn (`sentinel2-vessel-detection`), poids hors de l'image. Lancer avec
  `--shm-size=2g` et un dossier des poids accessible en écriture.
* **Panneau de contrôle (lot P)** : ne plus modifier `config/rules.yaml` sur le serveur pour suspendre quoi que ce soit
  (pause, plafond, mode d'une règle) : l'état est en base et prime, et une modification locale bloque `git pull`. Un
  module qui décide d'un lancement ou d'une alerte lit `rules["_controle"]` des règles appliquées
  (`controle.appliquer`), jamais `load_rules()` seul ; une nouvelle écriture d'alerte passe par `controle.decision`
  et `controle.marquer_zone` (ou `save_alerts`, `filtrer_sync`) et porte `controle_empreinte`.
* Un `Envois` vide (liste) est faux en Python : tester `envoi is not None`, jamais `envoi or défaut`.
* Photo des navires : source VesselFinder (fiche publique par MMSI), à usage personnel ; conditions d'utilisation à
  vérifier avant une démonstration publique.
* **Traces (étape D2)** : l'instrumentation de requests remplace l'en tête `traceparent` posé à la main par le
  contexte courant ; les travailleurs n'ont pas le kit (aucun conflit), mais un test qui exécute leur script dans le
  processus des tests doit désinstrumenter requests. Les images du collecteur et de Tempo sont sans système (pas de
  shell, pas de `du`) : lancées en root pour écrire dans leur volume nommé ; mesurer la place par un conteneur
  `alpine` qui monte le volume. TraceQL refuse un point échappé par une barre oblique inverse venu de Grafana :
  écrire `[.]`. Tempo 3 a changé sa configuration (plus d'ingester ni de compactor : `live_store`, `backend_worker`,
  `backend_scheduler`) ; la conservation se règle dans `backend_worker` et `backend_scheduler`. Une commande nouvelle
  de `taches.py` se déclare par `@commande` (sinon refusée par l'analyse des arguments, et le test le signale).
* Filtre du collecteur (OTTL) : écrire chaque condition entre apostrophes dans le YAML ; non citée, une condition
  `IsMatch(…)` a été acceptée par `validate` mais n'écartait rien. Vérifier par `otelcol_processor_filter_spans_filtered`.
* Grafana : une tuile colorée selon un seuil lu en base se fait en SQL (colonnes `metric`, texte affiché, et `value`,
  niveau 0, 1 ou 2, format time_series, `textMode: name`) ; les seuils d'un panneau sont fixes. Le texte « aucune
  donnée » d'un tableau (`noValue`) s'applique aussi à chaque cellule vide : remplir les cellules en SQL. Une variable
  d'environnement de Grafana changée demande `docker compose up -d grafana` (`restart` garde l'ancienne).
* Construire les images sur le Mac par `docker build` (docs/deploiement.md) : la surcouche du serveur exige ses
  secrets (`:?`), Compose refuse alors toute commande sans eux.
* **Chaîne d'AI2 et images partielles** : `detect_vessels` découpe en carreaux de 2 048 pixels (Web Mercator, après
  reprojection) ; une image plus petite dans une dimension donne des décalages négatifs et une découpe fausse. Une
  fenêtre fait au moins 2 300 pixels de côté (`COTE_MIN_PX` du travailleur). `gdal.Translate(srcWin=…)` recale les
  points d'appui d'un GRD sur la fenêtre (vérifié : écart nul) ; `prepare_scenes` reprojette par ces points d'appui.
* **Stock des GPU chez Scaleway** : catalogue et disponibilités publics (`/instance/v1/zones/<zone>/products/servers`
  et `/availability`, sans clé) ; « scarce » ne garantit rien (412 out_of_stock au démarrage le 16/10/2026). Le réseau
  privé est régional : un GPU de pl-waw-2 ne peut pas renvoyer ses résultats au serveur de fr-par.
* **GPU Pascal et image GPU de Scaleway** (essai 26 du 19/10/2026) : « Ubuntu Noble GPU OS 13 » charge les modules
  noyau ouverts de NVIDIA, qui ne gèrent que Turing et plus récent ; un P100 (RENDER-S) démarre mais `docker run
  --gpus` échoue (« failed to initialize NVML: Driver Not Loaded »). Le catalogue ne le dit pas : seul le contrôle du
  GPU sur l'instance (`controle_gpu`) le révèle.
* Une alerte « une fois » de Grafana (abandon Sentinel, GPU marqué incompatible) regarde une fenêtre de 55 minutes :
  avec le rappel d'une heure de la politique ntfy, une fenêtre plus longue enverrait une seconde notification.
