# Règles d'alerte de MARS C2

Référence de chaque type d'alerte : condition, mode, gravité, seuils et origine de chaque seuil, alertes réelles
connues. Règles en vigueur : **version 2026.10.47** (en tête de `config/rules.yaml`, commit `091d5c4` du 22/10/2026).
Toute alerte enregistre cette version (`alerts.rule_version`) et l'empreinte de l'état du panneau
(`alerts.controle_empreinte`). Le plan de recalibration est dans `docs/calibration.md`.

Sources : `config/rules.yaml`, `mars/rules.py`, `mars/viirs.py`, `mars/sentinel.py`, `mars/sentinel2.py`,
`mars/evaluation.py`, `mars/controle.py`, `mars/fusion/pipeline.py`, `db/init/25_sentinel.sql` (contrainte
`alerts_type_check`), `web/src/registres/alertes.tsx`, `docs/bilan.md` (lecture du serveur du 22/10/2026),
`docs/audit_code.md` (section 3), `docs/historique_danemark.md`, `docs/travailleurs_viirs.md`, `docs/sentinel.md`,
`docs/sentinel2.md`, `docs/trajectoire.md`, `docs/tip_and_cue.md`, `docs/mesures_calibration.md` et le journal Git.

## Lire ce document

**Origine d'un seuil.**

* **danoise** : calibrée sur deux journées AIS de la Danish Maritime Authority (05 et 17/06/2024, Skagerrak et
  Kattegat), avant le passage à la France (`docs/historique_danemark.md` ; raisons de recalibrer :
  `docs/audit_code.md`, section 3). La date d'apparition dans `config/rules.yaml` (journal Git, avant le 06/10/2026)
  le confirme quand l'audit ne la cite pas.
* **provisoire** : réglée en France sans mesure complète (valeur de départ, raisonnement, ou mesure partielle à
  refaire), par exemple la zone de réception fiable du 11/10/2026.
* **française mesurée** : recalée sur une mesure française (serveur ou copie réelle de ses données).
* Deux mentions de plus, pour ce qui n'est pas un seuil de calibration : **capteur** (valeur imposée par la physique
  du capteur ou par l'auteur du modèle) et **fonctionnement** (réglage d'exécution ou d'affichage).

**Mode d'une règle** (lot P, phase C, règles 2026.10.37, migration 34). Trois modes : **actif** (alertes),
**observation** (épisodes comptés dans `task_runs` et `episodes_observes`, aucune alerte), **coupé** (rien n'est
calculé). Le catalogue des règles et de leurs sous types est dans `config/rules.yaml`, section `controle.regles`
(coupure AIS, rendez vous, identité avec ses sous types « nom » et « omi », navire des listes, VIIRS, Sentinel 1 et
Sentinel 2 avec leurs sous types `dark_ship`, `ais_unconfirmed`, `identity_mismatch`).

* Dans le fichier, seule la coupure AIS a un mode : `ais_gap.mode: observation`. Toutes les autres règles sont
  actives par défaut (`mars/controle.py`, `mode_fichier`).
* **L'état du panneau en base prime sur le fichier** (`mode_effectif`), `ais_gap.mode` compris. Un changement de mode
  ne supprime aucune alerte.
* **La base du serveur n'est pas lisible depuis ce dépôt.** Dernier état connu : au 22/10/2026, aucun mode posé par
  le panneau, coupures en observation par le fichier (`docs/bilan.md`, section 1). Pour l'état réel : Coulisses,
  rubrique « Règles d'alerte », ou `GET /api/controle/regles` par le tunnel.
* Les règles satellites dépendent aussi des **interrupteurs des capteurs** (en base, lot P, phase A) : VIIRS,
  Sentinel 1, Sentinel 2 et tip and cue sont **en pause depuis le 21/10/2026** (`docs/bilan.md`, section 1). Une règle
  active dont le capteur est en pause ne produit rien.

**Gravités** : faible, moyenne, élevée, critique (`mars/rules.py`, `SEVERITIES`). Trois ajustements communs à tous
les types :

* **Zone de vigilance** (lot P, phase E, règles 2026.10.39) : une alerte dans une zone voit sa gravité relevée au
  minimum du profil (profil « Renforcé » : moyenne ; « Attention » : aucun plancher), zone et gravité d'avant notées
  dans l'alerte (`controle.marquer_zone`). Le profil « Renforcé » abaisse aussi, dans la zone seulement,
  `rendezvous.min_duration_min` à 60 et `ais_gap.min_gap_min` à 60 (`zones_vigilance.profils`). Aucune zone réelle
  vérifiée au 22/10/2026 (`docs/bilan.md`, section 1 : nombre de zones inconnu par l'API).
* **Vérification satellite** (tip and cue, `tip_and_cue.ajustements`) : selon le résultat, la gravité de l'alerte
  d'origine est relevée ou abaissée, jamais retirée ; elle est ensuite gardée quand la règle remet l'alerte à jour
  (`mars/evaluation.py`, `GARDE_VERIFICATION`). Aucune observation aboutie sur le serveur au 22/10/2026
  (`docs/bilan.md`, section 1 : 20 demandes, 0 résultat).
* **Décisions des opérateurs** : une règle en continu met ses alertes à jour en place par clé stable
  (`alerts.rule_key`) et ne les efface jamais (`save_alerts`) ; statut et décisions sont conservés.

**Alertes réelles** : comptes lus sur le serveur le 22/10/2026, sur 30 jours (`docs/bilan.md`, section 1).

---

## 1. WATCHLIST : navire des listes dans nos eaux

**Condition.** Un navire de la liste de surveillance (vue `vessel_watch`, niveaux fort, sanctionné, flotte fantôme,
suspect GUR) a au moins une position dans nos zones sur la fenêtre des règles. Une alerte par **passage** : un
silence de plus de `passage_gap_h` ouvre un nouveau passage ; un passage prolongé met à jour son alerte
(`mars/rules.py`, `run_watchlist`). Le niveau « autre risque » n'est pas signalé (trop bruyant, commentaire de
`watchlist.niveaux`).

**Séjour prolongé.** Un passage d'au moins `sejour_prolonge_h` porte un séjour mesuré depuis le vrai début du passage
(la fenêtre de 24 h le tronquerait) : durée, part du temps à l'arrêt pondérée par la durée, lieu (mouillage connu,
distance à la côte) et, depuis la version 2026.10.43, **part à l'arrêt hors port** (intervalles à l'arrêt à plus de
`hors_port_m` de la côte, `stay_offshore`). Le séjour ne change pas la gravité de l'alerte ; il rend le dossier « à
examiner » si la part hors port atteint `dossiers.sejour_hors_port_part` (affichage, lot U).

**Mode.** Actif (fichier). Sous types : aucun.

**Gravité** (`watch_severity`) : fort, critique ; sanctionné et flotte fantôme, élevée ; suspect GUR, moyenne. **Un
cran de moins** si le navire n'est reconnu que par son MMSI (pas par l'OMI).

| Seuil | Valeur | Origine | Note |
|---|---|---|---|
| `watchlist.niveaux` | fort, sanctionne, flotte_fantome, suspect_gur | fonctionnement | Choix des niveaux signalés |
| `watchlist.passage_gap_h` | 12 h | provisoire | Réglé le 06/10/2026 avec les règles en continu ; « Garder » sans mesure (`docs/mesures_calibration.md`, section 4) |
| `watchlist.sejour_prolonge_h` | 24 h | provisoire | Introduit le 09/10/2026 (règles 2026.10.21), sans mesure de la distribution des séjours |
| `watchlist.arret_kn` | 1,0 nœud | provisoire | Introduit le 09/10/2026 |
| `watchlist.hors_port_m` | 2 000 m | française mesurée | Règles 2026.10.43 (21/10/2026) : sur le serveur, 18 séjours en 30 jours, 15 à 0 (transits, escales à quai), NOSTOS 0,09, SAFEEN BARONESS 0,33, SMYRTOS 1 (commit `86a366d`) |
| `dossiers.sejour_hors_port_part` | 0,2 | française mesurée | Même mesure : 16 des 18 séjours redescendent à « à noter » (commit `86a366d`) ; affichage seulement |
| `continu.fenetre_h` | 24 h | fonctionnement | Fenêtre glissante des règles |

**Alertes réelles** : **94** en 30 jours, dernière le 22/10, dont 92 « nouvelles » et 2 confirmées.

## 2. IDENTITY_CHANGE : changement d'identité

**Condition** (`run_identity`, identités apparues depuis `continu.identite_fenetre_h`, 72 h). MMSI génériques (code
pays suivi de six zéros, comme 227000000) et `identite.mmsi_ignores` écartés. Deux sous types :

* **nom** (sous type `nom` du panneau) : le navire émet sous un nouveau nom, confirmé `confirmation_min` sans retour à
  un nom antérieur. Une alternance (MUTIN et FS MUTIN) n'est jamais signalée.
* **omi** (sous type `omi`, détail `changement: omi_autre_mmsi`) : le même OMI apparaît sous un autre MMSI, confirmé
  `confirmation_min`, avec ou sans changement de pavillon, avec ou sans usage simultané.

**Bâtiment militaire** (étiquette « militaire », gravité minimale sauf navire des listes) : type AIS 35, MMSI de
`identite.militaire.mmsi`, premier mot du nom parmi `prefixes_nom`, ou nom réduit à un numéro de coque.

**Mode.** Actif (fichier), sous types « nom » et « omi » réglables séparément au panneau.

**Gravité.**
* Nom : élevée pour un navire des listes ; faible pour un bâtiment militaire ou un navire de classe B ; moyenne sinon.
* OMI (`imo_severity`) : élevée si un navire des listes est en cause ; faible pour un bâtiment militaire ; même pays des
  deux côtés : faible, moyenne en cas d'usage simultané (cas de L'HORIZON 1, OMI 8542248) ; changement de pavillon :
  élevée.

| Seuil | Valeur | Origine | Note |
|---|---|---|---|
| `identite.confirmation_min` | 360 min | provisoire | Réglé le 06/10/2026 avec les règles en continu ; « Garder » sans cas mesuré (`docs/mesures_calibration.md`, section 4, base de test) |
| `identite.mmsi_ignores` | aucun | fonctionnement | |
| `identite.militaire.mmsi` | 232002833 (HMS CATTISTOCK) | française mesurée | Cas réel : il émettait « 101 » (règles 2026.10.18, commit `961c3f5`) |
| `identite.militaire.prefixes_nom` | 21 préfixes (HMS, FS, USS…) | provisoire | Liste raisonnée, préfixes ambigus (LE, LÉ, HS, SAS) exclus ; pas de mesure de faux positifs |
| `continu.identite_fenetre_h` | 72 h | fonctionnement | |

**Alertes réelles** : **38** en 30 jours, dernière le 21/10, dont 15 sur 7 jours.

## 3. RENDEZVOUS : rendez vous en mer

**Condition** (`run_rendezvous`). Deux navires quasi immobiles (moins de `max_speed_kn`), à moins de `max_distance_m`
l'un de l'autre, pendant au moins `min_duration_min`, loin des côtes (milieu de l'épisode à plus de `min_coast_km`).
Positions regroupées par tranches de `slot_min`, tranches séparées de `max_gap_min` au plus dans un même épisode.
Positions à moins de `coast_prefilter_m` de la terre écartées avant l'appariement (optimisation). Navires de service
exclus (`excluded_ship_types`) ; aucun statut AIS n'exclut (un « amarré » au large est un indice, leçon danoise de
SILVER KENNA). Le cycle exige le trait de côte (table `land`).

**Mode.** Actif (fichier).

**Gravité.** Faible dans une zone de mouillage connue (à `stationary_zone_buffer_m` près) ; élevée si la rencontre
dure au moins `high_duration_min`, ou se fait bord à bord (`alongside_m`), ou si un navire se déclare amarré en
pleine mer, ou à plus de `high_coast_km` des côtes ; moyenne sinon.

| Seuil | Valeur | Origine | Note |
|---|---|---|---|
| `rendezvous.max_distance_m` | 500 m | danoise | `docs/bilan.md`, section 1 : « seuils danois jamais recalés » |
| `rendezvous.max_speed_kn` | 2,0 nœuds | danoise | Idem |
| `rendezvous.min_duration_min` | 120 min | danoise | Idem ; 60 min dans une zone de vigilance « Renforcé » |
| `rendezvous.alongside_m` | 50 m | danoise | |
| `rendezvous.excluded_nav_status` | aucun | danoise | Exclure « amarré » effaçait les transbordements de SILVER KENNA (`docs/historique_danemark.md`, section 3) |
| `rendezvous.slot_min`, `max_gap_min` | 10 et 20 min | danoise | AIS dense ; risque de fragmentation avec un point toutes les dix minutes à l'arrêt (`docs/audit_code.md`, section 3) |
| `rendezvous.min_coast_km` | 3 km | danoise | Mouillage de Skagen, 4 à 8 km au large ; abaissé de 5 à 3 km au Danemark |
| `rendezvous.coast_prefilter_m` | 1 000 m | française mesurée | Optimisation, pas un seuil (règles 2026.10.18, 07/10/2026) : 419 s ramenées à 10,5 s sur la base de test, mêmes 4 épisodes retenus ; sur le serveur, 4 483 épisodes sur 4 485 étaient côtiers (docs/journal.md) |
| `rendezvous.stationary_zone_buffer_m` | 1 000 m | danoise | |
| `rendezvous.high_duration_min`, `high_coast_km` | 240 min, 20 km | danoise | Kattegat ; à réexaminer cas par cas (`docs/audit_code.md`, section 3) |
| `rendezvous.excluded_ship_types` | 12 types de service | danoise | Libellés DMA, compatibles avec l'ingestion française |
| `stationary_zones.*` (cellules 0,04 × 0,02°, 0,5 nœud, 4 navires, 30 positions lentes) | | danoise | Cellules calculées à 57° N ; 30 positions lentes demandent environ cinq heures à un point toutes les dix minutes (`docs/audit_code.md`, section 3) |

**Alertes réelles** : **7** en 30 jours, dernière le 20/10, dont 3 sur 7 jours. Coût : rendez vous 16,1 s sur un cycle
de 18,8 s (`docs/bilan.md`, section 1).

## 4. AIS_GAP : coupure AIS

**Condition** (`run_ais_gap`, `find_gaps`). Un navire de classe A qui faisait route (au moins `min_speed_kn`) et
émettait régulièrement (au moins `min_prior_messages` dans les `prior_window_min` précédentes) se tait au moins
`min_gap_min`, au large (plus de `min_coast_km`), loin du bord des données (`edge_margin_deg`), dans la zone de
réception fiable (`reception_cells`, sans elle la règle ne tourne pas). Sont écartés : les minutes de flux coupé
(moins de `continu.flux_min_ratio` de la médiane par minute) retirées du silence ; un silence qui commence dans un trou
de la collecte ou `debut_incertain_min` avant (début inconnu) ; une projection à l'estime sur `projection_min` (ou
`projection_open_min` sans réapparition) qui sort de la zone fiable ; une route déclarée suivie `route_horizon_min`
(pas de `route_pas_min`) qui la quitte (sortie de couverture). Les navires de service sont exclus.

**Mode.** **Observation** depuis le 11/10/2026 (`ais_gap.mode`) : épisodes comptés et décrits dans `task_runs` (tâche
`regles`, 30 plus longs) et `episodes_observes`, aucune alerte créée, mise à jour ni retirée, tant que les seuils
restent danois. Le panneau peut changer ce mode et prime sur le fichier. Même en observation, une coupure élevée près
d'une infrastructure peut lancer une vérification satellite (tip `coupure_ais`, motif « coupure observée », aucune
alerte créée).

**Gravité** (gravité hypothétique en observation). Élevée si un partenaire est resté lent hors mouillage près de la
dernière position ou de la réapparition, si un rendez vous est lié, ou si le navire s'est arrêté pendant le silence
(vitesse implicite sous `stop_ratio` de la vitesse déclarée, navire à `stop_min_speed_kn` au moins) ; faible si la
route a été poursuivie (au delà de `continue_ratio`) ; élevée sans réapparition au delà de `high_duration_min` ;
moyenne sinon. **Déclassée en faible** dans un point de disparition collectif (au moins `disparition_min_navires`
navires sans lien qui se taisent le même jour dans la même maille), sauf indice propre au navire ; gravité d'avant
gardée (`gravite_avant_declassement`). **Déclassée en faible aussi** sans réapparition, à moins de `bord_zone_km` du
bord en mer de la zone de réception fiable (bord dont le point le plus proche est à plus de `bord_zone_terre_km` de la
terre), sauf indice propre au navire : sortie de notre réception probable (`bord_zone_km` dans le détail, lot E01).

| Seuil | Valeur | Origine | Note |
|---|---|---|---|
| `ais_gap.min_gap_min` | 120 min | danoise | 60 min dans une zone de vigilance « Renforcé » |
| `ais_gap.min_speed_kn` | 1,0 nœud | danoise | |
| `ais_gap.min_prior_messages`, `prior_window_min` | 10 en 60 min | danoise | MMSI fantôme danois (code 506) ; 316 navires en route sur 988 (32 %) ne l'atteignent pas sur le serveur (docs/journal.md, 11/10/2026) : premier seuil à recalibrer |
| `ais_gap.min_coast_km` | 3 km | danoise | |
| `ais_gap.edge_margin_deg` | 0,25° | danoise | Emprise rectangulaire de l'import danois (`docs/audit_code.md`, section 3) |
| `ais_gap.ais_classes` | A | danoise | |
| `ais_gap.partner_radius_m`, `partner_min_slow_min`, `partner_window_max_min`, `partner_max_speed_kn`, `partner_min_positions` | 500 m, 30 min, 180 min, 2 nœuds, 5 positions | danoise | Zones de pêche danoises ; `partner_min_positions` à revoir avec l'allègement |
| `ais_gap.projection_min` | 120 min | danoise | PEARL SEAWAYS, parti vers Oslo |
| `ais_gap.projection_open_min` | 120 min | danoise | Présent depuis le 02/10/2026 (journal Git) ; raison non documentée |
| `ais_gap.stop_ratio`, `continue_ratio` | 0,3 et 0,5 | danoise | |
| `ais_gap.stop_min_speed_kn` | 5 nœuds | danoise | Ajouté le 03/10/2026 (journal Git) ; mesure non documentée |
| `ais_gap.high_duration_min` | 360 min | danoise | |
| `ais_gap.excluded_ship_types` | 12 types de service | danoise | La pêche n'est pas exclue |
| `ais_gap.route_horizon_min`, `route_pas_min` | 60 et 10 min | française mesurée | Règles 2026.10.24 (12/10/2026), voie ouest en Manche ; sur une copie réelle du 11/10, 22 sorties de couverture de plus, 63 coupures retenues au lieu de 85 (docs/journal.md) |
| `ais_gap.bord_zone_km`, `bord_zone_terre_km` | 10 km, 6 km | française mesurée | Épisodes du 27 au 29/10/2026 (résumés des cycles, 30 plus longs par cycle) : 15 silences élevés sans réapparition sur 36 déclassés, 7 réapparus sur 31 aussi près d'un bord en mer ; injection inchangée (les coupures injectées réapparaissent) (règles 2026.10.63) |
| `ais_gap.disparition_min_navires`, `disparition_cellule_deg` | 3 navires, 0,2 × 0,1° | française mesurée | Même copie : 13 points collectifs, 47 coupures déclassées (règles 2026.10.24 et 2026.10.25, 12 et 13/10/2026) ; mesure d'une seule journée |
| `ais_gap.debut_incertain_min` | 10 min | française mesurée | Règles 2026.10.25 (13/10/2026) : arrêts simulés de 20 et 140 min sur la copie du 11/10 sans effet sur les élevées |
| `continu.flux_min_ratio` | 0,2 | provisoire | 06/10/2026 : 41 fausses coupures évitées sur la base de test lors d'une coupure du flux de 140 min ; pas de mesure du seuil lui même |
| `reception.max_interval_s`, `min_coverage`, `min_pairs` | 600 s, 90 %, 50 paires | provisoire | 11/10/2026, règles 2026.10.22 : les valeurs danoises (180 s, 95 %, 200 paires) laissaient la zone vide du 06 au 09/10 ; 1 067 cellules, environ 44 000 km² ; à recalibrer sur dix jours |
| `reception.min_vessels` | 3 navires | danoise | Présent depuis le 02/10/2026 ; gardé au réglage provisoire |
| `reception.cell_deg_lon`, `cell_deg_lat` | 0,10 × 0,05° | danoise | Calculées à 57° N |

**Test par injection** : 181 coupures de 3 h retrouvées sur 253 injectées (72 %) sur 135 769 positions réelles de 500
navires, zone de test de 351 cellules (11/10/2026, docs/journal.md) ; 5 sur 5 au Danemark.

**Alertes réelles** : **aucune** (observation). 84 épisodes observés en 7 jours au 22/10/2026.

## 5. DARK_SHIP de source VIIRS : lumière nocturne sans AIS

**Condition** (`mars/viirs.py`). Une détection nocturne VIIRS (modèle d'allenai) sans navire AIS apparié, après les
filtres : appariement à `tolerance_m` augmentée de `estime_marge_ratio` du chemin parcouru pendant l'estime
(`estime_max_min` au plus) ; côte à moins de `cote_km` écartée ; lumière fixe (revue à `persistance_m` près sur
`persistance_nuits` nuits distinctes en `persistance_jours` jours) écartée, alertes vierges antérieures classées.
**Non évaluable** (aucune alerte, `mars/evaluation.py`) : coupure du flux AIS à l'heure du passage (à
`coupure_marge_min` près) ; hors de la zone de réception fiable dès qu'elle est construite ; tant qu'elle ne l'est
pas, moins de `couverture_min_navires` navire AIS reçu à moins de `couverture_rayon_km` dans les
`couverture_fenetre_min` autour du passage. L'alerte note le critère employé (`critere_evaluation`).

**Mode.** Actif (fichier), règle « viirs » du panneau, sans sous type. **Capteur en pause depuis le 21/10/2026**
(`docs/bilan.md`) ; reprise prévue au déploiement de `29bcfff` (CLAUDE.md, section 4).

**Gravité** (`viirs.severity`) : critique à moins de `corridor_m` d'une infrastructure **et** à moins de
`liste_rayon_km` d'un navire des listes ; élevée pour l'un des deux ; moyenne au delà de `tres_au_large_km` des
côtes ; **faible « à confirmer »** sinon. Vérification satellite : élevée si un écho sans AIS est trouvé, faible si
l'écho est apparié à un navire AIS.

| Seuil | Valeur | Origine | Note |
|---|---|---|---|
| `viirs.tolerance_m` | 1 500 m | française mesurée | Deux pixels ; distance bimodale sur 152 détections des 06 au 09/10/2026 : appariées 39 % à 1 000 m, 40 % à 1 500 m, 41 % à 5 000 m (`docs/travailleurs_viirs.md`) |
| `viirs.estime_max_min` | 20 min | française mesurée | Même mesure : fenêtre d'estime de 5 à 30 min, 32 à 42 % appariées |
| `viirs.estime_marge_ratio` | 0,5 | provisoire | |
| `viirs.cote_km` | 5 km | provisoire | « Valeurs de départ, à calibrer sur les premières nuits » (`config/rules.yaml`) |
| `viirs.persistance_m`, `persistance_nuits`, `persistance_jours` | 1 000 m, 3 nuits, 30 jours | provisoire | Idem |
| `viirs.corridor_m` | 3 704 m (2 milles) | provisoire | Choix opérationnel |
| `viirs.liste_rayon_km` | 10 km | provisoire | |
| `viirs.tres_au_large_km` | 22 km (12 milles) | provisoire | Limite des eaux territoriales (règles 2026.10.20) |
| `viirs.couverture_rayon_km`, `couverture_fenetre_min`, `couverture_min_navires` | 30 km, 30 min, 1 navire | française mesurée | 68 des 87 détections sans AIS n'avaient aucun navire AIS à moins de 20 km (09/10/2026, `docs/travailleurs_viirs.md`) ; critère de repli tant que la zone fiable n'existe pas |
| `viirs.coupure_marge_min` | 5 min | provisoire | |

**Alertes réelles** : **29** DARK_SHIP en 30 jours, **toutes de source VIIRS**, dernière le 17/10. Sur les trois
premières nuits, 87 alertes réévaluées : 61 non évaluables retirées, 10 élevées, 6 moyennes, 10 faibles
(`docs/travailleurs_viirs.md`).

## 6. DARK_SHIP de source radar Sentinel 1 : écho sans AIS

**Condition** (`mars/sentinel.py`, `_screen_and_alert`). Un écho du modèle d'AI2 (confiance `sentinel.confiance`)
dans une fenêtre lue, sans navire AIS compatible (tolérance orientée : `fusion.base_radius_m` plus un décalage Doppler
de `fusion.doppler_s` fois la vitesse vers le radar, direction de vol calculée par scène depuis l'inclinaison de
l'orbite), après les filtres : terre à moins de `masks.land_buffer_m`, écho fixe (`persistence`), éolienne probable
(moins de `eolien_m` d'un parc éolien), détection au bord d'une fenêtre (`fenetre_marge_m`). Même évaluation que VIIRS
(coupure du flux, zone de réception fiable ou rayon de 30 km).

**Mode.** Actif (fichier), sous type `sentinel1.dark_ship`. **Capteur en pause depuis le 21/10/2026**. Plafond du
fichier : `sentinel.scenes_max_par_jour` 2, budget mensuel 8 € partagé avec Sentinel 2.

**Gravité** : la même fonction que VIIRS (`viirs.severity`, seuils de la section `viirs`) : critique, élevée, moyenne
au delà de 22 km, faible « à confirmer ».

| Seuil | Valeur | Origine | Note |
|---|---|---|---|
| `sentinel.confiance`, `nms_m` | 0,9 et 10 m | capteur | Paramètres de la chaîne d'AI2 |
| `fusion.base_radius_m` | 300 m | danoise | |
| `fusion.doppler_s` | 150 s | danoise | Décalages de 410 à 494 m mesurés au Danemark ; physique du capteur, à vérifier en France |
| `fusion.max_gap_min`, `ais_window_min` | 10 et 15 min | danoise | AIS dense |
| `fusion.flight_heading_deg` | 348 et 192° | capteur | Calcul orbital vers 47° N (`docs/sentinel.md`) ; la chaîne du serveur le recalcule par scène (`track_heading`) |
| `masks.land_buffer_m` | 500 m | danoise | Générique, lié au capteur |
| `persistence.radius_m`, `min_days_apart` | 60 m, 1 jour | danoise | 107 des 111 éoliennes d'Anholt reconnues |
| `sentinel.eolien_m` | 150 m | provisoire | 11/10/2026, sans mesure |
| `sentinel.fenetre_marge_m` | 2 000 m | fonctionnement | Bord des fenêtres de 24 km |
| `sentinel.point_cote_min_km`, `point_mouillage_m`, `point_mouillage_cote_min_m` | 2 km, 1 000 m, 800 m | française mesurée | Sélection seulement (lot C5, 19/10/2026) : VICTOR ANDRYUKHIN à quai à Marseille, bassins à 92 m de la côte et port de Brest à 281 m écartés, rade de Brest à 1,8 km gardée (`docs/sentinel.md`) |
| `sentinel.contraste_obligatoire` | non | capteur | Le modèle d'AI2 a déjà filtré |

**Alertes réelles** : **aucune**. Une seule analyse réelle aboutie (analyse 97 du 19/10/2026 : MSC MERAVIGLIA appariée,
un écho écarté à terre).

## 7. DARK_SHIP de source optique Sentinel 2

**Condition** (`mars/sentinel2.py`, même fonction `_screen_and_alert` avec le capteur optique). Navire vu par le
modèle rslearn d'AI2 (confiance `sentinel2.confiance`) dans une fenêtre dégagée, sans navire AIS à
`sentinel2.fusion_rayon_m` (pas de tolérance Doppler, heure propre de la tuile). Navire sous un nuage (part masquée
au delà de `masque_part_max_pct` dans `masque_voisinage_m`) écarté « nuages ». Même évaluation que VIIRS.

**Mode.** Actif (fichier), sous type `sentinel2.dark_ship`, mais **livré coupé** : `sentinel2.scenes_max_par_jour` vaut
0, et le capteur est en pause depuis le 21/10/2026.

**Gravité** : comme VIIRS et Sentinel 1 ; toute alerte d'une analyse sans masque des nuages (« masque absent ») est
« à confirmer ».

| Seuil | Valeur | Origine | Note |
|---|---|---|---|
| `sentinel2.confiance` | 0,8 | capteur | Seuil d'AI2 (précision 77,2 %, rappel 78,6 % sur sa validation) |
| `sentinel2.fusion_rayon_m` | 300 m | française mesurée | Écarts de 8 à 130 m mesurés le 17/10/2026 sur cinq navires (petit échantillon) |
| `sentinel2.nuages_annonce_max_pct` | 50 % | française mesurée | Du 20/09 au 20/10/2026 : au delà, 3 % des fenêtres dégagées |
| `sentinel2.nuages_fenetre_max_pct` | 20 % | française mesurée | Même mesure |
| `sentinel2.masque_part_max_pct`, `masque_voisinage_m` | 25 %, 500 m | française mesurée | Essai du 20/10/2026 : 1,1 à 7,5 % dans 400 m autour de quatre navires dégagés |
| `sentinel2.masque_classes` | 3, 8, 9, 10 | capteur | Classes SCL |
| `sentinel2.l2a_attente_h` | 3 h | fonctionnement | Attente du masque, puis analyse « masque absent » ; publication médiane du L2A mesurée à 4,8 h (`config/rules.yaml`) |

**Alertes réelles** : **aucune** (trois essais réels en échec le 20/10/2026, correctif jamais rejoué).

## 8. AIS_UNCONFIRMED : position AIS non confirmée

**Condition** (`mars/fusion/pipeline.py`, `unconfirmed`, puis `_screen_and_alert`). Un navire AIS d'au moins
`unconfirmed.min_length_m` sans aucun écho compatible, même écarté par un filtre (hors terre), tolérance élargie de
`tolerance_factor` ; seulement dans le cœur d'une fenêtre lue (correctif du 21/10/2026), au large (plus de deux fois
`masks.land_buffer_m` de la terre), hors coupure du flux, dans la zone fiable quand elle existe. Sentinel 2 : seulement
dans une fenêtre dégagée avec masque ; jamais d'alerte sans masque des nuages (un navire sous un nuage n'est pas
absent).

**Mode.** Actif (fichier), sous types `sentinel1.ais_unconfirmed` et `sentinel2.ais_unconfirmed` ; capteurs en pause ;
non notifiée par défaut (`controle.notifications.alertes.types`).

**Gravité** : moyenne à partir de `high_length_m`, faible sinon. Contexte toujours joint : « le détecteur manque une
partie des navires ».

| Seuil | Valeur | Origine | Note |
|---|---|---|---|
| `unconfirmed.min_length_m` | 50 m | danoise | Générique, lié au capteur (`docs/audit_code.md`, section 3) |
| `unconfirmed.tolerance_factor` | 1,5 | danoise | Idem |
| `unconfirmed.high_length_m` | 150 m | danoise | Idem |

**Alertes réelles** : **aucune** (0 parmi les 156 alertes du serveur au 21/10/2026, `docs/sentinel.md`).

## 9. IDENTITY_MISMATCH : taille ou cap déclarés contredits

**Condition** (`mars/sentinel.py`, `mismatches`). Écho apparié à un navire AIS dont la longueur mesurée s'écarte de
la longueur déclarée de plus de `ecart_longueur_m` **et** de plus de `ecart_longueur_relatif` (longueur déclarée d'au
moins `longueur_declaree_min_m`), ou dont l'orientation est en travers de la route déclarée (écart entre
`cap_ecart_min_deg` et `cap_ecart_max_deg`, classe de probabilité `cap_probabilite_min` au moins, navire à
`cap_vitesse_min_kn` au moins). Sentinel 2 : cap mesuré sur la forme et le sillage, compté comme certain.

**Mode.** Actif (fichier), sous types `sentinel1.identity_mismatch` et `sentinel2.identity_mismatch` ; capteurs en
pause. Ajoutée par la migration 25 à la contrainte `alerts_type_check`.

**Gravité** : moyenne pour un navire des listes, faible sinon.

| Seuil | Valeur | Origine | Note |
|---|---|---|---|
| `sentinel.ecart_longueur_m`, `ecart_longueur_relatif` | 40 m et 40 % | provisoire | Raisonnement sur le pixel de 10 m et la résolution de 20 m (11/10/2026) ; « à recalibrer sur les premiers passages réels » (`docs/sentinel.md`). Un seul cas réel : MSC MERAVIGLIA, 9 m (3 %) de plus |
| `sentinel.longueur_declaree_min_m` | 15 m | provisoire | |
| `sentinel.cap_ecart_min_deg`, `cap_ecart_max_deg` | 67,5 et 112,5° | provisoire | Classes de 22,5°, inversion avant arrière écartée |
| `sentinel.cap_probabilite_min`, `cap_vitesse_min_kn` | 0,5 et 3 nœuds | provisoire | |

**Alertes réelles** : **aucune**.

## 10. Autres types

* **DARK_SHIP de la chaîne radar du Mac** (CircleNet) : retirée au lot Z4 avec la chaîne ; ses alertes passées restent
  en base et lisibles. La fusion commune garde `dark_ship.critical_length_m` (50 m), `critical_contrast_db` (20 dB),
  `model.thresholds.vessel` (0,338) et `contrast.min_vv_db` (10 dB) ; `model.thresholds.objectness` et `fishing` sont
  retirés (règles 2026.10.48).
* **Types à venir** du registre de l'interface (`web/src/registres/alertes.tsx`, `actif: false`), absents de la
  contrainte `alerts_type_check` : `INFRA_THREAT` (menace sur une infrastructure), `TRAJECTOIRE_ANORMALE` (pilote),
  `PASSAGE_PREVU` (étape 4). Aucun code ne les produit.

La contrainte `alerts_type_check` (migration 25) admet sept types : DARK_SHIP, RENDEZVOUS, AIS_GAP, AIS_UNCONFIRMED,
WATCHLIST, IDENTITY_CHANGE, IDENTITY_MISMATCH.

## 11. Récapitulatif

| Type et source | Règle du panneau | Mode (fichier) | État du capteur au 22/10 | Gravité | Seuils dominants | Origine dominante | Alertes réelles (30 jours au 22/10) |
|---|---|---|---|---|---|---|---|
| WATCHLIST et séjour prolongé | `watchlist` | actif | AIS en service | fort critique, sanctionné et flotte fantôme élevée, suspect GUR moyenne ; un cran de moins par MMSI seul | 12 h, 24 h, 2 km hors port | provisoire, hors port française mesurée | 94 |
| IDENTITY_CHANGE, nom | `identite.nom` | actif | AIS en service | listes élevée, militaire ou classe B faible, sinon moyenne | 360 min | provisoire | 38 (nom et OMI) |
| IDENTITY_CHANGE, OMI | `identite.omi` | actif | AIS en service | listes élevée, militaire faible, même pays faible ou moyenne, pavillon changé élevée | 360 min | provisoire | (compté ci dessus) |
| RENDEZVOUS | `rendezvous` | actif | AIS en service | mouillage faible, long, bord à bord, amarré ou au large élevée, sinon moyenne | 500 m, 2 nœuds, 120 min, 3 km | danoise | 7 |
| AIS_GAP | `ais_gap` | **observation** | AIS en service | arrêt, partenaire ou rendez vous élevée, route poursuivie faible, point collectif faible | 120 min, 10 messages par heure, zone fiable | danoise, zone provisoire | 0 (84 épisodes observés en 7 jours) |
| DARK_SHIP VIIRS | `viirs` | actif | en pause | corridor et listes critique, l'un élevée, au large moyenne, sinon faible à confirmer | 1 500 m, 5 km, 2 milles, 10 km, 22 km | provisoire et française mesurée | 29 |
| DARK_SHIP Sentinel 1 | `sentinel1.dark_ship` | actif | en pause | comme VIIRS | 300 m, 150 s, 150 m | danoise et capteur | 0 |
| DARK_SHIP Sentinel 2 | `sentinel2.dark_ship` | actif, plafond 0 | en pause | comme VIIRS, à confirmer sans masque | 300 m, nuages 50 % et 20 % | française mesurée et capteur | 0 |
| AIS_UNCONFIRMED | `sentinel1` et `sentinel2.ais_unconfirmed` | actif | en pause | moyenne à 150 m, sinon faible | 50 m, facteur 1,5 | danoise | 0 |
| IDENTITY_MISMATCH | `sentinel1` et `sentinel2.identity_mismatch` | actif | en pause | listes moyenne, sinon faible | 40 m et 40 %, 67,5 à 112,5° | provisoire | 0 |
| DARK_SHIP chaîne du Mac | aucune | retiré (lot Z4) | hors service | longueur et contraste critique, sinon élevée | 50 m, 20 dB | danoise | 0 connue |

Le mode et l'état réels se lisent en base (panneau), pas dans ce document : à vérifier par `GET /api/controle/regles`
avant toute conclusion.
