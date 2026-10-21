import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { api } from "../lib/api";
import { L } from "../lib/libelles";
import { COULEUR_NIVEAU, mesurer, systeme } from "../registres/etat";
import { useEtat } from "./BarreEtat";

const C = L.coulisses;

/** Où mène une carte : un panneau du rail, ou une section du panneau de contrôle */
export type Cible = { panneau: "systeme" | "couches" | "analyses" | "suivis" | "observations" } | { controle: string };

function Carte({ id, onOuvrir, voyant, children }: { id: string; onOuvrir: () => void; voyant?: string; children: ReactNode }) {
  return (
    <button onClick={onOuvrir} data-carte={id}
      className="flex min-h-[5.5rem] flex-col rounded-md border border-hair bg-raised/40 p-3 text-left hover:border-muted">
      <span className="flex items-center gap-1.5 font-semibold text-ink">
        {voyant && <span className="h-1.5 w-1.5 rounded-full" style={{ background: voyant }} />}{C.cartes[id]}
        <span className="ml-auto text-faint">›</span>
      </span>
      <span className="mt-1 space-y-0.5 text-[12px] leading-snug text-muted">{children}</span>
    </button>
  );
}

/** Coulisses (lot U, étape 6) : tout ce qui n'est pas la veille, en une grille compacte ; chaque carte ouvre son
 *  panneau, qui garde son contenu d'avant la refonte. */
export default function Coulisses({ region, now, onOuvrir, compte }: {
  region: string | null; now: number; onOuvrir: (c: Cible) => void;
  compte: { observations: number; suivis: number; analyse: boolean; couches: number; masquage: boolean; rejeu: boolean };
}) {
  const etat = useEtat(region);
  const ctl = useQuery({ queryKey: ["controle", "etat"], queryFn: api.controle.etat, refetchInterval: 30_000 });
  const session = useQuery({ queryKey: ["controle", "session"], queryFn: api.controle.session });
  const mesures = useQuery({ queryKey: ["mesuresDecisions"], queryFn: () => api.mesuresDecisions(30), staleTime: 60_000 });
  const ms = mesurer(etat.isError ? null : etat.data, now);
  const { niveau, cause } = systeme(ms);
  const e = ctl.data, m = mesures.data;
  const pauses = (e?.interrupteurs ?? []).filter((i) => i.en_pause).map((i) => i.nom);
  const decisions = m ? Object.values(m.par_type as Record<string, Record<string, number>>)
    .reduce((n, t) => n + Object.entries(t).filter(([k]) => k !== "part_vrais").reduce((a, [, v]) => a + (v ?? 0), 0), 0) : 0;
  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-hair px-4 pb-3 pt-4"><h2 className="text-[15px] font-semibold">{C.titre}</h2></header>
      <div className="grid flex-1 auto-rows-min grid-cols-2 gap-2 overflow-y-auto p-3">
        <Carte id="systeme" voyant={COULEUR_NIVEAU[niveau]} onOuvrir={() => onOuvrir({ panneau: "systeme" })}>
          <span className="block">{cause ? `${cause.ind.libelle} ${cause.m.resume}, ${L.etat.niveau[niveau]}` : L.etat.toutVa}</span>
        </Carte>
        <Carte id="capteurs" voyant={pauses.length ? COULEUR_NIVEAU.orange : undefined} onOuvrir={() => onOuvrir({ controle: "capteurs" })}>
          <span className="block">{C.enPause(pauses)}</span>
          {e && <span className="block">{C.budget(e.budget.budget_sentinel.engage, e.budget.budget_sentinel.plafond)}</span>}
        </Carte>
        <Carte id="regles" onOuvrir={() => onOuvrir({ controle: "regles" })}>
          {e && <span className="block">{C.version(e.version_regles)}</span>}
          {m && <span className="block">{C.faux(m.faux_positif, decisions, 30)}</span>}
        </Carte>
        <Carte id="satellites" onOuvrir={() => onOuvrir({ panneau: "observations" })}>
          <span className="block">{C.actives(compte.observations)}</span>
        </Carte>
        <Carte id="analyses" onOuvrir={() => onOuvrir({ panneau: "analyses" })}><span className="block">{C.enCours(compte.analyse)}</span></Carte>
        <Carte id="suivis" onOuvrir={() => onOuvrir({ panneau: "suivis" })}><span className="block">{C.suivis(compte.suivis)}</span></Carte>
        <Carte id="zones" onOuvrir={() => onOuvrir({ controle: "zones" })}><span className="block">{C.zones}</span></Carte>
        <Carte id="notifications" onOuvrir={() => onOuvrir({ controle: "notifications" })}><span className="block">{C.notifications}</span></Carte>
        <Carte id="demo" onOuvrir={() => onOuvrir({ controle: "demo" })}><span className="block">{C.demo(compte.masquage, compte.rejeu)}</span></Carte>
        <Carte id="couches" onOuvrir={() => onOuvrir({ panneau: "couches" })}><span className="block">{C.couches(compte.couches)}</span></Carte>
        <Carte id="operateur" onOuvrir={() => onOuvrir({ controle: "connexion" })}>
          <span className="block">{C.operateur(session.data?.operateur ?? null)}</span>
        </Carte>
      </div>
    </div>
  );
}
