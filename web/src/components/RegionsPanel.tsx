import { useState } from "react";
import type { Region } from "../lib/api";

const STATUS_LABEL: Record<string, string> = {
  prete: "prête", en_cours: "en cours", absente: "à provisionner", echec: "échec",
};
const STATUS_COLOR: Record<string, string> = {
  prete: "#4fb6c8", en_cours: "#f0a84b", absente: "#7c8b97", echec: "#ef6461",
};

function statusOf(r: Region, layer: string) {
  return r.layers.find((l) => l.layer === layer)?.status ?? "absente";
}

interface Props {
  regions: Region[];
  activeId?: number;
  drawing: boolean;
  draft: number[] | null;
  onStartDraw: () => void;
  onCancelDraw: () => void;
  onCreate: (name: string, bbox: number[]) => void;
  onActivate: (id: number) => void;
}

export default function RegionsPanel({ regions, activeId, drawing, draft, onStartDraw, onCancelDraw, onCreate, onActivate }: Props) {
  const [name, setName] = useState("");
  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-hair px-4 pb-3 pt-4">
        <h2 className="text-[15px] font-semibold">Régions</h2>
      </header>

      <div className="border-b border-hair px-4 py-3">
        {draft ? (
          <form onSubmit={(e) => { e.preventDefault(); if (name.trim()) { onCreate(name.trim(), draft); setName(""); } }}>
            <div className="mb-2 text-[12px] text-muted">Emprise tracée. Nommez la région, puis provisionnez.</div>
            <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="Nom de la région"
              className="mb-2 w-full rounded-md border border-hair bg-abyss px-2.5 py-1.5 text-ink placeholder:text-faint" />
            <div className="flex gap-2">
              <button type="submit" disabled={!name.trim()}
                className="flex-1 rounded-md bg-ink px-3 py-1.5 font-medium text-abyss hover:bg-white disabled:opacity-40">Provisionner</button>
              <button type="button" onClick={onCancelDraw} className="rounded-md border border-hair px-3 py-1.5 text-muted hover:text-ink">Annuler</button>
            </div>
          </form>
        ) : drawing ? (
          <div className="flex items-center justify-between gap-3 text-[12.5px]">
            <span className="text-signal">Cliquez deux coins opposés sur la carte</span>
            <button onClick={onCancelDraw} className="rounded-md border border-hair px-2.5 py-1 text-muted hover:text-ink">Annuler</button>
          </div>
        ) : (
          <button onClick={onStartDraw}
            className="w-full rounded-md bg-ink px-3 py-2 font-medium text-abyss hover:bg-white">Nouvelle région</button>
        )}
      </div>

      <ul className="flex-1 overflow-y-auto">
        {regions.map((r) => {
          const active = r.id === activeId;
          const size = r.layers.find((l) => l.layer === "bathymetry")?.size_bytes ?? 0;
          return (
            <li key={r.id}>
              <button onClick={() => !active && onActivate(r.id)} disabled={active}
                className={`w-full border-b border-hair/70 px-4 py-3 text-left transition-colors ${active ? "bg-raised" : "hover:bg-raised/60"}`}>
                <div className="flex items-center justify-between gap-3">
                  <span className="font-medium text-ink">{r.name}</span>
                  {active ? <span className="rounded-full bg-signal/20 px-2 py-0.5 text-[11px] text-signal">active</span>
                    : <span className="text-[11px] text-muted">activer</span>}
                </div>
                <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11.5px]">
                  {(["infrastructure", "bathymetry"] as const).map((layer) => {
                    const s = statusOf(r, layer);
                    return (
                      <span key={layer} className="flex items-center gap-1.5 text-muted">
                        <span className="h-1.5 w-1.5 rounded-full" style={{ background: STATUS_COLOR[s] ?? STATUS_COLOR.absente }} />
                        {layer === "infrastructure" ? "Infrastructures" : "Bathymétrie"} {STATUS_LABEL[s] ?? s}
                      </span>
                    );
                  })}
                  {size > 0 && <span className="text-faint">{(size / 1e6).toFixed(0)} Mo</span>}
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
