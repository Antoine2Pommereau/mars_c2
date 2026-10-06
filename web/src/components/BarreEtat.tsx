import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { L } from "../lib/libelles";
import { COULEUR_NIVEAU, INDICATEURS } from "../registres/etat";

interface Props { now: number; connected: boolean; onSearch: () => void }

/** Barre d'état : la plateforme voit elle bien ? Chaque indicateur est vert, orange ou rouge ; son détail s'ouvre au
 *  clic. Lecture de /api/ingestion toutes les 30 secondes. */
export default function BarreEtat({ now, connected, onSearch }: Props) {
  const q = useQuery({ queryKey: ["ingestion"], queryFn: api.ingestion, refetchInterval: 30_000 });
  const [open, setOpen] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(null); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, []);
  const status = q.isError ? null : q.data;

  return (
    <header ref={ref} className="relative z-20 flex h-9 shrink-0 items-center gap-1 border-b border-hair bg-panel px-3 text-[12px]">
      <span className="mr-3 font-cond text-[13px] font-semibold tracking-wide text-ink">{L.app.nom}</span>
      {INDICATEURS.map((ind) => {
        const aVenir = ind.etape != null;
        const m = aVenir ? null : ind.evaluer(status, now);
        const niveau = m?.niveau ?? "gris";
        return (
          <div key={ind.id} className="relative">
            <button disabled={aVenir} onClick={() => setOpen(open === ind.id ? null : ind.id)}
              title={aVenir ? `${ind.libelle}, ${L.commun.etape(ind.etape!)}` : `${ind.libelle} : ${L.etat.niveau[niveau]}`}
              className={`flex items-center gap-1.5 rounded px-2 py-1 ${aVenir ? "text-faint/60" : open === ind.id ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
              <span className="h-1.5 w-1.5 rounded-full" style={{ background: COULEUR_NIVEAU[niveau] }} />
              {ind.libelle}
              {m?.resume && <span className="tabular-nums text-faint">{m.resume}</span>}
            </button>
            {open === ind.id && m && (
              <div className="absolute left-0 top-8 w-72 rounded-md border border-hair bg-panel p-3 shadow-2xl">
                <div className="mb-2 flex items-center justify-between">
                  <span className="font-semibold text-ink">{ind.libelle}</span>
                  <span style={{ color: COULEUR_NIVEAU[niveau] }}>{L.etat.niveau[niveau]}</span>
                </div>
                {m.detail.map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-3 border-t border-hair/70 py-1">
                    <span className="text-muted">{k}</span><span className="text-right text-ink">{v}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}
      {!connected && <span className="ml-2 text-gap">{L.commun.reconnexion}</span>}
      <button onClick={onSearch} className="ml-auto flex items-center gap-2 rounded-md border border-hair px-2.5 py-1 text-muted hover:text-ink">
        <Search size={13} /> {L.recherche.titre} <span className="text-faint">{L.recherche.raccourci}</span>
      </button>
    </header>
  );
}
