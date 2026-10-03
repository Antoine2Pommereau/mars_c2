import { X } from "lucide-react";
import type { ReactNode } from "react";
import type { AisCandidate, AlertProps, GapPartner, Selection, VesselRef } from "../lib/types";
import { ALERT_COLOR, ALERT_LABEL, INFRA_CABLE, INFRA_PIPELINE, SEVERITY, SIGNAL, STATUS_LABEL, hm, num, utc } from "../lib/format";
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

function vessel(v?: VesselRef) {
  if (!v) return "inconnu";
  return `${v.name ?? "Sans nom"} (MMSI ${v.mmsi}${v.ship_type ? `, ${v.ship_type}` : ""}${v.length_m ? `, ${num(v.length_m, 0)} m` : ""})`;
}

function AlertBody({ p }: { p: AlertProps }) {
  switch (p.type) {
    case "DARK_SHIP": {
      const d = p.details ?? {};
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
              <tbody>{d.candidats_ais!.map((c: AisCandidate) => (
                <tr key={c.mmsi} className="border-t border-hair/70">
                  <td>{c.name ?? c.mmsi}</td><td>{c.distance_m} m</td><td>{c.tolerance_le_long_m ?? c.rayon_tolere_m} m</td>
                </tr>))}</tbody>
            </table>
          ) : <p className="text-muted">Aucun navire AIS à proximité.</p>}
        </>
      );
    }
    case "AIS_UNCONFIRMED": {
      const d = p.details ?? {};
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
    }
    case "RENDEZVOUS": {
      const d = p.details ?? {};
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
    case "AIS_GAP": {
      const d = p.details ?? {};
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
              <tbody>{d.partenaires_possibles!.map((x: GapPartner) => (
                <tr key={x.vessel_id} className="border-t border-hair/70">
                  <td>{x.name ?? x.mmsi}{x.au_mouillage ? " (mouillage)" : ""}</td><td>{x.distance_min_m} m</td><td>{hm(x.debut)} à {hm(x.fin)}</td>
                </tr>))}</tbody>
            </table>
          )}
        </>
      );
    }
    default:
      return null;
  }
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
  let color = SIGNAL;
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
        {p.status && p.status !== "nouvelle" && (
          <span className="mb-2 inline-block rounded-full bg-raised px-2 py-0.5 text-[11.5px] text-muted">{STATUS_LABEL[p.status]}</span>
        )}
        <p className="mb-3 text-ink/90">{d.motif}</p>
        {withChip && p.event_time && <Chip lon={lon} lat={lat} time={p.event_time} />}
        <AlertBody p={p} />
        <AlertActions id={p.id} status={p.status ?? "nouvelle"} onStatus={onStatus} />
      </>
    );
    footer = <>Règles {p.rule_version}{p.event_time ? `, alerte levée le ${utc(p.event_time)}` : ""}</>;
  } else if (selection.kind === "vessel") {
    const p = selection.properties;
    title = p.name || "Navire sans nom";
    body = (
      <>
        <Row label="MMSI">{p.mmsi}</Row>
        <Row label="Type">{p.ship_type ?? "non renseigné"}</Row>
        <Row label="Longueur">{p.length_m ? `${num(p.length_m, 0)} m` : "n.d."}</Row>
        <Row label="Vitesse">{num(p.sog_kn)} nœuds</Row>
        <Row label="Route">{num(p.cog_deg, 0)}°</Row>
        <Row label="Dernier message">il y a {num((p.age_s ?? 0) / 60, 0)} min</Row>
      </>
    );
  } else if (selection.kind === "infrastructure") {
    const p = selection.properties;
    const attrs = p.attrs ?? {};
    color = p.kind === "pipeline" ? INFRA_PIPELINE : INFRA_CABLE;
    title = p.name || (p.kind === "pipeline" ? "Pipeline" : "Câble sous marin");
    body = (
      <>
        <Row label="Type">{String(attrs.type ?? (p.kind === "pipeline" ? "Pipeline" : "Câble"))}</Row>
        <Row label="Opérateur">{p.operator ?? "non renseigné"}</Row>
        {attrs.tension_kv != null && <Row label="Tension">{String(attrs.tension_kv)} kV</Row>}
        {attrs.annee != null && <Row label="Mise en service">{String(attrs.annee)}</Row>}
        {attrs.reseau != null && <Row label="Réseau">{String(attrs.reseau)}</Row>}
        {attrs.trace != null && <Row label="Tracé">{String(attrs.trace)}</Row>}
        {attrs.description != null && <Row label="Description">{String(attrs.description)}</Row>}
      </>
    );
    footer = <>Source {p.source}</>;
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
        {passTime && p.lon != null && p.lat != null && <Chip lon={p.lon} lat={p.lat} time={passTime} />}
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
