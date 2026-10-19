# Supervision (étape D) : Grafana, alertes sur le téléphone, fin des pannes silencieuses

Rédigé le 19/10/2026, après l'incident du 18/10/2026.

## L'incident du 18/10/2026

* Vers 01:00 UTC, disque plein (8 Go).
* La collecte s'est figée à 01:27 sans s'arrêter : plus aucun journal ni fichier Parquet, mais Docker la voyait « Up ».
  Cause, lue dans `scripts/ais_live.py` : l'écriture des fichiers tournait dans une tâche asyncio sans protection ;
  disque plein, elle a levé une erreur qui a tué cette tâche en silence, pendant que la lecture du flux continuait de
  remplir la mémoire.
* L'ingestion a planté en boucle pendant 21 h sur DiskFull (`REFRESH MATERIALIZED VIEW CONCURRENTLY vessel_watch`) :
  chaque erreur arrêtait le processus, Docker le relançait, avec un rattrapage complet à chaque fois.
* Environ 21 h de données AIS perdues : AISStream ne garde rien.
* L'alerte disque des 15 % existait dans la barre d'état, mais rien n'a prévenu personne.

Le disque est passé à 15 Go (13 Go utilisables, 4,6 Go libres).

## Trois couches indépendantes

1. **Plus de mort silencieuse.**
   * La collecte journalise chaque écriture en échec (« ÉCHEC D'ÉCRITURE des fichiers Parquet ») et garde les messages
     en mémoire pour l'essai suivant (120 000 au plus, environ trois heures de flux ; au delà, les plus anciens partent
     et le journal le dit). Un fichier partiel est effacé. Si une tâche interne meurt, le processus s'arrête et Docker
     le relance (`restart: unless-stopped`) : jamais de collecte à moitié vivante.
   * L'ingestion ne s'arrête plus sur une erreur : elle dit clairement pourquoi (« DISQUE PLEIN, la base refuse
     d'écrire »), consigne l'échec dans `task_runs` (tâche `ingestion`, une fois par heure au plus ; disque plein, la
     base refuse aussi cette écriture), attend de plus en plus longtemps (jusqu'à 5 minutes) et reprend seule.
2. **Contrôle de santé qui relance un processus figé** (`scripts/sante.py`).
   * Docker, hors Swarm, ne relance jamais un conteneur « unhealthy » : il le signale, c'est tout. Les outils qui le
     font (autoheal) demandent le socket Docker, c'est à dire les droits de root sur l'hôte : écarté, tout comme une
     surveillance dans taches par le socket. Ici, le contrôle de santé arrête lui même le processus figé (SIGTERM,
     puis SIGKILL), le conteneur s'arrête avec lui, et la politique de redémarrage le relance. Aucun droit
     supplémentaire, aucune image en plus.
   * `init: true` est indispensable : le noyau ignore un signal envoyé, depuis son propre conteneur, au processus
     numéro 1 qui ne l'a pas prévu ; avec init, Python n'est plus le numéro 1.
   * Collecte : saine si un fichier Parquet a été écrit depuis 5 minutes (signe de vie `.signe_de_vie_collecte`,
     dont seule la date change à chaque écriture : aucun besoin de place, même disque plein). Figée : relancée. Mais si
     les écritures échouent (disque plein), pas de redémarrage, qui perdrait les messages gardés en mémoire ; le
     conteneur est « unhealthy » et l'alerte disque prévient.
   * Ingestion : relancée si sa boucle est figée depuis 5 minutes ; en échec répété, elle tourne et journalise.
   * Chaque redémarrage est consigné dans `task_runs` (tâche `redemarrage_collecte` ou `redemarrage_ingestion`, avec
     son motif) et dans le journal du conteneur (« REDÉMARRAGE AUTOMATIQUE de la collecte : … »).
3. **Alertes sur le téléphone** par ntfy : par Grafana, et en secours par taches.

## Alertes par ntfy

* Envoi HTTP vers https://ntfy.sh sur un sujet secret, `NTFY_SUJET` du `.env` (le seul secret : quiconque le connaît
  peut lire les messages ; choisir une chaîne longue et aléatoire). Messages courts, sans donnée sensible : ni nom de
  navire, ni position, ni clé.
* **Grafana** (règles dans `grafana/provisioning/alerting/regles.yaml`) : disque sous 15 % libres, collecte sans fichier
  depuis 5 minutes, ingestion en échec ou en retard de plus de 10 minutes, nuit VIIRS absente à 08:00 UTC, même tâche
  en échec deux fois de suite (les refus attendus, plafond ou budget, ne comptent pas). Une alerte est répétée au plus
  une fois par heure ; le retour à la normale est annoncé.
  Depuis l'étape D2 (docs/opentelemetry.md) : collecteur de traces muet depuis 15 minutes, ou spans refusés.
* **Secours par taches**, si Grafana ne répond pas (`/api/health`) : les trois plus graves (disque, collecte figée,
  ingestion), évaluées chaque minute **sans rien écrire en base**, pour fonctionner disque plein. Même rythme : au plus
  une fois par heure, retour à la normale annoncé. Quand Grafana revient, taches n'annonce plus que le retour à la
  normale de ce qu'il avait envoyé.
* `docker compose exec taches python scripts/taches.py tester-alerte` envoie un message d'essai.

Éprouvé sur le Mac avec un serveur ntfy local (v2.14.0) et Grafana 12.4.2 : collecte figée, ingestion en retard et
tâche en échec reçues (« MARS C2 : Collecte AIS figée », « Aucun fichier écrit par la collecte depuis 11 min (relevé
par taches). »), puis « MARS C2 : retour à la normale ».

## Seuils : un seul fichier

`config/supervision.env` : lu par Grafana, taches et les contrôles de santé. Grafana 12 ne remplace pas les variables
d'environnement dans les règles d'alerte provisionnées (constaté : `$SEUIL_…` restait tel quel dans le SQL) : taches
recopie les seuils dans la table `parametres_supervision` toutes les 10 minutes et au démarrage, et les règles les y
lisent (avec les valeurs du fichier en repli). Un changement prend effet au redémarrage des services :
`docker compose up -d grafana taches collector ingest`.

## Grafana

* Image officielle `grafana/grafana:12.4.2`, épinglée par version et par empreinte ; 214 Mo à télécharger, 763 Mo
  décompressée (linux/amd64). Mémoire limitée à 256 Mo (`GOMEMLIMIT` à 200 Mo) : 187 Mo mesurés sur le Mac après
  25 minutes, en émulation, cinq règles évaluées chaque minute ; la mesure sur 24 h se fait sur le serveur
  (`docker stats --no-stream grafana`).
* Écoute sur 127.0.0.1:3000 seulement : `ssh -N -L 3000:localhost:3000 -L 8080:localhost:8080 root@IP_DU_SERVEUR`, puis
  http://localhost:3000 (compte `admin`, mot de passe `GRAFANA_ADMIN_MOT_DE_PASSE` du `.env`).
* Rien n'est sorti vers Internet que les alertes : statistiques d'usage, recherche de mises à jour, fil d'actualité et
  greffons préinstallés coupés.
* Tout est en code dans `grafana/provisioning` : source de données, cinq tableaux (produits par `grafana/generer.py`),
  règles, canal ntfy, politique d'envoi. Rien n'est réglé à la main ; les tableaux ne sont pas modifiables depuis
  l'interface.
* Rôle PostgreSQL `grafana_lecture` (migration 26) : lecture seule, tables et colonnes utiles seulement (ni jeton, ni
  paramètres des travailleurs, ni position, ni nom de navire, ni note d'opérateur), `statement_timeout` de 10 s, 5
  connexions au plus. Créé sans connexion ; taches lui donne le mot de passe `GRAFANA_PG_MOT_DE_PASSE` du `.env` au
  démarrage.

## Mesures (toutes les 10 minutes, table mesures_disque)

Espace libre, taille de la base (`pg_database_size`), de la table des positions (index compris), du dossier
`data/ais_live`, et date du dernier fichier Parquet écrit ; un an d'historique (144 lignes par jour). Chaque minute,
`signes_de_vie` : collecte (dernier fichier écrit, relevé par taches), ingestion (dernier passage réussi et attente du
plus ancien fichier, écrite par l'ingestion), taches.

## Les tableaux (sept : la vue d'ensemble, les cinq de l'étape D, et « Traces » de l'étape D2)

| Tableau | Contenu |
|---|---|
| Direct | dernier fichier de la collecte, retard de l'ingestion, messages par minute (dernière heure et courbe), signe de vie de taches, positions conservées par minute, positions par heure et par région, coupures du flux (minutes sous 20 % de la médiane, comme la frise) |
| Disque et base | espace libre, base, positions, croissance par jour (sept derniers jours), jours restants, courbes et croissance par jour |
| Tâches | échecs sur 7 jours, redémarrages automatiques, dernier cycle des règles, tâches en échec deux fois de suite, dernière exécution de chaque tâche, cycle des règles par étape, durée des tâches, redémarrages, échecs |
| Satellites | coût Sentinel du mois et part du budget, coût VIIRS, dernière nuit VIIRS, ruptures de stock GPU, travailleurs actifs, détections VIIRS par statut, nuits, travailleurs (durée, mémoire, coût, motif), passages Sentinel 1 |
| Alertes | alertes de la période, élevées encore nouvelles, part traitée, délai médian de première décision, par jour et par type, par jour et par gravité, par type |

Aucun tableau ne lit la table des positions : statistiques (`stats_minute`, `stats_10min_region`), registre
d'ingestion, mesures, journal des tâches. Le registre d'ingestion ne garde que les deux derniers jours (les journées
archivées en sortent) : « Messages par minute » est donc limité à 48 h ; « Positions conservées par minute » et
« Coupures du flux » couvrent 30 jours. L'incident du 18/10 y apparaît comme une coupure de 21,1 h (essai sur un
historique synthétique de 30 jours).

## Vue d'ensemble, page d'accueil de Grafana

Tableau « Vue d'ensemble » (`mars-vue-ensemble`), ouvert d'office à la connexion
(`GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH`, docker-compose.serveur.yml) :

* **six tuiles** : Collecte, Ingestion, Disque, Tâches, Satellites, Traces. Chaque requête calcule son niveau avec les
  seuils des alertes, lus dans `parametres_supervision` (recopiés de `config/supervision.env` par taches) : rouge au
  seuil de l'alerte, orange avant (moitié du délai pour la collecte et l'ingestion, 10 points au dessus du seuil pour
  le disque, un échec sur 24 h pour les tâches, un travailleur en échec sur 24 h pour les satellites, plus de 100
  lots en attente pour les traces), vert sinon, gris sans mesure. Valeur courte : fichier il y a N min, retard N min,
  espace libre, échecs sur 24 h, dernière nuit VIIRS, spans reçus en 1 h. Un clic ouvre le tableau détaillé ;
* **Dernières traces de bout en bout** : les 20 dernières analyses satellites tracées (travailleurs VIIRS et
  Sentinel 1), date, durée totale (création à destruction de l'instance), statut, motif ; la colonne Trace ouvre la
  trace dans Tempo, chronologie et vue Gantt des spans. Sans analyse : « aucune analyse satellite tracée depuis le
  déploiement ». Une trace n'est gardée que 72 h : au delà, la ligne reste et le lien ne trouve plus rien.

Grafana n'est jamais tracé, pas plus que Tempo et le collecteur : ce sont des outils de supervision, pas MARS C2
(règle en tête de CLAUDE.md). Tracé un temps le 19/10/2026, Grafana envoyait au repos environ 330 spans par minute
(évaluation des règles d'alerte, sessions), soit environ 480 000 par jour, dix fois le reste du système. Le
collecteur écarte désormais tout span de ces outils (`filter/outils`, otel/collecteur.yaml).

## Trace de démonstration à la demande

```bash
docker compose exec taches python scripts/taches.py viirs
```

La commande cherche les granules des trois dernières nuits terminées qui ne sont ni traitées ni en cours, puis confie
celles qui restent à un travailleur éphémère (DEV1-M, facturée à l'heure entamée : environ 0,02 € HT).

* **Une nuit reste à traiter** (entre 06:00 UTC, fin de la nuit, et 06:30, passage automatique ; ou une nuit
  manquée) : trace complète en 10 à 15 minutes, du lancement à la destruction (étapes Scaleway, démarrage, 12
  granules, renvoi, traitement des résultats, alertes).
* **Toutes les nuits sont traitées** (cas habituel dans la journée) : rien n'est lancé, la commande répond
  `{"nuits": …, "granules": 0}` ; sa trace ne contient que la recherche des granules auprès de la NASA (CMR), sans
  travailleur, et n'apparaît donc pas dans « Dernières traces de bout en bout ».

Pour une analyse Sentinel 1 choisie à la main : `taches.py sentinel-analyser ID` (GPU facturé à la minute, plafond
et budget appliqués, `docs/sentinel.md`).

Où la retrouver :
* **Vue d'ensemble**, panneau « Dernières traces de bout en bout » : la ligne apparaît dès le lancement (statut
  « créée », puis « en cours », « résultats reçus », « terminée ») ; clic sur la colonne Trace ;
* **Traces**, panneau « Analyses satellites » (`{ name = "travailleur.lancement" }`), ou « Tâches » pour la seule
  commande (`commande viirs`) ;
* en ligne de commande : `docker compose exec taches python scripts/taches.py travailleurs` affiche l'identifiant de
  trace de chaque travailleur ; à coller dans Explorer, source Tempo, requête TraceQL.

## Durée des requêtes

`docker compose exec taches python scripts/taches.py mesurer-tableaux` exécute chaque requête des tableaux sous le rôle
de Grafana, en lecture seule, sur 48 h et 30 jours, et affiche les durées (code de sortie non nul au dessus de 1 s ou
en erreur). Sur le Mac, historique synthétique de 30 jours (42 000 minutes de flux, 6 500 fichiers au registre, 4 300
mesures, 865 alertes, 2 900 détections VIIRS) : 86 mesures, la plus longue 0,087 s (positions conservées par minute
sur 30 jours). À refaire sur le serveur après déploiement.

## Limites

* Le gel réel est éprouvé dans Docker (`scripts/verifier_images.sh sante`) avec un processus figé simulé, pas avec un
  disque réellement plein.
* Disque plein, aucune écriture en base n'aboutit : les redémarrages et les échecs ne sont alors que dans les journaux
  des conteneurs ; les alertes partent quand même (Grafana lit, taches lit et envoie sans écrire).
* Les alertes dépendent d'Internet et de ntfy.sh : une coupure du réseau du serveur n'est signalée par personne.
* Taches arrêté et Grafana arrêté ensemble : plus aucune alerte. La règle « collecte figée » se déclenche quand taches
  ne relève plus le signe de vie (le tableau Direct montre aussi celui de taches).
