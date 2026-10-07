// Zones collectées, en miroir de mars/ais/live.py (ZONES) : [lat_min, lon_min, lat_max, lon_max].
// Ordre de recherche identique à la collecte (un point dans deux zones appartient à la première).
const ZONES: Record<string, [number, number, number, number]> = {
  bretagne: [47.3, -6.8, 49.6, -3.0],
  mediterranee: [41.2, 3.0, 43.7, 9.8],
  manche: [48.4, -5.0, 51.2, 2.6],
  gascogne: [43.3, -6.0, 47.4, -1.0],
};
// Ordre d'affichage, du nord ouest au sud est
export const ZONE_KEYS = ["bretagne", "manche", "gascogne", "mediterranee"];
/** Emprise de toute la France (les quatre régions) : [lon_min, lat_min, lon_max, lat_max] */
export const FRANCE: [number, number, number, number] = [-6.8, 41.2, 9.8, 51.2];

/** Emprise d'une zone : [lon_min, lat_min, lon_max, lat_max] */
export function zoneBbox(k: string): [number, number, number, number] {
  const [a, b, c, d] = ZONES[k];
  return [b, a, d, c];
}

export function zoneOf(lon: number, lat: number): string | null {
  for (const [k, [a, b, c, d]] of Object.entries(ZONES)) if (a <= lat && lat <= c && b <= lon && lon <= d) return k;
  return null;
}

/** Contours des zones, pour la couche « couverture ». */
export function zonesGeoJSON() {
  return {
    type: "FeatureCollection" as const,
    features: Object.entries(ZONES).map(([k, [a, b, c, d]]) => ({
      type: "Feature" as const, properties: { zone: k },
      geometry: { type: "Polygon", coordinates: [[[b, a], [d, a], [d, c], [b, c], [b, a]]] },
    })),
  };
}
