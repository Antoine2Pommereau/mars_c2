import { appel } from "./traces";
import type { FC, Feature, Props } from "./types";

async function get<T>(path: string): Promise<T> {
  const r = await appel(`/api${path}`);
  if (!r.ok) throw new Error(`${path} : ${r.status}`);
  return r.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await appel(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} : ${r.status} ${await r.text()}`);
  return r.json();
}

/** Écriture du panneau de contrôle : le motif d'un refus de l'API (detail) devient le message de l'erreur */
async function ecrire<T>(path: string, body: unknown): Promise<T> {
  const r = await appel(`/api${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
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
  travailleurs: Props[];
  rayon_demande_m: { min: number; max: number; defaut: number } | null;
}

export interface SystemeControle {
  grafana: string;
  services: { service: string; dernier_le: string | null; age_s: number | null; version: string | null;
    memoire_mo?: number | null; memoire_max_mo?: number | null; retard_s: number | null }[];
  retard_positions_s: number | null;
  disque: { mesure_le: string; libre_pct: number; libre_octets: number; total_octets: number; base_octets: number | null } | null;
  echecs: { id: number; tache: string; le: string; erreur: string | null; trace_id: string | null; lien_trace: string | null }[];
  lien_regles: string | null;
}

export interface Pass {
  product_name: string;
  acquired_at: string;
  platform: string;
  orbit_direction: string | null;
  coverage: number | null;
  ais_available: boolean;
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

export const chipUrl = (lon: number, lat: number, time: string, sizeM = 800) =>
  `/api/chip?lon=${lon}&lat=${lat}&time=${encodeURIComponent(time)}&size_m=${sizeM}`;

export const api = {
  alertActions: (id: number) => get<AlertActionRow[]>(`/alerts/${id}/actions`),
  act: (id: number, body: { action: string; note?: string; motif?: string; author?: string }) =>
    post<{ id: number; status: string }>(`/alerts/${id}/actions`, body),
  /** Alertes d'une plage ; l'ancienne API ignore la plage et renvoie tout (filtré ensuite par l'interface) */
  alertsRange: (start: string, end: string, region: string | null) =>
    get<FC>(`/alerts?start=${enc(start)}&end=${enc(end)}${rg(region)}`),
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
  addNote: (id: number, note: string, author: string) => post<Props>(`/vessels/${id}/notes`, { note, author }),
  suivis: (region: string | null = null) => get<Props[]>(`/suivis${rg(region, "?")}`),
  suivre: (id: number, author: string) => post<Props>("/suivis", { vessel_id: id, author }),
  nePlusSuivre: async (id: number) => {
    const r = await appel(`/api/suivis/${id}`, { method: "DELETE" });
    if (!r.ok) throw new Error(`/suivis/${id} : ${r.status}`);
    return r.json();
  },
  infraCard: (id: number, start: string, end: string) => get<Props>(`/infrastructure/${id}?start=${enc(start)}&end=${enc(end)}`),
  zoneCard: (key: string) => get<Props>(`/zones/${key}`),
  passes: (bbox: number[]) => get<Pass[]>(`/passes?bbox=${bbox.join(",")}`),
  launch: (bbox: number[], product_name: string) => post<{ id: number }>("/analyses", { bbox, product_name, mode: "fast" }),
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
  demander: (body: { vessel_id?: number; alerte_id?: number; lon?: number; lat?: number; author?: string }) =>
    post<{ id: number; regroupee: boolean; etat: string }>("/demandes", body),
  annulerDemande: (id: number) => post<Props>(`/demandes/${id}/annuler`, {}),
  /** Panneau « Observations » : demandes de la plage et de la région, compteur des actives */
  observations: (start: string, end: string, region: string | null) =>
    get<{ actives: number; demandes: Props[] }>(`/observations?start=${enc(start)}&end=${enc(end)}${rg(region)}`),
  /** Panneau de contrôle (lot P) : session d'opérateur, état, journal, et écritures (connexion exigée) */
  controle: {
    session: () => get<{ operateur: string | null; configure: boolean }>("/controle/session"),
    connexion: (operateur: string, mot_de_passe: string) =>
      ecrire<{ operateur: string }>("/controle/connexion", { operateur, mot_de_passe }),
    deconnexion: () => ecrire<{ operateur: null }>("/controle/deconnexion", {}),
    etat: () => get<EtatControle>("/controle/etat"),
    journal: (limit = 30) => get<Props[]>(`/controle/journal?limit=${limit}`),
    systeme: () => get<SystemeControle>("/controle/systeme"),
    basculer: (source: string, body: { en_pause: boolean; motif?: string; jusqu_a?: string | null }) =>
      ecrire<Interrupteur>(`/controle/satellites/${source}`, body),
    plafond: (source: string, valeur: number, motif: string) =>
      ecrire<Props>(`/controle/plafonds/${source}`, { valeur, motif }),
    demande: (body: { lon: number; lat: number; rayon_m: number; motif: string }) =>
      ecrire<{ id: number }>("/controle/demandes", body),
  },
};
