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
    suivis: "Navires suivis", recherche: "Recherche",
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
    nuitDe: (d: string, g: number, n: number, e: number) => `${d}, ${g} granules${e ? ` dont ${e} en échec` : ""}, ${n} détections`,
    etatsTravailleur: { demande: "demandé", cree: "créé", demarre: "en cours", resultats: "résultats reçus",
      termine: "terminé", echec: "en échec" } as Record<string, string>,
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
    passage: (sat: string, t: string, prevu: boolean) => `${sat}, ${t}${prevu ? ", prévu" : ""}`,
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
  sources: { gur: "Catalogue GUR", opensanctions: "OpenSanctions", fiche: "fiche" },

  // Lignes du fil et preuves
  preuves: {
    navire: "Navire", navire1: "Navire 1", navire2: "Navire 2", signal: "Signal", dansNosEaux: "Dans nos eaux",
    zones: "Zones", positions: "Positions", sources: "Sources", nom: "Nom", omi: "OMI", depuis: "Depuis",
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
    intensite: "Intensité", lune: "Éclairement lunaire", infrastructure: "Infrastructure proche",
    navireListe: "Navire des listes proche",
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
      viirs: "Détection", apparie: "Navire AIS apparié",
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
    zone: { surface: "Surface", reception: "Réception fiable", mouillages: "Mouillages connus", navires: "Navires à l'instant",
      alertes: "Alertes de la plage", cellules: (n: number, km2: number, c: string) => `${n} cellules, ${km2} km², continuité ${c} %`,
      aucuneReception: "pas encore calculée" },
    navireSansNom: "Navire sans nom", mmsi: "MMSI", omi: "OMI", pavillon: "Pavillon", indicatif: "Indicatif",
    type: "Type", longueur: "Longueur", destination: "Destination", vitesse: "Vitesse", route: "Route",
    dernierMessage: "Dernier message", nonRenseigne: "non renseigné", ilYaMin: (m: string) => `il y a ${m} min`,
    regles: (v: string, t?: string) => `Règles ${v}${t ? `, alerte levée le ${t}` : ""}`, sansNom: "sans nom",
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
      detections: "Détections radar", viirs: "Détections nocturnes VIIRS", chaleur: "Cartes de chaleur",
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
