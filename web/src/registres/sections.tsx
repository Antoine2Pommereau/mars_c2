import type { ReactNode } from "react";
import AlertActions from "../components/AlertActions";
import Chip from "../components/Chip";
import PreuveImage from "../components/PreuveImage";
import { Row, Sources, Tag } from "../components/Elements";
import { Comportement, EnTeteNavire, IdentitesFrise, InfraAlertes, InfraIdentite, InfraNavires, ListeAlertes, Notes,
  PassageInfras, PassageListes, PassageResume, Trajectoire, ViirsAlertes, ViirsMesures, ViirsNavire, Vignette, ZoneResume,
  ZoneTrafic } from "../components/Fiches";
import type { VesselCard } from "../lib/api";
import { num } from "../lib/format";
import { L } from "../lib/libelles";
import type { Feature, Props } from "../lib/types";
import { zoneOf } from "../lib/zones";
import { COULEUR_LISTE, naviresAlerte, typeAlerte } from "./alertes";

// Registre des sections de fiche : chaque section déclare l'objet auquel elle s'applique, sa place, sa condition
// d'affichage et son étape. La fiche les empile dans l'ordre ; une section sans donnée ne s'affiche pas, une
// section d'une étape future non plus, tant qu'elle n'est pas branchée.

export type Objet = "alerte" | "navire" | "detection" | "infrastructure" | "zone" | "passage";

export interface Contexte {
  objet: Objet;
  alerte?: Feature;
  navire?: Props;               // propriétés du navire sélectionné (carte, fil, recherche)
  carte?: VesselCard;           // fiche navire de l'API
  detection?: Props;
  infra?: Props;                // { id, … } de l'infrastructure sélectionnée
  zone?: string;
  passage?: number;             // identifiant du passage satellite sélectionné
  passTime?: string | null;
  plage: { debut: string; fin: string };
  suivis: Set<number>;
  vessels: Map<number, Props>;  // navires affichés à l'instant
  alerts: Feature[];            // alertes de la plage, filtrées
  onStatus: (status: string) => void;
  onPickAlert: (f: Feature) => void;
  onPickVessel: (p: Props) => void;
  onPickInfra: (id: number) => void;
  onSuivre: (vesselId: number, on: boolean) => void;
  onRejeu: (vesselId: number) => void;
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

const zoneOfVessel = (v: Props) => (v.lon == null ? null : zoneOf(v.lon, v.lat));
const toujours = () => true;
const rien = () => null;

const SECTIONS: Section[] = [
  // Alerte
  { id: "motif", objet: "alerte", ordre: 10, etape: null, titre: false,
    condition: (c) => !!c.alerte?.properties.details?.motif,
    rendu: (c) => <p className="text-ink/90">{c.alerte!.properties.details.motif}</p> },
  { id: "vignette", objet: "alerte", ordre: 20, etape: null, titre: false,
    condition: (c) => !!typeAlerte(c.alerte!.properties.type).vignette && !!c.alerte!.properties.event_time
      && c.alerte!.properties.details?.source !== "viirs",
    rendu: (c) => {
      const [lon, lat] = c.alerte!.geometry.coordinates as [number, number];
      return <Chip lon={lon} lat={lat} time={c.alerte!.properties.event_time} />;
    } },
  { id: "preuves", objet: "alerte", ordre: 30, etape: null, titre: true, condition: toujours,
    rendu: (c) => typeAlerte(c.alerte!.properties.type).preuves(c.alerte!.properties) },
  { id: "navires", objet: "alerte", ordre: 40, etape: null, titre: true,
    condition: (c) => naviresAlerte(c.alerte!).length > 0,
    rendu: (c) => (
      <ul className="space-y-1 text-[12px]">
        {naviresAlerte(c.alerte!).map((n) => (
          <li key={n.vessel_id}>
            <button onClick={() => c.onPickVessel(c.vessels.get(n.vessel_id) ?? { ...n })} className="flex w-full items-center gap-2 text-left hover:text-signal">
              <Vignette vesselId={n.vessel_id} source />
              <span className="text-ink">{n.name ?? c.vessels.get(n.vessel_id)?.name ?? `MMSI ${n.mmsi ?? ""}`}</span>
              {(n.flag ?? c.vessels.get(n.vessel_id)?.flag) && <Tag>{n.flag ?? c.vessels.get(n.vessel_id)?.flag}</Tag>}
              <span className="text-muted">{L.fiche.ouvrir}</span>
            </button>
          </li>
        ))}
      </ul>
    ) },
  { id: "decisions", objet: "alerte", ordre: 90, etape: null, titre: true, condition: toujours,
    rendu: (c) => <AlertActions id={c.alerte!.properties.id} status={c.alerte!.properties.status ?? "nouvelle"}
      onStatus={c.onStatus} /> },

  // Navire, dans l'ordre de la vision ; les sections futures sont déclarées et ne s'affichent pas
  { id: "entete", objet: "navire", ordre: 5, etape: null, titre: false, condition: toujours,
    rendu: (c) => {
      const id = Number(c.navire!.vessel_id);
      return <EnTeteNavire navire={{ ...c.navire, ...(c.vessels.get(id) ?? {}) }} carte={c.carte} suivi={c.suivis.has(id)}
        onSuivre={(on) => c.onSuivre(id, on)} />;
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
    condition: (c) => (c.carte?.identities ?? []).length > 1, rendu: (c) => <IdentitesFrise rows={c.carte!.identities} /> },
  { id: "alertes", objet: "navire", ordre: 40, etape: null, titre: true,
    condition: (c) => (c.carte?.alerts ?? []).length > 0, rendu: (c) => <ListeAlertes alerts={c.carte!.alerts} onPick={c.onPickAlert} /> },
  { id: "comportement", objet: "navire", ordre: 50, etape: null, titre: true, condition: toujours,
    rendu: (c) => <Comportement vesselId={Number(c.navire!.vessel_id)} debut={c.plage.debut} fin={c.plage.fin} onPickInfra={c.onPickInfra} /> },
  { id: "trajectoire", objet: "navire", ordre: 60, etape: null, titre: true, condition: toujours,
    rendu: (c) => <Trajectoire vesselId={Number(c.navire!.vessel_id)} debut={c.plage.debut} fin={c.plage.fin}
      onRejeu={() => c.onRejeu(Number(c.navire!.vessel_id))} /> },
  { id: "risque", objet: "navire", ordre: 70, etape: "reporte", titre: true, condition: toujours, rendu: rien },
  { id: "satellite", objet: "navire", ordre: 80, etape: "3", titre: true, condition: toujours, rendu: rien },
  { id: "appris", objet: "navire", ordre: 85, etape: "pilote", titre: true, condition: toujours, rendu: rien },
  { id: "prediction", objet: "navire", ordre: 88, etape: "4", titre: true, condition: toujours, rendu: rien },
  { id: "notes", objet: "navire", ordre: 90, etape: null, titre: true, condition: toujours,
    rendu: (c) => <Notes vesselId={Number(c.navire!.vessel_id)} /> },

  // Infrastructure
  { id: "identite", objet: "infrastructure", ordre: 10, etape: null, titre: false, condition: toujours,
    rendu: (c) => <InfraIdentite id={c.infra!.id} debut={c.plage.debut} fin={c.plage.fin} /> },
  { id: "passes", objet: "infrastructure", ordre: 20, etape: null, titre: true, condition: toujours,
    rendu: (c) => <InfraNavires id={c.infra!.id} debut={c.plage.debut} fin={c.plage.fin} onPickVessel={c.onPickVessel} /> },
  { id: "liees", objet: "infrastructure", ordre: 30, etape: null, titre: true, condition: toujours,
    rendu: (c) => <InfraAlertes id={c.infra!.id} debut={c.plage.debut} fin={c.plage.fin} onPickAlert={c.onPickAlert} /> },

  // Zone collectée
  { id: "resume", objet: "zone", ordre: 10, etape: null, titre: false, condition: toujours, rendu: (c) => <ZoneResume zone={c.zone!} /> },
  { id: "trafic", objet: "zone", ordre: 20, etape: null, titre: true, condition: toujours,
    rendu: (c) => <ZoneTrafic zone={c.zone!} alerts={c.alerts} onPickAlert={c.onPickAlert}
      vessels={[...c.vessels.values()].filter((v) => zoneOfVessel(v) === c.zone)} /> },

  // Passage satellite : base du déclenchement des analyses (lots B et C)
  { id: "passage", objet: "passage", ordre: 10, etape: null, titre: false, condition: toujours,
    rendu: (c) => <PassageResume id={c.passage!} /> },
  { id: "infrastructures", objet: "passage", ordre: 20, etape: null, titre: true, condition: toujours,
    rendu: (c) => <PassageInfras id={c.passage!} onPickInfra={c.onPickInfra} /> },
  { id: "listes_couvertes", objet: "passage", ordre: 30, etape: null, titre: true, condition: toujours,
    rendu: (c) => <PassageListes id={c.passage!} onPickVessel={c.onPickVessel} /> },

  // Détection nocturne VIIRS
  { id: "viirs", objet: "detection", ordre: 10, etape: null, titre: false, condition: (c) => c.detection?.source === "viirs",
    rendu: (c) => <ViirsMesures id={Number(c.detection!.id)} /> },
  { id: "image", objet: "detection", ordre: 15, etape: null, titre: false, condition: (c) => c.detection?.source === "viirs",
    rendu: (c) => <PreuveImage source="viirs" detectionId={Number(c.detection!.id)} /> },
  { id: "apparie", objet: "detection", ordre: 20, etape: null, titre: true, condition: (c) => c.detection?.source === "viirs",
    rendu: (c) => <ViirsNavire id={Number(c.detection!.id)} onPickVessel={c.onPickVessel} /> },
  { id: "liees", objet: "detection", ordre: 30, etape: null, titre: true, condition: (c) => c.detection?.source === "viirs",
    rendu: (c) => <ViirsAlertes id={Number(c.detection!.id)} onPickAlert={c.onPickAlert} /> },

  // Détection radar
  { id: "mesures", objet: "detection", ordre: 10, etape: null, titre: false, condition: (c) => c.detection?.source !== "viirs",
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
    condition: (c) => !!c.passTime && c.detection?.lon != null && c.detection?.source !== "viirs",
    rendu: (c) => <Chip lon={c.detection!.lon} lat={c.detection!.lat} time={c.passTime!} /> },
];

/** Sections à afficher pour un contexte, dans l'ordre. */
export function sectionsDe(c: Contexte): Section[] {
  return SECTIONS.filter((s) => s.objet === c.objet && s.etape == null && s.condition(c)).sort((a, b) => a.ordre - b.ordre);
}

