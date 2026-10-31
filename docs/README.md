# Documentation de MARS C2

Index des documents de `docs/` au 24/10/2026 (lot Z3). La mémoire du projet est `CLAUDE.md`, à la racine du dépôt.

## Ordre de lecture conseillé

1. `CLAUDE.md` puis `bilan.md` : le projet, son état et ce qui reste à faire.
2. `exploitation.md`, `deploiement.md` et `supervision.md` : faire tourner, publier, surveiller et restaurer.
3. `regles_alertes.md`, `calibration.md`, puis le document de chaque sujet (`travailleurs.md`, `sentinel.md`,
   `ux_refonte.md`…) ; `journal.md` pour l'historique daté.

## Documents en vigueur

| Document | Sujet | État |
|---|---|---|
| `journal.md` | Journal daté des jalons du projet | à jour au 24/10/2026 |
| `rapport_mission_socle_a.md` | Rapport final de la mission socle A (27 au 31/10/2026) : étapes, dépense, verdicts, décisions | à jour au 31/10/2026 |
| `bilan.md` | Bilan et plan de consolidation du lot Z (audit du 22/10/2026) | à jour au 24/10/2026 |
| `exploitation.md` | Publier, déployer, restaurer, échéances des secrets, reprise après sinistre | à jour au 24/10/2026 |
| `deploiement.md` | Installation du serveur Scaleway, R2, vérification, opérations ponctuelles | à jour au 24/10/2026 |
| `supervision.md` | Grafana, notifications sur le téléphone, fin des pannes silencieuses (étape D) | à jour au 24/10/2026 |
| `opentelemetry.md` | Traces OpenTelemetry de tout le système, Grafana Tempo (étape D2) | à jour au 24/10/2026 |
| `regles_alertes.md` | Chaque type d'alerte : mode, seuils, origine | à jour au 24/10/2026 |
| `calibration.md` | Plan de calibration vivant des seuils pour la France | à jour au 24/10/2026 |
| `pieges.md` | Pièges vivants en détail (résumés dans `CLAUDE.md`, section 11) | à jour au 24/10/2026 |
| `pieges_archives.md` | Pièges résolus, sortis de `CLAUDE.md` | à jour au 24/10/2026 |
| `reference.md` | Référence technique détaillée : démarrage, architecture, arborescence, base et API, interface, modèle radar | à jour au 24/10/2026 |
| `travailleurs.md` | Architecture commune des travailleurs éphémères (VIIRS, Sentinel 1 et 2) | à jour au 24/10/2026 |
| `passages_satellites.md` | Calendrier des passages Sentinel 1 et 2 (étape 3, lot A) | à jour au 24/10/2026 |
| `sentinel.md` | Analyse Sentinel 1 des passages par un travailleur éphémère, processeur d'abord (étape 3, lot C) | à jour au 24/10/2026 |
| `sentinel2.md` | Sentinel 2 : mesures avant le lot S2 et détection optique | à jour au 24/10/2026 |
| `tip_and_cue.md` | Une alerte déclenche une vérification satellite (lot T1) | à jour au 24/10/2026 |
| `controle.md` | Panneau de contrôle : capteurs, dépenses, règles, notifications, zones, démonstration (lot P) | à jour au 24/10/2026 |
| `ux_refonte.md` | Interface de référence : refonte du lot U (dossiers, Veille, Coulisses), registres et principes | à jour au 24/10/2026 |
| `trajectoire.md` | Prévision de trajectoire, phase 1 : données, faisabilité, référence (lot T) | à jour au 24/10/2026 |
| `images/` | Captures du README de la vitrine, en mode démonstration (pseudonymes, aucune photo) | à jour au 24/10/2026 |

`mesures_calibration.md` n'est plus versionné : `taches.py mesures` le réécrit dans `docs/` à chaque exécution
(rapport de mesures pour la recalibration, voir `calibration.md`).

## Archives (`docs/archives/`)

| Document | Sujet | État |
|---|---|---|
| `archives/audit_code.md` | Audit du code du 06/10/2026, méthode de recalibration (reprise dans `calibration.md`) | archivé |
| `archives/historique_danemark.md` | Première version (rejeu de journées danoises) et ses enseignements de calibration | archivé |
| `archives/inventaire_worktree.md` | Inventaire du worktree de calibration à porter vers le dépôt principal | archivé |
| `archives/vision_interface.md` | Vision de l'interface du 06/10/2026 (lots 1 et 2) ; ce qui reste vrai est dans `ux_refonte.md`, section 14 | archivé |
| `archives/travailleurs_viirs.md` | Travailleurs éphémères et détections nocturnes VIIRS (lot B), fusionné dans `travailleurs.md` | archivé |
| `archives/CLAUDE_avant_Z3.md` | `CLAUDE.md` tel qu'il était avant sa réécriture du lot Z3 | archivé |
| `archives/maquettes_ux/` | Maquettes HTML du lot U, prototype des dossiers (`dossiers.py`) et générateur (`generer.py`) | archivé |
