import { jourHeure } from "../lib/format";
import { L } from "../lib/libelles";
import type { Props } from "../lib/types";
import { COULEUR_LISTE, couleurAlerte, libelleAlerte } from "../registres/alertes";
import { Tag } from "./Elements";

/** Navires suivis : dernière position et dernière alerte ; un clic ouvre la fiche et centre la carte. */
export default function SuivisPanel({ suivis, onPick }: { suivis: Props[]; onPick: (p: Props) => void }) {
  const S = L.suivis;
  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-hair px-4 pb-3 pt-4"><h2 className="text-[15px] font-semibold">{S.titre}</h2></header>
      {!suivis.length && <p className="px-4 py-6 text-muted">{S.aucun}</p>}
      <ul className="flex-1 overflow-y-auto">
        {suivis.map((v) => (
          <li key={v.vessel_id}>
            <button onClick={() => onPick(v)} className="w-full border-b border-hair/70 px-4 py-2.5 text-left hover:bg-raised/60">
              <span className="flex items-center gap-1.5">
                <span className="font-medium text-ink">{v.name ?? `MMSI ${v.mmsi}`}</span>
                {v.flag && <Tag>{v.flag}</Tag>}
                {v.watch && <Tag color={COULEUR_LISTE}>{L.signal[v.watch]}</Tag>}
              </span>
              <span className="mt-0.5 flex justify-between text-[12px] text-muted">
                <span>{S.dernierePosition} {v.derniere_position ? jourHeure(v.derniere_position) : L.commun.nd}</span>
                {v.alerte_type ? (
                  <span className="flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full" style={{ background: couleurAlerte(v.alerte_type) }} />
                    {libelleAlerte(v.alerte_type)}, {jourHeure(v.alerte_le)}
                  </span>
                ) : <span>{S.aucuneAlerte}</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
