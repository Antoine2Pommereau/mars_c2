import maplibregl, { type ExpressionSpecification, type GeoJSONSource, type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import { bboxPolygon } from "../lib/geo";
import { ALERT_COLOR, ALERT_FALLBACK, BATHY_CONTOUR, INFRA_CABLE, INFRA_PIPELINE, SHIP_NEUTRAL, SHIP_OTHER, SHIP_PALETTE, SIGNAL } from "../lib/format";
import { EMPTY, type AlertProps, type FC, type Feature, type Selection } from "../lib/types";

const STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

// Couleur par type de navire, construite depuis la palette centralisée (lib/format.ts)
const SHIP_COLOR: ExpressionSpecification = ["match", ["coalesce", ["get", "ship_type"], ""],
  ...SHIP_PALETTE.flatMap(({ types, color }) => [types.length === 1 ? types[0] : types, color] as const),
  SHIP_OTHER] as unknown as ExpressionSpecification;

// Couleur par type d'alerte, depuis la palette centralisée
const ALERT_STROKE: ExpressionSpecification = ["match", ["get", "type"],
  "DARK_SHIP", ALERT_COLOR.DARK_SHIP, "RENDEZVOUS", ALERT_COLOR.RENDEZVOUS,
  "AIS_GAP", ALERT_COLOR.AIS_GAP, "AIS_UNCONFIRMED", ALERT_COLOR.AIS_UNCONFIRMED, ALERT_FALLBACK];
const NEUTRAL = SHIP_NEUTRAL;

// Couleur des infrastructures provisionnées : câble ou pipeline
const INFRA_COLOR: ExpressionSpecification = ["match", ["get", "kind"], "pipeline", INFRA_PIPELINE, INFRA_CABLE];

// Les types de maplibre n'acceptent pas la comparaison littérale à null, pourtant valide à l'exécution.
const expr = (e: unknown): ExpressionSpecification => e as ExpressionSpecification;

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
  analysisAlerts: FC<AlertProps>;
  liveAlerts: FC<AlertProps>;
  zones: FC | null;
  reception: FC | null;
  infrastructure: FC | null;
  bathymetry: FC | null;
  highlight: FC;
  show: { analysis: boolean; zones: boolean; reception: boolean; byType: boolean; infrastructure: boolean; bathymetry: boolean };
  focus: { center: [number, number]; zoom: number } | null;
  onSelect: (s: Selection) => void;
  drawing: boolean;
  draft: number[] | null;
  onDraw: (bbox: number[]) => void;
  spotlight: { alertId: number | null; vesselIds: number[] } | null;
  onBounds: (b: number[]) => void;
}

// Opacités de base, et mode focus : tout ce qui ne concerne pas la sélection s'efface
const TRAFFIC_OPACITY: ExpressionSpecification = ["case", [">", ["get", "age_s"], 600], 0.3, 0.9];
const ALERT_OPACITY: ExpressionSpecification = ["case", ["any", ["==", ["get", "severity"], "faible"],
  ["==", ["get", "status"], "classee"], ["==", ["get", "status"], "acquittee"]], 0.35, 1];

export default function MapView(p: Props) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const [ready, setReady] = useState(false);
  const onSelect = useRef(p.onSelect);
  onSelect.current = p.onSelect;
  const alertIndex = useRef(new Map<number, Feature<AlertProps>>());
  const drawing = useRef(p.drawing);
  drawing.current = p.drawing;
  const onDraw = useRef(p.onDraw);
  onDraw.current = p.onDraw;
  const onBounds = useRef(p.onBounds);
  onBounds.current = p.onBounds;

  // Création de la carte et des couches, une seule fois
  useEffect(() => {
    // StrictMode monte puis démonte l'effet deux fois : ce drapeau empêche d'agir sur une carte déjà retirée
    let disposed = false;
    let draftFrame = 0; // limite la mise à jour du brouillon à une image par rafraîchissement
    const map = new maplibregl.Map({ container: container.current!, style: STYLE, center: [10.6, 57.6], zoom: 7.5, boxZoom: false });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    mapRef.current = map;

    map.on("load", () => {
      if (disposed) return;
      const src = (id: string) => map.addSource(id, { type: "geojson", data: EMPTY as any });
      ["bathymetry", "infrastructure", "zones", "reception", "trails", "traffic", "aoi", "det", "alerts", "live", "highlight", "draft"].forEach(src);

      // Couches de contexte provisionnées (régions), sous les données opérationnelles
      map.addLayer({ id: "bathymetry", type: "line", source: "bathymetry", layout: { visibility: "none" },
        paint: { "line-color": BATHY_CONTOUR, "line-width": ["interpolate", ["linear"], ["zoom"], 6, 0.4, 11, 1], "line-opacity": 0.55 } });
      // Étiquettes de profondeur le long des isobathes (depth_m est négatif, on affiche la profondeur positive)
      map.addLayer({ id: "bathymetry-labels", type: "symbol", source: "bathymetry",
        layout: {
          visibility: "none", "symbol-placement": "line", "symbol-spacing": 400, "text-max-angle": 30,
          "text-field": expr(["concat", ["to-string", ["round", ["*", ["get", "depth_m"], -1]]], " m"]),
          "text-font": ["Open Sans Regular"], "text-size": 10,
        },
        paint: { "text-color": "#8fa6b6", "text-halo-color": "#0e1419", "text-halo-width": 1.3, "text-opacity": 0.9 } });
      map.addLayer({ id: "infrastructure", type: "line", source: "infrastructure",
        layout: { visibility: "none", "line-cap": "round", "line-join": "round" },
        paint: { "line-color": INFRA_COLOR, "line-width": 1.4, "line-opacity": 0.85 } });
      // Couche invisible plus large pour faciliter le clic sur les lignes fines
      map.addLayer({ id: "infrastructure-hit", type: "line", source: "infrastructure", layout: { visibility: "none" },
        paint: { "line-color": "#000", "line-opacity": 0, "line-width": 12 } });
      map.addLayer({ id: "zones", type: "fill", source: "zones", layout: { visibility: "none" },
        paint: { "fill-color": ALERT_COLOR.RENDEZVOUS, "fill-opacity": 0.12, "fill-outline-color": ALERT_COLOR.RENDEZVOUS } });
      map.addLayer({ id: "reception", type: "fill", source: "reception", layout: { visibility: "none" },
        paint: { "fill-color": SIGNAL, "fill-opacity": 0.07, "fill-outline-color": SIGNAL } });
      map.addLayer({ id: "trails", type: "line", source: "trails",
        paint: { "line-color": "#7c8b97", "line-width": 1, "line-opacity": 0.45 } });
      map.addImage("chevron", makeIcon("chevron"), { sdf: true });
      map.addImage("dot", makeIcon("dot"), { sdf: true });
      map.addLayer({ id: "traffic", type: "symbol", source: "traffic",
        layout: {
          "icon-image": ["case", ["<", ["coalesce", ["get", "sog_kn"], 0], 0.5], "dot", "chevron"],
          "icon-size": ["interpolate", ["linear"], ["zoom"], 6, 0.22, 10, 0.34, 13, 0.5],
          "icon-rotate": ["coalesce", ["get", "cog_deg"], 0],
          "icon-rotation-alignment": "map",
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
        paint: {
          "icon-color": NEUTRAL,
          "icon-opacity": TRAFFIC_OPACITY,
        } });
      map.addLayer({ id: "aoi", type: "line", source: "aoi",
        paint: { "line-color": SIGNAL, "line-width": 1.2, "line-dasharray": [4, 3] } });
      map.addLayer({ id: "det", type: "circle", source: "det",
        paint: {
          "circle-radius": expr(["case", ["!=", ["get", "mask_reason"], null], 2, 8]),
          "circle-color": expr(["case", ["!=", ["get", "mask_reason"], null], "#4c5a66", "rgba(0,0,0,0)"]),
          "circle-stroke-width": expr(["case", ["!=", ["get", "mask_reason"], null], 0, 1.5]),
          "circle-stroke-color": expr(["case",
            ["!=", ["get", "mask_reason"], null], "#4c5a66",
            ["!=", ["get", "matched_mmsi"], null], "#e6ecf0",
            ALERT_COLOR.DARK_SHIP]),
        } });
      // Trajectoires surlignées (traits pleins) et trajet présumé d'une coupure AIS (pointillés)
      map.addLayer({ id: "highlight", type: "line", source: "highlight", filter: ["!=", ["get", "dashed"], true],
        paint: { "line-color": ["coalesce", ["get", "color"], ALERT_COLOR.RENDEZVOUS], "line-width": 2.5 } });
      map.addLayer({ id: "highlight-dash", type: "line", source: "highlight", filter: ["==", ["get", "dashed"], true],
        paint: { "line-color": ["coalesce", ["get", "color"], ALERT_COLOR.AIS_GAP], "line-width": 2, "line-dasharray": [2, 2] } });
      for (const id of ["alerts", "live"]) {
        map.addLayer({ id, type: "circle", source: id,
          paint: {
            "circle-radius": 14, "circle-color": "rgba(255,255,255,0.04)", "circle-stroke-width": 2,
            "circle-stroke-color": ALERT_STROKE,
            "circle-stroke-opacity": ALERT_OPACITY,
          } });
      }

      // Zone en cours de tracé : rouge si elle dépasse la taille maximale
      map.addLayer({ id: "draft-fill", type: "fill", source: "draft",
        paint: { "fill-color": ["case", ["get", "tooBig"], ALERT_COLOR.AIS_GAP, SIGNAL], "fill-opacity": 0.1 } });
      map.addLayer({ id: "draft-line", type: "line", source: "draft",
        paint: { "line-color": ["case", ["get", "tooBig"], ALERT_COLOR.AIS_GAP, SIGNAL], "line-width": 1.5 } });

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
        if (!drawing.current || !start) return;
        const lngLat = e.lngLat;
        if (draftFrame) return;
        draftFrame = requestAnimationFrame(() => {
          draftFrame = 0;
          if (drawing.current && start) (map.getSource("draft") as GeoJSONSource).setData(bboxPolygon(box(start, lngLat)) as any);
        });
      });

      map.on("click", "traffic", (e) => { if (!drawing.current) onSelect.current({ kind: "vessel", properties: e.features![0].properties as any }); });
      map.on("click", "det", (e) => {
        if (drawing.current) return;
        const f = e.features![0];
        const [lon, lat] = (f.geometry as any).coordinates;
        onSelect.current({ kind: "detection", properties: { ...(f.properties as any), lon, lat } });
      });
      map.on("click", "infrastructure-hit", (e) => {
        if (drawing.current) return;
        const props = e.features![0].properties as any;
        const attrs = typeof props.attrs === "string" ? JSON.parse(props.attrs || "{}") : (props.attrs ?? {});
        onSelect.current({ kind: "infrastructure", properties: { ...props, attrs } });
      });
      for (const id of ["alerts", "live"]) {
        map.on("click", id, (e) => {
          if (drawing.current) return;
          const f = alertIndex.current.get(Number(e.features![0].properties!.id));
          if (f) onSelect.current({ kind: "alert", feature: f });
        });
      }
      for (const id of ["traffic", "det", "alerts", "live", "infrastructure-hit"]) {
        map.on("mouseenter", id, () => (map.getCanvas().style.cursor = drawing.current ? "crosshair" : "pointer"));
        map.on("mouseleave", id, () => (map.getCanvas().style.cursor = drawing.current ? "crosshair" : ""));
      }
      const emitBounds = () => { const b = map.getBounds(); onBounds.current([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]); };
      map.on("moveend", emitBounds);
      emitBounds();
      if (!disposed) setReady(true);
    });
    return () => {
      disposed = true;
      if (draftFrame) cancelAnimationFrame(draftFrame);
      map.remove();
    };
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
  useEffect(() => setData("infrastructure", p.infrastructure), [ready, p.infrastructure]);
  useEffect(() => setData("bathymetry", p.bathymetry), [ready, p.bathymetry]);
  useEffect(() => setData("highlight", p.highlight), [ready, p.highlight]);

  useEffect(() => {
    alertIndex.current.clear();
    for (const f of [...p.analysisAlerts.features, ...p.liveAlerts.features]) alertIndex.current.set(f.properties.id, f);
  }, [p.analysisAlerts, p.liveAlerts]);

  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const map = mapRef.current;
    const vis = (layers: string[], on: boolean) => layers.forEach((l) => map.setLayoutProperty(l, "visibility", on ? "visible" : "none"));
    vis(["aoi", "det", "alerts"], p.show.analysis);
    vis(["zones"], p.show.zones);
    vis(["reception"], p.show.reception);
    vis(["infrastructure", "infrastructure-hit"], p.show.infrastructure);
    vis(["bathymetry", "bathymetry-labels"], p.show.bathymetry);
    map.setPaintProperty("traffic", "icon-color", p.show.byType ? SHIP_COLOR : NEUTRAL);
  }, [ready, p.show]);

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
    if (ready && p.focus) mapRef.current!.flyTo({ center: p.focus.center, zoom: p.focus.zoom });
  }, [ready, p.focus]);

  // Mode tracé : la carte ne se déplace plus au glisser, le curseur devient une croix
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const map = mapRef.current;
    if (p.drawing) map.doubleClickZoom.disable(); else map.doubleClickZoom.enable();
    map.getCanvas().style.cursor = p.drawing ? "crosshair" : "";
  }, [ready, p.drawing]);

  useEffect(() => setData("draft", p.draft ? (bboxPolygon(p.draft) as FC) : null), [ready, p.draft]);

  // Mode focus sur la sélection : spotlight est mémoïsé dans App, son identité change avec la sélection
  const spotlight = p.spotlight;
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const map = mapRef.current;
    const s = spotlight;
    const ids: ExpressionSpecification = ["literal", s?.vesselIds ?? []];
    map.setPaintProperty("traffic", "icon-opacity", s ? ["case", ["in", ["get", "vessel_id"], ids], 1, 0.12] : TRAFFIC_OPACITY);
    map.setPaintProperty("trails", "line-opacity", s ? ["case", ["in", ["get", "vessel_id"], ids], 0.9, 0.06] : 0.45);
    map.setPaintProperty("det", "circle-stroke-opacity", s ? 0.35 : 1);
    for (const id of ["alerts", "live"]) {
      map.setPaintProperty(id, "circle-stroke-opacity", s?.alertId != null ? ["case", ["==", ["get", "id"], s.alertId], 1, 0.15] : ALERT_OPACITY);
    }
  }, [ready, spotlight]);

  return <div ref={container} style={{ position: "absolute", inset: 0 }} />;
}
