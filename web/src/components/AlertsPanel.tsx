import { Check, ChevronDown } from "lucide-react";
import { useMemo, useState } from "react";
import type { AlertProps, AlertType, FC, Feature } from "../lib/types";
import { ALERT_COLOR, ALERT_LABEL, SEVERITY, STATUS_LABEL, hm, num } from "../lib/format";

type Alert = Feature<AlertProps>;
const TYPES: AlertType[] = ["DARK_SHIP", "RENDEZVOUS", "AIS_GAP", "AIS_UNCONFIRMED"];
const RANK: Record<string, number> = { critique: 0, elevee: 1, moyenne: 2, faible: 3 };

function headline(a: Alert): string {
  const p = a.properties;
  switch (p.type) {
    case "DARK_SHIP": return `Écho de ${num(p.details?.length_m, 0)} m sans AIS`;
    case "AIS_UNCONFIRMED": return p.details?.navire?.name ?? `MMSI ${p.details?.navire?.mmsi}`;
    case "RENDEZVOUS": {
      const [v1, v2] = p.details?.navires ?? [];
      return `${v1?.name ?? v1?.mmsi} et ${v2?.name ?? v2?.mmsi}`;
    }
    case "AIS_GAP": return p.details?.navire?.name ?? `MMSI ${p.details?.navire?.mmsi}`;
    default: return "";
  }
}

function detail(a: Alert): string {
  const p = a.properties;
  switch (p.type) {
    case "DARK_SHIP": return `contraste ${num(p.details?.contrast_vv_db, 0)} dB`;
    case "AIS_UNCONFIRMED": return `${num(p.details?.navire?.length_m, 0)} m déclarés, aucun écho`;
    case "RENDEZVOUS": return `${p.details?.duree_min} min, ${p.details?.distance_min_m} m au plus près`;
    case "AIS_GAP": return `silence de ${p.details?.duree_min} min`;
    default: return "";
  }
}

interface Props {
  bounds: number[] | null;
  now: string | null;
  analysisAlerts: FC<AlertProps>;
  liveAlerts: FC<AlertProps>;
  selectedId: number | null;
  onPick: (f: Alert) => void;
}

const PERIODS: [number, string][] = [[1, "1 h"], [6, "6 h"], [12, "12 h"]];

export default function AlertsPanel({ bounds, now, analysisAlerts, liveAlerts, selectedId, onPick }: Props) {
  const [inView, setInView] = useState(false);
  const [hours, setHours] = useState(12);
  const [types, setTypes] = useState<AlertType[]>(TYPES);
  const [view, setView] = useState<"todo" | "confirmed" | "all">("todo");

  const all = useMemo(() => {
    const list = [...analysisAlerts.features, ...liveAlerts.features];
    return list.sort((a, b) =>
      (RANK[a.properties.severity] ?? 9) - (RANK[b.properties.severity] ?? 9) ||
      String(b.properties.event_time).localeCompare(String(a.properties.event_time)));
  }, [analysisAlerts, liveAlerts]);

  const statusOf = (a: Alert) => a.properties.status ?? "nouvelle";

  // La liste affichée ne se recalcule que lorsque ses vraies entrées changent (pas à chaque rendu)
  const shown = useMemo(() => {
    const since = now ? new Date(now).getTime() - hours * 3600_000 : 0;
    const visible = (a: Alert) => {
      if (!inView || !bounds) return true;
      const [lon, lat] = a.geometry.coordinates;
      return lon >= bounds[0] && lon <= bounds[2] && lat >= bounds[1] && lat <= bounds[3];
    };
    const recent = (a: Alert) => !["RENDEZVOUS", "AIS_GAP"].includes(a.properties.type)
      || new Date(a.properties.event_time ?? 0).getTime() >= since;
    return all.filter((a) => types.includes(a.properties.type) && visible(a) && recent(a) &&
      (view === "all" || (view === "todo" ? statusOf(a) === "nouvelle" : statusOf(a) === "confirmee")));
  }, [all, types, inView, bounds, now, hours, view]);

  const todoCount = all.filter((a) => statusOf(a) === "nouvelle").length;

  const count = (t: AlertType) => all.filter((a) => a.properties.type === t).length;
  // Seuls les types qui ont au moins une alerte apparaissent dans le menu de filtre
  const visibleTypes = TYPES.filter((t) => count(t) > 0);
  const activeTypes = visibleTypes.filter((t) => types.includes(t)).length;

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-hair px-4 pb-3 pt-4">
        <h2 className="flex items-baseline justify-between text-[15px] font-semibold">
          Alertes <span className="text-[12px] font-normal text-muted">{shown.length} sur {all.length}</span>
        </h2>
        <div className="mt-3 flex items-center gap-2 text-[12px]">
          {/* Filtre par type : menu repliable, les types sans alerte sont masqués */}
          <details className="relative">
            <summary className="flex cursor-pointer list-none items-center gap-1 rounded-md border border-hair px-2 py-1 text-muted hover:text-ink [&::-webkit-details-marker]:hidden">
              Type{activeTypes < visibleTypes.length ? <span className="text-ink">{` ${activeTypes}`}</span> : null}
              <ChevronDown size={12} />
            </summary>
            <div className="absolute left-0 top-full z-20 mt-1 w-56 rounded-md border border-hair bg-raised p-1 shadow-xl">
              {visibleTypes.length === 0 && <div className="px-2 py-1.5 text-faint">Aucune alerte</div>}
              {visibleTypes.map((t) => {
                const on = types.includes(t);
                return (
                  <button key={t} onClick={() => setTypes(on ? types.filter((x) => x !== t) : [...types, t])}
                    className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-panel">
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: on ? ALERT_COLOR[t] : "#4c5a66" }} />
                    <span className={`flex-1 ${on ? "text-ink" : "text-faint"}`}>{ALERT_LABEL[t]}</span>
                    <span className="text-muted">{count(t)}</span>
                    {on && <Check size={13} className="text-signal" />}
                  </button>
                );
              })}
            </div>
          </details>
          <div className="flex rounded-md border border-hair p-0.5" aria-label="Période des alertes comportementales">
            {PERIODS.map(([h, l]) => (
              <button key={h} onClick={() => setHours(h)}
                className={`rounded px-2 py-0.5 ${hours === h ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>{l}</button>
            ))}
          </div>
          <button onClick={() => setInView(!inView)} title="Limiter à la zone affichée sur la carte"
            className={`rounded-md border px-2 py-1 transition-colors
              ${inView ? "border-signal text-ink" : "border-hair text-muted hover:text-ink"}`}>
            Zone
          </button>
        </div>
        <div className="mt-2 flex rounded-md border border-hair p-0.5 text-[12px]" role="tablist">
          {([["todo", `À traiter ${todoCount}`], ["confirmed", "Confirmées"], ["all", "Toutes"]] as const).map(([k, l]) => (
            <button key={k} role="tab" aria-selected={view === k} onClick={() => setView(k)}
              className={`flex-1 rounded px-2 py-1 ${view === k ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>{l}</button>
          ))}
        </div>
      </header>

      <ul className="flex-1 overflow-y-auto">
        {shown.length === 0 && (
          <li className="px-4 py-6 text-muted">
            {view === "todo" ? "Aucune alerte à traiter à cet instant." : "Aucune alerte."}
          </li>
        )}
        {shown.map((a) => {
          const low = a.properties.severity === "faible" || ["classee", "acquittee"].includes(statusOf(a));
          const selected = a.properties.id === selectedId;
          return (
            <li key={`${a.properties.type}${a.properties.id}`}>
              <button onClick={() => onPick(a)}
                className={`grid w-full grid-cols-[10px_1fr_auto] items-start gap-x-3 border-b border-hair/70 px-4 py-3 text-left transition-colors
                  ${selected ? "bg-raised" : "hover:bg-raised/60"} ${low ? "opacity-55" : ""}`}>
                <span className="mt-1.5 h-2 w-2 rounded-full" style={{ background: ALERT_COLOR[a.properties.type] }} />
                <span className="min-w-0">
                  <span className="block truncate font-medium text-ink">{headline(a)}</span>
                  <span className="block truncate text-[12px] text-muted">{detail(a)}</span>
                </span>
                <span className="text-right text-[12px]">
                  <span className="block text-ink/80">{hm(a.properties.event_time)}</span>
                  <span className="block text-muted">
                    {statusOf(a) === "nouvelle" ? SEVERITY[a.properties.severity] : STATUS_LABEL[statusOf(a)]}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
