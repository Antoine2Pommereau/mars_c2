# Spécification : régions de couverture et provisionnement de la donnée statique

Statut : proposition, version 0.3, 03/10/2026. À relire avant toute ligne de code.
Périmètre de cette spec : la donnée de référence géographique (statique) et la façon de la
provisionner par région. Elle ne couvre ni l'AIS, ni l'inférence, ni les règles de scénario,
qui sont dynamiques ou traités ailleurs.

## 1. Objectif

Permettre de définir, dans les réglages, une ou plusieurs régions de couverture, et de télécharger
pour chacune toute l'information statique (carte précise, bathymétrie, infrastructures à risque ou
d'intérêt, gros ports, et plus tard aires protégées et limites maritimes). La région active borne
ensuite tout le reste de la plateforme : centrage de la carte, emprise de l'AIS, recherche des
passages Sentinel 1, couches affichées.

Idée directrice : séparer proprement le statique du dynamique. Le statique se provisionne une fois
par région et se rafraîchit de loin en loin. Le dynamique (AIS, SAR, inférence) ne se provisionne
jamais, il se streame ou s'analyse par instant.

## 2. Périmètre de départ

- **Mers et régions européennes seulement.** La liste des régions se limite aux mers qui touchent
  les eaux européennes. Avantage : EMODnet couvre exactement ces mers, donc une qualité de données
  uniforme et fine, sans avoir à gérer de replis mondiaux pour l'instant.
- **Rectangle d'emprise** pour les régions tracées manuellement. Le polygone arbitraire viendra plus
  tard.
- **Hors périmètre de ce début**, à garder pour plus tard : régions non européennes et sources
  mondiales de repli (GEBCO, TeleGeography) ; fond de carte hors ligne par région (tuiles
  vectorielles) ; aires protégées et limites de ZEE ; le mode live ; les règles de scénario qui
  exploiteront ces couches.

## 3. Concept de région

Deux façons de définir une région :

1. **Prédéfinie** : choisie dans une liste des mers européennes. La découpe de référence est celle
   de l'OHI (limites des océans et mers, Special Publication 23), récupérée en polygones via Marine
   Regions (marineregions.org), filtrée aux mers européennes. Exemples pertinents pour les données
   déjà chargées : Kattegat, Skagerrak.
2. **Manuelle** : tracée sur la carte en deux clics (mécanisme déjà en place pour les analyses),
   nommée, puis provisionnée comme une région prédéfinie. Rectangle au début.

Caveat connu : les mers OHI varient énormément en taille. La liste reste un bon catalogue, mais le
tracé manuel sert justement à cibler un détroit sans avaler une mer entière.

## 4. Catalogue des couches et sources

Tout est provisionné en local, donc hors ligne. La distinction n'est pas en ligne contre hors ligne,
mais selon l'usage :

- **Couches opérationnelles** : téléchargées, découpées et stockées en local, elles nourrissent les
  règles de détection. Cohérentes avec ce que calcule le détecteur.
- **Couches d'affichage** : également téléchargées et stockées en local, mais seulement montrées à
  l'opérateur, jamais utilisées par les règles. On ingère la donnée (WFS pour les vecteurs, WCS ou
  export pour les rasters) et on la stylise nous mêmes pour coller au poste de commandement sombre.
  Un aperçu WMS d'EMODnet reste possible en secours, mais ce n'est pas le mode par défaut.

### 4.1 Couches opérationnelles (provisionnées, locales)

Toutes limitées aux mers européennes au début, donc EMODnet en source principale, qualité uniforme.
Le provisionneur inscrit au manifeste la source réellement utilisée pour chaque couche.

| Couche | Source (début, Europe) | Nature | Usage |
|---|---|---|---|
| Trait de côte | GSHHG pleine résolution (déjà en place) | vecteur | masque terre, fond |
| Bathymétrie | EMODnet Bathymetry (modèle numérique de terrain, environ 115 m) | raster | contexte, qualification d'un arrêt, isobathes, affichage hors ligne |
| Infrastructures | EMODnet Human Activities (câbles sous marins, pipelines) | vecteur lignes | corridors à risque, scénario infrastructures |
| Gros ports | World Port Index (mondial, domaine public) | vecteur points | repères, contexte |

Couches opérationnelles prévues ensuite, hors début : aires protégées (Natura 2000, WDPA), limites de
ZEE et d'eaux territoriales (Marine Regions), rails de navigation, mouillages officiels, fond de carte
hors ligne.

### 4.2 Couches d'affichage (provisionnées, hors ligne)

Téléchargées et découpées par région comme les couches opérationnelles, stockées en local, puis
stylisées par nous et rendues dans MapLibre. Activables par l'opérateur sur la région active, avec
légende et attribution. Hors ligne, sans dépendance au service EMODnet.

| Overlay | Source (téléchargement) | Contenu | Rôle |
|---|---|---|---|
| Bathymétrie | EMODnet Bathymetry, raster déjà provisionné (voir 4.1) | profondeur, isobathes | fond bathymétrique |
| Activités humaines | EMODnet Human Activities (WFS vecteurs, WCS rasters) | parcs éoliens, densité de trafic, câbles, pipelines, extraction, dragage | vue du contexte anthropique |

Note : la bathymétrie s'affiche à partir du raster déjà provisionné (même donnée que les règles). Les
activités humaines sont à la fois provisionnées pour la part utile aux règles (câbles, pipelines) et
provisionnées plus largement pour l'affichage (parcs éoliens, densité de trafic, etc.). Un aperçu WMS
d'EMODnet reste possible en secours, mais ce n'est pas le mode par défaut.

## 5. Le provisionneur

Généralisation de `build_masks.py` : une commande qui, pour une région donnée, parcourt un catalogue
de fournisseurs et pour chacun télécharge, découpe sur l'emprise de la région, insère, de façon
idempotente.

- Entrée : identifiant de région (prédéfinie) ou emprise tracée.
- Chaque fournisseur est un module autonome qui sait récupérer et découper sa couche pour un bbox.
- Avant téléchargement, le provisionneur **estime l'empreinte disque** et la présente, pour garder la
  main sur le poids.
- Idempotent : relançable pour rafraîchir ; écriture atomique des fichiers (fichier temporaire puis
  remplacement), comme pour le cache SAR.
- Écrit un **manifeste de provenance** par région.

## 6. Manifeste de provenance

Pour chaque couche d'une région : source, URL, licence, date de téléchargement, version du jeu,
empreinte disque, et nombre d'objets ou dimensions du raster. C'est la traçabilité appliquée à la
donnée de référence, et c'est ce qui règle le respect des licences et attributions (Marine Regions,
GSHHG, EMODnet, World Port Index ont chacun leurs exigences).

## 7. Modèle de données

- Table `regions(id, name, origin, geom, created_at)` : `origin` vaut `iho` ou `manuelle`, `geom` en
  polygone 4326.
- Table `region_layers(region_id, layer, source, source_version, license, fetched_at, size_bytes,
  feature_count, status)` : l'état de provisionnement, interrogeable, qui alimente l'interface.
- Tables de référence vectorielles taguées par région : la table `land` existante gagne un
  `region_id` ; nouvelle table `infrastructure(region_id, kind, name, operator, status, geom)` ;
  table `ports(region_id, name, geom, attrs)`.
- Rasters (bathymétrie) en fichiers `data/zones/<id>/bathymetry.tif`, échantillonnés à la volée par
  rasterio comme on lit déjà le SAR. Les isobathes d'affichage peuvent être précalculés en vecteur.
- La **marge des corridors d'infrastructure n'est pas figée en base** : elle s'applique à la requête
  (`ST_DWithin`), paramétrée dans `config/rules.yaml` et versionnée comme les autres seuils.

## 8. Interface de réglages

- Liste cherchable des mers européennes (OHI), avec pour chacune un interrupteur de provisionnement
  et un statut : non provisionnée, en téléchargement, prête, taille, dernier rafraîchissement.
- Bouton « tracer une région » pour le manuel.
- Vue de détail par région : couches disponibles et leur provenance (depuis `region_layers`).
- Dans le panneau Couches, des interrupteurs d'overlays d'affichage EMODnet (bathymétrie,
  activités humaines) pour la région active, servis depuis la donnée provisionnée en local, avec
  leur légende et l'attribution.
- Estimation d'empreinte avant tout téléchargement.
- Pour le début, un `config/zones.yaml` plus une commande de provisionnement suffisent ; la page de
  réglages complète peut venir ensuite.

## 9. Région active

La région active borne tout le reste : centrage et limites de la carte, emprise de l'import ou du
flux AIS, recherche des passages Sentinel 1, couches de référence affichées. Changer de région, c'est
changer de théâtre. Une seule région active à la fois au début.

## 10. Tranche verticale de départ

Fidèle au principe du projet (tranche verticale d'abord) :

- **Une seule région réelle** : la zone déjà chargée, qui correspond aux mers OHI Kattegat et
  Skagerrak.
- **Trois fournisseurs** : trait de côte (déjà en place), infrastructures (EMODnet), bathymétrie
  (EMODnet).
- Bout en bout : définition de la région, provisionnement, manifeste, couches activables dans
  l'interface. On prouve le mécanisme, puis on déroule la liste des mers européennes et on ajoute les
  fournisseurs suivants (ports, aires protégées).

## 11. Garde fous

- **Licences et attribution** tracées au manifeste et respectées.
- **Qualité uniforme** garantie par le choix Europe et EMODnet ; la question de la qualité variable
  ne se posera qu'en sortant d'Europe.
- **Empreinte disque** maîtrisée par la taille de région et l'estimation préalable.
- **Séquencement** : ce chantier est de la plomberie habilitante. L'évaluation xView3 reste le socle
  de crédibilité convenu et le prochain livrable chiffré ; les régions et le provisionnement
  viennent après, comme fondation du live et des scénarios infrastructures.
