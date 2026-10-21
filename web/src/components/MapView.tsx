import maplibregl, { type GeoJSONSource, type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import { bboxPolygon } from "../lib/geo";
import { EMPTY, type FC, type Feature, type Selection } from "../lib/types";
import { zonesGeoJSON } from "../lib/zones";
import { COULEUR_LISTE, COULEUR_SUIVI, STROKE_ALERTE } from "../registres/alertes";
import { COUCHES, COULEUR_PASSAGE, COULEURS_INFRA } from "../registres/couches";
import { URGENCE } from "../lib/urgence";
import { COULEURS_PREUVE as CP } from "../lib/preuve";

const STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

const SHIP_COLOR: any = ["match", ["coalesce", ["get", "ship_type"], ""],
  "Cargo", "#6ea8fe",
  "Tanker", "#f0a35e",
  "Fishing", "#5fd38d",
  ["Passenger", "HSC"], "#c792ea",
  ["Pleasure", "Sailing"], "#f5e663",
  ["Tug", "Towing", "Towing long/wide", "Pilot", "SAR", "Law enforcement", "Military", "Port tender",
   "Dredging", "Diving", "Anti-pollution", "Medical"], "#e07a5f",
  "#9fb3c2"];

const NEUTRAL = "#c9d3da";
// Couleur d'un navire : celle de son alerte ouverte la plus grave, sinon violet s'il est sur une liste, sinon neutre
const shipColor = (base: any): any => ["case", ["to-boolean", ["get", "alerte"]], ["get", "alerte"],
  ["to-boolean", ["get", "watch"]], COULEUR_LISTE, ["to-boolean", ["get", "suivi"]], COULEUR_SUIVI, base];
// Navire qui demande l'attention (alerte, liste, suivi) : il reste net et plus grand à toutes les échelles
const IMPORTANT: any = ["any", ["to-boolean", ["get", "alerte"]], ["to-boolean", ["get", "watch"]], ["to-boolean", ["get", "suivi"]]];
// Aux échelles larges, le trafic ordinaire s'estompe ; le détail revient en zoomant
// (k < 1 : version atténuée, quand une infrastructure ou une zone est sélectionnée ; l'interpolation sur le zoom doit
// rester l'expression la plus externe, d'où une fonction plutôt qu'un produit)
const opaciteTrafic = (k = 1): any => ["interpolate", ["linear"], ["zoom"],
  5, ["case", IMPORTANT, k, 0.22 * k],
  8.5, ["case", IMPORTANT, k, [">", ["get", "age_s"], 600], 0.3 * k, 0.9 * k]];
const TRAFFIC_OPACITY = opaciteTrafic();
// Infrastructures : estompées et fines aux échelles larges, nettes en zoomant (tracés simplifiés par l'API)
const INFRA_OPACITY: any = ["interpolate", ["linear"], ["zoom"], 5, 0.35, 9, 0.9];
const INFRA_WIDTH: any = ["interpolate", ["linear"], ["zoom"], 5, 0.7, 10, 1.6];
const INFRA_LIGNES = COUCHES.filter((c) => c.infra && c.infra !== "Parc éolien");

// Vue Veille (lot U) : carte calme. Navires en dossier colorés par l'urgence et nets, le reste du trafic en poussière
// grise ; alertes colorées par l'urgence (gravité) ; seules les infrastructures à moins d'un mille d'un dossier
// restent nettes. Les couleurs par type ne restent que dans la vue « Trafic complet ».
export type Vue = "veille" | "trafic" | "satellites";
const POUSSIERE = "#55636f";
const NIVEAU = ["coalesce", ["get", "niveau"], 0];
const COULEUR_VEILLE: any = ["match", NIVEAU, 3, URGENCE[3], 2, URGENCE[2], 1, URGENCE[1],
  ["case", ["to-boolean", ["get", "suivi"]], COULEUR_SUIVI, POUSSIERE]];
const OPACITE_VEILLE: any = ["case", [">", NIVEAU, 0], 1, ["to-boolean", ["get", "suivi"]], 0.9, 0.45];
const TAILLE_VEILLE: any = ["interpolate", ["linear"], ["zoom"], 5, ["case", [">", NIVEAU, 0], 0.42, 0.14],
  10, ["case", [">", NIVEAU, 0], 0.5, 0.3], 13, ["case", [">", NIVEAU, 0], 0.6, 0.48]];
const TAILLE_TRAFIC: any = ["interpolate", ["linear"], ["zoom"], 5, ["case", IMPORTANT, 0.34, 0.18], 10,
  ["case", IMPORTANT, 0.42, 0.34], 13, ["case", IMPORTANT, 0.56, 0.5]];
const ALERTE_URGENCE: any = ["match", ["get", "severity"], "critique", URGENCE[3], "elevee", URGENCE[2], URGENCE[1]];
const infraVeille = (ids: number[]): any => ["case", ["in", ["get", "id"], ["literal", ids]], 0.85, 0.06];

/** Icônes en champ de distance signé (teintables) : chevron pour un navire en route, point pour un navire immobile. */
function makeIcon(kind: "chevron" | "dot") {
  const n = 48;
  const c = document.createElement("canvas");
  c.width = c.height = n;
  const g = c.getContext("2d")!;
  g.fillStyle = "#fff";
  if (kind === "chevron") {
    g.beginPath(); g.moveTo(24, 4); g.lineTo(40, 44); g.lineTo(24, 34); g.lineTo(8, 44); g.closePath(); g.fill();
  } else {
    g.beginPath(); g.arc(24, 24, 11, 0, Math.PI * 2); g.fill();
  }
  return { width: n, height: n, data: new Uint8Array(g.getImageData(0, 0, n, n).data.buffer) };
}

interface Props {
  traffic: FC;
  trails: FC;
  aoi: Feature | null;
  detections: FC;
  analysisAlerts: FC;
  liveAlerts: FC;
  zones: FC | null;
  reception: FC | null;
  infrastructure: FC | null;
  highlight: FC;
  actives: string[];                     // couches actives (registres/couches.ts)
  byType: boolean;
  region: string | null;                 // région affichée : seul son contour reste dans la couche « couverture »
  passages: FC;                          // emprises des passages Sentinel 1 et 2 de la plage
  viirs: FC;                             // détections nocturnes VIIRS de la plage
  sentinel: FC;                          // échos radar Sentinel 1 et navires vus en optique Sentinel 2 de la plage
  concernedInfra: number[] | null;       // mode « concernées seulement » : identifiants à montrer
  focus: { center: [number, number]; zoom: number } | { bounds: [number, number, number, number] } | null;
  onSelect: (s: Selection) => void;
  drawing: boolean;
  draft: number[] | null;
  onDraw: (bbox: number[]) => void;
  spotlight: { alertId: number | null; vesselIds: number[]; infraId?: number; zone?: string } | null;
  focusGeom: FC;                         // géométrie de l'objet sélectionné, surlignée (infrastructure, zone)
  onContexte?: (pt: { lon: number; lat: number; x: number; y: number }) => void;   // clic droit (lot T1)
  vigilance: FC;                         // zones de vigilance actives (lot P)
  trace: number[][] | null;              // polygone d'une zone en cours de tracé (lot P) : un clic, un sommet
  onTracePoint: (lonLat: number[]) => void;
  vue: Vue;                              // vue prédéfinie (lot U) : Veille, Trafic complet, Satellites
  infraProches: number[];                // infrastructures à moins d'un mille d'un dossier (restent nettes en Veille)
  dossiersPoints: FC;                    // dossiers « point » (lumière, écho sans navire), colorés par l'urgence
  preuve: FC;                            // preuve du dossier ouvert (lib/preuve.ts), au dessus de tout
}

// Mode focus : tout ce qui ne concerne pas la sélection s'efface
const ALERT_OPACITY: any = ["case", ["any", ["==", ["get", "severity"], "faible"],
  ["==", ["get", "status"], "classee"], ["==", ["get", "status"], "acquittee"]], 0.35, 1];

export default function MapView(p: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const [ready, setReady] = useState(false);
  const onSelect = useRef(p.onSelect);
  onSelect.current = p.onSelect;
  const alertIndex = useRef(new Map<number, Feature>());
  const drawing = useRef(p.drawing);
  // Tracé d'une zone de vigilance : les clics ajoutent des sommets, rien d'autre ne se sélectionne
  drawing.current = p.drawing || p.trace != null;
  const traceRef = useRef(p.trace);
  traceRef.current = p.trace;
  const onTracePoint = useRef(p.onTracePoint);
  onTracePoint.current = p.onTracePoint;
  const onDraw = useRef(p.onDraw);
  onDraw.current = p.onDraw;
  const onContexte = useRef(p.onContexte);
  onContexte.current = p.onContexte;

  // Création de la carte et des couches, une seule fois
  useEffect(() => {
    const map = new maplibregl.Map({ container: container.current!, style: STYLE, center: [1.5, 46.4], zoom: 5.2, boxZoom: false });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    mapRef.current = map;

    map.on("load", () => {
      const src = (id: string) => map.addSource(id, { type: "geojson", data: EMPTY as any });
      ["zones", "reception", "trails", "traffic", "aoi", "det", "alerts", "live", "highlight", "draft", "passages", "viirs", "s1",
        "vigilance", "trace", "dossiers", "preuve"].forEach(src);
      // Infrastructures : simplification plus forte aux échelles larges (tolérance en pixels par niveau de zoom)
      map.addSource("infra", { type: "geojson", data: EMPTY as any, tolerance: 1.5 });
      map.addSource("couverture", { type: "geojson", data: zonesGeoJSON() as any });
      map.addSource("focus", { type: "geojson", data: EMPTY as any });

      // Infrastructures sous marines (sous le trafic), une couche par type
      map.addLayer({ id: "infra-eoliens-fond", type: "fill", source: "infra", layout: { visibility: "none" },
        filter: ["==", ["get", "type"], "Parc éolien"],
        paint: { "fill-color": COULEURS_INFRA["Parc éolien"], "fill-opacity": 0.12 } });
      map.addLayer({ id: "infra-eoliens", type: "line", source: "infra", layout: { visibility: "none" },
        filter: ["==", ["get", "type"], "Parc éolien"],
        paint: { "line-color": COULEURS_INFRA["Parc éolien"], "line-width": 1, "line-opacity": INFRA_OPACITY } });
      for (const c of INFRA_LIGNES) {
        map.addLayer({ id: c.calques[0], type: "line", source: "infra", layout: { visibility: "none", "line-cap": "round" },
          filter: ["==", ["get", "type"], c.infra!],
          paint: { "line-color": c.couleur!, "line-width": INFRA_WIDTH, "line-opacity": INFRA_OPACITY } });
      }
      // Noms des infrastructures, à partir du zoom 9
      map.addLayer({ id: "infra-noms", type: "symbol", source: "infra", minzoom: 9,
        layout: { "symbol-placement": "line", "text-field": ["coalesce", ["get", "name"], ""], "text-size": 10.5,
                  "text-font": ["Montserrat Regular", "Open Sans Regular", "Noto Sans Regular"] },
        paint: { "text-color": "#7c8b97", "text-halo-color": "#0e1419", "text-halo-width": 1.2 } });
      map.addLayer({ id: "couverture-fond", type: "fill", source: "couverture", layout: { visibility: "none" },
        paint: { "fill-color": "#4fb6c8", "fill-opacity": 0 } });
      map.addLayer({ id: "couverture", type: "line", source: "couverture", layout: { visibility: "none" },
        paint: { "line-color": "#4fb6c8", "line-width": 1, "line-opacity": 0.5, "line-dasharray": [3, 3] } });
      // Passages satellites : emprise à peine teintée, trait plein acquis, pointillé prévu (pas de pointillé dépendant
      // des données dans MapLibre : deux couches filtrées)
      map.addLayer({ id: "passages-fond", type: "fill", source: "passages", layout: { visibility: "none" },
        paint: { "fill-color": COULEUR_PASSAGE, "fill-opacity": 0.04 } });
      map.addLayer({ id: "passages", type: "line", source: "passages", layout: { visibility: "none" },
        filter: ["!=", ["get", "statut"], "prevu"], paint: { "line-color": COULEUR_PASSAGE, "line-width": 1, "line-opacity": 0.6 } });
      map.addLayer({ id: "passages-prevus", type: "line", source: "passages", layout: { visibility: "none" },
        filter: ["==", ["get", "statut"], "prevu"],
        paint: { "line-color": COULEUR_PASSAGE, "line-width": 1, "line-opacity": 0.6, "line-dasharray": [2, 3] } });
      // Objet sélectionné (infrastructure, zone, passage) : surligné au dessus de son groupe
      map.addLayer({ id: "focus-surface", type: "fill", source: "focus", filter: ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]],
        paint: { "fill-color": "#e6ecf0", "fill-opacity": 0.05 } });
      map.addLayer({ id: "focus-ligne", type: "line", source: "focus",
        paint: { "line-color": "#e6ecf0", "line-width": 2.5, "line-opacity": 0.85 } });

      map.addLayer({ id: "zones", type: "fill", source: "zones", layout: { visibility: "none" },
        paint: { "fill-color": "#f0a84b", "fill-opacity": 0.12, "fill-outline-color": "#f0a84b" } });
      map.addLayer({ id: "reception", type: "fill", source: "reception", layout: { visibility: "none" },
        paint: { "fill-color": "#4fb6c8", "fill-opacity": 0.07, "fill-outline-color": "#4fb6c8" } });
      map.addLayer({ id: "trails", type: "line", source: "trails",
        paint: { "line-color": "#7c8b97", "line-width": 1, "line-opacity": 0.45 } });
      map.addImage("chevron", makeIcon("chevron"), { sdf: true });
      map.addImage("dot", makeIcon("dot"), { sdf: true });
      map.addLayer({ id: "traffic", type: "symbol", source: "traffic",
        layout: {
          "icon-image": ["case", ["<", ["coalesce", ["get", "sog_kn"], 0], 0.5], "dot", "chevron"],
          "icon-size": ["interpolate", ["linear"], ["zoom"], 5, ["case", IMPORTANT, 0.34, 0.18], 10, ["case", IMPORTANT, 0.42, 0.34],
                        13, ["case", IMPORTANT, 0.56, 0.5]],
          "symbol-sort-key": ["case", IMPORTANT, 1, 0],
          "icon-rotate": ["coalesce", ["get", "cog_deg"], 0],
          "icon-rotation-alignment": "map",
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
        paint: {
          "icon-color": shipColor(NEUTRAL),
          "icon-opacity": TRAFFIC_OPACITY,
        } });
      // Dossiers (lot U) : nom des navires à examiner, en Veille ; points sans navire en losange de leur urgence
      map.addLayer({ id: "dossiers-noms", type: "symbol", source: "traffic", filter: [">=", NIVEAU, 2] as any,
        layout: { visibility: "none", "text-field": ["coalesce", ["get", "name"], ""], "text-size": 11.5, "text-offset": [0.9, 0],
                  "text-anchor": "left", "text-font": ["Montserrat Medium", "Open Sans Semibold", "Noto Sans Regular"] },
        paint: { "text-color": "#e6ecf0", "text-halo-color": "#0e1419", "text-halo-width": 1.6 } });
      map.addLayer({ id: "dossiers-points", type: "circle", source: "dossiers",
        paint: { "circle-radius": ["match", NIVEAU, 3, 9, 2, 7, 5], "circle-color": "rgba(0,0,0,0)",
                 "circle-stroke-width": ["match", NIVEAU, 1, 1.2, 2],
                 "circle-stroke-color": ["match", NIVEAU, 3, URGENCE[3], 2, URGENCE[2], URGENCE[1]] } as any });
      map.addLayer({ id: "aoi", type: "line", source: "aoi",
        paint: { "line-color": "#4fb6c8", "line-width": 1.2, "line-dasharray": [4, 3] } });
      map.addLayer({ id: "det", type: "circle", source: "det",
        paint: {
          "circle-radius": ["case", ["!=", ["get", "mask_reason"], null], 2, 8],
          "circle-color": ["case", ["!=", ["get", "mask_reason"], null], "#4c5a66", "rgba(0,0,0,0)"],
          "circle-stroke-width": ["case", ["!=", ["get", "mask_reason"], null], 0, 1.5],
          "circle-stroke-color": ["case",
            ["!=", ["get", "mask_reason"], null], "#4c5a66",
            ["!=", ["get", "matched_mmsi"], null], "#e6ecf0",
            "#e85bc7"],
        } as any });
      // Détections nocturnes VIIRS : point lumineux, neutre avec AIS, couleur du navire sombre sans AIS, discret écartée
      map.addLayer({ id: "viirs", type: "circle", source: "viirs", layout: { visibility: "none" },
        paint: {
          "circle-radius": ["match", ["get", "statut"], "ecartee", 2, "non_evaluable", 3, "sans_ais", 5.5, 4],
          "circle-color": ["match", ["get", "statut"], "ecartee", "#4c5a66", "non_evaluable", "#7c8b97", "sans_ais", "#e85bc7", "#e6ecf0"],
          "circle-opacity": ["match", ["get", "statut"], "avec_ais", 0.55, "non_evaluable", 0.5, 0.9],
          "circle-stroke-width": ["match", ["get", "statut"], "sans_ais", 4, 0],
          "circle-stroke-color": "#e85bc7", "circle-stroke-opacity": 0.25,
        } as any });
      // Échos radar Sentinel 1 : anneau (un écho, pas une lumière), mêmes couleurs que VIIRS selon le statut
      map.addLayer({ id: "s1", type: "circle", source: "s1", layout: { visibility: "none" },
        filter: ["!=", ["get", "source"], "sentinel2"],
        paint: {
          "circle-radius": ["match", ["get", "statut"], "ecartee", 2.5, "non_evaluable", 3.5, "sans_ais", 6, 4.5],
          "circle-color": "rgba(0,0,0,0)",
          "circle-stroke-width": ["match", ["get", "statut"], "sans_ais", 2.5, 1.5],
          "circle-stroke-color": ["match", ["get", "statut"], "ecartee", "#4c5a66", "non_evaluable", "#7c8b97", "sans_ais", "#e85bc7", "#e6ecf0"],
          "circle-stroke-opacity": ["match", ["get", "statut"], "avec_ais", 0.6, "non_evaluable", 0.5, 0.95],
        } as any });
      // Navires vus en optique Sentinel 2 (lot S2) : point plein cerclé de sombre (une image, pas un écho), mêmes
      // couleurs selon le statut ; même source de données que le radar
      map.addLayer({ id: "s2", type: "circle", source: "s1", layout: { visibility: "none" },
        filter: ["==", ["get", "source"], "sentinel2"],
        paint: {
          "circle-radius": ["match", ["get", "statut"], "ecartee", 2.5, "non_evaluable", 3, "sans_ais", 5, 4],
          "circle-color": ["match", ["get", "statut"], "ecartee", "#4c5a66", "non_evaluable", "#7c8b97", "sans_ais", "#e85bc7", "#e6ecf0"],
          "circle-opacity": ["match", ["get", "statut"], "avec_ais", 0.6, "non_evaluable", 0.5, 0.95],
          "circle-stroke-width": 1.5, "circle-stroke-color": "#0e1419",
        } as any });
      // Trajectoires surlignées (traits pleins) et trajet présumé d'une coupure AIS (pointillés)
      map.addLayer({ id: "highlight", type: "line", source: "highlight", filter: ["!=", ["get", "dashed"], true],
        paint: { "line-color": ["coalesce", ["get", "color"], "#f0a84b"], "line-width": 2.5 } });
      map.addLayer({ id: "highlight-dash", type: "line", source: "highlight", filter: ["==", ["get", "dashed"], true],
        paint: { "line-color": ["coalesce", ["get", "color"], "#ef6461"], "line-width": 2, "line-dasharray": [2, 2] } });
      for (const id of ["alerts", "live"]) {
        map.addLayer({ id, type: "circle", source: id,
          paint: {
            "circle-radius": 14, "circle-color": "rgba(255,255,255,0.04)", "circle-stroke-width": 2,
            "circle-stroke-color": STROKE_ALERTE,
            "circle-stroke-opacity": ALERT_OPACITY,
          } });
      }

      // Zones de vigilance (lot P) : trait clair en pointillé, surface à peine visible (la couleur reste aux alertes)
      map.addLayer({ id: "vigilance-fond", type: "fill", source: "vigilance", paint: { "fill-color": "#c2ccd3", "fill-opacity": 0.05 } });
      map.addLayer({ id: "vigilance", type: "line", source: "vigilance",
        paint: { "line-color": "#c2ccd3", "line-width": 1.4, "line-opacity": 0.8, "line-dasharray": [3, 2] } });
      map.addLayer({ id: "trace-ligne", type: "line", source: "trace", paint: { "line-color": "#4fb6c8", "line-width": 1.5 } });
      map.addLayer({ id: "trace-points", type: "circle", source: "trace", filter: ["==", ["geometry-type"], "Point"],
        paint: { "circle-radius": 3.5, "circle-color": "#4fb6c8" } });
      // Zone en cours de tracé : rouge si elle dépasse la taille maximale
      map.addLayer({ id: "draft-fill", type: "fill", source: "draft",
        paint: { "fill-color": ["case", ["get", "tooBig"], "#ef6461", "#4fb6c8"], "fill-opacity": 0.1 } });
      map.addLayer({ id: "draft-line", type: "line", source: "draft",
        paint: { "line-color": ["case", ["get", "tooBig"], "#ef6461", "#4fb6c8"], "line-width": 1.5 } });

      // Preuve du dossier ouvert (lot U) : route, silence en tirets jusqu'à l'écho, cercle d'incertitude autour de la
      // position estimée, écart estimée vers écho avec sa distance ; mêmes couleurs que la légende du récit
      const role = (r: string): any => ["==", ["get", "role"], r];
      map.addLayer({ id: "preuve-route", type: "line", source: "preuve", filter: role("route"),
        paint: { "line-color": CP.route, "line-width": 1.5, "line-opacity": 0.85 } });
      map.addLayer({ id: "preuve-cercle-fond", type: "fill", source: "preuve", filter: role("cercle"),
        paint: { "fill-color": CP.cercle, "fill-opacity": 0.05 } });
      map.addLayer({ id: "preuve-cercle", type: "line", source: "preuve", filter: role("cercle"),
        paint: { "line-color": CP.cercle, "line-width": 1, "line-opacity": 0.6 } });
      map.addLayer({ id: "preuve-silence", type: "line", source: "preuve", filter: role("silence"),
        paint: { "line-color": CP.silence, "line-width": 1.8, "line-dasharray": [2, 2] } });
      map.addLayer({ id: "preuve-ecart", type: "line", source: "preuve", filter: role("ecart"),
        paint: { "line-color": CP.ecart, "line-width": 1, "line-opacity": 0.8 } });
      map.addLayer({ id: "preuve-ecart-texte", type: "symbol", source: "preuve", filter: role("ecart"),
        layout: { "symbol-placement": "line-center", "text-field": ["get", "etiquette"], "text-size": 12, "text-offset": [0, -0.8],
                  "text-font": ["Montserrat Medium", "Open Sans Semibold", "Noto Sans Regular"], "text-allow-overlap": true },
        paint: { "text-color": CP.ecart, "text-halo-color": "#0e1419", "text-halo-width": 1.6 } });
      map.addLayer({ id: "preuve-estimee", type: "circle", source: "preuve", filter: role("estimee"),
        paint: { "circle-radius": 3, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 1.2, "circle-stroke-color": CP.cercle } });
      map.addLayer({ id: "preuve-derniere", type: "circle", source: "preuve", filter: role("derniere"),
        paint: { "circle-radius": 4, "circle-color": CP.derniere, "circle-stroke-width": 1.5, "circle-stroke-color": "#0e1419" } });
      map.addLayer({ id: "preuve-echo", type: "circle", source: "preuve", filter: role("echo"),
        paint: { "circle-radius": 7, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 2.5, "circle-stroke-color": CP.echo } });

      // Tracé d'une zone en deux clics : un coin, puis le coin opposé (fiable au trackpad comme à la souris)
      let start: maplibregl.LngLat | null = null;
      const box = (a: maplibregl.LngLat, b: maplibregl.LngLat) =>
        [Math.min(a.lng, b.lng), Math.min(a.lat, b.lat), Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)];
      map.on("click", (e) => {
        if (traceRef.current) { onTracePoint.current([e.lngLat.lng, e.lngLat.lat]); return; }
        if (!drawing.current) { start = null; return; }
        if (!start) { start = e.lngLat; return; }
        const b = box(start, e.lngLat);
        start = null;
        if (b[2] - b[0] > 0.005 && b[3] - b[1] > 0.005) onDraw.current(b);
        else (map.getSource("draft") as GeoJSONSource).setData(EMPTY as any);
      });
      // Clic droit : menu de la carte (demande d'observation satellite sur le point)
      map.on("contextmenu", (e) => {
        if (!drawing.current) onContexte.current?.({ lon: e.lngLat.lng, lat: e.lngLat.lat, x: e.point.x, y: e.point.y });
      });
      map.on("mousemove", (e) => {
        if (drawing.current && start) (map.getSource("draft") as GeoJSONSource).setData(bboxPolygon(box(start, e.lngLat)) as any);
      });

      map.on("click", "traffic", (e) => { if (!drawing.current) onSelect.current({ kind: "vessel", properties: e.features![0].properties as any }); });
      map.on("click", "det", (e) => {
        if (drawing.current) return;
        const f = e.features![0];
        const [lon, lat] = (f.geometry as any).coordinates;
        onSelect.current({ kind: "detection", properties: { ...(f.properties as any), lon, lat } });
      });
      for (const id of ["alerts", "live"]) {
        map.on("click", id, (e) => {
          if (drawing.current) return;
          const f = alertIndex.current.get(Number(e.features![0].properties!.id));
          if (f) onSelect.current({ kind: "alert", feature: f });
        });
      }
      // Infrastructure et zone : un clic ouvre la fiche
      for (const id of [...INFRA_LIGNES.map((c) => c.calques[0]), "infra-eoliens-fond"]) {
        map.on("click", id, (e) => {
          if (drawing.current) return;
          const pr = e.features![0].properties as any;
          onSelect.current({ kind: "infrastructure", properties: { id: pr.id, name: pr.name, type: pr.type } });
        });
        map.on("mouseenter", id, () => (map.getCanvas().style.cursor = drawing.current ? "crosshair" : "pointer"));
        map.on("mouseleave", id, () => (map.getCanvas().style.cursor = drawing.current ? "crosshair" : ""));
      }
      map.on("click", "passages-fond", (e) => {
        const dessus = ["traffic", "live", "alerts", "det", "infra-eoliens-fond", ...INFRA_LIGNES.map((c) => c.calques[0])]
          .filter((l) => map.getLayoutProperty(l, "visibility") !== "none");
        if (drawing.current || map.queryRenderedFeatures(e.point, { layers: dessus }).length) return;
        // Plusieurs emprises se recouvrent : la plus récente
        const fs = e.features!.map((f) => f.properties as any).sort((a, b) => String(b.acquired_at).localeCompare(String(a.acquired_at)));
        onSelect.current({ kind: "passage", properties: fs[0] });
      });
      map.on("click", "vigilance-fond", (e) => {
        const dessus = ["traffic", "live", "alerts", "det", "viirs", "s1", "s2"].filter((l) => map.getLayoutProperty(l, "visibility") !== "none");
        if (drawing.current || map.queryRenderedFeatures(e.point, { layers: dessus }).length) return;
        onSelect.current({ kind: "vigilance", properties: e.features![0].properties as any });
      });
      map.on("click", "couverture-fond", (e) => {
        if (drawing.current || map.queryRenderedFeatures(e.point, { layers: ["traffic", "live", "alerts", "det"] }).length) return;
        onSelect.current({ kind: "zone", properties: { zone: (e.features![0].properties as any).zone } });
      });
      map.on("click", "viirs", (e) => {
        if (drawing.current) return;
        const f = e.features![0];
        const [lon, lat] = (f.geometry as any).coordinates;
        onSelect.current({ kind: "detection", properties: { ...(f.properties as any), source: "viirs", lon, lat } });
      });
      for (const id of ["s1", "s2"]) map.on("click", id, (e) => {
        if (drawing.current) return;
        const f = e.features![0];
        const [lon, lat] = (f.geometry as any).coordinates;
        onSelect.current({ kind: "detection", properties: { ...(f.properties as any), source: id === "s2" ? "sentinel2" : "sentinel1",
          lon, lat } });
      });
      for (const id of ["traffic", "det", "alerts", "live", "viirs", "s1", "s2"]) {
        map.on("mouseenter", id, () => (map.getCanvas().style.cursor = drawing.current ? "crosshair" : "pointer"));
        map.on("mouseleave", id, () => (map.getCanvas().style.cursor = drawing.current ? "crosshair" : ""));
      }
      setReady(true);
    });
    return () => map.remove();
  }, []);

  const setData = (id: string, data: FC | Feature | null) => {
    if (!ready || !mapRef.current) return;
    (mapRef.current.getSource(id) as GeoJSONSource | undefined)?.setData((data ?? EMPTY) as any);
  };

  useEffect(() => setData("traffic", p.traffic), [ready, p.traffic]);
  useEffect(() => setData("trails", p.trails), [ready, p.trails]);
  useEffect(() => setData("aoi", p.aoi), [ready, p.aoi]);
  useEffect(() => setData("det", p.detections), [ready, p.detections]);
  useEffect(() => setData("alerts", p.analysisAlerts), [ready, p.analysisAlerts]);
  useEffect(() => setData("live", p.liveAlerts), [ready, p.liveAlerts]);
  useEffect(() => setData("zones", p.zones), [ready, p.zones]);
  useEffect(() => setData("reception", p.reception), [ready, p.reception]);
  useEffect(() => setData("infra", p.infrastructure), [ready, p.infrastructure]);
  useEffect(() => setData("highlight", p.highlight), [ready, p.highlight]);
  useEffect(() => setData("focus", p.focusGeom), [ready, p.focusGeom]);
  useEffect(() => setData("passages", p.passages), [ready, p.passages]);
  useEffect(() => setData("viirs", p.viirs), [ready, p.viirs]);
  useEffect(() => setData("vigilance", p.vigilance), [ready, p.vigilance]);
  useEffect(() => setData("dossiers", p.dossiersPoints), [ready, p.dossiersPoints]);
  useEffect(() => setData("preuve", p.preuve), [ready, p.preuve]);
  useEffect(() => {
    const t = p.trace ?? [];
    setData("trace", { type: "FeatureCollection", features: [
      ...t.map((c) => ({ type: "Feature", geometry: { type: "Point", coordinates: c }, properties: {} })),
      ...(t.length > 1 ? [{ type: "Feature", geometry: { type: "LineString", coordinates: t.length > 2 ? [...t, t[0]] : t }, properties: {} }] : []),
    ] } as FC);
    if (ready && mapRef.current) mapRef.current.getCanvas().style.cursor = p.trace ? "crosshair" : "";
  }, [ready, p.trace]);
  useEffect(() => setData("s1", p.sentinel), [ready, p.sentinel]);
  useEffect(() => {
    const z = zonesGeoJSON();
    setData("couverture", p.region ? { ...z, features: z.features.filter((f) => f.properties.zone === p.region) } as FC : z as FC);
  }, [ready, p.region]);


  useEffect(() => {
    alertIndex.current.clear();
    for (const f of [...p.analysisAlerts.features, ...p.liveAlerts.features]) alertIndex.current.set(f.properties.id, f);
  }, [p.analysisAlerts, p.liveAlerts]);

  // Couches actives (registre), couleur par type, filtre des infrastructures (concernées seulement ; la région est
  // appliquée par l'API)
  const activesKey = p.actives.join(",");
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const map = mapRef.current;
    for (const c of COUCHES) for (const l of c.calques) {
      if (map.getLayer(l)) map.setLayoutProperty(l, "visibility", p.actives.includes(c.id) ? "visible" : "none");
    }
    const anyInfra = COUCHES.some((c) => c.infra && p.actives.includes(c.id));
    map.setLayoutProperty("infra-noms", "visibility", anyInfra ? "visible" : "none");
    map.setLayoutProperty("live", "visibility", "visible");
    const veille = p.vue === "veille";
    map.setPaintProperty("traffic", "icon-color", veille ? COULEUR_VEILLE : shipColor(p.byType ? SHIP_COLOR : NEUTRAL));
    map.setLayoutProperty("traffic", "icon-size", veille ? TAILLE_VEILLE : TAILLE_TRAFIC);
    map.setLayoutProperty("dossiers-noms", "visibility", veille ? "visible" : "none");
    for (const id of ["alerts", "live"]) map.setPaintProperty(id, "circle-stroke-color", veille ? ALERTE_URGENCE : STROKE_ALERTE);
  }, [ready, activesKey, p.byType, p.vue]);

  const concernedKey = p.concernedInfra ? p.concernedInfra.join(",") : "tout";
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const map = mapRef.current;
    const extra: any[] = [];
    if (p.concernedInfra) extra.push(["in", ["get", "id"], ["literal", p.concernedInfra]]);
    const withExtra = (base: any) => (extra.length ? ["all", base, ...extra] : base);
    for (const c of INFRA_LIGNES) map.setFilter(c.calques[0], withExtra(["==", ["get", "type"], c.infra!]));
    for (const l of ["infra-eoliens-fond", "infra-eoliens"]) map.setFilter(l, withExtra(["==", ["get", "type"], "Parc éolien"]));
    map.setFilter("infra-noms", extra.length ? ["all", ...extra] : null);
  }, [ready, concernedKey]);

  // Cadrage sur la zone analysée quand une nouvelle analyse s'affiche
  const lastAoi = useRef<number | null>(null);
  useEffect(() => {
    if (!ready || !p.aoi || lastAoi.current === p.aoi.properties.id) return;
    lastAoi.current = p.aoi.properties.id;
    const b = new maplibregl.LngLatBounds();
    (p.aoi.geometry.coordinates[0] as [number, number][]).forEach((c) => b.extend(c));
    mapRef.current!.fitBounds(b, { padding: 60, duration: 800 });
  }, [ready, p.aoi]);

  useEffect(() => {
    if (!ready || !p.focus) return;
    if ("bounds" in p.focus) {
      const [x0, y0, x1, y1] = p.focus.bounds;
      mapRef.current!.fitBounds([[x0, y0], [x1, y1]], { padding: { top: 80, bottom: 200, left: 60, right: 430 }, duration: 800, maxZoom: 11 });
    } else mapRef.current!.flyTo({ center: p.focus.center, zoom: p.focus.zoom });
  }, [ready, p.focus]);

  // Mode tracé : la carte ne se déplace plus au glisser, le curseur devient une croix
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const map = mapRef.current;
    if (p.drawing) map.doubleClickZoom.disable(); else map.doubleClickZoom.enable();
    map.getCanvas().style.cursor = p.drawing ? "crosshair" : "";
  }, [ready, p.drawing]);

  useEffect(() => setData("draft", p.draft ? (bboxPolygon(p.draft) as FC) : null), [ready, p.draft]);

  // Mode focus sur la sélection
  const spotKey = (p.spotlight ? `${p.spotlight.alertId}:${p.spotlight.vesselIds.join(",")}:${p.spotlight.infraId}:${p.spotlight.zone}` : "")
    + `|${p.vue}|${p.infraProches.join(",")}`;
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const map = mapRef.current;
    const s = p.spotlight;
    const ids = ["literal", s?.vesselIds ?? []];
    const dimTraffic = !!s && (s.vesselIds.length > 0 || s.alertId != null);
    const veille = p.vue === "veille";
    map.setPaintProperty("traffic", "icon-opacity", dimTraffic ? ["case", ["in", ["get", "vessel_id"], ids], 1, 0.12]
      : s ? opaciteTrafic(0.5) : veille ? OPACITE_VEILLE : TRAFFIC_OPACITY);
    const infraOp = s?.infraId != null ? ["case", ["==", ["get", "id"], s.infraId], 1, 0.12] : s ? 0.25
      : veille ? infraVeille(p.infraProches) : INFRA_OPACITY;
    for (const c of INFRA_LIGNES) map.setPaintProperty(c.calques[0], "line-opacity", infraOp);
    map.setPaintProperty("infra-eoliens", "line-opacity", infraOp);
    map.setLayoutProperty("traffic", "symbol-sort-key", s ? ["case", ["in", ["get", "vessel_id"], ids], 2, IMPORTANT, 1, 0]
      : ["case", IMPORTANT, 1, 0]);
    map.setPaintProperty("trails", "line-opacity", s ? ["case", ["in", ["get", "vessel_id"], ids], 0.9, 0.06] : 0.45);
    map.setPaintProperty("det", "circle-stroke-opacity", s ? 0.35 : 1);
    for (const id of ["alerts", "live"]) {
      map.setPaintProperty(id, "circle-stroke-opacity", s?.alertId != null ? ["case", ["==", ["get", "id"], s.alertId], 1, 0.15]
        : s?.zone ? ["case", ["==", ["get", "zone"], s.zone], ALERT_OPACITY, 0.12] : s ? 0.3 : ALERT_OPACITY);
    }
  }, [ready, spotKey]);

  return <div ref={container} style={{ position: "absolute", inset: 0 }} />;
}
