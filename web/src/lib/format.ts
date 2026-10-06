import { L } from "./libelles";

// Formats d'affichage : pas de tiret, dates en JJ/MM/AAAA, heures UTC. Les libellés sont dans libelles.ts.
const pad = (n: number) => String(n).padStart(2, "0");

export function utc(iso?: string | null): string {
  if (!iso) return L.commun.nd;
  const d = new Date(iso);
  return `${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)}/${d.getUTCFullYear()} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`;
}

export function hm(iso?: string | number | null): string {
  if (iso == null) return L.commun.nd;
  const d = new Date(iso);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

/** Date courte JJ/MM HH:MM (frise, lignes du fil sur des plages longues) */
export function jourHeure(iso?: string | number | null): string {
  if (iso == null) return L.commun.nd;
  const d = new Date(iso);
  return `${pad(d.getUTCDate())}/${pad(d.getUTCMonth() + 1)} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
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
