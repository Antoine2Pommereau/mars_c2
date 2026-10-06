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

/** Infrastructures dont le tracé passe à moins de `metres` d'un point (distance point segment, en projection locale
 *  équirectangulaire, suffisante à quelques kilomètres). Mode « concernées seulement » des couches. */
export function nearInfra(fc: { features: { properties: any; geometry: any }[] }, lon: number, lat: number, metres: number): number[] {
  const kx = 111_320 * Math.cos((lat * Math.PI) / 180), ky = 110_574;
  const seg = (a: number[], b: number[]) => {
    const ax = (a[0] - lon) * kx, ay = (a[1] - lat) * ky, bx = (b[0] - lon) * kx, by = (b[1] - lat) * ky;
    const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
    const t = l2 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / l2)) : 0;
    return Math.hypot(ax + t * dx, ay + t * dy);
  };
  const lines = (g: any): number[][][] => g.type === "LineString" ? [g.coordinates] : g.type === "MultiLineString" ? g.coordinates
    : g.type === "Polygon" ? g.coordinates : g.type === "MultiPolygon" ? g.coordinates.flat() : [];
  const out: number[] = [];
  for (const f of fc.features) {
    if (!f.geometry) continue;
    const near = lines(f.geometry).some((l) => l.some((p, i) => i > 0 && seg(l[i - 1], p) <= metres));
    if (near) out.push(f.properties.id);
  }
  return out;
}
