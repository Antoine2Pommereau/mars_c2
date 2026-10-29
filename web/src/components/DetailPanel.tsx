import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { api } from "../lib/api";
import { utc } from "../lib/format";
import { L } from "../lib/libelles";
import type { Feature, Props, Selection, SourceDetection } from "../lib/types";
import { COULEUR_LISTE, libelleAlerte, typeAlerte } from "../registres/alertes";
import { COULEUR_PASSAGE, COULEURS_INFRA } from "../registres/couches";
import { sectionsDe, type Contexte } from "../registres/sections";
import { useInfraCard, usePassage } from "./Fiches";
import { jourHeure } from "../lib/format";

/** Fiche d'une zone de vigilance (lot P) : profil, motif, période, surface ; alertes qui portent la zone, demandes
 *  d'observation et passages qui la couvrent */
function FicheVigilance({ id, onPickAlert }: { id: number; onPickAlert: (f: Feature) => void }) {
  const V = L.fiche.vigilance;
  const q = useQuery({ queryKey: ["controle", "zone", id], queryFn: () => api.controle.zone(id), staleTime: 30_000 });
  const z = q.data;
  if (!z) return <p className="text-muted">{L.commun.chargement}</p>;
  const ouvrir = (aid: number) => api.alert(aid).then(onPickAlert).catch(() => undefined);
  return (
    <div className="space-y-3 text-[12.5px]">
      <div className="space-y-0.5 text-muted">
        <p><span className="text-ink">{z.profil}</span> · {z.motif}</p>
        <p>{V.creee(jourHeure(z.cree_le), z.cree_par)}{z.fin_le ? `, ${V.fin(jourHeure(z.fin_le))}` : ""}</p>
        {z.retiree_le && <p className="text-gap">{V.retiree(jourHeure(z.retiree_le), z.retiree_par)}</p>}
        <p className="tabular-nums">{V.surface(z.km2)}{z.tampon_m ? `, ${V.tampon(z.tampon_m)}` : ""}</p>
      </div>
      <div>
        <p className="mb-1 font-semibold text-ink">{V.alertes(z.alertes.length)}</p>
        <ul className="space-y-0.5">
          {z.alertes.slice(0, 20).map((a: Props) => (
            <li key={a.id}>
              <button className="w-full text-left text-muted hover:text-ink" onClick={() => ouvrir(a.id)}>
                <span className="tabular-nums text-faint">{jourHeure(a.event_time)}</span> {libelleAlerte(a.type)}, {L.gravite[a.severity] ?? a.severity}
                {a.gravite_avant && a.gravite_avant !== a.severity ? ` (${V.relevee(L.gravite[a.gravite_avant] ?? a.gravite_avant)})` : ""}
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <p className="mb-1 font-semibold text-ink">{V.demandes(z.demandes.length)}</p>
        <ul className="space-y-0.5 text-muted">
          {z.demandes.slice(0, 10).map((d: Props) => (
            <li key={d.id}><span className="tabular-nums text-faint">{jourHeure(d.cree_le)}</span> {d.tip} · {L.observations.etats[d.etat] ?? d.etat}</li>
          ))}
        </ul>
      </div>
      <div>
        <p className="mb-1 font-semibold text-ink">{V.passages(z.passages.length)}</p>
        <ul className="space-y-0.5 text-muted">
          {z.passages.slice(0, 10).map((s: Props) => (
            <li key={s.id}><span className="tabular-nums text-faint">{jourHeure(s.acquired_at)}</span> {s.satellite} · {s.statut}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

interface PanelProps {
  selection: Selection | null;
  onClose: () => void;
  plage: { debut: string; fin: string };
  suivis: Set<number>;
  vessels: Map<number, Props>;
  alerts: Feature[];
  onStatus: (status: string) => void;
  onPickAlert: (f: Feature) => void;
  onPickVessel: (p: Props) => void;
  onPickInfra: (id: number) => void;
  onSuivre: (vesselId: number, on: boolean) => void;
  onRejeu: (vesselId: number) => void;
  onPickDetection: (source: SourceDetection, id: number) => void;
  recit?: ReactNode;                     // récit du dossier de l'objet (lot U), en tête ; la fiche complète suit, repliée
  largeur: number;                       // largeur réglée par la poignée (retours du lot U)
  poignee: ReactNode;
}

/** Fiche de l'objet sélectionné (navire, alerte, infrastructure, zone, détection) : un en tête, puis les sections du
 *  registre (registres/sections.tsx) qui s'appliquent à cet objet et ont des données, dans leur ordre. */
export default function DetailPanel(p: PanelProps) {
  const { selection } = p;
  const vesselId = selection?.kind === "vessel" ? Number(selection.properties.vessel_id) : null;
  const card = useQuery({ queryKey: ["vessel", vesselId], queryFn: () => api.vessel(vesselId!), enabled: vesselId != null, staleTime: 60_000 });
  const infraId = selection?.kind === "infrastructure" ? Number(selection.properties.id) : -1;
  const infra = useInfraCard(infraId, p.plage.debut, p.plage.fin);
  const passageId = selection?.kind === "passage" ? Number(selection.properties.id) : -1;
  const passage = usePassage(passageId);
  if (!selection) return null;
  // Fiche allégée (lot U) : sections repliées pour un navire, et pour tout objet qui a un dossier (le récit est en tête)
  const replier = selection.kind === "vessel" || !!p.recit;

  let title = "", color = "var(--color-signal)", badge: string | null = null, footer: string | null = null, trace: string | null = null;
  const ctx: Contexte = { objet: "detection", plage: p.plage, suivis: p.suivis, vessels: p.vessels,
    alerts: p.alerts, onStatus: p.onStatus, onPickAlert: p.onPickAlert, onPickVessel: p.onPickVessel,
    onPickInfra: p.onPickInfra, onSuivre: p.onSuivre, onRejeu: p.onRejeu, onPickDetection: p.onPickDetection };
  if (selection.kind === "alert") {
    const a = selection.feature.properties;
    Object.assign(ctx, { objet: "alerte", alerte: selection.feature });
    title = `${libelleAlerte(a.type)}, ${L.gravite[a.severity] ?? a.severity}`;
    color = typeAlerte(a.type).couleur;
    badge = a.status && a.status !== "nouvelle" ? L.statut[a.status] : null;
    footer = L.fiche.regles(a.rule_version, a.event_time ? utc(a.event_time) : undefined, a.controle_empreinte);
    trace = a.details?.trace_id ?? null;               // trace qui a produit l'alerte (Grafana, Tempo)
  } else if (selection.kind === "vessel") {
    Object.assign(ctx, { objet: "navire", navire: selection.properties, carte: card.data });
    title = selection.properties.name || card.data?.name || L.fiche.navireSansNom;
    if (selection.properties.watch || card.data?.watch) color = COULEUR_LISTE;
  } else if (selection.kind === "infrastructure") {
    const d = { ...selection.properties, ...(infra.data ?? {}) };
    Object.assign(ctx, { objet: "infrastructure", infra: { id: infraId } });
    title = d.name || (d.type ? L.fiche.infra.sansNom(d.type, infraId) : L.carte.infrastructure);
    color = COULEURS_INFRA[d.type] ?? color;
  } else if (selection.kind === "passage") {
    Object.assign(ctx, { objet: "passage", passage: passageId });
    const sat = passage.data?.satellite ?? selection.properties.satellite;
    title = sat ? L.fiche.passage.titre(sat) : L.fiche.sections.passage;
    color = COULEUR_PASSAGE;
    badge = passage.data?.statut === "prevu" ? L.fiche.passage.statuts.prevu.split(",")[0] : null;
  } else if (selection.kind === "vigilance") {
    title = selection.properties.nom ?? L.fiche.vigilance.titre;
    color = "var(--color-neutre)";
  } else if (selection.kind === "zone") {
    Object.assign(ctx, { objet: "zone", zone: selection.properties.zone });
    title = L.zones[selection.properties.zone] ?? selection.properties.zone;
  } else {
    Object.assign(ctx, { detection: selection.properties });
    const viirs = selection.properties.source === "viirs", optique = selection.properties.source === "sentinel2";
    const radar = selection.properties.source === "sentinel1" || optique;
    title = viirs ? L.fiche.viirs.titre : optique ? L.fiche.radar.titreOptique : radar ? L.fiche.radar.titre : L.preuves.detection;
    if (viirs || radar) color = selection.properties.statut === "sans_ais" ? typeAlerte("DARK_SHIP").couleur : "#8a93c9";
  }

  return (
    <div className="absolute right-4 top-4 z-10 flex max-h-[calc(100%-12rem)] flex-col" style={{ width: p.largeur }}>
    {p.poignee}
    <div className="min-h-0 overflow-y-auto rounded-lg border border-hair bg-panel/95 p-4 shadow-2xl backdrop-blur">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color }} />
          <div>
            <h3 className="text-[15px] font-semibold leading-snug">{title}</h3>
            {badge && <span className="mt-1 inline-block rounded-full bg-raised px-2 py-0.5 text-[11.5px] text-muted">{badge}</span>}
          </div>
        </div>
        <button onClick={p.onClose} className="text-muted hover:text-ink" aria-label={L.commun.fermer}><X size={16} /></button>
      </div>
      {p.recit}
      {selection.kind === "vigilance" && <FicheVigilance id={Number(selection.properties.id)} onPickAlert={p.onPickAlert} />}
      {selection.kind !== "vigilance" && sectionsDe(ctx).map((s) => s.titre ? (
        <details key={s.id} open={!replier} className="group mt-3 border-t border-hair pt-2">
          <summary className="mb-1.5 cursor-pointer list-none font-semibold text-ink marker:hidden">
            <span className="mr-1.5 inline-block text-faint transition-transform group-open:rotate-90">›</span>{L.fiche.sections[s.id]}
          </summary>
          {s.rendu(ctx)}
        </details>
      ) : <div key={s.id} className="mt-2">{s.rendu(ctx)}</div>)}
      {footer && <p className="mt-4 border-t border-hair pt-3 text-[12px] text-muted">{footer}
        {trace && <span className="mt-1 block select-all font-mono text-[10px] opacity-60" title={trace}>{L.fiche.trace(trace)}</span>}</p>}
    </div>
    </div>
  );
}
