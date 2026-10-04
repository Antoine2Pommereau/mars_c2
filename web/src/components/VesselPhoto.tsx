import { useState } from "react";
import { vesselPhotoUrl } from "../lib/api";
import { num } from "../lib/format";

/** Repli hors ligne : silhouette schématique à l'échelle de la longueur déclarée. */
function Silhouette({ shipType, lengthM }: { shipType?: string | null; lengthM?: number | null }) {
  return (
    <div className="flex aspect-[3/2] w-full flex-col items-center justify-center gap-2 bg-abyss text-muted">
      <svg viewBox="0 0 120 32" className="w-3/5 text-hair" fill="currentColor">
        <path d="M4 14 L96 14 Q116 16 96 26 L14 26 Q4 26 4 20 Z" />
        <rect x="30" y="4" width="34" height="10" rx="1" />
      </svg>
      <span className="text-[11.5px]">
        {shipType ?? "Navire"}{lengthM ? `, ${num(lengthM, 0)} m` : ""}
      </span>
    </div>
  );
}

/** Photo AIS du navire par MMSI ; silhouette si aucune photo ou hors ligne. */
export default function VesselPhoto({ mmsi, shipType, lengthM, className = "mt-3" }: {
  mmsi?: number | null; shipType?: string | null; lengthM?: number | null; className?: string;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <figure className={className}>
      <div className="aspect-[3/2] w-full overflow-hidden rounded-md border border-hair bg-abyss">
        {mmsi && !failed ? (
          <img src={vesselPhotoUrl(mmsi)} alt="Photo AIS du navire" onError={() => setFailed(true)}
            className="h-full w-full object-cover" />
        ) : (
          <Silhouette shipType={shipType} lengthM={lengthM} />
        )}
      </div>
      <figcaption className="mt-1 text-[11.5px] text-muted">
        {mmsi && !failed ? "Photo AIS" : "Pas de photo, silhouette à l'échelle"}
      </figcaption>
    </figure>
  );
}
