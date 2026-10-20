# Tip and cue : une alerte déclenche une vérification satellite (lot T1, 20/10/2026)

Un signal suspect (le tip) crée une demande d'observation (le cue). MARS C2 prédit la position du navire au prochain
passage Sentinel 1 qui la couvre (ou à un passage acquis dans les dernières heures), analyse la fenêtre utile dans la
session Sentinel du passage et rattache le résultat à l'alerte d'origine. Code : `mars/tip_and_cue.py` ; réglages :
section `tip_and_cue` de `config/rules.yaml` (règles 2026.10.31) ; table `demandes_observation` (migration 31).

**Activé le 20/10/2026** (règles 2026.10.32), après le rejeu sur le serveur : tips coupure_ais, viirs, rendezvous,
identite et demande manuelle ; `watchlist` coupé (la sélection directe des navires des listes dans un corridor reste en
place) ; VIIRS limité à 12 h entre la lumière et le passage ; budget Sentinel de 8 € par mois, partagé entre tip and cue
et la sélection, plafond de 2 scènes par jour inchangé. Remettre `actif: false` coupe tout au cycle suivant.

## Chaîne

1. **Tips** (toutes les 5 minutes, tâche `tip_and_cue` de taches, après les règles) : alertes créées ou mises à jour
   depuis 6 h, et épisodes de coupure du dernier cycle des règles.

   | Tip | Source | Condition | Priorité |
   |---|---|---|---|
   | operateur | API, fiche navire, fiche alerte, clic droit sur la carte | demande manuelle | 6 |
   | coupure_ais | épisodes en observation (task_runs), ou AIS_GAP en mode alertes | gravité élevée, à moins de 2 milles d'une infrastructure ; motif « coupure observée » | 5 |
   | viirs | DARK_SHIP de source VIIRS | gravité moyenne au moins ; passage 12 h au plus après la lumière (`delai_max_h`) | 4 |
   | rendezvous | RENDEZVOUS | à 5 km de la côte au moins | 4 |
   | watchlist | WATCHLIST | navire à moins de 2 milles d'une infrastructure (clé du jour) | 3 |
   | identite | IDENTITY_CHANGE | gravité moyenne au moins | 2 |

   Chaque type se coupe par `actif: false`. Quand tip and cue est actif avec `watchlist` actif, le point « navire des
   listes dans un corridor » de la sélection Sentinel n'est plus cherché directement : il arrive par les demandes (un
   seul mécanisme). `watchlist: {actif: false}` rend la sélection d'avant.

2. **Demande** : origine (alerte, détection VIIRS, opérateur : table `demandes_origines`, une clé par tip, jamais deux
   fois la même), navire, dernière position et son heure, état (`en_attente`, `planifiee`, `analysee`,
   `sans_passage`, `expiree`, `refusee_budget`, `annulee`), passage, analyse, résultat, motif. Les tips d'un même navire
   (ou, sans navire, à moins de 10 km) rejoignent la demande active ; la priorité retenue est la plus haute. Expiration
   36 h après la création sans passage utile.

3. **Prédiction** à l'heure de chaque passage candidat :
   * navire qui émet encore : dernière position AIS reçue avant le passage (replanification à chaque cycle tant
     qu'aucune analyse ne porte la demande) ; navire muet : dernière position connue et estime (dernier cap, dernière
     vitesse) ;
   * cercle d'incertitude : 2 km, plus 30 % de la distance parcourue à la vitesse déclarée, 30 km au plus ;
   * à l'arrêt (moins de 1 nœud) ou au mouillage (statut AIS) : position fixe, 1,5 km ;
   * détection VIIRS ou point de la carte : position du tip, 3 km ;
   * le cercle devient une ou plusieurs fenêtres de 24 km (une jusqu'à 10 km de rayon, quatre jusqu'à 20 km) ; au delà
     de 4 fenêtres, ou centre à moins de 2 km de la côte : passage écarté avec son motif ; si c'était le seul, demande
     « sans_passage ».

4. **Passage** : d'abord un passage acquis dans les 6 dernières heures qui couvre la position prédite (déjà analysé :
   utile seulement si ses fenêtres couvrent le cercle, résultat aussitôt), sinon le prochain passage prévu (calendrier
   du lot A, ligne mise à jour en place à l'acquisition). Les centres des fenêtres deviennent des points de
   déclenchement « tip_and_cue » de la sélection Sentinel du passage : même session de travailleur que les autres points,
   mêmes plafonds, même budget. Dans le plafond quotidien, les passages et les scènes qui portent une demande passent
   d'abord, par priorité du tip. Plafond ou budget atteint : demande `refusee_budget`, avec le motif.

5. **Résultat**, à la fin de l'analyse (`sentinel.ingest`) : détections dans le cercle, de la plus proche à la plus
   lointaine, échos de terre, d'éolienne et fixes ignorés.

   | Résultat | Sens |
   |---|---|
   | present_apparie | écho apparié au navire attendu (ou, sans navire, à un navire AIS) : présent, AIS cohérent |
   | present_sans_ais | écho sans AIS : navire présent mais muet, preuve forte |
   | absent | aucun écho : absent ou parti (les petites unités échappent au radar) |
   | non_evaluable | écho jugé non évaluable pour l'AIS, ou analyse en échec |

   Preuves ajoutées à chaque alerte d'origine (`alert_evidence` : `demande`, et `detection` de l'écho retenu), section
   `verification_satellite` de ses détails (résultat, distance à la position prédite, passage, gravité avant), gravité
   relevée ou abaissée selon `tip_and_cue.ajustements`, jamais de retrait. Une notification ntfy part pour une alerte
   relevée à élevée ou critique. La vérification et la gravité ajustée survivent aux mises à jour en place de la règle
   (règles en continu, nuit VIIRS, analyse Sentinel : `GARDE_VERIFICATION` dans `mars/evaluation.py`).

   | Tip | Résultat | Gravité de l'alerte |
   |---|---|---|
   | viirs | present_sans_ais | élevée |
   | viirs | present_apparie | faible (la lumière était un navire AIS) |
   | coupure_ais, rendezvous, identite | present_sans_ais | élevée |
   | watchlist | present_sans_ais | critique |

## Interface, API, traces, Grafana

* Fiche d'alerte : section « Vérification satellite demandée » (source, état, passage et heure, cercle, puis résultat,
  vignette radar cliquable vers la fiche de la détection, gravité ajustée, motif d'un refus) ; fiche navire : même
  section ; bouton « Demander une observation » sur les deux fiches et au clic droit sur la carte ; frise : point au
  dessus d'un passage qui porte une demande ; indicateur « Satellites » : demandes en attente et planifiées, prochaine
  observation prévue.
* Panneau « Observations » du rail : une ligne par demande (icône et couleur du type de tip, celles des alertes ;
  pavillon et nom du navire, ou « point » ; état ; heure du passage ; résultat en un signe : coche pour présent avec
  AIS, œil barré couleur du navire sombre pour présent sans AIS, tiret pour absent, point d'interrogation pour non
  évaluable). Ordre de l'API : planifiées par passage le plus proche, en attente, analysées les plus récentes ; les
  expirées, sans passage, refusées et annulées repliées en bas. Filtre par état ; région de la barre d'état et plage
  de temps comme le fil (les demandes actives restent toujours listées). Un clic ouvre la fiche du navire (ou de
  l'alerte d'origine pour un tip sans navire) et dessine la position prédite et son cercle d'incertitude. Compteur des
  demandes actives de la région sur l'icône.
* API : `GET /api/observations?start&end&region&etat` (panneau : navire, alerte d'origine et son type, passage,
  résultat, position prédite, rayon, ordre et compteur `actives`), `GET /api/demandes?alerte_id=|vessel_id=`, `GET /api/demandes/{id}`, `POST /api/demandes` (navire, alerte ou
  point ; refus 409 tant que tip and cue est coupé), `POST /api/demandes/{id}/annuler` ; `satellites.tip_and_cue` dans
  `/api/ingestion` ; `demandes` (nombre) dans les passages de `/api/satellites/passes`.
* Traces : spans `tip_and_cue.demande` (lien vers la trace de l'alerte d'origine), `tip_and_cue.planification` (lien
  vers la trace de la demande : passage, heure, rayon, fenêtres, motif), `tip_and_cue.resultat` (liens vers la demande
  et l'analyse) et `tip_and_cue.retour_alerte` (lien vers l'alerte : gravité avant et après). Une alerte ne garde que
  l'identifiant de sa trace : le lien vise la trace, Tempo l'ouvre.
* Grafana, tableau Satellites, section « Tip and cue (lot T1) » : demandes en cours, délai médian du tip à
  l'observation, présents sans AIS, coût des analyses portant une demande, demandes par source et par état, résultats
  par catégorie, dernières demandes avec leur trace.

## Rejeu sur les données du serveur (20/10/2026)

Alertes du 13/10 08:26 au 20/10 08:26 UTC lues par l'API du serveur en lecture seule (78 alertes), passages Sentinel 1
IW de la période et des 36 h suivantes (29, dont 8 prévus), position, cap et vitesse des navires par
`/api/traffic?at=`, côte par le masque des terres de la base de test (même source), mêmes fonctions que le code
(`tip_and_cue.rejeu`, avec la replanification sur la dernière position reçue). Les épisodes de coupure observés ne sont
pas lisibles par l'API (task_runs) : à compter sur le serveur avec `taches.py tip-rejeu 7`.

| Tip | Tips | Regroupés | Passage utile | Côte | Incertitude | Sans passage en 36 h |
|---|---|---|---|---|---|---|
| viirs (moyenne au moins) | 9 | 0 | 5 | 0 | 0 | 4 |
| rendezvous (5 km au moins) | 3 | 0 | 1 | 0 | 0 | 2 |
| identite (moyenne au moins) | 4 | 0 | 0 | 1 | 0 | 3 |
| watchlist (près d'une infrastructure) | 27 | 3 | 1 | 4 | 4 | 15 |

Écartés avant toute demande : 11 changements d'identité faibles, 4 alertes VIIRS faibles, 8 navires des listes loin
de toute infrastructure, 5 navires absents du trafic à l'instant du tip.

* **7 demandes auraient trouvé un passage**, délai du tip au passage de 0 à 23 h (médiane 11 h), rayon de 1,5 à 3 km,
  une fenêtre chacune : **6 sessions en 7 jours** (deux demandes VIIRS sur le même passage), 1,14 € au total, **environ
  4,90 € par mois** à 0,19 € la session (GP1-S, une heure entamée).
* **Navires des listes : presque rien.** Ce sont surtout des pétroliers en transit (pas de Calais, rail d'Ouessant) :
  au passage suivant, ils ont quitté notre collecte AIS ; l'estime les sort des emprises ou porte leur cercle au delà
  de 4 fenêtres. Quatre sont écartés à quai (VLADIMIR LATYSHEV devant Saint Malo, ONIKS, CHAROIT).
* **VIIRS : 5 sur 9**, mais 10 à 23 h après la lumière, avec un rayon fixe de 3 km : un navire sombre qui fait route
  sera parti ; le résultat vaut surtout pour une activité stationnaire (pêche, rendez vous, attente).
* Sans la replanification sur la dernière position reçue, le rendez vous d'EEMS DOVER aurait fait analyser son ancien
  emplacement, quitté depuis : la replanification évite une session inutile.

## Rejeu sur le serveur (`taches.py tip-rejeu 7`, 20/10/2026, tous tips actifs)

coupure_ais 27 tips dont 6 avec passage, identite 6 dont 0, rendezvous 3 dont 1, viirs 9 dont 5, watchlist 28 dont 5 ;
délai médian 8,6 h ; 10 sessions en 7 jours, 26 fenêtres, environ 9,01 € par mois.

## Réglages de l'activation, rejoués

Rejeu par l'API avec les réglages 2026.10.32 (sans les coupures, illisibles par l'API) : VIIRS 9 tips dont **1** avec
passage (4 écartés par le délai de 12 h : la nuit VIIRS est vers 01 à 03 UTC, les passages de fin d'après midi tombent
14 à 16 h plus tard ; la proposition antérieure « 4 sur 5 » comptait à tort depuis la création de l'alerte, vers 06:30),
rendez vous 1, identité 0, navires des listes coupés : 2 sessions en 7 jours, environ 1,60 € par mois. Avec les 6
coupures à passage du rejeu serveur, au plus 8 sessions en 7 jours : **de 1,60 à 6,50 € par mois** selon que les
coupures partagent ou non leurs passages, à confirmer par `tip-rejeu 7` sur le serveur (colonne « délai » ajoutée).

## Propositions avant l'activation (20/10/2026, retenues sauf le budget, porté à 8 €)

1. `tip_and_cue.actif: true`, avec `tips.watchlist.actif: false` : la sélection directe des navires des listes dans un
   corridor (au passage même, positions réelles) reste en place ; le tip watchlist ne trouverait qu'un passage sur 27.
2. VIIRS : garder la gravité moyenne au moins, et ajouter un délai maximal entre la lumière et le passage (12 h ; le compte « 4 sur 5 » ci dessous était faux, voir plus haut :
   4 passages sur 5 gardés) pour ne pas analyser une zone qu'un navire sombre a quittée ; à coder sur accord.
3. Coupures AIS : mesurer d'abord sur le serveur (`taches.py tip-rejeu 7`) ; la règle reste en observation.
4. Budget : environ 5 € par mois pour le tip and cue seul, sur un budget de 6 € partagé avec la sélection habituelle :
   **10 € par mois** (le plafond de 2 scènes par jour suffit, moins d'une session par jour). Sinon les demandes de fin
   de mois seront refusées, avec leur motif.

## Commandes

```bash
docker compose exec taches python scripts/taches.py tip-rejeu 7     # lecture seule, même coupé : tips, passages, coût
```

Le cycle tourne dans la boucle de taches toutes les 5 minutes quand `actif: true` (`config/rules.yaml` relu à chaque
tour, aucun redémarrage).

## Limites

* La replanification suit la dernière position reçue ; un navire qui sort de notre collecte sans être muet (transit
  vers le large) est prédit à l'estime comme un navire sombre.
* Le résultat « absent » ne distingue pas un navire parti d'une petite unité invisible au radar (moins de 20 à 30 m).
* Un passage déjà analysé ne l'est pas une seconde fois (une analyse par passage) : utile seulement si ses fenêtres
  couvrent le cercle.
* Le regroupement par zone ne vaut que pour les tips sans navire (VIIRS, point) à moins de 10 km.
* Un épisode de coupure en observation n'est lu que parmi les 30 plus longs du dernier cycle des règles (task_runs).
