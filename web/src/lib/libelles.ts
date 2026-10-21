// Tous les libellés de l'interface, en français. Une traduction remplace ce fichier en entier (même structure) :
// les composants n'écrivent aucun texte visible en dur. Sans tiret dans les textes, dates au format 17/06/2024.

export const L = {
  app: { nom: "MARS C2" },
  commun: {
    nd: "n.d.", inconnu: "inconnu", sansNom: "Sans nom", fermer: "Fermer", annuler: "Annuler", aVenir: "à venir",
    etape: (n: number | string) => `étape ${n}`, toutes: "Toutes", tous: "Tous", oui: "oui", non: "non",
    noeuds: "nœuds", m: "m", km: "km", min: "min", s: "s", chargement: "Chargement…",
    reconnexion: "Reconnexion…",
  },

  // Étapes du plan, pour les éléments « à venir » des registres
  etapes: { "en place": "en place", recalibration: "après recalibration", reporte: "reporté", "lot 2": "prochain lot",
    pilote: "pilote GeoTrackNet", "3": "étape 3", "4": "étape 4" } as Record<string, string>,
  carte: { infrastructure: "Infrastructure", indisponible: "Carte indisponible : le navigateur ne prend pas en charge WebGL" },

  // Rail et panneaux
  rail: {
    alertes: "Alertes", analyses: "Analyses satellites", couches: "Couches et légende",
    suivis: "Navires suivis", recherche: "Recherche", observations: "Observations satellites", controle: "Contrôle",
  },
  // Panneau de contrôle (lot P)
  controle: {
    titre: "Contrôle", regles: (v: string) => `règles ${v}`,
    sections: { connexion: "Opérateur", capteurs: "Capteurs", depenses: "Dépenses", demande: "Demande d'observation",
      journal: "Journal" } as Record<string, string>,
    operateur: "Opérateur", motDePasse: "Mot de passe", seConnecter: "Se connecter", deconnexion: "Déconnexion",
    nonConfigure: "Aucun opérateur configuré (MARS_OPERATEURS)", connexionRequise: "Connexion requise pour agir",
    enService: "en service", enPause: "en pause", depuis: (t: string) => `depuis ${t}`, reprise: (t: string) => `reprise ${t}`,
    pause: "Mettre en pause", reprendre: "Reprendre", motif: "Motif", repriseLe: "Reprise le (facultatif)",
    confirmer: "Confirmer", annuler: "Annuler",
    plafond: "Scènes par jour", moins: "Une scène de moins", plus: "Une scène de plus", plafondDefaut: (n: number) => `fichier : ${n}`, appliquer: "Appliquer",
    budget: "Budget Sentinel du mois", depense: "dépensé", reserve: "réservé", restant: "restant",
    hors: "hors budget", derniers: "Derniers travailleurs", coutEstime: "estimé",
    lon: "Longitude", lat: "Latitude", rayon: "Rayon (m)", demander: "Demander",
    demandeFaite: (id: number) => `Demande ${id} en attente`,
    journalVide: "Aucune action", actions: {
      pause: "pause", reprise: "reprise", plafond: "plafond", demande: "demande", connexion: "connexion",
      connexion_refusee: "connexion refusée", deconnexion: "déconnexion", malgre_pause: "malgré la pause",
    } as Record<string, string>,
  },
  // Panneau « Observations » (tip and cue, lot T1)
  observations: {
    titre: "Observations", point: "point", filtre: "État", aucune: "Aucune demande d'observation",
    filtres: { toutes: "Toutes", planifiee: "Planifiées", en_attente: "En attente", analysee: "Analysées", closes: "Closes" },
    etats: { en_attente: "en attente", planifiee: "planifiée", analysee: "analysée", sans_passage: "sans passage",
      expiree: "expirée", refusee_budget: "refusée", annulee: "annulée" } as Record<string, string>,
    closes: (n: number) => `${n} sans résultat`,
  },

  // Barre d'état
  etat: {
    titre: "État de la plateforme",
    flux: "Flux AIS", ingestion: "Ingestion", listes: "Listes", disque: "Disque", archivage: "Archivage",
    satellites: "Satellites",
    niveau: { vert: "normal", orange: "à surveiller", rouge: "anomalie", gris: "sans mesure" },
    dernierMessage: "Dernier message", debit: "Débit", parMinute: "par minute", navires: "Navires vus en 1 h",
    retard: "Retard de l'ingestion", dernierFichier: "Dernier fichier chargé", fichiersHeure: "Fichiers en 1 h",
    conserves: "Positions conservées en 1 h", importeLe: "Importée le", aucuneListe: "Aucune liste importée",
    verifieeLe: "vérifiée le", echecListe: (d: string) => `mise à jour en échec le ${d}, liste précédente conservée`,
    attribution: "Données OpenSanctions.org, licence CC BY NC 4.0",
    libre: "Libre", mesure: "Mesure du", perimee: "mesure ancienne", archive: "Dernier archivage",
    sauvegarde: "Dernière sauvegarde", regles: "Dernier cycle des règles", echec: "en échec",
    ilYa: (t: string) => `il y a ${t}`, dans: (t: string) => `dans ${t}`,
    prochain: (m: string) => `Prochain ${m === "S1" ? "Sentinel 1" : "Sentinel 2"}`, dernierAcquis: "Dernier passage acquis",
    calendrier: "Calendrier mis à jour le", nuitViirs: "Dernière nuit VIIRS", travailleur: "Dernier travailleur",
    motif: "Motif", lancement: "Dernier lancement VIIRS",
    sentinel: "Dernier passage Sentinel 1 analysé", travailleurGpu: "Dernier travailleur GPU", lancementSentinel: "Dernier lancement Sentinel 1",
    sentinelDe: (sat: string, le: string, etat: string, n: number) => `${sat}, ${le}, ${etat}, ${n} détections`,
    coutMois: "Coût Sentinel du mois", euros: (e: number) => `${e.toFixed(2).replace(".", ",")} €`,
    sentinel2: "Dernier passage Sentinel 2 analysé",
    sentinel2De: (sat: string, le: string, resultat: string, n: number) => `${sat}, ${le}, ${resultat}, ${n} navires`,
    resultatsOptique: { analysee: "analysé", nuages: "sous les nuages", sans_detection: "aucun navire" } as Record<string, string>,
    coutParSource: (radar: number, optique: number) =>
      `radar ${radar.toFixed(2).replace(".", ",")} €, optique ${optique.toFixed(2).replace(".", ",")} €`,
    budgetPartage: "Budget partagé du mois",
    nuitDe: (d: string, g: number, n: number, e: number) => `${d}, ${g} granules${e ? ` dont ${e} en échec` : ""}, ${n} détections`,
    etatsTravailleur: { demande: "demandé", cree: "créé", demarre: "en cours", resultats: "résultats reçus",
      termine: "terminé", echec: "en échec" } as Record<string, string>,
    version: "Version", differente: "version différente de l'interface",
    analyseSentinel: "Analyse Sentinel 1",
    demandes: "Demandes d'observation", prochaineObservation: "Prochaine observation",
    demandesDe: (attente: number, planifiees: number) => `${attente} en attente, ${planifiees} planifiée${planifiees > 1 ? "s" : ""}`,
    observationDe: (sat: string, quand: string, objet: string) => `${sat.replace(/^S(\d)/, "Sentinel $1")} ${quand}, ${objet}`,
    tipCoupe: "tip and cue coupé",
    attenteGpu: (dans: string) => `GPU indisponible chez Scaleway, nouvel essai dans ${dans}`,
    services: { interface: "Interface", api: "API", taches: "Tâches", ingestion: "Ingestion", collecte: "Collecte" } as Record<string, string>,
    travailleurDe: (etat: string, type: string, le: string, s: number | null, eur: number | null) =>
      `${etat}, ${type}, ${le}${s != null ? `, ${Math.round(s / 60)} min d'analyse` : ""}${eur != null ? `, ${eur.toFixed(3).replace(".", ",")} €` : ""}`,
  },
  region: { titre: "Région affichée", france: "Toute la France" },

  // Recherche (contenu au lot 2)
  recherche: {
    titre: "Recherche", placeholder: "Navire, ancien nom, MMSI, OMI, infrastructure, alerte, lieu",
    raccourci: "⌘ K", groupes: { navires: "Navires", infrastructures: "Infrastructures", alertes: "Alertes", lieux: "Lieux" } as Record<string, string>,
    aucun: "Aucun résultat", saisir: "Au moins deux caractères, ou un numéro (MMSI, OMI, alerte)",
    par: { mmsi: "MMSI", omi: "OMI", nom: "nom", ancien_nom: "ancien nom" } as Record<string, string>,
    ancienNom: (n: string) => `ancien nom ${n}`, alerte: (id: number) => `Alerte n° ${id}`, silencieux: "sans position récente",
    aide: "↑ ↓ pour choisir, Entrée pour ouvrir",
  },
  suivis: {
    titre: "Navires suivis", aucun: "Aucun navire suivi. Le bouton « Suivre » d'une fiche navire l'ajoute ici.",
    suivre: "Suivre", nePlusSuivre: "Suivi", depuis: (d: string) => `suivi depuis le ${d}`,
    dernierePosition: "Dernière position", derniereAlerte: "Dernière alerte", aucuneAlerte: "aucune alerte",
  },

  // Frise et temps
  frise: {
    direct: "Direct", plage: "Plage", rejeu: "Rejeu", lecture: "Lecture", pause: "Pause",
    libre: "Plage libre", appliquer: "Appliquer", du: "Du", au: "au",
    presets: { "1h": "1 h", "6h": "6 h", "24h": "24 h", "7j": "7 j", "30j": "30 j" } as Record<string, string>,
    vitesse: "Vitesse du rejeu", instant: "Instant affiché", densite: "Navires suivis",
    pistes: { alertes: "Alertes", coupures: "Coupures du flux AIS", passages: "Passages satellites", viirs: "Nuits VIIRS" },
    coupure: (debut: string, fin: string) => `Flux AIS coupé de ${debut} à ${fin}`,
    passage: (sat: string, t: string, prevu: boolean, analyse?: string, demandes?: number, nuages?: number | null,
              resultat?: string) =>
      `${sat}, ${t}${prevu ? ", prévu" : ""}${nuages != null ? `, ${Math.round(nuages)} % de nuages annoncés` : ""}${
        analyse && analyse !== "non_analyse" ? `, analyse ${analyse}` : ""}${resultat === "nuages" ? ", fenêtres sous les nuages" : ""}${
        demandes ? `, ${demandes} demande${demandes > 1 ? "s" : ""} d'observation` : ""}`,
    nuit: (d: string, g: number, n: number, sans: number, nonEval: number, lune: number | null) =>
      `Nuit VIIRS du ${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)} : ${g} granules, ${n} détections dont ${sans} sans AIS et ${nonEval} non évaluables${lune != null ? `, lune ${lune} %` : ""}`,
    navires: (n: number) => `${n} navires`, limite: "Au delà de 30 jours : archive R2",
  },

  // Fil d'alertes
  fil: {
    titre: "Alertes", onglets: { todo: "À traiter", confirmed: "Confirmées", all: "Toutes" },
    type: "Type", gravite: "Gravité", vignettes: "Vignettes des navires",
    aucune: { todo: "Aucune alerte à traiter sur la plage.", autre: "Aucune alerte sur la plage." },
    surTotal: (n: number, total: number) => `${n} sur ${total}`,
    groupe: (n: number) => `${n} alertes`,
  },

  gravite: { critique: "critique", elevee: "élevée", moyenne: "moyenne", faible: "faible" } as Record<string, string>,
  statut: { nouvelle: "à traiter", acquittee: "acquittée", confirmee: "confirmée", classee: "classée" } as Record<string, string>,
  zones: { bretagne: "Bretagne", manche: "Manche", gascogne: "Gascogne", mediterranee: "Méditerranée" } as Record<string, string>,

  // Types d'alerte (registre : registres/alertes.tsx)
  alertes: {
    WATCHLIST: "Navire sur liste", IDENTITY_CHANGE: "Changement d'identité", RENDEZVOUS: "Rendez vous suspect",
    AIS_GAP: "Coupure AIS", INFRA_THREAT: "Menace sur infrastructure", DARK_SHIP: "Navire sombre",
    AIS_UNCONFIRMED: "Position AIS non confirmée", IDENTITY_MISMATCH: "Identité contredite par satellite",
    TRAJECTOIRE_ANORMALE: "Trajectoire anormale", PASSAGE_PREVU: "Passage prévu",
  } as Record<string, string>,

  // Niveaux de signal des listes de surveillance
  signal: {
    fort: "Signal fort", sanctionne: "Sanctionné", flotte_fantome: "Flotte fantôme", suspect_gur: "Suspect GUR",
    autre_risque: "Autre risque",
  } as Record<string, string>,
  reconnuPar: {
    omi: "par OMI", mmsi: "par MMSI seul, moins sûr", mmsi_omi_different: "par MMSI, OMI différent",
  } as Record<string, string>,
  sources: {
    fiche: "fiche",
    organismes: {
      gur: "Catalogue GUR (Ukraine)", ue: "Union européenne", gb: "Royaume Uni", us: "États Unis", ca: "Canada",
      ch: "Suisse", au: "Australie", nz: "Nouvelle Zélande", jp: "Japon", ua: "Ukraine", onu: "Nations unies",
      tokyo_mou: "Mémorandum de Tokyo", paris_mou: "Mémorandum de Paris", opensanctions: "OpenSanctions",
    } as Record<string, string>,
    motifs: { sanction: "sanction", flotte: "flotte fantôme", immobilisation: "immobilisation", poi: "point d'intérêt" } as Record<string, string>,
  },

  // Lignes du fil et preuves
  preuves: {
    navire: "Navire", navire1: "Navire 1", navire2: "Navire 2", signal: "Signal", dansNosEaux: "Dans nos eaux",
    zones: "Zones", positions: "Positions", sources: "Sources", nom: "Nom", omi: "OMI", depuis: "Depuis",
    sejour: "Séjour prolongé", sejourDuree: (h: string) => `${h} h dans nos eaux`, sejourArret: "Temps à l'arrêt",
    sejourLieu: "Lieu de l'arrêt", sejourMouillage: "mouillage connu",
    sejourHorsMouillage: (km: string) => `hors de tout mouillage connu, à ${km} km des côtes`,
    sejourHorsMouillageSeul: "hors de tout mouillage connu", sejourTag: (h: string) => `séjour ${h} h`,
    identites: "Identités successives", pavillon: "Pavillon", mmsi: "MMSI", vuDu: "Vu du", au: "au",
    rencontre: "Rencontre", distance: "Distance", distanceCote: "Distance à la côte",
    dernierMessage: "Dernier message", reapparition: "Réapparition", aucuneReapparition: "aucune dans la fenêtre",
    silence: "Silence", receptionZone: "Réception de la zone", partenaire: "Partenaire possible", auPlusPres: "Au plus près",
    lent: "Lent", mouillage: "(mouillage)", longueurEstimee: "Longueur estimée", contraste: "Contraste local VV",
    scorePresence: "Score de présence", scoreNavire: "Score navire", instantPassage: "Instant du passage",
    naviresExamines: "Navires AIS examinés", aucunNavireProche: "Aucun navire AIS à proximité.", tolerance: "Tolérance",
    vitesseDeclaree: "Vitesse déclarée", positionPassage: "Position à l'instant du passage",
    echoProche: "Écho radar le plus proche", aucun: "aucun", contexte: "Contexte", vignette: "Vignette radar",
    sousAutreMmsi: "sous un autre MMSI", pavillonChange: "pavillon changé", militaire: "militaire", militaireRow: "Militaire", puis: "puis", devenu: "devenu",
    silenceDe: (min: number) => `silence de ${min} min`, rencontreDe: (min: number, m: number) => `${min} min, ${m} m au plus près`,
    contrasteDe: (db: string) => `contraste ${db} dB`, sansEcho: (m: string) => `${m} m déclarés, aucun écho`,
    echo: (m: string) => `Écho de ${m} m sans AIS`, mmsiDe: (m: string | number) => `MMSI ${m}`, omiDe: (o: string | number) => `OMI ${o}`,
    deplacement: (km: string) => `, déplacement de ${km} km`, continuite: (n: number, pct: string) => `${n} navires, continuité ${pct} %`,
    toleranceDe: (le: number, tr: number) => `${le} m le long de la trace, ${tr} m en travers`,
    seuil: (v: string) => `seuil ${v}`, ecartee: (r: string) => `Écartée (${r})`, appariee: (n: string) => `Appariée à ${n}`,
    sansAis: "Sans AIS", detection: "Détection radar", statut: "Statut",
    lumiere: "Lumière en mer sans AIS", viirsDe: (nw: string) => `VIIRS, ${nw} nW`, aConfirmer: "à confirmer", capteur: "Capteur",
    radarDe: (m: string) => `radar, ${m} m`, largeurEstimee: "Largeur estimée", orientation: "Orientation de l'écho",
    optiqueDe: (m: string) => `optique, ${m} m`, vu: (m: string) => `Navire de ${m} m vu sans AIS`,
    vitesseEstimee: "Vitesse estimée", typeNavire: "Type", nuagesAutour: "Nuages autour du navire",
    masqueAbsent: "Masque des nuages absent", cap: "Cap mesuré", aucunNavireVu: "aucun navire vu",
    longueurDeclaree: "Longueur déclarée", routeDeclaree: "Route déclarée", ecarts: "Écarts",
    incoherence: "Écho contraire à la déclaration", longueurEcart: "longueur", capEcart: "orientation",
    intensite: "Intensité", lune: "Éclairement lunaire", infrastructure: "Infrastructure proche",
    navireListe: "Navire des listes proche",
  },

  // Preuves images des détections satellites (registre : registres/preuves.ts)
  preuveImage: {
    titre: "Image satellite", alt: "Vignette satellite de la détection", agrandir: "Agrandir l'image",
    aucune: "Pas d'image pour cette détection",
    legende: (n: number, apparie: string | number | null) =>
      `${n ? `${n} AIS à moins de 5 km` : "Aucun AIS à moins de 5 km"}${apparie ? `, apparié : ${apparie}` : ""}`,
    taille: (ko: number) => `${String(ko).replace(".", ",")} Ko`,
    copernicus: "Contient des données Copernicus Sentinel modifiées",
  },

  // Fiche (registre : registres/sections.tsx)
  fiche: {
    sections: {
      motif: "Motif", preuves: "Preuves", contexte: "Contexte", vignette: "Image", decisions: "Décisions",
      listes: "Listes de surveillance", identite: "Identité", identites: "Identités successives", alertes: "Alertes",
      comportement: "Comportement", trajectoire: "Trajectoire", notes: "Notes de l'opérateur",
      risque: "Score de risque", satellite: "Vérification satellite", appris: "Comportement appris",
      prediction: "Trajectoire prédite", mesures: "Mesures", navires: "Navires concernés",
      passes: "Navires passés à moins de 2 milles", liees: "Alertes liées", resume: "Zone", trafic: "Trafic",
      passage: "Passage", infrastructures: "Infrastructures couvertes", listes_couvertes: "Navires des listes couverts",
      viirs: "Détection", apparie: "Navire AIS apparié", sejour: "Séjour prolongé", navire: "Navire",
      observation: "Vérification satellite demandée", contexte_optique: "Contexte, 10 km",
    } as Record<string, string>,
    etat: { route: "en route", immobile: "immobile", silencieux: "silencieux" } as Record<string, string>,
    dernierMessageIlYa: (t: string) => `dernier message il y a ${t}`, horsTrafic: "hors du trafic affiché",
    photo: { source: (s: string) => `Photo : ${s}`, aucune: "Pas de photo", alt: "Photo du navire" },
    comportement: {
      silences: "Silences de plus de deux heures", arrets: "Arrêts au large", passages: "Passages près d'une infrastructure",
      aucun: "Rien de notable sur la plage", silence: (d: string, min: number) => `${d}, ${min} min`,
      arret: (d: string, min: number) => `${d}, ${min} min`, passage: (nom: string, min: number, kn: string, m: number) =>
        `${nom}, ${min} min, ${kn} nœuds au plus lent, ${m} m au plus près`,
    },
    trajectoire: { periode: (a: string, b: string) => `Sur la plage, du ${a} au ${b}`, rejouer: "Rejouer", gpx: "Exporter en GPX",
      carte: "Affichée sur la carte" },
    notes: { ajouter: "Ajouter", placeholder: "Note sur ce navire", aucune: "Aucune note", par: (a: string, d: string) => `${a}, le ${d}` },
    ouvrir: "Ouvrir la fiche",
    infra: { type: "Type", operateur: "Opérateur", longueur: "Longueur", zone: "Zone", source: "Source", sansNom: (t: string, id: number) => `${t} n° ${id}`,
      aucunNavire: "Aucun navire à moins de 2 milles sur la plage", aucuneAlerte: "Aucune alerte liée",
      ligne: (m: number, kn: string, d: string) => `${m} m, ${kn} nœuds au plus lent, ${d}` },
    viirs: {
      titre: "Détection nocturne VIIRS", heure: "Heure", capteur: "Capteur", intensite: "Intensité",
      lune: "Éclairement lunaire", statut: "Statut", cote: "Distance à la côte",
      satellites: { SNPP: "Suomi NPP", NOAA20: "NOAA 20", NOAA21: "NOAA 21" } as Record<string, string>,
      statuts: { avec_ais: "appariée à l'AIS", sans_ais: "sans AIS", ecartee: "écartée", non_evaluable: "non évaluable" } as Record<string, string>,
      aisProche: "AIS le plus proche", aisProcheDe: (m: number, estime: number | null) =>
        `${m} m${estime ? `, estimé sur ${Math.round(estime / 60)} min` : ", interpolé"}`, aucunAis: "aucun navire AIS connu à cette heure",
      reception: "Réception AIS", receptionDe: (n: number) => `${n} navires reçus à moins de 30 km`, motifNonEvaluable: "Non évaluable",
      motifs: { cote: "près de la côte", lumiere_fixe: "lumière fixe" } as Record<string, string>,
      aucunNavire: "Aucun navire AIS compatible à l'heure du passage", ecart: (m: string) => `${m} m`,
    },
    passage: {
      titre: (sat: string) => `Passage ${sat.replace(/^S(\d)/, "Sentinel $1")}`,
      heure: "Heure", a: "à", statut: "État", capteur: "Capteur", orbite: "Orbite", emprise: "Emprise", analyse: "Analyse",
      nuages: "Nuages annoncés",
      statuts: { acquis: "acquis, au catalogue Copernicus", prevu: "prévu, plan d'acquisition de l'ESA" } as Record<string, string>,
      analyses: { non_analyse: "non analysé", pending: "en attente", running: "en cours", done: "terminée", failed: "en échec" } as Record<string, string>,
      sens: { ascending: "ascendante", descending: "descendante" } as Record<string, string>,
      mode: (sat: string, mode: string | null) => `${sat}${mode ? `, mode ${mode}` : ""}`,
      orbiteDe: (rel: number | null, abs: number | null, sens: string) => `relative ${rel ?? "?"}, absolue ${abs ?? "?"}, ${sens}`,
      aucuneInfra: "Aucune infrastructure dans l'emprise", aucunNavire: "Aucun navire des listes dans l'emprise au moment du passage",
      listesApres: "Connus après le passage", toutes: (n: number) => `Afficher les ${n}`,
    },
    radar: {
      titre: "Détection radar Sentinel 1", heure: "Heure de la scène", capteur: "Capteur", score: "Score du modèle",
      longueur: "Longueur mesurée", largeur: "Largeur mesurée", cap: "Orientation de l'écho", vitesse: "Vitesse estimée",
      contraste: "Contraste local VV", statut: "Statut", cote: "Distance à la côte", reception: "Réception AIS",
      motifNonEvaluable: "Non évaluable", capDe: (deg: string, p: string) => `${deg}°, probabilité ${p}`,
      motifs: { terre: "sur la terre", "echo fixe": "écho fixe (revu ailleurs dans le temps)",
                "eolienne probable": "éolienne probable", "contraste faible": "contraste faible",
                "non navire": "non navire", nuages: "sous un nuage" } as Record<string, string>,
      declaree: (m: string) => `${m} m déclarés`, aucunNavire: "Aucun navire AIS compatible à l'heure de la scène",
      // Sentinel 2 (lot S2) : détection optique
      titreOptique: "Détection optique Sentinel 2", heureTuile: "Heure de la tuile", capOptique: "Cap mesuré",
      type: "Type", nuagesFenetre: "Nuages de la fenêtre", masqueAbsent: "masque des nuages absent",
      nuagesDe: (pct: string, absent: boolean) => absent ? "masque des nuages absent" : `${pct} %`,
      types: { cargo: "cargo", tanker: "pétrolier", fishing: "pêche", passenger: "passagers", tug: "remorqueur",
        pleasure: "plaisance", military: "militaire", high_speed: "grande vitesse", unknown: "indéterminé" } as Record<string, string>,
    },
    // Tip and cue (lot T1) : demande d'observation satellite
    observation: {
      demander: "Demander une observation", envoi: "Demande en cours", enregistree: "Demande enregistrée",
      regroupee: "Ajoutée à la demande en cours", aucune: "Aucune demande", annuler: "Annuler",
      etats: { en_attente: "en attente d'un passage", planifiee: "planifiée", analysee: "analysée",
        sans_passage: "sans passage utile", expiree: "expirée", refusee_budget: "refusée (plafond ou budget)",
        annulee: "annulée" } as Record<string, string>,
      tips: { operateur: "demande manuelle", coupure_ais: "coupure AIS", viirs: "lumière VIIRS", rendezvous: "rendez vous",
        watchlist: "navire des listes", identite: "changement d'identité" } as Record<string, string>,
      resultats: { present_apparie: "présent, AIS cohérent", present_sans_ais: "présent sans AIS", absent: "absent",
        non_evaluable: "non évaluable" } as Record<string, string>,
      passage: (sat: string, t: string, retro: boolean) =>
        `${sat.replace(/^S(\d)/, "Sentinel $1")}, ${t}${retro ? ", déjà acquis" : ""}`,
      capteurs: { radar: "radar", optique: "optique" } as Record<string, string>,
      cercle: (km: string, mode: string) => `cercle de ${km} km, ${mode}`,
      modes: { estime: "à l'estime", arret: "navire à l'arrêt", point: "position du signal" } as Record<string, string>,
      distance: (m: number) => `écho à ${m} m de la position prédite`,
      gravite: (avant: string, apres: string) => `gravité ${avant} portée à ${apres}`,
      passageLabel: "Passage", resultat: "Résultat", incertitude: "Incertitude",
    },
    verification: {
      radar: "Radar et optique", viirs: "Lumières VIIRS", aucun: "Aucune détection satellite appariée à ce navire",
      sources: { sentinel1: "radar", sentinel2: "optique" } as Record<string, string>,
      longueurs: (mesuree: string, declaree: string) => `${mesuree} m mesurés, ${declaree} m déclarés`,
      lumiere: (nw: string) => `${nw} nW/cm²/sr`,
    },
    zone: { surface: "Surface", reception: "Réception fiable", mouillages: "Mouillages connus", navires: "Navires à l'instant",
      alertes: "Alertes de la plage", cellules: (n: number, km2: number, c: string) => `${n} cellules, ${km2} km², continuité ${c} %`,
      aucuneReception: "pas encore calculée" },
    navireSansNom: "Navire sans nom", mmsi: "MMSI", omi: "OMI", pavillon: "Pavillon", indicatif: "Indicatif",
    type: "Type", longueur: "Longueur", destination: "Destination", vitesse: "Vitesse", route: "Route",
    dernierMessage: "Dernier message", nonRenseigne: "non renseigné", ilYaMin: (m: string) => `il y a ${m} min`,
    regles: (v: string, t?: string) => `Règles ${v}${t ? `, alerte levée le ${t}` : ""}`, sansNom: "sans nom",
    trace: (id: string) => `Trace ${id}`,
  },

  // Décisions
  decisions: {
    titre: "Décision", statut: (s: string) => `Statut : ${s}`, note: "Note (facultative)",
    acquitter: "Acquitter", confirmer: "Confirmer", classer: "Classer", rouvrir: "Rouvrir l'alerte",
    commenter: "Commenter", commentaire: "Commentaire", auteur: "Auteur", auteurDefaut: "Opérateur",
    motifRequis: "Motif du classement", envoyer: "Enregistrer",
    motifs: { faux_positif: "Faux positif", activite_legitime: "Activité légitime", doublon: "Doublon" } as Record<string, string>,
    verbe: { acquitter: "Acquittée", confirmer: "Confirmée", classer: "Classée", rouvrir: "Rouverte", commenter: "Commentaire" } as Record<string, string>,
    par: (auteur: string, date: string) => ` par ${auteur}, le ${date}`,
  },

  // Couches (registre : registres/couches.ts)
  couches: {
    titre: "Couches et légende", concernees: "Concernées seulement", couleurParType: "Couleur par type",
    groupes: { trafic: "Trafic", infrastructures: "Infrastructures", zones: "Zones", satellites: "Satellites",
      activite: "Activité", predictions: "Prédictions" } as Record<string, string>,
    noms: {
      navires: "Navires", trajectoires: "Trajectoires", telecoms: "Câbles télécoms", electriques: "Câbles électriques et interconnexions",
      pipelines: "Pipelines", eoliens: "Parcs éoliens", corridors: "Corridors de surveillance", couverture: "Couverture",
      mouillages: "Mouillages", reception: "Réception fiable", passages: "Passages Sentinel 1 et 2",
      detections: "Détections radar et optiques", viirs: "Détections nocturnes VIIRS", chaleur: "Cartes de chaleur",
      predictions: "Trajectoires prédites",
    } as Record<string, string>,
    legende: {
      enRoute: "En route", immobile: "Immobile", silencieux: "Silencieux", surListe: "Sur liste de surveillance",
      enAlerte: "Alerte ouverte", avecAis: "Avec AIS", sansAis: "Sans AIS", ecartee: "Écartée",
      acquis: "Acquis", prevu: "Prévu", nonEvaluable: "Non évaluable (AIS absent)",
    },
    passages: (n: number) => `${n} sur la plage`,
    typesNavire: [["#6ea8fe", "Cargo"], ["#f0a35e", "Pétrolier"], ["#5fd38d", "Pêche"], ["#c792ea", "Passagers"],
      ["#f5e663", "Plaisance, voile"], ["#e07a5f", "Service"], ["#9fb3c2", "Autre ou non renseigné"]] as [string, string][],
    traces: (n: number) => `${n} tracés`, aucuneConcernee: "Aucune infrastructure concernée par la sélection",
    navires: (n: number) => `${n} navires`,
    suivi: "Navire suivi",
  },

  // Analyses radar (panneau existant)
  analyses: {
    titre: "Analyses radar", etapes: { extraction: "Extrait radar", inference: "Détection", fusion: "Fusion avec l'AIS" } as Record<string, string>,
    numero: (id: number, etat: string) => `Analyse n° ${id} ${etat}`, enEchec: "en échec", terminee: "terminée", enCours: "en cours",
    passageDu: (t: string) => `Passage du ${t}`, detections: "Détections", retenues: "Retenues après filtres",
    appariees: "Appariées à l'AIS", sombres: "Navires sombres", nonConfirmees: "Positions non confirmées",
    echosFixes: "Échos fixes reconnus", traiteeEn: (s: string) => `Traitée en ${s} s`, aucune: "Aucune analyse.",
    precedentes: "Analyses précédentes", ligne: (id: number, d: string) => `n° ${id}, ${d}`,
    nAlertes: (n: number) => `${n} alerte${n > 1 ? "s" : ""}`,
    nouvelle: "Nouvelle analyse", tracer: "Cliquez un premier coin de la zone sur la carte, puis le coin opposé.",
    tracerCarte: "Cliquez deux coins opposés, Échap pour annuler", annulerTrace: "Annuler le tracé",
    zoneDe: (w: string, h: string) => `Zone de ${w} × ${h} km`, retracer: "Retracer",
    tropGrande: (km: number) => `Au delà de ${km} km de côté, retracez une zone plus petite.`,
    recherche: "Recherche des passages Sentinel 1…", impossible: (e: string) => `Recherche impossible : ${e}`,
    aucunPassage: "Aucun passage sur cette zone.", sansAisJournee: "Pas d'AIS chargé pour cette journée",
    orbite: { ascending: "ascendant", descending: "descendant" } as Record<string, string>, orbiteInconnue: "orbite inconnue",
    aisCharge: "AIS chargé", sansAis: "sans AIS", lancer: "Lancer l'analyse", lancement: "Lancement…",
  },

  vignette: {
    alt: "Vignette radar autour de l'écho", chargement: "Chargement de l'image radar…",
    indisponible: "Image radar indisponible (service d'inférence arrêté ?)",
    legende: (m: number) => `Sentinel 1, polarisation VV, ${m} m de côté`,
  },
};
