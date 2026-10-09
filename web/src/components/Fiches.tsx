import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Eye, EyeOff, Play, Ship } from "lucide-react";
import { useState } from "react";
import { api, gpxUrl, photoUrl, type VesselCard } from "../lib/api";
import { auteurMemorise, memoriserAuteur } from "../lib/auteur";
import { dayLabel, jourHeure, num, utc } from "../lib/format";
import { L } from "../lib/libelles";
import type { Feature, Props } from "../lib/types";
import { zoneAlerte } from "../lib/fil";
import { COULEUR_LISTE, couleurAlerte, libelleAlerte } from "../registres/alertes";
import { Row, Tag } from "./Elements";

const F = L.fiche;
const MIN = 60_000;

function ageTexte(s: number) {
  return s < 120 ? `${Math.round(s)} s` : s < 7200 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`;
}

/** Photo d'un navire : en cas d'échec, le motif donné par l'API (désactivée, absente de la source, source injoignable). */
function usePhoto(vesselId: number) {
  const [etat, setEtat] = useState<"chargement" | "ok" | "absente">("chargement");
  const [motif, setMotif] = useState<string | null>(null);
  const echec = () => {
    setEtat("absente");
    fetch(photoUrl(vesselId)).then((r) => (r.ok ? null : r.json())).then((j) => setMotif(j?.detail ?? null)).catch(() => setMotif(null));
  };
  return { etat, motif, ok: () => setEtat("ok"), echec };
}

function CadrePhoto({ vesselId, className }: { vesselId: number; className: string }) {
  const p = usePhoto(vesselId);
  return (
    <div className="shrink-0">
      <div className={`relative overflow-hidden rounded-md border border-hair bg-abyss ${className}`}>
        {p.etat !== "absente" && (
          <img src={photoUrl(vesselId)} alt={F.photo.alt} onLoad={p.ok} onError={p.echec}
            className="h-full w-full object-cover" style={{ opacity: p.etat === "ok" ? 1 : 0 }} />
        )}
        {p.etat !== "ok" && (
          <span className="absolute inset-0 flex flex-col items-center justify-center gap-1 px-2 text-center text-[11px] text-faint"
            title={p.motif ?? undefined}>
            <Ship size={20} strokeWidth={1.2} />{p.etat === "absente" ? F.photo.aucune : ""}
          </span>
        )}
      </div>
      <div className="mt-0.5 text-right text-[10.5px] text-faint">
        {p.etat === "ok" ? F.photo.source("VesselFinder") : p.etat === "absente" && p.motif ? p.motif : ""}
      </div>
    </div>
  );
}

/** En tête d'une fiche d'alerte qui concerne un navire : photo, nom et pavillon, accès à la fiche du navire. */
export function EnTeteAlerteNavire({ navire, onOuvrir }: { navire: Props; onOuvrir: () => void }) {
  return (
    <div className="flex items-start gap-3">
      <CadrePhoto key={Number(navire.vessel_id)} vesselId={Number(navire.vessel_id)} className="aspect-[16/9] w-[112px]" />
      <button onClick={onOuvrir} className="min-w-0 text-left hover:text-signal">
        <div className="truncate text-[14px] font-semibold text-ink">{navire.name ?? `MMSI ${navire.mmsi ?? ""}`}</div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[12px] text-muted">
          {navire.flag && <Tag>{navire.flag}</Tag>}{navire.mmsi && <span>MMSI {navire.mmsi}</span>}
        </div>
        <div className="mt-1 text-[12px] text-muted">{F.ouvrir}</div>
      </button>
    </div>
  );
}

/** En tête du navire : photo (source indiquée), niveau de signal, état et dernier message, bouton suivre, identité. */
export function EnTeteNavire({ navire, carte, suivi, onSuivre }:
  { navire: Props; carte?: VesselCard; suivi: boolean; onSuivre: (on: boolean) => void }) {
  const id = Number(navire.vessel_id);
  const age = navire.age_s as number | undefined;
  const etat = age == null || age > 1800 ? "silencieux" : (navire.sog_kn ?? 0) < 0.5 ? "immobile" : "route";
  const w = carte?.watch;
  return (
    <div>
      <div className="mb-2"><CadrePhoto key={id} vesselId={id} className="aspect-[16/9] w-full" /></div>
      <div className="mb-2 flex flex-wrap items-center gap-1.5">
        {w && <Tag color={COULEUR_LISTE}>{L.signal[w.level] ?? w.level}</Tag>}
        <Tag>{F.etat[etat]}</Tag>
        <span className="text-[12px] text-muted">{age != null ? F.dernierMessageIlYa(ageTexte(age)) : F.horsTrafic}</span>
        <button onClick={() => onSuivre(!suivi)}
          className={`ml-auto flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[12px] ${suivi ? "border-signal/60 text-signal" : "border-hair text-muted hover:text-ink"}`}>
          {suivi ? <Eye size={13} /> : <EyeOff size={13} />}{suivi ? L.suivis.nePlusSuivre : L.suivis.suivre}
        </button>
      </div>
      <Row label={F.mmsi}>{navire.mmsi ?? carte?.mmsi}</Row>
      <Row label={F.omi}>{carte?.imo ?? L.commun.nd}</Row>
      <Row label={F.pavillon}>{carte?.flag ?? navire.flag ?? L.commun.nd}</Row>
      <Row label={F.type}>{navire.ship_type ?? carte?.ship_type ?? F.nonRenseigne}</Row>
      <Row label={F.longueur}>{(navire.length_m ?? carte?.length_m) ? `${num(navire.length_m ?? carte?.length_m, 0)} m` : L.commun.nd}</Row>
      <Row label={F.indicatif}>{carte?.callsign ?? L.commun.nd}</Row>
      <Row label={F.destination}>{carte?.destination ?? L.commun.nd}</Row>
      {navire.sog_kn != null && <Row label={F.vitesse}>{num(navire.sog_kn)} {L.commun.noeuds}, {num(navire.cog_deg, 0)}°</Row>}
    </div>
  );
}

/** Identités successives en frise compacte : une barre par identité sur la période où elle a été vue. */
export function IdentitesFrise({ rows }: { rows: Props[] }) {
  const t = (x: Props, k: string) => Date.parse(x[k]);
  const a = Math.min(...rows.map((x) => t(x, "first_seen"))), b = Math.max(...rows.map((x) => t(x, "last_seen")));
  const span = Math.max(b - a, MIN);
  return (
    <div className="space-y-1.5 text-[11.5px]">
      {rows.map((x, i) => {
        const left = ((t(x, "first_seen") - a) / span) * 100, width = Math.max(1.5, ((t(x, "last_seen") - t(x, "first_seen")) / span) * 100);
        return (
          <div key={i}>
            <div className="flex justify-between gap-2"><span className="text-ink">{x.name ?? F.sansNom}</span>
              <span className="text-muted">{[x.flag, `MMSI ${x.mmsi}`, x.callsign].filter(Boolean).join(", ")}</span></div>
            <div className="relative mt-0.5 h-1.5 rounded bg-hair/60">
              <span className="absolute h-1.5 rounded bg-muted" style={{ left: `${left}%`, width: `${Math.min(width, 100 - left)}%` }} />
            </div>
          </div>
        );
      })}
      <div className="flex justify-between text-faint"><span>{dayLabel(new Date(a).toISOString())}</span><span>{dayLabel(new Date(b).toISOString())}</span></div>
    </div>
  );
}

/** Comportement sur la plage : silences, arrêts au large, passages à moins de 2 milles d'une infrastructure. */
export function Comportement({ vesselId, debut, fin, onPickInfra }:
  { vesselId: number; debut: string; fin: string; onPickInfra: (id: number) => void }) {
  const q = useQuery({ queryKey: ["comportement", vesselId, debut, fin], queryFn: () => api.comportement(vesselId, debut, fin), retry: 1 });
  const C = F.comportement;
  if (q.isLoading) return <p className="text-muted">{L.commun.chargement}</p>;
  const d = q.data;
  if (!d || (!d.silences.length && !d.arrets.length && !d.passages_infra.length)) return <p className="text-muted">{C.aucun}</p>;
  return (
    <div className="space-y-2 text-[12px]">
      {d.silences.length > 0 && <div><div className="text-muted">{C.silences}</div>
        {d.silences.map((x, i) => <div key={i}>{C.silence(jourHeure(x.debut), x.duree_min)}</div>)}</div>}
      {d.arrets.length > 0 && <div><div className="text-muted">{C.arrets}</div>
        {d.arrets.map((x, i) => <div key={i}>{C.arret(jourHeure(x.debut), x.duree_min)}</div>)}</div>}
      {d.passages_infra.length > 0 && <div><div className="text-muted">{C.passages}</div>
        {d.passages_infra.map((x, i) => (
          <button key={i} onClick={() => onPickInfra(x.infra_id)} className="block text-left hover:text-signal">
            {C.passage(x.name ?? F.infra.sansNom(x.type, x.infra_id), x.duree_min, num(x.vitesse_min), x.distance_min_m)}
          </button>
        ))}</div>}
    </div>
  );
}

/** Trajectoire sur la plage : affichée sur la carte, rejouable, exportable en GPX. */
export function Trajectoire({ vesselId, debut, fin, onRejeu }: { vesselId: number; debut: string; fin: string; onRejeu: () => void }) {
  const T = F.trajectoire;
  return (
    <div className="text-[12px]">
      <p className="text-muted">{T.periode(jourHeure(debut), jourHeure(fin))}, {T.carte.toLowerCase()}</p>
      <div className="mt-2 flex gap-2">
        <button onClick={onRejeu} className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-hair py-1.5 text-ink hover:border-muted">
          <Play size={12} />{T.rejouer}</button>
        <a href={gpxUrl(vesselId, debut, fin)} download className="flex flex-1 items-center justify-center gap-1.5 rounded-md border border-hair py-1.5 text-ink hover:border-muted">
          <Download size={12} />{T.gpx}</a>
      </div>
    </div>
  );
}

/** Notes de l'opérateur, horodatées et signées. */
export function Notes({ vesselId }: { vesselId: number }) {
  const qc = useQueryClient();
  const [texte, setTexte] = useState("");
  const q = useQuery({ queryKey: ["notes", vesselId], queryFn: () => api.notes(vesselId) });
  const add = useMutation({
    mutationFn: () => { const a = auteurMemorise(); memoriserAuteur(a); return api.addNote(vesselId, texte, a); },
    onSuccess: () => { setTexte(""); qc.invalidateQueries({ queryKey: ["notes", vesselId] }); },
  });
  const N = F.notes;
  return (
    <div className="text-[12px]">
      <div className="flex gap-2">
        <input value={texte} onChange={(e) => setTexte(e.target.value)} placeholder={N.placeholder}
          className="min-w-0 flex-1 rounded-md border border-hair bg-abyss px-2.5 py-1.5 text-ink placeholder:text-faint focus:border-signal focus:outline-none" />
        <button disabled={!texte.trim() || add.isPending} onClick={() => add.mutate()}
          className="rounded-md border border-hair px-2.5 text-ink hover:border-muted disabled:opacity-40">{N.ajouter}</button>
      </div>
      {add.isError && <p className="mt-1 text-gap">{(add.error as Error).message}</p>}
      <ul className="mt-2 space-y-1.5">
        {(q.data ?? []).map((n) => (
          <li key={n.id}><span className="text-ink">{n.note}</span>
            <span className="block text-muted">{N.par(n.author, utc(n.at).slice(0, 16))}</span></li>
        ))}
        {q.data && !q.data.length && <li className="text-muted">{N.aucune}</li>}
      </ul>
    </div>
  );
}

/** Liste d'alertes compacte, cliquable (fiches navire, infrastructure, zone). */
export function ListeAlertes({ alerts, onPick }: { alerts: Feature[]; onPick?: (f: Feature) => void }) {
  return (
    <ul className="space-y-1 text-[12px]">
      {alerts.map((a) => (
        <li key={a.properties.id}>
          <button onClick={() => onPick?.(a)} className="flex w-full items-center gap-2 text-left hover:text-ink">
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: couleurAlerte(a.properties.type) }} />
            <span className="flex-1">{libelleAlerte(a.properties.type)}</span>
            <span className="text-muted">{jourHeure(a.properties.event_time)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** Fiche infrastructure : identité, navires passés à moins de 2 milles sur la plage, alertes liées. */
export function useInfraCard(id: number, debut: string, fin: string) {
  return useQuery({ queryKey: ["infraCard", id, debut, fin], queryFn: () => api.infraCard(id, debut, fin), staleTime: 60_000,
    enabled: id > 0 });
}

export function InfraIdentite({ id, debut, fin }: { id: number; debut: string; fin: string }) {
  const d = useInfraCard(id, debut, fin).data;
  const I = F.infra;
  if (!d) return <p className="text-muted">{L.commun.chargement}</p>;
  return (
    <>
      <Row label={I.type}>{d.type}</Row>
      <Row label={I.operateur}>{d.operator ?? L.commun.nd}</Row>
      <Row label={I.longueur}>{d.longueur_km != null ? `${num(d.longueur_km)} km` : L.commun.nd}</Row>
      <Row label={I.zone}>{L.zones[(d.region ?? "").toLowerCase()] ?? d.region}</Row>
      <Row label={I.source}>{d.source ?? L.commun.nd}</Row>
    </>
  );
}

export function InfraNavires({ id, debut, fin, onPickVessel }:
  { id: number; debut: string; fin: string; onPickVessel: (p: Props) => void }) {
  const d = useInfraCard(id, debut, fin).data;
  if (!d) return null;
  if (!d.navires.length) return <p className="text-[12px] text-muted">{F.infra.aucunNavire}</p>;
  return (
    <ul className="space-y-1 text-[12px]">
      {d.navires.map((v: Props) => (
        <li key={v.vessel_id}>
          <button onClick={() => onPickVessel(v)} className="flex w-full items-baseline justify-between gap-2 text-left hover:text-ink">
            <span className="flex items-center gap-1.5">
              <span className="text-ink">{v.name ?? `MMSI ${v.mmsi}`}</span>{v.flag && <Tag>{v.flag}</Tag>}
              {v.watch && <Tag color={COULEUR_LISTE}>{L.signal[v.watch]}</Tag>}
            </span>
            <span className="text-muted">{F.infra.ligne(v.distance_min_m, num(v.vitesse_min), jourHeure(v.debut))}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

export function InfraAlertes({ id, debut, fin, onPickAlert }:
  { id: number; debut: string; fin: string; onPickAlert: (f: Feature) => void }) {
  const d = useInfraCard(id, debut, fin).data;
  if (!d) return null;
  if (!d.alertes.length) return <p className="text-[12px] text-muted">{F.infra.aucuneAlerte}</p>;
  return <ListeAlertes alerts={d.alertes.map((a: Props) => ({ type: "Feature", geometry: null, properties: a }))}
    onPick={(f) => api.alert(f.properties.id).then(onPickAlert)} />;
}

/** Fiche zone : surface, réception fiable, mouillages ; trafic à l'instant et alertes de la plage dans la zone. */
export function ZoneResume({ zone }: { zone: string }) {
  const d = useQuery({ queryKey: ["zoneCard", zone], queryFn: () => api.zoneCard(zone), staleTime: 300_000 }).data;
  const Z = F.zone;
  if (!d) return <p className="text-muted">{L.commun.chargement}</p>;
  const r = d.reception ?? {};
  return (
    <>
      <Row label={Z.surface}>{d.surface_km2?.toLocaleString("fr-FR")} km²</Row>
      <Row label={Z.reception}>{r.cellules ? Z.cellules(r.cellules, r.surface_km2, num((r.continuite ?? 0) * 100, 1)) : Z.aucuneReception}</Row>
      <Row label={Z.mouillages}>{d.mouillages}</Row>
    </>
  );
}

export function ZoneTrafic({ zone, vessels, alerts, onPickAlert }:
  { zone: string; vessels: Props[]; alerts: Feature[]; onPickAlert: (f: Feature) => void }) {
  const dans = alerts.filter((a) => zoneAlerte(a) === zone);
  return (
    <>
      <Row label={F.zone.navires}>{vessels.length}</Row>
      <div className="mt-2 text-muted">{F.zone.alertes}</div>
      <ListeAlertes alerts={dans} onPick={onPickAlert} />
    </>
  );
}

// Passage satellite (étape 3, lot A) : heure, emprise, ce qu'il couvre, état de l'analyse

export function usePassage(id: number) {
  return useQuery({ queryKey: ["passage", id], queryFn: () => api.passage(id), staleTime: 60_000, enabled: id > 0 });
}

export function PassageResume({ id }: { id: number }) {
  const d = usePassage(id).data;
  if (!d) return null;
  const P = F.passage;
  const regions = (d.regions ?? []).map((r: string) => L.zones[r] ?? r).join(", ");
  return (
    <div>
      <Row label={P.heure}>{utc(d.acquired_at)}{d.ended_at && d.ended_at !== d.acquired_at ? ` ${P.a} ${utc(d.ended_at).slice(11)}` : ""}</Row>
      <Row label={P.statut}>{P.statuts[d.statut] ?? d.statut}</Row>
      <Row label={P.capteur}>{P.mode(d.satellite, d.mode)}</Row>
      <Row label={P.orbite}>{P.orbiteDe(d.relative_orbit, d.absolute_orbit, P.sens[d.orbit_direction] ?? d.orbit_direction ?? L.commun.nd)}</Row>
      <Row label={P.emprise}>{`${num(d.surface_km2, 0)} km², ${regions}`}</Row>
      {d.nuages != null && <Row label={P.nuages}>{`${num(d.nuages, 0)} %`}</Row>}
      <Row label={P.analyse}>{P.analyses[d.analyse] ?? d.analyse}</Row>
    </div>
  );
}

export function PassageInfras({ id, onPickInfra }: { id: number; onPickInfra: (id: number) => void }) {
  const d = usePassage(id).data;
  const [tout, setTout] = useState(false);
  if (!d) return null;
  const items: Props[] = d.infrastructures ?? [];
  if (!items.length) return <p className="text-[12px] text-muted">{F.passage.aucuneInfra}</p>;
  const parType = Object.entries(items.reduce((a: Record<string, number>, x) => ({ ...a, [x.type]: (a[x.type] ?? 0) + 1 }), {}));
  // EMODnet nomme « Onbekend » (inconnu, en néerlandais) une partie des câbles : ils passent après les tracés nommés
  const nomme = (x: Props) => !!x.name && x.name !== "Onbekend";
  const montres = tout ? items : items.filter(nomme).slice(0, 12);
  return (
    <div className="text-[12px]">
      <div className="mb-1.5 text-muted">{parType.map(([t, n]) => `${n} ${t.toLowerCase()}`).join(", ")}</div>
      <ul className="space-y-0.5">
        {montres.map((x) => (
          <li key={x.id}>
            <button onClick={() => onPickInfra(x.id)} className="text-left text-ink hover:text-signal">
              {nomme(x) ? x.name : F.infra.sansNom(x.type, x.id)}
            </button>
          </li>
        ))}
      </ul>
      {!tout && items.length > montres.length && (
        <button onClick={() => setTout(true)} className="mt-1 text-muted hover:text-ink">{F.passage.toutes(items.length)}</button>
      )}
    </div>
  );
}

export function PassageListes({ id, onPickVessel }: { id: number; onPickVessel: (p: Props) => void }) {
  const d = usePassage(id).data;
  if (!d) return null;
  if (d.statut === "prevu") return <p className="text-[12px] text-muted">{F.passage.listesApres}</p>;
  if (!d.navires.length) return <p className="text-[12px] text-muted">{F.passage.aucunNavire}</p>;
  return (
    <ul className="space-y-1 text-[12px]">
      {d.navires.map((v: Props) => (
        <li key={v.vessel_id}>
          <button onClick={() => onPickVessel(v)} className="flex items-center gap-1.5 text-left hover:text-ink">
            <span className="text-ink">{v.name ?? `MMSI ${v.mmsi}`}</span>{v.flag && <Tag>{v.flag}</Tag>}
            {v.watch && <Tag color={COULEUR_LISTE}>{L.signal[v.watch]}</Tag>}
          </button>
        </li>
      ))}
    </ul>
  );
}

// Détection nocturne VIIRS (étape 3, lot B) : heure, intensité, navire AIS apparié ou absence d'appariement, alertes

function useViirs(id: number) {
  return useQuery({ queryKey: ["viirsDetection", id], queryFn: () => api.viirsDetection(id), staleTime: 60_000, enabled: id > 0 });
}

export function ViirsMesures({ id }: { id: number }) {
  const d = useViirs(id).data;
  if (!d) return null;
  const V = F.viirs;
  return (
    <div>
      <Row label={V.heure}>{utc(d.ts)}</Row>
      <Row label={V.capteur}>{V.satellites[d.satellite] ?? d.satellite}</Row>
      <Row label={V.intensite}>{`${num(d.nanowatts, 1)} nW/cm²/sr`}</Row>
      <Row label={V.lune}>{d.lune != null ? `${num(d.lune, 0)} %` : L.commun.nd}</Row>
      <Row label={V.statut}>{V.statuts[d.statut] ?? d.statut}{d.mask_reason ? ` (${V.motifs[d.mask_reason] ?? d.mask_reason})` : ""}</Row>
      <Row label={V.cote}>{d.distance_cote_m != null ? `${num(d.distance_cote_m / 1000, 1)} km` : L.commun.nd}</Row>
      <Row label={V.aisProche}>{d.ais_proche_m != null ? V.aisProcheDe(d.ais_proche_m, d.ais_proche_ecart_s) : V.aucunAis}</Row>
      {d.ais_navires_rayon != null && <Row label={V.reception}>{V.receptionDe(d.ais_navires_rayon)}</Row>}
      {d.non_evaluable && <Row label={V.motifNonEvaluable}>{d.non_evaluable}</Row>}
    </div>
  );
}

export function ViirsNavire({ id, onPickVessel }: { id: number; onPickVessel: (p: Props) => void }) {
  const d = useViirs(id).data;
  if (!d) return null;
  if (!d.vessel_id) return <p className="text-[12px] text-muted">{F.viirs.aucunNavire}</p>;
  return (
    <button onClick={() => onPickVessel({ vessel_id: d.vessel_id, name: d.name, mmsi: d.mmsi, flag: d.flag })}
      className="flex w-full items-center gap-2 text-left text-[12px] hover:text-ink">
      <Vignette vesselId={d.vessel_id} />
      <span className="text-ink">{d.name ?? `MMSI ${d.mmsi}`}</span>{d.flag && <Tag>{d.flag}</Tag>}
      {d.watch && <Tag color={COULEUR_LISTE}>{L.signal[d.watch]}</Tag>}
      <span className="ml-auto text-muted">{F.viirs.ecart(num(d.match_distance_m, 0))}</span>
    </button>
  );
}

export function ViirsAlertes({ id, onPickAlert }: { id: number; onPickAlert: (f: Feature) => void }) {
  const d = useViirs(id).data;
  if (!d) return null;
  if (!d.alertes.length) return <p className="text-[12px] text-muted">{F.infra.aucuneAlerte}</p>;
  return (
    <ul className="space-y-1 text-[12px]">
      {d.alertes.map((a: Props) => (
        <li key={a.id}>
          <button onClick={() => api.alert(a.id).then(onPickAlert).catch(() => undefined)}
            className="flex w-full items-center gap-1.5 text-left hover:text-ink">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: couleurAlerte(a.type) }} />
            <span className="text-ink">{libelleAlerte(a.type)}</span>
            <span className="ml-auto text-muted">{L.gravite[a.severity]}, {L.statut[a.status]}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** Vignette d'un navire (photo de la source, emplacement neutre sinon) ; `taille` : « petite » dans le fil. */
export function Vignette({ vesselId, taille = "moyenne", source = false }:
  { vesselId: number; taille?: "petite" | "moyenne"; source?: boolean }) {
  const [etat, setEtat] = useState<"chargement" | "ok" | "absente">("chargement");
  const box = taille === "petite" ? "h-[18px] w-[28px]" : "h-[30px] w-[48px]";
  return (
    <span className="flex shrink-0 flex-col items-end">
      <span className={`relative ${box} overflow-hidden rounded-sm border border-hair bg-abyss`}>
        {etat !== "absente" && (
          <img src={photoUrl(vesselId)} alt="" loading="lazy" onLoad={() => setEtat("ok")} onError={() => setEtat("absente")}
            className="h-full w-full object-cover" style={{ opacity: etat === "ok" ? 1 : 0 }} />
        )}
        {etat !== "ok" && <span className="absolute inset-0 flex items-center justify-center text-faint"><Ship size={taille === "petite" ? 10 : 13} strokeWidth={1.3} /></span>}
      </span>
      {source && etat === "ok" && <span className="text-[9.5px] leading-tight text-faint">VesselFinder</span>}
    </span>
  );
}
