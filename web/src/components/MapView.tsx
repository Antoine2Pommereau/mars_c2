import maplibregl, { type GeoJSONSource, type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import { bboxPolygon } from "../lib/geo";
import { EMPTY, type FC, type Feature, type Selection } from "../lib/types";
import { zonesGeoJSON } from "../lib/zones";
import { COULEUR_LISTE, COULEUR_SUIVI, STROKE_ALERTE } from "../registres/alertes";
import { COUCHES, COULEURS_INFRA } from "../registres/couches";

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
  zone: string | null;                   // filtre par zone des infrastructures
  concernedInfra: number[] | null;       // mode « concernées seulement » : identifiants à montrer
  focus: { center: [number, number]; zoom: number } | { bounds: [number, number, number, number] } | null;
  onSelect: (s: Selection) => void;
  drawing: boolean;
  draft: number[] | null;
  onDraw: (bbox: number[]) => void;
  spotlight: { alertId: number | null; vesselIds: number[]; infraId?: number; zone?: string } | null;
  focusGeom: FC;                         // géométrie de l'objet sélectionné, surlignée (infrastructure, zone)
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
  drawing.current = p.drawing;
  const onDraw = useRef(p.onDraw);
  onDraw.current = p.onDraw;

  // Création de la carte et des couches, une seule fois
  useEffect(() => {
    const map = new maplibregl.Map({ container: container.current!, style: STYLE, center: [1.5, 46.4], zoom: 5.2, boxZoom: false });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    mapRef.current = map;

    map.on("load", () => {
      const src = (id: string) => map.addSource(id, { type: "geojson", data: EMPTY as any });
      ["zones", "reception", "trails", "traffic", "aoi", "det", "alerts", "live", "highlight", "draft"].forEach(src);
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
      // Objet sélectionné (infrastructure, zone) : surligné au dessus de son groupe
      map.addLayer({ id: "focus-surface", type: "fill", source: "focus", filter: ["==", ["geometry-type"], "Polygon"],
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

      // Zone en cours de tracé : rouge si elle dépasse la taille maximale
      map.addLayer({ id: "draft-fill", type: "fill", source: "draft",
        paint: { "fill-color": ["case", ["get", "tooBig"], "#ef6461", "#4fb6c8"], "fill-opacity": 0.1 } });
      map.addLayer({ id: "draft-line", type: "line", source: "draft",
        paint: { "line-color": ["case", ["get", "tooBig"], "#ef6461", "#4fb6c8"], "line-width": 1.5 } });

      // Tracé d'une zone en deux clics : un coin, puis le coin opposé (fiable au trackpad comme à la souris)
      let start: maplibregl.LngLat | null = null;
      const box = (a: maplibregl.LngLat, b: maplibregl.LngLat) =>
        [Math.min(a.lng, b.lng), Math.min(a.lat, b.lat), Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)];
      map.on("click", (e) => {
        if (!drawing.current) { start = null; return; }
        if (!start) { start = e.lngLat; return; }
        const b = box(start, e.lngLat);
        start = null;
        if (b[2] - b[0] > 0.005 && b[3] - b[1] > 0.005) onDraw.current(b);
        else (map.getSource("draft") as GeoJSONSource).setData(EMPTY as any);
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
      map.on("click", "couverture-fond", (e) => {
        if (drawing.current || map.queryRenderedFeatures(e.point, { layers: ["traffic", "live", "alerts", "det"] }).length) return;
        onSelect.current({ kind: "zone", properties: { zone: (e.features![0].properties as any).zone } });
      });
      for (const id of ["traffic", "det", "alerts", "live"]) {
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


  useEffect(() => {
    alertIndex.current.clear();
    for (const f of [...p.analysisAlerts.features, ...p.liveAlerts.features]) alertIndex.current.set(f.properties.id, f);
  }, [p.analysisAlerts, p.liveAlerts]);

  // Couches actives (registre), couleur par type, filtres des infrastructures (zone, concernées seulement)
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
    map.setPaintProperty("traffic", "icon-color", shipColor(p.byType ? SHIP_COLOR : NEUTRAL));
  }, [ready, activesKey, p.byType]);

  const concernedKey = p.concernedInfra ? p.concernedInfra.join(",") : "tout";
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const map = mapRef.current;
    const extra: any[] = [];
    if (p.zone) extra.push(["==", ["downcase", ["coalesce", ["get", "region"], ""]], p.zone]);
    if (p.concernedInfra) extra.push(["in", ["get", "id"], ["literal", p.concernedInfra]]);
    const withExtra = (base: any) => (extra.length ? ["all", base, ...extra] : base);
    for (const c of INFRA_LIGNES) map.setFilter(c.calques[0], withExtra(["==", ["get", "type"], c.infra!]));
    for (const l of ["infra-eoliens-fond", "infra-eoliens"]) map.setFilter(l, withExtra(["==", ["get", "type"], "Parc éolien"]));
    map.setFilter("infra-noms", extra.length ? ["all", ...extra] : null);
  }, [ready, p.zone, concernedKey]);

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
      mapRef.current!.fitBounds([[x0, y0], [x1, y1]], { padding: 80, duration: 800, maxZoom: 11 });
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
  const spotKey = p.spotlight ? `${p.spotlight.alertId}:${p.spotlight.vesselIds.join(",")}:${p.spotlight.infraId}:${p.spotlight.zone}` : "";
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const map = mapRef.current;
    const s = p.spotlight;
    const ids = ["literal", s?.vesselIds ?? []];
    const dimTraffic = !!s && (s.vesselIds.length > 0 || s.alertId != null);
    map.setPaintProperty("traffic", "icon-opacity", dimTraffic ? ["case", ["in", ["get", "vessel_id"], ids], 1, 0.12]
      : s ? opaciteTrafic(0.5) : TRAFFIC_OPACITY);
    const infraOp = s?.infraId != null ? ["case", ["==", ["get", "id"], s.infraId], 1, 0.12] : s ? 0.25 : INFRA_OPACITY;
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
