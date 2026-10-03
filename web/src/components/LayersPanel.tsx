import type { ReactNode } from "react";
import { ALERT_COLOR, ALERT_LABEL, SHIP_NEUTRAL, SHIP_OTHER, SHIP_PALETTE } from "../lib/format";

export interface LayerState {
  analysis: boolean;
  zones: boolean;
  reception: boolean;
  byType: boolean;
}

// Libellés de la légende, dans l'ordre de la palette centralisée ; les teintes viennent de SHIP_PALETTE
const SHIP_LABELS = ["Cargo", "Pétrolier", "Pêche", "Passagers", "Plaisance, voile", "Service"];
const SHIP_TYPES: [string, string][] = [
  ...SHIP_PALETTE.map(({ color }, i) => [color, SHIP_LABELS[i]] as [string, string]),
  [SHIP_OTHER, "Autre ou non renseigné"],
];

function Switch({ on, onChange, label, hint }: { on: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <button onClick={() => onChange(!on)} role="switch" aria-checked={on} title={hint}
      className="flex w-full items-center justify-between gap-4 border-b border-hair px-4 py-2.5 text-left hover:bg-raised/50">
      <span className="font-medium">{label}</span>
      <span className={`flex h-5 w-9 shrink-0 items-center rounded-full p-0.5 transition-colors ${on ? "bg-signal" : "bg-hair"}`}>
        <span className={`h-4 w-4 rounded-full bg-ink transition-transform ${on ? "translate-x-4" : ""}`} />
      </span>
    </button>
  );
}

function Key({ swatch, children }: { swatch: ReactNode; children: ReactNode }) {
  return <div className="flex items-center gap-3 py-1 text-[12.5px]"><span className="flex w-4 justify-center">{swatch}</span>{children}</div>;
}

export default function LayersPanel({ state, onChange }: { state: LayerState; onChange: (s: LayerState) => void }) {
  const set = (k: keyof LayerState) => (v: boolean) => onChange({ ...state, [k]: v });
  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <header className="border-b border-hair px-4 pb-3 pt-4">
        <h2 className="text-[15px] font-semibold">Couches et légende</h2>
      </header>
      <Switch on={state.analysis} onChange={set("analysis")} label="Analyse radar" hint="Zone analysée, détections et leurs alertes" />
      <Switch on={state.byType} onChange={set("byType")} label="Couleur par type de navire" hint="Sinon, tous les navires en gris neutre" />
      <Switch on={state.zones} onChange={set("zones")} label="Zones de mouillage" hint="Déduites de l'AIS, rencontres déclassées" />
      <Switch on={state.reception} onChange={set("reception")} label="Réception AIS fiable" hint="Là où un silence est significatif" />

      <section className="px-4 py-4">
        <div className="mb-2 font-medium">Navires</div>
        <Key swatch={<svg width="12" height="12" viewBox="0 0 12 12"><path d="M6 1 L10 11 L6 8.5 L2 11 Z" fill={SHIP_NEUTRAL} /></svg>}>En route</Key>
        <Key swatch={<span className="h-2 w-2 rounded-full" style={{ background: SHIP_NEUTRAL }} />}>Immobile</Key>
        <Key swatch={<span className="h-2 w-2 rounded-full opacity-35" style={{ background: SHIP_NEUTRAL }} />}>Silencieux</Key>
        {state.byType && (
          <div className="mt-2 grid grid-cols-2 gap-x-3">
            {SHIP_TYPES.map(([c, l]) => <Key key={l} swatch={<span className="h-2 w-2 rounded-full" style={{ background: c }} />}>{l}</Key>)}
          </div>
        )}

        <div className="mb-2 mt-5 font-medium">Détections radar</div>
        <Key swatch={<span className="h-3 w-3 rounded-full border-[1.5px] border-[#e6ecf0]" />}>Avec AIS</Key>
        <Key swatch={<span className="h-3 w-3 rounded-full border-[1.5px] border-dark" />}>Sans AIS</Key>
        <Key swatch={<span className="h-3 w-3 rounded-full border-[1.5px] border-faint" />}>Écartée</Key>

        <div className="mb-2 mt-5 font-medium">Alertes</div>
        {Object.entries(ALERT_LABEL).map(([t, l]) => (
          <Key key={t} swatch={<span className="h-3.5 w-3.5 rounded-full border-2" style={{ borderColor: ALERT_COLOR[t] }} />}>{l}</Key>
        ))}
      </section>
    </div>
  );
}
