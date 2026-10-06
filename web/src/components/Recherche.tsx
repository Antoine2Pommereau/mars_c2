import { Search } from "lucide-react";
import { useEffect, useRef } from "react";
import { L } from "../lib/libelles";

/** Recherche globale, ouverte par Cmd + K (ou Ctrl + K) : la fenêtre et ses groupes sont en place, la recherche
 *  dans les navires, infrastructures, alertes et lieux arrive au prochain lot. */
export default function Recherche({ onClose }: { onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => input.current?.focus(), []);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-abyss/60 pt-[12vh]" onMouseDown={onClose}>
      <div role="dialog" aria-label={L.recherche.titre} onMouseDown={(e) => e.stopPropagation()}
        className="w-[560px] max-w-[92vw] rounded-lg border border-hair bg-panel shadow-2xl">
        <label className="flex items-center gap-3 border-b border-hair px-4 py-3">
          <Search size={16} className="text-muted" />
          <input ref={input} placeholder={L.recherche.placeholder} onKeyDown={(e) => e.key === "Escape" && onClose()}
            className="flex-1 bg-transparent text-[14px] text-ink placeholder:text-faint focus:outline-none" />
        </label>
        <div className="px-4 py-3 text-[12.5px]">
          {L.recherche.groupes.map((g) => <div key={g} className="py-1 text-faint">{g}</div>)}
          <p className="mt-2 text-muted">{L.recherche.aVenir}</p>
        </div>
      </div>
    </div>
  );
}
