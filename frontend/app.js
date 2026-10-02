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
let liveIds = "";
const liveById = new Map();
const STEP_LABEL = { extraction: "Extrait radar", inference: "Détection", fusion: "Fusion AIS" };
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

function renderProgress(list) {
  const box = document.getElementById("progress");
  box.innerHTML = list.map((a) => {
    const steps = {};
    (a.progress || []).forEach((p) => { steps[p.step] = p; });
    const rows = Object.keys(STEP_LABEL).map((k) => {
      const p = steps[k];
      const state = !p ? "en attente" : p.state === "done" ? `${fmt(p.seconds)} s` : "en cours…";
      return `<div class="step ${p && p.state !== "done" ? "run" : ""}"><span>${STEP_LABEL[k]}</span><span>${state}</span></div>`;
    }).join("");
    const head = a.status === "failed" ? `Analyse n° ${a.id} en échec` :
      a.status === "done" ? `Analyse n° ${a.id} terminée` : `Analyse n° ${a.id} en cours`;
    const err = a.error ? `<div style="margin-top:6px;color:#e5534b">${esc(a.error)}</div>` : "";
    return `<div class="job ${a.status === "failed" ? "failed" : ""}"><strong>${head}</strong>${rows}${err}</div>`;
  }).join("");
  // Une analyse plus récente que celle affichée vient de se terminer : on la charge
  const done = list.filter((a) => a.status === "done").map((a) => a.id);
  if (done.length && (!analysis || Math.max(...done) > analysis.properties.id)) loadAnalysis();
}

function hm(iso) {
  return new Date(iso).toISOString().slice(11, 16);
}

function vesselLabel(v) {
  if (!v) return "inconnu";
  return `${esc(v.name || "Sans nom")} (MMSI ${esc(v.mmsi)}, ${esc(v.ship_type || "type non renseigné")}${v.length_m ? `, ${fmt(v.length_m, 0)} m` : ""})`;
}

function rdvHtml(a) {
  const d = a.properties.details || {};
  const [v1, v2] = d.navires || [];
  return `<strong>Rendez vous suspect</strong>, sévérité ${esc(SEVERITY[a.properties.severity] || a.properties.severity)}<br>
    ${esc(d.motif)}<br><br>
    ${vesselLabel(v1)}<br>${vesselLabel(v2)}<br><br>
    De ${hm(d.debut)} à ${hm(d.fin)} UTC, soit ${d.duree_min} min<br>
    Distance entre les navires : ${d.distance_min_m} m au plus près, ${d.distance_moyenne_m} m en moyenne<br>
    Distance à la côte : ${d.distance_cote_km === null ? "n.d." : `${fmt(d.distance_cote_km)} km`}<br>
    ${(d.contexte || []).length ? `<br><strong>Contexte</strong><br>${d.contexte.map((t) => `· ${esc(t)}`).join("<br>")}<br>` : ""}
    <div class="meta" style="margin-top:6px">Alerte levée à ${hm(a.properties.event_time)} UTC, après ${d.parametres?.min_duration_min} min de rencontre. Règles ${esc(a.properties.rule_version)}</div>`;
}

async function openLiveAlert(a) {
  const [lon, lat] = a.geometry.coordinates;
  map.flyTo({ center: [lon, lat], zoom: 12 });
  new maplibregl.Popup({ offset: 14 }).setLngLat([lon, lat]).setHTML(rdvHtml(a)).addTo(map);
  const d = a.properties.details || {};
  const start = new Date(new Date(d.debut).getTime() - 30 * 60 * 1000).toISOString();
  const end = new Date(new Date(d.fin).getTime() + 30 * 60 * 1000).toISOString();
  const tracks = await Promise.all((d.navires || []).filter(Boolean).map((v) =>
    getJSON(`/vessels/${v.vessel_id}/track?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`).catch(() => null)));
  map.getSource("rdv-tracks").setData({ type: "FeatureCollection", features: tracks.filter(Boolean) });
}

function renderLiveAlerts(fc) {
  map.getSource("live-alerts")?.setData(fc);
  const ids = fc.features.map((f) => f.properties.id).join(",");
  if (ids === liveIds) return;
  liveIds = ids;
  liveById.clear();
  fc.features.forEach((a) => liveById.set(a.properties.id, a));
  const list = document.getElementById("live-alerts");
  list.innerHTML = fc.features.length ? "" : "Aucune à cet instant";
  fc.features.forEach((a) => {
    const d = a.properties.details || {};
    const [v1, v2] = d.navires || [];
    const el = document.createElement("div");
    el.className = `alert rdv ${a.properties.severity === "faible" ? "low" : ""}`;
    el.innerHTML = `<div class="sev">Rendez vous, ${esc(SEVERITY[a.properties.severity] || a.properties.severity)}</div>
      ${esc(v1?.name || v1?.mmsi)} et ${esc(v2?.name || v2?.mmsi)}<br>${hm(d.debut)} à ${hm(d.fin)} UTC, ${d.duree_min} min`;
    el.onclick = () => openLiveAlert(a);
    list.appendChild(el);
  });
}

function connectStream() {
  const es = new EventSource("/api/stream");
  es.addEventListener("traffic", (e) => {
    const d = JSON.parse(e.data);
    renderClock(d.clock);
    renderProgress(d.analyses || []);
    if (d.live_alerts) renderLiveAlerts(d.live_alerts);
    map.getSource("traffic")?.setData(d.traffic);
    const n = d.traffic.features.length;
    const silent = d.traffic.features.filter((f) => f.properties.age_s > 600).length;
    document.getElementById("traffic-info").textContent = `${n} navires affichés, dont ${silent} silencieux depuis plus de 10 minutes`;
    if (myAnalysisId) {
      const a = (d.analyses || []).find((x) => x.id === myAnalysisId);
      if (a) renderProgress(a);
    }
  });
  es.onerror = () => { document.getElementById("traffic-info").textContent = "Connexion perdue, nouvelle tentative…"; };
}

// Analyse à la demande : tracé de zone, choix du passage, suivi

let drawStart = null;
let zone = null;
let myAnalysisId = null;
const MAX_KM = 50;
const STEPS = [["en attente", "En attente"], ["extraction", "Extrait radar"], ["inference", "Détection"],
               ["fusion", "Fusion avec l'AIS"], ["terminee", "Terminée"]];

function sizeKm(b) {
  const lat = ((b[1] + b[3]) / 2) * Math.PI / 180;
  return [(b[2] - b[0]) * 111.32 * Math.cos(lat), (b[3] - b[1]) * 110.57];
}

function zoneFeature(b) {
  return { type: "Feature", properties: {}, geometry: { type: "Polygon",
    coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] } };
}

function bboxFrom(a, c) {
  return [Math.min(a.lng, c.lng), Math.min(a.lat, c.lat), Math.max(a.lng, c.lng), Math.max(a.lat, c.lat)];
}

function showZone(b, final) {
  map.getSource("draw").setData(zoneFeature(b));
  const [w, h] = sizeKm(b);
  const tooBig = Math.max(w, h) > MAX_KM;
  document.getElementById("zone").innerHTML = `Zone : ${fmt(w, 0)} × ${fmt(h, 0)} km` +
    (tooBig ? ` <span class="warn">, trop grande (${MAX_KM} km de côté au plus)</span>` : "");
  document.getElementById("search-passes").hidden = !final || tooBig;
  return !tooBig;
}

function enableDrawing() {
  map.boxZoom.disable();
  map.on("mousedown", (e) => {
    if (!e.originalEvent.shiftKey) return;
    e.preventDefault();
    map.dragPan.disable();
    drawStart = e.lngLat;
  });
  map.on("mousemove", (e) => { if (drawStart) showZone(bboxFrom(drawStart, e.lngLat), false); });
  map.on("mouseup", (e) => {
    if (!drawStart) return;
    const b = bboxFrom(drawStart, e.lngLat);
    drawStart = null;
    map.dragPan.enable();
    zone = showZone(b, true) ? b : null;
    document.getElementById("passes").innerHTML = "";
    document.getElementById("progress").innerHTML = "";
  });
}

document.getElementById("search-passes").onclick = async () => {
  if (!zone) return;
  const box = document.getElementById("passes");
  box.innerHTML = '<div class="meta" style="margin-top:8px">Recherche dans le catalogue…</div>';
  try {
    const passes = await getJSON(`/passes?bbox=${zone.map((v) => v.toFixed(5)).join(",")}`);
    if (!passes.length) {
      box.innerHTML = '<div class="meta" style="margin-top:8px">Aucun passage sur les journées AIS chargées.</div>';
      return;
    }
    box.innerHTML = "";
    passes.forEach((p) => {
      const el = document.createElement("button");
      el.className = "pass";
      el.innerHTML = `${esc(utc(p.acquired_at))}<br><span class="meta">${esc(p.platform || "")}, orbite
        ${esc(p.orbit_direction === "ascending" ? "ascendante" : p.orbit_direction === "descending" ? "descendante" : "n.d.")}</span>
        <span class="tag ${p.ais_available ? "" : "no"}">${p.ais_available ? "AIS disponible" : "sans AIS"}</span>`;
      el.onclick = () => launch(p);
      box.appendChild(el);
    });
  } catch (err) {
    box.innerHTML = `<div class="warn" style="margin-top:8px">${esc(err.message)}</div>`;
  }
};

async function launch(p) {
  document.getElementById("passes").innerHTML = "";
  const r = await postJSON("/analyses", { bbox: zone, product_name: p.product_name, acquired_at: p.acquired_at,
    platform: p.platform, orbit_direction: p.orbit_direction, mode: "fast" });
  if (!r.id) {
    document.getElementById("progress").innerHTML = `<span class="warn">${esc(r.detail || "Échec du lancement")}</span>`;
    return;
  }
  myAnalysisId = r.id;
  renderProgress({ id: r.id, status: "pending", progress: { etape: "en attente" } });
}

function renderProgress(a) {
  const el = document.getElementById("progress");
  if (a.status === "failed") {
    el.innerHTML = `<span class="warn">Analyse n° ${a.id} en échec : ${esc(a.error)}</span>`;
    myAnalysisId = null;
    return;
  }
  const cur = STEPS.findIndex(([k]) => k === (a.progress || {}).etape);
  el.innerHTML = `Analyse n° ${a.id}<ul class="steps">` + STEPS.map(([k, label], i) =>
    `<li class="${i < cur || a.status === "done" ? "done" : i === cur ? "now" : ""}">${i < cur || a.status === "done" ? "✓" : i === cur ? "▸" : "·"} ${label}</li>`).join("") + "</ul>";
  if (a.status === "done") {
    const id = a.id;
    myAnalysisId = null;
    loadAnalysis(id);
  }
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

async function loadAnalysis(wantedId = null) {
  const analyses = await getJSON("/analyses");
  const done = analyses.features.filter((f) => f.properties.status === "done");
  if (!done.length) {
    document.getElementById("analysis").textContent = "Aucune analyse terminée.";
    return;
  }
  analysis = done.find((f) => f.properties.id === wantedId) || done[0];
  const id = analysis.properties.id;
  const [det, alerts] = await Promise.all([getJSON(`/analyses/${id}/detections`), getJSON(`/alerts?analysis_id=${id}`)]);
  const nAlerts = alerts.features.length;
  document.getElementById("analysis").innerHTML = `
    Analyse n° ${id}, passage ${esc(analysis.properties.product_name.slice(0, 26))}…<br>
    Acquisition : ${esc(utc(analysis.properties.acquired_at))}<br>
    ${det.features.length} détection${det.features.length > 1 ? "s" : ""}, ${nAlerts} alerte${nAlerts > 1 ? "s" : ""}`;

  map.getSource("aoi").setData(analysis);
  map.getSource("det").setData(det);
  map.getSource("alerts").setData(alerts);

  const t = analysis.properties.timings || {};
  if (t.total_s !== undefined) {
    document.getElementById("analysis").innerHTML += `<br>Durées : extrait ${fmt(t.extraction_s)} s, détection ${fmt(t.inference_s)} s
      (${esc(t.device || "")}), fusion ${fmt(t.fusion_s)} s, total ${fmt(t.total_s)} s`;
  }
  alertsById.clear();
  alertsById.clear();
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

  map.addSource("draw", { type: "geojson", data: EMPTY });
  map.addLayer({ id: "draw-fill", type: "fill", source: "draw", paint: { "fill-color": "#3fa7b8", "fill-opacity": 0.08 } });
  map.addLayer({ id: "draw-line", type: "line", source: "draw", paint: { "line-color": "#3fa7b8", "line-width": 2 } });
  enableDrawing();

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

  map.addSource("zones", { type: "geojson", data: EMPTY });
  map.addLayer({ id: "zones", type: "fill", source: "zones", layout: { visibility: "none" },
    paint: { "fill-color": "#ffb547", "fill-opacity": 0.18, "fill-outline-color": "#ffb547" } }, "trails");

  map.addSource("rdv-tracks", { type: "geojson", data: EMPTY });
  map.addLayer({ id: "rdv-tracks", type: "line", source: "rdv-tracks",
    paint: { "line-color": "#ffb547", "line-width": 2.5, "line-opacity": 0.9 } });

  map.addSource("live-alerts", { type: "geojson", data: EMPTY });
  map.addLayer({ id: "live-alerts", type: "circle", source: "live-alerts",
    paint: { "circle-radius": 14, "circle-color": "rgba(255,181,71,0.12)", "circle-stroke-width": 2, "circle-stroke-color": "#ffb547",
             "circle-stroke-opacity": ["case", ["==", ["get", "severity"], "faible"], 0.45, 1] } });

  document.getElementById("show-zones").onchange = async (e) => {
    if (e.target.checked && !map.getSource("zones")._loaded) {
      map.getSource("zones").setData(await getJSON("/masks/stationary"));
      map.getSource("zones")._loaded = true;
    }
    map.setLayoutProperty("zones", "visibility", e.target.checked ? "visible" : "none");
  };
  map.on("click", "live-alerts", (e) => openLiveAlert(liveById.get(e.features[0].properties.id)));

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
  for (const layer of ["alerts", "traffic", "det", "live-alerts"]) {
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
    getJSON("/inference/health").then((h) => {
      document.getElementById("infer-status").textContent =
        `Service d'inférence prêt sur ${h.device}${h.amp ? ", précision mixte" : ""} (préchauffage ${fmt(h.warmup_s)} s)`;
    }).catch(() => {
      document.getElementById("infer-status").innerHTML = '<span class="warn">Service d\'inférence injoignable : lancez-le sur le Mac (voir README).</span>';
    });
    await loadAnalysis();
  } catch (err) {
    document.getElementById("analysis").textContent = `Erreur : ${err.message}`;
  }
  connectStream();
  refreshTrails();
  setInterval(refreshTrails, 3000);
});
