import { L } from "./libelles";
import { gdh, reglagesUnites } from "./unites";

// Formats d'affichage : pas de tiret, dates en JJ/MM/AAAA, heures UTC suffixées Z (lot UX1 : unités marines,
// lib/unites.ts). Les libellés sont dans libelles.ts.
const pad = (n: number) => String(n).padStart(2, "0");

export function utc(iso?: string | null): string {
  if (!iso) return L.commun.nd;
  const d = new Date(iso);
  return `${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)}/${d.getUTCFullYear()} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`;
}

/** Horodatage d'un dossier ou d'une alerte : UTC suffixé Z, ou groupe date heure (« 291654Z OCT 26 ») en option. */
export const horodatage = (iso?: string | null) => (iso && reglagesUnites.horodatage === "gdh" ? gdh(iso) : utc(iso));

export function hm(iso?: string | number | null): string {
  if (iso == null) return L.commun.nd;
  const d = new Date(iso);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}Z`;
}

/** Date courte JJ/MM HH:MM (frise, lignes du fil sur des plages longues) */
export function jourHeure(iso?: string | number | null): string {
  if (iso == null) return L.commun.nd;
  const d = new Date(iso);
  return `${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}Z`;
}

export function num(v: unknown, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return L.commun.nd;
  return Number(v).toLocaleString("fr-FR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function dayLabel(day?: string | null): string {
  if (!day) return L.commun.nd;
  const [y, m, d] = day.slice(0, 10).split("-");
  return `${d}/${m}/${y}`;
}

export const RANG_GRAVITE: Record<string, number> = { critique: 0, elevee: 1, moyenne: 2, faible: 3 };
