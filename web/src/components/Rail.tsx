import { Bell, Eye, Layers, Radar, Search } from "lucide-react";
import { L } from "../lib/libelles";

export type PanelId = "alertes" | "analyses" | "couches" | "suivis";

const ITEMS: { id: PanelId; label: string; Icon: typeof Bell; aVenir?: boolean }[] = [
  { id: "alertes", label: L.rail.alertes, Icon: Bell },
  { id: "couches", label: L.rail.couches, Icon: Layers },
  { id: "analyses", label: L.rail.analyses, Icon: Radar },
  { id: "suivis", label: L.rail.suivis, Icon: Eye },
];

interface Props {
  active: PanelId | null;
  onSelect: (id: PanelId | null) => void;
  onSearch: () => void;
  alertCount: number;
  running: boolean;
}

export default function Rail({ active, onSelect, onSearch, alertCount, running }: Props) {
  return (
    <nav className="flex h-full w-14 shrink-0 flex-col items-center border-r border-hair bg-panel py-2">
      {ITEMS.map(({ id, label, Icon, aVenir }) => {
        const on = active === id;
        const title = aVenir ? `${label}, ${L.commun.aVenir}` : label;
        return (
          <button key={id} title={title} aria-label={title} aria-pressed={on} disabled={aVenir}
            onClick={() => !aVenir && onSelect(on ? null : id)}
            className={`relative mb-1 flex h-10 w-10 items-center justify-center rounded-md transition-colors
              ${on ? "bg-raised text-ink" : aVenir ? "cursor-default text-faint/60" : "text-muted hover:text-ink"}`}>
            <Icon size={18} strokeWidth={1.6} />
            {id === "alertes" && alertCount > 0 && (
              <span className="absolute right-1 top-1 min-w-4 rounded-full bg-dark px-1 text-[10px] font-semibold leading-4 text-abyss">
                {alertCount}
              </span>
            )}
            {id === "analyses" && running && <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-signal" />}
            {on && <span className="absolute -left-2 top-2 h-6 w-0.5 rounded bg-signal" />}
          </button>
        );
      })}
      <button title={`${L.rail.recherche} (${L.recherche.raccourci})`} aria-label={L.rail.recherche} onClick={onSearch}
        className="mt-auto flex h-10 w-10 items-center justify-center rounded-md text-muted hover:text-ink">
        <Search size={18} strokeWidth={1.6} />
      </button>
    </nav>
  );
}
