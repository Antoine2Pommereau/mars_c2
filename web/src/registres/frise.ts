// Registre des marqueurs de frise : chaque piste déclare sa source d'événements, sa forme et son étape. La frise
// n'affiche que les pistes en place ; les passages satellites et les nuits VIIRS arriveront à l'étape 3.

interface Piste {
  id: "alertes" | "coupures" | "passages" | "viirs";
  forme: "marques" | "hachures";
  etape: number | null;
  /** Source : alertes de la plage, coupures du flux (/api/timeline), passages satellites… */
  source: "alertes" | "timeline" | "passages" | "viirs";
}

const PISTES: Piste[] = [
  { id: "alertes", forme: "marques", etape: null, source: "alertes" },
  { id: "coupures", forme: "hachures", etape: null, source: "timeline" },
  { id: "passages", forme: "marques", etape: 3, source: "passages" },
  { id: "viirs", forme: "hachures", etape: 3, source: "viirs" },
];

export const pistesActives = () => PISTES.filter((p) => p.etape == null);
