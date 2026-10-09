import type { ReactNode } from "react";
import { L } from "../lib/libelles";
import { COULEUR_LISTE } from "../registres/alertes";
import { COUCHES, COULEUR_PASSAGE, GROUPES, disponible, type Couche } from "../registres/couches";

interface Props {
  actives: string[];
  onActives: (ids: string[]) => void;
  concernees: boolean;
  onConcernees: (v: boolean) => void;
  byType: boolean;
  onByType: (v: boolean) => void;
  infraCounts: Record<string, number>;     // tracés par type d'infrastructure
  vesselCount: number;
  passageCount: number;                    // passages Sentinel 1 et 2 sur la plage et la région
  viirsCount: number;                      // détections nocturnes VIIRS sur la plage et la région
}

const C = L.couches;

function Switch({ on, onChange, label, hint, disabled, right }:
  { on: boolean; onChange: (v: boolean) => void; label: string; hint?: string; disabled?: boolean; right?: ReactNode }) {
  return (
    <button onClick={() => !disabled && onChange(!on)} role="switch" aria-checked={on} title={hint} disabled={disabled}
      className={`flex w-full items-center justify-between gap-3 px-4 py-2 text-left ${disabled ? "cursor-default" : "hover:bg-raised/50"}`}>
      <span className={disabled ? "text-faint/70" : "text-ink"}>{label}</span>
      <span className="flex items-center gap-2">
        {right}
        <span className={`flex h-4.5 w-8 shrink-0 items-center rounded-full p-0.5 transition-colors ${on && !disabled ? "bg-signal" : "bg-hair"}`}>
          <span className={`h-3.5 w-3.5 rounded-full ${disabled ? "bg-faint/50" : "bg-ink"} transition-transform ${on && !disabled ? "translate-x-3.5" : ""}`} />
        </span>
      </span>
    </button>
  );
}

function Key({ swatch, children }: { swatch: ReactNode; children: ReactNode }) {
  return <div className="flex items-center gap-3 py-0.5 text-[12px] text-muted"><span className="flex w-4 justify-center">{swatch}</span>{children}</div>;
}

function Legende({ c, byType }: { c: Couche; byType: boolean }) {
  if (c.id === "navires") {
    return (
      <div className="px-4 pb-2 pl-8">
        <Key swatch={<svg width="12" height="12" viewBox="0 0 12 12"><path d="M6 1 L10 11 L6 8.5 L2 11 Z" fill="#c9d3da" /></svg>}>{C.legende.enRoute}</Key>
        <Key swatch={<span className="h-2 w-2 rounded-full bg-[#c9d3da]" />}>{C.legende.immobile}</Key>
        <Key swatch={<span className="h-2 w-2 rounded-full bg-[#c9d3da] opacity-35" />}>{C.legende.silencieux}</Key>
        <Key swatch={<span className="h-2 w-2 rounded-full" style={{ background: COULEUR_LISTE }} />}>{C.legende.surListe}</Key>
        <Key swatch={<span className="h-2 w-2 rounded-full bg-gap" />}>{C.legende.enAlerte}</Key>
        {byType && (
          <div className="mt-1 grid grid-cols-2 gap-x-3">
            {C.typesNavire.map(([col, l]) => <Key key={l} swatch={<span className="h-2 w-2 rounded-full" style={{ background: col }} />}>{l}</Key>)}
          </div>
        )}
      </div>
    );
  }
  if (c.id === "passages") {
    return (
      <div className="px-4 pb-2 pl-8">
        <Key swatch={<span className="h-0.5 w-4" style={{ background: COULEUR_PASSAGE }} />}>{C.legende.acquis}</Key>
        <Key swatch={<span className="h-0 w-4 border-t border-dashed" style={{ borderColor: COULEUR_PASSAGE }} />}>{C.legende.prevu}</Key>
      </div>
    );
  }
  if (c.id === "viirs") {
    return (
      <div className="px-4 pb-2 pl-8">
        <Key swatch={<span className="h-2 w-2 rounded-full bg-[#e6ecf0] opacity-60" />}>{C.legende.avecAis}</Key>
        <Key swatch={<span className="h-2.5 w-2.5 rounded-full bg-dark ring-2 ring-dark/30" />}>{C.legende.sansAis}</Key>
        <Key swatch={<span className="h-1.5 w-1.5 rounded-full bg-faint" />}>{C.legende.ecartee}</Key>
        <Key swatch={<span className="h-2 w-2 rounded-full bg-muted opacity-50" />}>{C.legende.nonEvaluable}</Key>
      </div>
    );
  }
  if (c.id === "detections") {
    return (
      <div className="px-4 pb-2 pl-8">
        <Key swatch={<span className="h-3 w-3 rounded-full border-[1.5px] border-[#e6ecf0]" />}>{C.legende.avecAis}</Key>
        <Key swatch={<span className="h-3 w-3 rounded-full border-[1.5px] border-dark" />}>{C.legende.sansAis}</Key>
        <Key swatch={<span className="h-3 w-3 rounded-full border-[1.5px] border-faint" />}>{C.legende.ecartee}</Key>
      </div>
    );
  }
  return null;
}

function Pastille({ c }: { c: Couche }) {
  if (!c.couleur) return null;
  return c.legende === "surface"
    ? <span className="h-2.5 w-2.5 rounded-sm border" style={{ borderColor: c.couleur, background: `${c.couleur}33` }} />
    : <span className="h-0.5 w-4" style={{ background: c.couleur }} />;
}

/** Couches et légende, déduites du registre : une carte par groupe, les couches à venir grisées avec leur étape. */
export default function LayersPanel(p: Props) {
  const set = (id: string, on: boolean) => p.onActives(on ? [...p.actives, id] : p.actives.filter((x) => x !== id));
  return (
    <div className="flex h-full flex-col overflow-y-auto pb-4">
      <header className="border-b border-hair px-4 pb-3 pt-4">
        <h2 className="text-[15px] font-semibold">{C.titre}</h2>
      </header>
      {GROUPES.map((g) => (
        <section key={g} className="mx-3 mt-3 rounded-md border border-hair/70">
          <div className="px-4 pb-1 pt-2.5 text-[11.5px] font-medium uppercase tracking-wide text-faint">{C.groupes[g]}</div>
          {COUCHES.filter((c) => c.groupe === g).map((c) => {
            const on = p.actives.includes(c.id) && disponible(c);
            const etat = !disponible(c) ? `${L.commun.aVenir}, ${L.commun.etape(c.etape!)}`
              : c.infra ? C.traces(p.infraCounts[c.infra] ?? 0)
              : c.id === "navires" ? C.navires(p.vesselCount) : c.id === "passages" ? C.passages(p.passageCount)
              : c.id === "viirs" ? C.passages(p.viirsCount) : "";
            return (
              <div key={c.id}>
                <Switch on={on} disabled={!disponible(c)} onChange={(v) => set(c.id, v)} label={C.noms[c.id]}
                  right={<><span className="text-[11px] text-faint">{etat}</span><Pastille c={c} /></>} />
                {on && <Legende c={c} byType={p.byType} />}
                {on && c.id === "navires" && (
                  <div className="pl-4"><Switch on={p.byType} onChange={p.onByType} label={C.couleurParType} /></div>
                )}
              </div>
            );
          })}
          {g === "infrastructures" && (
            <div className="border-t border-hair/70">
              <Switch on={p.concernees} onChange={p.onConcernees} label={C.concernees} />
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
