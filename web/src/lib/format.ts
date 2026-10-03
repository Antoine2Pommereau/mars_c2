// Formats d'affichage : pas de tiret, dates en JJ/MM/AAAA
const pad = (n: number) => String(n).padStart(2, "0");

export function utc(iso?: string | null): string {
  if (!iso) return "n.d.";
  const d = new Date(iso);
  return `${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)}/${d.getUTCFullYear()} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
}

export function hm(iso?: string | null): string {
  if (!iso) return "n.d.";
  const d = new Date(iso);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

// Heure seule (sans la date) : la date est affichée une seule fois, par le sélecteur de journée
export function hms(iso?: string | null): string {
  if (!iso) return "n.d.";
  const d = new Date(iso);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
}

export function num(v: unknown, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "n.d.";
  return Number(v).toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export const SEVERITY: Record<string, string> = {
  faible: "faible", moyenne: "moyenne", elevee: "élevée", critique: "critique",
};

export const STATUS_LABEL: Record<string, string> = {
  nouvelle: "à traiter", acquittee: "acquittée", confirmee: "confirmée", classee: "classée",
};

export const ALERT_LABEL: Record<string, string> = {
  DARK_SHIP: "Navire sombre",
  RENDEZVOUS: "Rendez vous suspect",
  AIS_GAP: "Coupure AIS",
  AIS_UNCONFIRMED: "Position AIS non confirmée",
  INFRA_THREAT: "Menace infrastructure",
};

// Teintes centralisées : couleur réservée aux alertes, palette neutre par type de navire.
// Seul point de vérité ; réutilisé dans l'interface et dans les expressions MapLibre.
export const ALERT_COLOR: Record<string, string> = {
  DARK_SHIP: "#e85bc7", RENDEZVOUS: "#f0a84b", AIS_GAP: "#ef6461", AIS_UNCONFIRMED: "#e8d45a",
  INFRA_THREAT: "#e03030",
};
export const ALERT_FALLBACK = "#ffffff";
export const SIGNAL = "#4fb6c8";

// Couches de contexte provisionnées (régions) : teintes désaturées, la couleur vive reste aux alertes
export const INFRA_CABLE = "#6f8fa6";
export const INFRA_PIPELINE = "#b08d57";
export const BATHY_CONTOUR = "#4a6072";

// Surlignage des trajectoires : navire principal en orange rendez vous, autres en signal, trajet présumé en rouge coupure
export const HIGHLIGHT_PRIMARY = ALERT_COLOR.RENDEZVOUS;
export const HIGHLIGHT_SECONDARY = SIGNAL;
export const HIGHLIGHT_DASHED = ALERT_COLOR.AIS_GAP;

// Palette neutre par type de navire (aucune valeur changée par rapport au rendu existant)
export const SHIP_NEUTRAL = "#c9d3da";
export const SHIP_IDLE = "#4c5a66";
export const SHIP_PALETTE: { types: string[]; color: string }[] = [
  { types: ["Cargo"], color: "#6ea8fe" },
  { types: ["Tanker"], color: "#f0a35e" },
  { types: ["Fishing"], color: "#5fd38d" },
  { types: ["Passenger", "HSC"], color: "#c792ea" },
  { types: ["Pleasure", "Sailing"], color: "#f5e663" },
  { types: ["Tug", "Towing", "Towing long/wide", "Pilot", "SAR", "Law enforcement", "Military",
            "Port tender", "Dredging", "Diving", "Anti-pollution", "Medical"], color: "#e07a5f" },
];
export const SHIP_OTHER = "#9fb3c2";

export function dayLabel(day: string): string {
  const [y, m, d] = day.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

// Pour le champ datetime local du saut dans le temps (heure UTC)
export function toInputValue(iso: string): string {
  return iso.slice(0, 16);
}

// Le rejeu se place dix minutes avant l'instant d'un passage
const REPLAY_LEAD_MS = 600_000;
export function replayStart(acquiredAt: string): string {
  return new Date(new Date(acquiredAt).getTime() - REPLAY_LEAD_MS).toISOString();
}
