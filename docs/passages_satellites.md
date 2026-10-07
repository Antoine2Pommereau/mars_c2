# Calendrier des passages Sentinel 1 et Sentinel 2

Étape 3, lot A : savoir quand un satellite passe au dessus de nos quatre régions (Bretagne, Manche, Gascogne,
Méditerranée), ce que couvre son emprise (infrastructures, navires des listes), et préparer ainsi le déclenchement
automatique des analyses (lots B et C). Aucune image n'est téléchargée : seulement des métadonnées.

Code : `mars/satellites.py` (lecture, regroupement, base), `backend/satellites.py` (routes), tâche quotidienne
`passages` du conteneur taches. Mesures du 07/10/2026.

## Sources retenues

| Source | Ce qu'elle donne | Accès | Volume |
|---|---|---|---|
| Catalogue STAC de Copernicus Data Space, `https://stac.dataspace.copernicus.eu/v1/search`, collections `sentinel-1-grd` et `sentinel-2-l1c` | Passages **acquis** : un produit par tranche de 25 s (Sentinel 1) ou par tuile de 110 km (Sentinel 2), avec emprise, heures, satellite, orbites relative et absolue, sens de l'orbite, mode, identifiant de prise de vue | Public, sans compte ; recherche par emprise et par date ; réponse limitée aux champs utiles (`fields`) | 30 jours sur nos régions : 484 produits Sentinel 1 et 1 866 Sentinel 2, en 32 s ; chaque jour (3 jours relus) : environ 250 produits |
| Plans d'acquisition de l'ESA, pages `https://sentinels.copernicus.eu/copernicus/sentinel-1/acquisition-plans` et `.../sentinel-2/acquisition-plans` | Passages **prévus** : un fichier KML par satellite, de 18 à 20 jours, avec l'emprise de chaque prise de vue, ses heures, son mode, ses orbites | Public, sans compte ; la page liste les fichiers ; on prend pour chaque satellite le plus récent qui couvre encore l'avenir | Environ 2 Mo par fichier, cinq fichiers (S1C, S1D, S2A, S2B, S2C), lus en mémoire, rien n'est écrit sur le disque |

Écartés : Sentinel Hub (déjà utilisé pour l'extraction radar, mais demande un compte et compte des unités de
traitement), le protocole OData du même catalogue (équivalent, moins pratique pour les champs), et le calcul
d'orbite à partir des éléments orbitaux (il donne la trace, pas ce que le satellite va réellement acquérir).

## Regroupement en passages

Un passage est réuni sous la clé **satellite et orbite absolue** (`S1D_OR4898`, `S2C_OR10903`) : une orbite ne
traverse nos régions qu'une fois, et cette clé est la même dans le plan et dans le catalogue. Quand le catalogue
publie le passage, la ligne prévue devient « acquis » (heures et emprise réelles).

L'identifiant de prise de vue de Sentinel 1 ne convient pas comme clé : il est **réattribué à l'exécution** (77888 au
plan, 77889 au catalogue, sauf pour les plans publiés la veille). Il est gardé pour information. Le plan le donne en
hexadécimal, le catalogue en décimal.

L'emprise est découpée sur l'union des régions (les régions sont des rectangles : ils comprennent un peu de terre)
et simplifiée à environ 1 km. Les infrastructures couvertes sont celles qui touchent l'emprise (un tracé commun à
Bretagne et Manche n'est compté qu'une fois) ; les navires des listes, ceux qui y ont une position à 30 minutes près
de l'acquisition (seulement pour un passage passé).

## Fiabilité mesurée

Plans du 20/09 au 07/10/2026 confrontés au catalogue (passages prévus et déjà passés, qui touchent nos régions) :

| Satellite | Prévus | Retrouvés au catalogue |
|---|---|---|
| Sentinel 1C | 28 | 27 |
| Sentinel 1D | 28 | 28 |
| Sentinel 2A | 13 | 13 |
| Sentinel 2B | 8 | 8 |
| Sentinel 2C | 14 | 14 |

**Heure prévue.** Un plan Sentinel 1 donne le début et la fin de toute la prise de vue, souvent commencée plusieurs
minutes avant nos côtes (écart médian de 5,5 min, 9 min au plus). L'heure d'entrée et de sortie de nos régions est
donc estimée par interpolation en latitude le long de la bande : l'écart au catalogue tombe à **0,2 min en médiane,
2 min au plus**. Pour Sentinel 2, le catalogue date chaque tuile au début de la prise de vue : l'heure du plan, prise
telle quelle, le recoupe à la seconde (médiane 0,1 min), mais le satellite survole nos côtes quelques minutes plus tard
selon la latitude.

## Limites

* **Constellation Sentinel 1.** Sentinel 1A n'apparaît plus au catalogue depuis le 29/06/2026 ; la constellation est
  désormais **Sentinel 1C et 1D** (1D opérationnel depuis la mi avril 2026, sur le scénario de 1C). Les pages de
  l'ESA listent encore d'anciens plans de 1A : seuls les plans en vigueur sont lus, et un satellite sans plan en
  cours n'apparaît simplement pas. Une nouvelle unité (1E) serait prise en compte sans changer le code, par son nom de
  fichier et son code de plateforme.
* **Horizon variable.** Les plans couvrent de 5 à 20 jours selon la date de leur dernière publication (le 07/10,
  Sentinel 2B n'allait que jusqu'au 12/10). Le calendrier s'arrête donc plus tôt pour certains satellites.
* **Sens de l'orbite des passages prévus Sentinel 1** : absent du plan, déduit de l'heure (orbite crépusculaire,
  descendante vers 6 h, ascendante vers 18 h ; nos régions sont proches du méridien de Greenwich). Les plans
  Sentinel 2 sont toujours descendants (côté jour).
* **Délai du catalogue.** Un produit est publié quelques heures après l'acquisition ; la mise à jour est quotidienne.
  Un passage peut donc rester « prévu » jusqu'à un jour après l'heure. Un passage prévu jamais confirmé est retiré
  deux jours après son heure (replanification, panne, prise de vue annulée).
* **Replanifications.** L'ESA republie un plan quand le programme change (urgences, manœuvres) ; la mise à jour
  quotidienne relit le plus récent, sans garder l'historique des versions.
* **Nuages.** Le plan Sentinel 2 ne dit rien de la couverture nuageuse ; une image optique peut être inutilisable.
  Le catalogue donne `eo:cloud_cover` : à lire au lot B pour ne pas analyser une image couverte.
* **Modes.** Sentinel 1 est en IW sur nos régions, avec quelques prises de vue EW au large (signalées par le mode) ;
  le radar ne voit pas sous un même angle d'un passage à l'autre (orbite relative).
* **Conditions d'utilisation.** Catalogue et plans sont publics ; l'usage des données Copernicus est libre, avec
  mention de la source.

## Volume en base

258 passages (30 jours acquis et 20 jours prévus) occupent 896 Ko index compris, soit 3,5 Ko par passage et environ
35 Ko par jour (13 Mo par an). Négligeable devant les positions (7,5 Go à l'équilibre).
