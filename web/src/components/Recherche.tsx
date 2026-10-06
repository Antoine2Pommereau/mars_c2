import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../lib/api";
import { jourHeure } from "../lib/format";
import { L } from "../lib/libelles";
import { chercherLieux, type Lieu } from "../lib/lieux";
import type { Props } from "../lib/types";
import { COULEUR_LISTE, couleurAlerte, libelleAlerte } from "../registres/alertes";
import { Tag } from "./Elements";

export type Resultat =
  | { kind: "navire"; p: Props }
  | { kind: "infrastructure"; p: Props }
  | { kind: "alerte"; p: Props }
  | { kind: "lieu"; p: Lieu };

const R = L.recherche;

/** Recherche globale (Cmd + K) : navires par nom actuel ou ancien, MMSI ou OMI ; infrastructures par nom ; alertes par
 *  numéro ; lieux. Résultats groupés ; flèches et Entrée au clavier ; un résultat ouvre sa fiche et centre la carte. */
export default function Recherche({ onClose, onPick }: { onClose: () => void; onPick: (r: Resultat) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [cursor, setCursor] = useState(0);
  useEffect(() => input.current?.focus(), []);
  useEffect(() => { const id = setTimeout(() => setDebounced(q.trim()), 200); return () => clearTimeout(id); }, [q]);
  const ok = debounced.length >= 2 || /^\d+$/.test(debounced);
  const res = useQuery({ queryKey: ["search", debounced], queryFn: () => api.search(debounced), enabled: ok, staleTime: 30_000 });

  const groupes = useMemo(() => {
    const d = res.data;
    const g: [string, Resultat[]][] = [
      ["navires", (d?.navires ?? []).map((p) => ({ kind: "navire" as const, p }))],
      ["infrastructures", (d?.infrastructures ?? []).map((p) => ({ kind: "infrastructure" as const, p }))],
      ["alertes", (d?.alertes ?? []).map((p) => ({ kind: "alerte" as const, p }))],
      ["lieux", chercherLieux(debounced, (z) => L.zones[z] ?? z).map((p) => ({ kind: "lieu" as const, p }))],
    ];
    return g.filter(([, r]) => r.length);
  }, [res.data, debounced]);
  const flat = groupes.flatMap(([, r]) => r);
  useEffect(() => setCursor(0), [debounced]);

  function key(e: React.KeyboardEvent) {
    if (e.key === "Escape") onClose();
    else if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, flat.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
    else if (e.key === "Enter" && flat[cursor]) onPick(flat[cursor]);
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-abyss/60 pt-[12vh]" onMouseDown={onClose}>
      <div role="dialog" aria-label={R.titre} onMouseDown={(e) => e.stopPropagation()}
        className="w-[600px] max-w-[92vw] rounded-lg border border-hair bg-panel shadow-2xl">
        <label className="flex items-center gap-3 border-b border-hair px-4 py-3">
          <Search size={16} className="text-muted" />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder={R.placeholder} onKeyDown={key}
            className="flex-1 bg-transparent text-[14px] text-ink placeholder:text-faint focus:outline-none" />
        </label>
        <div className="max-h-[56vh] overflow-y-auto py-1 text-[12.5px]">
          {!ok && <p className="px-4 py-3 text-muted">{R.saisir}</p>}
          {ok && !res.isLoading && !flat.length && <p className="px-4 py-3 text-muted">{R.aucun}</p>}
          {groupes.map(([g, items]) => (
            <div key={g}>
              <div className="px-4 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-faint">{R.groupes[g]}</div>
              {items.map((r) => {
                const i = flat.indexOf(r);
                return (
                  <button key={`${r.kind}${i}`} onMouseEnter={() => setCursor(i)} onClick={() => onPick(r)}
                    className={`flex w-full items-center gap-2 px-4 py-1.5 text-left ${i === cursor ? "bg-raised" : ""}`}>
                    <Ligne r={r} />
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <div className="border-t border-hair px-4 py-1.5 text-[11px] text-faint">{R.aide}</div>
      </div>
    </div>
  );
}

function Ligne({ r }: { r: Resultat }) {
  if (r.kind === "navire") {
    const p = r.p;
    return (
      <>
        <span className="text-ink">{p.name ?? `MMSI ${p.mmsi}`}</span>
        {p.flag && <Tag>{p.flag}</Tag>}
        {p.watch && <Tag color={COULEUR_LISTE}>{L.signal[p.watch]}</Tag>}
        <span className="ml-auto text-muted">
          {p.ancien ? R.ancienNom(p.ancien) : `MMSI ${p.mmsi}${p.imo ? `, OMI ${p.imo}` : ""}`}{p.lon == null ? `, ${R.silencieux}` : ""}
        </span>
      </>
    );
  }
  if (r.kind === "infrastructure") {
    return <><span className="text-ink">{r.p.name}</span><span className="ml-auto text-muted">{r.p.type}, {L.zones[(r.p.region ?? "").toLowerCase()] ?? r.p.region}</span></>;
  }
  if (r.kind === "alerte") {
    return (
      <>
        <span className="h-2 w-2 rounded-full" style={{ background: couleurAlerte(r.p.type) }} />
        <span className="text-ink">{R.alerte(r.p.id)}</span>
        <span className="ml-auto text-muted">{libelleAlerte(r.p.type)}, {jourHeure(r.p.event_time)}</span>
      </>
    );
  }
  return <span className="text-ink">{r.p.zone ? L.zones[r.p.zone] : r.p.nom}</span>;
}
