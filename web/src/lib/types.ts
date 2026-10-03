export type Props = Record<string, any>;
export interface Feature<P = Props> {
  type: "Feature";
  geometry: { type: string; coordinates: any };
  properties: P;
}
export interface FC<P = Props> {
  type: "FeatureCollection";
  features: Feature<P>[];
}
export interface Clock {
  now: string;
  speed: number;
  paused: boolean;
}
export interface ProgressStep {
  step: "extraction" | "inference" | "fusion";
  state: "start" | "done";
  seconds?: number;
  detail?: string;
}
export interface ActiveAnalysis {
  id: number;
  status: "pending" | "running" | "done" | "failed";
  progress: ProgressStep[];
  summary?: Props;
  error?: string | null;
}
export interface StreamPayload {
  clock: Clock;
  traffic: FC;
  analyses: ActiveAnalysis[];
  live_alerts: FC;
}
export type Selection =
  | { kind: "alert"; feature: Feature }
  | { kind: "vessel"; properties: Props }
  | { kind: "detection"; properties: Props };

export const EMPTY: FC = { type: "FeatureCollection", features: [] };
