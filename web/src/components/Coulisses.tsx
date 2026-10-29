import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { L } from "../lib/libelles";
import { COULEUR_NIVEAU, mesurer } from "../registres/etat";
import { chercherReglages, GROUPES, type Reglage, type Rubrique } from "../registres/reglages";
import { quandEcheance, Visee } from "./Reglages";
import { Vide } from "./Elements";

const C = L.coulisses;
const A = C.attention;

/** Point d'attention par rubrique : seulement si quelque chose le demande, avec son motif en une ligne (au survol) ;
 *  rien quand tout va bien. Chaque mesure n'allume qu'une rubrique, celle qui la possède. */
function useAttention(status: unknown, now: number): Partial<Record<Rubrique, { couleur: string; motif: string }>> {
  const ctl = useQuery({ queryKey: ["controle", "etat"], queryFn: api.controle.etat, refetchInterval: 30_000 });
  const session = useQuery({ queryKey: ["controle", "session"], queryFn: api.controle.session });
  const notif = useQuery({ queryKey: ["controle", "notifications"], queryFn: api.controle.notifications, staleTime: 60_000 });
  const sysq = useQuery({ queryKey: ["controle", "systeme"], queryFn: api.controle.systeme, refetchInterval: 60_000 });
  const ms = mesurer(status, now);
  const out: Partial<Record<Rubrique, { couleur: string; motif: string }>> = {};
  const pire = (ids: string[]) => {
    const x = ms.filter(({ ind, m }) => ids.includes(ind.id) && (m.niveau === "rouge" || m.niveau === "orange"))
      .sort((a, b) => (a.m.niveau === "rouge" ? -1 : 0) - (b.m.niveau === "rouge" ? -1 : 0))[0];
    return x && { couleur: COULEUR_NIVEAU[x.m.niveau], motif: A.ligne(x.ind.libelle, x.m.resume) };
  };
  const sys = pire(["ingestion", "disque", "archivage", "version"]);
  // Échéance d'un secret à 14 jours ou moins (lot Z1) : rouge à 3 jours, orange avant
  const ech = (sysq.data?.echeances ?? []).map((x) => ({ x, q: quandEcheance(x) })).filter(({ q }) => q.couleur)
    .sort((a, b) => (a.q.couleur === COULEUR_NIVEAU.rouge ? -1 : 0) - (b.q.couleur === COULEUR_NIVEAU.rouge ? -1 : 0))[0];
  if (sys) out.systeme = sys;
  else if (ech) out.systeme = { couleur: ech.q.couleur!, motif: A.echeance(ech.x.libelle, ech.q.texte) };
  const src = pire(["flux", "listes"]);
  const pauses = (ctl.data?.interrupteurs ?? []).filter((i) => i.en_pause).map((i) => L.controle.sources[i.source] ?? i.nom);
  if (src) out.sources = src;
  else if (pauses.length) out.sources = { couleur: COULEUR_NIVEAU.orange, motif: A.pauses(pauses) };
  const act = pire(["satellites"]);
  if (act) out.activite = act;
  const b = ctl.data?.budget.budget_sentinel;
  if (b && b.plafond > 0 && b.engage >= 0.8 * b.plafond)
    out.budget = { couleur: COULEUR_NIVEAU.orange, motif: A.budget(Math.round((100 * b.engage) / b.plafond)) };
  if (notif.data && !notif.data.ntfy) out.notifications = { couleur: COULEUR_NIVEAU.orange, motif: A.ntfy };
  if (session.data?.abimes?.length) out.compte = { couleur: COULEUR_NIVEAU.rouge, motif: A.abime };
  return out;
}

/** Coulisses : une rubrique à la fois (modèle des Réglages du Mac), sans page d'accueil. À gauche, la recherche des
 *  réglages puis les rubriques en quatre groupes (docs/ux_refonte.md, section 13) ; un point de couleur seulement si
 *  une rubrique demande l'attention. Échap ramène à la Veille ; chaque rubrique a son adresse. */
export default function Coulisses({ status, now, rubrique, partie, onRubrique, onFermer, contenus }: {
  status: unknown; now: number; rubrique: Rubrique; partie: string | null;
  onRubrique: (r: Rubrique, partie?: string | null) => void; onFermer: () => void; contenus: Record<Rubrique, ReactNode>;
}) {
  const [q, setQ] = useState("");
  const resultats = chercherReglages(q);
  const attention = useAttention(status, now);
  const ouvrir = (r: Reglage) => { setQ(""); onRubrique(r.rubrique, r.partie); };
  const etat = useRef({ q, onFermer });
  etat.current = { q, onFermer };
  useEffect(() => {
    // Échap : vide la recherche, sinon revient à la Veille ; en capture, avant l'écouteur de l'application
    const k = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      if (etat.current.q) setQ(""); else etat.current.onFermer();
    };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, []);

  return (
    <div className="flex h-full">
      <nav className="flex w-48 shrink-0 flex-col border-r border-hair p-2" aria-label={C.titre}>
        <label className="flex items-center gap-1.5 rounded-md border border-hair bg-abyss px-2 py-1 text-[12.5px] focus-within:border-signal">
          <Search size={13} className="text-faint" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={C.chercher} aria-label={C.chercher} data-chercher-reglage
            onKeyDown={(e) => { if (e.key === "Enter" && resultats[0]) ouvrir(resultats[0]); }}
            className="w-full bg-transparent text-ink placeholder:text-faint focus:outline-none" />
        </label>
        {q.trim() ? (
          resultats.length ? (
            <ul className="mt-1 py-1">
              {resultats.map((r) => (
                <li key={r.libelle}>
                  <button onClick={() => ouvrir(r)} className="w-full rounded px-1.5 py-1 text-left hover:bg-raised">
                    <span className="block text-[12.5px] text-ink">{r.libelle}</span>
                    <span className="block text-[11px] text-faint">{C.rubriques[r.rubrique]}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : <Vide titre={C.aucun} raison={C.aucunRaison} className="px-1 py-2" />
        ) : (
          <div className="mt-2 space-y-3 overflow-y-auto">
            {GROUPES.map((g) => (
              <div key={g.id}>
                <p className="px-2 pb-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-faint">{C.groupes[g.id]}</p>
                <ul>
                  {g.rubriques.map((r) => {
                    const a = attention[r];
                    return (
                      <li key={r}>
                        <button onClick={() => onRubrique(r)} aria-current={r === rubrique ? "page" : undefined} data-rubrique-lien={r}
                          title={a?.motif} data-attention={a ? "1" : undefined}
                          className={`flex w-full items-center gap-1.5 rounded px-2 py-1 text-left text-[12.5px]
                            ${r === rubrique ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
                          <span className="flex-1">{C.rubriques[r]}</span>
                          {a && <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: a.couleur }} aria-label={a.motif} />}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))}
          </div>
        )}
        <p className="mt-auto px-1 pt-2 text-[11px] text-faint">{C.echap}</p>
      </nav>
      <section className="flex min-w-0 flex-1 flex-col" data-rubrique={rubrique}>
        <h2 className="border-b border-hair px-4 pb-3 pt-4 text-[15px] font-semibold">{C.rubriques[rubrique]}</h2>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-2">
          <Visee.Provider value={partie}>{contenus[rubrique]}</Visee.Provider>
        </div>
      </section>
    </div>
  );
}
