import { useQuery } from "@tanstack/react-query";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { type ReactNode, useEffect, useMemo, useState } from "react";
import { api, type Dossier, photoUrl } from "../lib/api";
import { num, utc } from "../lib/format";
import { L } from "../lib/libelles";
import { COULEURS_PREUVE, emprise, type Preuve, preuveGeoJSON } from "../lib/preuve";
import { FOND_CARTE, teinter } from "../lib/theme";
import type { Props } from "../lib/types";
import { coord, distance, gdh } from "../lib/unites";
import { Sources } from "./Elements";
import { Logotype } from "./Logotype";
import PreuveImage from "./PreuveImage";
import { chronologie, Legende } from "./Recit";

// Fiche de signalement (lot UX2) : ce qu'une autorité transmet à une autre à propos d'un navire. Page dédiée
// (/signalement?cle=…&instant=…), imprimable en A4 par le navigateur (aucune dépendance serveur nouvelle), toujours
// sur fond clair. Mêmes faits et mêmes chiffres que le récit du dossier ; en démonstration, les noms sont déjà
// masqués par l'API et aucune photo n'est montrée.

const S = L.signalement;
const CP = teinter(COULEURS_PREUVE, "clair");
const ROUTE_CLAIRE = "#5b6b78";

function Section({ titre, children }: { titre: string; children: ReactNode }) {
  return (
    <section className="signalement-section mt-6 break-inside-avoid">
      <h2 className="mb-2 border-b border-hair pb-1 text-[13px] font-semibold uppercase tracking-[0.08em] text-ink">{titre}</h2>
      {children}
    </section>
  );
}

function Ligne({ libelle, children }: { libelle: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[11rem_1fr] gap-3 py-0.5 text-[12.5px]">
      <span className="text-muted">{libelle}</span><span className="text-ink">{children}</span>
    </div>
  );
}

/** Carte statique : trajectoire et points de la preuve rendus par MapLibre hors de l'écran, exportés en image au moment
 *  de l'édition (le document imprimé n'a aucune carte interactive). */
function CarteStatique({ preuve, route, detections, onPrete }:
  { preuve: Preuve | null; route: [number, number, string][]; detections: Props[]; onPrete: () => void }) {
  const [image, setImage] = useState<string | null>(null);
  const fc = useMemo(() => {
    const g = preuveGeoJSON(preuve, route);
    return { ...g, features: [...g.features, ...detections.map((d) => ({ type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [d.lon, d.lat] }, properties: { role: "detection" } }))] };
  }, [preuve, route, detections]);
  const bornes = useMemo(() => {
    const pts = [...route.map((r) => [r[0], r[1]]), ...detections.map((d) => [d.lon, d.lat])];
    const e = emprise(preuve);
    if (e) pts.push([e[0], e[1]], [e[2], e[3]]);
    if (!pts.length) return null;
    const lons = pts.map((p) => p[0]), lats = pts.map((p) => p[1]);
    return [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]] as [[number, number], [number, number]];
  }, [preuve, route, detections]);

  useEffect(() => {
    if (!bornes) { onPrete(); return; }
    const div = document.createElement("div");
    div.style.cssText = "position:fixed;left:-20000px;top:0;width:1400px;height:820px";
    document.body.appendChild(div);
    const map = new maplibregl.Map({ container: div, style: FOND_CARTE.clair, bounds: bornes, interactive: false,
      fitBoundsOptions: { padding: 90, maxZoom: 12 }, attributionControl: false, preserveDrawingBuffer: true } as never);
    const role = (r: string): never => ["==", ["get", "role"], r] as never;
    map.on("load", () => {
      map.addSource("p", { type: "geojson", data: fc as never });
      map.addLayer({ id: "route", type: "line", source: "p", filter: role("route"), paint: { "line-color": ROUTE_CLAIRE, "line-width": 2 } });
      map.addLayer({ id: "cercle", type: "line", source: "p", filter: role("cercle"), paint: { "line-color": CP.cercle, "line-width": 1.2, "line-opacity": 0.7 } });
      map.addLayer({ id: "silence", type: "line", source: "p", filter: role("silence"), paint: { "line-color": CP.silence, "line-width": 2, "line-dasharray": [2, 2] } });
      map.addLayer({ id: "ecart", type: "line", source: "p", filter: role("ecart"), paint: { "line-color": CP.ecart, "line-width": 1 } });
      map.addLayer({ id: "derniere", type: "circle", source: "p", filter: role("derniere"),
        paint: { "circle-radius": 5, "circle-color": CP.derniere, "circle-stroke-width": 1.5, "circle-stroke-color": "#ffffff" } });
      map.addLayer({ id: "echo", type: "circle", source: "p", filter: ["any", role("echo"), role("detection")] as never,
        paint: { "circle-radius": 8, "circle-color": "rgba(0,0,0,0)", "circle-stroke-width": 2.5, "circle-stroke-color": CP.echo } });
      map.once("idle", () => {
        try { setImage(map.getCanvas().toDataURL("image/png")); } catch { /* fond refusé : pas d'image */ }
        map.remove(); div.remove(); onPrete();
      });
    });
    map.on("error", () => undefined);
    return () => { try { map.remove(); } catch { /* déjà retirée */ } div.remove(); };
  }, [fc, bornes]);

  if (!bornes) return <p className="text-[12.5px] text-muted">{S.carteAbsente}</p>;
  return image ? <img src={image} alt={S.carte(gdh(Date.now()))} className="w-full rounded border border-hair" />
    : <div className="h-[18rem] rounded border border-hair bg-raised" aria-busy="true" />;
}

/** Légende d'une vignette : capteur, heure, AIS le plus proche (mètres sous 0,1 mille), score, longueur mesurée. */
function LegendeVignette({ d }: { d: Props }) {
  const q = useQuery({ queryKey: ["signalement", "detection", d.source, d.id],
    queryFn: () => (d.source === "viirs" ? api.viirsDetection(d.id) : api.sentinelDetection(d.id)), staleTime: 60_000 });
  const f = q.data;
  const proche = f?.ais_proches?.length ? Math.min(...f.ais_proches.map((a: Props) => a.distance_m))
    : f?.ais_proche_m ?? null;
  const capteur = f?.satellite ?? ({ sentinel1: "Sentinel 1", sentinel2: "Sentinel 2", viirs: "VIIRS" } as Record<string, string>)[d.source] ?? d.source;
  return (
    <p className="mt-1 text-[11.5px] text-muted">
      {S.legendeVignette(capteur, utc(d.ts), proche != null ? S.aisProche(distance(proche, true)) : S.aucunAis,
        num(d.score, 2), d.length_m ? `${Math.round(d.length_m)} m` : null)}
    </p>
  );
}

export default function Signalement() {
  const q = new URLSearchParams(window.location.search);
  const cle = q.get("cle") ?? "", instant = q.get("instant") ?? new Date().toISOString(), aLInstant = q.get("a_l_instant");
  const [edition] = useState(() => Date.now());
  const [cartePrete, setCartePrete] = useState(false);
  useEffect(() => { document.documentElement.dataset.theme = "light"; document.documentElement.style.colorScheme = "light"; }, []);

  const dq = useQuery({ queryKey: ["signalement", cle, instant, aLInstant], queryFn: () => api.dossier(cle, instant, aLInstant),
    enabled: !!cle, retry: 1 });
  const demo = useQuery({ queryKey: ["controle", "demo"], queryFn: api.controle.demo });
  const session = useQuery({ queryKey: ["controle", "session"], queryFn: api.controle.session });
  const recit = dq.data;
  const d = recit as unknown as Dossier | undefined;
  const carte = useQuery({ queryKey: ["vessel", d?.vessel_id], queryFn: () => api.vessel(d!.vessel_id!),
    enabled: d?.vessel_id != null });
  useEffect(() => { if (d) document.title = `MARS C2 · ${S.onglet(d.nom)}`; }, [d?.nom]);
  const masque = demo.data?.masquage ?? true;            // tant que le mode n'est pas connu : aucune photo

  if (dq.isError || (!cle)) return <main className="mx-auto max-w-[190mm] p-8 text-ink">{S.erreur}</main>;
  if (!d || !recit) return <main className="mx-auto max-w-[190mm] p-8" aria-busy="true" />;

  const v = carte.data;
  const faits = chronologie(d, recit);
  const alertes: Props[] = recit.alertes_detail ?? [];
  const detections: Props[] = recit.detections ?? [];
  // Rendez vous : l'autre navire de chaque rencontre (noms et numéros déjà pseudonymisés par l'API en démonstration)
  const rencontres = [...new Map(alertes.filter((a) => a.type === "RENDEZVOUS").flatMap((a) => a.details?.navires ?? [])
    .filter((n: Props) => n && n.vessel_id !== d.vessel_id).map((n: Props) => [n.vessel_id, n])).values()];
  const anciens = [...new Set((v?.identities ?? []).map((i) => i.name).filter((n): n is string => !!n && n !== v?.name))];
  const sources = new Set<string>(["ais"]);
  for (const x of detections) sources.add(x.source);
  for (const a of alertes) if (a.type === "WATCHLIST" || a.type === "IDENTITY_CHANGE") sources.add("listes");
  if (v?.watch) sources.add("listes");
  if (alertes.some((a) => JSON.stringify(a.details ?? {}).includes("infrastructure"))) sources.add("infrastructures");

  return (
    <main className="signalement mx-auto max-w-[190mm] bg-panel px-10 py-8 text-ink print:max-w-none print:bg-white print:p-0"
      data-signalement={cle} data-pret={cartePrete || undefined}>
      <div className="mb-4 flex justify-end print:hidden">
        <button onClick={() => window.print()}
          className="rounded-md border border-hair bg-raised px-3 py-1.5 text-[12.5px] text-ink hover:border-muted">{S.imprimer}</button>
      </div>

      <header className="flex items-start justify-between gap-6 border-b-2 border-ink pb-3">
        <div>
          <Logotype taille={30} />
          <h1 className="mt-3 text-[20px] font-semibold">{S.titre}</h1>
          <p className="text-[12.5px] text-muted">{S.dossier(d.cle)} · {S.edite(gdh(edition))} · {S.auteur(session.data?.operateur ?? null)}</p>
        </div>
        <p className="mt-1 whitespace-nowrap text-right text-[10px] uppercase tracking-[0.14em] text-muted">{L.app.mention}</p>
      </header>

      <Section titre={S.identite}>
        {d.genre === "point" ? (
          <>
            <Ligne libelle={S.noms}>{S.sansNavire}</Ligne>
            <Ligne libelle={S.position}>{coord(d.lon, d.lat)}</Ligne>
          </>
        ) : (
          <div className="flex gap-6">
            <div className="min-w-0 flex-1">
              <Ligne libelle={S.noms}>{v?.name ?? d.nom}{anciens.length ? <span className="text-muted"> ({S.nomsAnciens(anciens)})</span> : null}</Ligne>
              <Ligne libelle="MMSI">{v?.mmsi ?? L.commun.nd}</Ligne>
              <Ligne libelle="OMI">{v?.imo ?? L.commun.nd}</Ligne>
              <Ligne libelle={S.pavillon}>{v?.flag ?? d.pavillon ?? L.commun.nd}</Ligne>
              <Ligne libelle={S.type}>{v?.ship_type ?? d.type_navire ?? L.commun.nd}</Ligne>
              <Ligne libelle={S.longueur}>{(v?.length_m ?? d.longueur_m) ? `${Math.round((v?.length_m ?? d.longueur_m)!)} m` : L.commun.nd}</Ligne>
              {rencontres.length > 0 && <Ligne libelle={S.rencontre}>{rencontres.map((n) => n.name ?? `MMSI ${n.mmsi}`).join(", ")}</Ligne>}
              <Ligne libelle={S.listes}>{v?.watch ? <Sources entries={v.watch.entries} /> : S.aucuneListe}</Ligne>
            </div>
            {!masque && d.vessel_id != null && (
              <figure className="w-[52mm] shrink-0">
                <img src={photoUrl(d.vessel_id)} alt="" className="w-full rounded border border-hair"
                  onError={(e) => ((e.target as HTMLImageElement).parentElement!.style.display = "none")} />
                <figcaption className="mt-0.5 text-right text-[10px] text-muted">{S.photoSource}</figcaption>
              </figure>
            )}
          </div>
        )}
      </Section>

      <Section titre={S.resume}>
        <p className="text-[14px] leading-snug">{d.phrase}</p>
        <Ligne libelle={S.niveau}>{L.veille.niveaux[d.niveau_cle] ?? d.niveau_cle}</Ligne>
      </Section>

      <Section titre={S.chronologie}>
        {faits.length ? (
          <ol className="space-y-1 text-[12.5px]">
            {faits.map((f, k) => (
              <li key={k} className="grid grid-cols-[11rem_1fr] gap-3 break-inside-avoid">
                <span className="tabular-nums text-muted">{utc(f.t)}</span>
                <span>{f.texte}{f.detail && <span className="text-muted">, {f.detail}</span>}{f.alerte != null && <span className="text-muted"> (alerte {f.alerte})</span>}</span>
              </li>
            ))}
          </ol>
        ) : <p className="text-[12.5px] text-muted">{S.aucunFait}</p>}
      </Section>

      <Section titre={S.preuves}>
        <CarteStatique preuve={recit.preuve ?? null} route={recit.route ?? []} detections={detections} onPrete={() => setCartePrete(true)} />
        <p className="mt-1 text-[11px] text-muted">{S.carte(gdh(edition))}</p>
        {recit.preuve && <div className="mt-2"><Legende preuve={recit.preuve} theme="clair" /></div>}
        {detections.length > 0 && (
          <>
            <h3 className="mt-4 break-after-avoid text-[12.5px] font-semibold">{S.vignettes}</h3>
            <div className="mt-1 grid grid-cols-2 gap-4">
              {detections.map((x) => (
                <figure key={`${x.source}:${x.id}`} className="break-inside-avoid">
                  <PreuveImage source={x.source} detectionId={x.id} />
                  <LegendeVignette d={x} />
                </figure>
              ))}
            </div>
          </>
        )}
      </Section>

      <Section titre={S.tracabilite}>
        <table className="w-full text-left text-[11.5px]">
          <thead className="text-muted">
            <tr><th className="py-0.5 font-normal">{S.alerte}</th><th className="font-normal">{S.gravite}</th><th className="font-normal">{S.regles}</th>
              <th className="font-normal">{S.trace}</th><th className="font-normal">{S.empreinte}</th></tr>
          </thead>
          <tbody className="font-mono text-[10.5px]">
            {alertes.map((a) => (
              <tr key={a.id} className="border-t border-hair/70">
                <td className="py-0.5 font-sans text-[11.5px]">{a.id}, {a.type}</td>
                <td className="font-sans text-[11.5px]">{L.gravite[a.severity] ?? a.severity}</td>
                <td>{a.rule_version}</td><td>{a.details?.trace_id ?? L.commun.nd}</td><td>{a.controle_empreinte ?? L.commun.nd}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3 className="mt-3 text-[12.5px] font-semibold">{S.sourcesDonnees}</h3>
        <ul className="mt-0.5 list-disc pl-5 text-[12px] text-ink/90">
          {[...sources].map((s) => S.sourcesListe[s] && <li key={s}>{S.sourcesListe[s]}</li>)}
        </ul>
      </Section>

      <Section titre={S.mentions}>
        <ul className="list-disc space-y-0.5 pl-5 text-[12px] text-ink/90">{S.mentionsTexte.map((m) => <li key={m}>{m}</li>)}</ul>
      </Section>
    </main>
  );
}
