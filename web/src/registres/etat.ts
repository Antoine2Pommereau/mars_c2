import { L } from "../lib/libelles";

// Registre des indicateurs de la barre d'état : chaque indicateur lit l'état du direct (/api/ingestion), se classe
// vert, orange ou rouge, et fournit son détail. Les seuils sont des repères d'exploitation, pas de la calibration.

type Niveau = "vert" | "orange" | "rouge" | "gris";
interface Mesure { niveau: Niveau; resume: string; detail: [string, string][] }
interface Indicateur { id: string; libelle: string; etape: number | null; evaluer: (s: any, now: number) => Mesure }

const MIN = 60_000;
const age = (iso: string | null | undefined, now: number) => (iso ? now - Date.parse(iso) : Infinity);
function duree(ms: number): string {
  if (!Number.isFinite(ms)) return L.commun.nd;
  if (ms < 2 * MIN) return `${Math.max(0, Math.round(ms / 1000))} s`;
  if (ms < 120 * MIN) return `${Math.round(ms / MIN)} min`;
  if (ms < 48 * 60 * MIN) return `${Math.round(ms / 60 / MIN)} h`;
  return `${Math.round(ms / 1440 / MIN)} j`;
}
const date = (iso?: string | null) => (iso ? `${iso.slice(8, 10)}/${iso.slice(5, 7)}/${iso.slice(0, 4)} ${iso.slice(11, 16)}` : L.commun.nd);
const palier = (v: number, vert: number, orange: number): Niveau => (v < vert ? "vert" : v < orange ? "orange" : "rouge");
const pire = (ns: Niveau[]): Niveau => (["rouge", "orange", "gris", "vert"] as Niveau[]).find((n) => ns.includes(n)) ?? "gris";
const E = L.etat;

export const INDICATEURS: Indicateur[] = [
  {
    id: "flux", libelle: E.flux, etape: null,
    evaluer: (s, now) => {
      const h = s?.derniere_heure ?? {};
      const a = age(h.derniere_position, now);
      const debit = Math.round((h.lus ?? 0) / 60);
      return { niveau: s ? palier(a, 5 * MIN, 15 * MIN) : "gris", resume: duree(a),
        detail: [[E.dernierMessage, `${date(h.derniere_position)} UTC, ${E.ilYa(duree(a))}`],
                 [E.debit, `${debit} ${E.parMinute}`], [E.navires, String(h.navires ?? L.commun.nd)]] };
    },
  },
  {
    id: "ingestion", libelle: E.ingestion, etape: null,
    evaluer: (s, now) => {
      const f = s?.dernier_fichier;
      const a = age(f?.ingested_at, now);
      const h = s?.derniere_heure ?? {};
      return { niveau: s ? palier(a, 2 * MIN, 10 * MIN) : "gris", resume: duree(a),
        detail: [[E.dernierFichier, f ? `${f.name}, ${E.ilYa(duree(a))}` : L.commun.nd],
                 [E.fichiersHeure, String(h.fichiers ?? L.commun.nd)], [E.conserves, String(h.conserves ?? L.commun.nd)]] };
    },
  },
  {
    id: "listes", libelle: E.listes, etape: null,
    evaluer: (s, now) => {
      const listes: any[] = s?.listes ?? [];
      if (!s) return { niveau: "gris", resume: L.commun.nd, detail: [] };
      if (!listes.length) return { niveau: "rouge", resume: E.aucuneListe, detail: [] };
      const j = 24 * 60 * MIN;
      const niveaux = listes.map((l) => palier(age(l.importe_le, now), 30 * j, 90 * j));
      return { niveau: pire(niveaux), resume: duree(Math.max(...listes.map((l) => age(l.importe_le, now)))),
        detail: listes.map((l) => [l.source === "gur" ? L.sources.gur : L.sources.opensanctions,
                                   `${l.navires}, ${E.importeLe} ${date(l.importe_le)}`]) };
    },
  },
  {
    id: "disque", libelle: E.disque, etape: null,
    evaluer: (s) => {
      const d: any[] = s?.disque ?? [];
      if (!d.length) return { niveau: "gris", resume: L.commun.nd, detail: [] };
      const libre = Math.min(...d.map((x) => x.libre_pct));
      const niveau = pire([libre < 15 ? "rouge" : libre < 25 ? "orange" : "vert", d.some((x) => x.perimee) ? "orange" : "vert"]);
      return { niveau, resume: `${libre.toFixed(0)} %`,
        detail: d.map((x) => [x.chemin, `${E.libre} ${x.libre_pct} %, ${x.libre_go} Go${x.perimee ? `, ${E.perimee}` : ""}`]) };
    },
  },
  {
    id: "archivage", libelle: E.archivage, etape: null,
    evaluer: (s, now) => {
      const t: any[] = s?.taches ?? [];
      const get = (k: string) => t.find((x) => x.tache === k);
      const lignes = ([["archivage", E.archive], ["sauvegarde", E.sauvegarde], ["regles", E.regles]] as const)
        .map(([k, lib]) => [k, lib, get(k)] as const);
      if (!t.length) return { niveau: "gris", resume: L.commun.nd, detail: [] };
      const niveaux = lignes.filter(([k]) => k !== "regles").map(([, , r]): Niveau =>
        !r ? "gris" : r.statut === "echec" ? "rouge" : palier(age(r.debut, now), 36 * 60 * MIN, 72 * 60 * MIN));
      return { niveau: pire(niveaux), resume: duree(age(get("archivage")?.debut, now)),
        detail: lignes.map(([, lib, r]) => [lib, r ? `${date(r.debut)}${r.statut === "echec" ? `, ${E.echec}` : ""}` : L.commun.nd]) };
    },
  },
  { id: "satellites", libelle: E.satellites, etape: 3, evaluer: () => ({ niveau: "gris", resume: "", detail: [] }) },
];

export const COULEUR_NIVEAU: Record<Niveau, string> = { vert: "#5fd38d", orange: "#f0a84b", rouge: "#ef6461", gris: "#4c5a66" };
