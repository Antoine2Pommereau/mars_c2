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
  live: boolean;
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
  analyses: ActiveAnalysis[];
}
export type Selection =
  | { kind: "alert"; feature: Feature }
  | { kind: "vessel"; properties: Props }
  | { kind: "detection"; properties: Props }
  | { kind: "infrastructure"; properties: Props }
  | { kind: "zone"; properties: Props }
  | { kind: "passage"; properties: Props };

export const EMPTY: FC = { type: "FeatureCollection", features: [] };
