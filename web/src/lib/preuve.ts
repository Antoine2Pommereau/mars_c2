// Preuve d'un dossier dessinée sur la carte (lot U, étape 4) : route AIS, dernière position avant le silence, silence
// en tirets jusqu'à l'écho, position estimée et cercle d'incertitude, écho, et la distance entre position estimée et
// écho. Calcul pur, partagé par la carte (MapView) et la légende du récit : les deux affichent les mêmes chiffres.

import { distance as distanceMarine } from "./unites.ts";

type Pt = { lon: number; lat: number; t?: string | null };
export interface Preuve {
  derniere_position: Pt | null;
  echo: (Pt & { longueur_m?: number | null; source?: string }) | null;
  estimee?: Pt & { rayon_m: number; heure_passage?: string | null };
  distance_estimee_echo_m?: number;
  distance_derniere_echo_m?: number;
}

/** Distance d'une preuve, en milles nautiques (mètres sous 0,1 mille : preuve fine), lib/unites.ts */
export const distance = (m: number): string => distanceMarine(m, true);

/** Cercle géodésique approché (64 sommets) autour d'un point, rayon en mètres */
export function cercle(lon: number, lat: number, rayon_m: number, n = 64): number[][] {
  const dLat = rayon_m / 111_320, dLon = rayon_m / (111_320 * Math.cos((lat * Math.PI) / 180));
  return Array.from({ length: n + 1 }, (_, k) => {
    const a = (2 * Math.PI * k) / n;
    return [lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)];
  });
}

type F = { type: "Feature"; geometry: { type: string; coordinates: unknown }; properties: Record<string, unknown> };
const point = (p: Pt, role: string, extra: Record<string, unknown> = {}): F =>
  ({ type: "Feature", geometry: { type: "Point", coordinates: [p.lon, p.lat] }, properties: { role, ...extra } });
const ligne = (c: number[][], role: string, extra: Record<string, unknown> = {}): F =>
  ({ type: "Feature", geometry: { type: "LineString", coordinates: c }, properties: { role, ...extra } });

/** Objets de la carte, chacun avec son rôle (route, silence, cercle, ecart, derniere, estimee, echo) ; l'écart
 *  porte l'étiquette de distance, la même que la légende */
export function preuveGeoJSON(preuve: Preuve | null | undefined, route: [number, number, string][] = []) {
  const f: F[] = [];
  if (route.length > 1) f.push(ligne(route.map((r) => [r[0], r[1]]), "route"));
  if (preuve) {
    const { derniere_position: d, estimee: e, echo } = preuve;
    if (e) f.push({ type: "Feature", geometry: { type: "Polygon", coordinates: [cercle(e.lon, e.lat, e.rayon_m)] },
      properties: { role: "cercle" } });
    if (d && echo) f.push(ligne([[d.lon, d.lat], [echo.lon, echo.lat]], "silence"));
    if (e && echo && preuve.distance_estimee_echo_m != null)
      f.push(ligne([[e.lon, e.lat], [echo.lon, echo.lat]], "ecart", { etiquette: distance(preuve.distance_estimee_echo_m) }));
    if (d) f.push(point(d, "derniere"));
    if (e) f.push(point(e, "estimee"));
    if (echo) f.push(point(echo, "echo"));
  }
  return { type: "FeatureCollection" as const, features: f };
}

/** Emprise à montrer pour la preuve (le cercle compris), en [ouest, sud, est, nord] */
export function emprise(preuve: Preuve | null | undefined): [number, number, number, number] | null {
  const g = preuveGeoJSON(preuve);
  const pts = g.features.flatMap((x) => x.geometry.type === "Point" ? [x.geometry.coordinates as number[]]
    : x.geometry.type === "Polygon" ? (x.geometry.coordinates as number[][][])[0] : x.geometry.coordinates as number[][]);
  if (!pts.length) return null;
  const lons = pts.map((p) => p[0]), lats = pts.map((p) => p[1]);
  return [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
}

/** Couleurs de la preuve, communes à la carte et à la légende */
export const COULEURS_PREUVE = { route: "#7c8b97", derniere: "#e6ecf0", silence: "#ef6461", cercle: "#e6ecf0",
  echo: "#e85bc7", ecart: "#e6ecf0" };
