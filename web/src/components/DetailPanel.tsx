import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { api } from "../lib/api";
import type { Props, Selection } from "../lib/types";
import { ALERT_COLOR, ALERT_LABEL, MATCHED_BY, SEVERITY, STATUS_LABEL, WATCH_COLOR, WATCH_LABEL, dayLabel, hm, num, utc } from "../lib/format";
import AlertActions from "./AlertActions";
import Chip from "./Chip";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex justify-between gap-4 border-b border-hair/70 py-1.5">
      <span className="text-muted">{label}</span><span className="text-right">{children}</span>
    </div>
  );
}

function Context({ items }: { items?: string[] }) {
  if (!items?.length) return null;
  return (
    <div className="mt-3">
      <div className="mb-1 font-semibold">Contexte</div>
      <ul className="list-disc space-y-1 pl-5 text-ink/85">{items.map((t, i) => <li key={i}>{t}</li>)}</ul>
    </div>
  );
}

function vessel(v?: Props) {
  if (!v) return "inconnu";
  return `${v.name ?? "Sans nom"} (MMSI ${v.mmsi}${v.ship_type ? `, ${v.ship_type}` : ""}${v.length_m ? `, ${num(v.length_m, 0)} m` : ""})`;
}

function AlertBody({ p }: { p: Props }) {
  const d = p.details ?? {};
  switch (p.type) {
    case "DARK_SHIP":
      return (
        <>
          <Row label="Longueur estimée">{num(d.length_m, 0)} m</Row>
          <Row label="Contraste local VV">{num(d.contrast_vv_db)} dB (seuil {num(d.parametres?.seuil_contraste_db, 0)})</Row>
          <Row label="Score de présence">{num(d.objectness, 2)}</Row>
          <Row label="Score navire">{num(d.vessel_score, 2)}</Row>
          <Row label="Instant du passage">{utc(p.event_time)}</Row>
          {d.reclassement && <p className="mt-3 rounded-md bg-raised p-2.5 text-ink/90">{d.reclassement}</p>}
          <div className="mt-3 font-semibold">Navires AIS examinés</div>
          {(d.candidats_ais ?? []).length ? (
            <table className="mt-1 w-full text-left">
              <thead className="text-muted"><tr><th>Navire</th><th>Distance</th><th>Tolérance</th></tr></thead>
              <tbody>{d.candidats_ais.map((c: Props) => (
                <tr key={c.mmsi} className="border-t border-hair/70">
                  <td>{c.name ?? c.mmsi}</td><td>{c.distance_m} m</td><td>{c.tolerance_le_long_m ?? c.rayon_tolere_m} m</td>
                </tr>))}</tbody>
            </table>
          ) : <p className="text-muted">Aucun navire AIS à proximité.</p>}
        </>
      );
    case "AIS_UNCONFIRMED":
      return (
        <>
          <Row label="Navire">{vessel(d.navire)}</Row>
          <Row label="Vitesse déclarée">{num(d.navire?.sog_kn)} nœuds</Row>
          <Row label="Position à l'instant du passage">{d.methode_position}</Row>
          <Row label="Écho radar le plus proche">{d.echo_le_plus_proche_m == null ? "aucun" : `${d.echo_le_plus_proche_m} m`}</Row>
          <Row label="Tolérance">{d.tolerance_le_long_m} m le long de la trace, {d.tolerance_en_travers_m} m en travers</Row>
          <Context items={d.contexte} />
        </>
      );
    case "RENDEZVOUS": {
      const [v1, v2] = d.navires ?? [];
      return (
        <>
          <Row label="Navire 1">{vessel(v1)}</Row>
          <Row label="Navire 2">{vessel(v2)}</Row>
          <Row label="Rencontre">{hm(d.debut)} à {hm(d.fin)} UTC, {d.duree_min} min</Row>
          <Row label="Distance">{d.distance_min_m} m au plus près, {d.distance_moyenne_m} m en moyenne</Row>
          <Row label="Distance à la côte">{d.distance_cote_km == null ? "n.d." : `${num(d.distance_cote_km)} km`}</Row>
          <Context items={d.contexte} />
        </>
      );
    }
    case "AIS_GAP":
      return (
        <>
          <Row label="Navire">{vessel(d.navire)}</Row>
          <Row label="Dernier message">{hm(d.dernier_message)} UTC, à {num(d.vitesse_avant_kn)} nœuds</Row>
          <Row label="Réapparition">{d.reapparition ? `${hm(d.reapparition)} UTC` : "aucune dans la journée"}</Row>
          <Row label="Silence">{d.duree_min} min{d.deplacement_km != null ? `, déplacement de ${num(d.deplacement_km)} km` : ""}</Row>
          <Row label="Réception de la zone">{d.reception_cellule?.navires} navires, continuité {num((d.reception_cellule?.continuite ?? 0) * 100, 1)} %</Row>
          <Context items={d.contexte} />
          {(d.partenaires_possibles ?? []).length > 0 && (
            <table className="mt-3 w-full text-left">
              <thead className="text-muted"><tr><th>Partenaire possible</th><th>Au plus près</th><th>Lent</th></tr></thead>
              <tbody>{d.partenaires_possibles.map((x: Props) => (
                <tr key={x.vessel_id} className="border-t border-hair/70">
                  <td>{x.name ?? x.mmsi}{x.au_mouillage ? " (mouillage)" : ""}</td><td>{x.distance_min_m} m</td><td>{hm(x.debut)} à {hm(x.fin)}</td>
                </tr>))}</tbody>
            </table>
          )}
        </>
      );
    default:
      return null;
  }
}

/** Fiche d'un navire : identité déclarée, listes de surveillance, identités successives (même MMSI ou même OMI). */
function VesselBody({ p }: { p: Props }) {
  const q = useQuery({ queryKey: ["vessel", p.vessel_id], queryFn: () => api.vessel(p.vessel_id), staleTime: 60_000 });
  const v = q.data;
  const ids = v?.identities ?? [];
  return (
    <>
      {v?.watch && (
        <div className="mb-3 rounded-md border p-2.5" style={{ borderColor: WATCH_COLOR }}>
          <div className="font-semibold" style={{ color: WATCH_COLOR }}>{WATCH_LABEL[v.watch.level] ?? v.watch.level}</div>
          <div className="text-[12px] text-muted">Reconnu {MATCHED_BY[v.watch.matched_by] ?? v.watch.matched_by}</div>
          <ul className="mt-1.5 space-y-0.5 text-[12px]">
            {v.watch.entries.map((e, i) => (
              <li key={i}>
                {e.source === "gur" ? "Catalogue GUR" : "OpenSanctions"}
                {e.name ? `, ${e.name}` : ""}
                {e.risks?.length ? <span className="text-muted"> ({e.risks.join(", ")})</span> : null}
                {e.url && <> <a href={e.url} target="_blank" rel="noreferrer" className="text-signal hover:underline">fiche</a></>}
              </li>
            ))}
          </ul>
        </div>
      )}
      <Row label="MMSI">{p.mmsi}</Row>
      <Row label="OMI">{v?.imo ?? "n.d."}</Row>
      <Row label="Pavillon">{v?.flag ?? p.flag ?? "n.d."}</Row>
      <Row label="Indicatif">{v?.callsign ?? "n.d."}</Row>
      <Row label="Type">{p.ship_type ?? "non renseigné"}</Row>
      <Row label="Longueur">{p.length_m ? `${num(p.length_m, 0)} m` : "n.d."}</Row>
      <Row label="Destination">{v?.destination ?? "n.d."}</Row>
      <Row label="Vitesse">{num(p.sog_kn)} nœuds</Row>
      <Row label="Route">{num(p.cog_deg, 0)}°</Row>
      <Row label="Dernier message">il y a {num(p.age_s / 60, 0)} min</Row>
      {ids.length > 1 && (
        <>
          <div className="mt-3 font-semibold">Identités</div>
          <table className="mt-1 w-full text-left text-[12px]">
            <thead className="text-muted"><tr><th>Nom</th><th>Pavillon</th><th>MMSI</th><th>Période</th></tr></thead>
            <tbody>{ids.map((x, i) => (
              <tr key={i} className="border-t border-hair/70">
                <td>{x.name ?? "sans nom"}</td><td>{x.flag ?? "n.d."}</td><td>{x.mmsi}</td>
                <td>{dayLabel(x.first_seen)} au {dayLabel(x.last_seen)}</td>
              </tr>))}</tbody>
          </table>
        </>
      )}
    </>
  );
}

interface PanelProps {
  selection: Selection | null;
  onClose: () => void;
  passTime: string | null;
  onStatus: (status: string) => void;
}

export default function DetailPanel({ selection, onClose, passTime, onStatus }: PanelProps) {
  if (!selection) return null;
  let title = "";
  let color = "#4fb6c8";
  let body: ReactNode = null;
  let footer: ReactNode = null;

  if (selection.kind === "alert") {
    const p = selection.feature.properties;
    const d = p.details ?? {};
    const [lon, lat] = selection.feature.geometry.coordinates as [number, number];
    const withChip = (p.type === "DARK_SHIP" || p.type === "AIS_UNCONFIRMED") && p.event_time;
    title = `${ALERT_LABEL[p.type] ?? p.type}, ${SEVERITY[p.severity] ?? p.severity}`;
    color = ALERT_COLOR[p.type] ?? color;
    body = (
      <>
        {p.status !== "nouvelle" && (
          <span className="mb-2 inline-block rounded-full bg-raised px-2 py-0.5 text-[11.5px] text-muted">{STATUS_LABEL[p.status]}</span>
        )}
        <p className="mb-3 text-ink/90">{d.motif}</p>
        {withChip && <Chip lon={lon} lat={lat} time={p.event_time} />}
        <AlertBody p={p} />
        <AlertActions id={p.id} status={p.status ?? "nouvelle"} onStatus={onStatus} />
      </>
    );
    footer = <>Règles {p.rule_version}{p.event_time ? `, alerte levée le ${utc(p.event_time)}` : ""}</>;
  } else if (selection.kind === "vessel") {
    const p = selection.properties;
    title = p.name || "Navire sans nom";
    if (p.watch) color = WATCH_COLOR;
    body = <VesselBody p={p} />;
  } else {
    const p = selection.properties;
    const status = p.mask_reason && p.mask_reason !== "null" ? `Écartée (${p.mask_reason})`
      : p.matched_mmsi && p.matched_mmsi !== "null" ? `Appariée à ${p.matched_name ?? p.matched_mmsi}` : "Sans AIS";
    title = "Détection radar";
    body = (
      <>
        <Row label="Statut">{status}</Row>
        <Row label="Longueur estimée">{num(p.length_m, 0)} m</Row>
        <Row label="Contraste local VV">{num(p.contrast_vv_db)} dB</Row>
        <Row label="Score de présence">{num(p.objectness, 2)}</Row>
        <Row label="Score navire">{num(p.vessel_score, 2)}</Row>
        {passTime && p.lon != null && <Chip lon={p.lon} lat={p.lat} time={passTime} />}
      </>
    );
  }

  return (
    <div className="absolute right-4 top-4 z-10 max-h-[calc(100%-11rem)] w-[380px] overflow-y-auto rounded-lg border border-hair bg-panel/95 p-4 shadow-2xl backdrop-blur">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-start gap-2.5">
          <span className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: color }} />
          <h3 className="text-[15px] font-semibold leading-snug">{title}</h3>
        </div>
        <button onClick={onClose} className="text-muted hover:text-ink" aria-label="Fermer"><X size={16} /></button>
      </div>
      {body}
      {footer && <p className="mt-4 border-t border-hair pt-3 text-[12px] text-muted">{footer}</p>}
    </div>
  );
}
