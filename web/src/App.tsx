import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import AlertsPanel from "./components/AlertsPanel";
import AnalysesPanel from "./components/AnalysesPanel";
import BarreEtat from "./components/BarreEtat";
import DetailPanel from "./components/DetailPanel";
import Frise from "./components/Frise";
import Garde from "./components/Garde";
import LayersPanel from "./components/LayersPanel";
import MapView from "./components/MapView";
import NewAnalysis from "./components/NewAnalysis";
import Rail, { type PanelId } from "./components/Rail";
import Recherche, { type Resultat } from "./components/Recherche";
import SuivisPanel from "./components/SuivisPanel";
import { api, type Pass } from "./lib/api";
import { FILTRES_DEFAUT, dansPlage, filtrer, naviresEnAlerte, statutDe, trier, type Filtres } from "./lib/fil";
import { auteurMemorise } from "./lib/auteur";
import { nearInfra } from "./lib/geo";
import { L } from "./lib/libelles";
import { avecPlage, iso, resolve, type Temps } from "./lib/temps";
import { decodeTraffic } from "./lib/trafic";
import { EMPTY, type FC, type Feature, type Props, type Selection } from "./lib/types";
import { ecrireAdresse, lireAdresse } from "./lib/url";
import { zoneBbox, zoneOf, zonesGeoJSON } from "./lib/zones";
import { useStream } from "./lib/useStream";
import { COULEUR_LISTE, naviresAlerte, typeAlerte } from "./registres/alertes";
import { COUCHES_DEFAUT } from "./registres/couches";

const INITIAL = lireAdresse();
const DEUX_MILLES = 3704;
// En direct, la plage glisse chaque seconde : les requêtes sur la plage sont arrondies à 30 s (clés stables)
const arrondi = (ms: number) => Math.floor(ms / 30_000) * 30_000;
const selKey = (s: Selection | null) => !s ? null : s.kind === "alert" ? `alerte:${s.feature.properties.id}`
  : s.kind === "vessel" ? `navire:${s.properties.vessel_id}` : s.kind === "infrastructure" ? `infrastructure:${s.properties.id}`
  : s.kind === "zone" ? `zone:${s.properties.zone}` : null;
type Focus = { center: [number, number]; zoom: number } | { bounds: [number, number, number, number] };

/** Emprise d'une géométrie GeoJSON */
function bboxOf(g: any): [number, number, number, number] | null {
  const acc = [1e9, 1e9, -1e9, -1e9];
  const walk = (c: any) => { if (typeof c[0] === "number") { acc[0] = Math.min(acc[0], c[0]); acc[1] = Math.min(acc[1], c[1]);
    acc[2] = Math.max(acc[2], c[0]); acc[3] = Math.max(acc[3], c[1]); } else c.forEach(walk); };
  if (g?.coordinates) walk(g.coordinates);
  return acc[0] <= acc[2] ? [acc[0], acc[1], acc[2], acc[3]] : null;
}

export default function App() {
  const qc = useQueryClient();
  const [temps, setTemps] = useState<Temps>(INITIAL.temps);
  const [now, setNow] = useState(Date.now());
  const [panel, setPanel] = useState<PanelId | null>("alertes");
  const [actives, setActives] = useState<string[]>(INITIAL.couches ?? COUCHES_DEFAUT);
  const [byType, setByType] = useState(false);
  const [concernees, setConcernees] = useState(false);
  const [filtres, setFiltres] = useState<Filtres>({ ...FILTRES_DEFAUT, zone: INITIAL.zone });
  const [selection, setSelection] = useState<Selection | null>(null);
  const [pendingSel, setPendingSel] = useState<string | null>(INITIAL.sel);
  const [highlight, setHighlight] = useState<FC>(EMPTY);
  const [focus, setFocus] = useState<Focus | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [draft, setDraft] = useState<number[] | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);
  const [searching, setSearching] = useState(false);

  // Horloge de l'écran (une seconde), et avance de l'instant en rejeu
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(id); }, []);
  useEffect(() => {
    if (temps.mode !== "rejeu" || !temps.lecture) return;
    const id = setInterval(() => setTemps((t) => {
      if (t.mode !== "rejeu" || !t.lecture || t.fin == null) return t;
      const instant = Math.min((t.instant ?? t.debut ?? t.fin) + 1000 * t.vitesse, t.fin);
      return { ...t, instant, lecture: instant < t.fin };
    }), 1000);
    return () => clearInterval(id);
  }, [temps.mode, temps.lecture]);
  const { debut, fin, instant } = resolve(temps, now);
  const direct = temps.mode === "direct";
  const qDebut = iso(direct ? arrondi(debut) : debut), qFin = iso(direct ? arrondi(fin) : fin);

  // Trafic : flux temps réel en direct ; instant choisi en plage et en rejeu
  const { data: stream, traffic: streamTraffic, connected } = useStream(direct);
  const trafficQ = useQuery({ queryKey: ["traffic", Math.round(instant / 1000)], queryFn: () => api.traffic(iso(instant)),
    enabled: !direct, placeholderData: keepPreviousData, retry: 2 });
  const rawTraffic = useMemo(() => decodeTraffic(direct ? streamTraffic : trafficQ.data, instant) ?? EMPTY,
    // eslint ne surveille pas ce fichier ; l'âge des positions n'a besoin que du trafic et de la minute courante
    [direct, streamTraffic, trafficQ.data, Math.floor(instant / 60_000)]);
  const trailsQ = useQuery({ queryKey: ["trails", Math.round(instant / 10_000)], queryFn: () => api.trails(iso(instant)),
    refetchInterval: direct ? 30_000 : false, placeholderData: keepPreviousData });

  // Alertes et frise de la plage
  const alertsQ = useQuery({ queryKey: ["alertsRange", qDebut, qFin], queryFn: () => api.alertsRange(qDebut, qFin),
    refetchInterval: direct ? 30_000 : false, placeholderData: keepPreviousData });
  const timelineQ = useQuery({ queryKey: ["timeline", qDebut, qFin], queryFn: () => api.timeline(qDebut, qFin, 240),
    // Deux nouvelles tentatives : en plage, la clé ne change plus et une erreur passagère resterait affichée
    refetchInterval: direct ? 60_000 : false, placeholderData: keepPreviousData, retry: 2 });
  const rangeAlerts = useMemo(() => dansPlage(alertsQ.data?.features ?? [], debut, fin),
    [alertsQ.data, Math.floor(debut / 30_000), Math.floor(fin / 30_000)]);
  const filtered = useMemo(() => trier(filtrer(rangeAlerts, filtres, false)), [rangeAlerts, filtres]);
  const alertColors = useMemo(() => naviresEnAlerte(filtered), [filtered]);

  // Navires suivis : visibles et colorés à toutes les échelles, nouvelles alertes en tête du fil
  const suivisQ = useQuery({ queryKey: ["suivis"], queryFn: api.suivis, refetchInterval: 60_000 });
  const suivis = useMemo(() => new Set<number>((suivisQ.data ?? []).map((v) => Number(v.vessel_id))), [suivisQ.data]);
  const suivre = useMutation({
    mutationFn: ({ id, on }: { id: number; on: boolean }) => (on ? api.suivre(id, auteurMemorise()) : api.nePlusSuivre(id)),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["suivis"] }),
  });

  // Navires affichés, avec la couleur de leur alerte ouverte la plus grave, et le suivi
  const traffic = useMemo<FC>(() => ({ type: "FeatureCollection", features: rawTraffic.features.map((f) => {
    const c = alertColors.get(f.properties.vessel_id), suivi = suivis.has(f.properties.vessel_id);
    return c || suivi ? { ...f, properties: { ...f.properties, ...(c ? { alerte: c } : {}), ...(suivi ? { suivi: true } : {}) } } : f;
  }) }), [rawTraffic, alertColors, suivis]);
  const vessels = useMemo(() => new Map<number, Props>(traffic.features.map((f) => [f.properties.vessel_id, {
    ...f.properties, lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1] }])), [traffic]);

  // Analyses radar (étape 3, panneau existant)
  const [pinnedWaiting, setPinnedWaiting] = useState(false);
  const analysesQ = useQuery({ queryKey: ["analyses"], queryFn: api.analyses, refetchInterval: pinnedWaiting ? 2000 : false });
  const done = analysesQ.data?.features.filter((f) => f.properties.status === "done") ?? [];
  useEffect(() => setPinnedWaiting(pinned !== null && !done.some((f) => f.properties.id === pinned)), [pinned, done.length]);
  const analysis = done.find((f) => f.properties.id === pinned) ?? done[0] ?? null;
  const analysisId = analysis?.properties.id as number | undefined;
  const showDet = actives.includes("detections");
  const detQ = useQuery({ queryKey: ["det", analysisId], queryFn: () => api.detections(analysisId!), enabled: !!analysisId && showDet });
  const analysisAlertsQ = useQuery({ queryKey: ["alerts", analysisId], queryFn: () => api.alerts(analysisId!), enabled: !!analysisId && showDet });
  useEffect(() => {
    const ids = (stream?.analyses ?? []).filter((a) => a.status === "done").map((a) => a.id);
    if (ids.length && (!analysisId || Math.max(...ids) > analysisId)) qc.invalidateQueries({ queryKey: ["analyses"] });
  }, [stream, analysisId, qc]);

  // Couches de fond, chargées à la première activation
  const zonesQ = useQuery({ queryKey: ["zones"], queryFn: api.zones, enabled: actives.includes("mouillages"), staleTime: Infinity });
  const receptionQ = useQuery({ queryKey: ["reception"], queryFn: api.reception, enabled: actives.includes("reception"), staleTime: Infinity });
  const wantInfra = ["electriques", "eoliens", "telecoms", "pipelines"].some((c) => actives.includes(c));
  const infraQ = useQuery({ queryKey: ["infrastructure"], queryFn: api.infrastructure,
    enabled: wantInfra || selection?.kind === "infrastructure" || (pendingSel ?? "").startsWith("infrastructure"), staleTime: Infinity });
  const infraCounts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const f of infraQ.data?.features ?? []) {
      if (filtres.zone && (f.properties.region ?? "").toLowerCase() !== filtres.zone) continue;
      out[f.properties.type] = (out[f.properties.type] ?? 0) + 1;
    }
    return out;
  }, [infraQ.data, filtres.zone]);

  // Sélection : adresse de la page à l'ouverture, puis carte, fil, frise, fiche
  useEffect(() => {
    if (!pendingSel) return;
    const [kind, id] = pendingSel.split(":");
    if (kind === "alerte") {
      const f = rangeAlerts.find((a) => String(a.properties.id) === id);
      if (f) { setSelection({ kind: "alert", feature: f }); setPendingSel(null); }
      else if (alertsQ.isFetched) {     // hors de la plage affichée : l'alerte est demandée par son numéro
        setPendingSel(null);
        api.alert(Number(id)).then((a) => setSelection({ kind: "alert", feature: a })).catch(() => undefined);
      }
    } else if (kind === "navire") {
      setSelection({ kind: "vessel", properties: vessels.get(Number(id)) ?? { vessel_id: Number(id) } });
      setPendingSel(null);
    } else if (kind === "infrastructure") {
      setSelection({ kind: "infrastructure", properties: { id: Number(id) } });
      setPendingSel(null);
    } else if (kind === "zone") {
      setSelection({ kind: "zone", properties: { zone: id } });
      setPendingSel(null);
    } else setPendingSel(null);
  }, [pendingSel, rangeAlerts, alertsQ.isFetched, vessels]);

  // État de l'écran dans l'adresse de la page
  useEffect(() => {
    ecrireAdresse({ temps, sel: selKey(selection) ?? pendingSel, couches: actives, zone: filtres.zone });
  }, [temps, selection, pendingSel, actives, filtres.zone]);

  const pickAlert = useCallback((f: Feature) => {
    setSelection({ kind: "alert", feature: f });
    setFocus({ center: f.geometry.coordinates as [number, number], zoom: 9 });
  }, []);
  const onSelect = useCallback((s: Selection) => setSelection(s), []);
  const pickVessel = useCallback((p: Props) => {
    const id = Number(p.vessel_id), v = vessels.get(id);
    setSelection({ kind: "vessel", properties: { ...p, ...(v ?? {}) } });
    const lon = v?.lon ?? p.lon, lat = v?.lat ?? p.lat;
    if (lon != null) setFocus({ center: [lon, lat], zoom: 10 });
  }, [vessels]);
  const pickInfra = useCallback((id: number, bbox?: [number, number, number, number]) => {
    setSelection({ kind: "infrastructure", properties: { id } });
    const b = bbox ?? bboxOf(infraQ.data?.features.find((f) => f.properties.id === id)?.geometry);
    if (b) setFocus({ bounds: b });
  }, [infraQ.data]);
  const pickZone = useCallback((zone: string) => {
    setSelection({ kind: "zone", properties: { zone } });
    setFocus({ bounds: zoneBbox(zone) });
  }, []);
  const onResult = useCallback((r: Resultat) => {
    setSearching(false);
    if (r.kind === "navire") pickVessel(r.p);
    else if (r.kind === "infrastructure") pickInfra(Number(r.p.id), r.p.bbox);
    else if (r.kind === "alerte") api.alert(Number(r.p.id)).then(pickAlert).catch(() => undefined);
    else if (r.p.zone) pickZone(r.p.zone);
    else setFocus({ center: [r.p.lon, r.p.lat], zoom: r.p.zoom });
  }, [pickVessel, pickInfra, pickZone, pickAlert]);
  // Rejeu de la plage depuis la fiche d'un navire : l'instant repart du début, la sélection reste
  const onRejeu = useCallback(() => {
    setTemps((t) => { const r = resolve(t, Date.now()); return { ...t, mode: "rejeu", debut: r.debut, fin: r.fin, instant: r.debut, lecture: true }; });
  }, []);
  const onStatus = useCallback((status: string) => {
    setSelection((s) => s?.kind === "alert" ? { kind: "alert", feature: { ...s.feature, properties: { ...s.feature.properties, status } } } : s);
  }, []);

  // Trajectoires surlignées : alerte (rendez vous, coupure, passage d'un navire des listes) ou navire sur la plage
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const features: Feature[] = [];
      let ids: number[] = [], start = qDebut, end = qFin, color = "#f0a84b";
      const shift = (s: string, min: number) => new Date(new Date(s).getTime() + min * 60_000).toISOString();
      if (selection?.kind === "alert") {
        const p = selection.feature.properties, d = p.details ?? {};
        if (p.type === "RENDEZVOUS") { ids = (d.navires ?? []).filter(Boolean).map((v: any) => v.vessel_id); start = shift(d.debut, -30); end = shift(d.fin, 30); }
        else if (p.type === "AIS_GAP") {
          ids = [d.navire, ...(d.partenaires_possibles ?? [])].filter(Boolean).map((v: any) => v.vessel_id);
          start = shift(d.dernier_message, -60); end = shift(d.reapparition ?? d.dernier_message, 60);
          if (d.derniere_position && d.position_reapparition) {
            features.push({ type: "Feature", geometry: { type: "LineString", coordinates: [d.derniere_position, d.position_reapparition] },
              properties: { dashed: true, color: typeAlerte("AIS_GAP").couleur } });
          }
        } else if (p.type === "WATCHLIST") { ids = [d.navire?.vessel_id].filter(Boolean); start = d.debut; end = d.fin; color = COULEUR_LISTE; }
      } else if (selection?.kind === "vessel") {
        ids = [Number(selection.properties.vessel_id)];
        color = selection.properties.watch ? COULEUR_LISTE : "#e6ecf0";
      }
      if (!ids.length || !actives.includes("trajectoires")) { if (!cancelled) setHighlight(EMPTY); return; }
      const tracks = await Promise.all(ids.map((id) => api.track(id, start, end).catch(() => null)));
      tracks.forEach((t, i) => t && features.push({ ...t, properties: { color: i === 0 ? color : "#4fb6c8" } }));
      if (!cancelled) setHighlight({ type: "FeatureCollection", features });
    })();
    return () => { cancelled = true; };
  }, [selection, qDebut, qFin, actives]);

  // Mode focus : navires concernés par la sélection
  const infraCardQ = useQuery({ queryKey: ["infraCard", selection?.kind === "infrastructure" ? Number(selection.properties.id) : -1, qDebut, qFin],
    queryFn: () => api.infraCard(Number((selection as any).properties.id), qDebut, qFin),
    enabled: selection?.kind === "infrastructure", staleTime: 60_000 });
  const spotlight = useMemo(() => {
    if (!selection || selection.kind === "detection") return null;
    if (selection.kind === "vessel") return { alertId: null, vesselIds: [Number(selection.properties.vessel_id)] };
    if (selection.kind === "infrastructure") return { alertId: null, infraId: Number(selection.properties.id),
      vesselIds: (infraCardQ.data?.navires ?? []).map((v: Props) => Number(v.vessel_id)) };
    if (selection.kind === "zone") return { alertId: null, vesselIds: [], zone: selection.properties.zone as string };
    return { alertId: selection.feature.properties.id as number, vesselIds: naviresAlerte(selection.feature).map((v) => v.vessel_id) };
  }, [selection, infraCardQ.data]);
  const focusGeom = useMemo<FC>(() => {
    if (selection?.kind === "infrastructure") {
      const f = infraQ.data?.features.find((x) => x.properties.id === Number(selection.properties.id));
      return f ? { type: "FeatureCollection", features: [f] } : EMPTY;
    }
    if (selection?.kind === "zone") {
      return { type: "FeatureCollection", features: zonesGeoJSON().features.filter((f) => f.properties.zone === selection.properties.zone) } as FC;
    }
    return EMPTY;
  }, [selection, infraQ.data]);

  // Infrastructures concernées : liées à une alerte ouverte, ou à moins de 2 milles de la sélection
  const concernedInfra = useMemo(() => {
    if (!concernees) return null;
    const ids = new Set<number>();
    for (const a of filtered) if (statutDe(a) !== "classee") (typeAlerte(a.properties.type).infrastructures?.(a.properties.details ?? {}) ?? []).forEach((i) => ids.add(i));
    const at = selection?.kind === "alert" ? selection.feature.geometry.coordinates
      : selection?.kind === "vessel" ? (() => { const v = vessels.get(Number(selection.properties.vessel_id)); return v ? [v.lon, v.lat] : null; })()
      : null;
    if (at && infraQ.data) nearInfra(infraQ.data, at[0], at[1], DEUX_MILLES).forEach((i) => ids.add(i));
    return [...ids];
  }, [concernees, filtered, selection, vessels, infraQ.data]);

  // Nouvelle analyse radar : tracé puis lancement ; la plage se place autour du passage choisi
  const startDraw = useCallback(() => { setPanel("analyses"); setSelection(null); setDraft(null); setDrawing(true); }, []);
  const cancelDraw = useCallback(() => { setDraft(null); setDrawing(false); }, []);
  const autourDe = useCallback((t: string) => {
    const ms = Date.parse(t);
    setTemps((x) => ({ ...avecPlage(x, ms - 6 * 3600_000, ms + 6 * 3600_000, Date.now()), instant: ms - 600_000 }));
  }, []);
  const onLaunched = useCallback((id: number, pass: Pass) => {
    setDraft(null); setPinned(id); autourDe(pass.acquired_at); qc.invalidateQueries({ queryKey: ["analyses"] });
  }, [autourDe, qc]);
  const pickAnalysis = useCallback((f: Feature) => { setPinned(f.properties.id); autourDe(f.properties.acquired_at); }, [autourDe]);

  // Clavier : Cmd + K ouvre la recherche ; Échap ferme la recherche, le tracé ou la fiche
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setSearching((v) => !v); return; }
      if (e.key !== "Escape") return;
      if (searching) setSearching(false); else if (drawing || draft) cancelDraw(); else setSelection(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [searching, drawing, draft, cancelDraw]);

  const jobs = (stream?.analyses ?? []).filter((a) => a.status !== "done" || a.id !== analysisId);
  const todo = filtered.filter((a) => statutDe(a) === "nouvelle" && a.properties.severity !== "faible").length;
  const selectedId = selection?.kind === "alert" ? selection.feature.properties.id : null;
  const analysisIds = new Set((analysisAlertsQ.data?.features ?? []).map((f) => f.properties.id));
  const mapAlerts = useMemo<FC>(() => ({ type: "FeatureCollection", features: filtered.filter((a) => !analysisIds.has(a.properties.id))
    .map((a) => ({ ...a, properties: { ...a.properties, zone: zoneOf(a.geometry.coordinates[0], a.geometry.coordinates[1]) } })) }),
    [filtered, analysisAlertsQ.data]);

  return (
    <div className="flex h-full flex-col">
      <BarreEtat now={now} connected={!direct || connected} onSearch={() => setSearching(true)} />
      <div className="flex min-h-0 flex-1">
        <Rail active={panel} onSelect={setPanel} onSearch={() => setSearching(true)} alertCount={todo}
          running={jobs.some((a) => a.status === "pending" || a.status === "running")} />
        {panel && (
          <aside className="h-full w-[350px] shrink-0 border-r border-hair bg-panel">
            {panel === "alertes" && <AlertsPanel alerts={rangeAlerts} filtres={filtres} onFiltres={setFiltres} vessels={vessels}
              suivis={suivis} now={now} selectedId={selectedId} onPick={pickAlert} />}
            {panel === "suivis" && <SuivisPanel suivis={suivisQ.data ?? []} onPick={pickVessel} />}
            {panel === "analyses" && (
              <AnalysesPanel jobs={jobs} analysis={analysis} nDetections={detQ.data?.features.length ?? 0} history={done.slice(0, 8)}
                onPick={pickAnalysis} launcher={<NewAnalysis drawing={drawing} draft={draft} onStartDraw={startDraw}
                  onCancel={cancelDraw} onLaunched={onLaunched} />} />
            )}
            {panel === "couches" && <LayersPanel actives={actives} onActives={setActives} zone={filtres.zone}
              onZone={(z) => setFiltres((f) => ({ ...f, zone: z }))} concernees={concernees} onConcernees={setConcernees}
              byType={byType} onByType={setByType} infraCounts={infraCounts} vesselCount={traffic.features.length} />}
          </aside>
        )}
        <main className="relative flex-1">
          <Garde message={L.carte.indisponible}>
          <MapView traffic={traffic} trails={trailsQ.data ?? EMPTY} aoi={showDet ? analysis : null} detections={detQ.data ?? EMPTY}
            analysisAlerts={analysisAlertsQ.data ?? EMPTY} liveAlerts={mapAlerts} zones={zonesQ.data ?? null}
            reception={receptionQ.data ?? null} infrastructure={infraQ.data ?? null} highlight={highlight} actives={actives}
            byType={byType} zone={filtres.zone} concernedInfra={concernedInfra} focus={focus} onSelect={onSelect}
            drawing={drawing} draft={draft} onDraw={(b) => { setDraft(b); setDrawing(false); }} spotlight={spotlight} focusGeom={focusGeom} />
          </Garde>
          <button onClick={drawing || draft ? cancelDraw : startDraw}
            className={`absolute left-4 top-4 z-10 rounded-md border px-3 py-1.5 text-[12.5px] backdrop-blur
              ${drawing ? "border-signal bg-panel/95 text-ink" : "border-hair bg-panel/90 text-ink hover:border-signal"}`}>
            {drawing ? L.analyses.tracerCarte : draft ? L.analyses.annulerTrace : L.analyses.nouvelle}
          </button>
          <DetailPanel selection={selection} onClose={() => setSelection(null)} passTime={analysis?.properties.acquired_at ?? null}
            plage={{ debut: qDebut, fin: qFin }} suivis={suivis} vessels={vessels} alerts={filtered} onStatus={onStatus}
            onPickAlert={pickAlert} onPickVessel={pickVessel} onPickInfra={(id) => pickInfra(id)}
            onSuivre={(id, on) => suivre.mutate({ id, on })} onRejeu={onRejeu} />
          <Frise temps={temps} now={now} onChange={setTemps} alerts={{ type: "FeatureCollection", features: filtered }}
            timeline={timelineQ.isError ? null : timelineQ.data ?? null} onPickAlert={pickAlert} />
        </main>
      </div>
      {searching && <Recherche onClose={() => setSearching(false)} onPick={onResult} />}
    </div>
  );
}
