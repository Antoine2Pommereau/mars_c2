import { Aperture, Check, CircleHelp, Crosshair, EyeOff, Minus, Radar } from "lucide-react";
import { type CSSProperties, useMemo, useState } from "react";
import { L } from "../lib/libelles";
import type { Props } from "../lib/types";
import { typeAlerte } from "../registres/alertes";
import { Squelette, Tag, Vide } from "./Elements";
import { Heure } from "./Heure";

// Type de tip : icône et couleur du type d'alerte correspondant (registre des alertes) ; demande manuelle à part
const ALERTE_DU_TIP: Record<string, string> = { viirs: "DARK_SHIP", coupure_ais: "AIS_GAP", rendezvous: "RENDEZVOUS",
  identite: "IDENTITY_CHANGE", watchlist: "WATCHLIST" };
const styleTip = (tip: string) => {
  const t = ALERTE_DU_TIP[tip];
  return t ? { Icone: typeAlerte(t).Icone, couleur: typeAlerte(t).couleur } : { Icone: Crosshair, couleur: "var(--color-signal)" };
};

// Résultat en un signe
const SIGNES: Record<string, { Icone: typeof Check; couleur: string }> = {
  present_apparie: { Icone: Check, couleur: "var(--color-ink)" }, present_sans_ais: { Icone: EyeOff, couleur: "#e85bc7" },
  absent: { Icone: Minus, couleur: "var(--color-muted)" }, non_evaluable: { Icone: CircleHelp, couleur: "var(--color-muted)" },
};

type Filtre = "toutes" | "planifiee" | "en_attente" | "analysee" | "closes";
const CLOSES = ["sans_passage", "expiree", "refusee_budget", "annulee"];
const FILTRES: Filtre[] = ["toutes", "planifiee", "en_attente", "analysee", "closes"];

/** Demandes d'observation (tip and cue, lot T1), dans l'ordre de l'API : planifiées par passage le plus proche, en
 *  attente, analysées les plus récentes ; les closes sans résultat repliées en bas. Un clic ouvre la fiche du navire
 *  (ou de l'alerte d'origine) et montre la position prédite et son cercle. */
export default function ObservationsPanel({ demandes, selectedId, onPick, titre = true, chargement = false }:
  { demandes: Props[]; selectedId: number | null; onPick: (d: Props) => void; titre?: boolean; chargement?: boolean }) {
  const O = L.observations;
  const [filtre, setFiltre] = useState<Filtre>("toutes");
  const [closesOuvertes, setClosesOuvertes] = useState(false);
  const garde = (d: Props) => filtre === "toutes" || (filtre === "closes" ? CLOSES.includes(d.etat) : d.etat === filtre);
  const [vivantes, closes] = useMemo(() => {
    const v = demandes.filter(garde);
    return [v.filter((d) => !CLOSES.includes(d.etat)), v.filter((d) => CLOSES.includes(d.etat))];
  }, [demandes, filtre]);
  const ligne = (d: Props) => {
    const { Icone, couleur } = styleTip(d.tip), signe = d.resultat ? SIGNES[d.resultat] : null;
    const close = CLOSES.includes(d.etat);
    // Motif d'une demande en attente ou close (pourquoi elle attend, pourquoi elle s'est close), au survol
    const motif = d.motif && (close || d.etat === "en_attente") ? d.motif : null;
    return (
      <li key={d.id} className="group relative">
        <button onClick={() => onPick(d)} aria-description={motif ?? undefined}
          className={`flex w-full items-center gap-2 border-b border-hair/70 px-4 py-2 text-left hover:bg-raised/60
            ${selectedId === d.id ? "bg-raised" : ""} ${close ? "opacity-60" : ""}`}>
          <Icone size={13} strokeWidth={2} style={{ "--teinte": couleur } as CSSProperties} className="teinte shrink-0" />
          {d.flag && <Tag>{d.flag}</Tag>}
          <span className="min-w-0 flex-1 truncate text-ink">{d.vessel_id != null ? (d.navire ?? `MMSI ${d.mmsi ?? ""}`) : O.point}</span>
          <span className="shrink-0 text-right text-[11.5px] leading-tight">
            <span className="block text-muted">{O.etats[d.etat] ?? d.etat}</span>
            {d.heure_passage && (
              <span className="flex items-center justify-end gap-1 text-ink/80 tabular-nums">
                {/* Capteur du passage retenu : radar (Sentinel 1) ou optique (Sentinel 2, lot S2) */}
                {(() => {
                  const optique = String(d.satellite ?? "").startsWith("S2");
                  const C = optique ? Aperture : Radar;
                  return <span title={L.fiche.observation.capteurs[optique ? "optique" : "radar"]}><C size={10} className="text-faint" /></span>;
                })()}
                <Heure iso={d.heure_passage} f="jourHeure" />
                {/* Contenance estimée : part des navires réels attendue dans les fenêtres (garde de probabilité) */}
                {d.contenance != null && <span className="text-faint" title={L.fiche.observation.contenanceAide}>
                  {L.fiche.observation.contenanceCourte(d.contenance)}</span>}
              </span>
            )}
          </span>
          <span className="w-4 shrink-0" title={signe ? L.fiche.observation.resultats[d.resultat] : undefined}>
            {signe && <signe.Icone size={13} strokeWidth={2.2} style={{ "--teinte": signe.couleur } as CSSProperties} className="teinte" />}
          </span>
        </button>
        {motif && (
          <div role="tooltip" className="pointer-events-none absolute inset-x-3 top-full z-10 hidden rounded-md border
            border-hair bg-raised px-2.5 py-1.5 text-[11.5px] leading-snug text-muted shadow-lg group-hover:block">
            {motif}
          </div>
        )}
      </li>
    );
  };
  return (
    <div className="flex h-full flex-col">
      <header className={titre ? "border-b border-hair px-4 pb-3 pt-4" : "border-b border-hair pb-2"}>
        {titre && <h2 className="flex items-baseline justify-between text-[15px] font-semibold">
          {O.titre}<span className="text-[12px] font-normal text-muted">{vivantes.length + closes.length}</span>
        </h2>}
        <div className="mt-3 flex flex-wrap gap-1.5" role="group" aria-label={O.filtre}>
          {FILTRES.map((f) => (
            <button key={f} onClick={() => setFiltre(f)} aria-pressed={filtre === f}
              className={`rounded-full border px-2 py-0.5 text-[11.5px] transition-colors
                ${filtre === f ? "border-hair bg-raised text-ink" : "border-transparent text-faint hover:text-muted"}`}>
              {O.filtres[f]}
            </button>
          ))}
        </div>
      </header>
      <ul className="flex-1 overflow-y-auto">
        {vivantes.map(ligne)}
        {closes.length > 0 && (
          <li>
            <button onClick={() => setClosesOuvertes(!closesOuvertes)} className="w-full px-4 py-2 text-left text-[11.5px] text-muted hover:text-ink">
              <span className={`mr-1.5 inline-block transition-transform ${closesOuvertes ? "rotate-90" : ""}`}>›</span>
              {O.closes(closes.length)}
            </button>
          </li>
        )}
        {closesOuvertes && closes.map(ligne)}
        {chargement && !vivantes.length && !closes.length && <li><Squelette className="px-4 py-4" /></li>}
        {!chargement && !vivantes.length && !closes.length && <li><Vide titre={O.aucune} raison={O.aucuneRaison} /></li>}
      </ul>
    </div>
  );
}
