import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, type Region } from "../lib/api";
import { L } from "../lib/libelles";
import { ZONE_KEYS } from "../lib/zones";
import { Logotype } from "./Logotype";
import { COULEUR_NIVEAU, mesurer, systeme } from "../registres/etat";

interface Props {
  now: number; connected: boolean; onSearch: () => void;
  region: string | null; regions: Region[] | null; onRegion: (r: string | null) => void;
}

/** État de la plateforme, lu dans /api/ingestion toutes les 30 s (même cache pour la barre, les Coulisses et l'état
 *  dégradé de la Veille) */
export function useEtat(region: string | null) {
  return useQuery({ queryKey: ["ingestion", region], queryFn: () => api.ingestion(region), refetchInterval: 30_000 });
}

/** Liste des indicateurs, chacun vert, orange ou rouge ; son détail se déplie au clic */
export function ListeIndicateurs({ status, now, ids }: { status: any; now: number; ids?: string[] }) {
  const [ouvert, setOuvert] = useState<string | null>(null);
  return (
    <ul>
      {mesurer(status, now).filter(({ ind }) => !ids || ids.includes(ind.id)).map(({ ind, m }) => (
        <li key={ind.id} className="border-t border-hair/70 first:border-t-0">
          <button onClick={() => setOuvert(ouvert === ind.id ? null : ind.id)} aria-expanded={ouvert === ind.id}
            className="flex w-full items-center gap-2 py-1.5 text-left text-muted hover:text-ink">
            <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: COULEUR_NIVEAU[m.niveau] }} />
            <span className="flex-1 text-ink">{ind.libelle}</span>
            <span className="tabular-nums text-faint">{m.resume}</span>
          </button>
          {ouvert === ind.id && m.detail.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-3 py-0.5 pl-3.5 text-[11.5px]">
              <span className="text-muted">{k}</span><span className="text-right text-ink">{v}</span>
            </div>
          ))}
        </li>
      ))}
    </ul>
  );
}

/** Barre d'état (lot U) : la région affichée, puis un seul voyant Système, vert quand tout fonctionne ; sinon il nomme
 *  la cause. Les indicateurs détaillés se déplient au clic (et dans les Coulisses). */
export default function BarreEtat({ now, connected, onSearch, region, regions, onRegion }: Props) {
  const q = useEtat(region);
  const cles = regions?.map((r) => r.key) ?? ZONE_KEYS;
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, []);
  const status = q.isError ? null : q.data;
  const { niveau, cause } = systeme(mesurer(status, now));
  const E = L.etat;

  return (
    <header ref={ref} className="relative z-20 flex h-11 shrink-0 items-center gap-1 border-b border-hair bg-panel px-3 text-[12px]">
      <span className="mr-4"><Logotype taille={24} /></span>
      <select aria-label={L.region.titre} title={L.region.titre} value={region ?? ""} onChange={(e) => onRegion(e.target.value || null)}
        className={`mr-2 rounded border bg-abyss px-1.5 py-0.5 text-[12px] ${region ? "border-signal/60 text-ink" : "border-hair text-muted"}`}>
        <option value="">{L.region.france}</option>
        {cles.map((k) => <option key={k} value={k}>{L.zones[k] ?? k}</option>)}
      </select>
      <div className="relative">
        <button onClick={() => setOpen(!open)} aria-expanded={open} data-systeme={niveau}
          title={`${E.systeme} : ${cause ? `${cause.ind.libelle}, ${E.niveau[niveau]}` : E.niveau[niveau]}`}
          className={`flex items-center gap-1.5 rounded px-2 py-1 ${open ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: COULEUR_NIVEAU[niveau] }} />
          {E.systeme}
          {cause && <span className="tabular-nums" style={{ color: COULEUR_NIVEAU[niveau] }}>{cause.ind.libelle} {cause.m.resume}</span>}
        </button>
        {open && (
          <div className="absolute left-0 top-8 w-80 rounded-md border border-hair bg-panel p-3 shadow-2xl">
            <div className="mb-1.5 flex items-center justify-between">
              <span className="font-semibold text-ink">{E.titre}</span>
              <span style={{ color: COULEUR_NIVEAU[niveau] }}>{niveau === "vert" ? E.toutVa : E.niveau[niveau]}</span>
            </div>
            <ListeIndicateurs status={status} now={now} />
          </div>
        )}
      </div>
      {!connected && <span className="ml-2 text-gap">{L.commun.reconnexion}</span>}
      <span className="ml-auto mr-3 hidden text-[10px] uppercase tracking-[0.14em] text-muted lg:inline">{L.app.mention}</span>
      <button onClick={onSearch} className="flex items-center gap-2 rounded-md border border-hair px-2.5 py-1 text-muted hover:text-ink">
        <Search size={13} /> {L.recherche.titre} <span className="text-faint">{L.recherche.raccourci}</span>
      </button>
    </header>
  );
}
