import { naviresAlerte, TYPES_ALERTE } from "../registres/alertes";
import { RANG_GRAVITE } from "./format";
import type { Feature } from "./types";
import { zoneOf } from "./zones";

// Fil d'alertes : filtres, tri par gravité puis date, regroupement des alertes d'un même navire.

export type Vue = "todo" | "confirmed" | "all";
export interface Filtres { types: string[]; gravites: string[]; vue: Vue; zone: string | null }

export const FILTRES_DEFAUT: Filtres = {
  types: TYPES_ALERTE.filter((t) => t.actif).map((t) => t.type),
  gravites: ["critique", "elevee", "moyenne", "faible"],
  vue: "todo",
  zone: null,
};

export const statutDe = (a: Feature) => a.properties.status ?? "nouvelle";
export const zoneAlerte = (a: Feature) => {
  const [lon, lat] = a.geometry?.coordinates ?? [];
  return lon == null ? null : zoneOf(lon, lat);
};

/** Instant qui situe l'alerte sur la plage : un navire des listes compte tant qu'il est vu (fin du passage). */
const finAlerte = (a: Feature) => Date.parse(a.properties.type === "WATCHLIST" && a.properties.details?.fin
  ? a.properties.details.fin : a.properties.event_time);

/** Alertes de la plage (l'ancienne API renvoie tout : la plage est aussi appliquée ici). */
export function dansPlage(alerts: Feature[], debut: number, fin: number): Feature[] {
  return alerts.filter((a) => Date.parse(a.properties.event_time) <= fin && finAlerte(a) >= debut);
}

/** Filtres sans l'onglet de statut (la carte et la frise montrent toutes les alertes filtrées). */
export function filtrer(alerts: Feature[], f: Filtres, avecVue = true): Feature[] {
  return alerts.filter((a) => f.types.includes(a.properties.type) && f.gravites.includes(a.properties.severity)
    && (!f.zone || zoneAlerte(a) === f.zone)
    && (!avecVue || f.vue === "all" || (f.vue === "todo" ? statutDe(a) === "nouvelle" : statutDe(a) === "confirmee")));
}

export const trier = (alerts: Feature[]) => [...alerts].sort((a, b) =>
  (RANG_GRAVITE[a.properties.severity] ?? 9) - (RANG_GRAVITE[b.properties.severity] ?? 9)
  || String(b.properties.event_time).localeCompare(String(a.properties.event_time)));

interface Groupe { cle: string; vesselId: number | null; alertes: Feature[] }

/** Les navires suivis dont une alerte est à traiter remontent en tête du fil, dans l'ordre de gravité et de date. */
export function suivisEnTete(groupes: Groupe[], suivis: Set<number>): Groupe[] {
  const prio = (g: Groupe) => g.vesselId != null && suivis.has(g.vesselId) && g.alertes.some((a) => statutDe(a) === "nouvelle");
  return [...groupes.filter(prio), ...groupes.filter((g) => !prio(g))];
}

/** Regroupe sous un même navire (le premier navire de chaque alerte) ; ordre des groupes : celui de leur alerte la
 *  plus grave et la plus récente. Une alerte sans navire forme son propre groupe. */
export function grouper(sorted: Feature[]): Groupe[] {
  const out: Groupe[] = [];
  const index = new Map<number, Groupe>();
  for (const a of sorted) {
    const v = naviresAlerte(a)[0]?.vessel_id ?? null;
    if (v != null && index.has(v)) { index.get(v)!.alertes.push(a); continue; }
    const g = { cle: v != null ? `n${v}` : `a${a.properties.id}`, vesselId: v, alertes: [a] };
    out.push(g);
    if (v != null) index.set(v, g);
  }
  return out;
}

/** Navires portant une alerte ouverte (non classée), avec la couleur de la plus grave : couleur sur la carte. */
export function naviresEnAlerte(sorted: Feature[]): Map<number, string> {
  const out = new Map<number, string>();
  for (const a of sorted) {
    if (statutDe(a) === "classee") continue;
    const t = TYPES_ALERTE.find((x) => x.type === a.properties.type);
    for (const n of naviresAlerte(a)) if (n?.vessel_id != null && !out.has(n.vessel_id)) out.set(n.vessel_id, t?.couleur ?? "#ffffff");
  }
  return out;
}
