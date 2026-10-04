import type { Clock, FC, Feature } from "./types";

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const r = await fetch(`/api${path}`, { signal });
  if (!r.ok) throw new Error(`${path} : ${r.status}`);
  return r.json();
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${path} : ${r.status} ${await r.text()}`);
  return r.json();
}

export interface Pass {
  product_name: string;
  acquired_at: string;
  platform: string;
  orbit_direction: string | null;
  coverage: number | null;
  ais_available: boolean;
}

export interface AlertActionRow { action: string; note: string | null; author: string; at: string }

export interface RegionLayer {
  layer: string; usage: string; status: string; source: string | null;
  license: string | null; feature_count: number | null; size_bytes: number | null; fetched_at: string | null;
}
export interface Region { id: number; name: string; origin: string; active: boolean; bbox: number[]; layers: RegionLayer[] }

export interface VesselHit {
  vessel_id: number; mmsi: number; name: string | null; ship_type: string | null;
  flag: string | null; length_m: number | null; last_seen: string | null; n_alertes: number;
}
export interface DossierContribution { alert_id: number; type: string; severity: string; status: string; jour: string; points: number }
export interface DossierAlert { id: number; type: string; severity: string; status: string; event_time: string; lon: number; lat: number; motif: string | null }
export interface Dossier {
  vessel_id: number;
  identite: { mmsi: number; imo: number | null; name: string | null; ship_type: string | null; flag: string | null;
              length_m: number | null; ais_class: string | null; mid: number; first_seen: string | null; last_seen: string | null };
  anomalies_identite: Record<string, boolean>;
  comptages: { par_type: Record<string, number>; total: number; confirmees: number; classees: number;
               jours_distincts: number; premiere_alerte: string | null; derniere_alerte: string | null };
  risque: { score: number; bande: string; contributions: DossierContribution[]; bonus_recurrence: number;
            jours_distincts: number; terme_identite: number; version: string };
  alertes: DossierAlert[];
}

export const chipUrl = (lon: number, lat: number, time: string, sizeM = 800) =>
  `/api/chip?lon=${lon}&lat=${lat}&time=${encodeURIComponent(time)}&size_m=${sizeM}`;

export const bathymetryImageUrl = (regionId: number) => `/api/regions/${regionId}/bathymetry/image`;

// TanStack Query passe { signal, ... } à queryFn ; on en extrait l'AbortSignal pour le relayer à fetch.
type QueryCtx = { signal?: AbortSignal };

export const api = {
  alertActions: (id: number) => get<AlertActionRow[]>(`/alerts/${id}/actions`),
  act: (id: number, action: string, note: string) =>
    post<{ id: number; status: string }>(`/alerts/${id}/actions`, { action, note }),
  passes: (bbox: number[]) => get<Pass[]>(`/passes?bbox=${bbox.join(",")}`),
  launch: (bbox: number[], product_name: string) => post<{ id: number }>("/analyses", { bbox, product_name, mode: "fast" }),
  analyses: (ctx?: QueryCtx) => get<FC>("/analyses", ctx?.signal),
  detections: (id: number, ctx?: QueryCtx) => get<FC>(`/analyses/${id}/detections`, ctx?.signal),
  alerts: (id: number, ctx?: QueryCtx) => get<FC>(`/alerts?analysis_id=${id}`, ctx?.signal),
  trails: (ctx?: QueryCtx) => get<FC>("/traffic/trails", ctx?.signal),
  alertsOfDay: (day: string, ctx?: QueryCtx) => get<FC>(`/alerts/day?day=${day}`, ctx?.signal),
  zones: (ctx?: QueryCtx) => get<FC>("/masks/stationary", ctx?.signal),
  reception: (ctx?: QueryCtx) => get<FC>("/masks/reception", ctx?.signal),
  days: (ctx?: QueryCtx) => get<{ day: string; messages: number; vessels: number }[]>("/ais/days", ctx?.signal),
  regions: (ctx?: QueryCtx) => get<Region[]>("/regions", ctx?.signal),
  infrastructure: (regionId: number, ctx?: QueryCtx) => get<FC>(`/regions/${regionId}/infrastructure`, ctx?.signal),
  protectedAreas: (regionId: number, ctx?: QueryCtx) => get<FC>(`/regions/${regionId}/protected_areas`, ctx?.signal),
  bathymetry: (regionId: number, ctx?: QueryCtx) => get<FC>(`/regions/${regionId}/bathymetry/contours`, ctx?.signal),
  depth: (regionId: number, lon: number, lat: number, signal?: AbortSignal) =>
    get<{ depth_m: number | null; note?: string }>(`/regions/${regionId}/depth?lon=${lon}&lat=${lat}`, signal),
  createRegion: (name: string, bbox: number[]) => post<{ id: number }>("/regions", { name, bbox }),
  activateRegion: (id: number) => post<{ active: number }>(`/regions/${id}/activate`, {}),
  provisionRegion: (id: number) => post<{ status: string }>(`/regions/${id}/provision`, {}),
  searchVessels: (q: string, ctx?: QueryCtx) => get<VesselHit[]>(`/vessels?q=${encodeURIComponent(q)}`, ctx?.signal),
  dossier: (vesselId: number, ctx?: QueryCtx) => get<Dossier>(`/vessels/${vesselId}/dossier`, ctx?.signal),
  track: (vesselId: number, start: string, end: string) =>
    get<Feature>(`/vessels/${vesselId}/track?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),
  clock: (body: { action: "play" | "pause" | "speed" | "seek"; speed?: number; time?: string }) =>
    post<Clock>("/clock", body),
};
