// Registre des preuves images des détections satellites : chaque source déclare son étape, son libellé et la route
// qui donne la fiche de la détection (preuve et positions AIS à l'heure du passage). Même table côté serveur
// (preuves_images, mars/preuves.py) ; Sentinel 2 (lot S2) partage la fiche des détections Sentinel 1.

interface SourcePreuve {
  source: "viirs" | "sentinel1" | "sentinel2";
  etape: string | null;          // null : en place
  /** Fiche de la détection : { preuve: { url, largeur, hauteur, geo }, ais_proches: [...] } */
  fiche: (id: number) => string;
}

const SOURCES_PREUVE: SourcePreuve[] = [
  { source: "viirs", etape: null, fiche: (id) => `/viirs/detections/${id}` },
  { source: "sentinel1", etape: null, fiche: (id) => `/sentinel/detections/${id}` },
  { source: "sentinel2", etape: null, fiche: (id) => `/sentinel/detections/${id}` },
];

export const sourcePreuve = (s: string) => SOURCES_PREUVE.find((x) => x.source === s && x.etape == null) ?? null;
