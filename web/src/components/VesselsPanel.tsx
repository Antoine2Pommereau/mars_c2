import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../lib/api";
import { num } from "../lib/format";

export default function VesselsPanel({ onOpen }: { onOpen: (vesselId: number) => void }) {
  const [q, setQ] = useState("");
  const ready = q.trim().length >= 2;
  const vq = useQuery({ queryKey: ["vsearch", q.trim()], queryFn: (ctx) => api.searchVessels(q.trim(), ctx), enabled: ready });
  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-hair px-4 pb-3 pt-4">
        <h2 className="text-[15px] font-semibold">Navires</h2>
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="MMSI ou nom"
          className="mt-3 w-full rounded-md border border-hair bg-abyss px-2.5 py-1.5 text-ink placeholder:text-faint" />
      </header>
      <ul className="flex-1 overflow-y-auto">
        {!ready && <li className="px-4 py-6 text-muted">Saisir au moins deux caractères.</li>}
        {ready && vq.data?.length === 0 && <li className="px-4 py-6 text-muted">Aucun navire.</li>}
        {vq.data?.map((v) => (
          <li key={v.vessel_id}>
            <button onClick={() => onOpen(v.vessel_id)}
              className="w-full border-b border-hair/70 px-4 py-3 text-left transition-colors hover:bg-raised/60">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate font-medium text-ink">{v.name ?? "Sans nom"}</span>
                {v.n_alertes > 0 && (
                  <span className="shrink-0 rounded-full bg-raised px-2 py-0.5 text-[11px] text-muted">
                    {v.n_alertes} alerte{v.n_alertes > 1 ? "s" : ""}
                  </span>
                )}
              </div>
              <div className="mt-0.5 truncate text-[12px] text-muted">
                MMSI {v.mmsi}
                {v.ship_type && v.ship_type !== "Undefined" ? ` · ${v.ship_type}` : ""}
                {v.length_m ? ` · ${num(v.length_m, 0)} m` : ""}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
