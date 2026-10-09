// Registre des preuves images des détections satellites : chaque source déclare son étape, son libellé et la route
// qui donne la fiche de la détection (preuve et positions AIS à l'heure du passage). Même table côté serveur
// (preuves_images, mars/preuves.py) ; Sentinel 1 et Sentinel 2 arrivent au lot C.

interface SourcePreuve {
  source: "viirs" | "sentinel1" | "sentinel2";
  etape: string | null;          // null : en place
  /** Fiche de la détection : { preuve: { url, largeur, hauteur, geo }, ais_proches: [...] } */
  fiche: (id: number) => string;
}

const SOURCES_PREUVE: SourcePreuve[] = [
  { source: "viirs", etape: null, fiche: (id) => `/viirs/detections/${id}` },
  { source: "sentinel1", etape: "C", fiche: (id) => `/detections/${id}` },
  { source: "sentinel2", etape: "C", fiche: (id) => `/detections/${id}` },
];

export const sourcePreuve = (s: string) => SOURCES_PREUVE.find((x) => x.source === s && x.etape == null) ?? null;
