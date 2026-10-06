import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "../lib/api";
import { utc } from "../lib/format";
import { L } from "../lib/libelles";
import type { Feature, Selection } from "../lib/types";
import { COULEUR_LISTE, libelleAlerte, typeAlerte } from "../registres/alertes";
import { sectionsDe, type Contexte } from "../registres/sections";

interface PanelProps {
  selection: Selection | null;
  onClose: () => void;
  passTime: string | null;
  onStatus: (status: string) => void;
  onPickAlert: (f: Feature) => void;
}

/** Fiche de l'objet sélectionné : un en tête, puis les sections du registre (registres/sections.tsx) qui
 *  s'appliquent à cet objet et ont des données, dans leur ordre. */
export default function DetailPanel({ selection, onClose, passTime, onStatus, onPickAlert }: PanelProps) {
  const vesselId = selection?.kind === "vessel" ? Number(selection.properties.vessel_id) : null;
  const card = useQuery({ queryKey: ["vessel", vesselId], queryFn: () => api.vessel(vesselId!), enabled: vesselId != null,
    staleTime: 60_000 });
  if (!selection) return null;

  let title = "", color = "#4fb6c8", badge: string | null = null, footer: string | null = null;
  const ctx: Contexte = { objet: "detection", passTime, onStatus, onPickAlert };
  if (selection.kind === "alert") {
    const p = selection.feature.properties;
    Object.assign(ctx, { objet: "alerte", alerte: selection.feature });
    title = `${libelleAlerte(p.type)}, ${L.gravite[p.severity] ?? p.severity}`;
    color = typeAlerte(p.type).couleur;
    badge = p.status && p.status !== "nouvelle" ? L.statut[p.status] : null;
    footer = L.fiche.regles(p.rule_version, p.event_time ? utc(p.event_time) : undefined);
  } else if (selection.kind === "vessel") {
    Object.assign(ctx, { objet: "navire", navire: selection.properties, carte: card.data });
    title = selection.properties.name || card.data?.name || L.fiche.navireSansNom;
    if (selection.properties.watch || card.data?.watch) color = COULEUR_LISTE;
  } else {
    Object.assign(ctx, { detection: selection.properties });
    title = L.preuves.detection;
  }

  return (
    <div className="absolute right-4 top-4 z-10 max-h-[calc(100%-12rem)] w-[380px] overflow-y-auto rounded-lg border border-hair bg-panel/95 p-4 shadow-2xl backdrop-blur">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color }} />
          <div>
            <h3 className="text-[15px] font-semibold leading-snug">{title}</h3>
            {badge && <span className="mt-1 inline-block rounded-full bg-raised px-2 py-0.5 text-[11.5px] text-muted">{badge}</span>}
          </div>
        </div>
        <button onClick={onClose} className="text-muted hover:text-ink" aria-label={L.commun.fermer}><X size={16} /></button>
      </div>
      {sectionsDe(ctx).map((s) => s.titre ? (
        <details key={s.id} open className="group mt-3 border-t border-hair pt-2">
          <summary className="mb-1.5 cursor-pointer list-none font-semibold text-ink marker:hidden">
            <span className="mr-1.5 inline-block text-faint transition-transform group-open:rotate-90">›</span>{L.fiche.sections[s.id]}
          </summary>
          {s.rendu(ctx)}
        </details>
      ) : <div key={s.id} className="mt-2">{s.rendu(ctx)}</div>)}
      {footer && <p className="mt-4 border-t border-hair pt-3 text-[12px] text-muted">{footer}</p>}
    </div>
  );
}
