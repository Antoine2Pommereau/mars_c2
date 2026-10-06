import type { FC, Feature } from "./types";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`/api${path}`);
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

export interface Timeline {
  debut: string; fin: string;
  densite: { t: string; navires: number | null }[];
  coupures: { debut: string; fin: string }[];
}

export const chipUrl = (lon: number, lat: number, time: string, sizeM = 800) =>
  `/api/chip?lon=${lon}&lat=${lat}&time=${encodeURIComponent(time)}&size_m=${sizeM}`;

export const api = {
  alertActions: (id: number) => get<AlertActionRow[]>(`/alerts/${id}/actions`),
  act: (id: number, body: { action: string; note?: string; motif?: string; author?: string }) =>
    post<{ id: number; status: string }>(`/alerts/${id}/actions`, body),
  /** Alertes d'une plage ; l'ancienne API ignore la plage et renvoie tout (filtré ensuite par l'interface) */
  alertsRange: (start: string, end: string) => get<FC>(`/alerts?start=${enc(start)}&end=${enc(end)}`),
  timeline: (start: string, end: string, bins: number) =>
    get<Timeline>(`/timeline?start=${enc(start)}&end=${enc(end)}&bins=${bins}`),
  traffic: (at: string) => get<any>(`/traffic?at=${enc(at)}`),
  ingestion: () => get<any>("/ingestion"),
  passes: (bbox: number[]) => get<Pass[]>(`/passes?bbox=${bbox.join(",")}`),
  launch: (bbox: number[], product_name: string) => post<{ id: number }>("/analyses", { bbox, product_name, mode: "fast" }),
  analyses: () => get<FC>("/analyses"),
  detections: (id: number) => get<FC>(`/analyses/${id}/detections`),
  alerts: (id: number) => get<FC>(`/alerts?analysis_id=${id}`),
  trails: (at?: string) => get<FC>(`/traffic/trails${at ? `?at=${enc(at)}` : ""}`),
  zones: () => get<FC>("/masks/stationary"),
  reception: () => get<FC>("/masks/reception"),
  /** Tracés simplifiés à environ 50 m : 2,6 Mo en pleine résolution, le détail suffit jusqu'au zoom 12 */
  infrastructure: () => get<FC>("/infrastructure?tolerance=0.0005"),
  track: (vesselId: number, start: string, end: string, maxPoints = 2000) =>
    get<Feature>(`/vessels/${vesselId}/track?start=${enc(start)}&end=${enc(end)}&max_points=${maxPoints}`),
  vessel: (id: number) => get<VesselCard>(`/vessels/${id}`),
};
