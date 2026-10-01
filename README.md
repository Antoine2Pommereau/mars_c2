# MARS C2

Maritime Autonomous Reconnaissance and Surveillance, Command and Control.
Surveillance maritime par fusion des données AIS et de l'imagerie radar Sentinel 1.
La spécification complète décrit la vision, l'architecture et la feuille de route.

## État : phase 1, tranche verticale

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
