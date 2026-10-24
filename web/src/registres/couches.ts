// Registre des couches : chaque couche déclare son groupe, son étape (« à venir » tant qu'elle n'est pas branchée),
// son état par défaut, les couches MapLibre qu'elle commande et sa légende. Le panneau des couches et la carte s'en
// déduisent ; une couche nouvelle s'ajoute par une entrée.

type Legende = "navires" | "trait" | "surface" | "cercles" | "passages" | "viirs" | null;

export interface Couche {
  id: string;
  groupe: "trafic" | "infrastructures" | "zones" | "satellites" | "activite" | "predictions";
  etape: number | null;          // null : en place ; sinon étape du plan où elle arrive
  defaut: boolean;
  calques: string[];             // identifiants des couches MapLibre
  couleur?: string;
  legende: Legende;
  /** Type d'infrastructure servi par l'API (/api/infrastructure, propriété « type ») */
  infra?: string;
}

/** Passages satellites : couleur du signal système, neutre (la couleur vive reste aux alertes) */
export const COULEUR_PASSAGE = "#9fb3c8";

export const COULEURS_INFRA: Record<string, string> = {
  "Câble télécom": "#4fb6c8", "Câble électrique": "#f0a84b", Pipeline: "#e07a5f", "Parc éolien": "#5fd38d",
};

export const COUCHES: Couche[] = [
  { id: "navires", groupe: "trafic", etape: null, defaut: true, calques: ["traffic"], legende: "navires" },
  { id: "trajectoires", groupe: "trafic", etape: null, defaut: true, calques: ["trails", "highlight", "highlight-dash"], legende: "trait", couleur: "#7c8b97" },
  { id: "electriques", groupe: "infrastructures", etape: null, defaut: true, calques: ["infra-electriques"], legende: "trait", couleur: COULEURS_INFRA["Câble électrique"], infra: "Câble électrique" },
  { id: "eoliens", groupe: "infrastructures", etape: null, defaut: true, calques: ["infra-eoliens-fond", "infra-eoliens"], legende: "surface", couleur: COULEURS_INFRA["Parc éolien"], infra: "Parc éolien" },
  { id: "telecoms", groupe: "infrastructures", etape: null, defaut: false, calques: ["infra-telecoms"], legende: "trait", couleur: COULEURS_INFRA["Câble télécom"], infra: "Câble télécom" },
  { id: "pipelines", groupe: "infrastructures", etape: null, defaut: false, calques: ["infra-pipelines"], legende: "trait", couleur: COULEURS_INFRA.Pipeline, infra: "Pipeline" },
  { id: "corridors", groupe: "infrastructures", etape: 2, defaut: false, calques: [], legende: "surface" },
  { id: "couverture", groupe: "zones", etape: null, defaut: false, calques: ["couverture", "couverture-fond"], legende: "trait", couleur: "#4fb6c8" },
  { id: "mouillages", groupe: "zones", etape: null, defaut: false, calques: ["zones"], legende: "surface", couleur: "#f0a84b" },
  // Zones de vigilance du panneau de contrôle (lot P) : trait clair en pointillé
  { id: "vigilance", groupe: "zones", etape: null, defaut: true, calques: ["vigilance-fond", "vigilance"], legende: "trait", couleur: "#c2ccd3" },
  { id: "reception", groupe: "zones", etape: null, defaut: false, calques: ["reception"], legende: "surface", couleur: "#4fb6c8" },
  // Analyses du Mac (zone tracée), échos Sentinel 1 (anneaux) et navires vus en optique Sentinel 2 (points pleins)
  { id: "detections", groupe: "satellites", etape: null, defaut: true, calques: ["aoi", "det", "alerts", "s1", "s2"], legende: "cercles" },
  // Emprises des passages Sentinel 1 et 2 de la plage : trait plein acquis, pointillé prévu (deux couches filtrées)
  { id: "passages", groupe: "satellites", etape: null, defaut: false, calques: ["passages-fond", "passages", "passages-prevus"],
    legende: "passages", couleur: COULEUR_PASSAGE },
  // Lumières détectées la nuit par VIIRS (lot B) : avec AIS (neutre), sans AIS (couleur du navire sombre), écartées
  { id: "viirs", groupe: "satellites", etape: null, defaut: true, calques: ["viirs"], legende: "viirs" },
  { id: "chaleur", groupe: "activite", etape: 3, defaut: false, calques: [], legende: "surface" },
  { id: "predictions", groupe: "predictions", etape: 4, defaut: false, calques: [], legende: "trait" },
];

export const GROUPES = ["trafic", "infrastructures", "zones", "satellites", "activite", "predictions"] as const;
const COUCHES_DEFAUT = COUCHES.filter((c) => c.defaut && c.etape == null).map((c) => c.id);
export const disponible = (c: Couche) => c.etape == null;

/** Vues prédéfinies de la carte (lot U) : couches actives de chacune ; « Couches » garde la liste détaillée */
export const VUES = {
  veille: ["navires", "trajectoires", "electriques", "eoliens", "vigilance"],   // carte calme : les points des dossiers ont leur couche
  trafic: COUCHES_DEFAUT,                     // l'écran par défaut d'avant la refonte
  satellites: ["navires", "electriques", "eoliens", "detections", "viirs", "passages", "couverture"],
} as const satisfies Record<string, readonly string[]>;
