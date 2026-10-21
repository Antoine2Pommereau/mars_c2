// Cadrage de la carte à l'ouverture de la Veille (lot U) : les dossiers à examiner, ou à défaut tous les dossiers,
// avec une marge pour qu'un dossier seul ne soit pas cadré au plus près.

type Pt = { lon: number; lat: number; niveau: number };

/** Emprise [ouest, sud, est, nord] des dossiers à examiner (niveau 2 et plus), sinon de tous ; null sans dossier */
export function cadreDossiers(ds: Pt[], marge = 0.3): [number, number, number, number] | null {
  const pts = ds.some((d) => d.niveau >= 2) ? ds.filter((d) => d.niveau >= 2) : ds;
  if (!pts.length) return null;
  const lons = pts.map((d) => d.lon), lats = pts.map((d) => d.lat);
  return [Math.min(...lons) - marge, Math.min(...lats) - marge, Math.max(...lons) + marge, Math.max(...lats) + marge];
}
