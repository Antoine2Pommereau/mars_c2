# MARS C2 : rapport de fin de projet (31/10/2026)

Mission finale « clôture et vitrine », menée en autonomie le 31/10/2026 avec accès SSH au serveur, sous les règles de
la mission socle A (garde, lecture seule par défaut, simulation avant toute écriture sur les données, `publier.sh`
puis `deployer.sh`, VERDICT à OK, jamais le `.env`, jamais d'historique réécrit, dépôt jamais rendu public). Détail
daté : `journal.md` ; audit : `audit_final.md` ; mission précédente : `rapport_mission_socle_a.md`.

## En bref

* **Version finale** : la dernière publication de `main`, déployée avec VERDICT à OK partout (section 6) ; règles
  2026.10.67, migrations 01 à 48.
* **Tests** : 602 tests Python avec la base de test, trois passages complets identiques ; 42 tests de l'interface ;
  typecheck et knip propres ; gitleaks vert sur tout l'historique (190 commits).
* **Dépense de tous les essais réels** (07 au 31/10/2026) : **5,03 € HT**, 55 travailleurs (Sentinel 1 3,32 €,
  Sentinel 2 1,14 €, VIIRS 0,57 €). Missions socle A et finale : 1,33 € sur les 3,00 € autorisés. Aucun essai payant
  hors de l'étape 6.
* **Serveur** : environ 19 € HT par mois en régime permanent (facture Scaleway), sauvegarde restaurée avec succès le
  31/10, aucun travailleur en vie.

## Phase 0 : ce qui était en cours

**0a, étape 6 (tip and cue).** Reprise le 31/10 à 10:28 UTC jusqu'au 05/11/2026 à 12:00 UTC, garde fou
`/tmp/z7/garde_tip4.sh` sur le serveur (journal `/tmp/z7/garde_tip.txt`) : il ne fait que remettre en pause tip and
cue et Sentinel 1, avec le motif « fin du projet », à la première demande analysée, à l'échéance, à 2,81 € de dépense
de la mission, sur un travailleur en vie depuis plus d'une heure, une analyse sans historique, ou une alerte élevée
ou critique issue d'une analyse, sauf si son seul motif d'élévation est un navire des listes à plus de 2 km.
* Arrêt de sécurité de la nuit précédente : analyse automatique 114, alerte 146448 (écho de 23 m, ALTAI à 6,3 km),
  examinée : petite unité sans AIS, aucun indice de rencontre.
* Défauts trouvés sur les vraies demandes et corrigés (`4cc4063`) : la demande 51 quittait son passage trois minutes
  après la prise de vue (passage marqué « acquis » seulement la nuit) ; l'analyse 113 était écrite avant le contrôle
  du plafond de travailleurs, et ses demandes seraient restées bloquées.
* **État au 31/10 à 11:10 UTC** : 60 demandes depuis le 20/10, **aucune encore analysée et évaluée** (53 « sans
  passage », presque toutes un navire au quai ou trop près de la côte, invisible au radar ; 7 expirées). La preuve de
  B05 reste à venir ; si elle arrive avant le 05/11, le garde fou met tout en pause et le bilan prévu (demande,
  analyse, alerte d'origine, trace, AIS, image publique) reste à écrire.

**0b, gravité des échos sans AIS** (règles 2026.10.66). Mesure sur les 10 analyses réelles depuis le 19/10 (58
détections, 41 appariées) : les 3 échos appariés mesurés sous 25 m sont des navires de 13 à 37 m, aucun des 38 navires
de plus de 50 m n'est mesuré sous 25 m (le plus court : 34 m pour 119 m déclarés) ; 4 des 6 alertes de navire sombre
radar étaient relevées par un navire des listes à 3,5 à 6,3 km, sans rencontre. Règle : un navire des listes ne relève
la gravité qu'à moins de 2 km (rendez vous à 500 m, plus l'approche et l'écart de l'AIS) ; un écho mesuré sous 25 m
sans autre indice reste faible « petite unité, AIS non obligatoire sous 15 m » ; une infrastructure à moins de 500 m
garde son effet. Simulations conformes au critère écrit avant, puis refusion de 108 et 114 : 76467 et 92603 de
critique à élevée (câble à 487 et 592 m), 146448 d'élevée à faible ; décisions d'opérateurs gardées (92604 reste
classée), rien effacé. 92603 (30 m) n'entre pas dans la petite unité : 25 m est la borne justifiée par la mesure.

**0c, flottilles.** En observation depuis le 31/10 (règles 2026.10.67) : rendez vous entre deux unités de même code
pays, MMSI à moins de 10 l'un de l'autre, classe B sans type ni longueur, aucune des listes ; compté dans `task_runs`
(`flottilles_observees`), gravité inchangée. **Décision chiffrée** : sur 25 jours, 2 rendez vous sur 10 répondaient à
la définition, dont un (56150) déjà écarté par la règle des balises de pêche ; il reste le seul cas 125730. Effet si la
règle était appliquée : une alerte élevée de moins, rien d'autre. Recommandation : l'appliquer si la semaine
d'observation ne compte aucune paire hors de ce type ; le compte se lit dans le détail des cycles de la tâche
`regles` de `task_runs` (clé `flottilles_observees`).

## Phase 1 : audit (lecture seule)

`audit_final.md`, écrit et versionné avant toute correction. Constats principaux : code sain (0 erreur F de ruff,
aucun TODO, aucun script orphelin), deux symboles morts, restes de la voie GPU, images de base non épinglées, paquet
de l'interface en un seul fichier, trois clés étrangères de traçabilité manquantes, statistiques du planificateur
jamais calculées sur deux tables, données saines (aucun doublon, aucune alerte sans preuve, aucune demande bloquée),
restauration réussie.

## Phase 2 : corrections, avant et après

| Constat | Avant | Après |
|---|---|---|
| Code mort réel | 2 symboles | 0 |
| Variables d'environnement à défaut non textuel | 6 | 0 |
| Images de base épinglées par empreinte | 0 sur 4 | 4 sur 4 |
| Restes de la voie GPU | table `types_incompatibles`, états `attente_gpu` et `gpu_inutilisable`, Grafana | migration 47 : table supprimée, état `attente_calcul`, tableaux régénérés |
| Clés étrangères de traçabilité | 0 sur 3 | 3 sur 3 (migration 48, aucun orphelin) |
| Statistiques du planificateur (`land`, `infrastructure`) | jamais calculées | `VACUUM ANALYZE` de toute la base (9,5 s ; le premier essai a échoué sur la mémoire partagée de Docker, refait sans parallélisme) |
| Paquet de l'interface | un fichier de 1 291 Ko (363 Ko compressé) | code de l'application 242 Ko ; MapLibre (802 Ko) et React (183 Ko) à part, en cache d'une version à l'autre |
| Premier chargement, mesuré sur le serveur | 1 395 Ko, sans compression ni cache | environ 363 Ko, gzip, fichiers gardés un an ; visites suivantes : la page seule |
| Infrastructures de la carte (API) | 210 Ko | 20 Ko (gzip) |
| Branches distantes fusionnées | 79 (puis 81 avec `flottille-observation` et `readme-vitrine`) | supprimées (liste en annexe) |

Non faits, avec leur raison (`audit_final.md`, section 7) : découpage des fonctions complexes et mises en commun du
lot Z4 (risque sans gain avant la clôture, harnais de rejeu à l'identique à construire), style ruff (1 254 remarques
de forme), passage à MapLibre 5 ou 6 (faille non atteignable : aucune bulle HTML), Coulisses non découpées (leurs
rubriques sont construites par la page principale). Une retouche à noter : la migration 39 (registre) reconnaît
désormais la 29 par une colonne de la 30 ; `deployer.sh` le signale par « 39 modifié depuis son application »,
attendu.

## Phase 3 : interface

Le bloc du lot UX1 annoncé n'a pas été joint au message. Les lots UX1 (identité, unités marines, glossaire, pages
d'erreur, liens des notifications, contraste) et UX2 (fiche de signalement) ont été livrés et déployés le 29/10/2026
(`6082406`, `884bb6b`) ; la version finale les garde. Rien d'autre n'a été ajouté.

## Phase 4 : vitrine et documentation

* `README.md` en anglais (paragraphe français à la fin), fusion de `readme-vitrine` réécrite avec l'état final :
  preuves chiffrées sur données réelles, ce que les données réelles ont appris, chiffres, état honnête, méthode.
  Aucun nom de navire réel dans le texte.
* Captures, en mode démonstration : Coulisses Budget sur le serveur réel par le tunnel ; vignette radar et vignette
  optique du même navire côte à côte (images des travailleurs, sans nom) ; trace d'une analyse réelle en vue Gantt,
  tracée depuis les données de Tempo (la connexion à Grafana demande le mot de passe du `.env`, non lu) ; fiche de
  signalement d'exemple (pseudonymes, MMSI et OMI absents de la base). Les vues avec carte viennent de la base de test :
  le navigateur sans affichage disponible n'a pas WebGL, Aside n'est pas installé.
* `CLAUDE.md` à l'état final, lien de la Claude Doc retiré (aussi de l'archive) ; `exploitation.md`, section 11
  « Mise en veille et réveil » ; index `docs/README.md` à jour ; journal clos.

## Phase 5 : état final du serveur

| Point | État |
|---|---|
| Version | finale déployée, VERDICT à OK partout |
| Restauration | essai complet réussi le 31/10 (sauvegarde de 6,9 Mo restaurée en 4,9 s, comptes identiques) |
| Travailleurs | aucun en vie |
| Sentinel 2 | en pause |
| Sentinel 1, tip and cue | en service pour l'étape 6 jusqu'au 05/11 12:00 UTC au plus tard, puis en pause par le garde fou, motif « fin du projet » |
| VIIRS | en service |
| `/tmp/z7` | nettoyé, sauf le garde fou en marche et son journal (à supprimer après le 05/11) |

## Ce qui revient à Antoine

1. **Rendre le dépôt public** quand tu le décides (Settings, General, Danger Zone) ; déposer aussi
   `docs/images/apercu_social.png` comme aperçu social.
2. **Renouveler la paire Copernicus des travailleurs avant le 14/11/2026** si le serveur reste en service
   (`exploitation.md`, section 6).
3. **Copier le `.env` du serveur dans ton gestionnaire de mots de passe** (liste des variables en section 5 de
   `exploitation.md`) : sans lui, aucune reconstruction depuis R2.
4. **Décider des alertes à confirmer** : 76467 (bateau feu de Sandettie, à classer), 92603 (petite unité réelle
   sans AIS près d'un câble), 146448 (petite unité, faible), 63928 (OMI de remplissage, acquittée), VIIRS 15198,
   45569 et 80242, le rendez vous 125730 (flottille), et les alertes WATCHLIST ouvertes (vraies par construction).
5. **Flottilles** : appliquer ou non la règle après la semaine d'observation (phase 0c).
6. **Quand éteindre le serveur** : procédure en section 11 de `exploitation.md` (tout en pause, archive et
   sauvegarde du jour, essai de restauration, extinction ; suppression seulement après l'essai réussi).
7. Supprimer `/tmp/z7` sur le serveur après le 05/11, une fois le garde fou arrivé à « FIN ».

## Si le projet reprend

Découper `run_ais_gap`, `launch` et `_screen_and_alert` avec un harnais de rejeu des scènes 103, 105, 108 et 112 ;
précalculer les tranches des rendez vous (8,6 h de calcul de la base en une semaine) ; MapLibre 5 ; sortir les
coupures AIS de l'observation après une injection de silences sans réapparition ; trajectoire, étape 2.

## Annexe : branches distantes supprimées

Fusionnées dans `main` et supprimées à la clôture : `bilan`, `cercle-appris`, `cles-travailleur-cdse`, `consolidation-z1-z2`, `consolidation-z3`, `consolidation-z4`,
`consolidation-z6`, `corrections-lot-c`, `coulisses-mece`, `coulisses-rubriques`, `d02-remasquer`,
`d03-estime-rebours`, `diagnostic-l4-cles`, `e01-bord-zone`, `e02-duree-elevee`, `e06-documentation`,
`eclaircissements-lot-c`, `enquete-s1-103`, `essai-refaire`, `fiches-sources-sejour`, `flottille-observation`,
`fusion-arret`, `garde-contenance`, `garde-travailleurs`, `historique-s1`, `hypotheses`, `imports-ai2`,
`journal-29-10`, `journal-30-10`, `journal-verdicts`, `lot-a-correctifs`, `lot-c-sentinel`, `lot-p-controle`,
`lot-u-construction`, `lot-u-ux`, `mission-socle-a`, `muets-transit`, `observations`, `opentelemetry`,
`plafond-commande`, `preuves-images`, `readme-vitrine`, `reglages-supervision`, `s1-absence`, `s1-echo-voisin`,
`s1-non-confirmees`, `s2-cap`, `s2-lecture-s3`, `s2-satlas-local`, `s2-seau-modeles`, `sejour-hors-port`,
`sentinel-automatique`, `sentinel-gpu-inutilisable`, `sentinel-robuste`, `sentinel2`, `sentinel2-mesures`,
`socle-a`, `stats-processeur`, `supervision`, `theme-clair`, `tip-and-cue`, `tip-preparation`, `trajectoire-phase1`,
`travailleurs-essai-5`, `travailleurs-essai-reel`, `travailleurs-image`, `ux1-identite`, `ux2-signalement`,
`verif-images-pg`, `viirs-calibration`, `viirs-masque`, `viirs-reevaluation`, `viirs-refusion`, `viirs-travailleurs`,
`viirs-voisines`, `volume-bloc-taille`, `vue-ensemble`, `z7-decisions`, `zone-fiable-observation`. Gardées :
`main`, `calibration-z7` (2 commits non fusionnés du 26/10, à examiner), `xview3-section-17-eval-57d052b3`
(évaluation xView3 du 04/10, hors du produit).
