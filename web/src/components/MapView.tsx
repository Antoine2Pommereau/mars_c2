import maplibregl, { type GeoJSONSource, type Map as MLMap } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";
import { WATCH_COLOR } from "../lib/format";
import { bboxPolygon } from "../lib/geo";
import { EMPTY, type FC, type Feature, type Selection } from "../lib/types";

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

const ALERT_STROKE: any = ["match", ["get", "type"],
  "DARK_SHIP", "#e85bc7", "RENDEZVOUS", "#f0a84b", "AIS_GAP", "#ef6461", "AIS_UNCONFIRMED", "#e8d45a", "#ffffff"];
const NEUTRAL = "#c9d3da";
// Navire d'une liste de surveillance : teinte dédiée, quel que soit le mode de couleur
const withWatch = (base: any): any => ["case", ["to-boolean", ["get", "watch"]], WATCH_COLOR, base];
// Infrastructures : câble télécom en cyan, câble électrique en ambre, pipeline en orange, parc éolien en vert
const INFRA_LINE: any = ["match", ["get", "type"], "Câble électrique", "#f0a84b", "Pipeline", "#e07a5f", "#4fb6c8"];
const WINDFARM = "#5fd38d";

/** Plus petit cadre englobant un ensemble de géométries GeoJSON (coordonnées imbriquées). */
function fitExtent(fc: FC): [[number, number], [number, number]] | null {
  const acc = [1e9, 1e9, -1e9, -1e9];
  const walk = (c: any) => {
    if (typeof c[0] === "number") {
      acc[0] = Math.min(acc[0], c[0]); acc[1] = Math.min(acc[1], c[1]);
      acc[2] = Math.max(acc[2], c[0]); acc[3] = Math.max(acc[3], c[1]);
    } else c.forEach(walk);
  };
  for (const f of fc.features) if (f.geometry) walk((f.geometry as any).coordinates);
  return acc[0] <= acc[2] ? [[acc[0], acc[1]], [acc[2], acc[3]]] : null;
}

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
  show: { analysis: boolean; zones: boolean; reception: boolean; byType: boolean; infrastructure: boolean };
  focus: { center: [number, number]; zoom: number } | null;
  onSelect: (s: Selection) => void;
  drawing: boolean;
  draft: number[] | null;
  onDraw: (bbox: number[]) => void;
  spotlight: { alertId: number | null; vesselIds: number[] } | null;
  onBounds: (b: number[]) => void;
}

// Opacités de base, et mode focus : tout ce qui ne concerne pas la sélection s'efface
const TRAFFIC_OPACITY: any = ["case", [">", ["get", "age_s"], 600], 0.3, 0.9];
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
  const onBounds = useRef(p.onBounds);
  onBounds.current = p.onBounds;

  // Création de la carte et des couches, une seule fois
  useEffect(() => {
    const map = new maplibregl.Map({ container: container.current!, style: STYLE, center: [1.5, 46.4], zoom: 5.2, boxZoom: false });
    map.addControl(new maplibregl.NavigationControl(), "top-right");
    mapRef.current = map;

    map.on("load", () => {
      const src = (id: string) => map.addSource(id, { type: "geojson", data: EMPTY as any });
      ["zones", "reception", "infra", "trails", "traffic", "aoi", "det", "alerts", "live", "highlight", "draft"].forEach(src);

      // Infrastructures sous marines (sous le trafic) : câbles et pipelines en lignes, parcs éoliens en surface
      map.addLayer({ id: "infra-wind-fill", type: "fill", source: "infra", layout: { visibility: "none" },
        filter: ["==", ["get", "kind"], "windfarm"],
        paint: { "fill-color": WINDFARM, "fill-opacity": 0.12 } });
      map.addLayer({ id: "infra-wind-line", type: "line", source: "infra", layout: { visibility: "none" },
        filter: ["==", ["get", "kind"], "windfarm"],
        paint: { "line-color": WINDFARM, "line-width": 1, "line-opacity": 0.8 } });
      map.addLayer({ id: "infra-lines", type: "line", source: "infra", layout: { visibility: "none", "line-cap": "round" },
        filter: ["!=", ["get", "kind"], "windfarm"],
        paint: { "line-color": INFRA_LINE, "line-width": 1.5, "line-opacity": 0.85 } });

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
          "icon-size": ["interpolate", ["linear"], ["zoom"], 6, 0.22, 10, 0.34, 13, 0.5],
          "icon-rotate": ["coalesce", ["get", "cog_deg"], 0],
          "icon-rotation-alignment": "map",
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
        paint: {
          "icon-color": withWatch(NEUTRAL),
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
            "circle-stroke-color": ALERT_STROKE,
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
      // Infrastructure : infobulle légère au clic (le détail va en popup, pas dans la fiche d'alerte)
      const infraPopup = new maplibregl.Popup({ closeButton: false, offset: 8 });
      for (const id of ["infra-lines", "infra-wind-fill"]) {
        map.on("click", id, (e) => {
          if (drawing.current) return;
          const pr = e.features![0].properties as any;
          const lines = [pr.name, pr.type, pr.operator].filter(Boolean).map((t) => `<div>${t}</div>`).join("");
          infraPopup.setLngLat(e.lngLat).setHTML(`<div style="font:12px/1.4 sans-serif;color:#0e1419">${lines || "Infrastructure"}</div>`).addTo(map);
        });
        map.on("mouseenter", id, () => (map.getCanvas().style.cursor = drawing.current ? "crosshair" : "pointer"));
        map.on("mouseleave", id, () => (map.getCanvas().style.cursor = drawing.current ? "crosshair" : ""));
      }
      for (const id of ["traffic", "det", "alerts", "live"]) {
        map.on("mouseenter", id, () => (map.getCanvas().style.cursor = drawing.current ? "crosshair" : "pointer"));
        map.on("mouseleave", id, () => (map.getCanvas().style.cursor = drawing.current ? "crosshair" : ""));
      }
      const emitBounds = () => { const b = map.getBounds(); onBounds.current([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]); };
      map.on("moveend", emitBounds);
      emitBounds();
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

  // Première fois qu'on active les infrastructures et qu'elles sont chargées : cadrer dessus (zones France)
  const infraFitted = useRef(false);
  useEffect(() => {
    if (!ready || !mapRef.current || !p.show.infrastructure || infraFitted.current) return;
    const ext = p.infrastructure && p.infrastructure.features.length ? fitExtent(p.infrastructure) : null;
    if (ext) { mapRef.current.fitBounds(ext, { padding: 60, duration: 800 }); infraFitted.current = true; }
  }, [ready, p.show.infrastructure, p.infrastructure]);

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
    vis(["infra-lines", "infra-wind-fill", "infra-wind-line"], p.show.infrastructure);
    map.setPaintProperty("traffic", "icon-color", withWatch(p.show.byType ? SHIP_COLOR : NEUTRAL));
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

  // Mode focus sur la sélection
  const spotKey = p.spotlight ? `${p.spotlight.alertId}:${p.spotlight.vesselIds.join(",")}` : "";
  useEffect(() => {
    if (!ready || !mapRef.current) return;
    const map = mapRef.current;
    const s = p.spotlight;
    const ids = ["literal", s?.vesselIds ?? []];
    map.setPaintProperty("traffic", "icon-opacity", s ? ["case", ["in", ["get", "vessel_id"], ids], 1, 0.12] : TRAFFIC_OPACITY);
    map.setPaintProperty("trails", "line-opacity", s ? ["case", ["in", ["get", "vessel_id"], ids], 0.9, 0.06] : 0.45);
    map.setPaintProperty("det", "circle-stroke-opacity", s ? 0.35 : 1);
    for (const id of ["alerts", "live"]) {
      map.setPaintProperty(id, "circle-stroke-opacity", s?.alertId != null ? ["case", ["==", ["get", "id"], s.alertId], 1, 0.15] : ALERT_OPACITY);
    }
  }, [ready, spotKey]);

  return <div ref={container} style={{ position: "absolute", inset: 0 }} />;
}
