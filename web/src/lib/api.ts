import type { Clock, FC, Feature } from "./types";

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

export type ClockAction = "play" | "pause" | "speed" | "seek" | "live";

export interface VesselIdentity {
  mmsi: number; name: string | null; imo: number | null; callsign: string | null; flag: string | null;
  first_seen: string; last_seen: string; messages: number;
}

export interface VesselCard {
  id: number; mmsi: number; imo: number | null; name: string | null; callsign: string | null; ship_type: string | null;
  flag: string | null; length_m: number | null; destination: string | null; first_seen: string | null; last_seen: string | null;
  identities: VesselIdentity[];
  watch: { level: string; matched_by: string; entries: { source: string; name: string | null; risks: string[]; url: string | null; par: string }[] } | null;
}

export interface AlertActionRow { action: string; note: string | null; author: string; at: string }

export const chipUrl = (lon: number, lat: number, time: string, sizeM = 800) =>
  `/api/chip?lon=${lon}&lat=${lat}&time=${encodeURIComponent(time)}&size_m=${sizeM}`;

export const api = {
  alertActions: (id: number) => get<AlertActionRow[]>(`/alerts/${id}/actions`),
  act: (id: number, action: string, note: string) =>
    post<{ id: number; status: string }>(`/alerts/${id}/actions`, { action, note }),
  passes: (bbox: number[]) => get<Pass[]>(`/passes?bbox=${bbox.join(",")}`),
  launch: (bbox: number[], product_name: string) => post<{ id: number }>("/analyses", { bbox, product_name, mode: "fast" }),
  analyses: () => get<FC>("/analyses"),
  detections: (id: number) => get<FC>(`/analyses/${id}/detections`),
  alerts: (id: number) => get<FC>(`/alerts?analysis_id=${id}`),
  trails: () => get<FC>("/traffic/trails"),
  alertsOfDay: (day: string) => get<FC>(`/alerts/day?day=${day}`),
  zones: () => get<FC>("/masks/stationary"),
  reception: () => get<FC>("/masks/reception"),
  infrastructure: () => get<FC>("/infrastructure"),
  days: () => get<{ day: string; messages: number; vessels: number }[]>("/ais/days"),
  track: (vesselId: number, start: string, end: string) =>
    get<Feature>(`/vessels/${vesselId}/track?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),
  vessel: (id: number) => get<VesselCard>(`/vessels/${id}`),
  clock: (body: { action: ClockAction; speed?: number; time?: string }) =>
    post<Clock>("/clock", body),
};
