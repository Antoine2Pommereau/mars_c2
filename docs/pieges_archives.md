# MARS C2 : pièges résolus

Retirés de CLAUDE.md le 24/10/2026 (lot Z3) : le défaut est corrigé, ou le récit est fini. Chaque entrée garde la
leçon, encore utile si le cas revient. Les pièges vivants sont dans CLAUDE.md, section 11.

## Poste de travail

* **Accès à Documents perdu (07 au 09/10/2026)** : l'application qui exécute Claude Code n'avait plus accès au dossier
  Documents (protection de macOS, « Operation not permitted », même hors bac à sable) : ni le `.venv` du dépôt, ni
  `data/`, ni Git (le worktree pointe vers `.git` du dépôt principal). Contournement d'alors : environnement Python et
  clone du dépôt dans le dossier temporaire, `npm ci` dans le worktree. Rétablir : Réglages Système, Confidentialité et
  sécurité, Fichiers et dossiers (ou Accès complet au disque) pour l'application du terminal. Documents est de nouveau
  accessible.
* **Dossier temporaire purgé** : le 12/10/2026 (clone Git du dépôt corrompu, environnement Python vidé) puis le
  23/10/2026 (`.git/config` du clone de travail disparu, objets manquants) ; leçon gardée dans CLAUDE.md : repartir
  d'un clone neuf.
* Node 26 ne lisait plus un `node_modules` relié par lien symbolique dans Documents : installer par `npm ci` dans le
  worktree.

## Premiers travailleurs (lot B, 07 et 08/10/2026)

* **Premier essai réel** : PrivateNetworksReadOnly ne permet pas de rattacher une carte au réseau privé (il faut
  PrivateNetworksFullAccess) ; le rattachement est asynchrone, et l'image Scaleway « docker » ne configurait pas
  d'elle même l'interface privée (travailleur rattaché en adresse privée, injoignable) : attendre la carte
  « available » avant de démarrer, configurer l'interface en DHCP dans `travailleurs/demarrage.sh`. Deux lancements
  simultanés (rattrapage au démarrage du conteneur et commande manuelle) avaient créé deux travailleurs : verrou par
  index unique en base.
* **Essai du 08/10 à 06:30 (travailleur 5)** : Ubuntu, réseau privé, Docker et image d'AI2 fonctionnaient ; les 39
  granules ont échoué (« NetCDF: Unknown file format ») : licence LANCE NRT non acceptée sur le compte Earthdata, la
  NASA renvoyait une page HTML de 10 Ko. Chaque fichier est désormais vérifié (taille, signature HDF5) et la cause
  nommée ; sans jeton valide, la redirection vers /profiles/licenses se poursuit vers /oauth/login : c'est alors le
  jeton qui manque. La destruction échouait sur une instance « stopped in place » (leçon gardée dans CLAUDE.md).
* **Cause réelle des premiers essais (08/10/2026)** : la console série affichait « UEFI Interactive Shell » puis
  `Shell>` : aucun système amorçable. La création imposait un volume racine décrit avec un nom, sans lien vérifié avec
  l'image (« docker » du catalogue, ni architecture ni volume racine contrôlés). Depuis : image Ubuntu 24.04 résolue
  (zone, type, x86_64, disque local, fiche de l'image), volume racine décrit par sa taille et son type, instance relue
  après création, Docker installé au démarrage.
* Image d'un travailleur : une image publique (allenai) n'entame pas le quota de 500 Mo des paquets privés ; le script
  passe par cloud-init plutôt que par une image privée de près d'un Go (décision prise).

## Sentinel 1 (lot C, 11 au 19/10/2026)

* **Second essai réel (14/10/2026)** : « No module named 'src' » (leçon gardée : imports `src.*`) ; le travailleur était
  compté « terminé » malgré son unique scène en échec : un travailleur sans scène aboutie finit désormais en « echec ».
* **Taille d'un volume bloc à 0** (essai du 14/10/2026, passage 110, travailleur 19, refusé pour « 0 Go ») et image GPU
  `instance_sbs` de taille nulle (11 et 12/10/2026) : taille lue dans l'API Block Storage (leçon gardée).
* **Plafond des travailleurs** : un refus de Scaleway avant création (image, quota), répété toutes les 30 minutes,
  épuisait le plafond quotidien et privait VIIRS de sa nuit (12/10/2026) : seules les instances créées comptent.
* **Disponibilités paginées** : « illisible (HTTP 200) » au diagnostic du 13/10/2026, les GPU n'étant pas sur la
  première page de `/products/servers/availability`.
* **GPU Pascal** (essai 26 du 19/10/2026, RENDER-S facturé 1,23 € pour 0,28 € estimés) : `docker run --gpus` en échec,
  « failed to initialize NVML: Driver Not Loaded » ; type retiré.

## Interface (avant le lot U)

* L'interface interrogeait l'API toutes les 2 s tant qu'une analyse épinglée n'était pas terminée (panneau Analyses
  de l'ancien rail).
* Tracé d'emprise au trackpad en deux clics, zoom par sélection désactivé (outil « Nouvelle analyse » de la chaîne du
  Mac, retrait au lot Z4).
* **Horloge partagée** (`sim_clock`) commune à tous les utilisateurs : un rejeu chez l'un changeait l'écran de tous.
  L'instant est tenu par le navigateur (routes `?at=`) ; `/api/clock` et `sim_clock` restent à retirer (lot Z4).
* **Trafic vide hors du direct** (lot A, 21/10/2026) : `decodeTraffic` n'acceptait que la forme du flux ; leçon gardée.
* **Ligne MARS_OPERATEURS abîmée** (21/10/2026) : copiée avec son suffixe « trace_id=… span_id=… », empreinte de 131
  caractères au lieu de 64, connexion impossible ; depuis, `traces.ligne_brute`, refus d'une entrée abîmée en le disant
  (`controle.operateurs_abimes`), et sortie lisible pour toute commande manuelle (24/10/2026).
* **Origine refusée** (21/10/2026) : nginx envoyait « backend:8000 » comme Host ; leçon gardée (`Host $http_host`).

## Supervision et exploitation

* **Incident du 18/10/2026** : disque plein (8 Go) vers 01:00 UTC ; la collecte s'est figée à 01:27 sans s'arrêter
  (plus de journal ni de fichier, conteneur « Up ») : l'écriture tournait dans une tâche asyncio sans protection,
  tuée en silence ; l'ingestion a planté en boucle 21 h sur DiskFull (`REFRESH MATERIALIZED VIEW CONCURRENTLY
  vessel_watch`) ; environ 21 h de données AIS perdues (AISStream ne garde rien) ; l'alerte des 15 % n'était que dans
  la barre d'état. Depuis : aucune tâche asyncio sans garde, aucun service qui meurt sur une erreur passagère, contrôle
  de santé qui relance un processus figé, alertes sur le téléphone ; disque porté à 15 Go puis serveur DEV1-M.
  Récit complet : `docs/supervision.md`.
* **Fausse alerte Grafana « Tâche en échec plusieurs fois de suite »** (20 au 23/10/2026) : deux échecs de
  travailleur_sentinel2 relus 7 jours pendant la pause de Sentinel 2 ; corrigé par `taches_en_echec_suite`
  (migration 42).
* **Journal d'accès d'uvicorn** : le filtre des traces vidait `record.args`, erreur à chaque requête tracée (déjà en
  production avant le 22/10/2026) ; corrigé.
* **Faux échec de l'essai de restauration** (24/10/2026) : sauvegarde d'avant les migrations 39 à 42, 55 tables pour
  60 ; la comparaison suit désormais le registre des migrations de la sauvegarde.
* Les tests passaient depuis le dépôt alors que `backend/sentinel.py` n'était pas copié par l'image (11/10/2026) :
  origine de `tests/test_images.py` et de `verifier_images.sh` (leçon gardée).
