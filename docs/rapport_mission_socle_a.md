# Mission socle A : rapport final

Mission autonome du 27/10/2026 au 31/10/2026, menée par `claude-code` avec accès SSH au serveur. Détail daté dans
`journal.md` (entrées du 27/10 au 30/10/2026). État au 31/10/2026 à 00:00 UTC : étape 6 prolongée jusqu'au
05/11/2026 à 12:00 UTC à la demande d'Antoine, sous garde fou ; toutes les autres étapes sont closes.

## En bref

* **Dépense** : 0,95 € HT sur 3,00 € (cinq sessions GP1-S à 0,19 € : travailleurs 44, 47, 49, 51 et 52). Aucun POP2,
  aucun type non éprouvé, aucun Sentinel 2 automatique.
* **Versions déployées**, toutes avec VERDICT à OK partout : `02b660e` (27/10), `59a526c`, `9f68925`, `988557c`
  (thème clair), `6082406` (UX1), `884bb6b` (UX2), `816697f` (cap Sentinel 2), `4cc4063` (demande 51 et analyse 113).
  Règles en service : 2026.10.64.
* **Tests** : 589 réussis avec la base de test.
* **Ce qui manque** : la preuve de B05 (une demande de tip and cue analysée et évaluée en réel) et le critère de
  l'étape 7 (trois appariements Sentinel 2 dont SMYRTOS). Deux défauts qui empêchaient B05 sont corrigés et déployés.

## Par étape

| Étape | Points de la liste | Résultat |
|---|---|---|
| 1. Diagnostics | A03 | Baisse du taux d'injection (80 à 64 %) expliquée par l'ancien tirage des navires, pas par la zone ; mesures par tranche de messages |
| 2. Correctifs | A03, B02, C02 | Historique Sentinel 1 sur la part de la fenêtre dans l'image, garde sans historique ; `min_prior_messages` à 8 ; `min_pairs` proportionnel aux jours ; place gardée pour une demande planifiée sous le plafond ; motif clair des candidats Sentinel 2 |
| 3. Déploiement | | `02b660e`, VERDICT à OK |
| 4. Opérations sur les données | | Refusion de 106, `viirs-reevaluer` (élevées VIIRS ouvertes de 13 à 3), zone fiable reconstruite (1 649 cellules), verdicts VIIRS ; simulations conformes avant chaque écriture, décisions conservées |
| 5. Historique en production | B01 | Tenu : analyse 108 avec 2 passages antérieurs, seuil 0,9, 11 détections comme le rejeu ; défaut de l'écho voisin (VICTORINE, fausse alerte 92604) corrigé en 2026.10.62 |
| 6. Boucle tip and cue | B05 | **En cours**, prolongée au 05/11 12:00 UTC. 13 demandes, aucune analysée. Deux défauts trouvés et corrigés (voir plus bas) |
| 7. Sentinel 2 réel | C01 | Chaîne réelle prouvée de bout en bout, HERMES CENTURY appariée (231 m mesurés, 229 m déclarés) ; **critère non atteint** (un appariement sur trois exigés, SMYRTOS vu mais AIS trop clairsemé) |
| 8. Chantiers sans dépense | D02, D03, E01, E02, E06 | Remasquage VIIRS de 18:30 ; estime à rebours mesurée ; silences au bord en mer de la zone fiable déclassés ; proposition chiffrée pour `high_duration_min` (valeur inchangée) ; documentation |

Hors mission, à la demande d'Antoine : thème clair, lots UX1 (identité, unités marines, glossaire, pages d'erreur,
lien des notifications) et UX2 (fiche de signalement imprimable).

## Étape 6 : pourquoi aucune demande n'a été analysée

* **Demande 51** (EEMS DOVER au mouillage en baie de Seine, tip rendez vous) : planifiée sur le passage 655 du 29/10
  à 17:54, elle l'a quitté trois minutes après la prise de vue, parce que le calendrier ne marque un passage acquis
  que la nuit. L'analyse 110 de ce passage, lancée à 02:37, ne la portait pas, et sa seule fenêtre était à 80 km du
  navire : aucune évaluation possible après coup. Corrigé : la demande garde son passage tant que la scène peut être
  publiée (12 h).
* **Analyse 113** (passage 1204 du 30/10) : refusée par le plafond de 4 travailleurs par jour, entamé par les deux
  essais Sentinel 2 ; l'analyse était écrite avant la vérification. Corrigé : plafond vérifié avant, demandes refusées
  avec leur motif plutôt que laissées sur une analyse morte.
* Les 12 autres demandes : 11 « sans passage » (position prédite au quai ou trop près de la côte, invisible au radar,
  ou navire hors de portée) et une expirée sans passage utile en 36 h, chacune avec son motif.

Garde fou en service (`/tmp/z7/garde_tip2.sh`, journal `/tmp/z7/garde_tip.txt`) : il ne fait que remettre en pause
tip and cue et Sentinel 1, à la première demande analysée, à l'échéance du 05/11 12:00 UTC, à 2,81 € de dépense, sur
un travailleur en vie depuis plus d'une heure, une analyse sans historique ou une alerte élevée issue d'une analyse.

## Verdicts sur les alertes élevées et critiques ouvertes (30/10/2026)

**Navire sombre, Sentinel 1** (critiques) :
* 76467 : bateau feu de Sandettie (MMSI 992351029, aide à la navigation) ; la collecte ne demande pas ces messages.
  Écho réel, pas un navire sombre : à classer.
* 92603 : petit navire réel sans AIS reçu (écho de 30 m à 0,969, aucun AIS à 3,4 km). Vrai écho sans AIS ; à garder
  ou classer en « petite unité ».

**Navire sombre, VIIRS** (élevées) : 15198, 45569 et 80242, aucune explication par un navire AIS. 80242 est dans le
parc en projet de Fécamp Grand Large (bouée de mesure possible) ; 15198 et 45569 à surveiller.

**Changement d'identité** (6 élevées) :
* 3238 MSC WORLD ASIA (Malte puis France) et 18303 LARGO CALIFORNIA (Marshall puis Malte) : changements de pavillon
  ordinaires.
* 47064 et 63928 : OMI 1234567, numéro de remplissage partagé par plusieurs navires sans lien (VIVA BEYOND, « US GOVT
  VESSEL »…) ; fausses alertes. Proposition : écarter les OMI de remplissage connus.
* 72145 COCOON devenu PHINN (Malte puis îles Caïmans) : renommage et changement de pavillon d'un yacht, probablement
  une vente.
* 141869 COTENTIN (France) devenu NEFERTITI (Jordanie) : roulier vendu par son armateur français, à confirmer par une
  source publique.

**Rendez vous** (1 élevée) : 125730, deux navires sans nom aux MMSI consécutifs (227632082 et 227632085) bord à bord
près du golfe de Fos ; cinq MMSI de la même série sont reçus : flottille d'un même armement, pas un transbordement.

**WATCHLIST** : 114 alertes ouvertes (94 critiques sur 64 navires, 20 élevées sur 17 navires), une par passage dans
nos eaux ; en tête VICTOR ANDRYUKHIN (8), VLADIMIR LATYSHEV (6), SMYRTOS (5). Vraies par construction ; aucune n'a
été classée sans Antoine.

Aucune de ces alertes n'a été modifiée par la mission, hormis les opérations simulées de l'étape 4 et la refusion de
108 (92604 classée, motif VICTORINE).

## Décisions laissées à Antoine

1. Décider des alertes ci dessus (classer 76467 et les deux OMI 1234567 ; garder ou non 92603 et les VIIRS).
2. Écarter les OMI de remplissage (1234567) des changements d'identité : changement de règle, donc de doctrine.
3. Étape 7 : un troisième essai Sentinel 2 payant (0,19 €) sur un passage avec AIS dense, ou la clore sur le vrai
   positif HERMES CENTURY.
4. Place des essais manuels dans le plafond de 4 travailleurs par jour : ils ont privé la boucle automatique du
   30/10 (analyse 113).
5. Fin de l'étape 6 : au plus tard le 05/11 à 12:00 UTC, le garde fou remet en pause ; reprise seulement sur décision.
6. Gestes manuels : aperçu social du dépôt GitHub (`docs/images/apercu_social.png`) ; facultatif,
   `MARS_URL_INTERFACE` dans le `.env` du serveur.
