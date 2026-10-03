import { Check, Loader2, X } from "lucide-react";
import type { ReactNode } from "react";
import type { ActiveAnalysis, Feature } from "../lib/types";
import { num, utc } from "../lib/format";

const STEPS: [string, string][] = [["extraction", "Extrait radar"], ["inference", "Détection"], ["fusion", "Fusion avec l'AIS"]];

function Job({ a }: { a: ActiveAnalysis }) {
  const steps: Record<string, any> = {};
  (a.progress ?? []).forEach((s) => (steps[s.step] = s));
  return (
    <div className="border-b border-hair px-4 py-3">
      <div className="mb-2 font-medium">
        Analyse n° {a.id} {a.status === "failed" ? "en échec" : a.status === "done" ? "terminée" : "en cours"}
      </div>
      {STEPS.map(([k, label]) => {
        const s = steps[k];
        const Icon = !s ? null : s.state === "done" ? Check : Loader2;
        return (
          <div key={k} className="flex items-center justify-between py-0.5 text-[12.5px]">
            <span className={`flex items-center gap-2 ${s ? "text-ink" : "text-faint"}`}>
              <span className="flex w-4 justify-center">
                {Icon && <Icon size={13} className={s.state === "done" ? "text-signal" : "animate-spin text-muted"} />}
              </span>
              {label}
            </span>
            <span className="text-muted">{s?.state === "done" ? `${num(s.seconds)} s` : s ? "en cours" : ""}</span>
          </div>
        );
      })}
      {a.error && <p className="mt-2 flex gap-2 text-[12.5px] text-gap"><X size={14} className="mt-0.5 shrink-0" />{a.error}</p>}
    </div>
  );
}

interface Props {
  history: Feature[];
  onPick: (f: Feature) => void;
  launcher: ReactNode;
  jobs: ActiveAnalysis[];
  analysis: Feature | null;
  nDetections: number;
}

export default function AnalysesPanel({ history, onPick, launcher, jobs, analysis, nDetections }: Props) {
  const s = analysis?.properties.summary ?? {};
  const t = analysis?.properties.timings ?? {};
  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <header className="border-b border-hair px-4 pb-3 pt-4">
        <h2 className="text-[15px] font-semibold">Analyses radar</h2>
      </header>
      {launcher}
      {jobs.map((a) => <Job key={a.id} a={a} />)}
      {analysis ? (
        <section className="px-4 py-4">
          <div className="text-[15px] font-medium">Passage du {utc(analysis.properties.acquired_at)}</div>
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3">
            {[
              ["Détections", nDetections],
              ["Retenues après filtres", s.retenues],
              ["Appariées à l'AIS", s.appariees],
              ["Navires sombres", s.alertes],
              ["Positions non confirmées", s.positions_non_confirmees],
              ["Échos fixes reconnus", s.echos_fixes],
            ].map(([label, v]) => (
              <div key={label as string}>
                <dt className="text-[12px] text-muted">{label}</dt>
                <dd className="font-cond text-[22px] font-medium leading-tight">{v ?? "n.d."}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-4 text-[12.5px] text-muted">Traitée en {num(t.total_s)} s</p>
        </section>
      ) : (
        <p className="px-4 py-6 text-muted">Aucune analyse.</p>
      )}
      {history.length > 1 && (
        <section className="border-t border-hair px-4 py-4">
          <div className="mb-2 font-medium">Analyses précédentes</div>
          {history.map((f) => {
            const on = f.properties.id === analysis?.properties.id;
            return (
              <button key={f.properties.id} onClick={() => onPick(f)}
                className={`flex w-full justify-between rounded-md px-2 py-1.5 text-left text-[12.5px]
                  ${on ? "bg-raised text-ink" : "text-muted hover:bg-raised/60 hover:text-ink"}`}>
                <span>n° {f.properties.id}, {utc(f.properties.acquired_at).slice(0, 10)}</span>
                <span>{f.properties.summary?.alertes ?? 0} alerte{(f.properties.summary?.alertes ?? 0) > 1 ? "s" : ""}</span>
              </button>
            );
          })}
        </section>
      )}
    </div>
  );
}
