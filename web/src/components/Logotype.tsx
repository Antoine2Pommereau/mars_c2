import { symboleSvg } from "../lib/symbole";
import { L } from "../lib/libelles";

/** Symbole « viseur » (lib/symbole.ts) à la couleur du jeton `symbole` du thème ; décoratif, le nom l'accompagne. */
function Symbole({ taille = 24, petit = false }: { taille?: number; petit?: boolean }) {
  return <span className="inline-flex shrink-0 text-symbole" style={{ width: taille, height: taille }}
    dangerouslySetInnerHTML={{ __html: symboleSvg({ couleur: "currentColor", enLigne: true, petit, px: taille }) }} />;
}

/** Logotype de la barre d'état et de la connexion : symbole, « MARS C2 » espacé, « Surveillance maritime » dessous. */
export function Logotype({ taille = 24 }: { taille?: number }) {
  return (
    <span className="flex items-center gap-2">
      <Symbole taille={taille} />
      <span className="flex flex-col leading-none">
        <span className="font-cond text-[13.5px] font-semibold tracking-[0.12em] text-ink">{L.app.nom}</span>
        <span className="mt-[3px] text-[9.5px] tracking-[0.02em] text-muted">{L.app.sousTitre}</span>
      </span>
    </span>
  );
}
