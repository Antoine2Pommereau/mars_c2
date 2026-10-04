export type AlertType = "DARK_SHIP" | "RENDEZVOUS" | "AIS_GAP" | "AIS_UNCONFIRMED" | "INFRA_THREAT" | "IDENTITY_MISMATCH" | "ZONE_BREACH";
export type Severity = "faible" | "moyenne" | "elevee" | "critique";
export type AlertStatus = "nouvelle" | "acquittee" | "confirmee" | "classee";

/** Navire tel qu'il apparaît dans le détail d'une alerte (sous ensemble des champs AIS). */
export interface VesselRef {
  vessel_id: number;
  mmsi?: number;
  name?: string | null;
  ship_type?: string | null;
  length_m?: number | null;
  sog_kn?: number | null;
}

/** Candidat AIS examiné pour un écho de navire sombre. */
export interface AisCandidate {
  mmsi?: number;
  name?: string | null;
  distance_m?: number | null;
  tolerance_le_long_m?: number | null;
  rayon_tolere_m?: number | null;
}

/** Partenaire possible d'une coupure AIS. */
export interface GapPartner {
  vessel_id: number;
  mmsi?: number;
  name?: string | null;
  au_mouillage?: boolean;
  distance_min_m?: number | null;
  debut?: string;
  fin?: string;
}

interface AlertDetailsBase {
  motif?: string;
  contexte?: string[];
  parametres?: Record<string, number>;
}

export interface DarkShipDetails extends AlertDetailsBase {
  length_m?: number | null;
  contrast_vv_db?: number | null;
  objectness?: number | null;
  vessel_score?: number | null;
  reclassement?: string;
  candidats_ais?: AisCandidate[];
}

export interface RendezvousDetails extends AlertDetailsBase {
  navires?: VesselRef[];
  debut?: string;
  fin?: string;
  duree_min?: number;
  distance_min_m?: number;
  distance_moyenne_m?: number;
  distance_cote_km?: number | null;
}

export interface AisGapDetails extends AlertDetailsBase {
  navire?: VesselRef;
  dernier_message?: string;
  vitesse_avant_kn?: number | null;
  reapparition?: string | null;
  duree_min?: number;
  deplacement_km?: number | null;
  reception_cellule?: { navires?: number; continuite?: number };
  partenaires_possibles?: GapPartner[];
  derniere_position?: [number, number];
  position_reapparition?: [number, number];
}

export interface AisUnconfirmedDetails extends AlertDetailsBase {
  navire?: VesselRef;
  methode_position?: string;
  echo_le_plus_proche_m?: number | null;
  tolerance_le_long_m?: number | null;
  tolerance_en_travers_m?: number | null;
}

export interface InfraThreatDetails extends AlertDetailsBase {
  navire?: VesselRef;
  debut?: string;
  fin?: string;
  duree_min?: number;
  infrastructure?: { nom?: string | null; type?: string | null; operateur?: string | null; distance_m?: number | null };
  distance_corridor_m?: number;
  vitesse_moyenne_kn?: number;
  deplacement_episode_m?: number;
  traine_ancre_possible?: boolean;
}

export interface IdentityMismatchDetails extends AlertDetailsBase {
  navire?: VesselRef;
  longueur_radar_m?: number;
  longueur_ais_m?: number;
  ecart_m?: number;
  rapport?: number;
  vessel_score?: number;
}

export interface ZoneBreachDetails extends AlertDetailsBase {
  navire?: VesselRef;
  debut?: string;
  fin?: string;
  duree_min?: number;
  aire?: { nom?: string | null; designation?: string | null; type?: string | null };
  vitesse_moyenne_kn?: number;
  en_peche?: boolean;
}

export type AlertDetails =
  | DarkShipDetails
  | RendezvousDetails
  | AisGapDetails
  | AisUnconfirmedDetails
  | InfraThreatDetails
  | IdentityMismatchDetails
  | ZoneBreachDetails;

interface AlertPropsBase {
  id: number;
  severity: Severity;
  status?: AlertStatus;
  event_time?: string;
  rule_version?: string;
}

/** Propriétés d'une alerte : union discriminée sur `type`. */
export type AlertProps =
  | (AlertPropsBase & { type: "DARK_SHIP"; details?: DarkShipDetails })
  | (AlertPropsBase & { type: "RENDEZVOUS"; details?: RendezvousDetails })
  | (AlertPropsBase & { type: "AIS_GAP"; details?: AisGapDetails })
  | (AlertPropsBase & { type: "AIS_UNCONFIRMED"; details?: AisUnconfirmedDetails })
  | (AlertPropsBase & { type: "INFRA_THREAT"; details?: InfraThreatDetails })
  | (AlertPropsBase & { type: "IDENTITY_MISMATCH"; details?: IdentityMismatchDetails })
  | (AlertPropsBase & { type: "ZONE_BREACH"; details?: ZoneBreachDetails });

/** Propriétés d'une trajectoire surlignée (couleur, pointillés). */
export interface TrackProps {
  color?: string;
  dashed?: boolean;
}

/** Propriétés d'un navire du trafic rejoué. */
export interface VesselProps {
  vessel_id: number;
  mmsi?: number;
  name?: string | null;
  ship_type?: string | null;
  length_m?: number | null;
  sog_kn?: number | null;
  cog_deg?: number | null;
  age_s?: number;
}

/** Propriétés d'une détection radar. */
export interface DetectionProps {
  mask_reason?: string | null;
  matched_mmsi?: number | string | null;
  matched_name?: string | null;
  length_m?: number | null;
  contrast_vv_db?: number | null;
  objectness?: number | null;
  vessel_score?: number | null;
  lon?: number;
  lat?: number;
}

/** Résumé chiffré d'une analyse. */
export interface AnalysisSummary {
  retenues?: number;
  appariees?: number;
  alertes?: number;
  positions_non_confirmees?: number;
  echos_fixes?: number;
}

/** Propriétés d'une analyse radar (emprise). */
export interface AnalysisProps {
  id: number;
  status: "pending" | "running" | "done" | "failed";
  acquired_at: string;
  summary?: AnalysisSummary;
  timings?: { total_s?: number };
}

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
  summary?: AnalysisSummary;
  error?: string | null;
}
export interface StreamPayload {
  clock: Clock;
  traffic: FC<VesselProps>;
  analyses: ActiveAnalysis[];
  live_alerts: FC<AlertProps>;
}
/** Propriétés d'une infrastructure sous marine (câble, pipeline). */
export interface InfraProps {
  kind: string;
  name: string | null;
  operator: string | null;
  source: string;
  attrs?: Record<string, unknown>;
}

/** Propriétés d'une aire marine protégée. */
export interface ProtectedAreaProps {
  kind: string;
  name: string | null;
  designation: string | null;
  country: string | null;
  source: string;
  attrs?: Record<string, unknown>;
}

export type Selection =
  | { kind: "alert"; feature: Feature<AlertProps> }
  | { kind: "vessel"; properties: VesselProps }
  | { kind: "detection"; properties: DetectionProps }
  | { kind: "infrastructure"; properties: InfraProps }
  | { kind: "protected_area"; properties: ProtectedAreaProps }
  | { kind: "vessel_dossier"; vesselId: number };

export const EMPTY: FC<any> = { type: "FeatureCollection", features: [] };
