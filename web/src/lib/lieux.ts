import { ZONE_KEYS, zoneBbox } from "./zones";

// Lieux pour la recherche : ports et repères maritimes des quatre zones, et les zones elles mêmes. Choisir un lieu
// recentre la carte ; une zone ouvre aussi sa fiche.
export interface Lieu { nom: string; lon: number; lat: number; zoom: number; zone?: string }

const PORTS: [string, number, number][] = [
  ["Brest", -4.49, 48.38], ["Ouessant", -5.1, 48.46], ["Rail d'Ouessant", -5.6, 48.65], ["Roscoff", -3.97, 48.72],
  ["Lorient", -3.36, 47.73], ["Concarneau", -3.92, 47.87], ["Douarnenez", -4.33, 48.1], ["Saint Malo", -2.03, 48.65],
  ["Cherbourg", -1.62, 49.65], ["Le Havre", 0.11, 49.48], ["Antifer", 0.15, 49.65], ["Dieppe", 1.08, 49.93],
  ["Boulogne sur Mer", 1.6, 50.73], ["Calais", 1.85, 50.97], ["Dunkerque", 2.33, 51.05], ["Pas de Calais", 1.5, 50.98],
  ["Caen Ouistreham", -0.25, 49.29], ["Saint Nazaire", -2.2, 47.27], ["La Rochelle", -1.22, 46.15],
  ["Les Sables d'Olonne", -1.79, 46.49], ["Bordeaux", -0.55, 44.86], ["Le Verdon", -1.07, 45.55], ["Bayonne", -1.52, 43.53],
  ["Marseille", 5.35, 43.31], ["Fos sur Mer", 4.87, 43.41], ["Toulon", 5.92, 43.11], ["Sète", 3.7, 43.4],
  ["Port la Nouvelle", 3.06, 43.02], ["Nice", 7.28, 43.69], ["Ajaccio", 8.74, 41.92], ["Bastia", 9.45, 42.7],
  ["Golfe du Lion", 4.0, 42.9],
];

const LIEUX: Lieu[] = [
  ...PORTS.map(([nom, lon, lat]) => ({ nom, lon, lat, zoom: 9.5 })),
  ...ZONE_KEYS.map((z) => { const [x0, y0, x1, y1] = zoneBbox(z); return { nom: z, lon: (x0 + x1) / 2, lat: (y0 + y1) / 2, zoom: 6.5, zone: z }; }),
];

/** Lieux dont le nom contient la recherche (accents et casse indifférents). */
export function chercherLieux(q: string, nomZone: (z: string) => string): Lieu[] {
  const n = (s: string) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const k = n(q.trim());
  if (k.length < 2) return [];
  return LIEUX.filter((l) => n(l.zone ? nomZone(l.zone) : l.nom).includes(k)).slice(0, 6);
}
