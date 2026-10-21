import { L } from "../lib/libelles";
import { VERSION_INTERFACE } from "../lib/traces";

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

const INDICATEURS: Indicateur[] = [
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
      // Dernière mise à jour automatique (conteneur taches) : un échec garde la liste précédente, signalé en orange.
      // La fraîcheur compte depuis la dernière vérification réussie, pas depuis le dernier changement de contenu.
      const t: any[] = s?.taches ?? [];
      const j = 24 * 60 * MIN;
      const lignes = listes.map((l) => {
        const run = t.find((x) => x.tache === `listes_${l.source}`);
        const ok = run?.statut === "ok" ? run.debut : null;
        const vu = ok && age(ok, now) < age(l.importe_le, now) ? ok : l.importe_le;
        const niveau = pire([palier(age(vu, now), 30 * j, 90 * j), run?.statut === "echec" ? "orange" : "vert"]);
        const texte = `${l.navires}, ${E.importeLe} ${date(l.importe_le)}`
          + (ok ? `, ${E.verifieeLe} ${date(ok)}` : "")
          + (run?.statut === "echec" ? `, ${E.echecListe(date(run.debut))}` : "");
        return { niveau, vu, detail: [L.sources.organismes[l.source === "gur" ? "gur" : "opensanctions"], texte] as [string, string] };
      });
      const detail = lignes.map((x) => x.detail);
      if (listes.some((l) => l.source === "opensanctions")) detail.push(["", E.attribution]);
      return { niveau: pire(lignes.map((x) => x.niveau)), resume: duree(Math.max(...lignes.map((x) => age(x.vu, now)))), detail };
    },
  },
  {
    id: "disque", libelle: E.disque, etape: null,
    evaluer: (s) => {
      const d: any[] = s?.disque ?? [];
      if (!d.length) return { niveau: "gris", resume: L.commun.nd, detail: [] };
      const libre = Math.min(...d.map((x) => x.libre_pct));
      // Seuils de config/supervision.env, transmis par l'API : mêmes couleurs que la tuile Disque de Grafana
      const rouge = s?.seuils?.disque_pct ?? 15, orange = rouge + (s?.seuils?.disque_orange_pts ?? 10);
      const niveau = pire([libre < rouge ? "rouge" : libre < orange ? "orange" : "vert", d.some((x) => x.perimee) ? "orange" : "vert"]);
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
  {
    // Calendrier des passages (conteneur taches, chaque jour) : prochain passage prévu sur la région affichée
    id: "satellites", libelle: E.satellites, etape: null,
    evaluer: (s, now) => {
      const sat = s?.satellites;
      if (!sat) return { niveau: "gris", resume: L.commun.nd, detail: [] };
      const maj = sat.mise_a_jour;
      // Nuits VIIRS (lot B) : dernière nuit traitée et dernier travailleur éphémère (état, durée, coût estimé)
      const nuit = sat.viirs?.derniere_nuit, w = sat.viirs?.travailleur, lance = sat.viirs?.lancement;
      const niveaux: Niveau[] = [!maj ? "gris" : maj.statut === "echec" ? "orange" : palier(age(maj.le, now), 36 * 60 * MIN, 72 * 60 * MIN)];
      if (nuit) niveaux.push(palier(age(nuit.traite_le, now), 30 * 60 * MIN, 54 * 60 * MIN));
      if (w?.etat === "echec" || lance?.statut === "echec") niveaux.push("orange");
      if (w && !w.detruit_le && age(w.cree_le, now) > 60 * MIN) niveaux.push("rouge");    // instance non détruite
      const gpu = sat.sentinel?.travailleur, attente = sat.sentinel?.attente;
      // Rupture de stock chez Scaleway : une attente (nouvel essai à chaque cycle), pas un échec
      if (gpu?.etat === "echec" && !gpu.rupture_stock) niveaux.push("orange");
      if (attente) niveaux.push("orange");
      if (gpu && !gpu.detruit_le && age(gpu.cree_le, now) > 60 * MIN) niveaux.push("rouge"); // GPU facturé à la minute
      const niveau = pire(niveaux);
      const prochains: any[] = sat.prochains ?? [];
      const premier = prochains.reduce((a: any, b: any) => (!a || b.acquired_at < a.acquired_at ? b : a), null);
      const quand = (iso: string) => `${date(iso)} UTC, ${E.dans(duree(Date.parse(iso) - now))}`;
      const detail: [string, string][] = (["S1", "S2"] as const).map((m) => {
        const p = prochains.find((x) => x.mission === m);
        return [E.prochain(m), p ? `${p.satellite} ${quand(p.acquired_at)}` : L.commun.nd];
      });
      detail.push([E.dernierAcquis, sat.dernier ? `${sat.dernier.satellite} ${date(sat.dernier.acquired_at)} UTC` : L.commun.nd]);
      detail.push([E.calendrier, maj ? `${date(maj.le)}${maj.statut === "echec" ? `, ${E.echec}` : ""}` : L.commun.nd]);
      const jour = (d: string) => `${d.slice(8, 10)}/${d.slice(5, 7)}/${d.slice(0, 4)}`;
      detail.push([E.nuitViirs, nuit ? E.nuitDe(jour(nuit.nuit), nuit.granules, nuit.detections ?? 0, nuit.en_echec) : L.commun.nd]);
      detail.push([E.travailleur, w ? E.travailleurDe(E.etatsTravailleur[w.etat] ?? w.etat, w.commercial_type, date(w.cree_le),
        w.duree_s, w.cout_eur) : L.commun.nd]);
      if (w?.erreur) detail.push([E.motif, w.erreur]);
      if (lance?.statut === "echec") detail.push([E.lancement, `${date(lance.le)}, ${E.echec}${lance.erreur ? ` : ${lance.erreur}` : ""}`]);
      // Sentinel 1 (lot C) : dernier passage analysé, dernier travailleur GPU, coût du mois, dernier lancement ou refus
      const s1 = sat.sentinel, a = s1?.dernier, g = s1?.travailleur, ls = s1?.lancement;
      detail.push([E.sentinel, a ? E.sentinelDe(String(a.satellite ?? "").replace(/^S1/, "Sentinel 1"), date(a.acquis_le),
        L.fiche.passage.analyses[a.statut] ?? a.statut, a.detections ?? 0) : L.commun.nd]);
      if (g) detail.push([E.travailleurGpu, E.travailleurDe(E.etatsTravailleur[g.etat] ?? g.etat, g.commercial_type, date(g.cree_le),
        null, g.cout_eur)]);
      if (g?.erreur) detail.push([E.motif, g.erreur]);
      if (attente) detail.push([E.analyseSentinel, E.attenteGpu(duree(Math.max(0, Date.parse(attente.prochain_essai) - now)))]);
      if (s1) detail.push([E.coutMois, E.euros(s1.euros_mois ?? 0)]);
      if (ls?.statut === "echec") detail.push([E.lancementSentinel, `${date(ls.le)}${ls.erreur ? ` : ${ls.erreur}` : ""}`]);
      // Sentinel 2 (lot S2) : dernière analyse optique et son résultat, part de chaque capteur dans le budget partagé
      const s2 = s1?.optique, o = s2?.dernier;
      if (o) detail.push([E.sentinel2, E.sentinel2De(String(o.satellite ?? "").replace(/^S2/, "Sentinel 2"), date(o.acquis_le),
        E.resultatsOptique[o.resultat] ?? L.fiche.passage.analyses[o.statut] ?? o.statut, o.detections ?? 0)]);
      if (s2?.travailleur?.erreur) detail.push([E.motif, s2.travailleur.erreur]);
      if (s2 && (s2.euros_mois.optique || s2.euros_mois.radar)) detail.push([E.budgetPartage,
        E.coutParSource(s2.euros_mois.radar ?? 0, s2.euros_mois.optique ?? 0)]);
      // Tip and cue (lot T1) : demandes en attente ou planifiées, prochaine observation prévue
      const tc = sat.tip_and_cue;
      if (tc) {
        detail.push([E.demandes, tc.actif ? E.demandesDe(tc.en_attente ?? 0, tc.planifiees ?? 0) : E.tipCoupe]);
        const n = tc.prochaine;
        if (n) detail.push([E.prochaineObservation, E.observationDe(n.satellite ?? "", quand(n.heure_passage),
          n.navire ?? L.fiche.observation.tips[n.tip] ?? n.tip)]);
      }
      return { niveau, resume: premier ? E.dans(duree(Date.parse(premier.acquired_at) - now)) : L.commun.nd, detail };
    },
  },
  {
    // Empreinte du commit de chaque image (étape D2) : interface (construite avec elle), API, tâches, ingestion,
    // collecte (signes de vie). Toutes égales : vert ; une image en retard : orange, avec le détail
    id: "version", libelle: E.version, etape: null,
    evaluer: (s) => {
      const v: Record<string, string> = { interface: VERSION_INTERFACE, ...(s?.versions ?? {}) };
      if (!s) return { niveau: "gris", resume: VERSION_INTERFACE, detail: [] };
      const services = ["interface", "api", "taches", "ingestion", "collecte"];
      const ecarts = services.filter((k) => v[k] && v[k] !== v.interface);
      return { niveau: ecarts.length ? "orange" : "vert", resume: VERSION_INTERFACE,
        detail: services.map((k) => [E.services[k] ?? k, v[k] ? `${v[k]}${ecarts.includes(k) ? `, ${E.differente}` : ""}` : L.commun.nd]) };
    },
  },
];

export const COULEUR_NIVEAU: Record<Niveau, string> = { vert: "#5fd38d", orange: "#f0a84b", rouge: "#ef6461", gris: "#4c5a66" };

/** Toutes les mesures en place, dans l'ordre du registre */
export const mesurer = (s: any, now: number) =>
  INDICATEURS.filter((i) => i.etape == null).map((ind) => ({ ind, m: ind.evaluer(s, now) }));

/** Voyant Système (lot U) : le pire niveau mesuré (une mesure absente ne l'emporte pas sur les autres), et
 *  l'indicateur qui le cause */
export function systeme(mesures: ReturnType<typeof mesurer>): { niveau: Niveau; cause: (typeof mesures)[number] | null } {
  const mesures_ = mesures.filter((x) => x.m.niveau !== "gris");
  if (!mesures_.length) return { niveau: "gris", cause: null };
  const niveau = pire(mesures_.map((x) => x.m.niveau));
  return { niveau, cause: niveau === "vert" ? null : mesures_.find((x) => x.m.niveau === niveau) ?? null };
}
