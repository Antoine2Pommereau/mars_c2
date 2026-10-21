import type { FC } from "./types";

/** Trafic reçu de l'API, en colonnes (version actuelle) ou en GeoJSON (ancienne API), vers une collection GeoJSON
 *  pour la carte. `age_s` est recalculé par rapport à l'instant affiché. */
export function decodeTraffic(payload: any, instantMs: number): FC | null {
  // Deux formes en colonnes : le flux en direct enveloppe le trafic ({navires: {cols, rows}}), la route /api/traffic
  // des modes plage et rejeu le renvoie tel quel ({at, cols, rows}). N'accepter que la première vidait la carte hors
  // du direct (constaté le 21/10/2026, lot U).
  if (payload?.cols && payload?.rows) payload = { navires: payload };
  if (payload?.navires?.cols) {
    const { cols, rows } = payload.navires as { cols: string[]; rows: any[][] };
    const k = Object.fromEntries(cols.map((c, i) => [c, i]));
    return {
      type: "FeatureCollection",
      features: rows.map((r) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [r[k.lon], r[k.lat]] },
        properties: {
          vessel_id: r[k.vessel_id], mmsi: r[k.mmsi], name: r[k.name], ship_type: r[k.ship_type], length_m: r[k.length_m],
          flag: r[k.flag], watch: r[k.watch], sog_kn: r[k.sog_kn], cog_deg: r[k.cog_deg],
          age_s: Math.max(0, instantMs / 1000 - r[k.t]),
        },
      })),
    };
  }
  if (payload?.traffic?.features) return payload.traffic as FC;
  return null;
}
