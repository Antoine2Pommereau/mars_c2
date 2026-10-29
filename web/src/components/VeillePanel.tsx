import { Aperture, Moon } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";
import { api, type Dossier, type ListeDossiers } from "../lib/api";
import { deplacer, toucheLibre } from "../lib/clavier";
import { hm } from "../lib/format";
import { L } from "../lib/libelles";
import { URGENCE } from "../lib/urgence";
import { typeAlerte } from "../registres/alertes";
import { Squelette, Tag } from "./Elements";

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

function Ligne({ d, instant, focus, curseur, onPick }:
  { d: Dossier; instant: number; focus: boolean; curseur: boolean; onPick: (d: Dossier) => void }) {
  return (
    <button onClick={() => onPick(d)} aria-current={focus || undefined} data-dossier={d.cle} data-curseur={curseur || undefined}
      style={{ boxShadow: d.niveau > 1 ? `inset 3px 0 0 ${URGENCE[d.niveau]}` : undefined }}
      className={`grid w-full grid-cols-[1fr_auto] gap-x-3 border-b border-hair/70 py-2.5 pl-4 pr-4 text-left transition-colors
        ${focus ? "bg-raised" : "hover:bg-raised/60"} ${curseur || focus ? "outline outline-1 -outline-offset-1 outline-signal" : ""}`}>
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
export type OngletVeille = "dossiers" | "alertes" | "suivis";

export default function VeillePanel({ liste, instant, focus, onPick, fil, suivis, aveugle, onglet, onOnglet }:
  { liste: ListeDossiers | undefined; instant: number; focus: string | null; onPick: (d: Dossier) => void; fil: ReactNode;
    suivis: ReactNode; aveugle: { minutes: number; derniere: string | null } | null;
    onglet: OngletVeille; onOnglet: (o: OngletVeille) => void }) {
  const [noterOuvert, setNoterOuvert] = useState(false);
  const examiner = liste?.dossiers.filter((d) => d.niveau >= 2) ?? [];
  const noter = liste?.dossiers.filter((d) => d.niveau === 1) ?? [];
  const calme = liste && !examiner.length;
  // Clavier : J et K déplacent le curseur dans les dossiers visibles, Entrée ouvre, ? montre l'aide
  const [curseur, setCurseur] = useState<string | null>(null);
  const [aide, setAide] = useState(false);
  const visibles = [...examiner, ...(noterOuvert ? noter : [])];
  const etat = useRef({ visibles, curseur, focus, onglet, aide });
  etat.current = { visibles, curseur: curseur ?? focus, focus, onglet, aide };
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const t = toucheLibre(e), s = etat.current;
      if (t === "?") { e.preventDefault(); setAide((a) => !a); return; }
      if (s.onglet !== "dossiers" || !t) return;
      const cles = s.visibles.map((d) => d.cle);
      if (t === "j" || t === "k") {
        e.preventDefault();
        const c = deplacer(cles, s.curseur, t === "j" ? 1 : -1);
        setCurseur(c);
        document.querySelector(`[data-dossier="${c}"]`)?.scrollIntoView({ block: "nearest" });
      } else if (t === "enter" && s.curseur && s.curseur !== s.focus) {
        const d = s.visibles.find((x) => x.cle === s.curseur);
        if (d) { e.preventDefault(); onPick(d); }
      }
    };
    // Échap ferme l'aide seule : en capture, avant l'écouteur de l'application qui fermerait aussi la fiche
    const echap = (e: KeyboardEvent) => {
      if (e.key === "Escape" && etat.current.aide) { e.stopPropagation(); setAide(false); }
    };
    window.addEventListener("keydown", k);
    window.addEventListener("keydown", echap, true);
    return () => { window.removeEventListener("keydown", k); window.removeEventListener("keydown", echap, true); };
  }, [onPick]);
  const mesuresQ = useQuery({ queryKey: ["mesuresDecisions"], queryFn: () => api.mesuresDecisions(30), enabled: aide, staleTime: 60_000 });
  return (
    <div className="relative flex h-full flex-col">
      {aide && (
        <div className="absolute inset-0 z-20 bg-panel px-5 py-4 text-[12.5px]" role="dialog" aria-label={V.aide.titre}>
          <p className="mb-3 text-[15px] font-semibold text-ink">{V.aide.titre}</p>
          <dl className="grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1.5">
            {V.aide.touches.map(([k, t]) => <Fragment key={k}><dt className="font-mono text-[11.5px] text-ink">{k}</dt><dd className="text-muted">{t}</dd></Fragment>)}
          </dl>
          <p className="mt-5 font-semibold text-ink">{V.aide.mesures(30)}</p>
          {mesuresQ.data && <>
            <p className="mt-1 text-muted">{V.aide.vraisFaux(mesuresQ.data.confirmees, mesuresQ.data.faux_positif)}</p>
            <p className="text-ink">{V.aide.part(mesuresQ.data.part_vrais)}</p>
          </>}
          <p className="mt-5 text-[11.5px] text-faint">{V.aide.fermer}</p>
        </div>
      )}
      {aveugle && (
        // État dégradé : dans la Veille, jamais par dessus la carte ni le sélecteur de vues
        <p role="alert" className="border-b border-hair bg-gap/10 px-4 py-2.5 text-[12.5px] leading-snug text-ink"
          style={{ boxShadow: `inset 3px 0 0 ${URGENCE[3]}` }}>
          {V.aveugle(aveugle.minutes, aveugle.derniere ? hm(aveugle.derniere) : null)}
        </p>
      )}
      <div className="flex gap-1 border-b border-hair px-3 pt-3" role="tablist">
        {(["dossiers", "alertes", "suivis"] as const).map((o) => (
          <button key={o} role="tab" aria-selected={onglet === o} onClick={() => onOnglet(o)}
            className={`rounded-t px-3 py-1.5 text-[12.5px] ${onglet === o ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
            {V.onglets[o]}
          </button>
        ))}
      </div>
      {onglet === "alertes" ? <div className="min-h-0 flex-1">{fil}</div> : onglet === "suivis" ? <div className="min-h-0 flex-1">{suivis}</div> : (
        <>
          <header className="flex items-baseline justify-between px-4 pb-2 pt-3">
            <h2 className="text-[20px] font-semibold tabular-nums">
              {calme ? V.rien : <>{examiner.length}<span className="ml-1.5 text-[13px] font-normal text-muted">{V.aExaminer(examiner.length)}</span></>}
            </h2>
            <span className="text-[11px] text-faint">{V.raccourcis}</span>
          </header>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {!liste && <Squelette lignes={4} className="px-4 py-4" />}
            {calme && !aveugle && (
              <div className="px-6 py-8 text-center text-muted">
                <p className="text-[15px] text-ink">{V.calme(liste.dernier_inhabituel ? (instant - Date.parse(liste.dernier_inhabituel)) / 3600_000 : null, liste.fenetre_h)}</p>
                <p className="mt-1 text-[12.5px]">{V.calmeDetail}</p>
              </div>
            )}
            {examiner.map((d) => <Ligne key={d.cle} d={d} instant={instant} focus={focus === d.cle} curseur={curseur === d.cle} onPick={onPick} />)}
            {noter.length > 0 && (
              <button onClick={() => setNoterOuvert(!noterOuvert)} aria-expanded={noterOuvert}
                className="w-full px-4 py-2.5 text-left text-[12.5px] text-muted hover:text-ink">
                <span className={`mr-1.5 inline-block transition-transform ${noterOuvert ? "rotate-90" : ""}`}>›</span>{V.aNoter(noter.length)}
              </button>
            )}
            {noterOuvert && noter.map((d) => <Ligne key={d.cle} d={d} instant={instant} focus={focus === d.cle} curseur={curseur === d.cle} onPick={onPick} />)}
          </div>
        </>
      )}
    </div>
  );
}
