import { useRef } from "react";
import { L } from "../lib/libelles";

/** Poignée de redimensionnement d'un panneau (retours du lot U) : glisser, flèches gauche et droite par pas de 16 px,
 *  double clic pour la largeur par défaut. `cote` : bord du panneau où elle se trouve (droit pour le panneau de
 *  gauche, gauche pour la fiche à droite) ; glisser vers l'extérieur élargit. */
export default function Poignee({ largeur, onLargeur, defaut, min, max, cote, nom }: {
  largeur: number; onLargeur: (l: number) => void; defaut: number; min: number; max: number;
  cote: "droit" | "gauche"; nom: string;
}) {
  const depart = useRef<{ x: number; l: number } | null>(null);
  const sens = cote === "droit" ? 1 : -1;
  return (
    <div role="separator" aria-orientation="vertical" aria-label={L.commun.redimensionner(nom)} tabIndex={0}
      aria-valuenow={largeur} aria-valuemin={min} aria-valuemax={max} data-poignee={nom}
      title={L.commun.redimensionnerAide}
      onPointerDown={(e) => {
        e.preventDefault();
        e.currentTarget.setPointerCapture(e.pointerId);
        depart.current = { x: e.clientX, l: largeur };
        document.body.style.userSelect = "none";
      }}
      onPointerMove={(e) => { const d = depart.current; if (d) onLargeur(d.l + sens * (e.clientX - d.x)); }}
      onPointerUp={(e) => {
        depart.current = null;
        document.body.style.userSelect = "";
        e.currentTarget.releasePointerCapture(e.pointerId);
      }}
      onDoubleClick={() => onLargeur(defaut)}
      onKeyDown={(e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        e.stopPropagation();
        onLargeur(largeur + (e.key === "ArrowRight" ? 16 : -16) * sens);
      }}
      className={`group absolute top-0 z-20 h-full w-2 cursor-col-resize touch-none focus:outline-none
        ${cote === "droit" ? "-right-1" : "-left-1"}`}>
      <span className="mx-auto block h-full w-px bg-transparent transition-colors group-hover:bg-signal/70 group-focus-visible:bg-signal" />
    </div>
  );
}
