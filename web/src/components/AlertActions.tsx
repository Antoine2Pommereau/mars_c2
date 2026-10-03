import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { STATUS_LABEL, utc } from "../lib/format";

const ACTIONS: { id: string; label: string; to: string; tone: string }[] = [
  { id: "acquitter", label: "Acquitter", to: "acquittee", tone: "border-hair text-ink hover:border-muted" },
  { id: "confirmer", label: "Confirmer", to: "confirmee", tone: "border-gap/60 text-gap hover:border-gap" },
  { id: "classer", label: "Classer", to: "classee", tone: "border-hair text-muted hover:text-ink" },
];
const VERB: Record<string, string> = { acquitter: "Acquittée", confirmer: "Confirmée", classer: "Classée", rouvrir: "Rouverte" };

/** Décisions de l'opérateur sur une alerte : acquitter, confirmer, classer, rouvrir, avec une note. */
export default function AlertActions({ id, status, onStatus }: { id: number; status: string; onStatus: (s: string) => void }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const history = useQuery({ queryKey: ["alertActions", id], queryFn: () => api.alertActions(id) });
  const act = useMutation({
    mutationFn: (action: string) => api.act(id, action, note),
    onSuccess: (r) => {
      setNote("");
      onStatus(r.status);
      qc.invalidateQueries({ queryKey: ["alertActions", id] });
      for (const k of ["alerts", "dayAlerts"]) qc.invalidateQueries({ queryKey: [k] });
    },
  });

  return (
    <section className="mt-4 border-t border-hair pt-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium">Décision</span>
        <span className="text-[12px] text-muted">Statut : {STATUS_LABEL[status] ?? status}</span>
      </div>
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="Note (facultative)"
        className="w-full resize-none rounded-md border border-hair bg-abyss px-2.5 py-2 text-[12.5px] text-ink placeholder:text-faint focus:border-signal focus:outline-none" />
      <div className="mt-2 flex gap-2">
        {status === "nouvelle" ? ACTIONS.map((a) => (
          <button key={a.id} disabled={act.isPending} onClick={() => act.mutate(a.id)}
            className={`flex-1 rounded-md border py-1.5 text-[12.5px] disabled:opacity-40 ${a.tone}`}>{a.label}</button>
        )) : (
          <button disabled={act.isPending} onClick={() => act.mutate("rouvrir")}
            className="flex-1 rounded-md border border-hair py-1.5 text-[12.5px] text-ink hover:border-muted disabled:opacity-40">
            Rouvrir l'alerte
          </button>
        )}
      </div>
      {act.isError && <p className="mt-2 text-[12px] text-gap">{(act.error as Error).message}</p>}
      {(history.data ?? []).length > 0 && (
        <ul className="mt-3 space-y-2">
          {history.data!.map((h) => (
            <li key={`${h.at}-${h.action}`} className="text-[12px]">
              <span className="text-ink">{VERB[h.action] ?? h.action}</span>
              <span className="text-muted"> par {h.author}, le {utc(h.at).slice(0, 16)}</span>
              {h.note && <span className="mt-0.5 block text-ink/80">{h.note}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
