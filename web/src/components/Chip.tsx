import { useState } from "react";
import { chipUrl } from "../lib/api";

/** Vignette radar (polarisation VV) centrée sur un point, avec réticule et échelle. */
export default function Chip({ lon, lat, time, sizeM = 800 }: { lon: number; lat: number; time: string; sizeM?: number }) {
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  return (
    <figure className="mt-3">
      <div className="relative aspect-square w-full overflow-hidden rounded-md border border-hair bg-abyss">
        <img src={chipUrl(lon, lat, time, sizeM)} alt="Vignette radar autour de l'écho"
          onLoad={() => setState("ok")} onError={() => setState("error")}
          className="h-full w-full object-cover" style={{ imageRendering: "pixelated", opacity: state === "ok" ? 1 : 0 }} />
        {state === "ok" && (
          <>
            <span className="pointer-events-none absolute left-1/2 top-1/2 h-10 w-10 -translate-x-1/2 -translate-y-1/2 rounded-full border border-signal/80" />
            <span className="absolute bottom-2 left-2 flex items-end gap-1.5 text-[11px] text-ink">
              <span className="block h-1.5 w-[25%] min-w-12 border-x border-b border-ink" />
              {sizeM / 4} m
            </span>
          </>
        )}
        {state !== "ok" && (
          <span className="absolute inset-0 flex items-center justify-center p-4 text-center text-[12px] text-muted">
            {state === "loading" ? "Chargement de l'image radar…" : "Image radar indisponible (service d'inférence arrêté ?)"}
          </span>
        )}
      </div>
      <figcaption className="mt-1 text-[11.5px] text-muted">Sentinel 1, polarisation VV, {sizeM} m de côté</figcaption>
    </figure>
  );
}
