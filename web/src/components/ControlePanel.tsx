import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, LogOut, Minus, Plus } from "lucide-react";
import { type ReactNode, useState } from "react";
import { api, type EtatControle, type Interrupteur } from "../lib/api";
import { jourHeure } from "../lib/format";
import { L } from "../lib/libelles";
import type { Props } from "../lib/types";

const C = L.controle;
const euros = (v: number) => `${v.toFixed(2).replace(".", ",")} €`;

/** Section repliable du panneau ; l'état ouvert est gardé dans le navigateur */
function Section({ id, titre, droite, children }: { id: string; titre: string; droite?: ReactNode; children: ReactNode }) {
  const cle = `mars.controle.${id}`;
  const [ouverte, setOuverte] = useState(() => {
    try { return localStorage.getItem(cle) !== "0"; } catch { return true; }
  });
  const basculer = () => {
    setOuverte(!ouverte);
    try { localStorage.setItem(cle, ouverte ? "0" : "1"); } catch { /* stockage indisponible */ }
  };
  return (
    <section className="border-b border-hair">
      <button onClick={basculer} aria-expanded={ouverte}
        className="flex w-full items-center gap-1.5 px-4 py-2.5 text-left text-[12px] font-semibold uppercase tracking-wide text-muted hover:text-ink">
        <ChevronRight size={13} className={`transition-transform ${ouverte ? "rotate-90" : ""}`} />
        <span className="flex-1">{titre}</span>
        {droite}
      </button>
      {ouverte && <div className="px-4 pb-3">{children}</div>}
    </section>
  );
}

const champ = "w-full rounded border border-hair bg-abyss px-2 py-1 text-[12.5px] text-ink placeholder:text-faint focus:border-signal";
const bouton = "rounded border border-hair px-2 py-0.5 text-[12px] text-ink hover:border-signal disabled:opacity-40";

function Connexion({ operateur, configure }: { operateur: string | null; configure: boolean }) {
  const qc = useQueryClient();
  const [nom, setNom] = useState(""), [mdp, setMdp] = useState("");
  const fin = () => qc.invalidateQueries({ queryKey: ["controle"] });
  const entrer = useMutation({ mutationFn: () => api.controle.connexion(nom, mdp), onSuccess: () => { setMdp(""); fin(); } });
  const sortir = useMutation({ mutationFn: api.controle.deconnexion, onSuccess: fin });
  if (operateur) {
    return (
      <div className="flex items-center justify-between text-[12.5px]">
        <span className="text-ink">{operateur}</span>
        <button onClick={() => sortir.mutate()} className="flex items-center gap-1 text-muted hover:text-ink" title={C.deconnexion}>
          <LogOut size={13} /> {C.deconnexion}
        </button>
      </div>
    );
  }
  if (!configure) return <p className="text-[12px] text-muted">{C.nonConfigure}</p>;
  return (
    <form className="space-y-1.5" onSubmit={(e) => { e.preventDefault(); entrer.mutate(); }}>
      <input className={champ} placeholder={C.operateur} value={nom} onChange={(e) => setNom(e.target.value)}
        autoComplete="username" aria-label={C.operateur} />
      <input className={champ} placeholder={C.motDePasse} type="password" value={mdp} onChange={(e) => setMdp(e.target.value)}
        autoComplete="current-password" aria-label={C.motDePasse} />
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11.5px] text-gap">{entrer.error?.message}</span>
        <button type="submit" className={bouton} disabled={!nom || !mdp || entrer.isPending}>{C.seConnecter}</button>
      </div>
    </form>
  );
}

/** Interrupteur d'une source : pause avec motif et date de reprise facultative, reprise d'un clic */
function Source({ i, actif }: { i: Interrupteur; actif: boolean }) {
  const qc = useQueryClient();
  const [ouvert, setOuvert] = useState(false);
  const [motif, setMotif] = useState(""), [reprise, setReprise] = useState("");
  const m = useMutation({
    mutationFn: (pause: boolean) => api.controle.basculer(i.source, pause
      ? { en_pause: true, motif, jusqu_a: reprise ? new Date(reprise).toISOString() : null } : { en_pause: false }),
    onSuccess: () => { setOuvert(false); setMotif(""); setReprise(""); qc.invalidateQueries({ queryKey: ["controle"] }); },
  });
  const detail = i.en_pause
    ? [i.depuis && C.depuis(jourHeure(i.depuis)), i.jusqu_a && C.reprise(jourHeure(i.jusqu_a)), i.motif].filter(Boolean).join(" · ")
    : null;
  return (
    <li className="py-1.5">
      <div className="flex items-center gap-2">
        <span className={`h-2 w-2 shrink-0 rounded-full ${i.en_pause ? "bg-rdv" : "bg-faint"}`} />
        <span className="flex-1 text-[12.5px] text-ink">{i.nom}</span>
        <span className={`text-[11.5px] ${i.en_pause ? "text-rdv" : "text-muted"}`}>{i.en_pause ? C.enPause : C.enService}</span>
        <button className={bouton} disabled={!actif || m.isPending}
          onClick={() => (i.en_pause ? m.mutate(false) : setOuvert(!ouvert))}>
          {i.en_pause ? C.reprendre : C.pause}
        </button>
      </div>
      {detail && <p className="mt-0.5 pl-4 text-[11.5px] leading-snug text-muted">{detail}</p>}
      {ouvert && !i.en_pause && (
        <form className="mt-1.5 space-y-1.5 pl-4" onSubmit={(e) => { e.preventDefault(); m.mutate(true); }}>
          <input className={champ} placeholder={C.motif} value={motif} onChange={(e) => setMotif(e.target.value)} aria-label={C.motif} />
          <label className="block text-[11.5px] text-muted">{C.repriseLe}
            <input className={`${champ} mt-0.5`} type="datetime-local" value={reprise} onChange={(e) => setReprise(e.target.value)} />
          </label>
          <div className="flex justify-end gap-2">
            <button type="button" className="text-[12px] text-muted hover:text-ink" onClick={() => setOuvert(false)}>{C.annuler}</button>
            <button type="submit" className={bouton} disabled={!motif.trim() || m.isPending}>{C.confirmer}</button>
          </div>
        </form>
      )}
      {m.error && <p className="pl-4 text-[11.5px] text-gap">{m.error.message}</p>}
    </li>
  );
}

function Plafond({ source, nom, p, actif }: { source: string; nom: string; p: EtatControle["plafonds"][string]; actif: boolean }) {
  const qc = useQueryClient();
  const [v, setV] = useState<number | null>(null), [motif, setMotif] = useState("");
  const valeur = v ?? p.valeur;
  const m = useMutation({
    mutationFn: () => api.controle.plafond(source, valeur, motif),
    onSuccess: () => { setV(null); setMotif(""); qc.invalidateQueries({ queryKey: ["controle"] }); },
  });
  return (
    <div className="py-1">
      <div className="flex items-center gap-2 text-[12.5px]">
        <span className="flex-1 text-ink">{nom}</span>
        <span className="text-[11px] text-faint">{C.plafondDefaut(p.defaut)}</span>
        <button className={bouton} disabled={!actif || valeur <= p.min} onClick={() => setV(valeur - 1)} aria-label={C.moins}><Minus size={11} /></button>
        <span className="w-5 text-center tabular-nums">{valeur}</span>
        <button className={bouton} disabled={!actif || valeur >= p.max} onClick={() => setV(valeur + 1)} aria-label={C.plus}><Plus size={11} /></button>
      </div>
      {v !== null && v !== p.valeur && (
        <form className="mt-1.5 flex gap-1.5" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <input className={champ} placeholder={C.motif} value={motif} onChange={(e) => setMotif(e.target.value)} aria-label={C.motif} />
          <button type="submit" className={bouton} disabled={motif.trim().length < 3 || m.isPending}>{C.appliquer}</button>
        </form>
      )}
      {m.error && <p className="text-[11.5px] text-gap">{m.error.message}</p>}
    </div>
  );
}

/** Jauge du budget Sentinel du mois : dépensé (plein), réservé (atténué), restant */
function Jauge({ b }: { b: EtatControle["budget"] }) {
  const { plafond } = b.budget_sentinel;
  const dep = b.sources.sentinel1.depense + b.sources.sentinel2.depense;
  const res = b.sources.sentinel1.reserve + b.sources.sentinel2.reserve;
  const pct = (x: number) => `${Math.min(100, (100 * x) / Math.max(plafond, 0.01))}%`;
  const alerte = dep + res >= 0.8 * plafond;
  return (
    <div>
      <div className="mb-1 flex justify-between text-[11.5px] text-muted">
        <span>{C.budget}</span><span className="tabular-nums text-ink">{euros(dep + res)} / {euros(plafond)}</span>
      </div>
      <div className="flex h-1.5 overflow-hidden rounded bg-raised">
        <div style={{ width: pct(dep) }} className={alerte ? "bg-rdv" : "bg-signal"} />
        <div style={{ width: pct(res) }} className={alerte ? "bg-rdv/40" : "bg-signal/40"} />
      </div>
      <table className="mt-2 w-full text-[11.5px] tabular-nums">
        <thead><tr className="text-faint"><td /><td className="text-right">{C.depense}</td><td className="text-right">{C.reserve}</td></tr></thead>
        <tbody>
          {(["sentinel1", "sentinel2", "viirs"] as const).map((s) => (
            <tr key={s} className="text-muted">
              <td>{{ sentinel1: "Sentinel 1", sentinel2: "Sentinel 2", viirs: `VIIRS (${C.hors})` }[s]}</td>
              <td className="text-right text-ink">{euros(b.sources[s].depense)}</td>
              <td className="text-right">{euros(b.sources[s].reserve)}</td>
            </tr>
          ))}
          <tr className="text-muted"><td>{C.restant}</td><td className="text-right text-ink">{euros(b.budget_sentinel.restant)}</td><td /></tr>
        </tbody>
      </table>
    </div>
  );
}

function Demande({ actif, rayon }: { actif: boolean; rayon: EtatControle["rayon_demande_m"] }) {
  const qc = useQueryClient();
  const [lon, setLon] = useState(""), [lat, setLat] = useState(""), [r, setR] = useState(String(rayon?.defaut ?? 5000));
  const [motif, setMotif] = useState("");
  const m = useMutation({
    mutationFn: () => api.controle.demande({ lon: Number(lon), lat: Number(lat), rayon_m: Number(r), motif }),
    onSuccess: () => { setMotif(""); qc.invalidateQueries({ queryKey: ["controle"] }); qc.invalidateQueries({ queryKey: ["observations"] }); },
  });
  const ok = actif && lon !== "" && lat !== "" && motif.trim().length >= 3;
  return (
    <form className="space-y-1.5" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
      <div className="grid grid-cols-3 gap-1.5">
        <input className={champ} placeholder={C.lat} inputMode="decimal" value={lat} onChange={(e) => setLat(e.target.value)} aria-label={C.lat} />
        <input className={champ} placeholder={C.lon} inputMode="decimal" value={lon} onChange={(e) => setLon(e.target.value)} aria-label={C.lon} />
        <input className={champ} placeholder={C.rayon} inputMode="numeric" value={r} onChange={(e) => setR(e.target.value)} aria-label={C.rayon}
          title={rayon ? `${rayon.min} à ${rayon.max} m` : undefined} />
      </div>
      <div className="flex gap-1.5">
        <input className={champ} placeholder={C.motif} value={motif} onChange={(e) => setMotif(e.target.value)} aria-label={C.motif} />
        <button type="submit" className={bouton} disabled={!ok || m.isPending}>{C.demander}</button>
      </div>
      {m.error && <p className="text-[11.5px] text-gap">{m.error.message}</p>}
      {m.data && <p className="text-[11.5px] text-muted">{C.demandeFaite(m.data.id)}</p>}
    </form>
  );
}

function Journal({ lignes }: { lignes: Props[] }) {
  if (!lignes.length) return <p className="text-[12px] text-muted">{C.journalVide}</p>;
  return (
    <ul className="space-y-1 text-[11.5px] leading-snug">
      {lignes.map((j) => (
        <li key={j.id} className="text-muted">
          <span className="tabular-nums text-faint">{jourHeure(j.le)}</span>{" "}
          <span className="text-ink">{j.auteur}</span> {C.actions[j.action] ?? j.action}
          {j.cible && <> · {j.cible}</>}{j.motif && <> · {j.motif}</>}
        </li>
      ))}
    </ul>
  );
}

/** Panneau de contrôle (lot P) : lecture pour tous, écriture après connexion d'un opérateur. Les sections suivantes
 *  (système, règles, notifications, zones de vigilance, démonstration) s'ajoutent à la liste. */
export default function ControlePanel() {
  const sessionQ = useQuery({ queryKey: ["controle", "session"], queryFn: api.controle.session });
  const etatQ = useQuery({ queryKey: ["controle", "etat"], queryFn: api.controle.etat, refetchInterval: 30_000 });
  const journalQ = useQuery({ queryKey: ["controle", "journal"], queryFn: () => api.controle.journal(30), refetchInterval: 30_000 });
  const op = sessionQ.data?.operateur ?? null;
  const actif = op != null;
  const e = etatQ.data;
  const pauses = e?.interrupteurs.filter((i) => i.en_pause).length ?? 0;
  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-hair px-4 pb-3 pt-4">
        <h2 className="flex items-baseline justify-between text-[15px] font-semibold">
          {C.titre}{e && <span className="text-[11.5px] font-normal text-faint">{C.regles(e.version_regles)}</span>}
        </h2>
      </header>
      <div className="flex-1 overflow-y-auto">
        <Section id="connexion" titre={C.sections.connexion}
          droite={op ? <span className="normal-case text-ink">{op}</span> : null}>
          <Connexion operateur={op} configure={sessionQ.data?.configure ?? true} />
        </Section>
        {e && (
          <>
            <Section id="capteurs" titre={C.sections.capteurs}
              droite={pauses ? <span className="normal-case text-rdv">{pauses} {C.enPause}</span> : null}>
              {!actif && <p className="mb-1 text-[11.5px] text-faint">{C.connexionRequise}</p>}
              <ul>{e.interrupteurs.map((i) => <Source key={i.source} i={i} actif={actif} />)}</ul>
              <div className="mt-2 border-t border-hair/60 pt-2">
                <p className="mb-0.5 text-[11.5px] text-muted">{C.plafond}</p>
                {Object.entries(e.plafonds).map(([s, p]) => (
                  <Plafond key={s} source={s} nom={s === "sentinel1" ? "Sentinel 1" : "Sentinel 2"} p={p} actif={actif} />
                ))}
              </div>
            </Section>
            <Section id="depenses" titre={C.sections.depenses}>
              <Jauge b={e.budget} />
              <p className="mb-1 mt-3 text-[11.5px] text-muted">{C.derniers}</p>
              <ul className="space-y-0.5 text-[11.5px] tabular-nums">
                {e.travailleurs.map((t) => (
                  <li key={t.id} className="flex gap-2 text-muted">
                    <span className="w-8 text-faint">{t.id}</span>
                    <span className="flex-1">{t.tache} · {jourHeure(t.cree_le)}</span>
                    <span className="text-ink">{t.cout_reel_eur != null ? euros(t.cout_reel_eur)
                      : t.cout_estime_eur != null ? `${euros(t.cout_estime_eur)} ${C.coutEstime}` : "·"}</span>
                  </li>
                ))}
              </ul>
            </Section>
            <Section id="demande" titre={C.sections.demande}>
              <Demande actif={actif} rayon={e.rayon_demande_m} />
            </Section>
          </>
        )}
        <Section id="journal" titre={C.sections.journal}>
          <Journal lignes={journalQ.data ?? []} />
        </Section>
      </div>
    </div>
  );
}
