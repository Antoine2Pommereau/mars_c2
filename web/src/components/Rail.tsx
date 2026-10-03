import { Bell, Layers, Radar } from "lucide-react";

export type PanelId = "alertes" | "analyses" | "couches";

const ITEMS: { id: PanelId; label: string; short: string; Icon: typeof Bell }[] = [
  { id: "alertes", label: "Alertes", short: "Alertes", Icon: Bell },
  { id: "analyses", label: "Analyses radar", short: "Analyses", Icon: Radar },
  { id: "couches", label: "Couches et légende", short: "Couches", Icon: Layers },
];

interface Props {
  active: PanelId | null;
  onSelect: (id: PanelId | null) => void;
  alertCount: number;
  running: boolean;
}

export default function Rail({ active, onSelect, alertCount, running }: Props) {
  return (
    <nav className="flex h-full w-[76px] flex-col items-center border-r border-hair bg-panel py-3">
      <div className="mb-4 font-cond text-[13px] font-semibold leading-none tracking-wide text-ink" title="MARS C2">M</div>
      {ITEMS.map(({ id, label, short, Icon }) => (
        <button key={id} title={label} aria-label={label} aria-pressed={active === id}
          onClick={() => onSelect(active === id ? null : id)}
          className={`relative mb-1 flex w-[62px] flex-col items-center gap-1 rounded-md py-2 transition-colors
            ${active === id ? "bg-raised text-ink" : "text-muted hover:text-ink"}`}>
          <span className="relative">
            <Icon size={18} strokeWidth={1.6} />
            {id === "alertes" && alertCount > 0 && (
              <span className="absolute -right-2.5 -top-1.5 min-w-4 rounded-full bg-dark px-1 text-center text-[10px] font-semibold leading-4 text-abyss">
                {alertCount}
              </span>
            )}
            {id === "analyses" && running && <span className="absolute -right-2 -top-1 h-1.5 w-1.5 rounded-full bg-signal" />}
          </span>
          <span className="text-[10px] leading-none">{short}</span>
          {active === id && <span className="absolute left-0 top-1/2 h-7 w-0.5 -translate-y-1/2 rounded bg-signal" />}
        </button>
      ))}
    </nav>
  );
}
