import { Fragment, type ReactNode } from "react";
import { L } from "../lib/libelles";

// Glossaire au survol (lot UX1) : les termes de L.glossaire repérés dans un texte, soulignés en pointillé, avec leur
// définition en bulle au survol et au focus du clavier (Tab). Sigles en majuscules exactes, expressions sans casse.
const TERMES = Object.keys(L.glossaire).sort((a, b) => b.length - a.length);
const MOTIF = new RegExp(`(?<![\\p{L}\\d])(${TERMES.map((t) => t.replace(/ /g, "\\s")).join("|")})(?![\\p{L}\\d])`, "giu");
const cle = (m: string) => TERMES.find((t) => (t === t.toUpperCase() ? t === m : t.toLowerCase() === m.toLowerCase()));

function Terme({ terme, children }: { terme: string; children: ReactNode }) {
  return (
    <span tabIndex={0} className="glose relative cursor-help underline decoration-dotted decoration-1 underline-offset-2 focus:outline-none
      focus-visible:rounded-sm focus-visible:ring-1 focus-visible:ring-signal">
      {children}
      <span role="tooltip" className="glose-bulle pointer-events-none absolute left-0 top-full z-50 mt-1 hidden w-64 rounded-md border
        border-hair bg-raised px-2.5 py-1.5 text-left text-[11.5px] font-normal normal-case leading-snug tracking-normal text-ink shadow-xl">
        {L.glossaire[terme]}
      </span>
    </span>
  );
}

/** Texte avec ses termes du glossaire (la première occurrence de chacun seulement, pour ne pas charger la ligne). */
export function Glose({ texte }: { texte: string }) {
  const vus = new Set<string>();
  const morceaux: ReactNode[] = [];
  let i = 0;
  for (const m of texte.matchAll(MOTIF)) {
    const k = cle(m[0]);
    if (!k || vus.has(k)) continue;
    vus.add(k);
    morceaux.push(texte.slice(i, m.index), <Terme key={m.index} terme={k}>{m[0]}</Terme>);
    i = m.index! + m[0].length;
  }
  if (!morceaux.length) return <>{texte}</>;
  return <>{morceaux.map((x, j) => <Fragment key={j}>{x}</Fragment>)}{texte.slice(i)}</>;
}
