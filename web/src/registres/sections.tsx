import type { ReactNode } from "react";
import AlertActions from "../components/AlertActions";
import PreuveImage from "../components/PreuveImage";
import { Row, Sejour, Sources, Tag } from "../components/Elements";
import { Comportement, DemandesObservation, EnTeteAlerteNavire, EnTeteNavire, RadarAlertes, RadarMesures, RadarNavire, VerificationSatellite, IdentitesFrise, InfraAlertes, InfraIdentite, InfraNavires, ListeAlertes, Notes,
  PassageInfras, PassageListes, PassageResume, Trajectoire, ViirsAlertes, ViirsMesures, ViirsNavire, Vignette, ZoneResume,
  ZoneTrafic } from "../components/Fiches";
import type { VesselCard } from "../lib/api";
import { num } from "../lib/format";
import { L } from "../lib/libelles";
import type { Feature, Props, SourceDetection } from "../lib/types";
import { zoneOf } from "../lib/zones";
import { COULEUR_LISTE, naviresAlerte, typeAlerte } from "./alertes";

// Registre des sections de fiche : chaque section déclare l'objet auquel elle s'applique, sa place, sa condition
// d'affichage et son étape. La fiche les empile dans l'ordre ; une section sans donnée ne s'affiche pas, une
// section d'une étape future non plus, tant qu'elle n'est pas branchée.

type Objet = "alerte" | "navire" | "detection" | "infrastructure" | "zone" | "passage";

export interface Contexte {
  objet: Objet;
  alerte?: Feature;
  navire?: Props;               // propriétés du navire sélectionné (carte, fil, recherche)
  carte?: VesselCard;           // fiche navire de l'API
  detection?: Props;
  infra?: Props;                // { id, … } de l'infrastructure sélectionnée
  zone?: string;
  passage?: number;             // identifiant du passage satellite sélectionné
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
  onPickDetection: (source: SourceDetection, id: number) => void;
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

// Séjour prolongé le plus récent porté par une alerte WATCHLIST du navire
const sejourNavire = (c: Contexte): Props | undefined => (c.carte?.alerts ?? [])
  .filter((a) => a.properties.type === "WATCHLIST" && a.properties.details?.sejour)
  .sort((a, b) => String(b.properties.event_time).localeCompare(String(a.properties.event_time)))[0]?.properties.details.sejour;

// Détections des travailleurs (VIIRS, Sentinel 1, Sentinel 2) : fiches propres ; les autres sont celles des analyses du Mac
const SATELLITES = ["viirs", "sentinel1", "sentinel2"];
// Radar et optique partagent la fiche de détection (mesures, image, navire apparié, alertes liées)
const sentinel = (c: Contexte) => c.detection?.source === "sentinel1" || c.detection?.source === "sentinel2";

const zoneOfVessel = (v: Props) => (v.lon == null ? null : zoneOf(v.lon, v.lat));
const toujours = () => true;
const rien = () => null;

const SECTIONS: Section[] = [
  // Alerte
  { id: "navire", objet: "alerte", ordre: 5, etape: null, titre: false,
    condition: (c) => naviresAlerte(c.alerte!)[0]?.vessel_id != null,
    rendu: (c) => {
      const n = naviresAlerte(c.alerte!)[0], v = c.vessels.get(n.vessel_id);
      return <EnTeteAlerteNavire navire={{ ...n, name: n.name ?? v?.name, flag: n.flag ?? v?.flag, mmsi: n.mmsi ?? v?.mmsi }}
        onOuvrir={() => c.onPickVessel(v ?? { ...n })} />;
    } },
  { id: "motif", objet: "alerte", ordre: 10, etape: null, titre: false,
    condition: (c) => !!c.alerte?.properties.details?.motif,
    rendu: (c) => <p className="text-ink/90">{c.alerte!.properties.details.motif}</p> },
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
  // Tip and cue (lot T1) : vérification satellite demandée, résultat et vignette ; demande manuelle
  { id: "observation", objet: "alerte", ordre: 35, etape: null, titre: true, condition: toujours,
    rendu: (c) => <DemandesObservation alerteId={Number(c.alerte!.properties.id)}
      verification={c.alerte!.properties.details?.verification_satellite} onPickDetection={c.onPickDetection} /> },
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
  { id: "sejour", objet: "navire", ordre: 25, etape: null, titre: true, condition: (c) => !!sejourNavire(c),
    rendu: (c) => <Sejour s={sejourNavire(c)!} /> },
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
  { id: "satellite", objet: "navire", ordre: 80, etape: null, titre: true, condition: toujours,
    rendu: (c) => <VerificationSatellite vesselId={Number(c.navire!.vessel_id)}
      onPickDetection={(source, id) => c.onPickDetection(source, id)} /> },
  { id: "observation", objet: "navire", ordre: 82, etape: null, titre: true, condition: toujours,
    rendu: (c) => <DemandesObservation vesselId={Number(c.navire!.vessel_id)} onPickDetection={c.onPickDetection} /> },
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
  // Détection radar Sentinel 1 (lot C) ou optique Sentinel 2 (lot S2) : image en grand d'abord pour l'optique
  { id: "image", objet: "detection", ordre: 5, etape: null, titre: false, condition: (c) => c.detection?.source === "sentinel2",
    rendu: (c) => <PreuveImage source="sentinel2" detectionId={Number(c.detection!.id)} /> },
  { id: "radar", objet: "detection", ordre: 10, etape: null, titre: false, condition: sentinel,
    rendu: (c) => <RadarMesures id={Number(c.detection!.id)} /> },
  { id: "image", objet: "detection", ordre: 15, etape: null, titre: false, condition: (c) => c.detection?.source === "sentinel1",
    rendu: (c) => <PreuveImage source="sentinel1" detectionId={Number(c.detection!.id)} /> },
  { id: "apparie", objet: "detection", ordre: 20, etape: null, titre: true, condition: sentinel,
    rendu: (c) => <RadarNavire id={Number(c.detection!.id)} onPickVessel={c.onPickVessel} /> },
  { id: "contexte_optique", objet: "detection", ordre: 25, etape: null, titre: true,
    condition: (c) => c.detection?.source === "sentinel2",
    rendu: (c) => <PreuveImage source="sentinel2" contexte detectionId={Number(c.detection!.id)} /> },
  { id: "liees", objet: "detection", ordre: 30, etape: null, titre: true, condition: sentinel,
    rendu: (c) => <RadarAlertes id={Number(c.detection!.id)} onPickAlert={c.onPickAlert} /> },
  // Détection d'une analyse du Mac (zone tracée)
  { id: "mesures", objet: "detection", ordre: 10, etape: null, titre: false, condition: (c) => !SATELLITES.includes(c.detection?.source),
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
];

/** Sections à afficher pour un contexte, dans l'ordre. */
export function sectionsDe(c: Contexte): Section[] {
  return SECTIONS.filter((s) => s.objet === c.objet && s.etape == null && s.condition(c)).sort((a, b) => a.ordre - b.ordre);
}

