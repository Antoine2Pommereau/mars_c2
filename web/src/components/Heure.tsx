import { horodatage, hm, jourHeure, utc } from "../lib/format";
import { heureParis } from "../lib/unites";

const FORMATS = { hm, jourHeure, utc, horodatage } as const;

/** Heure UTC suffixée Z (lib/format.ts), avec l'heure de Paris au survol et au focus du clavier (lot UX1). */
export function Heure({ iso, f = "hm" }: { iso?: string | number | null; f?: keyof typeof FORMATS }) {
  if (iso == null) return <>{FORMATS[f](iso as never)}</>;
  return <time dateTime={new Date(iso).toISOString()} title={heureParis(iso)} tabIndex={0}
    className="rounded-sm focus:outline-none focus-visible:ring-1 focus-visible:ring-signal">{FORMATS[f](iso as never)}</time>;
}
