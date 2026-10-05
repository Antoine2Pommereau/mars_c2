# Inventaire du worktree de calibration, à porter vers le dépôt principal

Ce document suit ce qui a été construit dans le worktree de la session de calibration
(branche `xview3-section-17-eval-57d052b3`) et qui n'est pas encore dans le dépôt principal.
Pour chaque brique : où elle vit, si elle est portée, et si elle est réutilisable pour la France
ou spécifique au Danemark. On le tient à jour au fil des portages.

Base de comparaison : commit `ef1560f`. La session a touché 53 fichiers, environ 3 400 lignes.

## Déjà porté dans le dépôt principal

* Régions et table `infrastructure` (migration `10_regions_infrastructure.sql`, reprise des migrations 11 et 13
  du worktree, avec en plus le type `windfarm` et les deux zones France Bretagne et Mediterranee).
* `mars/regions/provision.py` (partie infrastructure, adaptée : parcs éoliens ajoutés, distinction câbles
  télécoms et câbles électriques).
* `scripts/provision_region.py`.
* Donnée France en base : Bretagne 254 câbles et 7 parcs éoliens, Mediterranee 171 câbles et 8 parcs.
* Affichage des câbles et parcs sur la carte du dépôt principal. (à compléter, en cours)

## Non porté, réutilisable pour la France

| Élément | Fichiers (worktree) | Priorité France | Note |
|---|---|---|---|
| Alerte menace infrastructure (`INFRA_THREAT`) | `14_infra_threat_alert.sql`, `rules.py`, section infrastructure de `rules.yaml` | Coeur piste 1 | Ancre traînante, arrêt ou flânerie près d'un corridor. Besoin de positions AIS France en base. Code générique, seuils à recalibrer. |
| Tip and Cue | `tipcue` dans `app.py`, bouton `DetailPanel`, action `tip_and_cue` (migration 18) | Coeur cueing | La coupure AIS projette une zone et déclenche une analyse. Besoin de positions AIS France en base. |
| Recherche et fiche navire | `search_vessels`, `vessel_dossier`, `VesselsPanel`, dossier dans `DetailPanel` | Haute | Un des quatre emprunts GFW de la section 0. Générique. |
| Score de risque | `backend/risk.py`, `tests/test_risk.py`, section risk_score | Moyenne | Somme pondérée auditable. Générique. |
| Photo AIS et slideshow | `vessel_photo`, `VesselPhoto`, `Slideshow`, `Chip` | Moyenne | VesselFinder par MMSI, marche partout. |
| Aires protégées et franchissement (`ZONE_BREACH`) | `16_protected_areas.sql`, `17_zone_breach_alert.sql`, provision_protected_areas, `rules.py` | Moyenne | Natura 2000 et aires protégées européennes ; la France en a beaucoup. |
| Incohérence d'identité (`IDENTITY_MISMATCH`) | `15_identity_mismatch_alert.sql`, run_analysis, section identity | Utile piste 2 | Longueur radar contre AIS. Bridée par la sous mesure du radar. |
| Collaboration (commenter, assigner) | `18_collaboration.sql`, `AlertActions` | Basse | Confort opérationnel. |
| Bathymétrie | `12_bathymetry_contours.sql`, provision_bathymetry, endpoints, couche carte, `region_depth` | Basse | EMODnet Bathymetry pan européen. Contexte d'affichage. |
| Gestion des régions | endpoints regions, `RegionsPanel`, tracé | Moyenne | Gérer les zones France depuis l'interface. |
| Plomberie interface | couches `MapView`, `LayersPanel`, `App`, `Rail`, `api/types/format`, `styles` | Haute | La couche infrastructure de `MapView` sert à voir les câbles. |
| Spec régions | `docs/spec_regions.md` | Basse | Document de conception du provisionnement. |

## Non porté, spécifique au Danemark

| Élément | Fichiers | Pourquoi |
|---|---|---|
| Valeurs de calibration des règles | seuils de `rules.yaml` (côtier, mouillages, réception) | Calés sur deux journées danoises, à refaire pour la France. Le code des règles est générique. |
| Lecture AIS DMA | `mars/ais/dma.py`, `scripts/import_ais.py` | Format danois. La France passera par `ais_live.py`. |
| Zones et enseignements de test | Skagen, Anholt, SILVER KENNA | Cas danois, récit de calibration, pas donnée France. |

## Zone grise : améliorations génériques du moteur

Corrections de l'audit sur le moteur SAR et fusion, ni France ni Danemark, réutilisables et à porter
indépendamment (le CircleNet reste en repli et comparaison) : `mars/fusion/match.py`, `mars/fusion/pipeline.py`,
`mars/sar/inference.py`, `mars/sar/sentinelhub.py`, `scripts/sweep_threshold.py`, `scripts/build_masks.py`.
Attention aussi aux changements de `docker-compose.yml`, `pyproject.toml`, `requirements.txt`, `start.sh`
(dépendances comme `requests`, `rasterio`).

## À créer, n'existe encore nulle part

| Élément | Source | Réutilisable France | Note |
|---|---|---|---|
| Liste de la flotte fantôme | catalogue GUR repris par `shadow-fleet-tracker-light` (fichier `Vessels1.db`) et OpenSanctions, identification par **OMI** | Base de la piste 2 | Ni dans le worktree ni dans le dépôt principal. L'OMI est permanent, contrairement au MMSI. À importer en table dédiée, puis croiser avec les navires vus. |
