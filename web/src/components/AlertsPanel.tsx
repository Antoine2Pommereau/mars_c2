import { useMemo, useState } from "react";
import type { FC, Feature } from "../lib/types";
import { ALERT_COLOR, ALERT_LABEL, SEVERITY, hm, num } from "../lib/format";

const TYPES = ["DARK_SHIP", "RENDEZVOUS", "AIS_GAP", "AIS_UNCONFIRMED"];
const RANK: Record<string, number> = { critique: 0, elevee: 1, moyenne: 2, faible: 3 };

function headline(a: Feature): string {
  const d = a.properties.details ?? {};
  switch (a.properties.type) {
    case "DARK_SHIP": return `Écho de ${num(d.length_m, 0)} m sans AIS`;
    case "AIS_UNCONFIRMED": return d.navire?.name ?? `MMSI ${d.navire?.mmsi}`;
    case "RENDEZVOUS": {
      const [v1, v2] = d.navires ?? [];
      return `${v1?.name ?? v1?.mmsi} et ${v2?.name ?? v2?.mmsi}`;
    }
    case "AIS_GAP": return d.navire?.name ?? `MMSI ${d.navire?.mmsi}`;
    default: return "";
  }
}

function detail(a: Feature): string {
  const d = a.properties.details ?? {};
  switch (a.properties.type) {
    case "DARK_SHIP": return `contraste ${num(d.contrast_vv_db, 0)} dB`;
    case "AIS_UNCONFIRMED": return `${num(d.navire?.length_m, 0)} m déclarés, aucun écho`;
    case "RENDEZVOUS": return `${d.duree_min} min, ${d.distance_min_m} m au plus près`;
    case "AIS_GAP": return `silence de ${d.duree_min} min`;
    default: return "";
  }
}

interface Props {
  analysisAlerts: FC;
  liveAlerts: FC;
  selectedId: number | null;
  onPick: (f: Feature) => void;
}

export default function AlertsPanel({ analysisAlerts, liveAlerts, selectedId, onPick }: Props) {
  const [types, setTypes] = useState<string[]>(TYPES);
  const [hideLow, setHideLow] = useState(false);

  const all = useMemo(() => {
    const list = [...analysisAlerts.features, ...liveAlerts.features];
    return list.sort((a, b) =>
      (RANK[a.properties.severity] ?? 9) - (RANK[b.properties.severity] ?? 9) ||
      String(b.properties.event_time).localeCompare(String(a.properties.event_time)));
  }, [analysisAlerts, liveAlerts]);

  const shown = all.filter((a) => types.includes(a.properties.type) &&
    !(hideLow && (a.properties.severity === "faible" || a.properties.status === "classee")));

  const count = (t: string) => all.filter((a) => a.properties.type === t).length;

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-hair px-4 pb-3 pt-4">
        <h2 className="flex items-baseline justify-between text-[15px] font-semibold">
          Alertes <span className="text-[12px] font-normal text-muted">{shown.length} sur {all.length}</span>
        </h2>
        <div className="mt-3 flex flex-wrap gap-1.5">
          {TYPES.map((t) => {
            const on = types.includes(t);
            return (
              <button key={t} onClick={() => setTypes(on ? types.filter((x) => x !== t) : [...types, t])}
                className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[12px] transition-colors
                  ${on ? "border-hair bg-raised text-ink" : "border-transparent text-faint"}`}>
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: on ? ALERT_COLOR[t] : "#4c5a66" }} />
                {ALERT_LABEL[t]} <span className="text-muted">{count(t)}</span>
              </button>
            );
          })}
        </div>
        <label className="mt-3 flex cursor-pointer items-center gap-2 text-[12px] text-muted">
          <input type="checkbox" checked={hideLow} onChange={(e) => setHideLow(e.target.checked)} />
          Masquer faibles et classées
        </label>
      </header>

      <ul className="flex-1 overflow-y-auto">
        {shown.length === 0 && (
          <li className="px-4 py-6 text-muted">
            Aucune alerte à cet instant.
          </li>
        )}
        {shown.map((a) => {
          const low = a.properties.severity === "faible" || a.properties.status === "classee";
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
                    {a.properties.status === "classee" ? "classée" : SEVERITY[a.properties.severity]}
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
