import type { ReactNode } from "react";
import AlertActions from "../components/AlertActions";
import Chip from "../components/Chip";
import { Context, Identities, Row, Sources } from "../components/Elements";
import type { VesselCard } from "../lib/api";
import { dayLabel, hm, num } from "../lib/format";
import { L } from "../lib/libelles";
import type { Feature, Props } from "../lib/types";
import { COULEUR_LISTE, couleurAlerte, libelleAlerte, typeAlerte } from "./alertes";

// Registre des sections de fiche : chaque section déclare l'objet auquel elle s'applique, sa place, sa condition
// d'affichage et son étape. La fiche les empile dans l'ordre ; une section sans donnée ne s'affiche pas, une
// section d'une étape future non plus, tant qu'elle n'est pas branchée.

export type Objet = "alerte" | "navire" | "detection";

export interface Contexte {
  objet: Objet;
  alerte?: Feature;
  navire?: Props;               // propriétés du navire sélectionné (carte, fil)
  carte?: VesselCard;           // fiche navire de l'API
  detection?: Props;
  passTime?: string | null;
  onStatus?: (status: string) => void;
  onPickAlert?: (f: Feature) => void;
}

interface Section {
  id: string;
  objet: Objet;
  ordre: number;
  etape: string | null;         // null : en place ; sinon « lot 2 », « 3 », « 4 », « pilote »
  titre: boolean;               // affiche son titre (repliable) ou non (en tête)
  condition: (c: Contexte) => boolean;
  rendu: (c: Contexte) => ReactNode;
}

const F = L.fiche;
const toujours = () => true;
const rien = () => null;

const SECTIONS: Section[] = [
  // Alerte
  { id: "motif", objet: "alerte", ordre: 10, etape: null, titre: false,
    condition: (c) => !!c.alerte?.properties.details?.motif,
    rendu: (c) => <p className="text-ink/90">{c.alerte!.properties.details.motif}</p> },
  { id: "vignette", objet: "alerte", ordre: 20, etape: null, titre: false,
    condition: (c) => !!typeAlerte(c.alerte!.properties.type).vignette && !!c.alerte!.properties.event_time,
    rendu: (c) => {
      const [lon, lat] = c.alerte!.geometry.coordinates as [number, number];
      return <Chip lon={lon} lat={lat} time={c.alerte!.properties.event_time} />;
    } },
  { id: "preuves", objet: "alerte", ordre: 30, etape: null, titre: true, condition: toujours,
    rendu: (c) => typeAlerte(c.alerte!.properties.type).preuves(c.alerte!.properties) },
  { id: "decisions", objet: "alerte", ordre: 90, etape: null, titre: true, condition: toujours,
    rendu: (c) => <AlertActions id={c.alerte!.properties.id} status={c.alerte!.properties.status ?? "nouvelle"}
      onStatus={c.onStatus ?? (() => undefined)} /> },

  // Navire
  { id: "identite", objet: "navire", ordre: 10, etape: null, titre: false, condition: toujours,
    rendu: (c) => {
      const p = c.navire ?? {}, v = c.carte;
      return (
        <>
          <Row label={F.mmsi}>{p.mmsi ?? v?.mmsi}</Row>
          <Row label={F.omi}>{v?.imo ?? L.commun.nd}</Row>
          <Row label={F.pavillon}>{v?.flag ?? p.flag ?? L.commun.nd}</Row>
          <Row label={F.indicatif}>{v?.callsign ?? L.commun.nd}</Row>
          <Row label={F.type}>{p.ship_type ?? v?.ship_type ?? F.nonRenseigne}</Row>
          <Row label={F.longueur}>{(p.length_m ?? v?.length_m) ? `${num(p.length_m ?? v?.length_m, 0)} m` : L.commun.nd}</Row>
          <Row label={F.destination}>{v?.destination ?? L.commun.nd}</Row>
          {p.sog_kn != null && <Row label={F.vitesse}>{num(p.sog_kn)} {L.commun.noeuds}</Row>}
          {p.cog_deg != null && <Row label={F.route}>{num(p.cog_deg, 0)}°</Row>}
          {p.age_s != null && <Row label={F.dernierMessage}>{F.ilYaMin(num(p.age_s / 60, 0))}</Row>}
        </>
      );
    } },
  { id: "listes", objet: "navire", ordre: 20, etape: null, titre: true, condition: (c) => !!c.carte?.watch,
    rendu: (c) => {
      const w = c.carte!.watch!;
      return (
        <div className="rounded-md border p-2.5" style={{ borderColor: COULEUR_LISTE }}>
          <div className="font-semibold" style={{ color: COULEUR_LISTE }}>{L.signal[w.level] ?? w.level}</div>
          <div className="text-[12px] text-muted">{L.reconnuPar[w.matched_by] ?? w.matched_by}</div>
          <Sources entries={w.entries} />
        </div>
      );
    } },
  { id: "identites", objet: "navire", ordre: 30, etape: null, titre: true,
    condition: (c) => (c.carte?.identities ?? []).length > 1, rendu: (c) => <Identities rows={c.carte!.identities} /> },
  { id: "alertes", objet: "navire", ordre: 40, etape: null, titre: true,
    condition: (c) => (c.carte?.alerts ?? []).length > 0,
    rendu: (c) => (
      <ul className="space-y-1 text-[12px]">
        {c.carte!.alerts.map((a) => (
          <li key={a.properties.id}>
            <button onClick={() => c.onPickAlert?.(a)} className="flex w-full items-center gap-2 text-left hover:text-ink">
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: couleurAlerte(a.properties.type) }} />
              <span className="flex-1">{libelleAlerte(a.properties.type)}</span>
              <span className="text-muted">{dayLabel(a.properties.event_time)} {hm(a.properties.event_time)}</span>
            </button>
          </li>
        ))}
      </ul>
    ) },
  { id: "comportement", objet: "navire", ordre: 50, etape: "lot 2", titre: true, condition: toujours, rendu: rien },
  { id: "trajectoire", objet: "navire", ordre: 60, etape: "lot 2", titre: true, condition: toujours, rendu: rien },
  { id: "risque", objet: "navire", ordre: 70, etape: "reporte", titre: true, condition: toujours, rendu: rien },
  { id: "satellite", objet: "navire", ordre: 80, etape: "3", titre: true, condition: toujours, rendu: rien },
  { id: "appris", objet: "navire", ordre: 85, etape: "pilote", titre: true, condition: toujours, rendu: rien },
  { id: "prediction", objet: "navire", ordre: 88, etape: "4", titre: true, condition: toujours, rendu: rien },
  { id: "notes", objet: "navire", ordre: 90, etape: "lot 2", titre: true, condition: toujours, rendu: rien },

  // Détection radar
  { id: "mesures", objet: "detection", ordre: 10, etape: null, titre: false, condition: toujours,
    rendu: (c) => {
      const p = c.detection!, P = L.preuves;
      const statut = p.mask_reason && p.mask_reason !== "null" ? P.ecartee(p.mask_reason)
        : p.matched_mmsi && p.matched_mmsi !== "null" ? P.appariee(p.matched_name ?? p.matched_mmsi) : P.sansAis;
      return (
        <>
          <Row label={P.statut}>{statut}</Row>
          <Row label={P.longueurEstimee}>{num(p.length_m, 0)} m</Row>
          <Row label={P.contraste}>{num(p.contrast_vv_db)} dB</Row>
          <Row label={P.scorePresence}>{num(p.objectness, 2)}</Row>
          <Row label={P.scoreNavire}>{num(p.vessel_score, 2)}</Row>
        </>
      );
    } },
  { id: "vignette", objet: "detection", ordre: 20, etape: null, titre: false,
    condition: (c) => !!c.passTime && c.detection?.lon != null,
    rendu: (c) => <Chip lon={c.detection!.lon} lat={c.detection!.lat} time={c.passTime!} /> },
];

/** Sections à afficher pour un contexte, dans l'ordre. */
export function sectionsDe(c: Contexte): Section[] {
  return SECTIONS.filter((s) => s.objet === c.objet && s.etape == null && s.condition(c)).sort((a, b) => a.ordre - b.ordre);
}

