import { ChevronDown, ChevronRight, Eye, Image } from "lucide-react";
import { useMemo, useState } from "react";
import { FILTRES_DEFAUT, filtrer, grouper, statutDe, suivisEnTete, trier, zoneAlerte, type Filtres, type Vue } from "../lib/fil";
import { hm, jourHeure } from "../lib/format";
import { L } from "../lib/libelles";
import type { Feature, Props } from "../lib/types";
import { naviresAlerte, TYPES_ALERTE, typeAlerte } from "../registres/alertes";
import { Tag } from "./Elements";
import { Vignette } from "./Fiches";

// Vignette du navire dans chaque ligne du fil : réglage de l'opérateur, gardé dans ce navigateur
const CLE_VIGNETTES = "mars.fil.vignettes";
function lireVignettes(): boolean {
  try { return localStorage.getItem(CLE_VIGNETTES) === "1"; } catch { return false; }
}
function garderVignettes(v: boolean) {
  try { localStorage.setItem(CLE_VIGNETTES, v ? "1" : "0"); } catch { /* stockage indisponible */ }
}

interface PanelProps {
  alerts: Feature[];            // alertes de la plage
  filtres: Filtres;
  onFiltres: (f: Filtres) => void;
  vessels: Map<number, Props>;  // navires affichés, pour le pavillon et le nom
  suivis: Set<number>;          // navires suivis : leurs nouvelles alertes en tête du fil
  now: number;
  selectedId: number | null;
  onPick: (f: Feature) => void;
}

const GRAVITES = FILTRES_DEFAUT.gravites;
const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

function Ligne({ a, vessels, now, selected, onPick, indent, vignette }:
  { a: Feature; vessels: Map<number, Props>; now: number; selected: boolean; onPick: (f: Feature) => void; indent?: boolean;
    vignette?: boolean }) {
  const t = typeAlerte(a.properties.type);
  const d = a.properties.details ?? {};
  const n = naviresAlerte(a)[0];
  const flag = n?.flag ?? (n ? vessels.get(n.vessel_id)?.flag : null);
  const zone = zoneAlerte(a);
  const low = a.properties.severity === "faible" || ["classee", "acquittee"].includes(statutDe(a));
  const quand = now - Date.parse(a.properties.event_time) < 20 * 3600_000 ? hm : jourHeure;
  return (
    <button onClick={() => onPick(a)}
      className={`grid w-full grid-cols-[16px_1fr_auto] items-start gap-x-2.5 border-b border-hair/70 py-2.5 pr-4 text-left transition-colors
        ${indent ? "pl-9" : "pl-4"} ${selected ? "bg-raised" : "hover:bg-raised/60"} ${low ? "opacity-55" : ""}`}>
      <t.Icone size={14} strokeWidth={1.8} className="mt-0.5" style={{ color: t.couleur }} />
      <span className="min-w-0">
        <span className="flex items-center gap-1.5">
          {vignette && !indent && n?.vessel_id != null && <Vignette vesselId={n.vessel_id} taille="petite" />}
          <span className="truncate font-medium text-ink">{indent ? L.alertes[a.properties.type] : t.titre(d)}</span>
          {!indent && flag && <Tag>{flag}</Tag>}
        </span>
        <span className="mt-0.5 flex flex-wrap items-center gap-1 text-[12px] text-muted">
          {t.signe(d)}{zone && <span>{L.zones[zone]}</span>}
        </span>
      </span>
      <span className="text-right text-[12px]">
        <span className="block text-ink/80">{quand(a.properties.event_time)}</span>
        <span className="block text-muted">{statutDe(a) === "nouvelle" ? L.gravite[a.properties.severity] : L.statut[statutDe(a)]}</span>
      </span>
    </button>
  );
}

/** Fil d'alertes de la plage et de la région affichée (réglage de la barre d'état) : filtres par type, gravité et
 *  statut ; tri par gravité puis date ; alertes d'un même navire regroupées. */
export default function AlertsPanel({ alerts, filtres, onFiltres, vessels, suivis, now, selectedId, onPick }: PanelProps) {
  const [ouverts, setOuverts] = useState<Set<string>>(new Set());
  const [vignettes, setVignettes] = useState(lireVignettes);
  const sansVue = useMemo(() => filtrer(alerts, filtres, false), [alerts, filtres]);
  const shown = useMemo(() => trier(filtrer(alerts, filtres)), [alerts, filtres]);
  const groupes = useMemo(() => suivisEnTete(grouper(shown), suivis), [shown, suivis]);
  const count = (type: string) => alerts.filter((a) => a.properties.type === type).length;
  const todo = sansVue.filter((a) => statutDe(a) === "nouvelle").length;
  const set = (p: Partial<Filtres>) => onFiltres({ ...filtres, ...p });

  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-hair px-4 pb-3 pt-4">
        <h2 className="flex items-baseline justify-between text-[15px] font-semibold">
          {L.fil.titre}
          <span className="flex items-center gap-2 text-[12px] font-normal text-muted">
            {L.fil.surTotal(shown.length, alerts.length)}
            <button onClick={() => { garderVignettes(!vignettes); setVignettes(!vignettes); }} aria-pressed={vignettes}
              title={L.fil.vignettes} aria-label={L.fil.vignettes}
              className={`rounded p-0.5 ${vignettes ? "text-ink" : "text-faint hover:text-muted"}`}><Image size={13} /></button>
          </span>
        </h2>
        <div className="mt-3 flex flex-wrap gap-1.5" role="group" aria-label={L.fil.type}>
          {TYPES_ALERTE.map((t) => {
            const on = filtres.types.includes(t.type);
            const title = t.actif ? L.alertes[t.type] : `${L.alertes[t.type]}, ${L.commun.aVenir} (${L.etapes[t.etape] ?? t.etape})`;
            return (
              <button key={t.type} disabled={!t.actif} title={title} onClick={() => set({ types: toggle(filtres.types, t.type) })}
                className={`flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11.5px] transition-colors
                  ${!t.actif ? "border-transparent text-faint/50" : on ? "border-hair bg-raised text-ink" : "border-transparent text-faint"}`}>
                <t.Icone size={11} strokeWidth={2} style={{ color: t.actif && on ? t.couleur : undefined }} />
                {L.alertes[t.type]}{t.actif && <span className="text-muted">{count(t.type)}</span>}
              </button>
            );
          })}
        </div>
        <div className="mt-2 flex items-center gap-2 text-[11.5px]">
          <div className="flex rounded-md border border-hair p-0.5" role="group" aria-label={L.fil.gravite}>
            {GRAVITES.map((g) => (
              <button key={g} onClick={() => set({ gravites: toggle(filtres.gravites, g) })}
                className={`rounded px-1 py-0.5 ${filtres.gravites.includes(g) ? "bg-raised text-ink" : "text-faint hover:text-muted"}`}>{L.gravite[g]}</button>
            ))}
          </div>
        </div>
        <div className="mt-2 flex rounded-md border border-hair p-0.5 text-[12px]" role="tablist">
          {(["todo", "confirmed", "all"] as Vue[]).map((k) => (
            <button key={k} role="tab" aria-selected={filtres.vue === k} onClick={() => set({ vue: k })}
              className={`flex-1 rounded px-2 py-1 ${filtres.vue === k ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
              {L.fil.onglets[k]}{k === "todo" ? ` ${todo}` : ""}
            </button>
          ))}
        </div>
      </header>

      <ul className="flex-1 overflow-y-auto">
        {shown.length === 0 && (
          <li className="px-4 py-6 text-muted">{filtres.vue === "todo" ? L.fil.aucune.todo : L.fil.aucune.autre}</li>
        )}
        {groupes.map((g) => {
          const suivi = g.vesselId != null && suivis.has(g.vesselId);
          if (g.alertes.length === 1) {
            const a = g.alertes[0];
            return (
              <li key={g.cle} className="relative">
                {suivi && <Eye size={11} className="absolute left-1 top-3.5 text-signal" aria-label={L.couches.suivi} />}
                <Ligne a={a} vessels={vessels} now={now} selected={a.properties.id === selectedId} onPick={onPick} vignette={vignettes} />
              </li>
            );
          }
          const open = ouverts.has(g.cle) || g.alertes.some((a) => a.properties.id === selectedId);
          const first = g.alertes[0];
          const t = typeAlerte(first.properties.type);
          const n = naviresAlerte(first)[0];
          const v = g.vesselId != null ? vessels.get(g.vesselId) : undefined;
          const flag = n?.flag ?? v?.flag;
          return (
            <li key={g.cle}>
              <button onClick={() => setOuverts((s) => { const x = new Set(s); if (x.has(g.cle)) x.delete(g.cle); else x.add(g.cle); return x; })}
                className="grid w-full grid-cols-[16px_1fr_auto] items-center gap-x-2.5 border-b border-hair/70 px-4 py-2.5 text-left hover:bg-raised/60">
                {open ? <ChevronDown size={14} className="text-muted" /> : <ChevronRight size={14} className="text-muted" />}
                <span className="flex min-w-0 items-center gap-1.5">
                  {vignettes && g.vesselId != null && <Vignette vesselId={g.vesselId} taille="petite" />}
                  <span className="truncate font-medium text-ink">{n?.name ?? v?.name ?? t.titre(first.properties.details ?? {})}</span>
                  {flag && <Tag>{flag}</Tag>}
                  {suivi && <Eye size={11} className="text-signal" aria-label={L.couches.suivi} />}
                  <span className="flex gap-0.5">{g.alertes.map((a) => (
                    <span key={a.properties.id} className="h-1.5 w-1.5 rounded-full" style={{ background: typeAlerte(a.properties.type).couleur }} />
                  ))}</span>
                </span>
                <span className="text-right text-[12px] text-muted">{L.fil.groupe(g.alertes.length)}</span>
              </button>
              {open && g.alertes.map((a) => (
                <Ligne key={a.properties.id} a={a} vessels={vessels} now={now} selected={a.properties.id === selectedId} onPick={onPick} indent />
              ))}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
