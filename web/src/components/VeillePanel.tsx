import { Aperture, Moon } from "lucide-react";
import { type ReactNode, useState } from "react";
import type { Dossier, ListeDossiers } from "../lib/api";
import { L } from "../lib/libelles";
import { URGENCE } from "../lib/urgence";
import { typeAlerte } from "../registres/alertes";
import { Tag } from "./Elements";

const V = L.veille;

/** Icône d'un type de signal : celle du registre des alertes, la lune pour une lumière la nuit, l'objectif pour une
 *  image optique ; toujours neutre (la couleur est réservée à l'urgence) */
export function IconeSignal({ type, taille = 13 }: { type: string; taille?: number }) {
  if (type === "VIIRS") return <Moon size={taille} strokeWidth={1.8} />;
  if (type === "OPTIQUE") return <Aperture size={taille} strokeWidth={1.8} />;
  const I = typeAlerte(type).Icone;
  return <I size={taille} strokeWidth={1.8} />;
}

const depuis = (iso: string, instant: number) => V.ilYa((instant - Date.parse(iso)) / 60_000);

function Ligne({ d, instant, focus, onPick }: { d: Dossier; instant: number; focus: boolean; onPick: (d: Dossier) => void }) {
  return (
    <button onClick={() => onPick(d)} aria-current={focus || undefined} data-dossier={d.cle}
      style={{ boxShadow: d.niveau > 1 ? `inset 3px 0 0 ${URGENCE[d.niveau]}` : undefined }}
      className={`grid w-full grid-cols-[1fr_auto] gap-x-3 border-b border-hair/70 py-2.5 pl-4 pr-4 text-left transition-colors
        ${focus ? "bg-raised outline outline-1 -outline-offset-1 outline-signal" : "hover:bg-raised/60"}`}>
      <span className="min-w-0">
        <span className="flex items-center gap-1.5">
          <span className="truncate font-semibold text-ink">{d.nom}</span>
          {d.pavillon && <Tag>{d.pavillon}</Tag>}
        </span>
        <span className="mt-0.5 block text-[12.5px] leading-snug text-ink/85">{d.phrase}</span>
        <span className="mt-1 flex gap-1.5 text-muted">{d.icones.map((t) => <IconeSignal key={t} type={t} />)}</span>
      </span>
      <span className="text-right text-[12px] text-muted">{depuis(d.dernier, instant)}</span>
    </button>
  );
}

/** Veille (lot U) : les dossiers à examiner, triés par urgence ; les dossiers à noter repliés ; le fil d'alertes
 *  complet en second onglet. La question unique : quel navire regarder maintenant, et pourquoi. */
export default function VeillePanel({ liste, instant, focus, onPick, fil }:
  { liste: ListeDossiers | undefined; instant: number; focus: string | null; onPick: (d: Dossier) => void; fil: ReactNode }) {
  const [onglet, setOnglet] = useState<"dossiers" | "alertes">("dossiers");
  const [noterOuvert, setNoterOuvert] = useState(false);
  const examiner = liste?.dossiers.filter((d) => d.niveau >= 2) ?? [];
  const noter = liste?.dossiers.filter((d) => d.niveau === 1) ?? [];
  const calme = liste && !examiner.length;
  return (
    <div className="flex h-full flex-col">
      <div className="flex gap-1 border-b border-hair px-3 pt-3" role="tablist">
        {(["dossiers", "alertes"] as const).map((o) => (
          <button key={o} role="tab" aria-selected={onglet === o} onClick={() => setOnglet(o)}
            className={`rounded-t px-3 py-1.5 text-[12.5px] ${onglet === o ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
            {V.onglets[o]}
          </button>
        ))}
      </div>
      {onglet === "alertes" ? <div className="min-h-0 flex-1">{fil}</div> : (
        <>
          <header className="flex items-baseline justify-between px-4 pb-2 pt-3">
            <h2 className="text-[20px] font-semibold tabular-nums">
              {calme ? V.rien : <>{examiner.length}<span className="ml-1.5 text-[13px] font-normal text-muted">{V.aExaminer(examiner.length)}</span></>}
            </h2>
            <span className="text-[11px] text-faint">{V.raccourcis}</span>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {!liste && <p className="px-4 py-6 text-muted">{L.commun.chargement}</p>}
            {calme && (
              <div className="px-6 py-8 text-center text-muted">
                <p className="text-[15px] text-ink">{V.calme(liste.dernier_inhabituel ? (instant - Date.parse(liste.dernier_inhabituel)) / 3600_000 : null, liste.fenetre_h)}</p>
                <p className="mt-1 text-[12.5px]">{V.calmeDetail}</p>
              </div>
            )}
            {examiner.map((d) => <Ligne key={d.cle} d={d} instant={instant} focus={focus === d.cle} onPick={onPick} />)}
            {noter.length > 0 && (
              <button onClick={() => setNoterOuvert(!noterOuvert)} aria-expanded={noterOuvert}
                className="w-full px-4 py-2.5 text-left text-[12.5px] text-muted hover:text-ink">
                <span className={`mr-1.5 inline-block transition-transform ${noterOuvert ? "rotate-90" : ""}`}>›</span>{V.aNoter(noter.length)}
              </button>
            )}
            {noterOuvert && noter.map((d) => <Ligne key={d.cle} d={d} instant={instant} focus={focus === d.cle} onPick={onPick} />)}
          </div>
        </>
      )}
    </div>
  );
}
