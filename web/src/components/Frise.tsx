import { CalendarRange, Pause, Play } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import type { Timeline } from "../lib/api";
import { hm, jourHeure, utc } from "../lib/format";
import { L } from "../lib/libelles";
import { PRESETS, VITESSES, avecDuree, avecInstant, avecPlage, iso, lecture, resolve, ticks, versDirect,
  type Temps } from "../lib/temps";
import type { FC, Feature, Props as Objet } from "../lib/types";
import { couleurAlerte, libelleAlerte } from "../registres/alertes";
import { COULEUR_PASSAGE } from "../registres/couches";
import { pistesActives } from "../registres/frise";

interface Props {
  temps: Temps;
  now: number;
  onChange: (t: Temps) => void;
  alerts: FC;
  timeline: Timeline | null;
  onPickAlert: (f: Feature) => void;
  passages: FC;                         // passages Sentinel 1 et 2 de la plage et de la région
  onPickPassage: (f: Feature) => void;
  nuits: Objet[];                       // nuits VIIRS traitées (heures des granules, détections)
}

const F = L.frise;
// Saisie libre en UTC (champ datetime local, lu et écrit en heure UTC)
const versChamp = (ms: number) => new Date(ms).toISOString().slice(0, 16);
const depuisChamp = (v: string) => Date.parse(`${v}:00Z`);

/** Frise : direct, plage ou rejeu. La plage (période étudiée) gouverne tout l'écran ; l'instant place les navires.
 *  Pistes du registre (alertes, coupures du flux AIS hachurées) et histogramme du nombre de navires. */
export default function Frise({ temps, now, onChange, alerts, timeline, onPickAlert, passages, onPickPassage, nuits }: Props) {
  const { debut, fin, instant } = resolve(temps, now);
  const span = fin - debut;
  const track = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ kind: "debut" | "fin" | "instant"; value: number } | null>(null);
  const [libre, setLibre] = useState<{ debut: string; fin: string } | null>(null);
  const pct = (t: number) => ((t - debut) / span) * 100;
  const at = (clientX: number) => {
    const r = track.current!.getBoundingClientRect();
    return debut + Math.min(1, Math.max(0, (clientX - r.left) / r.width)) * span;
  };
  const etiquette = span > 30 * 3600_000 ? jourHeure : hm;

  function startDrag(kind: "debut" | "fin" | "instant", e: React.PointerEvent) {
    e.preventDefault();
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ kind, value: kind === "instant" ? at(e.clientX) : kind === "debut" ? debut : fin });
  }
  function moveDrag(e: React.PointerEvent) {
    if (drag) setDrag({ ...drag, value: at(e.clientX) });
  }
  function endDrag() {
    if (!drag) return;
    if (drag.kind === "instant") onChange(avecInstant(temps, drag.value, now));
    else if (drag.kind === "debut") onChange(avecPlage(temps, Math.min(drag.value, fin - 60_000), fin, now));
    else onChange(avecPlage(temps, debut, Math.max(drag.value, debut + 60_000), now));
    setDrag(null);
  }
  const shownInstant = drag?.kind === "instant" ? drag.value : instant;
  const left = drag?.kind === "debut" ? pct(drag.value) : 0;
  const right = drag?.kind === "fin" ? 100 - pct(drag.value) : 0;

  const densite = timeline?.densite ?? [];
  const max = Math.max(1, ...densite.map((d) => d.navires ?? 0));
  const pistes = pistesActives();
  const marques = useMemo(() => alerts.features.filter((a) => {
    const t = Date.parse(a.properties.event_time);
    return t >= debut && t <= fin;
  }), [alerts, debut, fin]);

  return (
    <div className="absolute inset-x-4 bottom-4 z-10 rounded-lg border border-hair bg-panel/95 px-4 pb-2.5 pt-2.5 shadow-2xl backdrop-blur">
      <div className="flex flex-wrap items-center gap-3">
        <button onClick={() => onChange(versDirect(temps))} title={F.direct}
          className={`flex items-center gap-2 rounded-md border px-2.5 py-1 text-[12px] font-medium uppercase tracking-wide
            ${temps.mode === "direct" ? "border-signal/50 text-signal" : "border-hair text-muted hover:border-signal hover:text-ink"}`}>
          <span className={`h-1.5 w-1.5 rounded-full ${temps.mode === "direct" ? "animate-pulse bg-signal" : "bg-faint"}`} />{F.direct}
        </button>
        {temps.mode !== "direct" && (
          <button aria-label={temps.lecture ? F.pause : F.lecture} onClick={() => onChange(lecture(temps, now, !temps.lecture))}
            className="flex h-8 w-8 items-center justify-center rounded-full bg-ink text-abyss hover:bg-white">
            {temps.lecture ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" className="ml-0.5" />}
          </button>
        )}
        <div className="min-w-[205px] font-cond text-[20px] font-medium leading-none tabular-nums" title={F.instant}>
          {utc(iso(shownInstant))}
        </div>
        {temps.mode === "rejeu" && (
          <div className="flex rounded-md border border-hair p-0.5" role="group" aria-label={F.vitesse}>
            {VITESSES.map((v) => (
              <button key={v} onClick={() => onChange({ ...temps, vitesse: v })}
                className={`rounded px-2 py-0.5 text-[12px] ${temps.vitesse === v ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>× {v}</button>
            ))}
          </div>
        )}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-[12px] text-muted">{temps.mode === "direct" ? "" : `${jourHeure(debut)} ${F.au} ${jourHeure(fin)}`}</span>
          <div className="flex rounded-md border border-hair p-0.5" role="group" aria-label={F.plage}>
            {PRESETS.map((p) => (
              <button key={p} onClick={() => onChange(avecDuree(temps, p, now))}
                className={`rounded px-2 py-0.5 text-[12px] ${temps.duree === p ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>{F.presets[p]}</button>
            ))}
            <button title={F.libre} aria-label={F.libre} onClick={() => setLibre(libre ? null : { debut: versChamp(debut), fin: versChamp(fin) })}
              className={`rounded px-2 py-0.5 ${temps.duree === "libre" ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}><CalendarRange size={13} /></button>
          </div>
        </div>
      </div>
      {libre && (
        <div className="mt-2 flex flex-wrap items-center justify-end gap-2 text-[12px] text-muted">
          {F.du} <input type="datetime-local" value={libre.debut} onChange={(e) => setLibre({ ...libre, debut: e.target.value })}
            className="rounded border border-hair bg-abyss px-1.5 py-0.5 text-ink" />
          {F.au} <input type="datetime-local" value={libre.fin} onChange={(e) => setLibre({ ...libre, fin: e.target.value })}
            className="rounded border border-hair bg-abyss px-1.5 py-0.5 text-ink" /> UTC
          <button onClick={() => { onChange(avecPlage(temps, depuisChamp(libre.debut), depuisChamp(libre.fin), now)); setLibre(null); }}
            className="rounded-md border border-hair px-2 py-0.5 text-ink hover:border-muted">{F.appliquer}</button>
          <span className="text-faint">{F.limite}</span>
        </div>
      )}

      {/* Pistes : alertes, coupures du flux, histogramme, puis passages satellites (plein : acquis, pointillé : prévu) ; un clic place l'instant, les bords réduisent la plage */}
      <div ref={track} className="relative mt-2 h-[76px] cursor-crosshair select-none"
        onPointerDown={(e) => startDrag("instant", e)} onPointerMove={moveDrag} onPointerUp={endDrag}>
        {pistes.some((p) => p.id === "alertes") && marques.map((a) => (
          <span key={a.properties.id} title={`${libelleAlerte(a.properties.type)}, ${utc(a.properties.event_time)}`}
            onPointerDown={(e) => { e.stopPropagation(); onPickAlert(a); }}
            className="absolute top-0 h-3 w-[3px] -translate-x-1/2 cursor-pointer rounded-sm"
            style={{ left: `${pct(Date.parse(a.properties.event_time))}%`, background: couleurAlerte(a.properties.type),
                     opacity: a.properties.status === "classee" || a.properties.severity === "faible" ? 0.45 : 1 }} />
        ))}
        {pistes.some((p) => p.id === "viirs") && nuits.map((n) => {
          const a = Math.max(debut, Date.parse(n.debut)), b = Math.min(fin, Date.parse(n.fin));
          if (b < a) return null;
          return <span key={n.nuit} title={F.nuit(n.nuit, n.granules, n.detections, n.sans_ais, n.non_evaluables ?? 0, n.lune)}
            className="absolute top-[57px] h-[5px] rounded-sm bg-[#8a93c9]/70"
            style={{ left: `${pct(a)}%`, width: `max(4px, ${pct(b) - pct(a)}%)` }} />;
        })}
        {pistes.some((p) => p.id === "passages") && passages.features.map((f) => {
          const t = Date.parse(f.properties.acquired_at);
          if (t < debut || t > fin) return null;
          const prevu = f.properties.statut === "prevu";
          return (
            <span key={f.properties.id} title={F.passage(f.properties.satellite, utc(f.properties.acquired_at), prevu)}
              onPointerDown={(e) => { e.stopPropagation(); onPickPassage(f); }}
              className={`absolute bottom-0 h-2.5 w-[5px] -translate-x-1/2 cursor-pointer rounded-sm border ${prevu ? "border-dashed" : ""}`}
              style={{ left: `${pct(t)}%`, borderColor: COULEUR_PASSAGE, background: prevu ? "transparent" : COULEUR_PASSAGE }} />
          );
        })}
        {pistes.some((p) => p.id === "coupures") && (timeline?.coupures ?? []).map((c, i) => {
          const a = Math.max(debut, Date.parse(c.debut)), b = Math.min(fin, Date.parse(c.fin));
          if (b <= a) return null;
          return <span key={i} title={F.coupure(jourHeure(c.debut), jourHeure(c.fin))} className="absolute top-4 h-[38px]"
            style={{ left: `${pct(a)}%`, width: `max(2px, ${pct(b) - pct(a)}%)`,
                     background: "repeating-linear-gradient(135deg, rgba(124,139,151,.55) 0 2px, transparent 2px 5px)" }} />;
        })}
        <svg className="pointer-events-none absolute inset-x-0 top-4 h-[38px] w-full" viewBox="0 0 1000 38" preserveAspectRatio="none" aria-label={F.densite}>
          {densite.map((d, i) => d.navires == null ? null : (
            <rect key={i} x={(i / densite.length) * 1000} width={Math.max(1, 1000 / densite.length - 0.6)}
              y={38 - (d.navires / max) * 34} height={(d.navires / max) * 34} fill="#26323d" />
          ))}
        </svg>
        {(left > 0 || right > 0) && (
          <span className="pointer-events-none absolute inset-y-0 bg-abyss/60" style={{ left: 0, width: `${left}%` }} />
        )}
        {right > 0 && <span className="pointer-events-none absolute inset-y-0 right-0 bg-abyss/60" style={{ width: `${right}%` }} />}
        <span className="pointer-events-none absolute top-0 h-full w-0.5 -translate-x-1/2 bg-ink" style={{ left: `${pct(shownInstant)}%` }} />
        {(["debut", "fin"] as const).map((k) => (
          <span key={k} onPointerDown={(e) => startDrag(k, e)} title={F.libre}
            className="absolute top-3 h-[42px] w-2 cursor-ew-resize rounded-sm border border-hair bg-raised hover:border-muted"
            style={k === "debut" ? { left: `calc(${left}% - 4px)` } : { right: `calc(${right}% - 4px)` }} />
        ))}
      </div>
      <div className="relative h-3.5 text-[10.5px] text-faint">
        {ticks(debut, fin).map((t) => (
          <span key={t} className="absolute -translate-x-1/2" style={{ left: `${pct(t)}%` }}>{etiquette(t)}</span>
        ))}
      </div>
      <span className="sr-only">{max > 1 ? F.navires(max) : ""}</span>
    </div>
  );
}
