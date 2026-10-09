import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { L } from "../lib/libelles";
import type { Props } from "../lib/types";
import { sourcePreuve } from "../registres/preuves";

const P = L.preuveImage;

async function fiche(path: string): Promise<Props> {
  const r = await fetch(`/api${path}`);
  if (!r.ok) throw new Error(`${path} : ${r.status}`);
  return r.json();
}

/** Point de la vignette (pixels) pour une position : géoréférence x = a·lon + b·lat + c, y = d·lon + e·lat + f. */
const pixel = (geo: Props, lon: number, lat: number) =>
  [geo.x[0] * lon + geo.x[1] * lat + geo.x[2], geo.y[0] * lon + geo.y[1] * lat + geo.y[2]] as const;

function Image({ preuve, ais, grand }: { preuve: Props; ais: Props[]; grand: boolean }) {
  const { largeur: w, hauteur: h, geo } = preuve;
  return (
    <span className="relative block" style={{ aspectRatio: `${w} / ${h}` }}>
      <img src={preuve.url} alt={P.alt} className="absolute inset-0 h-full w-full [image-rendering:pixelated]" />
      {geo && (
        <svg viewBox={`0 0 ${w} ${h}`} className="pointer-events-none absolute inset-0 h-full w-full">
          {ais.map((a) => {
            const [x, y] = pixel(geo, a.lon, a.lat);
            if (x < 0 || y < 0 || x > w || y > h) return null;
            return (
              <g key={a.vessel_id}>
                <circle cx={x} cy={y} r={a.apparie ? 6 : 4} fill="none" stroke={a.apparie ? "#e6ecf0" : "#4fb6c8"}
                  strokeWidth={a.apparie ? 2 : 1.4} />
                {(grand || a.apparie) && (
                  <text x={x + 8} y={y + 3} fontSize={grand ? 8 : 9} fill={a.apparie ? "#e6ecf0" : "#4fb6c8"}>
                    {a.name ?? a.mmsi}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      )}
    </span>
  );
}

/** Preuve image d'une détection satellite : vignette (sur R2, relayée par l'API) avec, en surimpression, les positions
 *  AIS à moins de 5 km à l'heure du passage et le navire apparié ; agrandissable au clic. */
export default function PreuveImage({ source, detectionId }: { source: string; detectionId: number }) {
  const s = sourcePreuve(source);
  const q = useQuery({ queryKey: ["viirsDetection", detectionId], queryFn: () => fiche(s!.fiche(detectionId)),
    enabled: !!s && detectionId > 0, staleTime: 60_000 });
  const [grand, setGrand] = useState(false);
  useEffect(() => {
    if (!grand) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setGrand(false); } };
    window.addEventListener("keydown", k, true);
    return () => window.removeEventListener("keydown", k, true);
  }, [grand]);
  const preuve = q.data?.preuve;
  if (!s || !q.data) return null;
  if (!preuve) return <p className="mb-2 text-[12px] text-muted">{P.aucune}</p>;
  const ais: Props[] = q.data.ais_proches ?? [];
  const apparie = ais.find((a) => a.apparie);
  return (
    <div className="mb-2">
      <button onClick={() => setGrand(true)} title={P.agrandir} aria-label={P.agrandir}
        className="block w-full overflow-hidden rounded-md border border-hair hover:border-muted">
        <Image preuve={preuve} ais={ais} grand={false} />
      </button>
      <div className="mt-1 flex justify-between text-[11px] text-faint">
        <span>{P.legende(ais.length, apparie?.name ?? apparie?.mmsi ?? null)}</span>
        <span>{P.taille(Math.round(preuve.octets / 100) / 10)}</span>
      </div>
      {/* Portail : la fiche (flou d'arrière plan) contiendrait sinon l'élément fixe */}
      {grand && createPortal(
        <div role="dialog" aria-label={P.titre} onClick={() => setGrand(false)}
          className="fixed inset-0 z-50 flex items-center justify-center bg-abyss/80 p-6">
          <div onClick={(e) => e.stopPropagation()} className="w-[min(82vh,92vw)] rounded-lg border border-hair bg-panel p-3 shadow-2xl">
            <Image preuve={preuve} ais={ais} grand />
            <div className="mt-2 flex items-center justify-between text-[12px] text-muted">
              <span>{P.legende(ais.length, apparie?.name ?? apparie?.mmsi ?? null)}</span>
              <button onClick={() => setGrand(false)} className="text-ink hover:text-signal">{L.commun.fermer}</button>
            </div>
          </div>
        </div>, document.body)}
    </div>
  );
}
