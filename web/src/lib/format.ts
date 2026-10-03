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
};

export const ALERT_COLOR: Record<string, string> = {
  DARK_SHIP: "#e85bc7", RENDEZVOUS: "#f0a84b", AIS_GAP: "#ef6461", AIS_UNCONFIRMED: "#e8d45a",
};

export function dayLabel(day: string): string {
  const [y, m, d] = day.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

// Pour le champ datetime local du saut dans le temps (heure UTC)
export function toInputValue(iso: string): string {
  return iso.slice(0, 16);
}
