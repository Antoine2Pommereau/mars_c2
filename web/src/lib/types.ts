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
export type Selection =
  | { kind: "alert"; feature: Feature }
  | { kind: "vessel"; properties: Props }
  | { kind: "detection"; properties: Props }
  | { kind: "infrastructure"; properties: Props }
  | { kind: "zone"; properties: Props }
  | { kind: "passage"; properties: Props }
  | { kind: "vigilance"; properties: Props };

export const EMPTY: FC = { type: "FeatureCollection", features: [] };

/** Source d'une détection satellite des travailleurs : radar Sentinel 1, optique Sentinel 2, lumière VIIRS */
export type SourceDetection = "sentinel1" | "sentinel2" | "viirs";
