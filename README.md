# MARS C2

Maritime Autonomous Reconnaissance and Surveillance, Command and Control.
Surveillance maritime par fusion des données AIS et de l'imagerie radar Sentinel 1.
La spécification complète décrit la vision, l'architecture et la feuille de route.

## État : phase 4 en cours, masques, rendez vous suspects et coupures AIS

**Coupures AIS** (`backend/rules.py`) : navire de classe A faisant route (au moins 1 nœud) qui cesse d'émettre
pendant au moins deux heures, à plus de 3 km des côtes, loin du bord des données chargées, et dans la **zone de
réception fiable** : cellules d'environ 6 km reçues pendant au moins 22 heures sur 24, pour au moins 20 navires.
Cette dernière condition évite de confondre un silence avec une sortie de la couverture des stations côtières.
Chaque alerte indique la dernière position et la réapparition, le déplacement pendant le silence, et les navires
restés lents à moins de 3 km du trajet présumé : des partenaires possibles d'une rencontre dissimulée.

```bash
docker compose exec -T db psql -U mars -d mars < db/init/06_ais_gap.sql
docker compose up -d --build backend
python scripts/import_ais.py --csv data/ais/aisdk-2024-06-05.csv --bbox 8.5 56.0 13.0 58.6
python scripts/build_masks.py --skip-land
python scripts/run_rules.py --day 2024-06-05
```


**Masques géographiques** (`scripts/build_masks.py`) : terres émergées issues de GSHHG en pleine résolution, qui
contient les petites îles (découpées sur la région et subdivisées pour accélérer les calculs de distance), et zones de
mouillage déduites de l'AIS : cellules d'environ 2 km où au moins quatre navires distincts sont restés immobiles. Une
détection radar à moins de 500 m de la terre est masquée.

**Rendez vous suspects** (`backend/rules.py`) : deux navires à moins de 500 m l'un de l'autre, à moins de 2 nœuds,
pendant au moins deux heures, à plus de 5 km des côtes ; tranches où l'un des navires se déclare amarré exclues ;
navires de service (remorqueurs, pilotes, secours…) exclus. Dans une zone de mouillage, l'alerte est conservée mais
déclassée en sévérité faible, avec son contexte ; un rendez vous bord à bord (moins de 50 m) est signalé comme tel. La règle est évaluée en SQL sur toute la journée, et chaque alerte est
horodatée à l'instant où les deux heures sont atteintes : elle n'apparaît qu'une fois cet instant franchi par le rejeu.
Tous les paramètres sont dans `config/rules.yaml`.

### Mise en route de la phase 4

```bash
pip install -e ".[test]"
docker compose exec -T db psql -U mars -d mars < db/init/04_masks.sql
docker compose exec -T db psql -U mars -d mars < db/init/05_nav_status.sql
docker compose up -d --build backend
python scripts/import_ais.py --csv data/ais/aisdk-2024-06-05.csv --bbox 8.5 56.0 13.0 58.6
python -m pytest tests
python scripts/build_masks.py --region 4 53 17 60
python scripts/run_rules.py --day 2024-06-05
```

## Phase 3, analyse à la demande

L'opérateur trace une zone sur la carte (Maj + glisser), choisit un passage Sentinel 1 parmi ceux qui couvrent la
zone sur les journées AIS chargées, et suit l'analyse étape par étape : extrait radar, détection, fusion avec l'AIS.
Les détections et les alertes s'affichent à la fin, avec la durée de chaque étape.

Architecture : le **service d'inférence** (dossier `inference`) porte l'accès à Sentinel Hub et le modèle, chargé et
préchauffé une seule fois au démarrage ; il traite les analyses l'une après l'autre. Le **backend** orchestre :
il crée l'analyse, confie l'extrait et la détection au service d'inférence, puis exécute la fusion et enregistre
détections, alertes et preuves. L'adresse du service d'inférence est un paramètre (`INFERENCE_URL`) : sur un Mac,
il tourne hors de Docker pour utiliser le GPU Apple ; ailleurs, il peut tourner en conteneur ou sur un GPU distant.

**Critère de sortie** : une zone et un passage soumis depuis l'interface produisent leurs détections en base en
quelques secondes, sans intervention manuelle.

### Passer de la phase 2 à la phase 3

```bash
docker compose exec -T db psql -U mars -d mars < db/init/02_simulation.sql
docker compose exec -T db psql -U mars -d mars < db/init/03_analyses.sql
pip install -e ".[test]"
docker compose up -d --build backend
docker compose restart frontend
```

Puis, dans un **second onglet du Terminal**, depuis `mars_c2` et avec l'environnement Python activé, démarrer le
service d'inférence et le laisser tourner :

```bash
uvicorn inference.service:app --host 0.0.0.0 --port 8001
```

Il affiche l'accélérateur utilisé et la durée du préchauffage. Vérifier enfin le contrat du modèle :

```bash
pytest tests
```

## Phase 2, flux AIS et simulation

La phase 2 met le trafic en mouvement. Le rejeu repose sur une horloge simulée stockée en base (table `sim_clock`,
fonction `sim_now()`) : l'API, l'interface et, plus tard, les règles raisonnent toutes sur le même « maintenant ».
Les positions importées constituent l'archive ; le rejeu révèle celles dont l'horodatage est dépassé par l'horloge.
L'interface reçoit le trafic par SSE une fois par seconde, avec la traînée récente de chaque navire.

**Critère de sortie** : les navires se déplacent sur la carte au rythme choisi.

### Passer de la phase 1 à la phase 2

```bash
docker compose exec -T db psql -U mars -d mars < db/init/02_simulation.sql
docker compose up -d --build backend
docker compose restart frontend
python scripts/import_ais.py --csv data/ais/aisdk-2024-06-05.csv --bbox 8.5 56.0 13.0 58.6
```

La première commande ajoute l'horloge et la table des journées AIS à la base existante. L'import élargi couvre le
Skagerrak et le Kattegat, et affiche le détail du nettoyage ainsi que le débit d'insertion. Ouvrir ensuite
http://localhost:8080 et cliquer sur Lecture.

## Phase 1, tranche verticale

Chaîne complète minimale, sur une zone et un passage fixes :

1. une base PostgreSQL et PostGIS avec le schéma initial ;
2. l'import d'une journée AIS de la Danish Maritime Authority ;
3. l'analyse radar d'une zone : extrait Sentinel Hub (sigma0, rééchantillonnage bilinéaire), détection par le
   modèle CircleNet V2S, filtre de contraste local, appariement avec l'AIS, alertes « navire sombre » et leurs preuves ;
4. une API FastAPI et une carte minimale.

**Critère de sortie** : une commande démarre le système, et un navire sombre apparaît sur la carte.

## Organisation du dépôt

| Dossier | Contenu |
|---|---|
| `db/init` | Schéma SQL, appliqué automatiquement au premier démarrage de la base |
| `config/rules.yaml` | Seuils et paramètres des règles, versionnés |
| `mars` | Code partagé : accès Sentinel Hub, inférence, lecture AIS, appariement |
| `scripts` | Import AIS et analyse d'une zone, exécutés depuis le Mac |
| `backend` | API FastAPI |
| `frontend` | Carte MapLibre servie par nginx |
| `data`, `models` | Données et modèle, non suivis par Git |

## Mise en route

**Prérequis** : Docker Desktop, Python 3.11 ou 3.12, un client OAuth Sentinel Hub (CDSE).

**1. Configuration.** Copier `.env.example` en `.env` et renseigner l'identifiant et le secret Sentinel Hub.

**2. Modèle et données.**

* Copier `xview3_membre4_v2s.jit` (Drive, `mars_c2/phase0/results`) dans `models/`.
* Télécharger `aisdk-2024-06-05.zip` sur aisdata.ais.dk et le décompresser dans `data/ais/`. Si le Finder échoue
  sur cette archive volumineuse, utiliser `ditto -x -k aisdk-2024-06-05.zip data/ais/` dans un terminal.

**3. Démarrer le système.**

```bash
docker compose up -d
```

La base, l'API (port 8000) et la carte (port 8080) démarrent. Au premier lancement, le schéma est créé automatiquement.

**4. Environnement Python des scripts.**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

**5. Importer l'AIS, puis analyser la zone.**

```bash
python scripts/import_ais.py --csv data/ais/aisdk-2024-06-05.csv --bbox 10.2 57.7 10.9 58.15
python scripts/analyze_zone.py --bbox 10.30 57.80 10.80 58.07 --time 2024-06-05T17:02:26Z
```

L'analyse affiche le passage retenu, le nombre de détections, les appariements, les alertes et les durées de chaque étape.
Sur un Mac, le modèle tourne sur le GPU Apple (MPS) s'il est disponible, sinon sur CPU.

**6. Ouvrir la carte** : http://localhost:8080

## Résultat attendu sur la zone de référence

Sur le passage du 5 juin 2024 à 17 h 02 UTC, la phase 0 a obtenu 11 détections, dont 5 appariées à l'AIS et 4 fausses
alertes sur du grain de mer éliminées par le contraste local. Il reste une alerte « navire sombre » : un écho de 44 m,
très contrasté, à près de 3 km de tout navire AIS. Selon la tolérance d'appariement, un navire proche d'un point AIS
peut aussi apparaître en alerte : c'est un réglage de la phase 4.

## Remettre la base à zéro

```bash
docker compose down -v
docker compose up -d
```
