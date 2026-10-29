import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, ExternalLink, LogOut, Minus, Plus } from "lucide-react";
import { createContext, type CSSProperties, type ReactNode, useContext, useEffect, useRef, useState } from "react";
import { choisirTheme, PREFERENCES, type Preference, useTheme } from "../lib/theme";
import { COULEUR_NIVEAU } from "../registres/etat";
import { api, type PrefsNotification, type BlocRegle, type Echeance, type EtatControle, type Interrupteur, type SystemeControle } from "../lib/api";
import { jourHeure } from "../lib/format";
import { L } from "../lib/libelles";
import type { Feature, Props } from "../lib/types";
import { libelleAlerte } from "../registres/alertes";
import type { VueCarte } from "../lib/preferences";
import { ListeIndicateurs } from "./BarreEtat";

const C = L.controle;
const euros = (v: number) => `${v.toFixed(2).replace(".", ",")} €`;

// Réglage visé par la recherche des Coulisses : sa partie s'ouvre, vient à l'écran et se signale un instant
export const Visee = createContext<string | null>(null);

/** Partie repliable d'une rubrique des Coulisses ; l'état ouvert est gardé dans le navigateur, la plus utile est
 *  ouverte par défaut */
export function Partie({ id, titre, ouverte: defaut = false, droite, children }:
  { id: string; titre: string; ouverte?: boolean; droite?: ReactNode; children: ReactNode }) {
  const cle = `mars.partie.${id}`;
  const [ouverte, setOuverte] = useState(() => {
    try { const v = localStorage.getItem(cle); return v == null ? defaut : v === "1"; } catch { return defaut; }
  });
  const visee = useContext(Visee) === id;
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!visee) return;
    setOuverte(true);
    ref.current?.scrollIntoView({ block: "nearest" });
  }, [visee]);
  const basculer = () => {
    setOuverte(!ouverte);
    try { localStorage.setItem(cle, ouverte ? "0" : "1"); } catch { /* stockage indisponible */ }
  };
  return (
    <section ref={ref} data-partie={id} className={`border-b border-hair last:border-0 ${visee ? "bg-signal/5" : ""}`}>
      <button onClick={basculer} aria-expanded={ouverte}
        className="flex w-full items-center gap-1.5 py-2.5 text-left text-[12px] font-semibold uppercase tracking-wide text-muted hover:text-ink">
        <ChevronRight size={13} className={`transition-transform ${ouverte ? "rotate-90" : ""}`} />
        <span className="flex-1">{titre}</span>
        {droite}
      </button>
      {ouverte && <div className="pb-3">{children}</div>}
    </section>
  );
}

const champ = "w-full rounded border border-hair bg-abyss px-2 py-1 text-[12.5px] text-ink placeholder:text-faint focus:border-signal";
const bouton = "rounded border border-hair px-2 py-0.5 text-[12px] text-ink hover:border-signal disabled:opacity-40";

function Connexion({ operateur, configure, abimes }: { operateur: string | null; configure: boolean; abimes: string[] }) {
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
  // Ligne MARS_OPERATEURS abîmée (empreinte tronquée ou suivie d'un suffixe) : dite telle quelle, sans empreinte
  const alerte = abimes.length > 0 && <p className="mb-1.5 text-[11.5px] text-gap">{abimes.join(" ; ")}</p>;
  if (!configure) return <>{alerte}<p className="text-[12px] text-muted">{C.nonConfigure}</p></>;
  return (
    <>{alerte}
    <form className="space-y-1.5" onSubmit={(e) => { e.preventDefault(); entrer.mutate(); }}>
      <input className={champ} placeholder={C.operateur} value={nom} onChange={(e) => setNom(e.target.value)}
        autoComplete="username" aria-label={C.operateur} />
      <input className={champ} placeholder={C.motDePasse} type="password" value={mdp} onChange={(e) => setMdp(e.target.value)}
        autoComplete="current-password" aria-label={C.motDePasse} />
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11.5px] text-gap">{entrer.error?.message}</span>
        <button type="submit" className={bouton} disabled={!nom || !mdp || entrer.isPending}>{C.seConnecter}</button>
      </div>
    </form></>
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
        <span className="flex-1 text-[12.5px] text-ink">{C.sources[i.source] ?? i.nom}</span>
        <span className={`text-[11.5px] ${i.en_pause ? "text-rdv" : "text-muted"}`}>{i.en_pause ? C.enPause : C.enService}</span>
        <button className={bouton} disabled={!actif || m.isPending} data-reglage={`pause-${i.source}`}
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

const Y = C.systeme;
const MUET_S = 600;                     // un signe de vie plus vieux que 10 minutes demande l'attention

function Lien({ href, titre }: { href: string | null; titre: string }) {
  if (!href) return null;
  return (
    <a href={href} target="_blank" rel="noreferrer" title={titre} aria-label={titre} className="text-faint hover:text-signal">
      <ExternalLink size={11} className="inline" />
    </a>
  );
}

/** Échéance d'un secret en clair : date et délai, « expirée », « non déclarée » ou « date illisible » ; couleur
 *  seulement à 14 jours (orange) puis 3 jours et au delà (rouge). */
export function quandEcheance(x: Echeance): { texte: string; couleur: string | null } {
  if (x.illisible) return { texte: Y.illisible(x.variable), couleur: COULEUR_NIVEAU.orange };
  if (!x.expire_le || x.jours == null) return { texte: Y.nonDeclaree(x.variable), couleur: null };
  const date = x.expire_le.slice(0, 10).split("-").reverse().join("/");
  if (x.jours < 0) return { texte: `${Y.expiree} (${date})`, couleur: COULEUR_NIVEAU.rouge };
  return { texte: `${date}, ${Y.dans(x.jours)}`,
    couleur: x.jours <= 3 ? COULEUR_NIVEAU.rouge : x.jours <= 14 ? COULEUR_NIVEAU.orange : null };
}

function Echeances({ e }: { e: Echeance[] }) {
  if (!e.length) return <p className="text-[12px] text-muted">{Y.aucuneEcheance}</p>;
  return (
    <ul className="space-y-0.5 text-[11.5px]">
      {e.map((x) => {
        const q = quandEcheance(x);
        return (
          <li key={x.cle} className="flex justify-between gap-2 text-muted">
            <span className="text-ink">{x.libelle}</span>
            <span className={`tabular-nums ${q.couleur ? "teinte" : x.expire_le ? "text-muted" : "text-faint"}`}
              style={q.couleur ? { "--teinte": q.couleur } as CSSProperties : undefined}>{q.texte}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** État du système en lecture seule (phase B) : services, version, signe de vie, mémoire, retard, disque, échecs */
function Systeme({ s }: { s: SystemeControle }) {
  const d = s.disque;
  return (
    <div className="space-y-2 text-[11.5px]">
      <table className="w-full tabular-nums">
        <tbody>
          {s.services.map((x) => {
            const muet = x.age_s != null && x.age_s > MUET_S && x.service !== "statistiques";
            return (
              <tr key={x.service} className="text-muted">
                <td className="py-0.5 text-ink">{Y.services[x.service] ?? x.service}</td>
                <td className="font-mono text-[10.5px] text-faint">{x.version?.slice(0, 7) ?? "·"}</td>
                <td className={`text-right ${muet ? "text-gap" : ""}`}>{x.age_s != null ? Y.il_y_a(x.age_s) : "·"}</td>
                <td className="text-right">{x.memoire_mo != null ? Y.memoire(x.memoire_mo, x.memoire_max_mo) : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="flex justify-between text-muted">
        <span>{Y.retard}</span>
        <span className={`tabular-nums ${(s.retard_positions_s ?? 1e9) > MUET_S ? "text-gap" : "text-ink"}`}>
          {s.retard_positions_s != null ? Y.il_y_a(s.retard_positions_s) : "·"}
        </span>
      </div>
      {d && (
        <div className="flex justify-between text-muted">
          <span>{Y.disque}</span>
          <span className={`tabular-nums ${d.libre_pct < 15 ? "text-gap" : "text-ink"}`}>{Y.libres(d.libre_pct, d.libre_octets / 1e9)}</span>
        </div>
      )}
      <div>
        <p className="mb-0.5 text-muted">{Y.echecs}</p>
        {!s.echecs.length && <p className="text-faint">{Y.aucunEchec}</p>}
        <ul className="space-y-0.5">
          {s.echecs.map((e) => (
            <li key={e.id} className="text-muted" title={e.erreur ?? undefined}>
              <span className="tabular-nums text-faint">{jourHeure(e.le)}</span> <span className="text-gap">{e.tache}</span>{" "}
              <span className="line-clamp-1 inline">{e.erreur}</span> <Lien href={e.lien_trace} titre={Y.trace} />
            </li>
          ))}
        </ul>
      </div>
      <a href={s.grafana} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-signal hover:underline">
        {Y.grafana} <ExternalLink size={11} />
      </a>
    </div>
  );
}

const R = C.regle;
const MODES = ["actif", "observation", "coupe"] as const;
const COULEUR_MODE: Record<string, string> = { actif: "text-ink", observation: "text-unconf", coupe: "text-gap" };

/** Sélecteur de mode d'une règle ou d'un sous type : le choix ouvre un motif obligatoire, puis la confirmation */
function ModeRegle({ regle, sousType, nom, b, actif }: { regle: string; sousType?: string; nom: string; b: BlocRegle; actif: boolean }) {
  const qc = useQueryClient();
  const [cible, setCible] = useState<string | null>(null), [motif, setMotif] = useState("");
  const m = useMutation({
    mutationFn: () => api.controle.mode(regle, { mode: cible!, sous_type: sousType ?? null, motif }),
    onSuccess: () => { setCible(null); setMotif(""); qc.invalidateQueries({ queryKey: ["controle"] }); },
  });
  return (
    <div className="py-1">
      <div className="flex items-center gap-2">
        <span className={`flex-1 truncate text-[12.5px] ${sousType ? "pl-3 text-muted" : "text-ink"}`} title={R.fichier(b.mode_fichier)}>{nom}</span>
        <div className="flex overflow-hidden rounded border border-hair" role="group" aria-label={nom} data-reglage="mode-regle">
          {MODES.map((x) => (
            <button key={x} disabled={!actif || m.isPending} aria-pressed={b.mode === x}
              onClick={() => x !== b.mode && setCible(x)}
              className={`px-1.5 py-0.5 text-[11px] ${b.mode === x ? `bg-raised ${COULEUR_MODE[x]}` : "text-faint hover:text-muted"} disabled:cursor-default`}>
              {R.modes[x]}
            </button>
          ))}
        </div>
      </div>
      {/* Le compte de la semaine seulement s'il n'est pas nul : la liste des modes tient sans défiler */}
      {(b.alertes_7j || b.episodes_7j) ? <p className={`text-[11px] text-faint ${sousType ? "pl-3" : ""}`}>{R.compte(b.alertes_7j, b.episodes_7j)}</p> : null}
      {cible && (
        <form className="mt-1 flex gap-1.5" onSubmit={(e) => { e.preventDefault(); m.mutate(); }}>
          <input className={champ} placeholder={R.changer(nom, R.modes[cible])} value={motif}
            onChange={(e) => setMotif(e.target.value)} aria-label={C.motif} autoFocus />
          <button type="submit" className={bouton} disabled={motif.trim().length < 3 || m.isPending}>{C.confirmer}</button>
          <button type="button" className="text-[12px] text-muted hover:text-ink" onClick={() => setCible(null)}>{C.annuler}</button>
        </form>
      )}
      {m.error && <p className="text-[11.5px] text-gap">{m.error.message}</p>}
    </div>
  );
}

const N = C.notif;

function Bascule({ on, onChange, actif, label }: { on: boolean; onChange: (v: boolean) => void; actif: boolean; label: string }) {
  return (
    <label className={`flex items-center gap-2 py-0.5 text-[12px] ${actif ? "cursor-pointer" : ""}`}>
      <input type="checkbox" checked={on} disabled={!actif} onChange={(e) => onChange(e.target.checked)}
        className="accent-[var(--color-signal)]" />
      <span className={on ? "text-ink" : "text-muted"}>{label}</span>
    </label>
  );
}

/** Préférences de notification (phase D) : gravité et types d'alerte, tip and cue, catégories système, heures calmes */
function Notifications({ actif }: { actif: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["controle", "notifications"], queryFn: api.controle.notifications });
  const [p, setP] = useState<PrefsNotification | null>(null);
  const prefs = p ?? q.data?.preferences ?? null;
  const save = useMutation({ mutationFn: () => api.controle.reglerNotifications(prefs!),
    onSuccess: () => { setP(null); qc.invalidateQueries({ queryKey: ["controle"] }); } });
  const test = useMutation({ mutationFn: api.controle.notificationTest,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["controle", "journal"] }) });
  if (!prefs || !q.data) return null;
  const maj = (f: (x: PrefsNotification) => void) => { const x = structuredClone(prefs); f(x); setP(x); };
  return (
    <div className="text-[12px]">
      <Connecter actif={actif} />
      <Partie id="notif-alertes" titre={N.alertes} ouverte>
        <div className="flex items-center justify-between">
          <span className="text-muted">{N.gravite}</span>
          <select disabled={!actif} value={prefs.alertes.gravite_min} aria-label={N.gravite}
            onChange={(e) => maj((x) => { x.alertes.gravite_min = e.target.value; })}
            className="rounded border border-hair bg-abyss px-1 py-0.5 text-[11.5px] text-ink">
            {q.data.gravites.map((g) => <option key={g} value={g}>{N.gravite} {L.gravite[g] ?? g}</option>)}
          </select>
        </div>
        <div className="mt-1 flex flex-wrap gap-1">
          {Object.entries(prefs.alertes.types).map(([t, on]) => (
            <button key={t} disabled={!actif} aria-pressed={on} onClick={() => maj((x) => { x.alertes.types[t] = !on; })}
              className={`rounded-full border px-2 py-0.5 text-[11px] ${on ? "border-hair bg-raised text-ink" : "border-transparent text-faint"}`}>
              {libelleAlerte(t)}
            </button>
          ))}
        </div>
      </Partie>
      <Partie id="notif-systeme" titre={N.systemeTitre}>
        <Bascule on={prefs.tip_and_cue} actif={actif} label={N.tip} onChange={(v) => maj((x) => { x.tip_and_cue = v; })} />
        <p className="mt-1 text-muted">{N.systeme}</p>
        <div className="grid grid-cols-2">
          {q.data.categories.map((c) => (
            <Bascule key={c} on={prefs.categories[c] ?? true} actif={actif} label={N.categories[c] ?? c}
              onChange={(v) => maj((x) => { x.categories[c] = v; })} />
          ))}
        </div>
      </Partie>
      <Partie id="notif-calmes" titre={N.calmes}>
        <div className="flex items-center gap-2" data-reglage="heures-calmes">
          <Bascule on={prefs.heures_calmes.actif} actif={actif} label={N.calmes}
            onChange={(v) => maj((x) => { x.heures_calmes.actif = v; })} />
          {q.data.heures_calmes_maintenant && <span className="text-[11px] text-signal">{N.maintenant}</span>}
        </div>
        <div className="flex items-center gap-1.5 pl-5">
          {(["debut", "fin"] as const).map((k) => (
            <input key={k} type="time" disabled={!actif} value={prefs.heures_calmes[k]} aria-label={`${N.calmes} ${k}`}
              onChange={(e) => maj((x) => { x.heures_calmes[k] = e.target.value; })}
              className="rounded border border-hair bg-abyss px-1 py-0.5 text-[11.5px] text-ink" />
          ))}
        </div>
        <p className="pl-5 text-[11px] text-faint">{N.calmesNote}{q.data.retenues ? `, ${N.retenues(q.data.retenues)}` : ""}</p>
      </Partie>
      {!q.data.ntfy && <p className="mt-2 text-[11px] text-gap">{N.sansNtfy}</p>}
      <div className="mt-2 flex items-center justify-between gap-2">
        <button className={bouton} disabled={!actif || test.isPending} onClick={() => test.mutate()}>{N.test}</button>
        <button className={bouton} disabled={!actif || !p || save.isPending} onClick={() => save.mutate()}>{N.enregistrer}</button>
      </div>
      {(save.error || test.error) && <p className="mt-1 text-[11.5px] text-gap">{(save.error ?? test.error)?.message}</p>}
      {test.data && <p className="text-[11.5px] text-muted">{N.envoyee}</p>}
    </div>
  );
}

const Z = C.zones;

/** Zones de vigilance (phase E) : liste des zones actives, création (polygone dessiné ou tampon autour de
 *  l'infrastructure sélectionnée, profil du fichier), retrait avec motif, priorité aux navires suivis */
function Zones({ actif, trace, onTrace, infra, onPickZone }: PanneauProps & { actif: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["controle", "zones"], queryFn: () => api.controle.zones() });
  const [ouvert, setOuvert] = useState(false);
  const [source, setSource] = useState<"dessin" | "infra">("dessin");
  const [nom, setNom] = useState(""), [motif, setMotif] = useState(""), [profil, setProfil] = useState("");
  const [fin, setFin] = useState(""), [tampon, setTampon] = useState("");
  const [retrait, setRetrait] = useState<number | null>(null), [motifRetrait, setMotifRetrait] = useState("");
  const [motifSuivis, setMotifSuivis] = useState("");
  const fini = () => { qc.invalidateQueries({ queryKey: ["controle"] }); };
  const creer = useMutation({
    mutationFn: () => api.controle.creerZone({ nom, motif, profil: profil || Object.keys(q.data!.profils)[0],
      fin_le: fin ? new Date(fin).toISOString() : null,
      ...(source === "dessin" ? { geometrie: { type: "Polygon", coordinates: [[...trace!, trace![0]]] } }
        : { infrastructure_id: infra!.id, tampon_m: tampon ? Number(tampon) : undefined }) }),
    onSuccess: () => { setOuvert(false); setNom(""); setMotif(""); setFin(""); onTrace(null); fini(); },
  });
  const retirer = useMutation({ mutationFn: (id: number) => api.controle.retirerZone(id, motifRetrait),
    onSuccess: () => { setRetrait(null); setMotifRetrait(""); fini(); } });
  const suivis = useMutation({ mutationFn: (v: boolean) => api.controle.suivis(v, motifSuivis),
    onSuccess: () => { setMotifSuivis(""); fini(); } });
  if (!q.data) return null;
  const profils = q.data.profils;
  const pret = actif && nom.trim().length >= 2 && motif.trim().length >= 3
    && (source === "dessin" ? (trace?.length ?? 0) >= 3 : infra != null);
  return (
    <div className="space-y-2 text-[12px]">
      {!q.data.features.length && <p className="text-muted">{Z.aucune}</p>}
      <ul className="space-y-1">
        {q.data.features.map((f) => {
          const z = f.properties;
          return (
            <li key={z.id}>
              <div className="flex items-center gap-2">
                <button className="min-w-0 flex-1 truncate text-left text-ink hover:text-signal" onClick={() => onPickZone(z)}>{z.nom}</button>
                <span className="text-[11px] text-muted">{z.profil_nom}</span>
                <button className="text-[11px] text-faint hover:text-gap disabled:opacity-40" disabled={!actif}
                  onClick={() => setRetrait(retrait === z.id ? null : z.id)}>{Z.retirer}</button>
              </div>
              {z.fin_le && <p className="text-[11px] text-faint">{Z.jusqua(jourHeure(z.fin_le))}</p>}
              {retrait === z.id && (
                <form className="mt-1 flex gap-1.5" onSubmit={(e) => { e.preventDefault(); retirer.mutate(z.id); }}>
                  <input className={champ} placeholder={C.motif} value={motifRetrait} onChange={(e) => setMotifRetrait(e.target.value)} aria-label={C.motif} autoFocus />
                  <button type="submit" className={bouton} disabled={motifRetrait.trim().length < 3}>{C.confirmer}</button>
                </form>
              )}
            </li>
          );
        })}
      </ul>
      {!ouvert ? (
        <button className={bouton} disabled={!actif} onClick={() => setOuvert(true)}>{Z.nouvelle}</button>
      ) : (
        <form className="space-y-1.5 rounded border border-hair/60 p-2" onSubmit={(e) => { e.preventDefault(); creer.mutate(); }}>
          <input className={champ} placeholder={Z.nom} value={nom} onChange={(e) => setNom(e.target.value)} aria-label={Z.nom} />
          <input className={champ} placeholder={C.motif} value={motif} onChange={(e) => setMotif(e.target.value)} aria-label={C.motif} />
          <select className={champ} value={profil || Object.keys(profils)[0]} onChange={(e) => setProfil(e.target.value)} aria-label={Z.profil}>
            {Object.entries(profils).map(([k, p]) => <option key={k} value={k}>{p.nom}</option>)}
          </select>
          <label className="block text-[11.5px] text-muted">{Z.fin}
            <input className={`${champ} mt-0.5`} type="datetime-local" value={fin} onChange={(e) => setFin(e.target.value)} />
          </label>
          <div className="flex gap-1" role="group">
            {(["dessin", "infra"] as const).map((s) => (
              <button type="button" key={s} aria-pressed={source === s} onClick={() => setSource(s)}
                className={`rounded-full border px-2 py-0.5 text-[11px] ${source === s ? "border-hair bg-raised text-ink" : "border-transparent text-faint"}`}>
                {s === "dessin" ? Z.sourceDessin : Z.sourceInfra}
              </button>
            ))}
          </div>
          {source === "dessin" ? (
            <div className="flex items-center gap-2">
              <button type="button" className={bouton} onClick={() => onTrace(trace ? null : [])}>{trace ? Z.effacer : Z.dessiner}</button>
              {trace && <span className="text-[11px] text-muted">{Z.sommets(trace.length)}</span>}
            </div>
          ) : infra ? (
            <div className="flex items-center gap-2">
              <span className="flex-1 truncate text-[11.5px] text-ink">{Z.tampon(infra.nom ?? `n° ${infra.id}`)}</span>
              <input className={`${champ} w-20`} placeholder={String(q.data.tampon_m?.defaut ?? 3704)} inputMode="numeric"
                value={tampon} onChange={(e) => setTampon(e.target.value)} aria-label={Z.tamponM} />
            </div>
          ) : <p className="text-[11px] text-faint">{Z.choisirInfra}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" className="text-[12px] text-muted hover:text-ink" onClick={() => { setOuvert(false); onTrace(null); }}>{C.annuler}</button>
            <button type="submit" className={bouton} disabled={!pret || creer.isPending}>{Z.creer}</button>
          </div>
          {creer.error && <p className="text-[11.5px] text-gap">{creer.error.message}</p>}
        </form>
      )}
      {retirer.error && <p className="text-[11.5px] text-gap">{retirer.error.message}</p>}
      <div className="border-t border-hair/60 pt-2">
        <Bascule on={q.data.suivis_prioritaires} actif={actif && motifSuivis.trim().length >= 3} label={Z.suivis}
          onChange={(v) => suivis.mutate(v)} />
        {actif && <input className={`${champ} mt-1`} placeholder={C.motif} value={motifSuivis} onChange={(e) => setMotifSuivis(e.target.value)} aria-label={C.motif} />}
        {suivis.error && <p className="text-[11.5px] text-gap">{suivis.error.message}</p>}
      </div>
    </div>
  );
}

const M = C.demo;

/** Démonstration (phase F) : masquage des navires (cookie de session, l'API réécrit ses réponses), rejeu tel qu'il
 *  était (instant de l'adresse, lecture seule), scénarios enregistrés rouverts d'un clic */
function Demonstration({ actif, masquage, rejeuFidele, onRejeuFidele }:
  { actif: boolean; masquage: boolean; rejeuFidele: boolean; onRejeuFidele: (v: boolean) => void }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["controle", "scenarios"], queryFn: api.controle.scenarios });
  const [nom, setNom] = useState("");
  const masquer = useMutation({ mutationFn: (v: boolean) => api.controle.masquage(v),
    onSuccess: () => window.location.reload() });           // tout est relu, flux compris, sous le nouveau masque
  const enregistrer = useMutation({ mutationFn: () => api.controle.enregistrerScenario(nom, window.location.search),
    onSuccess: () => { setNom(""); qc.invalidateQueries({ queryKey: ["controle"] }); } });
  const retirer = useMutation({ mutationFn: (id: number) => api.controle.retirerScenario(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["controle"] }) });
  return (
    <div className="space-y-2 text-[12px]">
      <div>
        <Bascule on={masquage} actif={!masquer.isPending} label={M.masquage} onChange={(v) => masquer.mutate(v)} />
        <p className="pl-5 text-[11px] text-faint">{M.masquageNote}</p>
      </div>
      <div>
        <Bascule on={rejeuFidele} actif label={M.rejeu} onChange={onRejeuFidele} />
        <p className="pl-5 text-[11px] text-faint">{M.rejeuNote}</p>
      </div>
      <div className="border-t border-hair/60 pt-2">
        <p className="mb-1 text-muted">{M.scenarios}</p>
        {!q.data?.length && <p className="text-faint">{M.aucun}</p>}
        <ul className="space-y-0.5">
          {(q.data ?? []).map((s) => (
            <li key={s.id} className="flex items-center gap-2">
              <button className="min-w-0 flex-1 truncate text-left text-ink hover:text-signal"
                title={s.debut ? `${jourHeure(s.debut)} · ${s.region ?? ""}` : undefined}
                onClick={() => window.location.assign(`${window.location.pathname}?${s.adresse}`)}>{s.nom}</button>
              <button className="text-[11px] text-faint hover:text-gap disabled:opacity-40" disabled={!actif}
                onClick={() => retirer.mutate(s.id)}>{M.retirer}</button>
            </li>
          ))}
        </ul>
        <form className="mt-1.5 flex gap-1.5" onSubmit={(e) => { e.preventDefault(); enregistrer.mutate(); }}>
          <input className={champ} placeholder={M.nom} value={nom} onChange={(e) => setNom(e.target.value)} aria-label={M.nom} />
          <button type="submit" className={bouton} disabled={!actif || nom.trim().length < 2 || enregistrer.isPending}>{M.enregistrer}</button>
        </form>
        {(enregistrer.error || masquer.error) && <p className="text-[11.5px] text-gap">{(enregistrer.error ?? masquer.error)?.message}</p>}
      </div>
    </div>
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

interface PanneauProps {
  trace: number[][] | null;                       // polygone de zone en cours de tracé sur la carte
  onTrace: (t: number[][] | null) => void;
  infra: { id: number; nom: string | null } | null;   // infrastructure sélectionnée (tampon)
  onPickZone: (z: Props) => void;
  masquage: boolean;
  rejeuFidele: boolean;
  onRejeuFidele: (v: boolean) => void;
}

// Rubriques des Coulisses (retours du lot U) : le contenu de l'ancien panneau Contrôle, réparti ; lecture pour tous,
// écriture après connexion d'un opérateur, journalisée par l'API comme avant
const useSession = () => useQuery({ queryKey: ["controle", "session"], queryFn: api.controle.session });
const useEtatControle = () => useQuery({ queryKey: ["controle", "etat"], queryFn: api.controle.etat, refetchInterval: 30_000 });
const Connecter = ({ actif }: { actif: boolean }) => (actif ? null : <p className="mb-1 text-[11.5px] text-faint">{C.connexionRequise}</p>);

export function RubriqueCompte() {
  const q = useSession();
  return <Connexion operateur={q.data?.operateur ?? null} configure={q.data?.configure ?? true} abimes={q.data?.abimes ?? []} />;
}

/** Sources et capteurs : ce que MARS C2 regarde. Source AIS et listes en lecture seule, interrupteurs des capteurs
 *  satellites et de la vérification satellite, plafond de scènes par jour */
export function RubriqueSources({ status, now }: { status: unknown; now: number }) {
  const actif = useSession().data?.operateur != null;
  const e = useEtatControle().data;
  const pauses = e?.interrupteurs.filter((i) => i.en_pause).length ?? 0;
  return (
    <>
      <Partie id="src-donnees" titre={C.sections.donnees} ouverte>
        <ListeIndicateurs status={status} now={now} ids={["flux", "listes"]} />
      </Partie>
      {e && <>
        <Partie id="capteurs" titre={C.sections.capteurs} ouverte
          droite={pauses ? <span className="normal-case text-rdv">{pauses} {C.enPause}</span> : null}>
          <Connecter actif={actif} />
          <ul>{e.interrupteurs.map((i) => <Source key={i.source} i={i} actif={actif} />)}</ul>
        </Partie>
        <Partie id="plafonds" titre={C.plafond}>
          {Object.entries(e.plafonds).map(([s, p]) => (
            <Plafond key={s} source={s} nom={C.sources[s] ?? s} p={p} actif={actif} />
          ))}
        </Partie>
      </>}
    </>
  );
}

/** Coût par résultat (lot Z6) : dépense de chaque source (échecs compris) rapportée à ses résultats utiles */
function CoutParResultat({ c }: { c: EtatControle["cout_par_resultat"] }) {
  const R = C.coutResultat;
  const [periode, setPeriode] = useState<"mois" | "total">("total");
  return (
    <div className="mt-4">
      <div className="mb-1 flex items-center justify-between text-[11.5px] text-muted">
        <span>{R.titre}</span>
        <span className="flex gap-1" role="group">
          {(["mois", "total"] as const).map((p) => (
            <button key={p} onClick={() => setPeriode(p)} aria-pressed={p === periode}
              className={`rounded px-1.5 ${p === periode ? "bg-raised text-ink" : "text-faint hover:text-ink"}`}>{R[p]}</button>
          ))}
        </span>
      </div>
      <table className="w-full text-[11.5px] tabular-nums" data-cout-resultat>
        <tbody>
          {(["viirs", "sentinel1", "sentinel2", "tip_and_cue"] as const).map((s) => {
            const x = c[periode][s];
            if (!x) return null;
            return (
              <tr key={s} className="align-top text-muted" title={s === "tip_and_cue" ? R.inclus : undefined}>
                <td className="py-0.5">{R.sources[s]}</td>
                <td className="text-right">{x.resultats} {R.unites[x.unite] ?? R.resultats}</td>
                <td className="text-right text-ink">{x.cout_par_resultat != null ? euros(x.cout_par_resultat) : R.aucun}</td>
                <td className="pl-2 text-right text-faint">{x.depense_echecs > 0 ? R.echecs(euros(x.depense_echecs)) : ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Budget : dépenses du mois par source et budget mensuel, coût par résultat, coût des derniers travailleurs */
export function RubriqueBudget() {
  const e = useEtatControle().data;
  if (!e) return <p className="text-muted">{L.commun.chargement}</p>;
  return (
    <>
      <Jauge b={e.budget} />
      {e.cout_par_resultat && <CoutParResultat c={e.cout_par_resultat} />}
      <p className="mb-1 mt-4 text-[11.5px] text-muted">{C.derniers}</p>
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
    </>
  );
}

/** Affichage et démonstration : réglages de ce navigateur seulement (vue par défaut, vignettes du fil, masquage,
 *  rejeu tel qu'il était) ; les scénarios enregistrés sont communs (cas signalé dans docs/ux_refonte.md) */
export function RubriqueAffichage(pp: Pick<PanneauProps, "masquage" | "rejeuFidele" | "onRejeuFidele"> & {
  vueDefaut: VueCarte; onVueDefaut: (v: VueCarte) => void; vignettes: boolean; onVignettes: (v: boolean) => void }) {
  const actif = useSession().data?.operateur != null;
  const { preference } = useTheme();
  const A = C.affichage;
  return (
    <>
      <Partie id="aff-carte" titre={A.carte} ouverte>
        <label className="flex items-center justify-between gap-2 py-0.5 text-[12px]">
          <span className="text-muted">{A.theme}</span>
          <select value={preference} onChange={(e) => choisirTheme(e.target.value as Preference)} aria-label={A.theme}
            className="rounded border border-hair bg-abyss px-1 py-0.5 text-[11.5px] text-ink">
            {PREFERENCES.map((t) => <option key={t} value={t}>{A.themes[t]}</option>)}
          </select>
        </label>
        <label className="flex items-center justify-between gap-2 py-0.5 text-[12px]">
          <span className="text-muted">{A.vueDefaut}</span>
          <select value={pp.vueDefaut} onChange={(e) => pp.onVueDefaut(e.target.value as VueCarte)} aria-label={A.vueDefaut}
            className="rounded border border-hair bg-abyss px-1 py-0.5 text-[11.5px] text-ink">
            {(["veille", "trafic", "satellites"] as const).map((v) => <option key={v} value={v}>{L.veille.vues[v]}</option>)}
          </select>
        </label>
        <Bascule on={pp.vignettes} actif label={A.vignettes} onChange={pp.onVignettes} />
      </Partie>
      <Partie id="aff-demo" titre={C.sections.demo} ouverte>
        <Demonstration actif={actif} masquage={pp.masquage} rejeuFidele={pp.rejeuFidele} onRejeuFidele={pp.onRejeuFidele} />
      </Partie>
    </>
  );
}

/** Passages à venir sur la région affichée (48 h) et état de l'activité satellite ; un clic ouvre la fiche du passage */
export function PartiePassages({ region, status, now, onPick }: { region: string | null; status: unknown; now: number;
  onPick: (f: Feature) => void }) {
  const debut = new Date(Math.floor(now / 600_000) * 600_000), fin = new Date(debut.getTime() + 48 * 3600_000);
  const q = useQuery({ queryKey: ["passagesAVenir", region, debut.toISOString()],
    queryFn: () => api.passages(debut.toISOString(), fin.toISOString(), region), staleTime: 600_000 });
  const venir = (q.data?.features ?? []).filter((f) => Date.parse(f.properties.acquired_at) >= now).slice(0, 8);
  return (
    <Partie id="passages" titre={C.sections.passages} ouverte>
      <ListeIndicateurs status={status} now={now} ids={["satellites"]} />
      {!venir.length && q.data && <p className="mt-1 text-[12px] text-muted">{C.aucunPassage}</p>}
      <ul className="mt-1 space-y-0.5 text-[12px]">
        {venir.map((f) => (
          <li key={f.properties.id}>
            <button onClick={() => onPick(f)} className="w-full text-left text-muted hover:text-ink">
              {C.passage(f.properties.satellite, jourHeure(f.properties.acquired_at), f.properties.n_infra, f.properties.n_listes)}
            </button>
          </li>
        ))}
      </ul>
    </Partie>
  );
}

export function PartieDemande() {
  const actif = useSession().data?.operateur != null;
  const e = useEtatControle().data;
  return (
    <Partie id="demande" titre={C.sections.demande}>
      <Connecter actif={actif} />
      <Demande actif={actif} rayon={e?.rayon_demande_m ?? null} />
    </Partie>
  );
}

export function RubriqueRegles() {
  const actif = useSession().data?.operateur != null;
  const q = useQuery({ queryKey: ["controle", "regles"], queryFn: api.controle.regles, refetchInterval: 60_000 });
  const m = useQuery({ queryKey: ["mesuresDecisions"], queryFn: () => api.mesuresDecisions(30), staleTime: 60_000 });
  if (!q.data) return <p className="text-muted">{L.commun.chargement}</p>;
  const par = Object.entries((m.data?.par_type ?? {}) as Record<string, Record<string, number | null>>);
  return (
    <>
      <p className="mb-1 text-[11.5px] text-faint">{C.regles(q.data.version_regles)} · {R.empreinte(q.data.empreinte)}</p>
      <Partie id="modes" titre={R.modesTitre} ouverte>
        <Connecter actif={actif} />
        <ul>
          {q.data.regles.map((r) => (
            <li key={r.regle} className="border-b border-hair/50 py-1 last:border-0">
              <ModeRegle regle={r.regle} nom={r.nom} b={r} actif={actif} />
              {r.sous_types.map((s) => <ModeRegle key={s.sous_type} regle={r.regle} sousType={s.sous_type} nom={s.nom} b={s} actif={actif} />)}
            </li>
          ))}
        </ul>
      </Partie>
      <Partie id="seuils" titre={R.seuils}>
        {q.data.regles.map((r) => (
          <div key={r.regle} className="mb-2">
            <p className="text-[12px] text-ink">{r.nom}</p>
            <table className="mt-0.5 w-full font-mono text-[10.5px] text-muted">
              <tbody>
                {r.seuils.map(([k, v]) => (
                  <tr key={k}><td className="pr-2 align-top text-faint">{k}</td><td className="text-right text-ink/80">{JSON.stringify(v)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </Partie>
      <Partie id="faux" titre={R.fauxTitre}>
        {!par.length && <p className="text-[12px] text-muted">{R.aucuneDecision}</p>}
        <table className="w-full text-[11.5px] tabular-nums">
          <tbody>
            {par.map(([t, x]) => (
              <tr key={t} className="text-muted">
                <td className="py-0.5 text-ink">{libelleAlerte(t)}</td>
                <td className="text-right">{R.vraisFaux(x.confirmees ?? 0, x.faux_positif ?? 0)}</td>
                <td className="w-12 text-right text-ink">{x.part_vrais == null ? "·" : `${Math.round(Number(x.part_vrais) * 100)} %`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Partie>
    </>
  );
}

export function RubriqueNotifications() {
  return <Notifications actif={useSession().data?.operateur != null} />;
}

export function RubriqueZones(pp: Pick<PanneauProps, "trace" | "onTrace" | "infra" | "onPickZone">) {
  const actif = useSession().data?.operateur != null;
  return <><Connecter actif={actif} /><Zones actif={actif} {...(pp as PanneauProps)} /></>;
}

/** Système : comment MARS C2 fonctionne, en lecture seule (ingestion, disque, archivage, versions ; services,
 *  mémoire, échecs, Grafana) */
export function RubriqueSysteme({ status, now }: { status: unknown; now: number }) {
  const q = useQuery({ queryKey: ["controle", "systeme"], queryFn: api.controle.systeme, refetchInterval: 60_000 });
  return (
    <>
      <Partie id="sys-etat" titre={L.etat.titre} ouverte>
        <ListeIndicateurs status={status} now={now} ids={["ingestion", "disque", "archivage", "version"]} />
      </Partie>
      <Partie id="services" titre={C.sections.services} ouverte>{q.data ? <Systeme s={q.data} /> : null}</Partie>
      <Partie id="echeances" titre={C.sections.echeances} ouverte>{q.data ? <Echeances e={q.data.echeances ?? []} /> : null}</Partie>
    </>
  );
}

/** Journal : actions du panneau (journal_controle) et décisions des opérateurs sur les alertes */
export function RubriqueJournal() {
  const q = useQuery({ queryKey: ["controle", "journal"], queryFn: () => api.controle.journal(30), refetchInterval: 30_000 });
  const d = useQuery({ queryKey: ["decisionsJournal"], queryFn: () => api.decisionsJournal(30), refetchInterval: 30_000 });
  return (
    <>
      <Partie id="jrn-decisions" titre={C.sections.decisions} ouverte>
        {!d.data?.length && <p className="text-[12px] text-muted">{C.journalVide}</p>}
        <ul className="space-y-1 text-[11.5px] leading-snug">
          {(d.data ?? []).map((x) => (
            <li key={x.id} className="text-muted">
              <span className="tabular-nums text-faint">{jourHeure(x.at)}</span>{" "}
              <span className="text-ink">{x.author}</span> {L.decisions.verbe[x.action] ?? x.action} · {libelleAlerte(x.type)} {x.alert_id}
              {x.motif && <> · {L.decisions.motifs[x.motif] ?? x.motif}</>}
            </li>
          ))}
        </ul>
      </Partie>
      <Partie id="jrn-panneau" titre={C.sections.journal} ouverte>
        <Journal lignes={q.data ?? []} />
      </Partie>
    </>
  );
}
