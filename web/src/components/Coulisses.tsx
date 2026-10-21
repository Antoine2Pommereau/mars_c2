import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { jourHeure } from "../lib/format";
import { L } from "../lib/libelles";
import { COULEUR_NIVEAU, mesurer, systeme } from "../registres/etat";
import { chercherReglages, type Reglage, type Rubrique, RUBRIQUES } from "../registres/reglages";
import { useEtat } from "./BarreEtat";
import { Visee } from "./Reglages";

const C = L.coulisses;

function Carte({ id, onOuvrir, voyant, children }: { id: Rubrique; onOuvrir: () => void; voyant?: string; children: ReactNode }) {
  return (
    <button onClick={onOuvrir} data-carte={id}
      className="flex min-h-[5rem] flex-col rounded-md border border-hair bg-raised/40 p-3 text-left hover:border-muted">
      <span className="flex items-center gap-1.5 font-semibold text-ink">
        {voyant && <span className="h-1.5 w-1.5 rounded-full" style={{ background: voyant }} />}{C.cartes[id]}
        <span className="ml-auto text-faint">›</span>
      </span>
      <span className="mt-1 space-y-0.5 text-[12px] leading-snug text-muted">{children}</span>
    </button>
  );
}

/** Champ de recherche des réglages : Entrée ouvre le premier résultat */
function Chercher({ q, onQ, resultats, onOuvrir }: { q: string; onQ: (q: string) => void; resultats: Reglage[];
  onOuvrir: (r: Reglage) => void }) {
  return (
    <label className="flex items-center gap-1.5 rounded-md border border-hair bg-abyss px-2 py-1 text-[12.5px] focus-within:border-signal">
      <Search size={13} className="text-faint" />
      <input value={q} onChange={(e) => onQ(e.target.value)} placeholder={C.chercher} aria-label={C.chercher} data-chercher-reglage
        onKeyDown={(e) => { if (e.key === "Enter" && resultats[0]) onOuvrir(resultats[0]); }}
        className="w-full bg-transparent text-ink placeholder:text-faint focus:outline-none" />
    </label>
  );
}

function Resultats({ q, resultats, onOuvrir }: { q: string; resultats: Reglage[]; onOuvrir: (r: Reglage) => void }) {
  if (!q.trim()) return null;
  if (!resultats.length) return <p className="px-1 py-2 text-[12px] text-muted">{C.aucun}</p>;
  return (
    <ul className="py-1">
      {resultats.map((r) => (
        <li key={`${r.rubrique}:${r.libelle}`}>
          <button onClick={() => onOuvrir(r)} className="w-full rounded px-1.5 py-1 text-left hover:bg-raised">
            <span className="block text-[12.5px] text-ink">{r.libelle}</span>
            <span className="block text-[11px] text-faint">{C.cartes[r.rubrique]}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** Coulisses (retours du lot U) : accueil en grille de cartes, chacune résume sa rubrique ; une rubrique à la fois,
 *  liste des rubriques et recherche des réglages à gauche (modèle des Réglages du Mac). Échap revient à l'accueil ;
 *  chaque rubrique a son adresse (espace=coulisses&rubrique=…). */
export default function Coulisses({ region, now, rubrique, partie, onRubrique, contenus, compte }: {
  region: string | null; now: number; rubrique: Rubrique | null; partie: string | null;
  onRubrique: (r: Rubrique | null, partie?: string | null) => void; contenus: Record<Rubrique, ReactNode>;
  compte: { observations: number; suivis: number; analyse: boolean; couches: number; masquage: boolean; rejeu: boolean };
}) {
  const [q, setQ] = useState("");
  const resultats = chercherReglages(q);
  const ouvrir = (r: Reglage) => { setQ(""); onRubrique(r.rubrique, r.partie); };
  const etat = useRef({ rubrique, q });
  etat.current = { rubrique, q };
  useEffect(() => {
    // Échap : vide la recherche, sinon revient à l'accueil ; en capture, avant l'écouteur de l'application
    const k = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (etat.current.q) { e.stopPropagation(); setQ(""); return; }
      if (etat.current.rubrique) { e.stopPropagation(); onRubrique(null); }
    };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [onRubrique]);

  if (rubrique) {
    return (
      <div className="flex h-full">
        <nav className="flex w-44 shrink-0 flex-col border-r border-hair p-2" aria-label={C.titre}>
          <Chercher q={q} onQ={setQ} resultats={resultats} onOuvrir={ouvrir} />
          {q.trim() ? <Resultats q={q} resultats={resultats} onOuvrir={ouvrir} /> : (
            <ul className="mt-2 space-y-0.5">
              <li>
                <button onClick={() => onRubrique(null)} className="w-full rounded px-2 py-1 text-left text-[12px] text-faint hover:text-ink">
                  ‹ {C.accueil}
                </button>
              </li>
              {RUBRIQUES.map((r) => (
                <li key={r}>
                  <button onClick={() => onRubrique(r)} aria-current={r === rubrique ? "page" : undefined} data-rubrique-lien={r}
                    className={`w-full rounded px-2 py-1 text-left text-[12.5px] ${r === rubrique ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
                    {C.cartes[r]}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-auto px-1 text-[11px] text-faint">{C.echap}</p>
        </nav>
        <section className="flex min-w-0 flex-1 flex-col" data-rubrique={rubrique}>
          <h2 className="border-b border-hair px-4 pb-3 pt-4 text-[15px] font-semibold">{C.cartes[rubrique]}</h2>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
            <Visee.Provider value={partie}>{contenus[rubrique]}</Visee.Provider>
          </div>
        </section>
      </div>
    );
  }
  return (
    <div className="flex h-full flex-col">
      <header className="space-y-2 border-b border-hair px-4 pb-3 pt-4">
        <h2 className="text-[15px] font-semibold">{C.titre}</h2>
        <Chercher q={q} onQ={setQ} resultats={resultats} onOuvrir={ouvrir} />
      </header>
      {q.trim() ? <div className="px-3"><Resultats q={q} resultats={resultats} onOuvrir={ouvrir} /></div> : (
        <Cartes region={region} now={now} compte={compte} onRubrique={onRubrique} />
      )}
    </div>
  );
}

function Cartes({ region, now, compte, onRubrique }: { region: string | null; now: number;
  compte: { observations: number; suivis: number; analyse: boolean; couches: number; masquage: boolean; rejeu: boolean };
  onRubrique: (r: Rubrique) => void }) {
  const etat = useEtat(region);
  const ctl = useQuery({ queryKey: ["controle", "etat"], queryFn: api.controle.etat, refetchInterval: 30_000 });
  const session = useQuery({ queryKey: ["controle", "session"], queryFn: api.controle.session });
  const mesures = useQuery({ queryKey: ["mesuresDecisions"], queryFn: () => api.mesuresDecisions(30), staleTime: 60_000 });
  const journal = useQuery({ queryKey: ["controle", "journal"], queryFn: () => api.controle.journal(30), refetchInterval: 30_000 });
  const { niveau, cause } = systeme(mesurer(etat.isError ? null : etat.data, now));
  const e = ctl.data, m = mesures.data, dernier = journal.data?.[0];
  const pauses = (e?.interrupteurs ?? []).filter((i) => i.en_pause).map((i) => i.nom);
  const decisions = m ? Object.values(m.par_type as Record<string, Record<string, number>>)
    .reduce((n, t) => n + Object.entries(t).filter(([k]) => k !== "part_vrais").reduce((a, [, v]) => a + (v ?? 0), 0), 0) : 0;
  // La couleur seulement si quelque chose demande l'attention
  const attention = (n: string) => (n === "rouge" || n === "orange" ? COULEUR_NIVEAU[n] : undefined);
  const o = (r: Rubrique) => () => onRubrique(r);
  return (
    <div className="grid flex-1 auto-rows-min grid-cols-2 gap-2 overflow-y-auto p-3">
      <Carte id="systeme" voyant={attention(niveau)} onOuvrir={o("systeme")}>
        <span className="block">{cause ? `${cause.ind.libelle} ${cause.m.resume}, ${L.etat.niveau[niveau]}` : L.etat.toutVa}</span>
      </Carte>
      <Carte id="capteurs" voyant={pauses.length ? COULEUR_NIVEAU.orange : undefined} onOuvrir={o("capteurs")}>
        <span className="block">{C.enPause(pauses)}</span>
        {e && <span className="block">{C.budget(e.budget.budget_sentinel.engage, e.budget.budget_sentinel.plafond)}</span>}
      </Carte>
      <Carte id="regles" onOuvrir={o("regles")}>
        {e && <span className="block">{C.version(e.version_regles)}</span>}
        {m && <span className="block">{C.faux(m.faux_positif, decisions, 30)}</span>}
      </Carte>
      <Carte id="satellites" onOuvrir={o("satellites")}>
        <span className="block">{C.actives(compte.observations)}</span>
        <span className="block">{C.enCours(compte.analyse)}</span>
      </Carte>
      <Carte id="notifications" onOuvrir={o("notifications")}><span className="block">{C.notifications}</span></Carte>
      <Carte id="zones" onOuvrir={o("zones")}><span className="block">{C.zones}</span></Carte>
      <Carte id="suivis" onOuvrir={o("suivis")}><span className="block">{C.suivis(compte.suivis)}</span></Carte>
      <Carte id="demo" onOuvrir={o("demo")}><span className="block">{C.demo(compte.masquage, compte.rejeu)}</span></Carte>
      <Carte id="couches" onOuvrir={o("couches")}><span className="block">{C.couches(compte.couches)}</span></Carte>
      <Carte id="journal" onOuvrir={o("journal")}>
        <span className="block">{dernier ? C.journal(dernier.auteur, L.controle.actions[dernier.action] ?? dernier.action, jourHeure(dernier.le))
          : C.journalVide}</span>
      </Carte>
      <Carte id="operateur" onOuvrir={o("operateur")}>
        <span className="block">{C.operateur(session.data?.operateur ?? null)}</span>
      </Carte>
    </div>
  );
}
