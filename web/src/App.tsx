import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useState } from "react";
import AlertsPanel from "./components/AlertsPanel";
import AnalysesPanel from "./components/AnalysesPanel";
import DetailPanel from "./components/DetailPanel";
import LayersPanel, { type LayerState } from "./components/LayersPanel";
import MapView from "./components/MapView";
import NewAnalysis from "./components/NewAnalysis";
import Rail, { type PanelId } from "./components/Rail";
import Timeline from "./components/Timeline";
import { api, type Pass } from "./lib/api";
import { EMPTY, type FC, type Feature, type Selection } from "./lib/types";
import { useStream } from "./lib/useStream";

export default function App() {
  const qc = useQueryClient();
  const { data: stream, connected } = useStream();
  const [panel, setPanel] = useState<PanelId | null>("alertes");
  const [layers, setLayers] = useState<LayerState>({ analysis: true, zones: false, reception: false, byType: false });
  const [selection, setSelection] = useState<Selection | null>(null);
  const [highlight, setHighlight] = useState<FC>(EMPTY);
  const [focus, setFocus] = useState<{ center: [number, number]; zoom: number } | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [draft, setDraft] = useState<number[] | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);

  const [pinnedWaiting, setPinnedWaiting] = useState(false);
  const analysesQ = useQuery({ queryKey: ["analyses"], queryFn: api.analyses,
    refetchInterval: pinnedWaiting ? 2000 : false });
  // Analyse affichée : la plus récente du jour rejoué, à défaut la plus récente tout court
  const replayDay = stream?.clock.now.slice(0, 10);
  const done = analysesQ.data?.features.filter((f) => f.properties.status === "done") ?? [];
  useEffect(() => {
    setPinnedWaiting(pinned !== null && !done.some((f) => f.properties.id === pinned));
  }, [pinned, done.length]);
  const analysis = done.find((f) => f.properties.id === pinned)
    ?? done.find((f) => String(f.properties.acquired_at).slice(0, 10) === replayDay) ?? done[0] ?? null;
  const analysisId = analysis?.properties.id as number | undefined;
  const detQ = useQuery({ queryKey: ["det", analysisId], queryFn: () => api.detections(analysisId!), enabled: !!analysisId });
  const alertsQ = useQuery({ queryKey: ["alerts", analysisId], queryFn: () => api.alerts(analysisId!), enabled: !!analysisId });

  const trailsQ = useQuery({ queryKey: ["trails"], queryFn: api.trails, refetchInterval: 3000 });
  const zonesQ = useQuery({ queryKey: ["zones"], queryFn: api.zones, enabled: layers.zones, staleTime: Infinity });
  const receptionQ = useQuery({ queryKey: ["reception"], queryFn: api.reception, enabled: layers.reception, staleTime: Infinity });
  const daysQ = useQuery({ queryKey: ["days"], queryFn: api.days, staleTime: Infinity });

  const day = stream?.clock.now.slice(0, 10);
  const dayAlertsQ = useQuery({ queryKey: ["dayAlerts", day], queryFn: () => api.alertsOfDay(day!), enabled: !!day, staleTime: 60_000 });

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
    setPanel("analyses"); setSelection(null); setDraft(null); setDrawing(true);
  }, []);
  const onDraw = useCallback((b: number[]) => { setDraft(b); setDrawing(false); }, []);
  const cancelDraw = useCallback(() => { setDraft(null); setDrawing(false); }, []);
  const onLaunched = useCallback((id: number, pass: Pass) => {
    setDraft(null);
    setPinned(id);
    onCommand({ action: "seek", time: new Date(new Date(pass.acquired_at).getTime() - 600_000).toISOString() });
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
    onCommand({ action: "seek", time: new Date(new Date(f.properties.acquired_at).getTime() - 600_000).toISOString() });
  }, [onCommand]);

  const timelineCommand = useCallback((body: Parameters<typeof api.clock>[0]) => {
    if (body.action === "seek") setPinned(null);
    onCommand(body);
  }, [onCommand]);

  const pickAlert = useCallback((f: Feature) => {
    setSelection({ kind: "alert", feature: f });
    setFocus({ center: f.geometry.coordinates as [number, number], zoom: 11 });
  }, []);

  // Trajectoires surlignées pour les rendez vous et les coupures AIS
  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (selection?.kind !== "alert") { setHighlight(EMPTY); return; }
      const p = selection.feature.properties;
      const d = p.details ?? {};
      const shift = (iso: string, min: number) => new Date(new Date(iso).getTime() + min * 60_000).toISOString();
      const features: Feature[] = [];
      let ids: number[] = [];
      let start = "", end = "";
      if (p.type === "RENDEZVOUS") {
        ids = (d.navires ?? []).filter(Boolean).map((v: any) => v.vessel_id);
        start = shift(d.debut, -30); end = shift(d.fin, 30);
      } else if (p.type === "AIS_GAP") {
        ids = [d.navire, ...(d.partenaires_possibles ?? [])].filter(Boolean).map((v: any) => v.vessel_id);
        start = shift(d.dernier_message, -60); end = shift(d.reapparition ?? d.dernier_message, 60);
        if (d.derniere_position && d.position_reapparition) {
          features.push({ type: "Feature", geometry: { type: "LineString", coordinates: [d.derniere_position, d.position_reapparition] },
                          properties: { dashed: true, color: "#ef6461" } });
        }
      }
      const tracks = await Promise.all(ids.map((id) => api.track(id, start, end).catch(() => null)));
      tracks.forEach((t, i) => t && features.push({ ...t, properties: { color: i === 0 ? "#f0a84b" : "#4fb6c8" } }));
      if (!cancelled) setHighlight({ type: "FeatureCollection", features });
    })();
    return () => { cancelled = true; };
  }, [selection]);

  const onSelect = useCallback((s: Selection) => setSelection(s), []);
  const liveAlerts = stream?.live_alerts ?? EMPTY;
  const analysisAlerts = alertsQ.data ?? EMPTY;
  const jobs = (stream?.analyses ?? []).filter((a) => a.status !== "done" || a.id !== analysisId);
  const passes = useMemo(() => (analysesQ.data?.features ?? [])
    .filter((f) => f.properties.status === "done")
    .map((f) => ({ id: f.properties.id, time: f.properties.acquired_at })), [analysesQ.data]);
  const openAlerts = [...analysisAlerts.features, ...liveAlerts.features]
    .filter((a) => a.properties.severity !== "faible" && a.properties.status !== "classee").length;
  const selectedId = selection?.kind === "alert" ? selection.feature.properties.id : null;
  const vessels = stream?.traffic.features.length ?? 0;

  return (
    <div className="flex h-full">
      <Rail active={panel} onSelect={setPanel} alertCount={openAlerts}
        running={jobs.some((a) => a.status === "pending" || a.status === "running")} />
      {panel && (
        <aside className="h-full w-[340px] shrink-0 border-r border-hair bg-panel">
          {panel === "alertes" && <AlertsPanel analysisAlerts={analysisAlerts} liveAlerts={liveAlerts} selectedId={selectedId} onPick={pickAlert} />}
          {panel === "analyses" && (
            <AnalysesPanel jobs={jobs} analysis={analysis} nDetections={detQ.data?.features.length ?? 0}
              history={done.slice(0, 8)} onPick={pickAnalysis}
              launcher={<NewAnalysis drawing={drawing} draft={draft} onStartDraw={startDraw} onCancel={cancelDraw} onLaunched={onLaunched} />} />
          )}
          {panel === "couches" && <LayersPanel state={layers} onChange={setLayers} />}
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
          highlight={highlight}
          show={layers}
          focus={focus}
          onSelect={onSelect}
          drawing={drawing}
          draft={draft}
          onDraw={onDraw}
        />
        <div className="absolute left-4 top-4 z-10 flex items-center gap-2 rounded-md border border-hair bg-panel/90 px-3 py-1.5 text-[12px] text-muted backdrop-blur">
          <span className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-signal" : "bg-gap"}`} />
          {connected ? `${vessels} navires` : "Reconnexion…"}
        </div>
        <button onClick={drawing || draft ? cancelDraw : startDraw}
          className={`absolute left-4 top-14 z-10 rounded-md border px-3 py-1.5 text-[12.5px] backdrop-blur
            ${drawing ? "border-signal bg-panel/95 text-ink" : "border-hair bg-panel/90 text-ink hover:border-signal"}`}>
          {drawing ? "Cliquez deux coins opposés, Échap pour annuler" : draft ? "Annuler le tracé" : "Nouvelle analyse"}
        </button>
        <DetailPanel selection={selection} onClose={() => setSelection(null)} />
        <Timeline clock={stream?.clock ?? null} days={(daysQ.data ?? []).map((d) => d.day)}
          dayAlerts={dayAlertsQ.data ?? EMPTY} passes={passes} onCommand={timelineCommand} />
      </main>
    </div>
  );
}
