import { useEffect } from "react";
import { L } from "../lib/libelles";
import { BoutonObservation } from "./Fiches";
import { coord } from "../lib/unites";

/** Menu du clic droit sur la carte (lot T1) : demande d'observation satellite sur le point. Échap ou un clic ailleurs
 *  le ferme. */
export default function MenuCarte({ point, onClose }: { point: { lon: number; lat: number; x: number; y: number };
  onClose: () => void }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);
  return (
    <div className="absolute z-20 rounded-md border border-hair bg-panel/95 px-3 py-2 text-[12px] shadow-lg backdrop-blur"
      style={{ left: point.x, top: point.y }} onPointerDown={(e) => e.stopPropagation()}>
      <div className="flex items-center gap-3 text-muted">
        <span className="tabular-nums">{coord(point.lon, point.lat)}</span>
        <button onClick={onClose} aria-label={L.commun.fermer} className="ml-auto hover:text-ink">×</button>
      </div>
      <BoutonObservation cible={{ lon: point.lon, lat: point.lat }} />
    </div>
  );
}
