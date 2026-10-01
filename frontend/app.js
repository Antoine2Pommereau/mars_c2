// MARS C2, carte minimale de la phase 1
const STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";
const getJSON = (path) => fetch(`/api${path}`).then((r) => {
  if (!r.ok) throw new Error(`${path} : ${r.status}`);
  return r.json();
});
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 1) => (v === null || v === undefined ? "n.d." : Number(v).toFixed(d));

const map = new maplibregl.Map({ container: "map", style: STYLE, center: [10.55, 57.93], zoom: 9 });
map.addControl(new maplibregl.NavigationControl(), "top-right");

const alertsById = new Map();

function alertHtml(a) {
  const d = a.properties.details || {};
  const cands = (d.candidats_ais || []).map((c) =>
    `<tr><td>${esc(c.name || c.mmsi)}</td><td>${c.distance_m} m</td><td>${c.rayon_tolere_m} m</td></tr>`).join("");
  return `<strong>Navire sombre</strong>, sévérité ${esc(a.properties.severity)}<br>
    ${esc(d.motif)}<br><br>
    Longueur estimée : ${fmt(d.length_m, 0)} m<br>
    Contraste local VV : ${fmt(d.contrast_vv_db)} dB (seuil ${fmt(d.parametres?.seuil_contraste_db, 0)} dB)<br>
    Score de présence : ${fmt(d.objectness, 2)}, score navire : ${fmt(d.vessel_score, 2)}<br>
    Instant : ${esc(a.properties.event_time)}<br>
    ${cands ? `<table><tr><th>AIS examiné</th><th>Distance</th><th>Toléré</th></tr>${cands}</table>` : "Aucun navire AIS à proximité"}
    <div class="meta" style="margin-top:6px">Règles ${esc(a.properties.rule_version)}</div>`;
}

function openAlert(a) {
  const [lon, lat] = a.geometry.coordinates;
  map.flyTo({ center: [lon, lat], zoom: 12 });
  new maplibregl.Popup({ offset: 12 }).setLngLat([lon, lat]).setHTML(alertHtml(a)).addTo(map);
}

map.on("load", async () => {
  try {
    const analyses = await getJSON("/analyses");
    if (!analyses.features.length) {
      document.getElementById("analysis").textContent = "Aucune analyse en base. Lancez scripts/analyze_zone.py.";
      return;
    }
    const an = analyses.features[0];
    const id = an.properties.id;
    const [ais, det, alerts] = await Promise.all([
      getJSON(`/analyses/${id}/ais`), getJSON(`/analyses/${id}/detections`), getJSON(`/alerts?analysis_id=${id}`),
    ]);

    document.getElementById("analysis").innerHTML = `
      Passage ${esc(an.properties.product_name.slice(0, 32))}…<br>
      Acquisition : ${esc(an.properties.acquired_at)}<br>
      ${ais.features.length} navires AIS, ${det.features.length} détections, ${alerts.features.length} alertes`;

    map.addSource("aoi", { type: "geojson", data: an });
    map.addLayer({ id: "aoi", type: "line", source: "aoi", paint: { "line-color": "#3fa7b8", "line-width": 1.5, "line-dasharray": [3, 2] } });

    map.addSource("ais", { type: "geojson", data: ais });
    map.addLayer({ id: "ais", type: "circle", source: "ais", paint: { "circle-radius": 4, "circle-color": "#3fd6e8" } });

    map.addSource("det", { type: "geojson", data: det });
    map.addLayer({
      id: "det", type: "circle", source: "det",
      paint: {
        "circle-radius": 9, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 2,
        "circle-stroke-color": ["case",
          ["!=", ["get", "mask_reason"], null], "#6b7c88",
          ["!=", ["get", "matched_mmsi"], null], "#4aa3ff",
          "#ff4fd8"],
      },
    });

    map.addSource("alerts", { type: "geojson", data: alerts });
    map.addLayer({ id: "alerts", type: "circle", source: "alerts",
      paint: { "circle-radius": 15, "circle-color": "rgba(255,79,216,0.12)", "circle-stroke-width": 1.5, "circle-stroke-color": "#ff4fd8" } });

    const b = new maplibregl.LngLatBounds();
    an.geometry.coordinates[0].forEach((c) => b.extend(c));
    map.fitBounds(b, { padding: 40 });

    alerts.features.forEach((a) => alertsById.set(a.properties.id, a));
    const list = document.getElementById("alerts");
    list.innerHTML = alerts.features.length ? "" : "Aucune";
    alerts.features.forEach((a) => {
      const d = a.properties.details || {};
      const el = document.createElement("div");
      el.className = "alert";
      el.innerHTML = `<div class="sev">Navire sombre, ${esc(a.properties.severity)}</div>
        ${fmt(d.length_m, 0)} m, contraste ${fmt(d.contrast_vv_db)} dB`;
      el.onclick = () => openAlert(a);
      list.appendChild(el);
    });

    map.on("click", "alerts", (e) => openAlert(alertsById.get(e.features[0].properties.id)));
    map.on("click", "ais", (e) => {
      const p = e.features[0].properties;
      new maplibregl.Popup().setLngLat(e.lngLat).setHTML(
        `<strong>${esc(p.name || "Sans nom")}</strong><br>MMSI ${esc(p.mmsi)}<br>${esc(p.ship_type)}<br>
         Vitesse ${fmt(p.sog_kn)} nœuds, route ${fmt(p.cog_deg, 0)}°<br>Message : ${esc(p.ts)}`).addTo(map);
    });
    map.on("click", "det", (e) => {
      const p = e.features[0].properties;
      const statut = p.mask_reason ? `Écartée (${esc(p.mask_reason)})` : (p.matched_mmsi ? `Appariée à ${esc(p.matched_name || p.matched_mmsi)}` : "Sans AIS");
      new maplibregl.Popup().setLngLat(e.lngLat).setHTML(
        `<strong>Détection radar</strong><br>${statut}<br>Longueur ${fmt(p.length_m, 0)} m<br>
         Contraste VV ${fmt(p.contrast_vv_db)} dB<br>Présence ${fmt(p.objectness, 2)}, navire ${fmt(p.vessel_score, 2)}`).addTo(map);
    });
    for (const layer of ["alerts", "ais", "det"]) {
      map.on("mouseenter", layer, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", layer, () => (map.getCanvas().style.cursor = ""));
    }
  } catch (err) {
    document.getElementById("analysis").textContent = `Erreur : ${err.message}`;
  }
});
