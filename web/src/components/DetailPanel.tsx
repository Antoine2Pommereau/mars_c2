import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import type { AisCandidate, AisGapDetails, AlertProps, GapPartner, Selection, VesselRef } from "../lib/types";
import { api } from "../lib/api";
import { ALERT_COLOR, ALERT_LABEL, INFRA_CABLE, INFRA_PIPELINE, PA_LINE, SEVERITY, SIGNAL, STATUS_LABEL, dayLabel, hm, num, utc } from "../lib/format";
import AlertActions from "./AlertActions";
import Chip from "./Chip";

const BAND: Record<string, { label: string; color: string }> = {
  neutre: { label: "neutre", color: "#7c8b97" },
  faible: { label: "faible", color: "#e8d45a" },
  a_surveiller: { label: "à surveiller", color: "#f0a84b" },
  eleve: { label: "élevé", color: "#ef6461" },
  prioritaire: { label: "prioritaire", color: "#e03030" },
};
const ANOM_LABEL: Record<string, string> = {
  mid_incoherent: "Code pays MMSI incohérent", mmsi_hors_format: "MMSI hors format",
  longueur_manquante: "Longueur manquante", imo_manquant_classe_a: "IMO manquant (classe A)",
  pavillon_manquant: "Pavillon manquant",
};
const PANEL = "absolute right-4 top-4 z-10 max-h-[calc(100%-11rem)] w-[380px] overflow-y-auto rounded-lg border border-hair bg-panel/95 p-4 shadow-2xl backdrop-blur";

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
    case "INFRA_THREAT": {
      const d = p.details ?? {};
      const infra = d.infrastructure;
      return (
        <>
          <Row label="Navire">{vessel(d.navire)}</Row>
          <Row label="Infrastructure">
            {infra?.nom ?? (infra?.type === "pipeline" ? "Gazoduc" : "Câble")}{infra?.operateur ? ` (${infra.operateur})` : ""}
          </Row>
          <Row label="Distance au corridor">{d.distance_corridor_m} m</Row>
          <Row label="Durée sur zone">{d.duree_min} min, de {hm(d.debut)} à {hm(d.fin)} UTC</Row>
          <Row label="Vitesse moyenne">{num(d.vitesse_moyenne_kn)} nœuds</Row>
          <Row label="Déplacement">{d.deplacement_episode_m} m{d.traine_ancre_possible ? ", traîne d'ancre possible" : ""}</Row>
          <Context items={d.contexte} />
        </>
      );
    }
    case "IDENTITY_MISMATCH": {
      const d = p.details ?? {};
      return (
        <>
          <Row label="Navire déclaré">{vessel(d.navire)}</Row>
          <Row label="Longueur radar">{d.longueur_radar_m} m</Row>
          <Row label="Longueur AIS déclarée">{d.longueur_ais_m} m</Row>
          <Row label="Écart">{d.ecart_m} m, rapport {num(d.rapport)}</Row>
          <Row label="Score navire">{num(d.vessel_score, 2)}</Row>
          <Context items={d.contexte} />
        </>
      );
    }
    case "ZONE_BREACH": {
      const d = p.details ?? {};
      return (
        <>
          <Row label="Navire">{vessel(d.navire)}</Row>
          <Row label="Aire protégée">{d.aire?.nom ?? "aire marine protégée"}{d.aire?.designation ? ` (${d.aire.designation})` : ""}</Row>
          <Row label="Durée">{d.duree_min} min, de {hm(d.debut)} à {hm(d.fin)} UTC</Row>
          <Row label="Vitesse moyenne">{num(d.vitesse_moyenne_kn)} nœuds</Row>
          {d.en_peche && <Row label="Activité">pêche</Row>}
          <Context items={d.contexte} />
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
  onOpenDossier: (vesselId: number) => void;
  onTipCue: (alertId: number) => void;
}

export default function DetailPanel({ selection, onClose, passTime, onStatus, onOpenDossier, onTipCue }: PanelProps) {
  const vesselId = selection?.kind === "vessel_dossier" ? selection.vesselId : undefined;
  const dossierQ = useQuery({ queryKey: ["dossier", vesselId], queryFn: (ctx) => api.dossier(vesselId!, ctx),
    enabled: vesselId !== undefined, staleTime: 10_000 });
  if (!selection) return null;

  if (selection.kind === "vessel_dossier") {
    const d = dossierQ.data;
    const band = d ? (BAND[d.risque.bande] ?? BAND.neutre) : BAND.neutre;
    const anomalies = d ? Object.entries(d.anomalies_identite).filter(([, on]) => on) : [];
    return (
      <div className={PANEL}>
        <div className="mb-3 flex items-start justify-between gap-3">
          <h3 className="text-[15px] font-semibold leading-snug">{d?.identite.name ?? "Dossier navire"}</h3>
          <button onClick={onClose} className="text-muted hover:text-ink" aria-label="Fermer"><X size={16} /></button>
        </div>
        {!d ? <p className="text-muted">Chargement…</p> : (
          <>
            <div className="mb-3 flex items-center gap-3 rounded-md border border-hair bg-raised px-3 py-2.5">
              <span className="font-cond text-[30px] font-medium leading-none tabular-nums" style={{ color: band.color }}>{d.risque.score}</span>
              <div>
                <div className="text-[13px] font-medium" style={{ color: band.color }}>{band.label}</div>
                <div className="text-[11.5px] text-muted">score de risque</div>
              </div>
            </div>
            <Row label="MMSI">{d.identite.mmsi}</Row>
            <Row label="IMO">{d.identite.imo ?? "n.d."}</Row>
            <Row label="Type">{d.identite.ship_type ?? "non renseigné"}</Row>
            <Row label="Pavillon">{d.identite.flag ?? "n.d."}</Row>
            <Row label="Longueur">{d.identite.length_m ? `${num(d.identite.length_m, 0)} m` : "n.d."}</Row>
            <Row label="Classe AIS">{d.identite.ais_class ?? "n.d."}</Row>
            {anomalies.length > 0 && (
              <div className="mt-3">
                <div className="mb-1 font-semibold">Anomalies d'identité</div>
                <div className="flex flex-wrap gap-1.5">
                  {anomalies.map(([k]) => (
                    <span key={k} className="rounded-full border border-hair px-2 py-0.5 text-[11.5px] text-[#e8d45a]">{ANOM_LABEL[k] ?? k}</span>
                  ))}
                </div>
              </div>
            )}
            {d.risque.contributions.length > 0 && <div className="mb-1 mt-4 font-semibold">Décomposition du score</div>}
            {d.risque.contributions.map((c) => (
              <div key={c.alert_id} className="flex justify-between gap-3 border-b border-hair/70 py-1.5 text-[12.5px]">
                <span className="text-muted">{ALERT_LABEL[c.type] ?? c.type} · {dayLabel(c.jour)} · {SEVERITY[c.severity] ?? c.severity}</span>
                <span className="tabular-nums text-ink">{num(c.points)}</span>
              </div>
            ))}
            {d.risque.bonus_recurrence > 0 && (
              <div className="flex justify-between gap-3 border-b border-hair/70 py-1.5 text-[12.5px]">
                <span className="text-muted">Récurrence, {d.risque.jours_distincts} jours</span>
                <span className="tabular-nums text-ink">{num(d.risque.bonus_recurrence)}</span>
              </div>
            )}
            {d.risque.terme_identite > 0 && (
              <div className="flex justify-between gap-3 border-b border-hair/70 py-1.5 text-[12.5px]">
                <span className="text-muted">Anomalies d'identité</span>
                <span className="tabular-nums text-ink">{num(d.risque.terme_identite)}</span>
              </div>
            )}
            <div className="mb-1 mt-4 font-semibold">Historique ({d.comptages.total})</div>
            {d.alertes.length === 0 && <p className="text-muted">Aucune alerte enregistrée.</p>}
            {d.alertes.map((a) => (
              <div key={a.id} className={`flex items-center justify-between gap-3 border-b border-hair/70 py-1.5 text-[12.5px] ${a.status === "classee" ? "opacity-55" : ""}`}>
                <span className="flex items-center gap-2">
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: ALERT_COLOR[a.type] ?? "#fff" }} />
                  {ALERT_LABEL[a.type] ?? a.type}
                </span>
                <span className="text-muted">{dayLabel(a.event_time.slice(0, 10))} {hm(a.event_time)}</span>
              </div>
            ))}
          </>
        )}
        {d && <p className="mt-4 border-t border-hair pt-3 text-[12px] text-muted">Score version {d.risque.version}</p>}
      </div>
    );
  }

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
        {p.type === "AIS_GAP" && (p.details as AisGapDetails | undefined)?.derniere_position && (
          <button onClick={() => onTipCue(p.id)}
            title="Projette la zone atteignable et lance une analyse radar sur le prochain passage qui la couvre"
            className="mt-3 w-full rounded-md border border-signal px-3 py-1.5 text-[12.5px] text-ink hover:bg-raised">
            Chercher ce navire
          </button>
        )}
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
        <button onClick={() => onOpenDossier(p.vessel_id)}
          className="mt-3 w-full rounded-md border border-hair px-3 py-1.5 text-[12.5px] text-ink hover:border-signal">
          Ouvrir le dossier
        </button>
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
  } else if (selection.kind === "protected_area") {
    const p = selection.properties;
    const attrs = p.attrs ?? {};
    color = PA_LINE;
    title = p.name || "Aire marine protégée";
    body = (
      <>
        <Row label="Désignation">{p.designation ?? String(attrs.designation ?? "n.d.")}</Row>
        <Row label="Type">{p.kind === "natura2000" ? "Natura 2000" : "Aire marine protégée"}</Row>
        {p.country && <Row label="Pays">{p.country}</Row>}
        {attrs.convention != null && <Row label="Convention">{String(attrs.convention)}</Row>}
        {attrs.autorite != null && <Row label="Autorité">{String(attrs.autorite)}</Row>}
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
