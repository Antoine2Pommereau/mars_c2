import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { auteurMemorise, memoriserAuteur } from "../lib/auteur";
import { utc } from "../lib/format";
import { L } from "../lib/libelles";

const D = L.decisions;
const MOTIFS = Object.keys(D.motifs);

/** Décisions de l'opérateur sur une alerte : acquitter, confirmer, classer (motif obligatoire), rouvrir, commenter.
 *  Chaque décision est horodatée et signée (champ auteur, un seul opérateur pour l'instant). */
export default function AlertActions({ id, status, onStatus }: { id: number; status: string; onStatus: (s: string) => void }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [comment, setComment] = useState("");
  const [author, setAuthor] = useState(auteurMemorise);
  const [classing, setClassing] = useState(false);
  const history = useQuery({ queryKey: ["alertActions", id], queryFn: () => api.alertActions(id) });
  const act = useMutation({
    mutationFn: (b: { action: string; motif?: string; note?: string }) => api.act(id, { ...b, author }),
    onSuccess: (r, b) => {
      memoriserAuteur(author);
      if (b.action === "commenter") setComment(""); else setNote("");
      setClassing(false);
      onStatus(r.status);
      for (const k of ["alertActions", "alertsRange", "vessel"]) qc.invalidateQueries({ queryKey: [k] });
    },
  });

  const btn = "flex-1 rounded-md border py-1.5 text-[12.5px] disabled:opacity-40";
  return (
    <div>
      <div className="mb-2 text-[12px] text-muted">{D.statut(L.statut[status] ?? status)}</div>
      {status === "nouvelle" ? (
        <>
          <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder={D.note}
            className="w-full resize-none rounded-md border border-hair bg-abyss px-2.5 py-2 text-[12.5px] text-ink placeholder:text-faint focus:border-signal focus:outline-none" />
          <div className="mt-2 flex gap-2">
            <button disabled={act.isPending} onClick={() => act.mutate({ action: "acquitter", note })}
              className={`${btn} border-hair text-ink hover:border-muted`}>{D.acquitter}</button>
            <button disabled={act.isPending} onClick={() => act.mutate({ action: "confirmer", note })}
              className={`${btn} border-gap/60 text-gap hover:border-gap`}>{D.confirmer}</button>
            <button disabled={act.isPending} onClick={() => setClassing(!classing)} aria-expanded={classing}
              className={`${btn} ${classing ? "border-muted text-ink" : "border-hair text-muted hover:text-ink"}`}>{D.classer}</button>
          </div>
          {classing && (
            <div className="mt-2">
              <div className="mb-1 text-[12px] text-muted">{D.motifRequis}</div>
              <div className="flex gap-2">
                {MOTIFS.map((m) => (
                  <button key={m} disabled={act.isPending} onClick={() => act.mutate({ action: "classer", motif: m, note })}
                    className={`${btn} border-hair text-ink hover:border-muted`}>{D.motifs[m]}</button>
                ))}
              </div>
            </div>
          )}
        </>
      ) : (
        <button disabled={act.isPending} onClick={() => act.mutate({ action: "rouvrir", note })}
          className={`${btn} w-full border-hair text-ink hover:border-muted`}>{D.rouvrir}</button>
      )}

      <div className="mt-3 flex gap-2">
        <input value={comment} onChange={(e) => setComment(e.target.value)} placeholder={D.commentaire}
          className="min-w-0 flex-1 rounded-md border border-hair bg-abyss px-2.5 py-1.5 text-[12.5px] text-ink placeholder:text-faint focus:border-signal focus:outline-none" />
        <input value={author} onChange={(e) => setAuthor(e.target.value)} aria-label={D.auteur} title={D.auteur}
          className="w-24 rounded-md border border-hair bg-abyss px-2 py-1.5 text-[12px] text-muted focus:border-signal focus:outline-none" />
        <button disabled={act.isPending || !comment.trim()} onClick={() => act.mutate({ action: "commenter", note: comment })}
          className="rounded-md border border-hair px-2.5 text-[12.5px] text-ink hover:border-muted disabled:opacity-40">{D.commenter}</button>
      </div>
      {act.isError && <p className="mt-2 text-[12px] text-gap">{(act.error as Error).message}</p>}

      {(history.data ?? []).length > 0 && (
        <ul className="mt-3 space-y-2">
          {history.data!.map((h, i) => (
            <li key={i} className="text-[12px]">
              <span className="text-ink">{D.verbe[h.action] ?? h.action}</span>
              {h.motif && <span className="text-ink"> ({D.motifs[h.motif] ?? h.motif})</span>}
              <span className="text-muted">{D.par(h.author, utc(h.at).slice(0, 16))}</span>
              {h.note && <span className="mt-0.5 block text-ink/80">{h.note}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
