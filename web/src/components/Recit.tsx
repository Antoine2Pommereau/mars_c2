import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, type Dossier } from "../lib/api";
import { toucheLibre } from "../lib/clavier";
import { hm, jourHeure } from "../lib/format";
import { L } from "../lib/libelles";
import { COULEURS_PREUVE as CP, distance, type Preuve } from "../lib/preuve";
import type { Feature, Props } from "../lib/types";
import { URGENCE } from "../lib/urgence";
import PreuveImage from "./PreuveImage";
import { IconeSignal } from "./VeillePanel";

const R = L.recit;
const MOTIFS = Object.keys(L.decisions.motifs);

/** Ligne de la chronologie : signal, vérification satellite ou décision */
interface Fait { t: string; icone?: string; texte: string; detail?: string; alerte?: number; detection?: number; source?: string }

function chronologie(d: Dossier, recit: Props | undefined): Fait[] {
  const faits: Fait[] = d.signaux.map((s) => ({ t: s.t, icone: s.icone, texte: s.fragment, alerte: s.alerte,
    detail: L.statut[s.statut] && s.statut !== "nouvelle" ? L.statut[s.statut] : undefined }));
  for (const v of recit?.verifications ?? []) {
    faits.push({ t: v.cree_le, texte: R.verificationDemandee });
    if (v.heure_passage && v.resultat) {
      const det = (recit?.detections ?? []).find((x: Props) => x.id === v.detection_id);
      const passage = { detection: v.detection_id ?? undefined, source: det?.source, detail: `${R.passage(v.satellite)}, ${v.motif ?? v.resultat}` };
      // L'écho de ce passage est déjà une alerte du dossier : une seule ligne, l'alerte, avec le passage en détail
      const echo = faits.find((f) => f.icone && ["DARK_SHIP", "OPTIQUE"].includes(f.icone)
        && Math.abs(Date.parse(f.t) - Date.parse(v.heure_passage)) < 15 * 60_000);
      if (echo) Object.assign(echo, passage);
      else faits.push({ t: v.heure_passage, icone: "DARK_SHIP", texte: R.passage(v.satellite), ...passage });
    }
  }
  for (const x of recit?.decisions ?? [])
    faits.push({ t: x.at, texte: R.decision(L.decisions.verbe[x.action] ?? x.action, x.author), detail: x.motif ? L.decisions.motifs[x.motif] : undefined });
  return faits.sort((a, b) => a.t.localeCompare(b.t));
}

/** Légende de la preuve dessinée sur la carte, avec les mêmes chiffres et les mêmes couleurs que le dessin */
function Legende({ preuve }: { preuve: Preuve }) {
  const { derniere_position: d, estimee: e, echo } = preuve;
  const ligne = (couleur: string, forme: "point" | "anneau" | "tirets" | "cercle", texte: string) => (
    <li className="flex items-center gap-2">
      <svg width="18" height="10" className="shrink-0" aria-hidden>
        {forme === "point" && <circle cx="9" cy="5" r="3.5" fill={couleur} />}
        {forme === "anneau" && <circle cx="9" cy="5" r="4" fill="none" stroke={couleur} strokeWidth="2" />}
        {forme === "tirets" && <line x1="1" y1="5" x2="17" y2="5" stroke={couleur} strokeWidth="1.8" strokeDasharray="3 2" />}
        {forme === "cercle" && <circle cx="9" cy="5" r="4.5" fill="none" stroke={couleur} strokeWidth="1" opacity="0.7" />}
      </svg>
      <span>{texte}</span>
    </li>
  );
  return (
    <ul className="space-y-1 text-[12px] text-muted">
      {d && ligne(CP.derniere, "point", R.derniere(d.t ? jourHeure(d.t) : L.commun.nd))}
      {d && echo && ligne(CP.silence, "tirets", R.silence)}
      {e && ligne(CP.cercle, "cercle", R.estimee(distance(e.rayon_m)))}
      {echo && ligne(CP.echo, "anneau", R.echo(echo.t ? jourHeure(echo.t) : L.commun.nd, echo.longueur_m, echo.source === "sentinel2"))}
      {preuve.distance_estimee_echo_m != null && <li className="pl-[26px] text-ink">{R.ecart(distance(preuve.distance_estimee_echo_m))}</li>}
      {preuve.distance_derniere_echo_m != null && <li className="pl-[26px]">{R.depuisDerniere(distance(preuve.distance_derniere_echo_m))}</li>}
    </ul>
  );
}

/** Récit d'un dossier (lot U, étape 4) : niveau, qui, phrase, gestes, chronologie avec les preuves, légende de la
 *  carte, détail technique replié. Une décision vaut pour toutes les alertes ouvertes du dossier, journalisée alerte
 *  par alerte par l'API. */
export default function Recit({ dossier: d, recit, instant, suivi, onSuivre, onPickAlert }: {
  dossier: Dossier; recit: Props | undefined; instant: string; suivi: boolean;
  onSuivre: (vesselId: number, on: boolean) => void; onPickAlert: (f: Feature) => void;
}) {
  const qc = useQueryClient();
  const [classer, setClasser] = useState(false);
  const [devis, setDevis] = useState<Props | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const rafraichir = () => { for (const k of ["dossiers", "dossier", "alertsRange", "vessel", "alertActions"]) qc.invalidateQueries({ queryKey: [k] }); };
  const decider = useMutation({
    mutationFn: (b: { action: string; motif?: string }) => api.decisionDossier(d.cle, { ...b, instant }),
    onSuccess: (r, b) => { setClasser(false); setMessage(R.fait(R.verbes[b.action], r.alertes.length)); rafraichir(); },
    onError: (e: Error) => setMessage(e.message),
  });
  const devisM = useMutation({ mutationFn: () => api.verificationDossier(d.cle, instant), onSuccess: setDevis,
    onError: (e: Error) => setMessage(e.message) });
  const demander = useMutation({
    mutationFn: () => api.demander(d.vessel_id != null ? { vessel_id: d.vessel_id }
      : { alerte_id: d.alertes[0] }),
    onSuccess: (r) => { setDevis(null); setMessage(r.regroupee ? R.verifier.regroupee(r.id) : R.verifier.demandee(r.id)); rafraichir(); },
    onError: (e: Error) => setMessage(e.message),
  });
  // Gestes au clavier : F, C puis 1 2 3, S, V (Entrée demande, Échap annule) ; écoute en capture, avant la Veille
  const gestes = useRef({ classer, devis, occupe: false, suivi, onSuivre });
  gestes.current = { classer, devis, occupe: decider.isPending || devisM.isPending || demander.isPending, suivi, onSuivre };
  const { mutate: decide } = decider, { mutate: demande } = demander, { mutate: chiffre } = devisM;
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      const t = toucheLibre(e), g = gestes.current;
      if (!t || g.occupe) return;
      const pris = () => { e.preventDefault(); e.stopPropagation(); };
      if (t === "escape" && (g.classer || g.devis)) { pris(); setClasser(false); setDevis(null); }
      else if (t === "enter" && g.devis && !g.devis.refus) { pris(); demande(); }
      else if (g.classer && ["1", "2", "3"].includes(t)) { pris(); decide({ action: "classer", motif: MOTIFS[Number(t) - 1] }); }
      else if (t === "f") { pris(); decide({ action: "confirmer" }); }
      else if (t === "c") { pris(); setClasser((x) => !x); }
      else if (t === "s" && d.vessel_id != null) { pris(); g.onSuivre(d.vessel_id, !g.suivi); }
      else if (t === "v") { pris(); chiffre(); }
    };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [d.vessel_id, decide, demande, chiffre]);
  const ouvrirAlerte = (id: number) => api.alert(id).then(onPickAlert).catch(() => undefined);
  const occupe = decider.isPending || devisM.isPending || demander.isPending;
  const btn = "rounded-md border border-hair px-2 py-1.5 text-[12.5px] text-ink hover:border-muted disabled:opacity-40";
  const touche = (k: string) => R.touches[k] && <kbd className="ml-1 text-[10px] text-faint">{R.touches[k]}</kbd>;
  const faits = chronologie(d, recit);
  const jours = new Set(faits.map((f) => f.t.slice(0, 10))).size > 1;
  const quand = (t: string) => (jours ? jourHeure(t) : hm(t));
  const preuve = recit?.preuve as Preuve | null | undefined;
  const alertes: Props[] = recit?.alertes_detail ?? [];

  return (
    <section className="mb-3 space-y-3" data-recit={d.cle}>
      <div>
        <p className="text-[12px] font-semibold uppercase tracking-wide" style={{ color: URGENCE[d.niveau] }}>{L.veille.niveaux[d.niveau_cle]}</p>
        <p className="mt-0.5 text-[12px] text-muted">{d.genre === "point" ? R.point : R.qui(d.type_navire, d.longueur_m)}</p>
        <p className="mt-1.5 text-[14px] leading-snug text-ink">{d.phrase}</p>
      </div>

      <div className="flex flex-wrap gap-1">
        <button className={btn} disabled={occupe} onClick={() => decider.mutate({ action: "vu" })}>{R.gestes.vu}</button>
        <button className={btn} disabled={occupe} onClick={() => decider.mutate({ action: "confirmer" })}>{R.gestes.confirmer}{touche("confirmer")}</button>
        <button className={btn} disabled={occupe} aria-expanded={classer} onClick={() => setClasser(!classer)}>{R.gestes.classer}{touche("classer")}</button>
        {d.vessel_id != null && (
          <button className={btn} onClick={() => onSuivre(d.vessel_id!, !suivi)} aria-pressed={suivi}>{suivi ? R.gestes.suivi : R.gestes.suivre}{touche("suivre")}</button>
        )}
        <button className={btn} disabled={occupe} onClick={() => devisM.mutate()}>{R.gestes.verifier}{touche("verifier")}</button>
      </div>
      {classer && (
        <div>
          <p className="mb-1 text-[12px] text-muted">{R.motif}</p>
          <div className="flex gap-1.5">
            {MOTIFS.map((m, k) => (
              <button key={m} className={btn} disabled={occupe} onClick={() => decider.mutate({ action: "classer", motif: m })}>
                <kbd className="mr-1 text-[10.5px] text-faint">{k + 1}</kbd>{L.decisions.motifs[m]}
              </button>
            ))}
          </div>
        </div>
      )}
      {devis && (
        <div className="rounded-md border border-hair bg-abyss/60 p-2.5 text-[12.5px]">
          <p className="font-semibold text-ink">{R.verifier.titre}</p>
          {devis.refus ? <p className="mt-1 text-gap">{devis.refus}</p> : (
            <>
              <p className="mt-1 text-muted">{devis.prochain_passage ? R.verifier.passage(devis.prochain_passage.satellite, jourHeure(devis.prochain_passage.heure))
                : R.verifier.aucunPassage}</p>
              <p className="text-muted">{R.verifier.cout(devis.cout_estime_eur)}</p>
              <div className="mt-2 flex gap-1.5">
                <button className={btn} disabled={occupe} onClick={() => demander.mutate()}>{R.verifier.demander}</button>
                <button className={btn} onClick={() => setDevis(null)}>{R.verifier.annuler}</button>
              </div>
            </>
          )}
        </div>
      )}
      {message && <p className="text-[12px] text-muted" role="status">{message}</p>}

      {preuve && (preuve.derniere_position || preuve.echo || preuve.estimee) && (
        <div className="border-t border-hair pt-2">
          <p className="mb-1.5 font-semibold text-ink">{R.legende}</p>
          <Legende preuve={preuve} />
        </div>
      )}

      <div className="border-t border-hair pt-2">
        <p className="mb-1.5 font-semibold text-ink">{R.chronologie}</p>
        <ol className="space-y-1.5 text-[12.5px]">
          {faits.map((f, k) => (
            <li key={k}>
              <button className={`grid w-full ${jours ? "grid-cols-[5.6rem_1rem_1fr]" : "grid-cols-[3.2rem_1rem_1fr]"} items-start gap-x-1.5 text-left hover:text-ink disabled:cursor-default`}
                disabled={f.alerte == null} onClick={() => f.alerte != null && ouvrirAlerte(f.alerte)}>
                <span className="tabular-nums text-faint">{quand(f.t)}</span>
                <span className="mt-0.5 text-muted">{f.icone && <IconeSignal type={f.icone} taille={12} />}</span>
                <span className="text-ink/90">{f.texte}{f.detail && <span className="text-muted">, {f.detail}</span>}</span>
              </button>
              {f.detection != null && f.source && <div className={`mt-1 ${jours ? "ml-[7rem]" : "ml-[4.6rem]"}`}><PreuveImage source={f.source} detectionId={f.detection} /></div>}
            </li>
          ))}
        </ol>
      </div>

      <details className="group border-t border-hair pt-2 text-[12px] text-muted">
        <summary className="cursor-pointer list-none font-semibold text-ink marker:hidden">
          <span className="mr-1.5 inline-block text-faint transition-transform group-open:rotate-90">›</span>{R.technique}
        </summary>
        <p className="mt-1.5 text-ink/80">{R.niveau}</p>
        <ul className="ml-2 list-disc pl-3">{d.contributions.map((c) => <li key={c}>{c}</li>)}</ul>
        <p className="mt-1.5 text-ink/80">{R.versions}, {R.empreintes.toLowerCase()}, {R.traces.toLowerCase()}</p>
        <ul className="mt-0.5 space-y-0.5 font-mono text-[10.5px]">
          {alertes.map((a) => (
            <li key={a.id} className="select-all break-all">
              {R.alerte(a.id)} · {a.rule_version}{a.controle_empreinte ? ` · ${a.controle_empreinte}` : ""}
              {a.details?.trace_id ? ` · ${a.details.trace_id}` : ""}
            </li>
          ))}
        </ul>
      </details>
      <p className="border-t border-hair pt-2 text-[11.5px] uppercase tracking-wide text-faint">{R.ficheComplete}</p>
    </section>
  );
}
