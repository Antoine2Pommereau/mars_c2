# Panneau de contrôle (lot P, 21/10/2026)

Piloter sans terminal les capteurs, les dépenses, les règles, les notifications, les zones de vigilance et un mode
démonstration, avec un état du système en lecture seule. Raison première : maîtriser les coûts (Sentinel 1 et 2 sont
payés à l'heure entamée), puis préparer des démonstrations.

Le panneau s'ouvre depuis l'icône « Contrôle » du rail (curseurs) ; chaque section se replie, et l'état replié est
gardé dans le navigateur.

## Principes

* **L'état du panneau est en base et prime sur `config/rules.yaml`.** On ne modifie plus le fichier sur le serveur
  pour suspendre quoi que ce soit (une modification locale bloque `git pull`). Le conteneur taches lit l'état à chaque
  cycle par `mars/controle.appliquer(conn, rules)` : copie des règles où l'état du panneau est appliqué, avec
  `rules["_controle"]` (pauses) ; les modules (`sentinel`, `sentinel2`, `tip_and_cue`) ne lisent que ce dictionnaire.
* **Les seuils ne s'éditent jamais dans l'interface** : ils restent dans le fichier versionné. Le panneau les affiche
  avec leur version, et ne choisit qu'entre des valeurs et profils définis dans le fichier (section `controle`).
* **Traçabilité** : chaque action est journalisée dans `journal_controle` (qui, quand, quoi, valeur avant et après,
  motif, trace), visible dans le panneau (section Journal) et dans Grafana (tableau Satellites).
* **Accès** : les routes d'écriture exigent une connexion d'opérateur ; les routes de lecture ne changent pas.
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
