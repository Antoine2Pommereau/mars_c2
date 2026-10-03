import { Pause, Play } from "lucide-react";
import { useState } from "react";
import type { Clock, FC } from "../lib/types";
import { ALERT_COLOR, ALERT_LABEL, dayLabel, utc } from "../lib/format";

const DAY = 86400;

interface Props {
  clock: Clock | null;
  days: string[];
  dayAlerts: FC;
  passes: { id: number; time: string }[];
  onCommand: (body: { action: "play" | "pause" | "speed" | "seek"; speed?: number; time?: string }) => void;
}

/** Frise de la journée : où en est le rejeu, quand surviennent les alertes, quand passe le satellite. */
export default function Timeline({ clock, days, dayAlerts, passes, onCommand }: Props) {
  const [drag, setDrag] = useState<number | null>(null);
  if (!clock) return null;

  const now = new Date(clock.now);
  const dayStart = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const day = new Date(dayStart).toISOString().slice(0, 10);
  const pos = drag ?? (now.getTime() - dayStart) / 1000;
  const at = (iso: string) => ((new Date(iso).getTime() - dayStart) / 1000 / DAY) * 100;
  const seek = (s: number) => onCommand({ action: "seek", time: new Date(dayStart + s * 1000).toISOString() });
  const commit = () => { if (drag !== null) { seek(drag); setDrag(null); } };
  const shownTime = drag !== null ? utc(new Date(dayStart + drag * 1000).toISOString()) : utc(clock.now);
  const passesToday = passes.filter((p) => p.time.slice(0, 10) === day);

  return (
    <div className="absolute inset-x-4 bottom-4 z-10 rounded-lg border border-hair bg-panel/95 px-4 pb-3 pt-3 shadow-2xl backdrop-blur">
      <div className="flex items-center gap-4">
        <button aria-label={clock.paused ? "Lecture" : "Pause"}
          onClick={() => onCommand({ action: clock.paused ? "play" : "pause" })}
          className="flex h-9 w-9 items-center justify-center rounded-full bg-ink text-abyss hover:bg-white">
          {clock.paused ? <Play size={16} fill="currentColor" className="ml-0.5" /> : <Pause size={16} fill="currentColor" />}
        </button>
        <div className="min-w-[230px] font-cond text-[22px] font-medium leading-none tabular-nums">{shownTime}</div>
        <div className="flex rounded-md border border-hair p-0.5" role="group" aria-label="Vitesse du rejeu">
          {[1, 10, 60, 300].map((v) => (
            <button key={v} onClick={() => onCommand({ action: "speed", speed: v })}
              className={`rounded px-2.5 py-1 text-[12px] ${clock.speed === v ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
              × {v}
            </button>
          ))}
        </div>
        <div className="ml-auto">
          <select aria-label="Journée" value={day} onChange={(e) => onCommand({ action: "seek", time: `${e.target.value}T12:00:00Z` })}
            className="rounded-md border border-hair bg-abyss px-2 py-1 text-ink">
            {(days.includes(day) ? days : [day, ...days]).map((d) => <option key={d} value={d}>{dayLabel(d)}</option>)}
          </select>
        </div>
      </div>

      <div className="relative mt-3">
        {/* Repères des alertes et des passages satellite */}
        <div className="pointer-events-none absolute inset-x-0 top-0 h-7">
          {dayAlerts.features.map((a) => (
            <span key={a.properties.id} title={`${ALERT_LABEL[a.properties.type]}, ${utc(a.properties.event_time)}`}
              className="absolute top-1 h-3 w-0.5 -translate-x-1/2 rounded"
              style={{ left: `${at(a.properties.event_time)}%`, background: ALERT_COLOR[a.properties.type],
                       opacity: a.properties.severity === "faible" || a.properties.status === "classee" ? 0.45 : 1 }} />
          ))}
          {passesToday.map((p) => (
            <span key={p.id} className="absolute -top-0.5 h-7 w-px -translate-x-1/2 bg-signal" style={{ left: `${at(p.time)}%` }}>
              <span className="absolute -top-1 left-1 whitespace-nowrap text-[10.5px] text-signal">Sentinel 1</span>
            </span>
          ))}
        </div>
        <input type="range" className="scrub relative" min={0} max={DAY} step={60} value={pos}
          aria-label="Instant du rejeu"
          onChange={(e) => setDrag(Number(e.target.value))}
          onMouseUp={commit} onTouchEnd={commit} onKeyUp={commit} />
        <div className="flex justify-between text-[10.5px] text-faint">
          {["00:00", "06:00", "12:00", "18:00", "24:00"].map((h) => <span key={h}>{h}</span>)}
        </div>
      </div>
    </div>
  );
}
