# Traces OpenTelemetry (étape D2)

Rédigé le 19/10/2026. Tout le système envoie des traces, travailleurs éphémères compris, à un collecteur
OpenTelemetry ; elles sont gardées 72 h dans Grafana Tempo et consultées dans le Grafana existant (tableau « Traces »,
liens depuis les tâches et les travailleurs).

**Règle permanente** (en tête de CLAUDE.md) : toute addition est instrumentée ; chaque tâche, route, appel externe et
étape métier produit des spans, avec les attributs utiles et sans donnée sensible. Une fonctionnalité non tracée
n'est pas terminée. `tests/test_traces.py` échoue si une commande de `scripts/taches.py` ou un type de travailleur
n'est pas tracé.

## Architecture

```
API (FastAPI)  collecte  ingestion  taches ──OTLP HTTP──► collecteur (otel:4318, 127.0.0.1:4318)
interface web ──► nginx :8080 /otel/v1/traces ─────────────►      │ memory_limiter, filtre, tail_sampling
travailleurs ──► nginx :8090 (adresse privée, jeton vérifié) ─►    │ file d'attente sur disque
                                                                   ▼
                                                     Tempo (binaire unique, 72 h) ◄── Grafana (source « Tempo »)
```

* **Collecteur** : `otel/opentelemetry-collector-contrib:0.163.1`, épinglé par empreinte (`otel/collecteur.yaml`).
  * **Contrib, pas core, vérifié** : la distribution de base refuse la configuration
    (`unknown type: "transform"`, `"file_storage"`) et n'a pas `tail_sampling`. Les trois sont nécessaires :
    garder les requêtes lentes ou en erreur exige une décision prise une fois la trace complète (tail sampling) ;
    le filtre des données sensibles est un processeur `transform` ; la file d'attente qui survit à un redémarrage
    de Tempo ou du collecteur est une `file_storage`.
  * Prix mesuré : 116 Mo à télécharger contre 39 Mo, environ 420 Mo décompressés (un seul binaire) contre 130 Mo.
  * Mémoire : processeur `memory_limiter` en premier (refus au delà de 120 Mo, l'émetteur réessaie), limite Docker
    de 200 Mo, `GOMEMLIMIT` de 130 Mo.
  * File d'attente sur disque (volume `otel_file`) : jusqu'à 2 h de nouvelles tentatives si Tempo redémarre.
  * Image sans système (un seul binaire, sans shell) : lancée en root pour écrire dans le volume nommé, que Docker
    crée au nom de root ; aucun port publié hors de 127.0.0.1.
* **Tempo** : `grafana/tempo:3.1.0`, épinglé par empreinte, binaire unique sans Kafka (Tempo 3 pousse directement
  l'ingestion locale), stockage local (volume `tempo_data`), conservation de **72 h** (`block_retention`, réglable
  dans `otel/tempo.yaml` seulement), traces en cours bornées à 30 Mo, limite Docker de 320 Mo. Aucun port publié.
* **Grafana** : source de données Tempo provisionnée en code (`grafana/provisioning/datasources/tempo.yaml`, uid
  `tempo`, non modifiable).

### Rien d'exposé, et l'authentification des travailleurs

Le collecteur écoute sur le réseau Docker et sur 127.0.0.1:4318 ; jamais sur une adresse publique, ni sur l'adresse
privée. **Choix : passage par nginx sur le port 8090**, déjà publié sur `MARS_IP_PRIVEE` pour les résultats.
Route `POST /api/travailleurs/<id>/traces` : nginx fait vérifier le jeton de l'exécution par l'API
(`auth_request` vers `GET /api/travailleurs/<id>/otel`), puis relaie le lot au collecteur sans le jeton. Raisons :

* un seul port privé et un seul secret, celui des résultats, déjà propre à chaque exécution ;
* le jeton est refusé dès que l'instance est détruite : un jeton volé ne sert plus à rien ;
* le collecteur n'a ni extension d'authentification à configurer ni secret à garder.

Un jeton faux reçoit 403 (éprouvé par `scripts/verifier_images.sh traces`).

## Ce qui est tracé

| Service (`service.name`) | Spans |
|---|---|
| `mars-api` | FastAPI, asyncpg (sans paramètres), httpx, requests ; spans métier : `trafic.lecture`, `trafic.trainees`, `frise.lecture`, `analyse.radar`. Exclus : flux SSE, `/api/health`, vérification du jeton par nginx (`API_NON_TRACEES`) |
| `mars-collecte` | `collecte.ecriture` (chaque minute), `aisstream.connexion`, `aisstream.coupure`, `aisstream.erreur` |
| `mars-ingestion` | `ingestion.cycle` (chaque passage), `ingestion.fichier` (chaque fichier), `ingestion.journees`, `ingestion.vessel_watch` |
| `mars-taches` | `tache <nom>` (par construction, `run()`), `commande <nom>` (registre `COMMANDES`), `taches.minute` et ses `etape …`, `regles.<étape>`, `archivage.dossier`, `purge.jour`, `sauvegarde.*`, `listes.*`, `passages.*`, `R2 <opération>`, `ntfy.envoi`, `travailleur.*`, `scaleway.*`, `viirs.*`, `sentinel.*` |
| `mars-travailleur` | `demarrage.<étape>` et `travailleur.demarrage` (script bash), `travailleur.<tâche>`, remise de la tâche, chaque téléchargement, chaque granule ou scène, inférence, vignettes, renvoi des résultats |
| `mars-web` | une requête de l'interface vers l'API (méthode, chemin réduit à son gabarit, code, durée) |

**Appels externes** : AISStream (websockets), Copernicus STAC et S3, ESA, NASA CMR et LANCE, Scaleway, OpenSanctions,
GitHub (requests, tracés d'office avec `peer.service`) ; R2 (boto3, un span par opération) ; VesselFinder (httpx) ;
ntfy (urllib, sans le sujet). Les téléchargements des travailleurs (NASA, Copernicus) sont des spans du travailleur.

**Journaux** : chaque ligne écrite pendant un span se termine par `trace_id=… span_id=…` (print et logging) ; aucun
journal n'est envoyé au collecteur.

### Une analyse satellite, une trace

```
tache viirs (taches) ─ travailleur.lancement ─ scaleway.image, creation, verification_demarrage, reseau_prive,
                                                cloud_init, demarrage
  └ travailleur.demarrage (bash) ─ demarrage.interface_privee, contact_serveur, telechargement_image, analyse
       └ travailleur.viirs (Python) ─ signal_demarre, chargement_modele, viirs.granule ×12
            (telechargement dnb, geolocalisation, inference), renvoi_resultats ─► POST …/resultats (API)
  └ travailleur.resultats (taches, minute suivante) ─ viirs.granule, evaluation_et_alertes, vignettes
  └ travailleur.destruction
```

Le contexte (traceparent) du lancement est enregistré dans `travailleurs.traceparent` et remis par cloud-init (ce
n'est pas un secret) ; le script de démarrage passe celui de son étape « analyse » au conteneur ; le script Python
le transmet avec ses envois à l'API. Pour Sentinel 1, la trace commence à `sentinel.selection` (passages et points
d'intérêt) et finit par `sentinel.fusion_et_alertes`.

### Pourquoi un exportateur maison dans les travailleurs

Vérifié le 19/10/2026 dans les images épinglées : celle d'AI2 (Sentinel 1) est en Python 3.8.10, celle d'allenai
(VIIRS) en Python 3.10.12. Le dernier kit OpenTelemetry qui accepte Python 3.8 est la version 1.33.1 ; mais tout
exportateur OTLP officiel exige protobuf 5 ou plus, et ces deux images embarquent protobuf 4 (4.25.1 et 4.23.4),
requis par google-api-core et wandb. Installer le kit à chaque lancement, sur une instance éphémère, demanderait
PyPI, coûterait des minutes facturées et risquerait de casser la chaîne d'analyse. D'où `travailleurs/otel_mini.py` :
OTLP en JSON par HTTP, bibliothèque standard seule, compatible Python 3.8, déposé par cloud-init à côté du script ;
les spans partent après chaque granule ou scène et sont gardés en cas d'échec d'envoi. Le script de démarrage bash
écrit le même format (`travailleurs/traces.sh`). Tous deux sont éprouvés contre le vrai collecteur.

## Traçabilité métier

* `trace_id` dans `task_runs` (chaque exécution, et le journal de chaque travailleur), `travailleurs` (avec
  `traceparent`), `analyses` (migration 27, `db/init/27_traces.sql`).
* Alertes : un déclencheur écrit `details.trace_id` (dernière trace qui l'a écrite) et `details.trace_id_creation`
  à chaque création ou mise à jour, depuis la trace donnée à la session (`traces.en_base`) ; l'API, dont les
  connexions sont partagées, écrit l'identifiant dans les détails elle même. Une décision d'un opérateur garde les
  deux. Une alerte remonte ainsi à ses preuves, à la version des règles et à la trace qui l'a produite.
* Grafana : colonne « Trace » cliquable (exploration de Tempo) dans « Dernière exécution de chaque tâche », « Échecs
  des 7 derniers jours » (tableau Tâches) et « Travailleurs » (tableau Satellites).
* Interface : identifiant de trace en petit, sous la version des règles, au pied de la fiche d'une alerte.

## Échantillonnage et données sensibles

Seuils dans un seul fichier, `otel/collecteur.yaml` (`tail_sampling`) :
* tâches, collecte, ingestion et travailleurs : 100 % ;
* API et interface : toute trace en erreur ou de plus de 1 s, et 10 % du reste ;
* décisions gardées en mémoire (50 000) : les spans d'un travailleur qui arrivent vingt minutes après le lancement
  rejoignent la même trace.

Données sensibles, deux barrières :
1. dans le code : `mars/traces.py` (`Nettoyeur`, appliqué à chaque span avant l'envoi : attributs, événements,
   message d'erreur) et `travailleurs/otel_mini.py` : clés au nom sensible retirées (jeton, mot de passe, clé, secret,
   authorization, signature, sujet), chaînes de requête et identifiants retirés des adresses (adresses signées,
   recherche par nom), chemin masqué pour ntfy (sujet secret) et VesselFinder (MMSI), `Bearer …` et `X-Amz-…=` masqués ;
   paramètres SQL jamais capturés ; ni nom de navire ni position dans un attribut (identifiants internes seulement) ;
2. dans le collecteur : processeur `transform/sensible` (mêmes règles).

Testé : `tests/test_traces.py` (code) et `scripts/verifier_images.sh traces`, qui envoie au collecteur un span non
nettoyé (jeton, adresse signée, `Bearer`) et vérifie que Tempo ne le contient plus.

## Grafana

Tableau **Traces** (`mars-traces`) : dernier span reçu par le collecteur, spans refusés en 15 min, file d'attente vers
Tempo, mémoire du collecteur ; recherches TraceQL (traces en erreur, traces lentes de l'API, tâches, analyses
satellites) ; durée par étape au 90e centile (TraceQL metrics, calculé par Tempo depuis ses blocs, sans Prometheus) ;
spans des analyses satellites ; spans reçus par minute ; versions des images.

Les mesures du collecteur (port 8888, réseau Docker) sont relevées chaque minute par taches dans `mesures_otel`
(30 jours) : Grafana les lit sans Prometheus. Alertes ntfy (`grafana/provisioning/alerting/regles.yaml`) :
* **Collecteur de traces muet** : plus aucun span reçu depuis `SEUIL_COLLECTEUR_MUET_MIN` (15) minutes, ou
  collecteur injoignable (taches lui envoie un span chaque minute : le silence est anormal) ;
* **Collecteur de traces saturé** : au moins un span refusé en 15 minutes (mémoire, file pleine).

Piège rencontré : TraceQL refuse un point échappé par une barre oblique inverse dans une expression régulière transmise
par Grafana (« parse error ») ; écrire `[.]`.

## Versions des images

Chaque image porte l'empreinte de son commit (`--build-arg MARS_VERSION=$(git rev-parse --short HEAD)`, étiquette
`org.opencontainers.image.revision`, variable `MARS_VERSION`). Elle est :
* l'attribut `service.version` de chaque span ;
* écrite dans les signes de vie (`signes_de_vie.detail.version` : API au démarrage, tâches, ingestion, collecte) ;
* montrée par l'indicateur **Version** de la barre d'état (vert si toutes égales à celle de l'interface, orange avec
  le détail sinon) et par le panneau « Versions des images » du tableau Traces.

## Mesures (19/10/2026, Mac en émulation linux/amd64)

| Mesure | Valeur |
|---|---|
| Surcoût sur le cycle des règles (159 000 positions réelles, 4 cycles × 3, hors premier) | 6,23 s sans, 5,97 s avec : dans le bruit, sous 1 % |
| Surcoût sur l'ingestion (460 fichiers réels du 05/10) | 9,72 s sans, 9,79 s avec : **+0,8 %** |
| Volume prévu | environ 45 000 spans gardés par jour (ingestion 29 000, minutes de taches 7 000, collecte 1 400, règles 1 700, nuit VIIRS une centaine, API 10 %) |
| Disque des traces | 2,2 Mo de blocs pour une journée type, plus 6 Mo de journal d'écriture transitoire : **moins de 30 Mo à 72 h** |
| Mémoire du collecteur | 113 à 160 Mo au repos, 199 Mo au plus sous une rafale d'une journée de spans en 5 s (limite 200), sans refus ni arrêt ; tas Go de 24 Mo, le reste est surtout le binaire de 419 Mo chargé et l'émulation |
| Mémoire de Tempo | 88 à 184 Mo sous la même rafale (limite 320), tas Go de 62 Mo |
| Images | collecteur 116 Mo compressés (environ 420 décompressés), Tempo 41 Mo (environ 137) ; API +17 Mo, scripts +29 Mo |
| Trace de bout en bout | 22 spans, trois services (taches, travailleur, API) dans une seule trace (`verifier_images.sh traces`) |

Limites de ces mesures : l'émulation gonfle la mémoire comptée (traduction, pages du binaire) ; la rafale est
volontairement extrême (3 450 traces sur environ 8 000 arrivées en 15 s ont été écartées par le tail sampling,
capacité de 5 000 traces en attente ; au rythme réel, une demi trace par seconde, aucune). La mémoire sur 24 h et la
place réelle se mesurent sur le serveur (docs/deploiement.md, section Traces).

## Limites

* Tempo garde 72 h : une alerte plus ancienne garde son identifiant de trace, mais la trace n'existe plus.
* Les spans de l'interface partent toutes les 10 s ; une page fermée avant perd au plus ce lot.
* Un travailleur détruit avant d'avoir envoyé ses derniers spans les perd (il envoie après chaque granule ou scène,
  et juste avant la fin).
* Sans collecteur (Mac), les services créent leurs spans sans les envoyer ; leurs identifiants sont consignés quand
  même.
