# Historique danois : la version de rejeu et sa calibration (juin à octobre 2026)

Brouillon proposé par l'audit du 06/10/2026 (`docs/audit_code.md`, section 5). Ce document rassemble ce que
CLAUDE.md contenait sur la première version du projet : rejeu de journées AIS danoises et analyse radar à la
demande. Le code de cette version reste la base technique de la plateforme française ; seules les données, les cas
et les valeurs de calibration sont propres au Danemark. Contenu repris de CLAUDE.md sans modification de fond.

## 1. La version initiale

**MARS C2** fusionnait deux sources : l'**AIS**, déclaratif et falsifiable, et l'**imagerie radar Sentinel 1**,
indépendante du navire. Question opérationnelle : **quels navires sont en mer sans le déclarer, et que font ceux qui
se déclarent ?** Le rejeu d'une journée AIS passée, horloge simulée en base, permettait de lancer une analyse radar
sur un passage du satellite et de confronter les échos aux positions déclarées.

## 2. Données

* **AIS** : Danish Maritime Authority (aisdata.ais.dk), Skagerrak et Kattegat (`--bbox 8.5 56.0 13.0 58.6`).
  Journées chargées : **5 juin 2024** (17,2 M messages lus, 2,45 M doublons, 4,44 M positions) et **17 juin 2024**
  (20,8 M lus, 2,95 M doublons, 535 MMSI invalides, 4,70 M positions). Un tiers de doublons, structurellement
  (plusieurs stations côtières). Décompresser les archives avec `ditto -x -k` (Safari les décompresse parfois seul).
* **Radar** : Sentinel 1A, passages ascendants à 17 h 02 UTC les 5 et 17 juin (même orbite, 12 jours d'écart), via
  Sentinel Hub (CDSE). Autres passages de juin 2024 disponibles mais sans AIS chargé.
* **Zones de test** : large Skagen `10.30 57.80 10.80 58.07`, mouillage de Skagen `10.45 57.58 10.85 57.80`, parc
  éolien d'Anholt `11.05 56.52 11.35 56.70`.
* Import : `python scripts/import_ais.py --csv data/ais/aisdk-2024-06-05.csv --bbox 8.5 56.0 13.0 58.6` (lecteur
  `mars/ais/dma.py`, proposé à l'archivage dans la branche `archive/danemark`).

## 3. Enseignements de la calibration, cas par cas

**Masques** (dans l'ordre) : terre GSHHG à 500 m ; contraste VV sous 10 dB (en phase 0, le grain de mer sur mer
agitée obtenait des scores supérieurs aux vrais navires, avec un contraste de 4 à 5 dB contre 22 à 44 dB) ; score
navire sous 0,338 ; écho fixe (persistance).

**Rendez vous** (`RENDEZVOUS`) : 9 736 épisodes bruts le 5 juin, presque tous au port ; Natural Earth ratait les
îles (faux rendez vous au port de Sejerø), d'où GSHHG ; exclure le statut « amarré » effaçait les transbordements
bord à bord de SILVER KENNA (navire avitailleur, deux rencontres de plus de 9 h) ; SSI GLORIOUS se déclarait « en
route » pendant 9 h 30 bord à bord ; au mouillage de Skagen, déclasser en sévérité faible plutôt qu'exclure ; seuil
côtier abaissé de 5 à 3 km (le mouillage s'étend de 4 à 8 km au large). Résultat : 9 alertes le 5 juin, 11 le
17 juin.

**Coupure AIS** (`AIS_GAP`) : un MMSI fantôme (un seul message, code pays 506, Myanmar), d'où l'exigence de 10
messages dans l'heure ; test par injection à 0 sur 5 parce que la zone fiable mesurait la densité du trafic
(3 700 km²), corrigé par la continuité des trajectoires (34 800 km² sur deux journées) ; ferries partis vers Oslo ou
la Norvège puis revenus (PEARL SEAWAYS, BERGENSFJORD), d'où la projection à l'estime portée à 2 h ; un navire qui
poursuit sa route n'est pas suspect, un navire qui s'arrête l'est ; partenaires de circonstance sur les zones de
pêche, d'où le rayon de 500 m autour des extrémités et 30 min de lenteur. Résultat : 3 alertes le 5 juin, 8 le
17 juin ; **test par injection 5 sur 5**.

**Position AIS non confirmée** (`AIS_UNCONFIRMED`) : elle a révélé que le seuil de 0,30 manquait des navires géants
pourtant nets sur l'image (ARCTIC AURORA, 288 m, score 0,29), d'où le balayage du seuil.

**Persistance** : 107 des 111 éoliennes d'Anholt reconnues à partir de deux passages, sans base externe ; l'alerte
erronée du 5 juin (éolienne de 50 m, 22 dB) a été reclassée automatiquement.

## 4. Résultats mesurés sur les journées danoises

| Mesure | Valeur |
|---|---|
| Décalage Doppler, navires en route | 410 à 494 m le long de la trace, 42 à 117 m en travers |
| Décalage, navires au mouillage | 19 m le long, 40 à 63 m en travers |
| Balayage du seuil (mouillage de Skagen) | appariées 14, 16, 17, 17, 18 et non confirmées 5, 4, 2, 2, 0 pour 0,30, 0,25, 0,20, 0,15, 0,10 ; 0,10 explose la latence (572 candidats) |
| Test par injection des coupures | 5 sur 5 |
| Persistance à Anholt | 107 échos fixes sur 111 éoliennes |

## 5. Points restés ouverts au Danemark

* SEA HAWK (17 juin) a perdu son partenaire HG35 VENDELBO (4 m du trajet présumé, loin des extrémités) avec le
  critère resserré : compromis assumé.
* Beaucoup de coupures du 17 juin sont des navires de pêche (immatriculations HG, HM, S) sur leurs zones de pêche :
  pas le schéma du transbordement, mais pertinent (AIS obligatoire au delà de 15 m en Europe). Piste reprise pour
  la France : un contexte « pêche » dédié.
* Les zones de mouillage s'étendent avec les journées chargées : des rendez vous du 5 juin sont passés en sévérité
  faible (dont XANTHIA et VINGAREN). Comportement voulu, à expliquer à l'opérateur.
* AIDANOVA (337 m, score 0,13) et MAERSK INVOLVER (138 m, 0,13) restent sous le seuil : le mode complet devrait les
  voir.
* 4 éoliennes d'Anholt non reconnues (une troisième date les rattraperait).
* Le seuil de 0,20 et le contraste de 10 dB n'ont pas été validés sur une vérité terrain (ancienne phase 6,
  évaluation sur xView3, devenue non prioritaire).

## 6. Historique des phases

* **Phase 0** : validation des hypothèses (reproduction exacte du script officiel, mode rapide, latence sur le Mac,
  harmonisation Sentinel Hub, filtre de contraste, premier candidat navire sombre de 43 m au nord de Skagen).
* **Phase 1** : tranche verticale (une commande, le navire sombre sur la carte).
* **Phase 2** : rejeu AIS par horloge en base, 1 000 navires à 60 fois le temps réel.
* **Phase 3** : analyse à la demande, service d'inférence natif, progression en direct.
* **Phase 4** : masques, quatre types d'alertes calibrés sur deux journées, tolérance Doppler orientée, seuil
  abaissé avec fusion des fragments, persistance des échos fixes.
* **Phase 5** : interface React (design épuré, rail, fil d'alertes, frise, fiche avec vignette radar, décisions des
  opérateurs, lancement d'analyse depuis la carte, historique, filtres, mode focus), servie sur le port 8080.
* **Octobre 2026** : pivot vers la France en direct (voir CLAUDE.md).
