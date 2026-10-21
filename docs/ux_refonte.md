# Refonte UX et UI de MARS C2 : remarquer les navires étranges (lot U, phase 1, 21/10/2026)

Question unique de l'opérateur : **« Quel navire dois je regarder maintenant, et pourquoi ? »** Ce rapport est l'audit
de l'interface actuelle, mesuré, et la proposition de cible, maquettée, avec son modèle de données et son plan de
construction. **Aucun code d'interface n'a été écrit** : la phase 2 attend la validation d'Antoine.

## 1. En bref

* L'interface répond à « quelles alertes ont été levées », pas à « quel navire regarder ». Sur le jeu d'essai, le
  navire le plus suspect (sur liste de sanctions, AIS coupé 18 h, revu au radar sans AIS) est éclaté en **trois
  lignes du fil**, dont la première n'est même pas rattachée à lui ; le lien n'apparaît qu'au fond d'une fiche.
* L'écran par défaut affiche **231 mots, 379 éléments, 60 commandes et 10 couleurs vives** ; une fiche navire 621
  mots, une fiche infrastructure 860. Dix types d'alerte ont chacun leur couleur ; la gravité n'est que du texte.
* Mesuré sur les trois tâches de référence : trouver le navire le plus suspect environ 20 s et une réponse fausse
  probable ; comprendre pourquoi environ 41 s et 5 gestes ; classer un faux positif 6 s et 4 gestes **par alerte**.
  Cible maquettée : 4,5 s, 6 s et 2 touches pour tout le dossier.
* **Trois défauts trouvés en passant**, dont un grave : **en mode plage et rejeu, la carte n'affiche aucun navire**
  (section 3). Je propose de le corriger tout de suite, avant la refonte (question 1).
* Proposition : le navire devient l'unité (« dossier »), trois niveaux d'urgence et une seule échelle de couleur,
  une phrase d'explication tirée des faits, trois espaces (Veille, Enquête, Coulisses), tri au clavier. Le moteur ne
  change pas.

## 2. Méthode et jeu d'essai

* **Données** : base de test, positions réelles des 08 et 09/10/2026 (503 navires, 135 891 positions, Manche et
  Bretagne nord), infrastructures EMODnet (816 tracés, lues sur le serveur par GET). Alertes produites par **le moteur
  inchangé** (règles toutes les 3 h sur le 09/10, coupures AIS en mode actif) : 9 coupures, 5 navires des listes.
  Ajoutés à la main pour couvrir chaque écran : quatre navires réels marqués « sur liste » (aucun ne l'est), un passage
  Sentinel 1 analysé (31 échos appariés, un écho sans AIS, un grand navire déclaré mais non vu), un passage Sentinel 2,
  une nuit VIIRS (une lumière sans AIS), une vérification satellite rattachée à la plus longue coupure. Le jeu ne
  contient ni rendez vous ni changement d'identité (le moteur n'en a trouvé aucun sur ces deux jours).
* **Masquage** : toutes les captures et maquettes passent par le mode démonstration (lot P, phase F) : les noms sont
  des pseudonymes (GOELAND 466 est un navire réel masqué). Aucun navire réel n'apparaît comme sanctionné.
* **Outils** : API et interface lancées sur le Mac contre la base de test ; Chromium sans affichage, rendu WebGL par
  ANGLE et SwiftShader (la carte s'affiche ; même Metal fonctionne sans affichage) ; 1440 × 900 ; mesures dans la page
  (mots visibles hors carte, éléments visibles, éléments interactifs, couleurs distinctes et vives). Les captures PNG ne
  sont pas versionnées (règle des fichiers de données) : 27 captures dans le dossier de session
  `scratchpad/ux/captures/`, à joindre à la demande.
* **Temps estimés** : modèle KLM (un clic 1,3 s, défilement 1 s, décision 1,35 s, lecture 250 mots par minute, seuls
  les mots utiles comptés).

## 3. Défauts trouvés pendant l'audit (indépendants de la refonte)

| Gravité | Défaut | Cause | Correctif |
|---|---|---|---|
| **4, bloquant** | En mode **plage** et **rejeu**, la carte n'affiche **aucun navire** (« 0 navires » dans les couches), sur le serveur aussi | `/api/traffic` renvoie `{at, cols, rows}`, l'interface (`lib/trafic.ts`, `decodeTraffic`) attend `{navires: {cols, rows}}`, le format du flux en direct ; le direct n'est pas touché | une ligne : accepter les deux formes ; vérifié localement (196 navires reviennent), non versionné |
| 2 | Couche « Zones de vigilance » sans libellé dans le panneau des couches | libellé oublié au lot P (`libelles.ts`) | ajouter le libellé |
| 2 | Le bandeau « démonstration » passe par dessus le détail d'un voyant | même empilement (z-index) | baisser le bandeau sous les détails |

## 4. Inventaire chiffré

### Structure

| Élément | Nombre |
|---|---|
| Espaces de premier niveau | 1 écran, **6 panneaux** au rail (alertes, couches, analyses, suivis, observations, contrôle) et la recherche |
| Voyants permanents de la barre d'état | **7** (flux AIS, ingestion, listes, disque, archivage, satellites, version), plus le sélecteur de région |
| Types d'alerte | **10** au registre (7 actifs, 3 « à venir » affichés grisés dans le fil), **10 couleurs** distinctes |
| Filtres du fil | 11 puces de type, 4 de gravité, 3 onglets de statut : **18 commandes avant la première alerte** |
| Couches | 16 (dont 3 « à venir »), 6 groupes |
| Types de fiche | 7 (alerte, navire, détection VIIRS, radar ou optique, infrastructure, zone, passage, zone de vigilance) ; **40 sections** au registre |
| Pistes de la frise | 5 |
| Sections du panneau Contrôle | 9 |
| Libellés de l'interface | 746 chaînes |
| Raccourcis clavier | 2 (⌘K, Échap) |

### Par écran (base de test, navires masqués)

| État | Mots | Éléments | Commandes | Couleurs vives |
|---|---|---|---|---|
| Accueil en direct (hors données) | 111 | 212 | 49 | 9 |
| **Plage de 24 h, fil d'alertes** | **231** | 379 | 60 | 10 |
| Couches | 167 | 272 | 46 | 12 |
| Analyses satellites | 98 | 173 | 33 | 6 |
| Navires suivis | 77 | 151 | 32 | 6 |
| Observations satellites | 84 | 202 | 39 | 7 |
| Contrôle | 493 | 279 | 54 | 6 |
| Fiche alerte : coupure AIS | 410 | 463 | 68 | 10 |
| Fiche alerte : écho radar sans AIS | 316 | 441 | 72 | 10 |
| Fiche alerte : navire sur liste | 350 | 462 | 72 | 10 |
| Fiche alerte : lumière VIIRS | 330 | 436 | 72 | 10 |
| Fiche alerte : déclaré non vu | 337 | 460 | 75 | 10 |
| **Fiche navire** | **621** | 461 | 69 | 10 |
| **Fiche infrastructure** | **860** | 535 | 83 | 10 |
| Fiche zone | 319 | 477 | 76 | 10 |
| Fiche passage | 280 | 417 | 63 | 10 |
| Fiche détection VIIRS | 296 | 430 | 64 | 11 |
| Fiche écho radar | 304 | 438 | 65 | 11 |
| Détail d'un voyant (six mesurés) | 234 à 289 | 383 à 410 | 60 | 10 |
| Recherche | 248 | 399 | 63 | 10 |
| Rejeu | 235 | 384 | 64 | 10 |

Les couleurs de la carte (WebGL) s'ajoutent : 10 couleurs de type d'alerte, 4 d'infrastructure, celles des couches.

## 5. Évaluation selon les dix heuristiques de Nielsen

Gravité : 4 catastrophe (à corriger avant tout), 3 majeur, 2 mineur, 1 cosmétique.

| Gravité | Heuristique | Problème |
|---|---|---|
| 4 | 6. Reconnaître plutôt que se souvenir | Les faits d'un même navire sont dispersés : l'écho radar sans AIS de 18:12 est la première ligne du fil, sans nom, alors qu'il est la preuve de la coupure de GOELAND 466 deux lignes plus bas ; le lien n'est écrit qu'au fond de la fiche de la coupure (« écho à 4100 m de la position prédite »). L'opérateur fait la synthèse de tête. |
| 4 | 1. Visibilité de l'état | Plage et rejeu sans aucun navire (section 3), « 0 navires » sans explication. |
| 3 | 8. Esthétique et minimalisme | 231 mots et 18 filtres avant la première alerte ; trois types « à venir » affichés grisés sur l'écran principal ; fiche navire 621 mots, fiche infrastructure 860 (liste de 40 navires passés). |
| 3 | 4. Cohérence | Dix couleurs pour dix types, la gravité en texte gris à droite : la couleur dit le type, pas l'urgence ; le rouge de la coupure AIS est aussi celui d'un voyant en panne et d'une alerte critique. |
| 3 | 7. Flexibilité et efficacité | Aucune navigation au clavier hors ⌘K ; classer prend 4 gestes par alerte, à refaire pour chaque alerte d'un même navire. |
| 3 | 2. Correspondance avec le monde réel | Termes d'ingénieur à l'écran : tip and cue, travailleur, fenêtre, granule, trace, empreinte, « Aucun opérateur configuré (MARS_OPERATEURS) » ; durées en minutes (« silence de 1168 min » pour 19 h) ; « Position AIS non confirmée » au lieu de « déclaré mais non vu ». |
| 3 | 1. Visibilité de l'état | Sept voyants toujours allumés, en rouge « n.d. » sur une installation sans collecte : le vrai danger (« la veille est aveugle ») ne se distingue pas d'un archivage en retard. |
| 2 | 4. Cohérence | Deux compteurs différents pour la même chose : 10 sur l'icône du rail (gravité moyenne et plus), « À traiter 14 » dans le fil. |
| 2 | 3. Contrôle et liberté | Un seul panneau du rail à la fois : ouvrir Contrôle ou Couches cache le fil. |
| 2 | 5. Prévention des erreurs | L'auteur d'une décision est un champ libre, prérempli par le navigateur ; sans connexion, rien ne garantit qui a classé. |
| 2 | 9. Diagnostic des erreurs | « n.d. » partout quand une mesure manque, sans dire pourquoi ni si c'est grave. |
| 1 | 10. Aide | Aucune aide ni glossaire dans l'interface. |

## 6. Tâches de référence

Mesurées sur le jeu d'essai, plage du 09/10, instant 21:00. Interface actuelle avec le correctif local du trafic
(sinon la carte est vide), cible d'après les maquettes.

| Tâche | Actuel : gestes, à lire, temps | Cible : gestes, à lire, temps |
|---|---|---|
| **a. Trouver le navire le plus suspect** | 1 clic (déplier le groupe), 9 lignes et 18 filtres à parcourir, environ 66 mots utiles ; **environ 20 s**, et la première ligne (écho sans nom) ou les deux coupures « élevée » passent avant le navire sur liste : réponse fausse probable | 0 geste, la première ligne du compteur « 3 à examiner » (13 mots) ; **environ 4,5 s** |
| **b. Comprendre pourquoi** (objectif moins de 10 s) | 3 clics et 1 défilement (groupe, coupure, fiche navire, puis la ligne de l'écho), environ 130 mots, rapprochement de tête ; **environ 41 s** | la phrase de la liste suffit (« Sanctionné, AIS coupé 18 h près d'un câble télécom, revu au radar sans AIS ») ; Entrée ouvre le récit, 3 lignes d'en tête ; **environ 6 s** |
| **c. Classer un faux positif avec motif** | par alerte : ouvrir, défiler, Classer, motif = 3 clics et 1 défilement, **environ 6 s par alerte**, 12 à 19 s pour un navire à 2 ou 3 alertes | C puis 1 : **2 touches, environ 2 s** pour tout le dossier, journalisé alerte par alerte |

## 7. La cible

Maquettes HTML autonomes, données de la base de test, navires masqués, carte en SVG tirée de la base (trait de côte,
câbles, trafic à l'instant) : `docs/ux/maquettes/index.html` (et le tableau des dossiers calculés),
`veille.html`, `dossier_navire.html`, `dossier_point.html`, `coulisses.html`, `calme.html`, `degrade.html`,
`triage.html`. Régénérées par `docs/ux/maquettes/generer.py`.

| Maquette | Mots | Ce qu'elle montre |
|---|---|---|
| Veille | 98 (231 aujourd'hui) | « 3 à examiner », une ligne par dossier : barre d'urgence, nom et pavillon, phrase, icônes, ancienneté ; « 10 à noter » replié ; carte calme : navires en dossier nets et colorés, le reste en poussière grise, câbles en trait fin ; vues Veille, Trafic complet, Satellites, Couches |
| Dossier d'un navire | 136 (621 à 860 aujourd'hui pour les fiches) | niveau, nom, qui, phrase ; chronologie des faits avec les preuves au fil du récit (vignette radar à l'écho) ; route AIS et silence en tirets jusqu'à l'écho ; détail technique replié ; trois gestes |
| Dossier point | 98 | lumière VIIRS sans navire : même récit, pourquoi elle reste « à noter » (à confirmer) |
| Coulisses | 161 | une porte, neuf cartes : système, capteurs et dépenses, règles et faux positifs, satellites, notifications, zones, démonstration, couches détaillées, journal |
| État calme | 58 | « Aucun navire inhabituel depuis 6 h », ce qui est surveillé |
| État dégradé | 119 | voyant Système allumé : « AIS coupé depuis 12 min : la veille est aveugle » |
| Triage au clavier | 137 | J et K, Entrée, C (motif en 1, 2, 3), S, V ; la décision vaut pour toutes les alertes ouvertes du dossier |

### Échelle de couleur unique : l'urgence

| Niveau | Couleur | Usage |
|---|---|---|
| Urgent | rouge `#ef6461` | barre de la ligne, cercle plein sur la carte, pastille du dossier |
| À examiner | ambre `#f0a84b` | idem |
| À noter | gris clair `#aab6bf`, en contour seulement | pas de couleur : il ne demande pas l'attention |
| Sélection, focus | cyan `#4fb6c8` (signal système actuel) | jamais pour un niveau |
| Le reste | gris | trafic, infrastructures, côte ; le type d'événement passe par l'**icône** |

Les dix couleurs de type disparaissent de l'écran principal ; elles peuvent rester dans la vue « Trafic complet »
et dans les Coulisses si Antoine y tient (question 5).

### Glossaire de l'opérateur

| Aujourd'hui | Demain | Où le terme technique reste |
|---|---|---|
| Alerte (une par règle) | **Dossier** (un par navire ou par point), fait de **signaux** | détail technique, journal |
| Gravité critique, élevée, moyenne, faible | **Urgent, À examiner, À noter** (niveau du dossier) | détail technique |
| Navire sur liste, niveaux fort, sanctionné, flotte fantôme, suspect GUR | **Sanctionné**, **Flotte fantôme**, **Sur liste GUR**, **Suspect (GUR)** | fiche des sources |
| Coupure AIS, silence de 1168 min | **AIS coupé 19 h** | règle « coupure AIS » en Coulisses |
| Navire sombre (DARK_SHIP) | **Écho radar sans AIS**, **Vu en image, sans AIS**, **Lumière la nuit, sans AIS** | idem |
| Position AIS non confirmée (AIS_UNCONFIRMED) | **Déclaré mais non vu** | idem |
| Identité contredite par satellite (IDENTITY_MISMATCH) | **Taille déclarée contredite** | idem |
| Changement d'identité | **Nom changé**, **Même OMI, autre MMSI** | idem |
| Rendez vous suspect | **Rendez vous de 3 h** | idem |
| Tip and cue, demande d'observation, Observations satellites | **Vérification satellite** | Coulisses, satellites |
| Passage Sentinel 1, Sentinel 2, nuit VIIRS | **passage radar**, **image satellite**, **image de nuit** | Coulisses, satellites |
| Évaluable, non évaluable, zone de réception fiable | **AIS bien reçu ici**, **AIS mal reçu ici** | détail technique |
| Acquitter | **Vu** (je l'ai regardé, pas de décision) | journal |
| Travailleur, fenêtre, SCL, granule, trace, empreinte, version | rien sur l'écran principal | Coulisses |

Règle des listes : un chiffre, une icône, cinq mots au plus par fragment, trois fragments au plus.

### Ce qui ne se perd pas

Tout reste accessible : le fil d'alertes complet (vue « Alertes » de la Veille), les fiches actuelles (dépli « Détail
technique » du dossier, et fiches infrastructure, zone, passage, détection depuis la carte), les couches détaillées,
les six panneaux du rail (Coulisses), l'adresse de la page (`?espace=veille&dossier=navire:6140`, l'ancien `sel=` reste
lu), ⌘K, direct, plage et rejeu (la frise reste en bas, repliable), démonstration et masquage.

## 8. Modèle de données du dossier

Prototype exécutable : `docs/ux/maquettes/dossiers.py`, éprouvé sur le jeu d'essai (tableau de
`docs/ux/maquettes/index.html`) : **17 alertes deviennent 13 dossiers**, dont 1 urgent, 2 à examiner, 10 à noter.

**Regroupement**
* Dossier **navire** : toute alerte ouverte (nouvelle, vue, confirmée) qui a le navire pour preuve
  (`alert_evidence` vessel), plus ses vérifications satellites et les détections qui le concernent.
* Dossier **point** : alerte sans navire (lumière VIIRS, écho radar ou optique sans AIS). **Rattaché au dossier d'un
  navire** quand une vérification satellite les relie (même détection en preuve des deux alertes) : c'est le cas
  de l'écho de 18:12 et de GOELAND 466. Sinon fusionné avec un autre point à moins de 2 km et 6 h.
* **Fenêtre** : alertes dont l'instant est dans les 72 h précédant l'instant affiché et déjà levées à cet instant
  (le rejeu tel qu'il était s'applique). Un dossier dont toutes les alertes sont classées est **clos** ; une nouvelle
  alerte le rouvre, avec l'historique.

**Niveau de suspicion (trois niveaux, explicable)**
1. Base : gravité la plus haute de ses signaux (critique 3, élevée 2, moyenne et faible 1).
2. Plus un si **deux familles de signaux indépendantes** au moins se recoupent (liste, AIS, satellite, comportement,
   identité).
3. Plus un si une **vérification satellite** a vu le navire sans AIS.
4. Ramené à 1 si tous les signaux sont « à confirmer » ; borné de 1 à 3.
Chaque contribution est listée dans le dossier (« gravité la plus haute : élevée (niveau 2) ; signaux recoupés : AIS,
satellite, liste (plus un) ; vérification satellite : vu sans AIS (plus un) »).

**Phrase** : un fragment par type de signal (gabarits du glossaire), du plus fort au plus faible (liste, puis AIS et
satellite, puis le reste), trois au plus ; plusieurs coupures se résument (« AIS coupé 2 fois »). Exemple :
« Sanctionné, AIS coupé 18 h près d'un câble télécom, revu au radar sans AIS ».

**Décision** : Classer (motif), Vu, Confirmer, Suivre, Vérifier par satellite, sur le dossier ; **appliquée à
chaque alerte ouverte du dossier** et journalisée alerte par alerte dans `alert_actions` (même auteur, même motif,
note « décision prise sur le dossier navire:6140 »), rien supprimé. Les motifs alimentent la mesure des faux positifs
par règle (Coulisses, Grafana).

**API (phase 2, tracée)**
* `GET /api/dossiers?start&end&region&a_l_instant` : liste triée par niveau, nombre de signaux, ancienneté ; span
  `dossiers.liste` (dossiers, alertes lues, durée). Calcul en Python sur les alertes de la fenêtre (quelques centaines),
  aucune table nouvelle.
* `GET /api/dossiers/{cle}` : récit (chronologie des signaux, vérifications, preuves, route AIS), contributions au
  niveau, détail technique ; span `dossiers.recit`.
* `POST /api/dossiers/{cle}/decision` : action, motif, note ; décision étendue aux alertes ouvertes, journalisée ; span
  `dossiers.decision` avec le nombre d'alertes touchées.
* Tests : regroupement (navire, point rattaché par la détection, points voisins), niveau et contributions, phrase,
  fenêtre et rejeu, décision appliquée aux seules alertes ouvertes, journal par alerte.

## 9. Plan de construction (phase 2), par étapes livrables et réversibles

| Étape | Contenu | L'interface reste utilisable |
|---|---|---|
| 0 | Correctifs de la section 3 (trafic en plage et rejeu, libellé, bandeau) | oui, rien d'autre ne change |
| 1 | API des dossiers et ses tests ; aucune page ne l'utilise encore | oui |
| 2 | Échelle d'urgence et glossaire (`styles.css`, `libelles.ts`), icônes de type ; la gravité en couleur dans le fil actuel | oui, même disposition |
| 3 | **Veille** : liste des dossiers par défaut, compteur « N à examiner », états vides, carte calme et trois vues ; le fil d'alertes reste en second onglet | oui, retour au fil d'un clic |
| 4 | **Enquête** : dossier en récit, trois gestes, détail technique replié vers les fiches actuelles | oui |
| 5 | **Triage au clavier** (J, K, Entrée, C, S, V, ?) et mesure des faux positifs | oui |
| 6 | **Coulisses** : les six panneaux du rail, la barre d'état réduite au voyant Système et l'état dégradé | oui ; les panneaux gardent leur contenu |
| 7 | Mesure des trois tâches, captures avant et après, publication | |

Après chaque étape : tests de l'API, typecheck, knip, construction de l'interface, `verifier_images.sh`, et mesure des
trois tâches comparée à la section 6.

## 10. Questions pour Antoine

1. **Correctif immédiat** du trafic en plage et rejeu (une ligne, section 3), sans attendre la refonte ?
2. **Niveau** : un navire des listes qui passe sans rien d'autre est « à noter » (quatre sur le jeu d'essai). Le
   veux tu « à examiner » ? Et une coupure AIS élevée seule (aujourd'hui « à examiner ») ?
3. **« Près d'un câble »** : à 2 milles, presque toute coupure en Manche est près d'un câble, le fragment perd sa
   valeur. Seuil de 1 mille, ou seulement les câbles électriques et les tracés nommés ?
4. **Fenêtre** des dossiers : 72 h ? Fusion des points : 2 km et 6 h ?
5. **Couleurs de type** : disparaissent entièrement, ou restent dans la vue « Trafic complet » ?
6. **Acquitter** devient « Vu » : d'accord ? « Confirmer » reste un geste du dossier ou passe dans le détail ?
7. **Touches** J, K, C, S, V : d'accord ? V crée une vérification satellite (coût possible) : faut il une confirmation ?
8. **Fil d'alertes complet** : second onglet de la Veille, ou Coulisses ?
9. **Ordre des étapes** de la section 9 : d'accord, ou la Veille avant l'API (maquette branchée sur le prototype) ?
