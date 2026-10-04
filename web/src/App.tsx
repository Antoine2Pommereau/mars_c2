import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import AlertsPanel from "./components/AlertsPanel";
import AnalysesPanel from "./components/AnalysesPanel";
import DetailPanel from "./components/DetailPanel";
import LayersPanel, { type LayerState } from "./components/LayersPanel";
import MapView from "./components/MapView";
import NewAnalysis from "./components/NewAnalysis";
import Rail, { type PanelId } from "./components/Rail";
import RegionsPanel from "./components/RegionsPanel";
import VesselsPanel from "./components/VesselsPanel";
import Timeline from "./components/Timeline";
import { api, bathymetryImageUrl, type Pass } from "./lib/api";
import { replayStart, HIGHLIGHT_PRIMARY, HIGHLIGHT_SECONDARY, HIGHLIGHT_DASHED } from "./lib/format";
import { EMPTY, type AlertProps, type AlertStatus, type FC, type Feature, type Selection, type VesselRef } from "./lib/types";
import { useStream } from "./lib/useStream";

export default function App() {
  const qc = useQueryClient();
  const { data: stream, connected } = useStream();
  const [panel, setPanel] = useState<PanelId | null>("alertes");
  const [layers, setLayers] = useState<LayerState>({ analysis: true, zones: false, reception: false, byType: false, infrastructure: false, bathymetry: false, protectedAreas: false });
  const [selection, setSelection] = useState<Selection | null>(null);
  const [highlight, setHighlight] = useState<FC>(EMPTY);
  const [reach, setReach] = useState<FC>(EMPTY);
  const [tipcueMsg, setTipcueMsg] = useState<string | null>(null);
  const [focus, setFocus] = useState<{ center: [number, number]; zoom: number } | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [draft, setDraft] = useState<number[] | null>(null);
  const [drawTarget, setDrawTarget] = useState<"analysis" | "region" | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);
  const [bounds, setBounds] = useState<number[] | null>(null);

  // Une analyse épinglée pas encore terminée : on interroge l'API toutes les deux secondes
  // jusqu'à ce qu'elle apparaisse terminée dans la liste
  const isPinnedWaiting = (features: Feature[]) =>
    pinned !== null && !features.some((f) => f.properties.status === "done" && f.properties.id === pinned);
  const analysesQ = useQuery({
    queryKey: ["analyses"], queryFn: api.analyses,
    refetchInterval: (q) => (isPinnedWaiting(q.state.data?.features ?? []) ? 2000 : false),
  });
  // Analyse affichée : la plus récente du jour rejoué, à défaut la plus récente tout court
  const replayDay = stream?.clock.now.slice(0, 10);
  const done = analysesQ.data?.features.filter((f) => f.properties.status === "done") ?? [];
  const analysis = done.find((f) => f.properties.id === pinned)
    ?? done.find((f) => String(f.properties.acquired_at).slice(0, 10) === replayDay) ?? done[0] ?? null;
  const analysisId = analysis?.properties.id as number | undefined;
  const detQ = useQuery({ queryKey: ["det", analysisId], queryFn: (ctx) => api.detections(analysisId!, ctx), enabled: !!analysisId });
  const alertsQ = useQuery({ queryKey: ["alerts", analysisId], queryFn: (ctx) => api.alerts(analysisId!, ctx), enabled: !!analysisId });

  const trailsQ = useQuery({ queryKey: ["trails"], queryFn: api.trails, refetchInterval: 3000 });
  const zonesQ = useQuery({ queryKey: ["zones"], queryFn: api.zones, enabled: layers.zones, staleTime: Infinity });
  const receptionQ = useQuery({ queryKey: ["reception"], queryFn: api.reception, enabled: layers.reception, staleTime: Infinity });
  const daysQ = useQuery({ queryKey: ["days"], queryFn: api.days, staleTime: Infinity });

  // Région active et ses couches de contexte provisionnées (câbles, pipelines, isobathes)
  const regionsQ = useQuery({ queryKey: ["regions"], queryFn: api.regions,
    refetchInterval: (q) => (q.state.data?.some((r) => r.layers?.some((l) => l.status === "en_cours")) ? 2000 : false) });
  const activeRegion = regionsQ.data?.find((r) => r.active) ?? regionsQ.data?.[0];
  const regionId = activeRegion?.id;
  const infraQ = useQuery({ queryKey: ["infrastructure", regionId], queryFn: (ctx) => api.infrastructure(regionId!, ctx), enabled: !!regionId && layers.infrastructure, staleTime: Infinity });
  const bathyQ = useQuery({ queryKey: ["bathymetry", regionId], queryFn: (ctx) => api.bathymetry(regionId!, ctx), enabled: !!regionId && layers.bathymetry, staleTime: Infinity });
  const protectedQ = useQuery({ queryKey: ["protected", regionId], queryFn: (ctx) => api.protectedAreas(regionId!, ctx), enabled: !!regionId && layers.protectedAreas, staleTime: Infinity });
  const bathymetryImage = regionId && layers.bathymetry && activeRegion
    ? { url: bathymetryImageUrl(regionId), bbox: activeRegion.bbox } : null;

  // Relevé de profondeur au survol de la carte, quand la bathymétrie est affichée
  const [depth, setDepth] = useState<{ depth_m: number | null; note?: string } | null>(null);
  const depthAbort = useRef<AbortController | null>(null);
  const onProbe = useCallback(async (pt: { lon: number; lat: number } | null) => {
    if (!pt || !regionId) { setDepth(null); return; }
    depthAbort.current?.abort();
    const ac = new AbortController();
    depthAbort.current = ac;
    try { setDepth(await api.depth(regionId, pt.lon, pt.lat, ac.signal)); }
    catch { /* survol interrompu, on ignore */ }
  }, [regionId]);

  const day = stream?.clock.now.slice(0, 10);
  const dayAlertsQ = useQuery({ queryKey: ["dayAlerts", day], queryFn: (ctx) => api.alertsOfDay(day!, ctx), enabled: !!day, staleTime: 60_000 });

  // Une analyse plus récente vient de se terminer : on recharge la liste
  useEffect(() => {
    const done = (stream?.analyses ?? []).filter((a) => a.status === "done").map((a) => a.id);
    if (done.length && (!analysisId || Math.max(...done) > analysisId)) qc.invalidateQueries({ queryKey: ["analyses"] });
  }, [stream, analysisId, qc]);

  const onCommand = useCallback(async (body: Parameters<typeof api.clock>[0]) => {
    await api.clock(body);
    qc.invalidateQueries({ queryKey: ["trails"] });
  }, [qc]);

  // Nouvelle analyse : tracé, puis lancement ; le rejeu se place juste avant le passage choisi
  const startDraw = useCallback(() => {
    setPanel("analyses"); setSelection(null); setDraft(null); setDrawTarget("analysis"); setDrawing(true);
  }, []);
  const startRegionDraw = useCallback(() => {
    setPanel("regions"); setSelection(null); setDraft(null); setDrawTarget("region"); setDrawing(true);
  }, []);
  const onDraw = useCallback((b: number[]) => { setDraft(b); setDrawing(false); }, []);
  const cancelDraw = useCallback(() => { setDraft(null); setDrawing(false); setDrawTarget(null); }, []);
  const onCreateRegion = useCallback(async (name: string, bbox: number[]) => {
    try {
      const { id } = await api.createRegion(name, bbox);
      await api.provisionRegion(id);
      await api.activateRegion(id);
      setDraft(null); setDrawTarget(null);
      qc.invalidateQueries({ queryKey: ["regions"] });
    } catch { /* nom déjà pris ou erreur réseau : le formulaire reste ouvert */ }
  }, [qc]);
  const onActivateRegion = useCallback(async (id: number) => {
    await api.activateRegion(id);
    qc.invalidateQueries({ queryKey: ["regions"] });
  }, [qc]);
  const onLaunched = useCallback((id: number, pass: Pass) => {
    setDraft(null);
    setPinned(id);
    onCommand({ action: "seek", time: replayStart(pass.acquired_at) });
    qc.invalidateQueries({ queryKey: ["analyses"] });
  }, [onCommand, qc]);
  // Échap : abandonne le tracé ou ferme la fiche
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (drawing || draft) cancelDraw(); else setSelection(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawing, draft, cancelDraw]);

  // Choix d'une analyse dans l'historique : affichage, et rejeu placé juste avant son passage
  const pickAnalysis = useCallback((f: Feature) => {
    setPinned(f.properties.id);
    onCommand({ action: "seek", time: replayStart(f.properties.acquired_at) });
  }, [onCommand]);

  const timelineCommand = useCallback((body: Parameters<typeof api.clock>[0]) => {
    if (body.action === "seek") setPinned(null);
    onCommand(body);
  }, [onCommand]);

  const pickAlert = useCallback((f: Feature<AlertProps>) => {
    setSelection({ kind: "alert", feature: f });
    setFocus({ center: f.geometry.coordinates as [number, number], zoom: 11 });
  }, []);
  const openDossier = useCallback((vesselId: number) => setSelection({ kind: "vessel_dossier", vesselId }), []);
  // Tip and Cue : zone atteignable affichee, analyse radar lancee sur le prochain passage qui la couvre
  const onTipCue = useCallback(async (alertId: number) => {
    setTipcueMsg(null);
    try {
      const r = await api.tipcue(alertId, true);
      setReach({ type: "FeatureCollection", features: [r.zone_atteignable] });
      if (r.analyse_id && r.passage) {
        setPinned(r.analyse_id);
        onCommand({ action: "seek", time: replayStart(r.passage.acquired_at) });
        qc.invalidateQueries({ queryKey: ["analyses"] });
        setPanel("analyses");
      } else if (r.raison) {
        setTipcueMsg(r.raison);
      }
    } catch {
      setTipcueMsg("Tip and cue indisponible pour cette alerte.");
    }
  }, [onCommand, qc]);

  // Trajectoires surlignées pour les rendez vous et les coupures AIS
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (selection?.kind !== "alert") { setHighlight(EMPTY); return; }
      const p: AlertProps = selection.feature.properties;
      const shift = (iso: string, min: number) => new Date(new Date(iso).getTime() + min * 60_000).toISOString();
      const features: Feature[] = [];
      let ids: number[] = [];
      let start = "", end = "";
      if (p.type === "RENDEZVOUS") {
        const d = p.details ?? {};
        ids = (d.navires ?? []).filter(Boolean).map((v: VesselRef) => v.vessel_id);
        start = shift(d.debut ?? "", -30); end = shift(d.fin ?? "", 30);
      } else if (p.type === "AIS_GAP") {
        const d = p.details ?? {};
        ids = [d.navire, ...(d.partenaires_possibles ?? [])].filter(Boolean).map((v) => v!.vessel_id);
        start = shift(d.dernier_message ?? "", -60); end = shift(d.reapparition ?? d.dernier_message ?? "", 60);
        if (d.derniere_position && d.position_reapparition) {
          features.push({ type: "Feature", geometry: { type: "LineString", coordinates: [d.derniere_position, d.position_reapparition] },
                          properties: { dashed: true, color: HIGHLIGHT_DASHED } });
        }
      }
      const tracks = await Promise.all(ids.map((id) => api.track(id, start, end).catch(() => null)));
      tracks.forEach((t, i) => t && features.push({ ...t, properties: { color: i === 0 ? HIGHLIGHT_PRIMARY : HIGHLIGHT_SECONDARY } }));
      if (!cancelled) setHighlight({ type: "FeatureCollection", features });
    })();
    return () => { cancelled = true; };
  }, [selection]);

  const onSelect = useCallback((s: Selection) => setSelection(s), []);
  // Après une décision : la fiche ouverte reflète tout de suite le nouveau statut
  const onStatus = useCallback((status: string) => {
    setSelection((s) => s?.kind === "alert"
      ? { kind: "alert", feature: { ...s.feature, properties: { ...s.feature.properties, status: status as AlertStatus } } } : s);
  }, []);
  const liveAlerts = stream?.live_alerts ?? EMPTY;
  const analysisAlerts = alertsQ.data ?? EMPTY;
  const jobs = (stream?.analyses ?? []).filter((a) => a.status !== "done" || a.id !== analysisId);
  const passes = useMemo(() => (analysesQ.data?.features ?? [])
    .filter((f) => f.properties.status === "done")
    .map((f) => ({ id: f.properties.id, time: f.properties.acquired_at })), [analysesQ.data]);
  // Pastille du rail = nombre d'alertes à traiter, cohérent avec l'onglet « À traiter » du panneau.
  // Les alertes de zone protégée sont une couche à activer, pas dans le fil : exclues du compte.
  const todoAlerts = [...analysisAlerts.features, ...liveAlerts.features]
    .filter((a) => a.properties.type !== "ZONE_BREACH" && (a.properties.status ?? "nouvelle") === "nouvelle").length;
  const selectedId = selection?.kind === "alert" ? selection.feature.properties.id : null;

  // Navires concernés par la sélection, pour le mode focus de la carte
  const spotlight = useMemo(() => {
    if (!selection) return null;
    if (selection.kind === "vessel") return { alertId: null, vesselIds: [selection.properties.vessel_id] };
    if (selection.kind !== "alert") return null;
    const p: AlertProps = selection.feature.properties;
    const vs: (VesselRef | undefined)[] =
      p.type === "RENDEZVOUS" ? p.details?.navires ?? []
      : p.type === "AIS_GAP" ? [p.details?.navire, ...(p.details?.partenaires_possibles ?? [])]
      : p.type === "AIS_UNCONFIRMED" ? [p.details?.navire]
      : [];
    return { alertId: p.id, vesselIds: vs.filter((v): v is VesselRef => !!v).map((v) => v.vessel_id) };
  }, [selection]);
  const vessels = stream?.traffic.features.length ?? 0;

  return (
    <div className="flex h-full">
      <Rail active={panel} onSelect={setPanel} alertCount={todoAlerts}
        running={jobs.some((a) => a.status === "pending" || a.status === "running")} />
      {panel && (
        <aside className="h-full w-[340px] shrink-0 border-r border-hair bg-panel">
          {panel === "alertes" && <AlertsPanel bounds={bounds} now={stream?.clock.now ?? null} analysisAlerts={analysisAlerts} liveAlerts={liveAlerts} selectedId={selectedId} onPick={pickAlert} />}
          {panel === "analyses" && (
            <AnalysesPanel jobs={jobs} analysis={analysis} nDetections={detQ.data?.features.length ?? 0}
              history={done.slice(0, 8)} onPick={pickAnalysis}
              launcher={<NewAnalysis drawing={drawTarget !== "region" && drawing} draft={drawTarget === "region" ? null : draft} onStartDraw={startDraw} onCancel={cancelDraw} onLaunched={onLaunched} />} />
          )}
          {panel === "navires" && <VesselsPanel onOpen={openDossier} />}
          {panel === "couches" && <LayersPanel state={layers} onChange={setLayers} />}
          {panel === "regions" && (
            <RegionsPanel regions={regionsQ.data ?? []} activeId={regionId}
              drawing={drawing && drawTarget === "region"} draft={drawTarget === "region" ? draft : null}
              onStartDraw={startRegionDraw} onCancelDraw={cancelDraw} onCreate={onCreateRegion} onActivate={onActivateRegion} />
          )}
        </aside>
      )}
      <main className="relative flex-1">
        <MapView
          traffic={stream?.traffic ?? EMPTY}
          trails={trailsQ.data ?? EMPTY}
          aoi={analysis}
          detections={detQ.data ?? EMPTY}
          analysisAlerts={analysisAlerts}
          liveAlerts={liveAlerts}
          zones={zonesQ.data ?? null}
          reception={receptionQ.data ?? null}
          infrastructure={infraQ.data ?? null}
          bathymetry={bathyQ.data ?? null}
          protectedAreas={protectedQ.data ?? null}
          bathymetryImage={bathymetryImage}
          regionBbox={activeRegion?.bbox ?? null}
          reach={reach}
          highlight={highlight}
          show={layers}
          focus={focus}
          onSelect={onSelect}
          drawing={drawing}
          draft={draft}
          onDraw={onDraw}
          spotlight={spotlight}
          onBounds={setBounds}
          onProbe={onProbe}
        />
        {layers.bathymetry && depth && (
          <div className="absolute left-4 top-24 z-10 rounded-md border border-hair bg-panel/90 px-3 py-1.5 text-[12px] backdrop-blur">
            {depth.depth_m != null
              ? <><span className="text-muted">Profondeur</span> <span className="tabular-nums text-ink">{Math.round(depth.depth_m)} m</span></>
              : <span className="text-muted">{depth.note === "terre" ? "Terre" : "Profondeur indisponible"}</span>}
          </div>
        )}
        <div className="absolute left-4 top-4 z-10 flex items-center gap-2 rounded-md border border-hair bg-panel/90 px-3 py-1.5 text-[12px] text-muted backdrop-blur">
          <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-signal" : "bg-gap"}`} />
          {connected ? `${vessels} navires` : "Reconnexion…"}
        </div>
        <button onClick={drawing || draft ? cancelDraw : startDraw}
          className={`absolute left-4 top-14 z-10 rounded-md border px-3 py-1.5 text-[12.5px] backdrop-blur
            ${drawing ? "border-signal bg-panel/95 text-ink" : "border-hair bg-panel/90 text-ink hover:border-signal"}`}>
          {drawing ? "Cliquez deux coins opposés, Échap pour annuler" : draft ? "Annuler le tracé" : "Nouvelle analyse"}
        </button>
        {tipcueMsg && (
          <div className="absolute left-1/2 top-4 z-20 flex -translate-x-1/2 items-center gap-3 rounded-md border border-hair bg-panel/95 px-3 py-2 text-[12.5px] text-ink backdrop-blur">
            {tipcueMsg}
            <button onClick={() => { setTipcueMsg(null); setReach(EMPTY); }} className="text-muted hover:text-ink" aria-label="Fermer">✕</button>
          </div>
        )}
        <DetailPanel selection={selection} onClose={() => setSelection(null)}
          passTime={analysis?.properties.acquired_at ?? null} onStatus={onStatus} onOpenDossier={openDossier} onTipCue={onTipCue} />
        <Timeline clock={stream?.clock ?? null} days={(daysQ.data ?? []).map((d) => d.day)}
          dayAlerts={dayAlertsQ.data ?? EMPTY} passes={passes} onCommand={timelineCommand} />
      </main>
    </div>
  );
}
