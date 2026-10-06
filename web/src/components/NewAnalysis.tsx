import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, type Pass } from "../lib/api";
import { MAX_ZONE_KM, bboxSizeKm } from "../lib/geo";
import { num, utc } from "../lib/format";
import { L } from "../lib/libelles";

interface Props {
  drawing: boolean;
  draft: number[] | null;
  onStartDraw: () => void;
  onCancel: () => void;
  onLaunched: (id: number, pass: Pass) => void;
}

/** Nouvelle analyse : tracer une zone, choisir un passage Sentinel 1, lancer. */
export default function NewAnalysis({ drawing, draft, onStartDraw, onCancel, onLaunched }: Props) {
  const [chosen, setChosen] = useState<string | null>(null);
  const size = draft ? bboxSizeKm(draft) : null;
  const tooBig = !!size && Math.max(size.w, size.h) > MAX_ZONE_KM;

  const passesQ = useQuery({
    queryKey: ["passes", draft?.map((v) => v.toFixed(3)).join(",")],
    queryFn: () => api.passes(draft!),
    enabled: !!draft && !tooBig,
    staleTime: 5 * 60_000,
  });

  // Par défaut, le passage avec AIS le mieux couvert
  useEffect(() => {
    const ok = (passesQ.data ?? []).filter((p) => p.ais_available);
    ok.sort((a, b) => (b.coverage ?? 0) - (a.coverage ?? 0));
    setChosen(ok[0]?.product_name ?? null);
  }, [passesQ.data]);

  const launch = useMutation({
    mutationFn: () => api.launch(draft!, chosen!),
    onSuccess: (r) => onLaunched(r.id, passesQ.data!.find((p) => p.product_name === chosen)!),
  });

  if (!drawing && !draft) {
    return (
      <div className="border-b border-hair px-4 py-3">
        <button onClick={onStartDraw}
          className="w-full rounded-md bg-ink py-2 font-medium text-abyss hover:bg-white">{L.analyses.nouvelle}</button>
      </div>
    );
  }

  return (
    <div className="border-b border-hair px-4 py-3">
      {drawing && !draft && (
        <p className="text-ink">{L.analyses.tracer}</p>
      )}
      {size && (
        <div className="flex items-baseline justify-between">
          <span className="font-medium">{L.analyses.zoneDe(num(size.w, 0), num(size.h, 0))}</span>
          <button onClick={onStartDraw} className="text-[12px] text-muted hover:text-ink">{L.analyses.retracer}</button>
        </div>
      )}
      {tooBig && <p className="mt-1 text-[12.5px] text-gap">{L.analyses.tropGrande(MAX_ZONE_KM)}</p>}

      {draft && !tooBig && (
        <div className="mt-3">
          {passesQ.isLoading && <p className="text-muted">{L.analyses.recherche}</p>}
          {passesQ.isError && <p className="text-gap">{L.analyses.impossible((passesQ.error as Error).message)}</p>}
          {passesQ.data && passesQ.data.length === 0 && <p className="text-muted">{L.analyses.aucunPassage}</p>}
          <ul className="space-y-1">
            {(passesQ.data ?? []).map((p) => {
              const on = chosen === p.product_name;
              return (
                <li key={p.product_name}>
                  <button disabled={!p.ais_available} onClick={() => setChosen(p.product_name)}
                    title={p.ais_available ? "" : L.analyses.sansAisJournee}
                    className={`flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-[12.5px]
                      ${on ? "border-signal bg-raised" : "border-hair hover:bg-raised/60"} disabled:cursor-not-allowed disabled:opacity-35`}>
                    <span>
                      <span className="block text-ink">{utc(p.acquired_at).slice(0, 16)}</span>
                      <span className="block text-muted">
                        {L.analyses.orbite[p.orbit_direction ?? ""] ?? L.analyses.orbiteInconnue}, {p.ais_available ? L.analyses.aisCharge : L.analyses.sansAis}
                      </span>
                    </span>
                    <span className="text-muted">{p.coverage == null ? "" : `${num(p.coverage * 100, 0)} %`}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {launch.isError && <p className="mt-2 text-[12.5px] text-gap">{(launch.error as Error).message}</p>}
      <div className="mt-3 flex gap-2">
        <button onClick={onCancel} className="flex-1 rounded-md border border-hair py-2 text-muted hover:text-ink">{L.commun.annuler}</button>
        <button disabled={!draft || tooBig || !chosen || launch.isPending} onClick={() => launch.mutate()}
          className="flex-1 rounded-md bg-ink py-2 font-medium text-abyss hover:bg-white disabled:opacity-35">
          {launch.isPending ? L.analyses.lancement : L.analyses.lancer}
        </button>
      </div>
    </div>
  );
}
