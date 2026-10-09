import { ArrowRightLeft, Cable, CircleHelp, EyeOff, Fingerprint, Navigation, Route, Ruler, ShieldAlert, WifiOff,
  type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Context, Identities, Row, Sources, Tag, vesselText } from "../components/Elements";
import { hm, num, utc } from "../lib/format";
import { L } from "../lib/libelles";
import type { Feature, Props } from "../lib/types";

// Registre des types d'alerte : un nouveau type s'ajoute par une entrée, sans toucher au fil ni à la fiche.

interface NavireRef { vessel_id: number; name?: string | null; mmsi?: number; flag?: string | null }

interface TypeAlerte {
  type: string;
  Icone: LucideIcon;
  couleur: string;
  piste: "flotte" | "infra" | "deux";
  etape: string;                 // clé de L.etapes : « en place », « recalibration », « 3 », « pilote »…
  actif: boolean;                // produit aujourd'hui par le moteur de règles ou l'analyse radar
  /** Navires concernés, le premier sert au regroupement du fil */
  navires: (d: Props) => (NavireRef | null | undefined)[];
  /** Titre de la ligne du fil */
  titre: (d: Props) => string;
  /** Signe distinctif dans le fil (niveau de signal, durée, infrastructure…) */
  signe: (d: Props) => ReactNode;
  /** Preuves dans la fiche de l'alerte */
  preuves: (p: Props) => ReactNode;
  /** Infrastructures concernées (mode « concernées seulement » des couches) */
  infrastructures?: (d: Props) => number[];
  /** Vignette radar dans la fiche */
  vignette?: boolean;
}

const P = L.preuves;
const nom = (v?: Props) => v?.name ?? (v?.mmsi ? P.mmsiDe(v.mmsi) : L.commun.inconnu);
const aVenir = (): ReactNode => null;

export const COULEUR_LISTE = "#b48cf2";
// Navire suivi par l'opérateur, sans alerte ni liste : couleur du signal système
export const COULEUR_SUIVI = "#4fb6c8";

export const TYPES_ALERTE: TypeAlerte[] = [
  {
    type: "WATCHLIST", Icone: ShieldAlert, couleur: COULEUR_LISTE, piste: "flotte", etape: "en place", actif: true,
    navires: (d) => [d.navire ? { ...d.navire, flag: d.navire.pavillon } : null],
    titre: (d) => nom(d.navire),
    // Niveau de signal ; le pavillon est affiché par la ligne du fil, pour tous les types
    signe: (d) => <Tag color={COULEUR_LISTE}>{L.signal[d.niveau] ?? d.niveau}</Tag>,
    preuves: (p) => {
      const d = p.details ?? {};
      return (
        <>
          <Row label={P.navire}>{vesselText(d.navire)}</Row>
          <Row label={P.signal}>{L.signal[d.niveau] ?? d.niveau}, {L.reconnuPar[d.reconnu_par] ?? d.reconnu_par}</Row>
          <Row label={P.dansNosEaux}>{utc(d.debut)} {P.au} {utc(d.fin)}</Row>
          <Row label={P.zones}>{(d.zones ?? []).map((z: string) => L.zones[z] ?? z).join(", ")}</Row>
          <Row label={P.positions}>{d.positions}</Row>
          <Context items={d.contexte} />
          <div className="mt-3 font-semibold">{P.sources}</div>
          <Sources entries={d.sources} />
        </>
      );
    },
  },
  {
    type: "IDENTITY_CHANGE", Icone: Fingerprint, couleur: "#5fd3a5", piste: "flotte", etape: "en place", actif: true,
    navires: (d) => [d.navire],
    titre: (d) => d.changement === "nom" ? `${d.ancien_nom} ${P.devenu} ${d.nouveau_nom}` : P.omiDe(d.omi),
    signe: (d) => d.militaire ? <Tag>{P.militaire}</Tag>
      : d.changement === "nom" ? <Tag>{P.mmsiDe(d.navire?.mmsi)}</Tag>
      : <Tag>{d.pavillon_change ? P.pavillonChange : P.sousAutreMmsi}</Tag>,
    preuves: (p) => {
      const d = p.details ?? {};
      return (
        <>
          <Row label={P.navire}>{vesselText(d.navire)}</Row>
          {d.changement === "nom"
            ? <Row label={P.nom}>{d.ancien_nom} {P.puis} {d.nouveau_nom}</Row>
            : <Row label={P.omi}>{d.omi}, {P.sousAutreMmsi}</Row>}
          <Row label={P.depuis}>{utc(d.debut)}</Row>
          {d.militaire && <Row label={P.militaireRow}>{d.militaire}</Row>}
          <Context items={d.contexte} />
          <div className="mt-3 font-semibold">{P.identites}</div>
          <Identities rows={d.identites} />
        </>
      );
    },
  },
  {
    type: "RENDEZVOUS", Icone: ArrowRightLeft, couleur: "#f0a84b", piste: "deux", etape: "en place", actif: true,
    navires: (d) => d.navires ?? [],
    titre: (d) => { const [a, b] = d.navires ?? []; return `${nom(a)} et ${nom(b)}`; },
    signe: (d) => <Tag>{P.rencontreDe(d.duree_min, d.distance_min_m)}</Tag>,
    preuves: (p) => {
      const d = p.details ?? {};
      const [v1, v2] = d.navires ?? [];
      return (
        <>
          <Row label={P.navire1}>{vesselText(v1)}</Row>
          <Row label={P.navire2}>{vesselText(v2)}</Row>
          <Row label={P.rencontre}>{hm(d.debut)} {P.au} {hm(d.fin)} UTC, {d.duree_min} min</Row>
          <Row label={P.distance}>{d.distance_min_m} m, {d.distance_moyenne_m} m</Row>
          <Row label={P.distanceCote}>{d.distance_cote_km == null ? L.commun.nd : `${num(d.distance_cote_km)} km`}</Row>
          <Context items={d.contexte} />
        </>
      );
    },
  },
  {
    type: "AIS_GAP", Icone: WifiOff, couleur: "#ef6461", piste: "deux", etape: "recalibration", actif: true,
    navires: (d) => [d.navire, ...(d.partenaires_possibles ?? [])],
    titre: (d) => nom(d.navire),
    signe: (d) => <Tag>{P.silenceDe(d.duree_min)}</Tag>,
    preuves: (p) => {
      const d = p.details ?? {};
      return (
        <>
          <Row label={P.navire}>{vesselText(d.navire)}</Row>
          <Row label={P.dernierMessage}>{hm(d.dernier_message)} UTC, {num(d.vitesse_avant_kn)} {L.commun.noeuds}</Row>
          <Row label={P.reapparition}>{d.reapparition ? `${hm(d.reapparition)} UTC` : P.aucuneReapparition}</Row>
          <Row label={P.silence}>{d.duree_min} min{d.deplacement_km != null ? P.deplacement(num(d.deplacement_km)) : ""}</Row>
          <Row label={P.receptionZone}>{P.continuite(d.reception_cellule?.navires, num((d.reception_cellule?.continuite ?? 0) * 100, 1))}</Row>
          <Context items={d.contexte} />
          {(d.partenaires_possibles ?? []).length > 0 && (
            <table className="mt-3 w-full text-left">
              <thead className="text-muted"><tr><th>{P.partenaire}</th><th>{P.auPlusPres}</th><th>{P.lent}</th></tr></thead>
              <tbody>{d.partenaires_possibles.map((x: Props) => (
                <tr key={x.vessel_id} className="border-t border-hair/70">
                  <td>{x.name ?? x.mmsi}{x.au_mouillage ? ` ${P.mouillage}` : ""}</td><td>{x.distance_min_m} m</td><td>{hm(x.debut)} {P.au} {hm(x.fin)}</td>
                </tr>))}</tbody>
            </table>
          )}
        </>
      );
    },
  },
  {
    type: "INFRA_THREAT", Icone: Cable, couleur: "#5b9cff", piste: "infra", etape: "recalibration", actif: false,
    navires: (d) => [d.navire], titre: (d) => nom(d.navire),
    signe: (d) => d.infrastructure?.name ? <Tag>{d.infrastructure.name}</Tag> : null,
    preuves: aVenir, infrastructures: (d) => (d.infrastructure?.id != null ? [d.infrastructure.id] : []),
  },
  {
    type: "DARK_SHIP", Icone: EyeOff, couleur: "#e85bc7", piste: "deux", etape: "3", actif: true, vignette: true,
    // Deux sources : écho radar sans AIS (Sentinel 1) ou lumière nocturne sans AIS (VIIRS, details.source)
    navires: (d) => [...(d.navire_liste ? [d.navire_liste] : []), ...(d.candidats_ais ?? [])],
    titre: (d) => d.source === "viirs" ? P.lumiere : P.echo(num(d.length_m, 0)),
    signe: (d) => d.source === "viirs"
      ? <>{d.a_confirmer && <Tag>{P.aConfirmer}</Tag>}<Tag>{P.viirsDe(num(d.nanowatts, 0))}</Tag></>
      : <Tag>{P.contrasteDe(num(d.contrast_vv_db, 0))}</Tag>,
    preuves: (p) => {
      const d = p.details ?? {};
      if (d.source === "viirs") return (
        <>
          <Row label={P.capteur}>{L.fiche.viirs.satellites[d.satellite] ?? d.satellite}</Row>
          <Row label={P.instantPassage}>{utc(d.heure ?? p.event_time)}</Row>
          <Row label={P.intensite}>{num(d.nanowatts, 1)} nW/cm²/sr</Row>
          <Row label={P.lune}>{d.lune != null ? `${num(d.lune, 0)} %` : L.commun.nd}</Row>
          <Row label={P.distanceCote}>{d.distance_cote_km != null ? `${num(d.distance_cote_km)} km` : L.commun.nd}</Row>
          {d.infrastructure && <Row label={P.infrastructure}>{d.infrastructure.name ?? d.infrastructure.type}, {d.infrastructure.distance_m} m</Row>}
          {d.navire_liste && <Row label={P.navireListe}>{d.navire_liste.name ?? d.navire_liste.mmsi}, {num(d.navire_liste.distance_km)} km</Row>}
          <Context items={d.contexte} />
          <div className="mt-3 font-semibold">{P.naviresExamines}</div>
          {(d.candidats_ais ?? []).length ? (
            <table className="mt-1 w-full text-left">
              <thead className="text-muted"><tr><th>{P.navire}</th><th>{P.distance}</th><th>{P.tolerance}</th></tr></thead>
              <tbody>{d.candidats_ais.map((c: Props) => (
                <tr key={c.vessel_id} className="border-t border-hair/70">
                  <td>{c.name ?? c.mmsi}</td><td>{c.distance_m} m</td><td>{c.rayon_tolere_m} m</td>
                </tr>))}</tbody>
            </table>
          ) : <p className="text-muted">{P.aucunNavireProche}</p>}
        </>
      );
      return (
        <>
          <Row label={P.longueurEstimee}>{num(d.length_m, 0)} m</Row>
          <Row label={P.contraste}>{num(d.contrast_vv_db)} dB ({P.seuil(num(d.parametres?.seuil_contraste_db, 0))})</Row>
          <Row label={P.scorePresence}>{num(d.objectness, 2)}</Row>
          <Row label={P.scoreNavire}>{num(d.vessel_score, 2)}</Row>
          <Row label={P.instantPassage}>{utc(p.event_time)}</Row>
          {d.reclassement && <p className="mt-3 rounded-md bg-raised p-2.5 text-ink/90">{d.reclassement}</p>}
          <div className="mt-3 font-semibold">{P.naviresExamines}</div>
          {(d.candidats_ais ?? []).length ? (
            <table className="mt-1 w-full text-left">
              <thead className="text-muted"><tr><th>{P.navire}</th><th>{P.distance}</th><th>{P.tolerance}</th></tr></thead>
              <tbody>{d.candidats_ais.map((c: Props) => (
                <tr key={c.mmsi} className="border-t border-hair/70">
                  <td>{c.name ?? c.mmsi}</td><td>{c.distance_m} m</td><td>{c.tolerance_le_long_m ?? c.rayon_tolere_m} m</td>
                </tr>))}</tbody>
            </table>
          ) : <p className="text-muted">{P.aucunNavireProche}</p>}
        </>
      );
    },
  },
  {
    type: "AIS_UNCONFIRMED", Icone: CircleHelp, couleur: "#e8d45a", piste: "deux", etape: "3", actif: true, vignette: true,
    navires: (d) => [d.navire], titre: (d) => nom(d.navire),
    signe: (d) => <Tag>{P.sansEcho(num(d.navire?.length_m, 0))}</Tag>,
    preuves: (p) => {
      const d = p.details ?? {};
      return (
        <>
          <Row label={P.navire}>{vesselText(d.navire)}</Row>
          <Row label={P.vitesseDeclaree}>{num(d.navire?.sog_kn)} {L.commun.noeuds}</Row>
          <Row label={P.positionPassage}>{d.methode_position}</Row>
          <Row label={P.echoProche}>{d.echo_le_plus_proche_m == null ? P.aucun : `${d.echo_le_plus_proche_m} m`}</Row>
          <Row label={P.tolerance}>{P.toleranceDe(d.tolerance_le_long_m, d.tolerance_en_travers_m)}</Row>
          <Context items={d.contexte} />
        </>
      );
    },
  },
  {
    type: "IDENTITY_MISMATCH", Icone: Ruler, couleur: "#c9a0ff", piste: "flotte", etape: "3", actif: false,
    navires: (d) => [d.navire], titre: (d) => nom(d.navire), signe: () => null, preuves: aVenir,
  },
  {
    type: "TRAJECTOIRE_ANORMALE", Icone: Route, couleur: "#ff86b3", piste: "deux", etape: "pilote", actif: false,
    navires: (d) => [d.navire], titre: (d) => nom(d.navire), signe: () => null, preuves: aVenir,
  },
  {
    type: "PASSAGE_PREVU", Icone: Navigation, couleur: "#9fd85a", piste: "infra", etape: "4", actif: false,
    navires: (d) => [d.navire], titre: (d) => nom(d.navire), signe: () => null, preuves: aVenir,
  },
];

const PAR_TYPE = Object.fromEntries(TYPES_ALERTE.map((t) => [t.type, t]));
const INCONNU: TypeAlerte = {
  type: "?", Icone: CircleHelp, couleur: "#ffffff", piste: "deux", etape: "", actif: true,
  navires: () => [], titre: () => "", signe: () => null, preuves: () => null,
};

export const typeAlerte = (type: string): TypeAlerte => PAR_TYPE[type] ?? { ...INCONNU, type };
export const couleurAlerte = (type: string) => typeAlerte(type).couleur;
export const libelleAlerte = (type: string) => L.alertes[type] ?? type;

/** Navires d'une alerte : les preuves en base (vessel_ids) à défaut des détails. */
export function naviresAlerte(a: Feature): NavireRef[] {
  const fromDetails = typeAlerte(a.properties.type).navires(a.properties.details ?? {}).filter(Boolean) as NavireRef[];
  if (fromDetails.length) return fromDetails;
  return (a.properties.vessel_ids ?? []).map((id: number) => ({ vessel_id: id }));
}

/** Expression MapLibre : couleur du contour d'une alerte selon son type. */
export const STROKE_ALERTE: any = ["match", ["get", "type"], ...TYPES_ALERTE.flatMap((t) => [t.type, t.couleur]), "#ffffff"];
