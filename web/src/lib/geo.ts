export const MAX_ZONE_KM = 50;

/** Dimensions d'une emprise [lon_min, lat_min, lon_max, lat_max], en kilomètres. */
export function bboxSizeKm(b: number[]): { w: number; h: number } {
  const lat = ((b[1] + b[3]) / 2) * (Math.PI / 180);
  return { w: (b[2] - b[0]) * 111.32 * Math.cos(lat), h: (b[3] - b[1]) * 110.57 };
}

export function bboxPolygon(b: number[]) {
  return {
    type: "FeatureCollection" as const,
    features: [{
      type: "Feature" as const,
      properties: { tooBig: Math.max(bboxSizeKm(b).w, bboxSizeKm(b).h) > MAX_ZONE_KM },
      geometry: { type: "Polygon", coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] },
    }],
  };
}
