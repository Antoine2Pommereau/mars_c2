import type { Feature } from "../lib/types";
import { num, utc } from "../lib/format";
import { L } from "../lib/libelles";

interface Props {
  history: Feature[];
  onPick: (f: Feature) => void;
  analysis: Feature | null;
  nDetections: number;
  titre?: boolean;
}

export default function AnalysesPanel({ history, onPick, analysis, nDetections, titre = true }: Props) {
  const s = analysis?.properties.summary ?? {};
  const t = analysis?.properties.timings ?? {};
  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {titre && <header className="border-b border-hair px-4 pb-3 pt-4">
        <h2 className="text-[15px] font-semibold">{L.analyses.titre}</h2>
      </header>}
      {analysis ? (
        <section className="px-4 py-4">
          <div className="text-[15px] font-medium">{L.analyses.passageDu(utc(analysis.properties.acquired_at))}</div>
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3">
            {[
              [L.analyses.detections, nDetections],
              [L.analyses.retenues, s.retenues],
              [L.analyses.appariees, s.appariees],
              [L.analyses.sombres, s.alertes],
              [L.analyses.nonConfirmees, s.positions_non_confirmees],
              [L.analyses.echosFixes, s.echos_fixes],
            ].map(([label, v]) => (
              <div key={label as string}>
                <dt className="text-[12px] text-muted">{label}</dt>
                <dd className="font-cond text-[22px] font-medium leading-tight">{v ?? L.commun.nd}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-4 text-[12.5px] text-muted">{L.analyses.traiteeEn(num(t.total_s))}</p>
        </section>
      ) : (
        <p className="px-4 py-6 text-muted">{L.analyses.aucune}</p>
      )}
      {history.length > 1 && (
        <section className="border-t border-hair px-4 py-4">
          <div className="mb-2 font-medium">{L.analyses.precedentes}</div>
          {history.map((f) => {
            const on = f.properties.id === analysis?.properties.id;
            return (
              <button key={f.properties.id} onClick={() => onPick(f)}
                className={`flex w-full justify-between rounded-md px-2 py-1.5 text-left text-[12.5px]
                  ${on ? "bg-raised text-ink" : "text-muted hover:bg-raised/60 hover:text-ink"}`}>
                <span>{L.analyses.ligne(f.properties.id, utc(f.properties.acquired_at).slice(0, 10))}</span>
                <span>{L.analyses.nAlertes(f.properties.summary?.alertes ?? 0)}</span>
              </button>
            );
          })}
        </section>
      )}
    </div>
  );
}
