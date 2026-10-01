// MARS C2, phase 2 : trafic AIS rejoué en temps simulé, et analyse radar
const STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
const getJSON = (path) => fetch(`/api${path}`).then((r) => {
  if (!r.ok) throw new Error(`${path} : ${r.status}`);
  return r.json();
});
const postJSON = (path, body) => fetch(`/api${path}`, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
}).then((r) => r.json());
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 1) => (v === null || v === undefined ? "n.d." : Number(v).toFixed(d));
const utc = (iso) => new Date(iso).toISOString().replace("T", " ").slice(0, 19) + " UTC";
const SEVERITY = { faible: "faible", moyenne: "moyenne", elevee: "élevée", critique: "critique" };

const SHIP_COLOR = ["match", ["coalesce", ["get", "ship_type"], ""],
  "Cargo", "#6ea8fe",
  "Tanker", "#f0a35e",
  "Fishing", "#5fd38d",
  ["Passenger", "HSC"], "#c792ea",
  ["Pleasure", "Sailing"], "#f5e663",
  ["Tug", "Towing", "Towing long/wide", "Pilot", "SAR", "Law enforcement", "Military", "Port tender",
   "Dredging", "Diving", "Anti-pollution", "Medical"], "#e07a5f",
  "#9fb3c2"];

const map = new maplibregl.Map({ container: "map", style: STYLE, center: [10.6, 57.6], zoom: 7.5 });
map.addControl(new maplibregl.NavigationControl(), "top-right");

let clock = null;
let analysis = null;
const alertsById = new Map();

// Rejeu

function renderClock(c) {
  clock = c;
  document.getElementById("clock").textContent = utc(c.now);
  document.getElementById("play").textContent = c.paused ? "Lecture" : "Pause";
  document.getElementById("speed").value = String(c.speed);
  document.getElementById("live").textContent = c.paused ? "en pause" : `× ${c.speed}`;
}

async function command(body) {
  renderClock(await postJSON("/clock", body));
  refreshTrails();
}

document.getElementById("play").onclick = () => command({ action: clock && !clock.paused ? "pause" : "play" });
document.getElementById("speed").onchange = (e) => command({ action: "speed", speed: Number(e.target.value) });
document.getElementById("seek").onclick = () => {
  const v = document.getElementById("seek-time").value;
  if (v) command({ action: "seek", time: `${v}:00Z` });
};
document.getElementById("to-pass").onclick = () => {
  if (!analysis) return;
  const t = new Date(new Date(analysis.properties.acquired_at).getTime() - 10 * 60 * 1000);
  command({ action: "seek", time: t.toISOString() });
};

async function refreshTrails() {
  const src = map.getSource("trails");
  if (src) src.setData(await getJSON("/traffic/trails"));
}

function connectStream() {
  const es = new EventSource("/api/stream");
  es.addEventListener("traffic", (e) => {
    const d = JSON.parse(e.data);
    renderClock(d.clock);
    map.getSource("traffic")?.setData(d.traffic);
    const n = d.traffic.features.length;
    const silent = d.traffic.features.filter((f) => f.properties.age_s > 600).length;
    document.getElementById("traffic-info").textContent = `${n} navires affichés, dont ${silent} silencieux depuis plus de 10 minutes`;
  });
  es.onerror = () => { document.getElementById("traffic-info").textContent = "Connexion perdue, nouvelle tentative…"; };
}

// Analyse radar

function alertHtml(a) {
  const d = a.properties.details || {};
  const cands = (d.candidats_ais || []).map((c) =>
    `<tr><td>${esc(c.name || c.mmsi)}</td><td>${c.distance_m} m</td><td>${c.rayon_tolere_m} m</td></tr>`).join("");
  return `<strong>Navire sombre</strong>, sévérité ${esc(SEVERITY[a.properties.severity] || a.properties.severity)}<br>
    ${esc(d.motif)}<br><br>
    Longueur estimée : ${fmt(d.length_m, 0)} m<br>
    Contraste local VV : ${fmt(d.contrast_vv_db)} dB (seuil ${fmt(d.parametres?.seuil_contraste_db, 0)} dB)<br>
    Score de présence : ${fmt(d.objectness, 2)}, score navire : ${fmt(d.vessel_score, 2)}<br>
    Instant : ${esc(utc(a.properties.event_time))}<br>
    ${cands ? `<table><tr><th>AIS examiné</th><th>Distance</th><th>Toléré</th></tr>${cands}</table>` : "Aucun navire AIS à proximité"}
    <div class="meta" style="margin-top:6px">Règles ${esc(a.properties.rule_version)}</div>`;
}

function openAlert(a) {
  const [lon, lat] = a.geometry.coordinates;
  map.flyTo({ center: [lon, lat], zoom: 12 });
  new maplibregl.Popup({ offset: 12 }).setLngLat([lon, lat]).setHTML(alertHtml(a)).addTo(map);
}

async function loadAnalysis() {
  const analyses = await getJSON("/analyses");
  if (!analyses.features.length) {
    document.getElementById("analysis").textContent = "Aucune analyse en base.";
    return;
  }
  analysis = analyses.features[0];
  const id = analysis.properties.id;
  const [det, alerts] = await Promise.all([getJSON(`/analyses/${id}/detections`), getJSON(`/alerts?analysis_id=${id}`)]);
  const nAlerts = alerts.features.length;
  document.getElementById("analysis").innerHTML = `
    Passage ${esc(analysis.properties.product_name.slice(0, 32))}…<br>
    Acquisition : ${esc(utc(analysis.properties.acquired_at))}<br>
    ${det.features.length} détection${det.features.length > 1 ? "s" : ""}, ${nAlerts} alerte${nAlerts > 1 ? "s" : ""}`;

  map.getSource("aoi").setData(analysis);
  map.getSource("det").setData(det);
  map.getSource("alerts").setData(alerts);

  alerts.features.forEach((a) => alertsById.set(a.properties.id, a));
  const list = document.getElementById("alerts");
  list.innerHTML = nAlerts ? "" : "Aucune";
  alerts.features.forEach((a) => {
    const d = a.properties.details || {};
    const el = document.createElement("div");
    el.className = "alert";
    el.innerHTML = `<div class="sev">Navire sombre, ${esc(SEVERITY[a.properties.severity] || a.properties.severity)}</div>
      ${fmt(d.length_m, 0)} m, contraste ${fmt(d.contrast_vv_db)} dB`;
    el.onclick = () => openAlert(a);
    list.appendChild(el);
  });
}

// Carte

const EMPTY = { type: "FeatureCollection", features: [] };
const ANALYSIS_LAYERS = ["aoi", "det", "alerts"];

map.on("load", async () => {
  map.addSource("trails", { type: "geojson", data: EMPTY });
  map.addLayer({ id: "trails", type: "line", source: "trails",
    paint: { "line-color": "#7fa6bd", "line-width": 1.2, "line-opacity": 0.55 } });

  map.addSource("traffic", { type: "geojson", data: EMPTY });
  map.addLayer({ id: "traffic", type: "circle", source: "traffic",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 6, 2.5, 10, 4.5, 13, 7],
      "circle-color": SHIP_COLOR,
      "circle-opacity": ["case", [">", ["get", "age_s"], 600], 0.35, 1],
      "circle-stroke-width": 0.5, "circle-stroke-color": "#0b1620",
    } });

  map.addSource("aoi", { type: "geojson", data: EMPTY });
  map.addLayer({ id: "aoi", type: "line", source: "aoi",
    paint: { "line-color": "#3fa7b8", "line-width": 1.5, "line-dasharray": [3, 2] } });

  map.addSource("det", { type: "geojson", data: EMPTY });
  map.addLayer({ id: "det", type: "circle", source: "det",
    paint: {
      "circle-radius": 9, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 2,
      "circle-stroke-color": ["case",
        ["!=", ["get", "mask_reason"], null], "#6b7c88",
        ["!=", ["get", "matched_mmsi"], null], "#e8f1f7",
        "#ff4fd8"],
    } });

  map.addSource("alerts", { type: "geojson", data: EMPTY });
  map.addLayer({ id: "alerts", type: "circle", source: "alerts",
    paint: { "circle-radius": 15, "circle-color": "rgba(255,79,216,0.12)", "circle-stroke-width": 1.5, "circle-stroke-color": "#ff4fd8" } });

  document.getElementById("show-analysis").onchange = (e) => {
    ANALYSIS_LAYERS.forEach((l) => map.setLayoutProperty(l, "visibility", e.target.checked ? "visible" : "none"));
  };

  map.on("click", "alerts", (e) => openAlert(alertsById.get(e.features[0].properties.id)));
  map.on("click", "traffic", (e) => {
    const p = e.features[0].properties;
    new maplibregl.Popup().setLngLat(e.lngLat).setHTML(
      `<strong>${esc(p.name || "Sans nom")}</strong><br>MMSI ${esc(p.mmsi)}, ${esc(p.ship_type || "type non renseigné")}<br>
       Vitesse ${fmt(p.sog_kn)} nœuds, route ${fmt(p.cog_deg, 0)}°<br>
       Dernier message il y a ${fmt(p.age_s / 60, 0)} min`).addTo(map);
  });
  map.on("click", "det", (e) => {
    const p = e.features[0].properties;
    const statut = p.mask_reason && p.mask_reason !== "null" ? `Écartée (${esc(p.mask_reason)})`
      : (p.matched_mmsi && p.matched_mmsi !== "null" ? `Appariée à ${esc(p.matched_name || p.matched_mmsi)}` : "Sans AIS");
    new maplibregl.Popup().setLngLat(e.lngLat).setHTML(
      `<strong>Détection radar</strong><br>${statut}<br>Longueur ${fmt(p.length_m, 0)} m<br>
       Contraste VV ${fmt(p.contrast_vv_db)} dB<br>Présence ${fmt(p.objectness, 2)}, navire ${fmt(p.vessel_score, 2)}`).addTo(map);
  });
  for (const layer of ["alerts", "traffic", "det"]) {
    map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
  }

  try {
    renderClock(await getJSON("/clock"));
    const days = await getJSON("/ais/days");
    const input = document.getElementById("seek-time");
    if (days.length) {
      input.min = `${days[0].day}T00:00`;
      input.max = `${days[days.length - 1].day}T23:59`;
    }
    input.value = clock.now.slice(0, 16);
    await loadAnalysis();
  } catch (err) {
    document.getElementById("analysis").textContent = `Erreur : ${err.message}`;
  }
  connectStream();
  refreshTrails();
  setInterval(refreshTrails, 3000);
});
