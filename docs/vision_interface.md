# MARS C2 : vision de l'interface opérationnelle

Oct 6, 2026

## Principes

L'interface sert un opérateur qui doit répondre, à tout instant, à trois questions : **qu'est ce qui demande mon attention, pourquoi, et qu'est ce que j'en fais ?** Elle est conçue une fois pour toutes, avec les emplacements des fonctionnalités des étapes 3 et 4 déjà prévus, pour qu'elles s'y ajoutent sans refonte.

1. **Une seule vue, opérationnelle.** La carte est au centre ; tout le reste (fil d'alertes, fiches, frise) l'entoure. Pas de tableau de bord séparé, pas de mode « exploration ».
2. **Tout s'aligne sur la plage de temps.** Une seule plage, choisie dans la frise, gouverne la carte, le fil d'alertes, les trajectoires, les compteurs et, plus tard, les cartes de chaleur. Par défaut : le direct.
3. **La couleur est réservée à ce qui demande l'attention.** Le trafic ordinaire est gris ; les navires des listes, les alertes et les anomalies portent la couleur. Un coup d'œil suffit pour savoir où regarder.
4. **Chaque alerte s'explique par ses preuves.** Positions, sources des listes, identités successives, images radar : l'opérateur juge sur pièces, jamais sur un score seul.
5. **Chaque décision est tracée.** Acquitter, confirmer, classer, commenter : tout laisse une trace datée, consultable dans la fiche.
6. **Une grammaire unique pour tous les objets.** Navire, alerte, infrastructure, détection satellite : chacun se sélectionne sur la carte, se retrouve par la recherche, et s'ouvre dans le même panneau de fiche.
7. **Sobriété.** Peu de texte, des chiffres utiles, et une interface qui reste fluide avec plusieurs milliers de navires, servie par un serveur de 2 Go de mémoire.

## L'écran

L'écran garde la disposition actuelle, que les étapes suivantes rempliront sans la changer : une barre d'état en haut, un rail et un panneau contextuel à gauche, la carte au centre, la fiche à droite, la frise en bas.

&#91;embedded content: disposition de l'écran · 6 zones, une plage de temps commune\]

| Zone | Rôle | Contenu |
| --- | --- | --- |
| Barre d'état | Dire si la plateforme voit bien | Flux AIS (dernier message, débit), retard de l'ingestion, date des listes, disque ; recherche globale à droite |
| Rail | Changer de panneau | Alertes, couches, analyses satellites, navires suivis, recherche ; une pastille indique les alertes non traitées |
| Panneau contextuel | Travailler une liste | Le fil d'alertes par défaut ; les couches, les analyses ou les navires suivis selon l'icône du rail |
| Carte | Situer | Tout ce qui a une position, filtré par la plage de temps et les couches actives |
| Fiche | Comprendre et décider | L'objet sélectionné, ses preuves, et les actions possibles |
| Frise | Choisir le temps | Direct ou plage, rejeu, et les événements repérés dans le temps |

Deux règles de comportement : sélectionner un objet n'importe où (carte, fil, recherche, fiche) l'ouvre dans la fiche et le met en valeur sur la carte (mode focus) ; et l'état de l'écran (objet sélectionné, plage, couches) est inscrit dans l'adresse de la page, pour partager un lien qui rouvre exactement la même vue.

## Les objets et leurs fiches

L'interface manipule six objets. Chacun se sélectionne sur la carte ou dans une liste, se retrouve par la recherche, et s'ouvre dans la fiche, avec la même structure : un en tête (identité et état), des sections repliables, et une barre d'actions en bas.

| Objet | Ce qu'il représente | Ce que montre sa fiche | Étape |
| --- | --- | --- | --- |
| **Navire** | Une coque, suivie par son OMI à travers ses MMSI successifs | Identité, listes, alertes, comportement, trajectoire, puis satellites et prédictions | 2, enrichie en 3 et 4 |
| **Alerte** | Un événement qui demande une décision | Motif, gravité, preuves, navire concerné, historique des décisions, actions | 2 |
| **Infrastructure** | Un câble, un pipeline ou un parc éolien | Type, nom, opérateur, corridor de surveillance, navires passés et présents, alertes liées | 2 |
| **Détection satellite** | Un écho radar, optique ou lumineux | Image, longueur et cap mesurés, navire AIS apparié ou absence d'appariement | 3 |
| **Passage satellite** | Une acquisition Sentinel 1, Sentinel 2 ou VIIRS sur une zone | Emprise, heure, état de l'analyse, détections obtenues | 3 |
| **Zone** | Une région couverte, un mouillage, une cellule de réception fiable | Emprise, statistiques de trafic, qualité de réception | 2 |

Le navire est l'objet central : la plupart des alertes, détections et prédictions s'y rattachent, et sa fiche est le dossier qu'un opérateur transmettrait.

## Le fil d'alertes

C'est le cœur de l'outil : la liste de ce qui demande une décision, triée par urgence. Il accueille dès maintenant tous les types d'alertes prévus, y compris ceux des étapes à venir.

| Type | Ce qu'il signale | Piste | Étape |
| --- | --- | --- | --- |
| WATCHLIST | Navire d'une liste de surveillance dans nos eaux | Flotte fantôme | En place |
| IDENTITY\_CHANGE | Nouveau nom, pavillon ou MMSI pour une même coque | Flotte fantôme | En place |
| RENDEZVOUS | Deux navires proches et lents pendant longtemps, au large | Les deux | En place, à recalibrer |
| AIS\_GAP | Silence d'un navire là où la réception est fiable | Les deux | Après recalibration |
| INFRA\_THREAT | Arrêt, flânerie ou vitesse d'ancre traînante dans un corridor d'infrastructure | Infrastructures | Après recalibration |
| DARK\_SHIP | Écho satellite sans AIS correspondant | Les deux | 3 |
| AIS\_UNCONFIRMED | Position AIS que le radar ne confirme pas | Les deux | 3 |
| IDENTITY\_MISMATCH | Longueur ou cap mesurés par satellite contraires à la déclaration | Flotte fantôme | 3 |
| TRAJECTOIRE\_ANORMALE | Trajectoire jugée improbable par GeoTrackNet | Les deux | Pilote |
| PASSAGE\_PREVU | Passage prédit d'un navire des listes dans un corridor | Infrastructures | 4 |

**Chaque ligne du fil** montre d'un coup d'œil : l'icône et la couleur du type, la gravité, le nom et le pavillon du navire, la zone, l'heure, et un signe distinctif selon le type (niveau de signal pour WATCHLIST, infrastructure concernée pour INFRA\_THREAT, image pour DARK\_SHIP).

**Priorisation.** Tri par gravité puis par date. La gravité tient compte du contexte : une même coupure AIS est plus grave près d'un câble ou pour un navire des listes. Les alertes d'un même navire sont regroupées sous lui, pour ne pas noyer le fil.

**Filtres.** Type, gravité, statut, zone (bretagne, mediterranee, manche, gascogne), et la plage de temps de la frise. Trois onglets rapides, comme aujourd'hui : à traiter, confirmées, toutes.

**Cycle de vie et actions.** Nouvelle, acquittée, confirmée, classée (avec un motif : faux positif, activité légitime, doublon), et réouverture possible. Actions prévues :

- **acquitter, confirmer, classer, commenter** : dès l'étape 2 ;
- **suivre le navire** (l'ajouter aux navires suivis) : dès l'étape 2 ;
- **demander une analyse satellite** de la zone (Tip & Cue) : étape 3 ;
- **exporter le dossier** de l'alerte (preuves, carte, décisions) en PDF : étape 3.

## La fiche navire

C'est le dossier d'un navire, celui qu'un opérateur lirait avant de décider ou transmettrait à un service partenaire. Elle est faite de sections indépendantes, empilées dans un ordre fixe ; les sections des étapes futures s'y insèrent à leur place, et une section sans donnée ne s'affiche pas.

**En tête, toujours visible** : nom, pavillon, MMSI, OMI, type et longueur ; une pastille du niveau de signal s'il figure sur une liste ; l'état du navire (en route, immobile, silencieux) avec l'heure du dernier message ; la photo du navire quand elle existe ; et le bouton « suivre ».

| Section | Contenu | Étape |
| --- | --- | --- |
| Listes de surveillance | Niveau de signal, sources (GUR, OpenSanctions et lien vers la fiche), risques, manière dont le navire est reconnu (OMI ou MMSI seul) | 2 |
| Identités successives | Noms, pavillons, indicatifs et MMSI dans le temps, sous forme de frise compacte | 2 |
| Alertes | Toutes les alertes du navire sur la période, avec leur statut | 2 |
| Comportement | Coupures AIS, arrêts au large, rendez vous, passages à moins de 2 milles d'une infrastructure (nom, durée, vitesse minimale) | 2 |
| Trajectoire | Route sur la période affichée sur la carte, rejeu, export GPX | 2 |
| Score de risque | Somme pondérée et auditable : chaque point s'explique par une alerte ou une liste | 2 ou 3 |
| Vérification satellite | Détections radar, optiques et nocturnes rattachées au navire ; longueur mesurée contre longueur déclarée ; vignettes | 3 |
| Comportement appris | Score d'anomalie de GeoTrackNet le long de la trajectoire, segments improbables surlignés | Pilote |
| Trajectoire prédite | Route anticipée sur 2 à 10 heures avec son incertitude, corridors qu'elle traverse | 4 |
| Notes de l'opérateur | Commentaires libres, horodatés | 2 |

**Barre d'actions** : suivre, recentrer la carte, exporter la trajectoire (étape 2) ; demander une analyse satellite autour de la dernière position et exporter le dossier complet (étape 3).

## La carte et ses couches

La carte affiche tout ce qui a une position, filtré par la plage de temps et par les couches actives. Le panneau des couches présente **une carte par source**, avec son interrupteur, sa légende et un **indicateur d'état** (fraîcheur de la donnée, alerte éventuelle). Les couches des étapes futures y apparaissent dès maintenant, grisées et marquées « à venir », pour que l'opérateur voie l'outil complet.

| Groupe | Couche | Représentation | Indicateur d'état | Étape |
| --- | --- | --- | --- | --- |
| Trafic | Navires | Chevrons orientés en route, points à l'arrêt, gris ; violet pour les listes ; couleur d'alerte si une alerte est ouverte | Dernier message reçu, débit | 2 |
| Trafic | Trajectoires | Route du navire sélectionné ou suivi, sur la plage |  | 2 |
| Infrastructures | Câbles télécoms | Tracés EMODnet | Date d'import | En place, à séparer |
| Infrastructures | Câbles électriques et interconnexions | Tracés EMODnet, couleur distincte | Date d'import | En place, à séparer |
| Infrastructures | Pipelines | Tracés EMODnet | Date d'import | En place, à séparer |
| Infrastructures | Parcs éoliens | Emprises EMODnet | Date d'import | En place, à séparer |
| Infrastructures | Corridors de surveillance | Bande autour des infrastructures affichées |  | 2 |
| Zones | Couverture, mouillages, réception fiable | Contours et cellules | Date de calcul | 2 |
| Satellites | Passages Sentinel 1 et 2 | Emprises des acquisitions | Dernier passage | 3 |
| Satellites | Détections radar et optiques | Cercles : avec AIS, sans AIS, écartée |  | 3 |
| Satellites | Détections nocturnes VIIRS | Points lumineux de la nuit | Dernière nuit traitée | 3 |
| Activité | Cartes de chaleur | Densité, arrêts dans les corridors, détections sans AIS, coupures, sur la plage ; comparaison à la normale EMODnet |  | 3 |
| Prédictions | Trajectoires prédites | Route anticipée et cône d'incertitude |  | 4 |

### Les infrastructures, en détail

Afficher les 816 tracés d'un coup charge la carte et la rend illisible. La couche est donc décomposée, et allégée selon le contexte :

1. **Une sous couche par type** (câbles télécoms, câbles électriques, pipelines, parcs éoliens), chacune avec son interrupteur. Par défaut, seuls les câbles électriques et les parcs éoliens sont affichés : moins nombreux, et les plus sensibles.
2. **Un filtre par zone** (bretagne, manche, gascogne, mediterranee), synchronisé avec le filtre du fil d'alertes.
3. **Le détail selon l'échelle** : aux échelles larges, des tracés simplifiés et estompés ; le détail et les noms reviennent en zoomant.
4. **Le mode « concernées seulement »** : un interrupteur qui ne montre que les infrastructures liées à une alerte ouverte ou proches du navire sélectionné. C'est le mode de travail naturel d'un opérateur.
5. **La fiche d'infrastructure** au clic sur un tracé : nom, type, opérateur, longueur, navires passés à proximité sur la plage, et alertes liées.

**Lisibilité avec plusieurs milliers de navires.** Aux échelles larges, les navires ordinaires s'estompent et seuls restent nets ceux des listes et ceux qui portent une alerte ; le détail revient en zoomant. Le mode focus, déjà en place, atténue tout ce qui ne concerne pas l'objet sélectionné.

**Fond de carte.** Le fond sombre actuel, avec la terre en noir et la mer en gris bleuté, pour que la couleur reste réservée à l'attention.

## La frise et le temps

La frise gouverne le temps de tout l'écran. Elle distingue deux notions : la **plage**, la période étudiée (ce que montrent le fil, les trajectoires et les cartes de chaleur), et l'**instant**, le moment où l'on place la position des navires.

**Trois modes.**

- **Direct** (par défaut) : l'instant suit l'heure réelle, la plage glisse avec lui (« les dernières 24 heures »). Un bouton « Direct » y ramène depuis n'importe quel autre mode.
- **Plage** : on choisit une période passée, par raccourci (1 h, 6 h, 24 h, 7 jours, 30 jours) ou librement en faisant glisser les bornes. Rien ne bouge tant qu'on ne relance pas.
- **Rejeu** : à l'intérieur de la plage, l'instant avance à vitesse choisie (× 1 à × 300) pour revoir une scène, par exemple un rendez vous ou un passage au dessus d'un câble.

**Marqueurs sur la frise.** Les événements y sont repérés dans le temps, sur des pistes distinctes : les alertes (couleur de leur type), les passages satellites Sentinel 1 et 2 et les nuits VIIRS (étape 3), et les **coupures du flux AIS** (zones hachurées), pour ne jamais confondre un navire silencieux avec une panne de notre réception.

**Densité.** Sous la piste des alertes, un fin histogramme montre le nombre de navires suivis dans le temps : un creux signale immédiatement une coupure de la collecte.

**Limite.** La plage ne remonte pas au delà de la conservation en base (30 jours). Au delà, les données sont dans l'archive R2 et se rechargent à la demande.

## Recherche, barre d'état et analyses

### Recherche

Une seule barre, ouverte par **Cmd + K** ou depuis la barre d'état, qui cherche dans tous les objets à la fois, avec les résultats groupés par type :

- **navires** : par nom (y compris les anciens noms), MMSI ou OMI ; un OMI retrouve tous les MMSI successifs de la coque ;
- **infrastructures** : par nom de câble ou de parc ;
- **alertes** : par numéro ;
- **lieux** : ports et zones (« Ouessant », « Pas de Calais »), pour recentrer la carte.

Choisir un résultat ouvre sa fiche et centre la carte dessus.

### Barre d'état

Elle dit en permanence si la plateforme voit bien, car une absence d'alerte n'a de valeur que si les capteurs fonctionnent. Chaque indicateur est vert, orange ou rouge, et ouvre son détail au clic :

| Indicateur | Ce qu'il mesure | Étape |
| --- | --- | --- |
| Flux AIS | Heure du dernier message, débit par minute | 2 |
| Ingestion | Retard entre réception et base | 2 |
| Listes | Date du dernier import GUR et OpenSanctions | 2 |
| Disque | Place libre sur le serveur | 2 |
| Archivage | Dernière archive et dernière sauvegarde sur R2 | 2 |
| Satellites | Dernier passage Sentinel 1 analysé, dernière nuit VIIRS | 3 |

### Panneau des analyses satellites

Il existe déjà pour les analyses radar à la demande (tracé d'une zone en deux clics). À l'étape 3, il devient la file de toutes les analyses : déclenchées automatiquement à chaque passage Sentinel 1 au dessus d'un corridor, déclenchées par une alerte (Tip & Cue), ou demandées par l'opérateur. Pour chacune : la zone, l'heure du passage, l'état (en attente, en cours, terminée, en échec) et le nombre de détections.

### Navires suivis

Une liste personnelle de navires que l'opérateur veut garder à l'œil, avec leur dernière position et leur dernière alerte. Un navire suivi reste visible et coloré à toutes les échelles, et chacune de ses nouvelles alertes remonte en tête du fil.

## Construit maintenant, préparé pour la suite

L'étape 2 construit toute la charpente de l'écran et la remplit avec ce qui existe déjà. Les fonctionnalités des étapes 3 et 4 ne demanderont ensuite que d'ajouter du contenu, pas de refondre l'interface.

| Élément | Construit à l'étape 2 | Ajouté plus tard |
| --- | --- | --- |
| Barre d'état | Flux AIS, ingestion, listes, disque, archivage | Satellites (3) |
| Recherche | Navires, infrastructures, alertes, lieux | Détections et passages satellites (3) |
| Fil d'alertes | Tous les types déclarés ; WATCHLIST, IDENTITY\_CHANGE, RENDEZVOUS actifs ; filtres, regroupement, cycle de vie, commentaires, suivi | AIS\_GAP et INFRA\_THREAT après recalibration ; types satellites (3) ; TRAJECTOIRE\_ANORMALE (pilote) ; PASSAGE\_PREVU (4) ; Tip & Cue et export du dossier (3) |
| Fiche navire | En tête, listes, identités, alertes, comportement, trajectoire, notes | Score de risque, vérification satellite (3), comportement appris (pilote), trajectoire prédite (4) |
| Autres fiches | Alerte, infrastructure, zone | Détection et passage satellites (3) |
| Carte | Navires, trajectoires, infrastructures, corridors, zones ; couches futures affichées « à venir » | Satellites et cartes de chaleur (3), prédictions (4) |
| Frise | Direct, plage, rejeu, alertes, coupures du flux, histogramme de densité | Passages satellites et nuits VIIRS (3) |
| Panneaux | Fil d'alertes, couches, analyses (existant), navires suivis | File des analyses automatiques (3) |

### Les points d'extension

Pour que les ajouts futurs restent simples, quatre registres centralisent ce qui varie, côté interface :

1. **Registre des types d'alerte** : pour chaque type, son libellé, son icône, sa couleur, son signe distinctif dans le fil et la façon d'afficher ses preuves. Un nouveau type d'alerte s'ajoute par une entrée, sans toucher au fil ni à la fiche.
2. **Registre des couches** : pour chaque couche, sa source de données, son style, sa légende, son indicateur d'état et son étape (« à venir » tant qu'elle n'est pas branchée).
3. **Registre des sections de fiche** : chaque section déclare l'objet auquel elle s'applique, sa place, et sa condition d'affichage.
4. **Registre des marqueurs de frise** : chaque piste déclare sa source d'événements et son style.

Côté API, les mêmes principes : chaque nouvelle source arrive par une route qui accepte la plage de temps et la zone, et renvoie un format commun. Les données lourdes (trajectoires sur 7 jours, cartes de chaleur) sont allégées ou agrégées côté serveur, avec des index adaptés, pour tenir sur une machine de 2 Go.

## Points à trancher

Cinq choix restent ouverts avant le développement : les voici tranchés.

| Question | Décision |
| --- | --- |
| Photo des navires | Oui, dans l'en tête de la fiche, avec la source indiquée sous l'image, et un emplacement neutre quand il n'y a pas de photo. Les conditions d'utilisation de la source restent à vérifier avant une démonstration publique |
| Couches « à venir » | Affichées en gris dès maintenant, avec la mention de leur étape |
| Plusieurs opérateurs | Un seul opérateur pour l'instant ; décisions et commentaires horodatés, avec un champ « auteur » déjà prévu |
| Score de risque | Reporté : il attendra les alertes de comportement (coupures, INFRA\_THREAT) |
| Langue | Français, avec tous les libellés centralisés pour permettre l'anglais plus tard |

Prochaine étape : la consigne de développement pour Claude Code, découpée en deux lots. D'abord la charpente et les registres (barre d'état, frise à plage, fil filtrable, couches décomposées, registres) ; puis le contenu (recherche, fiches navire, alerte, infrastructure et zone, navires suivis).
