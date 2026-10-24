import { demanderConnexion } from "./session";
import { appel } from "./traces";
import type { FC, Feature, Props } from "./types";

async function get<T>(path: string): Promise<T> {
  const r = await appel(`/api${path}`);
  if (!r.ok) throw new Error(`${path} : ${r.status}`);
  return r.json();
}

/** Écriture (POST ou DELETE), toujours en JSON (l'API refuse une écriture sans Content-Type: application/json) et
 *  avec la session de l'opérateur (cookie) ; l'auteur est celui de la session, jamais un champ envoyé. Refus faute de
 *  session (401) : la connexion est demandée, puis l'écriture reprend une fois. Le motif d'un refus (detail) devient
 *  le message de l'erreur. */
async function ecrire<T>(path: string, body: unknown, method: "POST" | "DELETE" = "POST"): Promise<T> {
  const envoyer = () => appel(`/api${path}`, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}),
  });
  let r = await envoyer();
  if (r.status === 401 && !path.startsWith("/controle/connexion") && await demanderConnexion()) r = await envoyer();
  if (!r.ok) {
    let motif = `${r.status}`;
    try {
      const d = (await r.json()).detail;
      motif = typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join(", ") : motif;
    } catch { /* corps non JSON : le code suffit */ }
    throw new Error(motif);
  }
  return r.json();
}
const post = ecrire;

export interface Interrupteur {
  source: string; nom: string; en_pause: boolean; depuis: string | null; jusqu_a: string | null; motif: string | null;
  auteur: string | null;
}
export interface EtatControle {
  version_regles: string;
  interrupteurs: Interrupteur[];
  plafonds: Record<string, { valeur: number; defaut: number; min: number; max: number; regle_par_le_panneau: boolean }>;
  budget: {
    mois: string;
    sources: Record<string, { depense: number; reserve: number; travailleurs: number }>;
    total: { depense: number; reserve: number };
    budget_sentinel: { plafond: number; engage: number; restant: number };
  };
  /** Lot Z6 : coût par résultat utile, sur le mois et depuis le début (échecs compris) */
  cout_par_resultat: Record<"mois" | "total", Record<string, CoutResultat>>;
  travailleurs: Props[];
  rayon_demande_m: { min: number; max: number; defaut: number } | null;
}

interface CoutResultat {
  depense: number; depense_echecs: number; travailleurs: number; aboutis: number; resultats: number; unite: string;
  cout_par_resultat: number | null;
}

/** Échéance d'un secret (lot Z1) : la date seule, relevée chaque jour par taches */
export interface Echeance {
  cle: string; libelle: string; variable: string; expire_le: string | null; illisible: boolean; jours: number | null;
}

export interface SystemeControle {
  grafana: string;
  services: { service: string; dernier_le: string | null; age_s: number | null; version: string | null;
    memoire_mo?: number | null; memoire_max_mo?: number | null; retard_s: number | null }[];
  retard_positions_s: number | null;
  disque: { mesure_le: string; libre_pct: number; libre_octets: number; total_octets: number; base_octets: number | null } | null;
  echecs: { id: number; tache: string; le: string; erreur: string | null; trace_id: string | null; lien_trace: string | null }[];
  echeances?: Echeance[];
  lien_regles: string | null;
}

export interface BlocRegle {
  mode: "actif" | "observation" | "coupe"; mode_fichier: string; mode_panneau: string | null;
  alertes_7j: number; episodes_7j: number; observes_7j: number;
}
export interface RegleControle extends BlocRegle {
  regle: string; nom: string; sous_types: (BlocRegle & { sous_type: string; nom: string })[];
  seuils: [string, unknown][];
}

export interface PrefsNotification {
  alertes: { gravite_min: string; types: Record<string, boolean> };
  tip_and_cue: boolean;
  categories: Record<string, boolean>;
  heures_calmes: { actif: boolean; debut: string; fin: string };
}

/** Dossier d'un navire ou d'un point (lot U, backend/dossiers.py) */
interface Signal { alerte: number; type: string; icone: string; gravite: string; statut: string; famille: string;
  fragment: string; t: string }
export interface Dossier {
  cle: string; genre: "navire" | "point"; nom: string; pavillon: string | null; vessel_id: number | null;
  type_navire: string | null; longueur_m: number | null; lon: number; lat: number;
  niveau: 1 | 2 | 3; niveau_cle: "urgent" | "a_examiner" | "a_noter"; phrase: string; contributions: string[];
  icones: string[]; alertes: number[]; statuts: string[]; signaux: Signal[]; premier: string; dernier: string;
}
export interface ListeDossiers {
  instant: string; fenetre_h: number; compteurs: Record<string, number>; dernier_inhabituel: string | null;
  infrastructures_proches: number[]; dossiers: Dossier[];
}

interface VesselIdentity {
  mmsi: number; name: string | null; imo: number | null; callsign: string | null; flag: string | null;
  first_seen: string; last_seen: string; messages: number;
}

export interface VesselCard {
  id: number; mmsi: number; imo: number | null; name: string | null; callsign: string | null; ship_type: string | null;
  flag: string | null; length_m: number | null; destination: string | null; first_seen: string | null; last_seen: string | null;
  identities: VesselIdentity[];
  watch: { level: string; matched_by: string; entries: { source: string; name: string | null; risks: string[]; url: string | null; par: string }[] } | null;
  alerts: Feature[];
}

interface AlertActionRow { action: string; note: string | null; motif?: string | null; author: string; at: string }

const enc = encodeURIComponent;
/** Paramètre de la région affichée, ajouté à une adresse qui a déjà ou non des paramètres */
const rg = (region: string | null | undefined, sep = "&") => (region ? `${sep}region=${enc(region)}` : "");

export interface Region { key: string; name: string; bbox: [number, number, number, number] }

export interface Timeline {
  debut: string; fin: string;
  densite: { t: string; navires: number | null }[];
  coupures: { debut: string; fin: string }[];
}

interface Resultats { navires: Props[]; infrastructures: Props[]; alertes: Props[] }
interface Comportement { silences: Props[]; arrets: Props[]; passages_infra: Props[] }

export const gpxUrl = (id: number, start: string, end: string) =>
  `/api/vessels/${id}/track.gpx?start=${enc(start)}&end=${enc(end)}`;
export const photoUrl = (id: number) => `/api/vessels/${id}/photo`;


export const api = {
  alertActions: (id: number) => get<AlertActionRow[]>(`/alerts/${id}/actions`),
  act: (id: number, body: { action: string; note?: string; motif?: string }) =>
    post<{ id: number; status: string }>(`/alerts/${id}/actions`, body),
  /** Alertes d'une plage ; l'ancienne API ignore la plage et renvoie tout (filtré ensuite par l'interface) */
  alertsRange: (start: string, end: string, region: string | null, aLInstant?: string | null) =>
    get<FC>(`/alerts?start=${enc(start)}&end=${enc(end)}${rg(region)}${aLInstant ? `&a_l_instant=${enc(aLInstant)}` : ""}`),
  timeline: (start: string, end: string, bins: number, region: string | null) =>
    get<Timeline>(`/timeline?start=${enc(start)}&end=${enc(end)}&bins=${bins}${rg(region)}`),
  traffic: (at: string, region: string | null) => get<any>(`/traffic?at=${enc(at)}${rg(region)}`),
  ingestion: (region: string | null) => get<any>(`/ingestion${rg(region, "?")}`),
  regions: () => get<Region[]>("/regions"),
  search: (q: string, region: string | null) => get<Resultats>(`/search?q=${enc(q)}${rg(region)}`),
  alert: (id: number) => get<Feature>(`/alerts/${id}`),
  comportement: (id: number, start: string, end: string) =>
    get<Comportement>(`/vessels/${id}/comportement?start=${enc(start)}&end=${enc(end)}`),
  notes: (id: number) => get<{ id: number; note: string; author: string; at: string }[]>(`/vessels/${id}/notes`),
  addNote: (id: number, note: string) => post<Props>(`/vessels/${id}/notes`, { note }),
  suivis: (region: string | null = null) => get<Props[]>(`/suivis${rg(region, "?")}`),
  suivre: (id: number) => post<Props>("/suivis", { vessel_id: id }),
  nePlusSuivre: (id: number) => ecrire<Props>(`/suivis/${id}`, {}, "DELETE"),
  infraCard: (id: number, start: string, end: string) => get<Props>(`/infrastructure/${id}?start=${enc(start)}&end=${enc(end)}`),
  zoneCard: (key: string) => get<Props>(`/zones/${key}`),
  analyses: () => get<FC>("/analyses"),
  detections: (id: number) => get<FC>(`/analyses/${id}/detections`),
  alerts: (id: number) => get<FC>(`/alerts?analysis_id=${id}`),
  trails: (at: string, region: string | null) => get<FC>(`/traffic/trails?at=${enc(at)}${rg(region)}`),
  zones: (region: string | null) => get<FC>(`/masks/stationary${rg(region, "?")}`),
  reception: (region: string | null) => get<FC>(`/masks/reception${rg(region, "?")}`),
  /** Tracés simplifiés à environ 50 m : 2,6 Mo en pleine résolution, le détail suffit jusqu'au zoom 12 */
  infrastructure: (region: string | null) => get<FC>(`/infrastructure?tolerance=0.0005${rg(region)}`),
  /** Passages Sentinel 1 et 2 (acquis et prévus) qui chevauchent la plage, sur la région */
  passages: (start: string, end: string, region: string | null) =>
    get<FC>(`/satellites/passes?start=${enc(start)}&end=${enc(end)}${rg(region)}`),
  passage: (id: number) => get<Props>(`/satellites/passes/${id}`),
  /** Détections nocturnes VIIRS de la plage et de la région (avec AIS, sans AIS, écartées) */
  viirs: (start: string, end: string, region: string | null) =>
    get<FC>(`/viirs/detections?start=${enc(start)}&end=${enc(end)}${rg(region)}`),
  viirsDetection: (id: number) => get<Props>(`/viirs/detections/${id}`),
  /** Détections Sentinel 1 de la plage (couche « Détections radar et optiques ») */
  sentinel: (start: string, end: string, region: string | null) =>
    get<FC>(`/sentinel/detections?start=${enc(start)}&end=${enc(end)}${rg(region)}`),
  sentinelDetection: (id: number) => get<Props>(`/sentinel/detections/${id}`),
  /** Vérification satellite d'un navire : échos radar et lumières VIIRS appariés */
  verification: (id: number) => get<{ radar: Props[]; viirs: Props[] }>(`/vessels/${id}/satellite`),
  viirsNuits: (start: string, end: string) => get<Props[]>(`/viirs/nuits?start=${enc(start)}&end=${enc(end)}`),
  track: (vesselId: number, start: string, end: string, maxPoints = 2000) =>
    get<Feature>(`/vessels/${vesselId}/track?start=${enc(start)}&end=${enc(end)}&max_points=${maxPoints}`),
  vessel: (id: number) => get<VesselCard>(`/vessels/${id}`),
  /** Tip and cue (lot T1) : demandes d'observation d'une alerte ou d'un navire, demande manuelle, annulation */
  demandes: (q: { alerte_id?: number; vessel_id?: number }) =>
    get<Props[]>(`/demandes?${q.alerte_id != null ? `alerte_id=${q.alerte_id}` : `vessel_id=${q.vessel_id}`}`),
  demander: (body: { vessel_id?: number; alerte_id?: number; lon?: number; lat?: number }) =>
    post<{ id: number; regroupee: boolean; etat: string }>("/demandes", body),
  annulerDemande: (id: number) => post<Props>(`/demandes/${id}/annuler`, {}),
  /** Panneau « Observations » : demandes de la plage et de la région, compteur des actives */
  observations: (start: string, end: string, region: string | null, aLInstant?: string | null) =>
    get<{ actives: number; demandes: Props[] }>(`/observations?start=${enc(start)}&end=${enc(end)}${rg(region)}${aLInstant ? `&a_l_instant=${enc(aLInstant)}` : ""}`),
  /** Dossiers (lot U) : liste à l'instant, récit, décision appliquée aux alertes ouvertes, devis de vérification */
  dossiers: (instant: string, region: string | null, aLInstant?: string | null) =>
    get<ListeDossiers>(`/dossiers?end=${enc(instant)}${rg(region)}${aLInstant ? `&a_l_instant=${enc(aLInstant)}` : ""}`),
  dossier: (cle: string, instant: string, aLInstant?: string | null) =>
    get<Props>(`/dossiers/${enc(cle)}?end=${enc(instant)}${aLInstant ? `&a_l_instant=${enc(aLInstant)}` : ""}`),
  decisionDossier: (cle: string, body: { action: string; motif?: string; note?: string; instant?: string }) =>
    ecrire<{ alertes: number[]; statut: string }>(`/dossiers/${enc(cle)}/decision`, body),
  decisionsJournal: (limite = 30) => get<{ id: number; at: string; author: string; action: string; motif: string | null;
    note: string | null; alert_id: number; type: string }[]>(`/decisions/journal?limite=${limite}`),
  mesuresDecisions: (jours = 30) => get<Props>(`/decisions/mesures?jours=${jours}`),
  verificationDossier: (cle: string, instant: string) => get<Props>(`/dossiers/${enc(cle)}/verification?end=${enc(instant)}`),
  /** Panneau de contrôle (lot P) : session d'opérateur, état, journal, et écritures (connexion exigée) */
  controle: {
    session: () => get<{ operateur: string | null; configure: boolean; abimes?: string[] }>("/controle/session"),
    connexion: (operateur: string, mot_de_passe: string) =>
      ecrire<{ operateur: string }>("/controle/connexion", { operateur, mot_de_passe }),
    deconnexion: () => ecrire<{ operateur: null }>("/controle/deconnexion", {}),
    etat: () => get<EtatControle>("/controle/etat"),
    journal: (limit = 30) => get<Props[]>(`/controle/journal?limit=${limit}`),
    systeme: () => get<SystemeControle>("/controle/systeme"),
    regles: () => get<{ version_regles: string; empreinte: string; regles: RegleControle[] }>("/controle/regles"),
    notifications: () => get<{ preferences: PrefsNotification; par_le_panneau: boolean; retenues: number;
      heures_calmes_maintenant: boolean; gravites: string[]; categories: string[]; ntfy: boolean }>("/controle/notifications"),
    reglerNotifications: (preferences: PrefsNotification) => ecrire<Props>("/controle/notifications", { preferences }),
    notificationTest: () => ecrire<{ envoyee: boolean }>("/controle/notifications/test", {}),
    zones: (toutes = false) => get<FC & { profils: Record<string, { nom: string; gravite_min?: string | null;
      priorite_plus?: number | null; priorite_sentinel?: number | null; seuils: Record<string, number> }>;
      tampon_m: { min: number; max: number; defaut: number } | null; suivis_prioritaires: boolean }>(`/controle/zones${toutes ? "?toutes=true" : ""}`),
    zone: (id: number) => get<Props>(`/controle/zones/${id}`),
    creerZone: (body: { nom: string; motif: string; profil: string; fin_le?: string | null; geometrie?: unknown;
      infrastructure_id?: number; tampon_m?: number }) => ecrire<{ id: number }>("/controle/zones", body),
    retirerZone: (id: number, motif: string) => ecrire<Props>(`/controle/zones/${id}/retirer`, { motif }),
    suivis: (actif: boolean, motif: string) => ecrire<Props>("/controle/suivis", { actif, motif }),
    demo: () => get<{ masquage: boolean }>("/controle/demo"),
    masquage: (masquage: boolean) => ecrire<{ masquage: boolean }>("/controle/demo", { masquage }),
    scenarios: () => get<Props[]>("/controle/scenarios"),
    enregistrerScenario: (nom: string, adresse: string) => ecrire<{ id: number }>("/controle/scenarios", { nom, adresse }),
    retirerScenario: (id: number) => ecrire<Props>(`/controle/scenarios/${id}/retirer`, {}),
    mode: (regle: string, body: { mode: string; sous_type?: string | null; motif: string }) =>
      ecrire<Props>(`/controle/regles/${regle}`, body),
    basculer: (source: string, body: { en_pause: boolean; motif?: string; jusqu_a?: string | null }) =>
      ecrire<Interrupteur>(`/controle/satellites/${source}`, body),
    plafond: (source: string, valeur: number, motif: string) =>
      ecrire<Props>(`/controle/plafonds/${source}`, { valeur, motif }),
    demande: (body: { lon: number; lat: number; rayon_m: number; motif: string }) =>
      ecrire<{ id: number }>("/controle/demandes", body),
  },
};
