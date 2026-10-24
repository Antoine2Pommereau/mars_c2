# Panneau de contrôle (lot P, 21/10/2026)

> **État au 24/10/2026**
> * Le panneau n'est plus une icône du rail : depuis le lot U (21/10/2026), ses sections sont des **rubriques des
>   Coulisses** (`docs/ux_refonte.md`, section 13) : Compte, Notifications, Affichage et démonstration (mon espace) ;
>   Règles d'alerte, Zones de vigilance (surveillance) ; Sources et capteurs, Activité satellite, Budget (capteurs) ;
>   Système, Journal (plateforme, lecture seule). Les fonctions décrites ci dessous sont inchangées.
> * Interrupteurs : **Sentinel 1, Sentinel 2 et tip and cue en pause depuis le 21/10/2026** ; VIIRS, mis en pause le
>   même jour (motif « too expensive », alors qu'une nuit coûte 0,02 €), repris au déploiement de 29bcfff. Cet état
>   n'existe qu'en base (`interrupteurs_satellites`) : `taches.py satellites-pause` sans argument le donne.
> * Accès : depuis les lots Z1 et Z2, **toutes** les routes d'écriture de l'API exigent une session d'opérateur, pas
>   seulement celles du panneau ; l'auteur est tiré de la session.
> * Budget Sentinel : 8 € par mois, partagé entre Sentinel 1, Sentinel 2 et tip and cue ; VIIRS hors budget.
> * Coût par résultat (lot Z6, rubrique Budget) : sur le mois ou depuis le début, dépense de chaque source (coût réel
>   des travailleurs détruits, échecs compris) divisée par ses résultats utiles (nuit VIIRS traitée sans erreur,
>   analyse Sentinel terminée, demande d'observation analysée). Tip and cue n'a pas de travailleur : son coût est celui
>   des analyses qui ont porté ses demandes, déjà compté dans Sentinel 1 et 2.

Piloter sans terminal les capteurs, les dépenses, les règles, les notifications, les zones de vigilance et un mode
démonstration, avec un état du système en lecture seule. Raison première : maîtriser les coûts (Sentinel 1 et 2 sont
payés à l'heure entamée), puis préparer des démonstrations.

À la livraison du lot P, le panneau s'ouvrait depuis l'icône « Contrôle » du rail (curseurs), en sections repliables.
Depuis le lot U (21/10/2026), chaque section est une rubrique des Coulisses : Capteurs et Plafond de scènes dans
Sources et capteurs, Dépenses dans Budget, Demande d'observation dans Activité satellite, Système, Règles (Règles
d'alerte), Notifications, Zones de vigilance, Démonstration (Affichage et démonstration) et Journal, chacune à son nom.

## Principes

* **L'état du panneau est en base et prime sur `config/rules.yaml`.** On ne modifie plus le fichier sur le serveur
  pour suspendre quoi que ce soit (une modification locale bloque `git pull`). Le conteneur taches lit l'état à chaque
  cycle par `mars/controle.appliquer(conn, rules)` : copie des règles où l'état du panneau est appliqué, avec
  `rules["_controle"]` (pauses) ; les modules (`sentinel`, `sentinel2`, `tip_and_cue`) ne lisent que ce dictionnaire.
* **Les seuils ne s'éditent jamais dans l'interface** : ils restent dans le fichier versionné. Le panneau les affiche
  avec leur version, et ne choisit qu'entre des valeurs et profils définis dans le fichier (section `controle`).
* **Traçabilité** : chaque action est journalisée dans `journal_controle` (qui, quand, quoi, valeur avant et après,
  motif, trace), visible dans les Coulisses (rubrique Journal) et dans Grafana (tableau Satellites).
* **Accès** : les routes d'écriture exigent une connexion d'opérateur (depuis le lot Z1, toutes celles de l'API) ; les
  routes de lecture ne changent pas.
* **Aucun bouton qui redémarre un service ou touche à Docker** (les conteneurs n'ont pas le socket Docker).

## Connexion des opérateurs

| Élément | Choix |
|---|---|
| Mot de passe | haché PBKDF2 SHA 256 (600 000 itérations, sel de 16 octets) dans le `.env` : `MARS_OPERATEURS=nom:pbkdf2_sha256:…` ; plusieurs opérateurs séparés par une virgule ; ligne produite par `taches.py operateur-hacher <nom>` (saisie masquée, deux fois, 12 caractères au moins) |
| Session | jeton aléatoire de 32 octets dans un cookie `mars_operateur` HttpOnly, SameSite strict, chemin `/api`, Secure dès que l'interface est servie en https ; seule son empreinte SHA 256 est en base (`sessions_operateur`) ; 12 h (`controle.session_h`) |
| Origine | une écriture qui porte un en tête Origin d'un autre hôte est refusée (403) |
| Essais | 5 échecs en 10 minutes par adresse : connexion bloquée 10 minutes (429) ; chaque refus journalisé |
| Journal | `connexion`, `connexion_refusee`, `deconnexion`, puis chaque action avec le nom de l'opérateur |

Toujours derrière le tunnel SSH, mais prêt pour le jour où l'interface sera partagée.

## Phase A : capteurs et dépenses (migration 33, règles 2026.10.36)

**Interrupteurs** (`interrupteurs_satellites`, une ligne par source : `sentinel1`, `sentinel2`, `viirs`,
`tip_and_cue`) : en pause, depuis, reprise prévue (facultative), motif, auteur.

| Source en pause | Effet |
|---|---|
| Sentinel 1 ou 2 | la boucle ne crée aucun travailleur ; la décision est consignée dans `task_runs` une fois par cycle (`{"en_pause": motif}`, statut ok, sans journal ni trace d'erreur) ; tip and cue ne retient plus leurs passages (la demande attend avec le motif de la pause) |
| VIIRS | aucun travailleur ; la décision est consignée une fois par jour ; à la reprise, la nuit est rattrapée le jour même ; la règle Grafana « Nuit VIIRS absente » se tait |
| Tip and cue | aucune nouvelle demande ; les demandes actives gardent leur état avec le motif « en pause » et reprennent au cycle suivant la reprise si elles n'ont pas expiré ; la sélection Sentinel ne les sert plus ; la demande manuelle est refusée (409) |

* Commandes manuelles (`viirs`, `sentinel`, `sentinel-analyser`, `sentinel2-analyser`) refusées en pause, sauf
  `--malgre-pause` (consigné, `--motif` facultatif).
* Reprise automatique à la date prévue (vérifiée chaque minute), notification ntfy la veille (une fois), puis à la
  reprise. Chaque pause et chaque reprise est notifiée.
* Commande : `taches.py satellites-pause on|off [sentinel1|sentinel2|viirs|tip_and_cue|tout] [--jusqua DATE]
  [--motif TEXTE]` ; « tout » exclut VIIRS, qui se nomme explicitement ; sans argument, l'état. Date au format
  17/06/2024 08:00 ou 17/06/2024 (08:00), heure de Paris. Auteur : `MARS_OPERATEUR`, « terminal » par défaut.

**Plafond quotidien de scènes** de Sentinel 1 et 2 réglable dans le panneau, dans la plage du fichier
(`controle.plafonds`, 0 à 4) ; motif obligatoire ; la valeur du fichier reste affichée (« fichier : 2 »). Le plafond 0
coupe la source comme avant.

**Dépenses** : jauge du budget Sentinel du mois (dépensé, réservé, restant ; orange à 80 %), par source et au total ;
VIIRS compté hors budget. Dépensé : coût réel des travailleurs détruits (estimé à défaut) ; réservé : travailleurs en
vie (coût estimé de leurs analyses). Coût réel des dix derniers travailleurs.

**Demande d'observation manuelle** d'un point (latitude, longitude, rayon de 1 à 30 km, motif), en plus du clic droit
sur la carte ; le rayon choisi remplace le rayon fixe d'un point (`rayon_demande_m`).

**Grafana** : tuile Satellites (sources en pause dans le texte), tableau Satellites (interrupteurs, journal du
panneau), règle « Nuit VIIRS absente » muette quand VIIRS est en pause (fonction SQL `source_en_pause`).

## Phase B : état du système (lecture seule, aucune migration)

Section « Système », aujourd'hui rubrique Système des Coulisses (`GET /api/controle/systeme`, rafraîchie chaque minute) :

* services et version déployée (empreinte du commit, `signes_de_vie`), âge du dernier signe de vie (en rouge au delà
  de 10 minutes) ;
* mémoire des conteneurs qui la publient d'eux mêmes, lue dans leur propre cgroup (v2, sinon v1) : taches et
  ingestion à chaque signe de vie, l'API en direct ; la collecte n'écrit pas son propre signe (taches le relève
  d'après ses fichiers) : sa mémoire est omise. Aucun socket Docker ;
* retard des positions (dernière position reçue), disque (dernière mesure de `mesures_disque`, en rouge sous 15 %) ;
* dix dernières tâches en échec (refus attendus exclus), chacune avec le lien vers sa trace ;
* lien vers Grafana (`MARS_GRAFANA_URL`, `http://localhost:3000` par défaut, par le tunnel SSH).

## Phase C : règles d'alerte (migration 34, règles 2026.10.37)

Section « Règles », aujourd'hui rubrique Règles d'alerte des Coulisses : pour chaque règle (coupure AIS, rendez vous, changement d'identité et ses deux sous types,
navire des listes, détections VIIRS, Sentinel 1 et Sentinel 2 avec leurs trois types d'alerte), un mode :

| Mode | Effet |
|---|---|
| actif | alertes créées et mises à jour comme avant |
| observation | aucune alerte ; chaque épisode est compté dans `task_runs` (règles du flux : `observees` dans le détail de la tâche regles ; VIIRS et Sentinel : `controle_observees` dans l'exécution du travailleur) et gardé une fois dans `episodes_observes` (clé de l'alerte qu'il aurait donnée) |
| coupé | rien ; une règle du flux entièrement coupée n'est même pas calculée |

* Le mode en base (`reglages_controle`, clé `mode:<règle>` ou `mode:<règle>.<sous type>`) prime sur le fichier,
  `ais_gap.mode` compris ; le mode d'un sous type prime sur celui de sa règle. Catalogue des règles et section des
  seuils : `controle.regles` de `config/rules.yaml`.
* **Aucune alerte n'est supprimée** par un changement de mode : une règle qui n'est pas active ne retire plus les
  alertes vierges non retrouvées. Au passage en coupé, les alertes ouvertes de la règle restent telles quelles, et le
  motif entre dans leur historique (commentaire signé de l'opérateur).
* Motif obligatoire, journalisé (`mode_regle`, mode avant et après, alertes annotées).
* Seuils affichés en lecture seule, à plat, avec la version des règles ; nombre d'alertes et d'épisodes (alertes plus
  épisodes observés) des 7 derniers jours par règle et sous type.
* **Empreinte de l'état du panneau** : toute alerte enregistre, en plus de la version des règles, l'empreinte
  (`alerts.controle_empreinte`, 16 caractères) de l'état du panneau qui l'a influencée (modes des règles, puis zones
  de vigilance) ; l'état complet est gardé une fois par empreinte (`empreintes_controle`). Pied de la fiche d'une
  alerte : « Règles 2026.10.37, panneau 1a2b3c4d ».

## Phase D : notifications (migration 35, règles 2026.10.38)

Préférences en base (`reglages_controle`, clé `notif:preferences`) par dessus celles du fichier
(`controle.notifications`), objet par objet :

| Préférence | Défaut | Effet |
|---|---|---|
| Nouvelles alertes | gravité élevée au moins ; tous les types sauf AIS_UNCONFIRMED | une notification par nouvelle alerte (au plus 5 par minute, puis une synthèse) ; au premier passage, la dernière alerte est seulement notée |
| Résultat de tip and cue | oui | alerte relevée par une vérification satellite (gravités de `tip_and_cue.ntfy_gravites`) |
| Catégories système | pannes, disque, collecte, travailleurs : oui | pannes : tâches en échec, collecteur de traces, statistiques ; disque ; collecte : collecte figée et ingestion ; travailleurs : nuit VIIRS absente, analyse abandonnée, pauses et reprises |
| Heures calmes | 23:00 à 07:00, heure de Paris | tout est retenu sauf les pannes graves (disque presque plein, collecte figée, ingestion) ; résumé au réveil en un message |

* Appliqué aux envois de taches (`controle.notifier`, `controle.decider`) et de l'API, aux alertes de secours (catégorie
  coupée respectée, une alerte déjà envoyée suit son cours), et aux règles de Grafana : chaque condition passe par la
  fonction SQL `notification_permise(catégorie, grave)`, qui lit les mêmes préférences (`preferences_notification`,
  défauts du fichier recopiés par taches chaque minute dans `notifications_etat`). Une condition de Grafana retenue la
  nuit se déclenche au réveil si elle dure encore ; le résumé du réveil réunit les notifications de taches et de l'API.
* Calcul des heures calmes identique en Python et en SQL (testé sur les mêmes instants, heure d'été et d'hiver).
* Bouton « Envoyer une notification de test » : part tout de suite (préférences ignorées), journalisé ; refusé si
  `NTFY_SUJET` manque.

## Phase E : zones de vigilance (migration 36, règles 2026.10.39)

Section « Zones de vigilance », aujourd'hui rubrique du même nom dans les Coulisses : une zone se crée par un **polygone dessiné sur la carte** (« Dessiner sur la carte »,
un clic par sommet, rien d'autre ne se sélectionne pendant le tracé) ou par un **tampon autour de l'infrastructure
sélectionnée** (500 m à 20 km, 2 milles par défaut), avec un nom, un motif, une date de fin facultative et un profil
choisi parmi ceux du fichier (`zones_vigilance.profils`). Une zone se retire avec un motif, jamais ne se supprime.

| Profil | Gravité minimale | Tip and cue | Sélection Sentinel | Seuils abaissés dans la zone |
|---|---|---|---|---|
| Renforcé | moyenne | priorité relevée de 2 | point d'intérêt classé comme une demande de priorité 5 | rendez vous 60 min (120 ailleurs), coupure AIS 60 min (120 ailleurs) |
| Attention | inchangée | relevée de 1 | priorité 3 | aucun |

* Une zone ne fait qu'abaisser un seuil, jamais le relever. Les règles du flux cherchent au seuil le plus bas des
  zones actives, puis jugent chaque épisode au seuil de sa position ; sans zone, le calcul est inchangé (mêmes
  requêtes, mêmes paramètres).
* L'alerte enregistre la zone et le profil appliqués (`details.zone_vigilance` : identifiant, nom, profil, gravité
  avant) ; l'empreinte de l'état du panneau inclut les zones actives et leur profil.
* **Priorité aux navires suivis** (oui ou non, motif obligatoire) : une demande d'observation d'un navire suivi est
  relevée de `controle.suivis.priorite_plus` (1), donc servie d'abord par la sélection Sentinel.
* Couche « Zones de vigilance » (groupe zones, active par défaut : trait clair en pointillé) ; un clic ouvre la fiche
  de la zone : profil, motif, période, surface, alertes qui portent la zone (gravité relevée signalée), demandes
  d'observation et passages qui la couvrent (7 jours avant, 3 jours après). La sélection est gardée dans l'adresse
  (`sel=vigilance:<id>`).

## Phase F : mode démonstration (migration 37, aucune règle changée)

**Rejeu tel qu'il était** (bascule propre au navigateur) : en mode plage ou rejeu, l'instant de l'adresse de la page
sert aussi de date de lecture, à la minute. Les alertes ne sont montrées que si elles étaient déjà levées
(`detected_at`), avec leur statut d'alors (dernière décision d'opérateur antérieure ; `a_l_instant` de
`/api/alerts`) ; les demandes d'observation, si elles étaient déjà créées, une demande close plus tard y apparaissant
encore active (planifiée si un passage lui était retenu) et sans résultat (`a_l_instant` de `/api/observations`). Le
trafic est déjà lu à l'instant. Rien n'est écrit : le rejeu ne fait que lire (test : la table des alertes est
identique avant et après). Le direct n'est jamais touché.

**Scénarios** (`scenarios_demo`) : la vue courante (adresse de la page : mode, période, instant, région, objet
sélectionné, couches) s'enregistre sous un nom, par un opérateur connecté ; un clic la rouvre. Exemple : le 19/10
autour du passage de l'analyse 97.

**Masquage pour une démonstration publique** : une graine aléatoire dans un cookie de session (effacé à la fermeture
du navigateur). L'API réécrit alors toutes ses réponses JSON et le flux en direct : noms, MMSI (pays gardé, le
pavillon reste juste), OMI (clé de contrôle valide) et indicatifs remplacés par des pseudonymes stables pendant la
session ; liens vers les sources retirés ; photos et traces GPX refusées. Aucune donnée réelle n'atteint le
navigateur, ni à l'écran ni dans les réponses visibles dans ses outils de développement. Bandeau discret
« démonstration » sur la carte. Les infrastructures, régions et zones gardent leur nom (données publiques).

Limites connues :
* un nom réel qui n'apparaît dans aucun champ structuré de la session (seulement dans un texte libre) resterait
  lisible ; les textes des alertes nomment toujours le navire dont elles portent aussi l'objet, le cas n'a pas été vu ;
* la recherche accepte un nom réel saisi au clavier et répond par le pseudonyme : le lien entre les deux est visible
  de qui tape la recherche ;
* le rejeu ne reconstitue pas une gravité relevée plus tard par tip and cue (la vérification satellite est montrée),
  ni l'heure de calcul des détections satellites (montrées à l'heure de leur prise de vue).
