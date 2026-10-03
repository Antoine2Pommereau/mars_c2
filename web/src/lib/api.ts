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

export const chipUrl = (lon: number, lat: number, time: string, sizeM = 800) =>
  `/api/chip?lon=${lon}&lat=${lat}&time=${encodeURIComponent(time)}&size_m=${sizeM}`;

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
  track: (vesselId: number, start: string, end: string) =>
    get<Feature>(`/vessels/${vesselId}/track?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`),
  clock: (body: { action: "play" | "pause" | "speed" | "seek"; speed?: number; time?: string }) =>
    post<Clock>("/clock", body),
};
