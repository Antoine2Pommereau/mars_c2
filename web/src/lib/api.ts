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

export const api = {
  passes: (bbox: number[]) => get<Pass[]>(`/passes?bbox=${bbox.join(",")}`),
  launch: (bbox: number[], product_name: string) => post<{ id: number }>("/analyses", { bbox, product_name, mode: "fast" }),
  analyses: () => get<FC>("/analyses"),
  detections: (id: number) => get<FC>(`/analyses/${id}/detections`),
  alerts: (id: number) => get<FC>(`/alerts?analysis_id=${id}`),
  trails: () => get<FC>("/traffic/trails"),
  alertsOfDay: (day: string) => get<FC>(`/alerts/day?day=${day}`),
  zones: () => get<FC>("/masks/stationary"),
  reception: () => get<FC>("/masks/reception"),
  days: () => get<{ day: string; messages: number; vessels: number }[]>("/ais/days"),
  track: (vesselId: number, start: string, end: string) =>
    get<Feature>(`/vessels/${vesselId}/track?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),
  clock: (body: { action: "play" | "pause" | "speed" | "seek"; speed?: number; time?: string }) =>
    post<Clock>("/clock", body),
};
