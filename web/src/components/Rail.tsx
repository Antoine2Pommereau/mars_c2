import { Bell, Layers, Radar } from "lucide-react";

export type PanelId = "alertes" | "analyses" | "couches";

const ITEMS: { id: PanelId; label: string; Icon: typeof Bell }[] = [
  { id: "alertes", label: "Alertes", Icon: Bell },
  { id: "analyses", label: "Analyses radar", Icon: Radar },
  { id: "couches", label: "Couches et légende", Icon: Layers },
];

interface Props {
  active: PanelId | null;
  onSelect: (id: PanelId | null) => void;
  alertCount: number;
  running: boolean;
}

export default function Rail({ active, onSelect, alertCount, running }: Props) {
  return (
    <nav className="flex h-full w-14 flex-col items-center border-r border-hair bg-panel py-3">
      <div className="mb-5 font-cond text-[13px] font-semibold leading-none tracking-wide text-ink" title="MARS C2">M</div>
      {ITEMS.map(({ id, label, Icon }) => (
        <button key={id} title={label} aria-label={label} aria-pressed={active === id}
          onClick={() => onSelect(active === id ? null : id)}
          className={`relative mb-1 flex h-10 w-10 items-center justify-center rounded-md transition-colors
            ${active === id ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
          <Icon size={18} strokeWidth={1.6} />
          {id === "alertes" && alertCount > 0 && (
            <span className="absolute right-1 top-1 min-w-4 rounded-full bg-dark px-1 text-[10px] font-semibold leading-4 text-abyss">
              {alertCount}
            </span>
          )}
          {id === "analyses" && running && <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-signal" />}
          {active === id && <span className="absolute -left-2 top-2 h-6 w-0.5 rounded bg-signal" />}
        </button>
      ))}
    </nav>
  );
}
